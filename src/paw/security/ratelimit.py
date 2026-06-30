from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable
from typing import cast

import redis.asyncio as aioredis

_LOGIN_FAILURE_SCRIPT = """
local fail_key = KEYS[1]
local lock_key = KEYS[2]
local fail_window_seconds = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local lock_seconds = tonumber(ARGV[3])

local failures = redis.call("INCR", fail_key)
if redis.call("TTL", fail_key) < 0 then
    redis.call("EXPIRE", fail_key, fail_window_seconds)
end
if failures >= threshold then
    redis.call("SET", lock_key, "1", "EX", lock_seconds)
end
return failures
"""


class RateLimiter:
    """Sliding-window counter backed by a Redis sorted set."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

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
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if lock_seconds <= 0:
            raise ValueError("lock_seconds must be positive")
        if fail_window_seconds <= 0:
            raise ValueError("fail_window_seconds must be positive")

        self._r = redis
        self._threshold = threshold
        self._lock_seconds = lock_seconds
        self._fail_window = fail_window_seconds

    async def record_failure(self, key: str) -> None:
        fail_key = f"loginfail:{key}"
        lock_key = f"loginlock:{key}"
        await cast(
            Awaitable[object],
            self._r.eval(
                _LOGIN_FAILURE_SCRIPT,
                2,
                fail_key,
                lock_key,
                str(self._fail_window),
                str(self._threshold),
                str(self._lock_seconds),
            ),
        )

    async def is_locked(self, key: str) -> bool:
        return bool(await self._r.exists(f"loginlock:{key}"))

    async def reset(self, key: str) -> None:
        await self._r.delete(f"loginfail:{key}", f"loginlock:{key}")
