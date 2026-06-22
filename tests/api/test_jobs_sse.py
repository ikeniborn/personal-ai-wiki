import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        yield c


async def test_sse_streams_replayed_log(client, db_session):
    # a terminal job with log entries -> SSE replays then closes
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.append_log(job.id, {"step": "extract"})
    await repo.append_log(job.id, {"step": "done", "status": "succeeded"})
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()
    r = await client.get(f"/api/v1/jobs/{job.id}/events")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "extract" in r.text
    assert "succeeded" in r.text
