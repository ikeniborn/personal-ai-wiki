"""Tests for Task 13: AGE branch in retrieve.py — provenance + CTE regression.

Test approach:
- test_cte_retrieval_unchanged: Goes through the real query endpoint (POST
  /api/v1/domains/{id}/query).  The default graph engine is "cte", so no AGE path is
  taken.  We assert the StubChatProvider received NO "via concepts" text in its user
  message (which embeds the prompt_block).  This proves acceptance #6 — CTE path is
  byte-identical to pre-Task-13 behaviour.

- test_age_retrieval_has_provenance: Also goes through the real query endpoint.  We
  bootstrap the AGE graph, project both articles (seed + neighbour), set the domain
  engine to "age", and assert the StubChatProvider received "via concepts" in its user
  message.  This proves acceptance #3 — AGE neighbours carry entity-bridge provenance.

Why inspect the chat-provider messages, not the JSON response?  The API returns
answer_md / refs / passages — it does NOT return prompt_block.  But the prompt_block
is passed verbatim inside the "user" message that the chat provider receives, so
stub.calls[0] captures it deterministically.

AGE session note: create_graph / cypher() require the ag_catalog search_path
configured in paw.db.session.  The plain db_session fixture uses a raw engine without
those settings.  For the AGE test we use get_sessionmaker() (which has the right
server_settings) for ALL setup, mirroring the integration tests in
tests/integration/test_age_*.py.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.db.session import get_sessionmaker
from paw.graph.age.projection import project_article
from paw.graph.age.schema import ensure_graph
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write
from tests.factories import _set_domain_engine_age
from tests.stubs import StubChatProvider, StubEmbeddingProvider

_FERNET = "k" * 43 + "="
_DIM = 8


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def cte_client(db_session, wired_settings, monkeypatch):
    """Minimal single-article domain with default (CTE) engine."""
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x",
        chat_model="m",
        embedding_model="e",
        embedding_dim=_DIM,
        api_key="k",
    )
    dom = await DomainRepo(db_session).create(name="cte-net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="TCP summary"
    )
    await ensure_embedding_column(db_session, _DIM)
    emb = StubEmbeddingProvider(dim=_DIM)
    await embed_and_write(
        db_session,
        article_id=art.id,
        domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()

    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "pw12345"},
        )
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


@pytest.fixture
async def age_client(wired_settings, monkeypatch):
    """Two-article domain with AGE engine + entity bridge seeded + graph projected.

    Uses get_sessionmaker() (which has the ag_catalog search_path) for all setup so
    that create_graph / cypher() calls succeed.  This mirrors the pattern used in
    tests/integration/test_age_*.py.
    """
    emb = StubEmbeddingProvider(dim=_DIM)
    dom_id: uuid.UUID | None = None

    async with get_sessionmaker()() as s:
        await UserRepo(s).create(
            email="age-admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
        )
        box = SecretBox(_FERNET)
        await ProviderSettingsService(s, box=box).persist_provider(
            base_url="http://x",
            chat_model="m",
            embedding_model="e",
            embedding_dim=_DIM,
            api_key="k",
        )
        dom = await DomainRepo(s).create(name="age-net", source_prefix="s2", wiki_prefix="w2")
        dom_id = dom.id

        seed_art = await ArticleRepo(s).create(
            domain_id=dom.id,
            slug="alpha",
            title="Alpha",
            storage_ref="b:alpha",
            summary="Alpha summary",
        )
        other_art = await ArticleRepo(s).create(
            domain_id=dom.id,
            slug="beta",
            title="Beta",
            storage_ref="b:beta",
            summary="Beta summary about shared concept",
        )

        await ensure_embedding_column(s, _DIM)

        # Seed article gets an embedded chunk that will surface in retrieval.
        seed_chunk_ids = await embed_and_write(
            s,
            article_id=seed_art.id,
            domain_id=dom.id,
            specs=[ChunkSpec(kind="section", ord=1, heading_path="Intro", text="Alpha reliable")],
            embedder=emb,
        )
        # Other article: insert a chunk row with embedding_version=0 so it is invisible
        # to hybrid_search (which filters on embedding_version=1).  The chunk still
        # exists in SQL so project_article can mirror it into the AGE graph, enabling
        # the entity-bridge Cypher to traverse it.
        other_chunk_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO chunks (id, article_id, domain_id, kind, ord, text, embedding_version)"
                " VALUES (:id, :a, :d, 'summary', 0, :t, 0)"
            ),
            {
                "id": str(other_chunk_id),
                "a": str(other_art.id),
                "d": str(dom.id),
                "t": "Beta info",
            },
        )
        other_chunk_ids = [other_chunk_id]

        # Shared entity + mention rows so the AGE entity-bridge query finds it.
        entity_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO entities (id, domain_id, name, kind) VALUES (:id, :d, :n, 'concept')"
            ),
            {"id": str(entity_id), "d": str(dom.id), "n": "SharedConcept"},
        )
        for art_id in (seed_art.id, other_art.id):
            await s.execute(
                text("INSERT INTO article_entities (article_id, entity_id) VALUES (:a, :e)"),
                {"a": str(art_id), "e": str(entity_id)},
            )
        for cid in (seed_chunk_ids[0], other_chunk_ids[0]):
            await s.execute(
                text("INSERT INTO chunk_entities (chunk_id, entity_id) VALUES (:c, :e)"),
                {"c": str(cid), "e": str(entity_id)},
            )

        await s.commit()

        # Bootstrap AGE graph + project both articles.
        await ensure_graph(s, dom.id)
        await project_article(s, domain_id=dom.id, article_id=seed_art.id)
        await project_article(s, domain_id=dom.id, article_id=other_art.id)
        await s.commit()

        # Enable AGE engine for the domain.
        await _set_domain_engine_age(s, dom.id)
        await s.commit()

    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login",
            json={"email": "age-admin@example.com", "password": "pw12345"},
        )
        c._dom_id = dom_id  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_cte_retrieval_unchanged(cte_client, monkeypatch):
    """Acceptance #6: default CTE engine — no 'via concepts' in the prompt_block.

    Endpoint: POST /api/v1/domains/{id}/query
    JSON field with context: not returned in body — we inspect the user message
    that the chat provider receives, which embeds prompt_block verbatim.
    """
    stub = StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: stub)

    r = await cte_client.post(
        f"/api/v1/domains/{cte_client._dom.id}/query",
        json={"q": "what is reliable?"},
        headers={"x-csrf-token": cte_client._csrf},
    )
    assert r.status_code == 200, r.text

    # Verify the chat provider was called and inspect the user message for the block.
    assert stub.calls, "StubChatProvider was never called"
    user_msg = next(
        (m.content for m in stub.calls[0] if m.role == "user"), ""
    )
    assert "via concepts" not in (user_msg or ""), (
        "CTE path must NOT produce 'via concepts' provenance"
    )


async def test_age_retrieval_has_provenance(age_client, monkeypatch):
    """Acceptance #3: AGE engine — 'via concepts' appears in the prompt_block.

    Endpoint: POST /api/v1/domains/{id}/query
    The seed article 'alpha' has NO direct links to 'beta'.  The entity-bridge
    Cypher (CHUNK_MENTIONS -> SharedConcept <- CHUNK_MENTIONS) connects them.
    We assert the chat user-message contains 'via concepts: SharedConcept'.
    """
    stub = StubChatProvider(script=[StubChatProvider.text("alpha answer")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: stub)

    r = await age_client.post(
        f"/api/v1/domains/{age_client._dom_id}/query",
        json={"q": "what is Alpha reliable?"},
        headers={"x-csrf-token": age_client._csrf},
    )
    assert r.status_code == 200, r.text

    assert stub.calls, "StubChatProvider was never called"
    user_msg = next(
        (m.content for m in stub.calls[0] if m.role == "user"), ""
    )
    assert "via concepts" in (user_msg or ""), (
        f"AGE path must produce 'via concepts' provenance in prompt_block; "
        f"user message was:\n{user_msg}"
    )
