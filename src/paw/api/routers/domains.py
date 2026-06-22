from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.api.pagination import encode_cursor
from paw.db.models import User
from paw.services.domains import DomainService

router = APIRouter(prefix="/domains", tags=["domains"])


class DomainCreate(BaseModel):
    name: str


class DomainOut(BaseModel):
    id: str
    name: str


class DomainPage(BaseModel):
    items: list[DomainOut]
    next_cursor: str | None


@router.post("", status_code=201, response_model=DomainOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def create_domain(body: DomainCreate, session: AsyncSession = Depends(db)) -> DomainOut:
    d = await DomainService(session).create(body.name)
    return DomainOut(id=str(d.id), name=d.name)


@router.get("", response_model=DomainPage)
async def list_domains(
    limit: int = 50,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> DomainPage:
    items = await DomainService(session).list()
    page = items[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at.isoformat(), str(page[-1].id))
        if len(items) > limit else None
    )
    return DomainPage(
        items=[DomainOut(id=str(d.id), name=d.name) for d in page],
        next_cursor=next_cursor,
    )
