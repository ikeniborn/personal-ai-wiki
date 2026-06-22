import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


@pytest.fixture
async def authed(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        csrf = c.cookies.get("paw_csrf")
        dom = (
            await c.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
        ).json()
        yield c, csrf, dom["id"]


async def test_setup_page_shown_when_no_users(client):
    r = await client.get("/")
    # first run redirects to setup
    assert r.status_code in (302, 307)
    assert "/setup" in r.headers["location"]


async def test_setup_then_dashboard(client):
    await client.post(
        "/api/v1/setup",
        json={
            "email": "admin@example.com",
            "password": "pw12345",
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
    )
    await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
    )
    r = await client.get("/")
    assert r.status_code == 200
    assert "Domains" in r.text or "domains" in r.text.lower()


async def test_domain_page_has_ingest_action(authed):
    c, csrf, dom = authed
    page = await c.get(f"/domains/{dom}")
    assert page.status_code == 200
    assert 'hx-post="/api/v1/domains/' in page.text
    assert 'id="job-drawer"' in page.text


async def test_settings_shows_dim_change_warning(authed):
    c, csrf, dom = authed
    page = await c.get("/settings")
    assert page.status_code == 200
    assert (
        "Changing the embedding dimension requires an ALTER + HNSW rebuild + reindex." in page.text
    )
