"""Wave-5 scenarios of the content-script rig (workstream UXFIN): what the wave-4 reviews handed to the content layer.

  1. the real chip never lies on the next two fields while a side is clear (and folds to its pill when none is)
  2. "Save to tracker" on a posting Meridian already has as sent says so; the duplicate notice carries its date and status
  3. the always-on switch: the note of a rule that still applies
  4. `drafts_paused` events
  5. "Use another CV"
  6. a popup the page opened next to a field: the key is the page's, a covered field is never written

Same rules as scenarios.py: REAL key presses, the view models handed to the UI and the messages the content script sent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from harness import FILE, NEEDS, T, Bg, active_name, chip_for, debug, emit, err, eventually, last_chip, last_panel, make_document, ok, tab, value_of
from scenarios import GH, Ctx, doc_ref, reached, scenario, watch_keys
from scenarios_wave4 import FIRST, JOB, overlap, rect_of, toasts

NARROW = (560, 900)
STACK = {"first name": T("Alex"), "last name": T("Newport"), "email": T("a@b.test"), "phone": T("+44 7700 900123"), "city": T("London")}
STACK_FIELDS = ["first_name", "last_name", "email", "phone", "city"]
# the part of a field a chip may lie on without it counting as "covering" it (a sliver of its margin): the UI's own threshold
SLIVER = 600


# ------------------------------------------------------------------------------------------------------------------------
# 1. the real chip stays off the next two fields while any side is clear


async def real_chip(page: Any, name: str) -> tuple[dict[str, float], bool]:
    """Focus `name` and wait for the REAL chip of that field to be placed: its box and whether it is the one-line pill."""
    await page.focus(f"input[name={name}]")
    chip = page.locator("[data-testid=chip]")

    async def placed() -> bool:
        state = await debug(page)
        if not state or not state["current"] or state["chipKey"] != state["current"]:
            return False
        return await chip.count() > 0 and await chip.first.get_attribute("data-key") == state["current"] and await chip.first.is_visible()

    await eventually(placed, 6, f"the chip of {name}")
    await asyncio.sleep(0.3)  # the fade-in and the placement that follows it
    box = await chip.first.bounding_box()
    assert box, "the chip has a box"
    return {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}, await chip.first.get_attribute("data-pill") == "true"


async def button_boxes(page: Any) -> list[dict[str, float]]:
    return await page.evaluate("""() => [...document.querySelectorAll('[data-testid=chip] button, [data-testid=chip] a')].map((b) => {
        const r = b.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; })""")


def click_point(chip: dict[str, float], field: dict[str, float], buttons: list[dict[str, float]]) -> tuple[float, float] | None:
    """A point of `field` that lies under the chip's body but under none of its buttons (a click there must reach the field)."""
    left, right = max(chip["x"], field["x"]), min(chip["x"] + chip["w"], field["x"] + field["w"])
    top, bottom = max(chip["y"], field["y"]), min(chip["y"] + chip["h"], field["y"] + field["h"])
    if right - left < 4 or bottom - top < 4:
        return None
    for fy in (0.5, 0.3, 0.7):
        for fx in (0.15, 0.5, 0.85, 0.3, 0.7):
            x, y = left + (right - left) * fx, top + (bottom - top) * fy
            if not any(b["x"] - 2 <= x <= b["x"] + b["w"] + 2 and b["y"] - 2 <= y <= b["y"] + b["h"] + 2 for b in buttons):
                return x, y
    return None


