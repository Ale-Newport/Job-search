"""Record explicit permission to fill an assisted application before final review."""

from alembic import op

revision = "0005_assisted_autofill"
down_revision = "0004_section_consent"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(applications)")}
    if "assisted_autofill" not in columns:
        connection.exec_driver_sql("ALTER TABLE applications ADD COLUMN assisted_autofill INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Application consent records must be retained")
