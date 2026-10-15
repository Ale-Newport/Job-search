"""Wave-2b scenarios of the content-script rig (workstream D1b): the defects handed off after wave 2a, and the fresh-eyes review of the
keyboard model. Same rules as scenarios.py: REAL key presses, view models handed to the UI, and the messages the content script sent.
Every scenario here fails without its fix (the report lists the mutation that proves it).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness import (
    BOXES, COMBO, FILE, NEEDS, SELECT, T, Bg, active_name, chip_for, debug, emit, eventually, last_chip, make_document, tab, ui_log, value_of,
)
from scenarios import GH, Ctx, first_name_selector, reached, scenario, watch_keys

PENDING_KEY = "__meridian_assist_pending_submit"


async def active_id(target: Any) -> str | None:
    frame = target.main_frame if hasattr(target, "main_frame") else target
    return await frame.evaluate("document.activeElement && document.activeElement.id")


async def active_in_frame(frame: Any) -> str | None:
    return await frame.evaluate("document.hasFocus() ? (document.activeElement.id || document.activeElement.name || document.activeElement.tagName) : null")


def relays(bg: Bg, kind: str) -> list[dict[str, Any]]:
    return [m["payload"] for m in bg.msgs("frame:relay") if m["payload"].get("k") == kind]


# ------------------------------------------------------------------------------------------------------------------------
# 1. the next stop is resolved even when the field vanishes, and a last asynchronous insertion still advances


@scenario("focus_advances_after_a_widget_hides_its_input_once_a_value_is_chosen")
async def vanishing_input(ctx: Ctx) -> None:
    """react-select without classNamePrefix hides its input after a choice: the rescan that follows the fill no longer lists the
    field, nextStop() found no position and answered 'native', so focus stayed (the person had to press Tab twice)."""
    answers = {"first name": T("Alex"), "seniority": COMBO("Senior"), "time zone": T("Europe/Madrid")}
    page, bg = await ctx.open("vanish_combo.html", answers)
    await page.focus("input[name=first]")
    await tab(page)
    await eventually(lambda: page.evaluate("document.activeElement.id === 'seniority'"), 6, "focus reached the combobox")
    await tab(page)  # the asynchronous fill; the key is swallowed and focus advances when it is done
    await eventually(lambda: page.evaluate("window.selected === 'Senior'"), 5, "the option was chosen")
    await eventually(lambda: page.evaluate("document.activeElement.name === 'tz'"), 6, "ONE Tab moved focus on to the next field")
    state = await debug(page)
    assert not any("seniority" in f["label"].lower() for f in state["fields"]), "the widget really dropped out of the scan"
    await tab(page)
    assert await value_of(page, "input[name=tz]") == "Europe/Madrid"


@scenario("focus_advances_after_a_widget_removes_the_input_it_was_on")
async def removed_input(ctx: Ctx) -> None:
    """Not even a tree position is left: the stop planned BEFORE the insertion answers."""
    answers = {"first name": T("Alex"), "seniority": COMBO("Senior"), "time zone": T("Europe/Madrid")}
    page, bg = await ctx.open("vanish_combo.html?remove=1", answers)
    await page.focus("input[name=first]")
    await tab(page)
    await eventually(lambda: page.evaluate("document.activeElement.id === 'seniority'"), 6, "focus reached the combobox")
    await tab(page)
    await eventually(lambda: page.evaluate("window.selected === 'Senior' && !document.getElementById('seniority')"), 5, "chosen, and the input is gone")
    await eventually(lambda: page.evaluate("document.activeElement.name === 'tz'"), 6, "focus went on to the next field")


@scenario("tab_on_the_last_assist_field_with_an_asynchronous_insertion_lands_on_the_next_control_in_one_press")
async def async_last_field(ctx: Ctx) -> None:
    """The key is swallowed before the code knows nothing follows: after the insertion the focus goes where the browser's Tab would have
    taken it (a Save button), so one press is enough and the keyboard is never held."""
    answers = {"first name": T("Alex"), "country": SELECT("United Kingdom", "gb")}
    page, bg = await ctx.open("last_select.html", answers)
    await page.focus("input[name=first]")
    await tab(page)
    assert await active_name(page) == "country"
    await tab(page)
    await eventually(lambda: page.evaluate("document.querySelector('select').value === 'gb'"), 6, "the country was chosen")
    if ctx.rig.browser_name == "chromium":
        await eventually(lambda: page.evaluate("document.activeElement.id === 'next'"), 6, "focus went on to the Save button with that one press")
        assert await page.evaluate("document.activeElement.tagName") == "BUTTON"
    else:
        # Safari's Tab does not stop on buttons or links: nothing follows, the next Tab is the browser's own
        await asyncio.sleep(0.3)
        assert await active_name(page) == "country"
        await watch_keys(page)
        await tab(page)
        assert (await reached(page))[-1] == ["Tab", False]
    assert not (await debug(page))["busy"], "the keyboard is free again"


@scenario("enter_on_a_protected_last_field_inserts_but_never_carries_focus_onto_a_button")
async def enter_stays(ctx: Ctx) -> None:
    """Tab goes where the browser's Tab would; Enter never does: the next Enter would press the button (a Submit among them)."""
    answers = {"first name": T("Alex"), "country": SELECT("United Kingdom", "gb", insert="explicit")}
    page, bg = await ctx.open("last_select.html", answers)
    await page.focus("select[name=country]")
    chip = await chip_for(page, "country")
    assert chip["insert"] == "explicit"
    await page.keyboard.press("Enter")
    await eventually(lambda: page.evaluate("document.querySelector('select').value === 'gb'"), 6, "Enter inserted the protected answer")
    await asyncio.sleep(0.4)
    assert await active_name(page) == "country", "focus stayed on the field"
    assert not (await debug(page))["busy"]


