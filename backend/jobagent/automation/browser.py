"""Persistent, visible Playwright browser with guarded observed-node execution."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from functools import wraps
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import platform
import re
import time
from urllib.parse import urlparse
import uuid
import weakref

from .types import Decision, HumanRequired, InvalidDecision, Operation, Paused, StaleState, UserEdit


OBSERVATION_SCRIPT = Path(__file__).with_name("observation.js").read_text()
PROGRESS = re.compile(r"^(?:next|continue|save and continue|review|proceed)(?:\s|$)", re.I)
SUBMIT = re.compile(r"\b(?:submit|send application|apply(?: now)?|finish application|complete application|confirm application)\b", re.I)
APPLICATION_ENTRY = re.compile(
    r"^(?:apply(?: now| manually| for (?:this |the )?(?:job|role|position))?|"
    r"(?:start|begin)(?: (?:your|an|the))? application)$", re.I,
)
CONFIRMATION = re.compile(
    r"(?:thank you for (?:applying|your application)|application (?:has been |was )?"
    r"(?:received|submitted|successfully submitted)|(?:we have|we've) received your application|"
    r"successfully (?:submitted|sent) your application)", re.I,
)
LOGGER = logging.getLogger(__name__)


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


def application_input_elements(snapshot: dict) -> list[dict]:
    fields = [e for e in snapshot.get('elements', [])
              if any(op in e.get('operations', []) for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD'))
              and not e.get('auxiliary_control')
              and e.get('type') != 'search'
              and not re.fullmatch(r'\s*(?:job\s*search|search (?:jobs|roles))(?:\s+(?:input|field|query))?\s*',
                                   e.get('label', ''), re.I)]
    # Career landing pages can include floating chat/contact controls. They are
    # not an applicant form when the page still presents job copy and external
    # Apply links, without any applicant identity or document controls.
    entry_links = [e for e in snapshot.get('elements', []) if e.get('tag') == 'a'
                   and APPLICATION_ENTRY.fullmatch(e.get('label', '').strip())
                   and e.get('form_id', 'page') == 'page']
    applicant_fields = [e for e in fields if 'UPLOAD' in e.get('operations', [])
                        or re.search(r'\b(?:full|first|last|preferred) name\b|\be-?mail\b', e.get('label', ''), re.I)]
    has_job_copy = any(frame.get('job_context', {}).get('sections') or
                       (frame.get('job_context', {}).get('title') and
                        re.search(r'\bjob description\b|\bcompany description\b|\bresponsibilities\b|\bqualifications\b',
                                  frame.get('text', ''), re.I)) for frame in snapshot.get('frames', []))
    if entry_links and has_job_copy and not applicant_fields and not snapshot.get('application_started'):
        return []
    return fields


def is_application_entry(element: dict, snapshot: dict) -> bool:
    """Recognise entry navigation before candidate fields or a final review.

    Links are opened by their observed GET destination, without their click
    handler. A button may open the form only outside a form, with no candidate
    inputs present. Explicit submit controls never qualify for this exception.
    """
    if (snapshot.get('application_started') or snapshot.get('blocked')
            or confirmation_evidence(snapshot) or element.get('form_id', 'page') != 'page'
            or element.get('navigation_parent_id')
            or not APPLICATION_ENTRY.fullmatch(element.get('label', '').strip())
            or application_input_elements(snapshot)):
        return False
    if element.get('tag') == 'a':
        return ('OPEN_TAB' in element.get('operations', [])
                and urlparse(element.get('href') or '').scheme in {'http', 'https'})
    return (element.get('tag') == 'button' and 'CLICK' in element.get('operations', [])
            and element.get('explicit_type') != 'submit')


def application_entry_target(snapshot: dict) -> dict | None:
    entries = [e for e in snapshot.get('elements', []) if is_application_entry(e, snapshot)]
    # Workday's method chooser must open the blank form rather than reuse a
    # previous application or invoke an unapproved resume/LinkedIn import.
    manual = [e for e in entries if e.get('label', '').strip().casefold() == 'apply manually']
    entries = manual or entries
    if len(entries) == 1:
        return entries[0]
    if entries and all(e.get('tag') == 'button' and e.get('label', '').strip().casefold() ==
                       entries[0].get('label', '').strip().casefold() for e in entries):
        # Responsive job pages repeat the same entry button in header/footer.
        # Each already passed the no-form/no-input/no-explicit-submit checks.
        return entries[0]
    if entries and all(e.get('tag') == 'a' and e.get('href') == entries[0].get('href') for e in entries):
        return entries[0]  # Repeated header/footer links to the same entry page.
    repeated = [e for e in entries if e.get('tag') == 'a'
                and sum(peer.get('href') == e.get('href') for peer in entries) > 1]
    if repeated and len({e['href'] for e in repeated}) == 1:
        return repeated[0]
    return None


def lost_posting_target(url: str, snapshot: dict) -> bool:
    """Do not treat a redirected board's search filters as an application."""
    from ..discovery import ats_identity
    expected_kind, expected_identity = ats_identity(url)
    actual_kind, actual_identity = ats_identity(snapshot.get('url') or url)
    if not expected_identity or snapshot.get('blocked') or actual_kind != expected_kind:
        return False
    if any(re.search(r"\b(?:job not found|position not found|job (?:is )?no longer available|no longer accepting applications|the page you are looking for doesn.t exist)\b",
                     frame.get('text',''), re.I) for frame in snapshot.get('frames', [])):
        return True
    if actual_identity:
        return actual_identity != expected_identity
    fields = application_input_elements(snapshot)
    applicant = any('UPLOAD' in field.get('operations', []) or
                    re.search(r'\b(?:full|first|last|preferred) name\b|\be-?mail\b', field.get('label', ''), re.I)
                    for field in fields)
    return not applicant and not any(APPLICATION_ENTRY.fullmatch(e.get('label', '').strip())
                                     for e in snapshot.get('elements', []))


