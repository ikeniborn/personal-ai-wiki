import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, wired_settings):
    # no users seeded -> needs setup
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_first_run_needs_setup(client):
    r = await client.get("/api/v1/setup/status")
    assert r.status_code == 200
    assert r.json()["needs_setup"] is True


_SETUP_BODY = {
    "email": "admin@example.com",
    "password": "pw12345678901",
    "base_url": "https://api.example/v1",
    "api_key": "sk-x",
    "chat_model": "gpt-x",
    "embedding_model": "emb-x",
    "embedding_dim": 8,
}


async def test_complete_setup_creates_admin(client):
    r = await client.post("/api/v1/setup", json=_SETUP_BODY)
    assert r.status_code == 201
    assert r.json()["role"] == "admin"
    # second call rejected
    r2 = await client.post("/api/v1/setup", json=_SETUP_BODY)
    assert r2.status_code == 409
    status = await client.get("/api/v1/setup/status")
    assert status.json()["needs_setup"] is False


async def test_setup_captures_dim_and_creates_vector_column(client, db_session):
    r = await client.post(
        "/api/v1/setup",
        json={
            "email": "admin@example.com",
            "password": "pw12345678901",
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
    )
    assert r.status_code == 201
    from paw.db.managed import embedding_dim

    assert await embedding_dim(db_session) == 8
