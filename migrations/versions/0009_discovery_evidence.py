"""Persist completeness and provenance for each career-source scan."""
from alembic import op

revision = '0009_discovery_evidence'
down_revision = '0008_reliability'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {r[1] for r in bind.exec_driver_sql('PRAGMA table_info(source_scans)')}
    if 'metadata' not in columns:
        bind.exec_driver_sql("ALTER TABLE source_scans ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
    bind.exec_driver_sql("""CREATE TABLE IF NOT EXISTS source_job_observations(
        source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        state TEXT NOT NULL, observed_at TEXT NOT NULL, PRIMARY KEY(source_id,job_id))""")


def downgrade():
    raise RuntimeError('Discovery evidence must be retained')
