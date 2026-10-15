"""'Save to tracker' (UXR-01) and what the panel is told about an application that already exists (UXR-08).

The panel's menu posts the exact body below to ``/track``; a content-rig test that mocks the answer cannot catch a backend that
refuses it, so these tests post what the extension sends to the real routes and read what the database holds afterwards."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import bridge, http, make_app, posting  # noqa: E402
from jobagent.discovery import ingest_job  # noqa: E402
from jobagent.extension.schemas import JobSummary, TrackResponse  # noqa: E402
from jobagent.notifications import collect_notifications  # noqa: E402
from jobagent.tracking import record_application  # noqa: E402

SAVE = {"event": "save", "evidence": "", "notes": ""}  # byte for byte what controller.saveToTracker sends
URL = "https://boards.greenhouse.io/acmerobotics/jobs/4001"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
async def ctx(tmp_path):
    app = make_app(tmp_path)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        yield SimpleNamespace(app=app, client=client, sid=created["session_id"], created=created, db=app.state.db)
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def ready(ctx):
    run = ctx.app.state.extension_sessions.sessions[ctx.sid].research_run_id
    await ctx.app.state.extension_research.wait(run, 10)


def table(ctx, name):
    return ctx.db.query(f"SELECT * FROM {name}")


async def track(ctx, body):
    return await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json=body)


async def reopen(ctx):
    """The page opened again (a reload): the job summary the panel starts from."""
    return (await ctx.client.post("/v1/sessions", json=posting())).json()["job"]


# --------------------------------------------------------------------------------------------- UXR-01: save
async def test_save_to_tracker_keeps_the_posting_without_saying_it_was_sent(ctx):
    await ready(ctx)
    response = await track(ctx, SAVE)
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["state"] == "saved" and body["tracked"] == "preparing"
    assert TrackResponse(**body).state == "saved"
    job, application = table(ctx, "jobs"), table(ctx, "applications")
    assert len(job) == 1 and job[0]["source"] == "extension" and job[0]["company"] == "Acme Robotics"
    assert job[0]["status"] == "DISCOVERED"
    assert len(application) == 1 and application[0]["status"] == "PREPARING" and application[0]["job_id"] == job[0]["id"]
    assert (body["job_id"], body["application_id"]) == (job[0]["id"], application[0]["id"])
    events = ctx.db.query("SELECT status,origin FROM application_events WHERE application_id=?", (application[0]["id"],))
    assert [(e["status"], e["origin"]) for e in events] == [("PREPARING", "extension")]  # nothing says it was sent
    assert table(ctx, "human_tasks") == [] and table(ctx, "document_versions") == []
    assert ctx.db.query("SELECT key FROM notification_receipts") == [{"key": "match:" + job[0]["id"]}]
    assert collect_notifications(ctx.db) == []


async def test_save_is_remembered_by_the_page_and_by_the_next_visit(ctx):
    await ready(ctx)
    first = await track(ctx, SAVE)
    cursor = ctx.created["cursor"]
    polled = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": cursor, "wait_ms": 0})).json()
    tracked = [e for e in polled["events"] if e["type"] == "tracked"]
    assert tracked and tracked[-1]["tracked"] == "preparing"
    again = await reopen(ctx)
    assert again["tracked"] == "preparing" and again["application_id"] == first.json()["application_id"]
    assert ctx.db.one("SELECT application_id FROM extension_sessions WHERE id=?", (ctx.sid,))["application_id"] \
        == first.json()["application_id"]
    lines = ctx.db.query("SELECT kind,outcome,details FROM extension_events WHERE kind='track'")
    assert [(r["kind"], r["outcome"]) for r in lines] == [("track", "saved")]
    assert json.loads(lines[0]["details"])["event"] == "save"


async def test_saving_twice_changes_nothing_the_second_time(ctx):
    await ready(ctx)
    first = (await track(ctx, SAVE)).json()
    before = (table(ctx, "jobs"), table(ctx, "applications"), table(ctx, "application_events"))
    second = await track(ctx, SAVE)
    assert second.status_code == 200 and second.json()["state"] == "saved" and second.json()["tracked"] == "preparing"
    assert second.json()["application_id"] == first["application_id"]
    assert (table(ctx, "jobs"), table(ctx, "applications"), table(ctx, "application_events")) == before


async def test_save_completes_a_job_meridian_already_has_for_this_address(ctx):
    """Prospective (a job, no application): saving adds only the application; the discovered job is not rewritten."""
    await ready(ctx)
    found, _ = ingest_job(ctx.db, {"url": URL, "title": "Software Engineer", "company": "Acme Robotics", "location": "London",
                                   "description": "x" * 300, "source": "greenhouse"})
    assert (await reopen(ctx))["tracked"] == "prospective"
    body = (await track(ctx, SAVE)).json()
    assert body["state"] == "saved" and body["tracked"] == "preparing" and body["job_id"] == found["id"]
    assert len(table(ctx, "jobs")) == 1 and table(ctx, "jobs")[0]["source"] == "greenhouse"
    assert len(table(ctx, "applications")) == 1 and table(ctx, "applications")[0]["status"] == "PREPARING"


async def test_save_never_reopens_an_application_that_was_already_sent(ctx):
    await ready(ctx)
    assert (await track(ctx, {"event": "record"})).json()["state"] == "recorded"
    before = (table(ctx, "applications"), table(ctx, "application_events"), table(ctx, "jobs"))
    response = await track(ctx, SAVE)
    body = response.json()
    assert response.status_code == 200 and body["state"] == "already_recorded" and body["tracked"] == "applied"
    assert (table(ctx, "applications"), table(ctx, "application_events"), table(ctx, "jobs")) == before
    assert table(ctx, "applications")[0]["status"] == "APPLIED"


async def test_save_on_a_page_whose_application_was_sent_in_meridian_is_refused_the_same_way(ctx):
    """The page opened BEFORE the user recorded the application in Meridian: the session does not know yet."""
    await ready(ctx)
    found, _ = ingest_job(ctx.db, {"url": URL, "title": "Software Engineer", "company": "Acme Robotics",
                                   "description": "x" * 300, "source": "greenhouse"})
    record_application(ctx.db, found["id"], None, "sent from the app")
    body = (await track(ctx, SAVE)).json()
    assert body["state"] == "already_recorded" and body["tracked"] == "applied"
    assert len(table(ctx, "applications")) == 1 and table(ctx, "applications")[0]["status"] == "APPLIED"
    assert [e["status"] for e in table(ctx, "application_events")].count("PREPARING") == 0


async def test_save_without_a_known_company_is_refused_like_record_and_writes_nothing(ctx):
    await ready(ctx)
    session = ctx.app.state.extension_sessions.sessions[ctx.sid]
    session.company = ""
    session.title = ""
    session.report = {**session.report, "h1": "", "title": ""}
    response = await track(ctx, SAVE)
    assert response.status_code == 409 and response.json()["code"] == "conflict"
    assert table(ctx, "jobs") == [] and table(ctx, "applications") == []


async def test_save_then_record_is_the_normal_path_to_applied(ctx):
    await ready(ctx)
    await track(ctx, SAVE)
    recorded = (await track(ctx, {"event": "record"})).json()
    assert recorded["state"] == "recorded" and recorded["tracked"] == "applied"
    assert [(e["status"], e["origin"]) for e in ctx.db.query(
        "SELECT status,origin FROM application_events ORDER BY rowid")] == [("PREPARING", "extension"), ("APPLIED", "extension")]


async def test_an_unknown_event_is_still_refused(ctx):
    assert (await track(ctx, {"event": "bogus"})).status_code == 422


# --------------------------------------------------------------------------------------------- UXR-08: date and status
async def test_a_page_with_no_application_is_told_nothing_about_one(ctx):
    job = ctx.created["job"]
    assert set(job) == {"company", "title", "location", "job_id", "application_id", "tracked"}
    JobSummary(**job)
    assert job["tracked"] == "none"


async def test_an_application_in_preparation_has_a_status_and_no_applied_date(ctx):
    await ready(ctx)
    await track(ctx, SAVE)
    job = await reopen(ctx)
    JobSummary(**job)
    assert job["tracked"] == "preparing" and job["status"] == "PREPARING"
    assert "since" not in job and "tracked_at" not in job  # nothing was sent: there is no date to show


async def test_the_panel_is_told_when_the_application_was_sent(ctx):
    await ready(ctx)
    await track(ctx, {"event": "record", "applied_at": "2026-01-02T10:00:00Z"})
    job = await reopen(ctx)
    JobSummary(**job)
    assert job["tracked"] == "applied" and job["status"] == "APPLIED"
    assert job["since"].startswith("2026-01-02") and job["tracked_at"] == job["since"]


@pytest.mark.parametrize("later", ["REJECTED", "WITHDRAWN", "GHOSTED"])
async def test_a_closed_application_keeps_its_sent_date_and_says_how_it_ended(ctx, later):
    """'tracked' is 'applied' for every sent application (rejected ones included): the status is what tells them apart, and
    the date stays the day it was SENT, never the day it was rejected."""
    await ready(ctx)
    await track(ctx, {"event": "record", "applied_at": "2026-01-02T10:00:00Z"})
    application = table(ctx, "applications")[0]
    ctx.db.event(application["id"], later, "Closed in Meridian", "manual")
    job = await reopen(ctx)
    assert job["tracked"] == "applied" and job["status"] == later and job["since"].startswith("2026-01-02")


async def test_a_sent_application_without_a_recorded_date_still_says_since_when(ctx):
    """An application imported straight into a later status has no APPLIED event: the date it entered the tracker is the
    honest fallback, never nothing and never today."""
    await ready(ctx)
    found, _ = ingest_job(ctx.db, {"url": URL, "title": "Software Engineer", "company": "Acme Robotics",
                                   "description": "x" * 300, "source": "greenhouse"})
    ctx.db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('a-imported',?,'manual',"
                   "'INTERVIEW','2025-12-01T09:00:00+00:00','2025-12-01T09:00:00+00:00')", (found["id"],))
    job = await reopen(ctx)
    assert job["tracked"] == "applied" and job["status"] == "INTERVIEW" and job["since"].startswith("2025-12-01")


async def test_a_change_made_in_meridian_reaches_the_open_page_with_its_status_and_date(ctx):
    await ready(ctx)
    await track(ctx, {"event": "record", "applied_at": "2026-01-02T10:00:00Z"})
    cursor = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": 0, "wait_ms": 0})).json()["cursor"]
    ctx.db.event(table(ctx, "applications")[0]["id"], "REJECTED", "Rejected", "manual")
    body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": cursor, "wait_ms": 12000})).json()
    tracked = [e for e in body["events"] if e["type"] == "tracked"]
    # 'tracked' did not move (a rejected application was sent): the page still hears that the wording changed
    assert tracked and tracked[-1]["tracked"] == "applied" and tracked[-1]["status"] == "REJECTED"
    assert tracked[-1]["since"].startswith("2026-01-02")


async def test_the_date_and_status_belong_to_this_application_only(ctx):
    """Another posting's application never lends its date to this page."""
    await ready(ctx)
    other, _ = ingest_job(ctx.db, {"url": "https://boards.greenhouse.io/other/jobs/9", "title": "Other", "company": "Other Co",
                                   "description": "x" * 300, "source": "greenhouse"})
    record_application(ctx.db, other["id"], "2020-05-05T00:00:00+00:00", "")
    job = await reopen(ctx)
    assert job["tracked"] == "none" and "since" not in job and "status" not in job


