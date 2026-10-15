"""Wave-3 scenarios of the content-script rig (workstream CTRL2): what the review of waves 1-2c found in the content logic. Same rules as
scenarios.py: REAL key presses, the view models handed to the UI and the messages the content script sent. Every scenario here fails
without its fix (the report names the mutation that proves it).
"""

from __future__ import annotations

import asyncio
from typing import Any

from harness import (
    BOXES, NEEDS, RADIO, SELECT, STATE, T, Bg, active_name, chip_for, debug, emit, err, eventually, press_fill_verified, last_chip, last_panel, normalize, snapshot, sug,
    tab, ui_log, value_of,
)
from scenarios import GH, Ctx, field_info, scenario


async def asks(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "askSave") if c["arg"]]


async def toasts(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "toast")]


async def rect_of(page: Any, selector: str) -> dict[str, float]:
    return await page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect(); return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}; }",
        selector)


def node_of(info: dict[str, Any]) -> int:
    return int(info["key"].split(":")[1])


def draft_for(info: dict[str, Any], text: str, *, generation_id: str = "g1", based_on: dict[str, Any] | None = None, kv: str = "kv1",
              status: str = "DONE") -> dict[str, Any]:
    """The GENERATED_DRAFT suggestion the bridge sends for the field `info` (a `field_info` row)."""
    node = node_of(info)
    out = sug({"node_id": node, "context": info["label"]}, {"frame_key": "top"}, state="GENERATED_DRAFT", op="TYPE_TEXT", text=text,
              generation={"id": generation_id, "status": status, "attempt": 1, "word_count": 12}, kv=kv, question_id=f"q:top:{node}",
              why={"code": "generated", "summary": "Based on your profile and the company"})
    out["based_on"] = based_on or {"job_description": True, "company_pages": 3, "research_state": "done"}
    return out


async def deliver_draft(page: Any, bg: Bg, label: str, draft: dict[str, Any]) -> None:
    bg.push_event({"type": "suggestion", "suggestion": draft})

    async def arrived() -> bool:
        field = await field_info(page, label)
        return field["state"] == "GENERATED_DRAFT"

    await eventually(arrived, 4, "the draft arrived")


# ------------------------------------------------------------------------------------------------------------------------
# CPL-05: the recommended option of a choice group is on the chip as rectangles the UI can outline


@scenario("the_recommended_options_of_a_choice_group_are_handed_to_the_ui_as_rectangles")
async def option_anchors(ctx: Ctx) -> None:
    answers = {"preferred work mode": RADIO("Hybrid", "hybrid"), "which languages": BOXES("English", "Spanish")}
    page, bg = await ctx.open("choices.html", answers)
    await page.focus("input[name=mode][value=remote]")
    chip = await chip_for(page, "preferred work mode")
    hybrid = await rect_of(page, "input[name=mode][value=hybrid]")
    assert chip["optionAnchors"] == [hybrid] and chip["optionAnchor"] == hybrid, (chip.get("optionAnchors"), hybrid)
    await page.focus("input[name=lang][value=en]")
    chip = await chip_for(page, "which languages")
    wanted = [await rect_of(page, "input[name=lang][value=en]"), await rect_of(page, "input[name=lang][value=es]")]
    assert chip["optionAnchors"] == wanted, "a checkbox group recommends several options: one rectangle each"
    assert chip["optionAnchor"] == wanted[0]
    assert await rect_of(page, "input[name=lang][value=fr]") not in chip["optionAnchors"]


# ------------------------------------------------------------------------------------------------------------------------
# CPL-06 / CPL-17: a file field the assistant leaves alone says why; the panel names it as the page does


def skip_file(reason_rules: list[str] | None = None) -> dict[str, Any]:
    return STATE("SKIP", why={"code": "skip", "summary": "Meridian only attaches your approved CV to a CV upload.", "rules": reason_rules or ["file"], "facts": []})


@scenario("a_file_field_that_is_skipped_explains_why_and_the_panel_uses_the_printed_label")
async def skipped_file_fields(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "resume": skip_file(), "cover letter": skip_file(), "writing sample": skip_file()}
    bg = Bg(answers, profile={"verified_facts": 12, "has_cv": False, "unapproved_cvs": 1, "display_name": "Alex"})
    page, bg = await ctx.open("files_labels.html", bg=bg)
    await eventually(lambda: bg.msgs("status"), 4, "status asked")
    await asyncio.sleep(0.2)
    # (a) the CV is imported but not approved: the chip says what to do, in one calm sentence
    await page.focus("#resume")
    chip = await chip_for(page, "resume")
    assert chip["state"] == "SKIP" and chip["insert"] == "never" and chip["value"] == ""
    assert chip["skipReason"]["code"] == "file_unapproved" and "Approve your CV" in chip["skipReason"]["text"]
    assert chip["keys"] == ["esc"], "nothing to insert: no Tab hint"
    # (b) a cover letter is never attached, by design
    await page.focus("#cover_letter")
    chip = await chip_for(page, "cover letter")
    assert chip["skipReason"]["code"] == "file_cover_letter"
    # (c) any other upload: the server's own sentence
    await page.focus("#sample")
    chip = await chip_for(page, "writing sample")
    assert chip["skipReason"]["code"] == "generic" and "approved CV" in chip["skipReason"]["text"]
    # (d) the panel lists the field by what the page prints, not by the name the engine reads
    panel = await last_panel(page)
    labels = [i["label"] for i in panel["progress"]["items"]]
    assert "Resume/CV" in labels and "Cover Letter" in labels, labels
    assert not [lbl for lbl in labels if "[" in lbl], labels
    # (e) nothing imported at all: say so
    page2, bg2 = await ctx.open("files_labels.html", bg=Bg(answers, profile={"verified_facts": 12, "has_cv": False, "unapproved_cvs": 0, "display_name": "Alex"}))
    await eventually(lambda: bg2.msgs("status"), 4, "status asked")
    await asyncio.sleep(0.2)
    await page2.focus("#resume")
    chip = await chip_for(page2, "resume")
    assert chip["skipReason"]["code"] == "file_none"
    # (f) a password, a hidden or a read-only field stays silent (only uploads explain themselves)
    readonly = STATE("SKIP", why={"code": "skip", "summary": "x", "rules": ["readonly"], "facts": []})
    page3, _ = await ctx.open("greenhouse.html", bg=Bg({"first name": readonly}))
    await page3.focus("input[name='job_application[first_name]']")
    await asyncio.sleep(0.3)
    assert await last_chip(page3) is None


