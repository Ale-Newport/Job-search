"""Wave-5 scenarios: the visual layer and the controller as they are after waves 3 and 4, proven through the real page, the real
extension and the real backend: chip action chords, the first-use card, the end-of-form review, the duplicate banner, "Save to
tracker", the "Fill verified" confirmation and its undo, and the always-on switch.

Every scenario asserts values AND the absence of forbidden effects (nothing submitted, nothing written to consent or protected
answers, nothing recorded as sent unless the person said so), like the rest of the suite.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import expectations as ex
import flows
import httpx
import seed
import ui
from framework import Env, check, scenario
from harness import REPO
from playwright.async_api import expect
from scenarios_assist import _applied
from scenarios_forms import SUGGEST, drafts_finished, ready_form
from scenarios_wave2 import _dictionary, _overlap
from ui import Chip, Panel
from scale import millis  # noqa: E402

UI_PREFS_KEY = "meridian.ui"  # chrome.storage.local: the panel's remembered look (dock side, dismissed notes), shared/storage.ts KEYS.ui
DAY = timedelta(days=1)


async def extension_bridge(rig, backend) -> httpx.AsyncClient:
    """The extension's OWN credential against the real bridge, for what the panel cannot show (the raw answer of a call). Read from the
    test browser's storage: this is a throw-away profile paired with a throw-away backend."""
    stored = await rig.stored_connection()
    return httpx.AsyncClient(base_url=backend.bridge_base, timeout=30, trust_env=False,
                             headers={"Origin": f"chrome-extension://{rig.ext_id}", "Authorization": f"Bearer {stored['token']}"})


async def ui_prefs(rig) -> dict:
    return (await rig.storage_local()).get(UI_PREFS_KEY) or {}


async def probe_keys(page) -> None:
    """From now on remember every digit key press that reaches the PAGE's own listeners (the modifier keys themselves always do). The assistant's handler runs first (capture phase on
    the window, registered when it started) and swallows a key it took with stopImmediatePropagation: a probe registered after it
    sees exactly the presses the assistant did NOT take."""
    await page.evaluate("""() => { window.__keysSeen = [];
        window.addEventListener('keydown', (e) => { if (/^Digit/.test(e.code)) window.__keysSeen.push([e.code, e.altKey, e.ctrlKey, e.shiftKey].join('|')); }, true); }""")


async def keys_seen(page) -> list[str]:
    return await page.evaluate("window.__keysSeen || []")


# ---------------------------------------------------------------------------------------------- the extension's own scan
@scenario(needs=SUGGEST, timeout=120)
async def scenario_the_extension_scans_with_the_widget_patterns_the_golden_file_holds(env: Env) -> None:
    """The scan the golden file calls ``widgets`` is the one the extension really asks for: on the widget-patterns page the assistant
    offers the hidden dropzone upload (named by what a person reads), the listbox button and BOTH buttons of the Yes/No pair as one
    question, exactly the controls of the golden file's widget-mode scan and not the plain one's."""
    golden = json.loads((REPO / "extension" / "tests" / "fixtures" / "scan_golden.json").read_text())["widget_patterns.html"]
    controls = {mode: [c for frame in golden[mode]["frames"] for c in frame["controls"]] for mode in ("plain", "widgets")}
    rig, page, panel = await ready_form(env, "widget_patterns.html")
    await flows.poll_until(lambda: _assist_fields(rig, page, len(controls["widgets"])), timeout=20, message="the assistant's fields of the page")
    fields = (await flows.assist_state(rig, page))["fields"]
    labels = sorted(field["label"] for field in fields)
    print(f"    the assistant offers: {labels}", flush=True)
    # a Yes/No pair is ONE question (one assist field); the golden file lists its two buttons
    pairs = {c[4] for c in controls["widgets"] if c[4].startswith("pair-")}
    check("the assistant offers the upload, the listbox button and the Yes/No pair as one question: the widget-mode scan",
          len(fields) == len(controls["widgets"]) - sum(1 for c in controls["widgets"] if c[4].startswith("pair-")) + len(pairs), labels)
    check("the dropzone upload is named by what a person reads (its label), not by the input's name", "Resume/CV" in labels, labels)
    check("the listbox button is a field of its own, named by its label", "Preferred team" in labels, labels)
    check("the Yes/No pair is one question named by what is asked", any("remotely" in label for label in labels), labels)
    check("none of this is what the plain scan would have offered", len(controls["plain"]) < len(controls["widgets"]), controls["plain"])
    await ui.assert_never_submitted(page)


async def _assist_fields(rig, page, expected: int) -> bool:
    state = await flows.assist_state(rig, page)
    return len(state.get("fields") or []) >= expected - 1


