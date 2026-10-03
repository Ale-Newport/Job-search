"""Persistent, visible Playwright browser with guarded observed-node execution."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import platform
import re
import time
from urllib.parse import urlparse
import uuid

from .types import Decision, HumanRequired, InvalidDecision, Operation, Paused, StaleState


OBSERVATION_SCRIPT = Path(__file__).with_name("observation.js").read_text()
PROGRESS = re.compile(r"^(?:next|continue|save and continue|review|proceed)(?:\s|$)", re.I)
SUBMIT = re.compile(r"\b(?:submit|send application|apply(?: now)?|finish application|complete application|confirm application)\b", re.I)
CONFIRMATION = re.compile(
    r"(?:thank you for (?:applying|your application)|application (?:has been |was )?"
    r"(?:received|submitted|successfully submitted)|(?:we have|we've) received your application|"
    r"successfully (?:submitted|sent) your application)", re.I,
)


def safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise InvalidDecision("Only HTTP(S) URLs without embedded credentials may be opened.")
    return url


def system_chrome_available() -> bool:
    executable = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return platform.system() == "Darwin" and executable.is_file() and os.access(executable, os.X_OK)


def is_submit(element: dict) -> bool:
    if element.get("role") != "button":
        return False
    label = element.get("label", "").strip()
    # Buttons inside custom widgets often inherit HTML's default type=submit.
    # That default alone is not evidence of the employer's final submit control.
    explicit = element.get("explicit_type", element.get("type"))
    return not PROGRESS.match(label) and (explicit == "submit" or bool(SUBMIT.search(label)))


def is_application_entry(element: dict, snapshot: dict) -> bool:
    """An observed application URL on a listing may be opened with a GET.

    Never click a button that could execute submission code, even if it looks
    like a navigation link. A page with candidate inputs is never an entry page.
    """
    return bool(element.get('tag') == 'a' and 'OPEN_TAB' in element.get('operations', [])
                and re.fullmatch(r'apply(?: now| for (?:this |the )?(?:job|role|position))?', element.get('label', '').strip(), re.I)
                and urlparse(element.get('href') or '').scheme in {'http', 'https'}
                and not any(any(op in e['operations'] for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD'))
                            for e in snapshot['elements']))


def confirmation_evidence(snapshot: dict) -> list[dict]:
    evidence = []
    for frame in snapshot.get("frames", []):
        match = CONFIRMATION.search(frame.get("text", ""))
        if match:
            start = max(0, match.start() - 60)
            evidence.append({"kind": "visible_confirmation", "text": frame["text"][start:match.end() + 180],
                             "url": frame["url"], "document_id": frame["document_id"]})
    return evidence


class BrowserSession:
    def __init__(self, profile_path: str | Path):
        self.profile_path = Path(profile_path)
        self.context = None
        self.page = None
        self._playwright = None
        self._paused = False
        self._takeover = False
        self._snapshots: OrderedDict[str, tuple[dict, dict]] = OrderedDict()
        self.allowed_uploads: set[Path] = set()
        self._headless = False
        self._channel: str | None = None
        self._last_error: str | None = None
        self._action_lock = asyncio.Lock()

    async def start(self, headless: bool = False, channel: str | None = "auto") -> dict:
        if self.context:
            return self.status()
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise HumanRequired("Playwright is unavailable. Install the browser extra and Chromium.") from error
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.profile_path.chmod(0o700)
        self._headless = headless
        self._channel = "chrome" if channel == "auto" and system_chrome_available() else None if channel == "auto" else channel
        if platform.system() == "Darwin":
            # Frozen Playwright otherwise defaults to Chromium inside _MEIPASS,
            # where the heavyweight browser is intentionally not bundled.
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "Library/Caches/ms-playwright"))
        try:
            self._playwright = await async_playwright().start()
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_path), headless=headless, channel=self._channel,
                viewport={"width": 1280, "height": 900}, accept_downloads=False, chromium_sandbox=True,
            )
        except Exception as error:
            if self._playwright:
                await self._playwright.stop()
            self._playwright = None
            self._last_error = (str(error).splitlines() or [type(error).__name__])[0][:220]
            raise HumanRequired("The application browser could not start. Install Google Chrome or Chromium "
                                "(`python -m playwright install chromium`), and close any other Meridian process "
                                f"using this profile. Details: {self._last_error}") from error
        self._last_error = None
        self.context.set_default_timeout(6000)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.status()

    def status(self) -> dict:
        return {"running": self.context is not None, "paused": self._paused, "takeover": self._takeover,
                "headless": self._headless, "channel": self._channel,
                "current_url": self.page.url if self.page and not self.page.is_closed() else None,
                "profile_path": str(self.profile_path), "last_error": self._last_error}

    def pause(self) -> dict:
        self._paused = True
        return self.status()

    async def resume(self) -> dict:
        self._paused = False
        self._takeover = False
        self._snapshots.clear()  # Human edits always invalidate old approvals.
        return self.status()

    async def takeover(self) -> dict:
        self._paused = True
        self._takeover = True
        self._snapshots.clear()
        if self.page:
            await self.page.bring_to_front()
        return self.status()

    def _check_pause(self):
        if self._paused:
            raise Paused("Automation is paused. Resume it after completing the requested manual action.")

    async def open(self, url: str) -> dict:
        safe_url(url)
        self._check_pause()
        await self.start()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._snapshots.clear()
        return await self.wait_until_ready()

    async def wait_until_ready(self) -> dict:
        """Allow an ATS's asynchronous application form to finish loading."""
        deadline = time.monotonic() + 20
        earliest = time.monotonic() + 1
        while True:
            self._check_pause()
            snapshot = await self.observe()
            loading = re.search(r"(?:fetching|loading) (?:the |your )?(?:application|form)", snapshot["text"], re.I)
            if not any(any(op in e['operations'] for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD')) for e in snapshot['elements']):
                loading = loading or re.search(r'^\s*Loading[.\s]*$', snapshot['text'], re.I | re.M)
            ready = bool(snapshot['elements'] and snapshot['text'].strip())
            if urlparse(snapshot['url']).path.rstrip('/').endswith(('/application', '/apply')):
                ready = any(any(op in e['operations'] for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD'))
                            for e in snapshot['elements']) or bool(confirmation_evidence(snapshot))
            if snapshot["blocked"] or time.monotonic() >= deadline or (not loading and ready and time.monotonic() >= earliest):
                return snapshot
            await asyncio.sleep(.25)

    async def observe(self) -> dict:
        if not self.page or self.page.is_closed():
            raise HumanRequired("Open an application in the browser first.")
        frames = []
        refs = {}
        elements = []
        for position, frame in enumerate(self.page.frames):
            # An invisible reCAPTCHA badge is not an interactive challenge or an application frame.
            # A visible challenge uses a separate bframe, which is still inspected and blocked.
            if re.search(r"/recaptcha/(?:api2|enterprise)/anchor\?", frame.url) and re.search(r"[?&]size=invisible(?:&|$)", frame.url):
                continue
            try:
                data = await frame.evaluate(OBSERVATION_SCRIPT)
            except Exception:
                # An inaccessible/navigating frame is never exposed as an actionable target.
                frames.append({"url": frame.url, "unavailable": True, "text": "", "document_id": "unavailable"})
                continue
            data["frame_index"] = position
            for element in data.pop("elements"):
                index = len(elements) + 1
                element.update(index=index, frame_index=position, document_id=data["document_id"], frame_url=data["url"])
                refs[index] = (frame, element["node_id"], data["document_id"])
                elements.append(element)
            frames.append(data)
        semantic = {"frames": [{k: f.get(k) for k in ("document_id", "url", "text", "errors", "unresolved_required")} for f in frames],
                    "elements": [{k: v for k, v in e.items() if k not in {"in_viewport"}} for e in elements]}
        fingerprint = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        snapshot_id = str(uuid.uuid4())
        snapshot = {"id": snapshot_id, "snapshot_id": snapshot_id, "fingerprint": fingerprint,
                    "url": self.page.url, "title": await self.page.title(), "frames": frames,
                    "elements": elements, "observed_at": time.time(),
                    "text": "\n".join(f.get("text", "") for f in frames)[:24000],
                    "blocked": any(f.get("challenge") or f.get("mfa") or f.get("login") for f in frames),
                    "challenge": any(f.get("challenge") for f in frames),
                    "mfa": any(f.get("mfa") for f in frames),
                    "login": any(f.get("login") for f in frames),
                    "unresolved_required": [e for f in frames for e in f.get("unresolved_required", [])],
                    "errors": [e for f in frames for e in f.get("errors", [])]}
        self._snapshots[snapshot_id] = (snapshot, refs)
        while len(self._snapshots) > 16:
            self._snapshots.popitem(last=False)
        return snapshot

    inspect = observe
    snapshot = observe

    async def _validate_snapshot(self, snapshot_id: str) -> tuple[dict, dict]:
        old = self._snapshots.get(snapshot_id)
        if not old:
            raise StaleState("The observation has expired. Inspect and review the page again.")
        fresh = await self.observe()
        if old[0]["fingerprint"] != fresh["fingerprint"]:
            raise StaleState("The page or form changed since it was observed. Review a fresh snapshot.")
        if fresh["blocked"]:
            raise HumanRequired("Human verification required" if fresh["challenge"] else
                                "Complete the sign-in or multi-factor verification in the browser.")
        return old

    async def execute(self, decision: Decision | dict, snapshot: str | dict, *, value: str | None = None,
                      file_path: str | Path | None = None, allow_submit: bool = False,
                      minimum_confidence: float = 0.85, upload_name: str | None = None,
                      upload_mime: str | None = None) -> dict:
        if isinstance(decision, dict):
            decision = Decision.from_dict(decision)
        if decision.confidence < minimum_confidence:
            raise HumanRequired("Decision confidence is below the configured threshold.")
        snapshot_id = snapshot if isinstance(snapshot, str) else snapshot["id"]
        async with self._action_lock:
            self._check_pause()
            observed, refs = await self._validate_snapshot(snapshot_id)
            op = decision.operation
            element = next((e for e in observed["elements"] if e["index"] == decision.target), None)
            if decision.target is not None and (not element or op not in element["operations"]):
                raise InvalidDecision("The target was not observed supporting this operation.")
            entry_navigation = element and op == Operation.OPEN_TAB and is_application_entry(element, observed)
            if element and is_submit(element) and not allow_submit and not entry_navigation:
                raise HumanRequired("Final submission requires a separate policy check and approval.")
            self._check_pause()
            if op in {Operation.BLOCKED, Operation.HUMAN_REQUIRED}:
                raise HumanRequired("The browser decision engine requested human assistance.")
            if op == Operation.DONE:
                return {"operation": op, "executed": False, "evidence": confirmation_evidence(observed),
                        "confirmed": bool(confirmation_evidence(observed))}
            if element:
                frame, node_id, document_id = refs[decision.target]
                handle = await frame.evaluate_handle(
                    "([id, doc]) => window.__meridianObservedControlsV1?.documentId === doc ? "
                    "window.__meridianObservedControlsV1.nodes.get(id) : null", [node_id, document_id])
                node = handle.as_element()
                if node is None:
                    raise StaleState("The original DOM node no longer exists in the observed document.")
                proxy_handle = None
                try:
                    visible_node = node
                    if element.get('interaction_proxy_id'):
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const proxy = window.__meridianObservedControlsV1.nodes.get(id);
                            return node.isConnected && proxy?.isConnected && node.getAttribute('role') === 'combobox'
                                && node.closest('.select__control') === proxy ? proxy : null;
                        }""", element['interaction_proxy_id'])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState('The observed dropdown control changed.')
                    if op == Operation.UPLOAD and element.get("upload_proxy_id"):
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const proxy = window.__meridianObservedControlsV1.nodes.get(id);
                            const field = node.closest('[data-field-path],.ashby-application-form-field-entry');
                            const inline = node.closest('a,button,label');
                            const attachment = node.matches('.visually-hidden') && node.parentElement?.querySelector(':scope > button[type="button"]');
                            const associated = field?.contains(proxy) || inline === proxy || attachment === proxy;
                            return node.isConnected && proxy?.isConnected && !node.disabled && associated ? proxy : null;
                        }""", element["upload_proxy_id"])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState("The observed upload control changed.")
                    await visible_node.scroll_into_view_if_needed()
                    valid = await visible_node.evaluate("""node => {
                        if (!node.isConnected || node.ownerDocument !== document) return 'document';
                        if (node.matches(':disabled') || node.closest('[aria-disabled="true"],[inert]')) return 'disabled';
                        const r = node.getBoundingClientRect(), s = getComputedStyle(node);
                        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) return 'hidden';
                        const x = Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2));
                        const y = Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2));
                        let top = document.elementFromPoint(x, y);
                        while (top?.shadowRoot?.elementFromPoint(x,y)) top = top.shadowRoot.elementFromPoint(x,y);
                        return top === node || node.contains(top) ? 'ok' : 'covered';
                    }""")
                    if valid != "ok":
                        raise StaleState(f"The observed control is {valid}; no action was performed.")
                    if frame.parent_frame:
                        iframe = await frame.frame_element()
                        outer_valid = await iframe.evaluate("""node => {
                            const r=node.getBoundingClientRect();
                            const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                            return top === node || node.contains(top);
                        }""")
                        if not outer_valid:
                            raise StaleState("The frame is covered by another element.")
                    self._check_pause()
                    if op == Operation.TYPE_TEXT:
                        if value is None or not isinstance(value, str) or len(value) > 20000:
                            raise InvalidDecision("A verified text value is required.")
                        # React Select opens on pointer interaction, not focus alone.
                        if element['role'] == 'combobox' and not element.get('expanded'):
                            await visible_node.click()
                        await node.fill(value)
                    elif op == Operation.SELECT:
                        option = next((o for o in element["options"] if o["value"] == value and not o["disabled"]), None)
                        if option is None:
                            raise InvalidDecision("Only an observed enabled dropdown option may be selected.")
                        await node.select_option(value=value)
                    elif op in {Operation.CHECK, Operation.UNCHECK}:
                        desired = op == Operation.CHECK
                        if element["type"] in {"checkbox", "radio"}:
                            await node.set_checked(desired)
                        elif bool(element["checked"]) != desired:
                            await node.click()
                    elif op == Operation.UPLOAD:
                        path = Path(file_path or "").resolve()
                        if path not in self.allowed_uploads or not path.is_file():
                            raise InvalidDecision("The file is not an explicitly selected application document.")
                        if path.stat().st_size > 50 * 1024 * 1024:
                            raise InvalidDecision("Application documents must be at most 50 MB.")
                        name = Path((upload_name or path.name).replace("\\", "/")).name
                        if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
                            raise InvalidDecision("The selected document has an invalid upload filename.")
                        mime = upload_mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
                        if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime):
                            raise InvalidDecision("The selected document has an invalid MIME type.")
                        content = await asyncio.to_thread(path.read_bytes)
                        if len(content) > 50 * 1024 * 1024:
                            raise InvalidDecision("Application documents must be at most 50 MB.")
                        self._check_pause()
                        await node.set_input_files({"name": name, "mimeType": mime, "buffer": content})
                    elif op == Operation.OPEN_TAB:
                        url = safe_url(element.get("href", ""))
                        self.page = await self.context.new_page()
                        await self.page.goto(url, wait_until="domcontentloaded")
                    else:
                        pages_before = set(self.context.pages)
                        await node.click()
                        new_pages = [p for p in self.context.pages if p not in pages_before]
                        if new_pages:
                            self.page = new_pages[-1]
                            await self.page.wait_for_load_state("domcontentloaded")
                finally:
                    if proxy_handle:
                        await proxy_handle.dispose()
                    await handle.dispose()
            elif op in {Operation.SCROLL_DOWN, Operation.SCROLL_UP}:
                await self.page.mouse.wheel(0, 650 if op == Operation.SCROLL_DOWN else -650)
            elif op == Operation.WAIT:
                await asyncio.sleep(0.35)
            elif op == Operation.BACK:
                await self.page.go_back(wait_until="domcontentloaded")
            elif op == Operation.CLOSE_TAB:
                if len(self.context.pages) <= 1:
                    raise HumanRequired("The only application tab will remain open for review.")
                old_page = self.page
                self.page = next(p for p in self.context.pages if p is not old_page)
                await old_page.close()
            else:
                raise InvalidDecision("Unsupported operation.")
            await asyncio.sleep(0.05)
            return {"operation": str(op), "target": decision.target, "label": element["label"] if element else None,
                    "confidence": decision.confidence, "executed": True, "url": self.page.url,
                    "decision_metadata": decision.metadata}

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self._playwright:
            await self._playwright.stop()
        self.page = self.context = self._playwright = None
        self._snapshots.clear()