# ------------------------------------------------------------------------------------------------------------------------
# CPL-09: a draft written for another company / profile / older research says so and is never inserted by Tab


@scenario("a_draft_whose_company_the_person_rejected_is_marked_never_tab_inserted_and_rewritten_only_on_request")
async def stale_draft_company_rejected(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test")}
    bg = Bg(answers, research=snapshot("run1", "done"))
    page, bg = await ctx.open("draft.html", bg=bg)
    info = await field_info(page, "why do you want")
    await deliver_draft(page, bg, "why do you want", draft_for(info, "I admire Acme Robotics' mission and my pipeline work fits."))
    await page.focus("textarea[name=why]")
    chip = await chip_for(page, "why do you want")
    assert "staleReason" not in chip and chip["insert"] == "tab" and chip["canRegenerate"] is True
    # the person says "Not this company?": nothing is regenerated by itself, the draft just stops being insertable
    suggests = len(bg.suggest_requests())
    await emit(page, {"type": "research-company", "company": "Zeta Dynamics"})

    async def marked() -> bool:
        c = await last_chip(page)
        return bool(c) and c.get("staleReason") == "company-rejected"

    await eventually(marked, 4, "the draft is marked as written for the rejected company")
    chip = await last_chip(page)
    assert chip["staleBlocksInsert"] is True and chip["insert"] == "never" and "tab" not in chip["keys"] and chip["canRegenerate"] is True
    await asyncio.sleep(0.7)  # past the dwell: only the rejected company keeps Tab out
    await tab(page)
    assert await value_of(page, "textarea[name=why]") == "", "a draft written for the wrong company is never inserted by Tab"
    await emit(page, {"type": "accept", "key": info["key"]})
    await asyncio.sleep(0.3)
    assert await value_of(page, "textarea[name=why]") == "", "nor by a click"
    assert not bg.msgs("regenerate") and len(bg.suggest_requests()) == suggests, "nothing regenerates or re-drafts by itself"
    # Rewrite is the explicit way out: a FRESH draft from the new research, not a revision of the wrong text
    await emit(page, {"type": "rewrite", "key": info["key"]})
    await eventually(lambda: bg.msgs("regenerate"), 3, "the rewrite request")
    request = bg.msgs("regenerate")[0]["request"]
    assert request["instruction"] == "again" and request["current_text"] is None and request["question_id"] == f"q:top:{node_of(info)}"
    # the rewrite is ONE new draft job: its answer to the request and its result on the poll carry the same `generation.id`
    rewritten = "Zeta Dynamics builds robots and that is what I want to build."
    bg.push_event({"type": "suggestion", "suggestion": draft_for(info, rewritten, generation_id="rev1")})
    await page.focus("textarea[name=why]")

    async def fresh_chip() -> dict[str, Any] | None:
        c = await last_chip(page)
        return c if c and c["value"] == rewritten and c["state"] == "GENERATED_DRAFT" else None

    await eventually(fresh_chip, 5, "the chip shows the rewritten draft")
    await asyncio.sleep(0.7)
    chip = await chip_for(page, "why do you want")
    assert "staleReason" not in chip and chip["insert"] == "tab" and "tab" in chip["keys"]
    await tab(page)
    assert (await value_of(page, "textarea[name=why]")).startswith("Zeta Dynamics")


@scenario("a_draft_written_before_the_research_finished_offers_a_rewrite_but_still_inserts_and_a_new_profile_blocks_it")
async def stale_draft_research_and_profile(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test")}
    bg = Bg(answers, research=snapshot("run1", "running"))
    page, bg = await ctx.open("draft.html", bg=bg)
    info = await field_info(page, "why do you want")
    await deliver_draft(page, bg, "why do you want", draft_for(info, "A draft written while the research was still running.",
                                                               based_on={"job_description": True, "company_pages": 0, "research_state": "running"}))
    await page.focus("textarea[name=why]")
    chip = await chip_for(page, "why do you want")
    assert "staleReason" not in chip
    # the research finishes AFTER the draft: it is "based on older research", and Tab still inserts it (the person decides)
    bg.push_event({"type": "research", "research": snapshot("run1", "done", version=5)})

    async def marked() -> bool:
        c = await last_chip(page)
        return bool(c) and c.get("staleReason") == "research"

    await eventually(marked, 4, "the draft is marked as based on older research")
    chip = await last_chip(page)
    assert "staleBlocksInsert" not in chip and chip["insert"] == "tab" and chip["canRegenerate"] is True
    await asyncio.sleep(0.7)
    await tab(page)
    assert await value_of(page, "textarea[name=why]") == "A draft written while the research was still running."
    # a draft the server says was written under another Profile version is not inserted at all
    page2, bg2 = await ctx.open("draft.html", bg=Bg(answers, research=snapshot("run1", "done")))
    info2 = await field_info(page2, "why do you want")
    await deliver_draft(page2, bg2, "why do you want", draft_for(info2, "Written under an older profile.", based_on={
        "job_description": True, "company_pages": 3, "research_state": "done", "knowledge_version": "kv0"}))
    await page2.focus("textarea[name=why]")
    await asyncio.sleep(0.7)
    chip = await chip_for(page2, "why do you want")
    assert chip["staleReason"] == "profile" and chip["staleBlocksInsert"] is True and chip["insert"] == "never"
    await tab(page2)
    assert await value_of(page2, "textarea[name=why]") == ""
    # a draft without any statement about its basis is never read as stale
    page3, bg3 = await ctx.open("draft.html", bg=Bg(answers, research=snapshot("run1", "done")))
    info3 = await field_info(page3, "why do you want")
    plain = draft_for(info3, "Nothing is said about its basis.")
    plain["based_on"] = None
    await deliver_draft(page3, bg3, "why do you want", plain)
    await page3.focus("textarea[name=why]")
    chip = await chip_for(page3, "why do you want")
    assert "staleReason" not in chip


# ------------------------------------------------------------------------------------------------------------------------
# CPL-10: the person can record the posting from the panel, without waiting for a confirmation page


@scenario("i_applied_opens_the_record_card_without_evidence_and_save_to_tracker_sends_a_save_not_a_record")
async def panel_tracking(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await emit(page, {"type": "track-applied"})
    ask = (await ui_log(page, "askTrack"))[-1]["arg"]
    assert ask["company"] == "Example Company" and ask["title"] == "Software Engineer" and ask["evidence"] == ""
    assert not bg.msgs("track"), "opening the card records nothing"
    await emit(page, {"type": "track-record"})
    await eventually(lambda: bg.msgs("track"), 3, "the record request")
    sent = bg.msgs("track")[-1]["request"]
    assert sent["event"] == "record" and sent["evidence"] == ""
    # "Save to tracker" never claims the application was sent
    await emit(page, {"type": "track-save"})
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "save"], 3, "the save request")
    assert not [m for m in bg.msgs("track")[1:] if m["request"]["event"] == "record"]

    async def saved() -> bool:
        panel = await last_panel(page)
        return panel["job"]["tracked"] == "preparing"

    await eventually(saved, 3, "the panel knows the posting is in the tracker now")
    assert any(t["kind"] == "saved" for t in await toasts(page))
    # a server that refuses it: a calm error, never a silent menu item
    bg.overrides["track"] = lambda msg, frame: err("invalid", "Unknown event") if msg["request"]["event"] == "save" else None
    before = len(await toasts(page))
    await emit(page, {"type": "track-save"})

    async def failed() -> bool:
        return any(t["kind"] == "error" for t in (await toasts(page))[before:])

    await eventually(failed, 3, "an error toast")


# ------------------------------------------------------------------------------------------------------------------------
# CPL-13: a posting that is already recorded as sent gets a banner, and Tab inserts nothing until the person says "continue"


@scenario("a_posting_already_recorded_as_applied_shows_a_banner_and_tab_inserts_nothing_until_continue")
async def duplicate_application(ctx: Ctx) -> None:
    job = {"company": "Example Company", "title": "Software Engineer", "location": "London", "job_id": "j1", "application_id": "a1",
           "tracked": "applied", "tracked_at": "2026-10-01T09:30:00+00:00"}
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, job=job))
    panel = await last_panel(page)
    assert panel["duplicate"] == {"state": "applied", "since": "2026-10-01T09:30:00+00:00", "status": "applied"}  # (`status` is additive: UXR-08)
    assert panel["fillableCount"] == 0, "Fill verified is paused too"
    first = "input[name='job_application[first_name]']"
    await page.focus(first)
    chip = await chip_for(page, "first name")
    assert "tab" not in chip["keys"] and chip["reasonCode"] == "duplicate" and "already applied" in chip["reasonText"]
    await tab(page)
    assert await value_of(page, first) == "", "Tab never inserts into an application that was already sent"
    assert await active_name(page) == "job_application[last_name]", "Tab still moves on: it never traps the keyboard"
    await press_fill_verified(page)
    await asyncio.sleep(0.4)
    assert await value_of(page, first) == ""
    # Continue: the banner goes and Tab works again
    await emit(page, {"type": "duplicate-continue"})

    async def banner_gone() -> bool:
        return (await last_panel(page))["duplicate"] is None

    await eventually(banner_gone, 3, "the banner is acknowledged")
    await page.focus(first)
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first) == "Alex"
    # an application that is merely being prepared in Meridian is shown but never blocks
    prep = {**job, "tracked": "preparing", "tracked_at": None}
    page2, bg2 = await ctx.open("greenhouse.html", bg=Bg(GH, job=prep))
    assert (await last_panel(page2))["duplicate"] == {"state": "preparing"}
    await page2.focus(first)
    await chip_for(page2, "first name")
    await tab(page2)
    assert await value_of(page2, first) == "Alex"
    # recording the application HERE (the person said so) never raises a banner against itself
    page3, bg3 = await ctx.open("greenhouse.html", bg=Bg(GH))
    assert (await last_panel(page3))["duplicate"] is None
    await emit(page3, {"type": "track-applied"})
    await emit(page3, {"type": "track-record"})
    await eventually(lambda: bg3.msgs("track"), 3, "recorded")
    await asyncio.sleep(0.3)
    assert (await last_panel(page3))["duplicate"] is None
    await page3.focus(first)
    await chip_for(page3, "first name")
    await tab(page3)
    assert await value_of(page3, first) == "Alex"


