import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.jobs import JobRepo
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
        yield c, csrf, dom["id"], db_session


async def test_domain_page_has_maintenance_actions(ctx):
    c, csrf, dom, _ = ctx
    html = (await c.get(f"/domains/{dom}")).text
    assert f"/domains/{dom}/lint" in html
    assert f"/domains/{dom}/format" in html
    assert f"/domains/{dom}/reindex" in html


async def test_web_lint_returns_job_drawer(ctx):
    c, csrf, dom, _ = ctx
    r = await c.post(f"/domains/{dom}/lint", data={}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert "sse-connect" in r.text  # the job drawer partial


async def test_lint_results_view_lists_issues_with_fix_form(ctx):
    c, csrf, dom, db_session = ctx
    # craft a finished lint job carrying one issue in its log
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=uuid.UUID(dom), kind="lint")
    await repo.append_log(
        job.id,
        {
            "step": "issues",
            "issues": [
                {
                    "id": "deadbeefdeadbeef",
                    "kind": "broken_ref",
                    "target_slug": "intro",
                    "detail": "broken wikilink [[ghost]]",
                    "fix": "remove it",
                }
            ],
        },
    )
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()

    html = (await c.get(f"/domains/{dom}/lint/{job.id}/results")).text
    assert "deadbeefdeadbeef" in html
    assert "broken_ref" in html
    assert f"/domains/{dom}/fix" in html  # the Fix form action
    assert 'name="issue_ids"' in html
