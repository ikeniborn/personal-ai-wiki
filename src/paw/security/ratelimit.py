from __future__ import annotations

import time
import uuid

import redis.asyncio as aioredis


class RateLimiter:
    """Sliding-window counter backed by a Redis sorted set."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.time()
        full = f"ratelimit:{key}"
        member = f"{now}:{uuid.uuid4()}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(full, 0, now - window_seconds)
        pipe.zadd(full, {member: now})
        pipe.zcard(full)
        pipe.expire(full, window_seconds)
        _, _, count, _ = await pipe.execute()
        return int(count) <= limit


class LoginGuard:
    """Tracks consecutive failures per key and applies a temporary lock."""

    def __init__(
        self,
        redis: aioredis.Redis,
        *,
        threshold: int,
        lock_seconds: int,
        fail_window_seconds: int = 900,
    ) -> None:
        self._r = redis
        self._threshold = threshold
        self._lock_seconds = lock_seconds
        self._fail_window = fail_window_seconds

    async def record_failure(self, key: str) -> None:
        fail_key = f"loginfail:{key}"
        n = await self._r.incr(fail_key)
        if n == 1:
            await self._r.expire(fail_key, self._fail_window)
        if n >= self._threshold:
            await self._r.set(f"loginlock:{key}", "1", ex=self._lock_seconds)

    async def is_locked(self, key: str) -> bool:
        return bool(await self._r.exists(f"loginlock:{key}"))

    async def reset(self, key: str) -> None:
        await self._r.delete(f"loginfail:{key}", f"loginlock:{key}")