def confirmation_evidence(snapshot: dict) -> list[dict]:
    evidence = []
    for frame in snapshot.get("frames", []):
        match = CONFIRMATION.search(frame.get("text", ""))
        if match:
            start = max(0, match.start() - 60)
            evidence.append({"kind": "visible_confirmation", "text": frame["text"][start:match.end() + 180],
                             "url": frame["url"], "document_id": frame["document_id"]})
    return evidence


def bounded(seconds):
    def decorate(method):
        @wraps(method)
        async def wrapped(*args, **kwargs):
            try:
                async with asyncio.timeout(seconds):
                    return await method(*args, **kwargs)
            except TimeoutError as exc:
                raise HumanRequired(f'{method.__name__} exceeded its {seconds}s operation limit. Inspect or retry this task.') from exc
        return wrapped
    return decorate


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
        self._lifecycle_lock = asyncio.Lock()
        self._configured = False
        self._watched_pages = weakref.WeakSet()
        self._disconnect_notified = False
        self.on_disconnect = None
        self.edits = None
        self.on_user_edit = None

    def _invalidate_approvals(self) -> None:
        self._snapshots.clear()

    def _notify_disconnect(self, reason: str) -> None:
        if self._disconnect_notified:
            return
        self._last_error = reason
        self._disconnect_notified = True
        if self.on_disconnect:
            try:
                self.on_disconnect(reason)
            except Exception:
                LOGGER.exception("Could not record application browser disconnection")

    def _context_lost(self, context, reason: str) -> None:
        # Late events from a retired browser must never invalidate its replacement.
        if context is not self.context:
            return
        self.context = self.page = None
        self._invalidate_approvals()
        self._notify_disconnect(reason)

    def _page_lost(self, page, reason: str) -> None:
        if page is not self.page:
            return
        self.page = None
        self._invalidate_approvals()
        self._notify_disconnect(reason)

    def _set_page(self, page) -> None:
        self.page = page
        self._disconnect_notified = False
        if page not in self._watched_pages:
            self._watched_pages.add(page)
            page.on("close", lambda *_: self._page_lost(
                page, "The application tab was closed. Retry Auto Apply to reopen the form."))
            page.on("crash", lambda *_: self._page_lost(
                page, "The application tab crashed. Retry Auto Apply to reopen the form."))

    def _context_running(self) -> bool:
        if self.context is None:
            return False
        browser = self.context.browser
        return browser is None or browser.is_connected()

    async def _release(self, context, driver) -> None:
        # Detach handles before calling this: both close and stop emit events.
        # Driver cleanup must still happen if the browser already died or hangs.
        try:
            if context:
                try:
                    await asyncio.wait_for(context.close(), timeout=5)
                except Exception:
                    pass
        finally:
            if driver:
                try:
                    await asyncio.wait_for(driver.stop(), timeout=5)
                except Exception:
                    pass

    @bounded(50)
    async def start(self, headless: bool | None = None, channel: str | None = "auto") -> dict:
        async with self._lifecycle_lock:
            return await self._start(headless, channel)

    async def _start(self, headless: bool | None, channel: str | None) -> dict:
        if self._context_running():
            if self.page and not self.page.is_closed():
                return self.status()
            # Closing one tab need not close a persistent browser context.
            # Reopen only on an explicit start/open; never resume an old approval.
            self._invalidate_approvals()
            try:
                self._set_page(await asyncio.wait_for(self.context.new_page(), timeout=15))
                self._last_error = None
                return self.status()
            except Exception:
                pass  # A simultaneous browser exit requires a fresh context.
        context, driver = self.context, self._playwright
        self.context = self.page = self._playwright = None
        self._invalidate_approvals()
        await self._release(context, driver)
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise HumanRequired("Playwright is unavailable. Install the browser extra and Chromium.") from error
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.profile_path.chmod(0o700)
        self._headless = self._headless if headless is None else headless
        if channel != "auto" or not self._configured:
            self._channel = "chrome" if channel == "auto" and system_chrome_available() else None if channel == "auto" else channel
        self._configured = True
        if platform.system() == "Darwin":
            # Frozen Playwright otherwise defaults to Chromium inside _MEIPASS,
            # where the heavyweight browser is intentionally not bundled.
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "Library/Caches/ms-playwright"))
        try:
            async with asyncio.timeout(35):
                self._playwright = await async_playwright().start()
                self.context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_path), headless=self._headless, channel=self._channel,
                    viewport={"width": 1280, "height": 900}, accept_downloads=False, chromium_sandbox=True,
                    timeout=30000,
                )
                context = self.context
                context.on("close", lambda *_: self._context_lost(
                    context, "The application browser was closed. Retry Auto Apply to reopen the form."))
                if context.browser:
                    context.browser.on("disconnected", lambda *_: self._context_lost(
                        context, "The application browser disconnected. Retry Auto Apply to reopen the form."))
                context.set_default_timeout(6000)
                self._set_page(context.pages[0] if context.pages else await context.new_page())
                if not self._context_running() or self.page.is_closed():
                    raise RuntimeError("The application browser closed while starting.")
        except BaseException as error:
            context, driver = self.context, self._playwright
            self.context = self.page = self._playwright = None
            self._invalidate_approvals()
            await self._release(context, driver)
            if not isinstance(error, Exception):
                raise
            self._last_error = (str(error).splitlines() or [type(error).__name__])[0][:220]
            raise HumanRequired("The application browser could not start. Install Google Chrome or Chromium "
                                "(`python -m playwright install chromium`), and close any other Meridian process "
                                f"using this profile. Details: {self._last_error}") from error
        self._last_error = None
        return self.status()

    def status(self) -> dict:
        running = self._context_running()
        return {"running": running, "paused": self._paused, "takeover": self._takeover,
                "headless": self._headless, "channel": self._channel,
                "current_url": self.page.url if running and self.page and not self.page.is_closed() else None,
                "profile_path": str(self.profile_path), "last_error": self._last_error}

    def pause(self) -> dict:
        self._paused = True
        return self.status()

    async def resume(self) -> dict:
        self._paused = False
        self._takeover = False
        self._invalidate_approvals()  # Human edits always invalidate old approvals.
        return self.status()

    @bounded(10)
    async def takeover(self) -> dict:
        self._paused = True
        self._takeover = True
        self._invalidate_approvals()
        if self.page and not self.page.is_closed():
            await self.page.bring_to_front()
        return self.status()

    def _check_pause(self):
        if self._paused:
            raise Paused("Automation is paused. Resume it after completing the requested manual action.")

    @bounded(55)
    async def open(self, url: str) -> dict:
        safe_url(url)
        self._check_pause()
        await self.start()
        self._check_pause()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._invalidate_approvals()
        return await self.wait_until_ready()

    @bounded(25)
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
            if re.search(r'/(?:application|apply)(?:/|$)', urlparse(snapshot['url']).path, re.I):
                ready = any(any(op in e['operations'] for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD'))
                            for e in snapshot['elements']) or bool(confirmation_evidence(snapshot)) or bool(application_entry_target(snapshot))
            if snapshot["blocked"] or time.monotonic() >= deadline or (not loading and ready and time.monotonic() >= earliest):
                return snapshot
            await asyncio.sleep(.25)

    @bounded(10)
    async def observe(self) -> dict:
        page = self.page
        if not self._context_running() or not page or page.is_closed():
            raise HumanRequired("The application browser is unavailable. Retry Auto Apply to reopen the form.")
        if getattr(self, '_active_application_id', None):
            if self.edits is None:
                from .manual_edits import ManualEdits
                self.edits = ManualEdits(OBSERVATION_SCRIPT, lambda record: self.on_user_edit(record) if self.on_user_edit else None)
            await self.edits.watch(page)
        frames = []
        refs = {}
        elements = []
        for position, frame in enumerate(page.frames):
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
                if self.edits:
                    self.edits.annotate(element)
                refs[index] = (frame, element["node_id"], data["document_id"])
                elements.append(element)
            frames.append(data)
        semantic = {"frames": [{k: f.get(k) for k in ("document_id", "url", "text", "errors", "unresolved_required")} for f in frames],
                    "elements": [{k: v for k, v in e.items() if k not in {"in_viewport"}} for e in elements]}
        fingerprint = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        snapshot_id = str(uuid.uuid4())
        snapshot = {"id": snapshot_id, "snapshot_id": snapshot_id, "fingerprint": fingerprint,
                    "url": page.url, "title": await page.title(), "frames": frames,
                    "elements": elements, "observed_at": time.time(),
                    "text": "\n".join(f.get("text", "") for f in frames)[:24000],
                    "blocked": any(f.get("challenge") or f.get("mfa") or f.get("login") for f in frames),
                    "challenge": any(f.get("challenge") for f in frames),
                    "mfa": any(f.get("mfa") for f in frames),
                    "login": any(f.get("login") for f in frames),
                    "unresolved_required": [e for f in frames for e in f.get("unresolved_required", [])],
                    "errors": [e for f in frames for e in f.get("errors", [])]}
        snapshot['manual_revision'] = self.edits.revision if self.edits else 0
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
            if old[0].get('manual_revision', 0) != fresh.get('manual_revision', 0):
                raise UserEdit('A user edited the form. Reinspect it and preserve the correction.')
            raise StaleState("The page or form changed since it was observed. Review a fresh snapshot.")
        if fresh["blocked"]:
            raise HumanRequired("Human verification required" if fresh["challenge"] else
                                "Complete the sign-in or multi-factor verification in the browser.")
        return old

    @bounded(30)
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
            upload_evidence = None
            self._check_pause()
            observed, refs = await self._validate_snapshot(snapshot_id)
            op = decision.operation
            element = next((e for e in observed["elements"] if e["index"] == decision.target), None)
            if decision.target is not None and (not element or op not in element["operations"]):
                raise InvalidDecision("The target was not observed supporting this operation.")
            entry_navigation = (element and is_application_entry(element, observed)
                                and op == (Operation.OPEN_TAB if element.get('tag') == 'a' else Operation.CLICK))
            if element and is_submit(element) and not allow_submit and not entry_navigation:
                raise HumanRequired("Final submission requires a separate policy check and approval.")
            if element and element.get('user_edit') and op in {Operation.TYPE_TEXT, Operation.CLEAR, Operation.SELECT,
                                                               Operation.CHECK, Operation.UNCHECK, Operation.UPLOAD}:
                raise UserEdit('This control belongs to the user; automatic writes are suspended.')
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
                if self.edits:
                    keys = [element.get('manual_key')]
                    keys.extend(e.get('manual_key') for e in observed['elements']
                                if e.get('document_id') == element.get('document_id') and
                                element['node_id'] in (e.get('dropdown_trigger_id'), e.get('dropdown_search_id')))
                    await self.edits.automate(self.page, keys)
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
                    if element.get('toggle_proxy_id'):
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const proxy = window.__meridianObservedControlsV1.nodes.get(id);
                            return node.isConnected && proxy?.isConnected && node instanceof HTMLInputElement &&
                                ['radio','checkbox'].includes(node.type) && !node.disabled &&
                                proxy instanceof HTMLLabelElement && proxy.control === node &&
                                [...(node.labels || [])].includes(proxy) ? proxy : null;
                        }""", element['toggle_proxy_id'])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState('The observed toggle label changed.')
                    if element.get('visual_toggle_proxy_id'):
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const proxy = window.__meridianObservedControlsV1.nodes.get(id);
                            return node.isConnected && proxy?.isConnected && node.matches('.checkbox') &&
                                proxy.matches('.label-text') && proxy.parentElement === node.parentElement &&
                                node.querySelector('.checkmark') ? proxy : null;
                        }""", element['visual_toggle_proxy_id'])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState('The observed visual toggle changed.')
                    if op == Operation.CLEAR and element.get("clear_proxy_id"):
                        if proxy_handle:
                            await proxy_handle.dispose()
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const clear = window.__meridianObservedControlsV1.nodes.get(id);
                            const container = node.closest('.select__control');
                            return node.isConnected && clear?.isConnected && container &&
                                container.contains(clear) && clear.matches('.select__clear-indicator') &&
                                node.getAttribute('role') === 'combobox' ? clear : null;
                        }""", element["clear_proxy_id"])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState("The observed dropdown clear control changed.")
                    if op == Operation.UPLOAD and element.get("upload_proxy_id"):
                        proxy_handle = await node.evaluate_handle("""(node, id) => {
                            const proxy = window.__meridianObservedControlsV1.nodes.get(id);
                            const field = node.closest('[data-field-path],.ashby-application-form-field-entry');
                            const inline = node.closest('a,button,label');
                            const attachment = node.matches('.visually-hidden') && node.parentElement?.querySelector(':scope > button[type="button"]');
                            const associated = field?.contains(proxy) || inline === proxy || attachment === proxy ||
                                (proxy instanceof HTMLLabelElement && proxy.control === node && [...(node.labels || [])].includes(proxy));
                            return node.isConnected && proxy?.isConnected && !node.disabled && associated ? proxy : null;
                        }""", element["upload_proxy_id"])
                        visible_node = proxy_handle.as_element()
                        if visible_node is None:
                            raise StaleState("The observed upload control changed.")
                    await visible_node.scroll_into_view_if_needed()
                    await visible_node.evaluate("n => n.scrollIntoView({block:'center',inline:'center',behavior:'instant'})")
                    valid = await visible_node.evaluate("""(node, nativeToggleId) => {
                        if (!node.isConnected || node.ownerDocument !== document) return 'document';
                        if (node.matches(':disabled') || node.closest('[aria-disabled="true"],[inert]')) return 'disabled';
                        const r = node.getBoundingClientRect(), s = getComputedStyle(node);
                        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) return 'hidden';
                        const x = Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2));
                        const y = Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2));
                        let top = document.elementFromPoint(x, y);
                        while (top?.shadowRoot?.elementFromPoint(x,y)) top = top.shadowRoot.elementFromPoint(x,y);
                        // A transparent native input can intentionally cover its
                        // own visible label. Only that exact observed input may
                        // be the hit target; other overlays still block action.
                        if (nativeToggleId && top === window.__meridianObservedControlsV1.nodes.get(nativeToggleId)) return 'native';
                        return top === node || node.contains(top) ? 'ok' : 'covered';
                    }""", node_id if element.get('toggle_proxy_id') or element.get('upload_proxy_id') else None)
                    if valid not in {"ok", "native"}:
                        raise StaleState(f"The observed control is {valid}; no action was performed.")
                    toggle_click_node = node if valid == "native" else visible_node
                    if frame.parent_frame:
                        point = await visible_node.evaluate('''node => {
                            const r=node.getBoundingClientRect();
                            return {x:r.x+r.width/2,y:r.y+r.height/2};
                        }''')
                        ancestor = frame
                        while ancestor.parent_frame:
                            iframe = await ancestor.frame_element()
                            try:
                                # Test the target's actual hit point through every
                                # ancestor. A tall iframe's centre may be offscreen
                                # even when the candidate control is visible.
                                point = await iframe.evaluate('''(node, point) => {
                                    const r=node.getBoundingClientRect();
                                    const x=r.x+(node.clientLeft+point.x)*r.width/node.offsetWidth;
                                    const y=r.y+(node.clientTop+point.y)*r.height/node.offsetHeight;
                                    const top=document.elementFromPoint(x,y);
                                    return {x,y,valid:top===node || node.contains(top)};
                                }''', point)
                                if not point['valid']:
                                    raise StaleState('The frame is covered at the observed control.')
                            finally:
                                await iframe.dispose()
                            ancestor = ancestor.parent_frame
                    self._check_pause()
                    # Scrolling and hit testing await browser IPC. A correction
                    # made during those awaits wins before any actual write.
                    if self.edits:
                        record = self.edits.records.get((element.get('frame_url'), element.get('manual_key')))
                        if record:
                            raise UserEdit('This control was edited by the user; preserve its value.')
                    if op == Operation.TYPE_TEXT:
                        if value is None or not isinstance(value, str) or len(value) > 20000:
                            raise InvalidDecision("A verified text value is required.")
                        # React Select opens on pointer interaction, not focus alone.
                        if element['role'] == 'combobox' and not element.get('expanded'):
                            await visible_node.click()
                        await self._write_text(node, value)
                    elif op == Operation.CLEAR:
                        if value is not None:
                            raise InvalidDecision("Clearing cannot include a replacement answer.")
                        if element.get("clear_proxy_id"):
                            await visible_node.click()
                        elif element.get("tag") == "select":
                            # Selecting no options differs from selecting a decline answer,
                            # and supports forms whose empty placeholder is disabled.
                            await node.select_option([])
                        elif element.get("type") == "checkbox":
                            if element.get('toggle_proxy_id'):
                                if await node.is_checked():
                                    await toggle_click_node.click()
                            else:
                                await node.set_checked(False)
                        elif element.get("type") == "radio":
                            # Browsers have no uncheck gesture for native radio inputs.
                            # Mutate this exact observed input only, then let the form's
                            # handlers update state. The runner independently re-observes
                            # and blocks if a controlled component restores the answer.
                            await node.evaluate("""node => {
                                if (!(node instanceof HTMLInputElement) || node.type !== 'radio')
                                    throw new Error('The observed radio control changed.');
                                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked')
                                    .set.call(node, false);
                                node.dispatchEvent(new Event('input', {bubbles:true}));
                                node.dispatchEvent(new Event('change', {bubbles:true}));
                                // React tracks checkable input changes from clicks.
                                // A generic Event notifies handlers without the
                                // activation behavior that would re-check a radio.
                                node.dispatchEvent(new Event('click', {bubbles:true}));
                            }""")
                        elif element.get("role") != "combobox" and "TYPE_TEXT" in element.get("operations", []):
                            await self._write_text(node, '')
                        else:
                            raise InvalidDecision("No supported observed clear operation exists for this control.")
                    elif op == Operation.SELECT:
                        values = value if isinstance(value, list) else [value]
                        if isinstance(value, list) and not element.get("multiple"):
                            raise InvalidDecision("This observed dropdown accepts a single value.")
                        if not values or any(not any(o["value"] == item and not o["disabled"]
                                                    for o in element["options"]) for item in values):
                            raise InvalidDecision("Only an observed enabled dropdown option may be selected.")
                        await node.select_option(value=value)
                    elif op in {Operation.CHECK, Operation.UNCHECK}:
                        desired = op == Operation.CHECK
                        if element.get('toggle_proxy_id'):
                            if await node.is_checked() != desired:
                                await toggle_click_node.click()
                        elif element["type"] in {"checkbox", "radio"}:
                            await node.set_checked(desired)
                        elif bool(element["checked"]) != desired:
                            await visible_node.click()
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
                        from .uploads import validate_upload, verify_upload
                        validate_upload(path, name, mime, element.get('accept') or '')
                        content = await asyncio.to_thread(path.read_bytes)
                        if len(content) > 50 * 1024 * 1024:
                            raise InvalidDecision("Application documents must be at most 50 MB.")
                        self._check_pause()
                        await node.set_input_files({"name": name, "mimeType": mime, "buffer": content})
                        deadline = time.monotonic()+20
                        while True:
                            try:
                                upload_evidence = await verify_upload(node, name)
                            except Exception as exc:
                                if isinstance(exc, InvalidDecision):
                                    raise
                                raise StaleState('The form reloaded during upload. Reinspect the attachment before continuing.') from exc
                            if not upload_evidence['busy'] and (upload_evidence['selected'] or upload_evidence['acknowledged']):
                                break
                            if time.monotonic() >= deadline:
                                raise HumanRequired('Upload did not finish within 20s. Inspect this attachment before retrying.')
                            await asyncio.sleep(.2)
                    elif op == Operation.OPEN_TAB:
                        url = safe_url(element.get("href", ""))
                        self._set_page(await self.context.new_page())
                        await self.page.goto(url, wait_until="domcontentloaded")
                    else:
                        pages_before = set(self.context.pages)
                        await node.click()
                        new_pages = [p for p in self.context.pages if p not in pages_before]
                        if new_pages:
                            self._set_page(new_pages[-1])
                            await self.page.wait_for_load_state("domcontentloaded")
                finally:
                    if self.edits:
                        await self.edits.automate(self.page, [])
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
                self._set_page(next(p for p in self.context.pages if p is not old_page))
                await old_page.close()
            else:
                raise InvalidDecision("Unsupported operation.")
            await asyncio.sleep(0.05)
            return {"operation": str(op), "target": decision.target, "label": element["label"] if element else None,
                    "confidence": decision.confidence, "executed": True, "url": self.page.url,
                    "decision_metadata": decision.metadata, "upload_evidence": upload_evidence}

    @staticmethod
    async def _write_text(node, value):
        # Check the synchronous user-intent marker and write in the same JS
        # task. Keyboard input arriving during browser IPC can never be lost.
        # Native setters also notify React without an artificial Delete key.
        written = await node.evaluate("""(node, value) => {
            if (node.hasAttribute('data-meridian-user-edited')) return false;
            if (!node.isConnected || node.disabled || node.readOnly) throw new Error('The observed text control changed.');
            if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
                const prototype = node instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
                Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value);
            } else if (node.isContentEditable) node.textContent = value;
            else throw new Error('The observed text control changed.');
            node.dispatchEvent(new Event('input', {bubbles:true}));
            node.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
        }""", value)
        if not written:
            raise UserEdit('The user changed this field before the write; preserve the correction.')

    @bounded(15)
    async def close(self) -> None:
        async with self._lifecycle_lock:
            context, driver = self.context, self._playwright
            try:
                if self.edits:
                    await self.edits.close()
            finally:
                self.edits = None
                self.page = self.context = self._playwright = None
                self._invalidate_approvals()
                await self._release(context, driver)
