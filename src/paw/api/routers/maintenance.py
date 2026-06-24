from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.services.maintenance import MaintenanceService

router = APIRouter(tags=["maintenance"])


@router.post(
    "/domains/{domain_id}/lint",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_lint(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_lint(domain_id=domain_id)
    return {"job_id": str(job.id)}


class FixRequest(BaseModel):
    issue_ids: list[str]


@router.post(
    "/domains/{domain_id}/fix",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_fix(
    domain_id: uuid.UUID, body: FixRequest, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_fix(
        domain_id=domain_id, issue_ids=body.issue_ids
    )
    return {"job_id": str(job.id)}


@router.post(
    "/domains/{domain_id}/format",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_format(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_format(domain_id=domain_id)
    return {"job_id": str(job.id)}


@router.post(
    "/domains/{domain_id}/reindex",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_reindex(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_reindex(domain_id=domain_id)
    return {"job_id": str(job.id)}
