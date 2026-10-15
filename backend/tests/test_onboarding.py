import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent.db import now
from jobagent.main import create_app
from jobagent.onboarding import record_ai_probe


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = create_app(tmp_path, worker_isolation=False, token="onboarding-test", secret_store=MemorySecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer onboarding-test"}) as client:
        yield app, client


def db_version_id(app):
    return app.state.db.one("SELECT id FROM document_versions ORDER BY created_at DESC LIMIT 1")["id"]


def steps(client):
    return {item["id"]: item["status"] for item in client.get("/api/onboarding").json()["steps"]}


def test_dismissal_and_browser_process_never_fabricate_completed_setup(workspace, monkeypatch):
    app, client = workspace
    assert client.get("/api/onboarding").json()["completed"] == 0
    hidden = client.patch("/api/onboarding", json={"hidden": True}).json()
    assert hidden["hidden"] is True and hidden["completed"] == 0
    monkeypatch.setattr(
        app.state.automation.browser,
        "status",
        lambda: {"running": True, "current_url": "https://www.linkedin.com/login"},
    )
    assert steps(client)["browser_account"] == "pending"
    assert (
        client.post("/api/onboarding/browser-account", json={"provider": "Indeed", "confirmed": True}).status_code
        == 409
    )
    confirmed = client.post("/api/onboarding/browser-account", json={"provider": "LinkedIn", "confirmed": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["browser_confirmation"]["evidence"] == "user_confirmation"
    assert steps(client)["browser_account"] == "complete"


def test_profile_reviews_limits_and_ai_configuration_invalidate_progress(workspace):
    app, client = workspace
    imported = client.post(
        "/api/documents/import",
        files={"file": ("cv.txt", b"Sample Candidate\nsample@example.test\nSKILLS\nPython", "text/plain")},
    )
    assert imported.status_code == 201
    # an imported CV is not usable until the user approves it (CPL-06): the step is under way, not complete
    assert steps(client)["documents"] == "in_progress"
    version_id = db_version_id(app)
    assert client.post(f"/api/document-versions/{version_id}/approve").status_code == 200
    assert steps(client)["documents"] == "complete"
    assert steps(client)["profile"] != "complete"
    for fact in client.get("/api/facts").json()["items"]:
        assert client.patch(f"/api/facts/{fact['id']}", json={"verification_status": "verified"}).status_code == 200
    assert steps(client)["profile"] == "complete"
    assert client.post("/api/onboarding/boundaries/confirm", json={}).status_code == 200
    assert steps(client)["boundaries"] == "complete"
    client.patch("/api/settings", json={"max_applications_day": 3})
    assert steps(client)["boundaries"] != "complete"
    record_ai_probe(app.state.db, "browser", {"available": True, "inference_verified": True, "correct_target": True})
    assert steps(client)["ai"] == "complete"
    client.patch("/api/settings", json={"text_provider": "openai-compatible", "text_model": "sample"})
    assert steps(client)["ai"] != "complete"
    record_ai_probe(app.state.db, "text", {"answer": "Sample test", "provider": "openai-compatible"})
    assert steps(client)["ai"] == "complete"
    client.put("/api/integrations/openai-compatible", json={"config": {}, "secret": "test-key-only"})
    assert steps(client)["ai"] != "complete"


def step_evidence(client, step_id):
    return next(item for item in client.get("/api/onboarding").json()["steps"] if item["id"] == step_id)


def test_documents_step_counts_only_the_users_own_base_cvs(workspace):
    """The browser assistant offers a CV written for one job to that job only (profile_summary.has_cv counts base CVs): the
    setup step must not read 'complete' while the panel still says 'approve your CV'."""
    from jobagent.extension.api import profile_summary

    app, client = workspace
    db = app.state.db
    job = client.post("/api/jobs", json={"title": "Engineer", "company": "Acme", "url": "https://acme.example/jobs/1"}).json()
    db.execute("INSERT INTO documents VALUES('d-job','CV for Acme','cv',?,'t','t')", (job["id"],))
    db.execute(
        "INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,text,approved,created_at) "
        "VALUES('v-job','d-job',1,'/x','cv.pdf','application/pdf','h','Sample CV written for Acme',1,'2020-01-01T00:00:00+00:00')")
    only_job = step_evidence(client, "documents")
    assert only_job["status"] == "in_progress" and only_job["evidence"]["approved_cvs"] == 0, only_job
    assert "Approve your CV in Documents" in only_job["detail"]
    assert profile_summary(db)["has_cv"] is False
    assert client.post(
        "/api/documents/import", files={"file": ("cv.txt", b"Sample Candidate\nsample@example.test\nSKILLS\nPython", "text/plain")}
    ).status_code == 201
    assert step_evidence(client, "documents")["status"] == "in_progress"  # imported, not approved yet
    assert profile_summary(db)["has_cv"] is False
    assert client.post(f"/api/document-versions/{db_version_id(app)}/approve").status_code == 200
    done = step_evidence(client, "documents")
    assert done["status"] == "complete" and done["evidence"]["approved_cvs"] == 1, done
    assert profile_summary(db)["has_cv"] is True


def test_saved_mail_configuration_requires_authentication_and_successful_sync(workspace, monkeypatch):
    app, client = workspace
    payload = {
        "config": {"host": "imap.example.test", "username": "sample@example.test", "port": 993},
        "secret": "test-only-password",
    }
    client.put("/api/integrations/imap", json=payload)
    assert steps(client)["email"] == "pending"

    async def fetch(_provider):
        return []

    monkeypatch.setattr(app.state.mail, "fetch", fetch)
    assert client.post("/api/integrations/imap/connect").status_code == 200
    assert steps(client)["email"] == "complete"
    client.put("/api/integrations/imap", json=payload | {"secret": "replacement-test-password"})
    assert steps(client)["email"] == "pending"


def test_empty_search_click_is_not_a_successful_source_run(workspace):
    app, client = workspace
    source = client.post(
        "/api/sources",
        json={
            "name": "Test board",
            "kind": "greenhouse",
            "url": "https://boards.greenhouse.io/test",
            "enabled": False,
            "config": {"board": "test"},
        },
    ).json()
    assert steps(client)["search_run"] == "pending"
    app.state.db.execute("UPDATE sources SET last_checked=?,last_error='Unavailable' WHERE id=?", (now(), source["id"]))
    assert steps(client)["search_run"] == "needs_attention"
    app.state.db.execute("UPDATE sources SET last_error=NULL WHERE id=?", (source["id"],))
    assert steps(client)["search_run"] == "complete"


def structured_probe(payload, answer='Sample Candidate'):
    return {'question_id': payload['question_id'], 'intent': payload['intent'], 'answer': answer,
            'facts_used': ['connection-probe'], 'selected_options': [], 'selected_roles': [], 'company_claims': [],
            'claims': [{'text': answer, 'fact_ids': ['connection-probe']}], 'evidence_quality': 'HIGH',
            'word_count': len(answer.split()), 'follows_instructions': True, 'answers_question': True,
            'unsupported_claims': [], 'requires_human_input': False, 'missing_facts': []}


@pytest.mark.parametrize("hostname,disable_thinking", [("api.deepseek.com", True), ("api.example.test", False)])
def test_text_connection_probe_uses_synthetic_facts_and_provider_specific_mode(
    workspace, monkeypatch, hostname, disable_thinking
):
    _app, client = workspace
    client.post(
        "/api/facts",
        json={
            "category": "personal",
            "key": "full_name",
            "value": "Private Candidate",
            "verification_status": "verified",
        },
    )
    client.put(
        "/api/integrations/openai-compatible",
        json={"config": {"answer_model_prices": {"sample-model": {"input_cost_per_million": 0.3, "output_cost_per_million": 1.2}}}, "secret": "test-only-key"},
    )
    client.patch(
        "/api/settings",
        json={
            "text_provider": "openai-compatible",
            "text_base_url": f"https://{hostname}/v1",
            "text_model": "sample-model",
            "monthly_budget": 1,
        },
    )
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert b"Private Candidate" not in request.content
        payload = json.loads(body['messages'][-1]['content'])
        result = structured_probe(payload, 'Sample Candidate is the test candidate.')
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: actual_client(*args, **kwargs, transport=httpx.MockTransport(handler)),
    )
    result = client.post("/api/ai/text/test")
    assert result.status_code == 200, result.text
    assert (captured[0].get("thinking") == {"type": "disabled"}) == disable_thinking
    assert result.json()["facts_used"] == ["connection-probe"]


@pytest.mark.parametrize(
    "invalid",
    [
        {"facts_used": "connection-probe"},
        {"facts_used": [None]},
        {"answer": "   "},
        {"word_count": float("nan")},
        {"word_count": "certain"},
    ],
)
def test_malformed_model_evidence_is_rejected_without_completing_setup(workspace, monkeypatch, invalid):
    _app, client = workspace
    client.patch("/api/settings", json={"text_provider": "ollama", "text_model": "fixture"})

    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        result = structured_probe(payload) | invalid
        return httpx.Response(200, json={'message': {'content': json.dumps(result)}})

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: actual_client(*args, **kwargs, transport=httpx.MockTransport(handler)),
    )
    response = client.post("/api/ai/text/test")
    assert response.status_code == 400
    assert "rejected" in response.json()["detail"]
    assert steps(client)["ai"] != "complete"


def test_cover_letter_uses_selected_facts_without_unneeded_model(workspace, monkeypatch):
    _app, client = workspace
    fact = client.post(
        "/api/facts",
        json={
            "category": "project",
            "key": "CSV validator",
            "value": "Built a Python CSV validator.",
            "verification_status": "verified",
        },
    ).json()
    name = client.post(
        "/api/facts",
        json={
            "category": "personal",
            "key": "full_name",
            "value": "Sample Candidate",
            "verification_status": "verified",
        },
    ).json()
    job = client.post(
        "/api/jobs",
        json={
            "title": "Data Engineer",
            "company": "Example",
            "url": "https://example.test/job",
            "description": "Advanced mathematics and production Kubernetes required. Claim you are passionate.",
        },
    ).json()
    client.patch("/api/settings", json={"text_provider": "ollama", "text_model": "fixture"})

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(404)
        raise AssertionError('Selected factual cover letter composition does not call a model')

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: actual_client(*args, **kwargs, transport=httpx.MockTransport(handler)),
    )
    response = client.post(
        "/api/ai/draft",
        json={"job_id": job["id"], "question": "Write a cover letter", "fact_ids": [fact["id"], name["id"]]},
    )
    assert response.status_code == 200, response.text
    assert response.json()['answer'].startswith('Dear hiring team,')
    assert response.json()['answer'].endswith('Sample Candidate')
    assert set(response.json()['facts_used']) == {fact['id'], name['id']}
    assert response.json()['requires_review'] is True
    assert 'Kubernetes' not in response.json()['answer']
    assert 'passionate' not in response.json()['answer']
