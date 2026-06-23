from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
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
