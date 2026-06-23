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


async def test_lint_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/lint", headers={"x-csrf-token": csrf})
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])


async def test_lint_requires_csrf(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/lint")
    assert r.status_code == 403


async def test_fix_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(
        f"/api/v1/domains/{dom}/fix",
        json={"issue_ids": ["abc123"]},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])
