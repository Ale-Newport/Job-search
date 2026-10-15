"""Form-filling scenarios: the keyboard model, every control type, and every thing the assistant must NOT do.

Each scenario starts from a connected browser (paired through the real bridge protocol) and a real, seeded backend
(see seed.py). They assert values AND the absence of forbidden effects: nothing submitted, nothing written to
passwords, consent boxes, protected answers or the CAPTCHA widget.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time

from playwright.async_api import Page, expect

import expectations as ex
import flows
import seed
import ui
from framework import Env, check, scenario
from ui import Chip, Panel
from scale import millis, seconds  # noqa: E402

SUGGEST = ("bridge", "worker", "e2e_ports", "content", "ui", "bridge_routes", "resolver")


async def ready_form(env: Env, name: str, query: str = "", *, variant: str = "e2e", backend_args: dict | None = None):
    """Connected browser + the page open and assisted; returns (rig, page, panel) once the panel says 'ready'."""
    rig = await env.connected(variant=variant, backend_args=backend_args)
    page = await env.open(name, query, activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    return rig, page, panel


async def drafts_finished(env: Env, page: Page, *, calls: int = 1) -> None:
    """Background drafting is over: the fake model answered ``calls`` generation requests and the panel shows none pending."""
    await flows.poll_until(lambda: _generated(env, calls), timeout=30, message=f"{calls} draft request(s) at the model")
    await flows.wait_drafts_done(page)
    await asyncio.sleep(0.3)


async def _generated(env: Env, calls: int) -> bool:
    return len(env.llm.generation_calls()) >= calls and len([c for c in env.llm.calls if c["kind"] == "review"]) >= calls


async def sha256_of_attached(root, selector: str) -> str:
    return await root.evaluate(
        """async (sel) => { const f = document.querySelector(sel).files[0]; if (!f) return '';
          const d = await crypto.subtle.digest('SHA-256', await f.arrayBuffer());
          return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join(''); }""", selector)


async def reach_submit(page: Page, selector: str = "#submit_app") -> None:
    """After the last assisted field the assistant steps aside: native Tab continues (through the page's own tab stops,
    e.g. an invisible reCAPTCHA badge frame) to the Submit button, which only the user may press."""
    for _ in range(4):
        if await page.evaluate("(s) => document.activeElement === document.querySelector(s)", selector):
            return
        await ui.tab(page)
    await ui.expect_focus(page, selector)


def written(events: list[dict]) -> set[str]:
    return {event.get("id") or event.get("name") for event in events if event.get("id") or event.get("name")}


@scenario(needs=SUGGEST, timeout=120)
async def scenario_activation_and_chip_on_focus(env: Env) -> None:
    """Toolbar click -> panel -> focusing a field shows the recommendation, its trust colour and the key hint."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await expect(panel.tid("panel-job")).to_contain_text("Acme Robotics")
    await expect(panel.tid("progress-item").first).to_be_visible(timeout=millis(15_000))
    check("the panel lists the form's fields", await panel.tid("progress-item").count() >= 20, await panel.tid("progress-item").count())
    await page.click("#first_name")
    chip = Chip(page)
    await chip.expect_chip(state="VERIFIED", tone="verified", insert="tab", value=seed.FIRST)
    await expect(chip.part("hint-tab")).to_be_visible()
    check("the chip is not in the page's tab order", await chip.part("chip-accept").get_attribute("tabindex") == "-1")
    check("focus stayed in the form", await ui.focused_id(page) == "first_name")
    await page.locator("#colour").focus()
    await chip.expect_chip(state="NEEDS_YOU")
    check("an unknown answer is a quiet one-liner, not a value", await chip.el.first.get_attribute("data-quiet") == "true")
    await page.locator("#cover_letter").focus()
    await chip.expect_skip()  # a quiet chip says why the upload is left alone
    await page.locator("#gender").focus()
    await chip.expect_chip(state="SENSITIVE_BLANK", insert="never")
    await page.locator("#gdpr_consent").focus()
    await chip.expect_chip(state="CONSENT", insert="never")
    events = await ui.synthetic_events(page)
    check("just looking wrote nothing to the page", events == [], events[:3])
    await ui.assert_never_submitted(page)
    check("no uncaught page error", not rig.errors, rig.errors[:2])


@scenario(needs=SUGGEST, timeout=150)
async def scenario_tab_walk_greenhouse(env: Env) -> None:
    """The heart of the product: Tab inserts and advances through a whole Greenhouse-style form, field by field."""
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, ex.GREENHOUSE, label="greenhouse")
    await reach_submit(page)
    check("after the last field native Tab reached the Submit button, which was never pressed", True)
    # FR-03: Tab found no further question, so the panel lists the REQUIRED questions that are still empty, in form order: the two protected
    # answers (work authorization and sponsorship take Enter, never Tab, and this walk used Tab) and the two consent boxes the assistant
    # never ticks for the person. Every other required question of the form was answered by the walk.
    await expect(panel.tid("panel-review")).to_be_visible(timeout=millis(15_000))
    listed = [text.strip().lower() for text in await panel.tid("review-item").all_inner_texts()]
    check("the end-of-form review lists exactly the four required questions Tab leaves to the person, in order",
          len(listed) == 4 and "authorized to work" in listed[0] and "visa sponsorship" in listed[1] and "consent" in listed[2]
          and "privacy notice" in listed[3] and not any("*" in t for t in listed), listed)
    events = await ui.synthetic_events(page)
    inserted = {row.sel.lstrip("#") for row in ex.GREENHOUSE if row.inserted and row.kind != "checks"} | {"lang_en", "lang_es"}
    touched = written(events) - {row.sel.lstrip("#") for row in ex.GREENHOUSE if row.known_issue}
    check("exactly the planned controls were written to", touched == inserted, sorted(touched ^ inserted))
    known = {row.sel.lstrip("#") for row in ex.GREENHOUSE if row.known_issue}
    ui.assert_untouched(events, *[name for name in ex.GREENHOUSE_NEVER_WRITTEN if name not in known])
    await ui.assert_never_submitted(page)
    expected_hash = hashlib.sha256(await env.backend.cv_bytes()).hexdigest()
    check("the attached file is byte-identical to the approved CV", await sha256_of_attached(page, "#resume") == expected_hash)
    check("passwords and the CAPTCHA response were not touched", await ui.field_value(page, "#g-recaptcha-response") == "")
    states = await panel.item_states()
    done = sum(1 for status in states.values() if status == "inserted")
    check("the panel counts the insertions", done >= len(inserted) - 2, (done, states))
    generated = env.llm.generation_calls()
    check("only the long personal question was drafted by the model", [c["question"] for c in generated]
          == ["Why do you want to work at Acme Robotics?"], [c["question"] for c in generated])
    check("no uncaught page error", not rig.errors, rig.errors[:2])


