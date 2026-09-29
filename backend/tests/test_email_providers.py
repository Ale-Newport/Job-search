import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jobagent.db import Database
from jobagent.mail import MailService


class Vault:
    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = value


@pytest.mark.asyncio
async def test_oauth_pkce_state_single_use_and_vault_only(tmp_path, monkeypatch):
    db = Database(tmp_path / "mail.db")
    db.execute(
        "INSERT INTO integrations(id,provider,config,status) VALUES('g','gmail',?,'configured')",
        (json.dumps({"client_id": "desktop-client"}),),
    )
    vault = Vault()
    service = MailService(db, vault)
    url = service.connect("gmail", "http://127.0.0.1:12345/api/oauth/callback")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32
    assert "login_hint" not in query

    def response(request):
        if request.url.path == "/gmail/v1/users/me/profile":
            assert request.method == "GET"
            assert request.headers["authorization"] == "Bearer secret-access-token"
            return httpx.Response(200, json={"emailAddress": "verified@example.test"})
        assert request.url == "https://oauth2.googleapis.com/token"
        form = parse_qs(request.content.decode())
        assert "code_verifier" in form
        assert form["grant_type"] == ["authorization_code"]
        return httpx.Response(
            200,
            json={"access_token": "secret-access-token", "refresh_token": "secret-refresh-token", "expires_in": 3600},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "jobagent.mail.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs)
    )
    await service.callback(query["state"][0], "single-use-code")
    assert "secret-refresh-token" in vault.get("gmail:tokens")
    assert b"secret-refresh-token" not in db.path.read_bytes()
    row = db.one("SELECT config,status FROM integrations WHERE provider='gmail'")
    assert json.loads(row["config"])["account_email"] == "verified@example.test"
    assert row["status"] == "connected"
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.callback(query["state"][0], "single-use-code")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile_case", ["matching", "mismatched", "http_error", "missing_email", "malformed_json", "network_error"]
)
async def test_gmail_verifies_expected_account_before_persisting_authorization(tmp_path, monkeypatch, profile_case):
    db = Database(tmp_path / "mail.db")
    config = {"client_id": "desktop-client", "expected_email": "  Candidate@Gmail.com  ", "other_option": "retained"}
    db.execute(
        "INSERT INTO integrations(id,provider,config,status) VALUES('gmail','gmail',?,'configured')",
        (json.dumps(config),),
    )
    vault = Vault()
    service = MailService(db, vault)
    query = parse_qs(urlparse(service.connect("gmail", "http://127.0.0.1:12345/api/oauth/callback")).query)
    assert query["login_hint"] == ["Candidate@Gmail.com"]
    calls = []

    def respond(request):
        calls.append(request)
        if request.url == "https://oauth2.googleapis.com/token":
            return httpx.Response(
                200, json={"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600}
            )
        assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/profile"
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer private-access"
        assert vault.data == {}  # Neither token has been persisted before account verification.
        if profile_case == "http_error":
            return httpx.Response(403, json={"error": "profile unavailable"})
        if profile_case == "network_error":
            raise httpx.ConnectError("Unreachable", request=request)
        if profile_case == "missing_email":
            return httpx.Response(200, json={"messagesTotal": 0})
        if profile_case == "malformed_json":
            return httpx.Response(200, content=b"not JSON")
        return httpx.Response(
            200, json={"emailAddress": "candidate@gmail.com" if profile_case == "matching" else "wrong@gmail.com"}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "jobagent.mail.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    if profile_case == "matching":
        assert await service.callback(query["state"][0], "single-use-code") == "gmail"
        row = db.one("SELECT config,status FROM integrations WHERE provider='gmail'")
        assert row["status"] == "connected"
        assert json.loads(row["config"]) == config | {"account_email": "candidate@gmail.com"}
        assert json.loads(vault.get("gmail:tokens"))["refresh_token"] == "private-refresh"
    else:
        with pytest.raises(
            ValueError, match="does not match" if profile_case == "mismatched" else "verification failed"
        ) as error:
            await service.callback(query["state"][0], "single-use-code")
        assert "private-" not in str(error.value)
        assert vault.data == {}
        row = db.one("SELECT config,status FROM integrations WHERE provider='gmail'")
        assert row["status"] == "configured"
        assert json.loads(row["config"]) == config
    assert len(calls) == 2
    assert not db.query("SELECT id FROM email_messages")
    assert b"private-access" not in db.path.read_bytes()
    assert b"private-refresh" not in db.path.read_bytes()


@pytest.mark.asyncio
async def test_changed_expected_account_invalidates_pending_gmail_authorization(tmp_path, monkeypatch):
    db = Database(tmp_path / "mail.db")
    db.execute(
        "INSERT INTO integrations(id,provider,config,status) VALUES('gmail','gmail',?,'configured')",
        (json.dumps({"client_id": "desktop-client", "expected_email": "first@gmail.com"}),),
    )
    vault = Vault()
    service = MailService(db, vault)
    query = parse_qs(urlparse(service.connect("gmail", "http://127.0.0.1:12345/api/oauth/callback")).query)
    db.execute(
        "UPDATE integrations SET config=? WHERE provider='gmail'",
        (json.dumps({"client_id": "desktop-client", "expected_email": "second@gmail.com"}),),
    )

    def no_network(**kwargs):
        pytest.fail("Changed identity must be rejected before token exchange")

    monkeypatch.setattr("jobagent.mail.httpx.AsyncClient", no_network)
    with pytest.raises(ValueError, match="configuration changed"):
        await service.callback(query["state"][0], "code")
    assert vault.data == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gmail", "outlook"])
async def test_readonly_provider_import_and_dedup(tmp_path, monkeypatch, provider):
    db = Database(tmp_path / "mail.db")
    db.execute("INSERT INTO integrations(id,provider,config,status) VALUES(?,?,'{}','connected')", (provider, provider))
    vault = Vault()
    vault.set(provider + ":tokens", json.dumps({"access_token": "test-access", "expires_at": 9999999999}))
    raw = b"From: Recruiting <jobs@arc.example>\nSubject: Arc Systems application\nDate: Tue, 29 Sep 2026 12:00:00 +0000\nMessage-ID: <unique@arc.example>\n\nThank you for applying."
    calls = []

    def response(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer test-access"
        if provider == "outlook":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "unique",
                            "subject": "Arc Systems application",
                            "from": {"emailAddress": {"address": "jobs@arc.example"}},
                            "body": {"content": "Thank you for applying."},
                            "receivedDateTime": "2026-09-29T12:00:00+00:00",
                            "conversationId": "thread",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "unique"}]})
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode(), "threadId": "thread"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "jobagent.mail.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs)
    )
    service = MailService(db, vault)
    first, second = await service.sync(), await service.sync()
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert len(db.query("SELECT * FROM email_messages")) == 1
    assert db.one("SELECT classification FROM email_messages")["classification"] == "APPLICATION_CONFIRMATION"
