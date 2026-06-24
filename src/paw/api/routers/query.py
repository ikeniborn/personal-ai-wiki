from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, get_redis, require_csrf, require_role
from paw.harness.ops.query import DONT_KNOW
from paw.harness.retrieve import Passage, Ref, RetrievedContext
from paw.services.query import Prepared, QueryService
from paw.services.query_cache import CacheHit, QueryCacheService

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    q: str


class RefOut(BaseModel):
    article_id: str
    slug: str
    title: str


class PassageOut(BaseModel):
    chunk_id: str
    article_id: str
    slug: str
    heading_path: str | None
    text: str
    score: float


class QueryResult(BaseModel):
    answer_md: str
    refs: list[RefOut]
    passages: list[PassageOut]
    stale: bool = False
    cached: bool = False


class SuggestResult(BaseModel):
    suggestions: list[str]


def _refs_json(refs: list[Ref]) -> list[dict[str, str]]:
    return [{"article_id": str(r.article_id), "slug": r.slug, "title": r.title} for r in refs]


def _passages_json(ps: list[Passage]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": str(p.chunk_id),
            "article_id": str(p.article_id),
            "slug": p.slug,
            "heading_path": p.heading_path,
            "text": p.text,
            "score": p.score,
        }
        for p in ps
    ]


def _to_result(answer_md: str, ctx: RetrievedContext) -> QueryResult:
    return QueryResult(
        answer_md=answer_md,
        refs=[RefOut(**r) for r in _refs_json(ctx.refs)],
        passages=[PassageOut(**p) for p in _passages_json(ctx.passages)],  # type: ignore[arg-type]
    )


def _cached_result(hit: CacheHit) -> QueryResult:
    return QueryResult(
        answer_md=hit.answer_md,
        refs=[RefOut(**r) for r in hit.refs],  # type: ignore[arg-type]
        passages=[PassageOut(**p) for p in hit.passages],  # type: ignore[arg-type]
        stale=hit.stale,
        cached=True,
    )


def _sse_cached(hit: CacheHit) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'token': hit.answer_md}, ensure_ascii=False)}\n\n"
        done = {
            "status": "done",
            "refs": hit.refs,
            "passages": hit.passages,
            "stale": hit.stale,
            "cached": True,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return gen()


def _sse_compute(
    prepared: Prepared,
    cache: QueryCacheService | None,
    *,
    domain_id: uuid.UUID,
    question: str,
    model: str,
) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        tokens: list[str] = []
        if prepared.messages is None:
            yield f"data: {json.dumps({'token': DONT_KNOW}, ensure_ascii=False)}\n\n"
        else:
            async for tok in prepared.chat.stream(prepared.messages):
                tokens.append(tok)
                yield f"data: {json.dumps({'token': tok}, ensure_ascii=False)}\n\n"
        done = {
            "status": "done",
            "refs": _refs_json(prepared.ctx.refs),
            "passages": _passages_json(prepared.ctx.passages),
            "stale": False,
            "cached": False,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        if cache is not None and prepared.ctx.refs and tokens:
            await cache.upsert(
                domain_id=domain_id, question=question, answer_md="".join(tokens),
                refs=prepared.ctx.refs, passages=prepared.ctx.passages, model=model,
            )

    return gen()


@router.get(
    "/domains/{domain_id}/suggest",
    dependencies=[Depends(require_role("admin", "editor", "viewer"))],
)
async def suggest_domain(
    domain_id: uuid.UUID,
    q: str = "",
    session: AsyncSession = Depends(db),
) -> SuggestResult:
    svc = QueryCacheService(session)
    cfg = await svc.config(domain_id)
    sugg = await svc.suggest(domain_id=domain_id, q=q, top_k=cfg.suggest_top_k)
    return SuggestResult(suggestions=sugg)


@router.post(
    "/domains/{domain_id}/query",
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor", "viewer"))],
)
async def query_domain(
    domain_id: uuid.UUID,
    body: QueryRequest,
    request: Request,
    refresh: int = 0,
    session: AsyncSession = Depends(db),
) -> object:
    qsvc = QueryService(session).with_redis(get_redis())
    csvc = QueryCacheService(session).with_redis(get_redis())
    cfg = await csvc.config(domain_id)
    wants_sse = "text/event-stream" in request.headers.get("accept", "")

    if cfg.enabled and not refresh:
        hit = await csvc.lookup(domain_id=domain_id, question=body.q, cfg=cfg)
        if hit is not None:
            await csvc.touch(hit.id)
            if wants_sse:
                return StreamingResponse(_sse_cached(hit), media_type="text/event-stream")
            return _cached_result(hit)

    prepared = await qsvc.prepare(domain_id=domain_id, question=body.q)  # raises 404/422
    model = str(getattr(prepared.chat, "chat_model", ""))
    if wants_sse:
        cache = csvc if cfg.enabled else None
        return StreamingResponse(
            _sse_compute(prepared, cache, domain_id=domain_id, question=body.q, model=model),
            media_type="text/event-stream",
        )
    answer = await qsvc.complete(prepared)
    if cfg.enabled and prepared.ctx.refs:
        await csvc.upsert(
            domain_id=domain_id, question=body.q, answer_md=answer.answer_md,
            refs=prepared.ctx.refs, passages=prepared.ctx.passages, model=model,
        )
    return _to_result(answer.answer_md, prepared.ctx)
