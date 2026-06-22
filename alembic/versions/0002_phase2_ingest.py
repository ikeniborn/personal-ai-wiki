from alembic import op

revision = "0002_phase2_ingest"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE job_status AS ENUM ('queued','running','succeeded','failed','cancelled')"
    )

    op.execute("""
    CREATE TABLE entities (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      name text NOT NULL, kind text,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, name))
    """)

    op.execute("""
    CREATE TABLE article_entities (
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      entity_id uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
      PRIMARY KEY (article_id, entity_id))
    """)
    op.execute("CREATE INDEX ix_article_entities_entity_id ON article_entities(entity_id)")

    op.execute("""
    CREATE TABLE links (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      src_article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      dst_article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      type text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (src_article_id, dst_article_id, type))
    """)
    op.execute("CREATE INDEX ix_links_domain_id ON links(domain_id)")

    op.execute("""
    CREATE TABLE citations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      source_id uuid REFERENCES sources(id) ON DELETE SET NULL,
      quote text, locator text,
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("CREATE INDEX ix_citations_article_id ON citations(article_id)")

    # chunks: NO embedding column here (managed migration adds vector(dim) + HNSW).
    op.execute("""
    CREATE TABLE chunks (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      kind text NOT NULL, ord int NOT NULL, heading_path text, text text NOT NULL,
      tsv tsvector,
      embedding_version int NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("CREATE INDEX ix_chunks_article_id ON chunks(article_id)")
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_chunks_embedding_version ON chunks(embedding_version)")

    op.execute("""
    CREATE TABLE chunk_entities (
      chunk_id uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      entity_id uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
      PRIMARY KEY (chunk_id, entity_id))
    """)

    op.execute("""
    CREATE TABLE jobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      kind text NOT NULL,
      status job_status NOT NULL DEFAULT 'queued',
      article_id uuid, error text,
      cancel_requested boolean NOT NULL DEFAULT false,
      log jsonb NOT NULL DEFAULT '[]',
      heartbeat_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz, finished_at timestamptz)
    """)
    op.execute("CREATE INDEX ix_jobs_domain_id ON jobs(domain_id)")
    op.execute("CREATE INDEX ix_jobs_status ON jobs(status)")


def downgrade() -> None:
    for t in (
        "jobs",
        "chunk_entities",
        "chunks",
        "citations",
        "links",
        "article_entities",
        "entities",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP TYPE IF EXISTS job_status")