# ---------------------------------------------------------------------------------------------- chip chords
@scenario(needs=SUGGEST, timeout=180)
async def scenario_chip_actions_run_from_the_keyboard_with_chords_that_type_nothing(env: Env) -> None:
    """The chip's numbered actions (Replace is 1, the four rewrites are 2 to 4) have a key chord that types no character: Control+Option+n
    on a Mac, Alt+Shift+n elsewhere. It is printed on the button and in aria-keyshortcuts; the other platform's chord and a bare
    Option/Alt+digit (which types a character on a Mac) do nothing and reach the page; the right chord is taken by the assistant only."""
    rig, page, panel = await ready_form(env, "prefilled.html")
    chip = Chip(page)
    mac = await ui.is_mac(page)
    print(f"    the browser says it runs on a Mac: {mac}", flush=True)
    await page.click("#first_name")  # holds 'Alexander': the chip offers Replace (action 1)
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    replace = chip.part("chip-replace")
    await expect(replace).to_be_visible()
    wanted = "Control+Alt+1" if mac else "Alt+Shift+1"
    check(f"the button announces its chord ({wanted})", await replace.get_attribute("aria-keyshortcuts") == wanted,
          await replace.get_attribute("aria-keyshortcuts"))
    printed = (await replace.locator("[data-testid=key-1]").inner_text()).strip()
    check("and prints it", printed == ("⌃⌥" if mac else "Alt+Shift+") + "1", printed)
    await probe_keys(page)
    for chord in (ui.other_chord(mac, 1), "Alt+Digit1"):
        await page.keyboard.press(chord)
    await ui.settle(page, 300)
    check("the other platform's chord and a bare Alt/Option+1 changed nothing", await ui.field_value(page, "#first_name") == "Alexander")
    check("both key presses reached the page (the assistant took neither)", len(await keys_seen(page)) == 2, await keys_seen(page))
    await page.keyboard.press(ui.chip_chord(mac, 1))
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    check("the chord ran Replace", True)
    check("and the assistant took it (the page never saw that press)", len(await keys_seen(page)) == 2, await keys_seen(page))
    check("no character was typed by the chord", await ui.field_value(page, "#first_name") == seed.FIRST)
    await page.keyboard.press("Alt+KeyZ")
    await expect(page.locator("#first_name")).to_have_value("Alexander")
    await ui.assert_never_submitted(page)
    check("no uncaught page error", not rig.errors, rig.errors[:2])


