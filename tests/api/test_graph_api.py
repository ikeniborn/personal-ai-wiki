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


async def _seed(db_session, domain_id):
    repo = ArticleRepo(db_session)
    did = uuid.UUID(domain_id)
    a = await repo.create(domain_id=did, slug="a", title="A", storage_ref="b:a", summary="sa")
    b = await repo.create(domain_id=did, slug="b", title="B", storage_ref="b:b", summary="sb")
    c = await repo.create(domain_id=did, slug="c", title="C", storage_ref="b:c", summary="sc")
    graph = GraphRepo(db_session)
    await graph.link(domain_id=did, src_article_id=a.id, dst_article_id=b.id, type="related")
    await graph.link(domain_id=did, src_article_id=a.id, dst_article_id=c.id, type="parent")
    await db_session.commit()
    return a, b, c


async def test_graph_requires_auth(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        r = await c.get(f"/api/v1/graph?domain={uuid.uuid4()}&root={uuid.uuid4()}")
    assert r.status_code == 401


async def test_graph_returns_nodes_and_edges(ctx):
    c, csrf, dom, db_session = ctx
    a, b, cc = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=1")
    assert r.status_code == 200
    data = r.json()
    assert data["root"] == str(a.id)
    assert {n["id"] for n in data["nodes"]} == {str(a.id), str(b.id), str(cc.id)}
    assert any(n["summary"] == "sa" for n in data["nodes"])
    assert {(e["src"], e["dst"], e["type"]) for e in data["edges"]} == {
        (str(a.id), str(b.id), "related"),
        (str(a.id), str(cc.id), "parent"),
    }


async def test_graph_type_filter_drops_edges(ctx):
    c, csrf, dom, db_session = ctx
    a, b, _c = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=2&types=related")
    data = r.json()
    assert {n["id"] for n in data["nodes"]} == {str(a.id), str(b.id)}
    assert [e["type"] for e in data["edges"]] == ["related"]


async def test_graph_clamps_depth_to_max(ctx):
    c, csrf, dom, db_session = ctx
    a, _b, _c = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=99")
    assert r.json()["depth"] == 4  # GraphConfig.max_depth default


async def test_graph_root_outside_domain_404(ctx):
    c, csrf, dom, db_session = ctx
    await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={uuid.uuid4()}")
    assert r.status_code == 404
