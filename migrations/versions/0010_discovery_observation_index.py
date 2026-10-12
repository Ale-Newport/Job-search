"""Keep cross-source availability reconciliation bounded as the library grows."""
from alembic import op

revision = '0010_discovery_observation_index'
down_revision = '0009_discovery_evidence'
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_source_observations_job_state ON source_job_observations(job_id,state,observed_at)')


def downgrade():
    op.get_bind().exec_driver_sql('DROP INDEX IF EXISTS idx_source_observations_job_state')
