from httpx import ASGITransport, AsyncClient

from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def test_login_and_setup_pages_render(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as client:
        login = await client.get("/login")
        setup = await client.get("/setup")

    assert login.status_code == 200
    assert setup.status_code == 200


async def test_authenticated_domain_page_and_bulk_rejection(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="editor@example.com", pw_hash=hash_password("pw12345"), role="editor"
    )
    dom = await DomainRepo(db_session).create(name="web", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "editor@example.com", "password": "pw12345"}
        )
        assert login.status_code == 200
        csrf = client.cookies.get("paw_csrf")
        assert csrf

        page = await client.get(f"/domains/{dom.id}")
        rejected = await client.post(
            f"/domains/{dom.id}/sources/bulk",
            headers={"x-csrf-token": csrf},
            files={"file": ("not.zip", b"not a zip", "application/zip")},
        )

    assert page.status_code == 200
    assert "Bulk upload" in page.text
    assert rejected.status_code == 422
