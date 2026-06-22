from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.auth import LoginRequest, LoginResponse
from paw.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    db,
    get_session_store,
)
from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.repos.users import UserRepo
from paw.security.csrf import issue_token
from paw.security.passwords import verify_password
from paw.security.sessions import SessionStore

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    user = await UserRepo(session).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.pw_hash):
        raise ProblemError(status=401, title="Unauthorized", detail="bad credentials")
    sid = await store.create(str(user.id))
    csrf = issue_token(get_settings().session_secret)
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
