from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from jobagent.db import Database
from jobagent.core import router, prepare_application
from jobagent.discovery import ingest_job
from jobagent.application_profile import profile, write_profile, application_knowledge, remember_answer, draft_fact_ids
from jobagent.automation.answers import resolve_answer


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "profile.sqlite")


def application(db, company="Trainline", location="London"):
    job = ingest_job(
        db,
        {
            "title": "Engineer",
            "company": company,
            "location": location,
            "url": f"https://jobs.example.test/{company}/{location}",
        },
    )[0]
    app = prepare_application(db, job["id"])
    return {**app, "company": company, "location": location}


def knowledge(db, app):
    return application_knowledge(
        db, app, db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    )


def test_profile_api_unknowns_suggestions_confirmation_and_clearing(db, tmp_path):
    app = FastAPI()
    app.state.db = db
    app.state.data_dir = tmp_path
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        fields = client.get("/api/application-profile").json()["fields"]
        assert len(fields) >= 50
        assert all(not f["value"] and f["state"] == "unknown" for f in fields if f["sensitive"])
        assert (
            client.put(
                "/api/application-profile/notice_period", json={"value": "1 month", "confirmed": False}
            ).status_code
            == 200
        )
        candidate = application(db)
        facts, _ = knowledge(db, candidate)
        assert resolve_answer("What is your current notice period?", facts)["answer"] is None
        client.put("/api/application-profile/notice_period", json={"value": "2 weeks", "confirmed": True})
        facts, _ = knowledge(db, candidate)
        assert resolve_answer("What is your current notice period?", facts)["answer"] == "2 weeks"
        client.put("/api/application-profile/notice_period", json={"value": "", "confirmed": False})
        assert resolve_answer("What is your current notice period?", knowledge(db, candidate)[0])["answer"] is None
        assert (
            client.put(
                "/api/application-profile/expected_salary_uk", json={"value": "NaN", "confirmed": True}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/application-profile/date_of_birth", json={"value": "2026-99-99", "confirmed": True}
            ).status_code
            == 422
        )


def test_sensitive_and_contextual_profile_values_need_confirmation_and_scope(db):
    uk = application(db)
    france = application(db, "Other", "Paris, France")
    write_profile(db, "gender", "Prefer not to say", False)
    assert resolve_answer("Describe your gender", knowledge(db, uk)[0])["answer"] is None
    write_profile(db, "gender", "Prefer not to say", True)
    write_profile(db, "sponsorship_uk", "No", True)
    write_profile(db, "expected_salary_uk", "45000", True)
    write_profile(db, "hybrid_trainline", "Yes", True)
    q = "Do you require sponsorship now or in the future to work in the job's location?"
    assert resolve_answer(q, knowledge(db, uk)[0])["answer"] == "No"
    assert resolve_answer(q, knowledge(db, france)[0])["answer"] is None
    assert resolve_answer("What are your salary expectations?", knowledge(db, france)[0])["answer"] is None
    assert (
        resolve_answer("Are you able to commit to our hybrid working policy?", knowledge(db, france)[0])["answer"]
        is None
    )
    assert resolve_answer("Describe your gender", knowledge(db, uk)[0])["answer"] == "Prefer not to say"
    assert not draft_fact_ids(knowledge(db, uk)[0], "Tell us about yourself")


def test_browser_memory_defaults_to_one_application_and_can_be_edited(db):
    first = application(db)
    second = application(db, "Other")
    q = "Why would you like to join our company?"
    remember_answer(db, first, q, "A considered answer")
    assert resolve_answer(q, knowledge(db, first)[0])["answer"] == "A considered answer"
    assert resolve_answer(q, knowledge(db, second)[0])["answer"] is None
    remember_answer(db, first, q, "A revised answer", reusable=True)
    assert resolve_answer(q, knowledge(db, second)[0])["answer"] == "A revised answer"
    remember_answer(db, first, "Describe your gender", "", skip=True)
    assert "describe your gender" in knowledge(db, first)[1]
    assert not knowledge(db, second)[1]
    assert len(profile(db)["answers"]) == 2
    assert db.one("SELECT count(*) AS n FROM fact_history")["n"] >= 1


