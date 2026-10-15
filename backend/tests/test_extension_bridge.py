"""The bridge application (guard, hello, pairing routes, status, settings, error format) and the BridgeServer listener."""

import asyncio
import json
import os
import re
import socket
import sys
import types
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import chrome_manifest  # noqa: E402
from jobagent.extension import app as bridge_module  # noqa: E402
from jobagent.extension import auth, server  # noqa: E402
from jobagent.extension.app import create_bridge_app
from jobagent.extension.auth import BridgeError, Client, current_client
from jobagent.extension.pairing import hello_proof, manager_for, revoked_proof
from jobagent.extension.schemas import StatusResponse
from jobagent.extension.server import BridgeServer, bridge_enabled, bridge_ports
from jobagent.main import create_app

CHROME = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc"
OTHER = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
SAFARI = "safari-web-extension://8F2D4C1B-9A3E-4E57-B1D0-6C7A5F3E2D10"
CHROME_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
NONCE = "AAECAwQFBgcICQoLDA0ODw"
BASE = "http://127.0.0.1:47615"


class MemorySecrets:
    """A credential store that fails the test if anything on the bridge path touches the Keychain."""

    def __init__(self):
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        raise AssertionError("the bridge must never read the Keychain")

    def set(self, name, value):
        raise AssertionError("the bridge must never write the Keychain")

    def delete(self, name):
        raise AssertionError("the bridge must never write the Keychain")


class Slow(BaseModel):
    seconds: float


def fake_routes():
    """A stand-in for workstream B's router, exercising auth, error mapping, deadlines and body limits."""
    router = APIRouter()

    @router.get("/ping")
    async def ping(client: Client = Depends(current_client)):
        return {"client": client.id, "browser": client.browser, "origin": client.origin, "version": client.version}

    @router.post("/echo")
    async def echo(payload: dict, client: Client = Depends(current_client)):
        return {"keys": sorted(payload)}

    @router.post("/slow")
    async def slow(payload: Slow, client: Client = Depends(current_client)):
        await asyncio.sleep(payload.seconds)
        return {"ok": True}

    @router.get("/teapot")
    async def teapot(client: Client = Depends(current_client)):
        raise HTTPException(418, "I am a teapot")

    @router.get("/coded")
    async def coded(client: Client = Depends(current_client)):
        raise BridgeError(409, "Another draft is running", "conflict")

    @router.get("/value")
    async def value(client: Client = Depends(current_client)):
        raise ValueError("That question is not supported")

    @router.get("/boom")
    async def boom(client: Client = Depends(current_client)):
        raise RuntimeError("secret page text that must not leak")

    return types.SimpleNamespace(router=router)


@pytest.fixture(autouse=True)
def bundled_extension(tmp_path_factory, monkeypatch):
    """A tiny bundled build, so the tests do not depend on whatever extension/dist currently holds."""
    chrome = tmp_path_factory.mktemp("bundle") / "chrome"
    chrome.mkdir()
    (chrome / "manifest.json").write_text(json.dumps(chrome_manifest("0.1.0")))
    (chrome / "background.js").write_text("// background")
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(chrome.parent))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setitem(sys.modules, "jobagent.extension.routes", fake_routes())
    secrets = MemorySecrets()
    app = create_app(tmp_path, worker_isolation=False, token="bridge-test", secret_store=secrets, start_scheduler=False)
    bridge_app = create_bridge_app(app)
    with TestClient(app, headers={"Authorization": "Bearer bridge-test"}) as main, \
            TestClient(bridge_app, base_url=BASE) as bridge:
        yield types.SimpleNamespace(app=app, main=main, bridge=bridge, bridge_app=bridge_app, secrets=secrets,
                                    db=app.state.db)


def pair(workspace, origin=CHROME, name="Meridian Assistant", version="0.1.0"):
    manager = manager_for(workspace.app)
    started = manager.start(origin, name, version)
    manager.approve(started["pairing_id"], via="app")
    issued = manager.status(started["pairing_id"], origin)
    return issued["client_id"], issued["token"]


def authed(token, origin=CHROME, **extra):
    headers = {"Authorization": f"Bearer {token}", **extra}
    if origin:
        headers["Origin"] = origin
    return headers


# ------------------------------------------------------------------------------------------------ guard


def test_hello_and_status_advertise_the_version_of_the_genuine_bundle_never_a_foreign_folders(workspace, tmp_path, monkeypatch):
    from jobagent.extension import assets
    decoy = tmp_path / "decoy" / "chrome"
    decoy.mkdir(parents=True)
    (decoy / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "DECOY", "version": "6.6.6"}))
    monkeypatch.setattr(assets, "source_candidates", lambda: [tmp_path / "decoy"])
    hello = workspace.bridge.get("/v1/hello")
    assert hello.status_code == 200 and hello.json()["latest_extension_version"] == "0.0.0"
    client_id, token = pair(workspace)
    status = workspace.bridge.get("/v1/status", headers=authed(token))
    assert status.status_code == 200 and status.json()["latest_extension_version"] == "0.0.0"
    monkeypatch.undo()
    assert workspace.bridge.get("/v1/hello").json()["latest_extension_version"] == "0.1.0"  # the genuine bundle again


def test_host_must_be_loopback(workspace):
    ok = workspace.bridge.get("/v1/hello", headers={"Host": "127.0.0.1:47615"})
    assert ok.status_code == 200
    assert workspace.bridge.get("/v1/hello", headers={"Host": "localhost:47615"}).status_code == 200
    for host in ("evil.example", "evil.example:47615", "127.0.0.1.evil.example", "192.168.1.5:47615", ""):
        response = workspace.bridge.get("/v1/hello", headers={"Host": host})
        assert response.status_code == 400 and response.json()["code"] == "host", host


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:47615", "localhost", "localhost:47615", "LOCALHOST:47615", "LocalHost", "127.0.0.1:1",
])
def test_loopback_host_spellings_are_accepted(workspace, host):
    assert bridge_module.host_allowed(host) is True
    assert workspace.bridge.get("/v1/hello", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", [
    "localhost.", "localhost.:47615", "127.0.0.1.", "[::1]", "[::1]:47615", "[::ffff:127.0.0.1]:47615",
    "127.0.0.1:", "127.0.0.1:abc", "127.0.0.1:123456", "127.0.0.1:-1", "127.0.0.1:1:2",
    "localhost:47615, evil.example", "localhost:47615 evil.example", "evil.example, localhost", "localhost@evil.example",
    "evil.example#localhost", "evil.example?localhost", "0x7f.0.0.1", "2130706433", "127.1", "0.0.0.0", "localhost\x00",
    "xlocalhost", "localhostx", "localhost.evil.example", "evil.example", "",
])
def test_every_other_host_spelling_is_refused(workspace, host):
    """DNS rebinding defence: the Host value must be exactly loopback with an optional numeric port, never a lookalike,
    a list, a trailing dot, an IPv6 literal (the listener is IPv4 only) or a port that is not a number."""
    assert bridge_module.host_allowed(host) is False
    response = workspace.bridge.get("/v1/hello", headers={"Host": host})
    assert response.status_code == 400 and response.json()["code"] == "host"


def test_origin_policy(workspace):
    for origin in ("https://evil.example", "http://127.0.0.1:47615", "null", "moz-extension://abcdefghij",
                   "chrome-extension://short", "chrome-extension://" + "a" * 70, "file://"):
        response = workspace.bridge.get("/v1/hello", headers={"Origin": origin})
        assert response.status_code == 403 and response.json() == {"detail": "Origin is not permitted", "code": "origin"}, origin
    assert workspace.bridge.get("/v1/hello", headers={"Origin": CHROME}).status_code == 200
    assert workspace.bridge.get("/v1/hello", headers={"Origin": SAFARI}).status_code == 200
    assert workspace.bridge.get("/v1/hello").status_code == 200  # extension GETs carry no Origin
    # a mutating request must carry an extension Origin
    missing = workspace.bridge.post("/v1/pair/start", json={})
    assert missing.status_code == 403 and missing.json()["code"] == "origin"
    assert workspace.bridge.patch("/v1/settings", json={"language": "es"}).status_code == 403


