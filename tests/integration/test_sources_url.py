import hashlib

import pytest
from sqlalchemy import text

from paw.db.repos.domains import DomainRepo
from paw.security.ssrf import SsrfRejected
from paw.services.sources import SourceService
from paw.storage.postgres import PostgresStorage


async def _domain(db_session):
    dom = await DomainRepo(db_session).create(
        name="url-domain", source_prefix="src/url", wiki_prefix="wiki/url"
    )
    await db_session.commit()
    return dom


async def test_upload_url_rejects_http_via_ssrf(db_session, wired_settings):
    dom = await _domain(db_session)

    with pytest.raises(SsrfRejected, match="only https urls are allowed"):
        await SourceService(db_session).upload_url(
            domain_id=dom.id, url="http://example.com/article"
        )


async def test_upload_url_registers_source(db_session, wired_settings, monkeypatch):
    dom = await _domain(db_session)
    calls = []

    def fake_validate_url(url: str, *, allowlist: list[str]) -> str:
        calls.append((url, allowlist))
        return "example.com"

    monkeypatch.setattr("paw.security.ssrf.validate_url", fake_validate_url)

    url = "https://example.com/article"
    src = await SourceService(db_session).upload_url(domain_id=dom.id, url=url)

    assert calls == [(url, [])]
    assert src.domain_id == dom.id
    assert src.filename == url
    assert src.type == "url"
    assert src.url == url
    assert src.checksum == hashlib.sha256(url.encode()).hexdigest()
    assert await PostgresStorage(db_session).get(src.storage_ref) == url.encode()

    blob_id = src.storage_ref.removeprefix("blob:")
    row = await db_session.execute(
        text("SELECT content_type FROM blobs WHERE id = :id"), {"id": blob_id}
    )
    assert row.scalar_one() == "text/uri-list"
