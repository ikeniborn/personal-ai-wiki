import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.api.errors import ProblemError
from paw.security.uploads import UploadRejected
from paw.services.sources import SourceService

router = APIRouter(prefix="/domains/{domain_id}/sources", tags=["sources"])


class SourceOut(BaseModel):
    id: str
    filename: str | None
    type: str


@router.post(
    "",
    status_code=201,
    response_model=SourceOut,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def upload_source(
    domain_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
) -> SourceOut:
    data = await file.read()
    try:
        src = await SourceService(session).upload_text(
            domain_id=domain_id,
            filename=file.filename or "upload",
            data=data,
            content_type=file.content_type,
        )
    except UploadRejected as e:
        raise ProblemError(status=422, title="Upload rejected", detail=str(e)) from e
    return SourceOut(id=str(src.id), filename=src.filename, type=src.type)
