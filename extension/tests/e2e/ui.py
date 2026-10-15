"""Selectors and interaction helpers for the extension's visual layer and for the fixture pages.

The extension renders in a shadow root that is OPEN in the e2e builds, so Playwright locators pierce it (the PRODUCTION build's
root is closed: ``ClosedChip`` reads it through the DevTools protocol). Everything is addressed through the ``data-testid`` and
``data-*`` attributes of extension/src/content/ui/*.ts; strings are never matched except where a fixture value (not UI copy) is the
thing being asserted.

Selectors the UI must keep providing (the contract of this suite):

chip     [data-testid=chip] with data-state (SuggestionState) data-tone data-insert data-conflict data-key data-quiet
         data-pill (the one-line pill: chip-pill-text instead of chip-value, no chip-label/chip-why/chip-reason) data-placement
         (above | below | beside | overlay: where the visual layer put it);
         chip-question (the PAGE's own label of the question: the chip's first line, never a leak) chip-value chip-trust
         chip-label chip-reason chip-why chip-sources chip-source chip-status chip-undo chip-accept chip-replace chip-draft
         chip-dismiss chip-regen-again|shorter|longer|different hint-tab hint-enter hint-esc chip-dwell chip-duplicate
         chip-protected chip-review key-<n> (the numbered action chords; aria-keyshortcuts carries the chord)
panel    panel panel-card[data-phase] panel-pill panel-conn[data-phase] panel-job panel-sub panel-progress
         progress-summary progress-inserted progress-item[data-key] panel-fill-verified panel-drafts panel-keys
         panel-collapse panel-more panel-deactivate panel-research[data-state] research-headline research-count
         research-toggle research-source research-not-this research-add-site research-refresh calm-<phase>
         calm-connect calm-retry calm-cancel calm-no-form calm-profile-empty pairing-code panel-first-use first-steps
         first-shortcut first-dismiss panel-review review-item review-more review-dismiss panel-duplicate[data-state] dup-title
         dup-hint dup-continue panel-fill-verified panel-fill-preview fill-preview-item fill-confirm fill-cancel panel-batch-undo
         panel-undo-all panel-more-box panel-track-save panel-track-applied panel-always-on panel-always-on-permission
prompts  save-ask save-application save-reuse save-no track-ask track-record track-dismiss toast[data-kind]
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from playwright.async_api import Frame, FrameLocator, Locator, Page, expect

from scale import millis

CHIP = "[data-testid=chip]"
PANEL = "[data-testid=panel]"
DEFAULT_TIMEOUT = millis(10_000)  # chips normally appear within a second; the budget is for a machine under heavy load (an event wait: it never slows a pass; see scale.py)

Root = Page | Frame | FrameLocator


# ---------------------------------------------------------------------------------------------- the chip
@lru_cache(maxsize=4)
def dictionary(locale: str) -> dict[str, str]:
    """The extension's own strings of one locale, read from content/i18n.ts (one 'key': 'value' per line): what a scenario compares UI copy
    with, so that no sentence of the product is repeated in the suite. Plural entries (objects) are left out."""
    source = (Path(__file__).resolve().parents[3] / "extension" / "src" / "content" / "i18n.ts").read_text()
    start = source.index("const en = {" if locale == "en" else "const es: Record<StringKey, string> = {")
    end = start + re.search(r"^\}", source[start:], flags=re.M).start()  # the block ends at its first closing brace in column 0
    strings: dict[str, str] = {}
    for key, raw in re.findall(r'^  "([^"]+)": ("(?:[^"\\]|\\.)*"),?\s*$', source[start:end], flags=re.M):
        try:
            strings[key] = json.loads(raw)
        except ValueError:
            strings[key] = raw[1:-1]
    return strings


def value_shown(wanted: str, shown: str) -> bool:
    """Does what a chip shows stand for ``wanted``? The whole value, or - for the one-line pill, which cuts a long answer and ends it with an
    ellipsis - its first characters."""
    if wanted in shown:
        return True
    if shown.endswith("\u2026"):
        stem = shown[:-1].rstrip()
        return len(stem) >= 12 and wanted.startswith(stem)
    return False


@dataclass
class Chip:
    """The chip of one frame. ``root`` is the page (top frame) or a FrameLocator (an iframe)."""

    root: Root

    @property
    def el(self) -> Locator:
        """The chip currently on screen. The UI keeps a hidden (class is-hidden) chip element after hiding, so only
        visible ones count."""
        return self.root.locator(f"{CHIP} >> visible=true")

    def part(self, testid: str) -> Locator:
        return self.el.locator(f"[data-testid={testid}]")

    @property
    def value(self) -> Locator:
        """The answer the chip shows: the full chip's value line, or the text of its one-line pill (the chip folds to a pill when the form
        leaves it no room, and while the person types)."""
        return self.el.locator("[data-testid=chip-value], [data-testid=chip-pill-text]")

    async def shown_value(self) -> str:
        """The answer the chip shows, without the short operation word ("Select", "Attach") a full chip puts in front of it."""
        return (await self.el.first.evaluate(
            """(chip) => { const node = chip.querySelector('[data-testid=chip-value], .pill-value');
                if (!node) return ''; const copy = node.cloneNode(true);
                copy.querySelectorAll('.op, kbd').forEach((el) => el.remove()); return copy.textContent.trim(); }""")).strip()

    async def expect_skip(self, *, code: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
        """An upload the assistant leaves alone (a cover letter, a transcript ...) shows a quiet chip that says why: state SKIP, nothing to
        insert (CPL-06), and its label and reason are the extension's own words for one of the skip reasons (``code`` names which one is
        expected). Waits for it (a moment after focus moved the previous field's chip can still be on screen). Returns the reason's code."""
        strings = dictionary("en")
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout / 1000
        seen = "no chip"
        while True:
            try:
                if await self.el.count():
                    attributes = await self.attributes()
                    label = (await self.part("chip-label").first.inner_text(timeout=1_000)).strip() if await self.part("chip-label").count() else ""
                    reason = (await self.part("chip-reason").first.inner_text(timeout=1_000)).strip() if await self.part("chip-reason").count() else ""
                    codes = [c for c in ("file_unapproved", "file_cover_letter", "file_none", "generic")
                             if strings.get(f"chip.skip.label.{c}") == label and strings.get(f"chip.skip.reason.{c}") == reason]
                    seen = f"{attributes.get('data-state')}/{attributes.get('data-insert')} {label!r} / {reason!r}"
                    if attributes.get("data-state") == "SKIP" and attributes.get("data-insert") == "never" and codes \
                            and (code is None or code in codes) and await self.part("chip-accept").count() == 0:
                        return codes[0]
            except Exception:  # noqa: BLE001 - the chip re-renders: look again
                pass
            if loop.time() >= end:
                raise AssertionError(f"no quiet SKIP chip with the reason {code or '(any)'} within {timeout} ms; on screen: {seen}")
            await asyncio.sleep(0.1)

    async def describe(self) -> str:
        """What the visible chip says right now (for failure messages)."""
        try:
            if await self.el.count() == 0:
                return "no chip visible"
            text = (await self.el.first.inner_text(timeout=2_000)).replace("\n", " | ")
            return f"{await self.attributes()} {text[:200]}"
        except Exception as exc:  # noqa: BLE001
            return f"(unreadable: {type(exc).__name__})"

    async def attributes(self) -> dict[str, str | None]:
        if await self.el.count() == 0:
            return {}
        names = ("data-state", "data-tone", "data-insert", "data-conflict", "data-key", "data-quiet", "data-placement", "data-pill")
        return {name: await self.el.first.get_attribute(name) for name in names}

    async def expect_value(self, text: str, *, timeout: int = DEFAULT_TIMEOUT, exact: bool = False) -> None:
        """The chip shows ``text`` (a fixture value). ``chip-value`` may carry a short operation prefix; the one-line pill shows the first
        characters of a long answer and an ellipsis (`value_shown`)."""
        locator = self.value
        if exact:
            await expect(locator).to_have_text(text, timeout=timeout)
            return
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout / 1000
        shown = ""
        while True:
            try:
                shown = (await locator.first.inner_text(timeout=1_000)).strip() if await locator.count() else ""
            except Exception:  # noqa: BLE001 - the chip re-renders: look again
                shown = ""
            if value_shown(text, shown):
                return
            if loop.time() >= end:
                raise AssertionError(f"Locator expected to contain text {text!r}\nActual value: {shown!r}")
            await asyncio.sleep(0.1)

    async def expect_state(self, state: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        await expect(self.el.first).to_have_attribute("data-state", state, timeout=timeout)

    async def expect_tone(self, tone: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        await expect(self.el.first).to_have_attribute("data-tone", tone, timeout=timeout)

    async def expect_insert(self, insert: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        await expect(self.el.first).to_have_attribute("data-insert", insert, timeout=timeout)

    async def expect_none(self, *, timeout: int = 3_000) -> None:
        await expect(self.el).to_have_count(0, timeout=timeout)

    async def expect_chip(self, *, value: str | None = None, state: str | None = None, tone: str | None = None,
                          insert: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        """Wait until a chip with these properties is on screen. NEVER press Tab before this (except in the
        dedicated 'Tab immediately' scenario): the key handler decides from the data it has when the key goes down."""
        try:
            await expect(self.el.first).to_be_visible(timeout=timeout)
            if state is not None:
                await self.expect_state(state, timeout=timeout)
            if tone is not None:
                await self.expect_tone(tone, timeout=timeout)
            if insert is not None:
                await self.expect_insert(insert, timeout=timeout)
            if value is not None:
                await self.expect_value(value, timeout=timeout)
        except AssertionError as error:
            raise AssertionError(f"{str(error)[:300]}\n  wanted chip: state={state} tone={tone} insert={insert} value={value!r}"
                                 f"\n  on screen: {await self.describe()}") from None

    async def wait_dwell_over(self, *, timeout: int = 8_000) -> None:
        """A draft only goes in with Tab after it has been on screen for 500 ms: wait for the chip to say so."""
        await expect(self.part("chip-dwell").first).to_be_hidden(timeout=timeout)

    async def click(self, testid: str) -> None:
        """Click one of the chip's buttons with a real (trusted) mouse click."""
        await self.part(testid).click()


# ---------------------------------------------------------------------------------------------- the chip of a CLOSED root
_CLOSED_READ = """function () {
  const shown = (el) => { const rects = el.getClientRects(); if (!rects.length || !(rects[0].width || rects[0].height)) return false;
    const style = getComputedStyle(el); return style.visibility !== 'hidden' && style.display !== 'none'; };
  const chip = [...this.querySelectorAll('[data-testid=chip]')].find(shown);
  if (!chip) return null;
  const value = chip.querySelector('[data-testid=chip-value]') || chip.querySelector('[data-testid=chip-pill-text]');
  const dwell = chip.querySelector('[data-testid=chip-dwell]');
  // The chip's first line names the question it answers: the PAGE's own label, shown back to the person (it is on the page by design).
  // Everything else on the chip is the assistant's own text, which the page must never be able to see (`own`).
  const question = chip.querySelector('[data-testid=chip-question]');
  const text = chip.innerText || '';
  const asked = question ? (question.innerText || '') : '';
  return {state: chip.getAttribute('data-state'), tone: chip.getAttribute('data-tone'), insert: chip.getAttribute('data-insert'),
          value: value ? value.textContent : '', dwell: !!(dwell && shown(dwell)), text: text.slice(0, 200),
          question: asked.trim(), own: (asked ? text.replace(asked, '') : text).slice(0, 600)};
}"""


class ClosedChip:
    """The chip of the PRODUCTION build, whose shadow root is closed: the page cannot reach it and neither can a Playwright locator.
    The Chrome DevTools Protocol can (it is how DevTools shows a closed root): the root is resolved to an object and read where it
    stands. Nothing in the product is test-only for this: it reads exactly what a person sees. Same interface as ``Chip`` for what
    ``flows.walk`` and the journeys use (expect_chip, expect_none, wait_dwell_over, describe)."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._cdp = None

    async def _session(self):
        if self._cdp is None:
            self._cdp = await self.page.context.new_cdp_session(self.page)
        return self._cdp

    async def read(self) -> dict | None:
        """The chip on screen (state, tone, insert, value, dwell, text, question, own) or None; ``question`` is the page's own label that
        the chip's first line repeats and ``own`` is the rest of its text. The host's closed root is resolved on every read."""
        cdp = await self._session()
        document = await cdp.send("DOM.getDocument", {"depth": 0})
        host = await cdp.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "[data-meridian-assistant]"})
        if not host.get("nodeId"):
            return None
        described = await cdp.send("DOM.describeNode", {"nodeId": host["nodeId"], "depth": 1, "pierce": True})
        roots = described["node"].get("shadowRoots") or []
        if not roots:
            return None
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": roots[0]["backendNodeId"]})
        object_id = resolved["object"]["objectId"]
        try:
            result = await cdp.send("Runtime.callFunctionOn", {"objectId": object_id, "functionDeclaration": _CLOSED_READ,
                                                               "returnByValue": True})
        finally:
            await cdp.send("Runtime.releaseObject", {"objectId": object_id})
        return result.get("result", {}).get("value")

    async def describe(self) -> str:
        try:
            chip = await self.read()
        except Exception as exc:  # noqa: BLE001
            return f"(unreadable: {type(exc).__name__})"
        return "no chip visible" if chip is None else f"{ {k: chip[k] for k in ('state', 'tone', 'insert')} } {chip['text'].replace(chr(10), ' | ')[:200]}"

    async def expect_chip(self, *, value: str | None = None, state: str | None = None, tone: str | None = None,
                          insert: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        wanted = {"state": state, "tone": tone, "insert": insert}

        async def shows() -> bool:
            chip = await self.read()
            return chip is not None and all(want is None or chip[key] == want for key, want in wanted.items()) \
                and (value is None or value_shown(value, chip["value"]))

        loop = asyncio.get_running_loop()
        end = loop.time() + timeout / 1000
        while True:
            if await shows():
                return
            if loop.time() >= end:
                raise AssertionError(f"no chip with state={state} tone={tone} insert={insert} value={value!r} within {timeout} ms"
                                     f"\n  on screen: {await self.describe()}")
            await asyncio.sleep(0.1)

    async def expect_none(self, *, timeout: int = 3_000) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout / 1000
        while await self.read() is not None:
            if loop.time() >= end:
                raise AssertionError(f"a chip is still on screen after {timeout} ms: {await self.describe()}")
            await asyncio.sleep(0.1)

    async def wait_dwell_over(self, *, timeout: int = 8_000) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout / 1000
        while True:
            chip = await self.read()
            if chip is None or not chip["dwell"]:
                return
            if loop.time() >= end:
                raise AssertionError(f"the draft's dwell notice was still showing after {timeout} ms")
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self._cdp is not None:
            try:
                await self._cdp.detach()
            except Exception:  # noqa: BLE001 - the page may be gone already
                pass
            self._cdp = None


async def assistant_focus(page: Page) -> str:
    """The data-testid (or tag) of the control that has the keyboard INSIDE the assistant's own UI ('' when the page's own field has it)."""
    return await page.evaluate("""() => { const host = document.querySelector('[data-meridian-assistant]');
        const active = host && host.shadowRoot && host.shadowRoot.activeElement;
        return active ? (active.getAttribute('data-testid') || active.tagName) : ''; }""")


async def is_mac(page: Page) -> bool:
    """Does the page's own browser say it runs on a Mac? (The same question the extension asks, in the same order: the platform the
    browser reports, then the user agent.) The chip's numbered actions use Control+Option there and Alt+Shift everywhere else."""
    return bool(await page.evaluate(
        "() => /mac|iphone|ipad|ipod/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || '')"))


