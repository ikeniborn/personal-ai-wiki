from typing import Any

from arq.connections import RedisSettings

from paw.config import get_settings


async def heartbeat(ctx: dict[str, Any]) -> str:
    """Liveness marker so deploys can assert the worker runs (LLD §7 seed for jobs)."""
    redis = ctx["redis"]
    await redis.set("paw:worker:heartbeat", "1", ex=120)
    return "ok"


class _LazyRedisSettings:
    def __get__(self, obj: object, owner: type | None = None) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions = [heartbeat]
    redis_settings = _LazyRedisSettings()

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        await heartbeat(ctx)
