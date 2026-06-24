import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column, ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
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
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("**reliable** means [tcp]")]),
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._art = art  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_query_page_has_suggestions_wiring(client):
    r = await client.get(f"/domains/{client._dom.id}/query")
    assert r.status_code == 200
    assert "/suggest" in r.text and "keyup changed delay:300ms" in r.text


async def test_web_query_then_stale_badge_and_refresh(client, db_session):
    url = f"/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}
    # first query -> live answer, no stale badge
    r1 = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "<strong>reliable</strong>" in r1.text
    assert "may be outdated" not in r1.text
    # mark the cached entry stale (simulating an article edit on the dependency)
    from paw.services.cache_seam import mark_cache_stale
    await mark_cache_stale(db_session, domain_id=client._dom.id, article_ids=[client._art.id])
    await db_session.commit()
    # second identical query -> served from cache, now flagged + Refresh present
    r2 = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "may be outdated" in r2.text
    assert "refresh=1" in r2.text


async def test_web_suggest_returns_fragment(client, db_session):
    repo = QueryCacheRepo(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    cid = await repo.upsert(
        domain_id=client._dom.id, query_norm="tcp handshake", answer_md="A", refs=[],
        passages=[], model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await repo.touch(cache_id=cid)
    await db_session.commit()
    r = await client.get(f"/domains/{client._dom.id}/suggest", params={"q": "tcp"})
    assert r.status_code == 200
    assert "tcp handshake" in r.text