@scenario("last_field_of_a_frame_with_an_asynchronous_insertion_hands_focus_to_the_parent_in_one_press")
async def async_last_field_in_frame(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "country": SELECT("United Kingdom", "gb"), "cover note": T("Hello")}
    page, bg = await ctx.open("frames_select_top.html", answers, wait_frames=1)
    child = await eventually(lambda: next((f for f in page.frames if f != page.main_frame and "frames_select_child" in f.url), None), 8, "child frame")
    await ctx.rig.ready(page, bg, timeout=10)
    await child.focus("select[name=country]")
    await chip_for(child, "country")
    await tab(page)  # SELECT_OPTION is asynchronous; nothing follows in the frame, the parent has the cover note
    await eventually(lambda: child.evaluate("document.querySelector('select').value === 'gb'"), 6, "the country was chosen in the frame")
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "focus reached the host page's next field")


@scenario("last_field_of_a_frame_with_an_asynchronous_insertion_and_a_control_after_the_frame_lands_on_that_control")
async def async_last_field_in_frame_gap(ctx: Ctx) -> None:
    """The parent does not own this Tab (a read-only field lies between the frame and its next question), so the frame's hint is 'native':
    after the asynchronous insertion the frame asks the top frame to emulate the browser's Tab anyway."""
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "country": SELECT("United Kingdom", "gb"), "cover note": T("Hello"), "reference": NEEDS()}
    page, bg = await ctx.open("frames_select_gap_top.html", answers, wait_frames=1)
    child = await eventually(lambda: next((f for f in page.frames if f != page.main_frame and "frames_select_child" in f.url), None), 8, "child frame")
    await ctx.rig.ready(page, bg, timeout=10)
    await child.focus("select[name=country]")
    await chip_for(child, "country")
    await tab(page)
    await eventually(lambda: child.evaluate("document.querySelector('select').value === 'gb'"), 6, "the country was chosen in the frame")
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'ref'"), 6, "focus reached the control after the frame")


# ------------------------------------------------------------------------------------------------------------------------
# 2. a failed fill does not outlive the suggestion and the control it failed for


@scenario("a_failed_fill_is_tried_again_once_the_options_have_arrived_and_a_fresh_suggestion_came_in")
async def failed_then_fresh(ctx: Ctx) -> None:
    """state.ts evaluate() refused every later Tab while `failed` was set; only a successful fill cleared it, never a new suggestion."""
    answers = {"first name": T("Alex"), "region": SELECT("South", "south"), "email": T("a@b.test")}
    page, bg = await ctx.open("late_select.html?late=manual", answers)
    await page.focus("#region")
    await chip_for(page, "region")
    await tab(page)  # the fill waits 1.5 s for options that are not there yet, then fails
    await eventually(lambda: field_failed(page, "region"), 6, "the fill failed and says so")
    assert await value_of(page, "#region") == ""
    await page.evaluate("window.loadRegions()")  # the page's options arrive after the fill gave up

    async def refreshed() -> bool:
        chip = await last_chip(page)
        return bool(chip) and chip["tone"] != "error" and not await field_failed(page, "region")

    await eventually(refreshed, 8, "the options arrived, the page was rescanned and a fresh suggestion cleared the failure")
    await page.focus("#region")
    await tab(page)
    await eventually(lambda: page.evaluate("document.querySelector('#region').value === 'south'"), 6, "the second Tab fills the select")


async def field_failed(page: Any, label_part: str) -> bool:
    state = await debug(page)
    return any(label_part in f["label"].lower() and f["failed"] for f in state["fields"])


# ------------------------------------------------------------------------------------------------------------------------
# 3. a disconnected browser stops offering and inserting what Meridian told it


async def lock_phase(ctx: Ctx, phase: str) -> None:
    bg = Bg(GH)
    page, _ = await ctx.open("greenhouse.html", bg=bg)
    await page.focus(first_name_selector())
    chip = await chip_for(page, "first name")
    assert chip["value"] == "Alex"
    bg.connection = {"phase": phase}
    await bg.to_all({"t": "conn:changed", "connection": {"phase": phase}})

    async def hidden() -> bool:
        return await last_chip(page) is None

    await eventually(hidden, 3, f"the chip is gone once the browser is {phase}")
    state = await debug(page)
    assert not any(f["hasEntry"] for f in state["fields"]), "the cached answers are dropped, not just hidden"
    await page.focus('input[name="job_application[last_name]"]')
    await asyncio.sleep(0.3)
    assert await last_chip(page) is None, "no chip from the cache on another field either"
    await watch_keys(page)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "", f"Tab inserts nothing while {phase}"
    assert (await reached(page))[0] == ["Tab", False], "and the key is left to the browser"
    # connected again: the answers are fetched afresh and everything works as before
    bg.connection = {"phase": "ready"}
    await bg.to_all({"t": "conn:changed", "connection": {"phase": "ready"}})
    await ctx.rig.ready(page, bg, timeout=10)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("a_browser_that_is_unpaired_revoked_unverified_or_disabled_stops_offering_cached_answers")
async def disconnected(ctx: Ctx) -> None:
    for phase in ("revoked", "unpaired", "proof_failed", "disabled"):
        await lock_phase(ctx, phase)
        await ctx.rig.reset()


@scenario("an_unreachable_meridian_keeps_its_prefetched_answers")
async def unreachable_keeps(ctx: Ctx) -> None:
    """The opposite case: Meridian merely not answering (no_bridge / error / connecting) is not a revocation."""
    bg = Bg(GH)
    page, _ = await ctx.open("greenhouse.html", bg=bg)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    for phase in ("no_bridge", "error", "connecting", "restoring"):
        await bg.to_all({"t": "conn:changed", "connection": {"phase": phase}})
        await asyncio.sleep(0.15)
        state = await debug(page)
        assert all(f["hasEntry"] for f in state["fields"]), phase
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


# ------------------------------------------------------------------------------------------------------------------------
# 4. the calm "connecting" card


@scenario("the_real_panel_shows_its_calm_connecting_card_not_meridian_isnt_open", webkit=False)
async def real_connecting_card(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "connecting"})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False, headless_ui=False)
    shadow = "document.querySelector('[data-meridian-assistant]').shadowRoot"
    await eventually(lambda: page.evaluate(f"!!document.querySelector('[data-meridian-assistant]') && !!{shadow}.querySelector('[data-testid=calm-connecting]')"), 6,
                     "the connecting card is on screen")
    assert await page.evaluate(f"!{shadow}.querySelector('[data-testid=calm-open-meridian]')"), "no 'Open Meridian' button while Meridian is merely slow"


