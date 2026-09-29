"""Retain historical candidate evidence after edits or deletion."""

from alembic import op

revision = "0002_fact_history"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        "CREATE TABLE fact_history(id TEXT PRIMARY KEY,fact_id TEXT NOT NULL,payload TEXT NOT NULL,valid_from TEXT NOT NULL,valid_until TEXT NOT NULL)"
    )
    op.get_bind().exec_driver_sql("CREATE INDEX idx_fact_history_at ON fact_history(fact_id,valid_from DESC)")


def downgrade():
    raise RuntimeError("Historical evidence cannot be discarded by a downgrade")
