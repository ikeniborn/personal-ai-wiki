import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.articles import ArticleRepo
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


async def test_domain_page_renders_tree_sidebar_and_graph_button(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=did, slug="b", title="Bravo", storage_ref="b:b")
    await GraphRepo(db_session).link(
        domain_id=did, src_article_id=a.id, dst_article_id=b.id, type="child"
    )
    await db_session.commit()

    page = await c.get(f"/domains/{dom}")
    assert page.status_code == 200
    assert 'id="tree-filter"' in page.text  # tree sidebar replaced the flat list
    assert 'class="tree"' in page.text
    assert f'href="/domains/{dom}/graph"' in page.text  # graph button


async def test_graph_page_has_canvas_and_vendored_scripts(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    await repo.create(domain_id=did, slug="ga", title="GA", storage_ref="b:ga")
    await db_session.commit()

    page = await c.get(f"/domains/{dom}/graph")
    assert page.status_code == 200
    assert 'id="cy"' in page.text
    assert "cytoscape.min.js" in page.text
    assert "graph.js" in page.text


async def test_graph_static_assets_served(ctx):
    c, csrf, dom, db_session = ctx

    r_cy = await c.get("/static/cytoscape.min.js")
    assert r_cy.status_code == 200

    r_g = await c.get("/static/graph.js")
    assert r_g.status_code == 200


async def test_graph_page_root_defaults_to_first_article(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="first", title="First", storage_ref="b:f")
    await db_session.commit()

    page = await c.get(f"/domains/{dom}/graph")
    assert page.status_code == 200
    # root article id should appear in the page as data-root attribute
    assert str(a.id) in page.text
