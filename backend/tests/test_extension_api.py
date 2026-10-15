"""The desktop-app API of the browser assistant (/api/extension/*), onboarding step and tracker integration."""

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import chrome_manifest  # noqa: E402
from jobagent.extension import api as extension_api  # noqa: E402
from jobagent.extension import assets  # noqa: E402
from jobagent.extension.pairing import manager_for  # noqa: E402
from jobagent.main import create_app  # noqa: E402

CHROME = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc"
OTHER = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
SAFARI = "safari-web-extension://8F2D4C1B-9A3E-4E57-B1D0-6C7A5F3E2D10"


class StrictSecrets:
    """Nothing in the assistant's app API may read the Keychain."""

    def __init__(self):
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        raise AssertionError("the assistant API must never read the Keychain")

    def set(self, name, value):
        raise AssertionError("no secrets are written")

    def delete(self, name):
        raise AssertionError("no secrets are written")


class FakeBridge:
    def __init__(self, running=True, error=None, ports=(47615, 47616), port=47615):
        self.ports, self.error, self.running, self.port, self.conflict = list(ports), error, running, port, None
        self.restarted = 0

    def status(self):
        return {"running": self.running, "host": "127.0.0.1", "port": self.port if self.running else None,
                "ports": self.ports, "error": self.error, "conflict_process": self.conflict, "started_at": None}

    async def explain_failure(self):
        if self.error and self.conflict is None:
            self.conflict = "Some Other App"

    async def restart(self):
        self.restarted += 1
        self.running, self.error = True, None
        return True


def source(tmp_path, version="5.0.0"):
    chrome = tmp_path / "bundle" / "chrome"
    chrome.mkdir(parents=True)
    (chrome / "manifest.json").write_text(json.dumps(chrome_manifest(version)))
    (chrome / "background.js").write_text("x")
    return tmp_path / "bundle"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(source(tmp_path)))
    monkeypatch.delenv("MERIDIAN_APP_BUNDLE", raising=False)
    secrets = StrictSecrets()
    data = tmp_path / "data"
    app = create_app(data, worker_isolation=False, token="extension-api", secret_store=secrets, start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer extension-api"}) as client:
        yield types.SimpleNamespace(app=app, client=client, db=app.state.db, secrets=secrets, data=data,
                                    manager=manager_for(app))


def connect(workspace, origin=CHROME, version="5.0.0", seen=None):
    manager = workspace.manager
    started = manager.start(origin, "Meridian Assistant", version)
    manager.approve(started["pairing_id"], via="app")
    client_id = manager.status(started["pairing_id"], origin)["client_id"]
    if seen:
        workspace.db.execute("UPDATE extension_clients SET last_seen_at=? WHERE id=?", (seen, client_id))
    return client_id


def status(workspace):
    response = workspace.client.get("/api/extension")
    assert response.status_code == 200, response.text
    return response.json()


def add_job(workspace, url="https://boards.greenhouse.io/acme/jobs/123", title="Backend Engineer", company="Acme"):
    response = workspace.client.post("/api/jobs", json={"title": title, "company": company, "url": url})
    assert response.status_code == 201, response.text
    return response.json()


def add_session(workspace, client_id, job_id=None, sid="s1", host="boards.greenhouse.io", company="Acme", title="Backend Engineer"):
    workspace.db.execute(
        "INSERT INTO extension_sessions(id,client_id,url,canonical_url,host,job_id,company,title,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (sid, client_id, "https://x.test/" + sid, "https://x.test/" + sid, host, job_id, company, title,
         "2026-10-13T10:00:00+00:00", "2026-10-13T10:05:00+00:00"))
    return sid


def add_events(workspace, session_id, kind, count, outcome="", stamp="2026-10-13T10:10:00+00:00"):
    for index in range(count):
        workspace.db.execute("INSERT INTO extension_events(id,session_id,kind,outcome,created_at) VALUES(?,?,?,?,?)",
                             (f"{session_id}-{kind}-{outcome}-{stamp}-{index}", session_id, kind, outcome, stamp))