def test_preflight_echoes_only_extension_origins(workspace):
    ask = {"Origin": CHROME, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    response = workspace.bridge.options("/v1/status", headers=ask | {"Access-Control-Request-Private-Network": "true"})
    assert response.status_code == 204 and response.content == b""
    assert response.headers["access-control-allow-origin"] == CHROME and response.headers["vary"] == "Origin"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["access-control-allow-private-network"] == "true"
    web = workspace.bridge.options("/v1/status", headers=ask | {"Origin": "https://evil.example"})
    assert web.status_code == 403 and "access-control-allow-origin" not in web.headers
    assert workspace.bridge.options("/v1/status").status_code == 403
    for origin in (CHROME, SAFARI):  # answers carry the CORS echo too (Safari without a host grant)
        assert workspace.bridge.get("/v1/hello", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
    plain = workspace.bridge.get("/v1/hello")
    assert "access-control-allow-origin" not in plain.headers
    assert plain.headers["cache-control"] == "no-store" and plain.headers["x-content-type-options"] == "nosniff"


def test_restoring_blocks_everything_and_bridge_requests_are_not_counted(workspace):
    _, token = pair(workspace)
    workspace.app.state.restoring = True
    try:
        for response in (workspace.bridge.get("/v1/hello"), workspace.bridge.get("/v1/status", headers=authed(token)),
                         workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": CHROME})):
            assert response.status_code == 503 and response.json()["code"] == "restoring"
        assert workspace.bridge.options("/v1/status", headers={"Origin": CHROME}).status_code == 204
    finally:
        workspace.app.state.restoring = False
    assert workspace.bridge.get("/v1/hello").status_code == 200
    for _ in range(3):
        workspace.bridge.get("/v1/status", headers=authed(token))
    assert workspace.app.state.active_requests == 0  # polling must never block a backup restore


def test_body_must_be_small_json(workspace):
    _, token = pair(workspace)
    headers = authed(token, **{"Content-Type": "application/json"})
    big = workspace.bridge.post("/v1/echo", content=b'{"a":"' + b"x" * (1024 * 1024) + b'"}', headers=headers)
    assert big.status_code == 413 and big.json()["code"] == "too_large"
    ok = workspace.bridge.post("/v1/echo", content=b'{"a":"' + b"x" * 1000 + b'"}', headers=headers)
    assert ok.status_code == 200 and ok.json() == {"keys": ["a"]}
    text = workspace.bridge.post("/v1/echo", content=b'{"a":1}', headers=authed(token, **{"Content-Type": "text/plain"}))
    assert text.status_code == 415 and text.json()["code"] == "invalid"
    form = workspace.bridge.post("/v1/echo", data={"a": "1"}, headers=authed(token))
    assert form.status_code == 415
    invalid = workspace.bridge.post("/v1/echo", content=b"{not json", headers=headers)
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid"
    bad_length = workspace.bridge.post("/v1/echo", content=b"{}", headers=headers | {"Content-Length": "abc"})
    assert bad_length.status_code in (400, 422)


@pytest.mark.parametrize("length", ["-5", "+5", "1_0", " ", "5.0", "0x5", b"\xd9\xa3", "5,5", "-0"])  # b"\xd9\xa3": an Arabic-Indic 3
def test_content_length_must_be_plain_ascii_digits(workspace, length):
    """int() accepts "-5", "+5", "1_0" and non-ASCII digits: a negative length used to make the guard believe the
    request had no body, which skipped the JSON-only check."""
    _, token = pair(workspace)
    headers = authed(token, **{"Content-Type": "text/plain", "Content-Length": length})
    response = workspace.bridge.post("/v1/echo", content=b"{}", headers=headers)
    assert response.status_code == 400 and response.json() == {"detail": "Invalid content length", "code": "invalid"}


def test_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(workspace):
    _, token = pair(workspace)
    headers = authed(token, **{"Content-Type": "application/json", "Content-Length": str(1024 * 1024 + 1)})
    response = workspace.bridge.post("/v1/echo", content=b"{}", headers=headers)
    assert response.status_code == 413 and response.json()["code"] == "too_large"


def test_an_origin_with_line_breaks_is_never_echoed(workspace):
    """The echoed CORS origin is only ever a string that matched the extension pattern in full."""
    guard = bridge_module.BridgeGuard
    from starlette.datastructures import Headers

    for origin in ("chrome-extension://abcdefgh\r\nSet-Cookie: x=1", "chrome-extension://abcdefgh\n",
                   "chrome-extension://abcdefgh ", "\nchrome-extension://abcdefgh", "chrome-extension://abcdefgh\x00"):
        assert guard._cors(origin, Headers({})) == {}, repr(origin)
    assert guard._cors(CHROME, Headers({}))["Access-Control-Allow-Origin"] == CHROME


def test_request_deadline_is_enforced(workspace, monkeypatch):
    _, token = pair(workspace)
    with monkeypatch.context() as patched:  # the short deadline applies to the slow request only, not to the next one
        patched.setattr(bridge_module, "DEADLINE_SECONDS", 1.5)
        response = workspace.bridge.post("/v1/slow", json={"seconds": 30}, headers=authed(token))
    assert response.status_code == 504 and response.json()["code"] == "unavailable"
    assert workspace.bridge.post("/v1/slow", json={"seconds": 0}, headers=authed(token)).status_code == 200


# ------------------------------------------------------------------------------------------------ errors


def test_every_error_has_detail_and_code(workspace):
    _, token = pair(workspace)
    headers = authed(token)
    cases = {
        ("get", "/v1/nope"): (404, "not_found"),
        ("get", "/v1/teapot"): (418, "invalid"),
        ("get", "/v1/coded"): (409, "conflict"),
        ("get", "/v1/value"): (400, "invalid"),
        ("get", "/v1/boom"): (500, "internal"),
        ("delete", "/v1/ping"): (405, "invalid"),
    }
    for (method, path), (status, code) in cases.items():
        response = getattr(workspace.bridge, method)(path, headers=headers)
        body = response.json()
        assert response.status_code == status and body["code"] == code and isinstance(body["detail"], str), path
        assert set(body) == {"detail", "code"}
    leaked = workspace.bridge.get("/v1/boom", headers=headers).text
    assert "secret page text" not in leaked and "RuntimeError" not in leaked


def test_validation_errors_do_not_echo_values(workspace):
    response = workspace.bridge.post("/v1/pair/start", json={"client_name": "x" * 500}, headers={"Origin": CHROME})
    assert response.status_code == 422 and response.json()["code"] == "invalid"
    assert "xxxx" not in response.text and "client_name" in response.json()["detail"]


def test_unauthorised_requests_all_look_the_same(workspace):
    client_id, token = pair(workspace)
    secret = token.split(".")[2]
    revoked_id, revoked_token = pair(workspace, origin=OTHER)
    manager_for(workspace.app).revoke(revoked_id)
    attempts = [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic " + token},
        {"Authorization": f"Bearer mrd1.{client_id}.{'A' * 43}"},  # right client, wrong secret
        {"Authorization": f"Bearer mrd1.00000000-0000-0000-0000-000000000000.{secret}"},  # unknown client
        authed(revoked_token, OTHER),  # revoked
        authed(token, OTHER),  # valid token, but presented by another extension
        authed(token, SAFARI),
    ]
    bodies = set()
    for headers in attempts:
        response = workspace.bridge.get("/v1/status", headers=headers)
        assert response.status_code == 401, headers
        bodies.add(response.text)
    assert len(bodies) == 1
    assert json.loads(bodies.pop()) == {"detail": "Connect this browser to Meridian", "code": "unauthorized"}


def test_valid_token_works_with_or_without_origin(workspace):
    client_id, token = pair(workspace, version="0.1.0")
    with_origin = workspace.bridge.get("/v1/ping", headers=authed(token))
    without = workspace.bridge.get("/v1/ping", headers=authed(token, origin=None))
    assert with_origin.status_code == without.status_code == 200
    assert with_origin.json() == {"client": client_id, "browser": "chrome", "origin": CHROME, "version": "0.1.0"}
    # a mutating call with a token still needs the Origin
    assert workspace.bridge.post("/v1/echo", json={}, headers=authed(token, origin=None)).status_code == 403


def test_disabled_assistant_is_reported_with_its_own_code(workspace):
    _, token = pair(workspace)
    workspace.main.patch("/api/extension/settings", json={"enabled": False})
    for response in (workspace.bridge.get("/v1/status", headers=authed(token)),
                     workspace.bridge.get("/v1/ping", headers=authed(token))):
        assert response.status_code == 403 and response.json()["code"] == "disabled"
    start = workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": OTHER})
    assert start.status_code == 403 and start.json()["code"] == "disabled"
    assert workspace.bridge.get("/v1/hello").status_code == 200  # the bridge itself still answers
    workspace.main.patch("/api/extension/settings", json={"enabled": True})
    assert workspace.bridge.get("/v1/status", headers=authed(token)).status_code == 200


def test_last_seen_and_version_are_written_at_most_once_a_minute(workspace, monkeypatch):
    client_id, token = pair(workspace, version="0.1.0")
    row = lambda: workspace.db.one("SELECT * FROM extension_clients WHERE id=?", (client_id,))  # noqa: E731
    assert row()["last_seen_at"] is None
    workspace.bridge.get("/v1/ping", headers=authed(token, **{"X-Meridian-Version": "0.1.3"}))
    first = row()
    assert first["last_seen_at"] and first["version"] == "0.1.3"
    workspace.db.execute("UPDATE extension_clients SET last_seen_at='SENTINEL' WHERE id=?", (client_id,))
    workspace.bridge.get("/v1/ping", headers=authed(token, **{"X-Meridian-Version": "0.1.4"}))
    assert row()["last_seen_at"] == "SENTINEL" and row()["version"] == "0.1.3"  # throttled: no write
    real = auth._monotonic()
    monkeypatch.setattr(auth, "_monotonic", lambda: real + 61)
    workspace.bridge.get("/v1/ping", headers=authed(token, **{"X-Meridian-Version": "0.1.4"}))
    assert row()["last_seen_at"] != "SENTINEL" and row()["version"] == "0.1.4"
    monkeypatch.setattr(auth, "_monotonic", lambda: real + 200)
    workspace.bridge.get("/v1/ping", headers=authed(token, **{"X-Meridian-Version": "not a version!!"}))
    assert row()["version"] == "0.1.4"  # garbage in the header is ignored


# ------------------------------------------------------------------------------------------------ hello


def test_hello_describes_the_app_and_proves_itself_to_paired_clients_only(workspace):
    hello = workspace.bridge.get("/v1/hello")
    assert hello.json() == {"app": "Meridian", "bridge": 1, "app_version": "0.1.0",
                            "latest_extension_version": "0.1.0", "port": 47615, "proof": None,
                            "revoked": False, "revoked_proof": None, "latest_extension_digest": ""}
    client_id, token = pair(workspace)
    proof = workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}").json()["proof"]
    token_hash = workspace.db.one("SELECT token_hash FROM extension_clients WHERE id=?", (client_id,))["token_hash"]
    assert proof == hello_proof(token_hash, NONCE, 47615)
    # the port is part of the proof: the same request on another port gives another proof
    workspace.bridge_app.state.port = 47619
    other = workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}").json()
    assert other["port"] == 47619 and other["proof"] == hello_proof(token_hash, NONCE, 47619) != proof
    for query in (f"client={client_id}", f"nonce={NONCE}", f"client={client_id}&nonce=short",
                  f"client=00000000-0000-0000-0000-000000000000&nonce={NONCE}"):
        assert workspace.bridge.get(f"/v1/hello?{query}").json()["proof"] is None
    manager_for(workspace.app).revoke(client_id)
    assert workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}").json()["proof"] is None


