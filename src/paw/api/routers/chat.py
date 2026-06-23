from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, get_redis, require_csrf, require_role
from paw.api.pagination import decode_cursor, encode_cursor
from paw.db.models import ChatSession, User
from paw.harness.ops.chat import refs_payload
from paw.harness.ops.query import DONT_KNOW
from paw.services.chat import ChatService, PreparedTurn

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    q: str
    domain_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


class RefOut(BaseModel):
    article_id: str
    slug: str
    title: str


class ChatResponse(BaseModel):
    session_id: str
    answer_md: str
    refs: list[RefOut]


class SessionOut(BaseModel):
    id: str
    title: str | None
    last_active_at: str


class SessionPage(BaseModel):
    items: list[SessionOut]
    next_cursor: str | None


class MessageOut(BaseModel):
    role: str
    content: str
    meta: dict[str, object]
    created_at: str


class SessionDetail(BaseModel):
    id: str
    title: str | None
    domain_id: str
    messages: list[MessageOut]


async def _sse(
    svc: ChatService, sess: ChatSession, question: str, prepared: PreparedTurn
) -> AsyncIterator[str]:
    if prepared.messages is None:
        answer = DONT_KNOW
        yield f"data: {json.dumps({'token': DONT_KNOW}, ensure_ascii=False)}\n\n"
    else:
        chunks: list[str] = []
        async for tok in prepared.chat.stream(prepared.messages, model=prepared.model):
            chunks.append(tok)
            yield f"data: {json.dumps({'token': tok}, ensure_ascii=False)}\n\n"
        answer = "".join(chunks) or DONT_KNOW
    await svc.record_turn(
        session=sess, question=question, answer_md=answer, refs=prepared.ctx.refs,
        model=prepared.model, prompt_version=prepared.prompt_version, usage={},
    )
    done = {
        "status": "done",
        "session_id": str(sess.id),
        "refs": refs_payload(prepared.ctx.refs),
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    _: None = Depends(require_csrf),
    user: User = Depends(require_role("admin", "editor", "viewer")),
    session: AsyncSession = Depends(db),
) -> object:
    svc = ChatService(session).with_redis(get_redis())
    sess = await svc.resolve_session(
        user=user, domain_id=body.domain_id, session_id=body.session_id
    )
    prepared = await svc.prepare_turn(session=sess, question=body.q)  # raises 404/422 here
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _sse(svc, sess, body.q, prepared), media_type="text/event-stream"
        )
    turn, usage = await svc.complete_turn(prepared)
    await svc.record_turn(
        session=sess, question=body.q, answer_md=turn.answer_md, refs=turn.refs,
        model=prepared.model, prompt_version=prepared.prompt_version, usage=usage,
    )
    return ChatResponse(
        session_id=str(sess.id),
        answer_md=turn.answer_md,
        refs=[RefOut(**r) for r in refs_payload(turn.refs)],
    )


@router.get("/chat/sessions", response_model=SessionPage)
async def list_sessions(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(require_role("admin", "editor", "viewer")),
    session: AsyncSession = Depends(db),
) -> SessionPage:
    decoded = decode_cursor(cursor) if cursor else None
    rows = await ChatService(session).list_user_sessions(
        user_id=user.id, limit=limit, cursor=decoded
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].last_active_at.isoformat(), str(page[-1].id)) if has_more else None
    )
    return SessionPage(
        items=[
            SessionOut(id=str(s.id), title=s.title, last_active_at=s.last_active_at.isoformat())
            for s in page
        ],
        next_cursor=next_cursor,
    )


@router.get("/chat/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    user: User = Depends(require_role("admin", "editor", "viewer")),
    session: AsyncSession = Depends(db),
) -> SessionDetail:
    svc = ChatService(session)
    sess = await svc.get_owned(session_id=session_id, user_id=user.id)
    msgs = await svc.session_messages(session_id)
    return SessionDetail(
        id=str(sess.id),
        title=sess.title,
        domain_id=str(sess.domain_id),
        messages=[
            MessageOut(
                role=m.role, content=m.content, meta=m.meta, created_at=m.created_at.isoformat()
            )
            for m in msgs
        ],
    )


@router.delete("/chat/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID,
    _: None = Depends(require_csrf),
    user: User = Depends(require_role("admin", "editor", "viewer")),
    session: AsyncSession = Depends(db),
) -> Response:
    await ChatService(session).delete_owned(session_id=session_id, user_id=user.id)
    return Response(status_code=204)
