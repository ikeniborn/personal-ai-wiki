from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import ApiKey


class ApiKeyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self, *, user_id: uuid.UUID, prefix: str, hash: str, scopes: list[str]
    ) -> ApiKey:
        k = ApiKey(user_id=user_id, prefix=prefix, hash=hash, scopes=scopes)
        self._s.add(k)
        await self._s.flush()
        return k

    async def by_prefix(self, prefix: str) -> list[ApiKey]:
        res = await self._s.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        return list(res.scalars().all())

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKey]:
        res = await self._s.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list(res.scalars().all())

    async def revoke(self, key_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        res = cast(
            CursorResult[Any],
            await self._s.execute(
                update(ApiKey)
                .where(
                    ApiKey.id == key_id,
                    ApiKey.user_id == user_id,
                    ApiKey.revoked_at.is_(None),
                )
                .values(revoked_at=func.now())
            ),
        )
        return bool(res.rowcount)

    async def touch_last_used(self, key_id: uuid.UUID) -> None:
        await self._s.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used=func.now())
        )
