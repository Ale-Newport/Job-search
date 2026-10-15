"""Wave-2c scenarios of the content-script rig (workstream D1c): the learning loop that no longer depends on the suggestion cache, the
Radix trigger drawn with a TEXT chevron, hidden file inputs entered through their drop area, the Safari tab-stop walk, the pending-submit
hop count, and the performance / hostile-DOM review. Same rules as scenarios.py: REAL key presses, view models handed to the UI and the
messages the content script sent. Every scenario here fails without its fix (the report lists the mutation that proves it).
"""

from __future__ import annotations

import asyncio
from typing import Any

from harness import BOXES, COMBO, FILE, NEEDS, RADIO, SELECT, T, Bg, active_name, chip_for, debug, emit, eventually, make_document, tab, ui_log
from scenarios import Ctx, field_info, reached, scenario, watch_keys


async def asks(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "askSave") if c["arg"]]


class Gate:
    """Holds every `suggest` answer of the mock worker until `open()`: the window in which the page has no answer is as long as the test
    needs (no sleeping, no race with the machine's speed). The answer is computed when the gate opens, i.e. under the knowledge of THEN."""

    def __init__(self, bg: Bg) -> None:
        self.event = asyncio.Event()
        bg.overrides["suggest"] = self.hold

    async def hold(self, msg: dict[str, Any], frame: Any) -> None:
        await self.event.wait()

    def open(self) -> None:
        self.event.set()


async def ask_for(page: Any, label: str) -> dict[str, Any] | None:
    return next((a for a in await asks(page) if a["fieldLabel"] == label), None)


async def blur(page: Any) -> None:
    await page.evaluate("document.activeElement && document.activeElement.blur()")


# ------------------------------------------------------------------------------------------------------------------------
# 1. the learning loop does not depend on the suggestion cache


@scenario("a_correction_typed_while_meridians_knowledge_changes_still_gets_its_save_prompt")
async def correction_in_the_knowledge_window(ctx: Ctx) -> None:
    """Saving a correction changes Meridian's knowledge: every cached suggestion is dropped and asked for again. A field the person typed
    into and left inside that window had no suggestion any more, so the loop silently returned and no prompt ever appeared."""
    bg = Bg({"email": NEEDS(), "phone": NEEDS()})
    page, bg = await ctx.open("prefilled.html", bg=bg)
    await page.focus("input[name=email]")
    await chip_for(page, "email")
    await page.keyboard.type("a@b.test")
    await tab(page)
    first = (await asks(page))[-1]
    assert first["fieldLabel"] == "Email"
    await page.focus("input[name=phone]")
    await chip_for(page, "phone")
    await page.keyboard.type("+44 7700 900123")
    # the person answers the prompt: Meridian saves and its knowledge moves on; the new answers are slow to arrive
    gate = Gate(bg)
    await emit(page, {"type": "save-answer", "key": first["key"], "remember": "application"})
    await eventually(lambda: bg.msgs("feedback"), 4, "the first correction was saved")
    bg.kv = "kv2"
    bg.push_event({"type": "knowledge", "knowledge_version": "kv2"})

    async def window_is_open() -> bool:
        state = await debug(page)
        phone = next(f for f in state["fields"] if f["label"] == "Phone")
        return state["knowledge"] == "kv2" and phone["hasEntry"] is False

    await eventually(window_is_open, 6, "the cache was dropped and nothing has been answered yet")
    await blur(page)  # inside the window
    prompt = await eventually(lambda: ask_for(page, "Phone"), 4, "save prompt for Phone")
    assert prompt["valuePreview"] == "+44 7700 900123" and prompt["edited"] is False
    gate.open()
    await ctx.rig.ready(page, bg)
    assert len([a for a in await asks(page) if a["fieldLabel"] == "Phone"]) == 1, "the late answers do not ask a second time"
    await emit(page, {"type": "save-answer", "key": prompt["key"], "remember": "reuse"})
    await eventually(lambda: len(bg.msgs("feedback")) == 2, 4, "the second correction was saved")
    sent = bg.msgs("feedback")[-1]["request"]
    assert sent["final_value"] == "+44 7700 900123" and sent["outcome"] == "manual" and sent["question_id"].startswith("q:top:")
    assert sent["element"]["name"] == "phone" and sent["element"]["value"] == "", "never the typed value inside the element descriptor"


