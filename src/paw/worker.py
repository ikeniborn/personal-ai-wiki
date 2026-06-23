from typing import Any

from arq.connections import RedisSettings

from paw.config import get_settings
from paw.jobs.tasks import ingest_domain


async def heartbeat(ctx: dict[str, Any]) -> str:
    """Liveness marker so deploys can assert the worker runs (LLD §7 seed for jobs)."""
    redis = ctx["redis"]
    await redis.set("paw:worker:heartbeat", "1", ex=120)
    return "ok"


class _LazyRedisSettings:
    def __get__(self, obj: object, owner: type | None = None) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)


async def reconcile_jobs(ctx: dict[str, Any]) -> str:
    from paw.db.repos.jobs import JobRepo
    from paw.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        n = await JobRepo(session).reconcile_stuck(older_than_seconds=120)
        await session.commit()
    return f"reconciled:{n}"


class WorkerSettings:
    functions = [heartbeat, ingest_domain]
    redis_settings = _LazyRedisSettings()

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        await heartbeat(ctx)
        await reconcile_jobs(ctx)