# ------------------------------------------------------------------------------------------------------------------------
# 5. hand-off between frames: acknowledged, not timed; in-frame buttons stay reachable


async def frame_ready(ctx: Ctx, path: str, answers: dict[str, Any], marker: str, **kw: Any) -> tuple[Any, Bg, Any]:
    page, bg = await ctx.open(path, answers, bg=kw.pop("bg", None), wait_frames=1, **kw)
    child = await eventually(lambda: next((f for f in page.frames if f != page.main_frame and marker in f.url), None), 8, "child frame")
    await ctx.rig.ready(page, bg, timeout=10)
    return page, bg, child


FRAME_ANSWERS = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test"),
                 "cover note": T("Why I fit.")}


@scenario("a_hand_off_the_relay_answers_late_does_not_switch_the_hand_off_off")
async def handoff_slow_relay(ctx: Ctx) -> None:
    """The child decided 'nobody answered' 600 ms after asking and turned the hand-off off; a relay slower than that (a loaded machine) made
    the next Tab a native one. An acknowledgement from the top frame replaces the timer."""
    bg = Bg(FRAME_ANSWERS)
    bg.relay_delay = lambda payload: 0.9 if payload.get("k") == "focus-after-frame" else 0.0
    page, bg, child = await frame_ready(ctx, "frames_top.html", FRAME_ANSWERS, "frames_child", bg=bg)
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)  # inserts the website and asks the parent to take focus (slowly)
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "the slow hand-off arrived")
    assert len(relays(bg, "focus-after-frame")) == 1
    await tab(page, shift=True)  # straight back into the frame's last field
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'website'"), 6, "Shift+Tab re-entered the frame")
    await asyncio.sleep(1.2)  # well past the old 600 ms timer
    await tab(page)
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "the second Tab handed focus out again")
    assert len(relays(bg, "focus-after-frame")) == 2, "the second Tab was a hand-off, not a native Tab"


@scenario("a_quick_shift_tab_back_into_the_frame_keeps_the_next_tab_a_hand_off")
async def handoff_quick_back(ctx: Ctx) -> None:
    page, bg, child = await frame_ready(ctx, "frames_top.html", FRAME_ANSWERS, "frames_child")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "hand-off to the parent")
    await tab(page, shift=True)
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'website'"), 6, "back inside the frame")
    await asyncio.sleep(1.2)
    await tab(page)
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "handed out again")
    assert len(relays(bg, "focus-after-frame")) == 2


@scenario("the_top_frame_acknowledges_every_hand_off_and_a_negative_answer_turns_the_hand_off_off")
async def handoff_ack_protocol(ctx: Ctx) -> None:
    page, bg, child = await frame_ready(ctx, "frames_top.html", FRAME_ANSWERS, "frames_child")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)
    await eventually(lambda: relays(bg, "handoff-ack"), 6, "the top frame answered the hand-off")
    ack = relays(bg, "handoff-ack")[0]
    assert ack["dir"] == 1 and ack["moved"] is True and ack["frameKey"].startswith("f"), ack
    # a top frame that has nothing to move focus to says so: from then on the browser's own Tab leaves the frame
    await tab(page, shift=True)
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'website'"), 6, "back in the frame")
    frame_key = ack["frameKey"]
    await bg.deliver(child, {"t": "frame", "fromFrameId": 0, "payload": {"k": "handoff-ack", "frameKey": frame_key, "dir": 1, "moved": False}})
    await watch_keys(child)
    before = len(relays(bg, "focus-after-frame"))
    await tab(page)
    assert (await reached(child))[-1] == ["Tab", False], "the Tab was left to the browser"
    assert len(relays(bg, "focus-after-frame")) == before, "no hand-off was attempted"


@scenario("a_hand_off_nobody_answers_for_a_long_time_hands_the_key_back_to_the_browser")
async def handoff_nobody(ctx: Ctx) -> None:
    page, bg, child = await frame_ready(ctx, "frames_top.html", FRAME_ANSWERS, "frames_child")
    bg.relay_drop = lambda payload: payload.get("k") in ("focus-after-frame", "handoff-ack")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)  # swallowed: the hand-off is sent... and lost
    assert await child.evaluate("document.activeElement.name") == "website"
    await asyncio.sleep(4.8)  # the fallback window (4 s)
    await watch_keys(child)
    await tab(page)
    assert (await reached(child))[-1] == ["Tab", False], "after the silence the Tab belongs to the browser again"
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "and the browser took focus out of the frame")


@scenario("in_frame_buttons_stay_reachable_when_the_host_page_has_fields_after_the_frame")
async def frame_buttons(ctx: Ctx) -> None:
    """WCAG 2.1.1: Tab out of the frame's last field went to the host page's next field, so the frame's own 'Save and Continue' could
    never be reached with the keyboard. A hand-off happens only when no tabbable control remains in the frame."""
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "cover note": T("Why I fit.")}
    page, bg, child = await frame_ready(ctx, "frames_btn_top.html", answers, "frames_btn_child")
    chromium = ctx.rig.browser_name == "chromium"
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)  # inserts, then hands focus into the frame
    if chromium:
        # the frame's first control is its Back button: that is the edge the browser's Tab enters on
        await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.id === 'back'"), 5, "Tab entered the frame on its Back button")
        await tab(page)  # native Tab from a button
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'email'"), 5, "the first field of the frame has focus")
    await chip_for(child, "email")
    await tab(page)
    await eventually(lambda: child.evaluate("document.activeElement.name === 'phone'"), 6, "email -> phone")
    await tab(page)
    assert await child.evaluate("document.querySelector('[name=phone]').value") == "+44 7700 900123"
    if chromium:
        await eventually(lambda: child.evaluate("document.activeElement.id === 'save'"), 6, "Save and Continue is the next stop, not the host page's cover note")
        assert await page.evaluate("document.activeElement.tagName") == "IFRAME"
        await tab(page)  # the browser leaves the frame to the host page's next control
        await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "native Tab left the frame to the cover note")
        # and the way back: Shift+Tab from the host field enters the frame on its last control
        await tab(page, shift=True)
        await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.id === 'save'"), 6, "Shift+Tab entered the frame on Save and Continue")
        assert len(relays(bg, "focus-after-frame")) == 0, "no hand-off out of the frame was needed on the way"
    else:
        # Safari's Tab does not stop on buttons: nothing is left in the frame after the phone, the host page's field takes the hand-off
        await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 6, "hand-off to the cover note")
        await tab(page, shift=True)
        await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'phone'"), 6, "Shift+Tab re-entered the frame on its last field")


