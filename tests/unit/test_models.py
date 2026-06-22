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


def test_article_unique_slug_per_domain():
    cols = {c.name for c in Base.metadata.tables["articles"].columns}
    assert {"id", "domain_id", "slug", "title", "storage_ref", "current_rev"} <= cols


def test_phase2_models_registered():
    from paw.db.base import Base
    from paw.db.models import JOB_STATUS

    tables = set(Base.metadata.tables)
    assert {
        "entities",
        "article_entities",
        "links",
        "citations",
        "chunks",
        "chunk_entities",
        "jobs",
    } <= tables
    assert JOB_STATUS == ("queued", "running", "succeeded", "failed", "cancelled")


def test_chunks_has_no_orm_embedding_column():
    # embedding/tsv are runtime-managed / raw — must NOT be ORM-mapped.
    from paw.db.models import Chunk

    cols = set(Chunk.__table__.columns.keys())
    assert "embedding" not in cols
    assert "tsv" not in cols
    assert "embedding_version" in cols