def walk(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")
    else:
        yield path, value


# ------------------------------------------------------------------------------------------------ status and precedence


def test_empty_status_shape(workspace):
    body = status(workspace)
    assert body["state"] == "not_installed" and body["app_version"] == "0.1.0"
    assert set(body) == {"state", "server_time", "app_version", "bridge", "browsers", "clients", "pending_pairing",
                         "settings", "drafts"}
    assert body["clients"] == [] and body["pending_pairing"] is None
    assert body["bridge"] == {"state": "stopped", "port": None, "candidate_ports": list(range(47615, 47625)),
                              "conflict_process": None, "message": "The browser connection is not running in this session."}
    chrome, safari = body["browsers"]["chrome"], body["browsers"]["safari"]
    assert chrome["bundled_version"] == "5.0.0" and chrome["bundled"] is True and chrome["state"] == "not_installed"
    assert chrome["extension_path"] is None and chrome["seen_unpaired_at"] is None and chrome["seen_version"] is None
    assert safari["extension_path"] is None and safari["bundled"] is False and safari["needs_unsigned_toggle"] is True
    assert "needs_unsigned_toggle" not in chrome
    assert isinstance(chrome["browser_installed"], bool) and isinstance(safari["browser_installed"], bool)
    assert body["drafts"]["configured"] is False and body["drafts"]["provider"] == "none"
    assert set(body["drafts"]) == {"configured", "provider", "model", "message", "month", "spent_usd", "calls",
                                   "monthly_limit_usd", "unlimited"}
    assert body["settings"]["enabled"] is True and body["settings"]["background_research"] is True
    datetime.fromisoformat(body["server_time"])


def test_state_precedence(workspace):
    manager, db = workspace.manager, workspace.db
    assert status(workspace)["state"] == "not_installed"
    manager.note_contact("chrome", CHROME, "5.0.0")
    assert status(workspace)["state"] == "detected"
    pending = manager.start(OTHER, "Meridian", "5.0.0")
    body = status(workspace)
    assert body["state"] == "pairing" and body["pending_pairing"]["id"] == pending["pairing_id"]
    manager.deny(pending["pairing_id"])
    assert status(workspace)["state"] == "detected"
    client_id = connect(workspace, version="5.0.0")
    assert status(workspace)["state"] == "ready"
    db.execute("UPDATE extension_clients SET version='4.9.9' WHERE id=?", (client_id,))
    outdated = status(workspace)
    assert outdated["state"] == "outdated" and outdated["clients"][0]["outdated"] is True
    assert outdated["browsers"]["chrome"]["state"] == "outdated"
    again = manager.start(OTHER, "Meridian", "5.0.0")
    assert status(workspace)["state"] == "pairing"  # a pending approval outranks an outdated client
    manager.deny(again["pairing_id"])
    workspace.app.state.bridge = FakeBridge(running=False, error="no free bridge port: [Errno 48]")
    broken = status(workspace)
    assert broken["state"] == "bridge_error"
    assert broken["bridge"] == {"state": "port_in_use", "port": 47615, "candidate_ports": [47615, 47616],
                                "conflict_process": "Some Other App", "message": "Meridian tried ports 47615 to 47616."}
    manager.start(OTHER, "Meridian", "5.0.0")
    assert status(workspace)["state"] == "bridge_error"  # a broken bridge outranks a pending approval
    assert workspace.client.patch("/api/extension/settings", json={"enabled": False}).json()["state"] == "disabled"
    workspace.client.patch("/api/extension/settings", json={"enabled": True})
    workspace.app.state.bridge = FakeBridge(running=True)
    healthy = status(workspace)
    assert healthy["bridge"]["state"] == "listening" and healthy["bridge"]["port"] == 47615
    assert healthy["state"] == "pairing"


def test_other_bridge_failures_are_errors_too(workspace):
    workspace.app.state.bridge = FakeBridge(running=False, error="bridge crashed")
    body = status(workspace)
    assert body["bridge"]["state"] == "error" and body["bridge"]["message"] == "bridge crashed"
    assert body["state"] == "bridge_error"
    workspace.app.state.bridge = FakeBridge(running=False, error=None)
    assert status(workspace)["bridge"]["state"] == "stopped" and status(workspace)["state"] == "not_installed"


def test_bridge_restart_endpoint(workspace):
    assert workspace.client.post("/api/extension/bridge/restart").status_code == 409
    workspace.app.state.bridge = bridge = FakeBridge(running=False, error="no free bridge port")
    body = workspace.client.post("/api/extension/bridge/restart").json()
    assert bridge.restarted == 1 and body["bridge"]["state"] == "listening" and body["state"] == "not_installed"


def test_clients_are_listed_without_secrets_and_with_stats(workspace):
    client_id = connect(workspace, version="5.0.0", seen="2026-10-13T11:00:00+00:00")
    sid = add_session(workspace, client_id)
    add_events(workspace, sid, "suggest", 5)
    add_events(workspace, sid, "accept", 3)
    add_events(workspace, sid, "feedback", 2, outcome="accepted")
    add_events(workspace, sid, "feedback", 4, outcome="edited")
    workspace.db.execute("INSERT INTO extension_events(id,session_id,kind,outcome,details,created_at) VALUES('err',?,'error','','{\"message\":\"Bridge slow\"}','2026-10-13T11:00:00+00:00')", (sid,))
    body = status(workspace)
    (client,) = body["clients"]
    assert client == {"id": client_id, "browser": "chrome", "label": "Chrome on this Mac", "version": "5.0.0",
                      "paired_at": client["paired_at"], "last_seen_at": "2026-10-13T11:00:00+00:00",
                      "last_error": "Bridge slow", "outdated": False,
                      "stats": {"pages": 1, "suggestions": 5, "insertions": 5}}
    assert body["browsers"]["chrome"]["state"] == "connected"
    assert body["browsers"]["chrome"]["extension_path"] is None  # nothing prepared yet
    workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"})
    assert status(workspace)["browsers"]["chrome"]["extension_path"] == str(workspace.data / "extensions" / "chrome")


def test_no_response_ever_contains_a_secret(workspace):
    manager = workspace.manager
    started = manager.start(CHROME, "Meridian Assistant", "5.0.0")
    responses = [status(workspace)]
    approved = workspace.client.post(f"/api/extension/pairings/{started['pairing_id']}/approve")
    responses.append(approved.json())
    issued = manager.status(started["pairing_id"], CHROME)
    secret = issued["token"].split(".")[2]
    token_hash = workspace.db.one("SELECT token_hash FROM extension_clients")["token_hash"]
    client_id = issued["client_id"]
    responses += [status(workspace), workspace.client.get("/api/extension/activity").json(),
                  workspace.client.patch("/api/extension/settings", json={"language": "es"}).json(),
                  workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"}).json(),
                  workspace.client.post("/api/extension/install/safari", json={"action": "prepare"}).json(),
                  workspace.client.delete(f"/api/extension/clients/{client_id}").json(),
                  workspace.client.get("/api/onboarding").json(), workspace.client.get("/api/tracker").json()]
    text = json.dumps(responses)
    for forbidden in ("mrd1", secret, token_hash, issued["token"]):
        assert forbidden not in text
    for path, _ in (item for response in responses for item in walk(response)):
        assert not path.lower().endswith((".token", ".token_hash", ".secret", ".password")), path
    assert "mrd1" not in json.dumps(workspace.client.get("/api/settings").json())
    assert workspace.secrets.reads == []


# ------------------------------------------------------------------------------------------------ pairings and clients


def test_approve_and_deny_pairings_from_the_app(workspace):
    manager = workspace.manager
    first = manager.start(CHROME, "Meridian", "5.0.0")
    body = status(workspace)
    pending = body["pending_pairing"]
    assert pending == {"id": first["pairing_id"], "browser": "chrome", "code": first["code"], "extension_version": "5.0.0",
                       "requested_at": pending["requested_at"], "expires_at": pending["expires_at"], "recognized": True}
    approved = workspace.client.post(f"/api/extension/pairings/{first['pairing_id']}/approve").json()
    assert approved["pending_pairing"] is None and approved["state"] == "ready" and len(approved["clients"]) == 1
    second = manager.start(OTHER, "Meridian", "5.0.0")
    assert status(workspace)["pending_pairing"]["recognized"] is False  # an unpinned extension id is flagged
    denied = workspace.client.post(f"/api/extension/pairings/{second['pairing_id']}/deny").json()
    assert denied["pending_pairing"] is None and len(denied["clients"]) == 1
    assert manager.status(second["pairing_id"], OTHER) == {"state": "denied"}


def test_answering_a_pairing_twice_or_late_is_explained(workspace):
    manager = workspace.manager
    first = manager.start(CHROME, "Meridian", "5.0.0")["pairing_id"]
    workspace.client.post(f"/api/extension/pairings/{first}/approve")
    again = workspace.client.post(f"/api/extension/pairings/{first}/approve")
    assert again.status_code == 409 and isinstance(again.json()["detail"], str)
    assert workspace.client.post(f"/api/extension/pairings/{first}/deny").status_code == 409
    assert workspace.client.post("/api/extension/pairings/unknown-id-0000000000/approve").status_code == 404
    late = manager.start(OTHER, "Meridian", "5.0.0")["pairing_id"]
    workspace.db.execute("UPDATE extension_pairings SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (late,))
    expired = workspace.client.post(f"/api/extension/pairings/{late}/approve")
    assert expired.status_code == 410 and "expired" in expired.json()["detail"].lower()
    assert status(workspace)["state"] == "ready" and status(workspace)["pending_pairing"] is None


def test_the_app_api_needs_the_app_token(workspace):
    anonymous = TestClient(workspace.app)
    assert anonymous.get("/api/extension").status_code == 401
    assert anonymous.post("/api/extension/pairings/x/approve").status_code == 401
    # an extension origin can never use it, even with the app token
    forged = workspace.client.get("/api/extension", headers={"Origin": CHROME})
    assert forged.status_code == 403


def test_disconnect_revokes_the_browser(workspace):
    client_id = connect(workspace)
    other = connect(workspace, origin=SAFARI)
    after = workspace.client.delete(f"/api/extension/clients/{client_id}").json()
    assert [c["id"] for c in after["clients"]] == [other]
    row = workspace.db.one("SELECT revoked_at FROM extension_clients WHERE id=?", (client_id,))
    assert row["revoked_at"] is not None
    assert workspace.client.delete(f"/api/extension/clients/{client_id}").status_code == 200  # idempotent
    assert workspace.client.delete("/api/extension/clients/00000000-0000-0000-0000-000000000000").status_code == 404
    # history is kept for the activity feed
    kinds = [item["kind"] for item in workspace.client.get("/api/extension/activity").json()["items"]]
    assert kinds.count("paired") == 2 and kinds.count("revoked") == 1


# ------------------------------------------------------------------------------------------------ settings


def test_core_defaults_for_every_extension_setting(workspace):
    settings = workspace.client.get("/api/settings").json()
    expected = {
        "extension_enabled": True, "extension_language": "auto", "extension_auto_advance": True,
        "extension_skip_filled": True, "extension_tab_inserts_protected": False, "extension_save_corrections": "ask",
        "extension_research_enabled": True, "extension_background_drafts": True, "extension_local_assist": True,
        "extension_web_lookup": True,
        "extension_hint_on_ats": True, "extension_pairing_dialog": True, "extension_draft_budget_usd": None,
        "extension_always_on_sites": [], "extension_research_concurrency": 2, "extension_company_sites": {},
    }
    assert {key: settings[key] for key in expected} == expected
    assert extension_api.read_extension_settings(workspace.db) == {
        "enabled": True, "language": "auto", "auto_advance": True, "skip_filled": True, "tab_inserts_protected": False,
        "save_corrections": "ask", "background_research": True, "background_drafts": True, "local_assist": True,
        "web_lookup": True, "hint_on_ats": True, "draft_budget_usd": extension_api.DEFAULT_DRAFT_BUDGET_USD, "always_on_sites": []}
    # the stored default stays "unset"; what a page may spend by default is never "unlimited" (SEC-06)
    assert extension_api.DEFAULT_DRAFT_BUDGET_USD == 1.0


def test_setting_name_mapping_is_one_bijection():
    forward = extension_api.translate_settings({"background_research": False, "enabled": True, "other": 1}, to_core=True)
    assert forward == {"extension_research_enabled": False, "extension_enabled": True}
    assert extension_api.translate_settings(forward, to_core=False) == {"background_research": False, "enabled": True}
    assert len(set(extension_api.SETTING_KEYS.values())) == len(extension_api.SETTING_KEYS) == 13


def test_settings_patch_persists_and_returns_the_status(workspace):
    patch = {"language": "es", "auto_advance": False, "skip_filled": False, "tab_inserts_protected": True,
             "save_corrections": "always", "background_research": False, "background_drafts": False, "local_assist": False,
             "web_lookup": False,
             "hint_on_ats": False, "draft_budget_usd": 2.5, "always_on_sites": ["Jobs.Example.com", "careers.acme.io.", "jobs.example.com"]}
    body = workspace.client.patch("/api/extension/settings", json=patch).json()
    assert body["settings"] == patch | {"enabled": True, "always_on_sites": ["jobs.example.com", "careers.acme.io"]}
    core = workspace.client.get("/api/settings").json()
    assert core["extension_research_enabled"] is False and core["extension_language"] == "es"
    assert core["extension_draft_budget_usd"] == 2.5 and core["extension_always_on_sites"] == ["jobs.example.com", "careers.acme.io"]
    cleared = workspace.client.patch("/api/extension/settings", json={"draft_budget_usd": None}).json()
    # clearing the amount means "the default", never "no limit" (a large amount is how to ask for that)
    assert cleared["settings"]["draft_budget_usd"] == extension_api.DEFAULT_DRAFT_BUDGET_USD
    assert workspace.client.get("/api/settings").json()["extension_draft_budget_usd"] is None
    assert cleared["settings"]["language"] == "es"


@pytest.mark.parametrize("bad", [
    {}, {"unknown": 1}, {"enabled": "yes"}, {"auto_advance": 1}, {"language": "fr"}, {"language": None},
    {"save_corrections": "sometimes"}, {"draft_budget_usd": 0}, {"draft_budget_usd": -3}, {"draft_budget_usd": "5"},
    {"draft_budget_usd": True}, {"draft_budget_usd": 100000}, {"always_on_sites": "example.com"},
    {"always_on_sites": ["https://example.com"]}, {"always_on_sites": ["localhost"]}, {"always_on_sites": ["*.example.com"]},
    {"always_on_sites": ["1.2.3.4"]}, {"always_on_sites": ["a b.example.com"]}, {"always_on_sites": [5]},
    {"always_on_sites": ["a.com"] * 101}, {"always_on_sites": ["x" * 300 + ".com"]},
])
def test_invalid_settings_are_rejected_with_a_sentence(workspace, bad):
    response = workspace.client.patch("/api/extension/settings", json=bad)
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)
    assert workspace.db.query("SELECT key FROM settings WHERE key LIKE 'extension\\_%' ESCAPE '\\'") == []


def test_values_stored_through_the_generic_settings_api_are_validated_when_read(workspace):
    assert workspace.client.patch("/api/settings", json={"extension_language": "klingon", "extension_enabled": True}).status_code == 200
    assert workspace.client.patch("/api/settings", json={"extension_always_on_sites": ["not a host"]}).status_code == 200
    assert workspace.client.patch("/api/settings", json={"extension_draft_budget_usd": -1}).status_code == 200
    settings = status(workspace)["settings"]
    assert settings["language"] == "auto" and settings["always_on_sites"] == []
    assert settings["draft_budget_usd"] == extension_api.DEFAULT_DRAFT_BUDGET_USD
    assert workspace.client.patch("/api/settings", json={"extension_enabled": "nope"}).status_code == 422  # type-checked


# ------------------------------------------------------------------------------------------------ drafts availability


def test_drafts_block_is_cheap_and_never_reads_the_keychain(workspace):
    db = workspace.db
    db.execute("INSERT INTO settings VALUES('text_provider','\"ollama\"')")
    db.execute("INSERT INTO settings VALUES('text_model','\"qwen3\"')")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    db.execute("INSERT INTO settings VALUES(?,?)", ("ai_usage:" + month, json.dumps({"cost": 0, "calls": 7})))
    drafts = status(workspace)["drafts"]
    assert drafts["configured"] is True and drafts["provider"] == "ollama" and drafts["model"] == "qwen3"
    assert drafts["calls"] == 7 and drafts["month"] == month and drafts["monthly_limit_usd"] is None
    db.execute("UPDATE settings SET value='\"openai\"' WHERE key='text_provider'")
    cloud = status(workspace)["drafts"]
    assert cloud["configured"] is False and "spending cap" in cloud["message"]
    assert workspace.secrets.reads == []


def test_generation_fingerprint_matches_the_onboarding_one(workspace):
    from jobagent.onboarding import TEXT_AI, fingerprint

    workspace.db.execute("INSERT INTO settings VALUES('text_provider','\"ollama\"')")
    workspace.db.execute("INSERT INTO settings VALUES('text_model','\"qwen3\"')")
    light = extension_api.light_settings(workspace.db, TEXT_AI)
    assert extension_api._fingerprint(light, TEXT_AI) == fingerprint(workspace.db, TEXT_AI)


# ------------------------------------------------------------------------------------------------ install helpers


def os_actions(workspace):
    return getattr(workspace.app.state, "extension_os_actions", [])


def test_chrome_install_actions(workspace):
    folder = str(workspace.data / "extensions" / "chrome")
    prepared = workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"}).json()
    assert prepared == {"ok": True, "message": "The Chrome extension folder is ready.", "path": folder}
    assert (workspace.data / "extensions" / "chrome" / "manifest.json").is_file() and os_actions(workspace) == []
    reveal = workspace.client.post("/api/extension/install/chrome", json={"action": "reveal"}).json()
    assert reveal["ok"] is True and reveal["path"] == folder
    page = workspace.client.post("/api/extension/install/chrome", json={"action": "open_browser_page"}).json()
    assert page["ok"] is True
    copied = workspace.client.post("/api/extension/install/chrome", json={"action": "copy_path"}).json()
    assert copied["ok"] is True and copied["path"] == folder
    assert os_actions(workspace) == [
        {"args": ["/usr/bin/open", "-R", folder], "stdin": None},
        {"args": ["/usr/bin/open", "-b", "com.google.Chrome", "chrome://extensions"], "stdin": None},
        {"args": ["/usr/bin/pbcopy"], "stdin": folder},
    ]


def test_safari_install_actions(workspace, tmp_path, monkeypatch):
    prepare = workspace.client.post("/api/extension/install/safari", json={"action": "prepare"}).json()
    assert prepare["ok"] is False and "does not include the Safari extension" in prepare["message"]
    page = workspace.client.post("/api/extension/install/safari", json={"action": "open_browser_page"}).json()
    assert page["ok"] is True and os_actions(workspace)[-1]["args"] == ["/usr/bin/open", "-b", "com.apple.Safari"]
    assert workspace.client.post("/api/extension/install/safari", json={"action": "copy_path"}).json()["ok"] is False
    assert workspace.client.post("/api/extension/install/safari", json={"action": "reveal"}).json()["ok"] is False
    bundle = tmp_path / "Meridian.app"
    (bundle / "Contents" / "PlugIns" / "Assistant.appex").mkdir(parents=True)
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", str(bundle))
    assert workspace.client.post("/api/extension/install/safari", json={"action": "prepare"}).json()["ok"] is True
    assert status(workspace)["browsers"]["safari"]["bundled"] is True
    reveal = workspace.client.post("/api/extension/install/safari", json={"action": "reveal"}).json()
    assert reveal["ok"] is True and os_actions(workspace)[-1]["args"] == ["/usr/bin/open", "-R", str(bundle)]
    # none of this ever touched /Applications/Meridian.app or ran for real
    assert all(action["args"][0] in ("/usr/bin/open", "/usr/bin/pbcopy") for action in os_actions(workspace))


@pytest.mark.parametrize("path,body", [
    ("/api/extension/install/chrome", {"action": "format"}), ("/api/extension/install/chrome", {}),
    ("/api/extension/install/chrome", {"action": "reveal", "path": "/etc"}), ("/api/extension/install/firefox", {"action": "prepare"}),
    ("/api/extension/install/chrome", {"action": "install", "path": "/etc"}),
    ("/api/extension/install/chrome", {"action": "install", "update": "yes"}),
    ("/api/extension/install/chrome", {"action": "reveal", "update": True}),  # `update` belongs to the combined install only
    ("/api/extension/install/safari", {"action": "install", "steps": ["format"]}),
])
def test_install_requests_cannot_choose_paths_or_commands(workspace, path, body):
    assert workspace.client.post(path, json=body).status_code == 422
    assert os_actions(workspace) == []


def test_install_without_a_bundled_extension_explains_itself(workspace, monkeypatch):
    monkeypatch.setattr(assets, "extension_source", lambda: None)
    response = workspace.client.post("/api/extension/install/chrome", json={"action": "reveal"})
    assert response.status_code == 409 and "not bundled" in response.json()["detail"]
    assert os_actions(workspace) == []


def install_chrome(workspace, **body):
    response = workspace.client.post("/api/extension/install/chrome", json={"action": "install", **body})
    assert response.status_code == 200, response.text
    return response.json()


def test_chrome_install_is_one_action_that_runs_every_step_in_order(workspace, monkeypatch):
    """FR-01: one click prepares the folder, puts its path on the clipboard, shows it in Finder and opens the extensions page."""
    monkeypatch.setattr(extension_api, "_chrome_installed", lambda: True)
    folder = str(workspace.data / "extensions" / "chrome")
    body = install_chrome(workspace)
    assert body["ok"] is True and body["path"] == folder and body["message"]
    assert [(step["action"], step["ok"]) for step in body["steps"]] == [
        ("prepare", True), ("copy_path", True), ("reveal", True), ("open_browser_page", True)]
    assert all(step["message"] and "skipped" not in step for step in body["steps"])
    assert (workspace.data / "extensions" / "chrome" / "manifest.json").is_file()
    # the path is copied while Meridian is in front and Chrome is the last window to come forward
    assert os_actions(workspace) == [
        {"args": ["/usr/bin/pbcopy"], "stdin": folder},
        {"args": ["/usr/bin/open", "-R", folder], "stdin": None},
        {"args": ["/usr/bin/open", "-b", "com.google.Chrome", "chrome://extensions"], "stdin": None},
    ]
    assert "path" not in body["steps"][0] and set(body) == {"ok", "message", "path", "steps"}


def test_chrome_update_only_prepares_the_folder_and_opens_the_page(workspace, monkeypatch):
    monkeypatch.setattr(extension_api, "_chrome_installed", lambda: True)
    body = install_chrome(workspace, update=True)
    assert body["ok"] is True and body["path"] == str(workspace.data / "extensions" / "chrome")
    assert [step["action"] for step in body["steps"]] == ["prepare", "open_browser_page"]
    assert os_actions(workspace) == [{"args": ["/usr/bin/open", "-b", "com.google.Chrome", "chrome://extensions"], "stdin": None}]
    assert install_chrome(workspace, update=False)["steps"][1]["action"] == "copy_path"


def test_chrome_install_without_chrome_still_prepares_copies_and_shows_the_folder(workspace, monkeypatch):
    """Brave, Edge and other Chromium browsers load the same folder: that is no failure, just no Chrome to open."""
    monkeypatch.setattr(extension_api, "_chrome_installed", lambda: False)
    body = install_chrome(workspace)
    assert body["ok"] is True and "Chrome was not found" in body["message"] and "chrome://extensions" in body["message"]
    assert [(step["action"], step["ok"], step.get("skipped")) for step in body["steps"]] == [
        ("prepare", True, None), ("copy_path", True, None), ("reveal", True, None), ("open_browser_page", True, True)]
    assert [action["args"][0] for action in os_actions(workspace)] == ["/usr/bin/pbcopy", "/usr/bin/open"]
    assert all("com.google.Chrome" not in action["args"] for action in os_actions(workspace))


def test_a_failed_install_step_is_named_and_the_others_still_run(workspace, monkeypatch):
    """The folder was prepared, so Show in Finder and the extensions page still help: only the honest answer changes."""
    monkeypatch.setattr(extension_api, "_chrome_installed", lambda: True)
    real = extension_api.run_os

    async def no_clipboard(app, *args, **kwargs):
        if args[0] == "/usr/bin/pbcopy":
            return False, "macOS did not respond to that request."
        return await real(app, *args, **kwargs)

    monkeypatch.setattr(extension_api, "run_os", no_clipboard)
    body = install_chrome(workspace)
    assert body["ok"] is False and "could not copy the folder path" in body["message"]
    assert [(step["action"], step["ok"]) for step in body["steps"]] == [
        ("prepare", True), ("copy_path", False), ("reveal", True), ("open_browser_page", True)]
    assert body["steps"][1]["message"] == "macOS did not respond to that request."
    assert body["path"] == str(workspace.data / "extensions" / "chrome")
    assert [action["args"][0] for action in os_actions(workspace)] == ["/usr/bin/open", "/usr/bin/open"]

    async def no_chrome(app, *args, **kwargs):
        if "com.google.Chrome" in args:
            return False, ""
        return await real(app, *args, **kwargs)

    monkeypatch.setattr(extension_api, "run_os", no_chrome)
    opened = install_chrome(workspace)
    assert opened["ok"] is False and opened["message"] == "Chrome is not installed."
    assert [step["ok"] for step in opened["steps"]] == [True, True, True, False]


def test_safari_install_prepares_then_opens_safari(workspace, tmp_path, monkeypatch):
    missing = workspace.client.post("/api/extension/install/safari", json={"action": "install"}).json()
    assert missing["ok"] is False and "does not include the Safari extension" in missing["message"]
    assert [(step["action"], step["ok"]) for step in missing["steps"]] == [("prepare", False)]
    assert "path" not in missing and os_actions(workspace) == []  # nothing to enable: Safari is not opened for it
    bundle = tmp_path / "Meridian.app"
    (bundle / "Contents" / "PlugIns" / "Assistant.appex").mkdir(parents=True)
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", str(bundle))
    body = workspace.client.post("/api/extension/install/safari", json={"action": "install"}).json()
    assert body["ok"] is True and "path" not in body
    assert [(step["action"], step["ok"]) for step in body["steps"]] == [("prepare", True), ("open_browser_page", True)]
    assert os_actions(workspace) == [{"args": ["/usr/bin/open", "-b", "com.apple.Safari"], "stdin": None}]


def test_the_individual_install_actions_still_answer_as_before(workspace):
    """The buttons next to the install path ("Copy path again", "Show in Finder", "Open extensions page") keep their calls."""
    for action in ("prepare", "reveal", "open_browser_page", "copy_path"):
        body = workspace.client.post("/api/extension/install/chrome", json={"action": action}).json()
        assert body["ok"] is True and "steps" not in body, action


def import_cv(workspace, name="cv.txt"):
    response = workspace.client.post(
        "/api/documents/import", files={"file": (name, b"Sample Candidate\nsample@example.test\nSKILLS\nPython", "text/plain")})
    assert response.status_code == 201, response.text
    return workspace.db.one("SELECT id FROM document_versions ORDER BY created_at DESC, rowid DESC LIMIT 1")["id"]


def test_the_status_follows_a_cv_import_and_approval_at_once(workspace):
    """ASSIST3 open issue: GET /v1/status is cached for a minute, but approving a CV in Meridian is what the panel waits for."""
    profile = lambda: extension_api.bridge_status(workspace.app)["profile"]  # noqa: E731
    assert profile()["has_cv"] is False and profile()["unapproved_cvs"] == 0  # the first answer fills the cache
    version = import_cv(workspace)
    assert profile()["unapproved_cvs"] == 1 and profile()["has_cv"] is False
    assert workspace.client.post(f"/api/document-versions/{version}/approve").status_code == 200
    assert profile()["has_cv"] is True and profile()["unapproved_cvs"] == 0
    assert workspace.client.post(f"/api/document-versions/{version}/approve").status_code == 200  # approving twice changes nothing
    assert profile()["has_cv"] is True
    # a repaired import is a new version that waits for approval again
    document = workspace.db.one("SELECT document_id FROM document_versions WHERE id=?", (version,))["document_id"]
    workspace.db.execute("DELETE FROM settings WHERE key=?", (f"document_parser:{version}",))
    assert workspace.client.post(f"/api/documents/{document}/reimport").status_code == 200
    assert profile()["unapproved_cvs"] == 1


def test_the_status_follows_the_writing_model_its_budget_and_its_test_at_once(workspace):
    """The panel's ``generation`` block is cached for a minute; changing the model, its budget or the connection test in
    Meridian must show in it on the next read, by whichever path the change was made."""
    from jobagent.onboarding import record_ai_probe

    generation = lambda: extension_api.bridge_status(workspace.app)["generation"]  # noqa: E731
    assert generation()["provider"] == "none" and generation()["tested"] is False  # the first answer fills the cache
    cached = workspace.app.state.extension_status_cache
    assert generation() and workspace.app.state.extension_status_cache is cached  # unchanged inputs: served from the cache
    assert workspace.client.patch("/api/settings", json={"text_provider": "ollama", "text_model": "qwen3"}).status_code == 200
    first = generation()
    assert first["provider"] == "ollama" and first["available"] is True and first["tested"] is False
    # the connection test is recorded straight into the database (POST /api/ai/text/test): no cache is cleared by hand
    record_ai_probe(workspace.db, "text", {"answer": "ok", "provider": "ollama"})
    assert generation()["tested"] is True
    workspace.db.execute("UPDATE settings SET value='\"qwen3-next\"' WHERE key='text_model'")  # not through the API either
    assert generation()["tested"] is False  # a different model: the test no longer vouches for it
    workspace.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('text_provider','\"openai\"')")
    cloud = generation()
    assert cloud["provider"] == "openai" and cloud["available"] is False and cloud["local"] is False
    stamp = extension_api.generation_stamp(workspace.db)
    workspace.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('monthly_budget','5')")
    assert extension_api.generation_stamp(workspace.db) != stamp  # the budget is part of what the cached block depends on
    assert workspace.client.patch("/api/settings", json={"monthly_budget": 7}).status_code == 200
    assert workspace.app.state.extension_status_cache is None  # and a change made through Settings drops the cache outright


def test_a_failed_approval_leaves_the_cached_status_alone(workspace):
    extension_api.bridge_status(workspace.app)
    cached = workspace.app.state.extension_status_cache
    assert workspace.client.post("/api/document-versions/not-a-version/approve").status_code in (404, 422)
    assert workspace.app.state.extension_status_cache is cached


def test_a_cv_written_for_one_job_is_not_the_users_own_cv(workspace):
    """has_cv and unapproved_cvs only count base CVs (job_id empty): the same rule the onboarding step reads."""
    job = add_job(workspace)
    workspace.db.execute("INSERT INTO documents VALUES('d-job','Acme CV','cv',?,'t','t')", (job["id"],))
    workspace.db.execute(
        "INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,text,approved,created_at) "
        "VALUES('v-job','d-job',1,'/x','cv.pdf','application/pdf','h','Sample CV',1,'2020-01-01T00:00:00+00:00')")
    assert extension_api.base_cv_versions(workspace.db, approved=True) == 0
    assert extension_api.profile_summary(workspace.db)["has_cv"] is False
    version = import_cv(workspace)
    assert extension_api.base_cv_versions(workspace.db, approved=False) == 1
    assert workspace.client.post(f"/api/document-versions/{version}/approve").status_code == 200
    assert extension_api.base_cv_versions(workspace.db, approved=True) == 1
    assert extension_api.profile_summary(workspace.db)["has_cv"] is True


async def test_os_commands_report_failures_and_never_use_a_shell(workspace, monkeypatch):
    monkeypatch.delenv("MERIDIAN_TEST")
    started = []

    class Process:
        returncode = 1

        async def communicate(self, data=None):
            started.append(("stdin", data))
            return b"", b"No application knows how to open the file\n"

    async def fake(*args, **kwargs):
        started.append(("argv", args, kwargs.get("stdin")))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    monkeypatch.setattr(extension_api.sys, "platform", "darwin")
    ok, message = await extension_api.run_os(workspace.app, "/usr/bin/open", "-R", "/some path/with spaces; rm -rf ~")
    assert ok is False and message == "No application knows how to open the file"
    assert started[0][1] == ("/usr/bin/open", "-R", "/some path/with spaces; rm -rf ~")  # one argv entry, no shell
    monkeypatch.setattr(extension_api.sys, "platform", "linux")
    assert (await extension_api.run_os(workspace.app, "/usr/bin/open"))[0] is False


async def test_a_helper_that_never_answers_is_killed(workspace, monkeypatch):
    monkeypatch.delenv("MERIDIAN_TEST")
    monkeypatch.setattr(extension_api.sys, "platform", "darwin")
    killed = []

    class Hung:
        returncode = None

        async def communicate(self, data=None):
            await asyncio.sleep(30)

        def kill(self):
            killed.append(True)

    async def fake(*args, **kwargs):
        return Hung()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    ok, message = await extension_api.run_os(workspace.app, "/usr/bin/open", "-b", "com.google.Chrome", timeout=0.05)
    assert ok is False and message == "macOS did not respond to that request." and killed == [True]


# ------------------------------------------------------------------------------------------------ open a job


def test_open_with_assistant_uses_the_paired_browser_and_the_exact_url(workspace):
    url = "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_x=1"
    job = add_job(workspace, url=url)
    stored = workspace.db.one("SELECT url,application_url FROM jobs WHERE id=?", (job["id"],))
    expected = stored["application_url"] or stored["url"]
    no_browser = workspace.client.post(f"/api/extension/jobs/{job['id']}/open")
    assert no_browser.status_code == 409 and "Connect a browser" in no_browser.json()["detail"]
    connect(workspace, origin=CHROME, seen="2026-10-13T09:00:00+00:00")
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()
    assert opened["ok"] is True and opened["browser"] == "chrome"
    assert os_actions(workspace)[-1] == {"args": ["/usr/bin/open", "-b", "com.google.Chrome", expected], "stdin": None}
    connect(workspace, origin=SAFARI, seen="2026-10-13T12:00:00+00:00")  # the most recently used browser wins
    assert workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()["browser"] == "safari"
    assert os_actions(workspace)[-1]["args"] == ["/usr/bin/open", "-b", "com.apple.Safari", expected]


def test_open_with_assistant_validates_the_job_and_the_address(workspace):
    connect(workspace)
    assert workspace.client.post("/api/extension/jobs/missing/open").status_code == 404
    job = add_job(workspace)
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://exa mple.com/x", "-b com.evil", "https://x\n.example/", ""):
        workspace.db.execute("UPDATE jobs SET application_url=?,url=? WHERE id=?", (bad, bad, job["id"]))
        response = workspace.client.post(f"/api/extension/jobs/{job['id']}/open")
        assert response.status_code == 422, bad
    assert os_actions(workspace) == []
    workspace.client.patch("/api/extension/settings", json={"enabled": False})
    workspace.db.execute("UPDATE jobs SET application_url='https://ok.example/apply' WHERE id=?", (job["id"],))
    assert workspace.client.post(f"/api/extension/jobs/{job['id']}/open").status_code == 409


def test_open_primes_research_through_the_research_manager(workspace):
    job = add_job(workspace, title="Platform Engineer", company="Globex")
    connect(workspace)
    calls = []

    class Research:
        def start(self, session_id, report, **kwargs):
            calls.append((session_id, report))
            return {"state": "running"}

    workspace.app.state.extension_research = Research()
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()
    assert "Researching the company" in opened["message"]
    (session_id, report), = calls
    assert session_id == f"prime:{job['id']}"
    assert report["user"] == {"company": "Globex", "title": "Platform Engineer"} and report["url"].startswith("https://")
    assert report["jsonld"] == [] and report["links"] == [] and "company_hint" in report
    workspace.client.patch("/api/extension/settings", json={"background_research": False})
    assert "Researching" not in workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()["message"]
    assert len(calls) == 1  # switched off: nothing is researched


def test_open_uses_the_research_modules_prime_research_and_keeps_no_copy_of_its_own(workspace, monkeypatch):
    from jobagent.extension import research

    assert not hasattr(extension_api, "prime_research")  # the duplicate wrapper is gone
    job = add_job(workspace, title="Platform Engineer", company="Globex")
    connect(workspace)
    seen = []
    outcome = {"value": {"run_id": "rs_1", "state": "running"}}

    def prime(app, job_id):
        seen.append((app, job_id))
        return outcome["value"]

    monkeypatch.setattr(research, "prime_research", prime)
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()
    assert seen == [(workspace.app, job["id"])] and "Researching the company" in opened["message"]
    outcome["value"] = None  # the module found nothing to start (unknown job, closed manager, malformed address)
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()
    assert opened["ok"] is True and "Researching" not in opened["message"] and len(seen) == 2
    workspace.client.patch("/api/extension/settings", json={"background_research": False})
    workspace.client.post(f"/api/extension/jobs/{job['id']}/open")
    assert len(seen) == 2  # the user's switch is honoured before the module is even called
    assert os_actions(workspace)[-1]["args"][:3] == ["/usr/bin/open", "-b", "com.google.Chrome"]


def test_open_starts_a_real_research_run_without_touching_the_network(workspace):
    job = add_job(workspace, title="Platform Engineer", company="Globex")
    connect(workspace)
    manager = workspace.app.state.extension_research
    assert manager is not None and not manager._runs
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open").json()
    assert opened["ok"] is True and "Researching the company" in opened["message"]
    (run,) = manager._runs.values()
    assert run.company["name"] == "Globex" and run.report["user"]["title"] == "Platform Engineer"


def test_research_problems_never_block_opening(workspace):
    job = add_job(workspace)
    connect(workspace)

    class Broken:
        def start(self, *args, **kwargs):
            raise RuntimeError("research crashed")

    workspace.app.state.extension_research = Broken()
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open")
    assert opened.status_code == 200 and opened.json()["ok"] is True and "Researching" not in opened.json()["message"]
    assert os_actions(workspace)[-1]["args"][:3] == ["/usr/bin/open", "-b", "com.google.Chrome"]
    workspace.app.state.extension_research = None  # the assistant's services failed to start
    opened = workspace.client.post(f"/api/extension/jobs/{job['id']}/open")
    assert opened.status_code == 200 and "Researching" not in opened.json()["message"]


# ------------------------------------------------------------------------------------------------ activity


def test_activity_feed_is_timeline_compatible_and_value_free(workspace):
    client_id = connect(workspace)
    sid = add_session(workspace, client_id)
    add_events(workspace, sid, "accept", 3, stamp="2026-10-13T10:10:00+00:00")
    add_events(workspace, sid, "policy_block", 2, outcome="sensitive_blocked", stamp="2026-10-13T10:11:00+00:00")
    add_events(workspace, sid, "draft_ready", 1, stamp="2026-10-13T10:12:00+00:00")
    add_events(workspace, sid, "error", 1, stamp="2026-10-13T10:13:00+00:00")
    add_events(workspace, sid, "suggest", 40, stamp="2026-10-13T10:14:00+00:00")  # not user-facing activity
    feed = workspace.client.get("/api/extension/activity?limit=100").json()
    assert feed["total"] == len(feed["items"])
    by_kind = {item["kind"]: item for item in feed["items"]}
    assert set(by_kind) == {"paired", "page_assisted", "inserted", "skipped_sensitive", "draft_ready", "blocked"}
    assert by_kind["inserted"]["message"] == "Filled 3 fields." and by_kind["skipped_sensitive"]["message"] == "Left 2 sensitive questions for you."
    assert by_kind["draft_ready"]["message"] == "1 draft ready to review."
    assert by_kind["blocked"]["message"] == "1 answer could not be prepared."
    assert by_kind["page_assisted"]["message"] == "Helped with Backend Engineer at Acme."
    assert by_kind["page_assisted"]["host"] == "boards.greenhouse.io" and by_kind["page_assisted"]["company"] == "Acme"
    for item in feed["items"]:
        assert set(item) == {"id", "created_at", "message", "origin", "kind", "browser", "host", "company", "title"}
        assert item["origin"] == "assistant" and item["browser"] == "chrome"
    stamps = [item["created_at"] for item in feed["items"]]
    assert stamps == sorted(stamps, reverse=True)
    page = workspace.client.get("/api/extension/activity?limit=2&offset=1").json()
    assert page["total"] == feed["total"] and [i["id"] for i in page["items"]] == [i["id"] for i in feed["items"][1:3]]
    assert workspace.client.get("/api/extension/activity?limit=0").status_code == 422
    assert workspace.client.get("/api/extension/activity?limit=101").status_code == 422


def test_activity_text_from_pages_is_cleaned(workspace):
    client_id = connect(workspace)
    add_session(workspace, client_id, company="Evil\x00\n Corp" + "x" * 300, title="")
    item = next(i for i in workspace.client.get("/api/extension/activity").json()["items"] if i["kind"] == "page_assisted")
    assert "\x00" not in item["company"] and "\n" not in item["company"] and len(item["company"]) <= 120
    assert item["title"] is None and item["message"].startswith("Helped with Evil")


# ------------------------------------------------------------------------------------------------ onboarding and tracker


def steps(workspace):
    return {step["id"]: step for step in workspace.client.get("/api/onboarding").json()["steps"]}


def test_onboarding_gets_a_ninth_evidence_step(workspace):
    body = workspace.client.get("/api/onboarding").json()
    assert body["total"] == 9 and [s["id"] for s in body["steps"]][-1] == "extension"
    assert steps(workspace)["extension"]["status"] == "pending"
    workspace.manager.note_contact("chrome", CHROME, "5.0.0")
    assert steps(workspace)["extension"]["status"] == "in_progress"
    workspace.manager._contacts.clear()
    pending = workspace.manager.start(CHROME, "Meridian", "5.0.0")
    assert steps(workspace)["extension"]["status"] == "in_progress" and "waiting" in steps(workspace)["extension"]["detail"]
    workspace.manager.approve(pending["pairing_id"])
    done = steps(workspace)["extension"]
    assert done["status"] == "complete" and done["evidence"]["clients"] == 1
    assert workspace.client.get("/api/onboarding").json()["completed"] == 1
    client_id = workspace.db.one("SELECT id FROM extension_clients")["id"]
    workspace.db.execute("UPDATE extension_clients SET version='1.0.0' WHERE id=?", (client_id,))
    assert steps(workspace)["extension"]["status"] == "needs_attention"
    workspace.db.execute("UPDATE extension_clients SET version='5.0.0' WHERE id=?", (client_id,))
    assert steps(workspace)["extension"]["status"] == "complete"
    workspace.manager.revoke(client_id)
    assert steps(workspace)["extension"]["status"] == "in_progress"  # the extension was seen a moment ago
    workspace.manager._contacts.clear()
    assert steps(workspace)["extension"]["status"] == "pending"
    workspace.app.state.bridge = FakeBridge(running=False, error="no free bridge port")
    assert steps(workspace)["extension"]["status"] == "needs_attention"
    assert "browser connection" in steps(workspace)["extension"]["detail"]


def test_tracker_rows_say_where_the_assistant_was_used(workspace):
    used, untouched, researched = (add_job(workspace, url=f"https://boards.greenhouse.io/acme/jobs/{n}", title=t)
                                   for n, t in ((1, "Used"), (2, "Untouched"), (3, "Researched")))
    chrome, safari = connect(workspace), connect(workspace, origin=SAFARI)
    first = add_session(workspace, chrome, job_id=used["id"], sid="a")
    second = add_session(workspace, safari, job_id=used["id"], sid="b")
    workspace.db.execute("UPDATE extension_sessions SET updated_at='2026-10-14T09:00:00+00:00' WHERE id=?", (second,))
    add_events(workspace, first, "accept", 4, stamp="2026-10-14T10:00:00+00:00")
    add_events(workspace, second, "feedback", 2, outcome="accepted", stamp="2026-10-14T08:00:00+00:00")
    add_events(workspace, second, "feedback", 3, outcome="edited", stamp="2026-10-14T08:00:00+00:00")
    add_session(workspace, chrome, job_id=researched["id"], sid="c")
    items = {row["title"]: row for row in workspace.client.get("/api/tracker").json()["items"]}
    # the browser of the most recently updated session; kept suggestions (accept + accepted feedback) are counted
    assert items["Used"]["assistant"] == {"browser": "safari", "fields_inserted": 6, "last_used_at": "2026-10-14T10:00:00+00:00"}
    assert items["Untouched"]["assistant"] is None
    assert items["Researched"]["assistant"] == {"browser": "chrome", "fields_inserted": 0, "last_used_at": "2026-10-13T10:05:00+00:00"}
    assert workspace.client.get("/api/daily").status_code == 200
    # deleting the paired browser keeps the history (sessions survive revocation)
    workspace.manager.revoke(chrome)
    again = {row["title"]: row for row in workspace.client.get("/api/tracker").json()["items"]}
    assert again["Researched"]["assistant"]["browser"] == "chrome"


def test_tracker_assistant_picks_the_most_recent_browser(workspace):
    job = add_job(workspace)
    chrome, safari = connect(workspace), connect(workspace, origin=SAFARI)
    add_session(workspace, chrome, job_id=job["id"], sid="a")
    newer = add_session(workspace, safari, job_id=job["id"], sid="b")
    workspace.db.execute("UPDATE extension_sessions SET updated_at='2026-10-20T00:00:00+00:00' WHERE id=?", (newer,))
    assert extension_api.tracker_assistant(workspace.db)[job["id"]]["browser"] == "safari"


def test_old_audit_events_are_pruned_and_recent_ones_kept(workspace):
    client_id = connect(workspace)
    sid = add_session(workspace, client_id)
    workspace.db.execute("UPDATE extension_sessions SET updated_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), sid))
    add_events(workspace, sid, "accept", 2, stamp="2020-01-01T00:00:00+00:00")
    add_events(workspace, sid, "accept", 3, stamp=datetime.now(timezone.utc).isoformat())
    assert extension_api.prune_audit(workspace.db) == 2
    assert workspace.db.one("SELECT COUNT(*) AS n FROM extension_events")["n"] == 3
    assert workspace.db.one("SELECT COUNT(*) AS n FROM extension_sessions")["n"] == 1  # the page history stays


def test_pages_only_ever_browsed_are_forgotten_after_a_year_and_pages_that_matter_stay(workspace):
    """One row per page ever opened would make the table grow for ever: a page tied to a job or an application, or used
    this year, stays; a page that was only browsed and left a year ago goes with its audit lines."""
    from datetime import timedelta
    chrome = connect(workspace)
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for sid, job_id, stamp in (("browsed-old", None, old), ("browsed-recent", None, recent), ("job-old", job["id"], old)):
        add_session(workspace, chrome, job_id=job_id, sid=sid)
        workspace.db.execute("UPDATE extension_sessions SET updated_at=? WHERE id=?", (stamp, sid))
        add_events(workspace, sid, "suggest", 2, stamp=recent)
    add_session(workspace, chrome, sid="application-old")
    link_session(workspace, "application-old", application)
    workspace.db.execute("UPDATE extension_sessions SET updated_at=? WHERE id='application-old'", (old,))
    extension_api.prune_audit(workspace.db)
    kept = {r["id"] for r in workspace.db.query("SELECT id FROM extension_sessions")}
    assert kept == {"browsed-recent", "job-old", "application-old"}
    assert {r["session_id"] for r in workspace.db.query("SELECT session_id FROM extension_events")} == {"browsed-recent", "job-old"}
    assert extension_api.prune_audit(workspace.db, session_days=1) == 0  # events newer than 90 days stay: only pages go
    assert {r["id"] for r in workspace.db.query("SELECT id FROM extension_sessions")} == {"job-old", "application-old"}


# ------------------------------------------------------------------------------------------------ application detail


def add_application(workspace, job_id, status="PREPARING"):
    identifier = "app-" + job_id
    workspace.db.execute(
        "INSERT INTO applications(id,job_id,mode,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (identifier, job_id, "manual", status, "", "2026-10-13T09:00:00+00:00", "2026-10-13T09:00:00+00:00"))
    return identifier


def add_answer(workspace, application_id, answer_id, question, answer, stamp, superseded=False):
    workspace.db.execute(
        "INSERT INTO application_answers(id,application_id,question,answer,fact_ids,verified,created_at) VALUES(?,?,?,?,?,?,?)",
        (answer_id, application_id, question, answer, "[]", 1, stamp))
    if superseded:
        workspace.db.execute("INSERT INTO superseded_application_answers(answer_id,superseded_at) VALUES(?,?)",
                             (answer_id, stamp))


def add_saved_edit(workspace, session_id, stamp, saved=True, kind="edit", details=None, tag="e"):
    workspace.db.execute(
        "INSERT INTO extension_events(id,session_id,kind,field_hash,outcome,details,created_at) VALUES(?,?,?,?,?,?,?)",
        (f"{tag}-{session_id}-{stamp}", session_id, kind, "abc", "edited",
         details if details is not None else json.dumps({"state": "NEEDS_YOU", "saved": saved, "scope": "application"}), stamp))


def detail(workspace, application_id):
    response = workspace.client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200, response.text
    return response.json()


def link_session(workspace, session_id, application_id):
    workspace.db.execute("UPDATE extension_sessions SET application_id=? WHERE id=?", (application_id, session_id))


def test_application_detail_carries_the_same_assistant_summary_as_the_tracker_row(workspace):
    used, untouched = add_job(workspace, title="Used"), add_job(workspace, url="https://x.test/2", title="Untouched")
    first, second = add_application(workspace, used["id"]), add_application(workspace, untouched["id"])
    chrome, safari = connect(workspace), connect(workspace, origin=SAFARI)
    one = add_session(workspace, chrome, job_id=used["id"], sid="a")
    other = add_session(workspace, safari, job_id=used["id"], sid="b")
    workspace.db.execute("UPDATE extension_sessions SET updated_at='2026-10-14T09:00:00+00:00' WHERE id=?", (other,))
    add_events(workspace, one, "accept", 4, stamp="2026-10-14T10:00:00+00:00")
    add_events(workspace, other, "feedback", 2, outcome="accepted", stamp="2026-10-14T08:00:00+00:00")
    expected = {"browser": "safari", "fields_inserted": 6, "last_used_at": "2026-10-14T10:00:00+00:00"}
    assert detail(workspace, first)["assistant"] == expected
    row = next(r for r in workspace.client.get("/api/tracker").json()["items"] if r["title"] == "Used")
    assert row["assistant"] == expected  # one computation, two screens
    assert extension_api.tracker_assistant(workspace.db, used["id"]) == {used["id"]: expected}
    body = detail(workspace, second)
    assert "assistant" in body and body["assistant"] is None  # the key is always there, null when never assisted
    assert extension_api.tracker_assistant(workspace.db, untouched["id"]) == {}
    # PATCH returns the same detail
    patched = workspace.client.patch(f"/api/applications/{first}", json={"notes": "n"}).json()
    assert patched["assistant"] == expected


def test_application_detail_without_any_extension_activity_is_unchanged(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    add_answer(workspace, application, "a1", "Why us?", "Because", "2026-10-13T10:00:00+00:00")
    body = detail(workspace, application)
    assert body["assistant"] is None
    assert [(a["id"], a["origin"]) for a in body["answers"]] == [("a1", None)]
    assert body["answer_history"] == []


def test_answers_saved_through_the_assistant_say_so(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    chrome = connect(workspace)
    sid = add_session(workspace, chrome, job_id=job["id"])
    link_session(workspace, sid, application)
    # a typed answer in Meridian, twenty seconds before the assistant saves one
    add_answer(workspace, application, "ui", "Notice period", "One month", "2026-10-13T10:00:00+00:00")
    add_answer(workspace, application, "ext", "Favourite colour", "Green", "2026-10-13T10:00:20.123456+00:00")
    add_saved_edit(workspace, sid, "2026-10-13T10:00:20.150000+00:00")
    body = detail(workspace, application)
    assert {a["id"]: a["origin"] for a in body["answers"]} == {"ui": None, "ext": "extension"}
    assert all("origin" in a for a in body["answers"])


def test_every_saved_line_claims_exactly_one_answer_even_when_two_pages_save_together(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    chrome = connect(workspace)
    first = add_session(workspace, chrome, job_id=job["id"], sid="p1")
    second = add_session(workspace, chrome, job_id=job["id"], sid="p2")
    for sid in (first, second):
        link_session(workspace, sid, application)
    add_answer(workspace, application, "typed", "Notice", "One month", "2026-10-13T10:00:00+00:00")
    add_answer(workspace, application, "a1", "Q1", "A", "2026-10-13T10:05:00.000100+00:00")
    add_answer(workspace, application, "a2", "Q2", "B", "2026-10-13T10:05:00.000200+00:00")
    # both pages stored their answer before either audit line was written
    add_saved_edit(workspace, first, "2026-10-13T10:05:00.000500+00:00")
    add_saved_edit(workspace, second, "2026-10-13T10:05:00.000600+00:00")
    assert extension_api.extension_answer_ids(workspace.db, application) == {"a1", "a2"}  # and not the typed one


@pytest.mark.parametrize("name,line", [
    ("not saved", dict(saved=False)),
    ("an accepted suggestion", dict(kind="accept")),
    ("a skipped suggestion", dict(kind="skip")),
    ("a note without details", dict(details="{}")),
    ("damaged details", dict(details="not json {")),
    ("saved as text", dict(details='{"saved": "yes"}')),
])
def test_only_a_saved_correction_labels_an_answer(workspace, name, line):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    sid = add_session(workspace, connect(workspace), job_id=job["id"])
    link_session(workspace, sid, application)
    add_answer(workspace, application, "typed", "Notice", "One month", "2026-10-13T10:00:00+00:00")
    add_saved_edit(workspace, sid, "2026-10-13T10:00:01+00:00", **line)
    assert extension_api.extension_answer_ids(workspace.db, application) == set(), name
    assert detail(workspace, application)["answers"][0]["origin"] is None  # and a damaged line never breaks the page


def test_the_audit_line_must_follow_the_answer_closely_and_belong_to_the_same_application(workspace):
    job, other_job = add_job(workspace), add_job(workspace, url="https://x.test/other", title="Other")
    application, other_application = add_application(workspace, job["id"]), add_application(workspace, other_job["id"])
    chrome = connect(workspace)
    mine = add_session(workspace, chrome, job_id=job["id"], sid="mine")
    theirs = add_session(workspace, chrome, job_id=other_job["id"], sid="theirs")
    unlinked = add_session(workspace, chrome, sid="unlinked")
    link_session(workspace, mine, application)
    link_session(workspace, theirs, other_application)
    add_answer(workspace, application, "old", "Q", "A", "2026-10-13T10:00:00+00:00")
    add_saved_edit(workspace, mine, "2026-10-13T10:10:00+00:00")  # ten minutes later: not this answer's line
    add_saved_edit(workspace, theirs, "2026-10-13T10:00:01+00:00")  # another application's page
    add_saved_edit(workspace, unlinked, "2026-10-13T10:00:01+00:00")  # a page without an application
    assert extension_api.extension_answer_ids(workspace.db, application) == set()
    assert extension_api.extension_answer_ids(workspace.db, other_application) == set()  # it has no answers at all
    add_answer(workspace, application, "before", "Q2", "B", "2026-10-13T10:09:30+00:00")
    assert extension_api.extension_answer_ids(workspace.db, application) == {"before"}
    # a line cannot claim an answer written after it
    add_answer(workspace, application, "after", "Q3", "C", "2026-10-13T10:10:30+00:00")
    assert extension_api.extension_answer_ids(workspace.db, application) == {"before"}


def named_line(workspace, session_id, stamp, answer_id, tag="n", **more):
    """The audit line session.feedback writes today: it names the answer it describes."""
    details = {"state": "NEEDS_YOU", "saved": True, "scope": "application", "answer_id": answer_id,
               "question_id": "q-" + answer_id, **more}
    add_saved_edit(workspace, session_id, stamp, details=json.dumps(details), tag=tag)


def test_a_line_that_names_its_answer_labels_exactly_that_answer(workspace):
    """The time-based claim is only the fallback of lines written before they carried the id: a non-assistant answer that
    lands between the assistant's save and its audit line must never take the label."""
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    sid = add_session(workspace, connect(workspace), job_id=job["id"])
    link_session(workspace, sid, application)
    add_answer(workspace, application, "mine", "Favourite colour", "Green", "2026-10-13T10:00:00.000100+00:00")
    add_answer(workspace, application, "typed", "Notice period", "One month", "2026-10-13T10:00:00.000300+00:00")
    named_line(workspace, sid, "2026-10-13T10:00:00.000400+00:00", "mine")
    assert extension_api.extension_answer_ids(workspace.db, application) == {"mine"}
    body = detail(workspace, application)
    assert {a["id"]: a["origin"] for a in body["answers"]} == {"mine": "extension", "typed": None}


def test_a_named_line_labels_nothing_it_does_not_own(workspace):
    job, other_job = add_job(workspace), add_job(workspace, url="https://x.test/other", title="Other")
    application, other_application = add_application(workspace, job["id"]), add_application(workspace, other_job["id"])
    chrome = connect(workspace)
    mine = add_session(workspace, chrome, job_id=job["id"], sid="mine")
    link_session(workspace, mine, application)
    add_answer(workspace, application, "typed", "Notice", "One month", "2026-10-13T10:00:00+00:00")
    add_answer(workspace, other_application, "theirs", "Q", "A", "2026-10-13T10:00:00+00:00")
    named_line(workspace, mine, "2026-10-13T10:00:01+00:00", "theirs", tag="x")  # another application's answer
    named_line(workspace, mine, "2026-10-13T10:00:02+00:00", "gone", tag="y")  # an answer that no longer exists
    assert extension_api.extension_answer_ids(workspace.db, application) == set()  # and no fallback to the typed one
    assert extension_api.extension_answer_ids(workspace.db, other_application) == set()


def test_old_unnamed_lines_never_take_an_answer_a_named_line_already_claimed(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    sid = add_session(workspace, connect(workspace), job_id=job["id"])
    link_session(workspace, sid, application)
    add_answer(workspace, application, "old", "Q1", "A", "2026-10-13T10:00:00+00:00")
    add_saved_edit(workspace, sid, "2026-10-13T10:00:01+00:00", tag="legacy")  # a line from before ids existed
    add_answer(workspace, application, "new", "Q2", "B", "2026-10-13T11:00:00+00:00")
    add_answer(workspace, application, "typed", "Q3", "C", "2026-10-13T11:00:00.500000+00:00")
    named_line(workspace, sid, "2026-10-13T11:00:01+00:00", "new")
    # the legacy line still labels the answer it always did; the named line labels its own, not the newest unclaimed one
    assert extension_api.extension_answer_ids(workspace.db, application) == {"old", "new"}


def test_memories_are_labelled_only_while_the_assistant_wrote_their_latest_version(workspace):
    sid = add_session(workspace, connect(workspace))
    named_line(workspace, sid, "2026-10-13T10:00:01+00:00", "a1", fact_id="f1", memory_at="2026-10-13T10:00:00+00:00")
    add_saved_edit(workspace, sid, "2026-10-13T10:00:02+00:00", tag="plain")  # an old line without the memory's stamp
    add_saved_edit(workspace, sid, "2026-10-13T10:00:03+00:00", saved=False, tag="no", details=json.dumps(
        {"saved": False, "fact_id": "f2", "memory_at": "t"}))
    assert extension_api.extension_memory_stamps(workspace.db) == {("f1", "2026-10-13T10:00:00+00:00")}


def test_superseded_answers_keep_their_label_in_the_history(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    sid = add_session(workspace, connect(workspace), job_id=job["id"])
    link_session(workspace, sid, application)
    add_answer(workspace, application, "v1", "Colour", "Green", "2026-10-13T10:00:00+00:00", superseded=True)
    add_saved_edit(workspace, sid, "2026-10-13T10:00:00.5+00:00")
    add_answer(workspace, application, "v2", "Colour", "Blue", "2026-10-13T10:30:00+00:00")
    add_saved_edit(workspace, sid, "2026-10-13T10:30:00.5+00:00")
    add_answer(workspace, application, "typed", "Notice", "One month", "2026-10-13T11:00:00+00:00")
    body = detail(workspace, application)
    assert {a["id"]: a["origin"] for a in body["answers"]} == {"v2": "extension", "typed": None}
    assert {a["id"]: a["origin"] for a in body["answer_history"]} == {"v1": "extension"}


def test_audit_pruning_keeps_the_record_that_labels_an_answer(workspace):
    job = add_job(workspace)
    application = add_application(workspace, job["id"])
    sid = add_session(workspace, connect(workspace), job_id=job["id"])
    link_session(workspace, sid, application)
    add_answer(workspace, application, "ext", "Colour", "Green", "2020-01-01T00:00:00+00:00")
    add_saved_edit(workspace, sid, "2020-01-01T00:00:01+00:00")  # years old, but it is the label's only trace
    add_saved_edit(workspace, sid, "2020-01-02T00:00:00+00:00", saved=False, tag="x")
    add_saved_edit(workspace, sid, "2020-01-03T00:00:00+00:00", kind="accept", tag="y")
    add_events(workspace, sid, "suggest", 3, stamp="2020-01-04T00:00:00+00:00")
    assert extension_api.prune_audit(workspace.db) == 5
    assert [r["kind"] for r in workspace.db.query("SELECT kind FROM extension_events")] == ["edit"]
    assert detail(workspace, application)["answers"][0]["origin"] == "extension"
    assert extension_api.prune_audit(workspace.db) == 0


def test_the_label_comes_from_the_real_feedback_path(tmp_path, monkeypatch):
    """The audit line is written by workstream B's session.feedback. This runs the real route end to end, so a change
    to the kind or the details of that line (which the label depends on) fails here and not in a user's tracker."""
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
    from harness import bridge, el, http, make_app, posting, suggest_body  # noqa: E402

    from jobagent.core import application_detail, save_answer  # noqa: E402

    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    app = make_app(tmp_path)
    request = SimpleNamespace(app=app)

    async def scenario():
        async with http(bridge(app)) as client:
            created = (await client.post("/v1/sessions", json=posting())).json()
            sid = created["session_id"]
            run = app.state.extension_sessions.sessions[sid].research_run_id
            await app.state.extension_research.wait(run, 10)

            async def save(question_label, value):
                body = suggest_body([el(1, "textbox", question_label)])
                suggestion = (await client.post(f"/v1/sessions/{sid}/suggest", json=body)).json()["results"][0]
                answered = await client.post(f"/v1/sessions/{sid}/feedback", json={
                    "question_id": suggestion["question_id"], "frame_key": suggestion["frame_key"], "outcome": "edited",
                    "request_id": suggestion["request_id"], "suggestion_state": suggestion["state"],
                    "final_value": value, "remember": "application"})
                assert answered.status_code == 200 and answered.json()["saved"] is True, answered.text
                return answered.json()["application_id"]

            application = await save("Favourite colour", "Green")
            save_answer(app.state.db, "Notice period", "One month", [], True, application)  # typed in Meridian
            await save("Favourite colour", "Blue")  # replaces the first one
            return application

    import asyncio

    async def run():
        try:
            application = await scenario()
            return application, await application_detail(application, request)
        finally:
            await app.state.extension_sessions.close()
            await app.state.extension_generation.close()
            await app.state.extension_research.close()

    application, body = asyncio.run(run())
    labels = {(a["question"], a["answer"]): a["origin"] for a in body["answers"]}
    assert labels == {("Notice period", "One month"): None, ("Favourite colour", "Blue"): "extension"}
    assert [(a["answer"], a["origin"]) for a in body["answer_history"]] == [("Green", "extension")]
    assert body["assistant"]["browser"] == "chrome" and body["assistant"]["fields_inserted"] == 0
    assert body["events"] and body["events"][0]["origin"] == "extension"  # what the header tag also reads


def foreign_bundle(tmp_path, monkeypatch, manifest=None):
    """The only folder this Meridian could find holds something that is NOT the pinned Meridian Assistant."""
    chrome = tmp_path / "foreign" / "chrome"
    chrome.mkdir(parents=True)
    (chrome / "manifest.json").write_text(json.dumps(manifest or {"manifest_version": 3, "name": "DECOY", "version": "6.6.6"}))
    (chrome / "background.js").write_text("// decoy")
    monkeypatch.setattr(assets, "source_candidates", lambda: [tmp_path / "foreign"])
    return tmp_path / "foreign"


def test_a_foreign_extension_folder_is_explained_never_installed_and_never_advertised(workspace, tmp_path, monkeypatch):
    foreign_bundle(tmp_path, monkeypatch)
    chrome = status(workspace)["browsers"]["chrome"]
    assert chrome["bundled"] is False and chrome["bundled_version"] == "0.0.0" and chrome["extension_path"] is None
    for action in ("prepare", "reveal", "copy_path", "open_browser_page", "install"):
        response = workspace.client.post("/api/extension/install/chrome", json={"action": action})
        assert response.status_code == 409, action
        assert "not the genuine Meridian Assistant" in response.json()["detail"]
        assert str(tmp_path) not in response.text and "6.6.6" not in response.text
    assert not (workspace.data / "extensions" / "chrome").exists() and os_actions(workspace) == []


def test_an_installed_folder_that_stopped_being_the_extension_is_offered_for_repair(workspace):
    assert workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"}).status_code == 200
    target = workspace.data / "extensions" / "chrome"
    assert status(workspace)["browsers"]["chrome"]["extension_path"] == str(target)
    (target / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "X", "version": "5.0.0"}))
    assert status(workspace)["browsers"]["chrome"]["extension_path"] is None  # not pointed at as "the extension"
    assert workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"}).status_code == 200
    assert json.loads((target / "manifest.json").read_text())["key"]  # repaired from the bundle
    assert status(workspace)["browsers"]["chrome"]["extension_path"] == str(target)


def test_prepare_explains_an_unwritable_extension_folder_instead_of_failing(workspace, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(assets, "install_chrome_extension", refuse)
    for action in ("prepare", "reveal", "copy_path", "install"):
        response = workspace.client.post("/api/extension/install/chrome", json={"action": action})
        assert response.status_code == 409 and "could not prepare the extension folder" in response.json()["detail"]
    assert os_actions(workspace) == []
    monkeypatch.setattr(assets, "install_chrome_extension", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad manifest")))
    damaged = workspace.client.post("/api/extension/install/chrome", json={"action": "prepare"})
    assert damaged.status_code == 409 and "damaged" in damaged.json()["detail"] and "bad manifest" not in damaged.text