async def test_saving_after_the_application_was_deleted_in_meridian_writes_it_again(ctx):
    """The page still remembers the application id; the row is gone. Save must not answer 'needs the company' (a lie): it
    writes the application again, exactly as for a first save."""
    await ready(ctx)
    first = (await track(ctx, SAVE)).json()
    ctx.db.execute("DELETE FROM application_events WHERE application_id=?", (first["application_id"],))
    ctx.db.execute("DELETE FROM applications WHERE id=?", (first["application_id"],))
    again = await track(ctx, SAVE)
    body = again.json()
    assert again.status_code == 200 and body["state"] == "saved" and body["tracked"] == "preparing"
    assert body["application_id"] != first["application_id"] and len(table(ctx, "applications")) == 1
    assert table(ctx, "applications")[0]["id"] == body["application_id"]


# --------------------------------------------------------------------------------------------- through the real bridge
# (the same fixtures the route tests use: a paired browser, bearer token, Origin check)
from test_extension_routes import real, writer_held  # noqa: E402,F401


async def test_the_real_bridge_accepts_what_the_panel_sends_and_answers_what_the_panel_reads(real):  # noqa: F811
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    sid = created["session_id"]
    await real.app.state.extension_research.wait(created["research"]["run_id"], 30)
    response = await real.client.post(f"/v1/sessions/{sid}/track", json=SAVE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"state", "job_id", "application_id", "tracked"}
    assert (body["state"], body["tracked"]) == ("saved", "preparing") and TrackResponse(**body)
    reopened = (await real.client.post("/v1/sessions", json=posting())).json()["job"]
    assert reopened["tracked"] == "preparing" and reopened["status"] == "PREPARING"
    JobSummary(**reopened)
    assert real.app.state.db.one("SELECT COUNT(*) AS n FROM applications WHERE status='APPLIED'")["n"] == 0


