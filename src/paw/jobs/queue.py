from __future__ import annotations

import uuid
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from paw.config import get_settings

# Process-global arq pool (lazy singleton), mirroring deps._redis / get_sessionmaker.
# Avoids opening a fresh connection pool on every enqueue (notably init_domain's loop).
_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue_ingest(
    redis: Any | None = None,
    *,
    job_id: uuid.UUID,
    domain_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    topic: str | None = None,
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job(
        "ingest_domain",
        str(job_id),
        str(domain_id),
        str(source_id) if source_id else None,
        topic,
    )


async def enqueue_gc_housekeeping(redis: Any | None = None) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("gc_housekeeping")


async def enqueue_lint(
    redis: Any | None = None, *, job_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("lint_domain", str(job_id), str(domain_id))


async def enqueue_fix(
    redis: Any | None = None,
    *,
    job_id: uuid.UUID,
    domain_id: uuid.UUID,
    issue_ids: list[str],
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("fix_issues", str(job_id), str(domain_id), issue_ids)


async def enqueue_format(
    redis: Any | None = None, *, job_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("format_articles", str(job_id), str(domain_id))