def chip_chord(mac: bool, number: int) -> str:
    """The Playwright key chord of the chip's numbered action ``number`` on this platform (Control+Option+n on a Mac, Alt+Shift+n elsewhere)."""
    return f"{'Control+Alt' if mac else 'Alt+Shift'}+Digit{number}"


def other_chord(mac: bool, number: int) -> str:
    """The OTHER platform's chord: it must do nothing here."""
    return chip_chord(not mac, number)


def chip_in(page: Page, frame: str | None = None) -> Chip:
    return Chip(page.frame_locator(frame) if frame else page)


# ---------------------------------------------------------------------------------------------- the panel
@dataclass
class Panel:
    page: Page

    def tid(self, testid: str) -> Locator:
        return self.page.locator(f"[data-testid={testid}]")

    @property
    def card(self) -> Locator:
        return self.tid("panel-card")

    async def phase(self) -> str | None:
        """The connection phase the panel shows (header dot), or None while it is collapsed to the pill."""
        for testid in ("panel-conn", "panel-pill"):
            node = self.tid(testid)
            if await node.count():
                return await node.first.get_attribute("data-phase")
        return None

    async def expect_phase(self, phase: str, *, timeout: int = DEFAULT_TIMEOUT) -> float:
        """Wait until the panel shows connection phase ``phase``; returns (and reports when slow) the seconds it took."""
        started = time.monotonic()
        await expect(self.page.locator(f"[data-testid=panel-conn][data-phase={phase}], "
                                       f"[data-testid=panel-pill][data-phase={phase}]").first
                     ).to_be_visible(timeout=timeout)
        waited = time.monotonic() - started
        if waited > 3:
            print(f"    (the panel took {waited:.1f}s to show phase {phase!r})", flush=True)
        return waited

    async def progress(self) -> dict[str, int]:
        """{'total': n, 'items': k, 'inserted': m} read from the progress list (statuses via CSS class st-<status>)."""
        items = self.tid("progress-item")
        count = await items.count()
        inserted = await self.page.locator("[data-testid=progress-item].st-inserted").count()
        return {"items": count, "inserted": inserted}

    async def item_states(self) -> dict[str, str]:
        """{progress item key: status} for every field of the panel list."""
        data = await self.page.evaluate("""() => [...document.querySelectorAll('[data-meridian-assistant]')]
            .flatMap((host) => [...(host.shadowRoot ? host.shadowRoot.querySelectorAll('[data-testid=progress-item]') : [])])
            .map((el) => [el.getAttribute('data-key') || '', (el.className.match(/st-([a-z_]+)/) || [])[1] || ''])""")
        return {key: status for key, status in data}

    async def research_state(self) -> str | None:
        node = self.tid("panel-research")
        return await node.first.get_attribute("data-state") if await node.count() else None


