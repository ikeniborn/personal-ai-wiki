import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.chat as chat_mod
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
    monkeypatch.setattr(chat_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("**reliable** means [tcp]")]),
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_chat_page_renders(client):
    r = await client.get("/chat")
    assert r.status_code == 200
    assert "name=\"q\"" in r.text or "name='q'" in r.text


async def test_web_post_creates_session_and_renders_answer(client):
    r = await client.post(
        "/chat",
        data={"q": "what is reliable?", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf},
    )
    assert r.status_code == 200
    assert "<strong>reliable</strong>" in r.text  # markdown rendered + sanitized
    assert "tcp" in r.text  # source chip
    assert "session_id" in r.text  # OOB hidden input for follow-up turns


async def test_session_page_shows_prior_messages(client):
    await client.post(
        "/chat",
        data={"q": "what is reliable?", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf},
    )
    sessions = (await client.get("/api/v1/chat/sessions")).json()["items"]
    sid = sessions[0]["id"]
    page = await client.get(f"/chat/{sid}")
    assert page.status_code == 200
    assert "what is reliable?" in page.text
    assert "<strong>reliable</strong>" in page.text
