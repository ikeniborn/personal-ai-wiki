from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf
from paw.db.models import User
from paw.services.api_keys import ApiKeyService

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    scopes: list[str] = ["read"]


class ApiKeyIssued(BaseModel):
    id: str
    prefix: str
    key: str  # full secret token — shown once
    scopes: list[str]


class ApiKeyOut(BaseModel):
    id: str
    prefix: str
    scopes: list[str]
    created_at: str
    last_used: str | None
    revoked_at: str | None


@router.post("", status_code=201, response_model=ApiKeyIssued, dependencies=[Depends(require_csrf)])
async def create_api_key(
    body: ApiKeyCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> ApiKeyIssued:
    issued = await ApiKeyService(session).issue(user_id=user.id, scopes=body.scopes)
    return ApiKeyIssued(
        id=str(issued.id), prefix=issued.prefix, key=issued.token, scopes=issued.scopes
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> list[ApiKeyOut]:
    rows = await ApiKeyService(session).list(user.id)
    return [
        ApiKeyOut(
            id=str(r.id),
            prefix=r.prefix,
            scopes=list(r.scopes),
            created_at=r.created_at.isoformat(),
            last_used=r.last_used.isoformat() if r.last_used else None,
            revoked_at=r.revoked_at.isoformat() if r.revoked_at else None,
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> None:
    await ApiKeyService(session).revoke(user_id=user.id, key_id=key_id)
