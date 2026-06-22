from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def domain_lock(redis: Any, domain_id: str, *, ttl: int = 3600) -> AsyncIterator[bool]:
    key = f"lock:domain:{domain_id}"
    acquired = bool(await redis.set(key, "1", nx=True, ex=ttl))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.delete(key)


@asynccontextmanager
async def model_lock(
    redis: Any, model: str, *, ttl: int = 600, poll: float = 0.05, timeout: float = 120.0
) -> AsyncIterator[None]:
    key = f"lock:model:{model}"
    waited = 0.0
    while not await redis.set(key, "1", nx=True, ex=ttl):
        await asyncio.sleep(poll)
        waited += poll
        if waited >= timeout:
            raise TimeoutError(f"model lock timeout: {model}")
    try:
        yield
    finally:
        await redis.delete(key)
