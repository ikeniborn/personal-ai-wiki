import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
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


class FixedEmbed:
    def __init__(self, default): self.default = default
    async def embed(self, texts, *, model=None): return [self.default for _ in texts]


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="a@b.c", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    emb = FixedEmbed([1.0, 0.0, 0.0, 0.0])
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_second_identical_query_served_from_cache(client, monkeypatch):
    calls = {"n": 0}

    def make_chat(pc, b):
        calls["n"] += 1
        return StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")])

    monkeypatch.setattr(query_mod, "build_chat_provider", make_chat)
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}

    r1 = await client.post(url, json={"q": "what is reliable?"}, headers=h)
    assert r1.status_code == 200 and r1.json()["cached"] is False
    r2 = await client.post(url, json={"q": "WHAT is   reliable? "}, headers=h)
    body = r2.json()
    assert body["cached"] is True and body["stale"] is False
    assert body["answer_md"] == "reliable means [tcp]"
    assert calls["n"] == 1  # the LLM provider was built/called exactly once


async def test_refresh_bypasses_and_recomputes(client, monkeypatch):
    answers = iter(["first [tcp]", "second [tcp]"])

    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text(next(answers))]),
    )
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}

    await client.post(url, json={"q": "q?"}, headers=h)                 # caches "first"
    cached = await client.post(url, json={"q": "q?"}, headers=h)
    assert cached.json()["answer_md"] == "first [tcp]"
    refreshed = await client.post(url + "?refresh=1", json={"q": "q?"}, headers=h)
    assert refreshed.json()["answer_md"] == "second [tcp]"
    assert refreshed.json()["cached"] is False
    again = await client.post(url, json={"q": "q?"}, headers=h)
    assert again.json()["answer_md"] == "second [tcp]"  # refreshed value now cached