async def test_text_draft_rejects_invented_current_employment(db, monkeypatch):
    import httpx
    import json
    from jobagent.text_ai import TextService
    from jobagent.application_answer_llm import SCORE_NAMES
    write_profile(db, 'general_context', 'I built Python tools at Example from January 2023 to June 2024.', True)
    identifier = db.one("SELECT id FROM facts WHERE key='general_context'")['id']
    reply = {'answer': 'Currently, as an Engineer at Example, I work on AI.'}
    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': True,
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': [], 'issues': [], 'reason': 'Grounded'}
        else:
            generated = {'question_id': payload['question_id'], 'intent': payload['intent'], 'answer': reply['answer'],
                         'facts_used': [identifier], 'selected_options': [], 'selected_roles': [], 'company_claims': [], 'claims': [{'text': reply['answer'], 'fact_ids': [identifier]}],
                         'evidence_quality': 'HIGH', 'word_count': len(reply['answer'].split()), 'follows_instructions': True,
                         'answers_question': True, 'unsupported_claims': [], 'requires_human_input': False, 'missing_facts': []}
        return httpx.Response(200, json={'message': {'content': json.dumps(generated)}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    service = TextService(db, None)
    settings = {'text_provider': 'ollama', 'text_model': 'fixture', 'text_base_url': 'http://127.0.0.1:11434/v1'}
    with pytest.raises(ValueError, match='current employment'):
        await service.infer_field(settings, 'Describe how you have used Python for software projects.', {}, [identifier], {'multiline': True})
    reply['answer'] = 'I built Python tools at Example from January 2023 to June 2024.'
    draft = await service.infer_field(settings, 'Describe how you have used Python for software projects.', {}, [identifier], {'multiline': True})
    assert draft['requires_review'] is True


def test_cleared_override_blocks_old_cv_and_summaries_follow_verified_sources(db):
    from jobagent.db import uid, now

    candidate = db.one("SELECT id FROM candidates LIMIT 1")["id"]
    for key, value, category in [
        ("email", "old@example.test", "personal"),
        ("project", "Built a Python tool.", "project"),
    ]:
        db.execute(
            "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), candidate, category, key, value, "document:fixture", "verified", 0, "", now(), now()),
        )
    app = application(db)
    write_profile(db, "email", "", False)
    assert resolve_answer("Email", knowledge(db, app)[0])["answer"] is None
    assert next(f for f in profile(db)["fields"] if f["key"] == "project_summary")["value"] == "Built a Python tool."
    db.execute("UPDATE facts SET verification_status='rejected' WHERE key='project'")
    assert not next(f for f in profile(db)["fields"] if f["key"] == "project_summary")["value"]


def test_direct_browser_corrections_override_old_facts_and_remember_clears(db):
    first = application(db)
    second = application(db, 'Another employer')
    write_profile(db, 'phone', '0123456789', True)
    write_profile(db, 'location', 'London, UK', True)
    remember_answer(db, first, 'Current location', 'Manchester (UK)', reusable=True, direct_edit=True)
    facts, _ = knowledge(db, second)
    correction = resolve_answer('Where are you currently located?', facts)
    assert correction['answer'] == 'Manchester (UK)'
    assert correction['source'] == 'user_edit'
    remember_answer(db, first, 'Phone', '', reusable=True, skip=True, direct_edit=True)
    facts, _ = knowledge(db, second)
    cleared = resolve_answer('Phone number', facts)
    assert cleared['answer'] is None and cleared['leave_blank'] and cleared['verified']
    assert not any(f['key'] == 'phone' and f['value'] == '0123456789' for f in facts)


def test_direct_edit_disclosures_still_respect_application_scope(db):
    first = application(db)
    second = application(db, 'Another employer')
    question = 'I certify all answers are accurate'
    remember_answer(db, first, question, 'Yes', reusable=True, direct_edit=True)
    assert resolve_answer(question, knowledge(db, first)[0])['answer'] == 'Yes'
    assert resolve_answer(question, knowledge(db, second)[0])['answer'] is None


def test_browser_edit_updates_parked_checkpoint_and_expires_approval(db, tmp_path):
    import json
    from jobagent.orchestrator import Orchestrator
    from jobagent.db import now
    app = application(db)
    coordinator = Orchestrator(db, tmp_path, recover=False)
    coordinator.browser._active_application_id = app['id']
    old = {'snapshot_id': 'old-review', 'review_fingerprint': 'old-facts',
           'answers': [{'question': 'Phone', 'answer': 'old phone', 'verified': True}]}
    db.execute("INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) VALUES(?,?,'needs_review','deterministic',?,?,?)",
               ('parked', app['id'], json.dumps(old), now(), now()))
    fact_id = coordinator.on_user_edit({'frame_url': 'https://jobs.example.test/Trainline/London', 'answer': 'new phone',
                                       'element': {'type': 'text', 'label': 'Phone'}})
    checkpoint = json.loads(db.one("SELECT checkpoint FROM automation_runs WHERE id='parked'")['checkpoint'])
    assert checkpoint['snapshot_id'] is None and checkpoint['review_fingerprint'] is None
    assert checkpoint['answers'] == [{'question': 'Phone', 'answer': 'new phone',
                                      'fact_ids': [fact_id], 'verified': True, 'source': 'user_edit'}]
    assert db.one('SELECT answer FROM application_answers WHERE application_id=?', (app['id'],))['answer'] == 'new phone'


def test_browser_salary_format_is_saved_even_when_profile_editor_format_differs(db):
    first, second = application(db), application(db, 'Other employer')
    remember_answer(db, first, 'What are your salary expectations?', '45,000',
                    reusable=True, direct_edit=True)
    assert resolve_answer('What are your salary expectations?', knowledge(db, second)[0])['answer'] == '45,000'
