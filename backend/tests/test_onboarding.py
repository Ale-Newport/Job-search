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
def workspace(tmp_path):
    app = create_app(tmp_path, token="onboarding-test", secret_store=MemorySecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer onboarding-test"}) as client:
        yield app, client


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
        json={"config": {"input_cost_per_million": 0.3, "output_cost_per_million": 1.2}, "secret": "test-only-key"},
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
        result = {
            "answer": "Sample Candidate is the test candidate.",
            "facts_used": ["connection-probe"],
            "confidence": 1,
        }
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
