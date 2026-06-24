import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.services.api_keys import ApiKeyService


@pytest.fixture
async def issued(db_session):
    """Return (read_token, noscope_token, revoked_token) for a seeded user."""
    user = await UserRepo(db_session).create(email="k@b.c", pw_hash="x", role="admin")
    await db_session.commit()
    svc = ApiKeyService(db_session)
    read = await svc.issue(user_id=user.id, scopes=["read"])
    noscope = await svc.issue(user_id=user.id, scopes=[])
    revoked = await svc.issue(user_id=user.id, scopes=["read"])
    await svc.revoke(user_id=user.id, key_id=revoked.id)
    return read.token, noscope.token, revoked.token


@pytest.fixture
async def client(wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_missing_or_bad_key_401(client, issued):
    r = await client.post("/mcp", json={})
    assert r.status_code == 401
    r = await client.post("/mcp", json={}, headers={"Authorization": "Bearer paw_dead.beef"})
    assert r.status_code == 401


async def test_revoked_key_401(client, issued):
    _read, _noscope, revoked = issued
    r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {revoked}"})
    assert r.status_code == 401


async def test_valid_key_without_scope_403(client, issued):
    _read, noscope, _revoked = issued
    r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {noscope}"})
    assert r.status_code == 403