# ------------------------------------------------------------------------------------------------------------------------
# 6. the pending submit is held by the worker (any origin), never by the page


def worker_pending(*, age_ms: int = 1000, identity: str = "", sid: str = "earlier-session", hops: int = 0) -> dict[str, Any]:
    import time

    return {"sid": sid, "at": int(time.time() * 1000) - age_ms, "label": "Submit application", "company": "Example Company",
            "title": "Software Engineer", "identity": identity, "hops": hops}


async def track_prompts(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "askTrack") if c["arg"]]


@scenario("a_submit_that_posts_to_another_origin_is_remembered_by_the_worker_and_prompts_on_the_thank_you_page")
async def pending_cross_origin(ctx: Ctx) -> None:
    bg = Bg({"first name": T("Alex")}, pending_holds=True)
    page, bg = await ctx.open("thanks_flow_alt.html", bg=bg)
    await page.click("button[type=submit]")
    await eventually(lambda: bg.msgs("pending:set"), 6, "the click was handed to the worker")
    record = bg.msgs("pending:set")[0]["record"]
    assert record["sid"] == "s1" and record["hops"] == 0 and record["company"] == "Example Company" and record["label"] == "Submit application"
    await page.wait_for_url("**/thanks.html**")
    assert page.url.startswith("http://localhost:"), "the thank-you page is on ANOTHER origin"
    assert await page.evaluate("sessionStorage.getItem('%s')" % PENDING_KEY) is None, "nothing was left in the page's storage"
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: track_prompts(page), 8, "the record prompt appears on the other origin's page")
    assert (await track_prompts(page))[0]["company"] == "Example Company"
    assert bg.session_open_count == 1, "the quiet thank-you page opened no session of its own"
    seen = [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"]
    assert seen and seen[0]["sessionId"] == "s1"
    # the worker's record moved one document on
    assert [m["record"]["hops"] for m in bg.msgs("pending:set")][-1] == 1
    await emit(page, {"type": "track-record"})
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "record"], 6, "recorded after the user's click")
    await eventually(lambda: bg.msgs("pending:clear"), 6, "the worker's record is cleared")
    assert bg.pending is None
    await eventually(lambda: [m for m in bg.msgs("session:release") if m["sessionId"] == "s1"], 6, "the earlier document's session is released")


@scenario("a_page_cannot_forge_a_pending_submit_when_the_worker_holds_them")
async def pending_forged(ctx: Ctx) -> None:
    record = json.dumps({"sid": "forged", "at": 0, "label": "Submit", "company": "Evil Corp", "title": "CEO", "identity": "", "hops": 0})
    init = ("(() => { try { sessionStorage.setItem('%s', JSON.stringify(Object.assign(%s, { at: Date.now() - 500 }))); } catch (e) {} })();" % (PENDING_KEY, record))
    bg = Bg({}, pending_holds=True)  # the worker knows the feature and has NO pending submit for this tab
    page, bg = await ctx.open("thanks.html", bg=bg, init=init, activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await asyncio.sleep(1.8)
    assert not await track_prompts(page), "a record only the page vouches for is not offered"
    assert not [m for m in bg.msgs("track")], "and nothing about it reaches the bridge"


@scenario("the_workers_pending_submit_obeys_the_ttl_the_hop_limit_and_the_job_identity")
async def pending_rules(ctx: Ctx) -> None:
    # young enough: prompt, and the record moves one hop on
    page, bg = await ctx.open("thanks.html", bg=Bg({}, pending=worker_pending(age_ms=9 * 60 * 1000, hops=1)), activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: track_prompts(page), 6, "a nine minute old submit still gets its prompt")
    assert bg.msgs("pending:set")[-1]["record"]["hops"] == 2
    # too old / too many documents ago: dropped (and the worker told to forget it)
    for kwargs, why in (({"age_ms": 11 * 60 * 1000}, "ten minutes is the limit"), ({"hops": 5}, "at most four documents")):
        page2, bg2 = await ctx.open("thanks.html", bg=Bg({}, pending=worker_pending(**kwargs)), activate=False)
        await ctx.rig.activate(page2, bg2, auto=True)
        await asyncio.sleep(1.6)
        assert not await track_prompts(page2) and not bg2.msgs("track"), why
        await eventually(lambda: bg2.msgs("pending:clear"), 3, f"{why}: the worker's record is cleared")
    # another application: no prompt, the earlier session is released, the record is cleared
    page3, bg3 = await ctx.open("confirm_other_job.html", bg=Bg({"first name": T("Alex")}, pending=worker_pending(identity="http://example.invalid/jobs/123")),
                                activate=False)
    await ctx.rig.activate(page3, bg3, auto=True)
    await asyncio.sleep(2.0)
    assert not await track_prompts(page3), "never offer to record job A on the page of job B"
    await eventually(lambda: [m for m in bg3.msgs("session:release") if m["sessionId"] == "earlier-session"], 6, "the earlier session is released")
    await eventually(lambda: bg3.msgs("pending:clear"), 6, "and the record is cleared")


@scenario("a_worker_that_does_not_know_the_feature_still_gets_the_same_origin_fallback")
async def pending_fallback(ctx: Ctx) -> None:
    page, bg = await ctx.open("thanks_flow.html", {"first name": T("Alex")})
    await page.click("button[type=submit]")
    await page.wait_for_url("**/thanks.html**")
    assert not bg.msgs("pending:set"), "an old worker is never sent a message it does not know"
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: track_prompts(page), 8, "same-origin confirmation through sessionStorage as before")