@scenario("a_field_left_before_meridian_has_answered_for_it_is_decided_when_the_answer_arrives")
async def left_before_the_answer(ctx: Ctx) -> None:
    """The very first suggestion of a field is still on its way when the person has typed and left it: nothing is known about the
    question yet, so the decision waits for the answer instead of being dropped."""
    bg = Bg({"email": NEEDS(), "phone": NEEDS()})
    gate = Gate(bg)
    page, bg = await ctx.open("prefilled.html", bg=bg, ready=False)
    await eventually(lambda: bg.suggest_requests(), 6, "the first request is held by the worker")
    await page.focus("input[name=phone]")
    await page.keyboard.type("+44 7700 900123")
    await blur(page)
    await eventually(lambda: waiting_count(page, 1), 4, "the left field waits for its answer")
    assert not await asks(page), "nothing can be offered before the question is known"
    gate.open()
    prompt = await eventually(lambda: ask_for(page, "Phone"), 6, "save prompt once the answer is known")
    assert prompt["valuePreview"] == "+44 7700 900123"
    assert (await debug(page))["retained"]["learning"]["waiting"] == 0
    # a field that was left UNCHANGED while the answer was pending is never offered
    gate.event.clear()
    await page.focus("input[name=email]")
    await blur(page)
    await asyncio.sleep(0.2)
    gate.open()
    assert not await ask_for(page, "Email")


async def waiting_count(page: Any, n: int) -> bool:
    return (await debug(page))["retained"]["learning"]["waiting"] == n


@scenario("a_field_left_in_one_session_is_never_decided_by_the_answers_of_another_job")
async def left_then_another_job(ctx: Ctx) -> None:
    """A question id belongs to the session that issued it: a correction typed for one job is never saved against the next one,
    even when the same control is still on the page."""
    bg = Bg({"email": NEEDS(), "phone": NEEDS()})
    gate = Gate(bg)
    page, bg = await ctx.open("job_switch.html", bg=bg, ready=False)
    await eventually(lambda: bg.suggest_requests(), 6, "the first request is held")
    await page.focus("input[name=phone]")
    await page.keyboard.type("typed for job A")
    await blur(page)
    await eventually(lambda: waiting_count(page, 1), 4, "waiting for the answer of job A")
    await page.evaluate("window.becomeAnotherJob()")
    await eventually(lambda: len(bg.msgs("session:open")) >= 2, 10, "a second session for the other job")
    gate.open()
    await ctx.rig.ready(page, bg, timeout=12)
    await asyncio.sleep(0.4)
    assert not await asks(page), "the correction typed for job A is not offered on job B"
    assert not bg.msgs("feedback")


# ------------------------------------------------------------------------------------------------------------------------
# 2. a Radix trigger drawn with a TEXT chevron is empty while it shows its placeholder


async def radix_state(page: Any) -> dict[str, Any]:
    return await page.evaluate("({picked: window.picked, text: document.getElementById('rx_fruit').innerText, open: document.getElementById('rx_fruit').getAttribute('aria-expanded')})")


@scenario("a_radix_trigger_with_a_text_chevron_is_empty_while_it_shows_its_placeholder_and_is_filled_by_tab")
async def radix_text_chevron(ctx: Ctx) -> None:
    """`<button role=combobox data-placeholder><span>Select a fruit…</span><span aria-hidden>▼</span></button>`: innerText is 'Select a fruit…▼',
    which read as an answer the person had already given, so Tab neither filled the field nor said why."""
    answers = {"first name": T("Alex"), "favourite fruit": COMBO("Banana", "Banana"), "last name": T("Newport")}
    for variant in ("", "?marker=0", "?chevron=leading", "?chevron=svg", "?ph=Any%20size"):
        page, bg = await ctx.open("radix_text_icon.html" + variant, answers)
        fruit = await field_info(page, "favourite fruit")
        assert fruit["kind"] == "combobox" and fruit["hasEntry"], (variant, fruit)
        reqs = [e for r in bg.suggest_requests() for e in r["elements"] if e.get("id") == "rx_fruit"]
        assert reqs and reqs[0]["selected_text"] == "" and reqs[0]["has_value"] is False, (variant, reqs[:1])
        await page.focus("#rx_fruit")
        chip = await chip_for(page, "favourite fruit")
        assert chip["conflictsWithCurrent"] is False and "tab" in chip["keys"], (variant, chip)
        await tab(page)
        await eventually(lambda: radix_picked(page, "banana"), 8, f"the option was chosen ({variant or 'plain'})")
        field = await field_info(page, "favourite fruit")
        assert not field["failed"] and field["inserted"] == "Banana", (variant, field)
        # what it shows now is its answer, whatever the chevron is drawn with
        state = await radix_state(page)
        assert state["text"].replace("▼", "").strip() == "Banana", (variant, state)
        await eventually(lambda: active_is(page, "last"), 8, f"focus moved on ({variant or 'plain'})")


async def radix_picked(page: Any, value: str) -> bool:
    return (await radix_state(page))["picked"] == value


async def active_is(page: Any, name: str) -> bool:
    return (await active_name(page)) == name


