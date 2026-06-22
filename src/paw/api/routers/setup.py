from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db
from paw.services.setup import SetupService

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupRequest(BaseModel):
    email: EmailStr
    password: str
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    embedding_dim: int
    vision_model: str | None = None


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
    admin = await SetupService(session).complete(
        email=body.email,
        password=body.password,
        base_url=body.base_url,
        api_key=body.api_key,
        chat_model=body.chat_model,
        embedding_model=body.embedding_model,
        embedding_dim=body.embedding_dim,
        vision_model=body.vision_model,
    )
    return SetupResult(id=str(admin.id), email=admin.email, role=admin.role)
