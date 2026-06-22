from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE TYPE user_role AS ENUM ('admin','editor','viewer')")
    op.execute("CREATE TYPE source_status AS ENUM ('uploaded','extracted','ingested','failed')")
    op.execute("CREATE TYPE rev_origin AS ENUM ('ai','user')")

    op.execute("""
    CREATE TABLE users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      email citext UNIQUE NOT NULL, pw_hash text NOT NULL,
      role user_role NOT NULL DEFAULT 'viewer',
      chat_prefs jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE api_keys (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      prefix text NOT NULL, hash text NOT NULL, scopes jsonb NOT NULL DEFAULT '[]',
      created_at timestamptz NOT NULL DEFAULT now(), last_used timestamptz, revoked_at timestamptz)
    """)
    op.execute("CREATE INDEX ix_api_keys_prefix ON api_keys(prefix)")
    op.execute("""
    CREATE TABLE app_settings (
      id boolean PRIMARY KEY DEFAULT true CHECK (id), settings jsonb NOT NULL DEFAULT '{}')
    """)
    op.execute("""
    CREATE TABLE domains (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      name text NOT NULL UNIQUE, source_prefix text NOT NULL, wiki_prefix text NOT NULL,
      config jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE blobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      data bytea NOT NULL, content_type text, created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE sources (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      storage_ref text NOT NULL, filename text, type text NOT NULL, url text,
      checksum text NOT NULL,
      status source_status NOT NULL DEFAULT 'uploaded',
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, checksum))
    """)
    op.execute("""
    CREATE TABLE articles (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      slug text NOT NULL, title text NOT NULL, storage_ref text NOT NULL, summary text,
      current_rev int NOT NULL DEFAULT 1,
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (domain_id, slug))
    """)
    op.execute("""
    CREATE TABLE article_revisions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      rev_no int NOT NULL, storage_ref text NOT NULL,
      author_id uuid REFERENCES users(id) ON DELETE SET NULL,
      origin rev_origin NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (article_id, rev_no))
    """)
    op.execute("""
    CREATE TABLE audit_log (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid REFERENCES users(id) ON DELETE SET NULL,
      action text NOT NULL, target_type text, target_id uuid,
      meta jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now())
    """)


def downgrade() -> None:
    for t in ("audit_log", "article_revisions", "articles", "sources", "blobs",
              "domains", "app_settings", "api_keys", "users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in ("rev_origin", "source_status", "user_role"):
        op.execute(f"DROP TYPE IF EXISTS {e}")
