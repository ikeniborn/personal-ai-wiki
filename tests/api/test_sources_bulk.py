import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "pw12345"},
        )
        yield c


async def test_bulk_upload_registers_sources_and_starts_jobs(client, monkeypatch):
    async def fake_enqueue(_ctx, *, job_id, domain_id, source_id=None, topic=None):
        return None

    monkeypatch.setattr("paw.services.jobs.enqueue_ingest", fake_enqueue)
    csrf = client.cookies.get("paw_csrf")
    dom = (
        await client.post(
            "/api/v1/domains", json={"name": "bulk"}, headers={"x-csrf-token": csrf}
        )
    ).json()

    files = {
        "file": (
            "sources.zip",
            _zip({"a.md": b"# A\n\nbody a", "b.txt": b"body b", "skip.exe": b"MZ"}),
            "application/zip",
        )
    }
    r = await client.post(
        f"/api/v1/domains/{dom['id']}/sources/bulk",
        files=files,
        headers={"x-csrf-token": csrf},
    )

    assert r.status_code == 201
    body = r.json()
    assert {s["filename"] for s in body["sources"]} == {"a.md", "b.txt"}
    assert len(body["job_ids"]) == len(body["sources"])


async def test_bulk_upload_rejects_zip_bomb(client):
    csrf = client.cookies.get("paw_csrf")
    dom = (
        await client.post(
            "/api/v1/domains", json={"name": "bomb"}, headers={"x-csrf-token": csrf}
        )
    ).json()
    files = {
        "file": (
            "bomb.zip",
            _zip({"z.bin": b"\x00" * 5_000_000}),
            "application/zip",
        )
    }

    r = await client.post(
        f"/api/v1/domains/{dom['id']}/sources/bulk",
        files=files,
        headers={"x-csrf-token": csrf},
    )

    assert r.status_code == 422