@scenario("real_chip_never_covers_the_next_two_fields_while_a_side_is_clear_and_folds_to_its_pill_when_none_is")
async def chip_next_two_fields(ctx: Ctx) -> None:
    clicked = 0
    for variant, query in (("base", ""), ("big labels", "&label=16"), ("roomy", "&gap=40")):
        for top in (4, 120, 400):
            page, _ = await ctx.open(f"stacked.html?top={top}{query}", STACK, headless_ui=False, viewport=NARROW)
            for i, name in enumerate(STACK_FIELDS[:4]):
                chip, pill = await real_chip(page, name)
                me = await rect_of(page, f"input[name={name}]")
                following = [(n, await rect_of(page, f"input[name={n}]")) for n in STACK_FIELDS[i + 1:i + 3]]
                covered = [(n, overlap(chip, r)) for n, r in following]
                where = f"{variant}, form at {top}px, on {name}"
                if any(area >= SLIVER for _, area in covered):
                    # Only when NO side is clear: the same chip above the field would not fit in the window (or it is no longer one line)
                    assert pill, f"{where}: the chip covers {covered} and it is not folded to its pill"
                    assert me["y"] - 6 - chip["h"] < 8, f"{where}: the chip covers {covered} although there is room above the field ({me['y']:.0f}px)"
                # a click on any field under the chip (anywhere but on its own buttons) focuses that field
                buttons = await button_boxes(page)
                for n, r in following:
                    point = click_point(chip, r, buttons) if overlap(chip, r) > 0 else None
                    if point:
                        await page.mouse.click(*point)
                        assert await active_name(page) == n, f"{where}: a click on {n} under the chip focused {await active_name(page)}"
                        clicked += 1
                        break
            await ctx.rig.reset()
    assert clicked >= 2, f"the click-through was exercised {clicked} times"


@scenario("real_chip_of_the_last_field_of_a_stack_sits_below_it_when_nothing_follows")
async def chip_last_field(ctx: Ctx) -> None:
    page, _ = await ctx.open("stacked.html?top=200", STACK, headless_ui=False, viewport=NARROW)
    chip, pill = await real_chip(page, "city")
    me = await rect_of(page, "input[name=city]")
    assert chip["y"] >= me["y"] + me["h"], "nothing follows: the chip is below the field it is for, not on top of the previous one"


# ------------------------------------------------------------------------------------------------------------------------
# 2. "Save to tracker" on a posting Meridian has as sent


def track_answers(state: str, tracked: str) -> Any:
    def answer(msg: dict[str, Any], frame: Any) -> Any:
        if msg["request"]["event"] == "save":
            return ok({"state": state, "job_id": "j1", "application_id": "a1", "tracked": tracked})
        return None

    return answer


@scenario("save_to_tracker_on_a_posting_meridian_has_as_sent_says_already_recorded_and_raises_the_notice_and_never_says_saved")
async def save_already_recorded(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "tracked": "none", "application_id": None, "job_id": None}))
    assert (await last_panel(page))["duplicate"] is None, "this page began with nothing recorded"
    bg.overrides["track"] = track_answers("already_recorded", "applied")
    await emit(page, {"type": "track-save"})

    async def said() -> bool:
        return any(t["kind"] == "already_recorded" for t in await toasts(page))

    await eventually(said, 3, "the person is told it is already recorded")
    assert not any(t["kind"] == "saved" for t in await toasts(page)), "and it never says 'saved'"
    panel = await last_panel(page)
    assert panel["job"]["tracked"] == "applied", panel["job"]
    assert panel["duplicate"] == {"state": "applied", "status": "applied"}, "the notice of a sent application is up (no date: the answer carries none)"
    await page.focus(FIRST)
    chip = await chip_for(page, "first name")
    assert chip["duplicateBlocksInsert"] is True and chip["insert"] == "never"
    await tab(page)
    assert await value_of(page, FIRST) == "", "nothing is inserted until the person says Continue"
    # "Continue anyway" stays answered: a second press says it again but does not put the notice back up
    await emit(page, {"type": "duplicate-continue"})
    await eventually(lambda: _no_notice(page), 3, "the notice is answered")
    await emit(page, {"type": "track-save"})
    await asyncio.sleep(0.4)
    assert (await last_panel(page))["duplicate"] is None, "an answered notice is not raised again"


async def _no_notice(page: Any) -> bool:
    return (await last_panel(page))["duplicate"] is None


