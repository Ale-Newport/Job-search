"""Wave-6 scenarios of the content-script rig: the security findings of the final review of the content layer.

  HIGH-1  a field hidden under a cover that never takes a click (`pointer-events: none`: a div, an ::after, an <img>, an opacity .999 layer,
          text, the field's own label) is no assist field, and a cover that comes after the scan stops every write (Tab, Enter, Replace,
          "Fill verified", the approved CV); a page-created popover opened after the assistant's own UI hides its chip and panel, so the UI
          takes the top of the top layer back first, and nothing is written while it cannot
  LOW-1   a script's `document.execCommand("insertText")` raises TRUSTED input events: it is not the person's correction
  LOW-2   what a person types into a tag / creatable select or into a div that echoes keys never reaches the bridge

Same rules as scenarios.py: REAL key presses, the view models handed to the UI and the messages the content script sent. Every scenario
here fails without its fix (the report names the mutation that proves it).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness import FILE, T, Bg, chip_for, debug, emit, eventually, last_chip, make_document, press_fill_verified, tab, value_of
from scenarios import Ctx, doc_ref, scenario
from scenarios_wave3 import HOSTILE, asks

# ------------------------------------------------------------------------------------------------------------------------
# HIGH-1: covers that never take a click

COVERED = ["c_div", "c_after", "c_img", "c_opacity", "c_text", "c_label", "c_phone", "c_cv", "c_cvl"]
SEEN = ["ok_first", "ok_float", "ok_icon", "ok_outline", "ok_veil", "ok_last"]


def cover_answers(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "first name": T("Alex"), "last name": T("Newport"), "hotel floating email": T("hotel@example.test"), "india icon phone": T("+44 7700 900123"),
        "juliet outlined city": T("London"), "kilo veiled surname": T("Veil"), "resume": FILE(doc_ref(doc)),
    }
    for label in ("alpha email", "bravo email", "charlie email", "delta email", "echo email", "foxtrot email", "golf phone"):
        out[label] = T(f"PWNED-{label.split()[0]}")
    return out


def sent_ids(bg: Bg) -> set[str]:
    return {e["id"] for r in bg.suggest_requests() for e in r["elements"]}


@scenario("a_field_under_a_cover_that_takes_no_click_is_no_assist_field_and_is_never_sent_filled_or_attached")
async def pointer_events_none_covers(ctx: Ctx) -> None:
    doc = make_document()
    answers = cover_answers(doc)
    page, bg = await ctx.open("paint_covers.html", answers, bg=Bg(answers, documents={"doc-1": doc}), viewport=(1200, 1500))
    sent = sent_ids(bg)
    assert not sent & set(COVERED), f"covered controls were sent to the bridge: {sorted(sent & set(COVERED))}"
    assert set(SEEN) <= sent, f"things with pointer-events: none that hide nothing must stay fields: missing {sorted(set(SEEN) - sent)}"
    # "Fill verified": what is seen is filled, what is not is left alone
    await press_fill_verified(page)
    await eventually(lambda: page.evaluate("document.getElementById('ok_last').value === 'Newport'"), 5, "the bulk fill ran")
    assert await page.evaluate("[...document.querySelectorAll('input')].filter((i) => i.id.startsWith('c_') && i.type !== 'file').map((i) => i.value).join('')") == ""
    assert await page.evaluate("document.getElementById('c_cv').files.length + document.getElementById('c_cvl').files.length") == 0, "the approved CV is never attached under a cover"
    # the Tab walk of a fresh page
    page2, bg2 = await ctx.open("paint_covers.html", answers, bg=Bg(answers, documents={"doc-1": doc}), viewport=(1200, 1500))
    await page2.focus("#ok_first")
    await chip_for(page2, "first name")
    for _ in range(len(SEEN) + len(COVERED)):
        await tab(page2)
        await asyncio.sleep(0.12)
    await asyncio.sleep(0.5)
    values = await page2.evaluate("Object.fromEntries([...document.querySelectorAll('input')].map((i) => [i.id, i.type === 'file' ? i.files.length : i.value]))")
    assert {k: v for k, v in values.items() if k in COVERED and v not in ("", 0)} == {}, f"written under a cover: {values}"
    assert values["ok_float"] == "hotel@example.test" and values["ok_icon"] == "+44 7700 900123" and values["ok_outline"] == "London", values


# ------------------------------------------------------------------------------------------------------------------------
# HIGH-1: a cover that comes after the scan, and the second gate of a file and of a Tab


LATE = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123")}


@scenario("a_cover_that_takes_no_click_and_comes_after_the_scan_stops_tab_enter_replace_and_fill_verified_until_it_goes")
async def late_cover(ctx: Ctx) -> None:
    for how, field in (("div", "email"), ("p", "first"), ("popover", "phone"), ("strong", "email")):
        page, bg = await ctx.open("late_cover.html", LATE)
        await page.focus(f"#{field}")
        chip = await chip_for(page, {"email": "email", "first": "first name", "phone": "phone"}[field])
        await page.evaluate(f"coverLater('{field}', '{how}')")
        await tab(page)  # at once: the scanner has not looked again yet, the field is still in the walk
        assert await value_of(page, f"#{field}") == "", f"Tab wrote under a {how} cover that takes no click"
        await emit(page, {"type": "accept", "key": chip["key"]})
        await emit(page, {"type": "replace", "key": chip["key"]})
        await press_fill_verified(page)
        await asyncio.sleep(1.4)  # the scanner looks again (the field was seen, so it stays in the walk): the write is the guard
        assert await value_of(page, f"#{field}") == "", f"a click or the bulk fill wrote under a {how} cover that takes no click"
        # the cover goes: the very same field is written again
        await page.evaluate(f"uncover('{field}')")
        await asyncio.sleep(0.6)
        await page.focus(f"#{field}")
        await chip_for(page, {"email": "email", "first": "first name", "phone": "phone"}[field])
        await tab(page)
        assert await value_of(page, f"#{field}") != "", f"nothing was written once the {how} cover went"


@scenario("the_chip_does_not_stand_next_to_a_field_a_cover_that_takes_no_click_has_hidden_since_the_scan")
async def chip_time(ctx: Ctx) -> None:
    page, bg = await ctx.open("late_cover.html", LATE)
    await page.focus("#first")
    await chip_for(page, "first name")
    await page.evaluate("coverLater('email', 'div')")
    await page.focus("#email")
    await asyncio.sleep(0.8)
    assert await last_chip(page) is None, "the chip offers to write into a field nobody can see"
    await page.evaluate("uncover('email')")
    await asyncio.sleep(0.9)
    await page.focus("#first")
    await page.focus("#email")
    await chip_for(page, "email")


@scenario("the_approved_cv_is_not_attached_under_a_cover_that_takes_no_click_and_is_attached_once_it_goes")
async def cv_under_cover(ctx: Ctx) -> None:
    doc = make_document()
    answers = {**LATE, "resume": FILE(doc_ref(doc))}
    page, bg = await ctx.open("late_cover.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    await page.focus("#cv")
    chip = await chip_for(page, "resume")
    await page.evaluate("coverLater('cv', 'div')")
    await tab(page)
    await asyncio.sleep(0.8)
    assert await page.evaluate("document.getElementById('cv').files.length") == 0, "the CV went into a file input under a cover"
    await emit(page, {"type": "accept", "key": chip["key"]})
    await press_fill_verified(page)
    await asyncio.sleep(0.8)
    assert await page.evaluate("document.getElementById('cv').files.length") == 0, "the CV went in through a click or the bulk fill"
    await page.evaluate("uncover('cv')")
    await asyncio.sleep(0.6)
    await page.focus("#cv")
    await chip_for(page, "resume")
    await tab(page)
    await eventually(lambda: page.evaluate("document.getElementById('cv').files.length === 1"), 5, "attached once nothing covers it")


@scenario("a_file_input_below_the_fold_under_a_cover_that_takes_no_click_is_never_attached_by_fill_verified")
async def cv_below_the_fold(ctx: Ctx) -> None:
    doc = make_document()
    answers = {**LATE, "resume": FILE(doc_ref(doc))}
    page, bg = await ctx.open("fold_cover.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    # off screen a cover cannot be seen by a hit test: the field is judged by its styles and stays in the walk ...
    assert any(f["kind"] == "file" for f in (await debug(page))["fields"]), "the file input is judged by its styles while it is off screen"
    await press_fill_verified(page)
    await eventually(lambda: page.evaluate("document.getElementById('email').value !== ''"), 5, "the bulk fill ran")
    await asyncio.sleep(0.6)
    # ... but the approved CV is attached only to a file input that was hit-tested on screen at the moment of the write
    assert await page.evaluate("document.getElementById('cv').files.length") == 0, "the CV went into a file input nobody could see"
    # scrolled to, the cover is seen by a hit test and the control is no field
    await page.evaluate("document.getElementById('cv').scrollIntoView({block: 'center'})")
    await eventually(lambda: page.evaluate("!window.__meridianAssist.debug().fields.some((f) => f.kind === 'file')"), 6, "the covered file input drops out when it is on screen")


# ------------------------------------------------------------------------------------------------------------------------
# HIGH-1: a layer of the page in the top layer, opened after the assistant's own UI


POPOVER_OVER_UI = """() => {
  // opened by the page AFTER the assistant's UI: the right part of the window, where the panel lives and the chip may stand; the fields are on the left
  const v = document.createElement('div');
  v.id = 'veil'; v.setAttribute('popover', 'manual');
  v.style.cssText = 'position:fixed;inset:0 0 0 600px;margin:0;border:0;padding:0;background:#fff;pointer-events:none;width:auto;height:auto';
  document.body.append(v); v.showPopover();
}"""

# what sits first at the middle of the chip and of the panel (pointer-events forced on, as a person's eye would see it)
TOP_AT_SURFACES = """() => {
  const host = document.querySelector('[data-meridian-assistant]'); const sh = host.shadowRoot;
  const sheet = new CSSStyleSheet(); sheet.replaceSync('@layer probe { *, *::before, *::after { pointer-events: auto !important; } }');
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]; sh.adoptedStyleSheets = [...sh.adoptedStyleSheets, sheet];
  try {
    return [...sh.querySelectorAll('.chip, .panel')].filter((n) => n.getBoundingClientRect().width > 1).map((n) => {
      const r = n.getBoundingClientRect(); const top = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2)[0];
      return [n.classList.contains('panel') ? 'panel' : 'chip', top === host ? 'ui' : (top ? top.tagName + '#' + top.id : 'none')];
    });
  } finally { document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== sheet); sh.adoptedStyleSheets = sh.adoptedStyleSheets.filter((s) => s !== sheet); }
}"""


@scenario("a_popover_the_page_opens_after_the_assistants_ui_does_not_hide_its_chip_while_tab_writes", webkit=False)
async def popover_over_the_ui(ctx: Ctx) -> None:
    page, bg = await ctx.open("late_cover.html", LATE, headless_ui=False, viewport=(1280, 900))
    await page.focus("#first")
    await page.locator("[data-testid=chip]").first.wait_for(state="visible", timeout=6000)
    await asyncio.sleep(0.4)
    await page.evaluate(POPOVER_OVER_UI)
    premise = await page.evaluate(TOP_AT_SURFACES)
    assert ["panel", "DIV#veil"] in premise, f"the premise: the page's popover is above the panel ({premise})"
    await tab(page)
    assert await value_of(page, "#first") == "Alex", "the field the person can see is written"
    await asyncio.sleep(0.5)
    tops = await page.evaluate(TOP_AT_SURFACES)
    assert any(t[0] == "panel" for t in tops) and all(t[1] == "ui" for t in tops), f"the chip and the panel are on top again before the write: {tops}"


@scenario("nothing_is_written_while_the_assistants_chip_cannot_be_put_back_on_top_of_a_popover_the_page_opened", webkit=False)
async def no_write_under_a_popover(ctx: Ctx) -> None:
    page, bg = await ctx.open("late_cover.html", LATE, headless_ui=False, viewport=(1280, 900))
    await page.focus("#first")
    await page.locator("[data-testid=chip]").first.wait_for(state="visible", timeout=6000)
    await asyncio.sleep(0.4)
    await page.evaluate(POPOVER_OVER_UI)
    # this page can stop the UI from taking its place again (in a content script's own world it could not: the rig runs in the page's world)
    await page.evaluate("() => { const h = document.querySelector('[data-meridian-assistant]'); h.showPopover = () => { throw new Error('no'); }; h.hidePopover = () => {}; }")
    assert ["panel", "DIV#veil"] in await page.evaluate(TOP_AT_SURFACES), "the premise: the page's popover is above the panel"
    await tab(page)
    await asyncio.sleep(0.5)
    assert await value_of(page, "#first") == "", "the answer went in while the chip that offers it was hidden under the page's layer"


# ------------------------------------------------------------------------------------------------------------------------
# LOW-1: execCommand makes trusted input events

FORGE_INSERT = """([id, value]) => { const el = document.getElementById(id); el.focus(); document.execCommand('insertText', false, value); el.blur(); }"""
WATCH_EVENTS = "window.__ev = []; for (const t of ['beforeinput', 'input', 'change']) document.addEventListener(t, (e) => window.__ev.push([t, e.isTrusted, e.target.id]), true);"


@scenario("a_value_a_script_inserts_with_exec_command_is_never_offered_for_saving_and_never_saved_silently")
async def exec_command_edits(ctx: Ctx) -> None:
    for mode in ("ask", "always"):
        page, bg = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE, settings={"save_corrections": mode}))
        await page.evaluate(WATCH_EVENTS)
        await page.evaluate(FORGE_INSERT, ["email", "attacker@evil.example"])
        await page.evaluate(FORGE_INSERT, ["nickname", "Mallory"])
        await asyncio.sleep(0.9)
        seen = await page.evaluate("window.__ev")
        assert ["input", True, "email"] in seen, f"the premise: the page's own insertText raises a TRUSTED input event ({seen})"
        assert not await asks(page), f"[{mode}] a value the page typed raised the save prompt"
        assert not bg.msgs("feedback"), f"[{mode}] a value the page typed was saved"
        # what the person types with the keyboard, in the same page, is theirs
        await page.focus("#phone")
        await chip_for(page, "phone")
        await page.keyboard.type("+44 7700 900123")
        await tab(page)
        if mode == "ask":
            ask = (await asks(page))[-1]
            assert ask["fieldLabel"] == "Phone" and ask["valuePreview"] == "+44 7700 900123"
        else:
            await eventually(lambda: bg.msgs("feedback"), 3, "the typed value is saved")
            assert [m["request"]["final_value"] for m in bg.msgs("feedback")] == ["+44 7700 900123"]


@scenario("text_that_arrives_with_no_key_pointer_paste_drop_or_composition_before_it_is_not_a_correction_and_one_after_a_pointer_is")
async def origin_needed(ctx: Ctx) -> None:
    page, bg = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE, settings={"save_corrections": "always"}))
    await page.focus("#phone")
    await chip_for(page, "phone")
    await page.keyboard.insert_text("+44 7700 900123")  # a trusted input event with no key, pointer, paste, drop or composition before it (as execCommand makes)
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.8)
    assert not bg.msgs("feedback"), "text that arrived with no key, pointer, paste, drop or composition before it is not taken as the person's"
    page2, bg2 = await ctx.open("hostile_edit.html", bg=Bg(HOSTILE, settings={"save_corrections": "always"}))
    await page2.focus("#phone")
    await chip_for(page2, "phone")
    await page2.click("#phone")  # a trusted pointer on the field, then the text arrives (an IME commit, a dictation after the click)
    await page2.keyboard.insert_text("+44 7700 900124")
    await page2.keyboard.press("Tab")
    await eventually(lambda: bg2.msgs("feedback"), 3, "a pointer, then the text: theirs")


# ------------------------------------------------------------------------------------------------------------------------
# LOW-2: typed choices stay on the page

SENTINELS = ["ZQ77SENTTAGONE", "ZQ78SENTCITYTWO", "ZQ79SENTTEAMTHREE"]


@scenario("text_typed_into_a_tag_select_or_a_creatable_combobox_never_reaches_the_bridge_while_the_pages_own_choices_do")
async def typed_choices(ctx: Ctx) -> None:
    page, bg = await ctx.open("typed_choices.html", {}, ready=False)
    await eventually(lambda: bg.suggest_requests(), 8, "the first scan")
    await asyncio.sleep(0.5)
    await page.click("#tagin")
    await page.keyboard.type(SENTINELS[0])
    await page.keyboard.press("Enter")
    await page.click("#cb")
    await page.keyboard.type(SENTINELS[1])
    await page.click("#cr")
    await page.keyboard.type(SENTINELS[2])
    await page.focus("#country")
    await page.keyboard.press("u")  # the page then fills its region list (its own, unselected options)
    await page.click("#other")
    await page.evaluate("() => { const d = document.createElement('div'); d.innerHTML = '<label for=late>Late</label><input id=late>'; document.querySelector('form').appendChild(d); }")
    await asyncio.sleep(3.0)
    blob = json.dumps([e["msg"] for e in bg.log]).lower()
    for sentinel in SENTINELS:
        assert sentinel.lower() not in blob, f"{sentinel} was sent to the background"
    last: dict[str, dict[str, Any]] = {}
    for request in bg.suggest_requests():
        for element in request["elements"]:
            last[element["id"]] = element
    skills = last["skills"]
    assert [o["label"] for o in skills["options"]] == ["Python", "Go"] and skills["selected_values"] == ["py"] and skills["has_value"] is True, skills
    for field in ("cb", "cr"):
        assert last[field]["selected_text"] == "" and last[field]["has_value"] is True, (field, last[field])
    assert last["lb"]["selected_text"] == "Beta" and last["lb"]["has_value"] is True, "a choice the page's own list offers is still sent"
    assert {o["label"] for o in last["state"]["options"]} >= {"California", "Texas"}, "options a page adds unselected after the first scan are its own"
