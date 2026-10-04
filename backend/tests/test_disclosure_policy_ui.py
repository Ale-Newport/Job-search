"""Synthetic API and browser-prompt disclosure preference contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from jobagent.core import prepare_application, router
from jobagent.db import Database, now, uid
from jobagent.discovery import ingest_job
from jobagent.intelligence.store import policy
from jobagent.orchestrator import Orchestrator


@pytest.fixture
def api(tmp_path):
    db = Database(tmp_path / "disclosure-api.sqlite3")
    app = FastAPI()
    app.state.db = db
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        yield db, client


@pytest.mark.parametrize("initial", ["leave_blank", {"action": "leave_blank"}])
@pytest.mark.parametrize("desired", ["leave_blank", "use_confirmed"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_policy_editor_accepts_supported_actions_including_existing_structured_policy(api, initial, desired, confirmed):
    db, client = api
    policy(db, "personal_disclosures", initial, {"company": "Example"})
    before = db.one("SELECT * FROM candidate_policies WHERE subject='personal_disclosures'")
    response = client.put("/api/intelligence/policies/" + before["id"],
                          json={"value": desired, "confirmed": confirmed})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == before["id"]
    after = db.one("SELECT * FROM candidate_policies WHERE id=?", (before["id"],))
    assert json.loads(after["value"]) == desired
    assert bool(after["confirmed"]) is confirmed
    assert json.loads(after["scope"]) == {"company": "Example"}
    assert after["revision"] == before["revision"] + 1


@pytest.mark.parametrize("invalid", ["", "infer", "always_yes", "ask", "USE_CONFIRMED", None, True, 7,
                                     {"action": "use_confirmed"}, ["leave_blank"]])
def test_policy_editor_rejects_invalid_actions_without_changing_existing_policy(api, invalid):
    db, client = api
    policy(db, "personal_disclosures", {"action": "leave_blank"})
    before = db.one("SELECT * FROM candidate_policies WHERE subject='personal_disclosures'")
    response = client.put("/api/intelligence/policies/" + before["id"],
                          json={"value": invalid, "confirmed": True})
    assert response.status_code == 422, response.text
    assert db.one("SELECT * FROM candidate_policies WHERE id=?", (before["id"],)) == before


class CapturedPrompt:
    def __init__(self):
        self.payload = None
        self.handle = None

    async def show(self, page, payload, handle):
        self.payload, self.handle = payload, handle

    async def close(self):
        pass


@pytest.fixture
def prompt_workspace(tmp_path):
    db = Database(tmp_path / "disclosure-prompt.sqlite3")
    job, _ = ingest_job(db, {"title": "Software Engineer", "company": "Example",
                             "url": "https://example.test/engineer", "location": "London"})
    prepared = prepare_application(db, job["id"], "review", data_dir=tmp_path)
    orchestrator = Orchestrator(db, tmp_path)
    application = orchestrator.application(prepared["id"])
    run_id = uid()
    db.execute("INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'human_required',?,?)",
               (run_id, application["id"], now(), now()))
    orchestrator.browser.page = SimpleNamespace(is_closed=lambda: False)
    orchestrator.browser._validate_snapshot = AsyncMock(return_value=None)
    orchestrator.browser._check_pause = lambda: None
    orchestrator.prompt = CapturedPrompt()
    orchestrator.text_service = SimpleNamespace(draft=AsyncMock(side_effect=AssertionError("Personal facts cannot be drafted")))
    launches = []
    orchestrator.launch = lambda application_id, **kwargs: launches.append((application_id, kwargs))
    return db, orchestrator, application, run_id, launches


@pytest.mark.parametrize("label,description,profile_key,answer", [
    ("Your response", "Please give your preferred pronouns.", "pronouns", "they/them/theirs"),
    ("Additional information", "Please describe your current gender.", "gender", "Fixture identity"),
    ("Further details", "Describe the religion you were raised in during childhood.", None, "Private fixture answer"),
    ("Anything else you'd like to share?", "Please describe your gender and sexual orientation.", None, "Private fixture answer"),
    ("Additional context", "Describe your mental and physical health conditions.", None, "Private fixture answer"),
    ("Please elaborate", "Please explain your personal medical history.", None, "Private fixture answer"),
])
async def test_generic_personal_prompt_never_drafts_and_reuses_only_precise_concept(
    prompt_workspace, label, description, profile_key, answer
):
    db, orchestrator, application, run_id, launches = prompt_workspace
    result = {"snapshot_id": "synthetic-snapshot", "questions": [{"target": 1, "question": label,
               "description": description, "multiline": True, "type": "text", "required": False}]}
    await orchestrator.show_browser_question(run_id, application, result, [])
    assert orchestrator.prompt.payload["can_draft"] is False
    assert orchestrator.prompt.payload["can_reuse"] is bool(profile_key)
    with pytest.raises(ValueError, match="needs your own answer"):
        await orchestrator.prompt.handle({"action": "draft", "answer": "", "reusable": True})
    orchestrator.text_service.draft.assert_not_awaited()

    # A forged reuse request must not broaden an ambiguous medical/demographic
    # answer beyond this application, even if the browser did not show the toggle.
    await orchestrator.prompt.handle({"action": "answer", "answer": answer, "reusable": True})
    memory = db.one("SELECT * FROM browser_answers WHERE application_id=?", (application["id"],))
    assert bool(memory["reusable"]) is bool(profile_key)
    profile_facts = db.query("SELECT * FROM facts WHERE source LIKE 'application_profile:%'")
    if profile_key:
        assert [(fact["key"], fact["value"], fact["verification_status"]) for fact in profile_facts] == [
            (profile_key, answer, "verified")]
    else:
        assert profile_facts == []
    assert launches == [(application["id"], {"resume": True})]
