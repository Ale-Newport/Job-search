"""What the assistant remembers: corrections, scope rules, lazy job/application creation, tracking and documents."""
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, add_cv, bridge, el, http, make_app, posting, radios, suggest_body  # noqa: E402
from jobagent.application_profile import profile  # noqa: E402
from jobagent.db import now, uid  # noqa: E402
from jobagent.notifications import collect_notifications  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
async def ctx(tmp_path):
    app = make_app(tmp_path)
    model = ScriptedModel().install(app)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        yield SimpleNamespace(app=app, client=client, model=model, sid=created["session_id"], cursor=created["cursor"],
                              db=app.state.db)
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def ready(ctx):
    """Let the page's own research settle so the company is known (feedback needs a company and a role)."""
    run = ctx.app.state.extension_sessions.sessions[ctx.sid].research_run_id
    await ctx.app.state.extension_research.wait(run, 10)


async def scan(ctx, elements, **kwargs):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body(elements, **kwargs))
    assert response.status_code == 200, response.text
    return {s["node_id"]: s for s in response.json()["results"]}


async def feedback(ctx, suggestion, outcome="edited", **body):
    payload = {"question_id": suggestion["question_id"], "frame_key": suggestion["frame_key"], "outcome": outcome,
               "request_id": suggestion["request_id"], "suggestion_state": suggestion["state"], **body}
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def table(ctx, name):
    return ctx.db.query(f"SELECT * FROM {name}")


# --------------------------------------------------------------------------------------------- lazy rows
async def test_browsing_creates_no_job_and_no_application(ctx):
    await scan(ctx, [el(1, "textbox", "First name"), el(3, "textbox", "Favourite colour")])
    await ready(ctx)
    for name in ("jobs", "applications", "human_tasks", "browser_answers", "application_answers", "notification_receipts"):
        assert table(ctx, name) == [], name
    assert table(ctx, "extension_sessions")[0]["job_id"] is None


async def test_accepting_or_skipping_a_suggestion_teaches_nothing_and_creates_nothing(ctx):
    results = await scan(ctx, [el(1, "textbox", "First name"), el(3, "textbox", "Email", type="email")])
    for outcome, node in (("accepted", 1), ("rejected", 3), ("skipped", 3)):
        body = await feedback(ctx, results[node], outcome)
        assert body == {"saved": False, "application_id": None, "scope": None, "promoted_to_profile": False,
                        "message": None}
    assert table(ctx, "applications") == [] and table(ctx, "browser_answers") == []
    kinds = [r["kind"] for r in ctx.db.query("SELECT kind FROM extension_events WHERE kind IN ('accept','skip')")]
    assert sorted(kinds) == ["accept", "skip", "skip"]


async def test_the_first_saved_answer_creates_the_job_and_a_bare_application_without_side_effects(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    assert body["saved"] is True and body["scope"] == "application" and body["promoted_to_profile"] is False
    job = table(ctx, "jobs")[0]
    assert job["source"] == "extension" and job["company"] == "Acme Robotics" and job["title"] == "Software Engineer"
    assert job["url"].endswith("/acmerobotics/jobs/4001") and job["status"] == "DISCOVERED"
    application = table(ctx, "applications")[0]
    assert (application["mode"], application["status"], application["job_id"]) == ("review", "PREPARING", job["id"])
    assert body["application_id"] == application["id"]
    events = ctx.db.query("SELECT status,origin FROM application_events WHERE application_id=?", (application["id"],))
    assert [(e["status"], e["origin"]) for e in events] == [("PREPARING", "extension")]
    assert table(ctx, "human_tasks") == [] and table(ctx, "automation_runs") == [] and table(ctx, "document_versions") == []
    assert ctx.db.query("SELECT key FROM notification_receipts") == [{"key": "match:" + job["id"]}]
    assert collect_notifications(ctx.db) == []  # no "new strong match", no "items need your review"
    saved = table(ctx, "browser_answers")[0]
    assert saved["application_id"] == application["id"] and saved["reusable"] == 0 and saved["action"] == "answer"
    answer = table(ctx, "application_answers")[0]
    assert answer["answer"] == "Green" and answer["verified"] == 1 and json.loads(answer["fact_ids"]) == [saved["fact_id"]]
    assert table(ctx, "application_answer_targets")[0]["question_id"] == results[1]["question_id"]
    assert ctx.db.one("SELECT application_id,job_id FROM extension_sessions WHERE id=?", (ctx.sid,)) == {
        "application_id": application["id"], "job_id": job["id"]}
    second = await feedback(ctx, results[1], final_value="Blue", remember="application")
    assert second["application_id"] == application["id"] and len(table(ctx, "applications")) == 1
    current = [a for a in table(ctx, "application_answers") if a["answer"] == "Blue"]
    assert len(current) == 1 and len(table(ctx, "superseded_application_answers")) == 1  # history kept, current replaced


async def test_the_audit_line_of_a_saved_answer_names_the_answer_and_the_question(ctx):
    """api.extension_answer_ids / api.extension_memory_stamps label by what this line says: its kind, ``saved`` and the ids
    must not be renamed or reshaped."""
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    assert set(body) == {"saved", "application_id", "scope", "promoted_to_profile", "message"}  # bookkeeping stays inside
    lines = ctx.db.query("SELECT kind,outcome,details,session_id FROM extension_events WHERE kind='edit'")
    assert len(lines) == 1 and lines[0]["session_id"] == ctx.sid and lines[0]["outcome"] == "edited"
    details = json.loads(lines[0]["details"])
    answer, memory = table(ctx, "application_answers")[0], table(ctx, "browser_answers")[0]
    assert details["saved"] is True and details["scope"] == "application" and details["state"] == results[1]["state"]
    assert details["answer_id"] == answer["id"] and details["question_id"] == results[1]["question_id"]
    assert details["fact_id"] == memory["fact_id"] and details["memory_at"] == memory["updated_at"]
    assert "Green" not in lines[0]["details"]  # a line never carries what was typed


async def test_the_answer_and_its_audit_line_are_saved_together_or_not_at_all(ctx, monkeypatch):
    from jobagent.extension import feedback as feedback_module
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])

    def broken(*args, **kwargs):
        raise RuntimeError("the audit table is not writable")

    with monkeypatch.context() as patched:
        patched.setattr(feedback_module, "record_event", broken)
        with pytest.raises(RuntimeError):
            await feedback(ctx, results[1], final_value="Green", remember="application")
    assert table(ctx, "application_answers") == [] and table(ctx, "application_answer_targets") == []
    assert ctx.db.query("SELECT 1 FROM extension_events WHERE kind='edit'") == []  # no label without an answer either
    again = await feedback(ctx, results[1], final_value="Green", remember="application")  # the retry just works
    assert again["saved"] is True and len(table(ctx, "application_answers")) == 1
    assert len(ctx.db.query("SELECT 1 FROM extension_events WHERE kind='edit'")) == 1


