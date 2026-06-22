from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.managed import ensure_embedding_column
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.entities import EntityRepo
from paw.graph.repo import GraphRepo
from paw.harness.prompts import get_prompt
from paw.ingest.chunking import build_chunks
from paw.providers.base import ChatProvider, EmbeddingProvider, Message
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article
from paw.vector.embed import embed_and_write

ProgressFn = Callable[[str], Awaitable[None]]
_HEADING_ANY = re.compile(r"^#{1,6}\s+", re.MULTILINE)


class Extraction(BaseModel):
    entities: list[str]
    key_points: list[str]


class CitationDraft(BaseModel):
    quote: str
    locator: str | None = None


class Draft(BaseModel):
    slug: str
    title: str
    summary: str
    markdown: str
    entities: list[str]
    citations: list[CitationDraft]


@dataclass
class IngestResult:
    article_id: uuid.UUID
    chunk_count: int
    entity_count: int
    citation_count: int
    link_count: int


def _normalize_headings(md: str) -> str:
    # collapse any heading level to '##' (headings <= ##).
    return _HEADING_ANY.sub("## ", md)


async def _emit(on_step: ProgressFn | None, msg: str) -> None:
    if on_step is not None:
        await on_step(msg)


async def run_ingest(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    source_md: str,
    chat: ChatProvider,
    embedder: EmbeddingProvider,
    cfg: WikiConfig,
    dim: int,
    on_step: ProgressFn | None = None,
    author_id: uuid.UUID | None = None,
) -> IngestResult:
    sys_extract = get_prompt(
        "extraction", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    sys_draft = get_prompt(
        "drafting", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )

    # Stage A — extraction (structured)
    await _emit(on_step, "extract")
    extraction = await chat.structured(  # type: ignore[attr-defined]
        [
            Message(role="system", content=sys_extract),
            Message(role="user", content=f"SOURCE:\n{source_md}"),
        ],
        Extraction,
        retries=cfg.max_retries,
    )

    # Stage B — drafting (structured)
    await _emit(on_step, "draft")
    draft = await chat.structured(  # type: ignore[attr-defined]
        [
            Message(role="system", content=sys_draft),
            Message(
                role="user",
                content=f"ENTITIES: {extraction.entities}\n"
                f"KEY POINTS: {extraction.key_points}\nSOURCE:\n{source_md}",
            ),
        ],
        Draft,
        retries=cfg.max_retries,
    )
    markdown = _normalize_headings(draft.markdown)

    # Stage C — deterministic write
    await _emit(on_step, "write")
    art, _created = await upsert_article(
        session,
        domain_id=domain_id,
        slug=draft.slug,
        title=draft.title,
        markdown=markdown,
        summary=draft.summary,
        author_id=author_id,
    )
    entities = EntityRepo(session)
    entity_ids: list[uuid.UUID] = []
    for name in dict.fromkeys(draft.entities):  # dedup, keep order
        e = await entities.upsert(domain_id=domain_id, name=name)
        await entities.tag_article(article_id=art.id, entity_id=e.id)
        entity_ids.append(e.id)
    citation_repo = CitationRepo(session)
    for c in draft.citations:
        await citation_repo.create(
            article_id=art.id, source_id=None, quote=c.quote, locator=c.locator
        )

    # Stage D — links (co-occurrence over shared entities >= hub_threshold)
    await _emit(on_step, "link")
    graph = GraphRepo(session)
    link_count = 0
    for target in await graph.cooccurrence_targets(
        domain_id=domain_id, article_id=art.id, threshold=cfg.hub_threshold
    ):
        if await graph.link(
            domain_id=domain_id, src_article_id=art.id, dst_article_id=target, type="related"
        ):
            link_count += 1

    # Stage E — chunking + embedding
    await _emit(on_step, "embed")
    await ensure_embedding_column(session, dim)
    specs = await build_chunks(summary=draft.summary, markdown=markdown, embedder=embedder, cfg=cfg)
    ids = await embed_and_write(
        session, article_id=art.id, domain_id=domain_id, specs=specs, embedder=embedder
    )
    chunk_repo = ChunkRepo(session)
    for cid in ids:
        for eid in entity_ids:
            await chunk_repo.tag_entity(chunk_id=cid, entity_id=eid)

    return IngestResult(
        article_id=art.id,
        chunk_count=len(ids),
        entity_count=len(entity_ids),
        citation_count=len(draft.citations),
        link_count=link_count,
    )