@scenario(needs=SUGGEST, timeout=180)
async def scenario_draft_rewrite_chord_asks_the_model_once_more(env: Env) -> None:
    """On a drafted answer the rewrite buttons carry chords too (2 shorter, 3 longer, 4 different): the chord asks for the rewrite
    exactly once and the old draft stays where it is until the person replaces it."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    chip = Chip(page)
    mac = await ui.is_mac(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATED_DRAFT", tone="draft", value=seed.PROJECT_STORY)
    different = chip.part("chip-regen-different")
    await expect(different).to_be_visible()
    check("the rewrite button announces chord 4", await different.get_attribute("aria-keyshortcuts") == ("Control+Alt+4" if mac else "Alt+Shift+4"),
          await different.get_attribute("aria-keyshortcuts"))
    before = len(env.llm.generation_calls())
    await page.keyboard.press(ui.chip_chord(mac, 4))
    await flows.poll_until(lambda: _generations_at_least(env, before + 1), timeout=40, message="the second generation, asked for by the chord")
    await drafts_finished(env, page, calls=before + 1)
    check("one chord, one request to the model", len(env.llm.generation_calls()) == before + 1, len(env.llm.generation_calls()))
    check("nothing was written into the field behind the person's back", await ui.field_value(page, "#why") == "")
    await ui.assert_never_submitted(page)


async def _generations_at_least(env: Env, count: int) -> bool:
    return len(env.llm.generation_calls()) >= count


# ---------------------------------------------------------------------------------------------- chip beside / avoid rectangles
async def option_boxes(page, member_selectors: list[str]) -> list[tuple[str, dict]]:
    """The boxes of every option of a choice group as a person sees them: the control and the label printed next to it."""
    boxes = []
    for selector in member_selectors:
        control = await page.locator(selector).bounding_box()
        if control:
            boxes.append((f"{selector} (the control)", control))
        label = page.locator(f"label:has({selector}), label[for='{selector.lstrip('#')}']")
        if await label.count():
            box = await label.first.bounding_box()
            if box:
                boxes.append((f"{selector} (its printed label)", box))
    return boxes


@scenario(needs=SUGGEST, timeout=240)
async def scenario_chip_stays_off_the_options_of_the_choice_group_it_is_for(env: Env) -> None:
    """The chip of a radio or checkbox group never covers an option of that group (the control or the word printed beside it) while any
    other side is free: it sits above, below or beside the LAST printed option. At a large window and at a small one."""
    rig, page, panel = await ready_form(env, "choices.html")
    chip = Chip(page)
    groups = {
        "#relocate_yes": ["#relocate_yes", "#relocate_no"],
        "#auth_yes": ["#auth_yes", "#auth_no"],
        "#lang_en": ["#lang_en", "#lang_es", "#lang_fr", "#lang_de"],
    }
    for width, height in ((1280, 900), (520, 640)):
        await page.set_viewport_size({"width": width, "height": height})
        for first, members in groups.items():
            await page.locator(first).scroll_into_view_if_needed()
            await page.locator(first).focus()
            await chip.expect_chip(timeout=millis(30_000))
            await ui.settle(page, 400)
            box = await chip.el.first.bounding_box()
            check(f"{width}x{height}: the chip of the group {first} is on screen", box is not None)
            options = await option_boxes(page, members)
            covered = [name for name, option in options if _overlap(box, option) is not None]
            check(f"{width}x{height}: the chip of {first} covers no option of its own group (controls and printed labels)", not covered,
                  (covered, box))
            placement = (await chip.attributes())["data-placement"]
            check(f"{width}x{height}: {first}: the chip is above, below or beside the group, never laid over it ({placement})",
                  placement in {"above", "below", "beside"}, placement)
            if placement == "beside":
                last_right = max(option["x"] + option["width"] for _, option in options)
                check(f"{width}x{height}: {first}: 'beside' starts after the last printed option", box["x"] >= last_right - 1, (box["x"], last_right))
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- the panel key from inside another site's frame
@scenario(needs=SUGGEST, timeout=180)
async def scenario_panel_key_inside_a_cross_origin_frame_reaches_the_top_panel_and_comes_back(env: Env) -> None:
    """A form in another site's frame has no panel of its own: Alt+Shift+M pressed in the frame's field is relayed to the top document's
    panel (the keyboard goes there, in the browser's real key routing across processes), and Esc there gives the keyboard back to the
    frame's field the person was on. In the top document the same key does the same without a relay."""
    rig, page, panel = await ready_form(env, "workday_apply.html")
    child = await flows.frame_by_selector(page, "#ats_frame")
    chip_frame = Chip(page.frame_locator("#ats_frame"))
    await child.locator("#legal_first").focus()
    await chip_frame.expect_chip(state="VERIFIED", value=seed.FIRST, timeout=millis(30_000))
    check("the keyboard is in the frame's field", await child.evaluate("document.hasFocus() && document.activeElement.id") == "legal_first")
    check("the frame has no panel of its own", await child.evaluate("document.querySelector('[data-testid=panel]') === null"))
    await page.keyboard.press("Alt+Shift+KeyM")
    await flows.poll_until(lambda: _panel_has_keyboard(page), timeout=15, message="the keyboard to reach the top document's panel")
    check("Alt+Shift+M in the frame put the keyboard into the top panel", await ui.assistant_focus(page) != "")
    await page.keyboard.press("Escape")
    await flows.poll_until(lambda: _frame_field_has_keyboard(child, "legal_first"), timeout=15, message="Esc to give the keyboard back to the frame's field")
    check("Esc gave it back to the frame's field the person was on", True)
    check("nothing was written on the way", await child.evaluate("document.getElementById('legal_first').value") == "")
    await ui.assert_never_submitted(page)


async def _frame_field_has_keyboard(child, element_id: str) -> bool:
    return await child.evaluate("(id) => document.hasFocus() && document.activeElement && document.activeElement.id === id", element_id)


# ---------------------------------------------------------------------------------------------- first-use card
@scenario(needs=SUGGEST, timeout=240)
async def scenario_first_use_card_ends_with_the_first_answer_and_is_remembered(env: Env) -> None:
    """A person who has never used the assistant sees the three steps and the shortcut once a form is there (not on a page without
    one); it goes away for the page as soon as they find the first field themselves, and for every later page once an answer went in."""
    en = _dictionary("en")
    rig = await env.connected()
    decoy = await env.open("decoy.html", activate=True)
    await Panel(decoy).expect_phase("ready", timeout=millis(25_000))
    await expect(Panel(decoy).tid("calm-no-form")).to_be_visible(timeout=millis(15_000))
    check("a page without a form explains nothing yet: no first-use card", await Panel(decoy).tid("panel-first-use").count() == 0)
    page = await env.open_tab("lever_apply.html")
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    card = panel.tid("panel-first-use")
    await expect(card).to_be_visible(timeout=millis(15_000))
    steps = panel.tid("first-steps").locator("li")
    check("three steps", await steps.count() == 3, await steps.count())
    check("they are the extension's own words", [(await steps.nth(i).inner_text()).strip().lstrip("0123456789\n ") for i in range(3)]
          == [en["first.step1"], en["first.step2"], en["first.step3"]], [await steps.nth(i).inner_text() for i in range(3)])
    shortcut = (await panel.tid("first-shortcut").inner_text()).strip()
    mac = await ui.is_mac(page)
    check("the default shortcut of the toolbar button is named, for this platform", ("\u2318\u21e7Y" if mac else "Ctrl+Shift+Y") in shortcut, shortcut)
    check("nothing is remembered before the person has used it", "first_use" not in (await ui_prefs(rig)).get("dismissed", {}), await ui_prefs(rig))
    # finding the first field alone ends the card for THIS page (it did its job) but remembers nothing
    await page.click("#name")
    await expect(card).to_have_count(0, timeout=millis(10_000))
    check("clicking the first field ended the card for this page", True)
    check("and remembered nothing: no answer went in yet", "first_use" not in (await ui_prefs(rig)).get("dismissed", {}), await ui_prefs(rig))
    other = await env.open_tab("greenhouse_apply.html")
    await Panel(other).expect_phase("ready", timeout=millis(25_000))
    await expect(Panel(other).tid("panel-first-use")).to_be_visible(timeout=millis(15_000))
    check("a later page, nothing inserted yet: the card is still offered", True)
    # the first insertion remembers it for every later page
    await page.bring_to_front()
    await page.locator("#name").focus()
    await Chip(page).expect_chip(state="VERIFIED", insert="tab", value=seed.NAME, timeout=millis(30_000))
    await ui.tab(page)
    await expect(page.locator("#name")).to_have_value(seed.NAME)
    await flows.poll_until(lambda: _first_use_remembered(rig), timeout=15, message="the first insertion to be remembered")
    stored = (await ui_prefs(rig))["dismissed"]["first_use"]
    check("the time it was seen is remembered, as a number", isinstance(stored, (int, float)) and abs(time.time() * 1000 - stored) < 120_000, stored)
    third = await env.open_tab("react_form.html")
    await Panel(third).expect_phase("ready", timeout=millis(25_000))
    await ui.settle(third, 800)
    check("a page opened after the first insertion shows no card", await Panel(third).tid("panel-first-use").count() == 0)
    await ui.assert_never_submitted(page)


async def _first_use_remembered(rig) -> bool:
    return "first_use" in (await ui_prefs(rig)).get("dismissed", {})


@scenario(needs=SUGGEST, timeout=200)
async def scenario_first_use_got_it_is_remembered_for_a_year_and_then_forgotten(env: Env) -> None:
    """'Got it' is remembered across pages and reloads; a note dismissed more than 400 days ago is forgotten and the card is offered again."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    card = panel.tid("panel-first-use")
    await expect(card).to_be_visible(timeout=millis(15_000))
    await panel.tid("first-dismiss").click()
    await expect(card).to_have_count(0, timeout=millis(10_000))
    await flows.poll_until(lambda: _first_use_remembered(rig), timeout=15, message="'Got it' to be remembered")
    await page.reload(wait_until="domcontentloaded")
    await rig.ensure_active(page)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    await ui.settle(page, 800)
    check("after a reload the card stays away", await Panel(page).tid("panel-first-use").count() == 0)
    old = int((datetime.now(timezone.utc) - 401 * DAY).timestamp() * 1000)
    await rig.sw.evaluate("(a) => chrome.storage.local.set(a)", {UI_PREFS_KEY: {"dock": "right", "dismissed": {"first_use": old}}})
    await page.reload(wait_until="domcontentloaded")
    await rig.ensure_active(page)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    await expect(Panel(page).tid("panel-first-use")).to_be_visible(timeout=millis(15_000))
    check("a dismissal 401 days old is forgotten: the card is offered again", True)


# ---------------------------------------------------------------------------------------------- end-of-form review
@scenario(needs=SUGGEST, timeout=240)
async def scenario_review_card_lists_the_required_questions_still_empty(env: Env) -> None:
    """Tab found no further question: the panel lists the required questions that are still empty (a reminder with a way to each, never
    a gate), the list follows the form, a click goes to the question, and the person can dismiss it. Native Tab is never blocked."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    await page.locator("#privacy_notice").focus()  # the last question of the form (a consent box: never ticked for the person)
    await Chip(page).expect_chip(state="CONSENT")
    await ui.tab(page)  # no further assist field: native Tab goes on to the page's own controls
    review = panel.tid("panel-review")
    await expect(review).to_be_visible(timeout=millis(15_000))
    title = (await panel.tid("review-title").inner_text()).strip()
    count = int(re.search(r"\d+", title).group())
    check("the title counts the required questions that are still empty (the whole form is empty: 9 are required)", count == 9, title)
    items = panel.tid("review-item")
    check("at most five are listed, the rest is a count", await items.count() == 5 and "+4" in await panel.tid("review-more").inner_text(),
          (await items.count(), await panel.tid("review-more").count()))
    check("native Tab was never blocked: focus left the form's last field", await ui.focused_id(page) != "privacy_notice", await ui.focused_id(page))
    await items.first.click()  # a way to each question
    await expect(page.locator("#first_name")).to_be_focused(timeout=millis(10_000))
    await Chip(page).expect_chip(state="VERIFIED", tone="verified")
    await ui.tab(page)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    await expect(panel.tid("review-title")).to_contain_text("8", timeout=millis(15_000))  # the list follows the form: one question fewer
    check("after an answer went in, the count went down by one", True)
    await panel.tid("review-dismiss").click()
    await expect(review).to_have_count(0, timeout=millis(10_000))
    check("the reminder can be dismissed", True)
    await ui.assert_never_submitted(page)
    ui.assert_untouched(await ui.synthetic_events(page), "gdpr_consent", "privacy_notice", "submit_app")


# ---------------------------------------------------------------------------------------------- Save to tracker
@scenario(needs=SUGGEST, timeout=240)
async def scenario_save_to_tracker_from_the_panel_menu(env: Env) -> None:
    """'Save to tracker' in the panel's menu puts the posting into Meridian's tracker WITHOUT saying it was sent: one bare PREPARING
    application of the extension, no task, no notification, no APPLIED event; saving again is the same row; once the person says
    they applied, a save is answered 'already recorded' and changes nothing."""
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=spa")
    check("browsing created no job and no application", backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 0
          and backend.db_rows("SELECT COUNT(*) AS n FROM jobs")[0]["n"] == 0)
    await panel.tid("panel-more").click()
    await expect(panel.tid("panel-track-save")).to_be_visible()
    await expect(panel.tid("panel-track-applied")).to_be_visible()
    await panel.tid("panel-track-save").click()
    await expect(page.locator("[data-testid=toast][data-kind=saved]").first).to_be_attached(timeout=millis(15_000))
    await flows.poll_until(lambda: _application_rows(backend, 1), timeout=15, message="the application row")
    rows = backend.db_rows("SELECT a.id,a.job_id,a.status,a.mode,j.source,j.company FROM applications a JOIN jobs j ON j.id=a.job_id")
    check("one application, bare PREPARING, made by the extension, for Acme Robotics",
          len(rows) == 1 and rows[0]["status"] == "PREPARING" and rows[0]["source"] == "extension" and rows[0]["company"] == "Acme Robotics", rows)
    check("it was not recorded as sent: no APPLYING/APPLIED/CONFIRMED event, nothing counted as applied",
          backend.db_rows("SELECT COUNT(*) AS n FROM application_events WHERE status IN ('APPLYING','APPLIED','CONFIRMED')")[0]["n"] == 0
          and _applied(backend) == 0)
    check("no human task was raised", backend.db_rows("SELECT COUNT(*) AS n FROM human_tasks")[0]["n"] == 0)
    check("and no 'new strong match' notification can fire for it",
          backend.db_rows("SELECT COUNT(*) AS n FROM notification_receipts WHERE key=?", ("match:" + rows[0]["job_id"],))[0]["n"] == 1)
    tracker = [row for row in await backend.tracker_rows() if "Acme Robotics" in str(row)]
    check("the tracker lists it", len(tracker) == 1, [r.get("company") for r in tracker])
    await panel.tid("panel-more").click()
    await expect(panel.tid("panel-track-applied")).to_be_visible()
    check("the menu no longer offers to save it again, and still offers 'I applied'", await panel.tid("panel-track-save").count() == 0)
    await expect(panel.tid("panel-sub")).to_contain_text(_dictionary("en")["panel.job.preparing"], timeout=millis(10_000))
    check("the panel says it is in the tracker", True)
    # the answer of the bridge itself: the same ids again, then 'already recorded' once the person said they applied
    sid = (await flows.assist_state(rig, page))["sessionId"]
    async with await extension_bridge(rig, backend) as bridge:
        again = (await bridge.post(f"/v1/sessions/{sid}/track", json={"event": "save", "evidence": "", "notes": ""})).json()
        check("saving again answers 'saved' with the same job and application", again["state"] == "saved" and again["tracked"] == "preparing"
              and (again["job_id"], again["application_id"]) == (rows[0]["job_id"], rows[0]["id"]), again)
        check("still exactly one application", backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 1)
        recorded = (await bridge.post(f"/v1/sessions/{sid}/track", json={"event": "record", "evidence": "I clicked submit", "notes": ""})).json()
        check("the person's own 'I applied' records it", recorded["tracked"] == "applied" and _applied(backend) == 1, recorded)
        late = (await bridge.post(f"/v1/sessions/{sid}/track", json={"event": "save", "evidence": "", "notes": ""})).json()
        check("a save of a sent application is 'already recorded' and changes nothing", late["state"] == "already_recorded" and late["tracked"] == "applied"
              and _applied(backend) == 1 and backend.db_rows("SELECT COUNT(*) AS n FROM application_events WHERE status='APPLIED'")[0]["n"] == 1, late)
        unknown = await bridge.post(f"/v1/sessions/{sid}/track", json={"event": "frobnicate", "evidence": "", "notes": ""})
        check("an unknown tracking event is refused, never taken for a submission", unknown.status_code == 422 and _applied(backend) == 1,
              (unknown.status_code, unknown.text[:120]))
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=240)
async def scenario_save_to_tracker_on_a_page_that_missed_the_news_says_it_is_already_recorded(env: Env) -> None:
    """The posting was recorded as SENT while this page was out of sight (a tab nobody is looking at is not polled): the page still offers
    'Save to tracker'. Pressing it must not claim to have saved anything: Meridian answers 'already recorded', the page says exactly that,
    puts up the 'you already applied' notice and changes nothing in the tracker."""
    en = _dictionary("en")
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=spa")
    await flows.poll_until(lambda: _session_open(rig, page), timeout=20, message="the page's session at Meridian")
    sid = (await flows.assist_state(rig, page))["sessionId"]
    tab = await rig.tab_id(page)
    await rig.set_visibility(page, False)
    await flows.poll_until(lambda: _is_hidden_tab(rig, tab), timeout=10, message="the worker to know the tab is hidden")
    async with await extension_bridge(rig, backend) as bridge:
        answer = await bridge.post(f"/v1/sessions/{sid}/track", json={"event": "record", "evidence": "recorded elsewhere", "notes": ""})
        recorded = answer.json()
    check(f"(the bridge took the record: {answer.status_code})", answer.status_code == 200, answer.text[:200])
    check("(the posting was recorded as sent while the page could not hear about it)", recorded["tracked"] == "applied" and _applied(backend) == 1, recorded)
    await panel.tid("panel-more").click()
    await expect(panel.tid("panel-track-save")).to_be_visible()
    check("the page still offers 'Save to tracker': it has not heard", await panel.tid("panel-duplicate").count() == 0)
    await panel.tid("panel-track-save").click()
    toast = page.locator("[data-testid=toast][data-kind=already_recorded]")
    await expect(toast.first).to_be_attached(timeout=millis(15_000))
    check("the page says it is already recorded", en["toast.already_recorded"] in (await toast.first.inner_text()), await toast.first.inner_text())
    check("and does NOT say it saved anything", await page.locator("[data-testid=toast][data-kind=saved]").count() == 0)
    banner = panel.tid("panel-duplicate")
    await expect(banner).to_have_attribute("data-state", "applied", timeout=millis(10_000))
    check("the 'you already applied' notice is up", True)
    check("the tracker is exactly as it was: one application, sent once", backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 1
          and backend.db_rows("SELECT COUNT(*) AS n FROM application_events WHERE status='APPLIED'")[0]["n"] == 1 and _applied(backend) == 1)
    await rig.set_visibility(page, True)
    await ui.assert_never_submitted(page)


