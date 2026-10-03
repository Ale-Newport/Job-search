"""Typed candidate knowledge with provenance, policies and answer audit."""

from alembic import op

revision = "0007_candidate_intelligence"
down_revision = "0006_browser_answers"
branch_labels = None
depends_on = None


def upgrade():
    for statement in (
        """CREATE TABLE IF NOT EXISTS knowledge_entities(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            aliases TEXT NOT NULL DEFAULT '[]', UNIQUE(kind,name))""",
        """CREATE TABLE IF NOT EXISTS knowledge_facts(
            fact_id TEXT PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL REFERENCES knowledge_entities(id), concept TEXT NOT NULL,
            value_type TEXT NOT NULL, unit TEXT, valid_from TEXT, valid_until TEXT,
            effective_at TEXT, expires_at TEXT, sensitivity TEXT NOT NULL DEFAULT 'professional',
            allowed_usage TEXT NOT NULL DEFAULT '["application"]', confidence REAL NOT NULL DEFAULT 1,
            source_priority INTEGER NOT NULL DEFAULT 50, evidence TEXT NOT NULL DEFAULT '{}',
            revision INTEGER NOT NULL DEFAULT 1)""",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_concept ON knowledge_facts(entity_id,concept)",
        """CREATE TABLE IF NOT EXISTS knowledge_relationships(
            id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES knowledge_entities(id),
            predicate TEXT NOT NULL, object_id TEXT NOT NULL REFERENCES knowledge_entities(id),
            fact_ids TEXT NOT NULL DEFAULT '[]', UNIQUE(subject_id,predicate,object_id))""",
        """CREATE TABLE IF NOT EXISTS candidate_policies(
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, value TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '{}',
            confirmed INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, UNIQUE(subject,scope))""",
        """CREATE TABLE IF NOT EXISTS answer_provenance(
            id TEXT PRIMARY KEY, application_id TEXT, question TEXT NOT NULL, context_hash TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_answer_context ON answer_provenance(application_id,context_hash)",
    ):
        op.get_bind().exec_driver_sql(statement)


def downgrade():
    raise RuntimeError("Candidate evidence and answer audit must be retained")
