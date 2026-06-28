import hashlib
import io
import uuid
import zipfile

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.models import Source
from paw.db.repos.sources import SourceRepo
from paw.security.uploads import (
    UploadRejected,
    inspect_zip,
    validate_source_upload,
    validate_text_upload,
)
from paw.storage.postgres import PostgresStorage


class SourceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = SourceRepo(session)
        self._store = PostgresStorage(session)

    async def upload_text(
        self, *, domain_id: uuid.UUID, filename: str, data: bytes, content_type: str | None
    ) -> Source:
        validate_text_upload(filename, data, max_bytes=get_settings().max_upload_bytes)
        checksum = hashlib.sha256(data).hexdigest()
        ref = await self._store.put(data, content_type=content_type or "text/markdown")
        ext = filename.rsplit(".", 1)[-1].lower()
        src = await self._repo.create(
            domain_id=domain_id, storage_ref=ref, filename=filename, type=ext, checksum=checksum
        )
        await self._s.commit()
        return src

    async def upload(
        self, *, domain_id: uuid.UUID, filename: str, data: bytes, content_type: str | None
    ) -> Source:
        kind = validate_source_upload(filename, data, max_bytes=get_settings().max_upload_bytes)
        checksum = hashlib.sha256(data).hexdigest()
        ref = await self._store.put(data, content_type=content_type, large=len(data) > 256 * 1024)
        src = await self._repo.create(
            domain_id=domain_id, storage_ref=ref, filename=filename, type=kind, checksum=checksum
        )
        await self._s.commit()
        return src

    async def upload_url(self, *, domain_id: uuid.UUID, url: str) -> Source:
        from paw.config import parse_allowlist
        from paw.security.ssrf import validate_url

        allow = parse_allowlist(get_settings().url_allowlist)
        validate_url(url, allowlist=allow)
        data = url.encode()
        checksum = hashlib.sha256(data).hexdigest()
        ref = await self._store.put(data, content_type="text/uri-list")
        src = await self._repo.create(
            domain_id=domain_id,
            storage_ref=ref,
            filename=url,
            type="url",
            checksum=checksum,
            url=url,
        )
        await self._s.commit()
        return src

    async def upload_bulk(self, *, domain_id: uuid.UUID, zip_bytes: bytes) -> list[Source]:
        s = get_settings()
        inspect_zip(
            zip_bytes,
            max_total=s.max_unzip_bytes,
            max_entries=s.max_unzip_entries,
            max_ratio=s.max_compression_ratio,
        )
        created: list[Source] = []
        seen: set[str] = set()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name == "mimetype" or name.startswith("META-INF/"):
                    continue
                body = zf.read(info)
                try:
                    kind = validate_source_upload(name, body, max_bytes=s.max_upload_bytes)
                except UploadRejected:
                    continue
                checksum = hashlib.sha256(body).hexdigest()
                if checksum in seen:
                    continue
                seen.add(checksum)
                ref = await self._store.put(body, large=len(body) > 256 * 1024)
                src = await self._repo.create(
                    domain_id=domain_id,
                    storage_ref=ref,
                    filename=name,
                    type=kind,
                    checksum=checksum,
                )
                created.append(src)
        await self._s.commit()
        return created

    async def list(self, domain_id: uuid.UUID) -> list[Source]:
        return await self._repo.list_by_domain(domain_id)
