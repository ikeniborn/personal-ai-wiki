from sqlalchemy import text
from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig
from paw.vector.embed import embed_and_write
from paw.vector.reindex import reindex_domain_chunks
from paw.vector.search import hybrid_search


async def _seed(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:1", summary="sum"
    )
    await ensure_embedding_column(db_session, dim)
    specs = [
        ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP summary"),
        ChunkSpec(kind="section", ord=1, heading_path="Reliability", text="TCP is reliable"),
    ]
    ids = await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id, specs=specs,
        embedder=StubEmbeddingProvider(dim=dim), embedding_version=1,
    )
    await db_session.commit()
    return dom, art, ids


async def test_reindex_flips_version_and_search_follows_it(db_session):
    dom, art, ids = await _seed(db_session)
    # simulate a model change: target version is now 2; all chunks are stale (v1)
    n = await reindex_domain_chunks(
        db_session, domain_id=dom.id, target_version=2,
        embedder=StubEmbeddingProvider(dim=8), batch_size=1,
    )
    await db_session.commit()
    assert n == len(ids)

    rows = await db_session.execute(
        text("SELECT DISTINCT embedding_version FROM chunks WHERE domain_id = :d"),
        {"d": str(dom.id)},
    )
    assert [r[0] for r in rows.all()] == [2]  # everything flipped to current

    cfg = RetrievalConfig(k1=10, k2=10, top_n=5)
    qvec = StubEmbeddingProvider(dim=8)._vec("reliable")
    # search at the new version returns the reindexed chunk...
    new_hits = await hybrid_search(
        db_session, domain_id=dom.id, query="reliable", query_vector=qvec,
        cfg=cfg, embedding_version=2,
    )
    assert any(h.chunk_id in ids for h in new_hits)
    # ...and the old version's vector arm ignores them
    old_hits = await hybrid_search(
        db_session, domain_id=dom.id, query="zzzznomatch", query_vector=qvec,
        cfg=cfg, embedding_version=1,
    )
    assert not {h.chunk_id for h in old_hits} & set(ids)


async def test_reindex_is_noop_when_nothing_stale(db_session):
    dom, art, ids = await _seed(db_session)
    n = await reindex_domain_chunks(
        db_session, domain_id=dom.id, target_version=1,  # already current
        embedder=StubEmbeddingProvider(dim=8), batch_size=10,
    )
    assert n == 0