# ------------------------------------------------------------------------------------------------------------------------
# 7. markers and anchors


async def marks_now(page: Any) -> list[dict[str, Any]]:
    calls = await ui_log(page, "setMarks")
    return calls[-1]["arg"] if calls else []


INFERRED_NAMES = {"first name": T("Alex", state="INFERRED"), "last name": T("Newport", state="INFERRED"), "email": T("alex@example.test", state="INFERRED"),
                  "phone": T("+44 7700 900123", state="INFERRED")}


@scenario("a_marker_never_floats_over_unrelated_ui_when_its_field_is_scrolled_out_of_a_container_or_covered")
async def marks_clipped(ctx: Ctx) -> None:
    """Marker anchors were the field's unclipped rectangle: a field scrolled above a modal body's top edge kept its marker over the header."""
    page, bg = await ctx.open("clipped_marks.html", INFERRED_NAMES, viewport=(900, 520))
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)
    await tab(page)
    await eventually(lambda: page.evaluate("document.querySelector('[name=last_name]').value === 'Newport'"), 6, "both fields were inserted")
    # (UXR-04: the second Tab walked on to the email field below the notes and scrolled the modal's body to show it, as the browser's own Tab
    # would: the two names are out of sight now and rightly have no marker. The person scrolls back up before the markers are looked at.)
    await eventually(lambda: page.evaluate("(() => { const b = document.getElementById('body'); const e = document.querySelector('[name=email]').getBoundingClientRect(); const r = b.getBoundingClientRect(); return e.bottom <= r.bottom + 1 && e.top >= r.top - 1; })()"),
                     4, "the walk brought the email field into the modal body")
    await page.evaluate("document.getElementById('body').scrollTop = 0")
    await eventually(lambda: marks_named(page, ["First name", "Last name"]), 6, "one marker per inserted field")
    header = await page.evaluate("(() => { const r = document.getElementById('head').getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; })()")
    # scroll the form so both fields leave the container through its top edge
    await page.evaluate("document.getElementById('body').scrollTop = 130")
    await eventually(lambda: marks_named(page, []), 6, "no marker over the modal's header while its fields are scrolled away")
    for mark in await marks_now(page):
        assert not (header["top"] <= mark["anchor"]["y"] <= header["bottom"])
    await page.evaluate("document.getElementById('body').scrollTop = 0")
    await eventually(lambda: marks_named(page, ["First name", "Last name"]), 6, "the markers are back when the fields can be seen again")
    # a banner covers the whole form: nothing is drawn over it
    await page.evaluate("document.getElementById('cover').style.display = 'block'")
    await page.evaluate("window.dispatchEvent(new Event('resize'))")
    await eventually(lambda: marks_named(page, []), 6, "a covered field has no marker")
    await page.evaluate("document.getElementById('cover').style.display = 'none'")
    await page.evaluate("window.dispatchEvent(new Event('resize'))")
    await eventually(lambda: marks_named(page, ["First name", "Last name"]), 6, "and it comes back when the banner goes")
    assert sorted(m["label"] for m in await marks_now(page)) == ["First name", "Last name"]


async def marks_named(page: Any, labels: list[str]) -> bool:
    return sorted(m["label"] for m in await marks_now(page)) == sorted(labels)


@scenario("a_chip_over_a_visually_hidden_file_input_is_anchored_to_the_visible_dropzone")
async def hidden_file_anchor(ctx: Ctx) -> None:
    """The input is 1 px and transparent (sr-only), the dropzone label is what the person sees: the chip sits there, not at a 1 px dot."""
    doc = make_document()
    answers = {"first name": T("Alex"), "resume": FILE({k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}), "last name": T("Newport")}
    page, bg = await ctx.open("hidden_file.html", bg=Bg(answers, documents={"doc-1": doc}))
    state = await debug(page)
    assert any(f["kind"] == "file" for f in state["fields"]), [f["label"] for f in state["fields"]]
    await page.focus("input[type=file]")
    chip = await chip_for(page, "resume")
    zone = await page.evaluate("(() => { const r = document.getElementById('zone').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()")
    assert chip["anchor"]["w"] > 100 and chip["anchor"]["h"] > 30, chip["anchor"]
    assert abs(chip["anchor"]["x"] - zone["x"]) <= 2 and abs(chip["anchor"]["y"] - zone["y"]) <= 2, (chip["anchor"], zone)


# ------------------------------------------------------------------------------------------------------------------------
# 8. the rule: Tab walks assist fields only while nothing else lies between them


async def tab_native_check(ctx: Ctx, page: Any, expect_native_target: str | None) -> None:
    """After a key press that must be left to the browser: it was not swallowed; in Chromium the browser took focus to `expect_native_target`."""
    keys = await reached(page)
    assert keys and keys[-1] == ["Tab", False], keys
    if ctx.rig.browser_name == "chromium" and expect_native_target:
        await eventually(lambda: page.evaluate(f"document.activeElement.id === '{expect_native_target}' || document.activeElement.name === '{expect_native_target}'"), 3,
                         f"the browser's Tab landed on {expect_native_target}")


BETWEEN = {"first name": T("Alex"), "last name": T("Newport"), "cover note": T("Hello there."), "email": T("a@b.test"), "phone": T("+44 7700 900123"),
           "which languages": BOXES("English"), "school": T("MIT"), "degree": T("PhD"), "notes": T("nothing else")}


