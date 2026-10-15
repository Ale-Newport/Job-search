"""Pairing state machine, bridge tokens, hello proof, redaction, native approval and backup purge."""

import asyncio
import hashlib
import hmac
import logging
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

from jobagent.backup import export_backup, restore_backup
from jobagent.extension import approval, auth, pairing
from jobagent.extension.auth import BridgeError
from jobagent.extension.pairing import PairingManager, hello_proof, manager_for
from jobagent.main import create_app
from jobagent.security import RedactingFilter

CHROME = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc"
OTHER = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
SAFARI = "safari-web-extension://8F2D4C1B-9A3E-4E57-B1D0-6C7A5F3E2D10"


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    return create_app(tmp_path, worker_isolation=False, token="pairing-test", secret_store=MemorySecrets(), start_scheduler=False)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def manager(app, clock):
    manager = PairingManager(app, clock=clock)
    app.state.extension_pairing = manager
    return manager


async def eventually(check, timeout=60.0):
    """Wait (without fixed sleeps, the machine may be busy) until ``check()`` is true."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end and not check():
        await asyncio.sleep(0.01)
    assert check(), "condition not reached in time"


def state_of(app, pairing_id):
    return app.state.db.one("SELECT state FROM extension_pairings WHERE id=?", (pairing_id,))["state"]


def start(manager, origin=CHROME, **kwargs):
    return manager.start(origin, kwargs.get("name", "Meridian"), kwargs.get("version", "0.1.0"))


def rows(app, table):
    return app.state.db.query(f"SELECT * FROM {table}")


# ------------------------------------------------------------------------------------------------ tokens


def test_token_format_hashing_and_verification():
    client_id = "5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11"
    token, stored = auth.new_token(client_id)
    prefix, found, secret = token.split(".")
    assert prefix == "mrd1" and found == client_id and len(secret) == 43
    assert stored == hashlib.sha256(secret.encode()).hexdigest() and secret not in stored
    assert auth.split_token("Bearer " + token) == (client_id, secret)
    row = {"token_hash": stored, "revoked_at": None}
    assert auth.verify(row, secret) is True
    assert auth.verify({**row, "revoked_at": "2026-01-01T00:00:00+00:00"}, secret) is False
    assert auth.verify(row, secret[:-1] + ("A" if secret[-1] != "A" else "B")) is False
    assert auth.verify(None, secret) is False
    # two tokens never share a secret
    assert auth.new_token(client_id)[0] != token


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer nonsense", "Basic mrd1.5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11." + "A" * 43,
     "Bearer mrd1.not-a-uuid." + "A" * 43, "Bearer mrd1.5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11.short",
     "bearer mrd1.5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11." + "A" * 43],
)
def test_malformed_authorization_headers_are_rejected(header):
    assert auth.split_token(header) == (None, None)
    assert auth.split_token(None) == (None, None)


def test_tokens_are_redacted_from_logs():
    token, _ = auth.new_token("5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11")
    for text in (f"paired with {token} ok", f"Authorization: Bearer {token}", f"header=Bearer {token}, next"):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, text, (), None)
        assert RedactingFilter().filter(record) is True
        assert token not in record.msg and token.split(".")[2] not in record.msg
    record = logging.LogRecord("t", logging.INFO, __file__, 1, f"token {token}", (), None)
    RedactingFilter().filter(record)
    assert "mrd1.[redacted]" in record.msg


# ------------------------------------------------------------------------------------------------ hello proof


def test_hello_proof_matches_the_webcrypto_vector():
    # Produced with Node's WebCrypto exactly as the extension computes it:
    #   key = SHA-256(secret) imported as HMAC-SHA256; msg = "meridian-bridge-v1|" + nonce + "|" + port
    secret = "Zm9yLXRlc3RzLW9ubHktc2VjcmV0LXZhbHVlLTEyMzQ"
    nonce, port = "AAECAwQFBgcICQoLDA0ODw", 47617
    token_hash = hashlib.sha256(secret.encode()).hexdigest()
    assert token_hash == "7bdaec0c3990919f1f51ebf8d2c63f2712d3659eec9883d98931b6aed9a1b18e"
    assert hello_proof(token_hash, nonce, port) == "5Cv3Kfcb7C1Owes5OXCxiBvmlUqXV9yZJsMo1XWeFQ4"
    # a different port, nonce or secret never produces the same proof
    assert hello_proof(token_hash, nonce, 47618) != hello_proof(token_hash, nonce, port)
    assert hello_proof(token_hash, "AAECAwQFBgcICQoLDA0ODx", port) != hello_proof(token_hash, nonce, port)
    other = hashlib.sha256(b"another-secret").hexdigest()
    assert hello_proof(other, nonce, port) != hello_proof(token_hash, nonce, port)


def test_proof_is_only_given_to_active_clients(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid, via="app")
    issued = manager.status(pid, CHROME)
    client_id, secret = issued["client_id"], issued["token"].split(".")[2]
    nonce = "AAECAwQFBgcICQoLDA0ODw"
    expected = pairing.b64url(hmac.new(hashlib.sha256(secret.encode()).digest(), b"meridian-bridge-v1|" + nonce.encode()
                                       + b"|47615", hashlib.sha256).digest())
    assert manager.proof(client_id, nonce, 47615) == expected
    assert manager.proof(client_id, "short", 47615) is None  # nonce must be 16-64 base64url characters
    assert manager.proof(client_id, "bad nonce with spaces!!", 47615) is None
    assert manager.proof("00000000-0000-0000-0000-000000000000", nonce, 47615) is None
    assert manager.proof(None, nonce, 47615) is None
    manager.revoke(client_id)
    assert manager.proof(client_id, nonce, 47615) is None  # revoked clients learn nothing


def test_revoked_proof_matches_the_webcrypto_vector_and_is_domain_separated_from_the_active_proof():
    # Node: HMAC-SHA256(SHA-256(secret), "meridian-bridge-v1-revoked|" + nonce + "|" + port), base64url without padding
    secret = "Zm9yLXRlc3RzLW9ubHktc2VjcmV0LXZhbHVlLTEyMzQ"
    nonce, port = "AAECAwQFBgcICQoLDA0ODw", 47617
    token_hash = hashlib.sha256(secret.encode()).hexdigest()
    assert pairing.revoked_proof(token_hash, nonce, port) == "rhVrMoEoYtSsHvcuSDziRs3beiovTbGwQvMj3sYNfs4"
    # a revoked proof can never be mistaken for the proof of an active client, and vice versa
    assert pairing.revoked_proof(token_hash, nonce, port) != hello_proof(token_hash, nonce, port)
    assert pairing.revoked_proof(token_hash, nonce, 47618) != pairing.revoked_proof(token_hash, nonce, port)
    assert pairing.revoked_proof(hashlib.sha256(b"another").hexdigest(), nonce, port) != pairing.revoked_proof(token_hash, nonce, port)


def test_a_revoked_client_is_told_so_with_a_proof_and_never_by_silence(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid, via="app")
    issued = manager.status(pid, CHROME)
    client_id, secret = issued["client_id"], issued["token"].split(".")[2]
    nonce = "AAECAwQFBgcICQoLDA0ODw"
    key = hashlib.sha256(secret.encode()).digest()

    def mac(prefix):
        return pairing.b64url(hmac.new(key, prefix + nonce.encode() + b"|47615", hashlib.sha256).digest())

    assert manager.proofs(client_id, nonce, 47615) == (mac(b"meridian-bridge-v1|"), None)
    manager.revoke(client_id)
    assert manager.proofs(client_id, nonce, 47615) == (None, mac(b"meridian-bridge-v1-revoked|"))
    assert manager.proof(client_id, nonce, 47615) is None  # the active proof is still never given to a revoked client
    # unknown clients and malformed asks learn nothing at all: silence means "I do not know you", never "revoked"
    assert manager.proofs("00000000-0000-0000-0000-000000000000", nonce, 47615) == (None, None)
    assert manager.proofs(client_id, "short", 47615) == (None, None)
    assert manager.proofs(None, nonce, 47615) == (None, None)


# ------------------------------------------------------------------------------------------------ state machine


def test_start_creates_a_pending_pairing_with_a_six_digit_code(app, manager):
    result = start(manager, name="Meridian Assistant")
    assert set(result) == {"pairing_id", "code", "expires_in", "poll_after_ms"}
    assert result["expires_in"] == 120 and len(result["code"]) == 6 and result["code"].isdigit()
    row = rows(app, "extension_pairings")[0]
    assert (row["state"], row["origin"], row["browser"], row["attempts"], row["client_name"]) == (
        "pending", CHROME, "chrome", 0, "Meridian Assistant")
    assert row["code"] == result["code"] and row["expires_at"] > row["created_at"]
    assert start(manager, origin=SAFARI)["pairing_id"] != result["pairing_id"]
    assert [r["browser"] for r in rows(app, "extension_pairings")] == ["chrome", "safari"]


@pytest.mark.parametrize("origin", [None, "", "https://evil.example", "null", "chrome-extension://short",
                                    "moz-extension://abcdefghijklmnop", "chrome-extension://abcdefghijklmnop/x"])
def test_start_requires_an_extension_origin(manager, origin):
    with pytest.raises(BridgeError) as error:
        manager.start(origin, "x", "1")
    assert (error.value.status_code, error.value.code) == (403, "origin")


def test_name_is_sanitised_and_browser_comes_from_the_origin(app, manager):
    start(manager, name="  Evil\x00\n\tName\u202e" + "x" * 200, version="9" * 80)
    row = rows(app, "extension_pairings")[0]
    assert "\x00" not in row["client_name"] and "\n" not in row["client_name"] and len(row["client_name"]) <= 60
    assert len(row["version"]) <= 32
    assert row["browser"] == "chrome"
    start(manager, origin=SAFARI, name="Chrome")
    assert rows(app, "extension_pairings")[1]["browser"] == "safari"


def test_one_pending_pairing_per_origin_supersedes_the_older_one(app, manager):
    first = start(manager)
    second = start(manager)
    states = {r["id"]: r["state"] for r in rows(app, "extension_pairings")}
    assert states[first["pairing_id"]] == "expired" and states[second["pairing_id"]] == "pending"
    assert sum(r["state"] == "pending" for r in rows(app, "extension_pairings")) == 1
    # the database itself refuses a second pending pairing for the same origin
    with pytest.raises(sqlite3.IntegrityError):
        app.state.db.execute(
            "INSERT INTO extension_pairings(id,code,client_name,browser,origin,version,state,created_at,expires_at) "
            "VALUES('x','111111','n','chrome',?,'', 'pending','a','b')", (CHROME,))
    with pytest.raises(BridgeError) as error:
        manager.approve(first["pairing_id"])
    assert error.value.status_code == 410


def test_start_rate_limit_is_five_per_minute_per_origin(manager, clock):
    for _ in range(5):
        start(manager)
    with pytest.raises(BridgeError) as error:
        start(manager)
    assert (error.value.status_code, error.value.code) == (429, "rate_limited")
    start(manager, origin=OTHER)  # another origin has its own budget
    clock.advance(61)
    start(manager)


def test_spelling_variants_of_an_origin_share_one_rate_limit_budget(manager, clock):
    for _ in range(pairing.MAX_STARTS_PER_MINUTE):
        start(manager)
    identifier = CHROME.split("//")[1]
    for variant in ("chrome-extension://" + identifier.upper(), CHROME.replace("ophng", "OPHNG"),
                    "chrome-extension://" + identifier.title()):
        with pytest.raises(BridgeError) as error:
            start(manager, origin=variant)
        assert (error.value.status_code, error.value.code) == (429, "rate_limited"), variant
    assert list(manager._starts) == [CHROME]
    clock.advance(61)
    start(manager, origin="chrome-extension://" + identifier.upper())  # the budget returned for every spelling at once


def test_starts_are_capped_overall_and_the_tracking_table_stays_small(manager, clock):
    """A local process can invent any number of well-formed origins. Without an overall cap, the in-memory table grew
    with every one (and every start pops a native dialog); with it, the table never exceeds the cap."""
    for index in range(pairing.MAX_STARTS_PER_MINUTE_TOTAL):
        origin = f"chrome-extension://abcdefghijklmnopabcdefghijkl{index:04d}"
        manager.deny(start(manager, origin=origin)["pairing_id"])  # denied, so the pending cap never applies
    assert len(manager._starts) == pairing.MAX_STARTS_PER_MINUTE_TOTAL
    with pytest.raises(BridgeError) as error:
        start(manager, origin="chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert (error.value.status_code, error.value.code) == (429, "rate_limited")
    assert len(manager._starts) == pairing.MAX_STARTS_PER_MINUTE_TOTAL  # the refused origin was not tracked
    assert error.value.headers == {"Retry-After": "60"}
    clock.advance(61)
    start(manager, origin="chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert list(manager._starts) == ["chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"]  # everything else aged out


def test_the_rate_limit_ages_out_per_hit_not_per_origin(manager, clock):
    for _ in range(3):
        start(manager)
    clock.advance(40)
    for _ in range(2):
        start(manager)
    clock.advance(30)  # the first three hits are now 70 s old, the last two 30 s
    start(manager)
    start(manager)
    start(manager)
    with pytest.raises(BridgeError):
        start(manager)  # 2 old + 3 new = the five of the last minute


def test_a_pending_token_never_shows_up_in_a_repr(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid)
    (issued,) = manager._issued.values()
    secret = issued.token.split(".")[2]
    for text in (repr(issued), repr(manager._issued), str(manager._issued), f"{issued}"):
        assert issued.token not in text and secret not in text and "mrd1" not in text
    assert manager.status(pid, CHROME)["token"] == issued.token  # it is still delivered


def test_pending_pairings_are_capped_overall(manager):
    for index in range(pairing.MAX_PENDING):
        start(manager, origin=f"chrome-extension://abcdefghijklmnopabcdefghijkl{index:04d}")
    with pytest.raises(BridgeError) as error:
        start(manager, origin="chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert error.value.code == "rate_limited"


def test_pairing_expires_after_120_seconds(app, manager, clock):
    pid = start(manager)["pairing_id"]
    clock.advance(119)
    assert manager.status(pid, CHROME) == {"state": "pending"}
    clock.advance(2)
    assert manager.status(pid, CHROME) == {"state": "expired"}
    assert manager.pending() == []
    with pytest.raises(BridgeError) as error:
        manager.approve(pid)
    assert (error.value.status_code, error.value.code) == (410, "expired")


def test_approve_creates_a_client_and_delivers_the_token_exactly_once(app, manager):
    pid = start(manager)["pairing_id"]
    assert manager.status(pid, CHROME) == {"state": "pending"}
    result = manager.approve(pid, via="app")
    assert result["state"] == "approved" and result["browser"] == "chrome"
    claimed = manager.status(pid, CHROME)
    assert claimed["state"] == "approved" and claimed["token"].startswith("mrd1." + claimed["client_id"] + ".")
    # claimed once: the second poll gets no token and the pairing is 'claimed'
    assert manager.status(pid, CHROME) == {"state": "claimed"}
    assert manager.status(pid, None) == {"state": "claimed"}
    client = rows(app, "extension_clients")[0]
    assert client["id"] == claimed["client_id"] and client["origin"] == CHROME and client["revoked_at"] is None
    secret = claimed["token"].split(".")[2]
    assert client["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert rows(app, "extension_pairings")[0]["client_id"] == client["id"]
    assert rows(app, "extension_pairings")[0]["state"] == "claimed"
    # the plaintext secret is nowhere in the database
    dump = "\n".join(app.state.db._connect().iterdump())
    assert secret not in dump and claimed["token"] not in dump


def test_first_decision_wins(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid, via="dialog")
    for action in (manager.approve, manager.deny):
        with pytest.raises(BridgeError) as error:
            action(pid)
        assert (error.value.status_code, error.value.code) == (409, "conflict")
    other = start(manager, origin=OTHER)["pairing_id"]
    manager.deny(other, via="app")
    with pytest.raises(BridgeError) as error:
        manager.approve(other)
    assert error.value.status_code == 409
    assert manager.status(other, OTHER) == {"state": "denied"}
    assert len(rows(app, "extension_clients")) == 1


def test_concurrent_approvals_create_a_single_client(app, manager):
    pid = start(manager)["pairing_id"]
    outcomes = []

    def attempt():
        try:
            manager.approve(pid, via="app")
            outcomes.append("ok")
        except BridgeError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 1 and len(rows(app, "extension_clients")) == 1


def test_token_is_only_released_to_the_same_origin(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid)
    with pytest.raises(BridgeError) as error:
        manager.status(pid, OTHER)
    assert (error.value.status_code, error.value.code) == (403, "origin")
    with pytest.raises(BridgeError):
        manager.status(pid, "https://evil.example")
    assert manager.status(pid, CHROME)["token"]  # the rightful origin still gets it afterwards
    with pytest.raises(BridgeError) as error:
        manager.status("no-such-pairing-id-0000", CHROME)
    assert error.value.status_code == 404
    with pytest.raises(BridgeError):
        manager.status("../../etc/passwd", CHROME)


def test_unclaimed_token_expires_and_the_client_is_revoked(app, manager, clock):
    pid = start(manager)["pairing_id"]
    manager.approve(pid)
    clock.advance(pairing.TOKEN_TTL_SECONDS + 1)
    assert manager.status(pid, CHROME) == {"state": "expired"}
    assert rows(app, "extension_clients")[0]["revoked_at"] is not None
    assert rows(app, "extension_pairings")[0]["state"] == "expired"


def test_wrong_codes_deny_the_pairing_after_three_attempts(app, manager):
    pid = start(manager)["pairing_id"]
    assert manager.wrong_code(pid) == 1 and manager.wrong_code(pid) == 2
    assert manager.status(pid, CHROME) == {"state": "pending"}
    assert manager.wrong_code(pid) == 3
    assert manager.status(pid, CHROME) == {"state": "denied"}
    with pytest.raises(BridgeError):
        manager.approve(pid)
    assert rows(app, "extension_clients") == []


def test_cancel_by_the_extension(app, manager):
    pid = start(manager)["pairing_id"]
    with pytest.raises(BridgeError) as error:
        manager.cancel(pid, OTHER)
    assert error.value.code == "origin"
    with pytest.raises(BridgeError):
        manager.cancel(pid, None)  # cancelling is a POST: the Origin is mandatory
    assert manager.cancel(pid, CHROME) == {"state": "expired"}
    assert manager.cancel(pid, CHROME) == {"state": "expired"}  # idempotent
    # cancelling an approved-but-unclaimed pairing revokes the credential that was just created
    second = start(manager)["pairing_id"]
    manager.approve(second)
    assert manager.cancel(second, CHROME) == {"state": "expired"}
    assert rows(app, "extension_clients")[0]["revoked_at"] is not None
    assert manager.status(second, CHROME) == {"state": "expired"}


def test_pending_listing_includes_the_code_for_the_app_only_while_open(manager, clock):
    start(manager)
    start(manager, origin=OTHER)
    pending = manager.pending()
    assert [p["browser"] for p in pending] == ["chrome", "chrome"]
    assert pending[0]["recognized"] is True and pending[1]["recognized"] is False  # unpinned extension id
    assert all(len(p["code"]) == 6 for p in pending)
    clock.advance(130)
    assert manager.pending() == []


def test_recognised_origins(monkeypatch):
    assert pairing.is_recognised_origin(CHROME) is True
    assert pairing.is_recognised_origin(OTHER) is False
    assert pairing.is_recognised_origin(SAFARI) is True  # Safari ids vary per install: approval alone protects
    assert pairing.is_recognised_origin("https://evil.example") is False
    monkeypatch.setenv("MERIDIAN_EXTENSION_IDS", "abcdefghijklmnopabcdefghijklmnop, other")
    assert pairing.is_recognised_origin(OTHER) is True


def test_revoking_disconnects_and_keeps_history(app, manager):
    pid = start(manager)["pairing_id"]
    manager.approve(pid)
    client_id = manager.status(pid, CHROME)["client_id"]
    assert manager.revoke(client_id) is True
    assert manager.revoke(client_id) is False  # idempotent
    assert rows(app, "extension_clients")[0]["revoked_at"] is not None
    with pytest.raises(BridgeError) as error:
        manager.revoke("00000000-0000-0000-0000-000000000000")
    assert error.value.status_code == 404


def test_reconnecting_retires_only_idle_credentials_of_the_same_origin(app, manager, clock):
    def connect(origin=CHROME):
        pid = start(manager, origin=origin)["pairing_id"]
        manager.approve(pid)
        return manager.status(pid, origin)["client_id"]

    old = connect()
    clock.advance(3600)
    recent = connect()  # the first one was created an hour ago: not idle for a day, so it stays
    assert {r["id"]: r["revoked_at"] is None for r in rows(app, "extension_clients")} == {old: True, recent: True}
    clock.advance(pairing.STALE_CLIENT_SECONDS + 10)
    newest = connect()
    active = {r["id"] for r in rows(app, "extension_clients") if r["revoked_at"] is None}
    assert active == {newest}
    other_origin = connect(OTHER)
    assert {r["id"] for r in rows(app, "extension_clients") if r["revoked_at"] is None} == {newest, other_origin}


def test_restart_ends_every_unfinished_pairing(app, clock):
    manager = PairingManager(app, clock=clock)
    pending = start(manager)["pairing_id"]
    approved = start(manager, origin=OTHER)["pairing_id"]
    manager.approve(approved)
    restarted = PairingManager(app, clock=clock)  # the plaintext token lived only in the old process
    states = {r["id"]: r["state"] for r in rows(app, "extension_pairings")}
    assert states == {pending: "expired", approved: "expired"}
    assert all(r["revoked_at"] is not None for r in rows(app, "extension_clients"))
    assert restarted.pending() == []


def test_old_pairing_rows_are_pruned(app, clock):
    manager = PairingManager(app, clock=clock)
    start(manager)
    clock.advance(8 * 86400)
    PairingManager(app, clock=clock)
    assert rows(app, "extension_pairings") == []


def test_disabled_assistant_refuses_pairing(app, manager):
    app.state.db.execute("INSERT INTO settings VALUES('extension_enabled','false')")
    with pytest.raises(BridgeError) as error:
        start(manager)
    assert (error.value.status_code, error.value.code) == (403, "disabled")


def test_manager_is_shared_per_application(app):
    assert manager_for(app) is manager_for(app)


def test_hello_contacts_expire_after_fifteen_minutes(manager, clock):
    manager.note_contact("chrome", CHROME, "0.1.0")
    manager.note_contact("firefox", None)  # unknown browsers are ignored
    assert manager.contact("chrome")["version"] == "0.1.0" and manager.contact("safari") is None
    clock.advance(15 * 60 + 1)
    assert manager.contact("chrome") is None


# ------------------------------------------------------------------------------------------------ native approval


class FakeProcess:
    def __init__(self, answer, delay=0):
        self.answer, self.delay, self.killed = answer, delay, False

    async def communicate(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answer.encode(), b""

    def kill(self):
        self.killed = True


def scripted(*answers, delay=0):
    calls, processes = [], []
    queue = list(answers)

    async def spawn(script, title, message, seconds):
        calls.append((script, title, message, seconds))
        process = FakeProcess(queue.pop(0), delay)
        processes.append(process)
        return process

    return spawn, calls, processes


async def test_dialog_approves_only_the_correct_code():
    spawn, calls, _ = scripted("code:482913")
    assert await approval.ask_for_code("Meridian", "chrome", CHROME, "482913", spawn=spawn) == "approved"
    script, title, message, seconds = calls[0]
    assert title == "Meridian" and 60 < seconds <= 90
    assert CHROME in message and "482913" not in message  # the code is shown by the extension, never by the dialog
    assert script is approval.CODE_DIALOG


async def test_dialog_accepts_spaces_in_the_typed_code_and_retries_after_a_wrong_one():
    wrong = []
    spawn, calls, _ = scripted("code:111111", "code:482 913")
    result = await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn, on_wrong=lambda: wrong.append(1))
    assert result == "approved" and len(wrong) == 1 and len(calls) == 2


async def test_three_wrong_codes_deny():
    wrong = []
    spawn, calls, _ = scripted("code:111111", "code:222222", "code:333333", "code:482913")
    result = await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn, on_wrong=lambda: wrong.append(1))
    assert result == "denied" and len(wrong) == 3 and len(calls) == 3  # the fourth (correct) code is never requested


async def test_empty_answers_do_not_burn_attempts_and_a_stray_return_cannot_approve():
    wrong = []
    spawn, calls, _ = scripted("code:", "code:   ", "code:482913")
    result = await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn, on_wrong=lambda: wrong.append(1))
    assert result == "approved" and wrong == [] and len(calls) == 3
    spawn, _, _ = scripted("code:", "deny")
    assert await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn) == "denied"


@pytest.mark.parametrize("typed", ["\u0664\u0668\u0662\u0669\u0661\u0663", "\uff14\uff18\uff12\uff19\uff11\uff13", "\u00b2\u00b3"])
async def test_digits_of_other_scripts_cannot_crash_or_approve(typed):
    """hmac.compare_digest raises TypeError for a non-ASCII str, which used to end the whole dialog (and with it the
    wrong-attempt counter). Such an answer is ignored like an empty one; only the ASCII digits typed count."""
    wrong = []
    spawn, calls, _ = scripted(f"code:{typed}", "code:482913")
    result = await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn, on_wrong=lambda: wrong.append(1))
    assert result == "approved" and wrong == [] and len(calls) == 2
    spawn, calls, _ = scripted(f"code:{typed}1", f"code:{typed}2", "deny")
    assert await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn, on_wrong=lambda: wrong.append(1)) == "denied"
    assert len(wrong) == 2  # "1" and "2" are wrong codes, counted as such


@pytest.mark.parametrize("answer,expected", [("deny", "denied"), ("timeout", "timeout"), ("", "unavailable"),
                                             ("something else", "unavailable")])
async def test_dialog_outcomes(answer, expected):
    spawn, _, _ = scripted(answer)
    assert await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn) == expected


async def test_missing_osascript_is_unavailable():
    async def broken(*args):
        raise FileNotFoundError("osascript")

    assert await approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=broken) == "unavailable"


async def test_cancelling_kills_the_dialog_process():
    spawn, _, processes = scripted("code:482913", delay=5)
    task = asyncio.create_task(approval.ask_for_code("x", "chrome", CHROME, "482913", spawn=spawn))
    await eventually(lambda: processes)  # the dialog process exists
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].killed is True


def test_dialog_text_is_sanitised_and_marks_unrecognised_extensions():
    message = approval.build_message("Evil\nName\x00" + "x" * 200, "chrome", CHROME + "\n; do shell script", recognised=False)
    first, _, rest = message.partition("\n")
    assert first.startswith("Evil Name ") and len(first) < 130  # the name cannot start a new paragraph or run on forever
    assert "\x00" not in message and "do shell script" in message and "\n; do" not in message  # origin flattened too
    assert "UNRECOGNISED EXTENSION" in rest
    assert message.splitlines()[-1] == "Type the 6-digit code shown in the extension to allow it."
    assert "UNRECOGNISED" not in approval.build_message("Meridian", "chrome", CHROME, recognised=True)


def test_dialog_is_skipped_in_tests_headless_runs_and_when_disabled(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    assert approval.dialog_allowed(True) is False
    monkeypatch.delenv("MERIDIAN_TEST")
    monkeypatch.setenv("MERIDIAN_NO_NOTIFICATIONS", "1")
    assert approval.dialog_allowed(True) is False
    monkeypatch.delenv("MERIDIAN_NO_NOTIFICATIONS")
    assert approval.dialog_allowed(False) is False
    monkeypatch.setattr(sys, "platform", "linux")
    assert approval.dialog_allowed(True) is False
    monkeypatch.setattr(sys, "platform", "darwin")
    assert approval.dialog_allowed(True) is True


def test_dialog_script_passes_every_value_through_argv():
    assert "item 1 of argv" in approval.CODE_DIALOG and "item 2 of argv" in approval.CODE_DIALOG
    assert "%" not in approval.CODE_DIALOG and "{0}" not in approval.CODE_DIALOG
    assert 'buttons {"Deny", "Connect"}' in approval.CODE_DIALOG and "Approve" not in approval.CODE_DIALOG
    assert 'default answer ""' in approval.CODE_DIALOG  # the code is typed, never pre-filled


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"), reason="needs macOS osacompile")
def test_dialog_script_compiles(tmp_path):
    source = tmp_path / "dialog.applescript"
    source.write_text(approval.CODE_DIALOG)
    result = subprocess.run(["/usr/bin/osacompile", "-o", str(tmp_path / "dialog.scpt"), str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


async def test_announce_never_runs_under_tests(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("MERIDIAN_APP_BUNDLE", "/Applications/Meridian.app")
    started = []

    async def spawn(*args, **kwargs):
        started.append(args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await approval.announce(None, "chrome")
    await approval.bring_app_forward()
    assert started == []  # neither a notification nor `open` on the app bundle


async def test_dialog_decides_the_pairing_and_the_in_app_decision_cancels_it(app, manager, monkeypatch):
    """A scripted dialog stands in for osascript: first decision wins, the loser is cancelled."""
    monkeypatch.setattr(approval, "dialog_allowed", lambda enabled=True: True)

    async def announce(db, browser):
        return None

    monkeypatch.setattr(approval, "announce", announce)
    seen = {}

    async def dialog(name, browser, origin, code, **kwargs):
        seen["code"], seen["recognised"] = code, kwargs["recognised"]
        return "approved"

    monkeypatch.setattr(approval, "ask_for_code", dialog)
    first = start(manager)
    await eventually(lambda: state_of(app, first["pairing_id"]) == "approved")
    assert seen == {"code": first["code"], "recognised": True}
    assert manager.status(first["pairing_id"], CHROME)["state"] == "approved"

    blocked = asyncio.Event()
    waiting_now = asyncio.Event()
    cancelled = []

    async def waiting(*args, **kwargs):
        waiting_now.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(approval, "ask_for_code", waiting)
    second = start(manager, origin=OTHER)
    await asyncio.wait_for(waiting_now.wait(), 60)
    manager.approve(second["pairing_id"], via="app")  # the in-app button wins
    await eventually(lambda: cancelled)
    assert cancelled == [True]
    assert manager.status(second["pairing_id"], OTHER)["state"] == "approved"
    await manager.close()


async def test_dialog_deny_and_wrong_codes_reach_the_database(app, manager, monkeypatch):
    monkeypatch.setattr(approval, "dialog_allowed", lambda enabled=True: True)

    async def announce(db, browser):
        return None

    monkeypatch.setattr(approval, "announce", announce)

    async def dialog(name, browser, origin, code, *, on_wrong, **kwargs):
        on_wrong()
        on_wrong()
        on_wrong()
        return "denied"

    monkeypatch.setattr(approval, "ask_for_code", dialog)
    pid = start(manager)["pairing_id"]
    await eventually(lambda: state_of(app, pid) == "denied")
    assert manager.status(pid, CHROME) == {"state": "denied"}
    assert rows(app, "extension_pairings")[0]["attempts"] == 3


async def test_dialog_respects_the_setting(app, manager, monkeypatch):
    monkeypatch.setattr(approval, "dialog_allowed", lambda enabled=True: enabled)
    calls = []

    async def dialog(*args, **kwargs):
        calls.append(1)
        return "timeout"

    async def announce(db, browser):
        return None

    monkeypatch.setattr(approval, "ask_for_code", dialog)
    monkeypatch.setattr(approval, "announce", announce)
    app.state.db.execute("INSERT INTO settings VALUES('extension_pairing_dialog','false')")
    start(manager)
    await eventually(lambda: not manager._dialogs)  # the prompt task ran to its end without asking
    assert calls == []


# ------------------------------------------------------------------------------------------------ backups


def test_backup_never_carries_paired_browsers(app, manager, tmp_path):
    db = app.state.db
    pid = start(manager)["pairing_id"]
    manager.approve(pid)
    issued = manager.status(pid, CHROME)
    start(manager, origin=OTHER)
    client_id = issued["client_id"]
    session = "s1"
    db.execute("INSERT INTO extension_sessions(id,client_id,url,canonical_url,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (session, client_id, "https://x.test/a", "https://x.test/a", "t", "t"))
    db.execute("INSERT INTO extension_events(id,session_id,kind,created_at) VALUES('e1',?, 'suggest','t')", (session,))
    token_hash = rows(app, "extension_clients")[0]["token_hash"]
    root = app.state.data_dir
    backup = export_backup(db, root, "long-secure-test-password")
    # The archive is AES-GCM ciphertext: scanning it proves nothing. What a holder of the password gets is the BYTES of the
    # archived database, and the purged rows must not be in its free pages either (test_extension_backup.py).
    from test_extension_backup import archived_database
    archived = archived_database(backup, "long-secure-test-password")
    assert token_hash.encode() not in archived and b"https://x.test/a" not in archived
    # a freshly paired browser after the backup must not survive restoring it, and the backup holds none
    manager.revoke(client_id)
    db.execute("INSERT INTO extension_pairings(id,code,client_name,browser,origin,state,created_at,expires_at) "
               "VALUES('p2','123456','n','chrome','chrome-extension://zzzzzzzzzzzzzzzz','denied','t','t')")
    restore_backup(db, root, backup.read_bytes(), "long-secure-test-password")
    for table in ("extension_events", "extension_sessions", "extension_pairings", "extension_clients"):
        assert rows(app, table) == [], table


def test_a_restore_leaves_no_claimable_token_and_the_next_pairing_works(app, manager, tmp_path):
    """The restore replaces the database but not the process: an approved pairing whose token still waits in memory,
    and a credential that was already issued, must both be dead afterwards."""
    db, root = app.state.db, app.state.data_dir
    backup = export_backup(db, root, "long-secure-test-password")
    waiting = start(manager)["pairing_id"]
    manager.approve(waiting)  # token in memory, row 'approved' in the database, nobody polled yet
    client_id = rows(app, "extension_clients")[0]["id"]
    token = manager._issued[waiting].token
    restore_backup(db, root, backup.read_bytes(), "long-secure-test-password")
    assert rows(app, "extension_pairings") == [] and rows(app, "extension_clients") == []
    with pytest.raises(BridgeError) as error:
        manager.status(waiting, CHROME)  # the token is still in the dict, but the pairing no longer exists
    assert error.value.status_code == 404
    assert manager.pending() == [] and manager.proof(client_id, "A" * 22, 47615) is None
    from types import SimpleNamespace

    request = SimpleNamespace(headers={"authorization": "Bearer " + token}, state=SimpleNamespace(),
                              app=SimpleNamespace(state=SimpleNamespace(main=app)))
    with pytest.raises(BridgeError) as unauthorised:
        auth.authenticate(request)
    assert (unauthorised.value.status_code, unauthorised.value.code) == (401, "unauthorized")
    fresh = start(manager, origin=OTHER)["pairing_id"]  # and the user can simply connect again
    manager.approve(fresh)
    assert manager.status(fresh, OTHER)["state"] == "approved"
