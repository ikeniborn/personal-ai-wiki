from paw.db import models  # noqa: F401  (registers tables on Base.metadata)
from paw.db.base import Base


def test_core_tables_registered():
    tables = set(Base.metadata.tables)
    assert {
        "users",
        "api_keys",
        "app_settings",
        "domains",
        "blobs",
        "sources",
        "articles",
        "article_revisions",
        "audit_log",
    } <= tables
    # No vector/chunk tables in Phase 1.
    assert "chunks" not in tables
    assert "entities" not in tables


def test_article_unique_slug_per_domain():
    cols = {c.name for c in Base.metadata.tables["articles"].columns}
    assert {"id", "domain_id", "slug", "title", "storage_ref", "current_rev"} <= cols
