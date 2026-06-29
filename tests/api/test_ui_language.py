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
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        yield c, c.cookies.get("paw_csrf")


async def test_default_lang_is_en(client):
    c, _ = client
    r = await c.get("/")
    assert r.status_code == 200
    assert '<html lang="en">' in r.text


async def test_switch_to_ru_then_back(client):
    c, csrf = client
    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "ru"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"
    page = await c.get("/")
    assert '<html lang="ru">' in page.text
    # a RU-only string proves the catalog is wired, not just the lang attr
    assert "Домены" in page.text

    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "en"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 204
    assert '<html lang="en">' in (await c.get("/")).text


async def test_invalid_lang_rejected(client):
    c, csrf = client
    r = await c.post(
        "/api/v1/users/me/ui-language", json={"ui_language": "de"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 422


async def test_switch_requires_csrf(client):
    c, _ = client
    r = await c.post("/api/v1/users/me/ui-language", json={"ui_language": "ru"})
    assert r.status_code == 403