@scenario("save_to_tracker_after_a_tracked_event_says_so_without_asking_and_the_notice_has_its_date_and_status")
async def save_after_event(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "tracked": "none", "application_id": None, "job_id": None}))
    # the application was sent from the app or another tab: the poll says so, with what became of it and when
    bg.push_event({"type": "tracked", "tracked": "applied", "status": "REJECTED", "since": "2026-03-04"})

    async def known() -> bool:
        return (await last_panel(page))["job"]["tracked"] == "applied"

    await eventually(known, 5, "the panel knows it was sent")
    await emit(page, {"type": "track-save"})
    await eventually(lambda: _notice_with(page, "rejected", "2026-03-04"), 3, "the notice carries the date and what became of it")
    assert not [m for m in bg.msgs("track") if m["request"]["event"] == "save"], "a posting that is known to be sent is not asked about again"
    assert any(t["kind"] == "already_recorded" for t in await toasts(page))
    assert not any(t["kind"] == "saved" for t in await toasts(page))
    # a plain 'saved' answer is still a save
    page2, bg2 = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "tracked": "none", "application_id": None, "job_id": None}))
    await emit(page2, {"type": "track-save"})
    await eventually(lambda: _saved_toast(page2), 3, "a save is still a save")
    assert not any(t["kind"] == "already_recorded" for t in await toasts(page2))
    assert (await last_panel(page2))["job"]["tracked"] == "preparing" and (await last_panel(page2))["duplicate"] is None


async def _notice_with(page: Any, status: str, since: str) -> bool:
    dup = (await last_panel(page))["duplicate"]
    return bool(dup) and dup.get("status") == status and dup.get("since") == since and dup["state"] == "applied"


async def _saved_toast(page: Any) -> bool:
    return any(t["kind"] == "saved" for t in await toasts(page))


# ------------------------------------------------------------------------------------------------------------------------
# 3. the always-on switch: the note of a rule that still applies


@scenario("switching_always_on_off_while_another_rule_still_applies_keeps_the_switch_on_and_names_the_rule_in_one_line")
async def always_on_rule(ctx: Ctx) -> None:
    host = "127.0.0.1"
    prefs = {"language": "auto", "alwaysOn": [host], "hintOff": [], "settings": None, "welcomed": True}
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, settings={"always_on_sites": [host]}, prefs=prefs))
    assert (await last_panel(page))["site"]["alwaysOn"] is True
    detail = ("The assistant still starts by itself here because of the rule for 127.0.0.1. "
              "Remove it on the extension's options page or in Meridian (Settings → Browser assistant).")
    bg.overrides["site:always-on"] = lambda msg, frame: err("rule", detail)
    await emit(page, {"type": "always-on-site"})

    async def noted() -> bool:
        return bool((await last_panel(page))["site"].get("alwaysOnNote"))

    await eventually(noted, 4, "the panel carries the note")
    site = (await last_panel(page))["site"]
    assert site["alwaysOn"] is True, "a rule still starts the assistant here: the switch says so"
    assert site["alwaysOnNote"] == {"code": "rule", "rule": host, "text": detail}, site
    assert not site.get("alwaysOnNeedsPermission")
    assert not [t for t in await toasts(page) if t["kind"] == "error"], "a calm line, not an alarm"
    # asking again is a new try: the old line goes while it is asked, and comes back with the new answer
    bg.overrides["site:always-on"] = lambda msg, frame: err("rule", "The assistant still starts by itself here because of the rule for *.example.test. Remove it somewhere.")
    await emit(page, {"type": "always-on-site"})
    await eventually(lambda: _note_rule(page, "*.example.test"), 4, "the new answer's rule")
    # any other failure of the worker: a calm toast, never a switch that says nothing
    bg.overrides["site:always-on"] = lambda msg, frame: err("invalid", "This page cannot be added.")
    await emit(page, {"type": "always-on-site"})

    async def errored() -> bool:
        return any(t["kind"] == "error" for t in await toasts(page))

    await eventually(errored, 4, "an error toast for an answer nobody planned for")


async def _note_rule(page: Any, rule: str) -> bool:
    note = (await last_panel(page))["site"].get("alwaysOnNote")
    return bool(note) and note["rule"] == rule


# ------------------------------------------------------------------------------------------------------------------------
# 4. drafts_paused