@scenario(needs=SUGGEST, timeout=120)
async def scenario_skip_filled_and_replace(env: Env) -> None:
    """Fields that already hold a value are skipped on the way forward; a different value is never overwritten."""
    _, page, panel = await ready_form(env, "prefilled.html")
    chip = Chip(page)
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    check("the chip says the field already holds something else", await chip.el.first.get_attribute("data-conflict") == "true")
    await expect(chip.part("chip-replace")).to_be_visible()
    await ui.tab(page)
    check("Tab did NOT overwrite 'Alexander'", await ui.field_value(page, "#first_name") == "Alexander")
    await ui.expect_focus(page, "#email")
    check("and skipped 'Last name', which already holds a value", True)
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    check("empty fields are filled", await ui.field_value(page, "#email") == seed.EMAIL)
    await ui.expect_focus(page, "#country")
    await chip.expect_chip(state="VERIFIED", value="United Kingdom")
    await ui.tab(page)
    check("a placeholder option ('Select an option') counts as empty", await ui.select_label(page, "#country") == "United Kingdom")
    await ui.expect_focus(page, "#phone")
    await chip.expect_chip(state="VERIFIED", value=seed.PHONE)
    await ui.tab(page)
    check("phone filled", await ui.field_value(page, "#phone") == seed.PHONE)
    check("last name was never touched", await ui.field_value(page, "#last_name") == "Morgan")
    # Replace is an explicit click on the chip, and it can be undone
    await page.locator("#first_name").focus()
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await chip.click("chip-replace")
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    await expect(chip.part("chip-undo")).to_be_visible()
    await chip.click("chip-undo")
    await expect(page.locator("#first_name")).to_have_value("Alexander")
    await ui.assert_never_submitted(page)
    ui.assert_untouched(await ui.synthetic_events(page), "last_name", "submit_app")


@scenario(needs=SUGGEST, timeout=120)
async def scenario_shift_tab_and_escape(env: Env) -> None:
    """Shift+Tab goes back without inserting or skipping; Esc dismisses a chip until the field is focused again."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    await ui.expect_focus(page, "#last_name")
    await chip.expect_chip(state="VERIFIED", value=seed.LAST)
    await ui.shift_tab(page)
    await ui.expect_focus(page, "#first_name")
    check("Shift+Tab inserted nothing into the field it left", await ui.field_value(page, "#last_name") == "")
    check("and kept what was already there", await ui.field_value(page, "#first_name") == seed.FIRST)
    await ui.tab(page)
    await ui.expect_focus(page, "#last_name")
    await chip.expect_chip(state="VERIFIED", value=seed.LAST)
    await page.keyboard.press("Escape")
    await chip.expect_none()
    await ui.tab(page)
    check("after Esc, Tab only moves on", await ui.field_value(page, "#last_name") == "")
    await ui.expect_focus(page, "#email")
    await ui.shift_tab(page)
    await ui.expect_focus(page, "#last_name")
    await chip.expect_chip(state="VERIFIED", value=seed.LAST)
    await ui.tab(page)
    await expect(page.locator("#last_name")).to_have_value(seed.LAST)
    check("refocusing brought the chip back and Tab inserted", True)
    await page.keyboard.press("Escape")  # no chip is showing for the next field yet or it is: either way Esc must not break anything
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_enter_inserts_protected(env: Env) -> None:
    """Legal/protected answers are never inserted by Tab: Enter (or a click on the chip) is the deliberate act."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATED_DRAFT", insert="tab")
    await page.keyboard.press("Enter")
    check("Enter in a long answer is just a new line (it never inserts a draft)", await ui.field_value(page, "#why") == "\n")
    await page.locator("#why").fill("")
    await page.locator("#authorised").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="Yes")
    await expect(chip.part("hint-enter")).to_be_visible()
    await expect(chip.part("chip-protected")).to_be_visible()
    await ui.tab(page)
    check("Tab left the work-authorization answer alone", await ui.select_label(page, "#authorised") in {"", "Please select"})
    await page.locator("#authorised").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="Yes")
    await page.keyboard.press("Enter")
    await expect(page.locator("#authorised")).to_have_value("1")
    check("Enter inserted the confirmed answer", await ui.select_label(page, "#authorised") == "Yes")
    await page.locator("#sponsorship_yes").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No")
    await chip.click("chip-accept")  # a real click on the chip does the same as Enter
    check("a click on the chip checked the 'No' radio", await ui.checked_group(page, "input[name=sponsorship]") == ["0"])
    await ui.assert_never_submitted(page)
    ui.assert_untouched(await ui.synthetic_events(page), "gdpr_consent", "privacy_notice", "gender", "disability_yes")


@scenario(needs=SUGGEST, timeout=90)
async def scenario_enter_is_not_hijacked(env: Env) -> None:
    """Enter in an ordinary field is the browser's: it submits the form natively (the user's own act) and inserts nothing."""
    _, page, panel = await ready_form(env, "masked_phone.html")
    chip = Chip(page)
    await page.click("#email")
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await page.keyboard.press("Enter")
    await ui.settle(page, 300)
    check("Enter inserted nothing", await ui.field_value(page, "#email") == "")
    submits = await ui.submit_events(page)
    check("the form's own submit event fired, from the user's key press (never hijacked, never extension-made)",
          [s["kind"] for s in submits] == ["click", "submit"] and all(s["trusted"] for s in submits), submits)
    await expect(chip.el.first).to_be_visible()
    check("the page is intact and the assistant still offers the answer", True)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_protected_tab_setting(env: Env) -> None:
    """The user may allow Tab for confirmed protected answers (Settings): then Tab inserts them, consent still never."""
    backend = await env.start_backend()
    response = await backend.request("PATCH", "/extension/settings", json={"tab_inserts_protected": True}, expect=None)
    check("the app accepts the setting", response.status_code == 200, response.text[:200])
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.locator("#authorised").focus()
    await chip.expect_chip(state="VERIFIED", value="Yes")
    await ui.tab(page)
    await expect(page.locator("#authorised")).to_have_value("1")
    await page.locator("#sponsorship_yes").focus()
    await chip.expect_chip(state="VERIFIED", value="No")
    await ui.tab(page)
    check("with the setting on, Tab inserted the protected answers", await ui.checked_group(page, "input[name=sponsorship]") == ["0"])
    await page.locator("#gdpr_consent").focus()
    await chip.expect_chip(state="CONSENT")
    await ui.tab(page)
    await page.locator("#gender").focus()
    await ui.tab(page)
    ui.assert_untouched(await ui.synthetic_events(page), "gdpr_consent", "privacy_notice", "gender", "ethnicity")


@scenario(needs=SUGGEST, timeout=150)
async def scenario_lever_walk(env: Env) -> None:
    """A Lever-style form (application-question markup, full-name field, survey selects) walks the same way."""
    _, page, panel = await ready_form(env, "lever_apply.html")
    await flows.focus_first(page, "#resume")
    await flows.walk(page, ex.LEVER, label="lever")
    await reach_submit(page)
    events = await ui.synthetic_events(page)
    ui.assert_untouched(events, "eeo_gender", "eeo_race", "work_auth", "org", "submit_app")
    await ui.assert_never_submitted(page)
    check("name, email and phone were inserted",
          [await ui.field_value(page, f"#{i}") for i in ("name", "email", "phone")] == [seed.NAME, seed.EMAIL, seed.PHONE])


