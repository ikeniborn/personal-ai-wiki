from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.query import DONT_KNOW
from paw.ingest.chunking import ChunkSpec
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


async def _provision(db_session, monkeypatch, *, answer="reliable means [tcp]"):
    box = SecretBox(_FERNET)
    psvc = ProviderSettingsService(db_session, box=box)
    await psvc.persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e",
        embedding_dim=8, api_key="secret",
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
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
    monkeypatch.setattr(query_mod, "build_chat_provider",
                        lambda pc, b: StubChatProvider(script=[StubChatProvider.text(answer)]))
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    return dom


async def test_answer_cites_articles(db_session, monkeypatch):
    dom = await _provision(db_session, monkeypatch)
    svc = QueryService(db_session, fernet_key=_FERNET)
    ans = await svc.answer(domain_id=dom.id, question="what does reliable mean?")
    assert ans.answer_md == "reliable means [tcp]"
    assert any(r.slug == "tcp" for r in ans.refs)
    assert ans.passages


async def test_empty_context_returns_dont_know(db_session, monkeypatch):
    dom = await _provision(db_session, monkeypatch)
    empty = await DomainRepo(db_session).create(name="empty", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    svc = QueryService(db_session, fernet_key=_FERNET)
    ans = await svc.answer(domain_id=empty.id, question="totally unrelated")
    assert ans.answer_md == DONT_KNOW and ans.refs == [] and ans.passages == []


async def test_missing_provider_raises_422(db_session, monkeypatch):
    from paw.api.errors import ProblemError
    dom = await DomainRepo(db_session).create(name="np", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    svc = QueryService(db_session, fernet_key=_FERNET)
    try:
        await svc.prepare(domain_id=dom.id, question="q")
        assert False, "expected ProblemError"
    except ProblemError as e:
        assert e.status == 422