@scenario("a_frame_of_a_page_already_applied_to_inserts_nothing_on_tab_either")
async def duplicate_in_frames(ctx: Ctx) -> None:
    job = {"company": "Example Company", "title": "Software Engineer", "location": "London", "job_id": "j1", "application_id": "a1", "tracked": "applied"}
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test")}
    page, bg = await ctx.open("frames_top.html", bg=Bg(answers, job=job), wait_frames=1)
    child = next(f for f in page.frames if f != page.main_frame)
    await child.focus("input[name=email]")
    await tab(page)
    await asyncio.sleep(0.4)
    assert await child.evaluate("document.querySelector('input[name=email]').value") == "", "the child frame follows the top frame's notice"
    await emit(page, {"type": "duplicate-continue"})
    await asyncio.sleep(0.4)
    await child.focus("input[name=email]")
    await tab(page)
    await eventually(lambda: child.evaluate("document.querySelector('input[name=email]').value === 'alex@example.test'"), 4, "the frame inserts again")


# ------------------------------------------------------------------------------------------------------------------------
# CPL-18 / FR-02: the first screen says what to do and brings the first empty question into view


@scenario("the_first_use_card_shows_until_a_field_is_touched_and_the_first_empty_question_is_brought_into_view")
async def first_use_and_guidance(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test")}
    page, bg = await ctx.open("below_fold.html", answers, viewport=(1100, 700))
    panel = await last_panel(page)
    assert panel["firstUse"] is True and panel["progress"]["total"] == 3

    async def in_view() -> bool:
        box = await rect_of(page, "input[name=first_name]")
        return 0 <= box["y"] and box["y"] + box["h"] <= 700

    await eventually(in_view, 4, "the first empty question was brought into view")
    assert await page.evaluate("document.activeElement === document.body"), "guidance never moves the focus"
    assert not [c for c in await ui_log(page, "showChip")], "and shows no chip"
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")

    async def card_gone() -> bool:
        return (await last_panel(page))["firstUse"] is False

    await eventually(card_gone, 3, "the first-use card goes once the person is on a field")
    # a person who scrolled before starting still gets the form brought into view when it lies below them ...
    page2, bg2 = await ctx.open("below_fold.html", answers, viewport=(1100, 700), activate=False)
    await page2.evaluate("window.scrollTo(0, 300)")
    await ctx.rig.activate(page2, bg2)
    await ctx.rig.ready(page2, bg2)

    async def in_view2() -> bool:
        box = await rect_of(page2, "input[name=first_name]")
        return 0 <= box["y"] and box["y"] + box["h"] <= 700

    await eventually(in_view2, 4, "the form below the person is brought into view")
    # ... but is never scrolled back up past the first question; an automatic start never scrolls
    page4, bg4 = await ctx.open("below_fold.html", answers, viewport=(1100, 700), activate=False)
    await page4.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # the very end of the page: the form is above the viewport
    where = await page4.evaluate("window.scrollY")
    assert where > 1500
    await ctx.rig.activate(page4, bg4)
    await ctx.rig.ready(page4, bg4)
    await asyncio.sleep(0.5)
    assert await page4.evaluate("window.scrollY") == where
    page3, bg3 = await ctx.open("below_fold.html", answers, viewport=(1100, 700), activate=True, ready=False, auto=True)
    await asyncio.sleep(1.0)
    assert await page3.evaluate("window.scrollY") == 0, "an automatic start does not scroll the page"


# ------------------------------------------------------------------------------------------------------------------------
# UX-01: the chip names the question, sits beside the field when it fits and stays off the question and the value just inserted


@scenario("the_chip_asks_to_sit_beside_the_field_names_the_question_and_avoids_the_label_and_the_previous_field")
async def chip_beside_and_question(ctx: Ctx) -> None:
    page, bg = await ctx.open("stacked.html", {"first name": T("Alex"), "last name": T("Newport"), "email": T("a@b.test")})
    await page.focus("input[name=last_name]")
    chip = await chip_for(page, "last name")
    assert chip["placement"] == "beside", "the form column leaves room to the right of the field"
    assert chip["questionText"] == "Last name" and chip["questionText"] == chip["fieldLabel"]
    previous = await rect_of(page, "input[name=first_name]")
    label = await page.evaluate("(() => { const r = document.querySelector('input[name=last_name]').labels[0].getBoundingClientRect(); return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}; })()")
    near = lambda a, b: all(abs(a[k] - b[k]) <= 1 for k in "xywh")  # noqa: E731
    assert any(near(a, previous) for a in chip["avoid"]), ("the value just inserted stays readable", chip["avoid"], previous)
    assert any(near(a, label) for a in chip["avoid"]), ("the question stays readable", chip["avoid"], label)
    email = await rect_of(page, "input[name=email]")
    assert near(chip["avoid"][0], {k: round(v) for k, v in email.items()}) or abs(chip["avoid"][0]["y"] - email["y"]) < 200, "the next fields still come first"
    # no room beside the field: below / above as before
    narrow, _ = await ctx.open("stacked.html", {"first name": T("Alex"), "last name": T("Newport")}, viewport=(560, 900))
    await narrow.focus("input[name=last_name]")
    chip = await chip_for(narrow, "last name")
    assert chip["placement"] in ("above", "below")


# ------------------------------------------------------------------------------------------------------------------------
# UX-02: from the panel, Esc or Alt+Shift+M gives the focus back to the form field the person was on

FAKE_PANEL = """() => {
  const host = document.createElement('div');
  host.setAttribute('data-meridian-assistant', '');
  const button = document.createElement('button');
  button.textContent = 'panel control';
  button.id = 'fake-panel-button';
  host.appendChild(button);
  document.body.appendChild(host);
  button.focus();
  return document.activeElement === button;
}"""


@scenario("esc_or_alt_shift_m_inside_the_panel_gives_the_focus_back_to_the_last_form_field")
async def leave_the_panel(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    last = 'input[name="job_application[last_name]"]'
    await page.focus(last)
    await chip_for(page, "last name")
    assert await page.evaluate(FAKE_PANEL), "the focus is inside the panel (the shortcut that brought it there is the UI's)"
    await page.keyboard.press("Escape")
    assert await active_name(page) == "job_application[last_name]", "Esc in the panel returns to the field the person was on"
    await chip_for(page, "last name")
    # Alt+Shift+M is a switch: into the panel from a field (the UI answers), back out of it from inside
    await page.evaluate("document.getElementById('fake-panel-button').focus()")
    await page.keyboard.press("Alt+Shift+KeyM")
    assert await active_name(page) == "job_application[last_name]"
    # a control of the panel that uses Esc itself keeps it (the research form cancels, the focus stays)
    await page.evaluate("""() => { const b = document.getElementById('fake-panel-button');
      b.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); } }); b.focus(); }""")
    await page.keyboard.press("Escape")
    assert await page.evaluate("document.activeElement.id") == "fake-panel-button"
    # a panel the person reached before ever touching a field: the first empty question
    page2, bg2 = await ctx.open("greenhouse.html", GH)
    assert await page2.evaluate(FAKE_PANEL)
    await page2.keyboard.press("Escape")
    assert await active_name(page2) == "job_application[first_name]"


# ------------------------------------------------------------------------------------------------------------------------
# UX-03: "Fill verified" is one batch: one Undo for all of it, and Alt+Z takes back only the field the person is on


@scenario("fill_verified_can_be_undone_as_one_batch_and_alt_z_takes_back_only_the_field_the_person_is_on")
async def fill_verified_undo(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    first, last, email = ("input[name='job_application[%s]']" % n for n in ("first_name", "last_name", "email"))
    await press_fill_verified(page)
    await wait_filled(page, first, "Alex")
    await wait_filled(page, email, "alex@example.test")
    # the batch writes the fields one after the other (the choices take a moment): the count is read once the last of them is in
    async def whole_batch() -> bool:
        return ((await last_panel(page)) or {}).get("batchUndo") == {"count": 5}

    await eventually(whole_batch, 6, "the batch holds five fields")
    panel = await last_panel(page)
    assert panel["batchUndo"] == {"count": 5}, panel["batchUndo"]  # first, last, email, country, relocation
    filled = [t for t in await toasts(page) if t["kind"] == "filled"][-1]
    assert filled["undoable"] is True and filled["params"]["count"] == 5
    # the chip of a field the batch filled offers its undo (the person's eyes are on THAT field)
    await page.focus(email)
    chip = await chip_for(page, "email")
    assert chip["undoable"] is True
    # Alt+Z: only the field the person is on
    await page.keyboard.press("Alt+KeyZ")
    await wait_filled(page, email, "")
    assert await value_of(page, first) == "Alex" and await value_of(page, last) == "Newport", "the other answers of the batch stay"
    assert (await last_panel(page))["batchUndo"] == {"count": 4}
    # "Undo all" takes back the rest
    await emit(page, {"type": "undo-all"})
    await wait_filled(page, first, "")
    assert await value_of(page, last) == "" and await value_of(page, "select[name=country]") == ""
    assert not await page.evaluate("document.querySelector('input[name=relocation][value=yes]').checked")
    assert (await last_panel(page))["batchUndo"] is None
    undone = [t for t in await toasts(page) if t["kind"] == "undone"][-1]
    assert undone["params"] == {"count": 4}
    # nothing left to take back: the keys are free again
    await page.focus(first)
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first) == "Alex", "a taken-back batch does not hold Tab hostage"


async def wait_filled(page: Any, selector: str, expected: str, timeout: float = 4.0) -> None:
    async def check() -> bool:
        return (await value_of(page, selector)) == expected

    await eventually(check, timeout, f"{selector} == {expected!r}")


# ------------------------------------------------------------------------------------------------------------------------
# UX-05: a narrow or short viewport starts with the panel as a pill


@scenario("the_panel_starts_as_a_pill_in_a_narrow_or_short_window_and_follows_the_window_until_the_person_decides")
async def panel_collapse(ctx: Ctx) -> None:
    big, _ = await ctx.open("greenhouse.html", GH, viewport=(1200, 900))
    assert (await last_panel(big))["collapsed"] is False
    narrow, _ = await ctx.open("greenhouse.html", GH, viewport=(640, 900))
    assert (await last_panel(narrow))["collapsed"] is True, "a window at 200% zoom: expanded the panel would cover most of the form"
    short, _ = await ctx.open("greenhouse.html", GH, viewport=(1200, 520))
    assert (await last_panel(short))["collapsed"] is True
    # re-expanding is the person's call and sticks: the window no longer decides
    await emit(narrow, {"type": "toggle-collapse"})
    assert (await last_panel(narrow))["collapsed"] is False
    await narrow.set_viewport_size({"width": 560, "height": 900})
    await asyncio.sleep(0.4)
    assert (await last_panel(narrow))["collapsed"] is False
    # a panel nobody touched follows the window: zoom in and it becomes a pill, zoom out and it opens again
    await big.set_viewport_size({"width": 600, "height": 800})

    async def collapsed() -> bool:
        return (await last_panel(big))["collapsed"] is True

    await eventually(collapsed, 3, "the window got narrow")
    await big.set_viewport_size({"width": 1200, "height": 900})

    async def expanded() -> bool:
        return (await last_panel(big))["collapsed"] is False

    await eventually(expanded, 3, "the window is wide again")


# ------------------------------------------------------------------------------------------------------------------------
# UX-08: the panel never claims "no writing model" before Meridian has said anything


@scenario("the_drafts_note_waits_for_the_real_generation_status")
async def generation_tri_state(ctx: Ctx) -> None:
    release = asyncio.Event()

    async def hold(msg: dict[str, Any], frame: Any) -> None:
        await release.wait()

    bg = Bg(GH)
    bg.overrides["session:open"] = hold
    page, bg = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: bg.msgs("session:open"), 4, "the session is being opened")
    await asyncio.sleep(0.3)
    panels = [c["arg"] for c in await ui_log(page, "setPanel")]
    assert panels, "the panel is up while the session opens"
    for view in panels:
        assert view["generation"]["known"] is False and view["generation"]["available"] is True, "nothing is known yet: no note"
    release.set()
    await ctx.rig.ready(page, bg)
    assert (await last_panel(page))["generation"]["known"] is True and (await last_panel(page))["generation"]["available"] is True
    # a model that really is not set up IS said, once Meridian has said so
    off = Bg(GH, generation={"available": False, "tested": False, "message": "No model"})
    page2, _ = await ctx.open("greenhouse.html", bg=off)
    view = (await last_panel(page2))["generation"]
    assert view["known"] is True and view["available"] is False


# ------------------------------------------------------------------------------------------------------------------------
# UX-09: a field that already holds the suggested value says so instead of offering Insert


@scenario("a_field_that_already_holds_the_suggested_value_is_marked_already_inserted")
async def already_inserted(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    first = "input[name='job_application[first_name]']"
    await page.focus(first)
    await chip_for(page, "first name")
    await page.keyboard.type("Alex")
    await tab(page)
    await tab(page, shift=True)
    chip = await chip_for(page, "first name")
    assert chip["alreadyInserted"] is True and "tab" not in chip["keys"] and chip["undoable"] is False
    before = len(await toasts(page))
    await emit(page, {"type": "accept", "key": chip["key"]})
    await asyncio.sleep(0.3)
    assert not [t for t in (await toasts(page))[before:] if t["kind"] == "no_suggestion"], "no misleading 'no suggestion' toast"
    # an empty field is not "already inserted"
    await page.focus("input[name='job_application[last_name]']")
    chip = await chip_for(page, "last name")
    assert "alreadyInserted" not in chip


# ------------------------------------------------------------------------------------------------------------------------
# UX-11: Alt+R / Alt+Z follow the letter on the key, whatever the layout (and the physical key when the key prints no letter)


async def alt_key(page: Any, key: str, code: str, vk: int) -> None:
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 1, "key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 1, "key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
    await asyncio.sleep(0.25)


@scenario("alt_z_and_alt_r_follow_the_printed_letter_on_any_keyboard_layout", webkit=False)
async def alt_layouts(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    first = "input[name='job_application[first_name]']"

    async def inserted() -> None:
        await page.focus(first)
        await chip_for(page, "first name")
        await tab(page)
        await wait_filled(page, first, "Alex")

    for key, code, vk, undone, what in [
        ("z", "KeyY", 90, True, "QWERTZ: the key printed Z sits where Y is on a US keyboard"),
        ("y", "KeyZ", 89, False, "QWERTZ: the key printed Y is not the undo key"),
        ("z", "KeyW", 90, True, "AZERTY: the key printed Z"),
        ("Ω", "KeyZ", 90, True, "a Mac Option+Z prints a symbol: the physical key decides"),
        ("z", "KeyZ", 90, True, "QWERTY"),
    ]:
        await inserted()
        await alt_key(page, key, code, vk)
        now = await value_of(page, first)
        assert (now == "") == undone, f"{what}: value after Alt+{key}/{code} is {now!r}"
        if not undone:
            await page.keyboard.press("Alt+KeyZ")  # clean up
            await wait_filled(page, first, "")
        else:
            await asyncio.sleep(0.1)
    # regenerate: a draft with a chip showing
    page2, bg2 = await ctx.open("draft.html", {"first name": T("Alex"), "email": T("alex@example.test")})
    info = await field_info(page2, "why do you want")
    await deliver_draft(page2, bg2, "why do you want", draft_for(info, "A draft to rewrite."))
    await page2.focus("textarea[name=why]")
    await chip_for(page2, "why do you want")
    await alt_key(page2, "p", "KeyR", 80)  # Dvorak: the physical R key prints P
    assert not bg2.msgs("regenerate")
    await alt_key(page2, "r", "KeyP", 82)  # Dvorak: the key printed R
    await eventually(lambda: bg2.msgs("regenerate"), 3, "Alt+R on the key printed R")


# ------------------------------------------------------------------------------------------------------------------------
# SEC-05 / SAFE-12: only the person's own gestures make a value a correction, and "Reuse" only where the server says the answer is theirs

HOSTILE = {"email": NEEDS(flags={"reusable": True}), "phone": NEEDS(), "visa sponsorship": SELECT("No", "no", insert="explicit", flags={"protected": True}),
           "nickname": T("Al")}


@scenario("a_value_a_script_set_is_never_offered_for_saving_and_never_saved_silently")
async def forged_edits(ctx: Ctx) -> None:
    page, bg = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE))
    # the page focuses the field, sets the value through the native setter, raises input / change and blurs: the person did nothing
    await page.evaluate("forge('email', 'attacker@evil.example')")
    await page.evaluate("forge('sponsor', 'yes')")
    await asyncio.sleep(0.9)
    assert not await asks(page), "a forged value never raises the prompt"
    assert not bg.msgs("feedback")
    # another field of the same page, typed by the person with the keyboard: asked as before
    await page.focus("#phone")
    await chip_for(page, "phone")
    await page.keyboard.type("+44 7700 900123")
    await tab(page)
    ask = (await asks(page))[-1]
    assert ask["fieldLabel"] == "Phone" and ask["valuePreview"] == "+44 7700 900123" and len(await asks(page)) == 1
    # the "always" setting saves what the person typed, never what a script set
    page2, bg2 = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE, settings={"save_corrections": "always"}))
    await page2.evaluate("forge('email', 'attacker@evil.example')")
    await page2.evaluate("forge('nickname', 'Mallory')")
    await asyncio.sleep(0.9)
    assert not bg2.msgs("feedback"), "nothing is saved silently for a value nobody typed"
    await page2.focus("#phone")
    await chip_for(page2, "phone")
    await page2.keyboard.type("+44 7700 900123")
    await tab(page2)
    await eventually(lambda: bg2.msgs("feedback"), 3, "the typed value is saved")
    assert bg2.msgs("feedback")[0]["request"]["final_value"] == "+44 7700 900123" and len(bg2.msgs("feedback")) == 1
    # a script that changes the value AFTER the person typed does not turn its value into theirs
    page3, bg3 = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE))
    await page3.focus("#email")
    await chip_for(page3, "email")
    await page3.keyboard.type("me@example.test")
    await asyncio.sleep(0.9)
    await page3.evaluate("forge('email', 'attacker@evil.example', false)")
    await page3.evaluate("document.activeElement.blur()")
    await asyncio.sleep(0.8)
    assert not await asks(page3), "the value on screen was last set by a script, not by the person"
    # what Meridian inserted, then rewritten by a script, is not an edit by the person either
    page4, bg4 = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE))
    await page4.focus("#nickname")
    await chip_for(page4, "nickname")
    await tab(page4)
    await wait_filled(page4, "#nickname", "Al")
    await page4.evaluate("forge('nickname', 'attacker')")
    await asyncio.sleep(0.8)
    assert not await asks(page4)


