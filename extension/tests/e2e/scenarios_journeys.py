"""Keyboard-only journeys: three realistic applications completed with Tab, Shift+Tab, Enter and Esc and nothing else
(no mouse after the toolbar activation), each ending with the whole form compared with an expected JSON file and with
every forbidden effect ruled out: nothing submitted by the extension, no consent ticked, no protected or demographic answer
written, no password or CAPTCHA touched, and no application recorded just by filling a form.
"""

from __future__ import annotations

from dataclasses import replace

from playwright.async_api import expect

import expectations as ex
import flows
import keyjourney
import seed
import ui
from framework import Env, check, scenario
from keyjourney import Keys
from scenarios_forms import SUGGEST, drafts_finished, ready_form
from ui import Chip
from scale import millis  # noqa: E402

# A protected answer is taken with Enter (the deliberate act): the Greenhouse table with the values Enter puts in.
GREENHOUSE_KEYS = [replace(row, result="Yes") if row.sel == "#authorised" else
                   replace(row, result=["0"]) if row.sel == "#sponsorship_yes" else row for row in ex.GREENHOUSE]
INDEX = {row.sel: position for position, row in enumerate(GREENHOUSE_KEYS)}

ASHBY_KEYS = [
    ex.verified("#name", seed.NAME),
    ex.verified("#email", seed.EMAIL),
    ex.F("#resume", ex.VERIFIED, "tab", chip=seed.CV_FILENAME, result=seed.CV_FILENAME, kind="file", tone="verified"),
    ex.verified("#linkedin", seed.LINKEDIN),
    ex.verified("#notice", "1 month"),
    ex.F("#location", ex.VERIFIED, "tab", chip="London", result="London, Greater London, England, United Kingdom", tone="verified"),
]
ASHBY_TAIL = [
    ex.verified("#salary", str(seed.SALARY)),
    ex.F("#ai", ex.DRAFT, "tab", kind="text", tone="draft", result=ex.DRAFT_TEXT, note="drafted in the background"),
]

WORKDAY_STEP1 = [ex.verified("#legal_first", seed.FIRST), ex.verified("#legal_last", seed.LAST),
                 ex.verified("#phone_number", seed.PHONE)]
WORKDAY_STEP2 = [ex.verified("#wd_linkedin", seed.LINKEDIN),
                 ex.F("#wd_resume", ex.VERIFIED, "tab", chip=seed.CV_FILENAME, result=seed.CV_FILENAME, kind="file", tone="verified")]


def no_application_recorded(backend) -> bool:
    return backend.db_rows("SELECT COUNT(*) AS n FROM applications WHERE status IN ('APPLYING','APPLIED','CONFIRMED')")[0]["n"] == 0 \
        and backend.db_rows("SELECT COUNT(*) AS n FROM human_tasks")[0]["n"] == 0


async def reach(page, keys: Keys, selector: str, *, limit: int = 5) -> None:
    """The assistant steps aside after the last field: native Tab (through whatever the page has) reaches the control."""
    for _ in range(limit):
        if await page.evaluate("(s) => document.activeElement === document.querySelector(s)", selector):
            return
        await keys.tab()
    await ui.expect_focus(page, selector)


async def finish_checks(env: Env, page, keys: Keys, name: str, *, planned: set[str], user_clicks: int) -> None:
    pointer = await keyjourney.pointer_events(page)
    check(f"{name}: not one trusted mouse event reached any frame ({len(keys.log)} key presses did everything)", pointer == [], pointer)
    events = await ui.synthetic_events(page)
    touched = {event.get("id") or event.get("name") for event in events if event.get("id") or event.get("name")}
    check(f"{name}: exactly the planned controls were written to", touched == planned, sorted(touched ^ planned))
    await ui.assert_never_submitted(page, user_clicks=user_clicks)
    check(f"{name}: nothing was recorded as applied and no task was raised just by filling the form", no_application_recorded(env.backend))
    print(f"    {name}: {len(keys.log)} key presses: Tab x{keys.log.count('Tab')}, Shift+Tab x{keys.log.count('Shift+Tab')}, "
          f"Enter x{keys.log.count('Enter')}, Esc x{keys.log.count('Escape')}", flush=True)


