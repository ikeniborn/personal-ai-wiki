from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from paw.db.repos.jobs import JobRepo

_TERMINAL = {"succeeded", "failed", "cancelled"}


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


def _frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def publish(redis: Any, job_id: uuid.UUID | str, event: dict[str, Any]) -> None:
    await redis.publish(channel(job_id), json.dumps(event, ensure_ascii=False))


async def sse_events(redis: Any, job_repo: JobRepo, job_id: uuid.UUID) -> AsyncIterator[str]:
    job = await job_repo.get(job_id)
    if job is None:
        return
    for entry in job.log:
        yield _frame(entry)
    if job.status in _TERMINAL:
        return
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(job_id))
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
            if msg is None:
                continue
            raw = msg["data"]
            text = raw.decode() if isinstance(raw, bytes) else raw
            event = json.loads(text)
            yield _frame(event)
            if event.get("status") in _TERMINAL:
                return
    finally:
        await pubsub.unsubscribe(channel(job_id))
        await pubsub.aclose()