@scenario("reuse_is_offered_only_for_an_answer_the_server_marks_reusable_and_the_correction_names_its_field")
async def reuse_only_when_reusable(ctx: Ctx) -> None:
    answers = {"email": NEEDS(flags={"reusable": True}), "phone": NEEDS(), "nickname": NEEDS(flags={"reusable": False})}
    page, bg = await ctx.open("hostile_edit.html", bg=Bg(answers))
    for field, text, label in (("email", "me@example.test", "Email"), ("phone", "+44 7700 900123", "Phone"), ("nickname", "Sandy", "Nickname")):
        await page.focus(f"#{field}")
        await chip_for(page, label)
        await page.keyboard.type(text)
        await tab(page)
    by_label = {a["fieldLabel"]: a for a in await asks(page)}
    # one prompt at a time: answer them in turn
    seen: dict[str, bool] = {}
    for _ in range(3):
        current = [a for a in await asks(page)][-1]
        seen[current["fieldLabel"]] = current["allowReuse"]
        await emit(page, {"type": "save-never", "key": current["key"]})
        await asyncio.sleep(0.3)
    assert seen.get("Email") is True, seen
    assert seen.get("Phone") is False and seen.get("Nickname") is False, seen
    assert by_label  # (the prompts were raised at all)
    # the correction names the field it belongs to (label, section, context, group): the server keys its memory by it
    page2, bg2 = await ctx.open("hostile_edit.html", bg=Bg(answers))
    await page2.focus("#email")
    await chip_for(page2, "email")
    await page2.keyboard.type("me@example.test")
    await tab(page2)
    ask = (await asks(page2))[-1]
    await emit(page2, {"type": "save-answer", "key": ask["key"], "remember": "reuse"})
    await eventually(lambda: bg2.msgs("feedback"), 3, "feedback")
    element = bg2.msgs("feedback")[-1]["request"]["element"]
    assert bg2.msgs("feedback")[-1]["request"]["remember"] == "reuse"
    assert element["label"] == "Email" and element["name"] == "email" and element["id"] == "email"
    for key in ("section_label", "context", "group", "field_path"):
        assert key in element, key
    assert element["value"] == "", "the descriptor never carries what was typed"


