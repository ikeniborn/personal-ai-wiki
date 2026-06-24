from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.services.cache_seam import mark_cache_stale


async def _cache_entry(db_session, *, domain_id, query_norm, dep_article_id):
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=domain_id, query_norm=query_norm, answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await repo.set_deps(cache_id=cid, deps=[(dep_article_id, 1)])
    return cid


async def test_seam_marks_only_dependent_entries(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    arts = ArticleRepo(db_session)
    a1 = await arts.create(domain_id=dom.id, slug="a1", title="A1", storage_ref="b:1")
    a2 = await arts.create(domain_id=dom.id, slug="a2", title="A2", storage_ref="b:2")
    await ensure_query_cache_embedding_column(db_session, 4)
    await _cache_entry(db_session, domain_id=dom.id, query_norm="dep-a1", dep_article_id=a1.id)
    await _cache_entry(db_session, domain_id=dom.id, query_norm="dep-a2", dep_article_id=a2.id)
    await db_session.commit()

    # editing a1 marks only the a1-dependent entry stale, same transaction
    await mark_cache_stale(db_session, domain_id=dom.id, article_ids=[a1.id])
    await db_session.commit()

    repo = QueryCacheRepo(db_session)
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep-a1")).stale is True
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep-a2")).stale is False


async def test_seam_rolls_back_with_the_write(db_session):
    # the stale mark is part of the caller's transaction: a rollback un-marks it
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a", title="A", storage_ref="b:1"
    )
    await ensure_query_cache_embedding_column(db_session, 4)
    cid = await _cache_entry(db_session, domain_id=dom.id, query_norm="q", dep_article_id=art.id)
    await db_session.commit()

    await mark_cache_stale(db_session, domain_id=dom.id, article_ids=[art.id])
    await db_session.rollback()  # caller aborts the write

    row = (await db_session.execute(
        text("SELECT stale FROM query_cache WHERE id = :i"), {"i": str(cid)}
    )).scalar_one()
    assert row is False  # un-marked along with the rolled-back write