# ---------------------------------------------------------------------------------------------- field values
async def field_value(root: Page | Frame, selector: str) -> object:
    """Current value of a fixture control: text for inputs, checked for checkbox/radio, joined names for files,
    the selected labels for <select>, innerText for contenteditable."""
    return await root.evaluate(
        """(sel) => { const el = document.querySelector(sel); if (!el) return null;
          if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
          if (el.type === 'file') return [...el.files].map((f) => f.name).join(',');
          if (el.tagName === 'SELECT') return [...el.selectedOptions].map((o) => o.label || o.text);
          if (el.isContentEditable) return el.innerText;
          return el.value; }""", selector)


async def select_label(root: Page | Frame, selector: str) -> str:
    labels = await field_value(root, selector)
    return labels[0] if isinstance(labels, list) and labels else ""


async def checked_group(root: Page | Frame, selector: str) -> list[str]:
    """Labels (value attribute) of the checked members matching ``selector`` (a radio/checkbox group)."""
    return await root.evaluate(
        "(sel) => [...document.querySelectorAll(sel)].filter((el) => el.checked).map((el) => el.value)", selector)


async def all_frames_eval(page: Page, expression: str) -> list:
    """Evaluate ``expression`` in every frame of the page that can run it (cross-origin frames included)."""
    results = []
    for frame in page.frames:
        try:
            results.append(await frame.evaluate(expression))
        except Exception:  # noqa: BLE001 - detached or about:blank frame
            results.append(None)
    return results


