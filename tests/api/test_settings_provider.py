import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.managed import embedding_dim
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.services.provider_settings import ProviderSettingsService


@pytest.fixture
async def ctx(db_session, wired_settings):
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
        yield c, csrf


async def test_set_provider_connection(ctx, db_session):
    c, csrf = ctx
    r = await c.post(
        "/api/v1/settings/provider",
        json={
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 204
    assert await embedding_dim(db_session) == 8
    svc = ProviderSettingsService(db_session)
    assert await svc.get_provider() is not None


async def test_provider_dim_change_rebuilds(ctx, db_session):
    c, csrf = ctx
    r1 = await c.post(
        "/api/v1/settings/provider",
        json={
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
        headers={"x-csrf-token": csrf},
    )
    assert r1.status_code == 204

    r2 = await c.post(
        "/api/v1/settings/provider",
        json={
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 16,
        },
        headers={"x-csrf-token": csrf},
    )
    assert r2.status_code == 204
    assert await embedding_dim(db_session) == 16


async def test_provider_requires_csrf(ctx):
    c, csrf = ctx
    r = await c.post(
        "/api/v1/settings/provider",
        json={
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
    )
    assert r.status_code == 403