# ------------------------------------------------------------------------------------------------------------------------
# SEC-01 (frames): the fields of a frame nobody can see are not assist fields

FRAME_ANSWERS = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test")}


async def frame_values(page: Any) -> dict[str, str]:
    """frame variant (the `?v=` of its URL) -> what its email / phone / website hold now."""
    out: dict[str, str] = {}
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        variant = frame.url.split("v=")[-1] if "v=" in frame.url else frame.url  # a srcdoc frame has no address of its own: "about:srcdoc"
        out[variant] = await frame.evaluate("[...document.querySelectorAll('input')].map((i) => i.value).join('|')")
    return out


@scenario("the_fields_of_an_invisible_tiny_covered_or_off_page_frame_are_never_filled_counted_or_walked_into")
async def hidden_frames(ctx: Ctx) -> None:
    page, bg = await ctx.open("hidden_frames_top.html", FRAME_ANSWERS, wait_frames=7)
    # (a) the panel counts the page's own fields and the frame a person can see: 1 + 3 + the cover note
    async def settled() -> bool:
        panel = await last_panel(page)
        return panel is not None and panel["progress"]["total"] >= 5

    await eventually(settled, 6, "the panel lists the fields")
    panel = await last_panel(page)
    labels = [i["label"] for i in panel["progress"]["items"]]
    assert panel["progress"]["total"] == 5, labels  # First name, Email, Phone, Website, Cover note
    assert panel["fillableCount"] == 4, panel["fillableCount"]
    # (b) "Fill verified" fills what is in front of the person and nothing else
    await press_fill_verified(page)

    async def seen_filled() -> bool:
        return (await frame_values(page)).get("seen") == "alex@example.test|+44 7700 900123|https://alex.example.test"

    await eventually(seen_filled, 6, "the visible frame is filled")
    await asyncio.sleep(0.6)
    values = await frame_values(page)
    for hidden in ("transparent", "tiny", "inert", "away", "covered", "about:srcdoc"):
        assert values[hidden] == "||", f"the {hidden} frame must stay empty, it holds {values[hidden]!r}"
    # (c) Tab walks from the page's own field into the frame a person can see, never into a hidden one
    page2, bg2 = await ctx.open("hidden_frames_top.html", FRAME_ANSWERS, wait_frames=7)
    await page2.focus("input[name=first_name]")
    await chip_for(page2, "first name")
    await tab(page2)  # inserts, then the hand-off goes to the visible frame
    seen = next(f for f in page2.frames if f.url.endswith("v=seen"))

    async def in_seen() -> bool:
        return await seen.evaluate("document.activeElement && document.activeElement.tagName === 'INPUT'")

    await eventually(in_seen, 4, "focus reached the visible frame")
    # leaving the visible frame past its last field never hands the focus to a frame nobody can see
    await seen.focus("input[name=website]")
    await asyncio.sleep(0.4)
    await page2.keyboard.press("Tab")
    await asyncio.sleep(0.8)
    for hidden in ("transparent", "tiny", "inert", "away", "covered"):
        frame = next(f for f in page2.frames if f.url.endswith(f"v={hidden}"))
        assert not await frame.evaluate("document.hasFocus() && document.activeElement && document.activeElement.tagName === 'INPUT'"), f"focus went into the {hidden} frame"
    # (d) a person (or a script) that puts the focus into a hidden frame gets no insertion on Tab: that frame offers nothing
    transparent = next(f for f in page2.frames if f.url.endswith("v=transparent"))
    await transparent.focus("input[name=email]")
    await asyncio.sleep(0.5)
    await page2.keyboard.press("Tab")
    await asyncio.sleep(0.6)
    assert (await frame_values(page2))["transparent"] == "||", "Tab in an invisible frame inserts nothing"


