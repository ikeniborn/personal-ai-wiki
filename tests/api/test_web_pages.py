import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_setup_page_shown_when_no_users(client):
    r = await client.get("/")
    # first run redirects to setup
    assert r.status_code in (302, 307)
    assert "/setup" in r.headers["location"]


async def test_setup_then_dashboard(client):
    await client.post("/api/v1/setup", json={"email": "admin@example.com", "password": "pw12345"})
    await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
    )
    r = await client.get("/")
    assert r.status_code == 200
    assert "Domains" in r.text or "domains" in r.text.lower()