async def test_a_typed_answer_next_to_the_assistants_save_never_takes_its_label(ctx):
    """The race described by the application-detail owner: a non-assistant answer written between the assistant's save and
    its audit line was labelled instead of the assistant's own. The line now names its answer, so the stamps of the two
    rows (here the typed one falls exactly between the answer and the line) cannot confuse them."""
    from jobagent.core import application_detail, save_answer
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    application = body["application_id"]
    save_answer(ctx.db, "Notice period", "One month", [], True, application)  # typed in Meridian, a moment later
    mine = table(ctx, "application_answers")[0]
    typed = ctx.db.one("SELECT id FROM application_answers WHERE question='Notice period'")
    line = ctx.db.one("SELECT created_at FROM extension_events WHERE kind='edit'")
    # put the typed answer between the assistant's answer and its audit line, as the old two-step save allowed
    middle = (datetime.fromisoformat(mine["created_at"]) + (datetime.fromisoformat(line["created_at"])
                                                             - datetime.fromisoformat(mine["created_at"])) / 2).isoformat()
    assert mine["created_at"] < middle < line["created_at"]
    ctx.db.execute("UPDATE application_answers SET created_at=? WHERE id=?", (middle, typed["id"]))
    detail = await application_detail(application, SimpleNamespace(app=ctx.app))
    labels = {a["question"]: a["origin"] for a in detail["answers"]}
    assert labels == {"Favourite colour": "extension", "Notice period": None}


async def test_the_profile_memory_says_when_the_assistant_wrote_it_and_forgets_after_any_other_edit(ctx):
    from jobagent.application_profile import remember_answer
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour"), el(3, "textbox", "Favourite food")])
    await feedback(ctx, results[1], final_value="Green", remember="application")
    await feedback(ctx, results[3], final_value="Rice", remember="application")
    memories = {m["answer"]: m for m in profile(ctx.db)["answers"]}
    assert {a: m["origin"] for a, m in memories.items()} == {"Green": "extension", "Rice": "extension"}
    application = ctx.db.one("SELECT a.*,j.company,j.location FROM applications a JOIN jobs j ON a.job_id=j.id")
    # the same memory corrected later in Auto Apply's browser (or the Profile) is no longer the assistant's
    remember_answer(ctx.db, application, memories["Rice"]["question"], "Pasta", direct_edit=True)
    after = {m["answer"]: m["origin"] for m in profile(ctx.db)["answers"]}
    assert after == {"Green": "extension", "Pasta": None}
    # and one saved through Meridian only was never labelled
    remember_answer(ctx.db, application, "Favourite film", "Alien", direct_edit=True)
    assert {m["answer"]: m["origin"] for m in profile(ctx.db)["answers"]}["Alien"] is None
    # saved again by the assistant: labelled again, whatever the earlier history was
    await feedback(ctx, results[3], final_value="Rice", remember="application")
    assert {m["answer"]: m["origin"] for m in profile(ctx.db)["answers"]}["Rice"] == "extension"


