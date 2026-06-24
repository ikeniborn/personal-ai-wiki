from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.services.query_cache import QueryCacheService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class FixedEmbed:
    """Deterministic, controllable embedder: maps text -> a fixed unit-ish vector."""

    def __init__(self, table: dict[str, list[float]], default: list[float]) -> None:
        self.table = table
        self.default = default

    async def embed(self, texts, *, model=None):
        return [self.table.get(t, self.default) for t in texts]


async def _provision(db_session, monkeypatch, *, embed):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=embed,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: embed)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: embed)
    return dom, art


async def test_miss_then_exact_hit_skips_llm(db_session, monkeypatch):
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    chat = StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)

    # MISS -> compute + upsert
    assert await csvc.lookup(domain_id=dom.id, question="what is reliable?", cfg=cfg) is None
    answer = await qsvc.answer(domain_id=dom.id, question="what is reliable?")
    await csvc.upsert(
        domain_id=dom.id, question="what is reliable?", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )
    assert len(chat.calls) == 1

    # HIT (exact norm, different casing/space) -> no further LLM call
    hit = await csvc.lookup(domain_id=dom.id, question="  WHAT is   reliable? ", cfg=cfg)
    assert hit is not None and hit.answer_md == "reliable means [tcp]"
    assert hit.stale is False
    assert len(chat.calls) == 1  # acceptance #1: zero LLM calls on the second request


async def test_ann_hit_within_threshold_else_miss(db_session, monkeypatch):
    embed = FixedEmbed(
        {
            "tcp explained": [0.96, 0.28, 0.0, 0.0],   # cos ~0.96 to the stored vector -> HIT
            "banana bread": [0.0, 0.0, 1.0, 0.0],      # cos 0 -> MISS
        },
        default=[1.0, 0.0, 0.0, 0.0],                  # the cached query embeds here
    )
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    chat = StubChatProvider(script=[StubChatProvider.text("answer [tcp]")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    answer = await qsvc.answer(domain_id=dom.id, question="what is tcp")  # default vec
    await csvc.upsert(
        domain_id=dom.id, question="what is tcp", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )

    near = await csvc.lookup(domain_id=dom.id, question="tcp explained", cfg=cfg)
    assert near is not None and near.answer_md == "answer [tcp]"
    far = await csvc.lookup(domain_id=dom.id, question="banana bread", cfg=cfg)
    assert far is None


async def test_upsert_records_article_deps(db_session, monkeypatch):
    from sqlalchemy import text
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("a [tcp]")]),
    )
    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    answer = await qsvc.answer(domain_id=dom.id, question="q")
    await csvc.upsert(
        domain_id=dom.id, question="q", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )
    rows = (await db_session.execute(
        text("SELECT article_id, rev FROM query_cache_articles")
    )).all()
    assert (str(art.id), 1) in {(str(r[0]), r[1]) for r in rows}


async def test_lookup_disabled_when_no_embedding_column(db_session, monkeypatch):
    # exact path still works even before any ANN column exists for the domain
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    # nothing cached yet, no query_cache embedding column -> clean miss, no error
    assert await csvc.lookup(domain_id=dom.id, question="anything", cfg=cfg) is None
