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


async def _sse(prepared: Prepared) -> AsyncIterator[str]:
    if prepared.messages is None:
        yield f"data: {json.dumps({'token': DONT_KNOW}, ensure_ascii=False)}\n\n"
    else:
        async for tok in prepared.chat.stream(prepared.messages):
            yield f"data: {json.dumps({'token': tok}, ensure_ascii=False)}\n\n"
    done = {
        "status": "done",
        "refs": _refs_json(prepared.ctx.refs),
        "passages": _passages_json(prepared.ctx.passages),
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"


@router.post(
    "/domains/{domain_id}/query",
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor", "viewer"))],
)
async def query_domain(
    domain_id: uuid.UUID,
    body: QueryRequest,
    request: Request,
    session: AsyncSession = Depends(db),
) -> object:
    svc = QueryService(session).with_redis(get_redis())
    prepared = await svc.prepare(domain_id=domain_id, question=body.q)  # raises 404/422 here
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_sse(prepared), media_type="text/event-stream")
    answer = await svc.complete(prepared)
    return _to_result(answer.answer_md, prepared.ctx)
