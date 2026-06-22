# tests/integration/test_migration.py
import sqlalchemy as sa
from sqlalchemy import create_engine


def test_baseline_creates_core_tables(pg_sync_url):
    # pg_sync_url: psycopg/sync URL to a fresh container with the baseline + Phase 2 applied.
    engine = create_engine(pg_sync_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {
        "users",
        "domains",
        "articles",
        "article_revisions",
        "sources",
        "blobs",
        "api_keys",
        "app_settings",
        "audit_log",
    } <= tables
    # extensions present
    with engine.connect() as conn:
        exts = {r[0] for r in conn.execute(sa.text("SELECT extname FROM pg_extension"))}
    assert {"vector", "pgcrypto", "citext"} <= exts
    engine.dispose()


def test_phase2_creates_ingest_tables(pg_sync_url):
    import sqlalchemy as sa
    from sqlalchemy import create_engine

    engine = create_engine(pg_sync_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {
        "entities",
        "article_entities",
        "links",
        "citations",
        "chunks",
        "chunk_entities",
        "jobs",
    } <= tables
    # chunks has tsv but NOT embedding (embedding is managed at runtime)
    chunk_cols = {c["name"] for c in insp.get_columns("chunks")}
    assert "tsv" in chunk_cols
    assert "embedding_version" in chunk_cols
    assert "embedding" not in chunk_cols
    # GIN index on tsv present
    idx = {i["name"] for i in insp.get_indexes("chunks")}
    assert any("tsv" in name for name in idx)
    engine.dispose()