@scenario("tab_walks_to_the_next_assist_field_only_when_no_other_tabbable_control_lies_between_them")
async def between_rule(ctx: Ctx) -> None:
    page, bg = await ctx.open("between.html", BETWEEN)
    chromium = ctx.rig.browser_name == "chromium"
    await watch_keys(page)
    await page.focus("input[name=first]")
    await chip_for(page, "first name")
    # 1. a link between two questions is the browser's: the answer is inserted, the key is NOT swallowed
    await tab(page)
    assert await value_of(page, "input[name=first]") == "Alex"
    if chromium:
        await tab_native_check(ctx, page, "help")
        await tab(page)  # from the link: native again, onto the next question
        assert await active_name(page) == "last"
    else:
        # Safari's Tab does not stop on links (see the Safari-model scenario): the walk simply goes on to the next question
        assert await active_name(page) == "last"
    await chip_for(page, "last name")
    # 2. an editor's toolbar belongs to the editor: Tab goes to the editor itself
    await tab(page)
    assert await value_of(page, "input[name=last]") == "Newport"
    assert await active_id(page) == "cv", "the toolbar buttons of the next question were not stops"
    # 3. a button inside the question's own wrapper (Clear) belongs to it as well
    await tab(page)
    assert await value_of(page, "#cv") == "Hello there."
    assert await active_name(page) == "email"
    await tab(page)
    assert await value_of(page, "input[name=email]") == "a@b.test"
    assert await active_name(page) == "phone", "the question's own Clear button was passed over"
    # 4. a plain input the assistant knows nothing about (a read-only reference) is a stop in every browser
    await tab(page)
    assert await value_of(page, "input[name=phone]") == "+44 7700 900123"
    await tab_native_check(ctx, page, "ref")
    # 5. the browser's Tab goes on to the checkbox group; the group is one stop (the other box is not walked)
    await tab(page)
    if await active_name(page) != "lang":
        await page.focus("input[name=lang]")  # (an engine whose Tab skips checkboxes)
    assert await active_name(page) == "lang"
    await tab(page)  # the asynchronous insertion (SET_CHECKBOXES): focus goes where the browser's Tab would have
    await eventually(lambda: page.evaluate("document.querySelector('[name=lang][value=en]').checked"), 6, "English ticked")
    if chromium:
        await eventually(lambda: page.evaluate("document.activeElement.id === 'add_education'"), 6, "the 'Add another education' button is where focus goes next")
        await tab(page)  # native, from the button
        assert await active_name(page) == "school"
    else:
        await eventually(lambda: page.evaluate("document.activeElement.name === 'school'"), 6, "(Safari's Tab does not stop on buttons)")
    # 6. a filled field the assistant passes over (skip_filled) does not count as an interruption
    await tab(page)
    assert await value_of(page, "input[name=school]") == "MIT"
    assert await active_name(page) == "notes", "the already-filled 'degree' was skipped, as designed, and is no interruption"
    # 7. nothing follows: the key is the browser's (it goes on to the Next button in Chromium)
    await tab(page)
    assert await value_of(page, "input[name=notes]") == "nothing else"
    await tab_native_check(ctx, page, "next" if chromium else None)


@scenario("shift_tab_is_the_browsers_when_a_link_or_button_lies_between_and_never_inserts")
async def between_shift(ctx: Ctx) -> None:
    page, bg = await ctx.open("between.html", BETWEEN)
    await watch_keys(page)
    await page.focus("input[name=last]")
    await chip_for(page, "last name")
    await tab(page, shift=True)
    assert await value_of(page, "input[name=last]") == "" and await value_of(page, "input[name=first]") == ""
    if ctx.rig.browser_name == "chromium":
        keys = await reached(page)
        assert keys[-1] == ["Tab", False]
        assert await active_id(page) == "help", "the link between the two questions is where the browser's Shift+Tab goes"
    # the toolbar of the editor is not a stop on the way back either
    await page.focus("input[name=email]")
    await chip_for(page, "email")
    await tab(page, shift=True)
    assert await active_id(page) == "cv", "Shift+Tab from the next question lands on the editor, not on its toolbar"


@scenario("a_native_stop_between_two_fields_is_a_stop_in_safaris_model_only_for_text_controls", webkit=False)
async def between_safari_model(ctx: Ctx) -> None:
    """Safari's Tab stops on text fields: a link or button between two questions is skipped by the browser itself, so the assistant's own
    walk goes on (as it always did); a read-only text field is a stop in both models."""
    page, bg = await ctx.open("between.html", BETWEEN, init="window.__MERIDIAN_SAFARI_TAB__ = true;")
    await page.focus("input[name=first]")
    await chip_for(page, "first name")
    await tab(page)
    assert await active_name(page) == "last", "the link is no stop in Safari's model: the walk went on to the next question"
    await page.focus("input[name=phone]")
    await chip_for(page, "phone")
    await watch_keys(page)
    await tab(page)
    assert (await reached(page))[-1] == ["Tab", False], "a text control between the questions still belongs to the browser"


# ------------------------------------------------------------------------------------------------------------------------
# 9. conditional follow-ups and document order


async def suggest_elements(bg: Bg) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(r["mode"], r["elements"]) for r in bg.suggest_requests()]


@scenario("a_follow_up_revealed_later_travels_with_its_parent_and_every_element_says_where_it_sits")
async def conditional_parent_and_position(ctx: Ctx) -> None:
    answers = {"visa sponsorship": SELECT("Yes", "yes"), "describe your visa": T("Skilled Worker visa"), "full name": T("Alex"), "nickname": T("Al")}
    page, bg = await ctx.open("conditional_late.html", answers)
    # every element of every request carries its position, and the positions follow the document
    scans = [els for mode, els in await suggest_elements(bg) if mode == "scan"]
    assert scans and all(isinstance(e["position"], int) for els in scans for e in els)
    first = scans[0]
    by_pos = sorted(first, key=lambda e: e["position"])
    assert [e["name"] for e in by_pos] == ["full_name", "sponsor", "nickname"], [e["name"] for e in by_pos]
    base = len(bg.suggest_requests())
    await page.select_option("select[name=sponsor]", "yes")  # the page reveals the follow-up 900 ms later, after the choice was already reported
    await eventually(lambda: any(any(e["name"] == "visa_detail" for e in els) for _, els in refreshes_after(bg, base)), 6, "the follow-up was requested")
    request = next(els for _, els in refreshes_after(bg, base) if any(e["name"] == "visa_detail" for e in els))
    names = [e["name"] for e in request]
    assert "sponsor" in names and "visa_detail" in names, f"the parent travels with its follow-up: {names}"
    parent = next(e for e in request if e["name"] == "sponsor")
    child = next(e for e in request if e["name"] == "visa_detail")
    assert parent["selected_values"] == ["yes"] and parent["position"] < child["position"], (parent["position"], child["position"])
    assert all(isinstance(e["position"], int) for e in request)


