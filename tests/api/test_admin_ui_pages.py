from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _client_for(db_session, email, role):
    await UserRepo(db_session).create(
        email=email, pw_hash=hash_password("pw12345"), role=role
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    await c.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    return c


async def test_admin_sees_users_and_apikeys_sections(db_session, wired_settings):
    c = await _client_for(db_session, "admin@example.com", "admin")
    try:
        page = await c.get("/settings")
        assert page.status_code == 200
        # api-keys management present and wired to the web route
        assert 'hx-post="/api-keys/issue"' in page.text
        assert 'hx-post="/api/v1/users"' in page.text
        # current admin's own row listed
        assert "admin@example.com" in page.text
    finally:
        await c.aclose()


async def test_editor_does_not_see_user_management(db_session, wired_settings):
    c = await _client_for(db_session, "editor@example.com", "editor")
    try:
        page = await c.get("/settings")
        assert page.status_code == 200
        # users management form is admin-only
        assert 'hx-post="/api/v1/users"' not in page.text
        # but api-keys are self-service for everyone
        assert 'hx-post="/api-keys/issue"' in page.text
    finally:
        await c.aclose()
