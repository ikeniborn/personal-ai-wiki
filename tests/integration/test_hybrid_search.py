from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig
from paw.vector.embed import embed_and_write
from paw.vector.search import hybrid_search, query_entities


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
        embedder=StubEmbeddingProvider(dim=dim),
    )
    await db_session.commit()
    return dom, art, ids


async def test_repo_reads(db_session):
    dom, art, ids = await _seed(db_session)
    repo = ChunkRepo(db_session)
    passages = await repo.fetch_passages(ids)
    assert {p.chunk_id for p in passages} == set(ids)
    assert any(p.slug == "tcp" and p.title == "TCP" for p in passages)
    summaries = await repo.fetch_summaries([art.id])
    assert summaries[0].text == "TCP summary" and summaries[0].slug == "tcp"
    # entity tagging + tagged_with
    e = await EntityRepo(db_session).upsert(domain_id=dom.id, name="TCP")
    await repo.tag_entity(chunk_id=ids[1], entity_id=e.id)
    await db_session.commit()
    tagged = await repo.tagged_with(chunk_ids=ids, entity_ids=[e.id])
    assert tagged == {ids[1]}
    assert [en.name for en in await EntityRepo(db_session).list_by_domain(dom.id)] == ["TCP"]


async def test_fts_arm_surfaces_term_exact(db_session):
    dom, art, ids = await _seed(db_session)
    cfg = RetrievalConfig(k1=10, k2=10, top_n=5)
    qvec = StubEmbeddingProvider(dim=8)._vec("reliable")
    hits = await hybrid_search(
        db_session, domain_id=dom.id, query="reliable", query_vector=qvec,
        cfg=cfg, embedding_version=1,
    )
    assert hits, "expected at least one fused hit"
    assert hits[0].article_id == art.id


async def test_embedding_version_filter_excludes_stale(db_session):
    from sqlalchemy import text
    dom, art, ids = await _seed(db_session)
    # bump one chunk to a different embedding_version -> excluded from the vector arm
    await db_session.execute(
        text("UPDATE chunks SET embedding_version = 2 WHERE id = :i"), {"i": str(ids[1])}
    )
    await db_session.commit()
    cfg = RetrievalConfig(k1=10, k2=10, top_n=5)
    qvec = StubEmbeddingProvider(dim=8)._vec("anything")
    hits = await hybrid_search(
        db_session, domain_id=dom.id, query="zzzznomatch", query_vector=qvec,
        cfg=cfg, embedding_version=1,
    )
    assert ids[1] not in {h.chunk_id for h in hits}  # stale chunk filtered


async def test_entity_boost_raises_ranking(db_session):
    from paw.db.repos.chunks import ChunkRepo
    from paw.db.repos.entities import EntityRepo
    dom, art, ids = await _seed(db_session)
    e = await EntityRepo(db_session).upsert(domain_id=dom.id, name="TCP")
    await ChunkRepo(db_session).tag_entity(chunk_id=ids[0], entity_id=e.id)
    await db_session.commit()
    ent_ids = await query_entities(db_session, domain_id=dom.id, query="what is TCP")
    assert e.id in ent_ids
    cfg = RetrievalConfig(k1=10, k2=10, top_n=5, entity_boost=10.0)
    qvec = StubEmbeddingProvider(dim=8)._vec("what is TCP")
    hits = await hybrid_search(
        db_session, domain_id=dom.id, query="what is TCP", query_vector=qvec,
        cfg=cfg, embedding_version=1, boost_entity_ids=ent_ids,
    )
    assert hits[0].chunk_id == ids[0]  # boosted summary chunk wins
