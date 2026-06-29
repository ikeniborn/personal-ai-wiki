import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_login_page_renders_frame(client):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "Personal AI Wiki" in r.text
    # CSP header present, no inline script allowed
    assert "content-security-policy" in {k.lower() for k in r.headers}
    assert "script-src 'self'" in r.headers["content-security-policy"]


async def test_static_htmx_served(client):
    r = await client.get("/static/htmx.min.js")
    assert r.status_code == 200


async def test_static_json_enc_served(client):
    r = await client.get("/static/json-enc.js")
    assert r.status_code == 200


async def test_login_page_loads_json_enc_extension(client):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "json-enc.js" in r.text
