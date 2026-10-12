"""Retain superseded application answers without reusing them as current answers."""
from alembic import op

revision = '0012_application_answer_history'
down_revision = '0011_posting_validity_correction'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE TABLE IF NOT EXISTS superseded_application_answers(answer_id TEXT PRIMARY KEY REFERENCES application_answers(id) ON DELETE CASCADE,superseded_at TEXT NOT NULL)')
    op.execute('CREATE TABLE IF NOT EXISTS application_answer_targets(answer_id TEXT PRIMARY KEY REFERENCES application_answers(id) ON DELETE CASCADE,question_id TEXT NOT NULL)')


def downgrade():
    raise RuntimeError('Application answer history must be retained')