@scenario("a_drafts_paused_event_marks_the_panel_and_keeps_draft_with_ai_working_and_a_new_session_clears_it")
async def drafts_paused(ctx: Ctx) -> None:
    answers = {**GH, "last name": NEEDS("setup", "Write it yourself.", needs={
        "root_cause": "ASYNC", "code": "missing_fact", "message": "Not in your profile.", "actions": [{"type": "draft_with_ai"}]})}
    page, bg = await ctx.open("greenhouse.html", bg=Bg(answers))
    assert not (await last_panel(page))["generation"].get("draftsPaused")
    bg.push_event({"type": "drafts_paused", "message": "Paused", "code": "AUTO_DRAFT_LIMIT"})

    async def paused() -> bool:
        return (await last_panel(page))["generation"].get("draftsPaused") is True

    await eventually(paused, 5, "the panel is told")
    generation = (await last_panel(page))["generation"]
    assert generation["available"] is True and generation["budgetExhausted"] is False, "writing is not unavailable: only the automatic drafts stopped"
    await page.focus('input[name="job_application[last_name]"]')
    chip = await chip_for(page, "last name")
    assert chip["canDraft"] is True, "an explicit draft is still one click away"
    # the page's session starts over (Meridian restarted): the count of automatic drafts starts over too
    bg.overrides["poll"] = lambda msg, frame: err("expired", "gone")
    await eventually(lambda: _paused_is(page, False), 8, "a new session clears the note")


async def _paused_is(page: Any, value: bool) -> bool:
    return bool((await last_panel(page))["generation"].get("draftsPaused")) is value


# ------------------------------------------------------------------------------------------------------------------------
# 5. Use another CV


def two_documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    doc = make_document("Alex-CV.pdf")
    other = {**make_document("Alex-CV-data.pdf", payload=b"%PDF-1.4\n% another approved cv\n"), "id": "doc-2"}
    offered = {**doc_ref(doc), "alternatives": [{"id": "doc-2", "filename": "Alex-CV-data.pdf", "version": 3}]}
    return doc, other, offered


async def attached(page: Any) -> list[str]:
    return await page.evaluate("[...document.querySelector('input[name=\"resume\"]').files].map((f) => f.name)")


@scenario("another_cv_is_a_click_on_the_chip_and_never_what_tab_does_and_the_one_swapped_out_stays_one_click_away")
async def use_another_cv(ctx: Ctx) -> None:
    doc, other, offered = two_documents()
    bg = Bg({"resume": FILE(offered)}, documents={"doc-1": doc, "doc-2": other})
    page, bg = await ctx.open("greenhouse.html", bg=bg)
    await page.focus('input[name="resume"]')
    chip = await chip_for(page, "resume")
    assert chip["alternatives"] == [{"id": "doc-2", "filename": "Alex-CV-data.pdf", "version": 3}] and chip["documentKind"] == "cv", chip.get("alternatives")
    await tab(page)
    await eventually(lambda: _attached_is(page, ["Alex-CV.pdf"]), 5, "Tab attaches the document on offer, never an alternative")
    assert [m["id"] for m in bg.msgs("document:content")] == ["doc-1"]
    await page.focus('input[name="resume"]')
    chip = await chip_for(page, "resume")
    await emit(page, {"type": "use-cv", "id": "doc-2", "key": chip["key"]})
    await eventually(lambda: _attached_is(page, ["Alex-CV-data.pdf"]), 5, "the chosen document replaced the one that was attached")
    assert [m["id"] for m in bg.msgs("document:content")] == ["doc-1", "doc-2"]

    async def swapped() -> bool:
        c = await last_chip(page)
        return bool(c) and c["value"] == "Alex-CV-data.pdf" and c.get("alternatives") == [{"id": "doc-1", "filename": "Alex-CV.pdf", "version": 0}]

    await eventually(swapped, 4, "the chip talks about the document that is really there, and the first one is an alternative now")
    assert not [t for t in await toasts(page) if t["kind"] == "error"]
    # back to the first one, the same way
    await emit(page, {"type": "use-cv", "id": "doc-1"})  # (`key` is optional: the field the chip is on)
    await eventually(lambda: _attached_is(page, ["Alex-CV.pdf"]), 5, "and back")
    # only a document Meridian offered for THIS field can be chosen
    before = len(bg.msgs("document:content"))
    await emit(page, {"type": "use-cv", "id": "doc-evil"})
    await asyncio.sleep(0.4)
    assert len(bg.msgs("document:content")) == before and await attached(page) == ["Alex-CV.pdf"], "an id nobody offered fetches and attaches nothing"
    # with one document there is no menu
    solo = Bg({"resume": FILE(doc_ref(doc))}, documents={"doc-1": doc})
    page2, _ = await ctx.open("greenhouse.html", bg=solo)
    await page2.focus('input[name="resume"]')
    chip2 = await chip_for(page2, "resume")
    assert "alternatives" not in chip2 and "documentKind" not in chip2, chip2


