import pytest
from httpx import ASGITransport, AsyncClient

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
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "pw12345"},
        )
        yield c


async def test_upload_md_source(client):
    csrf = client.cookies.get("paw_csrf")
    dom = (
        await client.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
    ).json()
    files = {"file": ("intro.md", b"# Intro\n\nbody", "text/markdown")}
    r = await client.post(
        f"/api/v1/domains/{dom['id']}/sources", files=files, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 201
    assert r.json()["filename"] == "intro.md"


async def test_upload_rejects_exe(client):
    csrf = client.cookies.get("paw_csrf")
    dom = (
        await client.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
    ).json()
    files = {"file": ("x.exe", b"MZbinary", "application/octet-stream")}
    r = await client.post(
        f"/api/v1/domains/{dom['id']}/sources", files=files, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 422
