"""Content-script test rig: fixture servers on two origins, a mock extension background and Playwright helpers.

The content bundle (`extension/dist/e2e/content.js`) is injected into every frame as a script together with a mock
`chrome.runtime`. Every runtime message goes through a Playwright binding to the Python `Bg` below, which answers with
canned bridge data on a later macrotask (like real extension IPC), records everything it receives and relays frame
payloads between the real frames of the page. Tests then press REAL keys with `page.keyboard`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Frame, Page, async_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
EXT = REPO / "extension"
# MERIDIAN_CONTENT_JS: a private build (node build.mjs --variant=e2e --out=<dir>) so parallel work never races on dist/e2e
CONTENT_JS = Path(os.environ["MERIDIAN_CONTENT_JS"]) if os.environ.get("MERIDIAN_CONTENT_JS") else EXT / "dist" / "e2e" / "content.js"
MOCK_JS = HERE / "mock_chrome.js"
PROBE_JS = HERE / "probe.js"
FIXTURE_DIRS = [HERE / "fixtures", REPO / "backend" / "tests" / "browser" / "fixtures"]
CHILD_FRAME_BASE = 34359738369  # Safari-sized frame ids: the keys must be treated as opaque integers


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^\w]+", " ", text).strip()


# ---------------------------------------------------------------------------------------------------- servers


class FixtureServer:
    """Serves the fixture directories; `{{MAIN}}` / `{{ALT}}` in HTML are replaced by the two origins."""

    replacements: dict[str, str] = {}

    def __init__(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0].lstrip("/") or "index.html"
                if path == "favicon.ico":
                    # Chromium asks for it once per page, at a moment nobody controls: a 404 is a console error that lands in whichever
                    # scenario watches the console by then (a race, never a fault of the page under test). No content, no error.
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                vendor = os.environ.get("MERIDIAN_REACT_UMD_DIR")
                if vendor and path.startswith("vendor/"):  # optional: real React/ReactDOM UMD builds for the real-React scenario
                    candidate = Path(vendor) / path[len("vendor/"):]
                    if candidate.is_file() and candidate.suffix == ".js":
                        body = candidate.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/javascript")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                for folder in FIXTURE_DIRS:
                    candidate = (folder / path).resolve()
                    if folder.resolve() in candidate.parents and candidate.is_file():
                        body = candidate.read_bytes()
                        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(candidate.suffix, "application/octet-stream")
                        if candidate.suffix == ".html":
                            text = body.decode("utf-8")
                            for key, value in owner.replacements.items():
                                text = text.replace(key, value)
                            body = text.encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                self.send_error(404)

        class QuietServer(ThreadingHTTPServer):
            # The default listen backlog is 5: a loaded machine (a full test run next to other work) then RESETS connections of the many
            # script and chunk requests one page makes, and the page comes up empty (TEST-01).
            request_queue_size = 256

            def handle_error(self, request: Any, client_address: Any) -> None:  # browsers abort connections routinely
                pass

        self.httpd = QuietServer(("127.0.0.1", 0), Handler)  # port 0: the OS picks a free port
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class Servers:
    def __init__(self) -> None:
        self.main = FixtureServer()
        self.alt = FixtureServer()
        self.main_url = f"http://127.0.0.1:{self.main.port}"
        self.alt_url = f"http://localhost:{self.alt.port}"
        reps = {"{{MAIN}}": self.main_url, "{{ALT}}": self.alt_url}
        FixtureServer.replacements = reps

    def url(self, path: str, host: str = "main") -> str:
        return f"{self.main_url if host == 'main' else self.alt_url}/{path.lstrip('/')}"

    def close(self) -> None:
        self.main.close()
        self.alt.close()


# ---------------------------------------------------------------------------------------------------- bridge data


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def err(code: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "detail": detail or code, **extra}}


DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True, "language": "auto", "auto_advance": True, "skip_filled": True, "tab_inserts_protected": False,
    "save_corrections": "ask", "background_research": True, "background_drafts": True, "web_lookup": False,
    "hint_on_ats": True, "draft_budget_usd": None, "always_on_sites": [],
}


def sug(
    el: dict[str, Any], req: dict[str, Any], *, state: str = "VERIFIED", insert: str | None = None, op: str | None = None,
    text: str | None = None, option: dict[str, str] | None = None, labels: list[str] | None = None, checked: bool | None = None,
    search_text: str | None = None, document: dict[str, Any] | None = None, flags: dict[str, Any] | None = None,
    needs: dict[str, Any] | None = None, why: dict[str, Any] | None = None, generation: dict[str, Any] | None = None,
    kv: str = "kv1", display: str | None = None, request_id: str | None = None, question_id: str | None = None,
) -> dict[str, Any]:
    if insert is None:
        insert = "tab" if state in ("VERIFIED", "INFERRED", "GENERATED_DRAFT") else "never"
    value = None
    if op:
        shown = display
        if shown is None:
            shown = text if text is not None else (option or {}).get("label") or ", ".join(labels or []) or (
                (document or {}).get("filename") or ("yes" if checked else "no"))
        value = {"display": shown, "operation": op, "text": text, "option": option, "labels": labels, "checked": checked,
                 "search_text": search_text, "document": document}
    label = el.get("context") or el.get("label") or ""
    default_why = {"code": {"VERIFIED": "profile_fact", "INFERRED": "derived", "GENERATED_DRAFT": "generated", "CONSENT": "consent",
                            "SENSITIVE_BLANK": "sensitive_blank", "NOT_APPLICABLE": "not_applicable"}.get(state, "missing_fact"),
                   "summary": f"{state} for {label}"}
    return {
        "node_id": el["node_id"], "frame_key": req.get("frame_key", "top"), "question": label,
        "question_id": question_id or f"q:{req.get('frame_key', 'top')}:{el['node_id']}",
        "request_id": request_id or f"r{next(_request_ids)}", "knowledge_version": kv, "state": state, "insert": insert,
        "suggestion": value, "confidence": {"value": 1, "level": "EXACT"} if state == "VERIFIED" else None,
        "why": why or default_why, "flags": flags or {}, "needs": needs, "generation": generation, "based_on": None,
    }


def _counter():
    n = 0
    while True:
        n += 1
        yield n


_request_ids = _counter()


# Answer specs: kwargs for sug(); a scenario maps a normalised label to one of these.
def T(text: str, **kw: Any) -> dict[str, Any]:
    return {"op": "TYPE_TEXT", "text": text, **kw}


def SELECT(label: str, value: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"op": "SELECT_OPTION", "option": {"label": label, "value": value if value is not None else label}, **kw}


def RADIO(label: str, value: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"op": "CHECK_RADIO", "option": {"label": label, "value": value if value is not None else label}, **kw}


def BOX(checked: bool = True, **kw: Any) -> dict[str, Any]:
    return {"op": "SET_CHECKBOX", "checked": checked, **kw}


def BOXES(*labels: str, **kw: Any) -> dict[str, Any]:
    return {"op": "SET_CHECKBOXES", "labels": list(labels), **kw}


def COMBO(label: str, search: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"op": "OPEN_AND_SELECT", "option": {"label": label, "value": label}, "search_text": search, **kw}


def FILE(doc: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"op": "ATTACH_FILE", "document": doc, **kw}


def NEEDS(code: str = "missing_fact", message: str = "Not in your profile yet.", **kw: Any) -> dict[str, Any]:
    return {"state": "NEEDS_YOU", "needs": {"root_cause": "MISSING_FACT", "code": code, "message": message, "actions": [{"type": "answer_here"}]}, **kw}


def STATE(state: str, **kw: Any) -> dict[str, Any]:
    return {"state": state, **kw}


def snapshot(run_id: str = "run1", state: str = "done", *, version: int = 1, company: str = "Example Company") -> dict[str, Any]:
    """A minimal research snapshot, as the bridge sends it (session reply, `research` events and the user's corrections)."""
    return {"run_id": run_id, "state": state, "version": version, "ready_for_answers": state in ("done", "partial"), "quality": "full",
            "stale": False, "headline": f"Researched {company}", "needs": None, "company": {"name": company, "key": company.lower()},
            "site": None, "role": None, "brief": "", "brief_source": None, "stages": [], "sources": [], "notes": [], "updated_at": ""}


def make_document(filename: str = "Alex-CV.pdf", mime: str = "application/pdf", payload: bytes | None = None) -> dict[str, Any]:
    data = payload if payload is not None else b"%PDF-1.4\n% fake approved cv for tests\n" + filename.encode()
    return {"id": "doc-1", "filename": filename, "mime": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode()}


class Bg:
    """The mock background: answers BgRequests, records them and relays frame payloads."""

    def __init__(
        self, answers: dict[str, dict[str, Any]] | None = None, *, settings: dict[str, Any] | None = None,
        connection: dict[str, Any] | None = None, latency: float = 0.004, default: Callable[..., dict[str, Any] | None] | None = None,
        job: dict[str, Any] | None = None, poll_cap: float = 0.4, kv: str = "kv1", documents: dict[str, dict[str, Any]] | None = None,
        generation: dict[str, Any] | None = None, prefs: dict[str, Any] | None = None, pending_holds: bool = False,
        pending: dict[str, Any] | None = None, profile: dict[str, Any] | None = None, research: dict[str, Any] | None = None,
    ) -> None:
        self.answers = {normalize(k): v for k, v in (answers or {}).items()}
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.connection = connection or {"phase": "ready"}
        self.latency = latency
        self.default = default
        self.poll_cap = poll_cap
        self.kv = kv
        self.documents = documents or {}
        self.generation = generation or {"available": True, "tested": True, "provider": "fake", "local": True}
        # what /v1/status says about the profile (`has_cv` = an APPROVED CV exists; `unapproved_cvs` = imported versions still waiting)
        self.profile = profile or {"verified_facts": 12, "has_cv": True, "display_name": "Alex"}
        self.research = research  # the research snapshot the session reply carries (None = none yet)
        self.job = job or {"company": "Example Company", "title": "Software Engineer", "location": "London", "job_id": None,
                           "application_id": None, "tracked": "none"}
        self.prefs = prefs or {"language": "auto", "alwaysOn": [], "hintOff": [], "settings": None, "welcomed": True}
        # the panel's remembered look the worker holds for `ui:get` / `ui:set` (dock side and the notes the person dismissed, by epoch ms)
        self.ui_prefs: dict[str, Any] = {"dock": "right", "dismissed": {}}
        self.log: list[dict[str, Any]] = []
        self.events: list[tuple[int, dict[str, Any]]] = []
        self.cursor = 0
        self.page: Page | None = None
        self.frame_ids: dict[Frame, int] = {}
        self.next_child = 0
        self.session_open_count = 0
        self.overrides: dict[str, Callable[[dict[str, Any], Frame], Any]] = {}
        self.suggest_delay: Callable[[dict[str, Any]], float] | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.pair_polls = 0
        self.activate_auto = False  # how the tab was last activated (the worker re-uses it for frames that appear later)
        # the worker half of "a submit awaiting its confirmation page" (C3): per TAB, so it outlives a navigation to another origin.
        # `pending_holds` False = a worker that does not know the feature (its ctx has no `pending` field): the page falls back to sessionStorage.
        self.pending_holds = pending_holds or pending is not None
        self.pending: dict[str, Any] | None = pending
        # relay hooks: delay (seconds) / drop a frame payload before it reaches its target (a slow or failing worker)
        self.relay_delay: Callable[[dict[str, Any]], float] | None = None
        self.relay_drop: Callable[[dict[str, Any]], bool] | None = None

    # ---- identity -----------------------------------------------------------------------------------------------------

    def frame_id(self, frame: Frame) -> int:
        if self.page is not None and frame == self.page.main_frame:
            return 0
        if frame not in self.frame_ids:
            self.frame_ids[frame] = CHILD_FRAME_BASE + self.next_child
            self.next_child += 1
        return self.frame_ids[frame]

    def frame_for(self, frame_id: int) -> Frame | None:
        if frame_id == 0 and self.page is not None:
            return self.page.main_frame
        return next((f for f, i in self.frame_ids.items() if i == frame_id), None)

    # ---- recording helpers -----------------------------------------------------------------------------------------------

    def msgs(self, kind: str, frame: str | None = None) -> list[dict[str, Any]]:
        return [e["msg"] for e in self.log if e["msg"].get("t") == kind and (frame is None or e["frame"] == frame)]

    def suggest_requests(self, frame: str | None = None) -> list[dict[str, Any]]:
        return [m["request"] for m in self.msgs("suggest", frame)]

    def push_event(self, event: dict[str, Any]) -> None:
        self.cursor += 1
        self.events.append((self.cursor, event))

    # ---- the binding --------------------------------------------------------------------------------------------------

    async def handle(self, source: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any]:
        frame: Frame = source["frame"]
        fid = self.frame_id(frame)
        name = "top" if fid == 0 else f"f{fid}"
        self.log.append({"frame": name, "msg": json.loads(json.dumps(msg)), "at": time.time()})
        if msg.get("t") != "poll":
            await asyncio.sleep(self.latency)
        override = self.overrides.get(msg["t"])
        if override is not None:
            result = override(msg, frame)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                return result
        return await self.answer(msg, frame, fid, name)

    async def answer(self, msg: dict[str, Any], frame: Frame, fid: int, name: str) -> dict[str, Any]:
        t = msg["t"]
        if t == "ctx":
            info: dict[str, Any] = {"tabId": 1, "frameId": fid, "isTop": fid == 0, "settings": self.settings, "prefs": self.prefs,
                                    "connection": self.connection, "e2e": True}
            if self.pending_holds:
                info["pending"] = self.pending
            return ok(info)
        if t == "ui:get":
            return ok(json.loads(json.dumps(self.ui_prefs)))
        if t == "ui:set":
            self.ui_prefs = json.loads(json.dumps(msg["value"]))
            return ok(self.ui_prefs)
        if t == "pending:set":
            self.pending = dict(msg["record"])
            return ok(None)
        if t == "pending:clear":
            self.pending = None
            return ok(None)
        if t == "conn:state":
            return ok(self.connection)
        if t == "session:open":
            self.session_open_count += 1
            return ok({"session_id": f"s{self.session_open_count}", "protocol": 1, "knowledge_version": self.kv, "job": self.job,
                       "research": self.research, "generation": self.generation, "settings": self.settings, "cursor": self.cursor})
        if t == "status":
            return ok({"protocol": 1, "app_version": "test", "latest_extension_version": "0.1.0", "language": "en",
                       "profile": self.profile, "generation": self.generation,
                       "settings": self.settings})
        if t == "suggest":
            return await self.suggest(msg)
        if t == "poll":
            return await self.poll(msg)
        if t == "frame:relay":
            self.relay(msg, frame)
            return ok(None)
        if t == "frames:inject":
            # the worker injects content.js into frames that lack it and activates exactly those, with the tab's own `auto` flag;
            # the init script already injected it, and the top frame is never asked again
            task = asyncio.ensure_future(self.to_children({"t": "activate", **({"auto": True} if self.activate_auto else {})}))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return ok(None)
        if t == "document:content":
            doc = self.documents.get(msg["id"])
            return ok(doc) if doc else err("not_found", "no such document")
        if t == "feedback":
            return ok({"saved": True, "application_id": "a1", "scope": "application", "promoted_to_profile": False})
        if t == "track":
            event = msg["request"]["event"]
            if event == "save":
                return ok({"state": "saved", "tracked": "preparing"})
            return ok({"state": "recorded" if event == "record" else "noted", "tracked": "applied" if event == "record" else "none"})
        if t == "regenerate":
            # every explicit revision is a NEW draft job of its own (the server's nonce): its own `generation.id`
            revision = sum(1 for e in self.log if e["msg"].get("t") == "regenerate")
            return ok(sug({"node_id": msg["request"].get("node_id") or 0, "context": "regen"}, {"frame_key": msg["request"].get("frame_key", "top")},
                          state="GENERATING", generation={"id": f"rev{revision}", "status": "REGENERATING", "attempt": 1},
                          question_id=msg["request"]["question_id"]))
        if t == "settings:patch":
            self.settings = {**self.settings, **msg["patch"]}
            return ok(self.settings)
        if t == "pair:start":
            return ok({"pairingId": "p1", "code": "482913", "expiresAt": int(time.time() * 1000) + 120000})
        if t == "pair:poll":
            self.pair_polls += 1
            if self.pair_polls >= 2:
                self.connection = {"phase": "ready"}
                return ok({"state": "claimed", "connection": self.connection})
            return ok({"state": "pending", "connection": {"phase": "pairing"}})
        if t in ("research:company", "research:site", "research:refresh"):
            return ok({"run_id": "r", "state": "done", "version": 1, "ready_for_answers": True, "quality": "full", "stale": False,
                       "headline": "Researched", "needs": None, "company": {"name": "Example Company"}, "site": None, "role": None,
                       "brief": "", "brief_source": None, "stages": [], "sources": [], "notes": [], "updated_at": ""})
        return ok(None)

    async def suggest(self, msg: dict[str, Any]) -> dict[str, Any]:
        req = msg["request"]
        kv = self.kv  # the answer is computed NOW (under today's knowledge) and may be delivered late
        results, skipped = [], []
        for el in req["elements"]:
            spec = self.lookup(el, req)
            if spec is None:
                skipped.append({"node_id": el["node_id"], "reason": "unsupported"})
                continue
            results.append(sug(el, req, kv=kv, **spec))
        if self.suggest_delay:
            await asyncio.sleep(self.suggest_delay(req))
        return ok({"knowledge_version": kv, "results": results, "skipped": skipped, "cursor": self.cursor})

    def lookup(self, el: dict[str, Any], req: dict[str, Any]) -> dict[str, Any] | None:
        texts = [normalize(str(el.get(k) or "")) for k in ("context", "label", "name", "placeholder", "id")]
        keys = sorted(self.answers, key=len, reverse=True)
        for text in texts:
            if not text:
                continue
            for key in keys:
                if key == text or key in text:
                    spec = self.answers[key]
                    return spec(el, req) if callable(spec) else dict(spec)
        if self.default:
            return self.default(el, req)
        return NEEDS()

    async def poll(self, msg: dict[str, Any]) -> dict[str, Any]:
        cursor = msg["cursor"]
        deadline = time.time() + min(msg["waitMs"] / 1000, self.poll_cap)
        while True:
            events = [e for c, e in self.events if c > cursor]
            if events:
                return ok({"cursor": self.events[-1][0], "events": events})
            if time.time() >= deadline:
                return ok({"cursor": max(cursor, self.cursor), "events": []})
            await asyncio.sleep(0.02)

    def relay(self, msg: dict[str, Any], source: Frame) -> None:
        page = self.page
        if page is None:
            return
        to = msg["to"]
        if to == "top":
            targets = [page.main_frame]
        elif to == "all":
            targets = list(page.frames)
        else:
            frame = self.frame_for(to["frameId"])
            targets = [frame] if frame else []
        envelope = {"t": "frame", "payload": msg["payload"], "fromFrameId": self.frame_id(source)}
        if self.relay_drop is not None and self.relay_drop(msg["payload"]):
            return
        delay = self.relay_delay(msg["payload"]) if self.relay_delay is not None else 0.0
        for target in targets:
            task = asyncio.ensure_future(self.deliver_later(target, envelope, delay))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    @staticmethod
    async def deliver_later(frame: Frame, message: dict[str, Any], delay: float) -> Any:
        if delay > 0:
            await asyncio.sleep(delay)
        return await Bg.deliver(frame, message)

    @staticmethod
    async def deliver(frame: Frame, message: dict[str, Any]) -> Any:
        try:
            return await frame.evaluate("(m) => window.__deliver && window.__deliver(m)", message)
        except Exception:
            return None  # the frame navigated away or was detached

    async def to_children(self, message: dict[str, Any]) -> None:
        assert self.page is not None
        await asyncio.gather(*(self.deliver(f, message) for f in self.page.frames if f != self.page.main_frame))

    async def to_all(self, message: dict[str, Any]) -> None:
        assert self.page is not None
        await asyncio.gather(*(self.deliver(f, message) for f in self.page.frames))


# ---------------------------------------------------------------------------------------------------- rig


class Rig:
    def __init__(self, browser_name: str, servers: Servers, headless: bool = True) -> None:
        self.browser_name = browser_name
        self.servers = servers
        self.headless = headless
        self._pw_cm = None
        self.pw = None
        self.browser = None
        self.contexts: list[Any] = []

    async def start(self) -> None:
        self._pw_cm = async_playwright()
        self.pw = await self._pw_cm.__aenter__()
        if self.browser_name == "chromium":
            self.browser = await self.pw.chromium.launch(channel="chromium", headless=self.headless)
        else:
            self.browser = await self.pw.webkit.launch(headless=self.headless)

    async def reset(self) -> None:
        for ctx in self.contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        self.contexts = []

    async def stop(self) -> None:
        await self.reset()
        if self.browser:
            await self.browser.close()
        if self._pw_cm:
            await self._pw_cm.__aexit__(None, None, None)

    async def open(
        self, path: str, bg: Bg | None = None, *, host: str = "main", activate: bool = True, ready: bool = True,
        headless_ui: bool = True, viewport: tuple[int, int] = (1200, 900), init: str = "", wait_frames: int = 0,
        probe: bool = False, auto: bool = False,
    ) -> tuple[Page, Bg]:
        bg = bg or Bg()
        ctx = await self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.contexts.append(ctx)
        page = await ctx.new_page()
        bg.page = page
        await page.expose_binding("__bg", bg.handle)
        await ctx.add_init_script(path=str(MOCK_JS))
        if probe:
            await ctx.add_init_script(path=str(PROBE_JS))
        if headless_ui:
            await ctx.add_init_script("window.__MERIDIAN_HEADLESS_UI__ = true;")
        if self.browser_name == "webkit":
            # Safari's Tab (default settings) stops on text fields only: the assistant's tab-stop model follows when asked to (e2e builds)
            await ctx.add_init_script("window.__MERIDIAN_SAFARI_TAB__ = true;")
        if init:
            await ctx.add_init_script(init)
        await ctx.add_init_script(path=str(CONTENT_JS))
        await page.goto(self.servers.url(path, host), wait_until="load")
        if wait_frames:
            await eventually(lambda: len(page.frames) >= wait_frames + 1, 8, "iframes load")
            for frame in page.frames:
                try:
                    await frame.wait_for_load_state("load")
                except Exception:
                    pass
        if activate:
            await self.activate(page, bg, auto=auto)
            if ready and not auto:
                await self.ready(page, bg)
        return page, bg

    async def activate(self, page: Page, bg: Bg, auto: bool = False, toggle: bool = False, frame: Frame | None = None) -> None:
        """What the worker sends: `activate` (the toolbar click on a fresh page), `auto` (it re-activates a tab by itself after a
        navigation) or `toggle` (the toolbar click on a page whose content script already runs)."""
        message = {"t": "activate", **({"auto": True} if auto else {}), **({"toggle": True} if toggle else {})}
        if not toggle:
            bg.activate_auto = auto
        if frame is not None:
            await bg.deliver(frame, message)
        else:
            await bg.to_all(message)

    async def ready(self, page: Page, bg: Bg, timeout: float = 8.0, expect_fields: int | None = None) -> None:
        """Ready = every frame runs the assistant, the top frame has its session and every scanned field has its answer. A page that came
        up EMPTY (a reset connection, a script that never loaded) has no fields to answer, which would be "ready" vacuously: so at least one
        field must have been scanned in some frame, unless the caller says the page has none (`expect_fields=0`) or does not wait (`ready=False`)."""
        async def check() -> bool:
            scanned = 0
            for frame in page.frames:
                state = await debug(frame)
                if state is None:
                    return False
                if not state["active"]:
                    return False
                if frame == page.main_frame and not state["sessionId"]:
                    return False
                # a frame nobody can see is given no session and asks Meridian nothing (SEC-01): there is nothing to wait for in it
                if state.get("frameHidden"):
                    continue
                if state["fields"] and not all(f["hasEntry"] for f in state["fields"]):
                    return False
                if frame != page.main_frame and not state["sessionId"]:
                    return False
                scanned += len(state["fields"])
            top = await debug(page.main_frame)
            if expect_fields is not None and len(top["fields"]) != expect_fields:
                return False
            if expect_fields is None and scanned == 0:
                return False
            return True

        await eventually(check, timeout, "assistant ready (session + suggestions for every field, at least one field scanned)")


# ---------------------------------------------------------------------------------------------------- helpers


async def eventually(check: Callable[[], Any], timeout: float = 5.0, what: str = "condition", interval: float = 0.03) -> Any:
    end = time.time() + timeout
    last: Any = None
    while True:
        try:
            value = check()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
            last = value
        except AssertionError as exc:
            last = exc
        if time.time() > end:
            raise AssertionError(f"timed out after {timeout}s waiting for {what} (last: {last!r})")
        await asyncio.sleep(interval)


def _frame(target: Page | Frame) -> Frame:
    return target.main_frame if isinstance(target, Page) else target


async def debug(target: Page | Frame) -> dict[str, Any] | None:
    try:
        return await _frame(target).evaluate("window.__meridianAssist && window.__meridianAssist.debug && window.__meridianAssist.debug()")
    except Exception:
        return None


async def ui_log(target: Page | Frame, name: str | None = None) -> list[dict[str, Any]]:
    log = await _frame(target).evaluate("window.__meridianUiLog || []")
    return [c for c in log if name is None or c["name"] == name]


async def last_chip(target: Page | Frame) -> dict[str, Any] | None:
    """The chip currently on screen in that frame (None when hidden)."""
    for call in reversed(await ui_log(target)):
        if call["name"] == "hideChip":
            return None
        if call["name"] == "showChip":
            return call["arg"]
    return None


async def last_panel(target: Page | Frame) -> dict[str, Any] | None:
    calls = await ui_log(target, "setPanel")
    return calls[-1]["arg"] if calls else None


async def emit(target: Page | Frame, action: dict[str, Any]) -> None:
    await _frame(target).evaluate("(a) => window.__meridianUiEmit(a)", action)


async def press_fill_verified(target: Page | Frame, *, confirm: bool = True) -> dict[str, Any] | None:
    """The person presses "Fill verified". The panel then lists every field the button will fill (`fillPreview`, SEC-01); confirming fills exactly
    those. Returns the confirmation row (None when there was nothing to fill or the UI has no row: it fills at once). The row is drawn
    synchronously by the press, so it is read right after it."""
    await emit(target, {"type": "fill-verified"})
    panel = await last_panel(target)
    preview = (panel or {}).get("fillPreview")
    if preview and confirm:
        await emit(target, {"type": "fill-confirm"})
    return preview


async def active_name(target: Page | Frame) -> str | None:
    return await _frame(target).evaluate(
        "(() => { let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;"
        " return a ? (a.getAttribute('name') || a.id || a.getAttribute('aria-label') || a.tagName.toLowerCase()) : null; })()")


async def value_of(target: Page | Frame, selector: str) -> str:
    return await _frame(target).evaluate("(s) => document.querySelector(s).value", selector)


async def tab(page: Page, times: int = 1, shift: bool = False) -> None:
    for _ in range(times):
        await page.keyboard.press("Shift+Tab" if shift else "Tab")


async def chip_for(target: Page | Frame, label_part: str, timeout: float = 4.0) -> dict[str, Any]:
    async def check() -> dict[str, Any] | None:
        chip = await last_chip(target)
        return chip if chip and normalize(label_part) in normalize(chip["fieldLabel"]) else None

    return await eventually(check, timeout, f"chip for {label_part!r}")

