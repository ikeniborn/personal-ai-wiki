from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.audit.log import record
from paw.db.repos.articles import ArticleRepo
from paw.graph.repo import GraphRepo
from paw.harness.limits import Budget
from paw.providers.base import EmbeddingProvider, ToolSpec
from paw.providers.config import RetrievalConfig, WikiConfig
from paw.storage.postgres import PostgresStorage


@dataclass
class ToolContext:
    session: AsyncSession
    domain_id: uuid.UUID
    user_id: uuid.UUID | None
    budget: Budget
    issues: list[dict[str, object]] | None = None
    embedder: EmbeddingProvider | None = None
    retrieval: RetrievalConfig | None = None


@dataclass
class Tool:
    spec: ToolSpec
    writes: bool
    run: Callable[[ToolContext, dict[str, object]], Awaitable[dict[str, object]]]


async def _read_source(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    from paw.db.repos.sources import SourceRepo

    src = await SourceRepo(ctx.session).get(uuid.UUID(str(args["source_id"])))
    if src is None or src.domain_id != ctx.domain_id:
        raise PermissionError("source not in domain")
    data = await PostgresStorage(ctx.session).get(src.storage_ref)
    return {"type": src.type, "bytes_len": len(data)}


async def _get_article(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    art = await ArticleRepo(ctx.session).get(uuid.UUID(str(args["article_id"])))
    if art is None or art.domain_id != ctx.domain_id:
        raise PermissionError("article not in domain")
    return {"id": str(art.id), "slug": art.slug, "title": art.title}


async def _list_articles(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    arts = await ArticleRepo(ctx.session).list_by_domain(ctx.domain_id)
    return {"articles": [{"id": str(a.id), "slug": a.slug, "title": a.title} for a in arts]}


async def _search_wiki(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    from paw.harness.retrieve import retrieve

    if ctx.embedder is None or ctx.retrieval is None:
        raise ValueError("search_wiki requires embedder + retrieval config in context")
    cfg = ctx.retrieval
    if args.get("top_k") is not None:
        cfg = ctx.retrieval.model_copy(
            update={"top_n": int(args["top_k"])}  # type: ignore[call-overload]
        )
    result = await retrieve(
        ctx.session,
        domain_id=ctx.domain_id,
        query=str(args["query"]),
        embedder=ctx.embedder,
        cfg=cfg,
        embed_model=getattr(ctx.embedder, "embedding_model", ""),
    )
    return {
        "passages": [
            {
                "chunk_id": str(p.chunk_id),
                "slug": p.slug,
                "heading_path": p.heading_path,
                "text": p.text,
                "score": p.score,
            }
            for p in result.passages
        ],
        "refs": [
            {"article_id": str(r.article_id), "slug": r.slug, "title": r.title}
            for r in result.refs
        ],
    }


async def _upsert_article(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    from paw.services.ingest_write import upsert_article

    art, created = await upsert_article(
        ctx.session,
        domain_id=ctx.domain_id,
        slug=str(args["slug"]),
        title=str(args["title"]),
        markdown=str(args["markdown"]),
        summary=str(args.get("summary") or ""),
        author_id=ctx.user_id,
    )
    return {"id": str(art.id), "created": created}


async def _add_link(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    link_type = str(args["type"])
    if link_type not in WikiConfig().link_types:
        raise ValueError(f"link type not allowed: {link_type}")
    src_id = uuid.UUID(str(args["src_id"]))
    dst_id = uuid.UUID(str(args["dst_id"]))
    for aid in (src_id, dst_id):
        art = await ArticleRepo(ctx.session).get(aid)
        if art is None or art.domain_id != ctx.domain_id:
            raise PermissionError("link target outside domain (write-scope)")
    created = await GraphRepo(ctx.session).link(
        domain_id=ctx.domain_id, src_article_id=src_id, dst_article_id=dst_id, type=link_type
    )
    return {"created": created}


async def _report_issue(ctx: ToolContext, args: dict[str, object]) -> dict[str, object]:
    if ctx.issues is None:
        ctx.issues = []
    ctx.issues.append(dict(args))  # collect-only; consumed in Phase 6
    return {"recorded": True}


def _spec(name: str, desc: str, props: dict[str, object], required: list[str]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=desc,
        parameters={"type": "object", "properties": props, "required": required},
    )


READ_TOOLS: dict[str, Tool] = {
    "read_source": Tool(
        _spec(
            "read_source",
            "Read a source's metadata.",
            {"source_id": {"type": "string"}},
            ["source_id"],
        ),
        writes=False,
        run=_read_source,
    ),
    "get_article": Tool(
        _spec(
            "get_article",
            "Get an article by id.",
            {"article_id": {"type": "string"}},
            ["article_id"],
        ),
        writes=False,
        run=_get_article,
    ),
    "list_articles": Tool(
        _spec("list_articles", "List articles in the domain.", {}, []),
        writes=False,
        run=_list_articles,
    ),
    "search_wiki": Tool(
        _spec(
            "search_wiki",
            "Hybrid search (vector + FTS + graph) over the domain wiki. Read-only.",
            {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            ["query"],
        ),
        writes=False,
        run=_search_wiki,
    ),
}

WRITE_TOOLS: dict[str, Tool] = {
    "upsert_article": Tool(
        _spec(
            "upsert_article",
            "Create or merge an article by slug.",
            {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "markdown": {"type": "string"},
                "summary": {"type": "string"},
            },
            ["slug", "title", "markdown"],
        ),
        writes=True,
        run=_upsert_article,
    ),
    "add_link": Tool(
        _spec(
            "add_link",
            "Add a typed link between two articles in the domain.",
            {
                "src_id": {"type": "string"},
                "dst_id": {"type": "string"},
                "type": {"type": "string"},
            },
            ["src_id", "dst_id", "type"],
        ),
        writes=True,
        run=_add_link,
    ),
}

COLLECT_TOOLS: dict[str, Tool] = {
    "report_issue": Tool(
        _spec(
            "report_issue",
            "Record a quality issue (collect-only).",
            {"kind": {"type": "string"}, "detail": {"type": "string"}},
            ["kind"],
        ),
        writes=False,
        run=_report_issue,
    ),
}

_ALLOWLISTS: dict[str, dict[str, Tool]] = {
    "ingest": {**READ_TOOLS, **WRITE_TOOLS, **COLLECT_TOOLS},
    "query": {
        "search_wiki": READ_TOOLS["search_wiki"],
        "get_article": READ_TOOLS["get_article"],
        "list_articles": READ_TOOLS["list_articles"],
    },
}


def tools_for(op: str) -> dict[str, Tool]:
    if op not in _ALLOWLISTS:
        raise ValueError(f"unknown op: {op}")
    return _ALLOWLISTS[op]


async def run_tool(ctx: ToolContext, name: str, args: dict[str, object]) -> dict[str, object]:
    tool = {**READ_TOOLS, **WRITE_TOOLS, **COLLECT_TOOLS}[name]
    ctx.budget.tool_call()
    if tool.writes:
        ctx.budget.write()
    result = await tool.run(ctx, args)
    await record(
        ctx.session,
        user_id=ctx.user_id,
        action=f"tool:{name}",
        target_type="domain",
        target_id=ctx.domain_id,
        meta={"args_keys": sorted(args)},
    )
    return result
