"""Initial normalized local workspace, indexes and FTS5."""

import sqlite3
from alembic import op
from jobagent.db import SCHEMA

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statement = ""
    for line in SCHEMA.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            op.get_bind().exec_driver_sql(statement)
            statement = ""


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an encrypted backup instead.")
