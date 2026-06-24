from datetime import UTC, datetime, timedelta

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo


async def _domain(db_session, name="d"):
    return await DomainRepo(db_session).create(name=name, source_prefix="s", wiki_prefix="w")


async def test_exact_upsert_and_get_by_norm(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="what is tcp?", answer_md="TCP [tcp]",
        refs=[{"article_id": "a", "slug": "tcp", "title": "TCP"}],
        passages=[{"chunk_id": "c", "slug": "tcp"}],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    row = await repo.get_by_norm(domain_id=dom.id, query_norm="what is tcp?")
    assert row is not None and row.id == cid
    assert row.answer_md == "TCP [tcp]" and row.stale is False
    assert row.refs[0]["slug"] == "tcp" and row.passages[0]["chunk_id"] == "c"
    # re-upsert preserves hit_count and clears stale
    await repo.set_stale(domain_id=dom.id, ids=[cid])  # helper from mark path (see Step 3)
    await repo.upsert(
        domain_id=dom.id, query_norm="what is tcp?", answer_md="TCP v2 [tcp]",
        refs=[], passages=[], model="m", prompt_version="1",
        query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    row2 = await repo.get_by_norm(domain_id=dom.id, query_norm="what is tcp?")
    assert row2.answer_md == "TCP v2 [tcp]" and row2.stale is False


async def test_ann_nearest_returns_distance(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    await repo.upsert(
        domain_id=dom.id, query_norm="q1", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    near = await repo.ann_nearest(domain_id=dom.id, query_vector=[0.96, 0.28, 0.0, 0.0])
    assert near is not None
    row, dist = near
    assert row.query_norm == "q1"
    assert 0.0 <= dist < 0.1  # cosine distance to a near-parallel vector is small


async def test_touch_increments_hit_count(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="q", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    await repo.touch(cache_id=cid)
    await repo.touch(cache_id=cid)
    await db_session.commit()
    row = await repo.get_by_norm(domain_id=dom.id, query_norm="q")
    assert row.hit_count == 2 and row.last_hit_at is not None


async def test_set_deps_and_mark_stale_for_articles(db_session):
    dom = await _domain(db_session)
    arts = ArticleRepo(db_session)
    a1 = await arts.create(domain_id=dom.id, slug="a1", title="A1", storage_ref="b:1")
    a2 = await arts.create(domain_id=dom.id, slug="a2", title="A2", storage_ref="b:2")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    c1 = await repo.upsert(
        domain_id=dom.id, query_norm="dep1", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    c2 = await repo.upsert(
        domain_id=dom.id, query_norm="dep2", answer_md="B", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[0.0, 1.0, 0.0, 0.0],
    )
    await repo.set_deps(cache_id=c1, deps=[(a1.id, 1)])
    await repo.set_deps(cache_id=c2, deps=[(a2.id, 1)])
    await db_session.commit()
    n = await repo.mark_stale_for_articles(domain_id=dom.id, article_ids=[a1.id])
    await db_session.commit()
    assert n == 1
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep1")).stale is True
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep2")).stale is False


async def test_suggest_ranks_by_hit_count(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    for norm, hits in [("tcp basics", 1), ("tcp handshake", 5), ("udp facts", 9)]:
        cid = await repo.upsert(
            domain_id=dom.id, query_norm=norm, answer_md="A", refs=[], passages=[],
            model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
        )
        for _ in range(hits):
            await repo.touch(cache_id=cid)
    await db_session.commit()
    out = await repo.suggest(domain_id=dom.id, q="tcp", limit=5)
    assert out == ["tcp handshake", "tcp basics"]  # only 'tcp%' matches, hit-count order


async def test_delete_expired(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="old", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    # backdate last_hit_at far into the past
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE query_cache SET last_hit_at = :w WHERE id = :i"),
        {"w": datetime.now(UTC) - timedelta(days=400), "i": str(cid)},
    )
    await db_session.commit()
    n = await repo.delete_expired(cutoff=datetime.now(UTC) - timedelta(days=30))
    await db_session.commit()
    assert n == 1
    assert await repo.get_by_norm(domain_id=dom.id, query_norm="old") is None