@scenario(needs=SUGGEST, timeout=150)
async def scenario_choices_and_selects(env: Env) -> None:
    """Native select, radio group, checkbox group and <select multiple>, driven by verified facts and profile answers."""
    backend = await env.start_backend()
    await backend.profile_answer("relocation", "Yes")
    await backend.profile_answer("workplace_preference", "Hybrid")
    _, page, panel = await ready_form(env, "choices.html")
    await flows.focus_first(page, "#country")
    await flows.walk(page, ex.CHOICES, label="choices")
    ui.assert_untouched(await ui.synthetic_events(page), "auth_yes", "auth_no", "lang_fr", "lang_de", "submit_app")
    check("the French and German options of the group stayed unchecked", await ui.checked_group(page, "input[name=lang]") == ["English", "Spanish"])
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=180)
async def scenario_ashby_combobox_and_yes_no(env: Env) -> None:
    """Late-rendered Ashby form: custom comboboxes are opened and clicked, Yes/No buttons answer, uploads never open a chooser."""
    backend = await env.start_backend()
    await backend.profile_answer("notice_period", "1 month")
    _, page, panel = await ready_form(env, "ashby_apply.html")
    chooser = []
    page.on("filechooser", lambda c: chooser.append(c))
    await page.wait_for_selector("#ashby_form", state="attached")
    chip = Chip(page)
    await page.click("#name")
    await chip.expect_chip(state="VERIFIED", value=seed.NAME)
    await ui.tab(page)
    await ui.expect_focus(page, "#email")
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    await expect(page.locator("#resume")).to_be_focused()  # the real (visually hidden) file input; the chip sits at its button
    await chip.expect_chip(state="VERIFIED", value=seed.CV_FILENAME)
    await ui.tab(page)
    await expect(page.locator("#linkedin")).to_be_focused(timeout=millis(15_000))
    check("the approved CV was attached to the hidden file input, without opening a file chooser",
          await ui.field_value(page, "#resume") == seed.CV_FILENAME and chooser == [])
    await chip.expect_chip(state="VERIFIED", value=seed.LINKEDIN)
    await ui.tab(page)
    await expect(page.locator("#notice")).to_be_focused()
    await chip.expect_chip(state="VERIFIED", value="1 month")
    await ui.tab(page)  # opens the custom menu, clicks the matching option, verifies the menu closed
    await expect(page.locator("#location")).to_be_focused(timeout=millis(15_000))  # focus moves on only when the fill is done
    check("the option was CLICKED (the page recorded the selection) and the menu is closed again",
          await page.evaluate("window.selections.notice") == "1 month"
          and await page.get_attribute("#notice", "aria-expanded") == "false" and await page.input_value("#notice") == "1 month")
    await chip.expect_chip(state="VERIFIED", value="London")
    await ui.tab(page)
    await expect(page.locator("#sponsor_yes")).to_be_focused(timeout=millis(15_000))
    await expect(page.locator("#location")).to_have_value(re.compile(r"^London.*United Kingdom$"))
    check("the location option was clicked too", await page.evaluate("window.selections.location") == await page.input_value("#location"))
    # the page declares its job location in JSON-LD: the country is known, so the protected answer is offered (Enter, never Tab)
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No", timeout=millis(15_000))
    await page.keyboard.press("Enter")
    await expect(page.locator("#sponsor_no")).to_have_attribute("aria-pressed", "true")
    check("the 'No' button is pressed and 'Yes' is not", await page.get_attribute("#sponsor_yes", "aria-pressed") == "false")
    await ui.assert_never_submitted(page)
    ui.assert_untouched(await ui.synthetic_events(page), "submit_app")


@scenario(needs=SUGGEST, timeout=120)
async def scenario_file_attach_cv(env: Env) -> None:
    """Only the user's approved CV goes into a resume field; cover letters, transcripts and other uploads are left alone."""
    _, page, panel = await ready_form(env, "file_upload.html")
    chooser = []
    page.on("filechooser", lambda c: chooser.append(c))
    chip = Chip(page)
    await page.locator("#resume").focus()
    await chip.expect_chip(state="VERIFIED", value=seed.CV_FILENAME)
    await ui.tab(page)
    await expect(page.locator("#resume_name")).to_have_text(seed.CV_FILENAME)
    expected = hashlib.sha256(await env.backend.cv_bytes()).hexdigest()
    check("the file's bytes equal the approved CV", await sha256_of_attached(page, "#resume") == expected)
    meta = await page.evaluate("window.__files.resume")
    check("name and type are those of the document", meta["name"] == seed.CV_FILENAME and meta["type"] == "application/pdf", meta)
    for other, code in (("cover_letter", "file_cover_letter"), ("transcript", "generic"), ("work_sample", "generic")):
        await ui.expect_focus(page, f"#{other}")
        await chip.expect_skip(code=code, timeout=millis(6_000))  # a transcript is told nothing about a cover letter (it is a plain upload)
        await ui.tab(page)
        check(f"{other}: nothing attached", await ui.field_value(page, f"#{other}") == "")
    check("no file chooser was ever opened", chooser == [])
    ui.assert_untouched(await ui.synthetic_events(page), "cover_letter", "transcript", "work_sample", "submit_app")
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_react_controlled_state(env: Env) -> None:
    """React 18 controlled inputs: insertions reach component state (value setter + events), consent stays unchecked."""
    _, page, panel = await ready_form(env, "react_form.html")
    await flows.focus_first(page, "#first")
    await flows.walk(page, ex.REACT, label="react")
    state = await page.evaluate("window.__state()")
    check("React's own state holds the inserted values", (state["first"], state["last"], state["email"], state["country"])
          == (seed.FIRST, seed.LAST, seed.EMAIL, "GB"), state)
    await ui.expect_focus(page, "#work_yes")
    chip = Chip(page)
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="Yes")
    await page.keyboard.press("Enter")
    await flows.poll_until(lambda: _react_state(page, "work", "Yes"), timeout=8, message="React state work == Yes")
    await ui.expect_focus(page, "#terms")
    await chip.expect_chip(state="CONSENT")
    await ui.tab(page)
    await ui.expect_focus(page, "#cv")
    await chip.expect_chip(state="VERIFIED", value=seed.CV_FILENAME)
    await ui.tab(page)
    await flows.poll_until(lambda: _react_state(page, "resume", None), timeout=8, message="React state resume set")
    state = await page.evaluate("window.__state()")
    check("the file reached React state with the document's name and size", state["resume"].startswith(seed.CV_FILENAME + ":"), state)
    check("the terms checkbox is still unchecked in React state and in the DOM", state["terms"] is False and not await page.is_checked("#terms"))
    await ui.assert_never_submitted(page)


async def _react_state(page: Page, key: str, value) -> bool:
    state = await page.evaluate("window.__state()")
    return bool(state[key]) if value is None else state[key] == value