# ------------------------------------------------------------------------------------------------------------------------
# FR-03: Tab at the end of the form lists the required questions that are still empty (and never blocks)


@scenario("tab_at_the_end_of_the_form_lists_the_required_questions_still_empty_and_never_blocks_the_key")
async def end_of_form_review(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    assert (await last_panel(page))["review"] is None
    await page.focus("input[name='job_application[first_name]']")
    await chip_for(page, "first name")
    for _ in range(6):  # first, last, email, country, relocation, resume (a file: nothing approved to attach): the privacy box is the last stop
        await tab(page)
    assert await active_name(page) == "privacy"
    assert (await last_panel(page))["review"] is None, "not at the end yet"
    await tab(page)  # the end of the form: the browser's own Tab goes on to the submit button
    if ctx.rig.browser_name == "chromium":
        assert await page.evaluate("document.activeElement.tagName") == "BUTTON", "Tab was never blocked"

    async def shown() -> bool:
        review = (await last_panel(page))["review"]
        return bool(review) and review["missingRequired"] == 2

    await eventually(shown, 4, "the review lists the two required questions that are still empty")
    review = (await last_panel(page))["review"]
    assert sorted(i["label"] for i in review["items"]) == ["I agree to the privacy policy", "Resume"], review
    assert all(i["key"].startswith("top:") for i in review["items"])
    # an item focuses its field; filling the questions updates the list; dismissing closes it
    await emit(page, {"type": "focus-item", "key": review["items"][0]["key"]})
    assert await active_name(page) in ("resume", "privacy")
    await emit(page, {"type": "review-dismiss"})

    async def closed() -> bool:
        return (await last_panel(page))["review"] is None

    await eventually(closed, 3, "dismissed")
    # a form with nothing required missing shows no review
    page2, bg2 = await ctx.open("choices.html", {"full name": T("Alex")})
    await page2.focus("input[name=nickname]")
    await chip_for(page2, "nickname")
    await tab(page2)
    await asyncio.sleep(0.5)
    assert (await last_panel(page2))["review"] is None


# ------------------------------------------------------------------------------------------------------------------------
# BK2 hand-off: an answer that is OLD NEWS never replaces a draft the page already holds at a later point


@scenario("a_late_answer_for_a_draft_the_page_already_finished_never_sends_it_back_to_writing")
async def monotone_drafts(ctx: Ctx) -> None:
    release = asyncio.Event()
    answers = {"first name": T("Alex"), "email": T("alex@example.test"),
               "why do you want": STATE("GENERATING", generation={"id": "g1", "status": "GENERATING", "attempt": 1})}

    async def hold(msg: dict[str, Any], frame: Any) -> None:
        await release.wait()

    bg = Bg(answers)
    bg.overrides["suggest"] = hold
    page, bg = await ctx.open("draft.html", bg=bg, ready=False)
    await eventually(lambda: bg.suggest_requests() or bg.msgs("suggest"), 5, "the first request is on its way (held)")
    info = await eventually(lambda: field_row(page), 4, "the field is scanned")
    text = "The finished draft."
    bg.push_event({"type": "suggestion", "suggestion": draft_for(info, text)})

    async def done() -> bool:
        return (await field_info(page, "why do you want"))["state"] == "GENERATED_DRAFT"

    await eventually(done, 5, "the finished draft arrived through the poll")
    release.set()  # the held response now arrives: it saw the draft at an EARLIER point (still GENERATING)
    await ctx.rig.ready(page, bg)
    await asyncio.sleep(0.6)
    now = await field_info(page, "why do you want")
    assert now["state"] == "GENERATED_DRAFT", "the late answer must not send a finished draft back to writing"
    await page.focus("textarea[name=why]")
    chip = await chip_for(page, "why do you want")
    assert chip["state"] == "GENERATED_DRAFT" and chip["value"] == text


async def field_row(page: Any) -> dict[str, Any] | None:
    state = await debug(page)
    for f in (state or {}).get("fields", []):
        if "why do you want" in normalize(f["label"]):
            return f
    return None


# ------------------------------------------------------------------------------------------------------------------------
# D2c hand-offs: two programs answering like Meridian clear by themselves; a refused pairing start is never silent


@scenario("an_ambiguous_connection_is_checked_again_by_itself_and_a_refused_pairing_start_says_so")
async def connection_handoffs(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "unpaired", "meridianPorts": [47615, 47616]})
    page, bg = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await asyncio.sleep(0.5)
    assert (await debug(page))["sessionId"] is None
    bg.connection = {"phase": "ready"}  # the extra program quit

    async def recovered() -> bool:
        return bool((await debug(page))["sessionId"])

    await eventually(recovered, 14, "the page looked again by itself and opened its session")
    # a pairing start that fails without telling the connection (the server's rate limit): a toast, not a silent button
    bg2 = Bg(GH, connection={"phase": "unpaired", "port": 47615})
    bg2.overrides["pair:start"] = lambda msg, frame: err("rate_limited", "Too many pairing attempts")
    page2, bg2 = await ctx.open("greenhouse.html", bg=bg2, ready=False)
    await asyncio.sleep(0.4)
    await emit(page2, {"type": "connect"})

    async def said() -> bool:
        return any(t["kind"] == "error" for t in await toasts(page2))

    await eventually(said, 4, "an error toast")


