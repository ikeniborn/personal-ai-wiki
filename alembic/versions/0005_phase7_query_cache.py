from alembic import op

revision = "0005_phase7_query_cache"
down_revision = "0004_phase5_backlink_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # query_cache: NO query_embedding column here (managed migration adds vector(dim) + HNSW).
    op.execute("""
    CREATE TABLE query_cache (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      query_norm text NOT NULL,
      answer_md text NOT NULL,
      refs jsonb NOT NULL DEFAULT '[]',
      passages jsonb NOT NULL DEFAULT '[]',
      model text,
      prompt_version text,
      stale boolean NOT NULL DEFAULT false,
      hit_count int NOT NULL DEFAULT 0,
      last_hit_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, query_norm))
    """)
    op.execute("CREATE INDEX ix_query_cache_domain_stale ON query_cache(domain_id, stale)")

    op.execute("""
    CREATE TABLE query_cache_articles (
      cache_id uuid NOT NULL REFERENCES query_cache(id) ON DELETE CASCADE,
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      rev int NOT NULL,
      PRIMARY KEY (cache_id, article_id))
    """)
    op.execute(
        "CREATE INDEX ix_query_cache_articles_article_id "
        "ON query_cache_articles(article_id)"
    )


def downgrade() -> None:
    for t in ("query_cache_articles", "query_cache"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
