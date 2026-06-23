import json

import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_sync_json_shape(client, monkeypatch):
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")]),
    )
    r = await client.post(
        f"/api/v1/domains/{client._dom.id}/query",
        json={"q": "what is reliable?"},
        headers={"x-csrf-token": client._csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer_md"] == "reliable means [tcp]"
    assert any(ref["slug"] == "tcp" for ref in body["refs"])
    assert body["passages"] and body["passages"][0]["chunk_id"]


async def test_sse_streams_tokens(client, monkeypatch):
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(stream_tokens=["reli", "able"]),
    )
    r = await client.post(
        f"/api/v1/domains/{client._dom.id}/query",
        json={"q": "what is reliable?"},
        headers={"x-csrf-token": client._csrf, "accept": "text/event-stream"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "reli" in r.text and "able" in r.text
    assert '"status": "done"' in r.text or '"status":"done"' in r.text
    assert "tcp" in r.text  # refs delivered in the terminal event


async def test_query_response_shape_valid(client, monkeypatch):
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("answer [tcp]")]),
    )
    r = await client.post(
        f"/api/v1/domains/{client._dom.id}/query",
        json={"q": "what is reliable?"},
        headers={"x-csrf-token": client._csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer_md", "refs", "passages"}
