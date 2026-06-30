import io
import uuid
import zipfile

from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", "# A\n")
        zf.writestr("b.md", "# B\n")
    return buf.getvalue()


async def test_web_bulk_upload_returns_drawer(
    db_session: AsyncSession, wired_settings: object, monkeypatch: MonkeyPatch
) -> None:
    enqueue_calls: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, str | None]] = []

    async def fake_enqueue(
        _ctx: object,
        *,
        job_id: uuid.UUID,
        domain_id: uuid.UUID,
        source_id: uuid.UUID | None = None,
        topic: str | None = None,
    ) -> None:
        enqueue_calls.append((job_id, domain_id, source_id, topic))

    monkeypatch.setattr("paw.services.jobs.enqueue_ingest", fake_enqueue)
    await UserRepo(db_session).create(
        email="ed@example.com", pw_hash=hash_password("pw12345678901"), role="editor"
    )
    dom = await DomainRepo(db_session).create(
        name="d", source_prefix="sources", wiki_prefix="wiki"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        login = await c.post(
            "/api/v1/auth/login",
            json={"email": "ed@example.com", "password": "pw12345678901"},
        )
        assert login.status_code == 200
        csrf = c.cookies.get("paw_csrf")
        assert csrf
        resp = await c.post(
            f"/domains/{dom.id}/sources/bulk",
            headers={"x-csrf-token": csrf},
            files={"file": ("s.zip", _zip_bytes(), "application/zip")},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert resp.text.count('class="job"') == 2
        assert resp.text.count('data-job-events="/api/v1/jobs/') == 2
        assert "sse-connect" not in resp.text
        assert len(enqueue_calls) == 2
        assert {domain_id for _, domain_id, _, _ in enqueue_calls} == {dom.id}
        assert all(source_id is not None for _, _, source_id, _ in enqueue_calls)
        assert len({source_id for _, _, source_id, _ in enqueue_calls}) == 2
        assert all(topic is None for _, _, _, topic in enqueue_calls)
    finally:
        await c.aclose()
