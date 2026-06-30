import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app

_STRONG_PASSWORD = "pw12345678901"


@pytest.fixture
async def client(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_full_walking_skeleton(client):
    # 1. first run -> setup
    assert (await client.get("/api/v1/setup/status")).json()["needs_setup"] is True
    # 2. create admin
    r = await client.post(
        "/api/v1/setup",
        json={
            "email": "admin@example.com",
            "password": _STRONG_PASSWORD,
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
    )
    assert r.status_code == 201
    # 3. login
    await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _STRONG_PASSWORD},
    )
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    # 4. create domain
    dom = (await client.post("/api/v1/domains", json={"name": "networking"}, headers=h)).json()
    # 5. upload md source
    files = {"file": ("intro.md", b"# Intro\n\nQUIC over UDP.", "text/markdown")}
    s = await client.post(f"/api/v1/domains/{dom['id']}/sources", files=files, headers=h)
    assert s.status_code == 201
    # 6. manually author an article
    art = await client.post(
        f"/api/v1/domains/{dom['id']}/articles",
        json={"slug": "quic", "title": "QUIC", "markdown": "# QUIC\n\n**fast** transport"},
        headers=h,
    )
    assert art.status_code == 201
    aid = art.json()["id"]
    # 7. render sanitized
    page = await client.get(f"/api/v1/articles/{aid}")
    assert "<h1>QUIC</h1>" in page.json()["html"]
    assert "<strong>fast</strong>" in page.json()["html"]
    # 8. web article page renders with edit form + 409 conflict banner element
    web = await client.get(f"/articles/{aid}")
    assert web.status_code == 200
    assert "QUIC" in web.text
    assert 'id="conflict-banner"' in web.text
    assert 'hx-put="/api/v1/articles/' in web.text