# ---------------------------------------------------------------------------------------------- Greenhouse
@scenario(needs=SUGGEST, timeout=240)
async def scenario_keyboard_only_journey_greenhouse(env: Env) -> None:
    """A whole Greenhouse-style application with the keyboard alone: Tab inserts and moves on, Shift+Tab goes back without
    inserting or skipping, Esc hides a hint, Enter takes the protected answers, consent and demographics stay untouched, and
    the form ends exactly as expected without ever being submitted."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    await greenhouse_journey(env, page, Chip(page), name="greenhouse")
    # Enter took the two protected answers, so what the end-of-form review still lists is the two consent boxes and nothing else
    await expect(panel.tid("panel-review")).to_be_visible(timeout=millis(15_000))
    listed = [text.strip().lower() for text in await panel.tid("review-item").all_inner_texts()]
    check("greenhouse: the end-of-form review lists the two consent questions only", len(listed) == 2 and "consent" in listed[0]
          and "privacy notice" in listed[1], listed)


async def greenhouse_journey(env: Env, page, chip, *, name: str) -> None:
    """The keyboard-only Greenhouse application, on whatever build the page is running: ``chip`` reads what the person sees (a
    ``Chip`` locator on the e2e builds, a ``ClosedChip`` on the production build, whose shadow root only the DevTools protocol can
    open). Everything else is the page's own DOM: focus, values, the final state of every control, and what must never happen."""
    await keyjourney.watch_pointer(page)  # from here on: no mouse
    keys = Keys(page)
    presses = await keyjourney.native_tab_until(page, keys, "#first_name")
    check(f"{name}: native Tab reached the first field in {presses} presses and wrote nothing on the way", await ui.synthetic_events(page) == [])
    rows = GREENHOUSE_KEYS
    await flows.walk(page, rows[:INDEX["#email"] + 1], label=name, keys=keys, enter_for_explicit=True, chip=chip)
    await ui.expect_focus(page, "#phone")
    # Shift+Tab goes back one field at a time, never inserts, never skips
    await keys.shift_tab()
    await ui.expect_focus(page, "#email")
    await keys.shift_tab()
    await ui.expect_focus(page, "#last_name")
    await keys.shift_tab()
    await ui.expect_focus(page, "#first_name")
    check(f"{name}: Shift+Tab changed nothing",
          [await ui.field_value(page, f"#{i}") for i in ("first_name", "last_name", "email", "phone")] == [seed.FIRST, seed.LAST, seed.EMAIL, ""])
    await keys.tab()  # a field that already holds its answer is skipped on the way forward
    await ui.expect_focus(page, "#phone")
    await flows.walk(page, rows[INDEX["#phone"]:INDEX["#colour"]], label=name, keys=keys, enter_for_explicit=True, chip=chip)
    # a question Meridian cannot answer: Esc hides the hint, Tab only moves on
    await ui.expect_focus(page, "#colour")
    await chip.expect_chip(state="NEEDS_YOU")
    await keys.esc()
    await chip.expect_none()
    await keys.tab()
    check(f"{name}: Esc then Tab wrote nothing into the unknown question", await ui.field_value(page, "#colour") == "")
    await ui.expect_focus(page, "#authorised")
    await flows.walk(page, rows[INDEX["#authorised"]:INDEX["#salary"]], label=name, keys=keys, enter_for_explicit=True, chip=chip)
    await ui.expect_focus(page, "#salary")
    # back over the two protected answers: still there, still not touched by Shift+Tab
    await keys.shift_tab()
    await expect(page.locator("input[name=sponsorship]:focus")).to_have_count(1)  # one stop for the whole radio group
    await keys.shift_tab()
    await ui.expect_focus(page, "#authorised")
    check(f"{name}: the protected answers survived the way back", await ui.select_label(page, "#authorised") == "Yes"
          and await ui.checked_group(page, "input[name=sponsorship]") == ["0"])
    await keys.tab()
    await ui.expect_focus(page, "#salary")
    await flows.walk(page, rows[INDEX["#salary"]:], label=name, keys=keys, enter_for_explicit=True, chip=chip)
    await reach(page, keys, "#submit_app")
    check(f"{name}: the last Tab arrived at the Submit button, which nothing pressed", True)
    await keyjourney.assert_form_state(page, "greenhouse")
    planned = {"first_name", "last_name", "email", "phone", "resume", "linkedin", "github", "portfolio", "degree", "employer",
               "authorised", "sponsorship_no", "salary", "start_date", "lang_en", "lang_es", "why"}
    await finish_checks(env, page, keys, name, planned=planned, user_clicks=0)
    ui.assert_untouched(await ui.synthetic_events(page), *ex.GREENHOUSE_NEVER_WRITTEN)
    check(f"{name}: the model drafted only the one long answer", [c["question"] for c in env.llm.generation_calls()]
          == ["Why do you want to work at Acme Robotics?"])