@scenario(needs=SUGGEST, timeout=120)
async def scenario_masked_phone(env: Env) -> None:
    """A national-number-only masked phone input gets the number without the dial code, formatted by the page's own mask."""
    _, page, panel = await ready_form(env, "masked_phone.html")
    chip = Chip(page)
    await page.click("#email")
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    await ui.expect_focus(page, "#phone_national")
    await chip.expect_chip(state="VERIFIED", tone="verified", insert="tab")
    await ui.tab(page)
    await expect(page.locator("#phone_national")).to_have_value(seed.PHONE_NATIONAL)
    raw = await page.evaluate("window.__maskInputs")
    check("the mask received the national digits only (no +44, no doubled prefix)", raw and all("+44" not in v and not v.replace(" ", "").startswith("44") for v in raw), raw)
    check("the field's digits are the candidate's national number", (await ui.field_value(page, "#phone_national")).replace(" ", "") == "7700900123")
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_contenteditable_editors(env: Env) -> None:
    """contenteditable answer boxes: plain editor, editor with its own model, and a drafted long answer; plain text only."""
    _, page, panel = await ready_form(env, "contenteditable.html")
    await drafts_finished(env, page)
    chip = Chip(page)
    await page.click("#editor_name")
    await chip.expect_chip(state="VERIFIED", value=seed.NAME)
    await ui.tab(page)
    await ui.expect_focus(page, "#editor_why")
    check("the plain editor learned about it through input events",
          await page.evaluate("window.__editorText.editor_name") == seed.NAME and await ui.field_value(page, "#editor_name") == seed.NAME)
    await chip.expect_chip(state="GENERATED_DRAFT", tone="draft", value=seed.PROJECT_STORY[:40])
    await flows.wait_dwell_over(chip)
    await ui.tab(page)
    draft = await ui.field_value(page, "#editor_why")
    check("the draft went into the rich text box as text", seed.PROJECT_STORY in draft and "<" not in draft, draft)
    check("and the page's state follows", (await page.evaluate("window.__editorText.editor_why")) == draft)
    await ui.expect_focus(page, "#editor_framework")
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    check("the model-owning editor accepted the text through beforeinput", await page.evaluate("window.__model.editor_framework") == seed.EMAIL
          and await ui.field_value(page, "#editor_framework") == seed.EMAIL)
    children = await page.evaluate("document.querySelector('#editor_why').children.length + document.querySelector('#editor_name').children.length")
    check("no HTML elements were created inside the editors", children == 0, children)
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_plain_text_never_html(env: Env) -> None:
    """A value that looks like markup is inserted as inert text (never parsed, never executed)."""
    backend = await env.start_backend()
    hostile = 'Alex <img src=x onerror="window.__xss=1"> Morgan'
    try:
        await backend.profile_answer("full_name", hostile)
    except Exception as exc:  # noqa: BLE001
        from framework import Skip

        raise Skip(f"the Profile API refused the markup-like name, so this path cannot be exercised: {exc}") from exc
    _, page, panel = await ready_form(env, "contenteditable.html")
    chip = Chip(page)
    await page.click("#editor_name")
    await chip.expect_chip(state="VERIFIED", value="Alex")
    await ui.tab(page)
    await expect(page.locator("#editor_name")).to_contain_text("Alex")
    probe = await page.evaluate("({xss: window.__xss === undefined, img: document.querySelector('#editor_name img') === null, "
                                "html: document.querySelector('#editor_name').innerHTML})")
    check("no element was created and no handler ran", probe["xss"] and probe["img"], probe)
    check("the chip rendered the same text inertly too", await page.locator("[data-testid=chip] img").count() == 0)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_conditional_question(env: Env) -> None:
    """A follow-up question that appears only after its parent is answered is picked up and respects the parent answer."""
    _, page, panel = await ready_form(env, "conditional.html")
    chip = Chip(page)
    before = await panel.tid("progress-item").count()
    check("the follow-up is not scanned while hidden", await page.locator("#visa_detail").is_hidden())
    await page.locator("#sponsor_yes").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No")
    await page.keyboard.press("Enter")
    await expect(page.locator("#visa_detail")).to_be_visible()
    await flows.poll_until(lambda: _count_grew(panel, before), timeout=10, message="the follow-up to appear in the panel list")
    await ui.expect_focus(page, "#visa_detail", timeout=millis(10_000))
    await expect(chip.el.first).to_be_visible(timeout=millis(10_000))
    state = await chip.el.first.get_attribute("data-state")
    check("the follow-up is 'not applicable' (or needs you), never a guess", state in {"NOT_APPLICABLE", "NEEDS_YOU"}, state)
    await ui.tab(page)
    check("Tab put nothing into the follow-up", await ui.field_value(page, "#visa_detail") == "")
    await ui.assert_never_submitted(page)


async def _count_grew(panel: Panel, before: int) -> bool:
    return await panel.tid("progress-item").count() > before


@scenario(needs=SUGGEST, timeout=240)
async def scenario_consent_and_demographics(env: Env) -> None:
    """Consent and demographics: blank by policy, never inserted by Tab; confirmed answers need a deliberate Enter."""
    backend = await env.start_backend()
    _, page, panel = await ready_form(env, "consent_demographics.html")
    await flows.focus_first(page, "#pronouns")
    await flows.walk(page, ex.CONSENT_PAGE_DEFAULT, label="no confirmed answers")
    ui.assert_untouched(await ui.synthetic_events(page), "pronouns", "gender", "disability_yes", "disability_no", "disability_na",
                        "terms", "marketing", "transcribe_yes", "transcribe_no")
    # the user confirms demographics in the app: Meridian's knowledge changes and the open page must notice
    await backend.profile_answer("pronouns", "they/them/theirs")
    await backend.profile_answer("gender", "Prefer not to say")
    await backend.profile_answer("disability", "No")
    await backend.set_policy("personal_disclosures", "use_confirmed")
    chip = Chip(page)
    await page.locator("#gender").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="Prefer not to say", timeout=millis(30_000))
    await ui.tab(page)
    check("a confirmed sensitive answer is still not inserted by Tab", await ui.select_label(page, "#gender") in {"", "Select..."})
    await page.locator("#gender").focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#gender")).to_have_value("Prefer not to say")
    await page.locator("#pronouns").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="they/them/theirs")
    await page.keyboard.press("Enter")
    await expect(page.locator("#pronouns")).to_have_value("they/them/theirs")
    await page.locator("#disability_yes").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No")
    await page.keyboard.press("Enter")
    check("the disability answer 'No' was chosen deliberately", await ui.checked_group(page, "input[name=disability]") == ["no"])
    for consent in ("#terms", "#marketing", "#transcribe_yes"):
        await page.locator(consent).focus()
        await chip.expect_chip(state="CONSENT", insert="never")
        await ui.tab(page)
    events = await ui.synthetic_events(page)
    ui.assert_untouched(events, "terms", "marketing", "transcribe_yes", "transcribe_no", "submit_app")
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=90)
async def scenario_no_form_decoy(env: Env) -> None:
    """A page with no application form: a calm 'waiting for the form' panel, no chip, the site search box untouched."""
    _, page, panel = await ready_form(env, "decoy.html")
    await expect(panel.tid("calm-no-form")).to_be_visible(timeout=millis(15_000))
    await page.click("#site_search")
    await page.keyboard.type("robots")
    await Chip(page).expect_none()
    await ui.tab(page)
    check("the user's own search text is untouched and nothing was written by the extension",
          await ui.field_value(page, "#site_search") == "robots" and await ui.synthetic_events(page) == [])


@scenario(needs=SUGGEST, timeout=150)
async def scenario_password_captcha_untouched(env: Env) -> None:
    """Password, sign-up and CAPTCHA pages: no chip, no write, no model call; the CAPTCHA widget is never touched."""
    rig, page, panel = await ready_form(env, "password_signup.html")
    chip = Chip(page)
    for selector in ("#signup_email", "#signup_password", "#signup_verify", "#signup_terms"):
        await page.locator(selector).focus()
        await ui.settle(page, 250)
        check(f"{selector}: no chip on a login / sign-up page", await chip.el.count() == 0 or await chip.el.first.get_attribute("data-quiet") == "true")
        await ui.tab(page)
    check("nothing was typed into the account form", [await ui.field_value(page, f"#{i}") for i in
                                                      ("signup_email", "signup_password", "signup_verify")] == ["", "", ""])
    await ui.assert_never_submitted(page)
    page2 = await env.open("captcha_gate.html", activate=False, rig=rig)  # the same tab: the worker restarts the assistant itself
    await rig.ensure_active(page2)
    await ui.settle(page2, 600)
    for selector in ("#first_name", "#email"):
        await page2.locator(selector).focus()
        await ui.settle(page2, 250)
        await ui.tab(page2)
    check("a visible CAPTCHA pauses the whole page: nothing was inserted",
          await ui.field_value(page2, "#first_name") == "" and await ui.field_value(page2, "#email") == "")
    frame = page2.frames[1]
    check("the CAPTCHA widget is untouched", not await frame.evaluate("document.getElementById('recaptcha-anchor').checked")
          and await frame.evaluate("window.__events || []") == [])
    check("no write reached either page", await ui.synthetic_events(page) == [] and await ui.synthetic_events(page2) == [])
    check("the model was never asked about these pages", env.llm.generation_calls() == [])


