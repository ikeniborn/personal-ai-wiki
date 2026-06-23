import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.chat as chat_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chat import ChatRepo
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
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_chat_sync_creates_session_and_answers(client, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")]),
    )
    r = await client.post(
        "/api/v1/chat",
        json={"q": "what is reliable?", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer_md"] == "reliable means [tcp]"
    assert body["session_id"]
    assert any(ref["slug"] == "tcp" for ref in body["refs"])


async def test_chat_sse_streams_and_persists(client, db_session, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(stream_tokens=["reli", "able"]),
    )
    r = await client.post(
        "/api/v1/chat",
        json={"q": "what is reliable?", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf, "accept": "text/event-stream"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "reli" in r.text and "able" in r.text
    assert '"status": "done"' in r.text
    assert "tcp" in r.text  # refs in terminal event
    # assistant turn persisted with refs in meta
    admin = await UserRepo(db_session).get_by_email("admin@example.com")
    sessions = await ChatRepo(db_session).list_by_user(admin.id, limit=10)
    assert sessions
    msgs = await ChatRepo(db_session).list_messages(sessions[0].id)
    assert msgs[-1].role == "assistant"
    assert msgs[-1].content == "reliable"
    assert any(ref["slug"] == "tcp" for ref in msgs[-1].meta["refs"])


async def test_sessions_list_cursor_and_detail(client, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("a [tcp]")]),
    )
    ids = []
    for _ in range(2):
        r = await client.post(
            "/api/v1/chat",
            json={"q": "hello tcp", "domain_id": str(client._dom.id)},
            headers={"x-csrf-token": client._csrf},
        )
        ids.append(r.json()["session_id"])
    page = await client.get("/api/v1/chat/sessions?limit=1")
    body = page.json()
    assert len(body["items"]) == 1 and body["next_cursor"]
    page2 = await client.get(f"/api/v1/chat/sessions?limit=1&cursor={body['next_cursor']}")
    assert len(page2.json()["items"]) == 1
    detail = await client.get(f"/api/v1/chat/{ids[0]}")
    dbody = detail.json()
    assert dbody["id"] == ids[0]
    assert [m["role"] for m in dbody["messages"]] == ["user", "assistant"]


async def test_cross_user_denied(client, db_session, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("a [tcp]")]),
    )
    r = await client.post(
        "/api/v1/chat",
        json={"q": "hello tcp", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf},
    )
    sid = r.json()["session_id"]
    # second user logs in on the same client (swaps session + csrf cookies)
    await UserRepo(db_session).create(
        email="b@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    await db_session.commit()
    await client.post(
        "/api/v1/auth/login", json={"email": "b@example.com", "password": "pw12345"}
    )
    csrf_b = client.cookies.get("paw_csrf", "")
    assert (await client.get(f"/api/v1/chat/{sid}")).status_code == 404
    assert (
        await client.request("DELETE", f"/api/v1/chat/{sid}", headers={"x-csrf-token": csrf_b})
    ).status_code == 404


async def test_delete_session(client, monkeypatch):
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("a [tcp]")]),
    )
    r = await client.post(
        "/api/v1/chat",
        json={"q": "hello tcp", "domain_id": str(client._dom.id)},
        headers={"x-csrf-token": client._csrf},
    )
    sid = r.json()["session_id"]
    d = await client.request(
        "DELETE", f"/api/v1/chat/{sid}", headers={"x-csrf-token": client._csrf}
    )
    assert d.status_code == 204
    assert (await client.get(f"/api/v1/chat/{sid}")).status_code == 404
