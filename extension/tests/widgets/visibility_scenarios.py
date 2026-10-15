"""Scenarios about WHAT THE SCANNER LETS THROUGH, as the bridge would see it, with the real content script in a real browser:

* an assist field is something a PERSON can see and click (SEC-01 / SAFE-17): covered, clipped, transparent, ghost, off-page and
  frame-hidden controls are not fields, are never filled and never sent; legitimate styled-away controls (custom checkboxes, file
  buttons, scroll boxes, fields under a cookie banner, fields far below the fold) are still fields;
* one-time-code, card, bank, ID-number, security-answer and password controls are never assist fields (SAFE-14);
* the text sent to the bridge is one line per field, bounded per field and per scan (SEC-02), and never contains what was typed
  into a rich-text editor (SEC-07);
* file inputs are named by what the page shows (CPL-17 / UX-07) and a section is titled by the nearest heading above (SAFE-05).

Registered through `widget_scenarios.scenario`; imported at the bottom of widget_scenarios.py. Pages: fixtures/visibility_*.html,
sensitive_fields.html, hostile_text.html, editor_labels.html, sections_nearest.html. The helpers `controlIsHumanVisible` and
`frameIsHumanVisible` (what the frame code imports) are also called directly through a test-only bundle (build-helpers.mjs).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from widget_rig import BOX, RADIO, Bg, T, debug, eventually, press_fill_verified, make_document
from widget_scenarios import Ctx, eq, expect, scenario
from scanner_scenarios import sent_elements

SCAN_TEXT_BUDGET = 240_000
EXTENSION = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------------------------------------------ helpers


def everything(el: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """A bridge that has a verified answer for EVERY control: whatever the scanner lets through would be filled."""
    role = el["role"]
    if role == "checkbox":
        return BOX(True)
    if role == "radio":
        option = (el.get("options") or [{"label": "Yes", "value": "y"}])[0]
        return RADIO(option["label"], option["value"])
    if role == "upload":
        return {"op": "ATTACH_FILE", "document": {k: v for k, v in DOCUMENT.items() if k != "data_b64"}}
    return T(f"FILLED-{el['id'] or el['name']}")


DOCUMENT = make_document("Alex-CV.pdf")


def everything_bg() -> Bg:
    return Bg({}, default=everything, documents={DOCUMENT["id"]: DOCUMENT})


def ids_sent(bg: Bg) -> set[str]:
    """DOM ids (or names) of every control the page has ever sent to the bridge: a control that was never sent never got a suggestion."""
    out: set[str] = set()
    for request in bg.suggest_requests():
        for element in request["elements"]:
            out.add(element["id"] or element["name"])
    return out


async def current_labels(page: Any) -> list[str]:
    return [f["label"] for f in (await debug(page))["fields"]]


async def helper_bundle(ctx: Ctx) -> Path:
    """The test-only bundle with visibility.ts and sensitive.ts, built next to the content bundle in use."""
    content = Path(ctx.rig.content_js)
    bundle = content.parent / "visibility-test.js"
    if not bundle.exists():
        build = subprocess.run(["node", str(EXTENSION / "tests/widgets/build-helpers.mjs"), str(bundle), str(content.parents[2])], capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)
    return bundle


async def with_helpers(ctx: Ctx, page: Any) -> None:
    await page.add_script_tag(path=str(await helper_bundle(ctx)))


# ------------------------------------------------------------------------------------------------------ the page patterns


HOSTILE = {
    "hp_opacity_parent": "an ancestor at opacity 0",
    "hp_covered": "an opaque layer on top of it",
    "hp_cover_out": "an opaque layer on top of it, outside any form",
    "hp_right": "far to the right of the page",
    "hp_right_vw": "one viewport to the right (left: 100vw)",
    "hp_ghost": "text, background and border all transparent",
    "hp_same_colour": "white text on a white background",
    "hp_clip_anc": "an ancestor clipped away with clip-path",
    "hp_filter": "an ancestor with filter: opacity(0)",
    "hp_wrap": "pushed out of a 10px overflow wrapper",
    "hp_fixed_below": "fixed below the viewport",
    "hp_font0": "font size 0",
    "hp_image": "a picture on top of it",
    "hp_tiny_scroller": "inside a 14px scroll box",
    "hp_pointer_none": "pointer-events: none",
    "hp_fixed_covered": "fixed, with a fixed layer on top of it",
    "hp_box_covered": "a covered checkbox without any label",
    "hp_file_covered": "a covered file input without any label",
    "hp_radio_covered": "a radio whose only label is covered",
}
LEGIT = {
    "ok_first": "a plain field", "ok_last": "a plain field", "ok_email": "a plain field",
    "ok_file_button": "a transparent file input over its own visible button",
    "ok_file_native": "a native file input with its label",
    "ok_sr_checkbox": "a visually hidden checkbox with a visible label",
    "ok_radio_a": "visually hidden radios with visible labels",
    "ok_scroll_1": "the first field of a scroll box", "ok_scroll_3": "scrolled away inside a real scroll box",
    "ok_scroll_4": "scrolled away inside a real scroll box",
    "ok_under_banner": "under a cookie banner that scrolling gets it out from under",
    "ok_far_below": "far below the fold",
}


@scenario("visibility_invisible_controls_are_never_assist_fields_never_sent_never_filled")
async def hostile_page(ctx: Ctx) -> None:
    page, bg = await ctx.open("visibility_patterns.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await ctx.rig.ready(page, bg, timeout=25)
    sent = ids_sent(bg)
    async with ctx.widget("controls a person cannot see are not assist fields and the bridge never hears of them"):
        leaked = {k: v for k, v in HOSTILE.items() if k in sent}
        eq(leaked, {}, "invisible controls that were sent to the bridge")
    async with ctx.widget("controls a person sees (including styled-away ones with a visible proxy) are still assist fields"):
        missing = {k: v for k, v in LEGIT.items() if k not in sent}
        eq(missing, {}, "visible controls that were NOT sent to the bridge")
    async with ctx.widget("'Fill verified' and the Tab walk fill what is visible and never touch what is not"):
        await press_fill_verified(page)
        await eventually(lambda: page.evaluate("document.getElementById('ok_email').value !== ''"), 8, "bulk fill ran")
        await page.focus("#ok_first")
        for _ in range(26):
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.4)
        filled = await page.evaluate(
            """(ids) => ids.filter((id) => { const el = document.getElementById(id); return el && (el.type === 'checkbox' || el.type === 'radio' ? el.checked :
              el.type === 'file' ? el.files.length > 0 : el.value !== ''); })""", list(HOSTILE))
        eq(filled, [], "invisible controls that were filled")
        expect(await page.evaluate("document.getElementById('ok_email').value") == "FILLED-ok_email", "the visible fields are filled as always")
        eq(await page.evaluate("document.getElementById('ok_file_button').files.length"), 1, "the approved CV goes to the visible upload button")


@scenario("visibility_controlIsHumanVisible_answers_for_every_control_of_the_page")
async def visible_api(ctx: Ctx) -> None:
    """The exported helper (what the frame and fill code import): the same verdicts, asked about the controls directly."""
    page, _ = await ctx.open("visibility_patterns.html", {}, activate=False)
    await with_helpers(ctx, page)
    verdicts = await page.evaluate(
        """(ids) => Object.fromEntries(ids.map((id) => [id, window.__vis.humanVisibility(document.getElementById(id))]))""", [*HOSTILE, *LEGIT, "ok_fade"])
    async with ctx.widget("hostile controls: not visible, with the reason"):
        for dom_id in HOSTILE:
            expect(not verdicts[dom_id]["visible"], f"{dom_id} ({HOSTILE[dom_id]}) counts as visible: {verdicts[dom_id]}")
    async with ctx.widget("legitimate controls: visible"):
        for dom_id in LEGIT:
            expect(verdicts[dom_id]["visible"], f"{dom_id} ({LEGIT[dom_id]}) counts as invisible: {verdicts[dom_id]}")
    async with ctx.widget("what is off screen is judged by styles and layout only (tested: geometry), what is on screen is hit-tested"):
        eq(verdicts["ok_far_below"]["tested"], "geometry", "far below")
        eq(verdicts["ok_first"]["tested"], "hit", "on screen")
        eq(verdicts["ok_scroll_3"]["tested"], "geometry", "scrolled away in a scroll box")
    async with ctx.widget("a fading-in field (opacity 0 now) is not visible yet"):
        eq(verdicts["ok_fade"]["visible"], False, "ok_fade")
        eq(verdicts["ok_fade"]["reason"], "transparent", "why")


# covers that never take a click (SEC-01): a pointer hit test falls through every one of them, a person cannot see the field
PAINT_HOSTILE = {
    "hp_pe_cover": "an opaque layer with pointer-events: none", "hp_pe_after": "an ::after layer with pointer-events: none",
    "hp_pe_img": "a picture with pointer-events: none", "hp_pe_opacity": "an opaque layer at opacity .999 with pointer-events: none",
    "hp_pe_text": "a paragraph with a background laid over it", "hp_pe_label": "its own label, opaque, laid over it",
    "hp_pe_file": "a file input under an opaque layer, its label beside it", "hp_pe_pop": "a popover (top layer) laid over it",
}
PAINT_LEGIT = {
    "ok_plain": "a plain field", "ok_pe_float": "a floating label with no background over it", "ok_pe_icon": "an icon over its edge",
    "ok_pe_outline": "a bordered outline over it (MUI notched outline)", "ok_pe_shimmer": "a 30% veil over it", "ok_pe_last": "a plain field",
}


@scenario("visibility_covers_that_take_no_click_are_never_assist_fields_never_sent_never_filled_and_what_lies_over_a_field_without_hiding_it_is_no_reason")
async def paint_covers(ctx: Ctx) -> None:
    page, bg = await ctx.open("visibility_paint.html", bg=everything_bg(), ready=False, viewport=(1280, 1500))
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await ctx.rig.ready(page, bg, timeout=25)
    sent = ids_sent(bg)
    async with ctx.widget("fields under a cover that takes no click are not assist fields and the bridge never hears of them"):
        eq({k: v for k, v in PAINT_HOSTILE.items() if k in sent}, {}, "covered controls that were sent to the bridge")
    async with ctx.widget("a floating label, an icon, an outline and a thin veil with pointer-events: none hide nothing: still assist fields"):
        eq({k: v for k, v in PAINT_LEGIT.items() if k not in sent}, {}, "visible controls that were NOT sent to the bridge")
    async with ctx.widget("'Fill verified' and the Tab walk write what is visible and attach the CV nowhere under a cover"):
        await press_fill_verified(page)
        await eventually(lambda: page.evaluate("document.getElementById('ok_pe_last').value !== ''"), 8, "bulk fill ran")
        await page.focus("#ok_plain")
        for _ in range(16):
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.4)
        filled = await page.evaluate(
            """(ids) => ids.filter((id) => { const el = document.getElementById(id); return el && (el.type === 'file' ? el.files.length > 0 : el.value !== ''); })""", list(PAINT_HOSTILE))
        eq(filled, [], "covered controls that were filled")
        expect(await page.evaluate("document.getElementById('ok_pe_float').value") == "FILLED-ok_pe_float", "the field with a floating label is filled as always")


@scenario("visibility_helpers_answer_for_covers_that_take_no_click_and_for_what_hides_nothing")
async def paint_api(ctx: Ctx) -> None:
    page, _ = await ctx.open("visibility_paint.html", {}, activate=False, viewport=(1280, 1500))
    await with_helpers(ctx, page)
    verdicts = await page.evaluate(
        """(ids) => Object.fromEntries(ids.map((id) => [id, window.__vis.humanVisibility(document.getElementById(id))]))""", [*PAINT_HOSTILE, *PAINT_LEGIT])
    async with ctx.widget("covers that take no click: not visible, and the verdict was made by a hit test"):
        for dom_id in PAINT_HOSTILE:
            expect(not verdicts[dom_id]["visible"], f"{dom_id} ({PAINT_HOSTILE[dom_id]}) counts as visible: {verdicts[dom_id]}")
    async with ctx.widget("what lies over a field without hiding it: visible"):
        for dom_id in PAINT_LEGIT:
            expect(verdicts[dom_id]["visible"], f"{dom_id} ({PAINT_LEGIT[dom_id]}) counts as invisible: {verdicts[dom_id]}")
    async with ctx.widget("the paint pass adds and removes its rule within the call: nothing is left adopted by the page"):
        eq(await page.evaluate("document.adoptedStyleSheets.length"), 0, "adopted style sheets left behind")


@scenario("visibility_a_fade_in_and_a_hidden_layer_that_goes_away_are_noticed_without_any_mutation_of_the_form")
async def fade_in(ctx: Ctx) -> None:
    page, bg = await ctx.open("visibility_patterns.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await asyncio.sleep(2.5)  # past the one settle rescan the scanner makes after a rejection: only the slow look can notice what follows
    async with ctx.widget("a field that is transparent at the first scan is not a field yet"):
        expect("fade_out" not in ids_sent(bg) and "ok_fade_out" not in ids_sent(bg), "a transparent field was sent")
    async with ctx.widget("when it fades in (a style change nobody observes) it becomes a field"):
        await page.evaluate("document.getElementById('fade_out').style.opacity = '1'")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Fades in outside'))"), 10, "the fade-in is noticed")


@scenario("visibility_hidden_layer_removed_by_style_makes_a_control_a_field_again")
async def layer_gone(ctx: Ctx) -> None:
    page, bg = await ctx.open("visibility_patterns.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await asyncio.sleep(2.5)
    expect(not any(lab.startswith("Covered outside") for lab in await current_labels(page)), "the covered field was a field")
    async with ctx.widget("the layer on top is hidden by a style change (no mutation the scanner watches): the control is looked at again"):
        await page.evaluate("document.getElementById('cover_out').style.display = 'none'")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Covered outside'))"), 10, "uncovered")


# ------------------------------------------------------------------------------------------------------ scroll and layers


@scenario("visibility_hit_test_runs_when_a_scroll_brings_a_control_on_screen_and_modals_hide_the_page")
async def scrolling(ctx: Ctx) -> None:
    page, bg = await ctx.open("visibility_scroll.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await asyncio.sleep(0.5)
    async with ctx.widget("off screen, a covered control cannot be hit-tested yet: it is judged by its styles and stays a field for now"):
        labels = await current_labels(page)
        for part in ("Near the top", "Hidden under an opaque layer", "Plainly visible below", "Gets covered later"):
            expect(any(lab.startswith(part) for lab in labels), f"{part!r} missing from {labels}")
    async with ctx.widget("scrolled on screen, the covered control is hit-tested and drops out; the plain one stays"):
        await page.evaluate("window.scrollTo(0, 1800)")
        await eventually(lambda: page.evaluate("!window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Hidden under'))"), 10, "the covered control drops out")
        expect(any(lab.startswith("Plainly visible below") for lab in await current_labels(page)), "the plain control was lost")
    async with ctx.widget("uncovered, it comes back"):
        await page.evaluate("document.getElementById('far_cover').style.display = 'none'")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Hidden under'))"), 10, "uncovered")
    async with ctx.widget("a modal dialog: the page behind it is not clickable, so it is not a field; the dialog's own field is"):
        await page.evaluate("window.scrollTo(0, 0)")
        await page.evaluate("document.getElementById('open_modal').click()")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Inside the dialog'))"), 10, "dialog field")
        labels = await current_labels(page)
        expect(not any(lab.startswith(("Near the top", "Behind layers")) for lab in labels), f"the page behind the modal is still assistable: {labels}")
        await page.evaluate("document.getElementById('dlg').close()")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Near the top'))"), 10, "page back")
    async with ctx.widget("a full-viewport veil hides what is on screen, not what is far below; hiding it brings the page back"):
        await page.evaluate("document.getElementById('open_veil').click()")
        await eventually(lambda: page.evaluate("!window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Near the top'))"), 10, "veiled")
        expect(any(lab.startswith("Plainly visible below") for lab in await current_labels(page)), "off-screen fields are judged by styles only")
        await page.evaluate("document.getElementById('veil').hidden = true")
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.some(f => f.label.startsWith('Near the top'))"), 10, "unveiled")
    del bg


# ------------------------------------------------------------------------------------------------------ frames


@scenario("visibility_frameIsHumanVisible_tells_invisible_frames_from_real_ones")
async def frames(ctx: Ctx) -> None:
    page, _ = await ctx.open("visibility_frames.html", {}, activate=False)
    await with_helpers(ctx, page)
    verdicts = await page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('iframe')].map((f) => [f.id, window.__vis.frameIsHumanVisible(f)]))""")
    async with ctx.widget("a frame a person sees (and the one far below, reachable by scrolling) is visible"):
        eq((verdicts["f_ok"], verdicts["f_far_below"]), (True, True), "visible frames")
    async with ctx.widget("transparent, click-through, tiny, hidden, undisplayed, clipped, far-right, fixed-below and covered frames are not"):
        invisible = {k: v for k, v in verdicts.items() if k not in ("f_ok", "f_far_below") and v}
        eq(invisible, {}, "invisible frames reported as visible")


