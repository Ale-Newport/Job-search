"""Scoped browser answers with fact provenance."""
from alembic import op
revision = "0006_browser_answers"
down_revision = "0005_assisted_autofill"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql("""CREATE TABLE IF NOT EXISTS browser_answers(
        id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        question TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
        reusable INTEGER NOT NULL DEFAULT 0, scope TEXT, action TEXT NOT NULL DEFAULT 'answer',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(application_id,question))""")


def downgrade():
    raise RuntimeError("User-confirmed answer records must be retained")
