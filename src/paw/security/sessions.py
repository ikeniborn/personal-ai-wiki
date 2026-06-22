import secrets
from typing import cast

import redis.asyncio as aioredis

_PREFIX = "session:"


class SessionStore:
    """Server-side sessions in Redis (cookie holds the opaque session id). LLD §8."""

    def __init__(self, client: aioredis.Redis, ttl_seconds: int) -> None:
        self._r = client
        self._ttl = ttl_seconds

    async def create(self, user_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        await self._r.set(_PREFIX + sid, user_id, ex=self._ttl)
        return sid

    async def get(self, sid: str) -> str | None:
        if not sid:
            return None
        return cast("str | None", await self._r.get(_PREFIX + sid))

    async def delete(self, sid: str) -> None:
        await self._r.delete(_PREFIX + sid)