async def test_a_saved_answer_is_offered_next_time_with_its_origin(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    await feedback(ctx, results[1], final_value="Green", remember="application")
    again = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    assert again[1]["state"] == "VERIFIED" and again[1]["suggestion"]["text"] == "Green"
    assert again[1]["why"]["code"] == "saved_answer" and again[1]["knowledge_version"] != results[1]["knowledge_version"]
    # the same question on another employer's application does not see it
    other = (await ctx.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/other/jobs/9",
                                                                title="Data Engineer - Other Co", h1="Data Engineer",
                                                                jsonld=[]))).json()["session_id"]
    response = await ctx.client.post(f"/v1/sessions/{other}/suggest", json=suggest_body([el(1, "textbox", "Favourite colour")]))
    assert response.json()["results"][0]["state"] == "NEEDS_YOU"


async def test_the_page_hears_about_saved_knowledge_through_the_event_stream(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    await feedback(ctx, results[1], final_value="Green", remember="application")
    body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": ctx.cursor, "wait_ms": 0})).json()
    types = [e["type"] for e in body["events"]]
    assert "knowledge" in types and "tracked" in types
    tracked = next(e for e in body["events"] if e["type"] == "tracked")
    assert tracked["tracked"] == "preparing"


async def test_an_existing_job_row_found_by_url_is_reused_not_duplicated(ctx):
    from jobagent.discovery import ingest_job
    job, _ = ingest_job(ctx.db, {"url": "https://boards.greenhouse.io/acmerobotics/jobs/4001", "title": "Software Engineer",
                                 "company": "Acme Robotics", "location": "London", "source": "greenhouse",
                                 "description": "Build things. " * 30})
    created = (await ctx.client.post("/v1/sessions", json=posting())).json()
    assert created["job"]["job_id"] == job["id"] and created["job"]["tracked"] == "prospective"
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    await feedback(ctx, results[1], final_value="Green", remember="application")
    assert [j["id"] for j in table(ctx, "jobs")] == [job["id"]] and table(ctx, "jobs")[0]["source"] == "greenhouse"
    assert table(ctx, "applications")[0]["job_id"] == job["id"]


# --------------------------------------------------------------------------------------------- scope rules
async def test_a_profile_field_is_promoted_only_when_the_user_explicitly_chooses_reuse(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Preferred name"), el(3, "textbox", "Current job title")])
    kept = await feedback(ctx, results[1], final_value="Tess", remember="application")
    assert kept["saved"] and kept["scope"] == "application" and kept["promoted_to_profile"] is False
    fields = {f["key"]: f for f in profile(ctx.db)["fields"]}
    assert fields["preferred_name"]["value"] == ""
    promoted = await feedback(ctx, results[1], final_value="Tess", remember="reuse")
    assert promoted["saved"] and promoted["scope"] == "profile" and promoted["promoted_to_profile"] is True
    fields = {f["key"]: f for f in profile(ctx.db)["fields"]}
    assert fields["preferred_name"]["value"] == "Tess" and fields["preferred_name"]["state"] == "confirmed"
    by_default = await feedback(ctx, results[3], final_value="Engineer")  # `remember` omitted: the default is this application
    assert by_default["scope"] == "application" and by_default["promoted_to_profile"] is False
    assert {f["key"]: f for f in profile(ctx.db)["fields"]}["current_title"]["value"] == ""


async def test_reuse_is_downgraded_for_questions_that_must_never_travel(ctx):
    await ready(ctx)
    results = await scan(ctx, [
        el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea"),
        el(3, "textbox", "Are you able to commit to our hybrid working policy?"),
        el(5, "textbox", "Salary expectations"), el(7, "checkbox", "I certify the information is true", group="c"),
        el(9, "textbox", "Do you have a favourite hobby?")])
    for _ in range(6000):  # the background draft of the first question has settled: nothing else is running
        if ctx.app.state.extension_generation.active_total() == 0:
            break
        await asyncio.sleep(0.01)
    for node, text in ((1, "Because I like the mission."), (3, "Yes"), (5, "70000"), (7, "Yes"), (9, "Chess")):
        body = await feedback(ctx, results[node], final_value=text, remember="reuse")
        assert body["saved"] is True and body["scope"] == "application" and body["promoted_to_profile"] is False, node
        assert "this application only" in (body["message"] or "").lower(), node
    assert {r["reusable"] for r in table(ctx, "browser_answers")} == {0}
    assert not ctx.db.query("SELECT 1 FROM facts WHERE source LIKE 'application_profile:%'")


async def test_protected_answers_are_saved_only_after_an_explicit_confirmation(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "What is your gender?"),
                               *radios(3, "Are you legally authorized to work in the United Kingdom?", ["Yes", "No"],
                                       group="wa")])
    silent = await feedback(ctx, results[1], final_value="Non-binary")  # no `remember` key was sent
    assert silent["saved"] is False and "confirm" in silent["message"]
    assert table(ctx, "applications") == [] and table(ctx, "browser_answers") == []
    declined = await feedback(ctx, results[1], final_value="Non-binary", remember="none")
    assert declined["saved"] is False
    confirmed = await feedback(ctx, results[1], final_value="Non-binary", remember="application")
    assert confirmed["saved"] and confirmed["scope"] == "application"
    escalated = await feedback(ctx, results[3], final_values=["Yes"], remember="reuse")
    assert escalated["saved"] and escalated["scope"] == "application" and escalated["promoted_to_profile"] is False
    assert {r["reusable"] for r in table(ctx, "browser_answers")} == {0}
    still_blank = await scan(ctx, [el(1, "textbox", "What is your gender?")])
    assert still_blank[1]["state"] == "SENSITIVE_BLANK"  # the user's disclosure policy outranks a remembered answer
    ctx.db.execute("DELETE FROM candidate_policies WHERE subject='optional_demographics'")
    unused = await scan(ctx, [el(1, "textbox", "What is your gender?")])
    assert unused[1]["state"] == "NEEDS_YOU"  # without an explicit policy a remembered personal answer is not applied
    from jobagent.intelligence.store import policy
    policy(ctx.db, "personal_disclosures", "use_confirmed")
    again = await scan(ctx, [el(1, "textbox", "What is your gender?")])
    assert again[1]["state"] == "VERIFIED" and again[1]["insert"] == "explicit" and again[1]["why"]["code"] == "saved_answer"