async def _attached_is(page: Any, names: list[str]) -> bool:
    return await attached(page) == names


@scenario("a_document_that_cannot_be_fetched_leaves_the_field_and_the_offer_as_they_were")
async def another_cv_unavailable(ctx: Ctx) -> None:
    doc, _, offered = two_documents()
    page, bg = await ctx.open("greenhouse.html", bg=Bg({"resume": FILE(offered)}, documents={"doc-1": doc}))  # doc-2 is not there: not_found
    await page.focus('input[name="resume"]')
    chip = await chip_for(page, "resume")
    await emit(page, {"type": "use-cv", "id": "doc-2", "key": chip["key"]})

    async def told() -> bool:
        return any(t["kind"] == "error" for t in await toasts(page))

    await eventually(told, 4, "a calm error")
    assert await attached(page) == [], "nothing was attached"
    await tab(page)
    await eventually(lambda: _attached_is(page, ["Alex-CV.pdf"]), 5, "the document on offer still goes in with Tab")


@scenario("another_cv_is_refused_while_the_duplicate_notice_is_up")
async def another_cv_duplicate(ctx: Ctx) -> None:
    doc, other, offered = two_documents()
    page, bg = await ctx.open("greenhouse.html", bg=Bg({"resume": FILE(offered)}, documents={"doc-1": doc, "doc-2": other}, job=JOB))
    await page.focus('input[name="resume"]')
    chip = await chip_for(page, "resume")
    assert "alternatives" not in chip or chip["duplicateBlocksInsert"], "nothing can be chosen while 'I won't insert anything' is true"
    await emit(page, {"type": "use-cv", "id": "doc-2", "key": chip["key"]})
    await asyncio.sleep(0.5)
    assert await attached(page) == [] and not bg.msgs("document:content")


# ------------------------------------------------------------------------------------------------------------------------
# 6. a popup the page opened next to a field


@scenario("a_date_fields_calendar_gets_the_tab_it_closes_on_the_answer_goes_in_and_focus_moves_on_with_nothing_left_open_over_the_next_field")
async def calendar_gets_its_tab(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "earliest start": T("2026-11-01"), "notice period": T("30"), "city": T("London")}
    page, bg = await ctx.open("calendar_popup.html", answers)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)  # inserts and walks to the date field (a plain field: the assistant's own walk); its calendar opens on focus
    assert await active_name(page) == "start"
    await eventually(lambda: page.evaluate("window.calendarOpen()"), 3, "the calendar opened on focus")
    await chip_for(page, "earliest start")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=start]") == "2026-11-01", "the answer went in"
    assert await reached(page) == [["Tab", False]], "and the Tab was not swallowed: the page saw it"
    assert await page.evaluate("window.tabsSeenByTheWidget") == 1
    assert not await page.evaluate("window.calendarOpen()"), "so the calendar closed itself, as it does for anybody"
    assert await active_name(page) == "notice", "the browser moved on to the next field"
    # the next field is free: its Tab inserts and walks on by itself
    await chip_for(page, "notice period")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=notice]") == "30" and await active_name(page) == "city"
    assert await reached(page) == [], "an ordinary Tab is still the assistant's"


@scenario("shift_tab_in_a_date_field_with_its_calendar_open_belongs_to_the_page_and_tab_only_inserts_when_auto_advance_is_off")
async def calendar_other_keys(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "earliest start": T("2026-11-01"), "notice period": T("30"), "city": T("London")}
    page, bg = await ctx.open("calendar_popup.html", answers)
    await page.focus("input[name=start]")
    await chip_for(page, "earliest start")
    await tab(page, shift=True)
    assert await value_of(page, "input[name=start]") == "", "walking back never inserts"
    assert await page.evaluate("window.tabsSeenByTheWidget") == 1, "the page's widget saw the key (the assistant's own walk comes after the page has had its say)"
    assert not await page.evaluate("window.calendarOpen()"), "so the calendar closed itself on it"
    assert await active_name(page) == "first_name", "and the person went back one field"
    # "Tab only inserts" (auto advance off): the answer goes in, the key is swallowed and the person stays where they are
    page2, bg2 = await ctx.open("calendar_popup.html", bg=Bg(answers, settings={"auto_advance": False}))
    await page2.focus("input[name=start]")
    await chip_for(page2, "earliest start")
    await watch_keys(page2)
    await tab(page2)
    assert await value_of(page2, "input[name=start]") == "2026-11-01"
    assert await reached(page2) == [] and await active_name(page2) == "start", "the setting says Tab only inserts: the key stays with the assistant"
    await tab(page2)  # now nothing is left to insert: the page's own Tab closes the calendar and the browser moves on
    assert await active_name(page2) == "notice" and not await page2.evaluate("window.calendarOpen()")


