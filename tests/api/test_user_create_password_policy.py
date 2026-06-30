from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _admin_client(db_session):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    login = await c.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "pw12345678901"},
    )
    assert login.status_code == 200
    assert c.cookies.get("paw_csrf") is not None
    return c


async def test_create_user_rejects_weak_password(db_session, wired_settings):
    c = await _admin_client(db_session)
    try:
        csrf = c.cookies.get("paw_csrf")
        resp = await c.post(
            "/api/v1/users",
            headers={"x-csrf-token": csrf},
            json={"email": "new@example.com", "password": "short", "role": "viewer"},
        )
        assert resp.status_code == 422
    finally:
        await c.aclose()