async def test_clearing_needs_an_explicit_empty_value_and_stays_in_this_application_by_default(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Phone", type="tel"), el(3, "textbox", "Email", type="email")])
    nothing = await feedback(ctx, results[1], remember="application")  # no value at all: nothing happens
    assert nothing["saved"] is False and table(ctx, "applications") == []
    cleared = await feedback(ctx, results[1], final_value="", remember="application")
    assert cleared["saved"] and cleared["scope"] == "application"
    assert table(ctx, "browser_answers")[0]["action"] == "skip" and table(ctx, "browser_answers")[0]["reusable"] == 0
    after = await scan(ctx, [el(1, "textbox", "Phone", type="tel")])
    assert after[1]["state"] == "SENSITIVE_BLANK" and after[1]["why"]["code"] == "saved_answer"
    fields = {f["key"]: f for f in profile(ctx.db)["fields"]}
    assert fields["phone"]["value"] != ""  # the Profile still has the phone number
    other = (await ctx.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/other/jobs/9",
                                                                title="Data Engineer - Other Co", h1="Data Engineer",
                                                                jsonld=[]))).json()["session_id"]
    response = await ctx.client.post(f"/v1/sessions/{other}/suggest", json=suggest_body([el(1, "textbox", "Phone", type="tel")]))
    assert response.json()["results"][0]["state"] == "VERIFIED"


async def test_multi_value_answers_are_saved_as_one_list(ctx):
    await ready(ctx)
    languages = [el(1 + 2 * i, "checkbox", label, multiple=True, group="lang", context="Which languages do you speak?")
                 for i, label in enumerate(["English", "Spanish", "French"])]
    results = await scan(ctx, languages)
    body = await feedback(ctx, results[1], final_values=["English", "French"], remember="application")
    assert body["saved"] is True
    assert table(ctx, "application_answers")[0]["answer"] == "English, French"


async def test_secret_hidden_and_file_controls_are_never_saved(ctx):
    await ready(ctx)
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
        "question_id": "x", "frame_key": "top", "outcome": "edited", "final_value": "hunter2", "remember": "application",
        "element": el(1, "textbox", "Password", type="password")})
    assert response.status_code == 200 and response.json()["saved"] is False and "never saved" in response.json()["message"]
    for control in (el(2, "textbox", "Token", type="hidden"), el(3, "upload", "Resume")):
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
            "question_id": "y", "frame_key": "top", "outcome": "manual", "final_value": "secret.pdf",
            "remember": "application", "element": control})
        assert response.json()["saved"] is False
    assert table(ctx, "applications") == [] and table(ctx, "facts") == [] or not any(
        "hunter2" in str(f) for f in table(ctx, "facts"))
    events = " ".join(r["details"] for r in ctx.db.query("SELECT details FROM extension_events"))
    assert "hunter2" not in events and "secret.pdf" not in events


async def test_the_save_setting_can_forbid_remembering_anything(ctx):
    await ready(ctx)
    ctx.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('extension_save_corrections','\"never\"')")
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    assert body["saved"] is False and table(ctx, "applications") == []


async def test_an_unknown_question_needs_the_element_and_is_normalised_server_side(ctx):
    await ready(ctx)
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
        "question_id": "never-seen", "frame_key": "top", "outcome": "edited", "final_value": "x", "remember": "application"})
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    loose = el(1, "textbox", "ignored", name="job_application[first_name]")
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
        "question_id": "never-seen", "frame_key": "top", "outcome": "edited", "final_value": "Sam", "remember": "application",
        "element": loose})
    assert response.status_code == 200 and response.json()["saved"] is True
    assert table(ctx, "browser_answers")[0]["question"] == "first name"  # the adapter's question, not the client's label


