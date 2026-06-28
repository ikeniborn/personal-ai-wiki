import io
import uuid
import zipfile

import pytest

from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.security.uploads import UploadRejected
from paw.services.sources import SourceService


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


async def _domain(db_session):
    return await DomainRepo(db_session).create(
        name=f"d-{uuid.uuid4().hex[:8]}", source_prefix="s", wiki_prefix="w"
    )


async def test_bulk_registers_multiple_sources(db_session):
    dom = await _domain(db_session)
    await db_session.commit()

    z = _zip({"a.md": b"# A\n\nbody a", "b.md": b"# B\n\nbody b", "skip.exe": b"MZ"})
    srcs = await SourceService(db_session).upload_bulk(domain_id=dom.id, zip_bytes=z)

    assert {s.filename for s in srcs} == {"a.md", "b.md"}
    rows = await SourceRepo(db_session).list_by_domain(dom.id)
    assert len(rows) == 2


async def test_bulk_rejects_zip_bomb(db_session):
    dom = await _domain(db_session)
    await db_session.commit()

    bomb = _zip({"z.bin": b"\x00" * 5_000_000})
    with pytest.raises(UploadRejected, match="ratio"):
        await SourceService(db_session).upload_bulk(domain_id=dom.id, zip_bytes=bomb)
