import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client, email, password):
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return client.cookies.get("paw_csrf")


@pytest.fixture
async def admin_client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def _make_user(c, csrf, email, role):
    r = await c.post(
        "/api/v1/users",
        json={"email": email, "password": "pw12345678901", "role": role},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_list_create_role_change_delete(admin_client):
    c = admin_client
    csrf = await _login(c, "admin@example.com", "pw12345")
    uid = await _make_user(c, csrf, "viewer@example.com", "viewer")

    listed = (await c.get("/api/v1/users")).json()
    assert any(u["id"] == uid and u["role"] == "viewer" for u in listed)

    r = await c.patch(
        f"/api/v1/users/{uid}", json={"role": "editor"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200 and r.json()["role"] == "editor"

    r = await c.request("DELETE", f"/api/v1/users/{uid}", headers={"x-csrf-token": csrf})
    assert r.status_code == 204
    assert all(u["id"] != uid for u in (await c.get("/api/v1/users")).json())


async def test_cannot_delete_last_admin(admin_client):
    c = admin_client
    csrf = await _login(c, "admin@example.com", "pw12345")
    me = [u for u in (await c.get("/api/v1/users")).json() if u["email"] == "admin@example.com"][0]
    r = await c.request("DELETE", f"/api/v1/users/{me['id']}", headers={"x-csrf-token": csrf})
    assert r.status_code == 409


async def test_cannot_demote_last_admin(admin_client):
    c = admin_client
    csrf = await _login(c, "admin@example.com", "pw12345")
    me = [u for u in (await c.get("/api/v1/users")).json() if u["email"] == "admin@example.com"][0]
    r = await c.patch(
        f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 409


async def test_role_change_requires_csrf(admin_client):
    c = admin_client
    await _login(c, "admin@example.com", "pw12345")
    uid = [u for u in (await c.get("/api/v1/users")).json()][0]["id"]
    r = await c.patch(f"/api/v1/users/{uid}", json={"role": "editor"})
    assert r.status_code == 403


async def test_non_admin_forbidden(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="editor@example.com", pw_hash=hash_password("pw12345"), role="editor"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "editor@example.com", "password": "pw12345"}
        )
        assert (await c.get("/api/v1/users")).status_code == 403
