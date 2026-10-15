"""Scenarios about what the SCANNER (src/content/scan.ts, page-report.ts and backend/jobagent/automation/observation.js, bundled verbatim)
makes of widget markup, as the bridge would see it: which controls are assist fields, what they are called, and what is (and is NOT) sent.

Registered through `widget_scenarios.scenario`; imported at the bottom of widget_scenarios.py. Pages: fixtures/scanner_patterns.html and
fixtures/job_header.html (hand-written markup of widget families and of their decoys; the scenarios say what each block stands for).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from widget_rig import COMBO, RADIO, Bg, debug, eventually
from widget_scenarios import Ctx, eq, expect, fill_with_tab, scenario, state_of

REPO = Path(__file__).resolve().parents[3]
# MERIDIAN_OBSERVATION_JS: the mutation harness points the observer-level scenarios at a mutated copy
OBSERVATION_JS = Path(os.environ.get("MERIDIAN_OBSERVATION_JS") or REPO / "backend" / "jobagent" / "automation" / "observation.js")


# ------------------------------------------------------------------------------------------------------ helpers


def sent_elements(bg: Bg) -> dict[str, dict[str, Any]]:
    """The LAST ElementIn the page sent for every control, by DOM id (or name when it has no id)."""
    out: dict[str, dict[str, Any]] = {}
    for request in bg.suggest_requests():
        for element in request["elements"]:
            out[element["id"] or element["name"] or f"#{element['node_id']}"] = element
    return out


async def labels_of(page: Any) -> list[str]:
    return [f["label"] for f in (await debug(page))["fields"]]


def has_label(labels: list[str], part: str) -> bool:
    return any(part.lower() in label.lower() for label in labels)


# ------------------------------------------------------------------------------------------------------ dropdown buttons


@scenario("scanner_button_dropdowns_are_fields_and_menu_nav_submit_buttons_are_not")
async def button_dropdowns(ctx: Ctx) -> None:
    page, bg = await ctx.open("scanner_patterns.html", {"favourite fruit": COMBO("Banana", "Banana")})
    labels = await labels_of(page)
    sent = sent_elements(bg)
    async with ctx.widget("<button role=combobox> (Radix Select) is a combobox field named by its <label>, its placeholder is no selection"):
        expect("Favourite fruit" in labels, f"missing from {labels}")
        element = sent["rx_fruit"]
        eq((element["role"], element["tag"], element["selected_text"], element["has_value"]), ("combobox", "button", "", False), "what the bridge is told")
    async with ctx.widget("a Radix trigger with the primitive's default TEXT icon (\"Select a size…▼\", data-placeholder on the trigger) is still empty"):
        element = sent["rx_size"]
        eq((element["label"], element["selected_text"], element["has_value"]), ("T-shirt size", "", False), "what the bridge is told")
    async with ctx.widget("a trigger that marks itself data-placeholder is empty whatever it shows (\"Any size▼\")"):
        element = sent["rx_any"]
        eq((element["label"], element["selected_text"], element["has_value"]), ("Preferred size", "", False), "what the bridge is told")
    async with ctx.widget("a chevron drawn as aria-hidden text is not part of the displayed placeholder (\"Select a colour…▼\")"):
        element = sent["rx_icon"]
        eq((element["label"], element["selected_text"], element["has_value"]), ("Favourite colour", "", False), "what the bridge is told")
    async with ctx.widget("<button aria-haspopup=listbox aria-labelledby=\"label self\"> (Headless UI Listbox) is named by the label alone, not by the value it shows"):
        expect("Preferred work mode" in labels, f"missing from {labels}")
        element = sent["lb_mode"]
        eq((element["label"], element["role"], element["selected_text"], element["has_value"]), ("Preferred work mode", "combobox", "", False), "what the bridge is told")
    async with ctx.widget("<button aria-haspopup=listbox aria-labelledby=label> (Workday) with the 'Select One' placeholder"):
        element = sent["wd_country"]
        eq((element["label"].startswith("Country"), element["role"], element["selected_text"], element["has_value"]), (True, "combobox", "", False), "what the bridge is told")
    async with ctx.widget("<button aria-haspopup=listbox aria-label=...> is named by its aria-label"):
        element = sent["al_level"]
        eq((element["label"], element["role"], element["has_value"]), ("Seniority level", "combobox", False), "what the bridge is told")
    async with ctx.widget("decoys: menu button, submit-by-default button, submit button, unlabelled dropdown buttons, the chevron of a combobox input, the language switcher"):
        for decoy in ("Row actions", "Pick one", "Pick a team", "Apply now", "Send application", "Sort by", "Newest first", "Show countries", "Show cities",
                      "Show regions", "Toggle the list", "Language", "English"):
            expect(not has_label(labels, decoy), f"{decoy!r} became an assist field: {labels}")
        for dom_id in ("menu_btn", "sub_default", "sub_explicit", "rc_submit", "rc_type_submit", "sort_by", "sort_by2", "cx_toggle", "cv_tab", "cv_sibling",
                       "cv_shared", "lang"):
            expect(dom_id not in sent, f"{dom_id} was sent to the bridge")
        for kept in ("Country of residence", "City", "Region"):
            expect(kept in labels, f"the combobox input next to a chevron is still a field ({kept}): {labels}")
    async with ctx.widget("Radix-style select is filled through Tab (open, pick the option, verify the trigger text)"):
        field = await fill_with_tab(page, "#rx_fruit", "Favourite fruit")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("fruit"), "banana", "page state")
        eq(await page.evaluate("document.querySelector('#rx_fruit').firstElementChild.textContent"), "Banana", "trigger text")
        eq(await page.evaluate("document.querySelector('#rx_fruit').hasAttribute('data-placeholder')"), False, "placeholder marker")


# ------------------------------------------------------------------------------------------------------ yes / no pairs


@scenario("scanner_two_yes_no_buttons_are_one_radio_group")
async def yes_no_pairs(ctx: Ctx) -> None:
    page, bg = await ctx.open("scanner_patterns.html", {"willing to relocate": RADIO("Yes", "Yes")})
    state = await debug(page)
    kinds = {f["label"]: f["kind"] for f in state["fields"]}
    sent = sent_elements(bg)
    async with ctx.widget("Yes/No <button aria-pressed> pair in a labelled group: one radio field, the group's question, options Yes and No"):
        eq(kinds.get("Are you willing to relocate?"), "radio", "kind")
        element = sent["yn1_yes"]
        eq([o["label"] for o in element["options"]], ["Yes", "No"], "options")
        eq((element["role"], element["multiple"], element["has_value"]), ("radio", False, False), "role / multiple / has_value")
    async with ctx.widget("Sí/No pair: a pressed button is a selection"):
        eq(kinds.get("¿Dispone de permiso de trabajo?"), "radio", "kind")
        element = sent["yn2_si"]
        eq(([o["label"] for o in element["options"]], element["has_value"], element["selected_text"]), (["Sí", "No"], True, "No"), "what the bridge is told")
    async with ctx.widget("decoys: Accept/Decline, Yes/No/Maybe, a feedback pair in the footer are not questions"):
        for label in ("Marketing e-mails", "Can you start next month?", "Was this page helpful?"):
            expect(label not in kinds, f"{label!r} became a field: {sorted(kinds)}")
        for dom_id in ("yn3_a", "yn3_d", "yn4_y", "yn4_n", "yn4_m", "fb_yes", "fb_no"):
            expect(dom_id not in sent, f"{dom_id} was sent")
    async with ctx.widget("Tab clicks the Yes button of the pair"):
        field = await fill_with_tab(page, "#yn1_yes", "willing to relocate")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("yn1"), "Yes", "page state")
        eq(await page.evaluate("document.querySelector('#yn1_yes').getAttribute('aria-pressed')"), "true", "pressed")


# ------------------------------------------------------------------------------------------------------ dropzones and traps


@scenario("scanner_file_inputs_in_a_drop_area_and_narrow_comboboxes")
async def dropzones_and_narrow_comboboxes(ctx: Ctx) -> None:
    page, bg = await ctx.open("scanner_patterns.html", {})
    state = await debug(page)
    kinds = {f["label"]: f["kind"] for f in state["fields"]}
    sent = sent_elements(bg)
    async with ctx.widget("a hidden file input inside the only visible drop area is a file field"):
        eq(kinds.get("Portfolio PDF"), "file", "kind")
        eq(sent["portfolio"]["role"], "upload", "role")
    async with ctx.widget("not a file field: a drop area with two inputs, a hidden input with no drop area, a 10px drop area"):
        for name in ("sample", "loose", "tiny"):
            expect(name not in sent, f"{name} was sent")
    async with ctx.widget("a few-pixel-wide combobox input inside a visible control, or with a visible label, is a field"):
        labels = list(kinds)
        expect("Where are you based" in labels and "Labelled narrow combobox" in labels, f"missing from {labels}")
    async with ctx.widget("honeypots stay honeypots: 1px, tabindex=-1, no control and no label, off-screen"):
        names = [n for n in sent if n.startswith("trap_")]
        eq(names, [], "honeypot comboboxes sent to the bridge")
        for label in ("Website", "Fax", "Company"):
            expect(label not in kinds, f"honeypot labelled {label!r} became a field: {sorted(kinds)}")


# ------------------------------------------------------------------------------------------------------ placeholders


@scenario("scanner_a_selection_the_assistant_made_in_a_widget_it_cannot_read_is_remembered")
async def blind_combobox(ctx: Ctx) -> None:
    page, bg = await ctx.open("scanner_patterns.html", {"preferred office": COMBO("Madrid", "Madrid")})
    async with ctx.widget("a div combobox that shows nothing itself: empty before, the label the assistant saw after its own fill"):
        element = sent_elements(bg)["bl_sel"]
        eq((element["selected_text"], element["has_value"]), ("", False), "before the fill")
        field = await fill_with_tab(page, "#bl_sel", "Preferred office")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("office"), "madrid", "page state")

        async def refreshed() -> Any:
            last = sent_elements(bg).get("bl_sel")
            return last if last and last["has_value"] else None

        element = await eventually(refreshed, 5, "the changed selection is sent to the bridge")
        eq((element["selected_text"], element["has_value"]), ("Madrid", True), "after the fill")


@scenario("scanner_placeholders_and_invisible_text_are_not_selections")
async def placeholders(ctx: Ctx) -> None:
    sentinel = "ZQ7SENTINELtyped"
    init = f"""addEventListener('DOMContentLoaded', () => {{
        const set = (el, v) => {{ const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {{bubbles: true}})); }};
        set(document.getElementById('typed'), '{sentinel}-name'); set(document.getElementById('typed_area'), '{sentinel}-notes'); }});"""
    page, bg = await ctx.open("scanner_patterns.html", {}, init=init)
    sent = sent_elements(bg)
    for dom_id, what in (("mui_sel", "MUI Select: a zero-width space"), ("s2_sel", "Select2: 'Select a country'"), ("wd2_sel", "Workday: 'Select One'")):
        async with ctx.widget(f"{what} is no selection"):
            element = sent[dom_id]
            eq((element["selected_text"], element["has_value"]), ("", False), "what the bridge is told")
    async with ctx.widget("a real selection (zero-width characters around it) is sent without them"):
        element = sent["ok_sel"]
        eq((element["selected_text"], element["has_value"]), ("United Kingdom", True), "what the bridge is told")
    async with ctx.widget("typed text is only ever reported as has_value, in no field of any message"):
        for dom_id in ("typed", "typed_area"):
            element = sent[dom_id]
            eq((element["has_value"], element["value"], element["selected_text"], element["selected_values"]), (True, "", "", []), f"{dom_id} as sent")
        expect(sentinel.lower() not in json.dumps(bg.log).lower(), "typed text appears in a message to the background")


# ------------------------------------------------------------------------------------------------------ positions and names


@scenario("scanner_positions_follow_document_order_and_custom_choices_are_named_by_their_own_text")
async def positions_and_names(ctx: Ctx) -> None:
    page, bg = await ctx.open("scanner_patterns.html", {})
    sent = sent_elements(bg)
    async with ctx.widget("every control sent carries its position in document order"):
        last_scan = [r for r in bg.suggest_requests() if r["mode"] == "scan"][-1]
        elements = last_scan["elements"]
        positions = [e["position"] for e in elements]
        eq(positions, list(range(len(elements))), "positions of the scan request")
        # the controls that can be found in the page again (an id or a name), in the order the page has them, against the scan's own order
        named = [(e["position"], e["id"] or e["name"]) for e in elements if e["id"] or e["name"]]
        dom_order = await page.evaluate("""(keys) => keys.map((k, i) => [document.getElementById(k) || document.querySelector('[name="' + k + '"]'), i])
            .filter((p) => p[0]).sort((a, b) => (a[0].compareDocumentPosition(b[0]) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1)).map((p) => p[1])""", [k for _, k in named])
        expect(len(named) >= 20 and dom_order == sorted(dom_order), f"the scan does not list the controls in document order: {dom_order}")
    state = await debug(page)
    kinds = {f["label"]: f["kind"] for f in state["fields"]}
    async with ctx.widget("div[role=radio] cards without aria-label: the options are named by their own text"):
        eq(kinds.get("Preferred contract"), "radio", "kind")
        element = next(e for e in sent.values() if e["role"] == "radio" and e["context"] == "Preferred contract")
        eq([o["label"] for o in element["options"]], ["Permanent", "Fixed term"], "options")
    async with ctx.widget("div[role=checkbox] cards in a group: named by their own text, the group question is the field's"):
        element = next(e for e in sent.values() if e["role"] == "checkbox" and e["context"] == "Which regions can you work in?")
        eq([o["label"] for o in element["options"]], ["Europe", "Americas"], "options")
    async with ctx.widget("a single div[role=checkbox] keeps the caption of its field as its name"):
        expect("Contact consent" in kinds, f"missing from {sorted(kinds)}")
    async with ctx.widget("radio groups in table layouts: the question is the cell before the answer cell, or the row above"):
        for question in ("Do you hold a driving licence?", "Have you worked for us before?", "Can you travel?", "Can you relocate?"):
            eq(kinds.get(question), "radio", f"{question!r} as a radio field")
        lone = next(e for e in sent.values() if e["role"] == "radio" and e["name"] == "lone")
        eq(lone["context"], "", "a radio group without any question text stays without one")


# ------------------------------------------------------------------------------------------------------ page report


LOCATION_CASES = {
    "greenhouse": "Remote - London, UK",
    "jobboards": "Madrid, Spain",
    "schema": "Dublin, Ireland",
    "lever": "Berlin, Germany",
    "label": "Lisbon, Portugal",
    "nested": "Paris",
    "limit": "x" * 199,
    "form_field": None,
    "filter": None,
    "footer": None,
    "too_long": None,
    "wrapper_of_everything": None,
}


@scenario("page_report_location_hint_comes_from_the_posting_header_never_from_form_fields")
async def location_hint(ctx: Ctx) -> None:
    for case, wanted in LOCATION_CASES.items():
        page, bg = await ctx.open(f"job_header.html?case={case}", {})
        async with ctx.widget(f"location_hint [{case}] = {wanted if wanted is None or len(wanted) < 40 else '(199 characters)'!r}"):
            reports = bg.msgs("session:open")
            expect(reports, "no session was opened")
            hint = reports[0]["report"].get("location_hint")
            eq(hint, wanted, "location_hint of the page report")
            expect(hint is None or len(hint) <= 200, "longer than 200 characters")


# ------------------------------------------------------------------------------------------------------ no typed value leaves the page

SENTINELS = {"text": "ZQ7SENTINELtext", "email": "zq7sentinel@example.test", "tel": "+447351849261", "number": "7351849261", "url": "https://zq7sentinel.example.test"}
TYPE_IT = """() => {
  const S = %s;
  let typed = 0;
  const setter = (el, v) => { const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); };
  const deep = (root, out) => { for (const el of root.querySelectorAll('*')) { out.push(el); if (el.shadowRoot) deep(el.shadowRoot, out); } return out; };
  for (const el of deep(document, [])) {
    if (el.closest && el.closest('[data-meridian-assistant]')) continue;
    if (el instanceof HTMLTextAreaElement && !el.readOnly && !el.disabled) { setter(el, S.text + '-area'); typed++; }
    else if (el instanceof HTMLInputElement && !el.readOnly && !el.disabled) {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      const v = {text: S.text, search: S.text, email: S.email, tel: S.tel, number: S.number, url: S.url}[t];
      if (v !== undefined) { setter(el, v); typed++; }
    } else if (el.isContentEditable && el.getAttribute('contenteditable') !== null) { el.textContent = S.text + '-editable'; el.dispatchEvent(new InputEvent('input', {bubbles: true})); typed++; }
  }
  return typed;
}""" % json.dumps(SENTINELS)

LEAK_GROUPS = {
    "widget libraries": [
        "scanner_patterns.html", "react_select.html", "mui.html", "intl_tel_input.html", "phone_libs.html", "vue_app.html", "forms.html",
        "select2_jquery.html", "editors.html", "datepicker.html", "headless.html", "downshift.html", "dropzone.html", "angular_cva.html",
    ],
    "ATS replicas and rig fixtures": [
        "workable_widgets.html", "icims_widgets.html", "lever_widgets.html", "workday_widgets.html", "smartrecruiters_widgets.html",
        "teamtailor_widgets.html", "aria_duplicates.html", "choices.html", "combobox.html", "editable.html", "honeypot.html",
        "honeypot_variants.html", "jobpage.html", "many_fields.html", "masked.html", "plain_editable.html", "prefilled.html",
        "react_shim.html", "rerender.html", "rich_editor.html", "shadow.html", "shadow_widget.html", "stacked.html",
    ],
    "the Playwright filler's own fixtures": [
        "greenhouse.html", "lever.html", "ashby_trainline.html", "workday.html", "generic.html", "sections.html", "inline_questions.html",
        "reliability_wizard.html", "application_entry.html", "search_location.html", "disclosure_policy.html",
    ],
}


async def leak_check(ctx: Ctx, paths: list[str], *, min_controls: int, min_pages: int) -> None:
    needles = [s.lower() for s in SENTINELS.values()]
    total_typed = pages_with_text = 0
    for path in paths:
        bg = Bg({})
        try:
            page, bg = await ctx.open(path, bg=bg, activate=False)
        except Exception as exc:  # noqa: BLE001 (a fixture that does not load is reported, not hidden)
            async with ctx.widget(f"{path}: loads"):
                raise AssertionError(f"could not open: {exc}") from None
            continue
        await asyncio.sleep(0.6)  # dynamic pages (React, Vue, Quill, Select2) render after load
        typed = await page.evaluate(TYPE_IT)
        await asyncio.sleep(0.25)
        await ctx.rig.activate(page, bg)
        try:
            await eventually(lambda: page.evaluate("!!window.__meridianAssist && window.__meridianAssist.debug().scan.scans >= 1"), 6, "first scan")
        except (AssertionError, TimeoutError):
            pass
        # focus every control once (without committing anything): focus requests are messages too
        await page.evaluate("""() => { for (const el of [...document.querySelectorAll('input,textarea,select,[contenteditable],[role=combobox],[role=textbox]')].slice(0, 40)) {
            try { el.focus({ preventScroll: true }); el.blur(); } catch (e) { /* not focusable */ } } }""")
        await asyncio.sleep(0.35)
        async with ctx.widget(f"{path}: {typed} typed controls, nothing typed in any message"):
            dump = json.dumps(bg.log).lower()
            for needle in needles:
                expect(needle not in dump, f"{needle!r} was sent to the background")
            for request in bg.suggest_requests():
                for element in request["elements"]:
                    if element["role"] == "textbox":
                        expect(element["value"] == "" and element["selected_text"] == "" and not element["selected_values"],
                               f"text control sent with a value: {element['label']!r}")
        total_typed += typed
        pages_with_text += bool(typed)
    async with ctx.widget(f"the corpus really exercised typed values ({total_typed} controls on {pages_with_text} pages)"):
        expect(total_typed >= min_controls and pages_with_text >= min_pages, f"only {total_typed} controls typed on {pages_with_text} of {len(paths)} pages")


@scenario("no_typed_value_reaches_any_message_widget_library_pages")
async def leak_widget_libraries(ctx: Ctx) -> None:
    """Sentinel texts are typed into EVERY text control (inputs of every text-like type, textareas, contenteditables, inside open shadow roots
    too) BEFORE the assistant scans; then every message the page sent to the background (page report, scans, focus requests) is searched
    for them. A text control may only ever be reported as `has_value`."""
    await leak_check(ctx, LEAK_GROUPS["widget libraries"], min_controls=60, min_pages=12)


@scenario("no_typed_value_reaches_any_message_ats_replicas_and_rig_fixtures")
async def leak_ats_replicas(ctx: Ctx) -> None:
    await leak_check(ctx, LEAK_GROUPS["ATS replicas and rig fixtures"], min_controls=200, min_pages=18)


@scenario("no_typed_value_reaches_any_message_playwright_filler_fixtures")
async def leak_filler_fixtures(ctx: Ctx) -> None:
    await leak_check(ctx, LEAK_GROUPS["the Playwright filler's own fixtures"], min_controls=20, min_pages=8)


# ------------------------------------------------------------------------------------------------------ cost


@scenario("observer_looks_up_a_section_heading_once_per_section_not_once_per_control", webkit=False)
async def observer_heading_lookups(ctx: Ctx) -> None:
    """observation.js (shared with the Playwright filler) used to search a whole <section> for its heading once per control: on a page
    of 40 sections and 120 controls that was half of the scan time. Counted, not timed: timings depend on the machine's load."""
    page, bg = await ctx.open("big_page.html", {}, ready=False, activate=False)
    source = OBSERVATION_JS.read_text()
    counts = await page.evaluate(
        """([src]) => { const original = Element.prototype.querySelector; let headingLookups = 0, all = 0;
            Element.prototype.querySelector = function (selector) { all++; if (String(selector).startsWith('legend,h1')) headingLookups++; return original.call(this, selector); };
            try { const snapshot = (0, eval)('(' + src + ')')(); return { headingLookups, all, controls: snapshot.elements.length, sections: document.querySelectorAll('section').length }; }
            finally { Element.prototype.querySelector = original; } }""", [source])
    print(f"      big page: {counts}", flush=True)
    async with ctx.widget("heading lookups of the observer are bounded by the number of sections"):
        expect(counts["controls"] >= 100 and counts["sections"] >= 30, f"the page is not the big page: {counts}")
        expect(counts["headingLookups"] <= counts["sections"] + 1, f"{counts['headingLookups']} heading lookups for {counts['sections']} sections and {counts['controls']} controls")