@scenario("a_radix_trigger_that_already_holds_an_answer_is_never_overwritten_whatever_its_chevron_is_drawn_with")
async def radix_text_chevron_filled(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "favourite fruit": COMBO("Banana", "Banana"), "last name": T("Newport")}
    # the last variant keeps a text-valued data-placeholder (a SETTING of some libraries) on a widget that shows a real answer
    for variant in ("?value=Cherry", "?value=Cherry&dp=Pick%20a%20fruit"):
        page, bg = await ctx.open("radix_text_icon.html" + variant, answers)
        await page.focus("#rx_fruit")
        chip = await chip_for(page, "favourite fruit")
        assert chip["conflictsWithCurrent"] is True, variant
        await tab(page)
        await asyncio.sleep(0.3)
        assert (await radix_state(page))["picked"] == "cherry", f"the person's own answer stays ({variant})"
        assert (await field_info(page, "favourite fruit"))["inserted"] is None, variant
        if not variant.endswith("fruit"):  # scan.ts still reads ANY data-placeholder attribute as "empty" (hand-off to its owner)
            reqs = [e for r in bg.suggest_requests() for e in r["elements"] if e.get("id") == "rx_fruit"]
            assert reqs[0]["has_value"] is True, "the scanner reports the answer too"


# ------------------------------------------------------------------------------------------------------------------------
# 3. a pending submit travels at most four DOCUMENTS: switching the assistant off and on in one of them is not a hop


async def is_active(page: Any, active: bool) -> bool:
    state = await debug(page)
    return bool(state) and state["active"] is active


@scenario("switching_the_assistant_off_and_on_in_one_document_is_not_a_hop_of_the_pending_submit")
async def pending_hops_are_documents(ctx: Ctx) -> None:
    """The hop count rose on every activation, so toggling the assistant five times on the thank-you page dropped the record (and the
    prompt with it) although no navigation had happened."""
    from scenarios_wave2b import track_prompts, worker_pending

    bg = Bg({}, pending=worker_pending(hops=0))
    page, bg = await ctx.open("thanks.html", bg=bg, activate=False)
    for round_ in range(6):
        await ctx.rig.activate(page, bg, auto=True)
        await eventually(lambda: is_active(page, True), 6, f"switched on ({round_})")
        await eventually(lambda: len(bg.msgs("pending:set")) > round_, 6, f"the record was handed back ({round_})")
        await bg.to_all({"t": "deactivate"})
        await eventually(lambda: is_active(page, False), 6, f"switched off ({round_})")
    hops = [m["record"]["hops"] for m in bg.msgs("pending:set")]
    assert hops == [1] * len(hops), f"one document, one hop: {hops}"
    assert not bg.msgs("pending:clear"), "the record is still alive"
    await ctx.rig.activate(page, bg, auto=True)
    await eventually(lambda: track_prompts(page), 8, "and the prompt is still offered on this page")
    # a real navigation is a hop
    page2, bg2 = await ctx.open("thanks.html", bg=Bg({}, pending=bg.pending), activate=False)
    await ctx.rig.activate(page2, bg2, auto=True)
    await eventually(lambda: bg2.msgs("pending:set"), 6, "the next document took the record over")
    assert bg2.msgs("pending:set")[-1]["record"]["hops"] == 2


# ------------------------------------------------------------------------------------------------------------------------
# 4. a file input that cannot take focus is entered through its drop area


DOC_REF = {k: make_document()[k] for k in ("id", "filename", "mime", "size", "sha256")}


async def dropzone_page(ctx: Ctx, **kw: Any) -> tuple[Any, Bg]:
    doc = make_document()
    ref = {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}
    answers = {"first name": T("Alex"), "resume": FILE(ref), "cover letter": FILE(ref), "last name": T("Newport")}
    return await ctx.open("dropzone_hidden.html", answers, bg=Bg(answers, documents={"doc-1": doc}), **kw)


async def current_key(page: Any) -> str | None:
    return (await debug(page))["current"]


async def attached(page: Any) -> dict[str, Any]:
    return await page.evaluate("window.attached")


@scenario("tab_enters_a_file_field_whose_input_cannot_take_focus_through_its_drop_area_and_attaches_from_there")
async def dropzone_by_tab(ctx: Ctx) -> None:
    page, bg = await dropzone_page(ctx)
    assert [f["kind"] for f in (await debug(page))["fields"]] == ["text", "file", "file", "text"], await debug(page)
    await page.focus("input[name=first]")
    await chip_for(page, "first name")
    await tab(page)  # inserts Alex, then the next stop is the Resume drop area
    assert await page.evaluate("document.querySelector('input[name=first]').value") == "Alex"
    chip = await chip_for(page, "resume")
    assert chip["operation"] == "ATTACH_FILE" and "tab" in chip["keys"]
    assert await page.evaluate("document.activeElement.id") == "cv", "focus is on the visible drop area"
    zone = await page.evaluate("(() => { const r = document.getElementById('cv').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()")
    assert abs(chip["anchor"]["x"] - zone["x"]) <= 2 and chip["anchor"]["w"] > 100, (chip["anchor"], zone)
    await tab(page)  # Tab attaches the approved CV and moves on to the NEXT question (the cover letter drop area)
    await eventually(lambda: attached_names(page, "cv", ["Alex-CV.pdf"]), 8, "the CV was attached through the hidden input")
    await eventually(lambda: active_id_is(page, "cl"), 8, "focus moved on to the cover letter drop area")
    assert (await bg_opened(page)) == 0, "the assistant never opens the file dialog"
    await chip_for(page, "cover letter")
    await tab(page)
    await eventually(lambda: attached_names(page, "cl", ["Alex-CV.pdf"]), 8, "the second drop area works the same (display:none input)")
    await eventually(lambda: active_name_is(page, "last"), 8, "and Tab arrives at the last name")
    # Shift+Tab walks back into the drop area; the person can still go on with native keys
    await tab(page, shift=True)
    await eventually(lambda: active_id_is(page, "cl"), 6, "Shift+Tab re-enters the drop area")
    assert not await page.evaluate("window.submissions || 0")


