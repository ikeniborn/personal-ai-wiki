from collections.abc import AsyncIterator, Iterator

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_async_url(pg_container: PostgresContainer) -> str:
    return pg_container.get_connection_url()


@pytest.fixture(scope="session")
def pg_sync_url(pg_container: PostgresContainer) -> str:
    # psycopg2 sync URL for alembic/inspection helpers
    return pg_container.get_connection_url().replace("+asyncpg", "+psycopg2")


@pytest.fixture(scope="session", autouse=True)
def _migrate(pg_async_url: str, monkeypatch_session: pytest.MonkeyPatch) -> None:
    # Apply the alembic baseline once per session against the container.
    monkeypatch_session.setenv("DATABASE_URL", pg_async_url)
    monkeypatch_session.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch_session.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch_session.setenv("FERNET_KEY", "k" * 44)
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session(pg_async_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(pg_async_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as rc:
        yield rc


@pytest.fixture
def wired_settings(pg_async_url: str, redis_container: RedisContainer,
                   monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the app's cached settings/redis at the live test containers."""
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("DATABASE_URL", pg_async_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("FERNET_KEY", "k" * 44)
    import paw.api.deps as deps
    import paw.db.session as db_session_mod
    from paw.config import get_settings
    get_settings.cache_clear()
    deps._redis = None
    db_session_mod._engine = None
    db_session_mod._sessionmaker = None
    yield
    get_settings.cache_clear()
    deps._redis = None
    db_session_mod._engine = None
    db_session_mod._sessionmaker = None


@pytest.fixture
async def redis_client(redis_container: RedisContainer) -> AsyncIterator["aioredis.Redis"]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = aioredis.Redis(host=host, port=int(port), decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()
