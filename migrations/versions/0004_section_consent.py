"""Persist the per-application requirement for section-by-section consent."""

from alembic import op

revision = "0004_section_consent"
down_revision = "0003_notifications"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(applications)")}
    if "section_consent" not in columns:
        connection.exec_driver_sql("ALTER TABLE applications ADD COLUMN section_consent INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Application consent requirements must be retained")
