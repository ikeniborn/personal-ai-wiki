import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf, require_role
from paw.db.models import User
from paw.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "viewer"


class UserOut(BaseModel):
    id: str
    email: str
    role: str


class RoleUpdate(BaseModel):
    role: str


class UiLanguageUpdate(BaseModel):
    ui_language: str


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin")),
) -> list[UserOut]:
    return [
        UserOut(id=str(u.id), email=u.email, role=u.role) for u in await UserService(session).list()
    ]


@router.post(
    "",
    status_code=201,
    response_model=UserOut,
    dependencies=[Depends(require_csrf)],
)
async def create_user(
    body: UserCreate,
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin")),
) -> UserOut:
    u = await UserService(session).create(
        email=body.email, password=body.password, role=body.role, actor_id=user.id
    )
    return UserOut(id=str(u.id), email=u.email, role=u.role)


@router.post("/me/ui-language", status_code=204, dependencies=[Depends(require_csrf)])
async def set_my_ui_language(
    body: UiLanguageUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> Response:
    await UserService(session).set_ui_language(user_id=user.id, lang=body.ui_language)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(require_csrf)],
)
async def update_user_role(
    user_id: uuid.UUID,
    body: RoleUpdate,
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin")),
) -> UserOut:
    u = await UserService(session).set_role(user_id=user_id, role=body.role, actor_id=user.id)
    return UserOut(id=str(u.id), email=u.email, role=u.role)


@router.delete(
    "/{user_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin")),
) -> Response:
    await UserService(session).delete(user_id=user_id, actor_id=user.id)
    return Response(status_code=204)
