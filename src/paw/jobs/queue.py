from __future__ import annotations

import uuid
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from paw.config import get_settings


async def enqueue_ingest(
    redis: Any | None = None,
    *,
    job_id: uuid.UUID,
    domain_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    topic: str | None = None,
) -> None:
    pool = redis or await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await pool.enqueue_job(
        "ingest_domain",
        str(job_id),
        str(domain_id),
        str(source_id) if source_id else None,
        topic,
    )
