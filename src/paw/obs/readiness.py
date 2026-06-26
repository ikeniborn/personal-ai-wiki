from __future__ import annotations

from sqlalchemy import text

from paw.api.deps import get_redis
from paw.db.session import get_sessionmaker


async def check_readiness() -> tuple[bool, dict[str, str]]:
    components: dict[str, str] = {}
    ok = True
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        components["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        components["db"] = f"error: {type(exc).__name__}"
        ok = False
    try:
        await get_redis().ping()
        components["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        components["redis"] = f"error: {type(exc).__name__}"
        ok = False
    return ok, components