@scenario(needs=SUGGEST, timeout=120)
async def scenario_tab_immediately(env: Env) -> None:
    """Tab pressed at once after focusing (no chip awaited) never inserts something wrong and never traps the user."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    await page.click("#first_name")
    await page.keyboard.press("Tab")
    value = await ui.field_value(page, "#first_name")
    check("the field holds either nothing or exactly the verified fact", value in {"", seed.FIRST}, value)
    check("focus moved on to the next field", await ui.focused_id(page) in {"last_name", "job_application[last_name]"})
    await page.keyboard.press("Tab")
    await page.keyboard.press("Tab")
    check("nothing but the planned fields was written", written(await ui.synthetic_events(page)) <= {"first_name", "last_name", "email"})
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_shadow_dom_form(env: Env) -> None:
    """A form inside an open shadow root (web component) is scanned, assisted and written to; the chip follows focus."""
    _, page, panel = await ready_form(env, "shadow_form.html")
    chip = Chip(page)
    inside = "document.querySelector('apply-widget').shadowRoot.getElementById"
    await page.locator("apply-widget >> #first_name").click()
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    first_key = await chip.el.first.get_attribute("data-key")
    await ui.tab(page)
    check("first name inserted inside the shadow root", await page.evaluate(f"{inside}('first_name').value") == seed.FIRST)
    check("focus advanced to the next field inside the shadow root",
          await page.evaluate("(document.querySelector('apply-widget').shadowRoot.activeElement || {}).id") == "email")
    # the new focus target retargets to the same <apply-widget> host: the chip must still follow it
    await expect(chip.el.first).to_have_attribute("data-key", re.compile(f"^(?!{re.escape(first_key or '')}$).*"), timeout=millis(5_000))
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    await chip.expect_chip(state="VERIFIED", value=seed.PHONE)
    await ui.tab(page)
    values = [await page.evaluate(f"{inside}('{i}').value") for i in ("first_name", "email", "phone")]
    check("the three fields hold the verified facts", values == [seed.FIRST, seed.EMAIL, seed.PHONE], values)
    # backwards and by mouse: the chip tracks every focus change inside the shadow tree
    keys = {}
    await ui.shift_tab(page)
    await chip.expect_chip(state="VERIFIED", value=seed.PHONE)
    keys["phone"] = await chip.el.first.get_attribute("data-key")
    await ui.shift_tab(page)
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    keys["email"] = await chip.el.first.get_attribute("data-key")
    await page.locator("apply-widget >> #phone").click()
    await chip.expect_chip(state="VERIFIED", value=seed.PHONE)
    check("the chip of the phone field is the phone field's chip again", await chip.el.first.get_attribute("data-key") == keys["phone"])
    await page.locator("apply-widget >> #first_name").click()
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    keys["first"] = await chip.el.first.get_attribute("data-key")
    check("every field of the shadow tree has its own chip identity", len(set(keys.values())) == 3, keys)
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_spa_step_navigation(env: Env) -> None:
    """A single-page wizard: the next step's fields are picked up after the user's own Continue click."""
    _, page, panel = await ready_form(env, "spa_wizard.html")
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, ex.SPA_STEP1, label="step 1")
    await ui.expect_focus(page, "#continue")
    check("after the last field native Tab reached Continue; nothing pressed it", await page.evaluate("location.search") == "")
    await page.click("#continue")  # the user's own click
    await expect(page.locator("#email")).to_be_visible()
    check("the document did not reload, the URL changed", await page.evaluate("location.search") == "?step=2")
    await flows.poll_until(lambda: _panel_has(panel, 3), timeout=12, message="the panel list to show step 2's three fields")
    await flows.focus_first(page, "#email")
    await flows.walk(page, ex.SPA_STEP2, label="step 2")
    await ui.assert_never_submitted(page, user_clicks=1)


async def _panel_has(panel: Panel, count: int) -> bool:
    return await panel.tid("progress-item").count() == count


@scenario(needs=SUGGEST, timeout=180)
async def scenario_cross_origin_iframe_handoff(env: Env) -> None:
    """A multi-step ATS in a cross-origin iframe: chips render in the frame, Tab goes into the frame and on out to the host
    page, the frame's own 'Save and Continue' stays reachable on the way (WCAG 2.1.1), and Shift+Tab retraces the same stops."""
    rig, page, panel = await ready_form(env, "workday_apply.html")
    frame = page.frame_locator("#ats_frame")
    chip_top, chip_frame = Chip(page), Chip(frame)
    child = await flows.frame_by_selector(page, "#ats_frame")
    check("the form really lives on another origin", child.url.startswith(env.fixtures.frame) and not page.url.startswith(env.fixtures.frame), child.url)
    await page.click("#contact_name")
    await chip_top.expect_chip(state="VERIFIED", value=seed.NAME)
    await ui.tab(page)
    check("top field inserted", await ui.field_value(page, "#contact_name") == seed.NAME)
    await expect(frame.locator("#legal_first")).to_be_focused(timeout=millis(10_000))
    check("real focus is inside the cross-origin frame", await child.evaluate("document.hasFocus() && document.activeElement.id") == "legal_first")
    await chip_frame.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    await expect(frame.locator("#legal_last")).to_be_focused()
    await chip_frame.expect_chip(state="VERIFIED", value=seed.LAST)
    await ui.tab(page)
    await expect(frame.locator("#phone_number")).to_be_focused()
    await chip_frame.expect_chip(state="VERIFIED", value=seed.PHONE)
    await ui.tab(page)
    check("the frame's three fields hold the verified facts", [await child.evaluate(f"document.getElementById('{i}').value")
          for i in ("legal_first", "legal_last", "phone_number")] == [seed.FIRST, seed.LAST, seed.PHONE])
    # The frame's own 'Save and Continue' follows its last field, so it is the browser's next stop: Tab lands on it, and it is NOT
    # skipped for the host page's next field (design 7.4 as amended: hand focus to the parent only when no tabbable control remains
    # after the last field in the frame; frame_handoff_survives_a_quick_shift_tab_back covers the hand-off proper).
    await expect(frame.locator("#next1")).to_be_focused(timeout=millis(10_000))
    check("real focus is still inside the cross-origin frame, on its button", await child.evaluate("document.hasFocus()"))
    await ui.tab(page)  # from the button the browser's own Tab leaves the frame for the host page's next field
    await expect(page.locator("#alert_email")).to_be_focused(timeout=millis(10_000))
    check("the top document has real focus again", await page.evaluate("document.hasFocus()"))
    # 'Email for job alerts' is not the plain question 'What is your email?': it is a sign-up for something (and may be another address),
    # so the plain-question gate leaves it to the person: a quiet chip, nothing to insert
    await chip_top.expect_chip(state="NEEDS_YOU", insert="never")
    # Shift+Tab goes back INTO the frame on its last stop (the button) and then onto its last field, inserting nothing
    await ui.shift_tab(page)
    await expect(frame.locator("#next1")).to_be_focused(timeout=millis(10_000))
    check("real focus is back inside the cross-origin frame", await child.evaluate("document.hasFocus()"))
    await ui.shift_tab(page)
    await expect(frame.locator("#phone_number")).to_be_focused(timeout=millis(10_000))
    await ui.tab(page)
    await expect(frame.locator("#next1")).to_be_focused(timeout=millis(10_000))
    await ui.tab(page)
    await expect(page.locator("#alert_email")).to_be_focused(timeout=millis(10_000))
    await ui.tab(page)
    check("the field after the frame was left alone (it is not the plain 'email' question), and focus moved on past it",
          await ui.field_value(page, "#alert_email") == "" and await ui.focused_id(page) != "alert_email")
    states = await panel.item_states()
    check("the panel (top frame) lists fields of both frames", len(states) >= 5, states)
    # backwards from the frame's first field to the top document's previous field
    await child.locator("#legal_first").focus()
    await ui.shift_tab(page)
    await expect(page.locator("#contact_name")).to_be_focused(timeout=millis(10_000))
    # step 2 inside the frame after the user's own click
    await flows.click_in_frame(child, "#next1")
    await expect(frame.locator("#wd_linkedin")).to_be_visible()
    await child.locator("#wd_linkedin").focus()
    await chip_frame.expect_chip(state="VERIFIED", value=seed.LINKEDIN)
    await ui.tab(page)
    await expect(frame.locator("#wd_resume")).to_be_focused(timeout=millis(10_000))
    await chip_frame.expect_chip(state="VERIFIED", value=seed.CV_FILENAME)
    await ui.tab(page)
    await flows.poll_until(lambda: _frame_file(child), timeout=10, message="the CV in the cross-origin frame")
    await ui.assert_never_submitted(page, user_clicks=1)
    check("nothing ever wrote to the Back-to-jobs or Submit controls", True)


