import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
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


async def test_wikilink_renders_as_anchor_in_api_html(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="A", storage_ref="b:a")
    await db_session.commit()
    # article B references [[a]] -> must resolve to /articles/{a.id}
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "b", "title": "B", "markdown": "see [[a]]"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert f'href="/articles/{a.id}"' in g.json()["html"]


async def test_article_page_shows_meta_sections(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    # Create 'a' via API so get_body() can read a real blob from storage.
    a_resp = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "a", "title": "Alpha", "markdown": "# Alpha"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    a_id = uuid.UUID(a_resp["id"])
    b = await repo.create(domain_id=did, slug="b", title="Bravo", storage_ref="b:b")
    src = await SourceRepo(db_session).create(
        domain_id=did, storage_ref="b:s", filename="rfc.txt", type="md", checksum="x"
    )
    graph = GraphRepo(db_session)
    await graph.link(domain_id=did, src_article_id=b.id, dst_article_id=a_id, type="related")
    await graph.link(domain_id=did, src_article_id=a_id, dst_article_id=b.id, type="child")
    await CitationRepo(db_session).create(
        article_id=a_id, source_id=src.id, quote="reliable", locator="p1"
    )
    await db_session.commit()

    page = await c.get(f"/articles/{a_id}")
    assert page.status_code == 200
    assert "Backlinks" in page.text
    assert f'href="/articles/{b.id}"' in page.text  # backlink + outgoing both point at b
    assert "rfc.txt" in page.text  # citation source filename
    assert "Citations" in page.text
    assert f"/domains/{dom}/graph?root={a_id}" in page.text  # open-in-graph link
    assert 'id="tree-filter"' in page.text  # sidebar tree filter box


async def test_web_rollback_returns_hx_refresh(ctx):
    c, csrf, dom, db_session = ctx
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "tls", "title": "TLS", "markdown": "# v1"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    await c.put(
        f"/api/v1/articles/{art['id']}",
        json={"title": "TLS", "markdown": "# v2", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    rb = await c.post(
        f"/articles/{art['id']}/rollback",
        data={"rev_no": 1},
        headers={"x-csrf-token": csrf},
    )
    assert rb.status_code == 204
    assert rb.headers.get("HX-Refresh") == "true"
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert "v1" in g.json()["html"]
    assert g.json()["current_rev"] == 3
