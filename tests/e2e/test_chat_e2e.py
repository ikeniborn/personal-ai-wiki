from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.chat as chat_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.harness.ops.ingest import run_ingest
from paw.providers.config import WikiConfig
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.chat import ChatService
from paw.services.provider_settings import ProviderSettingsService

_FERNET = "k" * 43 + "="

_SOURCE = (
    "# TCP\n\nTransmission Control Protocol provides reliable, ordered delivery of a "
    "byte stream between applications. It uses sequence numbers and acknowledgements."
)


def _ingest_chat() -> StubChatProvider:
    extraction = {"entities": ["TCP"], "key_points": ["reliable ordered delivery"]}
    draft = {
        "slug": "tcp", "title": "TCP", "summary": "TCP gives reliable ordered delivery.",
        "markdown": "## Overview\nTCP provides reliable ordered delivery.",
        "entities": ["TCP"],
        "citations": [{"quote": "reliable, ordered delivery", "locator": None}],
    }
    payloads = iter([extraction, draft])

    def responder(messages, tools):
        return StubChatProvider.tool("emit_result", next(payloads))

    return StubChatProvider(responder=responder)


async def test_multi_turn_carries_context_and_cites(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    user = await UserRepo(db_session).create(
        email="u@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await run_ingest(
        db_session, domain_id=dom.id, source_md=_SOURCE,
        chat=_ingest_chat(), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    await db_session.commit()

    monkeypatch.setattr(chat_mod, "build_embedding_provider", lambda pc, b: emb)

    captured: list[list] = []

    class _Capture(StubChatProvider):
        async def chat(self, messages, *, tools=None, model=None, json_mode=False):
            captured.append(list(messages))
            return StubChatProvider.text("TCP is reliable [tcp]")

    monkeypatch.setattr(chat_mod, "build_chat_provider", lambda pc, b: _Capture())

    svc = ChatService(db_session, fernet_key=_FERNET)
    sess = await svc.resolve_session(user=user, domain_id=dom.id, session_id=None)

    # turn 1
    p1 = await svc.prepare_turn(session=sess, question="is TCP reliable?")
    t1, u1 = await svc.complete_turn(p1)
    await svc.record_turn(
        session=sess, question="is TCP reliable?", answer_md=t1.answer_md, refs=t1.refs,
        model=p1.model, prompt_version=p1.prompt_version, usage=u1,
    )
    repo = ChatRepo(db_session)
    after_first = (await repo.get(sess.id)).last_active_at
    assert (await repo.get(sess.id)).title == "is TCP reliable?"  # auto-title from first turn

    # turn 2 references turn 1
    p2 = await svc.prepare_turn(session=sess, question="and is it ordered?")
    t2, u2 = await svc.complete_turn(p2)
    await svc.record_turn(
        session=sess, question="and is it ordered?", answer_md=t2.answer_md, refs=t2.refs,
        model=p2.model, prompt_version=p2.prompt_version, usage=u2,
    )

    # second prompt folded turn 1 in (carried context)
    second_user = captured[1][1].content
    assert "is TCP reliable?" in second_user and "<<THREAD" in second_user

    # cited answer + last_active bumped
    msgs = await repo.list_messages(sess.id)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert "[tcp]" in msgs[-1].content
    assert any(r["slug"] == "tcp" for r in msgs[-1].meta["refs"])
    assert (await repo.get(sess.id)).last_active_at >= after_first
