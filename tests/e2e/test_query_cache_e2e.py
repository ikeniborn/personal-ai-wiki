from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.fix import FixProposal, apply_fix
from paw.harness.ops.lint import LintIssue
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.services.query_cache import QueryCacheService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class FixedEmbed:
    def __init__(self, default):
        self.default = default

    async def embed(self, texts, *, model=None):
        return [self.default for _ in texts]


async def test_query_cached_then_edit_marks_stale_then_refresh(db_session, monkeypatch):
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
        db_session,
        article_id=art.id,
        domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: emb)

    answers = iter(["v1 reliable [tcp]", "v2 reliable [tcp]"])

    def chat(pc, b):
        return StubChatProvider(script=[StubChatProvider.text(next(answers))])

    monkeypatch.setattr(query_mod, "build_chat_provider", chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    Q = "is tcp reliable?"

    # 1) MISS -> compute v1 -> upsert (with art dependency)
    a1 = await qsvc.answer(domain_id=dom.id, question=Q)
    await csvc.upsert(
        domain_id=dom.id, question=Q, answer_md=a1.answer_md,
        refs=a1.refs, passages=a1.passages, model="m",
    )
    assert a1.answer_md == "v1 reliable [tcp]"

    # 2) HIT (fresh) -> cached v1, no LLM
    hit = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert hit is not None and hit.answer_md == "v1 reliable [tcp]" and hit.stale is False

    # 3) edit the cited article via the fix op (uses the real seam) -> entry goes stale
    await apply_fix(
        db_session,
        domain_id=dom.id,
        issue=LintIssue(id="i1", kind="thin", target_slug="tcp", detail="d", fix="f"),
        proposal=FixProposal(markdown="## TCP\nNow with more detail.", summary="s"),
        cfg=WikiConfig(),
        author_id=None,
    )
    await db_session.commit()
    stale_hit = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert stale_hit is not None and stale_hit.stale is True
    assert stale_hit.answer_md == "v1 reliable [tcp]"  # still served, just flagged

    # 4) refresh -> recompute v2 -> upsert clears stale
    a2 = await qsvc.answer(domain_id=dom.id, question=Q)
    await csvc.upsert(
        domain_id=dom.id, question=Q, answer_md=a2.answer_md,
        refs=a2.refs, passages=a2.passages, model="m",
    )
    fresh = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert fresh is not None and fresh.answer_md == "v2 reliable [tcp]" and fresh.stale is False
