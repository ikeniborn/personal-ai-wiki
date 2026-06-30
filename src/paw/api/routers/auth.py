from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.auth import LoginRequest, LoginResponse
from paw.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    db,
    get_redis,
    get_session_store,
)
from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.repos.users import UserRepo
from paw.security.csrf import issue_token
from paw.security.passwords import verify_password
from paw.security.ratelimit import LoginGuard, RateLimiter
from paw.security.sessions import SessionStore

router = APIRouter(prefix="/auth", tags=["auth"])


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
    ip = request.client.host if request.client else "unknown"
    email_guard_key = f"email:{body.email}"
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
    email_allowed = await limiter.hit(
        f"login:email:{body.email}",
        limit=s.login_rate_limit,
        window_seconds=s.login_rate_window_seconds,
    )
    if not ip_allowed or not email_allowed:
        raise ProblemError(status=429, title="Too many attempts", detail="slow down")

    user = await UserRepo(session).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.pw_hash):
        await guard.record_failure(email_guard_key)
        await guard.record_failure(ip_guard_key)
        raise ProblemError(status=401, title="Unauthorized", detail="bad credentials")
    await guard.reset(email_guard_key)
    await guard.reset(ip_guard_key)
    sid = await store.create(str(user.id))
    csrf = issue_token(s.session_secret)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=True)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=True)
    return LoginResponse(id=str(user.id), email=user.email, role=user.role)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    store: SessionStore = Depends(get_session_store),
) -> Response:
    sid = request.cookies.get(SESSION_COOKIE, "")
    if sid:
        await store.delete(sid)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return Response(status_code=204)