# ------------------------------------------------------------------------------------------------------ sensitive fields

SENSITIVE_OK = ["ok_phone", "ok_swift", "ok_pin", "ok_essay", "ok_ssb_y", "ok_passport_have", "ok_name"]
SENSITIVE_NEVER = ["x_cc", "x_cvv", "x_exp_m", "x_otp", "x_otp2", "x_pw", "x_pw2", "x_ssn", "x_ni", "x_iban", "x_routing", "x_bank", "x_passport",
                   "x_tax", "x_dni", "x_maiden", "x_secq", "x_named", "cvv", "x_ph", "x_file_bank"]


@scenario("sensitive_one_time_code_card_bank_id_and_password_controls_are_never_assist_fields")
async def sensitive(ctx: Ctx) -> None:
    page, bg = await ctx.open("sensitive_fields.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await ctx.rig.ready(page, bg, timeout=20)
    sent = ids_sent(bg)
    names = {e["name"] for r in bg.suggest_requests() for e in r["elements"]}
    async with ctx.widget("ordinary look-alikes (Swift, PIN code, Social Security benefits, a passport question) stay assist fields"):
        for dom_id in SENSITIVE_OK:
            expect(dom_id in sent or dom_id.replace("ok_ssb_y", "social_security_benefits") in names, f"{dom_id} is not an assist field: {sorted(sent)}")
    async with ctx.widget("cards, codes, bank and ID numbers, security answers and passwords never reach the bridge"):
        eq([d for d in SENSITIVE_NEVER if d in sent], [], "sensitive controls that were sent")
        expect(not {"cc", "otp", "pw", "ssn_field", "iban", "acct", "cardNumber", "bank_details"} & names, f"sent by name: {sorted(names)}")
    async with ctx.widget("'Fill verified' leaves them to the person"):
        await press_fill_verified(page)
        await eventually(lambda: page.evaluate("document.getElementById('ok_name').value !== ''"), 8, "bulk fill ran")
        values = await page.evaluate("(ids) => ids.map((id) => { const el = document.getElementById(id); return el && el.type === 'file' ? el.files.length : el.value; })", SENSITIVE_NEVER)
        eq([v for v in values if v not in ("", 0)], [], "values written into sensitive controls")


# ------------------------------------------------------------------------------------------------------ what leaves the page


@scenario("hostile_text_is_one_bounded_line_per_field_and_a_scan_has_a_budget")
async def bounded_text(ctx: Ctx) -> None:
    page, bg = await ctx.open("hostile_text.html", bg=everything_bg(), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 20, "first scan")
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.length >= 80"), 20, "every field scanned")
    await asyncio.sleep(1.0)
    requests = bg.suggest_requests()
    last: dict[int, dict[str, Any]] = {}
    for request in requests:
        for element in request["elements"]:
            last[element["node_id"]] = element
    elements = list(last.values())
    texts = ("label", "context", "description", "group", "section_label", "placeholder")
    async with ctx.widget("a 20,000-character whitespace run in a legend is one space; nothing longer than its cap leaves the page"):
        by_name = {e["name"]: e for e in elements}
        eq(by_name["r_redos"]["context"], "a x", "radio group question")
        eq(by_name["b_redos"]["context"], "a x", "checkbox group question")
        for e in elements:
            for key in texts:
                expect(not re.search(r"\s{2,}", e[key]), f"{key} of {e['name']} has a whitespace run")
            expect(len(e["label"]) <= 2000 and len(e["context"]) <= 4000 and len(e["description"]) <= 3000, f"{e['name']} is over its caps")
    async with ctx.widget("a long paragraph keeps its beginning and its end (the actual question)"):
        consent = by_name["consent"]
        expect(consent["context"].startswith("Lorem ipsum") and consent["context"].endswith("DO YOU CONSENT TO BACKGROUND CHECKS?"), consent["context"][:60] + "…" + consent["context"][-60:])
    async with ctx.widget("a scan hands out a bounded amount of text: the rest of the fields are sent lean, none is dropped"):
        total = sum(sum(len(e[k]) for k in texts) + sum(len(o["label"]) + len(o["value"]) for o in e["options"]) for e in elements)
        expect(total <= SCAN_TEXT_BUDGET + 60_000, f"{total} characters of text in one scan")
        expect(sum(1 for e in elements if e["name"].startswith("many_")) == 80, "a field was dropped")
        expect(all(len(e["label"]) > 0 for e in elements if e["name"].startswith("many_")), "a lean field lost its label")
        expect(any(e["description"] == "" for e in elements if e["name"].startswith("many_")), "no field was sent lean: the budget never bit")
        expect(any(len(e["description"]) > 1000 for e in elements if e["name"].startswith("many_")), "the first fields keep their descriptions")
        eq(by_name["full_name"]["label"], "Full name", "an ordinary field keeps its whole label")
        for request in requests:
            expect(len(json.dumps(request)) < 900_000, "a request nears the bridge's 1 MiB limit")


EDITORS = {"#bio": "MY_PRIVATE_BIO_TEXT", "#info": "MY_PRIVATE_INFO_TEXT", "#cap_editor": "MY_PRIVATE_CAPTION_TEXT", "#why": "MY_PRIVATE_WHY_TEXT"}
SET_EDITORS = """(editors) => { for (const [sel, text] of Object.entries(editors)) { const el = document.querySelector(sel); el.textContent = text;
  el.dispatchEvent(new InputEvent('input', {bubbles: true})); } }"""


@scenario("editor_text_typed_before_or_after_activation_never_reaches_a_label_a_context_or_any_message")
async def editor_text(ctx: Ctx) -> None:
    for phase in ("before", "after"):
        bg = Bg({})
        page, bg = await ctx.open("editor_labels.html", bg=bg, activate=(phase == "after"), ready=False)
        if phase == "before":
            await page.evaluate(SET_EDITORS, EDITORS)
            await ctx.rig.activate(page, bg)
        await eventually(lambda: page.evaluate("window.__meridianAssist && window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
        await asyncio.sleep(0.4)
        quiet = len(bg.suggest_requests())
        if phase == "after":
            await page.evaluate(SET_EDITORS, EDITORS)
            await asyncio.sleep(1.6)  # the page changed under the questions: whatever the scanner does next must not carry the text
        await page.focus("#linkedin")
        await asyncio.sleep(0.4)
        async with ctx.widget(f"editor text typed {phase} the first scan: no message carries it, and the questions keep their names"):
            dump = json.dumps(bg.log).lower()
            for marker in ("my_private", "private_bio", "private_info"):
                expect(marker not in dump, f"{marker!r} was sent to the background")
            sent = sent_elements(bg)
            eq(sent["linkedin"]["label"], "LinkedIn Profile", "label of the field next to the editor")
            expect("Phone" in sent["phone"]["label"], f"phone label: {sent['phone']['label']!r}")
            expect("Cover" in sent["cover"]["label"] and "letter" in sent["cover"]["label"], f"label through aria-labelledby: {sent['cover']['label']!r}")
            eq(sent["site"]["label"], "Website", "the other question")
        del quiet


# ------------------------------------------------------------------------------------------------------ names and sections


@scenario("file_inputs_are_named_by_their_visible_label_in_the_panel_and_for_the_bridge")
async def file_labels(ctx: Ctx) -> None:
    for path, wanted in (("greenhouse.html", "Resume"), ("lever.html", "Resume/CV"), ("icims_widgets.html", "Resume")):
        page, bg = await ctx.open(path, bg=Bg({}), ready=False)
        await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
        await asyncio.sleep(0.4)
        async with ctx.widget(f"{path}: the CV field is called {wanted!r}, not by its name and id"):
            fields = (await debug(page))["fields"]
            files = [f for f in fields if f["kind"] == "file"]
            expect(files, f"no file field among {[f['label'] for f in fields]}")
            expect(any(f["label"] == wanted for f in files), f"panel labels: {[f['label'] for f in files]}")
            sent = [e for r in bg.suggest_requests() for e in r["elements"] if e["role"] == "upload"]
            expect(any(e["label"] == wanted and (e["name"] or e["id"]) for e in sent), f"sent: {[(e['label'], e['name'], e['id']) for e in sent]}")


@scenario("sections_are_titled_by_the_nearest_heading_above_the_field_as_the_bridge_is_told")
async def sections(ctx: Ctx) -> None:
    page, bg = await ctx.open("sections_nearest.html", bg=Bg({}), ready=False)
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().scan.scans >= 1"), 15, "first scan")
    await asyncio.sleep(0.4)
    sent = sent_elements(bg)
    async with ctx.widget("Name / Phone / Email of three blocks of one <section> are titled by their own block, not by the first heading"):
        eq({k: sent[k]["section_label"] for k in ("a_name", "a_phone")}, {"a_name": "Personal information", "a_phone": "Personal information"}, "first block")
        eq({k: sent[k]["section_label"] for k in ("b_name", "b_phone")}, {"b_name": "Emergency contact", "b_phone": "Emergency contact"}, "second block")
        eq({k: sent[k]["section_label"] for k in ("c_name", "c_email")}, {"c_name": "Referee", "c_email": "Referee"}, "third block")


@scenario("page_report_json_ld_is_bounded_in_total_and_typed_editor_text_is_not_description")
async def page_report_limits(ctx: Ctx) -> None:
    page, bg = await ctx.open("jsonld_flood.html", bg=Bg({}), ready=False)
    await eventually(lambda: bool(bg.msgs("session:open")), 15, "the session opens")
    report = bg.msgs("session:open")[0]["report"]
    async with ctx.widget("eight huge JSON-LD scripts are cut to a total that fits one request"):
        total = sum(len(text) for text in report["jsonld"])
        expect(0 < total <= 400_000, f"{total} characters of JSON-LD ({len(report['jsonld'])} scripts)")
        expect(len(json.dumps(report)) < 900_000, "the page report nears the bridge's 1 MiB request limit")
    async with ctx.widget("text typed into a (plaintext-only) editor is never part of the description the page report sends"):
        expect("robots" in report["description_text"], report["description_text"][:80])
        expect("MY_PRIVATE" not in json.dumps(report), "typed editor text in the page report")


@scenario("scanner_stillVisible_is_a_fresh_and_strict_look_right_before_a_field_is_written")
async def still_visible(ctx: Ctx) -> None:
    """A control the person has been seen to see stays in the walk while a popup covers it (the next field of a date picker), but the check
    made right before a value is WRITTEN is strict: a cover on top of it now means no."""
    page, _ = await ctx.open("visibility_patterns.html", {}, activate=False)
    await with_helpers(ctx, page)
    first = await page.evaluate(
        """() => { const roots = new window.__scan.RootRegistry();
          const scanner = new window.__scan.Scanner({ frameKey: 'top', isActive: () => true, onResult: () => {}, roots });
          window.__scanner = scanner; scanner.start(); scanner.scanNow();
          const names = () => scanner.list().map((f) => f.element.id || f.element.name);
          window.__names = names;
          const field = (id) => scanner.list().find((f) => (f.element.id || f.element.name) === id);
          return { names: names(), still: field('ok_last') ? scanner.stillVisible(field('ok_last')) : null }; }""")
    async with ctx.widget("a visible field is a field and the fresh check agrees; a covered one that was never seen is no field"):
        expect("ok_last" in first["names"] and "hp_covered" not in first["names"], f"scan: {first['names']}")
        eq(first["still"], True, "stillVisible of a visible field")
    after = await page.evaluate(
        """() => { const el = document.getElementById('ok_last'); const r = el.getBoundingClientRect();
          const cover = document.createElement('div'); cover.id = 'popup'; cover.setAttribute('role', 'listbox');
          cover.style.cssText = `position:absolute;background:#fff;border:1px solid #888;z-index:40;left:${r.left + scrollX - 4}px;top:${r.top + scrollY - 4}px;width:${r.width + 8}px;height:${r.height + 8}px`;
          document.body.append(cover);
          const field = window.__scanner.list().find((f) => f.element.id === 'ok_last');
          const strict = window.__scanner.stillVisible(field);
          window.__scanner.scanNow();
          return { strict, kept: window.__names().includes('ok_last') }; }""")
    async with ctx.widget("a popup covers it: the walk keeps the seen field, the fresh check says no"):
        eq(after, {"strict": False, "kept": True}, "strict check / still in the walk")
    gone = await page.evaluate(
        """() => { document.getElementById('popup').remove(); const field = window.__scanner.list().find((f) => f.element.id === 'ok_last');
          return window.__scanner.stillVisible(field); }""")
    async with ctx.widget("the popup closes: yes again"):
        eq(gone, True, "stillVisible after the popup closed")
    async with ctx.widget("a field hidden from the start is no field however long it stays covered, and a seen field turned invisible by its own styles goes"):
        res = await page.evaluate(
            """() => { document.getElementById('ok_email').style.opacity = '0'; window.__scanner.scanNow();
              return { names: window.__names(), covered: window.__names().includes('hp_covered') }; }""")
        expect("ok_email" not in res["names"] and not res["covered"], f"names: {res['names']}")


@scenario("page_report_description_never_contains_what_was_typed_into_an_editor_even_without_employer_markup")
async def report_fallback(ctx: Ctx) -> None:
    page, bg = await ctx.open("report_fallback.html", bg=Bg({}), ready=False)
    await eventually(lambda: bool(bg.msgs("session:open")), 15, "the session opens")
    report = bg.msgs("session:open")[0]["report"]
    async with ctx.widget("the fallback description (no employer markup) is a block without any editor, and nothing typed is in the report"):
        expect("robots" in report["description_text"], report["description_text"][:80])
        expect("MY_PRIVATE" not in json.dumps(report), "typed editor text in the page report")