def test_hello_proves_a_revocation_to_the_client_that_was_revoked_and_to_nobody_else(workspace):
    """MEDIUM-1: 'no proof' must never mean 'revoked'. Meridian answers a revoked client with a proof only that client can
    check, so that an extension can tell the real app saying 'you were disconnected' from a listener that says nothing."""
    client_id, token = pair(workspace)
    token_hash = workspace.db.one("SELECT token_hash FROM extension_clients WHERE id=?", (client_id,))["token_hash"]
    active = workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}").json()
    assert active["proof"] and active["revoked"] is False and active["revoked_proof"] is None
    manager_for(workspace.app).revoke(client_id)
    gone = workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}").json()
    assert gone["proof"] is None and gone["revoked"] is True
    assert gone["revoked_proof"] == revoked_proof(token_hash, NONCE, 47615) != active["proof"]
    # it is bound to the nonce and the port like the active proof
    other_nonce = "BBECAwQFBgcICQoLDA0ODw"
    assert workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={other_nonce}").json()["revoked_proof"] != gone["revoked_proof"]
    # an unknown client, a missing nonce and a malformed nonce get no proof of any kind
    for query in ("client=00000000-0000-0000-0000-000000000000&nonce=" + NONCE, f"client={client_id}", f"client={client_id}&nonce=short"):
        body = workspace.bridge.get(f"/v1/hello?{query}").json()
        assert body["proof"] is None and body["revoked"] is False and body["revoked_proof"] is None, query


def test_hello_advertises_the_digest_of_the_bundled_build_from_its_stamp(workspace, tmp_path, monkeypatch):
    """M1: a rebuilt extension with the same version is told apart by the digest of its tree, stamped by the build."""
    from jobagent.extension import assets
    source = tmp_path / "stamped"
    chrome = source / "chrome"
    chrome.mkdir(parents=True)
    (chrome / "manifest.json").write_text(json.dumps(chrome_manifest("0.1.0")))
    (chrome / "background.js").write_text("// one")
    monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(source))
    assert workspace.bridge.get("/v1/hello").json()["latest_extension_digest"] == ""  # a build without a stamp: nothing to compare
    digest = assets.tree_digest(chrome, ignore={assets.STAMP})
    (chrome / assets.STAMP).write_text(json.dumps({"digest": digest}))
    hello = workspace.bridge.get("/v1/hello").json()
    assert hello["latest_extension_digest"] == digest and hello["latest_extension_version"] == "0.1.0"
    # the stamp is outside what it covers, so it can be part of the folder the installer copies
    assert assets.tree_digest(chrome, ignore={assets.STAMP}) == digest
    (chrome / "background.js").write_text("// two")  # a rebuild: same version, new content, new stamp
    new = assets.tree_digest(chrome, ignore={assets.STAMP})
    assert new != digest
    (chrome / assets.STAMP).write_text(json.dumps({"digest": new}) + " ")  # the cache follows the stamp file
    assert workspace.bridge.get("/v1/hello").json()["latest_extension_digest"] == new
    for junk in ('{"digest": "short"}', '{"digest": 5}', "[]", "not json", '{"digest": "' + "G" * 64 + '"}'):
        (chrome / assets.STAMP).write_text(junk)
        assert workspace.bridge.get("/v1/hello").json()["latest_extension_digest"] == "", junk


