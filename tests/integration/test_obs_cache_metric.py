"""Integration tests for obs Task 6: cache hit/miss counter + SSE-active gauge."""
from __future__ import annotations

import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column, ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.obs import metrics
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query_cache import QueryCacheService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class _FixedEmbed:
    """Deterministic embedder for tests."""

    def __init__(self, default: list[float]) -> None:
        self.default = default

    async def embed(self, texts: list[str], *, model: object = None) -> list[list[float]]:
        return [self.default for _ in texts]


async def _provision(db_session, monkeypatch):
    """Seed provider + domain + article + embedding column."""
    embed = _FixedEmbed(default=[1.0, 0.0, 0.0, 0.0])
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="obs-d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="obs-art", title="Obs", storage_ref="b:obs", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    await ensure_query_cache_embedding_column(db_session, 4)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="obs text")],
        embedder=embed,
    )
    await db_session.commit()
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: embed)
    return dom, embed


def _cache_hits_value(label: str) -> float:
    """Read the current counter value for CACHE_HITS{result=label}."""
    return metrics.CACHE_HITS.labels(result=label)._value.get()


# ---------------------------------------------------------------------------
# Cache hit/miss counter tests
# ---------------------------------------------------------------------------

async def test_obs_cache_miss_increments_counter(db_session, monkeypatch):
    """A cold lookup must increment result='miss' by exactly 1."""
    dom, _ = await _provision(db_session, monkeypatch)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)

    before = _cache_hits_value("miss")
    result = await csvc.lookup(domain_id=dom.id, question="never seen before obs q", cfg=cfg)
    after = _cache_hits_value("miss")

    assert result is None
    assert after - before == 1.0


async def test_obs_cache_hit_increments_counter(db_session, monkeypatch):
    """After upserting, a repeated query must increment result='hit' by exactly 1."""
    dom, embed = await _provision(db_session, monkeypatch)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)

    # Seed the cache.
    await csvc.upsert(
        domain_id=dom.id, question="obs hit q", answer_md="the answer",
        refs=[], passages=[], model="m",
    )

    before_hit = _cache_hits_value("hit")
    before_miss = _cache_hits_value("miss")
    result = await csvc.lookup(domain_id=dom.id, question="obs hit q", cfg=cfg)
    after_hit = _cache_hits_value("hit")
    after_miss = _cache_hits_value("miss")

    assert result is not None
    assert after_hit - before_hit == 1.0
    assert after_miss - before_miss == 0.0  # no miss recorded


# ---------------------------------------------------------------------------
# SSE-active gauge balance tests
# ---------------------------------------------------------------------------

async def test_obs_sse_gauge_balanced_cached(db_session, monkeypatch):
    """Exhausting _sse_cached's generator leaves the SSE_ACTIVE gauge balanced."""
    import uuid

    from paw.api.routers.query import _sse_cached
    from paw.services.query_cache import CacheHit

    hit = CacheHit(
        id=uuid.uuid4(), answer_md="cached answer", refs=[], passages=[], stale=False
    )

    before = metrics.SSE_ACTIVE._value.get()
    # Drive the generator to exhaustion.
    async for _ in _sse_cached(hit):
        pass
    after = metrics.SSE_ACTIVE._value.get()

    assert after == before


async def test_obs_sse_gauge_balanced_on_disconnect_cached():
    """Even when the consumer closes _sse_cached early, the gauge stays balanced."""
    import uuid

    from paw.api.routers.query import _sse_cached
    from paw.services.query_cache import CacheHit

    hit = CacheHit(
        id=uuid.uuid4(), answer_md="cached answer", refs=[], passages=[], stale=False
    )

    before = metrics.SSE_ACTIVE._value.get()
    gen = _sse_cached(hit)
    # Pull one item then close (simulates client disconnect).
    await gen.__anext__()
    await gen.aclose()
    after = metrics.SSE_ACTIVE._value.get()

    assert after == before


async def test_obs_sse_gauge_balanced_compute(monkeypatch):
    """Exhausting _sse_compute's generator leaves the SSE_ACTIVE gauge balanced."""
    from paw.api.routers.query import _sse_compute
    from paw.harness.retrieve import RetrievedContext
    from paw.services.query import Prepared

    class _StubChat:
        async def stream(self, messages):  # type: ignore[override]
            yield "tok"

    ctx = RetrievedContext(refs=[], passages=[], prompt_block="")
    prepared = Prepared(
        chat=_StubChat(),  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "q"}],
        ctx=ctx,
        chat_model="m",
    )

    import uuid as _uuid

    before = metrics.SSE_ACTIVE._value.get()
    async for _ in _sse_compute(
        prepared, None, domain_id=_uuid.uuid4(), question="q", model="m"
    ):
        pass
    after = metrics.SSE_ACTIVE._value.get()

    assert after == before


async def test_obs_sse_gauge_balanced_on_disconnect_compute(monkeypatch):
    """Closing _sse_compute's generator early still decrements the gauge."""
    import uuid as _uuid

    from paw.api.routers.query import _sse_compute
    from paw.harness.retrieve import RetrievedContext
    from paw.services.query import Prepared

    class _SlowChat:
        async def stream(self, messages):  # type: ignore[override]
            for i in range(10):
                yield f"tok{i}"

    ctx = RetrievedContext(refs=[], passages=[], prompt_block="")
    prepared = Prepared(
        chat=_SlowChat(),  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "q"}],
        ctx=ctx,
        chat_model="m",
    )

    before = metrics.SSE_ACTIVE._value.get()
    gen = _sse_compute(
        prepared, None, domain_id=_uuid.uuid4(), question="q", model="m"
    )
    await gen.__anext__()  # pull first token
    await gen.aclose()     # simulate disconnect
    after = metrics.SSE_ACTIVE._value.get()

    assert after == before