async def synthetic_events(page: Page) -> list[dict]:
    """Every synthetic event any frame of the page received (fixtures/_guard.js): the extension's writes."""
    events: list[dict] = []
    for found in await all_frames_eval(page, "window.__events || []"):
        events.extend(found or [])
    return events


async def submit_events(page: Page) -> list[dict]:
    submits: list[dict] = []
    for found in await all_frames_eval(page, "window.__submits || []"):
        submits.extend(found or [])
    return submits


async def assert_never_submitted(page: Page, *, user_clicks: int = 0) -> None:
    """No synthetic click and no form submission ever reached a Next/Submit control; only the user's own clicks did."""
    submits = await submit_events(page)
    untrusted = [event for event in submits if not event.get("trusted")]
    assert not untrusted, f"the extension triggered a submit/next control: {untrusted}"
    clicks = [event for event in submits if event.get("kind") == "click"]
    assert len(clicks) == user_clicks, f"expected {user_clicks} user click(s) on submit/next controls, saw {clicks}"


def written_targets(events: list[dict]) -> list[str]:
    """Names (id) of the controls the extension wrote to, in order, one entry per distinct write burst."""
    order: list[str] = []
    for event in events:
        name = event.get("id") or event.get("name") or ""
        if name and (not order or order[-1] != name):
            order.append(name)
    return order


