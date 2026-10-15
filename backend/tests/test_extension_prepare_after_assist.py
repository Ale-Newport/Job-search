"""INT-01 (the rest): Prepare on a job the assistant touched first.

The assistant writes a BARE application (``PREPARING``, no task, no draft, origin ``extension``) the first time it saves
something. ``core.prepare_application`` used to read any existing application as 'already prepared': the user pressed
Prepare and got no draft CV, no 'review your facts' task and a job still ``DISCOVERED``. A bare row the assistant made is
completed the way a fresh row is; a row anyone else made, a row that already has work attached, and a row that was sent are
left exactly as they are."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import make_app  # noqa: E402
from jobagent.core import prepare_application  # noqa: E402
from jobagent.db import now, uid  # noqa: E402
from jobagent.discovery import ingest_job  # noqa: E402
from jobagent.extension.session import ensure_extension_application  # noqa: E402
from jobagent.tracking import record_application  # noqa: E402

PAGE = dict(url="https://boards.greenhouse.io/acmerobotics/jobs/4001", application_url="https://boards.greenhouse.io/acmerobotics/jobs/4001",
            title="Software Engineer", company="Acme Robotics", location="London", ats="greenhouse",
            description="Build and run reliable robot fleet services in Python. " * 6)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
def app(tmp_path):
    app = make_app(tmp_path)
    db, stamp = app.state.db, now()
    # a verified fact outside the answer engine's knowledge base: the evidence a draft CV is written from
    candidate = db.one("SELECT id FROM candidates")
    db.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (uid(), candidate["id"] if candidate else None, "skill", "python", "Python", "test", "verified", 0, "", stamp, stamp))
    return app


def rows(db, sql, *args):
    return db.query(sql, args)


def prepared_shape(db, application_id, job_id):
    """Everything a Prepare leaves behind: the status the user sees and the work waiting for them."""
    return {
        "application": rows(db, "SELECT status,mode FROM applications WHERE id=?", application_id)[0],
        "job": rows(db, "SELECT status FROM jobs WHERE id=?", job_id)[0]["status"],
        "tasks": [(t["kind"], t["state"] if "state" in t else None) for t in rows(
            db, "SELECT * FROM human_tasks WHERE application_id=? ORDER BY created_at", application_id)],
        "events": [(e["status"], e["origin"]) for e in rows(
            db, "SELECT status,origin FROM application_events WHERE application_id=? ORDER BY rowid", application_id)],
    }


def test_prepare_completes_the_row_the_assistant_made_like_a_fresh_one(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    fresh_job, _ = ingest_job(db, {**PAGE, "url": "https://boards.greenhouse.io/acmerobotics/jobs/4002",
                                   "application_url": "https://boards.greenhouse.io/acmerobotics/jobs/4002", "source": "greenhouse"})
    fresh = prepare_application(db, fresh_job["id"], "review", None, app.state.data_dir)
    assert fresh["duplicate"] is False and fresh["status"] == "NEEDS_REVIEW"

    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is False and done["id"] == assisted["id"] and done["status"] == "NEEDS_REVIEW"
    assert done["draft"] is not None and fresh["draft"] is not None  # the reviewable draft CV a fresh Prepare makes
    assert "Application: Software Engineer · Acme Robotics" in done["draft"]["text"] and "Python" in done["draft"]["text"]
    assert done["draft"]["approved"] is False and done["draft"]["kind"] == "cv"
    mine, theirs = prepared_shape(db, assisted["id"], assisted["job_id"]), prepared_shape(db, fresh["id"], fresh_job["id"])
    assert mine["job"] == theirs["job"] == "SHORTLISTED"
    assert mine["application"] == theirs["application"] == {"status": "NEEDS_REVIEW", "mode": "review"}
    assert mine["tasks"] == theirs["tasks"] and [t[0] for t in mine["tasks"]] == ["PREPARATION_REVIEW"]
    # the history keeps what the assistant wrote and adds what Prepare did, in order
    assert mine["events"] == [("PREPARING", "extension"), ("PREPARING", "manual"), ("NEEDS_REVIEW", "system")]
    assert theirs["events"] == [("PREPARING", "manual"), ("NEEDS_REVIEW", "system")]


def test_prepare_keeps_the_users_choice_of_mode(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    assert assisted["mode"] == "review"
    done = prepare_application(db, assisted["job_id"], "auto", None, app.state.data_dir)
    assert done["mode"] == "auto" and rows(db, "SELECT mode FROM applications WHERE id=?", assisted["id"])[0]["mode"] == "auto"


def test_preparing_twice_prepares_once(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    first = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    again = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert first["duplicate"] is False and again["duplicate"] is True and again["id"] == first["id"]
    assert len(rows(db, "SELECT 1 FROM human_tasks WHERE application_id=?", assisted["id"])) == 1
    assert "draft" not in again  # nothing was written a second time
    assert [e["origin"] for e in rows(db, "SELECT origin FROM application_events WHERE application_id=? ORDER BY rowid",
                                      assisted["id"])] == ["extension", "manual", "system"]


def test_what_the_assistant_saved_survives_prepare(app):
    from jobagent.application_profile import remember_answer
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    fact_id = remember_answer(db, assisted, "Favourite colour", "Green", reusable=False, skip=False,
                              element={"label": "Favourite colour"}, direct_edit=True)
    db.execute("INSERT INTO application_answers(id,application_id,question,answer,fact_ids,verified,created_at) VALUES(?,?,?,?,?,1,?)",
               (uid(), assisted["id"], "Favourite colour", "Green", f'["{fact_id}"]', now()))
    before = (rows(db, "SELECT question,answer FROM application_answers"), rows(db, "SELECT * FROM browser_answers"))
    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is False and before[0] and before[1]
    assert (rows(db, "SELECT question,answer FROM application_answers"), rows(db, "SELECT * FROM browser_answers")) == before


def test_a_row_that_was_sent_is_never_reopened_by_prepare(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    record_application(db, assisted["job_id"], None, "sent", origin="extension")
    before = prepared_shape(db, assisted["id"], assisted["job_id"])
    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is True and done["status"] == "APPLIED"
    assert prepared_shape(db, assisted["id"], assisted["job_id"]) == before


def test_a_row_somebody_else_made_is_left_alone(app):
    """Only the assistant's own bare row is completed: a PREPARING row with a different origin is somebody's work in progress."""
    db = app.state.db
    job, _ = ingest_job(db, {**PAGE, "source": "greenhouse"})
    stamp = now()
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('a-other',?,'manual','PREPARING',?,?)",
               (job["id"], stamp, stamp))
    db.execute("INSERT INTO application_events VALUES(?,?,?,?,?,?)", (uid(), "a-other", "PREPARING", "Created elsewhere", "manual", stamp))
    before = prepared_shape(db, "a-other", job["id"])
    done = prepare_application(db, job["id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is True and done["mode"] == "manual"
    assert prepared_shape(db, "a-other", job["id"]) == before


def test_a_row_that_already_has_work_attached_is_left_alone(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    db.execute("INSERT INTO human_tasks(id,application_id,kind,question,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (uid(), assisted["id"], "PREPARATION_REVIEW", "Already waiting", now(), now()))
    before = prepared_shape(db, assisted["id"], assisted["job_id"])
    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is True and prepared_shape(db, assisted["id"], assisted["job_id"]) == before


def test_a_row_with_a_run_is_left_alone(app):
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    db.execute("INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) "
               "VALUES(?,?,'running','deterministic','{}',?,?)", (uid(), assisted["id"], now(), now()))
    before = prepared_shape(db, assisted["id"], assisted["job_id"])
    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)
    assert done["duplicate"] is True and prepared_shape(db, assisted["id"], assisted["job_id"]) == before


def test_two_prepares_at_once_still_prepare_once(app):
    """The first call marks the row (its task is written in the transaction that adopts it): a second, concurrent call sees a row
    that already has work attached and makes nothing."""
    import threading
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    results = []

    def go():
        results.append(prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir)["duplicate"])

    threads = [threading.Thread(target=go) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True, True, True]
    assert len(rows(db, "SELECT 1 FROM human_tasks WHERE application_id=?", assisted["id"])) == 1


def test_the_section_consent_entry_point_completes_it_too(app):
    """'Assisted apply' (section consent) goes through the same function: the row it finds is completed, then consent is set."""
    db = app.state.db
    assisted = ensure_extension_application(db, **PAGE)
    done = prepare_application(db, assisted["job_id"], "review", None, app.state.data_dir, section_consent=True, assisted_autofill=True)
    assert done["duplicate"] is False and done["id"] == assisted["id"]
    row = rows(db, "SELECT section_consent,assisted_autofill,mode FROM applications WHERE id=?", assisted["id"])[0]
    assert (row["section_consent"], row["assisted_autofill"], row["mode"]) == (1, 1, "review")
    tasks = rows(db, "SELECT question FROM human_tasks WHERE application_id=?", assisted["id"])
    assert len(tasks) == 1 and "Review each proposed section" in tasks[0]["question"]
