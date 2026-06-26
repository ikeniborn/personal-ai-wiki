from __future__ import annotations

import asyncio
import time
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
    redis: Any,
    model: str,
    *,
    kind: str = "unknown",
    ttl: int = 600,
    poll: float = 0.05,
    timeout: float = 120.0,
) -> AsyncIterator[None]:
    from paw.obs import metrics  # lazy: keep jobs.locks import-light

    key = f"lock:model:{model}"
    deadline = time.monotonic() + timeout
    wait_start = time.monotonic()
    while not await redis.set(key, "1", nx=True, ex=ttl):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"model lock timeout: {model}")
        await asyncio.sleep(poll)
    metrics.JOB_LOCK_WAIT.labels(kind=kind).observe(time.monotonic() - wait_start)
    try:
        yield
    finally:
        await redis.delete(key)