async def _session_open(rig, page) -> bool:
    return bool((await flows.assist_state(rig, page)).get("sessionId"))


async def _is_hidden_tab(rig, tab: int) -> bool:
    return tab in (await rig.polling())["hidden"]


async def _application_rows(backend, count: int) -> bool:
    return backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == count


# ---------------------------------------------------------------------------------------------- duplicate banner
async def submit_and_record(env: Env, page, panel) -> None:
    """The user fills the short form, clicks Submit themselves, and says yes to 'Record this application?'."""
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, ex.SUBMIT_FLOW, label="submit_flow")
    await page.click("#submit_app")  # the user's own click
    await expect(page.locator("[data-testid=track-ask]")).to_be_visible(timeout=millis(20_000))
    await page.locator("[data-testid=track-record]").click()
    await flows.poll_until(lambda: _applied_one(env.backend), timeout=15, message="the application to be recorded")


async def _applied_one(backend) -> bool:
    return _applied(backend) == 1


@scenario(needs=SUGGEST, timeout=300)
async def scenario_duplicate_banner_after_record_and_reload(env: Env) -> None:
    """A posting Meridian has recorded as sent: recording it here is not a duplicate of anything, but the NEXT visit opens with the banner
    (with the day it was sent), Tab inserts nothing, 'Fill verified' writes nothing, and only the person's 'Continue anyway' lifts it."""
    en = _dictionary("en")
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=spa")
    await submit_and_record(env, page, panel)
    check("recording it here raised no 'you already applied' banner (this visit created it)", await panel.tid("panel-duplicate").count() == 0)
    # the next visit to the same posting (the form again, a new document)
    page = await env.open("submit_flow.html", "mode=spa")
    await rig.ensure_active(page)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    banner = panel.tid("panel-duplicate")
    await expect(banner).to_be_visible(timeout=millis(20_000))
    check("the banner says it was applied", await banner.get_attribute("data-state") == "applied")
    check("its title is the extension's own words", (await panel.tid("dup-title").inner_text()).strip() == en["dup.applied.title"])
    hint = (await panel.tid("dup-hint").inner_text()).strip()
    prefix, suffix = en["dup.applied.hint.since"].split("{date}")
    today = datetime.now().astimezone().strftime("%b %-d")  # the day it was recorded, written the way the panel writes a day
    check("and it names the day it was sent (the day Meridian recorded it)", hint.startswith(prefix) and hint.endswith(suffix) and today in hint,
          (hint, today))
    await page.locator("#first_name").focus()
    await Chip(page).expect_chip(state="VERIFIED", insert="never", value=seed.FIRST)
    await expect(Chip(page).part("chip-duplicate")).to_be_visible()
    check("the chip says why nothing goes in and offers no Insert", await Chip(page).part("chip-accept").count() == 0
          and await Chip(page).part("chip-replace").count() == 0)
    await ui.tab(page)
    await ui.expect_focus(page, "#last_name")
    check("Tab inserted nothing (and still moved on)", await ui.field_value(page, "#first_name") == "")
    fill = panel.tid("panel-fill-verified")
    if await fill.count() and await fill.is_enabled():
        await fill.click()
        await ui.settle(page, 800)
    if await panel.tid("fill-confirm").count():
        await panel.tid("fill-confirm").click()
        await ui.settle(page, 800)
    check("'Fill verified' wrote nothing either", [await ui.field_value(page, f"#{i}") for i in ("first_name", "last_name", "email")] == ["", "", ""])
    check("no write reached the page", await ui.synthetic_events(page) == [], (await ui.synthetic_events(page))[:2])
    await panel.tid("dup-continue").click()
    await expect(banner).to_have_count(0, timeout=millis(10_000))
    await page.locator("#first_name").focus()
    await Chip(page).expect_chip(state="VERIFIED", insert="tab", value=seed.FIRST)
    await ui.tab(page)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    check("after 'Continue anyway' Tab inserts again", True)
    check("the application is still recorded once and was not reopened", _applied(backend) == 1
          and backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 1)
    await ui.assert_never_submitted(page, user_clicks=0)


