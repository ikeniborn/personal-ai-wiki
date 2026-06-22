from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.db.models import User
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
