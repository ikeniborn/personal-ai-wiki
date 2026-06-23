from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.chat as chat_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.ingest.chunking import ChunkSpec
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.chat import ChatService, auto_title
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


def test_auto_title_first_line_truncated():
    assert auto_title("  Hello world  \nsecond") == "Hello world"
    assert auto_title("x" * 100, max_len=10) == "x" * 10
    assert auto_title("   ") == "New chat"


async def _provision(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    user = await UserRepo(db_session).create(
        email="a@example.com", pw_hash=hash_password("pw12345"), role="viewer"
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
    return user, dom


async def test_first_turn_titles_and_persists(db_session, monkeypatch):
    user, dom = await _provision(db_session, monkeypatch)
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")]),
    )
    svc = ChatService(db_session, fernet_key=_FERNET)
    sess = await svc.resolve_session(user=user, domain_id=dom.id, session_id=None)
    prepared = await svc.prepare_turn(session=sess, question="what is reliable?")
    turn, usage = await svc.complete_turn(prepared)
    await svc.record_turn(
        session=sess, question="what is reliable?", answer_md=turn.answer_md, refs=turn.refs,
        model=prepared.model, prompt_version=prepared.prompt_version, usage=usage,
    )
    repo = ChatRepo(db_session)
    refreshed = await repo.get(sess.id)
    assert refreshed.title == "what is reliable?"
    msgs = await repo.list_messages(sess.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].meta["model"] == "m"
    assert msgs[1].meta["prompt_version"] == prepared.prompt_version
    assert any(r["slug"] == "tcp" for r in msgs[1].meta["refs"])


async def test_second_turn_sees_prior_context(db_session, monkeypatch):
    user, dom = await _provision(db_session, monkeypatch)
    captured: list[list] = []

    class _Capture(StubChatProvider):
        async def chat(self, messages, *, tools=None, model=None, json_mode=False):
            captured.append(list(messages))
            return StubChatProvider.text("ok [tcp]")

    monkeypatch.setattr(chat_mod, "build_chat_provider", lambda pc, b: _Capture())
    svc = ChatService(db_session, fernet_key=_FERNET)
    sess = await svc.resolve_session(user=user, domain_id=dom.id, session_id=None)
    for q in ("first question about tcp", "and the second one"):
        prepared = await svc.prepare_turn(session=sess, question=q)
        turn, usage = await svc.complete_turn(prepared)
        await svc.record_turn(
            session=sess, question=q, answer_md=turn.answer_md, refs=turn.refs,
            model=prepared.model, prompt_version=prepared.prompt_version, usage=usage,
        )
    # second call's user message must carry the first turn folded in
    second_user = captured[1][1].content
    assert "first question about tcp" in second_user
    assert "<<THREAD" in second_user


async def test_get_owned_rejects_other_user(db_session, monkeypatch):
    from paw.api.errors import ProblemError
    user, dom = await _provision(db_session, monkeypatch)
    other = await UserRepo(db_session).create(
        email="b@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    await db_session.commit()
    svc = ChatService(db_session, fernet_key=_FERNET)
    sess = await svc.resolve_session(user=user, domain_id=dom.id, session_id=None)
    try:
        await svc.get_owned(session_id=sess.id, user_id=other.id)
        raise AssertionError("expected ProblemError")
    except ProblemError as e:
        assert e.status == 404


async def test_empty_domain_turn_is_dont_know(db_session, monkeypatch):
    user, dom = await _provision(db_session, monkeypatch)
    empty = await DomainRepo(db_session).create(name="empty", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    monkeypatch.setattr(
        chat_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("never called")]),
    )
    svc = ChatService(db_session, fernet_key=_FERNET)
    sess = await svc.resolve_session(user=user, domain_id=empty.id, session_id=None)
    prepared = await svc.prepare_turn(session=sess, question="totally unrelated")
    assert prepared.messages is None
    turn, usage = await svc.complete_turn(prepared)
    from paw.harness.ops.query import DONT_KNOW
    assert turn.answer_md == DONT_KNOW and turn.refs == [] and usage == {}
