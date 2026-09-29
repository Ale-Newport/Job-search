import json

import pytest
from fastapi.testclient import TestClient

from jobagent.main import create_app


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def set(self, name, value):
        self.values[name] = value

    def get(self, name):
        return self.values.get(name)

    def delete(self, name):
        self.values.pop(name, None)


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, token="test-only-token", secret_store=MemorySecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer test-only-token"}) as client:
        yield client


def test_authentication_origin_and_headers(api):
    assert api.get("/api/health").status_code == 200
    assert api.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert api.post("/api/oauth/callback").status_code == 405
    assert api.get("/api/oauth/callback?state=wrong&code=wrong").status_code == 400


def test_empty_workspace_is_real_and_persistent(api):
    assert api.get("/api/jobs").json()["total"] == 0
    assert api.get("/api/applications").json()["total"] == 0
    assert api.get("/api/settings").json()["automation_paused"] is True
    response = api.post(
        "/api/facts",
        json={
            "category": "contact",
            "key": "email",
            "value": "candidate@example.test",
            "verification_status": "verified",
        },
    )
    assert response.status_code == 201, response.text
    fact = response.json()
    assert api.get("/api/facts").json()["items"][0]["value"] == "candidate@example.test"
    assert api.patch(f"/api/facts/{fact['id']}", json={"locked": True}).status_code == 200
    assert api.patch(f"/api/facts/{fact['id']}", json={"value": "incorrect@example.test"}).status_code == 409


def test_document_import_review_generation_and_download(api):
    imported = api.post(
        "/api/documents/import",
        files={
            "file": (
                "resume.txt",
                b"Candidate Example\ncandidate@example.test\nPython developer\nBSc Computer Science",
                "text/plain",
            )
        },
    )
    assert imported.status_code == 201, imported.text
    for fact in api.get("/api/facts").json()["items"]:
        assert fact["verification_status"] != "verified"
    verified = api.post(
        "/api/facts",
        json={
            "category": "experience",
            "key": "project",
            "value": "Built a Python data pipeline",
            "verification_status": "verified",
        },
    ).json()
    job = api.post(
        "/api/jobs",
        json={
            "title": "Graduate Engineer",
            "company": "Arc Systems",
            "url": "https://example.test/jobs/1",
            "description": "Python developer",
        },
    ).json()
    assert "id" in job, job
    draft = api.post(
        "/api/documents/generate",
        json={"job_id": job["id"], "kind": "cv", "fact_ids": [verified["id"]], "approved": False},
    )
    assert draft.status_code == 200, draft.text
    generated = api.post(
        "/api/documents/generate",
        json={"job_id": job["id"], "kind": "cv", "fact_ids": [verified["id"]], "approved": True},
    )
    assert generated.status_code == 200, generated.text
    docs = api.get("/api/documents").json()["items"]
    version_id = docs[0]["latest_version_id"]
    download = api.get(f"/api/document-versions/{version_id}/download")
    assert download.status_code == 200 and download.content.startswith(b"%PDF")


def test_duplicate_prepare_manual_gate_and_tracking(api):
    payload = {
        "title": "Software Engineer",
        "company": "Arc Systems",
        "url": "https://example.test/jobs/software?utm_source=test",
        "description": "Graduate Python",
    }
    first = api.post("/api/jobs", json=payload).json()
    second = api.post("/api/jobs", json=payload).json()
    assert first["id"] == second["id"]
    application = api.post(f"/api/jobs/{first['id']}/prepare", json={"mode": "manual"}).json()
    duplicate = api.post(f"/api/jobs/{first['id']}/prepare", json={"mode": "manual"}).json()
    assert application["id"] == duplicate["id"]
    response = api.post(f"/api/applications/{application['id']}/apply")
    assert response.status_code == 400 and "Manual" in response.text
    response = api.post(f"/api/applications/{application['id']}/approve", json={"approved": True})
    assert response.status_code == 400
    api.patch(f"/api/applications/{application['id']}", json={"status": "INTERVIEW"})
    assert api.get(f"/api/applications/{application['id']}").json()["events"][-1]["status"] == "INTERVIEW"