def test_the_build_stamp_is_the_python_tree_digest_of_the_real_build_and_node_agrees_with_python(tmp_path):
    """The stamp the build writes (node) must be exactly what ``assets.tree_digest`` computes (python), or the digests the
    app advertises and the worker holds could never be equal."""
    import shutil
    import subprocess
    from jobagent.extension import assets
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    tree = tmp_path / "tree"
    for name, text in {"manifest.json": "{}", "a.txt": "x", "a/b.js": "y", "a/c/d.js": "z", "a-b.js": "w", "B.js": "v",
                       "_locales/en/messages.json": "{}", ".hidden": "no", "x.js.map": "no", "build.json": "{}"}.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tree / "link").symlink_to(tree / "a.txt")
    script = Path(__file__).resolve().parents[2] / "extension" / "scripts" / "tree-digest.mjs"
    out = subprocess.run([node, "--input-type=module", "-e",
                          f"import {{ treeDigest }} from {json.dumps(script.as_uri())};"
                          f"console.log(treeDigest({json.dumps(str(tree))}, ['build.json']), treeDigest({json.dumps(str(tree))}))"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == assets.tree_digest(tree, ignore={"build.json"})
    assert out[1] == assets.tree_digest(tree)
    assert out[0] != out[1]
    dist = Path(__file__).resolve().parents[2] / "extension" / "dist" / "chrome"
    if (dist / assets.STAMP).is_file():  # a build made by `npm run build`
        assert json.loads((dist / assets.STAMP).read_text())["digest"] == assets.tree_digest(dist, ignore={assets.STAMP})


def test_unpaired_hello_marks_the_browser_as_detected(workspace):
    manager = manager_for(workspace.app)
    assert workspace.main.get("/api/extension").json()["state"] == "not_installed"
    workspace.bridge.get("/v1/hello", headers={"User-Agent": CHROME_AGENT})  # no version header: any web page could do this
    assert manager.contact("chrome") is None
    workspace.bridge.get("/v1/hello", headers={"User-Agent": CHROME_AGENT, "X-Meridian-Version": "0.1.0"})
    status = workspace.main.get("/api/extension").json()
    assert status["state"] == "detected" and status["browsers"]["chrome"]["state"] == "detected"
    assert status["browsers"]["chrome"]["seen_version"] == "0.1.0" and status["browsers"]["chrome"]["seen_unpaired_at"]
    assert status["browsers"]["safari"]["state"] == "not_installed"
    workspace.bridge.get("/v1/hello", headers={"Origin": SAFARI, "User-Agent": "x", "X-Meridian-Version": "0.1.0"})
    assert workspace.main.get("/api/extension").json()["browsers"]["safari"]["state"] == "detected"
    # a paired browser saying hello with its proof request is not "unpaired"
    client_id, _ = pair(workspace)
    manager._contacts.clear()
    workspace.bridge.get(f"/v1/hello?client={client_id}&nonce={NONCE}", headers={"User-Agent": CHROME_AGENT,
                                                                               "X-Meridian-Version": "0.1.0"})
    assert manager.contact("chrome") is None


# ------------------------------------------------------------------------------------------------ pairing routes


def test_pairing_over_http_end_to_end(workspace):
    start = workspace.bridge.post("/v1/pair/start", json={"client_name": "Meridian Assistant", "extension_version": "0.1.0",
                                                          "locale": "es-ES"}, headers={"Origin": CHROME})
    assert start.status_code == 200
    body = start.json()
    assert set(body) == {"pairing_id", "code", "expires_in", "poll_after_ms"} and body["expires_in"] == 120
    pid = body["pairing_id"]
    assert workspace.bridge.get(f"/v1/pair/{pid}").json() == {"state": "pending"}
    pending = workspace.main.get("/api/extension").json()["pending_pairing"]
    assert pending["id"] == pid and pending["code"] == body["code"] and pending["browser"] == "chrome"
    assert workspace.main.post(f"/api/extension/pairings/{pid}/approve").status_code == 200
    # another extension cannot take the token; the rightful one gets it once (Chrome sends no Origin on GET)
    stolen = workspace.bridge.get(f"/v1/pair/{pid}", headers={"Origin": OTHER})
    assert stolen.status_code == 403 and stolen.json()["code"] == "origin" and "token" not in stolen.text
    claimed = workspace.bridge.get(f"/v1/pair/{pid}").json()
    assert claimed["state"] == "approved" and claimed["token"].startswith("mrd1.")
    assert workspace.bridge.get(f"/v1/pair/{pid}", headers={"Origin": CHROME}).json() == {"state": "claimed"}
    ok = workspace.bridge.get("/v1/status", headers=authed(claimed["token"]))
    assert ok.status_code == 200
    StatusResponse(**ok.json())


def test_pairing_routes_validate_origin_and_ids(workspace):
    assert workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert workspace.bridge.post("/v1/pair/start", headers={"Origin": CHROME}).status_code == 200  # empty body is fine
    assert workspace.bridge.get("/v1/pair/does-not-exist-anywhere").status_code == 404
    assert workspace.bridge.get("/v1/pair/short").status_code == 404
    pid = workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": OTHER}).json()["pairing_id"]
    assert workspace.bridge.post(f"/v1/pair/{pid}/cancel", headers={"Origin": CHROME}).status_code == 403
    assert workspace.bridge.post(f"/v1/pair/{pid}/cancel").status_code == 403
    assert workspace.bridge.post(f"/v1/pair/{pid}/cancel", headers={"Origin": OTHER}).json() == {"state": "expired"}


def test_pairing_start_is_rate_limited_over_http(workspace):
    statuses = [workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": CHROME}).status_code for _ in range(7)]
    assert statuses == [200] * 5 + [429, 429]
    limited = workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": CHROME})
    assert limited.json()["code"] == "rate_limited" and limited.headers["retry-after"] == "60"


def test_the_extension_can_disconnect_itself(workspace):
    client_id, token = pair(workspace)
    assert workspace.bridge.post("/v1/disconnect", headers=authed(token)).json() == {"disconnected": True}
    assert workspace.db.one("SELECT revoked_at FROM extension_clients WHERE id=?", (client_id,))["revoked_at"]
    assert workspace.bridge.get("/v1/status", headers=authed(token)).status_code == 401
    assert workspace.bridge.post("/v1/disconnect", headers=authed(token)).status_code == 401
    assert workspace.main.get("/api/extension").json()["clients"] == []
    assert workspace.bridge.post("/v1/disconnect", headers={"Origin": CHROME}).status_code == 401


def test_denied_pairing_never_yields_a_token(workspace):
    pid = workspace.bridge.post("/v1/pair/start", json={}, headers={"Origin": CHROME}).json()["pairing_id"]
    assert workspace.main.post(f"/api/extension/pairings/{pid}/deny").status_code == 200
    assert workspace.bridge.get(f"/v1/pair/{pid}").json() == {"state": "denied"}
    assert workspace.main.post(f"/api/extension/pairings/{pid}/approve").status_code == 409


# ------------------------------------------------------------------------------------------------ status and settings


def test_status_shape_and_no_keychain_access(workspace):
    _, token = pair(workspace)
    response = workspace.bridge.get("/v1/status", headers=authed(token))
    assert response.status_code == 200
    body = response.json()
    parsed = StatusResponse(**body)
    assert parsed.protocol == 1 and parsed.app_version == "0.1.0" and parsed.latest_extension_version == "0.1.0"
    assert body["profile"] == {"verified_facts": 0, "has_cv": False, "unapproved_cvs": 0, "display_name": None}
    assert set(body["generation"]) == {"available", "tested", "provider", "local", "message", "monthly_limit_usd", "spent_usd"}
    assert body["generation"]["available"] is False and body["generation"]["provider"] == "none"
    assert body["settings"] == {
        "enabled": True, "language": "auto", "auto_advance": True, "skip_filled": True, "tab_inserts_protected": False,
        "save_corrections": "ask", "background_research": True, "background_drafts": True, "local_assist": True,
        "web_lookup": True, "hint_on_ats": True, "draft_budget_usd": 1.0, "always_on_sites": [],  # never "unlimited" by default (SEC-06)
    }
    assert "mrd1" not in response.text and "token_hash" not in response.text
    assert workspace.secrets.reads == []  # the Keychain is never touched


def test_status_profile_and_generation_reflect_the_workspace(workspace):
    db = workspace.db
    _, token = pair(workspace)
    candidate = db.one("SELECT id FROM candidates LIMIT 1")["id"]
    for key, value, status in (("full_name", "Ada Lovelace", "verified"), ("email", "ada@example.test", "verified"),
                               ("phone", "123", "unverified")):
        db.execute(
            "INSERT INTO facts(id,candidate_id,category,key,value,source,verification_status,locked,notes,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,0,'','t','t')",
            (key, candidate, "application_profile", key, value, "application_profile:" + key, status))
    db.execute("INSERT INTO documents VALUES('d1','cv.pdf','import',NULL,'t','t')")
    db.execute("INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,approved,created_at) "
               "VALUES('v1','d1',1,'/x','cv.pdf','application/pdf','h',1,'t')")
    db.execute("INSERT INTO settings VALUES('text_provider','\"ollama\"')")
    db.execute("INSERT INTO settings VALUES('text_model','\"qwen3\"')")
    body = workspace.bridge.get("/v1/status", headers=authed(token)).json()
    assert body["profile"] == {"verified_facts": 2, "has_cv": True, "unapproved_cvs": 0, "display_name": "Ada Lovelace"}
    assert body["generation"]["available"] is True and body["generation"]["local"] is True
    assert body["generation"]["tested"] is False and "Test your writing model" in body["generation"]["message"]
    assert body["generation"]["monthly_limit_usd"] is None
    from jobagent.onboarding import TEXT_AI, fingerprint, record

    record(db, "ai_text", {"success": True, "fingerprint": fingerprint(db, TEXT_AI)})
    workspace.app.state.extension_status_cache = None
    assert workspace.bridge.get("/v1/status", headers=authed(token)).json()["generation"]["tested"] is True
    assert workspace.secrets.reads == []


def test_cloud_generation_needs_a_cap_and_exact_model_prices(workspace):
    db = workspace.db
    _, token = pair(workspace)
    for key, value in (("text_provider", '"openai"'), ("text_model", '"gpt-x"')):
        db.execute("INSERT INTO settings VALUES(?,?)", (key, value))
    generation = lambda: workspace.bridge.get("/v1/status", headers=authed(token)).json()["generation"]  # noqa: E731
    first = generation()
    assert first["available"] is False and "spending cap" in first["message"]
    db.execute("INSERT INTO settings VALUES('monthly_budget','5')")
    workspace.app.state.extension_status_cache = None
    assert generation()["available"] is False and "token prices" in generation()["message"]
    prices = {"answer_model_prices": {"gpt-x": {"input_cost_per_million": 1, "output_cost_per_million": 2,
                                                "cached_input_cost_per_million": .5, "cache_write_cost_per_million": 1}}}
    db.execute("INSERT INTO integrations(id,provider,config,status) VALUES('i1','openai',?, 'configured')", (json.dumps(prices),))
    from datetime import datetime, timezone

    db.execute("INSERT INTO settings VALUES(?,?)", ("ai_usage:" + datetime.now(timezone.utc).strftime("%Y-%m"),
                                                    json.dumps({"cost": 1.25, "calls": 3})))
    workspace.app.state.extension_status_cache = None
    ready = generation()
    assert ready["available"] is True and ready["local"] is False
    assert ready["monthly_limit_usd"] == 5.0 and ready["spent_usd"] == 1.25
    assert workspace.secrets.reads == []


def test_status_profile_is_cached_but_settings_are_live(workspace):
    _, token = pair(workspace)
    status = lambda: workspace.bridge.get("/v1/status", headers=authed(token)).json()  # noqa: E731
    assert status()["profile"]["verified_facts"] == 0
    candidate = workspace.db.one("SELECT id FROM candidates LIMIT 1")["id"]
    workspace.db.execute(
        "INSERT INTO facts(id,candidate_id,category,key,value,source,verification_status,locked,notes,created_at,updated_at)"
        " VALUES('f','%s','contact','email','a@b.test','manual','verified',0,'','t','t')" % candidate)
    workspace.db.execute("INSERT INTO settings VALUES('extension_language','\"es\"')")
    again = status()
    assert again["profile"]["verified_facts"] == 0  # cached for a minute
    assert again["language"] == "es" and again["settings"]["language"] == "es"  # but settings are read live
    workspace.app.state.extension_status_cache = None
    assert status()["profile"]["verified_facts"] == 1


def test_extension_may_only_patch_its_own_preferences(workspace):
    _, token = pair(workspace)
    headers = authed(token)
    changed = workspace.bridge.patch("/v1/settings", json={"auto_advance": False, "language": "es",
                                                           "save_corrections": "never"}, headers=headers)
    assert changed.status_code == 200
    assert changed.json()["auto_advance"] is False and changed.json()["language"] == "es"
    assert changed.json()["save_corrections"] == "never" and changed.json()["enabled"] is True
    stored = workspace.db.one("SELECT value FROM settings WHERE key='extension_auto_advance'")
    assert stored["value"] == "false"
    assert workspace.bridge.get("/v1/status", headers=headers).json()["settings"]["language"] == "es"
    for bad in ({"enabled": False}, {"background_drafts": False}, {"always_on_sites": ["a.example.com"]},
                {"draft_budget_usd": 1}, {"unknown": 1}, {"language": "fr"}, {"auto_advance": "yes"}, {}):
        response = workspace.bridge.patch("/v1/settings", json=bad, headers=headers)
        assert response.status_code == 422 and response.json()["code"] == "invalid", bad
    assert workspace.db.one("SELECT value FROM settings WHERE key='extension_enabled'") is None


# ------------------------------------------------------------------------------------------------ route mounting


def test_workstream_b_routes_are_mounted_under_v1_and_need_a_client(workspace):
    assert workspace.bridge_app.state.routes_loaded is True
    assert workspace.bridge.get("/v1/ping").status_code == 401
    assert workspace.bridge.get("/ping").status_code == 404  # only under /v1


def test_the_real_route_module_works_behind_the_guard(tmp_path, monkeypatch):
    """Workstream B's actual router (not the stand-in) mounted by create_bridge_app: auth, JSON-only, error format."""
    pytest.importorskip("jobagent.extension.routes")
    monkeypatch.setenv("MERIDIAN_TEST", "1")  # nothing reaches the Internet under MERIDIAN_TEST
    monkeypatch.delitem(sys.modules, "jobagent.extension.routes", raising=False)
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    bridge_app = create_bridge_app(app)
    assert bridge_app.state.routes_loaded is True and bridge_app.state.routes_error is None
    manager = manager_for(app)
    started = manager.start(CHROME, "Meridian", "0.1.0")
    manager.approve(started["pairing_id"])
    token = manager.status(started["pairing_id"], CHROME)["token"]
    page = {"page": {"url": "https://boards.greenhouse.io/acme/jobs/1", "title": "Engineer - Acme", "h1": "Engineer",
                     "company_hint": "Acme"}, "tab_key": "t1"}
    with TestClient(bridge_app, base_url=BASE) as client:
        assert client.get("/v1/documents").status_code == 401
        assert client.post("/v1/sessions", json=page, headers={"Origin": CHROME}).status_code == 401
        headers = authed(token)
        assert client.get("/v1/documents", headers=headers).json() == []
        assert client.post("/v1/sessions", content=json.dumps(page), headers=headers | {"Content-Type": "text/plain"}).status_code == 415
        invalid = client.post("/v1/sessions", json={}, headers=headers)
        assert invalid.status_code == 422 and invalid.json() == {"detail": "Invalid request: page", "code": "invalid"}
        created = client.post("/v1/sessions", json=page, headers=headers)
        assert created.status_code == 200 and created.json()["session_id"] and created.json()["protocol"] == 1
        sid = created.json()["session_id"]
        field = {"node_id": 1, "tag": "input", "type": "text", "label": "First name", "role": "textbox"}
        suggested = client.post(f"/v1/sessions/{sid}/suggest", json={"elements": [field], "mode": "scan"}, headers=headers)
        assert suggested.status_code == 200 and suggested.json()["results"][0]["node_id"] == 1
        assert client.post("/v1/sessions/unknown/suggest", json={"elements": []}, headers=headers).json()["code"] == "not_found"
        assert client.delete(f"/v1/sessions/{sid}", headers=headers).json() == {"released": True}


def test_missing_routes_module_fails_loudly(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setitem(sys.modules, "jobagent.extension.routes", None)
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    with caplog.at_level("ERROR", logger="meridian.bridge"):
        bridge_app = create_bridge_app(app)
    assert bridge_app.state.routes_loaded is False and bridge_app.state.routes_error
    assert any("bridge_routes_unavailable" in record.getMessage() and record.levelname == "ERROR" for record in caplog.records)
    with TestClient(bridge_app, base_url=BASE) as client:  # hello and pairing still work, so the extension can say why
        assert client.get("/v1/hello").status_code == 200


# ------------------------------------------------------------------------------------------------ listener


def test_port_candidates_and_enablement(monkeypatch):
    for variable in ("MERIDIAN_BRIDGE", "MERIDIAN_BRIDGE_PORT", "MERIDIAN_TEST"):
        monkeypatch.delenv(variable, raising=False)
    assert bridge_ports("") == list(range(47615, 47625))
    assert bridge_ports("47700") == [47700] and bridge_ports("47700-47702") == [47700, 47701, 47702]
    assert bridge_ports("0") == [0]
    for bad in ("9-3", "abc", "70000", "-5", "1-70000"):
        with pytest.raises(ValueError):
            bridge_ports(bad)
    assert bridge_enabled() is True  # real runs
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    assert bridge_enabled() is False  # tests
    monkeypatch.setenv("MERIDIAN_BRIDGE_PORT", "0")
    assert bridge_enabled() is True
    monkeypatch.setenv("MERIDIAN_BRIDGE", "0")
    assert bridge_enabled() is False
    monkeypatch.setenv("MERIDIAN_BRIDGE", "1")
    monkeypatch.delenv("MERIDIAN_BRIDGE_PORT")
    assert bridge_enabled() is True
    assert bridge_enabled(True) is True and bridge_enabled(False) is False


@pytest.fixture
async def listening(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setitem(sys.modules, "jobagent.extension.routes", fake_routes())
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    bridge = BridgeServer(create_bridge_app(app), [0])
    assert await bridge.start() is True
    yield types.SimpleNamespace(app=app, server=bridge, url=f"http://127.0.0.1:{bridge.port}")
    await bridge.stop()


def whole_responses(data: bytes, count: int = 1) -> bool:
    """Do ``data`` hold ``count`` complete HTTP/1.1 responses (status line, headers and the whole Content-Length body)?"""
    position = 0
    for _ in range(count):
        end = data.find(b"\r\n\r\n", position)
        if end < 0:
            return False
        length = re.search(rb"(?i)\r\ncontent-length:\s*(\d+)", data[position:end])
        position = end + 4 + (int(length.group(1)) if length else 0)
        if len(data) < position:
            return False
    return True


async def read_http(reader, count: int = 1, timeout: float = 60.0) -> bytes:
    """Read until ``count`` responses are complete, the server closes the connection, or ``timeout`` passes (what arrived
    so far is returned). One ``read`` returns whatever a single TCP segment held: on a loaded machine the headers and the
    body of one answer arrive apart, so a test that reads once sees half an answer."""
    data = b""
    deadline = asyncio.get_running_loop().time() + timeout
    while not whole_responses(data, count):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(reader.read(65536), remaining)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        data += chunk
    return data


async def raw(port, data: bytes, count=1, wait=1.0):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(data)
    await writer.drain()
    try:
        return await read_http(reader, count, wait)
    finally:
        writer.close()


async def test_the_socket_helpers_wait_for_an_answer_that_arrives_in_pieces():
    """The headers and the body of one answer may reach the test in different reads (a loaded machine does that): the
    helper keeps reading until the whole body is there, which a single read never could."""
    body = b'{"detail":"x","code":"too_large"}'
    head = b"HTTP/1.1 413 Payload Too Large\r\ncontent-length: %d\r\n\r\n" % len(body)

    release = asyncio.Event()

    async def serve(reader, writer):
        writer.write(head)
        await writer.drain()
        await asyncio.sleep(0.3)  # the body follows in a later segment
        writer.write(body)
        await writer.drain()
        await release.wait()  # and the connection stays open (keep-alive): only the length says it is complete
        writer.close()

    server_ = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server_.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.read(4096), 5)  # what a single read gets
        assert first == head and not whole_responses(first)
        writer.close()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await read_http(reader) == head + body
        writer.close()
        assert await raw(port, b"x", wait=30) == head + body
        assert whole_responses(head + body + head + body, 2) and not whole_responses(head + body + head, 2)
    finally:
        release.set()
        server_.close()


async def test_listener_serves_the_bridge_app_over_real_sockets(listening):
    async with httpx.AsyncClient() as client:
        hello = (await client.get(listening.url + "/v1/hello")).json()
        assert hello["app"] == "Meridian" and hello["port"] == listening.server.port
        assert listening.server.status()["running"] is True and listening.server.status()["port"] == listening.server.port
        # the Host header decides, whatever address was dialled
        rebinding = await client.get(listening.url + "/v1/hello", headers={"Host": "evil.example"})
        assert rebinding.status_code == 400 and rebinding.json()["code"] == "host"
        web = await client.get(listening.url + "/v1/hello", headers={"Origin": "https://evil.example"})
        assert web.status_code == 403
        manager = manager_for(listening.app)
        started = manager.start(CHROME, "Meridian", "0.1.0")
        manager.approve(started["pairing_id"])
        issued = manager.status(started["pairing_id"], CHROME)
        proof = (await client.get(listening.url + f"/v1/hello?client={issued['client_id']}&nonce={NONCE}")).json()["proof"]
        token_hash = listening.app.state.db.one("SELECT token_hash FROM extension_clients")["token_hash"]
        assert proof == hello_proof(token_hash, NONCE, listening.server.port)  # bound to the port that really served it
        ping = await client.get(listening.url + "/v1/ping", headers={"Authorization": f"Bearer {issued['token']}"})
        assert ping.status_code == 200 and ping.json()["client"] == issued["client_id"]


async def test_streamed_bodies_are_limited_without_content_length(listening):
    manager = manager_for(listening.app)
    started = manager.start(CHROME, "Meridian", "0.1.0")
    manager.approve(started["pairing_id"])
    token = manager.status(started["pairing_id"], CHROME)["token"]
    head = (f"POST /v1/echo HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {CHROME}\r\nAuthorization: Bearer {token}\r\n"
            "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n").encode()
    chunk = b'{"a":"' + b"x" * (600 * 1024)
    body = head + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
    reader, writer = await asyncio.open_connection("127.0.0.1", listening.server.port)
    writer.write(body)
    await writer.drain()
    try:
        answer = await read_http(reader)  # the status line, the headers and the JSON body, however the segments fall
    finally:
        writer.close()
    assert answer.startswith(b"HTTP/1.1 413") and b'"too_large"' in answer


async def test_pipelined_requests_are_each_judged_on_their_own_over_a_real_socket(listening):
    """Three requests in one write on one connection: the middle one has a Host that is not loopback."""
    port = listening.server.port

    def request(host):
        return f"GET /v1/hello HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request(f"127.0.0.1:{port}") + request(f"[::1]:{port}") + request(f"LOCALHOST:{port}")
                 + request("localhost.") + request(f"localhost:{port}, evil.example"))
    await writer.drain()
    try:
        answers = await read_http(reader, count=5)
    finally:
        writer.close()
    statuses = [part.split(b" ", 1)[0] for part in answers.split(b"HTTP/1.1 ")[1:]]
    assert statuses == [b"200", b"400", b"200", b"400", b"400"]


async def test_a_streamed_body_cannot_hide_a_second_request_from_the_guard(listening):
    """A request smuggled inside a chunked body is body, not a request: the guard (and uvicorn's parser) never serve it."""
    manager = manager_for(listening.app)
    started = manager.start(CHROME, "Meridian", "0.1.0")
    manager.approve(started["pairing_id"])
    token = manager.status(started["pairing_id"], CHROME)["token"]
    smuggled = f"GET /v1/ping HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\n\r\n".encode()
    body = b'{"a":1}' + smuggled  # a body that merely looks like it ends with a second request
    head = (f"POST /v1/echo HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {CHROME}\r\nAuthorization: Bearer {token}\r\n"
            "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n").encode()
    payload = head + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    answer = await raw(listening.server.port, payload, wait=60)
    assert answer.count(b"HTTP/1.1 ") == 1 and answer.startswith(b"HTTP/1.1 422")  # one answer: the body is not JSON
    assert b'"client"' not in answer  # /v1/ping was never executed


async def test_malformed_requests_cannot_crash_the_listener(listening):
    for junk in (b"GARBAGE\r\n\r\n", b"GET /v1/hello HTTP/1.1\r\n\r\n", b"\x00\x01\x02", b"POST /v1/pair/start HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 99999999\r\n\r\n"):
        await raw(listening.server.port, junk)
    async with httpx.AsyncClient() as client:
        assert (await client.get(listening.url + "/v1/hello")).status_code == 200


async def test_fallback_to_the_next_port_and_all_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    bridge_app = create_bridge_app(app)
    first = BridgeServer(bridge_app, [0])
    assert await first.start()
    second = BridgeServer(bridge_app, [first.port, 0])
    assert await second.start() and second.port != first.port
    busy = BridgeServer(bridge_app, [first.port])
    assert await busy.start() is False
    assert "no free bridge port" in busy.status()["error"] and busy.status()["running"] is False
    assert busy.status()["port"] is None
    async with httpx.AsyncClient() as client:
        assert (await client.get(f"http://127.0.0.1:{second.port}/v1/hello")).json()["port"] == second.port
    await first.stop()
    await second.stop()
    await busy.stop()
    assert first.status()["running"] is False


async def test_system_exit_inside_serve_never_ends_the_process(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    bridge = BridgeServer(create_bridge_app(app), [0])

    async def boom(self, sockets=None):
        raise SystemExit(3)

    monkeypatch.setattr(server.EmbeddedServer, "serve", boom)
    assert await bridge.start() is False
    assert "exit 3" in bridge.status()["error"]
    await asyncio.sleep(0)  # the event loop (and the primary server) are still alive


async def test_embedded_server_leaves_process_signals_alone(listening):
    import signal

    before = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    other = BridgeServer(listening.server.app, [0])
    assert await other.start()
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before
    await other.stop()


async def test_stop_releases_the_port_and_restart_rebinds(listening):
    port = listening.server.port
    await listening.server.stop()
    assert listening.server.status()["running"] is False
    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}/v1/hello")
    assert await listening.server.restart() is True
    async with httpx.AsyncClient() as client:
        assert (await client.get(f"http://127.0.0.1:{listening.server.port}/v1/hello")).status_code == 200
    assert await listening.server.start() is True  # starting a running bridge is a no-op


@pytest.fixture
async def guarded(tmp_path, monkeypatch):
    """A listener with a short head deadline and a small per-peer cap (the real values are 10 s and 16)."""
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setitem(sys.modules, "jobagent.extension.routes", fake_routes())
    monkeypatch.setattr(server, "HEADER_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(server, "MAX_UNFINISHED_PER_PEER", 8)
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    bridge = BridgeServer(create_bridge_app(app), [0])
    assert await bridge.start() is True
    yield types.SimpleNamespace(app=app, server=bridge, port=bridge.port, url=f"http://127.0.0.1:{bridge.port}")
    await bridge.stop()


async def hello_status(url: str) -> int | str:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            return (await client.get(url + "/v1/hello")).status_code
    except Exception as exc:  # noqa: BLE001 - the test reports what a client would see
        return type(exc).__name__


async def closed_by_server(reader, timeout: float) -> bool:
    try:
        return await asyncio.wait_for(reader.read(1), timeout) == b""
    except ConnectionError:
        return True  # closed with unread bytes pending: the peer sees a reset instead of an end of stream
    except asyncio.TimeoutError:
        return False


async def test_half_open_connections_cannot_keep_the_bridge_from_answering(guarded):
    """LOW (final-sec t18): 64 sockets that sent a partial head held every slot of limit_concurrency, so every real request
    was answered 503 for as long as the attacker kept them (45 s measured). They are cut at the head deadline, and the cap
    keeps room for a real request even before that."""
    sockets = []
    for index in range(70):
        reader, writer = await asyncio.open_connection("127.0.0.1", guarded.port)
        if index % 2:
            writer.write(b"GET /v1/hello HTTP/1.1\r\nHost: 127.0.0.1\r\nX-A: b")  # a head that never ends
        sockets.append((reader, writer))
    try:
        assert await hello_status(guarded.url) == 200  # at once: the cap closed the oldest ones to keep room
        assert all([await closed_by_server(reader, 3) for reader, _ in sockets]), "every unfinished connection is cut at the deadline"
        assert len(guarded.server.server.server_state.connections) <= 2
        assert await hello_status(guarded.url) == 200
    finally:
        for _, writer in sockets:
            writer.close()


async def test_the_per_peer_cap_closes_the_oldest_unfinished_connection_not_the_new_one(guarded, monkeypatch):
    monkeypatch.setattr(server, "HEADER_TIMEOUT_SECONDS", 60.0)  # only the cap acts here
    await guarded.server.restart()
    url = f"http://127.0.0.1:{guarded.server.port}"
    opened = []
    for _ in range(8):
        opened.append(await asyncio.open_connection("127.0.0.1", guarded.server.port))
        await asyncio.sleep(0.02)
    assert not any([await closed_by_server(reader, 0.1) for reader, _ in opened]), "within the cap nothing is touched"
    newest = await asyncio.open_connection("127.0.0.1", guarded.server.port)
    assert await closed_by_server(opened[0][0], 2), "the oldest made room"
    assert not await closed_by_server(newest[0], 0.2) and not await closed_by_server(opened[-1][0], 0.2)
    assert await hello_status(url) == 200
    for _, writer in [*opened, newest]:
        writer.close()


async def test_a_trickled_head_is_cut_from_its_first_byte_but_a_whole_head_is_never_timed_by_it(guarded):
    reader, writer = await asyncio.open_connection("127.0.0.1", guarded.port)
    writer.write(b"GET /v1/hello HTTP/1.1\r\n")
    for _ in range(4):  # one more line every 0.25 s: slow-loris keeps the connection busy without ever finishing the head
        await asyncio.sleep(0.25)
        try:
            writer.write(b"X-Pad: 1\r\n")
            await writer.drain()
        except ConnectionError:
            break
    assert await closed_by_server(reader, 2)
    writer.close()
    # a complete head, then a slow answer (the long poll case): the deadline of the HEAD does not apply to the wait for the response
    token = manager_for(guarded.app)
    started = token.start(CHROME, "Meridian", "0.1.0")
    token.approve(started["pairing_id"])
    secret = token.status(started["pairing_id"], CHROME)["token"]
    body = b'{"seconds": 1.4}'
    request = (f"POST /v1/slow HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {CHROME}\r\nAuthorization: Bearer {secret}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
    answer = await raw(guarded.port, request, wait=6)
    assert answer.startswith(b"HTTP/1.1 200") and b'"ok"' in answer


async def test_a_keep_alive_connection_that_waits_between_requests_is_not_a_slow_head(guarded):
    reader, writer = await asyncio.open_connection("127.0.0.1", guarded.port)
    request = b"GET /v1/hello HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    try:
        for _ in range(3):
            writer.write(request)
            await writer.drain()
            assert (await read_http(reader, 1, 5)).startswith(b"HTTP/1.1 200")
            await asyncio.sleep(1.0)  # longer than the head deadline (0.6 s), shorter than the keep-alive timeout (5 s)
    finally:
        writer.close()


async def test_the_registry_of_unfinished_connections_empties_itself(guarded):
    unfinished = server.guarded_protocol().unfinished
    assert unfinished == {}  # a fresh listener's class starts empty
    protocol = guarded.server.server.config.http_protocol_class
    for _ in range(3):
        reader, writer = await asyncio.open_connection("127.0.0.1", guarded.port)
        writer.write(b"GET /v1/hello HTTP/1.1\r\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert sum(len(peers) for peers in protocol.unfinished.values()) >= 1
        writer.close()
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.2)
    assert protocol.unfinished == {}, "closed and finished connections leave nothing behind"
    assert await hello_status(guarded.url) == 200
    assert protocol.unfinished == {}


async def test_shutdown_is_quick_with_an_idle_keepalive_client(listening):
    async with httpx.AsyncClient() as client:
        await client.get(listening.url + "/v1/hello")
        started = asyncio.get_running_loop().time()
        await listening.server.stop()
        assert asyncio.get_running_loop().time() - started < 6


def test_create_app_with_bridge_starts_installs_and_stops_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("MERIDIAN_BRIDGE_PORT", "0")
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False, bridge=True)
    with TestClient(app, headers={"Authorization": "Bearer t"}) as client:
        status = app.state.bridge.status()
        assert status["running"] is True and status["port"]
        assert httpx.get(f"http://127.0.0.1:{status['port']}/v1/hello").json()["port"] == status["port"]
        view = client.get("/api/extension").json()
        assert view["bridge"]["state"] == "listening" and view["bridge"]["port"] == status["port"]
        assert app.state.extension_install["installed"] is True and app.state.extension_install["version"] == "0.1.0"
        assert (tmp_path / "extensions" / "chrome" / "manifest.json").is_file()
        port = status["port"]
    assert app.state.bridge.status()["running"] is False
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/v1/hello")


def test_default_app_has_no_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer t"}) as client:
        assert getattr(app.state, "bridge", None) is None
        assert client.get("/api/extension").json()["bridge"]["state"] == "stopped"


def test_port_conflict_does_not_stop_meridian_and_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    monkeypatch.setenv("MERIDIAN_BRIDGE_PORT", str(holder.getsockname()[1]))
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False, bridge=True)
    try:
        with TestClient(app, headers={"Authorization": "Bearer t"}) as client:
            assert client.get("/api/health").status_code == 200
            view = client.get("/api/extension").json()
            assert view["bridge"]["state"] == "port_in_use" and view["bridge"]["port"] == holder.getsockname()[1]
            assert view["state"] == "bridge_error"
            assert client.get("/api/onboarding").json()["steps"][-1]["status"] == "needs_attention"
            holder.close()
            restarted = client.post("/api/extension/bridge/restart").json()
            assert restarted["bridge"]["state"] == "listening" and restarted["state"] == "not_installed"
    finally:
        holder.close()


@pytest.mark.skipif(not os.path.exists("/usr/sbin/lsof"), reason="needs lsof")
async def test_a_port_conflict_names_the_process_but_never_its_command_line(monkeypatch):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        monkeypatch.setenv("MERIDIAN_TEST", "1")
        assert await server.listener_name(port) is None  # tests never shell out to lsof
        monkeypatch.delenv("MERIDIAN_TEST")
        name = await server.listener_name(port)
        assert name and name.isprintable() and "/" not in name and len(name) <= 40  # a process NAME, not argv
    finally:
        holder.close()
    assert await server.listener_name(port) is None  # nothing listens any more


@pytest.mark.parametrize("problem", ["unwritable folder", "damaged manifest"])
def test_a_failed_install_is_not_fatal_is_logged_once_and_heals_without_a_restart(tmp_path, monkeypatch, caplog, problem):
    """The packaged app installs its Chrome folder at startup. Whatever goes wrong (a data folder that cannot be
    written, a bundle with a damaged manifest) Meridian and the bridge still start, the problem is logged exactly once
    and the Settings action explains it in a sentence instead of failing with a server error."""
    import logging

    from jobagent.extension import assets

    if problem == "unwritable folder" and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("MERIDIAN_BRIDGE_PORT", "0")
    data = tmp_path / "data"
    extensions = data / "extensions"
    extensions.mkdir(parents=True)
    if problem == "unwritable folder":
        extensions.chmod(0o500)
    else:
        bundle = tmp_path / "damaged"
        (bundle / "chrome").mkdir(parents=True)
        (bundle / "chrome" / "manifest.json").write_text("{not json")
        monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(bundle))
        monkeypatch.setattr(assets, "source_candidates", lambda: [bundle])  # (a broken override would fall through to the repo)
    caplog.set_level(logging.INFO)
    app = create_app(data, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False, bridge=True)
    try:
        with TestClient(app, headers={"Authorization": "Bearer t"}) as client:
            assert client.get("/api/health").status_code == 200
            assert app.state.bridge.status()["running"] is True
            assert app.state.extension_install["installed"] is False and app.state.extension_install["reason"]
            view = client.get("/api/extension").json()
            assert view["browsers"]["chrome"]["extension_path"] is None and view["bridge"]["state"] == "listening"
            assert httpx.get(f"http://127.0.0.1:{app.state.bridge.status()['port']}/v1/hello").status_code == 200
            for action in ("prepare", "reveal", "copy_path"):  # a sentence, not a 500
                response = client.post("/api/extension/install/chrome", json={"action": action})
                assert response.status_code == 409, response.text
                assert response.json()["detail"] == (
                    "Meridian could not prepare the extension folder. Check that its data folder can be written to."
                    if problem == "unwritable folder"
                    else "The browser extension bundled with this Meridian is damaged. Reinstall Meridian.")
            failures = [r for r in caplog.records if r.getMessage().startswith("extension_install_failed")]
            assert len(failures) == 1 and failures[0].levelno == logging.WARNING  # once, and only the exception's type
            assert str(tmp_path) not in failures[0].getMessage()
            # the user fixes the cause: the same button works at once
            extensions.chmod(0o700)
            monkeypatch.setenv("MERIDIAN_EXTENSION_SRC", str(tmp_path / "healthy"))
            monkeypatch.setattr(assets, "source_candidates", lambda: [tmp_path / "healthy"])
            healthy = tmp_path / "healthy" / "chrome"
            healthy.mkdir(parents=True)
            (healthy / "manifest.json").write_text(json.dumps(chrome_manifest("2.0.0")))
            prepared = client.post("/api/extension/install/chrome", json={"action": "prepare"})
            assert prepared.status_code == 200 and prepared.json()["ok"] is True
            assert (extensions / "chrome" / "manifest.json").is_file()
            assert client.get("/api/extension").json()["browsers"]["chrome"]["extension_path"] == str(extensions / "chrome")
    finally:
        extensions.chmod(0o700)


def test_extension_failures_never_prevent_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("MERIDIAN_BRIDGE_PORT", "0")
    from jobagent.extension import assets

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(assets, "install_chrome_extension", explode)
    app = create_app(tmp_path, worker_isolation=False, token="t", secret_store=MemorySecrets(), start_scheduler=False, bridge=True)
    with TestClient(app, headers={"Authorization": "Bearer t"}) as client:
        assert client.get("/api/health").status_code == 200
        assert app.state.extension_install["installed"] is False
        assert app.state.bridge.status()["running"] is True


# ------------------------------------------------------------------------------------------------ a locked database
def writer_held(workspace):
    """Another writer holds the database's single write lock: writes wait a moment and fail with 'database is locked'."""
    import contextlib
    import sqlite3

    db = workspace.db
    original = db._connect

    @contextlib.contextmanager
    def hold():
        blocker = sqlite3.connect(str(db.path), timeout=0.1, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")

        def quick():
            connection = original()
            connection.execute("PRAGMA busy_timeout=50")
            return connection

        db._connect = quick
        try:
            yield
        finally:
            del db._connect
            blocker.execute("ROLLBACK")
            blocker.close()

    return hold()


def test_a_busy_database_answers_503_unavailable_writes_nothing_and_reads_still_work(workspace):
    """Reads (hello, status) keep working; pairing and a settings change answer 503 ``unavailable`` and leave no trace;
    the same request works once the database is free. The last-seen stamp of an authenticated request is bookkeeping
    only: it never fails the request."""
    _, token = pair(workspace)
    before = workspace.db.one("SELECT COUNT(*) AS n FROM extension_pairings")["n"]
    settings_before = workspace.db.query("SELECT key,value FROM settings ORDER BY key")
    with writer_held(workspace):
        assert workspace.bridge.get("/v1/hello").status_code == 200
        assert workspace.bridge.get("/v1/status", headers=authed(token, origin=None)).status_code == 200
        started = workspace.bridge.post("/v1/pair/start", json={"client_name": "x"}, headers={"Origin": OTHER})
        assert started.status_code == 503 and started.json()["code"] == "unavailable"
        patched = workspace.bridge.patch("/v1/settings", json={"language": "es"}, headers=authed(token))
        assert patched.status_code == 503 and patched.json() == {
            "detail": "Meridian's local database is busy. Try again in a moment.", "code": "unavailable"}
    assert workspace.db.one("SELECT COUNT(*) AS n FROM extension_pairings")["n"] == before
    assert workspace.db.query("SELECT key,value FROM settings ORDER BY key") == settings_before
    assert workspace.bridge.post("/v1/pair/start", json={"client_name": "x"}, headers={"Origin": OTHER}).status_code == 200
    assert workspace.bridge.patch("/v1/settings", json={"language": "es"}, headers=authed(token)).json()["language"] == "es"
