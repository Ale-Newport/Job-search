import pytest

from jobagent.core import prepare_application
from jobagent.db import Database, now, uid
from jobagent.discovery import ingest_job
from jobagent.orchestrator import Orchestrator


class FailingSubmitBrowser:
    def __init__(self, *_):
        self.on_step = None

    async def fill_application(self, *_):
        return {
            "status": "needs_review",
            "answers": [],
            "steps": [],
            "questions": [],
            "snapshot_id": "observed-snapshot",
        }

    async def submit(self, *_, **__):
        raise RuntimeError("Connection was lost after Submit")

    def pause(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_auto_submit_exception_preserves_uncertainty_and_blocks_retry(tmp_path, monkeypatch):
    import jobagent.automation

    monkeypatch.setattr(jobagent.automation, "BrowserManager", FailingSubmitBrowser)
    db = Database(tmp_path / "database" / "meridian.sqlite3")
    job, _ = ingest_job(db, {"title": "Engineer", "company": "Example", "url": "https://example.test/position"})
    application = prepare_application(db, job["id"], "auto", data_dir=tmp_path)
    db.execute("INSERT INTO settings VALUES('automation_paused','false')")
    run_id = uid()
    db.execute(
        "INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'running',?,?)",
        (run_id, application["id"], now(), now()),
    )
    orchestrator = Orchestrator(db, tmp_path)
    # Isolate exception handling from independent confidence/domain/document gates.
    monkeypatch.setattr(orchestrator, "auto_gate", lambda *_: None)
    monkeypatch.setattr(orchestrator, "rate_gate", lambda *_: None)
    await orchestrator._fill(run_id, orchestrator.application(application["id"]), False)
    assert db.one("SELECT status FROM automation_runs WHERE id=?", (run_id,))["status"] == "unconfirmed"
    with pytest.raises(ValueError, match="uncertain"):
        orchestrator.launch(application["id"])
    assert db.one("SELECT COUNT(*) AS n FROM application_events WHERE status='APPLYING'")["n"] == 1
    assert db.one("SELECT id FROM human_tasks WHERE kind='SUBMISSION_UNCONFIRMED'")