async def test_saving_to_the_tracker_needs_a_paired_browser(real):  # noqa: F811
    import uuid
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    for headers in ({"Authorization": ""}, {"Authorization": "Bearer mrd1." + str(uuid.uuid4()) + "." + "A" * 43}):
        denied = await real.client.post(f"/v1/sessions/{created['session_id']}/track", json=SAVE,
                                        headers={"Origin": real.headers["Origin"], **headers})
        assert denied.status_code == 401 and denied.json()["code"] == "unauthorized"
    assert real.app.state.db.query("SELECT 1 FROM jobs") == []


async def test_a_locked_database_is_said_calmly_and_nothing_is_half_saved(real, writer_held):  # noqa: F811
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    sid = created["session_id"]
    await real.app.state.extension_research.wait(created["research"]["run_id"], 30)
    with writer_held():
        refused = await real.client.post(f"/v1/sessions/{sid}/track", json=SAVE)
        assert refused.status_code == 503 and refused.json()["code"] == "unavailable", refused.text
        assert real.app.state.db.query("SELECT 1 FROM jobs") == [] and real.app.state.db.query("SELECT 1 FROM applications") == []
    again = await real.client.post(f"/v1/sessions/{sid}/track", json=SAVE)
    assert again.status_code == 200 and again.json()["state"] == "saved"
    assert len(real.app.state.db.query("SELECT 1 FROM applications")) == 1
