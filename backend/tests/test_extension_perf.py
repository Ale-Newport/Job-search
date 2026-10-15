"""GET /api/extension is polled every few seconds: what it reads must not grow with the audit trail (PERF-01)."""
import json

import pytest

from jobagent.db import Database
from jobagent.extension import api


@pytest.fixture
def database(tmp_path):
    (tmp_path / "database").mkdir()
    db = Database(tmp_path / "database/meridian.sqlite3")
    db.execute("INSERT INTO extension_clients(id,name,browser,origin,token_hash,version,created_at) VALUES"
               "('c1','Chrome','chrome','chrome-extension://aaaaaaaaaaaaaaaa','h1','1.0.0','2026-01-01T00:00:00+00:00')")
    return db


def fill(db, sessions=200, per_session=150):
    """~30,000 audit lines of the kinds the assistant writes."""
    kinds = [("suggest", "answered"), ("suggest", "needs_review"), ("accept", "accepted"), ("feedback", "accepted"),
             ("policy_block", "sensitive_blocked"), ("draft_ready", "answered"), ("skip", "skipped")]
    with db.transaction() as conn:
        for number in range(sessions):
            conn.execute("INSERT INTO extension_sessions(id,client_id,url,canonical_url,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?)", (f"s{number}", "c1", f"https://x.test/{number}", f"https://x.test/{number}",
                                                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
            conn.executemany(
                "INSERT INTO extension_events(id,session_id,kind,field_hash,outcome,details,created_at) VALUES(?,?,?,?,?,?,?)",
                [(f"e{number}-{i}", f"s{number}", *kinds[i % len(kinds)][:1], "", kinds[i % len(kinds)][1], "{}",
                  f"2026-02-{1 + i % 27:02d}T00:00:{i % 60:02d}+00:00") for i in range(per_session)])
    return sessions * per_session


def plan(db, sql, params=()):
    return " | ".join(row["detail"] for row in db.query("EXPLAIN QUERY PLAN " + sql, params))


def test_the_client_counters_are_one_indexed_pass_not_a_scan_of_every_event(database):
    fill(database)
    with_index = plan(database, api.CLIENT_COUNTERS_SQL)
    assert "SCAN e" not in with_index and "SCAN extension_events" not in with_index, with_index
    assert "COVERING INDEX" in with_index, with_index


def test_the_latest_error_is_found_through_the_index(database):
    fill(database)
    text = plan(database, api.LATEST_ERROR_ID_SQL, ("c1",))
    # only the error lines of each page are visited (a search on session + kind), never every event of the client
    assert "SCAN e" not in text and "idx_extension_event_kind (session_id=? AND kind=?)" in text, text


def test_the_saved_edit_scan_uses_the_partial_index(database):
    text = plan(database, f"SELECT created_at,details FROM extension_events WHERE 1=1 AND {api.SAVED_EDIT}")
    assert "idx_extension_event_edit" in text, text


def test_the_counters_are_still_exact(database):
    total = fill(database, sessions=40, per_session=70)
    client = api._clients(database, "1.0.0")[0]
    assert client["stats"]["pages"] == 40 and client["stats"]["suggestions"] == database.one(
        "SELECT COUNT(*) AS n FROM extension_events WHERE kind='suggest'")["n"]
    assert client["stats"]["insertions"] == database.one(
        "SELECT COUNT(*) AS n FROM extension_events WHERE kind IN ('accept','insert','inserted') "
        "OR (kind='feedback' AND outcome='accepted')")["n"]
    assert total == 2800 and client["last_error"] is None


def test_the_last_error_is_the_newest_one_of_any_page_of_the_client(database):
    fill(database, sessions=5, per_session=10)
    database.execute("INSERT INTO extension_events(id,session_id,kind,outcome,details,created_at) VALUES"
                     "('err1','s1','error','unknown',?,'2026-03-01T00:00:00+00:00'),"
                     "('err2','s3','error','unknown',?,'2026-03-02T00:00:00+00:00')",
                     (json.dumps({"message": "older"}), json.dumps({"message": "newest"})))
    assert api._clients(database, "1.0.0")[0]["last_error"] == "newest"


def test_an_existing_database_gets_the_indexes_at_startup(database):
    database.execute("DROP INDEX idx_extension_event_kind")
    database.execute("DROP INDEX idx_extension_event_edit")
    assert "idx_extension_event_kind" not in str(database.query("SELECT name FROM sqlite_master"))
    api.ensure_event_indexes(database)
    names = {row["name"] for row in database.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_extension_event_kind", "idx_extension_event_edit"} <= names
    api.ensure_event_indexes(database)  # idempotent