@scenario("a_calendar_that_stays_open_over_the_next_field_never_gets_that_field_written_and_the_key_goes_on")
async def calendar_stays_open(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "earliest start": T("2026-11-01"), "notice period": T("30"), "city": T("London")}
    page, bg = await ctx.open("calendar_popup.html?sticky=1", answers)
    await page.focus("input[name=start]")
    await chip_for(page, "earliest start")
    await tab(page)
    assert await value_of(page, "input[name=start]") == "2026-11-01"
    assert await active_name(page) == "notice"
    assert await page.evaluate("window.calendarOpen()"), "this widget never closes by itself: it lies over the notice field"
    await chip_for(page, "notice period")
    errors = len([t for t in await toasts(page) if t["kind"] == "error"])
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=notice]") == "", "nobody can see the notice field: nothing is written under the calendar"
    assert len([t for t in await toasts(page) if t["kind"] == "error"]) > errors, "and the person is told, calmly"
    assert await reached(page) == [["Tab", False]] and await active_name(page) == "city", "the key was never trapped"
    # the calendar goes: the field is written again
    await page.evaluate("window.closeCalendar()")
    await tab(page, shift=True)
    assert await active_name(page) == "notice"
    await chip_for(page, "notice period")
    await tab(page)
    assert await value_of(page, "input[name=notice]") == "30"


@scenario("the_popup_the_page_calls_a_dialog_is_no_excuse_to_write_under_it_while_a_plain_layer_of_a_neighbour_still_is")
async def popup_with_a_role_covers(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "city": T("London")}
    page, bg = await ctx.open("popup_cover.html?role=dialog", answers)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await page.evaluate("showPopup()")  # the first question's calendar, a dialog, lies over the email field
    await asyncio.sleep(1.4)
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    assert await active_name(page) == "email"
    await chip_for(page, "email")
    await tab(page)
    assert await value_of(page, "input[name=email]") == "", "a calendar the page calls a dialog covers the email field: nothing goes in under it"


@scenario("a_form_inside_a_modal_dialog_and_a_cookie_bar_are_no_popups_of_a_field")
async def modal_is_not_a_popup(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test")}
    page, bg = await ctx.open("modal_form.html", answers, viewport=(1200, 420))  # (the cookie bar is then within 300 px of the fields)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex" and await active_name(page) == "last_name"
    assert await reached(page) == [], "the assistant's own walk: the dialog around the form and the cookie bar do not hand the key to the page"


# ------------------------------------------------------------------------------------------------------------------------
# UXR-12: the field Tab lands on leaves room for a chip under it


@scenario("tab_leaves_room_for_a_chip_under_the_field_it_lands_on_whenever_the_page_can_scroll")
async def room_for_the_chip(ctx: Ctx) -> None:
    height = 700
    page, _ = await ctx.open("many_fields.html", {}, viewport=(1200, height))
    await page.focus("input[name=q1]")
    scrolled = 0
    for step in range(70):
        await tab(page)
        name = await active_name(page)
        field = await rect_of(page, f"input[name={name}]")
        room = height - (field["y"] + field["h"])
        at_end = await page.evaluate("window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 1")
        assert room >= 170 or at_end, f"step {step}: {name} has {room:.0f}px under it (a chip needs about 170)"
        assert field["y"] >= 0, f"step {step}: {name} is above the window"
        scrolled = max(scrolled, await page.evaluate("window.scrollY"))
    assert scrolled > 0, "the walk was long enough to need scrolling"
