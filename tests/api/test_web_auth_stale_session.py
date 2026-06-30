import uuid

from httpx import ASGITransport, AsyncClient

from paw.api.deps import SESSION_COOKIE, get_session_store
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def test_deleted_user_session_is_rejected_and_evicted(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="gone@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="https://t", follow_redirects=False
    )
    try:
        await c.post(
            "/api/v1/auth/login",
            json={"email": "gone@example.com", "password": "pw12345678901"},
        )
        sid = c.cookies.get(SESSION_COOKIE)
        assert sid
        await UserRepo(db_session).delete(user.id)
        await db_session.commit()

        resp = await c.get("/")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"
        assert await get_session_store().get(sid) is None
    finally:
        await c.aclose()


async def test_deleted_user_session_redirects_from_suggest_route(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="suggest-gone@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="https://t", follow_redirects=False
    )
    try:
        await c.post(
            "/api/v1/auth/login",
            json={"email": "suggest-gone@example.com", "password": "pw12345678901"},
        )
        sid = c.cookies.get(SESSION_COOKIE)
        assert sid
        await UserRepo(db_session).delete(user.id)
        await db_session.commit()

        resp = await c.get(f"/domains/{uuid.uuid4()}/suggest")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"
        assert await get_session_store().get(sid) is None
    finally:
        await c.aclose()
