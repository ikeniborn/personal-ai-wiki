import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client: AsyncClient, email: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    csrf = client.cookies.get("paw_csrf")
    assert csrf is not None
    return csrf


@pytest.fixture
async def seeded(db_session):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()


@pytest.fixture
async def client(seeded, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_issue_list_revoke_roundtrip(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.post(
        "/api/v1/api-keys", json={"scopes": ["read"]}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("paw_")
    key_id = body["id"]

    r = await client.get("/api/v1/api-keys")
    assert r.status_code == 200
    listed = r.json()
    assert any(k["id"] == key_id for k in listed)
    assert all("key" not in k and "hash" not in k for k in listed)  # secret never returned

    r = await client.request(
        "DELETE", f"/api/v1/api-keys/{key_id}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 204


async def test_issue_requires_csrf(client):
    await _login(client, "admin@example.com", "pw12345")
    r = await client.post("/api/v1/api-keys", json={"scopes": ["read"]})
    assert r.status_code == 403


async def test_issue_rejects_unknown_scope(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.post(
        "/api/v1/api-keys", json={"scopes": ["write"]}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 422


async def test_revoke_unknown_returns_404(client):
    import uuid

    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.request(
        "DELETE", f"/api/v1/api-keys/{uuid.uuid4()}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 404


async def test_anonymous_cannot_list(client):
    r = await client.get("/api/v1/api-keys")
    assert r.status_code == 401