def assert_untouched(events: list[dict], *ids: str) -> None:
    touched = {event.get("id") or event.get("name") for event in events}
    bad = [name for name in ids if name in touched]
    assert not bad, f"the extension wrote to controls it must never touch: {bad}"


# ---------------------------------------------------------------------------------------------- keyboard
async def record_phases(page: Page) -> None:
    """Start a sampler INSIDE the page (every 20 ms) that remembers every distinct (connection phase, 'Open Meridian' button shown, calm card
    shown) the panel went through. A state that lasts a few seconds (``connecting``, while Meridian is merely slow) can be missed by a test that
    asks the page from outside every so often: on a loaded machine the questions queue up behind the browser's own work and the answer
    comes after the state has gone. Read the record with ``phases_seen``."""
    await page.evaluate("""() => {
        window.__phases = [];
        clearInterval(window.__phaseTimer);
        window.__phaseTimer = setInterval(() => {
          for (const host of document.querySelectorAll('[data-meridian-assistant]')) {
            const root = host.shadowRoot;
            const node = root && root.querySelector('[data-testid=panel-conn], [data-testid=panel-pill]');
            if (!node) continue;
            const sample = { phase: node.getAttribute('data-phase'), open: !!root.querySelector('[data-testid=calm-open-meridian]'),
                             calm: [...root.querySelectorAll('[data-testid^=calm-]')].map((el) => el.getAttribute('data-testid')).sort().join(',') };
            const last = window.__phases[window.__phases.length - 1];
            if (!last || last.phase !== sample.phase || last.open !== sample.open || last.calm !== sample.calm) window.__phases.push(sample);
          }
        }, 20);
    }""")


async def phases_seen(page: Page) -> list[dict]:
    return await page.evaluate("window.__phases || []")


async def tab(page: Page, times: int = 1) -> None:
    for _ in range(times):
        await page.keyboard.press("Tab")


async def shift_tab(page: Page) -> None:
    await page.keyboard.press("Shift+Tab")


async def focused_id(page: Page) -> str:
    """id (or name) of the deepest focused element of the top document, piercing open shadow roots."""
    return await page.evaluate("""() => { let el = document.activeElement;
        while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
        return el ? (el.id || el.name || el.tagName) : ''; }""")


async def expect_focus(root: Page | FrameLocator, selector: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
    await expect(root.locator(selector)).to_be_focused(timeout=timeout)


async def settle(page: Page, ms: int = 150) -> None:
    """A short pause for 'nothing more should happen' assertions (negative checks cannot wait for an event)."""
    await asyncio.sleep(ms / 1000)
    await page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


async def timed_tab(page: Page) -> float:
    """Press Tab and return how long the key press took (for the dwell test)."""
    start = time.monotonic()
    await page.keyboard.press("Tab")
    return time.monotonic() - start


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
