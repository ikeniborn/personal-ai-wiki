import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.ingest import run_ingest
from paw.main import create_app
from paw.providers.config import WikiConfig
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService

_FERNET = "k" * 43 + "="


def _ingest_chat(slug: str, title: str) -> StubChatProvider:
    # extraction then drafting; two shared entities (TCP, IP) -> co-occurrence link on 2nd ingest
    extraction = {"entities": ["TCP", "IP"], "key_points": ["reliable delivery"]}
    draft = {
        "slug": slug,
        "title": title,
        "summary": f"{title} summary",
        "markdown": f"## Overview\n{title} relies on TCP and IP.",
        "entities": ["TCP", "IP"],
        "citations": [{"quote": "reliable delivery", "locator": None}],
    }
    payloads = iter([extraction, draft])

    def responder(messages, tools):
        return StubChatProvider.tool("emit_result", next(payloads))

    return StubChatProvider(responder=responder)


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
        yield c, csrf, db_session


async def test_graph_editing_roundtrip(ctx):
    c, csrf, db_session = ctx
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)

    # Ingest two articles sharing >= hub_threshold(=2) entities -> a "related" link a2 -> a1.
    r1 = await run_ingest(
        db_session, domain_id=dom.id, source_md="# TCP\n\nreliable delivery",
        chat=_ingest_chat("tcp", "TCP"), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    r2 = await run_ingest(
        db_session, domain_id=dom.id, source_md="# IP\n\naddressing",
        chat=_ingest_chat("ip", "IP"), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    await db_session.commit()
    a1, a2 = r1.article_id, r2.article_id

    # Seed a typed parent/child link + a sourced citation to exercise tree + citation join.
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="rfc793.txt", type="md", checksum="z"
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=a1, dst_article_id=a2, type="child"
    )
    await CitationRepo(db_session).create(
        article_id=a1, source_id=src.id, quote="reliable", locator="p1"
    )
    await db_session.commit()

    # 1) Graph shows the co-occurrence + child links around a1.
    g = await c.get(f"/api/v1/graph?domain={dom.id}&root={a1}&depth=1")
    assert g.status_code == 200
    data = g.json()
    assert {n["id"] for n in data["nodes"]} == {str(a1), str(a2)}
    assert {(e["src"], e["dst"], e["type"]) for e in data["edges"]} == {
        (str(a2), str(a1), "related"),
        (str(a1), str(a2), "child"),
    }

    # 2) Article page: a1 has a backlink from a2 (related), a child link to a2, sourced citation.
    page = await c.get(f"/articles/{a1}")
    assert page.status_code == 200
    assert f'href="/articles/{a2}"' in page.text
    assert "rfc793.txt" in page.text
    assert "Backlinks" in page.text

    # 3) Sidebar tree nests a2 (child) under a1.
    assert 'class="tree"' in page.text
    assert "TCP" in page.text and "IP" in page.text

    # 4) Edit a1 (new revision) then roll back to rev 1, and confirm 409 on a stale write.
    put = await c.put(
        f"/api/v1/articles/{a1}",
        json={"title": "TCP", "markdown": "## Overview\nedited body", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert put.status_code == 200 and put.json()["current_rev"] == 2

    stale = await c.put(
        f"/api/v1/articles/{a1}",
        json={"title": "TCP", "markdown": "## Overview\nv3", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert stale.status_code == 409  # optimistic lock holds

    rb = await c.post(
        f"/articles/{a1}/rollback", data={"rev_no": 1}, headers={"x-csrf-token": csrf}
    )
    assert rb.status_code == 204 and rb.headers.get("HX-Refresh") == "true"

    after = await c.get(f"/api/v1/articles/{a1}")
    assert after.json()["current_rev"] == 3
    assert "TCP and IP" in after.json()["html"]  # original rev-1 body restored
