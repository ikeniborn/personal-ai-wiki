import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(wired_settings, db_session):
    # clean up any leftover seed from a previous test run
    await db_session.execute(text("DELETE FROM users WHERE email = 'admin@example.com'"))
    await db_session.commit()
    # seed a user
    repo = UserRepo(db_session)
    await repo.create(email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin")
    await db_session.commit()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_login_sets_session_cookie(client):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
    )
    assert r.status_code == 200
    assert "paw_session" in r.cookies


async def test_login_wrong_password_401(client):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "WRONG"}
    )
    assert r.status_code == 401


async def test_logout_clears_session(client):
    await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
    )
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204
