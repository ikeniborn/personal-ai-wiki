from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.ingest import run_ingest
from paw.harness.ops.query import DONT_KNOW
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService

_FERNET = "k" * 43 + "="

_SOURCE = (
    "# TCP\n\nTransmission Control Protocol provides reliable, ordered delivery of a "
    "byte stream between applications. It uses sequence numbers and acknowledgements."
)


def _ingest_chat() -> StubChatProvider:
    # structured() extraction then drafting; responder returns schema-valid tool calls
    extraction = {"entities": ["TCP"], "key_points": ["reliable ordered delivery"]}
    draft = {
        "slug": "tcp", "title": "TCP", "summary": "TCP gives reliable ordered delivery.",
        "markdown": "## Overview\nTCP provides reliable ordered delivery.",
        "entities": ["TCP"], "citations": [{"quote": "reliable, ordered delivery", "locator": None}],
    }
    payloads = iter([extraction, draft])

    def responder(messages, tools):
        return StubChatProvider.tool("emit_result", next(payloads))

    return StubChatProvider(responder=responder)


async def test_ingest_then_query_cited(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await run_ingest(
        db_session, domain_id=dom.id, source_md=_SOURCE,
        chat=_ingest_chat(), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    await db_session.commit()

    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("TCP is reliable [tcp]")]),
    )
    svc = QueryService(db_session, fernet_key=_FERNET)
    ans = await svc.answer(domain_id=dom.id, question="is TCP reliable?")
    assert "[tcp]" in ans.answer_md
    assert any(r.slug == "tcp" for r in ans.refs)
    assert ans.passages


async def test_off_topic_query_dont_know(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    # a domain with NO ingested corpus -> both arms empty -> don't-know without LLM
    dom = await DomainRepo(db_session).create(name="empty", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    emb = StubEmbeddingProvider(dim=8)
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("should never be called")]),
    )
    svc = QueryService(db_session, fernet_key=_FERNET)
    ans = await svc.answer(domain_id=dom.id, question="what is quantum chromodynamics?")
    assert ans.answer_md == DONT_KNOW
    assert ans.refs == [] and ans.passages == []