# ------------------------------------------------------------------------------------------------------ the filler's observer is unchanged by default


@scenario("observer_default_output_is_what_the_playwright_filler_always_got", webkit=False)
async def observer_default_mode(ctx: Ctx) -> None:
    """The Playwright filler calls observation.js without arguments: the widget patterns (dropzone inputs, listbox buttons, Yes/No pairs)
    must not appear for it; the extension asks for them with {widgets: true}."""
    page, bg = await ctx.open("scanner_patterns.html", {}, activate=False)
    source = OBSERVATION_JS.read_text()
    summary = await page.evaluate(
        """([src]) => { const fn = (0, eval)('(' + src + ')'); const pick = (s) => s.elements.map((e) => [e.id || e.name, e.role]);
            return { plain: pick(fn()), widgets: pick(fn({ widgets: true })) }; }""", [source])
    plain, widgets = {k: v for k, v in summary["plain"]}, {k: v for k, v in summary["widgets"]}
    async with ctx.widget("without options: buttons stay buttons, hidden dropzone inputs stay unseen, Yes/No buttons stay buttons"):
        eq((plain["lb_mode"], plain["wd_country"], plain["al_level"], plain["yn1_yes"]), ("button", "button", "button", "button"), "roles for the filler")
        expect("portfolio" not in plain, "a hidden dropzone input is visible to the filler")
    async with ctx.widget("with {widgets: true}: the same page shows the dropdowns, the drop area and the radio pairs"):
        eq((widgets["lb_mode"], widgets["wd_country"], widgets["al_level"], widgets["yn1_yes"], widgets["yn2_si"]), ("combobox", "combobox", "combobox", "radio", "radio"), "roles for the extension")
        eq(widgets["portfolio"], "upload", "role of the hidden dropzone input")
        eq(tuple(widgets[k] for k in ("menu_btn", "sub_default", "sub_explicit", "sort_by2", "cv_tab", "cv_sibling", "cv_shared", "lang")), ("button",) * 8,
           "menu, submit and chevron buttons stay buttons")
    del bg

