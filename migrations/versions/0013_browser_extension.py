"""Browser-extension bridge: paired clients, pairing handshakes, page sessions and a field-level audit."""
from alembic import op

revision = '0013_browser_extension'
down_revision = '0012_application_answer_history'
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE extension_clients(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, browser TEXT NOT NULL, origin TEXT NOT NULL,
            token_hash TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            last_seen_at TEXT, revoked_at TEXT)""",
        "CREATE UNIQUE INDEX idx_extension_client_token ON extension_clients(token_hash)",
        "CREATE INDEX idx_extension_client_origin ON extension_clients(origin,revoked_at)",
        """CREATE TABLE extension_pairings(
            id TEXT PRIMARY KEY, code TEXT NOT NULL, client_name TEXT NOT NULL, browser TEXT NOT NULL,
            origin TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','denied','expired','claimed')),
            attempts INTEGER NOT NULL DEFAULT 0, client_id TEXT REFERENCES extension_clients(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT)""",
        "CREATE UNIQUE INDEX idx_extension_pairing_pending ON extension_pairings(origin) WHERE state='pending'",
        "CREATE INDEX idx_extension_pairing_expiry ON extension_pairings(state,expires_at)",
        """CREATE TABLE extension_sessions(
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES extension_clients(id) ON DELETE CASCADE,
            url TEXT NOT NULL, canonical_url TEXT NOT NULL, host TEXT NOT NULL DEFAULT '',
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            application_id TEXT REFERENCES applications(id) ON DELETE SET NULL,
            company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            research_state TEXT NOT NULL DEFAULT 'none'
                CHECK(research_state IN ('none','queued','running','ready','failed','skipped')),
            metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE UNIQUE INDEX idx_extension_session_page ON extension_sessions(client_id,canonical_url)",
        "CREATE INDEX idx_extension_session_job ON extension_sessions(job_id)",
        "CREATE INDEX idx_extension_session_recent ON extension_sessions(updated_at DESC)",
        """CREATE TABLE extension_events(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES extension_sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, field_hash TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""",
        "CREATE INDEX idx_extension_event_session ON extension_events(session_id,created_at)",
        # The status of the assistant counts and looks up audit lines on every poll: both read these indexes only (api.py
        # repeats them as ``EVENT_INDEXES`` for a database that ran this revision before they existed).
        "CREATE INDEX idx_extension_event_kind ON extension_events(session_id,kind,created_at,outcome)",
        "CREATE INDEX idx_extension_event_edit ON extension_events(created_at) WHERE kind='edit'",
    ]
    bind = op.get_bind()
    for statement in statements:
        # Idempotent like 0008: restoring/replaying an older revision must not fail on existing objects.
        statement = (statement.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ')
                     .replace('CREATE UNIQUE INDEX ', 'CREATE UNIQUE INDEX IF NOT EXISTS ')
                     .replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS '))
        bind.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Paired-browser and extension audit records must be retained')
