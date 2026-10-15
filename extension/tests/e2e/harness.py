"""Real-browser harness for the Meridian Assistant extension (Playwright async API, Python 3.12).

What lives here: building the extension variants, the two launch recipes, service-worker discovery, activation through
the REAL toolbar listener, pairing shortcuts that use the real bridge, failure artifacts and capability probing.
(The visual-layer selectors are in ``ui.py``, the backend in ``backend.py``, fixtures/servers in ``servers.py``.)

Recipes (docs/EXTENSION_DESIGN.md section 9, verified in the chromium-harness investigation):

* ``chromium`` (default, CI): bundled Chromium through ``channel="chromium"`` with ``headless=True`` = the *new*
  headless mode, which supports ``--load-extension``. The default ``chromium-headless-shell`` does NOT load extensions.
* ``chrome``: branded Google Chrome (>= 137 ignores ``--load-extension``): launched with
  ``ignore_default_args=["--disable-extensions"]`` and loaded through the CDP command ``Extensions.loadUnpacked``.

Activation: Playwright cannot click the toolbar icon, so ``Rig.activate`` calls ``chrome.action.onClicked.dispatch``
from inside the service worker. That invokes the real registered production listener; no test hook is involved.
dispatch() returns before the async listener finishes, so activation ALWAYS waits for a readiness signal (the UI host
attached and the panel rendered) before any click or key.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, Worker
from playwright.async_api import TimeoutError as PlaywrightTimeout

sys.dont_write_bytecode = True

from backend import make_dir, temp_root  # noqa: E402
from scale import millis, seconds  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
# MERIDIAN_E2E_EXTENSION_DIR points the suite at another extension project root (the same layout: build.mjs, src/,
# node_modules). It exists for mutation testing: a scratch copy with one product fix reverted must make its scenario fail.
EXTENSION = Path(os.environ.get("MERIDIAN_E2E_EXTENSION_DIR") or REPO / "extension")

PINNED_EXTENSION_ID = "ophngbnbiekjhemmabbpildjedfphlbc"
SLOW_ACTIVATIONS: list[float] = []  # seconds of every activation that took more than 5 s (printed in the summary)
# The production build looks for Meridian on the FIXED ports 47615-47624. Scenarios that need those ports (a production
# build talking to a test backend, or a production build that must find nothing there) take this lock, so they never overlap.
FIXED_PORTS_LOCK = asyncio.Lock()
UI_HOST = "[data-meridian-assistant]"
# Keys of chrome.storage (extension/src/shared/storage.ts, connection.ts)
STORAGE_CONNECTION = "meridian.connection"
STORAGE_SCHEMA = "meridian.schema"
STORAGE_CONN_CACHE = "meridian.conn"
E2E_PORTS_KEY = "meridian.e2e.ports"
VIEWPORT = {"width": 1280, "height": 900}


class HarnessError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- building
_BUILT: dict[str, Path] = {}


def build_extension(variant: str) -> Path:
    """Build one extension variant into this run's private temp dir (never into extension/dist, which other
    workstreams and the app build also write). Cached for the run."""
    if variant in _BUILT:
        return _BUILT[variant]
    if not (EXTENSION / "node_modules" / "esbuild").exists():
        raise HarnessError("extension/node_modules is missing: run `npm --prefix extension ci` first")
    out = make_dir(f"ext-{variant}-")
    result = subprocess.run(
        ["node", str(EXTENSION / "build.mjs"), f"--variant={variant}", f"--out={out}"],
        cwd=str(EXTENSION), capture_output=True, text=True, timeout=seconds(180), check=False,
    )
    if result.returncode != 0:
        raise HarnessError(f"building the {variant} extension failed:\n{result.stdout}\n{result.stderr}")
    manifest = json.loads((out / "manifest.json").read_text())
    _BUILT[variant] = out
    _BUILT[variant + ":manifest"] = manifest  # type: ignore[assignment]
    return out


def manifest_of(variant: str) -> dict:
    build_extension(variant)
    return _BUILT[variant + ":manifest"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------- launching
async def wait_for_extension_worker(ctx: BrowserContext, timeout: float = 20.0) -> Worker:
    """Race-free: poll ``ctx.service_workers`` (the 'serviceworker' event can fire before anybody subscribed). An event wait (scale.py)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds(timeout)
    while loop.time() < end:
        for worker in ctx.service_workers:
            if worker.url.startswith("chrome-extension://"):
                return worker
        await asyncio.sleep(0.1)
    raise HarnessError("the extension service worker did not start (is the extension built and loaded?)")