# ---------------------------------------------------------------------------------------------- Ashby
@scenario(needs=SUGGEST, timeout=240)
async def scenario_keyboard_only_journey_ashby(env: Env) -> None:
    """A late-rendered Ashby-style application with the keyboard alone: comboboxes are opened and chosen by Tab, the
    sponsorship Yes/No buttons by Enter, the draft after its dwell, and the final Enter on Submit is the person's own."""
    backend = await env.start_backend()
    await backend.profile_answer("notice_period", "1 month")
    rig, page, panel = await ready_form(env, "ashby_apply.html")
    await page.wait_for_selector("#ashby_form", state="attached")
    await drafts_finished(env, page)
    await keyjourney.watch_pointer(page)
    keys = Keys(page)
    chooser = []
    page.on("filechooser", lambda c: chooser.append(c))
    presses = await keyjourney.native_tab_until(page, keys, "#name")
    check(f"native Tab reached the first field in {presses} press(es), nothing written", await ui.synthetic_events(page) == [])
    await flows.walk(page, ASHBY_KEYS, label="ashby", keys=keys)
    chip = Chip(page)
    await ui.expect_focus(page, "#sponsor_yes")
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No")
    await keys.enter()
    await expect(page.locator("#sponsor_no")).to_have_attribute("aria-pressed", "true")
    check("Enter pressed 'No' (and not 'Yes')", await page.get_attribute("#sponsor_yes", "aria-pressed") == "false")
    await ui.expect_focus(page, "#salary")
    await flows.walk(page, ASHBY_TAIL, label="ashby", keys=keys)
    await reach(page, keys, "#submit_app")
    check("no file chooser was opened by the CV attachment", chooser == [])
    await keyjourney.assert_form_state(page, "ashby")
    check("nothing was submitted before the person's own Enter", await page.evaluate("window.submissions || 0") == 0)
    await keys.enter()  # the person submits
    await expect(page.locator("h1")).to_have_text("Application submitted", timeout=millis(10_000))
    check("the form was submitted exactly once, by the person's key press", await page.evaluate("window.submissions") == 1)
    # (the combobox options are the page's own elements: choosing one is a click on it, which is how a person does it too)
    planned = {"name", "email", "resume", "linkedin", "notice", "location", "sponsor_no", "salary", "ai",
               "notice-opt-3", "location-opt-0"}
    await finish_checks(env, page, keys, "ashby", planned=planned, user_clicks=1)


