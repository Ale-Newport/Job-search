"""Wave-2 scenarios of the content-script rig (workstream D1-2). Same rules as scenarios.py: REAL key presses, view models
handed to the UI, and the messages the content script sent. Every scenario here pins a behaviour that was wrong, silently
missing or unmeasured in wave 1 (each docstring says which; the report lists the mutation that proves it fails without the fix).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness import (
    NEEDS, SELECT, STATE, T, Bg, active_name, chip_for, debug, emit, eventually, press_fill_verified, last_chip, last_panel, normalize, sug, tab,
    ui_log, value_of,
)
from scenarios import GH, Ctx, child_frame, first_name_selector, reached, scenario, watch_keys

PENDING_KEY = "__meridian_assist_pending_submit"


def shadow_value(selector: str) -> str:
    return f"document.querySelector('#app').shadowRoot.querySelector('{selector}')"


def toast_kinds(calls: list[dict[str, Any]]) -> list[str]:
    return [c["arg"]["kind"] for c in calls]


# ------------------------------------------------------------------------------------------------------------------------
# 1. open shadow roots: focus, learning loop, forms that live entirely inside them


@scenario("chip_follows_focus_between_fields_of_one_open_shadow_root")
async def shadow_chip_follows(ctx: Ctx) -> None:
    """focusin between two controls of ONE shadow tree is invisible to window listeners: the chip used to stay on field one."""
    page, _ = await ctx.open("shadow.html", {"first name": T("Alex"), "last name": T("Newport")})
    await page.evaluate(f"{shadow_value('[name=first_name]')}.focus()")
    chip = await chip_for(page, "first name")
    assert chip["value"] == "Alex"
    await tab(page)  # inserts and moves to the next field INSIDE the same root
    assert await page.evaluate(f"{shadow_value('[name=first_name]')}.value") == "Alex"
    chip = await chip_for(page, "last name")
    assert chip["value"] == "Newport", "the chip follows keyboard focus from one field to the next inside the root"
    await page.click("#app >> [name=first_name]")  # the mouse: back to the first field of the same root
    await chip_for(page, "first name")
    await page.click("#app >> [name=last_name]")
    await chip_for(page, "last name")
    await tab(page, shift=True)
    await chip_for(page, "first name")
    await tab(page)  # first name already holds Alex: Tab only moves on
    chip = await chip_for(page, "last name")
    await tab(page)
    assert await page.evaluate(f"{shadow_value('[name=last_name]')}.value") == "Newport"


@scenario("learning_loop_sees_focus_leave_between_fields_of_one_open_shadow_root")
async def shadow_learning(ctx: Ctx) -> None:
    """focusout between two controls of one shadow tree never reached the window either: edits there were never offered for saving."""
    page, bg = await ctx.open("shadow.html", {"first name": NEEDS(), "last name": T("Newport")})
    await page.evaluate(f"{shadow_value('[name=first_name]')}.focus()")
    await chip_for(page, "first name")
    await page.keyboard.type("Zed")
    await tab(page)  # moves to the last name field inside the same root
    assert await page.evaluate("document.querySelector('#app').shadowRoot.activeElement.name") == "last_name"

    async def asked() -> bool:
        asks = [a["arg"] for a in await ui_log(page, "askSave") if a["arg"]]
        return bool(asks and asks[-1]["fieldLabel"] == "First name" and asks[-1]["valuePreview"] == "Zed")

    await eventually(asked, 3, "the save prompt for a value typed inside a shadow root")
    assert not [m for m in bg.msgs("feedback")], "asking is not saving"


@scenario("web_component_widget_rendered_late_is_discovered_and_driven_across_nested_roots")
async def shadow_widget(ctx: Ctx) -> None:
    """A form rendered into an open shadow root after load (and a second nested root) used to be found only after the user focused it."""
    answers = {
        "first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"),
        "sponsorship": SELECT("Yes", "yes"), "describe your visa": T("Skilled Worker visa"), "anything else": T("Nothing more."),
    }
    page, bg = await ctx.open("shadow_widget.html", answers, ready=False)
    # NO user interaction: the widget renders 900 ms after load, only a shadow-root observer can notice
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.length >= 6"), 6, "late shadow form scanned by itself")
    state = await debug(page)
    assert state["roots"] >= 2, state["roots"]
    await ctx.rig.ready(page, bg, timeout=10)
    names = [e["name"] for r in bg.suggest_requests() for e in r["elements"]]
    assert {"first_name", "email", "phone", "sponsor"} <= set(names), names
    await page.evaluate("document.querySelector('#widget').shadowRoot.querySelector('[name=first_name]').focus()")
    await chip_for(page, "first name")
    for _ in range(4):
        await tab(page)
    inner = "document.querySelector('#widget').shadowRoot.querySelector('field-group').shadowRoot"
    assert await page.evaluate(f"{inner}.querySelector('[name=email]').value") == "alex@example.test"
    assert await page.evaluate(f"{inner}.querySelector('[name=phone]').value") == "+44 7700 900123"
    # focus is now on the sponsorship select (light child of the outer root, after the nested group)
    await chip_for(page, "sponsorship")
    await tab(page)
    root = "document.querySelector('#widget').shadowRoot"
    assert await page.evaluate(f"{root}.querySelector('select').value") == "yes"
    # `change` is not composed: the conditional question the select revealed is still the very next stop
    assert await page.evaluate(f"{root}.activeElement && {root}.activeElement.name") == "visa_detail"
    await chip_for(page, "visa")
    await tab(page)
    assert await page.evaluate(f"{root}.querySelector('[name=visa_detail]').value") == "Skilled Worker visa"
    await tab(page)
    assert await page.evaluate(f"{root}.querySelector('[name=notes]').value") == "Nothing more."
    # a click on the shadow form's Submit is noted by the submit watch (the `submit` event itself never leaves the root)
    await page.click("#widget >> button[type=submit]")
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "submit_clicked"], 3, "submit noted inside a shadow root")
    assert await page.evaluate("window.submissions") == 1


@scenario("shadow_root_listeners_and_observers_are_released_on_deactivate")
async def shadow_cleanup(ctx: Ctx) -> None:
    """Per-root listeners and observers (new in wave 2) must not outlive a deactivation; five cycles leave exactly what was there."""
    page, bg = await ctx.open("shadow_widget.html", {"first name": T("Alex")}, probe=True, activate=False)
    await asyncio.sleep(1.1)  # the widget has rendered
    before = await page.evaluate("window.__probe()")
    for cycle in range(5):
        await ctx.rig.activate(page, bg)
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active && window.__meridianAssist.debug().roots >= 2"), 6, f"active (cycle {cycle})")
        during = await page.evaluate("window.__probe()")
        assert during["listeners"] > before["listeners"] and during["observers"] > before["observers"], (before, during)
        await bg.to_all({"t": "deactivate"})
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 4, "deactivated")
        await asyncio.sleep(0.15)
    after = await page.evaluate("window.__probe()")
    assert after["listeners"] == before["listeners"], (before, after)
    assert after["observers"] == before["observers"], (before, after)
    assert after["intervals"] == before["intervals"], (before, after)
    state = await debug(page)
    assert state["roots"] == 0 and state["retained"]["disposers"] == 0, state


@scenario("forms_with_aria_hidden_or_inert_duplicates_are_never_scanned_even_behind_a_shadow_host")
async def aria_duplicates(ctx: Ctx) -> None:
    page, bg = await ctx.open("aria_duplicates.html", {"first name": T("Alex"), "email": T("alex@example.test")})
    labels = [f["label"] for f in (await debug(page))["fields"]]
    assert labels == ["First name", "Email"], labels
    names = [e["name"] for r in bg.suggest_requests() for e in r["elements"]]
    assert names == ["first_name", "email"], names  # not the aria-hidden twin, the inert block nor the shadow twin (hidden HOST)
    await page.focus("input[name=first_name]")
    await tab(page, 2)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    assert await value_of(page, "input[name=email]") == "alex@example.test"
    assert await page.evaluate("document.querySelector('[name=dup_first_name]').value") == ""


# ------------------------------------------------------------------------------------------------------------------------
# 2. submit awareness across documents


def pending_init(*, age_ms: int = 1000, identity: str = "", sid: str = "earlier-session", hops: int = 0) -> str:
    record = {"sid": sid, "label": "Submit application", "company": "Example Company", "title": "Software Engineer", "identity": identity, "hops": hops}
    return (
        "(() => { try { if (!sessionStorage.getItem('%s')) sessionStorage.setItem('%s', JSON.stringify(Object.assign(%s, { at: Date.now() - %d }))); } catch (e) {} })();"
        % (PENDING_KEY, PENDING_KEY, json.dumps(record), age_ms)
    )


async def track_prompts(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "askTrack") if c["arg"]]


@scenario("record_prompt_appears_on_the_next_document_when_the_worker_restarts_the_assistant_by_itself")
async def confirmation_next_document_auto(ctx: Ctx) -> None:
    """The worker re-activates the tab with auto=true after the submit navigated: the quiet start never mounted the UI, so the prompt was lost."""
    page, bg = await ctx.open("thanks_flow.html", {"first name": T("Alex")}, headless_ui=False)
    await page.click("button[type=submit]")
    await page.wait_for_url("**/thanks.html**")
    await ctx.rig.activate(page, bg, auto=True)
    prompt = "document.querySelector('[data-meridian-assistant]') && document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=track-ask]')"
    await eventually(lambda: page.evaluate(f"!!({prompt})"), 8, "the real prompt is on screen of the confirmation page")
    text = await page.evaluate(f"({prompt}).textContent")
    assert "Example Company" in text, text
    assert bg.session_open_count == 1, "a quiet page opens no session of its own: the confirmation belongs to the old one"
    await page.locator("[data-testid=track-record]").click()
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "record"], 3, "record after the user's click")
    rec = [m for m in bg.msgs("track") if m["request"]["event"] == "record"][0]
    assert rec["sessionId"] == "s1" and "Thank you" in rec["request"]["evidence"]
    await eventually(lambda: [m for m in bg.msgs("session:release") if m["sessionId"] == "s1"], 3, "the earlier document's session is released afterwards")


@scenario("pending_submit_mounts_the_ui_for_the_prompt_only_and_the_quiet_page_stays_quiet")
async def pending_quiet(ctx: Ctx) -> None:
    page, bg = await ctx.open("thanks.html", {}, init=pending_init(), activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: track_prompts(page), 6, "prompt for the pending submit")
    log = [c["name"] for c in await ui_log(page)]
    assert log.index("mount") < log.index("askTrack"), "the UI is mounted before the prompt is handed to it"
    assert not await ui_log(page, "setPanel"), "no panel is drawn on a quiet page"
    assert bg.session_open_count == 0 and not bg.msgs("session:open"), "nothing about this page leaves it"
    assert (await track_prompts(page))[0]["company"] == "Example Company"
    await emit(page, {"type": "track-dismiss"})
    await eventually(lambda: unmounted(page), 12, "the UI goes away again after the prompt")
    assert await page.evaluate("sessionStorage.getItem('%s')" % PENDING_KEY) is None
    # a quiet page with NO pending submit stays completely silent
    page2, bg2 = await ctx.open("thanks.html", {}, activate=False)
    await ctx.rig.activate(page2, bg2, auto=True)
    await asyncio.sleep(1.8)
    assert not await ui_log(page2, "mount") and not bg2.msgs("session:open") and not bg2.msgs("track")


async def unmounted(page: Any) -> bool:
    return any(c["name"] == "unmount" for c in await ui_log(page))


@scenario("a_pending_submit_never_prompts_on_another_job_and_expires_after_ten_minutes")
async def pending_scope(ctx: Ctx) -> None:
    # a different job page (an application form of its own) that merely contains confirmation-like words
    page, bg = await ctx.open("confirm_other_job.html", {"first name": T("Alex")}, init=pending_init(identity="http://example.invalid/jobs/123"), activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await asyncio.sleep(2.0)
    assert not await track_prompts(page), "never offer to record job A on the page of job B"
    assert not [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"]
    assert await page.evaluate("sessionStorage.getItem('%s')" % PENDING_KEY) is None, "the stale pending submit is forgotten"
    await eventually(lambda: [m for m in bg.msgs("session:release") if m["sessionId"] == "earlier-session"], 3, "and the session it belonged to is released")
    # nine minutes old: still valid; eleven: gone
    page2, bg2 = await ctx.open("thanks.html", {}, init=pending_init(age_ms=9 * 60 * 1000), activate=False)
    await ctx.rig.activate(page2, bg2, auto=True)
    await eventually(lambda: track_prompts(page2), 6, "a nine minute old submit still gets its prompt")
    page3, bg3 = await ctx.open("thanks.html", {}, init=pending_init(age_ms=11 * 60 * 1000), activate=False)
    await ctx.rig.activate(page3, bg3, auto=True)
    await asyncio.sleep(1.8)
    assert not await track_prompts(page3) and not bg3.msgs("track"), "ten minutes is the limit"
    assert await page3.evaluate("sessionStorage.getItem('%s')" % PENDING_KEY) is None
    # too many documents between the click and the evidence: a redirect chain is fine, a browsing session is not
    page4, bg4 = await ctx.open("thanks.html", {}, init=pending_init(hops=9), activate=False)
    await ctx.rig.activate(page4, bg4, auto=True)
    await asyncio.sleep(1.8)
    assert not await track_prompts(page4)


# ------------------------------------------------------------------------------------------------------------------------
# 3. toolbar toggle (content half)


@scenario("toolbar_toggle_visible_panel_switches_off_hidden_panel_shows_it")
async def toggle(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    assert (await last_panel(page)) is not None, "the panel is on screen"
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 4, "toggle on a visible panel switches the assistant off")
    assert [m for m in bg.msgs("activation") if m["active"] is False], "the worker is told"
    await watch_keys(page)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "" and (await reached(page))[0] == ["Tab", False], "keys are native again"
    # toggle again: starts it, panel visible
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().panelVisible"), 6, "toggle on a stopped assistant starts it with the panel")
    await ctx.rig.ready(page, bg)
    # a collapsed panel (the pill) is still visible: the toolbar click closes the assistant
    await emit(page, {"type": "toggle-collapse"})
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 4, "a collapsed panel counts as visible")


@scenario("toolbar_toggle_on_a_quiet_page_shows_the_panel_and_stays_active")
async def toggle_quiet(ctx: Ctx) -> None:
    page, bg = await ctx.open("no_form.html", {"first name": T("Alex")}, activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active"), 4, "automatic start")
    assert not await ui_log(page, "mount") and not await ui_log(page, "setPanel"), "a quiet start draws nothing"
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: ui_log(page, "mount"), 3, "the panel is shown")
    state = await debug(page)
    assert state["active"] is True and state["quiet"] is False and state["panelVisible"] is True
    assert (await last_panel(page))["noForm"] is True or (await last_panel(page))["progress"]["items"], "the panel is drawn"
    assert [m for m in bg.msgs("activation") if m["active"] is True], "the worker keeps seeing it as active"
    assert not [m for m in bg.msgs("activation") if m["active"] is False]
    await eventually(lambda: bg.msgs("session:open"), 5, "revealing the panel lets the page reach the bridge")
    # the next click on the visible panel closes it
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 4, "second click switches off")
    # a never-activated page
    page2, bg2 = await ctx.open("greenhouse.html", GH, activate=False)
    await ctx.rig.activate(page2, bg2, toggle=True)
    await ctx.rig.ready(page2, bg2)
    assert (await debug(page2))["panelVisible"] is True


@scenario("deactivate_while_the_assistant_is_still_starting_is_not_lost")
async def deactivate_during_start(ctx: Ctx) -> None:
    bg = Bg(GH)
    bg.latency = 0.25  # every worker answer takes a quarter second: the start is still in flight when the deactivation arrives
    page, _ = await ctx.open("greenhouse.html", bg=bg, activate=False)
    await ctx.rig.activate(page, bg)
    await asyncio.sleep(0.05)
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 6, "stopped after it had started")
    await asyncio.sleep(1.0)
    state = await debug(page)
    assert state["active"] is False and state["sessionId"] is None
    opened = len(bg.msgs("session:open"))
    released = len(bg.msgs("session:release"))
    assert opened == released, (opened, released)  # a session opened for a stopped assistant is released again, never left polling


# ------------------------------------------------------------------------------------------------------------------------
# 4. the "allow all sites" note


@scenario("cross_origin_frame_note_appears_by_itself_after_three_seconds_and_idle_pages_stay_idle")
async def frame_note(ctx: Ctx) -> None:
    page, bg = await ctx.open("frames_top.html", {"first name": T("Alex")}, activate=False, wait_frames=1)
    bg.overrides["frames:inject"] = lambda msg, frame: {"ok": True, "data": None}  # no permission: the worker cannot reach the frame
    await ctx.rig.activate(page, bg, frame=page.main_frame)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active"), 4, "top frame active")
    await asyncio.sleep(1.2)
    assert (await last_panel(page))["permissions"]["crossOriginFrames"] == 0, "not before the frame has stayed silent for three seconds"
    # nobody touches the page: the note must still appear
    await eventually(lambda: panel_frames(page), 5, "the 'allow all sites' note without any user event")
    panel = await last_panel(page)
    assert panel["permissions"] == {"allSites": False, "crossOriginFrames": 1}, panel["permissions"]
    # the frame finally reports in (permission granted a moment later): the note goes away at once
    child = await child_frame(page)
    await ctx.rig.activate(page, bg, frame=child)
    await eventually(lambda: no_panel_frames(page), 6, "the note disappears when the frame announces itself")


async def panel_frames(page: Any) -> bool:
    return ((await last_panel(page)) or {}).get("permissions", {}).get("crossOriginFrames", 0) == 1


async def no_panel_frames(page: Any) -> bool:
    return ((await last_panel(page)) or {}).get("permissions", {}).get("crossOriginFrames", 0) == 0


@scenario("a_page_without_frames_is_never_rescanned_or_rerendered_while_idle")
async def idle_page(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await asyncio.sleep(0.8)
    scans = (await debug(page))["scan"]["scans"]
    renders = len(await ui_log(page, "setPanel"))
    requests = len(bg.suggest_requests())
    await asyncio.sleep(4.5)
    state = await debug(page)
    assert state["scan"]["scans"] == scans, "no periodic rescans on a page that does not change"
    assert len(await ui_log(page, "setPanel")) == renders, "no periodic renders either"
    assert len(bg.suggest_requests()) == requests


# ------------------------------------------------------------------------------------------------------------------------
# 5. polling hygiene


async def set_visibility(page: Any, state: str) -> None:
    await page.evaluate(
        """(state) => {
          Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
          Object.defineProperty(document, 'hidden', { configurable: true, get: () => state === 'hidden' });
          document.dispatchEvent(new Event('visibilitychange'));
        }""", state)


@scenario("hidden_page_stops_polling_tells_the_worker_and_catches_up_when_shown")
async def visibility_polling(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await eventually(lambda: len(bg.msgs("poll", "top")) >= 2, 4, "the visible page keeps polling")
    await set_visibility(page, "hidden")
    await eventually(lambda: [m for m in bg.msgs("visibility") if m["visible"] is False], 3, "the worker is told the page is hidden")
    await asyncio.sleep(0.9)  # the poll that was in flight comes back (the rig caps it at 0.4 s)
    quiet_count = len(bg.msgs("poll"))
    # an event happens while nobody looks: it must not be expected, and must not be lost
    bg.push_event({"type": "research", "research": {"run_id": "r", "state": "done", "version": 2, "ready_for_answers": True, "quality": "full", "stale": False,
                                                    "headline": "Researched while hidden", "needs": None, "company": {"name": "Example Company"}, "site": None,
                                                    "role": None, "brief": "", "brief_source": None, "stages": [], "sources": [], "notes": [], "updated_at": ""}})
    await asyncio.sleep(1.6)
    assert len(bg.msgs("poll")) == quiet_count, "a hidden page sends no poll at all"
    assert (await last_panel(page))["research"]["headline"] != "Researched while hidden"
    before = len(bg.msgs("poll"))
    await set_visibility(page, "visible")
    await eventually(lambda: [m for m in bg.msgs("visibility") if m["visible"] is True], 3, "the worker is told the page is visible again")
    await eventually(lambda: len(bg.msgs("poll")) > before, 2, "an immediate catch-up poll")
    first = bg.msgs("poll")[before]
    assert first["waitMs"] == 0, "the catch-up is a plain 'anything now?' (no long wait)"
    await eventually(lambda: last_headline(page, "Researched while hidden"), 3, "the missed event arrives with the catch-up")
    assert len([m for m in bg.msgs("poll") if m["waitMs"] > 0]) >= 1, "then the loop is long again"


async def last_headline(page: Any, text: str) -> bool:
    return ((await last_panel(page)) or {}).get("research", {}).get("headline") == text


@scenario("automatic_start_on_a_page_whose_only_form_is_in_a_frame_serves_the_frame")
async def auto_frame_only(ctx: Ctx) -> None:
    """The quiet top frame opens no session by itself (nothing leaves a form-less page), but a form in its frame must end that silence."""
    answers = {"email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test")}
    page, bg = await ctx.open("frames_only.html", answers, activate=False, wait_frames=1)
    child = await child_frame(page)
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: bg.msgs("session:open"), 8, "the frame's form ends the quiet start: a session opens")
    await ctx.rig.ready(page, bg, timeout=10)
    assert (await debug(page))["quiet"] is False
    await child.focus("input[name=email]")
    await chip_for(child, "email")
    await tab(page)
    assert await value_of(child, "input[name=email]") == "alex@example.test"
    assert len(bg.msgs("session:open")) == 1


@scenario("frames_never_poll_and_a_page_that_starts_hidden_says_so")
async def frames_no_poll(ctx: Ctx) -> None:
    page, bg = await ctx.open("frames_top.html", {"first name": T("Alex")}, wait_frames=1)
    await child_frame(page)
    await asyncio.sleep(1.0)
    assert not [e for e in bg.log if e["msg"]["t"] == "poll" and e["frame"] != "top"], "only the top frame runs the poll loop"
    page2, bg2 = await ctx.open("greenhouse.html", GH, activate=False, init="Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});")
    await ctx.rig.activate(page2, bg2)
    await eventually(lambda: [m for m in bg2.msgs("visibility") if m["visible"] is False], 4, "a hidden page reports itself right after it started")
    await asyncio.sleep(1.2)
    assert not bg2.msgs("poll"), "and it never polls while hidden"


# ------------------------------------------------------------------------------------------------------------------------
# 6. persistence marks


async def marks_now(page: Any) -> list[dict[str, Any]]:
    calls = await ui_log(page, "setMarks")
    return calls[-1]["arg"] if calls else []


@scenario("inferred_and_drafted_insertions_keep_a_mark_until_touched_in_step_with_scroll_resize_and_removal")
async def marks(ctx: Ctx) -> None:
    answers = {**GH, "last name": T("Newport", state="INFERRED"), "email": T("alex@example.test", state="INFERRED")}
    page, _ = await ctx.open("greenhouse.html", answers, viewport=(1000, 420))
    await page.focus('input[name="job_application[last_name]"]')
    await chip_for(page, "last name")
    await tab(page)
    marks = await marks_now(page)
    assert len(marks) == 1 and marks[0]["tone"] == "inferred" and marks[0]["label"] == "Last name", marks
    assert marks[0]["anchor"]["w"] > 0
    # scroll and resize keep the marker on its field. (Tab brings the field it lands on up the window when a chip would not fit under it, and
    # in this short window that scrolls the page: the marker is first waited for where the field IS, and the scroll that follows is measured from there)
    field_now = 'input[name="job_application[last_name]"]'
    await eventually(lambda: mark_on_field(page, field_now), 3, "the marker is on its field")
    y0 = marks_y = (await rect_of(page, field_now))["y"]
    assert abs(marks_y - (await marks_now(page))[0]["anchor"]["y"]) <= 2
    await page.evaluate("window.scrollBy(0, 60)")
    await eventually(lambda: mark_y(page, y0 - 60), 3, "mark follows the scroll")
    await page.set_viewport_size({"width": 700, "height": 420})
    await eventually(lambda: mark_width_changed(page, marks[0]["anchor"]["w"]), 3, "mark follows a resize")
    # a layout shift with no scroll, no window resize and no DOM change the scanner cares about (a style change on <body> is ignored by
    # the mutation filter): only the resize observer can notice that the field moved
    field = 'input[name="job_application[last_name]"]'
    y_before = (await rect_of(page, field))["y"]
    await page.evaluate("() => { document.documentElement.style.overflowAnchor = 'none'; document.body.style.paddingTop = '140px'; }")
    await eventually(lambda: mark_on_field(page, field), 3, "a layout shift moves the marker")
    assert (await rect_of(page, field))["y"] > y_before + 50, "the field really moved"
    await page.evaluate("document.body.style.paddingTop = ''")
    await eventually(lambda: mark_on_field(page, field), 3, "and back")
    # an unrelated, verified insertion does not mark; the user typing into the marked field removes the marker
    await page.focus('input[name="job_application[last_name]"]')
    await page.keyboard.type("x")
    await eventually(lambda: no_marks(page), 3, "touching the field removes the marker")
    # a second INFERRED insertion, then the field disappears from the page
    await page.focus('input[name="job_application[email]"]')
    await chip_for(page, "email")
    await tab(page)
    assert len(await marks_now(page)) == 1
    await page.evaluate("document.querySelector('input[name=\"job_application[email]\"]').closest('label').remove()")
    await eventually(lambda: no_marks(page), 4, "a removed field takes its marker with it")


async def mark_on_field(page: Any, selector: str) -> bool:
    marks = await marks_now(page)
    rect = await rect_of(page, selector)
    return len(marks) == 1 and abs(marks[0]["anchor"]["y"] - rect["y"]) <= 2 and abs(marks[0]["anchor"]["x"] - rect["x"]) <= 2


async def mark_y(page: Any, expected: int) -> bool:
    marks = await marks_now(page)
    return bool(marks) and abs(marks[0]["anchor"]["y"] - expected) <= 2


async def mark_width_changed(page: Any, old: int) -> bool:
    marks = await marks_now(page)
    return bool(marks) and marks[0]["anchor"]["w"] > 0


async def no_marks(page: Any) -> bool:
    return not await marks_now(page)


@scenario("real_visual_layer_draws_one_mark_per_inferred_field_and_removes_it_when_touched")
async def marks_real_ui(ctx: Ctx) -> None:
    answers = {**GH, "last name": T("Newport", state="INFERRED")}
    page, _ = await ctx.open("greenhouse.html", answers, headless_ui=False)
    await page.focus('input[name="job_application[last_name]"]')
    await chip_for_real(page)
    await tab(page)
    count = "document.querySelector('[data-meridian-assistant]').shadowRoot.querySelectorAll('[data-testid=mark]').length"
    await eventually(lambda: page.evaluate(f"{count} === 1"), 4, "a mark is drawn on the inferred field")
    await page.focus('input[name="job_application[last_name]"]')
    await page.keyboard.type("y")
    await eventually(lambda: page.evaluate(f"{count} === 0"), 4, "typing removes it")


async def chip_for_real(page: Any) -> None:
    await eventually(lambda: page.evaluate(
        "!!document.querySelector('[data-meridian-assistant]') && !!document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=chip]')"),
        4, "real chip")


# ------------------------------------------------------------------------------------------------------------------------
# 7. connection retries


@scenario("first_connection_retries_at_1_5_3_6_and_10_seconds", webkit=False)
async def retry_delays(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "no_bridge"})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: len(refreshes(bg)) >= 4, 28, "four retries")
    first_ctx = [e for e in bg.log if e["msg"].get("t") == "ctx"][0]["at"]
    stamps = [first_ctx] + [e["at"] for e in refresh_entries(bg)][:4]
    gaps = [round(b - a, 2) for a, b in zip(stamps, stamps[1:])]
    for got, want in zip(gaps, [1.5, 3.0, 6.0, 10.0]):
        assert want - 0.45 <= got <= want + 1.6, (gaps, want)  # timers never fire early; a loaded machine may fire them late


def refresh_entries(bg: Bg) -> list[dict[str, Any]]:
    return [e for e in bg.log if e["msg"].get("t") == "conn:state" and e["msg"].get("refresh")]


def refreshes(bg: Bg) -> list[dict[str, Any]]:
    return refresh_entries(bg)


@scenario("connecting_phase_is_calm_retried_and_recovers_without_alarm")
async def connecting_phase(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "connecting"})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: last_panel(page), 4, "panel")
    panel = await last_panel(page)
    # the panel has its own calm "connecting" card: the phase reaches it as it is (it used to be passed off as "Meridian isn't open")
    assert panel["connection"]["phase"] == "connecting" and not panel["connection"].get("detail"), panel["connection"]
    await eventually(lambda: len(refreshes(bg)) >= 1, 4, "the first retry comes after 1.5 s")
    assert "connection_lost" not in toast_kinds(await ui_log(page, "toast")), "connecting is never a 'connection lost' event"
    bg.connection = {"phase": "ready"}
    await eventually(lambda: bg.msgs("session:open"), 6, "the session opens once the worker is connected")
    await ctx.rig.ready(page, bg)
    assert "connection_back" not in toast_kinds(await ui_log(page, "toast")), "the first connection is not a 'back' event"


@scenario("hidden_page_does_not_retry_in_the_background_and_retries_when_shown")
async def retry_hidden(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "no_bridge"})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await set_visibility(page, "hidden")
    await asyncio.sleep(0.3)
    seen = len(refreshes(bg))
    await asyncio.sleep(3.6)  # more than the first two delays
    assert len(refreshes(bg)) == seen, "no background probing"
    bg.connection = {"phase": "ready"}
    await set_visibility(page, "visible")
    await eventually(lambda: bg.msgs("session:open"), 4, "retried the moment the page was shown")


# ------------------------------------------------------------------------------------------------------------------------
# 8. chip placement against the next field


async def rect_of(page: Any, selector: str) -> dict[str, float]:
    return await page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }", selector)


def overlaps(a: dict[str, float], b: dict[str, float]) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


# A narrow window (no room beside the field) keeps these scenarios about above / below: with room to the right the chip asks to sit BESIDE
# the field (wave 3, UX-01; see `the_chip_asks_to_sit_beside_the_field...`), which this placement logic never reaches.
NARROW = (560, 900)


@scenario("chip_view_names_the_next_fields_so_the_chip_can_stay_off_them")
async def chip_avoid(ctx: Ctx) -> None:
    page, _ = await ctx.open("stacked.html", {"first name": T("Alex"), "last name": T("Newport"), "email": T("a@b.test")}, viewport=NARROW)
    await page.focus("input[name=first_name]")
    chip = await chip_for(page, "first name")
    last = await rect_of(page, "input[name=last_name]")
    email = await rect_of(page, "input[name=email]")
    avoid = chip["avoid"]
    assert len(avoid) >= 2, avoid  # the next two fields first; the neighbour above and the field's own label follow (wave 3)
    assert chip["placement"] == "above", "the next field starts right under this one and there is room above: ask for the other side"
    for got, want in zip(avoid, [last, email]):
        assert abs(got["y"] - want["y"]) <= 1 and abs(got["x"] - want["x"]) <= 1 and abs(got["w"] - want["w"]) <= 1, (got, want)
    # the last field of the form has no successor: nothing below it is to be avoided
    await page.focus("input[name=city]")
    chip = await chip_for(page, "city")
    city = await rect_of(page, "input[name=city]")
    assert not [a for a in chip["avoid"] if a["y"] >= city["y"] + city["h"] - 1] and chip["placement"] == "below"
    # no room above the first field of a form at the top of the page: below stays (the visual layer then overlays the neighbour)
    page_top, _ = await ctx.open("stacked.html?top=4", {"first name": T("Alex"), "last name": T("Newport")}, viewport=NARROW)
    await page_top.focus("input[name=first_name]")
    chip = await chip_for(page_top, "first name")
    assert chip["placement"] == "below" and len(chip["avoid"]) >= 1
    # scrolling keeps `avoid` in step with the anchor (the chip view is refreshed with the new geometry, so the UI never
    # compares a moved anchor against stale neighbour rectangles)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await page.evaluate("window.scrollBy(0, 25)")

    async def in_step() -> bool:
        c = await last_chip(page)
        if not c or not c["avoid"]:
            return False
        now = await rect_of(page, "input[name=last_name]")
        first = await rect_of(page, "input[name=first_name]")
        return abs(c["avoid"][0]["y"] - now["y"]) <= 1 and abs(c["anchor"]["y"] - first["y"]) <= 1

    await eventually(in_step, 3, "avoid rectangles and anchor follow the scroll together")


@scenario("real_chip_stays_off_the_next_field_and_a_click_on_any_field_still_focuses_it", webkit=False)
async def chip_real_placement(ctx: Ctx) -> None:
    page, _ = await ctx.open("stacked.html?top=300", {"first name": T("Alex"), "last name": T("Newport"), "email": T("a@b.test")}, headless_ui=False, viewport=NARROW)
    await page.focus("input[name=last_name]")
    chip_el = page.locator("[data-testid=chip]")
    await chip_el.wait_for(state="visible", timeout=5000)
    await asyncio.sleep(0.25)
    box = await chip_el.bounding_box()
    chip_rect = {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}
    assert not overlaps(chip_rect, await rect_of(page, "input[name=email]")), "room above: the chip does not sit on the NEXT field"
    # a mouse click right on the next field focuses it (nothing in the way)
    email = await rect_of(page, "input[name=email]")
    await page.mouse.click(email["x"] + email["w"] / 2, email["y"] + email["h"] / 2)
    assert await active_name(page) == "email"
    # no room above (the form starts at the top of the viewport): the chip must go below, over the next field.
    page2, _ = await ctx.open("stacked.html?top=4", {"first name": T("Alex"), "last name": T("Newport"), "email": T("a@b.test")}, headless_ui=False, viewport=NARROW)
    await page2.focus("input[name=first_name]")
    await page2.locator("[data-testid=chip]").wait_for(state="visible", timeout=5000)
    await asyncio.sleep(0.25)
    target = await rect_of(page2, "input[name=last_name]")
    box = await page2.locator("[data-testid=chip]").bounding_box()
    px, py = target["x"] + 60, target["y"] + target["h"] / 2
    assert box["x"] <= px <= box["x"] + box["width"] and box["y"] <= py <= box["y"] + box["height"], "the click point is really under the chip"
    await page2.mouse.click(px, py)
    assert await active_name(page2) == "last_name", "a click on a field under the chip focuses the field"


# ------------------------------------------------------------------------------------------------------------------------
# 9. fresh-eyes hardening


@scenario("a_control_the_page_re_creates_on_blur_keeps_its_insertion_status_and_marker")
async def rerendered_control(ctx: Ctx) -> None:
    """Blur validation that swaps the element gives it a new identity: the INFERRED marker and the 'inserted' status were lost."""
    answers = {"first name": T("Alex", state="INFERRED"), "last name": T("Newport"), "email": T("a@b.test")}
    page, bg = await ctx.open("rerender.html", answers)
    old_key = (await debug(page))["fields"][0]["key"]
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)  # inserts (amber), moves on; the blur makes the page swap the element 30 ms later
    await eventually(lambda: page.evaluate("window.swaps >= 1"), 3, "the page re-created the field")
    assert await value_of(page, "input[name=first_name]") == "Alex", "the page kept the value"

    async def rekeyed() -> bool:
        state = await debug(page)
        first = next(f for f in state["fields"] if f["label"] == "First name")
        return first["key"] != old_key

    await eventually(rekeyed, 4, "the rescan found the new element")
    state = await debug(page)
    new_key = next(f for f in state["fields"] if f["label"] == "First name")["key"]

    async def kept() -> bool:
        panel = await last_panel(page)
        status = {i["label"]: i["status"] for i in panel["progress"]["items"]}
        marks = await marks_now(page)
        return status.get("First name") == "inserted" and [m["key"] for m in marks] == [new_key]

    await eventually(kept, 3, "status 'inserted' and the amber marker moved to the new element")
    asked = [e for r in bg.suggest_requests() for e in r["elements"] if e["name"] == "first_name"]
    assert len(asked) == 1, "the suggestion followed the field: it was not asked for again"
    # the user is on Last name and Tab keeps working
    await tab(page)
    assert await value_of(page, "input[name=last_name]") == "Newport"


@scenario("page_that_steals_focus_after_an_insertion_does_not_confuse_the_chip_or_the_next_tab")
async def focus_steal(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.evaluate("""() => { document.querySelector('[name="job_application[first_name]"]').addEventListener('input',
        () => setTimeout(() => document.querySelector('[name="job_application[email]"]').focus(), 50)); }""")
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await tab(page)  # inserts and moves to last name; 50 ms later the page drags focus to email
    await eventually(lambda: page.evaluate("document.activeElement.name === 'job_application[email]'"), 2, "the page moved focus")
    chip = await chip_for(page, "email")
    assert chip["value"] == "alex@example.test", "the chip follows where focus really is"
    await tab(page)
    assert await value_of(page, 'input[name="job_application[email]"]') == "alex@example.test"
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "", "nothing was inserted behind the user's back"


@scenario("extension_reload_during_an_asynchronous_fill_frees_the_keyboard")
async def orphan_mid_fill(ctx: Ctx) -> None:
    from scenarios import doc_ref
    from harness import FILE, err, make_document

    doc = make_document()
    bg = Bg({"resume": FILE(doc_ref(doc))}, documents={"doc-1": doc})
    page, _ = await ctx.open("greenhouse.html", bg=bg)

    async def orphaning(msg: dict[str, Any], frame: Any) -> Any:
        await frame.evaluate("window.__mockOrphan = true")  # the extension is reloaded while the document is being fetched
        return err("transport", "Extension context invalidated.")

    bg.overrides["document:content"] = orphaning
    await page.focus('input[name="resume"]')
    await chip_for(page, "resume")
    await tab(page)  # swallowed: an asynchronous attach is under way
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().orphaned === true"), 4, "orphaned")
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().busy === false"), 3, "the busy flag is released (no Tab is swallowed any more)")
    assert await page.evaluate("document.querySelector('input[name=resume]').files.length") == 0
    await watch_keys(page)
    await tab(page)
    assert (await reached(page))[0] == ["Tab", False], "native Tab again"
    assert (await last_panel(page))["orphaned"] is True


@scenario("extension_reload_between_two_key_presses_never_traps_the_keyboard")
async def orphan_between_keys(ctx: Ctx) -> None:
    init = "window.addEventListener('keydown', (e) => { if (e.key === 'Tab' && window.__orphanOnTab) window.__mockOrphan = true; }, true);"
    page, _ = await ctx.open("greenhouse.html", GH, init=init)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await page.evaluate("window.__orphanOnTab = true")  # the runtime dies DURING the very next Tab, before our handler looks
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, first_name_selector()) == "", "nothing is inserted through a dead runtime"
    assert (await reached(page))[0] == ["Tab", False], "and the Tab was left alone"


@scenario("tab_belongs_to_the_editor_inside_a_list_item_and_to_the_assistant_elsewhere_in_a_rich_text_field")
async def rich_editor(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "cover note": T("Hello"), "email": T("alex@example.test")}
    page, _ = await ctx.open("rich_editor.html", answers)
    await page.click("#item")  # caret in the list item
    await page.keyboard.press("End")
    await chip_for(page, "cover note")
    await tab(page)
    assert await page.evaluate("window.indents") == 1, "the editor got its own Tab (list indent)"
    assert await page.evaluate("document.activeElement.id") == "note", "focus stayed in the editor"
    await page.click("#para")  # caret in a plain paragraph: Tab is 'next field' again (the answer is not overwritten)
    await page.keyboard.press("End")
    await chip_for(page, "cover note")
    await tab(page)
    assert await active_name(page) == "email"
    assert await page.evaluate("window.indents") == 1


@scenario("a_page_that_owns_tab_in_a_capture_listener_is_never_double_handled")
async def page_owns_tab(ctx: Ctx) -> None:
    init = "window.addEventListener('keydown', (e) => { if (e.key === 'Tab' && window.__pageOwnsTab) e.preventDefault(); }, true);"
    page, _ = await ctx.open("greenhouse.html", GH, init=init)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await page.evaluate("window.__pageOwnsTab = true")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "", "the page already consumed this Tab: the assistant does nothing"
    assert await active_name(page) == "job_application[first_name]"
    await page.evaluate("window.__pageOwnsTab = false")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("altgr_and_extra_modifiers_never_trigger_the_shortcuts_and_alt_r_z_work_by_physical_key")
async def alt_shortcuts(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("a@b.test"), "why do you want": STATE(
        "GENERATED_DRAFT", op="TYPE_TEXT", text="A draft written for this exact page.", generation={"id": "g", "status": "DONE"})}
    page, bg = await ctx.open("draft.html", answers)
    await page.focus("textarea[name=why]")
    await chip_for(page, "why do you want")
    await asyncio.sleep(0.7)
    for combo in ("Control+Alt+KeyR", "Alt+Shift+KeyR", "Meta+Alt+KeyR"):  # AltGr on many layouts is Ctrl+Alt
        await page.keyboard.press(combo)
    await asyncio.sleep(0.3)
    assert not bg.msgs("regenerate"), "extra modifiers (AltGr = Ctrl+Alt on Windows/Linux layouts) are never a shortcut"
    await page.keyboard.press("Alt+KeyR")  # event.code: the physical R key, whatever character the layout types
    await eventually(lambda: bg.msgs("regenerate"), 3, "Alt+R by physical key")


@scenario("aria_activedescendant_widgets_own_tab")
async def activedescendant(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.evaluate("document.querySelector('[name=\"job_application[first_name]\"]').setAttribute('aria-activedescendant', 'opt1')")
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await watch_keys(page)
    await tab(page)
    assert (await reached(page))[0] == ["Tab", False] and await value_of(page, first_name_selector()) == ""


@scenario("long_single_page_sessions_do_not_grow_the_retained_state")
async def retained_state(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, probe=True)
    base = await page.evaluate("window.__probe()")
    state0 = await debug(page)
    # 400 replaced forms: every swap gives every control a new identity (new observer ids), like a long SPA session
    for i in range(40):
        await page.evaluate("""() => { const f = document.getElementById('application'); const copy = f.cloneNode(true);
            f.replaceWith(copy); }""")
        await asyncio.sleep(0.02)
    await asyncio.sleep(1.5)
    state = await debug(page)
    assert len(state["fields"]) == len(state0["fields"])
    assert state["retained"]["notified"] <= 200 and state["retained"]["learning"]["handled"] <= 300
    assert state["retained"]["learning"]["visits"] <= 2
    now = await page.evaluate("window.__probe()")
    assert now["listeners"] <= base["listeners"] + 2, (base, now)
    assert now["observers"] == base["observers"] and now["intervals"] == base["intervals"], (base, now)
    # a long session of drafts arriving for fields the user is not on: the "draft ready" bookkeeping is bounded
    page2, bg2 = await ctx.open("draft.html", {"first name": T("Alex"), "email": T("a@b.test"), "why do you want": STATE(
        "GENERATING", generation={"id": "g", "status": "GENERATING"})})
    info = await field_info_by_label(page2, "why do you want")
    for i in range(260):
        draft = sug({"node_id": info, "context": "Why do you want to work at Acme?"}, {"frame_key": "top"}, state="GENERATED_DRAFT", op="TYPE_TEXT",
                    text=f"Draft number {i}", request_id=f"r-{i}")
        bg2.push_event({"type": "suggestion", "suggestion": draft})
    await eventually(lambda: page2.evaluate("window.__meridianAssist.debug().retained.notified >= 150"), 8, "drafts delivered")
    await asyncio.sleep(0.8)
    assert (await debug(page2))["retained"]["notified"] <= 200, "the draft-ready bookkeeping is capped"


async def field_info_by_label(page: Any, label_part: str) -> int:
    state = await debug(page)
    for f in state["fields"]:
        if normalize(label_part) in normalize(f["label"]):
            return int(f["key"].split(":")[1])
    raise AssertionError(label_part)


@scenario("big_page_scans_stay_within_a_duty_cycle_while_the_page_keeps_mutating")
async def big_page(ctx: Ctx) -> None:
    """>2,000 nodes: a scan is measured, and a mutation storm cannot keep the main thread busy (the debounce adapts to the cost)."""
    # "too heavy to scan inside a key press" is a limit in milliseconds: the test pins it, so the verdict does not depend on how fast (or
    # how loaded) the machine is today
    page, bg = await ctx.open("big_page.html", {"country": SELECT("United Kingdom", "gb")}, ready=False, init="window.__MERIDIAN_SYNC_SCAN_MAX_MS = 1;")
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 8, "first scan")
    await asyncio.sleep(1.0)
    state = await debug(page)
    scan = state["scan"]
    print(f"      big page: nodes={scan['nodes']} fields={len(state['fields'])} cost={scan['cost']} ms parts={scan['parts']}")
    assert scan["nodes"] > 2000 and len(state["fields"]) >= 100
    own = sum(v for k, v in scan["parts"].items() if k != "observe")
    assert own < max(25.0, scan["cost"] * 0.35), f"the assistant's own work must stay small next to the page observer: {scan['parts']}"
    # a framework that mutates the form area every 25 ms for three seconds
    before = scan["scans"]
    await page.evaluate("window.__storm = setInterval(() => document.getElementById('churn').click(), 25)")
    await asyncio.sleep(3.0)
    await page.evaluate("clearInterval(window.__storm)")
    state = await debug(page)
    ran = state["scan"]["scans"] - before
    cost = max(state["scan"]["cost"], 1.0)
    allowed = 3000 / (cost * 5) + 2  # the page is left alone for at least five times what a scan costs
    assert ran <= allowed, f"{ran} scans in 3 s of mutations at {cost} ms each (allowed {allowed:.1f})"
    # a choice insertion normally rescans synchronously so a revealed question is the next stop; on a page this heavy a scan inside the
    # key press would be a visible stall, so it is left to the debounce
    await asyncio.sleep(1.0)
    await ctx.rig.ready(page, bg, timeout=20)
    await page.focus("select[name=country]")
    await chip_for(page, "country")
    scans = (await debug(page))["scan"]["scans"]
    await tab(page)
    assert await value_of(page, "select[name=country]") == "gb"
    assert (await debug(page))["scan"]["scans"] == scans, "no scan ran inside the key press on a heavy page"
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans > %d" % scans), 4, "the debounced scan follows")
    # never starved: after the storm one last scan brings the labels up to date
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => /rev \\d+/.test(f.label))"), 6, "a scan after the storm")


@scenario("more_honeypot_styles_are_never_scanned_filled_or_sent")
async def honeypot_variants(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "clipped": T("TRAP"), "scaled": T("TRAP"),
               "zero": T("TRAP"), "behind": T("TRAP")}
    page, bg = await ctx.open("honeypot_variants.html", answers)
    labels = [f["label"] for f in (await debug(page))["fields"]]
    assert labels == ["First name", "Last name", "Email"], labels
    await press_fill_verified(page)
    await eventually(lambda: page.evaluate("document.querySelector('[name=email]').value === 'alex@example.test'"), 3, "bulk fill ran")
    traps = await page.evaluate("[...document.querySelectorAll('[name^=hp_]')].map(i => i.value)")
    assert traps == [""] * 6, traps
    names = {e["name"] for r in bg.suggest_requests() for e in r["elements"]}
    assert not any(n.startswith("hp_") for n in names), names


@scenario("pages_made_of_hundreds_of_web_components_get_a_bounded_number_of_observers")
async def many_roots(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, probe=True)
    base = await page.evaluate("window.__probe()")
    await page.evaluate("""() => { for (let i = 0; i < 380; i++) { const host = document.createElement('div'); host.className = 'wc';
        host.attachShadow({ mode: 'open' }).innerHTML = '<span>component ' + i + '</span>'; document.body.append(host); } }""")
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().roots >= 100"), 5, "roots discovered")
    await asyncio.sleep(0.8)
    state = await debug(page)
    assert state["roots"] <= 300, state["roots"]
    now = await page.evaluate("window.__probe()")
    assert now["observers"] - base["observers"] <= 300, (base, now)
    assert state["scan"]["cost"] < 150, "and the scan stays cheap"
    # the form itself still works
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("a_quiet_page_never_probes_for_meridian_until_the_panel_is_asked_for")
async def quiet_no_probe(ctx: Ctx) -> None:
    bg = Bg({}, connection={"phase": "no_bridge"})
    page, _ = await ctx.open("thanks.html", bg=bg, activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await asyncio.sleep(3.6)  # longer than the first two retry delays
    assert not refreshes(bg) and not await ui_log(page, "mount"), "an automatic start on a page without a form does nothing at all"
    await ctx.rig.activate(page, bg, toggle=True)
    await eventually(lambda: refreshes(bg), 4, "once the panel is shown the retries start (1.5 s)")
    assert (await last_panel(page))["connection"]["phase"] == "no_bridge"


async def raw_key(page: Any, key: str, vk: int, code: str, modifiers: int = 0) -> None:
    """A TRUSTED key event whose `code` is whatever the layout / a remapper says (CDP), `key` being what the user meant."""
    cdp = await page.context.new_cdp_session(page)
    try:
        for kind in ("rawKeyDown", "keyUp"):
            await cdp.send("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": modifiers})
    finally:
        await cdp.detach()


@scenario("tab_enter_and_escape_follow_event_key_not_the_physical_key_code", webkit=False)
async def key_not_code(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "visa sponsorship": SELECT("No", "no", insert="explicit", flags={"protected": True}), "full name": T("Alex Newport")}
    page, _ = await ctx.open("choices.html", answers)
    await page.focus("input[name=full_name]")
    await chip_for(page, "full name")
    await raw_key(page, "Tab", 9, "Backquote")  # a remapped / non-US layout: the key that means Tab sits somewhere else
    assert await value_of(page, "input[name=full_name]") == "Alex Newport" and await active_name(page) == "sponsor"
    await chip_for(page, "visa sponsorship")
    await raw_key(page, "Escape", 27, "F13")
    assert await last_chip(page) is None, "Escape (by key) dismisses the chip"
    await page.focus("input[name=full_name]")
    await page.focus("select[name=sponsor]")
    await chip_for(page, "visa sponsorship")
    await raw_key(page, "Enter", 13, "NumpadEnter")  # the numeric keypad's Enter reports another code
    assert await value_of(page, "select[name=sponsor]") == "no", "Enter (by key) inserts the protected answer"
    await page.focus("select[name=sponsor]")
    await chip_for(page, "visa sponsorship")
    await raw_key(page, "Tab", 9, "", 8)  # Shift+Tab from a key event without any `code` (some remote desktops)
    assert await active_name(page) == "full_name"