async def _frame_file(child) -> bool:
    return await child.evaluate("document.getElementById('wd_resume').files.length") == 1


@scenario(needs=SUGGEST, timeout=120)
async def scenario_frame_handoff_survives_a_quick_shift_tab_back(env: Env) -> None:
    """When the frame's last tab stop IS an assist field (its button is disabled until the form is valid), Tab out of it hands
    focus to the host page, passing over a field that is already filled. Going straight back with Shift+Tab does not switch the
    hand-off off: the next Tab hands focus over again, rather than leaving it to the browser's own Tab (which would stop on the
    filled field)."""
    rig, page, panel = await ready_form(env, "workday_apply.html", "tail=field&prefill=1")
    frame = page.frame_locator("#ats_frame")
    child = await flows.frame_by_selector(page, "#ats_frame")
    chip_frame = Chip(frame)
    check("the frame's button is disabled, so its last field is its last tab stop", await child.evaluate("document.getElementById('next1').disabled"))
    check("the host page's email field already holds a value", await ui.field_value(page, "#alert_email") == "already.there@example.test")
    await child.locator("#phone_number").focus()
    await chip_frame.expect_chip(state="VERIFIED", value=seed.PHONE)
    await ui.tab(page)  # inserts the phone number and hands focus to the host page: over the filled field, onto the button after it
    await flows.poll_until(lambda: _focused(page, "back_to_jobs"), timeout=10, interval=0.02, message="the hand-off to the host page")
    check("the phone number went in", await child.evaluate("document.getElementById('phone_number').value") == seed.PHONE)
    # straight back (the button is a native stop: Shift+Tab lands on the email field, the next one re-enters the frame)
    await ui.shift_tab(page)
    await ui.shift_tab(page)
    await expect(frame.locator("#phone_number")).to_be_focused(timeout=millis(10_000))
    # Wait out the window of the old bug (a 600 ms timer that switched the hand-off off whenever the frame held focus when it fired;
    # a lower bound, so a slow machine cannot make this pass by accident).
    await asyncio.sleep(1.0)
    await ui.tab(page)
    await flows.poll_until(lambda: _focused(page, "back_to_jobs"), timeout=10, interval=0.02, message="the second hand-off to the host page")
    check("the second hand-off passed over the filled field as the first one did", True)
    check("the filled field was left as the page had it", await ui.field_value(page, "#alert_email") == "already.there@example.test")
    await ui.assert_never_submitted(page)


async def _focused(page: Page, element_id: str) -> bool:
    return await page.evaluate("(id) => document.activeElement && document.activeElement.id === id", element_id)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_fill_verified_button(env: Env) -> None:
    """'Fill verified' inserts every verified, tab-tier, non-protected answer at once; nothing else."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    button = panel.tid("panel-fill-verified")
    await expect(button).to_be_enabled(timeout=millis(15_000))
    label = await button.inner_text()
    await button.click()
    plan = [row for row in ex.GREENHOUSE if row.inserted and row.state == ex.VERIFIED and row.insert == "tab" and row.bulk]
    # SEC-01: the first press writes nothing: the panel lists every field the button will fill (label and value); one more click confirms
    await expect(panel.tid("panel-fill-preview")).to_be_visible(timeout=millis(5_000))
    check("the confirmation row lists exactly the fields the button will fill", await panel.tid("fill-preview-item").count() == len(plan),
          await panel.tid("fill-preview-item").count())
    check("and nothing was written before the confirmation", await flows.read_value(page, plan[0]) in ("", None, []), await flows.read_value(page, plan[0]))
    await panel.tid("fill-confirm").click()
    for row in plan:
        await flows.poll_until(lambda row=row: _row_done(page, row), timeout=15, message=f"{row.sel} filled")
    count = int(re.search(r"\((\d+)\)", label).group(1)) if re.search(r"\((\d+)\)", label) else -1
    check("the button advertised exactly how many fields it fills", count == len(plan), f"{label!r}: {count} vs {len(plan)}")
    for row in ex.GREENHOUSE:
        if row in plan:
            continue
        value = await flows.read_value(page, row)
        check(f"{row.sel} ({row.state}/{row.insert}) left alone by Fill verified", flows.is_empty(row, value), value)
    ui.assert_untouched(await ui.synthetic_events(page), *ex.GREENHOUSE_NEVER_WRITTEN, "degree", "employer", "why", "authorised",
                        "sponsorship_yes", "sponsorship_no")
    await ui.assert_never_submitted(page)


async def _row_done(page: Page, row: ex.F) -> bool:
    try:
        await flows.expect_row_inserted(page, row)
    except AssertionError:
        return False
    return True


@scenario(needs=SUGGEST, timeout=120)
async def scenario_undo_and_pause_keys(env: Env) -> None:
    """Alt/Option+Z undoes the last insertion; 'Pause Tab' gives the keyboard back to the browser."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    await ui.expect_focus(page, "#last_name")
    await page.keyboard.press("Alt+KeyZ")
    await expect(page.locator("#first_name")).to_have_value("")
    check("the insertion was undone (the field is as before)", True)
    toggle = panel.tid("panel-keys")
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-checked", "false")
    await page.locator("#last_name").focus()
    await ui.settle(page, 300)
    await ui.tab(page)
    check("with the keys paused Tab inserts nothing and moves natively", await ui.field_value(page, "#last_name") == ""
          and await ui.focused_id(page) == "email")
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-checked", "true")
    await page.locator("#email").focus()
    await chip.expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    await expect(page.locator("#email")).to_have_value(seed.EMAIL)
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=150)
async def scenario_trust_labels_and_insertion_feedback(env: Env) -> None:
    """Every answer says how far to trust it (verified / check this / AI draft / protected / left blank / yours to decide),
    in the user's language, and an insertion is visibly confirmed on the field."""
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    chip = Chip(page)
    labels = {
        "#first_name": ("VERIFIED", "Verified"), "#degree": ("INFERRED", "Check this"),
        "#why": ("GENERATED_DRAFT", "AI draft"), "#authorised": ("VERIFIED", "Protected answer"),
        "#gender": ("SENSITIVE_BLANK", "Left blank"), "#gdpr_consent": ("CONSENT", "Yours to decide"),
        "#colour": ("NEEDS_YOU", "Needs you"),
    }
    for selector, (state, text) in labels.items():
        await page.locator(selector).focus()
        await chip.expect_chip(state=state)
        shown = await (chip.part("chip-trust") if state not in {"SENSITIVE_BLANK", "CONSENT", "NEEDS_YOU"}
                       else chip.part("chip-label")).first.inner_text()
        check(f"{selector}: the chip says {text!r}", text.lower() in shown.lower(), shown)
    await page.locator("#degree").focus()
    await chip.expect_chip(state="INFERRED", tone="inferred")
    check("a derived answer wears the amber 'inferred' tone", (await chip.attributes())["data-tone"] == "inferred")
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    # the flash lives well under a second: watch for it instead of sampling (a loaded machine can miss a sample)
    await page.evaluate("""() => { const root = document.querySelector('[data-meridian-assistant]').shadowRoot; window.__flashSeen = false;
        const seen = () => { if (root.querySelector('[data-testid=flash]')) window.__flashSeen = true; };
        new MutationObserver(seen).observe(root, {subtree: true, childList: true, attributes: true}); seen(); }""")
    await page.keyboard.press("Tab")
    await flows.poll_until(lambda: page.evaluate("window.__flashSeen === true"), timeout=3, interval=0.02,
                           message="the sparkle flash on the inserted field")
    check("the inserted field was outlined for a moment (sparkle flash)", True)
    # the same page in Spanish after the user changes the language in Meridian
    await backend.request("PATCH", "/extension/settings", json={"language": "es"})
    await page.reload(wait_until="domcontentloaded")
    await rig.ensure_active(page)  # the worker restarts the assistant after a reload by itself; a click would switch it off
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST, timeout=millis(20_000))
    check("the labels follow the language setting", "verificado" in (await chip.part("chip-trust").first.inner_text()).lower())
    await page.locator("#degree").focus()
    await chip.expect_chip(state="INFERRED")
    check("derived answers: 'Revísalo'", "revísalo" in (await chip.part("chip-trust").first.inner_text()).lower())
    html_lang = await page.evaluate("document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=ui]').lang")
    check("the UI declares its language to assistive technology", html_lang.startswith("es"), html_lang)


