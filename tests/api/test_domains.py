import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client: AsyncClient, email: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    csrf = client.cookies.get("paw_csrf")
    assert csrf is not None
    return csrf


@pytest.fixture
async def seeded(db_session):
    repo = UserRepo(db_session)
    await repo.create(email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin")
    await repo.create(email="viewer@example.com", pw_hash=hash_password("pw12345"), role="viewer")
    await db_session.commit()


@pytest.fixture
async def client(seeded, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_admin_creates_domain(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    assert r.json()["name"] == "net"


async def test_viewer_cannot_create_domain(client):
    csrf = await _login(client, "viewer@example.com", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 403


async def test_create_without_csrf_rejected(client):
    await _login(client, "admin@example.com", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"})
    assert r.status_code == 403


async def test_list_domains_paginates(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    for n in ("a", "b", "c"):
        await client.post("/api/v1/domains", json={"name": n}, headers={"x-csrf-token": csrf})
    r = await client.get("/api/v1/domains?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None


async def test_duplicate_domain_name_returns_409(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r1 = await client.post(
        "/api/v1/domains", json={"name": "duplicate"}, headers={"x-csrf-token": csrf}
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/domains", json={"name": "duplicate"}, headers={"x-csrf-token": csrf}
    )
    assert r2.status_code == 409
