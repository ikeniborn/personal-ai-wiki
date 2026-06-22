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


async def test_create_and_get_article(ctx):
    c, csrf, dom = ctx
    r = await c.post(
        f"/api/v1/domains/{dom}/articles",
        json={"slug": "quic", "title": "QUIC", "markdown": "# QUIC\n\n**fast**"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201
    art = r.json()
    assert art["current_rev"] == 1
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert g.status_code == 200
    assert "<h1>QUIC</h1>" in g.json()["html"]
    assert "<strong>fast</strong>" in g.json()["html"]


async def test_update_optimistic_lock_conflict(ctx):
    c, csrf, dom = ctx
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "tcp", "title": "TCP", "markdown": "# TCP"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    ok = await c.put(
        f"/api/v1/articles/{art['id']}",
        json={"title": "TCP", "markdown": "# TCP v2", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert ok.status_code == 200
    assert ok.json()["current_rev"] == 2
    stale = await c.put(
        f"/api/v1/articles/{art['id']}",
        json={"title": "TCP", "markdown": "# TCP v3", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert stale.status_code == 409


async def test_rollback_creates_new_revision(ctx):
    c, csrf, dom = ctx
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "tls", "title": "TLS", "markdown": "# TLS v1"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    await c.put(
        f"/api/v1/articles/{art['id']}",
        json={"title": "TLS", "markdown": "# TLS v2", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    rb = await c.post(
        f"/api/v1/articles/{art['id']}/rollback", json={"rev_no": 1}, headers={"x-csrf-token": csrf}
    )
    assert rb.status_code == 200
    assert rb.json()["current_rev"] == 3
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert "TLS v1" in g.json()["html"]
