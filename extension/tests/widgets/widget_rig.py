"""Browser rig of the real-widget suite. It REUSES the content-script rig (extension/tests/content/harness.py: fixture
servers, the mock background `Bg` with canned suggestions, the key/debug/chip helpers) and adds

* the widget fixture folder to the servers,
* an ISOLATED-WORLD mode: mock extension runtime + the real content bundle are injected into a CDP isolated world, so the
  extension sees the page exactly like a real content script does (own prototypes, own globals, page-owned custom element
  members and framework value trackers invisible to it); the page world only keeps the Playwright binding and tiny proxies,
* soft per-widget assertions that record PASS / FAIL / XFAIL per widget instead of stopping at the first failure.

World "main" (what tests/content/run.py does) is kept for WebKit, where Playwright has no isolated worlds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

HERE = Path(__file__).resolve().parent
EXTENSION = HERE.parents[1]
CONTENT_RIG = HERE.parent / "content"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(CONTENT_RIG))

import harness as content_harness  # noqa: E402  (tests/content/harness.py)
from harness import (  # noqa: E402,F401  (re-exported for the scenarios)
    BOX, BOXES, COMBO, FILE, NEEDS, RADIO, SELECT, STATE, T, Bg, Rig, Servers, active_name, chip_for, debug, emit, eventually,
    press_fill_verified, last_chip, last_panel, make_document, normalize, sug, ui_log, value_of,
)

if FIXTURES not in content_harness.FIXTURE_DIRS:
    content_harness.FIXTURE_DIRS.insert(0, FIXTURES)  # first: the widget pages win over same-named ATS fixtures of the other suites

MOCK_ISOLATED = HERE / "mock_isolated.js"
MAIN_SHIM = HERE / "main_shim.js"
WORLD = "meridian-content-script"


class WidgetRig(Rig):
    """`Rig` whose pages can run the content script in an isolated world."""

    def __init__(self, browser_name: str, servers: Servers, content_js: Path, headless: bool = True) -> None:
        super().__init__(browser_name, servers, headless=headless)
        self.content_js = content_js

    async def open(  # type: ignore[override]
        self, path: str, bg: Bg | None = None, *, host: str = "main", activate: bool = True, ready: bool = True,
        headless_ui: bool = True, viewport: tuple[int, int] = (1200, 900), init: str = "", wait_frames: int = 0,
        world: str = "isolated",
    ) -> tuple[Any, Bg]:
        if world == "main" or self.browser_name != "chromium":
            return await self._open_main(path, bg, host=host, activate=activate, ready=ready, headless_ui=headless_ui,
                                         viewport=viewport, init=init, wait_frames=wait_frames)
        bg = bg or Bg()
        ctx = await self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.contexts.append(ctx)
        page = await ctx.new_page()
        bg.page = page
        await page.expose_binding("__bg", bg.handle)
        await ctx.add_init_script(path=str(MAIN_SHIM))
        if init:
            await ctx.add_init_script(init)
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Page.enable")
        source = "\n".join([
            MOCK_ISOLATED.read_text("utf-8"),
            "window.__MERIDIAN_HEADLESS_UI__ = true;" if headless_ui else "",
            self.content_js.read_text("utf-8"),
        ])
        await cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": source, "worldName": WORLD})
        await page.goto(self.servers.url(path, host), wait_until="load")
        if wait_frames:
            await eventually(lambda: len(page.frames) >= wait_frames + 1, 8, "iframes load")
        if activate:
            await self.activate(page, bg)
            if ready:
                await self.ready(page, bg)
        return page, bg

    async def _open_main(self, path: str, bg: Bg | None, **kw: Any) -> tuple[Any, Bg]:
        bg = bg or Bg()
        ctx = await self.browser.new_context(viewport={"width": kw["viewport"][0], "height": kw["viewport"][1]})
        self.contexts.append(ctx)
        page = await ctx.new_page()
        bg.page = page
        await page.expose_binding("__bg", bg.handle)
        await ctx.add_init_script(path=str(content_harness.MOCK_JS))
        if kw["headless_ui"]:
            await ctx.add_init_script("window.__MERIDIAN_HEADLESS_UI__ = true;")
        if kw["init"]:
            await ctx.add_init_script(kw["init"])
        await ctx.add_init_script(path=str(self.content_js))
        await page.goto(self.servers.url(path, kw["host"]), wait_until="load")
        if kw["wait_frames"]:
            await eventually(lambda: len(page.frames) >= kw["wait_frames"] + 1, 8, "iframes load")
        if kw["activate"]:
            await self.activate(page, bg)
            if kw["ready"]:
                await self.ready(page, bg)
        return page, bg


# ------------------------------------------------------------------------------------------------------ results


@dataclass
class Outcome:
    scenario: str
    widget: str
    status: str  # PASS | FAIL | XFAIL | XPASS
    detail: str = ""
    seconds: float = 0.0
    handoff: str = ""


@dataclass
class Recorder:
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)
        mark = {"PASS": "PASS ", "FAIL": "FAIL ", "XFAIL": "XFAIL", "XPASS": "XPASS"}[outcome.status]
        extra = f"   [{outcome.detail}]" if outcome.detail and outcome.status != "PASS" else ""
        print(f"    {mark} {outcome.widget}{extra}", flush=True)


class Soft:
    """`async with ctx.widget("react-select/single"):` records the verdict of one widget; the scenario goes on after a failure.

    `xfail="why / hand-off"` marks a known gap owned elsewhere: a failure is XFAIL (accepted), a pass is XPASS (reported as a
    failure of the expectation so stale marks get removed). Only assertion-like failures count as XFAIL: a crash of the
    scenario code itself (NameError, TypeError ...) is always a FAIL.
    """

    ACCEPTED = (AssertionError, TimeoutError, asyncio.TimeoutError)

    def __init__(self, recorder: Recorder, scenario: str) -> None:
        self.recorder = recorder
        self.scenario = scenario

    @contextlib.asynccontextmanager
    async def widget(self, name: str, *, xfail: str | None = None, handoff: str = "") -> AsyncIterator[None]:
        started = time.time()
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 (recorded, then the scenario continues; cancellation re-raised below)
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            detail = _brief(exc)
            if xfail and isinstance(exc, self.ACCEPTED):
                self.recorder.add(Outcome(self.scenario, name, "XFAIL", f"{xfail}: {detail}", time.time() - started, handoff))
            else:
                if not isinstance(exc, self.ACCEPTED):
                    detail += "  | " + " / ".join(traceback.format_exc().strip().splitlines()[-3:])
                self.recorder.add(Outcome(self.scenario, name, "FAIL", detail, time.time() - started, handoff))
        else:
            if xfail:
                self.recorder.add(Outcome(self.scenario, name, "XPASS", f"expected to fail ({xfail}) but passed: remove the mark", time.time() - started, handoff))
            else:
                self.recorder.add(Outcome(self.scenario, name, "PASS", "", time.time() - started))


def _brief(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text if len(text) < 600 else text[:597] + "..."


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
