from datetime import datetime, timedelta, timezone

from jobagent.core import prepare_application
from jobagent.db import Database, dumps, now, uid
from jobagent.discovery import ingest_job
from jobagent.notifications import collect_notifications


def test_notifications_deduplicate_and_respect_deadline_status_and_preference(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    clock = datetime.now(timezone.utc)
    job, _ = ingest_job(db, {"title": "Engineer", "company": "Example", "url": "https://example.test/jobs/1"})
    db.execute("UPDATE jobs SET match_score=90,match_details=? WHERE id=?", (dumps({"eligible": True}), job["id"]))
    application = prepare_application(db, job["id"], "review", data_dir=tmp_path)
    db.event(application["id"], "TECHNICAL_TEST", "Assessment received", "email")
    db.execute(
        "INSERT INTO email_messages(id,provider,external_id,received_at,application_id,deadline) VALUES(?,?,?,?,?,?)",
        (uid(), "fixture", "deadline", now(), application["id"], (clock + timedelta(hours=12)).isoformat()),
    )
    messages = collect_notifications(db, clock)
    assert any("deadlines" in item for item in messages)
    assert any("strong matches" in item for item in messages)
    assert any("review" in item for item in messages)
    assert any("recruitment updates" in item for item in messages)
    assert collect_notifications(Database(db.path), clock) == []
    db.execute("INSERT INTO settings VALUES('notifications_enabled','false')")
    db.event(application["id"], "INTERVIEW", "Interview received", "email")
    assert collect_notifications(db, clock) == []
    db.execute("UPDATE settings SET value='true' WHERE key='notifications_enabled'")
    assert len(collect_notifications(db, clock)) == 1
    db.execute("UPDATE email_messages SET deadline=?", ((clock + timedelta(hours=24)).isoformat(),))
    db.event(application["id"], "REJECTED", "Role closed")
    assert not any("deadline" in item for item in collect_notifications(db, clock))