def refreshes_after(bg: Bg, base: int) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(r["mode"], r["elements"]) for r in bg.suggest_requests()[base:] if r["mode"] == "refresh"]


# ------------------------------------------------------------------------------------------------------------------------
# 10. fresh eyes on the keyboard model


@scenario("a_widget_that_owns_tab_keeps_it_when_the_assistant_has_nothing_to_insert")
async def page_widget_owns_tab(ctx: Ctx) -> None:
    """A tag input commits what was typed on Tab and keeps the focus (it calls preventDefault). The assistant walked on regardless, because
    it decided in the capture phase and stopped the event before the page's own handler ran."""
    answers = {"first name": T("Alex"), "skills": NEEDS(), "start date": NEEDS(), "last name": T("Newport")}
    page, bg = await ctx.open("tab_widgets.html", answers)
    await page.click("#tags")
    await chip_for(page, "skills")
    await page.keyboard.type("python")
    await tab(page)
    assert await page.evaluate("window.tags") == ["python"], "the page's own Tab handler ran"
    assert await page.evaluate("document.activeElement.id") == "tags", "and kept the focus, as the widget wants"
    await tab(page)  # nothing typed now: the handler does nothing, the assistant's own walk continues
    assert await active_id(page) == "start"


@scenario("an_autocomplete_whose_list_is_on_screen_keeps_tab_even_without_aria_expanded")
async def autocomplete_owns_tab(ctx: Ctx) -> None:
    answers = {"location": T("Elsewhere"), "last name": T("Newport")}
    page, bg = await ctx.open("tab_widgets.html", answers)
    await page.focus("#ac")
    await chip_for(page, "location")
    assert await page.evaluate("!document.getElementById('ac-list').hidden"), "the list is on screen"
    await watch_keys(page)
    await tab(page)
    assert await page.evaluate("window.picked") == "London, United Kingdom", "the page's own Tab handler picked the suggestion"
    assert (await reached(page))[-1] == ["Tab", False], "the assistant did not take the key"
    assert await value_of(page, "#ac") == "London, United Kingdom", "and inserted nothing over it"


@scenario("a_native_date_input_keeps_its_segments_unless_the_assistant_inserts_into_it", webkit=False)
async def date_segments(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "start date": NEEDS(), "last name": T("Newport")}
    page, bg = await ctx.open("tab_widgets.html", answers)
    await page.focus("#start")
    await chip_for(page, "start date")
    await watch_keys(page)
    await tab(page)
    assert (await reached(page))[-1] == ["Tab", False], "with nothing to insert the key belongs to the date's own segments"
    assert await active_id(page) == "start", "the browser moved to the day segment, still inside the date input"
    # an empty date with an eligible answer is inserted (and the walk goes on)
    page2, _ = await ctx.open("tab_widgets.html", {"start date": T("2026-03-04"), "last name": T("Newport"), "snippet": NEEDS()})
    await page2.focus("#start")
    await chip_for(page2, "start date")
    await tab(page2)
    assert await value_of(page2, "#start") == "2026-03-04"
    assert await active_id(page2) == "snippet"


@scenario("a_partly_typed_date_is_never_overwritten", webkit=False)
async def partial_date(ctx: Ctx) -> None:
    page, bg = await ctx.open("tab_widgets.html", {"start date": T("2026-03-04")})
    await page.focus("#start")
    await chip_for(page, "start date")
    await page.keyboard.type("03")  # month typed, day and year still missing: value is '' but the person has started
    assert await value_of(page, "#start") == ""
    await tab(page)
    await asyncio.sleep(0.2)
    assert await value_of(page, "#start") == "", "a date the person has started is not overwritten by Tab"


@scenario("a_code_editor_owns_tab")
async def code_editor(ctx: Ctx) -> None:
    answers = {"portfolio snippet": T("print('hi')"), "last name": T("Newport")}
    page, bg = await ctx.open("tab_widgets.html", answers)
    await page.focus("#snippet")
    await chip_for(page, "portfolio snippet")
    await watch_keys(page)
    await tab(page)
    assert await page.evaluate("window.indents") == 1, "the editor indented"
    assert await value_of(page, "#snippet") == "", "nothing was inserted into a code editor by Tab"
    assert await active_id(page) == "snippet"


@scenario("alt_z_never_wipes_what_the_person_typed_after_an_insertion")
async def undo_after_typing(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    await page.focus(first_name_selector())
    await page.keyboard.press("End")
    await page.keyboard.type("andra")
    assert await value_of(page, first_name_selector()) == "Alexandra"
    await page.keyboard.press("Alt+KeyZ")
    await asyncio.sleep(0.2)
    assert await value_of(page, first_name_selector()) == "Alexandra", "Undo restored the old value over the person's own typing"
    assert "undone" not in [c["arg"]["kind"] for c in await ui_log(page, "toast")]
    # an untouched insertion is still undone
    await page.focus('input[name="job_application[last_name]"]')
    await chip_for(page, "last name")
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Newport"
    await page.keyboard.press("Alt+KeyZ")
    await eventually(lambda: page.evaluate("document.querySelector('input[name=\"job_application[last_name]\"]').value === ''"), 3,
                     "undo works while the field is as it was inserted")


@scenario("a_form_the_page_replaces_as_a_whole_keeps_working_after_one_native_tab_at_most")
async def form_remounted(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test")}
    page, bg = await ctx.open("remount.html", answers)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    old = (await debug(page))["fields"][0]["key"]
    await page.evaluate("window.remount(true)")  # new elements, focus restored into the new first field
    await asyncio.sleep(0.05)
    await tab(page)  # may race the rescan: at worst the browser's own Tab
    await asyncio.sleep(0.8)
    state = await debug(page)
    assert state["renderError"] == "" and not state["orphaned"], state["renderError"]
    assert state["fields"] and all(f["key"] != old for f in state["fields"]), "the rescan found the new form"
    # from here on everything works on the new form
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    assert await active_name(page) == "last_name"
    await tab(page)
    assert await value_of(page, "input[name=last_name]") == "Newport"
    # the form is replaced while an asynchronous fill's key press is in flight: nothing breaks, nothing steals focus
    await page.evaluate("window.remount(false)")
    await asyncio.sleep(0.8)
    assert not (await debug(page))["busy"]
    for _ in range(6):
        await page.evaluate("window.remount(false)")
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)
    state = await debug(page)
    assert len(state["fields"]) == 3 and state["retained"]["learning"]["visits"] <= 2


@scenario("activating_and_deactivating_again_leaves_no_keyboard_listener_behind")
async def listener_cleanup(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, probe=True, activate=False)
    base = await page.evaluate("window.__probe()")
    for _ in range(3):
        await ctx.rig.activate(page, bg)
        await ctx.rig.ready(page, bg, timeout=10)
        await page.focus(first_name_selector())
        await chip_for(page, "first name")
        await bg.to_all({"t": "deactivate"})
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 6, "deactivated")
    now = await page.evaluate("window.__probe()")
    assert now["listeners"] == base["listeners"], f"a listener outlived the deactivation: {base['byType']} -> {now['byType']}"
    assert now["observers"] == base["observers"] and now["intervals"] == base["intervals"], (base, now)


