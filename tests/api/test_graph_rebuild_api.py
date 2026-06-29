import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def ctx(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await UserRepo(db_session).create(
        email="viewer@example.com", pw_hash=hash_password("pw12345"), role="viewer"
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


async def test_rebuild_graph_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/rebuild-graph", headers={"x-csrf-token": csrf})
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])


async def test_rebuild_graph_requires_csrf(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/rebuild-graph")
    assert r.status_code == 403


async def test_rebuild_graph_requires_admin_or_editor(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="viewer2@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        # login as admin first to create domain
        await c.post(
            "/api/v1/auth/login", json={"email": "viewer2@example.com", "password": "pw12345"}
        )
        csrf = c.cookies.get("paw_csrf")
        # viewer cannot access rebuild-graph
        dom_id = str(uuid.uuid4())
        r = await c.post(
            f"/api/v1/domains/{dom_id}/rebuild-graph", headers={"x-csrf-token": csrf}
        )
        assert r.status_code == 403
