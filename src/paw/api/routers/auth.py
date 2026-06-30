import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.auth import LoginRequest, LoginResponse
from paw.api.client_ip import client_ip
from paw.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    db,
    get_redis,
    get_session_store,
)
from paw.api.errors import ProblemError
from paw.audit import actions
from paw.audit.log import record
from paw.config import get_settings
from paw.db.repos.users import UserRepo
from paw.security.csrf import issue_token
from paw.security.passwords import verify_password
from paw.security.ratelimit import LoginGuard, RateLimiter
from paw.security.sessions import SessionStore

router = APIRouter(prefix="/auth", tags=["auth"])

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$kPkNAztwLj1cOKf4AEC6rA"  # nosec B105  # dummy hash for constant-time missing-user login checks
    "$Kboljui51UbAfJfUmKUyfvdMmBipqa466f7/N2HAlAY"
)


def _login_email_key(email: str) -> str:
    return email.strip().casefold()


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    s = get_settings()
    redis = get_redis()
    ip = client_ip(request)
    email_key = _login_email_key(str(body.email))
    email_guard_key = f"email:{email_key}"
    ip_guard_key = f"ip:{ip}"

    guard = LoginGuard(
        redis,
        threshold=s.login_lockout_threshold,
        lock_seconds=s.login_lockout_seconds,
    )
    if await guard.is_locked(email_guard_key) or await guard.is_locked(ip_guard_key):
        raise ProblemError(
            status=429,
            title="Too many attempts",
            detail="temporarily locked, try again later",
        )

    limiter = RateLimiter(redis)
    ip_allowed = await limiter.hit(
        f"login:ip:{ip}",
        limit=s.login_rate_limit,
        window_seconds=s.login_rate_window_seconds,
    )
    if not ip_allowed:
        raise ProblemError(status=429, title="Too many attempts", detail="slow down")
    email_allowed = await limiter.hit(
        f"login:email:{email_key}",
        limit=s.login_rate_limit,
        window_seconds=s.login_rate_window_seconds,
    )
    if not email_allowed:
        raise ProblemError(status=429, title="Too many attempts", detail="slow down")

    user = await UserRepo(session).get_by_email(body.email)
    pw_hash = user.pw_hash if user is not None else _DUMMY_PASSWORD_HASH
    if not verify_password(body.password, pw_hash) or user is None:
        await guard.record_failure(email_guard_key)
        await guard.record_failure(ip_guard_key)
        raise ProblemError(status=401, title="Unauthorized", detail="bad credentials")
    await guard.reset(email_guard_key)
    await guard.reset(ip_guard_key)
    sid = await store.create(str(user.id))
    csrf = issue_token(s.session_secret)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=True)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=True)
    await record(session, user_id=user.id, action=actions.LOGIN)
    await session.commit()
    return LoginResponse(id=str(user.id), email=user.email, role=user.role)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    sid = request.cookies.get(SESSION_COOKIE, "")
    if sid:
        raw_user_id = await store.get(sid)
        user_id: uuid.UUID | None = None
        if raw_user_id:
            try:
                user_id = uuid.UUID(raw_user_id)
            except ValueError:
                user_id = None
        if user_id is not None and await UserRepo(session).get(user_id) is not None:
            await record(session, user_id=user_id, action=actions.LOGOUT)
            await session.commit()
        await store.delete(sid)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return Response(status_code=204)
