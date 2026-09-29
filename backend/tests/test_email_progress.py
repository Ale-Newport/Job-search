import asyncio
import json
import threading

import httpx
import pytest
from keyring.errors import KeyringError

from jobagent.main import create_app


@pytest.mark.asyncio
async def test_keychain_wait_keeps_api_responsive_and_reports_truthful_progress(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class WaitingVault:
        calls = 0

        def get(self, name):
            self.calls += 1
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test credential wait timed out")
            return json.dumps({"access_token": "private-test-token", "expires_at": 9999999999})

    vault = WaitingVault()
    app = create_app(tmp_path, token="test-token", secret_store=vault, start_scheduler=False)
    app.state.db.execute(
        "INSERT INTO integrations(id,provider,config,status) VALUES('g','gmail',?,'connected')",
        (json.dumps({"client_id": "desktop", "account_email": "candidate@example.test"}),),
    )

    async def fetch(provider):
        await app.state.mail.token(provider)
        return []

    monkeypatch.setattr(app.state.mail, "fetch", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        task = asyncio.create_task(client.post("/api/email/sync"))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            assert not task.done(), "Keychain waiting must not block the API event loop"
            status = await asyncio.wait_for(client.get("/api/email/status"), 0.5)
            assert status.json() == {
                "syncing": True,
                "phase": "waiting_for_keychain",
                "provider": "gmail",
                "messages_read": 0,
            }
            assert "private" not in status.text
            assert (await asyncio.wait_for(client.get("/api/health"), 0.5)).status_code == 200
            assert (await client.get("/api/email/status", headers={"Authorization": ""})).status_code == 401
            account = (await client.get("/api/integrations")).json()["items"][0]
            assert account["status"] == "connected" and account["last_sync"] is None
            setup = (await client.get("/api/onboarding")).json()
            email_step = next(item for item in setup["steps"] if item["id"] == "email")
            assert email_step["status"] == "in_progress"
            assert "account connected" in email_step["detail"]
            duplicate = await client.post("/api/email/sync")
            assert duplicate.json()["status"] == "already_running"
            assert vault.calls == 1
        finally:
            release.set()
            result = await asyncio.wait_for(task, 2)
        assert result.status_code == 200 and result.json()["errors"] == []
        assert (await client.get("/api/email/status")).json()["phase"] == "idle"
        account = (await client.get("/api/integrations")).json()["items"][0]
        assert account["last_sync"] and account["last_error"] is None
        setup = (await client.get("/api/onboarding")).json()
        assert next(item for item in setup["steps"] if item["id"] == "email")["status"] == "complete"


@pytest.mark.asyncio
async def test_keychain_denial_preserves_connection_and_gives_recovery_action(tmp_path):
    class DeniedVault:
        def get(self, name):
            raise KeyringError("private system error")

    app = create_app(tmp_path, token="test-token", secret_store=DeniedVault(), start_scheduler=False)
    app.state.db.execute("INSERT INTO integrations(id,provider,config,status) VALUES('g','gmail','{}','connected')")
    result = await app.state.mail.sync()
    assert result["imported"] == 0
    assert "Allow Meridian" in result["errors"][0]["error"]
    assert "private" not in str(result)
    account = app.state.db.one("SELECT status,last_sync,last_error FROM integrations WHERE provider='gmail'")
    assert account["status"] == "connected" and account["last_sync"] is None
    assert "Allow Meridian" in account["last_error"]
    assert app.state.mail.status()["phase"] == "idle"
