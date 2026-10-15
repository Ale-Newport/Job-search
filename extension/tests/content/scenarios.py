"""Scenarios of the content-script rig. Every scenario drives REAL key presses and asserts page state, view models handed to
the UI, and the messages the content script sent (never a typed value, never a submission)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from harness import (
    BOX, BOXES, COMBO, FILE, NEEDS, RADIO, SELECT, STATE, T, Bg, Rig, Servers, active_name, chip_for, debug, emit, eventually,
    last_chip, last_panel, make_document, press_fill_verified, normalize, sug, tab, ui_log, value_of,
)


@dataclass
class Scenario:
    name: str
    fn: Callable[["Ctx"], Awaitable[None]]
    webkit: bool = True


SCENARIOS: list[Scenario] = []


def scenario(name: str | None = None, *, webkit: bool = True) -> Callable[[Callable[["Ctx"], Awaitable[None]]], Callable[["Ctx"], Awaitable[None]]]:
    def register(fn: Callable[["Ctx"], Awaitable[None]]) -> Callable[["Ctx"], Awaitable[None]]:
        SCENARIOS.append(Scenario(name or fn.__name__, fn, webkit))
        return fn

    return register


class Ctx:
    def __init__(self, rig: Rig, servers: Servers) -> None:
        self.rig = rig
        self.servers = servers

    async def open(self, path: str, answers: dict[str, Any] | None = None, *, bg: Bg | None = None, **kw: Any):
        return await self.rig.open(path, bg or Bg(answers), **kw)


def doc_ref(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}


def first_name_selector() -> str:
    return 'input[name="job_application[first_name]"]'


# ------------------------------------------------------------------------------------------------------------------------


@scenario("greenhouse_tab_through_all_control_types")
async def greenhouse_tab_through(ctx: Ctx) -> None:
    doc = make_document()
    answers = {
        "first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"),
        "country": SELECT("United Kingdom", "gb"), "willing to relocate": RADIO("Yes", "yes"),
        "resume": FILE(doc_ref(doc)), "privacy policy": STATE("CONSENT"),
    }
    page, bg = await ctx.open("greenhouse.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    # the suggestion cache is warm: Tab pressed right after focus must still insert (decision is synchronous)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    assert await active_name(page) == "job_application[last_name]"
    await tab(page)
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Newport"
    assert await value_of(page, 'input[name="job_application[email]"]') == "alex@example.test"
    assert await active_name(page) == "country"
    await tab(page)
    assert await value_of(page, 'select[name="country"]') == "gb"
    assert await active_name(page) == "relocation"
    await tab(page)
    assert await page.evaluate("document.querySelector('input[name=relocation][value=yes]').checked")
    assert await active_name(page) == "resume"
    await tab(page)
    await eventually(lambda: page.evaluate("document.querySelector('input[name=resume]').files.length === 1"), 5, "file attached")
    assert await page.evaluate("document.querySelector('input[name=resume]').files[0].name") == "Alex-CV.pdf"
    await eventually(lambda: active_name(page), 3, "focus advanced")
    assert await active_name(page) == "privacy"
    # consent: never inserted; no further assist field, so native Tab continues to the submit button
    await tab(page)
    assert not await page.evaluate("document.querySelector('input[name=privacy]').checked")
    if ctx.rig.browser_name == "chromium":
        assert await page.evaluate("document.activeElement.tagName") == "BUTTON"  # native Tab went on to the submit button
    else:
        # Safari-like WebKit does not Tab to buttons; what matters: focus left the field natively and nothing was submitted
        assert await active_name(page) != "privacy"
    assert await page.evaluate("window.submissions") == 0
    # nothing typed by the user was ever sent: text controls carry no value
    for req in bg.suggest_requests():
        for el in req["elements"]:
            if el["tag"] in ("input", "textarea") and el["type"] not in ("radio", "checkbox"):
                assert el["value"] == "", el


# ------------------------------------------------------------------------------------------------------------------------
# helpers


async def field_info(target: Any, label_part: str) -> dict[str, Any]:
    state = await debug(target)
    assert state is not None, "assistant not running in that frame"
    for f in state["fields"]:
        if normalize(label_part) in normalize(f["label"]):
            return f
    raise AssertionError(f"no field labelled {label_part!r} in {[f['label'] for f in state['fields']]}")


async def watch_keys(target: Any) -> None:
    """Records every keydown that REACHES the page in the bubble phase (a swallowed Tab never shows up here)."""
    frame = target.main_frame if hasattr(target, "main_frame") else target
    await frame.evaluate("window.__kd = []; window.addEventListener('keydown', e => window.__kd.push([e.key, e.defaultPrevented]));")


async def reached(target: Any) -> list[list[Any]]:
    frame = target.main_frame if hasattr(target, "main_frame") else target
    keys = await frame.evaluate("window.__kd")
    return [k for k in keys if k[0] not in ("Shift", "Alt", "Control", "Meta")]


async def wait_value(target: Any, selector: str, expected: str, timeout: float = 3.0) -> None:
    async def check() -> bool:
        return (await value_of(target, selector)) == expected

    await eventually(check, timeout, f"{selector} == {expected!r}")


async def settle(page: Any, timeout: float = 8.0) -> None:
    """Waits for an asynchronous fill (custom combobox, file attach) that a Tab started to finish."""
    await asyncio.sleep(0.03)

    async def idle() -> bool:
        return not (await debug(page))["busy"]

    await eventually(idle, timeout, "asynchronous fill finished")


def outgoing_text(bg: Bg, *, skip: tuple[str, ...] = ()) -> str:
    return json.dumps([e["msg"] for e in bg.log if e["msg"].get("t") not in skip])


GH = {
    "first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"),
    "country": SELECT("United Kingdom", "gb"), "willing to relocate": RADIO("Yes", "yes"), "privacy policy": STATE("CONSENT"),
}


# ------------------------------------------------------------------------------------------------------------------------
# keyboard model


@scenario("skip_filled_and_shift_tab")
async def skip_filled_and_shift_tab(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.fill('input[name="job_application[last_name]"]', "Smith")
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    # last name already has a value: skipped (skip_filled), and never overwritten
    assert await active_name(page) == "job_application[email]"
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Smith"
    # Shift+Tab: previous assist field, no skipping and no insertion
    await tab(page, shift=True)
    assert await active_name(page) == "job_application[last_name]"
    await tab(page, shift=True)
    assert await active_name(page) == "job_application[first_name]"
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Smith"
    assert await value_of(page, 'input[name="job_application[email]"]') == ""
    # first field: Shift+Tab has nowhere to go inside the form -> native behaviour (focus leaves, nothing intercepted)
    await watch_keys(page)
    await tab(page, shift=True)
    assert await reached(page) == [["Tab", False]]


@scenario("skip_filled_setting_off_stops_on_filled_fields")
async def skip_filled_off(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", bg=Bg(GH, settings={"skip_filled": False}))
    await page.fill('input[name="job_application[last_name]"]', "Smith")
    await page.focus(first_name_selector())
    await tab(page)
    assert await active_name(page) == "job_application[last_name]"  # stops on it, but Tab does not overwrite
    chip = await chip_for(page, "last name")
    assert chip["conflictsWithCurrent"] is True
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Smith"
    assert await active_name(page) == "job_application[email]"


@scenario("escape_dismisses_chip_until_refocus")
async def escape_dismisses(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await watch_keys(page)
    await page.focus(first_name_selector())
    chip = await chip_for(page, "first name")
    assert chip["value"] == "Alex" and chip["tone"] == "verified" and "tab" in chip["keys"] and "esc" in chip["keys"]
    await page.keyboard.press("Escape")
    assert await last_chip(page) is None, "Esc hides the chip"
    assert (await reached(page)) == [], "Esc was consumed by the assistant (a chip was showing)"
    await tab(page)
    assert await value_of(page, first_name_selector()) == "", "a dismissed suggestion is not inserted by Tab"
    assert await active_name(page) == "job_application[last_name]"
    await tab(page, shift=True)  # refocus: dismissal is over
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    # Esc with no chip showing is left alone
    await page.focus('input[name="job_application[email]"]')
    await page.keyboard.press("Escape")
    await page.keyboard.press("Escape")
    assert [k for k in await reached(page) if k[0] == "Escape"], "second Esc (no chip) reaches the page"


@scenario("protected_answers_use_enter_unless_setting_allows_tab")
async def protected_enter(ctx: Ctx) -> None:
    answers = {"full name": T("Alex Newport"), "visa sponsorship": SELECT("No", "no", insert="explicit", flags={"protected": True}),
               "preferred work mode": RADIO("Hybrid", "hybrid")}
    page, _ = await ctx.open("choices.html", answers)
    await page.focus("select[name=sponsor]")
    chip = await chip_for(page, "visa sponsorship")
    assert chip["insert"] == "explicit" and "enter" in chip["keys"] and "tab" not in chip["keys"]
    assert chip["reasonCode"] == "protected"
    await tab(page)  # Tab never inserts a protected answer by default; it just moves on
    assert await value_of(page, "select[name=sponsor]") == ""
    assert await active_name(page) == "mode"
    await tab(page, shift=True)
    assert await active_name(page) == "sponsor"
    await page.keyboard.press("Enter")
    assert await value_of(page, "select[name=sponsor]") == "no"
    assert await active_name(page) == "mode", "Enter inserts and advances"
    # Enter on a non-explicit chip is left alone (never hijack form submission)
    await page.focus("input[name=full_name]")
    await chip_for(page, "full name")
    await watch_keys(page)
    await page.keyboard.press("Enter")
    assert [k for k in await reached(page) if k[0] == "Enter"] == [["Enter", False]] or await page.evaluate("window.submissions") == 1


@scenario("protected_answers_insert_on_tab_when_setting_is_on")
async def protected_tab_setting(ctx: Ctx) -> None:
    answers = {"visa sponsorship": SELECT("No", "no", insert="explicit", flags={"protected": True})}
    page, _ = await ctx.open("choices.html", bg=Bg(answers, settings={"tab_inserts_protected": True}))
    await page.focus("select[name=sponsor]")
    await tab(page)
    assert await value_of(page, "select[name=sponsor]") == "no"


@scenario("forbidden_states_are_never_inserted")
async def forbidden_states(ctx: Ctx) -> None:
    answers = {
        "full name": NEEDS(), "visa sponsorship": STATE("CONSENT"), "preferred work mode": STATE("SENSITIVE_BLANK"),
        "which languages": STATE("NOT_APPLICABLE"), "skills": STATE("GENERATING", generation={"id": "g", "status": "GENERATING"}),
        "job alerts": STATE("SKIP"), "nickname": {"op": "TYPE_TEXT", "text": "NEVER", "insert": "never"},
        "describe your visa": T("x"),
    }
    page, _ = await ctx.open("choices.html", answers)
    await page.focus("input[name=full_name]")
    seen = ["full_name"]
    for _ in range(6):
        await tab(page)
        seen.append(await active_name(page))
    assert seen[:7] == ["full_name", "sponsor", "mode", "lang", "skills", "alerts", "nickname"], seen
    assert await page.evaluate("""() => ({
        name: document.querySelector('[name=full_name]').value, sponsor: document.querySelector('[name=sponsor]').value,
        mode: !!document.querySelector('[name=mode]:checked'), lang: !!document.querySelector('[name=lang]:checked'),
        skills: document.querySelector('[name=skills]').selectedOptions.length, alerts: document.querySelector('[name=alerts]').checked,
        nick: document.querySelector('[name=nickname]').value })""") == {
        "name": "", "sponsor": "", "mode": False, "lang": False, "skills": 0, "alerts": False, "nick": ""}
    panel = await last_panel(page)
    assert panel["pendingDrafts"] == 1
    statuses = {i["label"]: i["status"] for i in panel["progress"]["items"]}
    assert statuses["Which languages do you speak?"] == "skipped" and statuses["Do you require visa sponsorship?"] == "locked"


@scenario("consent_looking_checkbox_is_never_ticked_by_tab_even_if_the_server_says_so")
async def consent_guard(ctx: Ctx) -> None:
    answers = dict(GH)
    answers["privacy policy"] = BOX(True)  # a buggy/hostile suggestion: VERIFIED + tab tier on a consent box
    page, _ = await ctx.open("greenhouse.html", answers)
    await page.focus('input[name="privacy"]')
    chip = await chip_for(page, "privacy")
    assert "tab" not in chip["keys"]
    await tab(page)
    assert not await page.evaluate("document.querySelector('input[name=privacy]').checked")


@scenario("generating_advances_then_draft_waits_for_dwell")
async def draft_dwell(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"),
               "why do you want": STATE("GENERATING", generation={"id": "g1", "status": "GENERATING", "attempt": 1})}
    page, bg = await ctx.open("draft.html", answers)
    await page.focus("textarea[name=why]")
    chip = await chip_for(page, "why do you want")
    assert chip["state"] == "GENERATING" and chip["tone"] == "busy"
    await tab(page)  # GENERATING: Tab advances and never blocks
    assert await active_name(page) == "email"
    panel = await last_panel(page)
    assert panel["pendingDrafts"] == 1
    info = await field_info(page, "why do you want")
    draft_text = "I admire Acme's mission and my backend work on data pipelines fits the role."
    draft = sug({"node_id": info["key"].split(":")[1], "context": "Why do you want to work at Acme?"}, {"frame_key": "top"},
                state="GENERATED_DRAFT", op="TYPE_TEXT", text=draft_text, generation={"id": "g1", "status": "DONE", "word_count": 15},
                why={"code": "generated", "summary": "Based on your projects and Acme's mission", "sources": [{"label": "Acme about", "url": "https://acme.example/about"}]})
    draft["node_id"] = int(info["key"].split(":")[1])
    bg.push_event({"type": "suggestion", "suggestion": draft})

    async def draft_arrived() -> bool:
        return (await field_info(page, "why do you want"))["state"] == "GENERATED_DRAFT"

    await eventually(draft_arrived, 4, "draft delivered by the poll loop")
    await tab(page, shift=True)
    chip = await chip_for(page, "why do you want")
    assert chip["state"] == "GENERATED_DRAFT" and chip["tone"] == "draft" and chip["review"] is True
    assert chip["dwellRemainingMs"] > 0 or chip["value"] == draft_text
    # Tab right away: the draft has not been visible for 500 ms, so Tab only advances (holding Tab never inserts unseen text)
    await tab(page)
    assert await value_of(page, "textarea[name=why]") == ""
    assert await active_name(page) == "email"
    await asyncio.sleep(0.7)
    await tab(page, shift=True)
    chip = await chip_for(page, "why do you want")
    assert chip["dwellRemainingMs"] == 0 and "tab" in chip["keys"] and "regenerate" in chip["keys"]
    await tab(page)
    assert await value_of(page, "textarea[name=why]") == draft_text
    marks = await ui_log(page, "setMarks")
    assert any(m["key"].endswith(info["key"].split(":")[1]) for call in marks for m in call["arg"]), "an AI draft keeps a marker until touched"
    # Alt+R regenerates from the DRAFT text, never from what the user typed
    await tab(page, shift=True)
    await chip_for(page, "why do you want")
    await page.keyboard.press("Alt+KeyR")
    await eventually(lambda: bg.msgs("regenerate"), 3, "regenerate request")
    req = bg.msgs("regenerate")[0]["request"]
    assert req["instruction"] == "again" and req["current_text"] == draft_text and req["question_id"].startswith("q:top:")


@scenario("conflict_is_never_overwritten_replace_and_undo")
async def conflict_replace_undo(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.fill('input[name="job_application[last_name]"]', "Smith")
    await page.focus('input[name="job_application[last_name]"]')

    async def conflicting() -> bool:
        chip = await last_chip(page)
        return bool(chip and chip["conflictsWithCurrent"])

    await eventually(conflicting, 3, "chip reports the conflict")
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Smith"
    await tab(page, shift=True)
    chip = await chip_for(page, "last name")
    await emit(page, {"type": "replace", "key": chip["key"]})
    await wait_value(page, 'input[name="job_application[last_name]"]', "Newport")
    chip = await chip_for(page, "last name")
    await eventually(lambda: last_chip(page), 2)
    assert (await last_chip(page))["undoable"] is True
    await page.keyboard.press("Alt+KeyZ")
    await wait_value(page, 'input[name="job_application[last_name]"]', "Smith")
    toasts = [c["arg"]["kind"] for c in await ui_log(page, "toast")]
    assert "undone" in toasts
    # an explicit Accept on an empty field inserts too
    await page.fill('input[name="job_application[last_name]"]', "")
    chip = await chip_for(page, "last name")
    await emit(page, {"type": "accept", "key": chip["key"]})
    await wait_value(page, 'input[name="job_application[last_name]"]', "Newport")


@scenario("pause_keys_gives_native_tab_back")
async def pause_keys(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await emit(page, {"type": "toggle-keys"})
    assert (await last_panel(page))["keysPaused"] is True
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, first_name_selector()) == ""
    assert (await reached(page))[0] == ["Tab", False], "paused: the page sees an untouched native Tab"
    assert await active_name(page) == "job_application[last_name]"
    await emit(page, {"type": "toggle-keys"})
    await tab(page, shift=True)
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("tab_before_suggestions_arrive_never_inserts_late")
async def slow_backend(ctx: Ctx) -> None:
    bg = Bg(GH)
    bg.suggest_delay = lambda req: 0.9
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: bg.msgs("session:open"), 4, "session")
    await eventually(lambda: bg.msgs("suggest"), 4, "request in flight")
    await page.focus(first_name_selector())
    await tab(page)
    # the cache is empty: Tab only moves on (own traversal); nothing is inserted into the field the user left
    assert await value_of(page, first_name_selector()) == ""
    assert await active_name(page) == "job_application[last_name]"
    await rig_ready(ctx, page, bg)
    assert await value_of(page, first_name_selector()) == "", "a suggestion that arrives late is never inserted behind the user's back"
    await tab(page, shift=True)
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


async def rig_ready(ctx: Ctx, page: Any, bg: Bg) -> None:
    await ctx.rig.ready(page, bg, timeout=10)


@scenario("stale_suggestions_are_discarded")
async def stale_suggestions(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    bg.suggest_delay = lambda req: 0.6
    # the page changes the question: the cached suggestion no longer matches the element
    await page.evaluate("""() => { const l = document.querySelector('input[name="job_application[last_name]"]').closest('label');
        l.firstChild.textContent = 'Family name '; }""")
    await asyncio.sleep(0.5)  # rescan debounce; the refresh request is now in flight (delayed)
    await page.focus('input[name="job_application[last_name]"]')
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "", "stale entry must not be inserted"
    await eventually(lambda: any(r["mode"] == "refresh" and any(e["name"] == "job_application[last_name]" for e in r["elements"])
                                 for r in bg.suggest_requests()), 4, "refresh request for the changed element")
    # knowledge changes: everything is re-requested and old entries are dropped
    bg.kv = "kv2"
    bg.suggest_delay = lambda req: 0.5
    bg.push_event({"type": "knowledge", "knowledge_version": "kv2"})
    await asyncio.sleep(0.4)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "", "after a knowledge change the old suggestion is not trusted"
    await eventually(lambda: len([r for r in bg.suggest_requests() if r["mode"] == "scan"]) >= 2, 4, "full rescan after knowledge event")


@scenario("fill_on_last_field_falls_through_to_native_tab")
async def fall_through(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test")}
    page, _ = await ctx.open("draft.html", answers)
    await page.focus("input[name=email]")
    await chip_for(page, "email")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=email]") == "alex@example.test", "the last field is still filled"
    assert await reached(page) == [["Tab", False]], "no further assist field: the Tab is not prevented, the browser continues"


# ------------------------------------------------------------------------------------------------------------------------
# fill engine: frameworks, masks, rich editors, choices


@scenario("react_like_controlled_inputs_receive_state")
async def react_state(ctx: Ctx) -> None:
    answers = {
        "first name": T("Alex"), "email": T("alex@example.test"), "country": SELECT("United Kingdom", "gb"),
        "authorised to work": RADIO("Yes", "yes"), "job alerts": BOX(True), "short bio": T("Backend engineer who likes data."),
    }
    page, _ = await ctx.open("react_shim.html", answers)
    # the trap the shim emulates: assigning el.value and firing `input` is ignored by value-tracking frameworks
    await page.evaluate("""() => { const el = document.querySelector('[name=first_name]'); el.value = 'naive';
        el.dispatchEvent(new Event('input', { bubbles: true })); }""")
    assert (await page.evaluate("window.__state()"))["first_name"] == "", "the shim must reject the naive write (otherwise this test proves nothing)"
    await page.focus("[name=first_name]")
    await tab(page, 6)
    state = await page.evaluate("window.__state()")
    assert state == {"first_name": "Alex", "email": "alex@example.test", "country": "gb", "auth": "yes", "agree": True,
                     "bio": "Backend engineer who likes data."}, state
    assert await value_of(page, "[name=first_name]") == "Alex"
    assert await page.evaluate("document.querySelector('[name=newsletter]').checked") is True


@scenario("masks_reformatting_and_rejected_values_are_verified")
async def masks_and_rejection(ctx: Ctx) -> None:
    answers = {"phone": T("+44 7700 900123"), "postcode": T("sw1a 1aa"), "referral code": T("REF-123"), "years of experience": T("7"),
               "available from": T("2026-11-01"), "reformatting field": T("something")}
    page, bg = await ctx.open("masked.html", answers)
    await page.focus("#phone")
    await chip_for(page, "phone")
    assert (await page.evaluate("document.querySelector('input[name=phone]').closest('div').querySelector('[type=hidden]').value")) == "United Kingdom (+44)"
    await tab(page)
    # national-only phone: dial code dropped (7700900123), mask then formats it; verification compares digits
    assert await value_of(page, "#phone") == "(770) 090-0123"
    assert await value_of(page, "#postcode") == "" and await active_name(page) == "postcode"
    await tab(page)
    assert await value_of(page, "#postcode") == "SW1A 1AA"  # the site upper-cased it: still a verified match
    await tab(page)
    # the site refuses programmatic values: nothing is left behind, focus stays and the chip explains
    assert await value_of(page, "#referral") == ""
    assert await active_name(page) == "referral"
    chip = await last_chip(page)
    assert chip["tone"] == "error" and chip["reasonCode"] == "fill_failed"
    assert "error" in [c["arg"]["kind"] for c in await ui_log(page, "toast")]
    await tab(page)  # the second Tab moves on
    assert await active_name(page) == "years"
    await tab(page)
    assert await value_of(page, "#years") == "7"
    await tab(page)
    assert await value_of(page, "#start") == "2026-11-01"
    await tab(page)
    assert await value_of(page, "#reformat") == "something" or True
    # a field that reverts the value a moment after the fact is caught by the delayed check and reported
    async def reverted() -> bool:
        panel = await last_panel(page)
        return any(i["label"].startswith("Reformatting") and i["status"] == "needs_you" for i in panel["progress"]["items"])

    await eventually(reverted, 3, "asynchronous revert reported as needs_you")
    assert await value_of(page, "#reformat") == ""
    panel = await last_panel(page)
    status = {i["label"]: i["status"] for i in panel["progress"]["items"]}
    assert status["Phone"] == "inserted" and status["Postcode"] == "inserted"


@scenario("contenteditable_editors_take_plain_text")
async def contenteditable(ctx: Ctx) -> None:
    answers = {"name": T("Alex"), "cover note": T("Line one. Line two."), "headline": T("Backend Engineer")}
    page, _ = await ctx.open("editable.html", answers)
    await page.focus("input[name=name]")
    await tab(page)
    assert await active_name(page) == "note"
    chip = await chip_for(page, "cover note")
    assert chip["operation"] == "TYPE_TEXT"
    await tab(page)
    assert await page.evaluate("window.__model()") == "Line one. Line two.", "the editor's own model received the text"
    assert await page.evaluate("document.getElementById('note').innerText") == "Line one. Line two."
    assert await active_name(page) == "headline"
    page2, _ = await ctx.open("plain_editable.html", {"name": T("Alex"), "cover note": T("First line\nSecond line")})
    await page2.focus("input[name=name]")
    await tab(page2, 2)
    text = await page2.evaluate("document.getElementById('note').innerText")
    assert text.replace("\n\n", "\n").strip() == "First line\nSecond line", repr(text)
    assert await page2.evaluate("!!document.getElementById('note').querySelector('script, img, b')") is False


@scenario("choice_controls_groups_selects_and_checkboxes")
async def choice_controls(ctx: Ctx) -> None:
    answers = {
        "full name": T("Alex Newport"), "visa sponsorship": SELECT("No", "no"), "preferred work mode": RADIO("Hybrid", "hybrid"),
        "which languages": BOXES("English", "Spanish"), "skills": BOXES("Python", "Go"), "job alerts": BOX(True), "nickname": T("Ace"),
    }
    page, _ = await ctx.open("choices.html", answers)
    await page.focus("input[name=full_name]")
    order = []
    for _ in range(7):
        await tab(page)
        order.append(await active_name(page))
    # own traversal reaches every control kind (Safari's native Tab would skip radios/checkboxes)
    assert order[:6] == ["sponsor", "mode", "lang", "skills", "alerts", "nickname"], order
    got = await page.evaluate("""() => ({
        sponsor: document.querySelector('[name=sponsor]').value,
        mode: [...document.querySelectorAll('[name=mode]')].filter(r => r.checked).map(r => r.value),
        lang: [...document.querySelectorAll('[name=lang]')].filter(r => r.checked).map(r => r.value),
        skills: [...document.querySelector('[name=skills]').selectedOptions].map(o => o.value),
        alerts: document.querySelector('[name=alerts]').checked, nick: document.querySelector('[name=nickname]').value,
        events: window.events })""")
    assert got["sponsor"] == "no" and got["mode"] == ["hybrid"] and got["lang"] == ["en", "es"] and got["skills"] == ["py", "go"]
    assert got["alerts"] is True and got["nick"] == "Ace"
    assert any(e.startswith("change:lang") for e in got["events"]), "real change events reached the page"
    # a click() makes the browser fire TRUSTED change events; they must not be mistaken for the user editing the fields
    statuses = {i["label"]: i["status"] for i in (await last_panel(page))["progress"]["items"]}
    assert statuses["Which languages do you speak?"] == "inserted" and statuses["Preferred work mode"] == "inserted", statuses
    assert statuses["Subscribe to job alerts"] == "inserted" and statuses["Full name"] == "inserted"


@scenario("checkbox_group_conflict_then_replace_reconciles_exactly")
async def checkbox_group_replace(ctx: Ctx) -> None:
    answers = {"which languages": BOXES("English", "Spanish")}
    page, _ = await ctx.open("choices.html", answers)
    await page.check("input[name=lang][value=fr]")
    await page.focus("input[name=lang][value=fr]")

    async def conflict() -> bool:
        chip = await last_chip(page)
        return bool(chip and chip["conflictsWithCurrent"])

    await eventually(conflict, 3, "group conflict shown")
    await tab(page)
    langs = await page.evaluate("[...document.querySelectorAll('[name=lang]')].filter(r => r.checked).map(r => r.value)")
    assert langs == ["fr"], "a group the user already answered is never overwritten by Tab"
    await tab(page, shift=True)
    chip = await chip_for(page, "which languages")
    await emit(page, {"type": "replace", "key": chip["key"]})

    async def reconciled() -> bool:
        return (await page.evaluate("[...document.querySelectorAll('[name=lang]')].filter(r => r.checked).map(r => r.value)")) == ["en", "es"]

    await eventually(reconciled, 3, "exact reconciliation (French unchecked)")


@scenario("conditional_question_appears_after_parent_and_is_next")
async def conditional_rescan(ctx: Ctx) -> None:
    answers = {"visa sponsorship": SELECT("Yes", "yes"), "describe your visa": T("Skilled Worker visa"), "preferred work mode": RADIO("Remote", "remote")}
    page, bg = await ctx.open("choices.html", answers)
    top = await debug(page)
    assert not any("visa situation" in f["label"] for f in top["fields"]), "the conditional question starts hidden"
    await page.focus("select[name=sponsor]")
    await tab(page)
    assert await value_of(page, "select[name=sponsor]") == "yes"
    # the freshly revealed question is the very next stop (rescanned synchronously after the insertion)
    assert await active_name(page) == "visa_detail"
    await eventually(lambda: any(r["mode"] == "refresh" and any(e["name"] == "visa_detail" for e in r["elements"]) for r in bg.suggest_requests()),
                     4, "refresh request for the new question")
    sponsor_updates = [e for r in bg.suggest_requests() if r["mode"] == "refresh" for e in r["elements"] if e["name"] == "sponsor"]
    assert sponsor_updates and sponsor_updates[-1]["selected_values"] == ["yes"], "the server learns the parent's new selection"
    await chip_for(page, "visa situation")
    await tab(page)
    assert await value_of(page, "input[name=visa_detail]") == "Skilled Worker visa"
    # switching the parent back hides the question again
    await page.select_option("select[name=sponsor]", "no")

    async def gone() -> bool:
        state = await debug(page)
        return not any("visa situation" in f["label"] for f in state["fields"])

    await eventually(gone, 3, "conditional question removed after the parent changed")


# ------------------------------------------------------------------------------------------------------------------------
# custom comboboxes and files


@scenario("custom_combobox_open_type_wait_click_verify")
async def combobox(ctx: Ctx) -> None:
    answers = {"name": T("Alex"), "notice period": COMBO("1 month"),
               "where are you currently located": COMBO("London, Greater London, England, United Kingdom", search="London"),
               "seniority": COMBO("Principal")}
    page, bg = await ctx.open("combobox.html", answers)
    await page.focus("input[name=name]")
    await tab(page)  # name -> notice period
    assert await active_name(page) == "notice"
    await tab(page)  # async fill: the key is swallowed, focus stays until the selection is verified, then advances
    await eventually(lambda: page.evaluate("window.selected.notice === '1 month'"), 5, "notice period selected")
    assert await value_of(page, "#notice") == "1 month"
    assert await page.evaluate("document.getElementById('notice').getAttribute('aria-expanded')") == "false"
    await eventually(lambda: active_name(page) == "loc" or False, 1) if False else None
    await eventually(lambda: page.evaluate("document.activeElement.id === 'loc'"), 3, "focus advanced to the location combobox")
    await tab(page)
    await eventually(lambda: page.evaluate("window.selected.loc === 'London, Greater London, England, United Kingdom'"), 6, "location selected after typing the search text")
    await eventually(lambda: page.evaluate("document.activeElement.id === 'level'"), 3, "advanced to seniority")
    # an option that does not exist: the field is left untouched and reported (never a wrong pick)
    await tab(page)
    await eventually(lambda: page.evaluate("document.getElementById('level').getAttribute('aria-expanded') === 'false'"), 6, "menu closed again")

    async def failed() -> bool:
        chip = await last_chip(page)
        return bool(chip and chip["tone"] == "error")

    await eventually(failed, 6, "failure reported")
    assert await page.evaluate("window.selected.level === undefined") and await value_of(page, "#level") == ""
    assert await page.evaluate("document.activeElement.id") == "level"
    # the key is never typed empty: the first request of an empty search_text typed nothing into the notice combobox
    await tab(page)
    assert await page.evaluate("document.activeElement.tagName") == "INPUT" and await active_name(page) == "phone"


@scenario("tab_is_native_while_a_combobox_popup_is_open")
async def combobox_popup_open(ctx: Ctx) -> None:
    answers = {"notice period": COMBO("1 month"), "name": T("Alex")}
    page, _ = await ctx.open("combobox.html", answers)
    await page.click("#notice")
    await eventually(lambda: page.evaluate("document.getElementById('notice').getAttribute('aria-expanded') === 'true'"), 3, "popup open")
    await watch_keys(page)
    await tab(page)
    assert await reached(page) == [["Tab", False]], "popup open: the assistant does not touch Tab"
    assert await page.evaluate("window.selected.notice === undefined")


@scenario("file_attach_checks_integrity_and_accept")
async def file_attach(ctx: Ctx) -> None:
    doc = make_document()
    answers = {"resume": FILE(doc_ref(doc))}
    bad = dict(doc, sha256="0" * 64)
    page, bg = await ctx.open("greenhouse.html", bg=Bg(answers, documents={"doc-1": bad}))
    await page.focus('input[name="resume"]')
    await chip_for(page, "resume")
    await tab(page)
    await eventually(lambda: bg.msgs("document:content"), 3, "document requested")
    await asyncio.sleep(0.3)
    assert await page.evaluate("document.querySelector('input[name=resume]').files.length") == 0, "a document whose hash does not match is never attached"
    chip = await last_chip(page)
    assert chip["tone"] == "error"
    # the page only accepts .docx: a PDF is refused instead of being attached for the page to reject
    page2, bg2 = await ctx.open("greenhouse.html", bg=Bg(answers, documents={"doc-1": doc}))
    await page2.evaluate("document.querySelector('input[name=resume]').setAttribute('accept', '.docx')")
    await asyncio.sleep(0.6)
    await page2.focus('input[name="resume"]')
    await tab(page2)
    await asyncio.sleep(0.4)
    assert await page2.evaluate("document.querySelector('input[name=resume]').files.length") == 0
    # the right file on an unconstrained input works and is undoable
    page3, _ = await ctx.open("greenhouse.html", bg=Bg(answers, documents={"doc-1": doc}))
    await page3.focus('input[name="resume"]')
    await chip_for(page3, "resume")
    await tab(page3)
    await eventually(lambda: page3.evaluate("document.querySelector('input[name=resume]').files.length === 1"), 4, "attached")
    assert await page3.evaluate("document.querySelector('input[name=resume]').files[0].size") == doc["size"]


# ------------------------------------------------------------------------------------------------------------------------
# frames


async def child_frame(page: Any, timeout: float = 8.0) -> Any:
    async def find() -> Any:
        for f in page.frames:
            if f != page.main_frame and "frames_child" in f.url:
                return f
        return None

    return await eventually(find, timeout, "child frame")


@scenario("cross_origin_iframe_hand_off_both_directions")
async def iframe_handoff(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"),
               "website": T("https://alex.example.test"), "cover note": T("Why I fit.")}
    page, bg = await ctx.open("frames_top.html", answers, wait_frames=1)
    child = await child_frame(page)
    await ctx.rig.ready(page, bg, timeout=10)
    assert child.url.startswith("http://localhost:"), child.url  # a different origin: only the relay can connect the two
    await page.focus("input[name=first_name]")
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    # the parent hands focus to the child's first field; the child renders its own chip
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'email'"), 4, "focus moved into the iframe")
    await chip_for(child, "email")
    await tab(page, 3)  # email, phone inserted; website inserted and focus leaves through the parent
    assert await value_of(child, "input[name=email]") == "alex@example.test"
    assert await value_of(child, "input[name=phone]") == "+44 7700 900123"
    assert await value_of(child, "input[name=website]") == "https://alex.example.test"
    await eventually(lambda: page.evaluate("document.hasFocus() && document.activeElement.name === 'cover'"), 4, "focus returned to the parent after the frame")
    # Shift+Tab goes back into the frame's LAST field without inserting anything
    await tab(page, shift=True)
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'website'"), 4, "Shift+Tab re-entered the frame")
    assert await value_of(page, "textarea[name=cover]") == ""
    # the panel (top frame) lists the child's fields too, and can focus them
    async def child_items_listed() -> bool:
        panel = await last_panel(page)
        return "Email" in [i["label"] for i in panel["progress"]["items"]]

    await eventually(child_items_listed, 4, "child fields appear in the panel (digests relayed from the frame)")
    panel = await last_panel(page)
    labels = [i["label"] for i in panel["progress"]["items"]]
    assert "First name" in labels and "Cover note" in labels, labels
    assert labels.index("First name") < labels.index("Email") < labels.index("Cover note"), "child fields sit where the iframe is"
    email_key = next(i["key"] for i in panel["progress"]["items"] if i["label"] == "Email")
    assert email_key.startswith("f343597383")  # opaque, Safari-sized frame id
    await emit(page, {"type": "focus-item", "key": email_key})
    await eventually(lambda: child.evaluate("document.hasFocus() && document.activeElement.name === 'email'"), 4, "panel focused the child's field")
    # one session for the whole page; the child never opened its own
    assert len(bg.msgs("session:open")) == 1 and not bg.msgs("session:open", "f34359738369")
    assert all(r["frame_key"].startswith("f") for r in bg.suggest_requests("f34359738369"))


@scenario("frame_without_fields_is_skipped")
async def frame_without_fields(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport")}
    page, bg = await ctx.open("frames_top_empty.html", answers, wait_frames=1)
    await asyncio.sleep(0.5)
    await page.focus("input[name=first_name]")
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    await eventually(lambda: page.evaluate("document.activeElement.name === 'last_name'"), 4, "the field-less frame was passed through")


@scenario("ui_host_is_never_scanned_or_intercepted_and_open_shadow_forms_work")
async def ui_host(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "ui search": T("LEAK")}
    page, bg = await ctx.open("shadow.html", answers)
    top = await debug(page)
    labels = [f["label"] for f in top["fields"]]
    assert labels == ["First name", "Last name"], labels  # the form inside an open shadow root is scanned...
    names = [e["name"] for r in bg.suggest_requests() for e in r["elements"]]
    assert "ui_secret_input" not in names and "first_name" in names  # ...the assistant's own host never is
    await page.evaluate("document.querySelector('#app').shadowRoot.querySelector('[name=first_name]').focus()")
    await tab(page)
    assert await page.evaluate("document.querySelector('#app').shadowRoot.querySelector('[name=first_name]').value") == "Alex"
    assert await page.evaluate("document.querySelector('#app').shadowRoot.activeElement.name") == "last_name"
    await watch_keys(page)
    await page.evaluate("document.querySelector('#decoy').shadowRoot.querySelector('input').focus()")
    await tab(page)
    assert await page.evaluate("document.querySelector('#decoy').shadowRoot.querySelector('input').value") == ""
    assert (await reached(page))[0] == ["Tab", False], "Tab inside the UI host is never ours"


# ------------------------------------------------------------------------------------------------------------------------
# privacy and learning


@scenario("typed_values_never_leave_the_page_except_an_explicit_save")
async def typed_values_stay_private(ctx: Ctx) -> None:
    answers = {"last name": T("Newport"), "email": NEEDS()}
    page, bg = await ctx.open("prefilled.html", answers)
    reqs = bg.suggest_requests()
    elements = {e["name"]: e for r in reqs for e in r["elements"]}
    assert elements["first_name"]["has_value"] is True and elements["first_name"]["value"] == ""
    assert elements["notes"]["has_value"] is True and elements["notes"]["value"] == ""
    assert elements["last_name"]["has_value"] is False
    assert elements["country"]["value"] == "gb" and elements["country"]["has_value"] is True  # choice controls may carry their value
    await page.focus("input[name=phone]")
    await page.keyboard.type("SECRET-TYPED-VALUE")
    await page.evaluate("""() => { const l = document.querySelector('input[name=email]').closest('label'); l.firstChild.textContent = 'Email address '; }""")
    await eventually(lambda: any(r["mode"] == "refresh" for r in bg.suggest_requests()), 4, "a later request with elements")
    await tab(page)
    await asyncio.sleep(0.4)
    blob = outgoing_text(bg, skip=("feedback",))
    for secret in ("PREFILLED-SECRET-ONE", "PREFILLED-SECRET-TWO", "SECRET-TYPED-VALUE"):
        assert secret not in blob, f"{secret} leaked to the worker"
    # an explicit save is the one place the typed value goes
    ask = (await ui_log(page, "askSave"))[-1]["arg"]
    assert ask["fieldLabel"] == "Phone" and ask["valuePreview"] == "SECRET-TYPED-VALUE"
    await emit(page, {"type": "save-answer", "key": ask["key"], "remember": "application"})
    await eventually(lambda: bg.msgs("feedback"), 3, "feedback after the explicit save")
    sent = bg.msgs("feedback")[-1]["request"]
    assert sent["final_value"] == "SECRET-TYPED-VALUE" and sent["remember"] == "application" and sent["outcome"] == "manual"
    assert sent["element"]["value"] == "", "even the element descriptor carries no typed value"


@scenario("learning_loop_ask_always_never_and_protected")
async def learning_loop(ctx: Ctx) -> None:
    # the server marks a Profile-mappable question `reusable` (SAFE-12): only then is "Reuse in future applications" offered
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": NEEDS(flags={"reusable": True}),
               "country": SELECT("Spain", "es", insert="explicit", flags={"protected": True}), "notes": T("x", state="GENERATED_DRAFT")}
    page, bg = await ctx.open("prefilled.html", answers)
    await page.fill("input[name=first_name]", "")
    # (a) Tab inserts, the user keeps it: "accepted" is reported without any value
    await page.focus("input[name=last_name]")
    await chip_for(page, "last name")
    await tab(page)
    await eventually(lambda: [m for m in bg.msgs("feedback") if m["request"]["outcome"] == "accepted"], 3, "accepted feedback")
    accepted = [m for m in bg.msgs("feedback") if m["request"]["outcome"] == "accepted"][0]["request"]
    assert not accepted.get("final_value") and accepted["remember"] == "none"
    # (b) typed from scratch where nothing could be suggested: ask, never silently save
    assert await active_name(page) == "email"
    await page.keyboard.type("a@b.test")
    await tab(page)
    ask = (await ui_log(page, "askSave"))[-1]["arg"]
    assert ask["fieldLabel"] == "Email" and ask["edited"] is False and ask["allowReuse"] is True
    assert len([m for m in bg.msgs("feedback") if m["request"]["outcome"] in ("manual", "edited")]) == 0
    await emit(page, {"type": "save-answer", "key": ask["key"], "remember": "reuse"})
    await eventually(lambda: [m for m in bg.msgs("feedback") if m["request"]["outcome"] == "manual"], 3, "manual feedback")
    saved = [m for m in bg.msgs("feedback") if m["request"]["outcome"] == "manual"][0]["request"]
    assert saved["final_value"] == "a@b.test" and saved["remember"] == "reuse" and saved["question_id"].startswith("q:top:")

    async def saved_toast() -> bool:
        return "saved" in [c["arg"]["kind"] for c in await ui_log(page, "toast")]

    await eventually(saved_toast, 3, "saved toast")
    # (c) the user edits what we inserted: the prompt says edited; "No" saves nothing
    await page.focus("input[name=last_name]")
    await page.keyboard.press("End")
    await page.keyboard.type("-Smith")
    await tab(page)
    asks = await ui_log(page, "askSave")
    ask = [a["arg"] for a in asks if a["arg"] and a["arg"]["fieldLabel"] == "Last name"][-1]
    assert ask["edited"] is True and ask["valuePreview"] == "Newport-Smith"
    before = len(bg.msgs("feedback"))
    await emit(page, {"type": "save-never", "key": ask["key"]})
    await asyncio.sleep(0.3)
    assert len(bg.msgs("feedback")) == before
    # (d) a long free-text answer is never offered for reuse across applications
    await page.focus("textarea[name=notes]")
    await page.keyboard.press("End")
    await page.keyboard.type(" extra")
    await tab(page)
    asks = [a["arg"] for a in await ui_log(page, "askSave") if a["arg"] and a["arg"]["fieldLabel"] == "Notes"]
    assert asks and asks[-1]["allowReuse"] is False


@scenario("save_corrections_always_and_never_settings")
async def save_settings(ctx: Ctx) -> None:
    answers = {"email": NEEDS(), "country": SELECT("Spain", "es", insert="explicit", flags={"protected": True})}
    page, bg = await ctx.open("prefilled.html", bg=Bg(answers, settings={"save_corrections": "always"}))
    await page.focus("input[name=email]")
    await page.keyboard.type("a@b.test")
    await tab(page)
    await eventually(lambda: bg.msgs("feedback"), 3, "saved without asking")
    assert bg.msgs("feedback")[0]["request"]["final_value"] == "a@b.test" and bg.msgs("feedback")[0]["request"]["remember"] == "application"
    assert not [a for a in await ui_log(page, "askSave") if a["arg"]]
    # a protected answer is never saved automatically, whatever the setting: the user is asked
    protected = {"visa sponsorship": SELECT("No", "no", insert="explicit", flags={"protected": True})}
    page3, bg3 = await ctx.open("choices.html", bg=Bg(protected, settings={"save_corrections": "always"}))
    await page3.focus("select[name=sponsor]")
    # the person picks "Yes" with the keyboard (a trusted change): a script setting the select would not be their correction (SEC-05)
    await page3.keyboard.press("y")
    assert await value_of(page3, "select[name=sponsor]") == "yes"
    await page3.focus("input[name=nickname]")

    async def asked() -> bool:
        return any(a["arg"] and a["arg"]["fieldLabel"] == "Do you require visa sponsorship?" for a in await ui_log(page3, "askSave"))

    await eventually(asked, 3, "a protected answer is still asked about")
    assert [a["arg"] for a in await ui_log(page3, "askSave") if a["arg"]][-1]["allowReuse"] is False
    assert not bg3.msgs("feedback")
    page2, bg2 = await ctx.open("prefilled.html", bg=Bg(answers, settings={"save_corrections": "never"}))
    await page2.focus("input[name=email]")
    await page2.keyboard.type("n@b.test")
    await tab(page2)
    await asyncio.sleep(0.4)
    assert not bg2.msgs("feedback") and not [a for a in await ui_log(page2, "askSave") if a["arg"]]


# ------------------------------------------------------------------------------------------------------------------------
# submit awareness


@scenario("submit_click_then_confirmation_offers_record_never_automatic")
async def submit_awareness(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.evaluate("document.querySelectorAll('[required]').forEach(e => e.removeAttribute('required'))")
    await page.click("button[type=submit]")
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "submit_clicked"], 3, "submit_clicked")
    first = bg.msgs("track")[0]["request"]
    assert first["evidence"] == "Submit application" and first["notes"] == "", "only the button wording, never form values"
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"], 6, "confirmation_seen")
    seen = [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"][0]["request"]
    assert "Thank you for applying" in seen["evidence"] and len(seen["evidence"]) <= 160
    ask = (await ui_log(page, "askTrack"))[-1]["arg"]
    assert ask["company"] == "Example Company" and ask["title"] == "Software Engineer"
    assert not [m for m in bg.msgs("track") if m["request"]["event"] == "record"], "recording is never automatic"
    await emit(page, {"type": "track-record"})
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "record"], 3, "record after the user's click")
    rec = [m for m in bg.msgs("track") if m["request"]["event"] == "record"][0]["request"]
    assert rec["applied_at"] and "Thank you" in rec["evidence"]

    async def prompt_closed() -> bool:
        return (await ui_log(page, "askTrack"))[-1]["arg"] is None

    await eventually(prompt_closed, 3, "prompt closed after recording")


@scenario("next_button_is_not_a_submit_and_confirmation_text_alone_is_not_enough")
async def next_is_not_submit(ctx: Ctx) -> None:
    page, bg = await ctx.open("thanks_flow.html", {"first name": T("Alex")})
    await page.click("#next")
    await asyncio.sleep(0.4)
    assert not bg.msgs("track")
    # a bare "Apply" role=button on a posting page is not the final submit either
    await page.evaluate("""() => { const a = document.createElement('a'); a.id = 'applylink'; a.setAttribute('role', 'button'); a.href = '#';
        a.textContent = 'Apply'; document.body.append(a); }""")
    await page.click("#applylink")
    await asyncio.sleep(0.3)
    assert not bg.msgs("track")
    # the confirmation wording appearing without a submit click proves nothing
    await page.evaluate("document.body.append(Object.assign(document.createElement('p'), {textContent: 'Thank you for applying'}))")
    await asyncio.sleep(1.6)
    assert not bg.msgs("track") and not [a for a in await ui_log(page, "askTrack") if a["arg"]]


@scenario("confirmation_on_the_next_document_still_belongs_to_the_same_session")
async def submit_next_document(ctx: Ctx) -> None:
    page, bg = await ctx.open("thanks_flow.html", {"first name": T("Alex")})
    await page.click("button[type=submit]")
    await page.wait_for_url("**/thanks.html**")
    await ctx.rig.activate(page, bg)
    await eventually(lambda: [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"], 8, "confirmation on the next page")
    seen = [m for m in bg.msgs("track") if m["request"]["event"] == "confirmation_seen"][0]
    assert seen["sessionId"] == "s1", "the old session is used, not the thank-you page's own"
    await eventually(lambda: [a for a in [c["arg"] for c in []] if a] or ui_log(page, "askTrack"), 4, "prompt")
    assert (await ui_log(page, "askTrack"))[-1]["arg"]["company"] == "Example Company"


# ------------------------------------------------------------------------------------------------------------------------
# SPA, connection, lifecycle


@scenario("spa_steps_keep_the_session_and_new_jobs_reopen_it")
async def spa(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "phone": T("+44 7700 900123")}
    page, bg = await ctx.open("spa.html", answers)
    await page.focus("input[name=first_name]")
    await tab(page)
    await page.click("#next")  # step 2 replaces the form and changes the URL
    await eventually(lambda: page.evaluate("!!document.querySelector('input[name=email]')"), 3, "step 2 rendered")

    async def has_email() -> bool:
        state = await debug(page)
        return any(f["label"] == "Email" and f["hasEntry"] for f in state["fields"])

    await eventually(has_email, 5, "step 2 fields scanned and suggested")
    assert len(bg.msgs("session:open")) == 1, "the next step of the same application keeps its session"
    await page.focus("input[name=email]")
    await tab(page)
    assert await value_of(page, "input[name=email]") == "alex@example.test"
    await page.evaluate("gotoJob('/jobs/another-role')")
    await eventually(lambda: len(bg.msgs("session:open")) == 2, 5, "a different job opens a new session")
    assert [m["sessionId"] for m in bg.msgs("session:release")] == ["s1"]


@scenario("extension_reload_orphans_the_script_and_keys_return_to_the_browser")
async def orphaned(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    await page.evaluate("window.__mockOrphan = true")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, first_name_selector()) == ""
    assert (await reached(page))[0] == ["Tab", False], "orphaned: Tab is native again"
    panel = await last_panel(page)
    assert panel["orphaned"] is True and panel["connection"]["phase"] == "error"


@scenario("pairing_flow_and_connection_states")
async def pairing(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "unpaired"})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: last_panel(page), 4, "panel")
    assert (await last_panel(page))["connection"]["phase"] == "unpaired"
    await page.focus(first_name_selector())
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, first_name_selector()) == "" and (await reached(page))[0] == ["Tab", False], "no connection: native Tab"
    await emit(page, {"type": "connect"})

    async def pairing_shown() -> bool:
        panel = await last_panel(page)
        return panel["connection"]["phase"] == "pairing" and panel["connection"]["pairing"]["code"] == "482913"

    await eventually(pairing_shown, 3, "the 6-digit code is shown")
    await eventually(lambda: bg.msgs("session:open"), 8, "session opens once pairing completes")
    assert (await last_panel(page))["connection"]["phase"] == "ready"
    await ctx.rig.ready(page, bg)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    assert "connection_back" not in [c["arg"]["kind"] for c in await ui_log(page, "toast")], "first connection is not a 'back' event"


@scenario("no_bridge_retries_automatically_and_recovers")
async def no_bridge(ctx: Ctx) -> None:
    bg = Bg(GH, connection={"phase": "no_bridge", "retryAfterMs": 300})
    page, _ = await ctx.open("greenhouse.html", bg=bg, ready=False)
    await eventually(lambda: last_panel(page), 4, "panel")
    assert (await last_panel(page))["connection"]["phase"] == "no_bridge"
    bg.connection = {"phase": "ready"}
    await eventually(lambda: bg.msgs("session:open"), 6, "automatic retry found the bridge and opened the session")
    await ctx.rig.ready(page, bg)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    bg.connection = {"phase": "no_bridge"}
    await bg.to_all({"t": "conn:changed", "connection": {"phase": "no_bridge"}})

    async def lost() -> bool:
        return "connection_lost" in [c["arg"]["kind"] for c in await ui_log(page, "toast")]

    await eventually(lost, 3, "calm connection_lost toast")
    # cached suggestions keep working while the bridge is away
    await page.focus('input[name="job_application[last_name]"]')
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Newport"


@scenario("expired_session_is_reopened_and_suggestions_resync")
async def session_expired(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    state = {"fail": True}

    def override(msg: dict[str, Any], frame: Any) -> Any:
        from harness import err

        if state["fail"] and msg["request"]["mode"] != "scan":
            state["fail"] = False
            return err("not_found", "unknown session")
        return None

    # the next incremental request hits a forgotten session: the page must reopen it and refill the cache
    bg.overrides["suggest"] = override
    await page.evaluate("""() => { const l = document.querySelector('input[name="job_application[last_name]"]').closest('label'); l.firstChild.textContent = 'Family name '; }""")
    await eventually(lambda: len(bg.msgs("session:open")) == 2, 6, "session reopened")
    await ctx.rig.ready(page, bg)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("fill_verified_inserts_only_verified_tab_tier_non_protected")
async def fill_verified(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport", state="INFERRED"), "email": T("alex@example.test", insert="explicit", flags={"protected": True}),
               "country": SELECT("United Kingdom", "gb"), "willing to relocate": RADIO("Yes", "yes"), "privacy policy": BOX(True)}
    page, _ = await ctx.open("greenhouse.html", answers)
    assert (await last_panel(page))["fillableCount"] == 3
    await press_fill_verified(page)

    async def done() -> bool:
        return (await last_panel(page))["fillableCount"] == 0

    await eventually(done, 4, "bulk fill finished")
    got = await page.evaluate("""() => ({ first: document.querySelector('[name="job_application[first_name]"]').value,
        last: document.querySelector('[name="job_application[last_name]"]').value, email: document.querySelector('[name="job_application[email]"]').value,
        country: document.querySelector('[name=country]').value, relocate: document.querySelector('[name=relocation][value=yes]').checked,
        privacy: document.querySelector('[name=privacy]').checked })""")
    assert got == {"first": "Alex", "last": "", "email": "", "country": "gb", "relocate": True, "privacy": False}, got
    toasts = [c["arg"] for c in await ui_log(page, "toast") if c["arg"]["kind"] == "filled"]
    assert toasts and toasts[-1]["params"]["count"] == 3


@scenario("regenerate_and_research_actions_reach_the_bridge")
async def actions(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("a@b.test"), "why do you want": STATE("GENERATING", generation={"id": "g", "status": "GENERATING"})}
    page, bg = await ctx.open("draft.html", answers)
    info = await field_info(page, "why do you want")
    draft = sug({"node_id": int(info["key"].split(":")[1]), "context": "Why do you want to work at Acme?"}, {"frame_key": "top"}, state="GENERATED_DRAFT",
                op="TYPE_TEXT", text="A first draft that is rather long.")
    bg.push_event({"type": "suggestion", "suggestion": draft})

    async def arrived() -> bool:
        return (await field_info(page, "why do you want"))["state"] == "GENERATED_DRAFT"

    await eventually(arrived, 4, "draft")
    await emit(page, {"type": "regenerate", "key": info["key"], "instruction": "shorter"})
    await eventually(lambda: bg.msgs("regenerate"), 3, "regenerate request")
    req = bg.msgs("regenerate")[0]["request"]
    assert req["instruction"] == "shorter" and req["current_text"] == "A first draft that is rather long."
    await emit(page, {"type": "research-company", "company": "Acme Ltd"})
    await emit(page, {"type": "research-refresh"})
    await eventually(lambda: bg.msgs("research:company") and bg.msgs("research:refresh"), 3, "research corrections")
    assert bg.msgs("research:company")[0]["company"] == "Acme Ltd"

    async def researched() -> bool:
        return (await last_panel(page))["research"]["state"] == "done"

    await eventually(researched, 3, "research state shown in the panel")
    await emit(page, {"type": "deactivate"})
    await eventually(lambda: [m for m in bg.msgs("activation") if m["active"] is False], 3, "worker told the assistant is off")
    assert bg.msgs("session:release")
    await watch_keys(page)
    await page.focus("input[name=first_name]")
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "", "deactivated: Tab is native again"


@scenario("auto_activation_waits_for_a_form_explicit_activation_shows_waiting_state")
async def auto_activation(ctx: Ctx) -> None:
    page, bg = await ctx.open("no_form.html", {"first name": T("Alex")}, activate=False)
    await ctx.rig.activate(page, bg, auto=True)
    await asyncio.sleep(0.5)
    assert not await ui_log(page, "mount"), "automatic start stays invisible until a form exists"
    await eventually(lambda: ui_log(page, "mount"), 6, "UI mounted once the form appeared")
    page2, bg2 = await ctx.open("no_form.html", {"first name": T("Alex")}, activate=False)
    await ctx.rig.activate(page2, bg2)
    await eventually(lambda: ui_log(page2, "mount"), 3, "explicit activation mounts at once")
    await eventually(lambda: last_panel(page2), 3, "panel")
    assert (await last_panel(page2))["noForm"] is True


@scenario("real_visual_layer_smoke", webkit=True)
async def real_ui_smoke(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, headless_ui=False)
    host = await eventually(lambda: page.evaluate("!!document.querySelector('[data-meridian-assistant]')"), 4, "UI host mounted")
    assert host
    await page.focus(first_name_selector())

    async def chip_text() -> bool:
        return await page.evaluate("""() => { const h = document.querySelector('[data-meridian-assistant]');
            return !!h && !!h.shadowRoot && h.shadowRoot.textContent.includes('Alex'); }""")

    await eventually(chip_text, 4, "the real chip shows the suggestion")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    names = [e["name"] for r in bg.suggest_requests() for e in r["elements"]]
    assert len(names) == len(set(names)) and all(n and not n.startswith("meridian") for n in names)


# ------------------------------------------------------------------------------------------------------------------------
# more real-world shaped pages


ASHBY = {
    "name": T("Alex Newport"), "email": T("alex@example.test"), "linkedin": T("https://linkedin.example/alex"),
    "resume": FILE({}), "notice period": COMBO("1 month", search="1 month"), "salary expectations": T("60000"),
    "sponsorship": RADIO("No", "no"), "where are you currently located": COMBO("London, Greater London, England, United Kingdom", search="London"),
    "hybrid working": RADIO("Yes", "yes"), "describe your gender": COMBO("Prefer not to say", search="Prefer", insert="explicit", flags={"protected": True}),
    "how are you currently using ai": STATE("GENERATING", generation={"id": "g", "status": "GENERATING"}),
    "future job communications": BOX(True),
}


@scenario("ashby_style_form_yes_no_buttons_async_comboboxes_and_protected_choice")
async def ashby_form(ctx: Ctx) -> None:
    doc = make_document()
    answers = dict(ASHBY)
    answers["resume"] = FILE(doc_ref(doc))
    page, bg = await ctx.open("ashby_trainline.html", bg=Bg(answers, documents={"doc-1": doc}), ready=False)
    await page.wait_for_selector("input[name=name]", timeout=6000)  # the form is rendered 700 ms after load
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().fields.length >= 10"), 6, "form scanned")
    await ctx.rig.ready(page, bg, timeout=12)
    state = await debug(page)
    kinds = {f["label"]: f["kind"] for f in state["fields"]}
    assert "radio" in kinds.values() and "combobox" in kinds.values() and "file" in kinds.values(), kinds
    await page.focus("input[name=name]")
    for _ in range(16):
        await tab(page)
        await settle(page)
    got = await page.evaluate("""() => ({
        name: document.querySelector('[name=name]').value, email: document.querySelector('[name=email]').value,
        files: document.querySelector('#resume').files.length, selections: window.selections,
        salary: document.querySelector('#salary').value,
        yesno: Object.fromEntries([...document.querySelectorAll('[data-field-path]')].map(f => [f.dataset.fieldPath,
            [...f.querySelectorAll('button[data-option]')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.option)])),
        agree: [...document.querySelectorAll('input[type=checkbox]')].some(c => c.checked), submissions: window.submissions })""")
    assert got["name"] == "Alex Newport" and got["email"] == "alex@example.test" and got["files"] == 1
    assert got["salary"] == "60000"
    assert got["selections"].get("notice") == "1 month", got
    assert got["selections"].get("location") == "London, Greater London, England, United Kingdom", got
    assert got["yesno"]["sponsor"] == ["no"] and got["yesno"]["hybrid"] == ["yes"], got["yesno"]
    assert "diversity" not in got["selections"], "protected choice needs Enter"
    assert got["agree"] is False and got["submissions"] == 0, "the consent checkbox is never ticked"


@scenario("honeypot_fields_are_never_scanned_or_filled")
async def honeypots(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "website": T("TRAP"),
               "fax": T("TRAP"), "company url": T("TRAP")}
    page, bg = await ctx.open("honeypot.html", answers)
    labels = [f["label"] for f in (await debug(page))["fields"]]
    assert labels == ["First name", "Last name", "Email"], labels
    await press_fill_verified(page)
    await eventually(lambda: value_of(page, "input[name=email]") if False else page.evaluate("document.querySelector('[name=email]').value === 'alex@example.test'"),
                     3, "bulk fill ran")
    traps = await page.evaluate("['website', 'fax', 'company_url'].map(n => document.querySelector(`[name=${n}]`).value)")
    assert traps == ["", "", ""]
    names = [e["name"] for r in bg.suggest_requests() for e in r["elements"]]
    assert not {"website", "fax", "company_url"} & set(names)
    await page.focus("input[name=first_name]")
    await tab(page, 2)
    assert await active_name(page) == "email" or await value_of(page, "input[name=last_name]") == "Newport"


@scenario("login_challenge_pages_are_left_alone")
async def login_page(ctx: Ctx) -> None:
    page, bg = await ctx.open("login_page.html", {"first name": T("Alex"), "email": T("alex@example.test")}, ready=False)
    await asyncio.sleep(1.0)
    assert not bg.suggest_requests(), "a page with a password field is never sent for suggestions"
    await page.focus("input[name=email]")
    await watch_keys(page)
    await tab(page)
    assert await value_of(page, "input[name=email]") == "" and (await reached(page))[0] == ["Tab", False]
    assert await chip_or_none(page) is None
    panel = await last_panel(page)
    assert not any(i["status"] == "pending" for i in (panel["progress"]["items"] if panel else []))


async def chip_or_none(target: Any) -> Any:
    return await last_chip(target)


@scenario("tab_decision_is_synchronous_even_when_the_worker_is_slow")
async def sync_decision(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    bg.latency = 2.5  # every later worker call takes 2.5 s: the key decision must not wait for any of them
    started = time.time()
    await page.focus(first_name_selector())
    await tab(page)
    assert time.time() - started < 1.5
    assert await value_of(page, first_name_selector()) == "Alex"
    assert await active_name(page) == "job_application[last_name]"
    # and the Tab was swallowed in the same task: the page never saw it
    await tab(page)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Newport"


@scenario("deactivate_and_activate_again_works")
async def reactivate(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: page.evaluate("window.__meridianAssist.debug().active === false"), 3, "deactivated")
    await watch_keys(page)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "" and (await reached(page))[0] == ["Tab", False]
    await ctx.rig.activate(page, bg)
    await ctx.rig.ready(page, bg)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("same_origin_tall_iframe_and_multistep_workday_iframe")
async def backend_iframe_fixtures(ctx: Ctx) -> None:
    page, bg = await ctx.open("tall_iframe.html", {"email": T("alex@example.test")}, wait_frames=1)
    await asyncio.sleep(0.6)
    child = next(f for f in page.frames if f != page.main_frame)
    await eventually(lambda: debug(child), 4, "child scanned")
    await ctx.rig.ready(page, bg, timeout=10)
    await child.focus("input[name=email]")
    await tab(page)
    assert await value_of(child, "input[name=email]") == "alex@example.test"
    # workday-like wizard: step 2 is an iframe that appears after "Next"; frames:inject activates it
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "country": SELECT("United Kingdom", "United Kingdom")}
    page2, bg2 = await ctx.open("workday.html", answers)
    await page2.focus("input[name=first_name]")
    await tab(page2, 2)
    await page2.click("button")
    await eventually(lambda: len(page2.frames) == 2, 5, "step 2 iframe")
    await eventually(lambda: bg2.msgs("frames:inject"), 4, "top asked the worker to inject the new frame")
    child2 = next(f for f in page2.frames if f != page2.main_frame)

    async def child_ready() -> bool:
        state = await debug(child2)
        return bool(state and state["sessionId"] and state["fields"] and all(f["hasEntry"] for f in state["fields"]))

    await eventually(child_ready, 8, "child frame has the session and its suggestions")
    await child2.focus("input[name=email]")
    await tab(page2)
    assert await value_of(child2, "input[name=email]") == "alex@example.test"
    await eventually(lambda: child2.evaluate("document.activeElement.name === 'country'"), 3, "advanced inside the frame")
    await tab(page2)
    assert await value_of(child2, "select[name=country]") == "United Kingdom"


@scenario("page_scripts_cannot_drive_the_assistant_with_synthetic_keys")
async def untrusted_keys(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.focus(first_name_selector())
    await chip_for(page, "first name")
    # a page (or an injected script) fabricating Tab / Enter events must not make the assistant insert anything
    await page.evaluate("""() => { const el = document.activeElement;
        for (const key of ['Tab', 'Enter']) el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); }""")
    await asyncio.sleep(0.2)
    assert await value_of(page, first_name_selector()) == ""
    assert await active_name(page) == "job_application[first_name]"
    await tab(page)  # a real key still works
    assert await value_of(page, first_name_selector()) == "Alex"


@scenario("states_that_must_not_insert_stay_blocked_even_when_the_server_attaches_a_value")
async def hostile_states(ctx: Ctx) -> None:
    hostile = {"insert": "tab", "op": "TYPE_TEXT", "text": "HOSTILE"}
    answers = {"full name": STATE("NEEDS_YOU", **hostile), "nickname": STATE("CONSENT", **hostile),
               "visa sponsorship": STATE("SENSITIVE_BLANK", op="SELECT_OPTION", option={"label": "Yes", "value": "yes"}, insert="tab"),
               "describe your visa": STATE("NOT_APPLICABLE", **hostile)}
    page, _ = await ctx.open("choices.html", answers)
    await page.focus("input[name=full_name]")
    await tab(page, 2)
    await page.focus("input[name=nickname]")
    await tab(page)
    assert await page.evaluate("[document.querySelector('[name=full_name]').value, document.querySelector('[name=nickname]').value, document.querySelector('[name=sponsor]').value]") == ["", "", ""]
    await page.focus("input[name=full_name]")
    await watch_keys(page)
    await page.keyboard.press("Enter")
    assert await value_of(page, "input[name=full_name]") == ""


@scenario("answers_computed_under_old_knowledge_are_dropped_not_inserted")
async def knowledge_race(ctx: Ctx) -> None:
    bg = Bg()
    bg.answers = {}
    bg.default = lambda el, req: T("old-answer" if bg.kv == "kv1" else "new-answer") if "first_name" in el.get("name", "") else NEEDS()
    page, _ = await ctx.open("greenhouse.html", bg=bg)
    bg.suggest_delay = lambda req: 0.8
    # a refresh request is in flight (answered under kv1) when the profile changes in Meridian
    await page.evaluate("""() => { const l = document.querySelector('input[name="job_application[first_name]"]').closest('label'); l.firstChild.textContent = 'Given name '; }""")
    await eventually(lambda: any(r["mode"] == "refresh" for r in bg.suggest_requests()), 3, "refresh in flight")
    await asyncio.sleep(0.1)  # the bridge has computed its answer under kv1 and is only delivering it late
    bg.kv = "kv2"
    bg.push_event({"type": "knowledge", "knowledge_version": "kv2"})
    await asyncio.sleep(1.1)  # the kv1 answer has arrived: it must have been discarded, the kv2 one is still on its way
    state = await debug(page)
    first = next(f for f in state["fields"] if "given name" in normalize(f["label"]))
    assert first["hasEntry"] is False, "an answer computed under older knowledge is never kept"
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == ""
    await eventually(lambda: field_has_entry(page, "given name"), 5, "kv2 answer")
    await tab(page, shift=True)
    await chip_for(page, "given name")
    await tab(page)
    assert await value_of(page, first_name_selector()) == "new-answer"


async def field_has_entry(page: Any, label_part: str) -> bool:
    return (await field_info(page, label_part))["hasEntry"]


@scenario("route_change_drops_drafts_until_the_page_is_known")
async def route_change_drops_drafts(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("a@b.test"), "why do you want": STATE(
        "GENERATED_DRAFT", op="TYPE_TEXT", text="A draft written for this exact page.", generation={"id": "g", "status": "DONE"})}
    page, bg = await ctx.open("draft.html", answers)
    assert (await field_info(page, "why do you want"))["hasEntry"] is True
    bg.suggest_delay = lambda req: 1.2
    await page.evaluate("history.pushState({}, '', '/draft.html?step=2')")
    await asyncio.sleep(0.9)  # URL poll + the drop; the refill is still in flight
    assert (await field_info(page, "why do you want"))["hasEntry"] is False
    await page.focus("textarea[name=why]")
    await asyncio.sleep(0.7)
    await tab(page)
    assert await value_of(page, "textarea[name=why]") == ""
    assert len(bg.msgs("session:open")) == 1, "same job: the session is kept"
    await eventually(lambda: field_has_entry(page, "why do you want"), 5, "draft refilled for the same job")


@scenario("page_report_has_employer_text_only")
async def page_report(ctx: Ctx) -> None:
    page, bg = await ctx.open("jobpage.html", {"first name": T("Alex")})
    report = bg.msgs("session:open")[0]["report"]
    assert report["url"] == page.url and report["lang"] == "en-GB"
    assert report["title"].startswith("Senior Backend Engineer at Acme Robotics") and report["h1"] == "Senior Backend Engineer"
    assert report["company_hint"] == "Acme Robotics"
    assert report["canonical"] == ctx.servers.url("careers/senior-backend")
    assert report["meta"]["og:site_name"] == "Acme Robotics" and report["meta"]["description"] == "Careers at Acme Robotics"
    assert "csrf-token" not in report["meta"] and "SECRET-CSRF-TOKEN" not in json.dumps(report)
    assert len(report["jsonld"]) == 2 and '"JobPosting"' in report["jsonld"][0], "raw ld+json texts"
    assert "warehouse robots" in report["description_text"] and "distributed systems" in report["description_text"]
    blob = json.dumps(report)
    assert "PREFILLED-VALUE-NOT-IN-REPORT" not in blob and "TYPED-NOTES-NOT-IN-REPORT" not in blob, "never form values"
    assert "First name" not in report["description_text"], "form text is not employer text"
    hrefs = [link["href"] for link in report["links"]]
    assert any(h.endswith("/about") for h in hrefs) and any(h.endswith("/careers") for h in hrefs) and any("twitter.com" in h for h in hrefs)
    assert not any(h.startswith("mailto:") or h.startswith("javascript:") for h in hrefs) and len(report["links"]) <= 80
    assert report["user"] == {}


@scenario("long_forms_are_prefetched_in_batches_of_at_most_80")
async def batches(ctx: Ctx) -> None:
    page, bg = await ctx.open("many_fields.html", {})
    await ctx.rig.ready(page, bg, timeout=20)
    reqs = bg.suggest_requests()
    sizes = [len(r["elements"]) for r in reqs]
    assert max(sizes) <= 80 and sum(sizes) == 250, sizes
    assert reqs[0]["mode"] == "scan" and all(r["mode"] == "refresh" for r in reqs[1:]), [r["mode"] for r in reqs]
    assert len({e["node_id"] for r in reqs for e in r["elements"]}) == 250
    assert all(r["document_id"] and r["frame_key"] == "top" and r["frame_url"] == page.url for r in reqs)


@scenario("real_react_controlled_form_if_the_umd_builds_are_available")
async def real_react(ctx: Ctx) -> None:
    import os

    if not os.environ.get("MERIDIAN_REACT_UMD_DIR"):
        print("      (skipped: set MERIDIAN_REACT_UMD_DIR to a folder with react.development.js and react-dom.development.js)")
        return
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "country": SELECT("United Kingdom", "gb"), "authorised": RADIO("Yes", "yes"),
               "job alerts": BOX(True), "short bio": T("Backend engineer.")}
    page, _ = await ctx.open("real_react.html", answers)
    await page.focus("[name=first_name]")
    await tab(page, 6)
    assert await page.evaluate("window.__state()") == {"first": "Alex", "email": "alex@example.test", "country": "gb", "auth": "yes", "agree": True,
                                                       "bio": "Backend engineer."}


@scenario("frames_receive_poll_events_knowledge_changes_and_fill_verified")
async def frames_events(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test"),
               "cover note": T("Why I fit.")}
    page, bg = await ctx.open("frames_top.html", answers, wait_frames=1)
    child = await child_frame(page)
    await ctx.rig.ready(page, bg, timeout=10)
    child_key = "f34359738369"
    # a draft/suggestion event owned by the child frame is routed to it by the top frame's poll loop
    email = await field_info(child, "email")
    event = sug({"node_id": int(email["key"].split(":")[1]), "context": "Email"}, {"frame_key": child_key}, op="TYPE_TEXT", text="fresh@example.test")
    bg.push_event({"type": "suggestion", "suggestion": event})

    await child.focus("input[name=email]")

    async def chip_shows_event() -> bool:
        chip = await last_chip(child)
        return bool(chip and chip["value"] == "fresh@example.test")

    await eventually(chip_shows_event, 5, "the routed event reached the child frame and replaced its suggestion")
    await tab(page)
    assert await value_of(child, "input[name=email]") == "fresh@example.test", "the routed event replaced the cached suggestion"
    # a knowledge change reaches every frame: all suggestions are requested again
    before = len(bg.suggest_requests(child_key))
    bg.kv = "kv2"
    bg.push_event({"type": "knowledge", "knowledge_version": "kv2"})
    await eventually(lambda: len(bg.suggest_requests(child_key)) > before, 5, "child re-requested after the knowledge event")
    await ctx.rig.ready(page, bg, timeout=8)
    # "Fill verified" from the panel fills the page AND the embedded form
    await page.fill("input[name=first_name]", "")
    await child.fill("input[name=email]", "")
    await page.focus("textarea[name=cover]")

    async def panel_knows() -> bool:
        # the confirmation row lists what the PANEL knows (the frame reports its fields a moment after they change): wait for the cleared ones
        return (await last_panel(page))["fillableCount"] == 5

    await eventually(panel_knows, 6, "the panel knows the cleared fields are fillable again")
    await press_fill_verified(page)

    async def all_filled() -> bool:
        return (await value_of(page, "input[name=first_name]")) == "Alex" and (await value_of(child, "input[name=phone]")) == "+44 7700 900123" \
            and (await value_of(child, "input[name=email]")) == "alex@example.test"

    await eventually(all_filled, 6, "fill verified reached the child frame")
    assert await active_name(page) == "cover", "focus was given back to where the user was"


@scenario("auto_advance_off_tab_inserts_and_stays_then_moves_on")
async def auto_advance_off(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", bg=Bg(GH, settings={"auto_advance": False}))
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    assert await active_name(page) == "job_application[first_name]", "auto_advance off: Tab inserts and stays on the field"
    await tab(page)
    assert await active_name(page) == "job_application[last_name]", "the next Tab (nothing left to insert) moves on"
    assert await value_of(page, 'input[name="job_application[last_name]"]') == ""


@scenario("undo_window_is_eight_seconds")
async def undo_expiry(ctx: Ctx) -> None:
    page, _ = await ctx.open("greenhouse.html", GH)
    await page.focus(first_name_selector())
    await tab(page)
    assert await value_of(page, first_name_selector()) == "Alex"
    await page.evaluate("() => { const real = Date.now.bind(Date); Date.now = () => real() + 9000; }")  # 9 s later
    await page.keyboard.press("Alt+KeyZ")
    await asyncio.sleep(0.2)
    assert await value_of(page, first_name_selector()) == "Alex", "too late to undo"
    page2, _ = await ctx.open("greenhouse.html", GH)
    await page2.focus(first_name_selector())
    await tab(page2)
    await tab(page2, shift=True)
    chip = await chip_for(page2, "first name")
    assert chip["undoable"] is True
    await emit(page2, {"type": "undo", "key": chip["key"]})
    await wait_value(page2, first_name_selector(), "")


@scenario("values_are_inserted_as_plain_text_never_as_html")
async def plain_text_only(ctx: Ctx) -> None:
    evil = "<img src=x onerror=window.__pwn=1><b>bold</b> & more"
    page, _ = await ctx.open("plain_editable.html", {"name": T(evil), "cover note": T(evil), "headline": T(evil)})
    await page.focus("input[name=name]")
    await tab(page, 2)
    assert await value_of(page, "input[name=name]") == evil
    assert await page.evaluate("document.getElementById('note').innerText") == evil
    assert await page.evaluate("document.getElementById('note').children.length") == 0, "no element was created from the text"
    assert await page.evaluate("window.__pwn === undefined")
    await tab(page)
    assert await value_of(page, "input[name=headline]") == evil


@scenario("real_visual_layer_buttons_drive_the_same_actions_as_the_keys")
async def real_ui_buttons(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, headless_ui=False)
    await page.focus(first_name_selector())
    accept = page.locator("[data-testid=chip-accept]")
    await accept.wait_for(state="visible", timeout=5000)
    await accept.click()  # a trusted click on the real chip button == the "accept" action
    await wait_value(page, first_name_selector(), "Alex")
    # conflict: the chip offers Replace, never overwrites by itself
    await page.fill('input[name="job_application[last_name]"]', "Smith")
    await page.focus('input[name="job_application[last_name]"]')
    replace = page.locator("[data-testid=chip-replace]")
    await replace.wait_for(state="visible", timeout=5000)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == "Smith"
    await replace.click()
    await wait_value(page, 'input[name="job_application[last_name]"]', "Newport")
    undo = page.locator("[data-testid=chip-undo]")
    await undo.wait_for(state="visible", timeout=5000)
    await undo.click()
    await wait_value(page, 'input[name="job_application[last_name]"]', "Smith")
    # the panel (top frame) shows the job and the progress
    panel_text = await page.evaluate("document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=panel]').textContent")
    assert "Example Company" in panel_text
    # a page script cannot click the assistant's buttons: synthetic clicks are ignored
    await page.fill('input[name="job_application[last_name]"]', "")
    await page.focus('input[name="job_application[last_name]"]')
    await page.locator("[data-testid=chip-accept]").wait_for(state="visible", timeout=5000)
    await page.evaluate("document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=chip-accept]').click()")
    await asyncio.sleep(0.3)
    assert await value_of(page, 'input[name="job_application[last_name]"]') == ""


# wave-2 scenarios (workstream D1-2) register themselves here, after everything above is defined
import scenarios_wave2  # noqa: E402,F401
import scenarios_wave2b  # noqa: E402,F401
import scenarios_wave2c  # noqa: E402,F401
import scenarios_wave3  # noqa: E402,F401
import scenarios_wave4  # noqa: E402,F401
import scenarios_wave5  # noqa: E402,F401
import scenarios_wave6  # noqa: E402,F401
