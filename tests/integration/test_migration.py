# tests/integration/test_migration.py
import sqlalchemy as sa
from sqlalchemy import create_engine


def test_baseline_creates_core_tables(pg_sync_url):
    # pg_sync_url: psycopg/sync URL to a fresh container with the baseline applied.
    engine = create_engine(pg_sync_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {"users", "domains", "articles", "article_revisions", "sources",
            "blobs", "api_keys", "app_settings", "audit_log"} <= tables
    assert "chunks" not in tables  # vector tables are Phase 2
    # extensions present
    with engine.connect() as conn:
        exts = {r[0] for r in conn.execute(sa.text("SELECT extname FROM pg_extension"))}
    assert {"vector", "pgcrypto", "citext"} <= exts
    engine.dispose()
