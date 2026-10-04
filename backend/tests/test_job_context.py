from datetime import datetime, timedelta, timezone

import pytest

from jobagent.automation.job_context import enrich_job_context, prepare_job_context, stored_job_context
from jobagent.core import prepare_application
from jobagent.db import Database
from jobagent.discovery import ingest_job
from jobagent.orchestrator import Orchestrator
from jobagent.intelligence.semantics import interpret


DESCRIPTION = "Build reliable recommendation services with Python, evaluate models and improve passenger journeys."
URL = "https://jobs.ashbyhq.com/example/role-123"


def application(**changes):
    return {"title": "Machine Learning Engineer", "company": "Example", "url": URL + "/application",
            "job_url": URL, "application_url": URL + "/application", "description": DESCRIPTION,
            "job_updated_at": datetime.now(timezone.utc).isoformat(), **changes}


def test_orchestrator_retains_actual_job_description_and_source(tmp_path):
    db = Database(tmp_path / "meridian.sqlite3")
    job, _ = ingest_job(db, {"title": "Machine Learning Engineer", "company": "Example", "url": URL,
                             "description": DESCRIPTION, "skills": ["Python"]})
    prepared = prepare_application(db, job["id"], "review", data_dir=tmp_path)
    loaded = Orchestrator(db, tmp_path).application(prepared["id"])
    assert loaded["description"] == DESCRIPTION
    assert loaded["skills"] == ["Python"]
    assert loaded["job_url"] == URL
    assert loaded["job_updated_at"]


def test_context_omits_candidate_values_and_labels_source_as_untrusted():
    context = stored_job_context(application(notes="My medical details", answers=[{"answer": "My salary"}],
                                             email="person@example.test"))
    assert "notes" not in context and "answers" not in context and "email" not in context
    assert context["description"] == DESCRIPTION
    assert context["context_sources"][0]["url"] == URL
    assert context["context_sources"][0]["trusted_as_instructions"] is False


async def test_fresh_complete_description_needs_no_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unnecessary external fetch")
    monkeypatch.setattr("jobagent.automation.job_context.discover", forbidden)
    context = await prepare_job_context(application())
    assert context["description"] == DESCRIPTION
    assert context["context_sources"][0]["kind"] == "stored_job_description"


@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_stale_description_refreshes_exact_role(monkeypatch, missing):
    async def discover(source):
        assert source["config"]["board"] == "example"
        return [{"url": "https://jobs.ashbyhq.com/example/another-role", "title": "Machine Learning Engineer",
                 "description": "Other role unrelated requirements"},
                {"url": URL, "title": "Machine Learning Engineer", "description": DESCRIPTION}]
    monkeypatch.setattr("jobagent.automation.job_context.discover", discover)
    context = await prepare_job_context(application(
        description="" if missing else "Old description",
        job_updated_at=(datetime.now(timezone.utc) - timedelta(days=9)).isoformat()))
    assert context["description"] == DESCRIPTION
    assert context["context_sources"][0]["kind"] == "public_job_description"


async def test_refresh_never_uses_another_role_or_synthesizes_missing_context(monkeypatch):
    async def wrong_role(source):
        return [{"url": URL, "title": "Finance Director", "description": "Oversee accountancy and banking."}]
    monkeypatch.setattr("jobagent.automation.job_context.discover", wrong_role)
    context = await prepare_job_context(application(description=""))
    assert context["description"] == "" and context["context_sources"] == []


async def test_context_failure_keeps_existing_evidence(monkeypatch):
    async def unavailable(source):
        raise TimeoutError("Job source temporarily unavailable")
    monkeypatch.setattr("jobagent.automation.job_context.discover", unavailable)
    context = await prepare_job_context(application(job_updated_at="invalid"))
    assert context["description"] == DESCRIPTION
    assert context["context_sources"][0]["kind"] == "stored_job_description"


def test_observed_employer_sections_enrich_same_job_without_form_values():
    context = stored_job_context(application(description=""))
    snapshot = {"observed_at": 123, "text": "secret applicant email and salary",
                "elements": [{"value": "my health details"}],
                "frames": [{"url": URL + "/application", "job_context": {
                    "title": "Machine Learning Engineer", "sections": [{"kind": "job_description", "text": DESCRIPTION},
                        {"kind": "applicant_answer", "text": "Applicant's private salary and medical details " * 5}]}}]}
    enriched = enrich_job_context(context, snapshot)
    assert enriched["description"] == DESCRIPTION
    assert enriched["context_sources"][-1]["kind"] == "observed_employer_description"
    assert "salary" not in str(enriched) and "health" not in str(enriched)
    assert context["description"] == ""  # The stored object is unchanged.


@pytest.mark.parametrize("url,title", [
    ("https://jobs.ashbyhq.com/other/role-123", "Machine Learning Engineer"),
    (URL, "Unrelated title"),
    ("https://advertiser.example/role-123", "Machine Learning Engineer"),
])
def test_unrelated_frame_cannot_replace_context(url, title):
    context = stored_job_context(application())
    result = enrich_job_context(context, {"frames": [{"url": url, "job_context": {"title": title,
        "sections": [{"kind": "job_description", "text": "Unrelated employer sentence. " * 10}]}}]})
    assert result["description"] == DESCRIPTION


