import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.models import User
from paw.db.repos.users import UserRepo
from paw.db.session import get_session
from paw.security.csrf import verify_token
from paw.security.sessions import SessionStore

SESSION_COOKIE = "paw_session"
CSRF_COOKIE = "paw_csrf"
CSRF_HEADER = "x-csrf-token"

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url, decode_responses=True
        )
    return _redis


def get_session_store() -> SessionStore:
    return SessionStore(get_redis(), ttl_seconds=get_settings().session_ttl_seconds)


async def db() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


async def current_user(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> User:
    sid = request.cookies.get(SESSION_COOKIE, "")
    user_id = await store.get(sid)
    if not user_id:
        raise ProblemError(status=401, title="Unauthorized")
    user = await UserRepo(session).get(uuid.UUID(user_id))
    if user is None:
        raise ProblemError(status=401, title="Unauthorized")
    return user


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    async def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise ProblemError(status=403, title="Forbidden",
                               detail=f"requires role in {roles}")
        return user

    return _dep


async def require_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not verify_token(get_settings().session_secret, cookie, header):
        raise ProblemError(status=403, title="CSRF validation failed")