async def test_without_a_known_company_nothing_is_saved_and_the_user_is_told_why(tmp_path):
    app = make_app(tmp_path)
    page = {"url": "https://apply.example-jobs.co/p/9", "title": "Backend Engineer", "h1": "Backend Engineer",
            "jsonld": [], "links": [], "meta": {}, "user": {}}
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json={"page": page, "tab_key": "t"})).json()["session_id"]
        run = app.state.extension_sessions.sessions[sid].research_run_id
        await app.state.extension_research.wait(run, 10)
        results = await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([el(1, "textbox", "Favourite colour")]))
        item = results.json()["results"][0]
        body = (await client.post(f"/v1/sessions/{sid}/feedback", json={
            "question_id": item["question_id"], "frame_key": "top", "outcome": "edited", "final_value": "Green",
            "remember": "application"})).json()
        assert body["saved"] is False and "company" in body["message"]
        assert app.state.db.query("SELECT 1 FROM jobs") == []
        fixed = await client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Acme Robotics"})
        assert fixed.status_code == 200 and fixed.json()["company"]["name"] == "Acme Robotics"
        await app.state.extension_research.wait(fixed.json()["run_id"], 10)
        body = (await client.post(f"/v1/sessions/{sid}/feedback", json={
            "question_id": item["question_id"], "frame_key": "top", "outcome": "edited", "final_value": "Green",
            "remember": "application"})).json()
        assert body["saved"] is True
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


# --------------------------------------------------------------------------------------------- tracking
async def test_noting_a_submit_or_a_confirmation_never_records_anything(ctx):
    for event, evidence in (("submit_clicked", ""), ("confirmation_seen", "Thank you for applying")):
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": event, "evidence": evidence})
        assert response.status_code == 200 and response.json()["state"] == "noted"
        assert response.json()["tracked"] == "none"
    assert table(ctx, "applications") == [] and table(ctx, "jobs") == []
    notes = ctx.db.query("SELECT kind,outcome FROM extension_events WHERE kind='track'")
    assert [n["outcome"] for n in notes] == ["noted", "noted"]


async def test_only_the_users_confirmed_record_marks_the_application_as_applied(ctx):
    await ready(ctx)
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record", "notes": "Sent from the form"})
    body = response.json()
    assert response.status_code == 200 and body["state"] == "recorded" and body["tracked"] == "applied"
    application = table(ctx, "applications")[0]
    assert application["status"] == "APPLIED" and application["id"] == body["application_id"]
    events = ctx.db.query("SELECT status,origin,message FROM application_events WHERE application_id=? ORDER BY rowid",
                          (application["id"],))
    assert [(e["status"], e["origin"]) for e in events] == [("PREPARING", "extension"), ("APPLIED", "extension")]
    assert events[-1]["message"] == "Application recorded from the browser assistant; confirmed by you."
    assert table(ctx, "jobs")[0]["status"] == "APPLIED" and table(ctx, "human_tasks") == []
    again = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record"})
    assert again.json()["state"] == "already_recorded" and len(table(ctx, "application_events")) == 2
    from jobagent.tracking import tracker_rows
    row = next(r for r in tracker_rows(ctx.db) if r["id"] == body["job_id"])
    assert row["submitted"] is True and row["assistant"]["browser"] == "chrome"


async def test_a_future_application_date_is_refused_and_a_past_one_is_kept(ctx):
    await ready(ctx)
    future = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record", "applied_at": "2999-01-01T00:00:00Z"})
    assert future.status_code == 422 and future.json()["code"] == "invalid" and table(ctx, "applications") == []
    garbage = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record", "applied_at": "yesterday"})
    assert garbage.status_code == 422
    ok = await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record", "applied_at": "2026-01-02T10:00:00Z"})
    assert ok.json()["state"] == "recorded"
    assert ctx.db.one("SELECT created_at FROM application_events WHERE status='APPLIED'")["created_at"].startswith("2026-01-02")