async def mark_geometry_ok(page: Page, mark, field_selector: str) -> bool:
    """The marker badge sits on the field's end-side top corner (marks.ts): inside a generous frame around the field."""
    field = await page.locator(field_selector).bounding_box()
    badge = await mark.bounding_box()
    if not field or not badge:
        return False
    cx, cy = badge["x"] + badge["width"] / 2, badge["y"] + badge["height"] / 2
    return (field["x"] + field["width"] - 40 <= cx <= field["x"] + field["width"] + 40
            and field["y"] - 40 <= cy <= field["y"] + field["height"])


@scenario(needs=SUGGEST, timeout=150)
async def scenario_inserted_marker_until_touched(env: Env) -> None:
    """An INFERRED or drafted insertion keeps a persistent, named marker on the field until the user touches it; verified
    insertions get none; the marker follows the field when the page scrolls."""
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    await drafts_finished(env, page)
    chip = Chip(page)
    marks = page.locator("[data-testid=mark]")
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED")
    await ui.tab(page)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    await ui.settle(page, 400)
    check("a verified insertion leaves no marker (nothing to double-check)", await marks.count() == 0)
    await page.locator("#degree").focus()
    await chip.expect_chip(state="INFERRED")
    await ui.tab(page)
    await expect(page.locator("#degree")).to_have_value("Master's degree")
    await expect(marks).to_have_count(1, timeout=millis(5_000))
    check("the marker is amber ('inferred')", await marks.first.get_attribute("data-tone") == "inferred"
          and "tone-inferred" in (await marks.first.get_attribute("class") or ""))
    check("it has an accessible name that says what to do", "check" in (await marks.first.get_attribute("aria-label") or "").lower(),
          await marks.first.get_attribute("aria-label"))
    check("it sits on the degree field's corner", await mark_geometry_ok(page, marks.first, "#degree"))
    check("it is not in the tab order and ignores the mouse",
          await marks.first.evaluate("(el) => getComputedStyle(el).pointerEvents === 'none' && el.tabIndex < 0"))
    # a drafted answer gets a (blue) marker too
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATED_DRAFT")
    await flows.wait_dwell_over(chip)
    await ui.tab(page)
    await expect(marks).to_have_count(2, timeout=millis(5_000))
    tones = sorted([await marks.nth(i).get_attribute("data-tone") for i in range(2)])
    check("one amber and one blue marker", tones == ["draft", "inferred"], tones)
    # it follows the field when the page scrolls (the draft's field is the one in view now)
    draft_mark = page.locator("[data-testid=mark][data-tone=draft]")
    check("the draft's marker sits on the answer box", await mark_geometry_ok(page, draft_mark, "#why"))
    before = await draft_mark.bounding_box()
    await page.evaluate("window.scrollBy(0, -60)")
    await ui.settle(page, 300)
    after = await draft_mark.bounding_box()
    check("the marker moved with the page when it scrolled", before and after and abs((after["y"] - before["y"]) - 60) <= 2,
          (before, after))
    check("and still sits on the answer box", await mark_geometry_ok(page, draft_mark, "#why"))
    await page.evaluate("window.scrollTo(0, 0)")
    # touching the field removes ITS marker only
    await page.locator("#degree").focus()
    await page.keyboard.type("!")
    await expect(marks).to_have_count(1, timeout=millis(5_000))
    check("typing into the degree field removed its marker", await marks.first.get_attribute("data-tone") == "draft")
    await page.locator("#why").focus()
    await page.keyboard.type(" Thanks.")
    await expect(marks).to_have_count(0, timeout=millis(5_000))
    check("editing the draft removed the last marker (the user has touched both answers)", True)
    await ui.assert_never_submitted(page)


GH = "[name='job_application[%s]']"
EXISTING_GREENHOUSE = [
    ex.F(GH % "first_name", ex.VERIFIED, "tab", chip=seed.FIRST, result=seed.FIRST, tone="verified"),
    ex.F(GH % "last_name", ex.VERIFIED, "tab", chip=seed.LAST, result=seed.LAST, tone="verified"),
    ex.F(GH % "email", ex.VERIFIED, "tab", chip=seed.EMAIL, result=seed.EMAIL, tone="verified"),
    ex.F("[name=country]", ex.VERIFIED, "tab", chip="United Kingdom", kind="select", result="United Kingdom", tone="verified"),
    ex.F("[name=relocation][value=yes]", ex.ANY, "never", kind="radio", note="no confirmed relocation answer: left alone"),
    ex.F("[name=resume]", ex.VERIFIED, "tab", chip=seed.CV_FILENAME, kind="file", result=seed.CV_FILENAME, tone="verified"),
    ex.F("[name=privacy]", ex.CONSENT, "never", kind="checks", note="'I agree to the privacy policy' is the user's decision"),
]


async def existing_page(env: Env, name: str, rig) -> Page:
    """An existing fixture in its own tab, with the assistant ready (each scenario keeps to few assisted pages: every
    assisted page holds a long-poll connection, see many_assisted_pages_do_not_starve)."""
    page = await env.open_tab(name, rig=rig)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    return page


@scenario(needs=SUGGEST, timeout=120)
async def scenario_existing_greenhouse_fixture(env: Env) -> None:
    """backend/tests/browser/fixtures/greenhouse.html (read-only): contact fields, country, CV, privacy checkbox."""
    rig = await env.connected()
    page = await existing_page(env, "greenhouse.html", rig)
    await flows.focus_first(page, GH % "first_name")
    await flows.walk(page, EXISTING_GREENHOUSE, label="greenhouse.html")
    check("the privacy checkbox is still unchecked", not await page.is_checked("[name=privacy]"))
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_existing_lever_and_ashby_fixtures(env: Env) -> None:
    """lever.html and ashby_trainline.html (late-rendered) from the repository's fixtures."""
    rig = await env.connected()
    page = await existing_page(env, "lever.html", rig)
    lever = [ex.F("[name=name]", ex.VERIFIED, "tab", chip=seed.NAME, result=seed.NAME, tone="verified"),
             ex.F("[name=email]", ex.VERIFIED, "tab", chip=seed.EMAIL, result=seed.EMAIL, tone="verified"),
             ex.F("[name=resume]", ex.VERIFIED, "tab", chip=seed.CV_FILENAME, kind="file", result=seed.CV_FILENAME, tone="verified")]
    await flows.focus_first(page, "[name=name]")
    await flows.walk(page, lever, label="lever.html")
    page = await existing_page(env, "ashby_trainline.html", rig)
    await page.wait_for_selector("[name=email]", state="attached")
    ashby = [ex.F("[name=name]", ex.VERIFIED, "tab", chip=seed.NAME, result=seed.NAME, tone="verified"),
             ex.F("[name=email]", ex.VERIFIED, "tab", chip=seed.EMAIL, result=seed.EMAIL, tone="verified"),
             ex.F("[name=linkedin]", ex.VERIFIED, "tab", chip=seed.LINKEDIN, result=seed.LINKEDIN, tone="verified")]
    await flows.focus_first(page, "[name=name]")
    await flows.walk(page, ashby, label="ashby_trainline.html")


