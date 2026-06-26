from typing import Any

from arq.connections import RedisSettings

from paw.config import get_settings
from paw.jobs.tasks import (
    fix_issues,
    format_articles,
    gc_housekeeping,
    ingest_domain,
    lint_domain,
    reindex_domain,
)
from paw.obs import metrics


async def heartbeat(ctx: dict[str, Any]) -> str:
    """Liveness marker so deploys can assert the worker runs (LLD §7 seed for jobs)."""
    redis = ctx["redis"]
    await redis.set("paw:worker:heartbeat", "1", ex=120)
    return "ok"


class _LazyRedisSettings:
    def __get__(self, obj: object, owner: type | None = None) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)


async def set_queue_depth(redis: Any) -> None:
    """Refresh the QUEUE_DEPTH gauge from the live arq Redis sorted set."""
    try:
        import arq.constants

        n = await redis.zcard(arq.constants.default_queue_name)
        metrics.QUEUE_DEPTH.set(n)
    except Exception:  # noqa: BLE001
        pass  # gauge update must never fail startup or a job


async def reconcile_jobs(ctx: dict[str, Any]) -> str:
    from paw.db.repos.jobs import JobRepo
    from paw.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        n = await JobRepo(session).reconcile_stuck(older_than_seconds=120)
        await session.commit()
    return f"reconciled:{n}"


class WorkerSettings:
    functions = [
        heartbeat,
        ingest_domain,
        gc_housekeeping,
        lint_domain,
        fix_issues,
        format_articles,
        reindex_domain,
    ]
    redis_settings = _LazyRedisSettings()

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        port = get_settings().worker_metrics_port
        if port > 0:
            try:
                from prometheus_client import start_http_server

                start_http_server(port)
            except Exception:  # noqa: BLE001
                pass  # metrics server must never crash the worker
        await heartbeat(ctx)
        await reconcile_jobs(ctx)
        await set_queue_depth(ctx["redis"])