async def launch_with_extension(p: Playwright, ext_dir: Path, *, recipe: str = "chromium", headless: bool = True,
                                profile: Path | None = None) -> BrowserContext:
    profile = profile or make_dir("meridian-e2e-profile-")
    common = dict(headless=headless, viewport=dict(VIEWPORT), timeout=millis(40_000), locale="en-US",
                  color_scheme="light", accept_downloads=False)
    if recipe == "chromium":
        return await p.chromium.launch_persistent_context(
            str(profile), channel="chromium",
            args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"], **common)
    if recipe == "chrome":
        ctx = await p.chromium.launch_persistent_context(
            str(profile), channel="chrome", args=["--enable-unsafe-extension-debugging"],
            ignore_default_args=["--disable-extensions"], **common)
        cdp = await ctx.browser.new_browser_cdp_session()
        await cdp.send("Extensions.loadUnpacked", {"path": str(ext_dir)})
        return ctx
    raise HarnessError(f"unknown recipe {recipe!r}")


# ---------------------------------------------------------------------------------------------- the rig
@dataclass
class Rig:
    """One browser with the extension loaded, plus conveniences. ``page`` is a fresh blank test page."""

    ctx: BrowserContext
    sw: Worker
    ext_id: str
    page: Page
    variant: str
    profile: Path
    pages: list[Page] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    _bridge_page: Page | None = None

    # ------------------------------------------------------------------ pages
    def watch(self, page: Page) -> Page:
        """Collect uncaught page errors and console errors (a clean run has none)."""
        if page not in self.pages:
            self.pages.append(page)
            page.on("pageerror", lambda exc: self.errors.append(f"{page.url}: {exc}"))
            page.on("console", lambda msg: self.console.append(f"{msg.type}: {msg.text}")
                    if msg.type in {"error", "warning"} else None)
        return page

    async def new_page(self) -> Page:
        return self.watch(await self.ctx.new_page())

    async def goto(self, url: str, page: Page | None = None) -> Page:
        page = page or self.page
        await page.goto(url, wait_until="domcontentloaded")
        return page

    # ------------------------------------------------------------------ extension internals
    async def tab_id(self, page: Page | None = None) -> int:
        """Chrome tab id of ``page``. Resolved by URL (needs the loopback host permission of the e2e build)."""
        page = page or self.page
        url = page.url
        # Several tabs may show the same URL (a second tab on the same fixture): prefer the one in front.
        found = await self.sw.evaluate(
            """(u) => chrome.tabs.query({}).then((ts) => { const same = ts.filter((t) => t.url === u);
               return ((same.find((t) => t.active) || same[same.length - 1]) || {}).id ?? null; })""", url)
        if found is None:
            raise HarnessError(f"no tab with url {url}")
        return found

    async def storage_local(self) -> dict:
        return await self.sw.evaluate("() => chrome.storage.local.get(null)")

    async def storage_session(self) -> dict:
        return await self.sw.evaluate("() => chrome.storage.session.get(null)")

    async def set_bridge_ports(self, ports: list[int]) -> None:
        """Test-only candidate-port override honoured by __E2E__ builds (shared/connection.ts)."""
        await self.sw.evaluate("(a) => chrome.storage.local.set(a)", {E2E_PORTS_KEY: ports})
        await self.forget_connection_cache()

    async def forget_connection_cache(self) -> None:
        await self.sw.evaluate("(k) => chrome.storage.session.remove(k)", STORAGE_CONN_CACHE)

    async def stored_connection(self) -> dict | None:
        return (await self.storage_local()).get(STORAGE_CONNECTION)

    async def install_connection(self, connection: dict) -> None:
        """Store a credential exactly as the worker stores one after a successful pairing."""
        await self.sw.evaluate("(a) => chrome.storage.local.set(a)",
                               {STORAGE_SCHEMA: 1, STORAGE_CONNECTION: connection})
        await self.forget_connection_cache()

    async def clear_connection(self) -> None:
        await self.sw.evaluate("(k) => chrome.storage.local.remove(k)", STORAGE_CONNECTION)
        await self.forget_connection_cache()

    # ------------------------------------------------------------------ activation
    async def toolbar_click(self, page: Page | None = None) -> None:
        """Run the REAL ``chrome.action.onClicked`` listener (what a click on the toolbar icon does)."""
        tab = await self.tab_id(page)
        await self.sw.evaluate(
            "(id) => chrome.tabs.get(id).then((t) => { chrome.action.onClicked.dispatch(t); })", tab)

    async def activate(self, page: Page | None = None, *, wait_panel: bool = True) -> None:
        """Toolbar click, then wait until the assistant shows (UI host attached, panel rendered). The product must get
        there by itself (a slow first connection check shows 'connecting', never nothing): there is no rescue here, and a
        wait that takes unusually long is printed so a slow machine shows up in the report instead of hiding."""
        page = page or self.page
        await page.bring_to_front()
        started = time.monotonic()
        await self.toolbar_click(page)
        try:
            await page.locator(UI_HOST).first.wait_for(state="attached", timeout=millis(30_000))
        except PlaywrightTimeout:
            raise PlaywrightTimeout(f"no assistant UI 30 s after the toolbar click: {(await self.assist_debug(page))[:300]}") from None
        if wait_panel:
            await page.locator("[data-testid=panel], [data-testid=panel-pill]").first.wait_for(state="visible", timeout=millis(15_000))
        waited = time.monotonic() - started
        if waited > 5:
            SLOW_ACTIVATIONS.append(waited)
            print(f"    (activation took {waited:.1f}s)", flush=True)

    async def ensure_active(self, page: Page | None = None, *, wait_panel: bool = True, settle: float = 8.0) -> None:
        """For a page that has just been RELOADED: the worker starts the assistant again by itself after a same-site reload, and a
        toolbar click on a running assistant switches it OFF. So wait for it to come back, and click only if it does not."""
        page = page or self.page
        await page.bring_to_front()
        try:
            await page.locator(UI_HOST).first.wait_for(state="attached", timeout=int(settle * 1000))
        except PlaywrightTimeout:
            await self.activate(page, wait_panel=wait_panel)
            return
        if wait_panel:
            await page.locator("[data-testid=panel], [data-testid=panel-pill]").first.wait_for(state="visible", timeout=millis(15_000))

    async def deactivate_by_click(self, page: Page | None = None) -> None:
        await self.toolbar_click(page)

    async def assist_debug(self, page: Page | None = None) -> str:
        """The content controller's own debug snapshot of a page (__meridianAssist.debug, e2e builds only)."""
        try:
            tab = await self.tab_id(page)
            result = await self.sw.evaluate(
                """(id) => chrome.scripting.executeScript({target: {tabId: id, allFrames: true}, world: 'ISOLATED', func: () => {
                    try { return JSON.stringify(globalThis.__meridianAssist.debug()); } catch (e) { return 'n/a: ' + e; } }})
                    .then((rs) => rs.map((r) => r.result))""", tab)
            return "\n".join(str(item) for item in result)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the real failure
            return f"(debug snapshot unavailable: {type(exc).__name__})"

    async def polling(self) -> dict:
        """The worker's shared long-poll state (e2e hook of the e2e builds): waits held, request in flight, many|single, counters."""
        return await self.sw.evaluate("() => globalThis.__e2e.polling()")

    async def set_visibility(self, page: Page, visible: bool) -> None:
        """Make the content script of ``page`` believe its tab is hidden (or visible again), exactly as the browser would:
        the content script reads ``document.visibilityState`` in its isolated world and listens for ``visibilitychange``
        there. Headless Chromium reports every tab as visible, so a hidden tab cannot be had any other way; a headed run
        really hides background tabs as well, and the override is then redundant."""
        tab = await self.tab_id(page)
        await self.sw.evaluate(
            """([id, hidden]) => chrome.scripting.executeScript({target: {tabId: id, allFrames: true}, world: 'ISOLATED', args: [hidden],
                func: (hide) => {
                  if (hide) {
                    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
                    Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                  } else {
                    delete document.visibilityState;
                    delete document.hidden;
                  }
                  document.dispatchEvent(new Event('visibilitychange'));
                }})""", [tab, not visible])

    async def slow_clock(self, page: Page, factor: int = 50) -> None:
        """The content script's own clock (``Date.now`` of its isolated world) runs ``factor`` times slower from now on, until ``restore_clock``.
        A rule that says "on screen for 500 ms" can only be tested at the speed of the machine otherwise: on a loaded one the harness itself needs
        that long between the draft appearing and the key press, and the check turns inconclusive (a version of the product with NO dwell
        passed it). Timers keep their real speed and nothing waits on this clock in the window the scenario uses; it is not frozen, so no loop can spin."""
        tab = await self.tab_id(page)
        await self.sw.evaluate(
            """([id, factor]) => chrome.scripting.executeScript({target: {tabId: id, allFrames: true}, world: 'ISOLATED', args: [factor],
                func: (slow) => {
                  if (globalThis.__realDateNow) return;
                  const real = Date.now.bind(Date), origin = real();
                  globalThis.__realDateNow = real;
                  Date.now = () => origin + Math.floor((real() - origin) / slow);
                }})""", [tab, factor])

    async def restore_clock(self, page: Page) -> None:
        tab = await self.tab_id(page)
        await self.sw.evaluate(
            """(id) => chrome.scripting.executeScript({target: {tabId: id, allFrames: true}, world: 'ISOLATED',
                func: () => { if (globalThis.__realDateNow) { Date.now = globalThis.__realDateNow; delete globalThis.__realDateNow; } }})""", tab)

    async def open_options(self) -> Page:
        page = await self.new_page()
        await page.goto(f"chrome-extension://{self.ext_id}/options.html")
        await page.wait_for_load_state("load")
        return page

    # ------------------------------------------------------------------ talking to the worker like a content script
    async def bg_request(self, message: dict) -> dict:
        """Send a content->background message (messages.ts BgRequest) to the worker's real router and return the
        BgResponse. Goes through a tiny extension page (e2e.html, present in e2e builds), as a content script would."""
        if self._bridge_page is None or self._bridge_page.is_closed():
            self._bridge_page = await self.ctx.new_page()
            await self._bridge_page.goto(f"chrome-extension://{self.ext_id}/e2e.html")
        return await self._bridge_page.evaluate("(m) => window.send(m)", message)

    # ------------------------------------------------------------------ teardown
    async def close(self) -> None:
        try:
            await self.ctx.close()
        except Exception:  # noqa: BLE001 - already gone
            pass


async def start_rig(p: Playwright, *, variant: str = "e2e", recipe: str = "chromium", headless: bool = True,
                    profile: Path | None = None) -> Rig:
    ext_dir = build_extension(variant)
    profile = profile or make_dir("meridian-e2e-profile-")
    ctx = await launch_with_extension(p, ext_dir, recipe=recipe, headless=headless, profile=profile)
    try:
        sw = await wait_for_extension_worker(ctx)
    except HarnessError:
        await ctx.close()
        raise
    ext_id = sw.url.split("/")[2]
    page = await ctx.new_page()
    rig = Rig(ctx=ctx, sw=sw, ext_id=ext_id, page=page, variant=variant, profile=profile)
    rig.watch(page)
    # The extension may open its welcome page on install; leave it alone but never confuse it with a test page.
    return rig


# ---------------------------------------------------------------------------------------------- fast pairing
async def pair_directly(rig: Rig, backend, *, name: str = "E2E Chrome") -> dict:
    """Pair this browser with the real bridge WITHOUT the panel: same protocol the worker speaks (pair/start with the
    extension origin, approval through the in-app API, one claim of the token), then store the credential exactly as
    the worker does. Scenarios that are not about pairing use this to start 'connected'."""
    import httpx

    origin = f"chrome-extension://{rig.ext_id}"
    headers = {"Origin": origin, "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=backend.bridge_base, timeout=15, trust_env=False) as bridge:
        started = await bridge.post("/v1/pair/start", headers=headers,
                                    json={"client_name": name, "extension_version": "0.0.0-e2e", "locale": "en-US"})
        if started.status_code != 200:
            raise HarnessError(f"pair/start -> {started.status_code} {started.text[:300]}")
        info = started.json()
        await backend.approve_pairing({"id": await _pairing_row_id(backend, info["code"]), "code": info["code"]})
        claimed = await bridge.get(f"/v1/pair/{info['pairing_id']}", headers={"Origin": origin})
        data = claimed.json()
    if data.get("state") != "approved" or not data.get("token"):
        raise HarnessError(f"pairing was not approved: {data}")
    connection = {"port": backend.bridge_port, "clientId": data["client_id"], "token": data["token"],
                  "clientName": name, "pairedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    await rig.install_connection(connection)
    return connection


async def _pairing_row_id(backend, code: str) -> str:
    status = await backend.extension_status()
    pending = status.get("pending_pairing")
    if not pending or pending.get("code") != code:
        raise HarnessError(f"the app does not show the pairing with code {code}: {pending}")
    return pending["id"]


# ---------------------------------------------------------------------------------------------- artifacts
_FAILURE_DIR: Path | None = None


def failure_dir() -> Path:
    """Directory for screenshots of failed scenarios. Printed by the runner; kept after the run on purpose."""
    global _FAILURE_DIR
    if _FAILURE_DIR is None:
        import tempfile

        _FAILURE_DIR = Path(tempfile.mkdtemp(prefix="meridian-e2e-failures-"))
    return _FAILURE_DIR


async def save_failure(scenario: str, rig: Rig | None, backend=None, error: BaseException | None = None) -> list[str]:
    saved: list[str] = []
    folder = failure_dir() / re.sub(r"[^A-Za-z0-9_.-]", "_", scenario)
    folder.mkdir(parents=True, exist_ok=True)
    if rig is not None:
        for index, page in enumerate(rig.pages):
            if page.is_closed():
                continue
            target = folder / f"page{index}.png"
            try:
                await page.screenshot(path=str(target), timeout=8_000)
                saved.append(str(target))
                (folder / f"page{index}.url").write_text(page.url)
                (folder / f"page{index}.assist.json").write_text(await rig.assist_debug(page))
            except Exception:  # noqa: BLE001 - a dying page must not hide the real failure
                continue
        if rig.errors or rig.console:
            (folder / "browser-console.txt").write_text("\n".join(rig.errors + rig.console))
        try:  # what the worker believed (no secrets): is a credential stored, was it marked revoked, what phase it cached
            local, session = await rig.storage_local(), await rig.storage_session()
            conn = session.get("meridian.conn") or {}
            (folder / "worker-state.json").write_text(json.dumps({
                "local_keys": sorted(local), "credential_stored": bool(local.get(STORAGE_CONNECTION)),
                "stored_port": (local.get(STORAGE_CONNECTION) or {}).get("port"), "session_keys": sorted(session),
                "revoked_flag": session.get("meridian.revoked"), "cached_phase": (conn.get("state") or {}).get("phase"),
                "cached_port": (conn.get("state") or {}).get("port"), "verified_at": conn.get("verifiedAt"), "checked_at": conn.get("at"),
                "polling": await rig.polling() if rig.variant.startswith("e2e") else None}, indent=1, default=str))
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real failure
            pass
    if backend is not None:
        (folder / "backend.log").write_text(redact_tokens(backend.log_tail(8000)))
    if error is not None:
        (folder / "error.txt").write_text("".join(traceback.format_exception(error)))
    return saved


def redact_tokens(text: str) -> str:
    return re.sub(r"mrd1\.[A-Za-z0-9_.-]+", "mrd1.<redacted>", text)


# ---------------------------------------------------------------------------------------------- capabilities
def _read(path: str) -> str:
    try:
        return (REPO / path).read_text()
    except OSError:
        return ""


def _exists(path: str) -> bool:
    return (REPO / path).exists()


def capabilities() -> dict[str, tuple[bool, str]]:
    """Which parts of the product exist in THIS checkout. Scenarios declare what they need; a scenario whose needs are
    missing is reported BLOCKED with the owning workstream instead of failing (``--run-blocked`` runs them anyway)."""
    main_py = _read("backend/jobagent/main.py")
    worker_src = _read("extension/src/background/index.ts")
    content_src = _read("extension/src/content/index.ts")
    research = _read("backend/jobagent/extension/research.py")
    routes = _read("backend/jobagent/extension/routes.py")
    return {
        "bridge": ("bridge=False" in main_py and _exists("backend/jobagent/extension/app.py"),
                   "workstream A: create_app(bridge=...) and jobagent/extension/app.py"),
        "bridge_routes": (_exists("backend/jobagent/extension/routes.py") and "/sessions" in routes,
                          "workstream B: jobagent/extension/routes.py (sessions, suggest, poll, feedback, track, documents)"),
        "resolver": (_exists("backend/jobagent/extension/resolver.py"),
                     "workstream B: jobagent/extension/resolver.py"),
        "research_hosts": ("MERIDIAN_EXTENSION_RESEARCH_LOOPBACK" in research,
                           "workstream B: research fetcher honouring MERIDIAN_EXTENSION_RESEARCH_LOOPBACK under MERIDIAN_TEST"),
        "worker": ("onClicked" in worker_src and len(worker_src.splitlines()) > 20,
                   "workstream C: extension/src/background/index.ts (service worker)"),
        "e2e_ports": ("meridian.e2e.ports" in _read("extension/src/shared/connection.ts"),
                      "workstream C: the __E2E__ candidate-port override (storage key meridian.e2e.ports)"),
        "content": (_exists("extension/src/content/controller.ts") and len(content_src.splitlines()) > 20,
                    "workstream D1: extension/src/content/controller.ts wired by index.ts"),
        "ui": ("createUI" in _read("extension/src/content/ui/create.ts") and "createUI" in content_src,
               "workstream D2: extension/src/content/ui/create.ts (AssistUI) used by index.ts"),
    }


def missing(needs: tuple[str, ...]) -> list[str]:
    caps = capabilities()
    return [f"{name} ({caps[name][1]})" for name in needs if not caps.get(name, (False, name))[0]]


def real_bridge_listening() -> list[int]:
    """Candidate bridge ports (47615-47624) that accept a TCP connection right now: a real Meridian app (or anything
    else) is there. Production-build scenarios refuse to run then, so they can never talk to the tester's app."""
    import socket

    busy = []
    for port in range(47615, 47625):
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    return busy


def cleanup_all() -> None:
    from backend import cleanup_temp

    cleanup_temp()
    _BUILT.clear()


__all__ = [
    "PINNED_EXTENSION_ID", "UI_HOST", "Rig", "HarnessError", "start_rig", "build_extension", "manifest_of",
    "pair_directly", "capabilities", "missing", "save_failure", "failure_dir", "cleanup_all", "temp_root",
    "real_bridge_listening",
]
