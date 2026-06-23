from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import ChatMessage, ChatSession


class ChatRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_session(
        self, *, user_id: uuid.UUID, domain_id: uuid.UUID, title: str | None = None
    ) -> ChatSession:
        sess = ChatSession(user_id=user_id, domain_id=domain_id, title=title)
        self._s.add(sess)
        await self._s.flush()
        return sess

    async def get(self, session_id: uuid.UUID) -> ChatSession | None:
        result = await self._s.get(ChatSession, session_id)
        if result is not None:
            await self._s.refresh(result)
        return result

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[str, str] | None = None,
    ) -> list[ChatSession]:
        stmt = select(ChatSession).where(ChatSession.user_id == user_id)
        if cursor is not None:
            last_active_iso, ident = cursor
            stmt = stmt.where(
                tuple_(ChatSession.last_active_at, ChatSession.id)
                < (datetime.fromisoformat(last_active_iso), uuid.UUID(ident))
            )
        stmt = stmt.order_by(ChatSession.last_active_at.desc(), ChatSession.id.desc()).limit(limit)
        res = await self._s.execute(stmt)
        return list(res.scalars().all())

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]:
        # user + assistant of one turn share now(); break the tie so user comes first.
        res = await self._s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(
                ChatMessage.created_at,
                case((ChatMessage.role == "user", 0), else_=1),
            )
        )
        return list(res.scalars().all())

    async def count_messages(self, session_id: uuid.UUID) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(ChatMessage).where(
                ChatMessage.session_id == session_id
            )
        )
        return int(res.scalar_one())

    async def add_message(
        self, *, session_id: uuid.UUID, role: str, content: str, meta: dict[str, Any]
    ) -> ChatMessage:
        msg = ChatMessage(session_id=session_id, role=role, content=content, meta=meta)
        self._s.add(msg)
        await self._s.flush()
        return msg

    async def set_title(self, session_id: uuid.UUID, title: str) -> None:
        await self._s.execute(
            update(ChatSession).where(ChatSession.id == session_id).values(title=title)
        )
        await self._s.flush()

    async def bump_last_active(self, session_id: uuid.UUID) -> None:
        await self._s.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_active_at=func.now())
        )
        await self._s.flush()

    async def delete(self, session: ChatSession) -> None:
        await self._s.delete(session)
        await self._s.flush()

    async def list_for_gc(self, user_id: uuid.UUID) -> list[tuple[uuid.UUID, datetime]]:
        res = await self._s.execute(
            select(ChatSession.id, ChatSession.last_active_at).where(
                ChatSession.user_id == user_id
            )
        )
        return [(r[0], r[1]) for r in res.all()]

    async def delete_by_ids(self, session_ids: list[uuid.UUID]) -> None:
        if not session_ids:
            return
        await self._s.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))
        await self._s.flush()
