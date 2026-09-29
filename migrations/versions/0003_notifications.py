"""Deduplicate actionable desktop notifications across application restarts."""

from alembic import op

revision = "0003_notifications"
down_revision = "0002_fact_history"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        "CREATE TABLE notification_receipts(key TEXT PRIMARY KEY,created_at TEXT NOT NULL)"
    )


def downgrade():
    raise RuntimeError("Notification history is retained to avoid duplicate alerts")
