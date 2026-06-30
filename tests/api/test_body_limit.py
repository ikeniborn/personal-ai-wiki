from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _admin_client(db_session: AsyncSession) -> AsyncClient:
    await UserRepo(db_session).create(
        email="a@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    await c.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "pw12345678901"},
    )
    return c


async def test_oversized_upload_rejected_with_413(
    db_session: AsyncSession, wired_settings: object, monkeypatch: MonkeyPatch
) -> None:
    from paw.config import get_settings

    monkeypatch.setattr(get_settings(), "max_request_bytes", 1024, raising=False)
    c = await _admin_client(db_session)
    try:
        csrf = c.cookies.get("paw_csrf")
        dom = (
            await c.post(
                "/api/v1/domains",
                json={"name": "d"},
                headers={"x-csrf-token": csrf},
            )
        ).json()
        big = b"x" * 4096
        resp = await c.post(
            f"/api/v1/domains/{dom['id']}/sources",
            headers={"x-csrf-token": csrf},
            files={"file": ("big.md", big, "text/markdown")},
        )
        assert resp.status_code == 413
    finally:
        await c.aclose()
