"""SQLite persistence. Every operation owns a short-lived connection.

JSON is explicit text at the persistence boundary; API consumers use decode_row.
Schema changes are applied through Alembic, including frozen sidecar builds.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return str(uuid4())


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


JSON_COLUMNS = {
    "config",
    "metadata",
    "details",
    "skills",
    "match_details",
    "locations",
    "preferred_roles",
    "blacklist_keywords",
    "salary_preference",
    "fact_ids",
    "checkpoint",
    "evidence",
    "payload",
    "rule",
    "facts_used",
}
BOOL_COLUMNS = {"locked", "verified", "enabled", "auto_apply", "remote", "approved", "interested", "section_consent"}


def decode_row(row):
    if row is None:
        return None
    result = dict(row)
    for key, value in result.items():
        if key in JSON_COLUMNS and isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except (ValueError, TypeError):
                pass
        elif key in BOOL_COLUMNS and value is not None:
            result[key] = bool(value)
    return result


def historical_facts(db, fact_ids, at):
    """Resolve provenance to the value that existed when a document/answer was saved."""
    if isinstance(fact_ids, str):
        fact_ids = json.loads(fact_ids)
    result = []
    for fact_id in fact_ids:
        fact = db.one("SELECT * FROM facts WHERE id=? AND updated_at<=?", (fact_id, at))
        if fact is None:
            previous = db.one(
                "SELECT payload FROM fact_history WHERE fact_id=? AND valid_from<=? AND valid_until>=? ORDER BY valid_from DESC LIMIT 1",
                (fact_id, at, at),
            )
            fact = json.loads(previous["payload"]) if previous else None
        if fact:
            result.append(decode_row(fact))
    return result


SCHEMA = """
CREATE TABLE candidates(id TEXT PRIMARY KEY,name TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE facts(id TEXT PRIMARY KEY,candidate_id TEXT REFERENCES candidates(id),category TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_status IN ('unverified','verified','rejected')),locked INTEGER NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX idx_facts_category ON facts(category,verification_status);
CREATE TABLE relationships(id TEXT PRIMARY KEY,from_fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,to_fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,kind TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(from_fact_id,to_fact_id,kind));
CREATE TABLE education(id TEXT PRIMARY KEY,fact_id TEXT UNIQUE REFERENCES facts(id) ON DELETE CASCADE,institution TEXT,qualification TEXT,start_date TEXT,end_date TEXT);
CREATE TABLE experiences(id TEXT PRIMARY KEY,fact_id TEXT UNIQUE REFERENCES facts(id) ON DELETE CASCADE,company TEXT,title TEXT,start_date TEXT,end_date TEXT);
CREATE TABLE projects(id TEXT PRIMARY KEY,fact_id TEXT UNIQUE REFERENCES facts(id) ON DELETE CASCADE,name TEXT,url TEXT);
CREATE TABLE skills(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE);
CREATE TABLE candidate_skills(candidate_id TEXT REFERENCES candidates(id),skill_id TEXT REFERENCES skills(id),fact_id TEXT REFERENCES facts(id) ON DELETE CASCADE,PRIMARY KEY(candidate_id,skill_id));
CREATE TABLE preferences(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,value TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE saved_answers(id TEXT PRIMARY KEY,question TEXT NOT NULL,answer TEXT NOT NULL,fact_ids TEXT NOT NULL DEFAULT '[]',verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX idx_saved_question ON saved_answers(question);
CREATE TABLE documents(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,job_id TEXT REFERENCES jobs(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE document_versions(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,version INTEGER NOT NULL,path TEXT NOT NULL,filename TEXT NOT NULL,mime_type TEXT NOT NULL,content_hash TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',fact_ids TEXT NOT NULL DEFAULT '[]',approved INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(document_id,version));
CREATE INDEX idx_document_versions_doc ON document_versions(document_id,version DESC);
CREATE TABLE companies(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,careers_url TEXT NOT NULL DEFAULT '',locations TEXT NOT NULL DEFAULT '[]',preferred_roles TEXT NOT NULL DEFAULT '[]',priority INTEGER NOT NULL DEFAULT 50,notes TEXT NOT NULL DEFAULT '',poll_minutes INTEGER NOT NULL DEFAULT 360,auto_apply INTEGER NOT NULL DEFAULT 0,salary_preference TEXT NOT NULL DEFAULT '{}',blacklist_keywords TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE company_domains(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,domain TEXT NOT NULL UNIQUE,approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE company_watch_rules(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,rule TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE jobs(id TEXT PRIMARY KEY,title TEXT NOT NULL,company TEXT NOT NULL,company_id TEXT REFERENCES companies(id),location TEXT NOT NULL DEFAULT '',url TEXT NOT NULL,canonical_url TEXT NOT NULL UNIQUE,identity_key TEXT NOT NULL UNIQUE,application_url TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT 'manual',ats TEXT NOT NULL DEFAULT 'generic',match_score REAL NOT NULL DEFAULT 0,priority_score REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'DISCOVERED',skills TEXT NOT NULL DEFAULT '[]',salary_min REAL,salary_max REAL,currency TEXT,remote INTEGER,experience_level TEXT,posted_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,match_details TEXT NOT NULL DEFAULT '{}',metadata TEXT NOT NULL DEFAULT '{}');
CREATE INDEX idx_jobs_status_match ON jobs(status,match_score DESC);
CREATE INDEX idx_jobs_company ON jobs(company_id,company);
CREATE INDEX idx_jobs_created ON jobs(created_at DESC);
CREATE TABLE job_sources(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,url TEXT NOT NULL UNIQUE,external_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE job_skills(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,skill_id TEXT NOT NULL REFERENCES skills(id),required INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(job_id,skill_id));
CREATE TABLE job_snapshots(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,content_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(job_id,content_hash));
CREATE TABLE job_matches(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,search_profile_id TEXT REFERENCES search_profiles(id) ON DELETE SET NULL,score REAL NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX idx_job_matches_job ON job_matches(job_id,created_at DESC);
CREATE TABLE job_duplicates(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,duplicate_url TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(job_id,duplicate_url));
CREATE TABLE job_feedback(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,interested INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE TABLE applications(id TEXT PRIMARY KEY,job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),mode TEXT NOT NULL DEFAULT 'review' CHECK(mode IN ('manual','review','auto')),status TEXT NOT NULL DEFAULT 'PREPARING',notes TEXT NOT NULL DEFAULT '',quality_score REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX idx_applications_status ON applications(status,updated_at DESC);
CREATE TABLE application_events(id TEXT PRIMARY KEY,application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,status TEXT NOT NULL,message TEXT NOT NULL,origin TEXT NOT NULL DEFAULT 'manual',created_at TEXT NOT NULL);
CREATE INDEX idx_events_application ON application_events(application_id,created_at);
CREATE TABLE application_answers(id TEXT PRIMARY KEY,application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,question TEXT NOT NULL,answer TEXT NOT NULL,fact_ids TEXT NOT NULL DEFAULT '[]',verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE application_documents(application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,document_version_id TEXT NOT NULL REFERENCES document_versions(id),kind TEXT NOT NULL DEFAULT 'cv',PRIMARY KEY(application_id,document_version_id));
CREATE TABLE application_emails(application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,email_id TEXT NOT NULL REFERENCES email_messages(id) ON DELETE CASCADE,PRIMARY KEY(application_id,email_id));
CREATE TABLE email_messages(id TEXT PRIMARY KEY,provider TEXT NOT NULL,external_id TEXT NOT NULL,subject TEXT NOT NULL DEFAULT '',sender TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',received_at TEXT NOT NULL,classification TEXT,confidence REAL NOT NULL DEFAULT 0,application_id TEXT REFERENCES applications(id),deadline TEXT,metadata TEXT NOT NULL DEFAULT '{}',UNIQUE(provider,external_id));
CREATE INDEX idx_emails_application ON email_messages(application_id,received_at DESC);
CREATE INDEX idx_emails_deadline ON email_messages(deadline);
CREATE TABLE email_classifications(id TEXT PRIMARY KEY,email_id TEXT NOT NULL REFERENCES email_messages(id) ON DELETE CASCADE,classification TEXT NOT NULL,confidence REAL NOT NULL,evidence TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL);
CREATE TABLE automation_runs(id TEXT PRIMARY KEY,application_id TEXT NOT NULL REFERENCES applications(id),status TEXT NOT NULL,engine TEXT NOT NULL DEFAULT 'deterministic',checkpoint TEXT NOT NULL DEFAULT '{}',error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX idx_runs_application ON automation_runs(application_id,created_at DESC);
CREATE TABLE automation_steps(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,operation TEXT NOT NULL,target TEXT,confidence REAL,details TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE INDEX idx_steps_run ON automation_steps(run_id,created_at);
CREATE TABLE human_tasks(id TEXT PRIMARY KEY,application_id TEXT REFERENCES applications(id),kind TEXT NOT NULL,question TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',answer TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX idx_tasks_status ON human_tasks(status,created_at DESC);
CREATE TABLE search_profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,config TEXT NOT NULL DEFAULT '{}',poll_minutes INTEGER NOT NULL DEFAULT 360,last_checked TEXT,last_error TEXT,jobs_found INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE scheduler_tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,next_run TEXT,last_run TEXT,status TEXT NOT NULL DEFAULT 'idle',error TEXT,config TEXT NOT NULL DEFAULT '{}');
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE integrations(id TEXT PRIMARY KEY,provider TEXT NOT NULL UNIQUE,config TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'disconnected',last_sync TEXT,last_error TEXT);
CREATE TABLE secret_references(id TEXT PRIMARY KEY,provider TEXT NOT NULL,keychain_service TEXT NOT NULL,account TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(keychain_service,account));
CREATE VIRTUAL TABLE jobs_fts USING fts5(title,company,description,content='jobs',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER jobs_ai AFTER INSERT ON jobs BEGIN INSERT INTO jobs_fts(rowid,title,company,description) VALUES(new.rowid,new.title,new.company,new.description); END;
CREATE TRIGGER jobs_ad AFTER DELETE ON jobs BEGIN INSERT INTO jobs_fts(jobs_fts,rowid,title,company,description) VALUES('delete',old.rowid,old.title,old.company,old.description); END;
CREATE TRIGGER jobs_au AFTER UPDATE OF title,company,description ON jobs BEGIN INSERT INTO jobs_fts(jobs_fts,rowid,title,company,description) VALUES('delete',old.rowid,old.title,old.company,old.description); INSERT INTO jobs_fts(rowid,title,company,description) VALUES(new.rowid,new.title,new.company,new.description); END;
"""

_migration_lock = threading.Lock()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _migration_lock:
            self._migrate()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _migrate(self):
        from alembic import command
        from alembic.config import Config

        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        cfg = Config()
        cfg.set_main_option("script_location", str(base / "migrations"))
        cfg.set_main_option("sqlalchemy.url", "sqlite:///" + str(self.path).replace("%", "%%"))
        command.upgrade(cfg, "head")
        with self.transaction() as conn:
            if not conn.execute("SELECT id FROM candidates LIMIT 1").fetchone():
                conn.execute("INSERT INTO candidates VALUES(?,?,?,?)", (uid(), "", now(), now()))

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params=()) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql: str, params=()) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def execute(self, sql: str, params=()):
        with self.transaction() as conn:
            return conn.execute(sql, params).lastrowid

    def event(self, application_id: str, status: str, message: str, origin="manual", *, conn=None):
        with nullcontext(conn) if conn is not None else self.transaction() as conn:
            app = conn.execute("SELECT status FROM applications WHERE id=?", (application_id,)).fetchone()
            if app is None:
                raise ValueError("Application not found")
            ranks = {
                "DISCOVERED": 0,
                "SCORED": 0,
                "SHORTLISTED": 0,
                "PREPARING": 0,
                "NEEDS_REVIEW": 0,
                "APPLYING": 1,
                "APPLIED": 2,
                "CONFIRMED": 3,
                "RECRUITER_SCREEN": 4,
                "ASSESSMENT": 5,
                "TECHNICAL_TEST": 5,
                "INTERVIEW": 6,
                "FINAL_INTERVIEW": 7,
                "OFFER": 8,
                "REJECTED": 9,
                "WITHDRAWN": 9,
            }
            current = app["status"]
            progress = origin != "email" or (
                current not in {"REJECTED", "WITHDRAWN"} and ranks.get(status, 99) >= ranks.get(current, 0)
            )
            stamp = now()
            conn.execute(
                "INSERT INTO application_events VALUES(?,?,?,?,?,?)",
                (uid(), application_id, status, message, origin, stamp),
            )
            if progress:
                conn.execute(
                    "UPDATE applications SET status=?,updated_at=? WHERE id=?", (status, stamp, application_id)
                )


def get_db(request):
    return request.app.state.db