@scenario(needs=SUGGEST, timeout=120)
async def scenario_existing_workday_and_tall_iframe_fixtures(env: Env) -> None:
    """workday.html (step 2 in a frame after the user's own Next) and tall_iframe.html (a 2000 px frame)."""
    rig = await env.connected()
    page = await existing_page(env, "workday.html", rig)
    await flows.focus_first(page, "[name=first_name]")
    await flows.walk(page, [ex.F("[name=first_name]", ex.VERIFIED, "tab", chip=seed.FIRST, result=seed.FIRST, tone="verified"),
                            ex.F("[name=last_name]", ex.VERIFIED, "tab", chip=seed.LAST, result=seed.LAST, tone="verified")],
                     label="workday.html step 1")
    await page.get_by_role("button", name="Next").click()  # the user's own click
    frame = page.frame_locator("iframe")
    await frame.locator("[name=email]").wait_for(state="visible", timeout=millis(10_000))
    child = await flows.frame_by_selector(page, "iframe")
    await child.locator("[name=email]").focus()
    await Chip(frame).expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    await expect(frame.locator("[name=country]")).to_be_focused(timeout=millis(10_000))
    await Chip(frame).expect_chip(state="VERIFIED", value="United Kingdom")
    await ui.tab(page)
    check("workday frame: email and country inserted",
          await child.evaluate("document.querySelector('[name=email]').value") == seed.EMAIL
          and await ui.select_label(child, "[name=country]") == "United Kingdom")
    page = await existing_page(env, "tall_iframe.html", rig)
    frame = page.frame_locator("iframe")
    child = await flows.frame_by_selector(page, "iframe")
    await child.locator("[name=email]").focus()
    await Chip(frame).expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    check("tall iframe: email inserted", await child.evaluate("document.querySelector('[name=email]').value") == seed.EMAIL)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_existing_wizard_fixture(env: Env) -> None:
    """reliability_wizard.html: the same document shows new fields on every step."""
    rig = await env.connected()
    page = await existing_page(env, "reliability_wizard.html", rig)
    await flows.focus_first(page, "[name=email]")
    await Chip(page).expect_chip(state="VERIFIED", value=seed.EMAIL)
    await ui.tab(page)
    check("wizard step 1: email inserted", await ui.field_value(page, "[name=email]") == seed.EMAIL)
    await page.get_by_role("button", name="Save and continue").click()  # the user's own click
    await expect(page.locator("[name=resume]")).to_be_visible()
    await flows.poll_until(lambda: _panel_has(Panel(page), 2), timeout=12, message="the panel to list step 2's two uploads")
    await page.locator("[name=resume]").focus()
    await Chip(page).expect_chip(state="VERIFIED", value=seed.CV_FILENAME)
    await ui.tab(page)
    await flows.poll_until(lambda: _has_file(page, "[name=resume]"), timeout=10, message="the CV on wizard step 2")
    await ui.expect_focus(page, "[name=cover]")
    await Chip(page).expect_skip()
    await ui.tab(page)
    check("the cover letter input stayed empty", await ui.field_value(page, "[name=cover]") == "")


async def _has_file(page: Page, selector: str) -> bool:
    return await ui.field_value(page, selector) == seed.CV_FILENAME


MANY_PAGES = [("greenhouse_apply.html", "#first_name"), ("lever_apply.html", "#name"), ("react_form.html", "#first"),
              ("masked_phone.html", "#email"), ("prefilled.html", "#email"), ("submit_flow.html", "#first_name"),
              ("conditional.html", "#email"), ("choices.html", "#country"), ("contenteditable.html", "#editor_name"),
              ("spa_wizard.html", "#first_name")]  # ten different postings: ten different bridge sessions


async def worker_polling(rig) -> dict:
    """The worker's shared long-poll state (extension e2e hook: waits held, request in flight, many|single mode, counters)."""
    return await rig.sw.evaluate("() => globalThis.__e2e.polling()")


@scenario(needs=SUGGEST, timeout=240)
async def scenario_many_assisted_pages_do_not_starve(env: Env) -> None:
    """A person with ten application tabs open (each assisted) still gets the first chip of the newest one at once: the
    worker multiplexes every page into ONE long poll instead of one connection per page (Chrome allows six per host)."""
    rig = await env.connected()
    slowest = (0.0, "")
    for index, (name, selector) in enumerate(MANY_PAGES, start=1):
        started = time.monotonic()
        page = await env.open_tab(name)
        await Panel(page).expect_phase("ready", timeout=millis(30_000))
        activated = time.monotonic() - started
        asked = time.monotonic()
        await page.locator(selector).focus()
        await Chip(page).expect_chip(state="VERIFIED", timeout=millis(10_000))
        took = time.monotonic() - asked
        print(f"    assisted page #{index} ({name}): panel ready after {activated:.1f}s, first chip {took:.2f}s after focus", flush=True)
        slowest = max(slowest, (took, name))
        # The newest page is the one the old design starved (it waited ~20 s behind six idle polls): it must have its chip within
        # 3 s on a quiet machine (MERIDIAN_E2E_SLOWDOWN=1), 9 s at the default patience factor of 3: still far under that starvation, but
        # a loaded machine (a load average of 30-40 from other projects measured 3.5 s and 5.9 s with nothing wrong) no longer fails it.
        # The earlier ones only need to be nowhere near that starvation (the bound leaves room for a loaded machine).
        limit = seconds(3.0) if index == len(MANY_PAGES) else 8.0
        check(f"page #{index} ({name}): the first chip came within {limit:.0f} seconds", took < limit, f"{took:.1f}s")
    polling = await worker_polling(rig)
    print("    worker polling state:", polling, flush=True)
    check("the worker used the multiplexed poll for all of them (no per-page fallback)",
          polling["mode"] == "many" and polling["stats"]["many"] >= 1 and polling["stats"]["single"] == 0, polling)
    check("one wait per assisted page, all served by the one shared request", polling["waiting"] == len(MANY_PAGES)
          and polling["inFlight"], polling)
    # going back to the FIRST tab still works at once (it catches up from its cursor)
    first = rig.pages[1]
    await first.bring_to_front()
    await first.locator("#first_name").focus()
    await Chip(first).expect_chip(state="VERIFIED", value=seed.FIRST, timeout=3_000)
    await ui.tab(first)
    await expect(first.locator("#first_name")).to_have_value(seed.FIRST)
    print(f"    slowest first chip of the ten pages: {slowest[0]:.2f}s ({slowest[1]})", flush=True)
    # closing every tab releases the waits (nothing left holding the bridge)
    for page in list(rig.pages[1:]):
        await page.close()
    await flows.poll_until(lambda: _no_waiters(rig), timeout=15, message="the worker to stop waiting for closed tabs")


async def _no_waiters(rig) -> bool:
    return (await worker_polling(rig))["waiting"] == 0
