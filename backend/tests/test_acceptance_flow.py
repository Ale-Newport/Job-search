"""A complete local acceptance path through the public API and a real Chromium form.

Only test infrastructure is configured directly: temporary storage, an isolated
headless browser and an in-memory secret vault. The BrowserManager, orchestrator,
SQLite, document generation and email pipeline are all real implementations.
"""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from jobagent.main import create_app


class AcceptanceSecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def acceptance_server():
    fixtures = Path(__file__).parent / "browser" / "fixtures"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietFixtureHandler, directory=str(fixtures)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def require_ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def wait_for_review(client, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = require_ok(client.get(f"/api/automation/runs/{run_id}"))
        status = require_ok(client.get("/api/automation"))
        if run["status"] != "running" and not status["running"]:
            assert run["status"] == "needs_review", run
            return run
        time.sleep(.05)
    pytest.fail(f"Application preparation did not finish: {run}")


def test_api_to_browser_approval_email_deadline_and_timeline(tmp_path, acceptance_server, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = create_app(tmp_path, token="acceptance-only-token", secret_store=AcceptanceSecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer acceptance-only-token"}) as client:
        # Headless configuration only: the complete real browser engine runs on the
        # same lifespan loop as the orchestrator and does not mock any interaction.
        client.portal.call(app.state.automation.browser.start, True)

        fact_ids = []
        for category, key, value in [
            ("identity", "first_name", "Alex"),
            ("identity", "last_name", "Example"),
            ("contact", "email", "alex@example.test"),
            ("experience", "project", "Built a Python data pipeline for a university project."),
        ]:
            fact = require_ok(client.post("/api/facts", json={
                "category": category, "key": key, "value": value,
                "verification_status": "verified", "source": "Acceptance fixture reviewed by candidate",
            }), 201)
            fact_ids.append(fact["id"])

        job = require_ok(client.post("/api/jobs", json={
            "title": "Graduate Software Engineer", "company": "Arc Systems", "location": "London",
            "description": "Graduate Python software engineer working on data pipelines.",
            "url": acceptance_server + "/lever.html", "application_url": acceptance_server + "/lever.html",
            "source": "manual", "ats": "lever",
        }), 201)
        preview = require_ok(client.post("/api/documents/generate", json={
            "job_id": job["id"], "kind": "cv", "fact_ids": fact_ids, "approved": False,
        }))
        assert "Python data pipeline" in preview["text"]
        assert preview["approved"] is False and "diff" in preview
        generated = require_ok(client.post("/api/documents/generate", json={
            "job_id": job["id"], "kind": "cv", "fact_ids": fact_ids, "approved": True,
        }))
        version = generated["version"]
        assert version["approved"]
        download = client.get(f"/api/document-versions/{version['id']}/download")
        assert download.status_code == 200 and download.content.startswith(b"%PDF")

        application = require_ok(client.post(f"/api/jobs/{job['id']}/prepare", json={
            "mode": "review", "document_version_id": version["id"],
        }))
        application_id = application["id"]
        assert require_ok(client.post("/api/automation/resume"))["paused"] is False
        started = require_ok(client.post(f"/api/applications/{application_id}/apply"))
        run = wait_for_review(client, started["run_id"])
        assert run["checkpoint"]["snapshot_id"]
        assert run["checkpoint"]["questions"] == []
        assert run["steps"]
        assert all(step["operation"] != "CLICK" for step in run["steps"])
        browser = app.state.automation.browser
        assert client.portal.call(browser.page.evaluate, "window.submissions") == 0
        assert client.portal.call(browser.page.locator('[name="name"]').input_value) == "Alex Example"
        assert client.portal.call(browser.page.locator('[name="email"]').input_value) == "alex@example.test"
        uploaded_hash = client.portal.call(browser.page.locator('input[type="file"]').evaluate, """async node => {
            const digest = await crypto.subtle.digest('SHA-256', await node.files[0].arrayBuffer());
            return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
        }""")
        assert uploaded_hash == version["content_hash"]
        assert client.portal.call(browser.page.locator('input[type="file"]').evaluate, "node => node.files[0].name") == version["filename"]

        before_submit = require_ok(client.get(f"/api/applications/{application_id}"))
        assert before_submit["status"] == "NEEDS_REVIEW"
        assert before_submit["documents"][0]["id"] == version["id"]
        assert any(answer["fact_ids"] for answer in before_submit["answers"])
        assert not any(event["status"] == "CONFIRMED" for event in before_submit["events"])

        submitted = require_ok(client.post(f"/api/applications/{application_id}/approve", json={"approved": True}))
        assert submitted["status"] == "confirmed", submitted
        assert submitted["evidence"][0]["kind"] == "visible_confirmation"
        assert client.portal.call(browser.page.evaluate, "window.submissions") == 1
        confirmed = require_ok(client.get(f"/api/applications/{application_id}"))
        assert confirmed["status"] == "CONFIRMED"
        assert [event["status"] for event in confirmed["events"]][-2:] == ["APPLYING", "CONFIRMED"]
        confirmation_history = confirmed["events"]
        blocked_duplicate = client.post(f"/api/applications/{application_id}/apply")
        assert blocked_duplicate.status_code == 400
        assert client.portal.call(browser.page.evaluate, "window.submissions") == 1

        received = datetime.now(timezone.utc).replace(microsecond=0)
        message = EmailMessage()
        message["Message-ID"] = "<acceptance-coding-test@arc.example>"
        message["Date"] = format_datetime(received)
        message["From"] = "Arc Systems Recruitment <recruitment@arc.example>"
        message["To"] = "Alex Example <alex@example.test>"
        message["Subject"] = "Arc Systems — Graduate Software Engineer — coding challenge"
        message.set_content("Thank you for your interest in the Graduate Software Engineer role at Arc Systems. "
                            "Please complete the HackerRank coding challenge within 5 days. "
                            "Your test is at https://tests.example.test/challenge/acceptance.")
        imported = require_ok(client.post("/api/emails/import", files={
            "file": ("coding-test.eml", message.as_bytes(), "message/rfc822"),
        }))
        assert imported["application_id"] == application_id
        assert imported["classification"]["category"] == "CODING_TEST"
        assert datetime.fromisoformat(imported["classification"]["deadline"]) == received + timedelta(days=5)
        after_email = require_ok(client.get(f"/api/applications/{application_id}"))
        assert after_email["status"] == "TECHNICAL_TEST"
        assert after_email["events"][:-1] == confirmation_history
        assert after_email["events"][-1]["origin"] == "email"
        assert after_email["events"][-1]["status"] == "TECHNICAL_TEST"
        assert after_email["emails"][0]["id"] == imported["id"]
        dashboard = require_ok(client.get("/api/dashboard"))
        assert any(deadline["application_id"] == application_id for deadline in dashboard["deadlines"])

        repeated = require_ok(client.post("/api/emails/import", files={
            "file": ("same-coding-test.eml", message.as_bytes(), "message/rfc822"),
        }))
        assert repeated["duplicate"] is True
        assert require_ok(client.get(f"/api/applications/{application_id}"))["events"] == after_email["events"]

        # Read-only SQL evidence verifies the API assertions were persisted, rather
        # than produced only by the browser/UI response objects.
        db = app.state.db
        assert db.one("SELECT status FROM applications WHERE id=?", (application_id,))["status"] == "TECHNICAL_TEST"
        assert db.one("SELECT COUNT(*) AS n FROM email_messages WHERE application_id=?", (application_id,))["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM automation_steps WHERE run_id=? AND operation='UPLOAD'", (started["run_id"],))["n"] == 1