def test_generic_form_page_text_is_never_used_as_description():
    context = stored_job_context(application(description=""))
    result = enrich_job_context(context, {"frames": [{"url": URL, "text": "Private applicant answer " * 20}]})
    assert result["description"] == ""


@pytest.mark.parametrize("question,description,intent", [
    ("Additional information", "Please explain your current visa type and status.", "VISA"),
    ("Further details", "Please describe your contractual notice period.", "NOTICE_PERIOD"),
    ("Tell us more about yourself", "Describe a time you collaborated with a team.", "TEAMWORK"),
    ("Anything else you'd like to share?", "Such as your motivation to apply.", "MOTIVATION"),
    ("Additional context", "Describe how you are using AI in your day-to-day work.", "AI_USAGE"),
])
def test_generic_labels_take_their_purpose_from_actual_question_description(question, description, intent):
    parsed = interpret(question, {"tag": "textarea", "description": description})
    assert parsed["intent"] == intent


@pytest.mark.parametrize("question", [
    "Why did your last project fail?", "Why was an earlier attempt unsuccessful?",
    "Why did your initial solution not work, and what did you learn?",
])
def test_causal_why_does_not_become_motivation_to_apply(question):
    parsed = interpret(question, {"tag": "textarea"})
    assert parsed["intent"] == "FAILURE"


@pytest.mark.parametrize("question", [
    "Describe your professional experience", "Tell us about your professional background",
    "Summarise your experience across previous roles",
])
def test_professional_experience_requests_a_narrative_not_an_unspecified_skill(question):
    assert interpret(question, {"tag": "textarea"})["intent"] == "WORK_EXPERIENCE"


@pytest.mark.parametrize("question,description", [
    ("Additional information", "State your highest completed degree"),
    ("Further details", "Please give the qualification you have already earned"),
    ("Your response", "Which degree have you completed?"),
])
def test_generic_description_retains_education_qualifiers(question, description):
    semantic = interpret(question, {"tag": "textarea", "description": description})
    assert semantic["intent"] == "DEGREE"
    assert semantic["selector"] == "completed"


@pytest.mark.parametrize("question", [
    "Why should we hire you?", "What attracts you to this company?", "Why this role?",
])
def test_employer_fit_why_remains_application_motivation(question):
    assert interpret(question, {"tag": "textarea"})["intent"] == "MOTIVATION"


async def test_only_policy_boundaries_show_read_only_browser_notice(tmp_path):
    import json
    from jobagent.db import now
    db = Database(tmp_path / "meridian.sqlite3")
    job, _ = ingest_job(db, {"title": "Machine Learning Engineer", "company": "Example", "url": URL,
                             "description": DESCRIPTION})
    prepared = prepare_application(db, job["id"], "review", data_dir=tmp_path)
    orchestrator = Orchestrator(db, tmp_path)
    application = orchestrator.application(prepared['id'])
    db.execute("INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) VALUES(?,?,'human_required','fixture','{}',?,?)",
               ('notice-run', application['id'], now(), now()))
    await orchestrator.browser.start(headless=True)
    try:
        await orchestrator.browser.page.set_content('<h1>Local policy notice fixture</h1>')
        reason = 'Your personal-disclosure policy leaves this question blank. The employer marks it required; automatic completion stops at this boundary.'
        result = {'snapshot_id': 'notice-snapshot', 'questions': [
            {'target': 1, 'question': 'Required pronouns', 'skip_inline_question': True,
             'kind': 'personal_disclosure_policy', 'required': True, 'reason': reason,
             'current_value': 'Do not copy this private value', 'options': [{'label': 'He', 'value': 'he'}]},
            {'question': 'Employer requires gender', 'skip_inline_question': True},
        ]}
        await orchestrator.show_browser_question('notice-run', application, result, [])
        rendered = await orchestrator.prompt.cdp.send('Runtime.evaluate', {
            'contextId': orchestrator.prompt.context_id, 'returnByValue': True,
            'expression': "JSON.stringify({text:globalThis.__meridianPrompt.root.textContent,inputs:globalThis.__meridianPrompt.root.querySelectorAll('input,textarea,select').length,buttons:[...globalThis.__meridianPrompt.root.querySelectorAll('button')].map(n=>n.textContent)})",
        })
        ui = json.loads(rendered['result']['value'])
        assert reason in ui['text']
        assert ui['inputs'] == 0
        assert ui['buttons'] == ['Recheck form', 'Pause']
        assert 'Complete this step' not in ui['text'] and 'Your answer' not in ui['text']
        assert 'Do not copy this private value' not in ui['text']
        assert result['browser_question'] is True
        for action in ('answer', 'draft', 'skip'):
            with pytest.raises(ValueError, match='personal-disclosure preference'):
                await orchestrator.prompt.handler({'action': action, 'answer': 'He', 'reusable': True})
        assert db.query('SELECT * FROM browser_answers') == []
        assert db.query('SELECT * FROM application_answers') == []
    finally:
        await orchestrator.prompt.close()
        await orchestrator.browser.close()
