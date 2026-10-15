"""Reusable multi-step flows: reading fixture values, walking a form field by field with Tab, waiting for drafts."""

from __future__ import annotations

import asyncio
import json

from playwright.async_api import Frame, FrameLocator, Page, expect

import expectations as ex
import seed
import ui
from framework import check, finding
from scale import millis, seconds
from ui import Chip, Panel

Root = Page | Frame


async def frame_by_selector(page: Page, selector: str) -> Frame:
    """The Frame object behind an <iframe> element of the page (for JS evaluation inside the frame)."""
    handle = await page.wait_for_selector(selector, state="attached", timeout=millis(10_000))
    frame = await handle.content_frame()
    if frame is None:
        raise AssertionError(f"{selector} has no content frame")
    return frame


async def click_in_frame(child: Frame, selector: str) -> None:
    """A real mouse click on a control inside a cross-process frame, once the frame has rendered since the last focus change.
    Chromium routes a mouse event by the hit-test data of the frame's latest composited frame. Right after focus crossed the frame
    boundary (the keyboard hand-offs) that data can still be the old one, and the click then lands on the host page's <iframe>
    element instead of reaching the frame (seen with nothing but Playwright and the fixtures involved: 4 of 16 runs lost the
    click: the host received it with target #ats_frame, the frame received nothing, and 0 of 24 once the two animation frames
    below were awaited). Waiting for the frame's own animation frames is an event, not a delay, so it never slows a fast machine."""
    await child.evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    await child.locator(selector).click()


async def group_name(root: Root, selector: str) -> str:
    return await root.evaluate("(sel) => document.querySelector(sel).name || ''", selector)


async def read_value(root: Root, row: ex.F) -> object:
    """The control's value in the vocabulary of ``ex.F.result``."""
    if row.kind == "radio":
        name = await group_name(root, row.sel)
        return await ui.checked_group(root, f"input[type=radio][name='{name}']")
    if row.kind == "checks":
        name = await group_name(root, row.sel)
        return await ui.checked_group(root, f"input[type=checkbox][name='{name}']")
    if row.kind == "select":
        return await ui.select_label(root, row.sel)
    return await ui.field_value(root, row.sel)  # text, file, editable and (list of labels) multi


EMPTY_SELECT = ("", "Please select", "Select...", "Select an option", "Select", "Choose")


def is_empty(row: ex.F, value: object) -> bool:
    if row.kind in {"radio", "checks", "multi"}:
        return value == []
    if row.kind == "select":
        return value in EMPTY_SELECT
    return value in ("", None)


async def expect_row_inserted(root: Root, row: ex.F) -> None:
    value = await read_value(root, row)
    if row.result == ex.DRAFT_TEXT:
        assert seed.PROJECT_STORY in str(value), f"{row.sel}: expected the draft, found {value!r}"
    elif row.kind in {"checks", "multi"}:
        assert sorted(value) == sorted(row.result), f"{row.sel}: selected {value}, expected {row.result}"
    elif row.kind == "radio":
        assert value == row.result, f"{row.sel}: checked {value}, expected {row.result}"
    else:
        assert value == row.result, f"{row.sel}: value {value!r}, expected {row.result!r}"


async def row_is_inserted(root: Root, row: ex.F) -> bool:
    try:
        await expect_row_inserted(root, row)
    except AssertionError:
        return False
    return True


