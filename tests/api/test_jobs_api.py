import pytest
from httpx import ASGITransport, AsyncClient

import paw.services.jobs as jobs_svc
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()

    async def fake_enqueue(redis, **kwargs):
        return None  # do not actually enqueue in API tests

    monkeypatch.setattr(jobs_svc, "enqueue_ingest", fake_enqueue)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        yield c


async def test_start_ingest_returns_job_id(client, db_session):
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nbody", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources", files=files, headers=h)).json()
    r = await client.post(
        f"/api/v1/domains/{dom['id']}/ingest", json={"source_id": src["id"]}, headers=h
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    g = await client.get(f"/api/v1/jobs/{job_id}")
    assert g.status_code == 200
    assert g.json()["status"] == "queued"


async def test_cancel_sets_flag(client):
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nbody", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources", files=files, headers=h)).json()
    job_id = (
        await client.post(
            f"/api/v1/domains/{dom['id']}/ingest", json={"source_id": src["id"]}, headers=h
        )
    ).json()["job_id"]
    r = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 202


async def test_cancel_unknown_job_404(client):
    import uuid

    csrf = client.cookies.get("paw_csrf")
    r = await client.post(f"/api/v1/jobs/{uuid.uuid4()}/cancel", headers={"x-csrf-token": csrf})
    assert r.status_code == 404
