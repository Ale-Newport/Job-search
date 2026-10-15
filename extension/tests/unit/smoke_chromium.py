"""Real-Chromium smoke test of the extension core (service worker, pairing, options page, toolbar path, ATS hint).

    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python extension/tests/unit/smoke_chromium.py [--keep-shots DIR]

Uses the Chromium that Playwright bundles (channel "chromium", headless, --load-extension), a throw-away profile, and a
fake Meridian bridge written in Python (so the hello proof is verified against an INDEPENDENT HMAC implementation, not the
TypeScript one). The page content script is replaced by a tiny stub in the temporary build, so this file tests workstream C
(worker, bridge client, pairing, options, hint, activation) and not the in-page assistant. No real network, no real data.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import re
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

ROOT = Path(__file__).resolve().parents[3]
EXT = ROOT / "extension"
EXT_ID = "ophngbnbiekjhemmabbpildjedfphlbc"
SECRET_LEN = 43

class RoomyServer(ThreadingHTTPServer):
    """A listen backlog of 5 (the default) makes a loaded machine refuse some of the many requests one page makes (TEST-01)."""

    daemon_threads = True
    request_queue_size = 256


STUB_CONTENT = """
(() => {
  if (globalThis.__meridianAssist) return;
  globalThis.__meridianAssist = { active: false };
  const mark = (name, value) => { document.documentElement.dataset[name] = value; };
  const send = (message) => chrome.runtime.sendMessage(message);
  // content scripts must not be able to read the credential: ask the browser for storage and record what happens
  chrome.storage.local.get(null).then(() => mark('stubStorage', 'open'), () => mark('stubStorage', 'blocked'));
  mark('stubInjected', String(Number(document.documentElement.dataset.stubInjected || 0) + 1));
  // the worker's own per-tab store (storage.session) must be as closed to page scripts as storage.local
  try {
    if (typeof chrome.storage.session === 'undefined') mark('stubSessionArea', 'absent');
    else chrome.storage.session.get(null).then(() => mark('stubSessionArea', 'open'), () => mark('stubSessionArea', 'blocked'));
  } catch { mark('stubSessionArea', 'blocked'); }
  // a submit waiting for its confirmation page (cross-origin follow-up): ask for what the worker kept, say what was kept
  const pendingDemo = async () => {
    if (location.search.includes('pending=1')) {
      const set = await send({ t: 'pending:set', record: { sid: 'smoke-sid', at: Date.now(), label: 'Submit', company: 'Acme', title: 'Engineer', identity: 'smoke-identity', hops: 0 } });
      mark('stubPendingSet', set.ok ? 'ok' : 'failed:' + set.error.code);
    }
    if (location.search.includes('follow=1') || location.search.includes('pending=1')) {
      const info = await send({ t: 'ctx' });
      mark('stubPendingSeen', info.ok && info.data.pending ? info.data.pending.sid + ':' + info.data.pending.hops : 'none');
    }
    if (location.search.includes('clear=1')) {
      const cleared = await send({ t: 'pending:clear' });
      const info = await send({ t: 'ctx' });
      mark('stubPendingCleared', cleared.ok && info.ok && !info.data.pending ? 'yes' : 'no');
    }
  };
  // what the real controller does after activation: open a session, ask for suggestions, keep ONE 20 s poll pending
  const assist = async () => {
    const ctx = await send({ t: 'ctx' });
    mark('stubCtx', ctx.ok ? ctx.data.connection.phase : 'failed:' + ctx.error.code);
    const opened = await send({ t: 'session:open', report: { url: location.href, title: document.title }, tabKey: 'k' + Math.random() });
    if (!opened.ok) { mark('stubSession', 'failed:' + opened.error.code); return; }
    const sid = opened.data.session_id;
    mark('stubSession', sid);
    const began = performance.now();
    const suggested = await send({ t: 'suggest', sessionId: sid, request: { elements: [], mode: 'scan' } });
    mark('stubSuggest', suggested.ok ? 'ok' : 'failed:' + suggested.error.code);
    mark('stubSuggestMs', String(Math.round(performance.now() - began)));
    let cursor = 0;
    while (globalThis.__meridianAssist.active) {
      const answer = await send({ t: 'poll', sessionId: sid, cursor, waitMs: 20000 });
      if (answer.ok) {
        cursor = answer.data.cursor;
        if (answer.data.events.length) mark('stubEvents', String(Number(document.documentElement.dataset.stubEvents || 0) + answer.data.events.length));
      } else {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
  };
  chrome.runtime.onMessage.addListener((message) => {
    if (message.t === 'conn:changed') { mark('stubConn', message.connection.phase); return false; }
    mark('stubLast', JSON.stringify(message));
    if (message.t === 'activate') {
      if (message.toggle && globalThis.__meridianAssist.active) {
        // the real controller: a visible panel means "switch off", and the page tells the worker
        globalThis.__meridianAssist.active = false;
        mark('stubActive', 'no');
        send({ t: 'activation', active: false });
      } else if (!globalThis.__meridianAssist.active) {
        globalThis.__meridianAssist.active = true;
        mark('stubActive', 'yes');
        void pendingDemo();
        if (location.search.includes('assist=1')) void assist();
      }
    }
    if (message.t === 'deactivate') { globalThis.__meridianAssist.active = false; mark('stubActive', 'no'); }
    return false;
  });
})();
"""

FORM = """<!doctype html><meta charset=utf-8><title>Apply</title>
<form><h1>Apply to Acme</h1>
<label>First name <input name=first_name></label><label>Last name <input name=last_name></label>
<label>Email <input type=email name=email></label><label>Phone <input type=tel name=phone></label>
<label>Cover letter <textarea name=cover></textarea></label><button type=button>Submit application</button></form>"""

SEARCH = """<!doctype html><meta charset=utf-8><title>Jobs</title>
<form><input type=search name=q><select name=dept><option>All</option></select><select name=loc><option>All</option></select></form>"""


class Bridge:
    """Minimal Meridian bridge: hello + proof, pairing, a few authenticated routes. Records every request."""

    def __init__(self) -> None:
        self.log: list[dict] = []
        self.clients: dict[str, dict] = {}
        self.pairings: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.events: dict[str, list] = {}
        self.latest = "0.1.0"
        self.port = 0
        self.lock = threading.Lock()
        self.hello_delay = 0.0
        self.active = 0  # requests being served right now (hello included)
        self.max_active = 0
        self.active_polls = 0  # multiplexed polls being held right now
        self.max_polls = 0
        self.poll_log: list[list[str]] = []  # the sessions of every multiplexed poll, in order
        self.per_session_polls = 0
        self.disconnects: list[str] = []
        self.deleted: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # noqa: D401 - silence
                return

            def _send(self, status: int, body: dict | None = None) -> None:
                raw = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.send_header("access-control-allow-origin", self.headers.get("origin", "*"))
                self.send_header("connection", "close")  # no keep-alive: stopping the server must really stop answering
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client gave up on a long poll that was replaced or cancelled: that is the point

            def _body(self) -> dict:
                length = int(self.headers.get("content-length") or 0)
                return json.loads(self.rfile.read(length) or b"{}") if length else {}

            def handle_any(self) -> None:
                with outer.lock:
                    outer.active += 1
                    outer.max_active = max(outer.max_active, outer.active)
                try:
                    self.route()
                finally:
                    with outer.lock:
                        outer.active -= 1

            def client_gone(self) -> bool:
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
                except OSError:
                    return True

            def route(self) -> None:
                path, _, query = self.path.partition("?")
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                body = self._body() if self.command in {"POST", "PATCH", "PUT"} else None
                entry = {
                    "method": self.command, "path": path, "origin": self.headers.get("origin"), "auth": self.headers.get("authorization"),
                    "version": self.headers.get("x-meridian-version"), "params": params, "body": body,
                }
                outer.log.append(entry)
                if path == "/v1/hello":
                    if outer.hello_delay:
                        time.sleep(outer.hello_delay)
                    reply = {"app": "Meridian", "bridge": 1, "app_version": "1.4.0", "latest_extension_version": outer.latest, "port": outer.port, "proof": None}
                    client = outer.clients.get(params.get("client", ""))
                    if client and not client["revoked"] and params.get("nonce"):
                        key = hashlib.sha256(client["secret"].encode()).digest()
                        msg = f"meridian-bridge-v1|{params['nonce']}|{outer.port}".encode()
                        reply["proof"] = base64.urlsafe_b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode().rstrip("=")
                    elif client and client["revoked"] and params.get("nonce"):
                        # like Meridian itself: a revoked client is told so with a proof only the holder of the secret can check
                        key = hashlib.sha256(client["secret"].encode()).digest()
                        msg = f"meridian-bridge-v1-revoked|{params['nonce']}|{outer.port}".encode()
                        reply["revoked"] = True
                        reply["revoked_proof"] = base64.urlsafe_b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode().rstrip("=")
                    return self._send(200, reply)
                if path == "/v1/pair/start" and self.command == "POST":
                    if not (entry["origin"] or "").startswith("chrome-extension://"):
                        return self._send(403, {"detail": "Origin required", "code": "origin"})
                    pid = base64.urlsafe_b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode().rstrip("=")
                    outer.pairings[pid] = {"state": "pending", "origin": entry["origin"]}
                    return self._send(200, {"pairing_id": pid, "code": "482913", "expires_in": 120, "poll_after_ms": 500})
                match = re.fullmatch(r"/v1/pair/([A-Za-z0-9_-]+)(/cancel)?", path)
                if match:
                    pairing = outer.pairings.get(match.group(1))
                    if not pairing:
                        return self._send(404, {"detail": "unknown", "code": "not_found"})
                    if match.group(2):
                        pairing["state"] = "expired"
                        return self._send(200, {"state": "expired"})
                    if pairing["state"] == "approved":
                        pairing["state"] = "claimed"
                        return self._send(200, {"state": "approved", "token": pairing["token"], "client_id": pairing["client_id"]})
                    return self._send(200, {"state": pairing["state"]})
                header = self.headers.get("authorization", "")
                token = re.fullmatch(r"Bearer mrd1\.([0-9a-f-]{36})\.([A-Za-z0-9_-]{43})", header)
                client = outer.clients.get(token.group(1)) if token else None
                if not client or client["revoked"] or client["secret"] != token.group(2):
                    return self._send(401, {"detail": "unauthorized", "code": "unauthorized"})
                if path == "/v1/status":
                    return self._send(200, {
                        "protocol": 1, "app_version": "1.4.0", "latest_extension_version": "0.1.0", "language": "auto",
                        "profile": {"verified_facts": 3, "has_cv": True, "display_name": "Test"}, "generation": {"available": False, "tested": False},
                        "settings": outer.settings,
                    })
                if path == "/v1/documents" and self.command == "GET":
                    return self._send(200, [
                        {"id": "doc-2.v1", "name": "Data CV", "filename": "data-cv.pdf", "mime": "application/pdf", "size": 1200, "kind": "cv", "version": 1, "default": False},
                        {"id": "doc-1.v3", "name": "General CV", "filename": "general-cv.pdf", "mime": "application/pdf", "size": 1500, "kind": "cv", "version": 3, "default": True},
                    ])
                if path == "/v1/settings" and self.command == "PATCH":
                    outer.settings.update(body or {})
                    return self._send(200, outer.settings)
                if path == "/v1/sessions" and self.command == "POST":
                    sid = uuid.uuid4().hex[:16]
                    outer.sessions[sid] = body
                    return self._send(200, {"session_id": sid, "protocol": 1, "knowledge_version": "k1", "settings": outer.settings, "cursor": 0})
                if path == "/v1/disconnect" and self.command == "POST":
                    client["revoked"] = True
                    outer.disconnects.append(token.group(1))
                    return self._send(200, {"disconnected": True})
                if path == "/v1/poll" and self.command == "POST":
                    asked = {item["session_id"]: int(item.get("cursor", 0)) for item in (body or {}).get("sessions", [])}
                    wanted = list(asked)
                    outer.poll_log.append(wanted)
                    with outer.lock:
                        outer.active_polls += 1
                        outer.max_polls = max(outer.max_polls, outer.active_polls)
                    try:
                        waited = 0.0
                        limit = min(25.0, float((body or {}).get("wait_ms", 0)) / 1000)
                        while waited < limit and not self.client_gone():
                            if any(len(outer.events.get(sid, [])) > asked[sid] for sid in wanted):
                                break
                            time.sleep(0.05)
                            waited += 0.05
                        pages = [{"session_id": sid, "status": "ok", "cursor": len(outer.events.get(sid, [])), "events": list(outer.events.get(sid, []))[asked[sid]:]}
                                 if sid in outer.sessions else {"session_id": sid, "status": "unknown", "cursor": asked[sid], "events": []} for sid in wanted]
                        return self._send(200, {"sessions": pages})
                    finally:
                        with outer.lock:
                            outer.active_polls -= 1
                match = re.fullmatch(r"/v1/sessions/([0-9a-f]+)", path)
                if match and self.command == "DELETE":
                    outer.sessions.pop(match.group(1), None)
                    outer.deleted.append(match.group(1))
                    return self._send(200, {"released": True})
                match = re.fullmatch(r"/v1/sessions/([0-9a-f]+)/(suggest|poll)", path)
                if match and self.command == "POST":
                    if match.group(2) == "poll":
                        outer.per_session_polls += 1
                    return self._send(200, {"knowledge_version": "k1", "results": [], "skipped": [], "cursor": 0, "events": []})
                return self._send(404, {"detail": "no route", "code": "not_found"})

            do_GET = do_POST = do_PATCH = do_DELETE = handle_any

        self.settings = {
            "enabled": True, "language": "auto", "auto_advance": True, "skip_filled": True, "tab_inserts_protected": False,
            "save_corrections": "ask", "background_research": True, "background_drafts": True, "web_lookup": True, "hint_on_ats": True,
            "draft_budget_usd": None, "always_on_sites": [],
        }
        self.server = RoomyServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def approve_latest(self) -> dict:
        pid = next(reversed(self.pairings))
        client_id = str(uuid.uuid4())
        secret = base64.urlsafe_b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes)[:SECRET_LEN].decode()
        self.clients[client_id] = {"secret": secret, "revoked": False}
        self.pairings[pid].update(state="approved", token=f"mrd1.{client_id}.{secret}", client_id=client_id)
        return {"client_id": client_id, "secret": secret, "token": f"mrd1.{client_id}.{secret}"}


class Pages:
    """Plain HTTP server for the local form pages."""

    def __init__(self) -> None:
        pages = {"/form.html": FORM, "/search.html": SEARCH, "/thanks.html": "<!doctype html><meta charset=utf-8><title>Thanks</title><h1>Thank you for applying</h1>"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def do_GET(self) -> None:
                body = pages.get(self.path.split("?")[0])
                self.send_response(200 if body else 404)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write((body or "not found").encode())

        self.server = RoomyServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def build(variant: str, out: Path) -> None:
    subprocess.run(["node", str(EXT / "build.mjs"), f"--variant={variant}", f"--out={out}"], check=True, capture_output=True, cwd=EXT)


class Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, condition: bool, label: str, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  ok    {label}")
        else:
            self.failed.append(f"{label} {detail}".strip())
            print(f"  FAIL  {label} {detail}")


async def wait_for(predicate, timeout: float = 10.0, interval: float = 0.1):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    return None


async def worker_of(ctx: BrowserContext, timeout: float = 15.0):
    async def find():
        return next((sw for sw in ctx.service_workers if sw.url.startswith("chrome-extension://")), None)

    sw = await wait_for(find, timeout)
    if sw is None:
        raise RuntimeError("the extension service worker did not start")
    return sw


async def launch(playwright, ext_dir: Path, profile: Path) -> BrowserContext:
    return await playwright.chromium.launch_persistent_context(
        str(profile), channel="chromium", headless=True, viewport={"width": 1100, "height": 900}, locale="en-US",
        args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}", "--lang=en-US"],
    )


async def send(page: Page, message: dict):
    return await page.evaluate("(m) => chrome.runtime.sendMessage(m)", message)


async def tab_id(sw, url_part: str) -> int:
    return await sw.evaluate("(part) => chrome.tabs.query({}).then(ts => ts.find(t => (t.url || '').includes(part))?.id)", url_part)


async def click_toolbar(sw, tid: int) -> None:
    await sw.evaluate("(id) => chrome.tabs.get(id).then(t => { chrome.action.onClicked.dispatch(t); })", tid)


async def badge(sw, tid: int) -> str:
    return await sw.evaluate("(id) => chrome.action.getBadgeText({ tabId: id })", tid)


async def main(shots: Path, keep: bool) -> int:
    check = Check()
    work = Path(tempfile.mkdtemp(prefix="meridian-ext-smoke-"))
    bridge, pages = Bridge(), Pages()
    bridge.start()
    errors: list[str] = []
    try:
        shots.mkdir(parents=True, exist_ok=True)
        e2e_dir, prod_dir = work / "e2e", work / "prod"
        build("e2e", e2e_dir)
        build("chrome", prod_dir)
        (e2e_dir / "content.js").write_text(STUB_CONTENT)  # isolate the worker from the in-page assistant under test elsewhere
        async with async_playwright() as playwright:
            ctx = await launch(playwright, e2e_dir, work / "profile-e2e")
            ctx.on("weberror", lambda error: errors.append(str(error.error)))
            try:
                sw = await worker_of(ctx)
                check.ok(sw.url == f"chrome-extension://{EXT_ID}/background.js", "service worker is up with the pinned extension id", sw.url)
                manifest = await sw.evaluate("() => chrome.runtime.getManifest()")
                check.ok(manifest["version"] == "0.1.0" and manifest["manifest_version"] == 3, "manifest version comes from tauri.conf.json")
                check.ok(await sw.evaluate("() => typeof globalThis.__e2e") == "object", "e2e build exposes the test hook")

                # -- test page on the extension origin (the build writes e2e.html next to the worker)
                hub = await ctx.new_page()
                await hub.goto(f"chrome-extension://{EXT_ID}/e2e.html")
                await send(hub, {"t": "e2e:seed", "reset": True, "ports": [bridge.port]})

                state = await send(hub, {"t": "conn:state", "refresh": True})
                check.ok(state["ok"] and state["data"]["phase"] == "unpaired", "unpaired bridge is reported as unpaired", json.dumps(state))
                hello = next(entry for entry in bridge.log if entry["path"] == "/v1/hello")
                check.ok(hello["version"] == "0.1.0" and hello["auth"] is None, "hello carries the extension version and no credential")

                # -- options page: welcome, unpaired state
                options = await ctx.new_page()
                await options.set_viewport_size({"width": 1000, "height": 1100})
                await options.goto(f"chrome-extension://{EXT_ID}/options.html?welcome=1")
                await options.get_by_role("heading", name="Welcome. You are three steps from your first application.").wait_for()
                await options.get_by_role("button", name="Connect", exact=True).wait_for()
                await options.screenshot(path=str(shots / "options-unpaired.png"), full_page=True)
                check.ok(await options.locator("#connection .status").inner_text() == "Connect this browser to Meridian.", "unpaired status text")
                unpaired_hint = await options.locator("#connection .hint").first.inner_text()
                check.ok(f"Meridian answered on port {bridge.port}" in unpaired_hint and "opens a window that asks for a 6-digit code" in unpaired_hint,
                         "before connecting the user is told which port answered and that Meridian will ask for a code", unpaired_hint)
                check.ok("no shared secret" in await options.locator("#diagnostics .diag-trust").inner_text(),
                         "the diagnostics say that the first connection cannot be proved")

                # -- two programs that both answer like Meridian: the extension will not choose, says so, and posts nothing to either
                second = Bridge()
                second.start()
                try:
                    pair_starts = sum(1 for entry in bridge.log if entry["path"] == "/v1/pair/start")
                    await send(hub, {"t": "e2e:seed", "ports": [bridge.port, second.port]})
                    await options.reload()
                    ambiguous = options.locator("#connection .hint").filter(has_text="More than one program on this Mac says it is Meridian")
                    await ambiguous.wait_for(timeout=15_000)
                    shown = await ambiguous.inner_text()
                    check.ok(str(bridge.port) in shown and str(second.port) in shown, "the options page names both ports that answered", shown)
                    check.ok(await options.locator("#connection [data-action=connect]").is_hidden() and await options.locator("#connection [data-action=retry]").is_visible(),
                             "Connect is not offered, Retry is")
                    refused = await send(hub, {"t": "pair:start"})
                    check.ok(not refused["ok"] and refused["error"]["code"] == "ambiguous", "pair:start is refused as ambiguous", json.dumps(refused)[:200])
                    check.ok(sum(1 for entry in bridge.log if entry["path"] == "/v1/pair/start") == pair_starts
                             and not any(entry["path"] == "/v1/pair/start" for entry in second.log), "no pairing request reached either program")
                    await options.select_option("#language", "es")
                    await options.locator("#connection .hint").filter(has_text="Más de un programa de este Mac dice ser Meridian").wait_for(timeout=10_000)
                    check.ok(True, "and says so in Spanish")
                    await options.select_option("#language", "en")
                finally:
                    await send(hub, {"t": "e2e:seed", "ports": [bridge.port]})
                    second.stop()
                await options.reload()
                await options.get_by_role("button", name="Connect", exact=True).wait_for(timeout=15_000)

                # -- pairing through the page: code shown, approved on the 'Meridian' side, connected state
                await options.get_by_role("button", name="Connect", exact=True).click()
                await options.locator(".code").filter(has_text="482 913").wait_for()
                start = next(entry for entry in bridge.log if entry["path"] == "/v1/pair/start")
                check.ok(start["origin"] == f"chrome-extension://{EXT_ID}" and start["auth"] is None, "pair/start carries the extension origin and no credential", json.dumps(start))
                check.ok(start["body"]["client_name"] == "Google Chrome" and start["body"]["extension_version"] == "0.1.0", "pair/start names the browser and version")
                trust = await options.locator("#connection .pair-trust").inner_text()
                check.ok(str(bridge.port) in trust and "Meridian itself must open a window asking for this code" in trust,
                         "the pairing box names the port that answered and says Meridian itself must open the code window", trust)
                await options.screenshot(path=str(shots / "options-pairing.png"), full_page=True)
                issued = bridge.approve_latest()
                approved_at = len(bridge.log)
                await options.locator("#connection .status").filter(has_text="Connected to Meridian").wait_for(timeout=15_000)
                check.ok(True, "pairing completes by itself when approved in Meridian (proof verified against Python HMAC)")
                after = bridge.log[approved_at:]
                with_auth = [entry for entry in after if entry["auth"]]
                hellos = [entry for entry in after if entry["path"] == "/v1/hello"]
                check.ok(bool(hellos) and all(entry["params"].get("nonce") and entry["params"].get("client") == issued["client_id"] for entry in hellos),
                         "every hello after pairing asks for a proof")
                first_auth = bridge.log.index(with_auth[0]) if with_auth else len(bridge.log)
                proofed = [entry for entry in bridge.log[:first_auth] if entry["path"] == "/v1/hello" and entry["params"].get("client") == issued["client_id"]]
                check.ok(bool(proofed), "a proof request preceded the first credentialed request")
                stored = await sw.evaluate("() => chrome.storage.local.get('meridian.connection')")
                check.ok(stored["meridian.connection"]["token"] == issued["token"], "the credential is stored in storage.local")
                page_text = await options.evaluate("() => document.documentElement.outerHTML")
                check.ok(issued["secret"] not in page_text and issued["token"] not in page_text, "the options page never contains the credential")
                await options.screenshot(path=str(shots / "options-connected.png"), full_page=True)

                # -- behaviour toggle goes through PATCH /v1/settings
                await options.locator("#sw-skip_filled").uncheck()
                await options.locator(".flash").filter(has_text="Saved").wait_for()
                patch = next(entry for entry in bridge.log if entry["method"] == "PATCH")
                check.ok(patch["body"] == {"skip_filled": False} and bool(patch["auth"]), "toggle patches only that setting, authenticated", json.dumps(patch["body"]))

                # -- CPL-20: the options page says which approved CV the assistant attaches by default
                await options.locator("#documents .docs li").first.wait_for(timeout=10_000)
                docs = await options.locator("#documents .docs li").all_inner_texts()
                check.ok(len(docs) == 2 and "data-cv.pdf" in docs[0] and "general-cv.pdf" in docs[1], "both approved CVs are listed", json.dumps(docs))
                check.ok("default" in docs[1] and "default" not in docs[0], "exactly the default one is marked", json.dumps(docs))
                check.ok(await options.locator("#documents .hint").last.is_hidden(), "the 'no CV yet' note is hidden while there are CVs")

                # -- UX-12 and CPL-18: the keys and colours are on the page, and Help is a section the panel can point at
                help_text = await options.locator("#help").inner_text()
                mac = await options.evaluate("() => /mac/i.test(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent)")
                alt = "Option" if mac else "Alt"  # the key names follow the platform the page is shown on
                for key in ("Tab", "Shift + Tab", "Enter", "Esc", f"{alt} + Z", f"{alt} + R", f"{alt} + Shift + M", f"{alt} + Shift + Z", "← ↑ ↓ →"):
                    check.ok(await options.locator("#help kbd", has_text=key).count() >= 1, f"the help lists the key {key}")
                chip_chord = "Control + Option + 1 to 7" if mac else "Alt + Shift + 1 to 7"
                check.ok(await options.locator("#help kbd", has_text=chip_chord).count() == 1, f"the help lists the chip actions' chord ({chip_chord})")
                check.ok("Verified" in help_text and "Check this" in help_text and "AI draft" in help_text, "the help explains the trust colours")
                check.ok(await options.locator("#help kbd").count() >= 13, "every key has a row, the start row included")
                start_row = options.locator("#help .key-row").filter(has_text="chrome://extensions/shortcuts")
                check.ok(await start_row.count() == 1 and "Safari" not in await start_row.inner_text(), "the start row points Chrome users at the shortcuts page")
                check.ok("Safari does not let extensions" not in help_text, "and says nothing about Safari in Chrome")
                docs_text = await options.locator("#documents").inner_text()
                check.ok("Settings → Automation → Default CV" in docs_text and "Your approved CVs. The first one is the default." in docs_text, "the CV section says where the default is chosen and what the list is")
                privacy_text = await options.locator("#privacy").inner_text()
                check.ok(await options.locator("#privacy li").count() == 7 and "writing model you chose" in privacy_text and "only the company name" in privacy_text,
                         "the privacy section says what can leave this Mac", privacy_text[:120])

                # -- language switch to Spanish renders Spanish
                await options.select_option("#language", "es")
                await options.get_by_role("heading", name="Conexión").wait_for()
                await options.screenshot(path=str(shots / "options-es.png"), full_page=True)
                check.ok("Conectado con Meridian" in await options.locator("#connection .status").inner_text(), "Spanish connection text")
                await options.select_option("#language", "en")

                # -- toolbar path on a normal page
                form = await ctx.new_page()
                await form.goto(f"http://127.0.0.1:{pages.port}/form.html")
                tid = await tab_id(sw, "/form.html")
                await click_toolbar(sw, tid)
                await form.locator("html[data-stub-active=yes]").wait_for(timeout=8000)
                check.ok(True, "toolbar click injects content.js and sends activate")
                check.ok(await badge(sw, tid) == "ON", "badge shows ON while connected", await badge(sw, tid))
                storage_access = await wait_for(lambda: form.evaluate("() => document.documentElement.dataset.stubStorage || null"), timeout=5)
                check.ok(storage_access == "blocked", "an injected content script cannot read extension storage (the credential)", str(storage_access))
                last = json.loads(await form.evaluate("() => document.documentElement.dataset.stubLast"))
                check.ok(last == {"t": "activate", "auto": False}, "activate message is exact", json.dumps(last))
                await click_toolbar(sw, tid)
                await form.locator("html[data-stub-active=no]").wait_for(timeout=8000)
                last = json.loads(await form.evaluate("() => document.documentElement.dataset.stubLast"))
                check.ok(last == {"t": "activate", "auto": False, "toggle": True}, "the second click ASKS the page to toggle (the worker decides nothing)", json.dumps(last))
                check.ok(await wait_for(lambda: _badge_is(sw, tid, ""), timeout=5) is not None, "the page switched itself off and told the worker: the badge follows", await badge(sw, tid))
                await click_toolbar(sw, tid)
                await form.locator("html[data-stub-active=yes]").wait_for(timeout=8000)
                last = json.loads(await form.evaluate("() => document.documentElement.dataset.stubLast"))
                check.ok(last == {"t": "activate", "auto": False}, "a click on a tab that is off starts it again, plainly", json.dumps(last))
                await click_toolbar(sw, tid)
                await form.locator("html[data-stub-active=no]").wait_for(timeout=8000)

                # -- content-script round trip with the worker (what the in-page assistant does first)
                ctx_reply = await form.evaluate("() => 1")  # keep the page alive
                check.ok(ctx_reply == 1, "page alive")
                ctx_info = await send(hub, {"t": "ctx"})
                check.ok(ctx_info["ok"] and ctx_info["data"]["connection"]["phase"] == "ready" and ctx_info["data"]["e2e"] is True, "ctx reports the ready connection")
                check.ok(issued["token"] not in json.dumps(ctx_info), "ctx never contains the credential")
                opened = await send(hub, {"t": "session:open", "report": {"url": "http://x/", "title": "t"}, "tabKey": "k"})
                check.ok(opened["ok"] and opened["data"]["session_id"], "session:open reaches the bridge with the bearer token")
                bearer = [entry for entry in bridge.log if entry["path"] == "/v1/sessions"][0]
                check.ok(bearer["auth"] == f"Bearer {issued['token']}" and bearer["origin"] == f"chrome-extension://{EXT_ID}", "POST carries bearer + extension origin")

                # -- every credentialed request is preceded by a hello proof of its own (no 2 s window any more)
                before_status = len(bridge.log)
                for _ in range(5):
                    answer = await send(hub, {"t": "status"})
                    check.ok(answer["ok"], "status answers")
                kinds = [("hello" if e["path"] == "/v1/hello" else "authed" if e["auth"] else "other") for e in bridge.log[before_status:]]
                gaps_ok = all("hello" in kinds[a + 1:b] for a, b in zip([i for i, k in enumerate(kinds) if k == "authed"], [i for i, k in enumerate(kinds) if k == "authed"][1:]))
                nonces = [e["params"].get("nonce") for e in bridge.log[before_status:] if e["path"] == "/v1/hello"]
                check.ok(kinds.count("authed") == 5 and gaps_ok and len(set(nonces)) == len(nonces) >= 5,
                         "five requests in a row: a hello with a fresh nonce before EACH credentialed request", " ".join(kinds))

                # -- a submit waiting for its confirmation follows the tab to ANOTHER origin (worker-side store, trusted contexts only)
                origin_a = f"http://127.0.0.1:{pages.port}"
                origin_b = f"http://localhost:{pages.port}"
                follow = await ctx.new_page()
                await follow.goto(f"{origin_a}/form.html?pending=1&n=follow")
                follow_id = await tab_id(sw, "n=follow")
                await click_toolbar(sw, follow_id)
                await follow.locator("html[data-stub-pending-set]").wait_for(state="attached", timeout=8000)
                check.ok(await follow.evaluate("() => document.documentElement.dataset.stubPendingSet") == "ok", "the page stored its pending submit in the worker")
                await follow.locator("html[data-stub-pending-seen]").wait_for(state="attached", timeout=8000)
                check.ok(await follow.evaluate("() => document.documentElement.dataset.stubPendingSeen") == "smoke-sid:0", "and gets it back from ctx")
                check.ok(await wait_for(lambda: follow.evaluate("() => document.documentElement.dataset.stubSessionArea || null"), timeout=5) in {"absent", "blocked"},
                         "a page script cannot read the worker's storage.session either")
                check.ok(await sw.evaluate("(id) => chrome.storage.session.get('meridian.pending.' + id).then(r => !!r['meridian.pending.' + id])", follow_id),
                         "the record lives in storage.session, per tab")
                await follow.goto(f"{origin_b}/thanks.html?follow=1&n=follow")
                await follow.locator("html[data-stub-pending-seen]").wait_for(state="attached", timeout=12_000)
                check.ok(await follow.evaluate("() => document.documentElement.dataset.stubPendingSeen") == "smoke-sid:0",
                         "on another origin the worker started the assistant by itself and hands the pending submit to the new page")
                last_follow = json.loads(await follow.evaluate("() => document.documentElement.dataset.stubLast"))
                check.ok(last_follow == {"t": "activate", "auto": True}, "re-activated with auto=true", json.dumps(last_follow))
                await follow.goto(f"{origin_b}/thanks.html?clear=1&n=follow")
                await follow.locator("html[data-stub-pending-cleared]").wait_for(state="attached", timeout=12_000)
                check.ok(await follow.evaluate("() => document.documentElement.dataset.stubPendingCleared") == "yes", "pending:clear forgets it")
                await follow.close()
                check.ok(await wait_for(lambda: sw.evaluate("(id) => chrome.storage.session.get('meridian.pending.' + id).then(r => !r['meridian.pending.' + id])", follow_id), timeout=5) is not None,
                         "nothing is left for that tab")

                # -- a slow first connection check: calm "connecting", then the real answer, never an error
                await send(hub, {"t": "e2e:seed", "ports": [bridge.port]})  # forgets the cached connection: the next check is a first look
                bridge.hello_delay = 3.0
                slow = await ctx.new_page()
                await slow.goto(f"http://127.0.0.1:{pages.port}/form.html?assist=1&n=slow")
                await click_toolbar(sw, await tab_id(sw, "n=slow"))
                await slow.locator("html[data-stub-ctx]").wait_for(state="attached", timeout=6000)
                first_phase = await slow.evaluate("() => document.documentElement.dataset.stubCtx")
                check.ok(first_phase == "connecting", "a first check slower than 1.5 s is reported as connecting, not as an error", first_phase)
                await slow.locator("html[data-stub-conn=ready]").wait_for(state="attached", timeout=9000)
                check.ok(True, "and the real state (ready) is pushed to the page when Meridian answers")
                bridge.hello_delay = 0.0
                await slow.close()

                # -- ten assisted tabs: ONE long poll, instant suggestions, never more than 4 requests at the bridge
                bridge.max_active = bridge.max_polls = 0
                bridge.poll_log.clear()
                bridge.per_session_polls = 0
                bridge.deleted.clear()
                tabs = []
                for index in range(10):
                    page = await ctx.new_page()
                    await page.goto(f"http://127.0.0.1:{pages.port}/form.html?assist=1&n={index}")
                    await click_toolbar(sw, await tab_id(sw, f"n={index}"))
                    await page.locator("html[data-stub-suggest]").wait_for(state="attached", timeout=15_000)
                    tabs.append(page)
                outcomes = [await page.evaluate("() => document.documentElement.dataset.stubSuggest") for page in tabs]
                check.ok(all(outcome == "ok" for outcome in outcomes), "every assisted tab got its suggestions through the worker", json.dumps(outcomes))
                last_ms = int(await tabs[-1].evaluate("() => document.documentElement.dataset.stubSuggestMs"))
                check.ok(last_ms < 3000, f"the 10th assisted tab's first suggestion arrived in {last_ms} ms (limit 3000)")
                await asyncio.sleep(1.0)
                check.ok(bridge.active_polls == 1, "ten waiting tabs hold exactly ONE poll connection", str(bridge.active_polls))
                check.ok(bridge.per_session_polls == 0, "no per-session poll was ever made")
                check.ok(len(bridge.poll_log[-1]) == 10 if bridge.poll_log else False, "the shared poll covers all ten sessions", str([len(item) for item in bridge.poll_log]))
                check.ok(bridge.max_active <= 5, f"never more than 4 requests plus the hello probe in flight at the bridge (saw {bridge.max_active})")
                session_3 = await tabs[3].evaluate("() => document.documentElement.dataset.stubSession")
                bridge.events[session_3] = [{"type": "knowledge", "knowledge_version": "k9"}]
                try:
                    await tabs[3].locator("html[data-stub-events='1']").wait_for(state="attached", timeout=8000)
                except Exception:
                    print("DEBUG dataset", await tabs[3].evaluate("() => JSON.stringify(document.documentElement.dataset)"), "polls", bridge.active_polls, bridge.poll_log[-1:], bridge.events.keys())
                    raise
                others = [await page.evaluate("() => document.documentElement.dataset.stubEvents || ''") for index, page in enumerate(tabs) if index != 3]
                check.ok(all(value == "" for value in others), "an event reaches the page whose session it belongs to, and only that page", json.dumps(others))
                for page in tabs:
                    await page.close()
                released = await wait_for(lambda: _released(bridge, 10), timeout=10)
                check.ok(released is not None, "closing the tabs released their sessions at the bridge", str(len(bridge.deleted)))
                check.ok(await wait_for(lambda: _no_polls(bridge), timeout=5) is not None, "and no poll is left open once nobody waits")

                # -- Disconnect: Meridian drops this browser too, then the credential is forgotten here
                await options.reload()
                await options.get_by_role("button", name="Disconnect this browser").click()
                await options.locator("#connection .hint").filter(has_text="Meridian no longer lists this browser").wait_for(timeout=10_000)
                check.ok(bridge.disconnects == [issued["client_id"]], "disconnect revoked exactly this browser at Meridian", json.dumps(bridge.disconnects))
                revoke_call = next(entry for entry in bridge.log if entry["path"] == "/v1/disconnect")
                check.ok(revoke_call["auth"] == f"Bearer {issued['token']}" and revoke_call["origin"] == f"chrome-extension://{EXT_ID}", "the revocation carries the bearer and the extension origin")
                check.ok((await sw.evaluate("() => chrome.storage.local.get('meridian.connection')")) == {}, "the credential is gone from storage")
                await options.locator("#connection .status").filter(has_text="Connect this browser to Meridian").wait_for()
                await options.get_by_role("button", name="Connect", exact=True).click()
                await options.locator(".code").filter(has_text="482 913").wait_for()
                issued = bridge.approve_latest()
                await options.locator("#connection .status").filter(has_text="Connected to Meridian").wait_for(timeout=15_000)
                check.ok(True, "connecting again after a disconnect works")

                # -- the vault: a browser that cannot restrict storage.local keeps only ciphertext there (Safari today)
                await sw.evaluate("""() => {
                  Object.defineProperty(chrome.storage.local, 'setAccessLevel', { value: undefined, configurable: true, writable: true });
                  globalThis.__e2e.resetPlacement();
                }""")
                await options.reload()
                await options.get_by_role("button", name="Disconnect this browser").click()
                await options.locator("#connection .status").filter(has_text="Connect this browser to Meridian").wait_for(timeout=10_000)
                await options.get_by_role("button", name="Connect", exact=True).click()
                await options.locator(".code").filter(has_text="482 913").wait_for()
                issued = bridge.approve_latest()
                await options.locator("#connection .status").filter(has_text="Connected to Meridian").wait_for(timeout=15_000)
                stored = await sw.evaluate("() => chrome.storage.local.get(null)")
                record = stored.get("meridian.connection") or {}
                check.ok("token" not in record and "sealed" in record and record.get("clientId") == issued["client_id"],
                         "without setAccessLevel only a sealed record is written to storage.local", json.dumps(record)[:200])
                check.ok(issued["secret"] not in json.dumps(stored) and issued["token"] not in json.dumps(stored), "the secret is nowhere in storage.local")
                status = await send(hub, {"t": "status"})
                check.ok(status["ok"] and status["data"]["app_version"] == "1.4.0", "the worker opens the sealed credential (real IndexedDB + WebCrypto) and the bridge accepts it", json.dumps(status)[:200])
                await sw.evaluate("() => chrome.storage.local.get(null).then(() => 0)")

                # -- restricted page: the options page explains instead of failing silently
                before_pages = len(ctx.pages)
                restricted = await ctx.new_page()
                await restricted.goto("chrome://version")
                rtid = await tab_id(sw, "chrome://version")
                if rtid is None:  # url hidden without the tabs permission: use the page the worker can see by id
                    rtid = await sw.evaluate("() => chrome.tabs.query({active: true, currentWindow: true}).then(ts => ts[0]?.id)")
                await click_toolbar(sw, rtid)
                reason = await wait_for(lambda: _options_reason(ctx), timeout=8)
                check.ok(reason == "restricted", "chrome:// pages open the options page with reason=restricted", str(reason))
                if reason:
                    opts = next(page for page in ctx.pages if "options.html" in page.url and "reason=restricted" in page.url)
                    await opts.get_by_text("Meridian can't work on that page").wait_for()
                    await opts.screenshot(path=str(shots / "options-restricted.png"), full_page=True)
                    check.ok(True, "the restricted-page explanation is visible")
                del before_pages

                # -- ATS hint on a (routed) Greenhouse page
                async def greenhouse(route):
                    await route.fulfill(status=200, content_type="text/html", body=FORM)

                async def greenhouse_search(route):
                    await route.fulfill(status=200, content_type="text/html", body=SEARCH)

                await ctx.route("https://boards.greenhouse.io/acme/jobs/1", greenhouse)
                await ctx.route("https://boards.greenhouse.io/acme", greenhouse_search)
                ats = await ctx.new_page()
                await ats.goto("https://boards.greenhouse.io/acme/jobs/1")
                pill = ats.locator("[data-meridian-hint] button.main")
                await pill.wait_for(timeout=10_000)
                check.ok("Meridian is ready" in await pill.inner_text() or "click to assist" in await pill.inner_text(), "the hint pill appears on an application form")
                await asyncio.sleep(0.5)  # let the 180 ms fade-in finish before the picture
                await ats.screenshot(path=str(shots / "hint-pill.png"), clip={"x": 600, "y": 780, "width": 500, "height": 120})
                atid = await tab_id(sw, "greenhouse.io/acme/jobs/1")
                check.ok(await wait_for(lambda: _badge_is(sw, atid, "●"), timeout=5) is not None, "the toolbar gets the green dot")
                search = await ctx.new_page()
                await search.goto("https://boards.greenhouse.io/acme")
                await asyncio.sleep(2.6)
                check.ok(await search.locator("[data-meridian-hint]").count() == 0, "a search form is not mistaken for an application")
                await pill.click()
                await ats.locator("html[data-stub-active=yes]").wait_for(timeout=8000)
                check.ok(await ats.locator("[data-meridian-hint]").count() == 0, "clicking the pill starts the assistant and removes the pill")
                check.ok(await badge(sw, atid) == "ON", "badge flips from the dot to ON")

                # -- hide the hint for the site (turn the assistant off first: while it runs the pill is not needed)
                await click_toolbar(sw, atid)
                await ats.locator("html[data-stub-active=no]").wait_for(timeout=8000)
                await ats.reload()
                await pill.wait_for(timeout=10_000)
                await ats.locator("[data-meridian-hint] button.close").click()
                prefs = await wait_for(lambda: _hint_off(sw), timeout=5)
                check.ok(prefs == ["boards.greenhouse.io"], "the close button hides the hint on this site", str(prefs))
                await ats.reload()
                await asyncio.sleep(2.6)
                check.ok(await ats.locator("[data-meridian-hint]").count() == 0, "the hint stays hidden after a reload")
                await options.reload()
                await options.get_by_role("heading", name="Site rules").wait_for()
                check.ok(await options.locator("#sites .host", has_text="boards.greenhouse.io").count() == 1, "the options page lists the hidden site")
                await options.screenshot(path=str(shots / "options-sites.png"), full_page=True)

                # -- a permission notice, input validation and the update banner
                perm = await ctx.new_page()
                await perm.goto(f"chrome-extension://{EXT_ID}/options.html?reason=permission&site=careers.acme.com#permissions")
                await perm.get_by_text("needs your permission to work on careers.acme.com").wait_for()
                await perm.get_by_role("button", name="Allow careers.acme.com").wait_for()
                check.ok(True, "the permission notice names the site and offers a one-click allow")
                await perm.close()

                # -- UX-13: stored rules say whether they work, and the small window answers the question in context
                await sw.evaluate("""async () => {
                  const all = await chrome.storage.local.get('meridian.prefs');
                  const prefs = all['meridian.prefs'] || {};
                  prefs.alwaysOn = ['jobs.stale.example'];
                  await chrome.storage.local.set({ 'meridian.prefs': prefs });
                }""")
                bridge.settings["always_on_sites"] = ["careers.fromapp.example"]  # chosen in Meridian's own settings (the bridge is the authority)
                await options.reload()
                stale = options.locator("#sites li[data-host='jobs.stale.example']")
                await stale.wait_for()
                check.ok("Needs your permission" in await stale.inner_text(), "a rule stored without its permission says so")
                check.ok(await stale.get_by_role("button", name="Allow jobs.stale.example").count() == 1, "and offers the one-click allow")
                check.ok(await stale.get_by_role("button", name="Remove jobs.stale.example").count() == 1, "and can be removed")
                from_app = options.locator("#sites li[data-host='careers.fromapp.example']")
                check.ok("Set in Meridian" in await from_app.inner_text() and "Needs your permission" in await from_app.inner_text(), "a site chosen in Meridian is listed, flagged, and not removable here")
                check.ok(await from_app.get_by_role("button", name="Remove careers.fromapp.example").count() == 0, "(the app owns that list)")
                await options.screenshot(path=str(shots / "options-sites-needing-permission.png"), full_page=True)
                ctx_info = await send(hub, {"t": "ctx"})
                check.ok(ctx_info["ok"] is False or ctx_info["data"]["prefs"]["alwaysOn"] == [], "a page is told none of those rules is in effect")

                # the worker's side: the permission the (stubbed) browser grants makes the rule real
                await sw.evaluate("""() => {
                  const real = chrome.permissions.contains.bind(chrome.permissions);
                  globalThis.__realContains = real;
                  chrome.permissions.contains = async (query) => ((query.origins || []).length && query.origins.every((o) => o.includes('careers.acme.com'))) ? true : real(query);
                }""")
                popup = await ctx.new_page()
                await popup.add_init_script("chrome.permissions.request = async () => true;")  # the native prompt cannot be answered here
                await popup.goto(f"chrome-extension://{EXT_ID}/options.html?reason=permission&site=careers.acme.com&intent=always-on&popup=1")
                await popup.get_by_text("needs your permission to start automatically on careers.acme.com").wait_for()
                shown = [await popup.locator(selector).is_visible() for selector in ("h1", "#connection", "#behaviour", "#privacy", "#diagnostics")]
                check.ok(not any(shown), "the small window shows the question and nothing else", json.dumps(shown))
                check.ok(await popup.locator("button[data-action=allow-notice]").evaluate("el => document.activeElement === el"), "Allow has the focus: Enter answers at once")
                await popup.screenshot(path=str(shots / "options-popup-question.png"))
                await popup.get_by_role("button", name="Allow careers.acme.com").click()
                await popup.get_by_text("Meridian now starts by itself on careers.acme.com").wait_for()
                stored_rules = await wait_for(lambda: _always_on_has(sw, "careers.acme.com"), timeout=20)
                check.ok(stored_rules is not None, "the rule is stored once the browser has agreed", str(stored_rules))
                closed = await wait_for(lambda: _is_closed(popup), timeout=25)
                check.ok(closed is not None, "and the small window closes itself: the application tab was never left")
                await sw.evaluate("() => { chrome.permissions.contains = globalThis.__realContains; }")
                refused = await ctx.new_page()
                await refused.add_init_script("chrome.permissions.request = async () => false;")
                await refused.goto(f"chrome-extension://{EXT_ID}/options.html?reason=permission&site=careers.nope.org&intent=always-on&popup=1")
                await refused.get_by_role("button", name="Allow careers.nope.org").click()
                await refused.get_by_text("did not give Meridian access to careers.nope.org. Nothing was changed.").wait_for()
                check.ok(await _always_on_has(sw, "careers.nope.org") is None, "a refusal stores nothing")
                await refused.close()
                bridge.settings["always_on_sites"] = []
                await sw.evaluate("""async () => {
                  const all = await chrome.storage.local.get('meridian.prefs');
                  const prefs = all['meridian.prefs'] || {};
                  prefs.alwaysOn = [];
                  await chrome.storage.local.set({ 'meridian.prefs': prefs });
                }""")
                await options.reload()
                await options.fill("#add-alwaysOn", "not a site")
                await options.get_by_role("button", name="Add and allow").click()
                await options.get_by_text("Enter a website such as jobs.acme.com.").wait_for()
                check.ok(True, "an invalid site is refused with a clear message")
                bridge.latest = "9.9.9"
                await options.reload()
                await options.get_by_text("An update is ready (version 9.9.9)").wait_for(timeout=10_000)
                check.ok(await options.get_by_role("button", name="Reload extension").is_visible(), "an available update offers a reload button")
                bridge.latest = "0.1.0"

                # -- revocation on the Meridian side: calm, specific, and no credential sent afterwards
                bridge.clients[issued["client_id"]]["revoked"] = True
                revoked_at = len(bridge.log)
                state = await send(hub, {"t": "conn:state", "refresh": True})
                check.ok(state["data"]["phase"] == "revoked", "a revoked client is reported as revoked", json.dumps(state) + json.dumps(bridge.log[-4:]))
                authed_after = [entry for entry in bridge.log[revoked_at:] if entry["auth"]]
                check.ok(not authed_after, "no credential was sent to a bridge that gave no proof")
                await options.reload()
                await options.locator("#connection .status").filter(has_text="disconnected this browser").wait_for()
                await options.screenshot(path=str(shots / "options-revoked.png"), full_page=True)

                # -- no bridge at all
                bridge.stop()
                state = await send(hub, {"t": "conn:state", "refresh": True})
                check.ok(state["data"]["phase"] == "no_bridge", "no listener -> no_bridge", json.dumps(state))
                await options.reload()
                await options.locator("#connection .status").filter(has_text="Meridian isn't open.").wait_for()
                await options.screenshot(path=str(shots / "options-no-bridge.png"), full_page=True)

                # -- phone width and dark scheme
                await options.set_viewport_size({"width": 375, "height": 812})
                await options.emulate_media(color_scheme="dark")
                await options.reload()
                await options.get_by_role("heading", name="Connection").wait_for()
                overflow = await options.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
                check.ok(not overflow, "no horizontal scroll at phone width")
                await options.screenshot(path=str(shots / "options-phone-dark.png"), full_page=True)
                check.ok(not errors, "no uncaught exceptions in any extension page or the worker", "; ".join(errors))
            finally:
                await ctx.close()

            # -- the production build: no hooks, same page renders
            ctx = await launch(playwright, prod_dir, work / "profile-prod")
            try:
                sw = await worker_of(ctx)
                welcome = await wait_for(lambda: _welcome_page(ctx), timeout=8)
                check.ok(welcome is not None, "installing opens the welcome page by itself")
                if welcome:
                    await welcome.get_by_role("heading", name="Welcome. You are three steps from your first application.").wait_for()
                    await welcome.screenshot(path=str(shots / "welcome-production.png"), full_page=False)
                check.ok(await sw.evaluate("() => typeof globalThis.__e2e") == "undefined", "production worker has no test hook")
                page = await ctx.new_page()
                await page.goto(f"chrome-extension://{EXT_ID}/options.html")
                await page.get_by_role("heading", name="Connection").wait_for()
                reply = await page.evaluate("(m) => chrome.runtime.sendMessage(m)", {"t": "e2e:seed", "reset": True})
                check.ok(reply["ok"] is False and reply["error"]["code"] == "invalid", "production worker rejects the test message", json.dumps(reply))
                check.ok(not (await page.evaluate("() => fetch('e2e.html').then(r => r.ok).catch(() => false)")), "no e2e page in production")
            finally:
                await ctx.close()
    finally:
        bridge.stop() if bridge.thread.is_alive() else None
        pages.stop()
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    print(f"\n{check.passed} checks passed, {len(check.failed)} failed")
    for item in check.failed:
        print(f"  FAILED: {item}")
    return 1 if check.failed else 0


async def _options_reason(ctx: BrowserContext):
    for page in ctx.pages:
        if "options.html" in page.url:
            match = re.search(r"reason=(\w+)", page.url)
            if match:
                return match.group(1)
    return None


async def _welcome_page(ctx: BrowserContext):
    return next((page for page in ctx.pages if "options.html?welcome=1" in page.url), None)


async def _released(bridge: Bridge, count: int):
    return len(bridge.deleted) >= count or None


async def _no_polls(bridge: Bridge):
    return bridge.active_polls == 0 or None


async def _badge_is(sw, tid: int, text: str):
    return (await badge(sw, tid)) == text


async def _always_on_has(sw, host: str):
    stored = await sw.evaluate("() => chrome.storage.local.get('meridian.prefs')")
    hosts = (stored.get("meridian.prefs") or {}).get("alwaysOn") or []
    return hosts if host in hosts else None


async def _is_closed(page):
    return True if page.is_closed() else None


async def _hint_off(sw):
    stored = await sw.evaluate("() => chrome.storage.local.get('meridian.prefs')")
    hosts = (stored.get("meridian.prefs") or {}).get("hintOff") or []
    return hosts or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-shots", default="", help="directory for the screenshots (default: a temp dir, removed afterwards)")
    args = parser.parse_args()
    shots_dir = Path(args.keep_shots) if args.keep_shots else Path(tempfile.mkdtemp(prefix="meridian-ext-shots-"))
    try:
        code = asyncio.run(main(shots_dir, keep=bool(args.keep_shots)))
    finally:
        if not args.keep_shots:
            shutil.rmtree(shots_dir, ignore_errors=True)  # also when the run crashed: nothing is left behind
    sys.exit(code)
