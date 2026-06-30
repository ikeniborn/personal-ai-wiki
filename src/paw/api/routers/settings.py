from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.db.models import User
from paw.services.provider_settings import ProviderSettingsService
from paw.services.settings import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings_endpoint(
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    return await SettingsService(session).get()


@router.put("", dependencies=[Depends(require_csrf), Depends(require_role("admin"))])
async def put_settings_endpoint(
    body: dict[str, Any], session: AsyncSession = Depends(db)
) -> dict[str, Any]:
    return await SettingsService(session).update(body)


class ProviderConnRequest(BaseModel):
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    embedding_dim: int
    vision_model: str | None = None


@router.post(
    "/provider",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def set_provider_connection(
    body: ProviderConnRequest,
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin")),
) -> Response:
    await ProviderSettingsService(session).update_provider(**body.model_dump(), actor_id=user.id)
    return Response(status_code=204)
