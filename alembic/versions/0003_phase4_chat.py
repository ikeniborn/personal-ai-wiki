from alembic import op

revision = "0003_phase4_chat"
down_revision = "0002_phase2_ingest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE chat_sessions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      title text,
      created_at timestamptz NOT NULL DEFAULT now(),
      last_active_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute(
        "CREATE INDEX ix_chat_sessions_user_last_active "
        "ON chat_sessions(user_id, last_active_at DESC)"
    )

    op.execute("""
    CREATE TABLE chat_messages (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
      role text NOT NULL,
      content text NOT NULL,
      meta jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute(
        "CREATE INDEX ix_chat_messages_session_created ON chat_messages(session_id, created_at)"
    )


def downgrade() -> None:
    for t in ("chat_messages", "chat_sessions"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
