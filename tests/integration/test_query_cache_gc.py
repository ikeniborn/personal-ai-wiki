from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.jobs.tasks import gc_housekeeping


async def test_gc_deletes_expired_cache_entries(db_session, wired_settings):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    await repo.upsert(
        domain_id=dom.id, query_norm="fresh", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    expired = await repo.upsert(
        domain_id=dom.id, query_norm="expired", answer_md="B", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    # default ttl is 30 days; backdate the expired entry past it
    await db_session.execute(
        text("UPDATE query_cache SET last_hit_at = :w WHERE id = :i"),
        {"w": datetime.now(UTC) - timedelta(days=40), "i": str(expired)},
    )
    await db_session.commit()

    await gc_housekeeping({})

    assert await repo.get_by_norm(domain_id=dom.id, query_norm="fresh") is not None
    assert await repo.get_by_norm(domain_id=dom.id, query_norm="expired") is None