async def attached_names(page: Any, key: str, expected: list[str]) -> bool:
    return (await attached(page)).get(key) == expected


async def active_id_is(page: Any, ident: str) -> bool:
    return await page.evaluate("document.activeElement && document.activeElement.id") == ident


async def active_name_is(page: Any, name: str) -> bool:
    return (await active_name(page)) == name


async def bg_opened(page: Any) -> int:
    return await page.evaluate("window.opened")


@scenario("a_click_on_a_drop_area_shows_the_chip_and_attaches_from_the_chip_without_touching_the_file_dialog")
async def dropzone_by_click(ctx: Ctx) -> None:
    page, bg = await dropzone_page(ctx)
    assert await page.evaluate("document.getElementById('cv').hasAttribute('tabindex')") is False
    await page.click("#cv .dz-message")
    assert await bg_opened(page) == 1, "the page's own click handler ran (it opens the file dialog)"
    chip = await chip_for(page, "resume")
    assert chip["operation"] == "ATTACH_FILE" and chip["state"] == "VERIFIED"
    assert await page.evaluate("document.activeElement.id") == "cv"
    await emit(page, {"type": "accept", "key": chip["key"]})
    await eventually(lambda: attached_names(page, "cv", ["Alex-CV.pdf"]), 8, "attached from the chip")
    assert await bg_opened(page) == 1, "the assistant never clicked the drop area itself"
    # the assistant only borrowed tabindex=-1: switching it off gives the page back exactly as it was
    assert await page.evaluate("document.getElementById('cv').getAttribute('tabindex')") == "-1"
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: is_active(page, False), 6, "switched off")
    assert await page.evaluate("document.getElementById('cv').hasAttribute('tabindex')") is False


