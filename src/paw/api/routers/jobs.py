from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, get_redis, require_csrf, require_role
from paw.api.errors import ProblemError
from paw.db.models import User
from paw.db.repos.jobs import JobRepo
from paw.jobs.progress import sse_events
from paw.services.jobs import JobService

router = APIRouter(tags=["jobs"])


class IngestRequest(BaseModel):
    source_id: uuid.UUID


class InitRequest(BaseModel):
    brief: str = ""


@router.post(
    "/domains/{domain_id}/ingest",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_ingest(
    domain_id: uuid.UUID, body: IngestRequest, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await JobService(session).start_ingest(domain_id=domain_id, source_id=body.source_id)
    return {"job_id": str(job.id)}


@router.post(
    "/domains/{domain_id}/init",
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def init_domain(
    domain_id: uuid.UUID, body: InitRequest, session: AsyncSession = Depends(db)
) -> dict[str, list[dict[str, str]]]:
    pairs = await JobService(session).init_domain(domain_id=domain_id, brief=body.brief)
    return {"topics": [{"topic": t, "job_id": str(j)} for t, j in pairs]}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> dict[str, object]:
    job = await JobRepo(session).get(job_id)
    if job is None:
        raise ProblemError(status=404, title="Job not found")
    return {
        "id": str(job.id),
        "status": job.status,
        "kind": job.kind,
        "article_id": str(job.article_id) if job.article_id else None,
        "error": job.error,
        "log": job.log,
    }


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> StreamingResponse:
    repo = JobRepo(session)
    return StreamingResponse(sse_events(get_redis(), repo, job_id), media_type="text/event-stream")


@router.post(
    "/jobs/{job_id}/cancel",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def cancel_job(job_id: uuid.UUID, session: AsyncSession = Depends(db)) -> dict[str, str]:
    await JobService(session).cancel(job_id)
    return {"status": "cancelling"}