async def wait_row_inserted(root: Root, row: ex.F, *, timeout: float = 30.0) -> None:
    """Insertions into files and custom widgets finish a moment after the key press: wait for the expected value (the CV is read from
    Meridian first: a machine under extreme load, load average over 100, took longer than the former 10 s). An event wait: a pass is never slower."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await row_is_inserted(root, row):
            return
        await asyncio.sleep(0.1)
    await expect_row_inserted(root, row)  # raises with the observed value


async def wait_dwell_over(chip: Chip | ui.ClosedChip, *, timeout: int = 8_000) -> None:
    """A draft only goes in with Tab after it has been on screen for 500 ms: wait for the chip to say so."""
    await chip.wait_dwell_over(timeout=timeout)


async def walk(page: Page, table: list[ex.F], *, frame: str | None = None, label: str = "",
               wait_drafts: bool = True, keys=None, enter_for_explicit: bool = False, chip: Chip | ui.ClosedChip | None = None) -> None:
    """Walk ``table`` with the real keyboard; the first field must already have focus. For every row: focus is where it
    should be, the chip shows what the table says (Tab is never pressed before it is on screen), the key press does or
    does not insert, and everything that must stay untouched does."""
    root: Page | FrameLocator = page.frame_locator(frame) if frame else page
    target: Root = await frame_by_selector(page, frame) if frame else page
    chip = chip or Chip(root)  # (a ClosedChip reads the closed root of the production build)
    prefix = f"{label}: " if label else ""
    # ``keys`` (keyjourney.Keys) routes every press through a keyboard-only journey's recorder; with ``enter_for_explicit`` a
    # protected answer (insert tier "explicit") is taken with Enter, the deliberate act, instead of Tab.
    press = keys.press if keys is not None else page.keyboard.press
    for index, row in enumerate(table):
        await ui.expect_focus(root, row.sel)
        if row.known_issue:
            await expect(chip.el.first).to_be_visible(timeout=millis(10_000))
            actual = await chip.el.first.get_attribute("data-state")
            if actual == row.state:
                print(f"    {prefix}{row.sel}: the known issue appears FIXED (state {actual}); drop its known_issue", flush=True)
            else:
                finding(f"{prefix}{row.sel} shows {actual}, expected {row.state}", row.known_issue)
        elif row.state == ex.ANY:
            await ui.settle(page, 400)
        elif row.state == ex.SKIP:
            if row.kind == "file":
                # CPL-06: an upload the assistant leaves alone shows a quiet chip that says why (no value, nothing to insert)
                if isinstance(chip, Chip):
                    await chip.expect_skip(code=row.skip_code)
                else:  # (the closed root of the production build is read through the DevTools protocol: state and tier only)
                    await chip.expect_chip(state="SKIP", insert="never")
            else:
                await chip.expect_none()
        else:
            # the first chip of a page waits for the session and the first suggestions: a loaded machine (load average 75 beside the suite,
            # the first suggestions of a 15-field page still missing after 30 s) can need a minute. An event wait: a pass is never slower.
            await chip.expect_chip(state=row.state, tone=row.tone, insert=row.insert, value=row.chip,
                                   timeout=90_000 if index == 0 else ui.DEFAULT_TIMEOUT)
            if row.state == ex.DRAFT and wait_drafts:
                await wait_dwell_over(chip)
        await press("Enter" if enter_for_explicit and row.insert == "explicit" and row.inserted else "Tab")
        if row.state == ex.ANY or row.known_issue:
            value = await read_value(target, row)
            print(f"    {prefix}{row.sel:18} ANY              left as {value!r}", flush=True)
        elif row.inserted:
            await wait_row_inserted(target, row)
            print(f"    {prefix}{row.sel:18} {row.state:16} inserted -> {row.result!r}", flush=True)
        else:
            value = await read_value(target, row)
            keeps = is_empty(row, value) or row.note.startswith("holds") or row.note.startswith("already")
            check(f"{prefix}{row.sel} untouched ({row.state}, insert={row.insert})", keeps, value)
        if index + 1 < len(table):
            await ui.expect_focus(root, table[index + 1].sel)


async def assist_state(rig, page: Page) -> dict:
    """The top frame's own debug snapshot of the assistant (e2e builds: __meridianAssist.debug()): knowledge version, every field's state."""
    for line in (await rig.assist_debug(page)).splitlines():
        try:
            state = json.loads(line)
        except ValueError:
            continue
        if state.get("frameKey") == "top":
            return state
    raise AssertionError("the assistant has no top-frame snapshot (not active?)")


async def wait_suggestions_settled(rig, page: Page, *, changed_from: str | None = None, timeout: float = 30.0) -> None:
    """Meridian's answers have been (re)loaded for every field of the page: no field is waiting for a suggestion. A saved
    correction changes what Meridian knows, the assistant drops what it was told and asks again for the whole page; until that
    round trip is over a field has no question id, so a correction typed and left in that moment cannot be offered for saving
    (suspected product gap, handed to the content owner: LearningLoop.onFocusOut needs the field's suggestion entry). ``changed_from``: also wait for the knowledge version to have moved on."""
    async def settled() -> bool:
        state = await assist_state(rig, page)
        if changed_from is not None and state.get("knowledge") == changed_from:
            return False
        return bool(state.get("fields")) and all(field.get("hasEntry") for field in state["fields"])

    await poll_until(settled, timeout=timeout, message="every field's suggestion to be loaded again")


async def wait_drafts_done(page: Page, *, timeout: int = 30_000) -> None:
    """No draft pending in the panel (the pending-drafts line is gone)."""
    await expect(Panel(page).tid("panel-drafts")).to_have_count(0, timeout=timeout)


async def focus_first(page: Page, selector: str) -> None:
    """Focus a field the way a user does: a real mouse click for text controls, programmatic focus otherwise (a click
    would toggle checkboxes and radios, or open a native select popup)."""
    kind = await page.evaluate(
        "(sel) => { const el = document.querySelector(sel); return el.tagName + ':' + (el.type || ''); }", selector)
    tag, _, kind_type = kind.partition(":")
    if tag == "SELECT" or kind_type in {"checkbox", "radio", "file"}:
        await page.locator(selector).focus()
    else:
        await page.click(selector)


async def poll_until(predicate, *, timeout: float = 15.0, interval: float = 0.2, message: str = "condition"):
    """Await an async predicate until it returns a truthy value; raise with ``message`` otherwise. ``timeout`` is for a quiet machine
    (scale.py multiplies it: this is an event wait, a pass is never slower)."""
    timeout = seconds(timeout)
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    last = None
    while loop.time() < end:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {message} (last={last!r})")