@scenario("enter_attaches_a_protected_tier_file_through_the_drop_area_and_a_page_owned_tabindex_is_left_alone")
async def dropzone_by_enter(ctx: Ctx) -> None:
    doc = make_document()
    ref = {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}
    answers = {"first name": T("Alex"), "resume": FILE(ref, insert="explicit", flags={"protected": True}), "cover letter": NEEDS(), "last name": T("Newport")}
    page, bg = await ctx.open("dropzone_hidden.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    await page.evaluate("document.getElementById('cl').setAttribute('tabindex', '0')")  # the page made this drop area focusable itself
    await page.focus("input[name=first]")
    await tab(page)
    chip = await chip_for(page, "resume")
    assert chip["insert"] == "explicit" and "enter" in chip["keys"] and "tab" not in chip["keys"], chip["keys"]
    await tab(page)  # Tab does not insert a protected answer: the browser's own Tab takes over from the drop area
    await asyncio.sleep(0.3)
    assert await attached(page) == {}, "a protected answer needs Enter"
    await page.evaluate("document.getElementById('cv').focus()")  # back to the drop area (it already has tabindex=-1 from the visit above)
    await chip_for(page, "resume")
    await page.keyboard.press("Enter")
    await eventually(lambda: attached_names(page, "cv", ["Alex-CV.pdf"]), 8, "Enter attached the file")
    await eventually(lambda: active_id_is(page, "cl"), 8, "and moved on to the next drop area")
    assert await page.evaluate("document.getElementById('cl').getAttribute('tabindex')") == "0", "the page's own tabindex is never rewritten"
    await bg.to_all({"t": "deactivate"})
    await eventually(lambda: is_active(page, False), 6, "switched off")
    assert await page.evaluate("document.getElementById('cl').getAttribute('tabindex')") == "0"


# ------------------------------------------------------------------------------------------------------------------------
# 5. Safari's Tab stops on text fields only: from a control that is no assist field the assistant walks to a checkbox / radio / menu itself

SAFARI_WALK = {"first name": T("Alex"), "which languages": BOXES("English"), "country": SELECT("Spain", "es"), "notes": T("none"),
               "willing to relocate": RADIO("Yes", "yes")}


class KeyLog:
    """Which Tab presses reached the page's own listeners (a key the assistant took over never does) and with which default."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.seen = 0

    async def press(self, shift: bool = False) -> str:
        """Presses Tab and says who handled it: "assistant" (swallowed before the page's bubble listeners) or "browser" (left alone)."""
        await tab(self.page, shift=shift)
        keys = await reached(self.page)
        if len(keys) == self.seen:
            return "assistant"
        self.seen = len(keys)
        assert keys[-1] == ["Tab", False], keys[-1]
        return "browser"


@scenario("safari_model_tab_from_a_plain_control_walks_to_the_next_checkbox_radio_or_menu_the_browser_would_skip")
async def safari_walk(ctx: Ctx) -> None:
    page, bg = await ctx.open("safari_walk.html", SAFARI_WALK, init="window.__MERIDIAN_SAFARI_TAB__ = true;")
    await watch_keys(page)
    keys = KeyLog(page)

    async def stand_on(name: str) -> None:
        await page.evaluate(f"document.querySelector('input[name={name}]').focus()")

    # forward: from the read-only reference the browser would land on the next TEXT field and jump over the languages
    await stand_on("ref")
    assert await keys.press() == "assistant", "the key was the assistant's, not the browser's"
    assert await active_name(page) == "lang", "the languages are reached, not jumped over"
    await chip_for(page, "languages")
    # from the read-only badge number (never an assist field) to the radio group
    await stand_on("badge")
    assert await keys.press() == "assistant"
    assert await active_name(page) == "relocate"
    # backwards: from the second reference the browser would skip the menu on its way to the first reference
    await stand_on("ref2")
    assert await keys.press(shift=True) == "assistant"
    assert await active_name(page) == "country", "Shift+Tab walks back to the menu"
    # where the browser's own Tab reaches the next stop by itself, the key is left alone
    await stand_on("ref2")
    assert await keys.press() == "browser"
    assert await active_name(page) == "notes", "a text field follows: the browser's own Tab goes there"
    await stand_on("badge")
    assert await keys.press(shift=True) == "browser"
    assert await active_name(page) == "notes"
    await stand_on("ref")
    assert await keys.press(shift=True) == "browser"
    assert await active_name(page) == "first"
    assert not await page.evaluate("document.querySelector('input[name=relocate]:checked')"), "walking never fills anything"


@scenario("chrome_model_tab_from_a_plain_control_is_always_the_browsers_own", webkit=False)
async def chrome_plain_tab_is_native(ctx: Ctx) -> None:
    """Chrome's Tab reaches every control: the assistant stays out of a key pressed on a control that is none of its fields."""
    page, bg = await ctx.open("safari_walk.html", SAFARI_WALK)
    await watch_keys(page)
    keys = KeyLog(page)
    await page.evaluate("document.querySelector('input[name=ref]').focus()")
    assert await keys.press() == "browser"
    assert await active_name(page) == "lang", "the browser itself reaches the first checkbox"
    await page.evaluate("document.querySelector('input[name=badge]').focus()")
    assert await keys.press() == "browser"
    assert await active_name(page) == "relocate"
    await page.evaluate("document.querySelector('input[name=ref2]').focus()")
    assert await keys.press(shift=True) == "browser"
    assert await active_name(page) == "country"


# ------------------------------------------------------------------------------------------------------------------------
# 6. hostile pages: whatever a page does to its own DOM, nothing the assistant does may surface as an error of the PAGE

HOSTILE = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "country": SELECT("Spain", "es"),
           "which languages": BOXES("English"), "phone": T("+44 7700 900123"), "nickname": T("Al")}


class PageErrors:
    """Everything a page's own error monitors (window.onerror, unhandledrejection, the console) would have heard."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.seen: list[str] = []
        page.on("pageerror", lambda e: self.seen.append(f"pageerror: {str(e)[:120]} <- {self.where(e)}"))
        page.on("console", lambda m: self.seen.append(f"console.error: {m.text[:120]}") if m.type == "error" else None)

    @staticmethod
    def where(error: Any) -> str:
        """The first frames of the page error's stack: who raised it."""
        lines = (error.stack or "").splitlines()[1:5]
        return " <- ".join(line.strip().split(" (")[0] for line in lines)

    async def all(self) -> list[str]:
        try:
            inside = await self.page.evaluate("window.__errors || []")
        except Exception:
            inside = []
        return sorted(set(self.seen + [f"window: {e}" for e in inside]))


async def poke(page: Any, rounds: int = 9) -> None:
    """Real keys and clicks over the whole form (a failing step is part of the game, never the test's problem)."""
    for _ in range(rounds):
        try:
            await page.keyboard.press("Tab")
            await page.keyboard.type("ab")
            await page.keyboard.press("Enter")
            await page.keyboard.press("Escape")
        except Exception:
            break
        await asyncio.sleep(0.03)
    for selector in ("input[name=first]", "select[name=country]", "label"):
        try:
            await page.click(selector, timeout=400)
        except Exception:
            pass
    await asyncio.sleep(0.4)


# each case: what the page does (JS, {{MAIN}} = the fixture origin) and the control the assistant must still serve afterwards (or None)
HOSTILE_CASES: dict[str, tuple[str, str | None]] = {
    "the page removes a field the moment it gets focus": ("hazard.removeOn('focusin', 'last')", "email"),
    "the page removes the field in a capture keydown that runs before the assistant's": ("hazard.removeOn('keydown', 'first', 'window')", "email"),
    "the page removes the field in its input handler": ("hazard.removeOn('input', 'first')", "email"),
    "the page removes the whole form when a field loses focus": ("hazard.removeFormOn('focusout', 'email')", None),
    "the page empties the body when a field gets focus": ("hazard.wipeBodyOn('focusin', 'email')", None),
    "a field whose value getter throws": ("hazard.throwing('first', ['value'])", "email"),
    "a field whose geometry getters throw": ("hazard.throwing('first', ['getBoundingClientRect', 'getClientRects'])", "email"),
    "a field whose isConnected and focus throw": ("hazard.throwing('email', ['focus', 'isConnected'])", "phone"),
    "a field whose labels, form, type, name, id and tagName throw": ("hazard.throwing('phone', ['labels', 'form', 'type', 'name', 'id', 'tagName'])", "email"),
    "a document whose activeElement and hasFocus throw": ("hazard.throwingGlobals([['document', 'hasFocus'], ['document', 'activeElement']])", None),
    "a document whose elementsFromPoint and getComputedStyle throw": ("hazard.throwingGlobals([['document', 'elementsFromPoint'], ['window', 'getComputedStyle']])", "email"),
    "window.parent and window.frames replaced by a proxy that throws on every touch": ("hazard.hostileParent()", "email"),
    "an iframe whose src, contentDocument, contentWindow and rect getters throw": ("hazard.frameWithThrowingProps()", "email"),
    "an iframe that is detached right after it appeared": ("hazard.frame('{{MAIN}}/frames_child.html'); setTimeout(() => hazard.removeFrame(), 30)", "email"),
}


@scenario("a_hostile_page_never_makes_the_assistant_throw_into_it_and_the_rest_of_the_form_keeps_working", webkit=False)
async def hostile_pages(ctx: Ctx) -> None:
    problems: list[str] = []
    for name, (js, probe) in HOSTILE_CASES.items():
        page, bg = await ctx.open("hostile.html", HOSTILE, ready=False)
        watch = PageErrors(page)
        await eventually(lambda: bg.suggest_requests(), 6, f"first request ({name})")
        await asyncio.sleep(0.4)
        await page.evaluate(js.replace("{{MAIN}}", ctx.servers.main_url))
        await poke(page)
        errors = await watch.all()
        if errors:
            problems.append(f"{name}: {errors[:3]}")
        state = await debug(page)
        if not state or not state["active"]:
            problems.append(f"{name}: the assistant is not running any more")
        elif probe:
            # the part of the form the page left alone is still served
            try:
                await page.focus(f"input[name={probe}]", timeout=800)
                await asyncio.sleep(0.2)
                await page.keyboard.press("Tab")
                if not await page.evaluate(f"document.querySelector('[name={probe}]').value"):
                    problems.append(f"{name}: Tab no longer inserts into an untouched field")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: the probe field could not be used ({type(exc).__name__})")
        await ctx.rig.reset()
    assert not problems, "\n".join(problems)


@scenario("a_page_that_rewrites_its_own_document_gets_a_working_assistant_again")
async def document_rewrite(ctx: Ctx) -> None:
    """document.open() erases the window's listeners and replaces <html>: the observers, the keyboard handler and the UI went with the
    old document, while the assistant still believed it was running (a toolbar click would even have switched it OFF)."""
    page, bg = await ctx.open("hostile.html", HOSTILE)
    watch = PageErrors(page)
    assert (await debug(page))["sessionId"] == "s1"
    await page.evaluate("hazard.docWrite()")

    async def serving_the_new_page() -> bool:
        state = await debug(page)
        return bool(state) and state["active"] and sorted(f["label"] for f in state["fields"]) == ["Nickname", "Phone"] and all(f["hasEntry"] for f in state["fields"])

    await eventually(serving_the_new_page, 10, "the new document is scanned and answered")
    await page.focus("input[name=nick]")
    await chip_for(page, "nickname")
    await tab(page)
    assert await page.evaluate("document.querySelector('[name=nick]').value") == "Al", "Tab inserts on the rewritten page"
    assert await active_name(page) == "phone2"
    assert not await watch.all()
    assert len(bg.msgs("session:open")) == 2, "one new session for the new page"
    assert bg.msgs("session:release"), "and the old one was let go"


@scenario("a_failing_answer_batch_never_stops_the_batches_queued_behind_it", webkit=False)
async def failing_batch_keeps_the_queue(ctx: Ctx) -> None:
    """Processing one answer threw (a hostile node under the chip): the loop that sends the next batches died with it and 170 questions
    never got an answer."""
    bg = Bg({})
    gate = Gate(bg)
    page, bg = await ctx.open("many_fields.html", bg=bg, ready=False)
    await eventually(lambda: bg.suggest_requests(), 8, "the first batch is on its way (and held)")
    watch = PageErrors(page)
    await page.focus("input[name=q1]")
    await page.evaluate("""() => { const input = document.querySelector('input[name=q1]');
      Object.defineProperty(input, 'isConnected', { configurable: true, get() { throw new Error('hostile isConnected'); } }); }""")
    gate.open()  # the answer for the focused, hostile field arrives: showing its chip throws
    await ctx.rig.ready(page, bg, timeout=25)
    sizes = [len(r["elements"]) for r in bg.suggest_requests()]
    assert sum(sizes) == 250, sizes
    assert not await watch.all()


# ------------------------------------------------------------------------------------------------------------------------
# 7. seeded random abuse: real keys, real clicks, and a page that keeps rewriting itself


def fuzz_steps(seed: int, count: int = 45) -> list[tuple[str, str]]:
    import random

    rng = random.Random(seed)
    keys = ["Tab", "Shift+Tab", "Enter", "Escape", "Alt+z", "Alt+r", "ArrowDown", "Space", "a", "Backspace"]
    mutations = [
        "(() => { const els = [...document.querySelectorAll('#f label')]; if (els.length > 2) els[Math.floor(window.__rand() * els.length)].remove(); })()",
        "(() => { const f = document.getElementById('f'); if (f) { const l = f.querySelector('label'); if (l) f.append(l.cloneNode(true)); } })()",
        "(() => { const f = document.getElementById('f'); if (f) f.insertAdjacentHTML('afterbegin', '<label>Fresh ' + Date.now() + ' <input name=\"fresh' + Date.now() + '\"></label>'); })()",
        "(() => { const els = [...document.querySelectorAll('#f input, #f select')]; const e = els[Math.floor(window.__rand() * els.length)]; if (e) e.disabled = !e.disabled; })()",
        "(() => { const els = [...document.querySelectorAll('#f label')]; const e = els[Math.floor(window.__rand() * els.length)]; if (e) e.style.display = e.style.display ? '' : 'none'; })()",
        "(() => { const f = document.getElementById('f'); if (f) { const c = f.cloneNode(true); f.replaceWith(c); } })()",
        "hazard.frame('{{MAIN}}/frames_child.html')",
        "hazard.removeFrame()",
        "(() => { const h = document.createElement('div'); h.attachShadow({mode:'open'}).innerHTML = '<label>In shadow <input name=\"sh\"></label>'; document.body.append(h); })()",
        "history.pushState({}, '', '/hostile.html?step=' + Date.now())",
        "(() => { const el = document.activeElement; if (el && el.remove && el !== document.body) el.remove(); })()",
        # a node that starts to misbehave: the page cannot be trusted to answer any question about it
        "(() => { const els = [...document.querySelectorAll('#f input')]; const e = els[Math.floor(window.__rand() * els.length)]; if (e && e.name) hazard.throwing(e.name, [['value'], ['getBoundingClientRect', 'getClientRects'], ['isConnected', 'focus'], ['labels', 'form']][Math.floor(window.__rand() * 4)]); })()",
    ]
    steps: list[tuple[str, str]] = []
    for _ in range(count):
        roll = rng.random()
        if roll < 0.55:
            steps.append(("key", rng.choice(keys)))
        elif roll < 0.7:
            steps.append(("click", rng.choice(["input", "select", "label", "button", "legend", "iframe"])))
        else:
            steps.append(("mutate", rng.choice(mutations)))
    return steps


@scenario("random_keys_clicks_and_page_rewrites_never_produce_an_error_of_the_page", webkit=False)
async def random_abuse(ctx: Ctx) -> None:
    problems: list[str] = []
    for seed in range(1, 7):
        page, bg = await ctx.open("hostile.html", HOSTILE)
        watch = PageErrors(page)
        # the page-side choices (which node, which getter) follow the seed too: a failure replays exactly
        await page.evaluate("(seed) => { let a = seed; window.__rand = () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }", seed)
        done: list[str] = []
        for kind, arg in fuzz_steps(seed):
            done.append(f"{kind}:{arg[:40]}")
            try:
                if kind == "key":
                    await page.keyboard.press(arg, delay=5)
                elif kind == "click":
                    els = await page.query_selector_all(arg)
                    if els:
                        await els[len(done) % len(els)].click(timeout=300, no_wait_after=True)
                else:
                    await page.evaluate(arg.replace("{{MAIN}}", ctx.servers.main_url))
            except Exception:
                pass  # a step the page made impossible (the element is gone): part of the abuse
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.6)
        errors = await watch.all()
        if errors:
            problems.append(f"seed {seed}: {errors[:3]} after {done[-6:]}")
        state = await debug(page)
        if not state or not state["active"] or state["orphaned"]:
            problems.append(f"seed {seed}: the assistant stopped ({state and state['connection']})")
        await ctx.rig.reset()
    assert not problems, "\n".join(problems)