@scenario(needs=SUGGEST, timeout=300)
async def scenario_open_page_learns_it_was_sent_and_then_what_became_of_it(env: Env) -> None:
    """What Meridian knows about the posting reaches a page that is already open: saved in the tracker here (no banner: this visit did
    it), recorded as sent in Meridian's own screen (the open page says so), and, on the next visit, the banner. While the banner is up
    Meridian moves the application on (rejected): the wording follows, and the day it was sent stays the SENDING day, never the day it
    was rejected. A banner that said 'in your tracker' (not sent yet) escalates on the open page when it is recorded as sent."""
    en = _dictionary("en")
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=spa")
    check("nothing to warn about at first", await panel.tid("panel-duplicate").count() == 0)
    # visit 1: the person saves the posting from the panel; that is what this visit did, so it is no 'duplicate' of anything
    await panel.tid("panel-more").click()
    await panel.tid("panel-track-save").click()
    await flows.poll_until(lambda: _application_rows(backend, 1), timeout=15, message="the saved application")
    job_id = backend.db_rows("SELECT job_id FROM applications")[0]["job_id"]
    await expect(panel.tid("panel-sub")).to_contain_text(en["panel.job.preparing"], timeout=millis(10_000))
    check("saving here raised no banner", await panel.tid("panel-duplicate").count() == 0)
    # visit 2: the posting is in the tracker, not sent: a calm notice that blocks nothing
    page = await env.open("submit_flow.html", "mode=spa")
    await rig.ensure_active(page)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    banner = panel.tid("panel-duplicate")
    await expect(banner).to_be_visible(timeout=millis(20_000))
    check("the banner says it is only in the tracker", await banner.get_attribute("data-state") == "preparing")
    check("with the extension's own title", (await panel.tid("dup-title").inner_text()).strip() == en["dup.preparing.title"])
    await page.locator("#first_name").focus()
    await Chip(page).expect_chip(state="VERIFIED", insert="tab", value=seed.FIRST)
    check("a posting that is only in the tracker still takes answers (the notice blocks nothing)", True)
    # Meridian's own screen: the person records it as sent, three days ago
    sent = datetime.now(timezone.utc) - 3 * DAY
    await backend.post(f"/jobs/{job_id}/record-application", {"applied_at": sent.isoformat(), "notes": "recorded in Meridian"})
    await expect(banner).to_have_attribute("data-state", "applied", timeout=millis(60_000))
    check("the open page learned it was sent: the banner escalated", True)
    hint = (await panel.tid("dup-hint").inner_text()).strip()
    day = sent.astimezone().strftime("%b %-d")
    check("with the day it was sent", hint.startswith(en["dup.applied.hint.since"].split("{date}")[0]) and day in hint, (hint, day))
    await expect(panel.tid("panel-sub")).to_contain_text(en["panel.job.applied"], timeout=millis(10_000))
    await Chip(page).expect_chip(state="VERIFIED", insert="never")
    await ui.tab(page)
    check("Tab inserts nothing on the page that was open all along", await ui.field_value(page, "#first_name") == "")
    # Meridian moves the application on: it was rejected (an email arrived), today
    changed = backend.db_write("UPDATE applications SET status='REJECTED', updated_at=? WHERE job_id=?", (datetime.now(timezone.utc).isoformat(), job_id))
    check("(Meridian's own screens moved the application to REJECTED)", changed == 1)
    rejected = en["dup.rejected.hint.since"].split("{date}")
    await flows.poll_until(lambda: _banner_says(panel, rejected[1]), timeout=60, message="the banner to say it was rejected")
    hint = (await panel.tid("dup-hint").inner_text()).strip()
    check("the wording follows the status", hint.startswith(rejected[0]) and hint.endswith(rejected[1]), hint)
    check("and still names the day it was SENT, not the day it was rejected", day in hint, (hint, day))
    await page.locator("#first_name").focus()
    await Chip(page).expect_chip(insert="never")
    check("a rejected application still blocks Tab until the person continues", True)
    await ui.assert_never_submitted(page)


