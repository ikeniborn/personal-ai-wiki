from paw.db.base import Base
from paw.db.models import QueryCache, QueryCacheArticle  # noqa: F401


def test_query_cache_tables_registered():
    tables = set(Base.metadata.tables)
    assert {"query_cache", "query_cache_articles"} <= tables


def test_query_embedding_not_orm_mapped():
    # query_embedding is a runtime-managed vector(dim) column, like chunks.embedding.
    cols = set(QueryCache.__table__.columns.keys())
    assert "query_embedding" not in cols
    assert {"domain_id", "query_norm", "answer_md", "refs", "passages", "stale",
            "hit_count", "last_hit_at"} <= cols


def test_query_cache_articles_shape():
    cols = set(QueryCacheArticle.__table__.columns.keys())
    assert {"cache_id", "article_id", "rev"} <= cols