# ------------------------------------------------------------------------------------------------------------------------
# 8. typing and scrolling on a 14,690-node page

PERF_BUDGET_MS = 8.0


async def type_cost(rig: Any, active: bool, keystrokes: int = 50) -> tuple[float, float]:
    """(main-thread work per keystroke in ms: script + layout + style, median input-to-paint in ms) with the assistant on or off."""
    page, bg = await rig.open("big_page.html", Bg({"country": SELECT("United Kingdom", "gb")}), activate=active, ready=active, headless_ui=False)
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Performance.enable")
    await page.evaluate("""() => { window.__lat = []; const mc = new MessageChannel(); const pending = [];
      mc.port1.onmessage = () => { const p = pending.shift(); if (p) window.__lat.push(performance.now() - p); };
      document.addEventListener('keydown', () => { pending.push(performance.now()); requestAnimationFrame(() => mc.port2.postMessage(0)); }, true); }""")
    await page.focus("input[name=q_20_1]")
    await asyncio.sleep(0.5)

    async def work() -> float:
        m = {x["name"]: x["value"] for x in (await cdp.send("Performance.getMetrics"))["metrics"]}
        return m["ScriptDuration"] + m["LayoutDuration"] + m["RecalcStyleDuration"]

    before = await work()
    await page.keyboard.type("x" * keystrokes, delay=45)
    await asyncio.sleep(0.8)  # the debounced chip / panel / digest work lands after the typing: it is part of the cost
    cost = (await work() - before) * 1000 / keystrokes
    latency = sorted(await page.evaluate("window.__lat"))
    return cost, latency[len(latency) // 2]


@scenario("typing_on_a_14690_node_page_costs_the_assistant_less_than_8_ms_per_keystroke", webkit=False)
async def typing_budget(ctx: Ctx) -> None:
    """Measured against the same page without the assistant, on the same machine, three rounds interleaved: the machine's load cancels out,
    the best round counts (a spike is not the assistant's)."""
    rounds: list[tuple[float, float]] = []
    for _ in range(3):
        off_cost, off_latency = await type_cost(ctx.rig, False)
        await ctx.rig.reset()
        on_cost, on_latency = await type_cost(ctx.rig, True)
        await ctx.rig.reset()
        rounds.append((on_cost - off_cost, on_latency - off_latency))
    cost = min(r[0] for r in rounds)
    latency = min(r[1] for r in rounds)
    print(f"      added per keystroke: work {cost:.2f} ms, median input-to-paint {latency:+.1f} ms (rounds {[(round(a, 2), round(b, 1)) for a, b in rounds]})")
    assert cost < PERF_BUDGET_MS, f"the assistant adds {cost:.2f} ms of main-thread work per keystroke"
    assert latency < PERF_BUDGET_MS, f"the assistant adds {latency:.1f} ms to input-to-paint"


@scenario("a_drop_area_that_refuses_focus_keeps_the_pages_own_tabindex")
async def dropzone_refuses_focus(ctx: Ctx) -> None:
    """The assistant only ever ADDS tabindex=-1 where there is none: a drop area that has its own tabindex and still refuses focus is left
    exactly as the page wrote it (the key then falls back to the browser's own Tab)."""
    page, bg = await dropzone_page(ctx)
    await page.evaluate("""() => { const zone = document.getElementById('cv'); zone.setAttribute('tabindex', '0'); zone.focus = () => {}; }""")
    await page.focus("input[name=first]")
    await chip_for(page, "first name")
    await tab(page)
    assert await page.evaluate("document.querySelector('input[name=first]').value") == "Alex"
    await asyncio.sleep(0.3)
    assert await page.evaluate("document.getElementById('cv').getAttribute('tabindex')") == "0"


@scenario("safari_model_tab_from_a_plain_control_enters_the_drop_area_the_browser_cannot_focus")
async def dropzone_from_plain_safari(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "resume": FILE(DOC_REF), "cover letter": FILE(DOC_REF)}
    page, bg = await ctx.open("dropzone_hidden.html?plain=1", answers, bg=Bg(answers, documents={"doc-1": make_document()}),
                              init="window.__MERIDIAN_SAFARI_TAB__ = true;")
    await watch_keys(page)
    keys = KeyLog(page)
    await page.evaluate("document.querySelector('input[name=ref]').focus()")
    assert await keys.press() == "assistant"
    assert await active_id_is(page, "cv"), "the drop area is entered, not jumped over"
    await chip_for(page, "resume")


@scenario("chrome_model_tab_from_a_plain_control_is_left_alone_even_before_a_drop_area", webkit=False)
async def dropzone_from_plain_chrome(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "resume": FILE(DOC_REF), "cover letter": FILE(DOC_REF)}
    page, bg = await ctx.open("dropzone_hidden.html?plain=1", answers, bg=Bg(answers, documents={"doc-1": make_document()}))
    await watch_keys(page)
    keys = KeyLog(page)
    await page.evaluate("document.querySelector('input[name=ref]').focus()")
    assert await keys.press() == "browser", "Chrome's behaviour is unchanged: the key is the browser's"
