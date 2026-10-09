"""Durable execution, submission receipts, verified jobs and professional contacts."""
from alembic import op

revision = "0008_reliability"
down_revision = "0007_candidate_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        "CREATE TABLE company_aliases(alias TEXT PRIMARY KEY COLLATE NOCASE,company_id TEXT NOT NULL REFERENCES companies(id),payload TEXT NOT NULL DEFAULT '{}')",
        """CREATE TABLE automation_tasks(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, application_id TEXT REFERENCES applications(id),
            run_id TEXT REFERENCES automation_runs(id), state TEXT NOT NULL DEFAULT 'QUEUED',
            dedupe_key TEXT NOT NULL, account_scope TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
            checkpoint TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, worker_id TEXT, lease_until REAL,
            next_run REAL NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE UNIQUE INDEX idx_task_active ON automation_tasks(dedupe_key) WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED')",
        "CREATE INDEX idx_task_queue ON automation_tasks(state,next_run,created_at)",
        """CREATE TABLE worker_heartbeats(
            worker_id TEXT PRIMARY KEY, task_id TEXT REFERENCES automation_tasks(id), process_id INTEGER,
            state TEXT NOT NULL, heartbeat REAL NOT NULL, last_progress REAL NOT NULL,
            operation TEXT NOT NULL, current_url TEXT, memory_bytes INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0, timeout_status TEXT)""",
        """CREATE TABLE task_checkpoints(
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES automation_tasks(id),
            payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
        "CREATE INDEX idx_checkpoint_task ON task_checkpoints(task_id,created_at)",
        """CREATE TABLE worker_failures(
            id TEXT PRIMARY KEY, task_id TEXT REFERENCES automation_tasks(id), worker_id TEXT,
            failure_type TEXT NOT NULL, recovery_action TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE application_submissions(
            id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
            run_id TEXT REFERENCES automation_runs(id), identity_key TEXT NOT NULL,
            state TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE UNIQUE INDEX idx_submission_pending ON application_submissions(identity_key) WHERE state IN ('SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED','CONFIRMED')",
        """CREATE TABLE document_uploads(
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES automation_runs(id),
            document_version_id TEXT REFERENCES document_versions(id), question TEXT NOT NULL,
            state TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""",
        """CREATE TABLE source_scans(
            id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
            state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            jobs_found INTEGER NOT NULL DEFAULT 0, new_jobs INTEGER NOT NULL DEFAULT 0, error TEXT)""",
        "CREATE INDEX idx_source_scan ON source_scans(source_id,started_at)",
        "ALTER TABLE jobs ADD COLUMN application_deadline TEXT",
        "ALTER TABLE jobs ADD COLUMN deadline_timezone TEXT",
        "ALTER TABLE jobs ADD COLUMN deadline_source TEXT",
        "ALTER TABLE jobs ADD COLUMN deadline_state TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "ALTER TABLE jobs ADD COLUMN deadline_verified_at TEXT",
        "ALTER TABLE jobs ADD COLUMN availability TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "ALTER TABLE jobs ADD COLUMN last_verified TEXT",
        "CREATE INDEX idx_jobs_availability_deadline ON jobs(availability,application_deadline)",
        """CREATE TABLE professional_contacts(
            id TEXT PRIMARY KEY, full_name TEXT NOT NULL, company TEXT NOT NULL,
            company_id TEXT REFERENCES companies(id), role TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', profile_url TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}', last_verified TEXT,
            relevance REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'NOT_CONTACTED',
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE contact_job_links(
            contact_id TEXT REFERENCES professional_contacts(id), job_id TEXT REFERENCES jobs(id),
            evidence TEXT NOT NULL DEFAULT '', PRIMARY KEY(contact_id,job_id))""",
        """CREATE TABLE outreach_messages(
            id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES professional_contacts(id),
            job_id TEXT REFERENCES jobs(id), kind TEXT NOT NULL, body TEXT NOT NULL,
            fact_ids TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'MESSAGE_PREPARED',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE UNIQUE INDEX idx_outreach_sent ON outreach_messages(contact_id,job_id,kind) WHERE state='CONTACTED'",
        """CREATE TABLE outreach_events(
            id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES outreach_messages(id),
            state TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE contact_followups(
            id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES professional_contacts(id),
            due_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL)""",
    ]
    bind = op.get_bind()
    for statement in statements:
        if statement.startswith('ALTER TABLE jobs ADD COLUMN '):
            column = statement.split()[5]
            columns = {r[1] for r in bind.exec_driver_sql('PRAGMA table_info(jobs)')}
            if column in columns:
                continue
        statement = statement.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ').replace('CREATE UNIQUE INDEX ', 'CREATE UNIQUE INDEX IF NOT EXISTS ').replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ')
        bind.exec_driver_sql(statement)
    # Backfill published metadata without replacing any existing job/application.
    import json
    from jobagent.job_validity import deadline_evidence
    for row in bind.exec_driver_sql('SELECT id,url,description,metadata FROM jobs').mappings().all():
        raw = dict(row)
        raw['metadata'] = json.loads(raw['metadata'] or '{}')
        result = deadline_evidence(raw)
        bind.exec_driver_sql('UPDATE jobs SET application_deadline=?,deadline_timezone=?,deadline_source=?,deadline_state=?,deadline_verified_at=? WHERE id=?',
                            (result['application_deadline'], result['deadline_timezone'], result['deadline_source'], result['deadline_state'], result['deadline_verified_at'], raw['id']))


def downgrade():
    raise RuntimeError("Execution and recruitment history must be retained")