@scenario("a_hung_asynchronous_fill_never_holds_the_keyboard_for_long", webkit=False)
async def busy_watchdog(ctx: Ctx) -> None:
    doc = make_document()
    answers = {"first name": T("Alex"), "resume": FILE({k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")})}
    bg = Bg(answers, documents={"doc-1": doc})

    async def hang(msg: dict[str, Any], frame: Any) -> Any:
        await asyncio.sleep(30)

    bg.overrides["document:content"] = hang
    page, bg = await ctx.open("greenhouse.html", bg=bg, init="window.__MERIDIAN_BUSY_MAX_MS = 3000;")
    await page.focus('input[name="resume"]')
    await chip_for(page, "resume")
    await tab(page)  # the CV never arrives: the key was swallowed and the fill is still waiting
    assert (await debug(page))["busy"]
    await watch_keys(page)
    await tab(page)
    assert not (await reached(page)), "while the fill is genuinely running a second Tab is swallowed too"
    await asyncio.sleep(3.2)  # past the (pinned) limit, however slow the machine made the steps above
    await tab(page)
    assert await active_name(page) != "resume", "a fill that does not finish gives the keyboard back: Tab leaves the field"
    assert len(bg.msgs("document:content")) == 1, "and the same fill is not started again on that Tab"
    assert not (await debug(page))["busy"]


@scenario("a_control_between_the_frame_and_the_next_host_field_is_the_browsers_stop_for_the_hand_off")
async def frame_gap(ctx: Ctx) -> None:
    """The host page has a read-only field between the frame and its next question: leaving the frame lands on it (as Tab would), and
    the child is told the parent does not own this Tab, so its own Tab leaves the frame natively."""
    answers = {**FRAME_ANSWERS, "reference": NEEDS()}
    page, bg, child = await frame_ready(ctx, "frames_gap_top.html", answers, "frames_child")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await watch_keys(child)
    await tab(page)
    assert (await reached(child))[-1] == ["Tab", False], "the child left this Tab to the browser: the next stop is not an assist field"
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'ref'"), 6, "the browser took focus to the reference field")
    assert not relays(bg, "focus-after-frame"), "no hand-off was needed"
    # the same, backwards: Shift+Tab from the cover note enters the reference field first
    await page.focus("textarea[name=cover]")
    await tab(page, shift=True)
    await eventually(lambda: page.evaluate("document.activeElement.name === 'ref'"), 6, "Shift+Tab stops on the reference field before the frame")


@scenario("a_hand_off_out_of_a_frame_passes_over_a_field_that_is_already_filled")
async def frame_filled_next(ctx: Ctx) -> None:
    page, bg, child = await frame_ready(ctx, "frames_filled_top.html", {**FRAME_ANSWERS, "notes": T("Some notes.")}, "frames_child")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'notes'"), 6, "focus skipped the filled cover note")
    assert await value_of(page, "textarea[name=cover]") == "Already written by me."


@scenario("hand_off_listeners_and_timers_are_released_when_the_assistant_is_switched_off")
async def handoff_cleanup(ctx: Ctx) -> None:
    page, bg, child = await frame_ready(ctx, "frames_top.html", FRAME_ANSWERS, "frames_child", probe=True)
    # what a clean deactivation leaves behind in the frame (nothing of the assistant)
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: child.evaluate("window.__meridianAssist.debug().active === false"), 6, "deactivated")
    clean = await child.evaluate("window.__probe()")
    await ctx.rig.activate(page, bg)
    await ctx.rig.ready(page, bg, timeout=10)
    bg.relay_drop = lambda payload: payload.get("k") in ("focus-after-frame", "handoff-ack")
    await child.focus("input[name=website]")
    await chip_for(child, "website")
    await tab(page)  # the hand-off is lost: the child waits for an answer that never comes
    during = await child.evaluate("window.__probe()")
    assert during["listeners"] > clean["listeners"]
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: child.evaluate("window.__meridianAssist.debug().active === false"), 6, "deactivated again")
    after = await child.evaluate("window.__probe()")
    # the pending hand-off's blur listener went with everything else
    assert after["listeners"] == clean["listeners"] and after["intervals"] == clean["intervals"], (clean, during, after)


@scenario("fields_removed_while_they_have_the_focus_do_not_pile_up_in_the_learning_loop")
async def visits_bounded(ctx: Ctx) -> None:
    """A page that drops the focused control reports no focusout: the 'what was in it when the person arrived' memory is capped."""
    page, bg = await ctx.open("many_fields.html", {}, ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.length >= 200"), 10, "250 fields scanned")
    await page.evaluate("""() => { for (const input of [...document.querySelectorAll('input')].slice(0, 160)) { input.focus(); input.remove(); } }""")
    await asyncio.sleep(0.3)
    assert (await debug(page))["retained"]["learning"]["visits"] <= 100
