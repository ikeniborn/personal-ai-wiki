from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db
from paw.services.setup import SetupService

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupRequest(BaseModel):
    email: EmailStr
    password: str


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupResult(BaseModel):
    id: str
    email: str
    role: str


@router.get("/status", response_model=SetupStatus)
async def status(session: AsyncSession = Depends(db)) -> SetupStatus:
    return SetupStatus(needs_setup=await SetupService(session).needs_setup())


@router.post("", status_code=201, response_model=SetupResult)
async def complete(body: SetupRequest, session: AsyncSession = Depends(db)) -> SetupResult:
    admin = await SetupService(session).complete(email=body.email, password=body.password)
    return SetupResult(id=str(admin.id), email=admin.email, role=admin.role)
