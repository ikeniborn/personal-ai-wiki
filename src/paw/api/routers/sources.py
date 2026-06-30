import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.api.errors import ProblemError
from paw.db.models import User
from paw.security.ssrf import SsrfRejected
from paw.security.uploads import UploadRejected
from paw.services.jobs import JobService
from paw.services.sources import SourceService

router = APIRouter(prefix="/domains/{domain_id}/sources", tags=["sources"])


class SourceOut(BaseModel):
    id: str
    filename: str | None
    type: str


class BulkOut(BaseModel):
    sources: list[SourceOut]
    job_ids: list[str]


@router.post(
    "",
    status_code=201,
    response_model=SourceOut,
    dependencies=[Depends(require_csrf)],
)
async def upload_source(
    domain_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor")),
) -> SourceOut:
    data = await file.read()
    try:
        src = await SourceService(session).upload(
            domain_id=domain_id,
            filename=file.filename or "upload",
            data=data,
            content_type=file.content_type,
        )
    except (UploadRejected, SsrfRejected) as e:
        raise ProblemError(status=422, title="Upload rejected", detail=str(e)) from e
    return SourceOut(id=str(src.id), filename=src.filename, type=src.type)


@router.post(
    "/bulk",
    status_code=201,
    response_model=BulkOut,
    dependencies=[Depends(require_csrf)],
)
async def upload_bulk(
    domain_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
    user: User = Depends(require_role("admin", "editor")),
) -> BulkOut:
    data = await file.read()
    try:
        srcs = await SourceService(session).upload_bulk(domain_id=domain_id, zip_bytes=data)
    except (UploadRejected, SsrfRejected) as e:
        raise ProblemError(status=422, title="Bulk upload rejected", detail=str(e)) from e
    job_ids: list[str] = []
    jobs = JobService(session)
    for src in srcs:
        job = await jobs.start_ingest(domain_id=domain_id, source_id=src.id, actor_id=user.id)
        job_ids.append(str(job.id))
    return BulkOut(
        sources=[SourceOut(id=str(src.id), filename=src.filename, type=src.type) for src in srcs],
        job_ids=job_ids,
    )