async def _banner_says(panel: Panel, fragment: str) -> bool:
    node = panel.tid("dup-hint")
    return await node.count() > 0 and fragment in (await node.first.inner_text())


# ---------------------------------------------------------------------------------------------- "Fill verified": cancel and undo all
@scenario(needs=SUGGEST, timeout=240)
async def scenario_fill_verified_can_be_cancelled_and_taken_back_in_one_key(env: Env) -> None:
    """'Fill verified' lists every field it will fill, with its value and without the page's required marker, and writes nothing until
    the person confirms. Cancel (or Esc) writes nothing. After a confirmed batch, Alt+Shift+Z takes the WHOLE batch back."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    button = panel.tid("panel-fill-verified")
    await expect(button).to_be_enabled(timeout=millis(15_000))
    plan = [row for row in ex.GREENHOUSE if row.inserted and row.state == ex.VERIFIED and row.insert == "tab" and row.bulk]
    await button.click()
    await expect(panel.tid("panel-fill-preview")).to_be_visible(timeout=millis(10_000))
    shown = await panel.tid("fill-preview-item").all_inner_texts()
    check("one line per field", len(shown) == len(plan), (len(shown), len(plan)))
    check("no line carries the page's required marker", not any("*" in line for line in shown), shown)
    values = {seed.FIRST, seed.LAST, seed.EMAIL, seed.PHONE, seed.LINKEDIN, seed.GITHUB, seed.PORTFOLIO}
    check("the values that go in are named", all(any(value in line for line in shown) for value in values), shown)
    check("nothing was written yet", await ui.synthetic_events(page) == [])
    await panel.tid("fill-cancel").click()
    await expect(panel.tid("panel-fill-preview")).to_have_count(0, timeout=millis(10_000))
    await ui.settle(page, 500)
    check("Cancel wrote nothing and the button is back", await ui.synthetic_events(page) == [] and await button.count() == 1)
    # the keyboard way in: Alt+Shift+M puts the keyboard into the panel, Enter on the button opens the question with the keyboard on
    # its confirm button, and Esc closes it and gives the keyboard back to the button
    await page.locator("#first_name").focus()
    await page.keyboard.press("Alt+Shift+KeyM")
    await flows.poll_until(lambda: _panel_has_keyboard(page), timeout=10, message="the keyboard to be inside the panel")
    await button.focus()
    await page.keyboard.press("Enter")
    await expect(panel.tid("panel-fill-preview")).to_be_visible(timeout=millis(10_000))
    await flows.poll_until(lambda: _focus_is(page, "fill-confirm"), timeout=10, message="the keyboard on the confirm button")
    await page.keyboard.press("Escape")  # inside the question: Esc closes it, the way it closes every other panel question
    await expect(panel.tid("panel-fill-preview")).to_have_count(0, timeout=millis(10_000))
    await flows.poll_until(lambda: _focus_is(page, "panel-fill-verified"), timeout=10, message="the keyboard back on 'Fill verified'")
    check("Esc closed the question, wrote nothing and gave the keyboard back to the button", await ui.synthetic_events(page) == [])
    await button.click()
    await panel.tid("fill-confirm").click()
    for row in plan:
        await flows.poll_until(lambda row=row: _row_done(page, row), timeout=20, message=f"{row.sel} filled")
    batch = panel.tid("panel-batch-undo")
    await expect(batch).to_be_visible(timeout=millis(10_000))
    check("the panel counts the batch", str(len(plan)) in await panel.tid("batch-count").inner_text(), await panel.tid("batch-count").inner_text())
    await page.locator("#first_name").focus()
    await page.keyboard.press("Alt+Shift+KeyZ")
    for row in plan:
        await flows.poll_until(lambda row=row: _row_empty(page, row), timeout=20, message=f"{row.sel} taken back")
    check("Alt+Shift+Z took the whole batch back", True)
    await expect(batch).to_have_count(0, timeout=millis(10_000))
    ui.assert_untouched(await ui.synthetic_events(page), *ex.GREENHOUSE_NEVER_WRITTEN)
    await ui.assert_never_submitted(page)


async def _panel_has_keyboard(page) -> bool:
    return await ui.assistant_focus(page) != ""


async def _focus_is(page, testid: str) -> bool:
    return await ui.assistant_focus(page) == testid


async def _row_done(page, row: ex.F) -> bool:
    return await flows.row_is_inserted(page, row)


async def _row_empty(page, row: ex.F) -> bool:
    return flows.is_empty(row, await flows.read_value(page, row))


# ---------------------------------------------------------------------------------------------- always-on switch
@scenario(needs=SUGGEST, timeout=240)
async def scenario_always_on_switch_asks_calmly_for_the_access_the_browser_has_not_given(env: Env) -> None:
    """'Start automatically on this site' needs the browser's access to the site. Where the worker has no click to ask with, the question
    opens in a small window and the panel says so in one calm line instead of a switch that would say Off: nothing is stored, the
    application tab stays where it is, and no rule exists until the person answers. (The e2e build holds the loopback sites over plain
    http; the switch asks for the secure origin of the site, which no fixture page has.)"""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    host = "127.0.0.1"
    await panel.tid("panel-more").click()
    switch = panel.tid("panel-always-on")
    await expect(switch).to_be_visible()
    check("no rule yet: the switch is off", await switch.get_attribute("aria-checked") == "false", await switch.get_attribute("aria-checked"))
    await switch.click()
    note = panel.tid("panel-always-on-permission")
    await expect(note).to_be_visible(timeout=millis(15_000))
    check("one calm line says what to do", (await note.inner_text()).strip() == _dictionary("en")["more.alwaysOn.permission"], await note.inner_text())
    check("the switch that would say Off is gone", await panel.tid("panel-always-on").count() == 0)
    check("nothing was stored: there is no rule", ((await rig.storage_local()).get("meridian.prefs") or {}).get("alwaysOn", []) == [])
    asked = [p.url for p in rig.ctx.pages if "options.html" in p.url]
    check("the question was opened in the extension's own small window, for this site", any("reason=permission" in u and host in u for u in asked), asked)
    check("the application tab is still the page the person was on", page.url.startswith(env.fixtures.top))
    await ui.settle(page, 1500)
    check("and the note stays until the access is given", await note.count() == 1)
    await ui.assert_never_submitted(page)


async def hold_meridians_site_list(rig, sites: list[str]) -> None:
    """Make the worker hold ``sites`` as Meridian's own 'start automatically' list and treat its copy of Meridian's settings as fresh (so a
    page load does not replace it). Meridian's API only accepts dotted hostnames and the fixtures live on a loopback address, so this is the
    one way to put a loopback site on the list; the worker-side logic that reads it is the real one."""
    await rig.sw.evaluate("""async (sites) => {
        const stored = (await chrome.storage.local.get('meridian.prefs'))['meridian.prefs'] || {};
        const settings = {...(stored.settings || {}), always_on_sites: sites};
        await chrome.storage.local.set({'meridian.prefs': {...stored, settings}});
        await chrome.storage.session.set({'meridian.settingsAt': Date.now()}); }""", sites)


@scenario(needs=SUGGEST, timeout=300)
async def scenario_meridian_site_list_reaches_the_worker_and_the_panel_names_the_rule_that_remains(env: Env) -> None:
    """Meridian's own 'start automatically' list reaches the browser at a page load, the panel's switch reads ON for a site on it, and
    switching it OFF cannot remove what is not the switch's to remove: the switch stays ON and one calm line says which rule still starts
    it and where to remove it. (Starting by itself over plain http needs access to both origins of the site, which no loopback fixture
    has: that half is covered by the worker's unit tests, not here.)"""
    backend = await env.start_backend()
    rig = await env.connected()
    # the real chain: Meridian's list reaches the worker (a page load asks again when its copy is old)
    response = await backend.request("PATCH", "/extension/settings", json={"always_on_sites": ["jobs.example.test"]}, expect=None)
    check("Meridian accepts the site list", response.status_code == 200, response.text[:200])
    await rig.sw.evaluate("() => chrome.storage.session.remove('meridian.settingsAt')")
    warm = await rig.new_page()
    await warm.goto(env.fixtures.url("decoy.html"), wait_until="domcontentloaded")
    await flows.poll_until(lambda: _worker_list(rig, ["jobs.example.test"]), timeout=30, message="Meridian's list to reach the worker at a page load")
    check("a page load made the worker read Meridian's list", True)
    await warm.close()
    # the loopback fixtures cannot be named in Meridian's list (its API wants dotted names): hold the loopback site as the worker would
    host = "127.0.0.1"
    await hold_meridians_site_list(rig, [host])
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    # the page's own first look at Meridian (the status call of the session) replaces the worker's copy of the list with Meridian's real
    # one: let it happen, then hold the loopback site again. The page already read the list at its start, which is what its switch shows.
    try:
        await flows.poll_until(lambda: _worker_list(rig, []), timeout=10, message="the page's first look at Meridian")
    except AssertionError:
        pass
    await hold_meridians_site_list(rig, [host])
    await panel.tid("panel-more").click()
    switch = panel.tid("panel-always-on")
    await expect(switch).to_have_attribute("aria-checked", "true", timeout=millis(15_000))
    check("the switch reads ON for a site Meridian lists", True)
    await switch.click()
    note = panel.tid("panel-always-on-rule")
    await expect(note).to_be_visible(timeout=millis(15_000))
    text = (await note.inner_text()).strip()
    check("one calm line names the rule that still starts it", host in text, text)
    check("the switch stays ON: the rule is not the switch's to remove", await panel.tid("panel-always-on").get_attribute("aria-checked") == "true")
    check("nothing was added to the browser's own list", ((await rig.storage_local()).get("meridian.prefs") or {}).get("alwaysOn", []) == [])
    await ui.assert_never_submitted(page)


async def _worker_list(rig, wanted: list[str]) -> bool:
    prefs = (await rig.storage_local()).get("meridian.prefs") or {}
    return (prefs.get("settings") or {}).get("always_on_sites") == wanted