# ---------------------------------------------------------------------------------------------- Workday in a frame
@scenario(needs=SUGGEST, timeout=300)
async def scenario_keyboard_only_journey_workday(env: Env) -> None:
    """A Workday-style multi-step application inside another site's frame, keyboard alone: focus enters the frame, the
    frame's own Save and Continue buttons are reachable and are the person's (Enter), every step is filled, and the last Enter
    submits. (The partner's frame is the last thing on the host page: see the trap scenario for a page with fields after it.)"""
    rig, page, panel = await ready_form(env, "workday_apply.html", "bare=1")
    frame = page.frame_locator("#ats_frame")
    child = await flows.frame_by_selector(page, "#ats_frame")
    await keyjourney.watch_pointer(page)
    keys = Keys(page)
    presses = await keyjourney.native_tab_until(page, keys, "#contact_name")
    check(f"native Tab reached the host page's field in {presses} press(es), nothing written", await ui.synthetic_events(page) == [])
    await flows.walk(page, [ex.verified("#contact_name", seed.NAME)], label="workday", keys=keys)
    await expect(frame.locator("#legal_first")).to_be_focused(timeout=millis(10_000))
    check("Tab handed real focus to the first field of the cross-origin frame", await child.evaluate("document.hasFocus()"))
    await flows.walk(page, WORKDAY_STEP1, frame="#ats_frame", label="workday step 1", keys=keys)
    await expect(frame.locator("#next1")).to_be_focused(timeout=millis(10_000))
    check("after the frame's last field Tab reached the frame's own 'Save and Continue'", True)
    await keys.enter()  # the person's own press
    await expect(frame.locator("#wd_linkedin")).to_be_visible(timeout=millis(10_000))
    # the step changed under the keyboard user: the next Tab(s) are the browser's own until the first field of step 2
    written_before = len(await child.evaluate("window.__events || []"))
    for attempt in range(1, 5):
        await keys.tab()
        if await keyjourney.frame_active_id(child) == "wd_linkedin":
            break
    else:
        raise AssertionError(f"step 2's first field never got focus (frame focus: {await keyjourney.frame_active_id(child)!r})")
    print(f"    step 2: its first field had focus after {attempt} Tab press(es)", flush=True)
    check("nothing was written while the browser moved focus onto step 2",
          len(await child.evaluate("window.__events || []")) == written_before)
    await flows.walk(page, WORKDAY_STEP2, frame="#ats_frame", label="workday step 2", keys=keys)
    # Tab on the LAST assist field of the frame (a file input: the CV goes in asynchronously) lands on 'Save and Continue' in one press
    await expect(frame.locator("#next2")).to_be_focused(timeout=millis(10_000))
    check("one Tab after the CV landed on the frame's 'Save and Continue' (no second press needed)",
          await keyjourney.frame_active_id(child) == "next2")
    await keys.enter()
    await expect(frame.locator("#wd_submit")).to_be_visible(timeout=millis(10_000))
    for _ in range(4):
        if await keyjourney.frame_active_id(child) == "wd_submit":
            break
        await keys.tab()
    check("Tab reached the Submit button of the review step", await keyjourney.frame_active_id(child) == "wd_submit")
    await keyjourney.assert_form_state(page, "workday")
    check("nothing was submitted before the person's own Enter", await child.evaluate("window.submissions || 0") == 0)
    await keys.enter()
    await expect(frame.locator("h1")).to_have_text("Application submitted", timeout=millis(10_000))
    check("the frame's form was submitted exactly once, by the person", await child.evaluate("window.submissions") == 1)
    planned = {"contact_name", "legal_first", "legal_last", "phone_number", "wd_linkedin", "wd_resume"}
    await finish_checks(env, page, keys, "workday", planned=planned, user_clicks=3)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_keyboard_reaches_buttons_inside_a_frame_with_host_fields_after_it(env: Env) -> None:
    """workday_apply.html has 'Email for job alerts' on the host page after the partner's frame: the frame's 'Save and Continue'
    must still be reachable with Tab and Shift+Tab."""
    rig, page, panel = await ready_form(env, "workday_apply.html")
    child = await flows.frame_by_selector(page, "#ats_frame")
    keys = Keys(page)
    await keyjourney.native_tab_until(page, keys, "#contact_name")
    reached = False
    for _ in range(14):  # a full lap through the page and back to the start
        await keys.tab()
        if await keyjourney.frame_active_id(child) == "next1":
            reached = True
            break
    check("Tab reaches the frame's 'Save and Continue' button", reached, await ui.focused_id(page))
    check("on the way nothing was written to the button or submitted", await child.evaluate("(window.__submits || []).length") == 0)
    # on from the button the browser's own Tab leaves the frame for the host page's next field...
    await keys.tab()
    await ui.expect_focus(page, "#alert_email")
    # ...and Shift+Tab from there enters the frame on its LAST stop, the button, and then reaches the last field
    held = await child.evaluate("document.getElementById('phone_number').value")  # (whatever the forward lap put there)
    await keys.shift_tab()
    await flows.poll_until(lambda: _frame_focus_is(child, "next1"), timeout=10, message="Shift+Tab into the frame on its button")
    await keys.shift_tab()
    await flows.poll_until(lambda: _frame_focus_is(child, "phone_number"), timeout=10, message="Shift+Tab onto the frame's last field")
    check("walking back never inserted anything", await child.evaluate("document.getElementById('phone_number').value") == held,
          held)
    await ui.assert_never_submitted(page)


async def _frame_focus_is(child, element_id: str) -> bool:
    return await keyjourney.frame_active_id(child) == element_id