def test_secrets_never_persisted_in_database(api):
    secret = "test-not-a-real-key"
    response = api.put(
        "/api/integrations/openai",
        json={"config": {"input_cost_per_million": 1, "output_cost_per_million": 2}, "secret": secret},
    )
    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in json.dumps(api.get("/api/integrations").json())
    db = api.app.state.db
    assert secret not in db.path.read_bytes().decode("latin1")
    assert api.put("/api/integrations/openai", json={"config": {"api_key": secret}}).status_code == 422
    assert api.patch("/api/settings", json={"api_key": secret}).status_code == 422


def test_core_route_smoke(api):
    for path in (
        "dashboard",
        "analytics",
        "activity",
        "human-tasks",
        "automation",
        "browser/status",
        "companies",
        "sources",
        "search-profiles",
        "documents",
        "emails",
        "integrations",
    ):
        response = api.get("/api/" + path)
        assert response.status_code == 200, (path, response.text)


def test_text_drafts_only_trusted_facts(api):
    fact = api.post("/api/facts", json={"category": "skill", "key": "Python", "value": "Python"}).json()
    job = api.post(
        "/api/jobs", json={"title": "Python Engineer", "company": "Arc Systems", "url": "https://example.test/job/3"}
    ).json()
    assert (
        api.post(
            "/api/ai/draft",
            json={"job_id": job["id"], "question": "What experience do you have?", "fact_ids": [fact["id"]]},
        ).status_code
        == 400
    )
    api.patch("/api/facts/" + fact["id"], json={"verification_status": "verified"})
    answer = api.post(
        "/api/ai/draft",
        json={"job_id": job["id"], "question": "What experience do you have?", "fact_ids": [fact["id"]]},
    ).json()
    assert answer["requires_review"] is True
    assert answer["facts_used"] == [fact["id"]]


def test_oauth_callback_shows_recoverable_error_without_leaking_provider_payload(api, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    import httpx

    api.put("/api/integrations/gmail", json={"config": {"client_id": "desktop-client"}})
    authorization = api.post("/api/integrations/gmail/connect").json()["url"]
    state = parse_qs(urlparse(authorization).query)["state"][0]
    original = httpx.AsyncClient

    def respond(request):
        return httpx.Response(
            400,
            json={
                "error": "invalid_request",
                "error_description": "client_secret is missing. private-provider-details",
                "access_token": "private-token",
            },
        )

    monkeypatch.setattr(
        "jobagent.mail.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    response = api.get(
        "/api/oauth/callback", params={"state": state, "code": "private-code"}, headers={"Authorization": ""}
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "requires the OAuth client secret" in response.text
    assert "private-" not in response.text
    row = api.get("/api/integrations").json()["items"][0]
    assert "requires the OAuth client secret" in row["last_error"]
    assert row["status"] == "configured"
    repeated = api.get("/api/oauth/callback", params={"state": state, "code": "private-code"})
    assert "invalid or expired" in repeated.text


def test_oauth_denial_requires_valid_state_and_is_single_use(api):
    from urllib.parse import parse_qs, urlparse

    api.put("/api/integrations/gmail", json={"config": {"client_id": "desktop-client"}})
    authorization = api.post("/api/integrations/gmail/connect").json()["url"]
    state = parse_qs(urlparse(authorization).query)["state"][0]
    forged = api.get("/api/oauth/callback", params={"state": "wrong", "error": "access_denied"})
    assert forged.status_code == 400
    assert api.get("/api/integrations").json()["items"][0]["last_error"] is None
    denied = api.get("/api/oauth/callback", params={"state": state, "error": "<script>private-error</script>"})
    assert denied.status_code == 400
    assert "Email access was not authorized" in denied.text
    assert "private-error" not in denied.text
    assert "<script>" not in denied.text
    assert api.get("/api/integrations").json()["items"][0]["last_error"]
    assert state not in api.app.state.mail.pending