async def test_a_recorded_application_is_never_reopened_by_later_edits(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    await ctx.client.post(f"/v1/sessions/{ctx.sid}/track", json={"event": "record"})
    before = table(ctx, "application_events")
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    assert body["saved"] is False and "already recorded" in body["message"]
    assert table(ctx, "application_events") == before and table(ctx, "applications")[0]["status"] == "APPLIED"
    assert table(ctx, "browser_answers") == []
    created = (await ctx.client.post("/v1/sessions", json=posting())).json()
    assert created["job"]["tracked"] == "applied"


async def test_tracking_follows_changes_made_in_meridian_itself(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    await feedback(ctx, results[1], final_value="Green", remember="application")
    cursor = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": 0, "wait_ms": 0})).json()["cursor"]
    from jobagent.tracking import record_application
    record_application(ctx.db, table(ctx, "jobs")[0]["id"], None, "recorded in the app")
    body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": cursor, "wait_ms": 3000})).json()
    # UXR-08: the event also names the application's status and when it was sent (additive keys; see test_extension_tracker_save)
    applied = [e for e in body["events"] if e["type"] == "tracked" and e["tracked"] == "applied"]
    assert len(applied) == 1 and applied[0]["status"] == "APPLIED" and applied[0]["since"]


# --------------------------------------------------------------------------------------------- documents
async def test_only_approved_cvs_are_listed_and_downloadable(ctx):
    assert (await ctx.client.get("/v1/documents")).json() == []
    draft = add_cv(ctx.app, approved=False, filename="draft.pdf")
    cover = add_cv(ctx.app, approved=True, filename="cover.pdf", kind="cover_letter")
    old = add_cv(ctx.app, approved=True, filename="old.pdf", content=b"%PDF-old")
    new = add_cv(ctx.app, approved=True, filename="new.pdf", content=b"%PDF-new-version", kind="cv")
    listing = (await ctx.client.get("/v1/documents")).json()
    assert [d["id"] for d in listing] == [new["id"], old["id"]] or [d["id"] for d in listing] == [old["id"], new["id"]]
    assert sum(d["default"] for d in listing) == 1 and listing[0]["default"] is True
    assert set(listing[0]) == {"id", "name", "filename", "mime", "size", "kind", "version", "default", "scope"}
    assert {d["scope"] for d in listing} == {"base"}  # the user's own CVs; a CV written for one job is listed per page
    assert {d["id"] for d in listing}.isdisjoint({draft["id"], cover["id"]})  # a cover letter is not a CV: never listed
    content = (await ctx.client.get(f"/v1/documents/{new['id']}/content")).json()
    data = b"%PDF-new-version"
    assert content["sha256"] == hashlib.sha256(data).hexdigest() and content["size"] == len(data)
    import base64
    assert base64.b64decode(content["data_b64"]) == data and content["filename"] == "new.pdf"
    response = await ctx.client.get(f"/v1/documents/{draft['id']}/content")  # never an unapproved version
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    # an APPROVED cover letter is downloadable by its id (a field is only ever offered the one written for its job: CPL-14)
    letter = await ctx.client.get(f"/v1/documents/{cover['id']}/content")
    assert letter.status_code == 200 and letter.json()["filename"] == "cover.pdf"
    unapproved = add_cv(ctx.app, approved=False, filename="letter-draft.pdf", kind="cover_letter")
    assert (await ctx.client.get(f"/v1/documents/{unapproved['id']}/content")).status_code == 404
    assert (await ctx.client.get("/v1/documents/missing/content")).status_code == 404


async def test_the_default_cv_is_the_one_chosen_in_settings_or_attached_to_the_application(ctx):
    first = add_cv(ctx.app, filename="first.pdf", content=b"%PDF-1")
    second = add_cv(ctx.app, filename="second.pdf", content=b"%PDF-2")
    assert (await ctx.client.get("/v1/documents")).json()[0]["id"] == second["id"]  # newest by default
    ctx.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('default_document_version_id',?)", (json.dumps(first["id"]),))
    assert (await ctx.client.get("/v1/documents")).json()[0]["id"] == first["id"]


async def test_documents_are_checked_against_the_vault_before_they_leave(ctx):
    cv = add_cv(ctx.app, filename="cv.pdf")
    stored = Path(ctx.db.one("SELECT path FROM document_versions WHERE id=?", (cv["id"],))["path"])
    stored.write_bytes(b"tampered after approval")
    response = await ctx.client.get(f"/v1/documents/{cv['id']}/content")
    assert response.status_code == 404 and response.json()["code"] == "not_found"  # the hash no longer matches
    outside = ctx.app.state.data_dir.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-outside")
    escaped = add_cv(ctx.app, filename="escape.pdf")
    ctx.db.execute("UPDATE document_versions SET path=?,content_hash=? WHERE id=?",
                   (str(outside), hashlib.sha256(b"%PDF-outside").hexdigest(), escaped["id"]))
    assert (await ctx.client.get(f"/v1/documents/{escaped['id']}/content")).status_code == 404
    assert escaped["id"] not in [d["id"] for d in (await ctx.client.get("/v1/documents")).json()]  # not listed either


async def test_a_document_larger_than_ten_megabytes_is_refused(ctx):
    big = add_cv(ctx.app, filename="big.pdf", content=b"%PDF" + b"0" * (10 * 1024 * 1024 + 1))
    response = await ctx.client.get(f"/v1/documents/{big['id']}/content")
    assert response.status_code == 413 and response.json()["code"] == "too_large"


async def test_the_tracker_words_the_applied_event_by_who_recorded_it(ctx):
    """record_application owns the sentence: the assistant needs no relabelling and the other origins keep theirs."""
    from jobagent.discovery import ingest_job
    from jobagent.tracking import record_application
    expected = {"manual": "Application submitted externally; recorded by you.",
                "extension": "Application recorded from the browser assistant; confirmed by you.",
                "email": "Application identified from confirmation email."}
    for number, (origin, message) in enumerate(expected.items()):
        job, _ = ingest_job(ctx.db, {"url": f"https://boards.greenhouse.io/acme/jobs/{900 + number}", "title": "Engineer",
                                     "company": "Acme", "description": "x" * 40, "source": "manual"})
        applied = record_application(ctx.db, job["id"], "2026-01-02T10:00:00+00:00", "n", origin=origin)
        event = ctx.db.one("SELECT status,origin,message,created_at FROM application_events WHERE application_id=? "
                           "AND status='APPLIED'", (applied["id"],))
        assert (event["origin"], event["message"]) == (origin, message)
        assert event["created_at"].startswith("2026-01-02") and applied["already_recorded"] is False
        assert ctx.db.one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"] == "APPLIED"
        again = record_application(ctx.db, job["id"], None, "", origin=origin)
        assert again["already_recorded"] is True  # idempotent for every origin
    assert not ctx.db.query("SELECT 1 FROM application_events WHERE message LIKE '%after you sent it%'")


async def test_a_saved_answer_holds_no_control_characters_and_text_made_only_of_them_saves_nothing(ctx):
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour"), el(3, "textbox", "Favourite food")])
    body = await feedback(ctx, results[1], final_value="Gre\x00en\x07 \u202e", remember="application")
    assert body["saved"] is True
    assert [a["answer"] for a in table(ctx, "application_answers")] == ["Green"]
    assert [f["value"] for f in ctx.db.query("SELECT value FROM facts WHERE category='application_answer'")] == ["Green"]
    empty = await feedback(ctx, results[3], final_value="\x00\u202e\x07", remember="application")
    assert empty["saved"] is False and "readable" in empty["message"]
    assert len(table(ctx, "application_answers")) == 1 and len(table(ctx, "browser_answers")) == 1  # not taken for a clear
    again = await scan(ctx, [el(1, "textbox", "Favourite colour"), el(3, "textbox", "Favourite food")])
    assert again[1]["suggestion"]["text"] == "Green"  # what the page is later given
    assert again[3]["state"] == "NEEDS_YOU"


# --------------------------------------------------------------------------------------------- job + receipt are one commit (wave 2c)
PAGE = dict(url="https://boards.greenhouse.io/acmerobotics/jobs/4001", application_url="https://boards.greenhouse.io/acmerobotics/jobs/4001",
            title="Software Engineer", company="Acme Robotics", location="London", ats="greenhouse")


def rows(db, name):
    return db.query(f"SELECT * FROM {name}")


def strong_match(db, job_id):
    """What a good score does to the job: the daily 'new strong matches' notice would announce it."""
    db.execute("UPDATE jobs SET match_score=99, match_details=? WHERE id=?", (json.dumps({"eligible": 1}), job_id))


def test_the_receipt_exists_before_anything_else_can_see_the_job(tmp_path, monkeypatch):
    """ingest_job commits the job and then rescores it; a receipt written after that left a window in which the notice
    scheduler found a strong match without a receipt and announced a job the user is already applying to."""
    from jobagent import discovery
    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    seen = []
    original = discovery.rescore_job

    def rescoring(database, job, context=None):
        seen.append(database.one("SELECT key FROM notification_receipts WHERE key=?", ("match:" + job["id"],)))
        strong_match(database, job["id"])
        seen.append(collect_notifications(database))  # the scheduler running right now, in another thread
        return original(database, job, context)

    monkeypatch.setattr(discovery, "rescore_job", rescoring)
    application = ensure_extension_application(db, **PAGE)
    assert seen[0] == {"key": "match:" + application["job_id"]}, seen
    assert seen[1] == [], "the notice scheduler announced a job the user is already applying to"
    assert [r["key"] for r in rows(db, "notification_receipts")] == ["match:" + application["job_id"]]


def test_a_failing_receipt_leaves_no_job_and_no_application_behind(tmp_path):
    from sqlite3 import IntegrityError

    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    db.execute("CREATE TRIGGER no_receipts BEFORE INSERT ON notification_receipts "
               "BEGIN SELECT RAISE(ABORT, 'receipts are unavailable'); END")
    with pytest.raises(IntegrityError):
        ensure_extension_application(db, **PAGE)
    assert [len(rows(db, name)) for name in ("jobs", "applications", "application_events", "job_sources", "job_snapshots",
                                              "notification_receipts")] == [0] * 6
    db.execute("DROP TRIGGER no_receipts")
    application = ensure_extension_application(db, **PAGE)  # the same call works once the cause is gone
    assert [len(rows(db, name)) for name in ("jobs", "applications", "notification_receipts")] == [1, 1, 1]
    assert application["job_id"] == rows(db, "jobs")[0]["id"] and application["status"] == "PREPARING"


def test_a_failing_application_insert_also_leaves_no_orphan_job(tmp_path):
    from sqlite3 import IntegrityError

    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    db.execute("CREATE TRIGGER no_applications BEFORE INSERT ON applications BEGIN SELECT RAISE(ABORT, 'no'); END")
    with pytest.raises(IntegrityError):
        ensure_extension_application(db, **PAGE)
    assert [len(rows(db, name)) for name in ("jobs", "applications", "notification_receipts")] == [0, 0, 0]


def test_calling_it_again_is_harmless_and_never_touches_a_submitted_application(tmp_path):
    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    first = ensure_extension_application(db, **PAGE)
    db.execute("UPDATE applications SET status='SUBMITTED' WHERE id=?", (first["id"],))
    again = ensure_extension_application(db, **{**PAGE, "description": "A fuller text of the posting. " * 20})
    assert again["id"] == first["id"] and again["status"] == "SUBMITTED"
    assert [len(rows(db, name)) for name in ("jobs", "applications", "application_events", "notification_receipts")] == [
        1, 1, 1, 1]


def test_a_page_that_copies_a_real_posting_text_is_never_merged_into_the_real_job(tmp_path):
    """SEC-09. The company, title, location and text of a page are what the page SAYS: a cloned scam posting that copied them
    used to be taken for the real job (its answers landed in the real application, its address became an alias of it)."""
    from jobagent.discovery import ingest_job
    from jobagent.extension.session import ASSISTED_NOTE, ensure_extension_application
    db = make_app(tmp_path).state.db
    text = "Build and run reliable payment services used by millions of customers every day. " * 6
    real, _ = ingest_job(db, {"url": "https://careers.acmerobotics.example/jobs/777", "title": "Software Engineer",
                              "company": "Acme Robotics", "location": "London", "description": text, "source": "careers-feed"})
    original = db.one("SELECT description,metadata,skills FROM jobs WHERE id=?", (real["id"],))
    real_application = ensure_extension_application(db, url="https://careers.acmerobotics.example/jobs/777",
                                                    application_url="https://careers.acmerobotics.example/jobs/777",
                                                    title="Software Engineer", company="Acme Robotics", location="London")
    assert real_application["job_id"] == real["id"]  # the SAME address is the same job
    clone = ensure_extension_application(db, **{**PAGE, "description": text})  # PAGE is another address, identical text
    assert clone["job_id"] != real["id"] and clone["id"] != real_application["id"]
    jobs = {j["id"]: j for j in rows(db, "jobs")}
    assert set(jobs) == {real["id"], clone["job_id"]} and jobs[clone["job_id"]]["source"] == "extension"
    assert jobs[real["id"]]["source"] == "careers-feed" and jobs[real["id"]]["url"].endswith("/jobs/777")  # not renamed
    assert {k: jobs[real["id"]][k] for k in ("description", "metadata", "skills")} == original  # the real job is untouched
    assert rows(db, "job_duplicates") == []  # no alias of the real job was written for the page's address
    assert jobs[clone["job_id"]]["description"].endswith(ASSISTED_NOTE) and text.strip()[:60] in jobs[clone["job_id"]]["description"]
    assert sorted(r["key"] for r in rows(db, "notification_receipts")) == sorted(
        ["match:" + real["id"], "match:" + clone["job_id"]])


def test_a_known_job_is_completed_by_its_address_without_taking_the_pages_text(tmp_path):
    from jobagent.discovery import ingest_job
    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    real, _ = ingest_job(db, {"url": PAGE["url"], "title": "Software Engineer", "company": "Acme Robotics", "location": "London",
                              "description": "The employer's own text. " * 20, "source": "greenhouse"})
    before = rows(db, "jobs")[0]
    application = ensure_extension_application(db, **{**PAGE, "description": "A different text from the page. " * 30})
    after = rows(db, "jobs")[0]
    assert application["job_id"] == real["id"] and len(rows(db, "jobs")) == 1
    assert after["description"] == before["description"] and after["metadata"] == before["metadata"]
    assert after["skills"] == before["skills"] and rows(db, "job_duplicates") == []
    assert [r["key"] for r in rows(db, "notification_receipts")] == ["match:" + real["id"]]
    assert [len(rows(db, name)) for name in ("applications", "application_events")] == [1, 1]


async def test_the_assistants_row_does_not_block_prepare_or_auto_apply(tmp_path):
    """INT-01. A row in mode 'manual' made 'Prepare application' answer with a mode the user never chose, and Auto Apply refuse
    to start ('Manual mode prepares documents only'). Prepare also COMPLETES the row (it is no longer answered as 'already
    prepared', with no review task and a job still DISCOVERED): the whole of that is test_extension_prepare_after_assist.py."""
    from jobagent.core import prepare_application
    from jobagent.extension.session import ensure_extension_application
    app = make_app(tmp_path)
    app.state.db.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",  # a verified fact a draft CV can be written from
                         (uid(), app.state.db.one("SELECT id FROM candidates")["id"], "skill", "python", "Python", "test", "verified",
                          0, "", now(), now()))
    application = ensure_extension_application(app.state.db, **PAGE)
    prepared = prepare_application(app.state.db, application["job_id"], "review", data_dir=app.state.data_dir)
    assert prepared["duplicate"] is False and prepared["mode"] == "review" and prepared["id"] == application["id"]
    assert [t["application_id"] for t in rows(app.state.db, "human_tasks")] == [application["id"]]  # the review task it never had
    row = app.state.db.one("SELECT * FROM applications WHERE id=?", (application["id"],))
    assert row["mode"] == "review"  # what the 'Apply' button of the application needs: Meridian only fills a form when asked


def test_the_application_rows_are_exactly_what_the_lazy_creation_documents(tmp_path):
    from jobagent.extension.session import ensure_extension_application
    db = make_app(tmp_path).state.db
    application = ensure_extension_application(db, **PAGE)
    assert (application["mode"], application["status"], application["notes"]) == (
        "review", "PREPARING", "Assisted by the browser extension; submission is always manual")
    job = rows(db, "jobs")[0]
    assert (job["source"], job["status"]) == ("extension", "DISCOVERED")
    events = db.query("SELECT status,message,origin FROM application_events WHERE application_id=?", (application["id"],))
    assert events == [{"status": "PREPARING", "message": "Assisted by the browser extension", "origin": "extension"}]
    assert rows(db, "human_tasks") == [] and collect_notifications(db) == []
