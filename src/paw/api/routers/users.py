from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
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


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin")),
) -> list[UserOut]:
    return [UserOut(id=str(u.id), email=u.email, role=u.role)
            for u in await UserService(session).list()]


@router.post("", status_code=201, response_model=UserOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin"))])
async def create_user(body: UserCreate, session: AsyncSession = Depends(db)) -> UserOut:
    u = await UserService(session).create(email=body.email, password=body.password, role=body.role)
    return UserOut(id=str(u.id), email=u.email, role=u.role)
