from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from paw.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            connect_args={
                # AGE: resolve cypher()/agtype without a per-call LOAD, and avoid the
                # prepared-statement cache colliding with AGE's cypher(cstring) parse hook.
                "server_settings": {"search_path": 'ag_catalog,"$user",public'},
                "statement_cache_size": 0,
            },
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