@scenario("the_end_of_a_form_that_lives_in_a_frame_shows_the_review_in_the_top_frame")
async def end_of_form_in_a_frame(ctx: Ctx) -> None:
    page, bg = await ctx.open("frames_only_required.html", {"email": NEEDS(), "phone": NEEDS(), "website": NEEDS()}, wait_frames=1)
    child = next(f for f in page.frames if f != page.main_frame)
    await child.focus("input[name=email]")
    await asyncio.sleep(0.4)
    await tab(page, 2)  # email -> phone -> website: the last question of the frame (and of the page)
    assert await child.evaluate("document.activeElement.name") == "website"
    assert (await last_panel(page))["review"] is None
    await tab(page)

    async def shown() -> bool:
        review = (await last_panel(page))["review"]
        return bool(review) and review["missingRequired"] == 2

    await eventually(shown, 6, "the frame told the top frame that the form ended")
    review = (await last_panel(page))["review"]
    assert sorted(i["label"] for i in review["items"]) == ["Email", "Phone"]
    assert all(not i["key"].startswith("top:") for i in review["items"]), "the items are the frame's own fields"


# ------------------------------------------------------------------------------------------------------------------------
# CPL-10: more of the ways an ATS says "received" are recognised (and the words that merely mention a submission are not)

CONFIRMING = [
    "Submission complete. We will be in touch.",
    "Your submission has been received",
    "Su solicitud se ha enviado correctamente",
    "Hemos recibido tu CV",
    "Inscripción realizada con éxito",
]
NOT_CONFIRMING = ["Please read the submission guidelines before you apply", "Su solicitud será revisada por nuestro equipo"]


@scenario("more_ats_confirmation_wordings_offer_to_record_and_mere_mentions_of_a_submission_do_not")
async def confirmation_wordings(ctx: Ctx) -> None:
    from urllib.parse import quote

    for text in CONFIRMING + NOT_CONFIRMING:
        page, bg = await ctx.open("confirm_phrase.html#" + quote(text), {"first name": T("Alex")})
        await page.focus("input[name=first_name]")
        await chip_for(page, "first name")
        await page.click("button[type=submit]")
        if text in CONFIRMING:

            async def asked() -> bool:
                return any(c["arg"] for c in await ui_log(page, "askTrack"))

            await eventually(asked, 6, f"the record prompt for {text!r}")
        else:
            await asyncio.sleep(2.5)
            assert not any(c["arg"] for c in await ui_log(page, "askTrack")), f"{text!r} is no confirmation"
        await ctx.rig.reset()
