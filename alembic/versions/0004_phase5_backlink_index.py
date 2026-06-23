from alembic import op

revision = "0004_phase5_backlink_index"
down_revision = "0003_phase4_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_links_dst_article_id ON links(dst_article_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_links_dst_article_id")
