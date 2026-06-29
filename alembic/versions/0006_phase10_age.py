from alembic import op

revision = "0006_phase10_age"
down_revision = "0005_phase7_query_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AGE objects live in the ag_catalog schema; per-domain graphs are created at runtime.
    op.execute("CREATE EXTENSION IF NOT EXISTS age")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS age CASCADE")
