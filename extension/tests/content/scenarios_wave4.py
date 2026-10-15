"""Wave-4 scenarios of the content-script rig (workstream CTRL3): what the re-review of wave 3 found in the content logic (UXR-02 ... UXR-14),
the SEC-01 leftovers and the hostile-DOM hand-offs. Same rules as scenarios.py: REAL key presses, the view models handed to the UI and the
messages the content script sent. Every scenario here fails without its fix (the report names the mutation that proves it).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from harness import (
    BOXES, RADIO, SELECT, T, Bg, active_name, chip_for, debug, emit, err, eventually, last_chip, last_panel, normalize,
    press_fill_verified, tab, ui_log, value_of,
)
from scenarios import GH, Ctx, child_frame, field_info, reached, scenario, watch_keys

PLATFORM = {
    # what the visual layer and the logic ask the browser: userAgentData.platform first, navigator.platform second
    "mac": "Object.defineProperty(Navigator.prototype,'platform',{configurable:true,get:()=>'MacIntel'});"
           "Object.defineProperty(Navigator.prototype,'userAgentData',{configurable:true,get:()=>({platform:'macOS'})});",
    "other": "Object.defineProperty(Navigator.prototype,'platform',{configurable:true,get:()=>'Win32'});"
             "Object.defineProperty(Navigator.prototype,'userAgentData',{configurable:true,get:()=>({platform:'Windows'})});",
}
ALT, CTRL, META, SHIFT = 1, 2, 4, 8  # Input.dispatchKeyEvent modifier bits


async def toasts(page: Any) -> list[dict[str, Any]]:
    return [c["arg"] for c in await ui_log(page, "toast")]


async def ui_calls(page: Any, name: str) -> list[Any]:
    return [c["arg"] for c in await ui_log(page, name)]


async def rect_of(page: Any, selector: str) -> dict[str, float]:
    return await page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }", selector)


def inside(inner: dict[str, float], outer: dict[str, float], slack: float = 1.0) -> bool:
    return (inner["x"] >= outer["x"] - slack and inner["y"] >= outer["y"] - slack and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + slack
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + slack)


def overlap(a: dict[str, float], b: dict[str, float]) -> float:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return w * h if w > 0 and h > 0 else 0.0


async def cdp_key(page: Any, key: str, code: str, modifiers: int = 0, text: str | None = None, vk: int = 0) -> None:
    """One trusted key press exactly as the OS layout would deliver it (the key may be "¡" for Option+1 on a Mac, "m" for the printed M of an
    AZERTY keyboard whose `code` says Semicolon ...). Chromium only."""
    session = await page.context.new_cdp_session(page)
    try:
        base = {"modifiers": modifiers, "key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        await session.send("Input.dispatchKeyEvent", {"type": "keyDown" if text else "rawKeyDown", **base, **({"text": text, "unmodifiedText": text} if text else {})})
        await session.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
    finally:
        await session.detach()


# ------------------------------------------------------------------------------------------------------------------------
# UXR-02: the chip is placed beside the field; for a radio / checkbox group "the field" includes the printed words of every option


CHOICE_ANSWERS = {
    "full name": T("Alex Newport"), "visa sponsorship": RADIO("No", "no"), "languages": BOXES("English", "Spanish"), "expected salary": T("55000"),
}


@scenario("the_chip_of_a_choice_group_never_sits_on_the_words_of_an_option")
async def chip_off_option_labels(ctx: Ctx) -> None:
    page, bg = await ctx.open("radios_inline.html", CHOICE_ANSWERS, viewport=(1280, 900))
    for first, name, labels in (("#s_yes", "visa sponsorship", ["#s_yes", "#s_no", "#s_na"]), ("#l_en", "languages", ["#l_en", "#l_es", "#l_de"])):
        await page.focus(first)
        chip = await chip_for(page, name)
        # every option is a rectangle the chip must stay off: the control AND its printed label
        for sel in labels:
            control = await rect_of(page, sel)
            label = await rect_of(page, f"label[for={sel[1:]}]")
            wanted = {"x": min(control["x"], label["x"]), "y": min(control["y"], label["y"]), "w": 0, "h": 0}
            wanted["w"] = max(control["x"] + control["w"], label["x"] + label["w"]) - wanted["x"]
            wanted["h"] = max(control["y"] + control["h"], label["y"] + label["h"]) - wanted["y"]
            assert any(inside(wanted, a, 2) for a in chip["avoid"]), (name, sel, wanted, chip["avoid"])
        # the field the chip is anchored to is the whole group as the person reads it: 'beside' starts after the LAST label
        right = 0.0
        for sel in labels:
            box = await rect_of(page, f"label[for={sel[1:]}]")
            right = max(right, box["x"] + box["w"])
        assert chip["anchor"]["x"] + chip["anchor"]["w"] >= right - 1, (name, chip["anchor"], right)
        assert chip["placement"] == "beside", chip["placement"]
        # so the box 'beside' asks for (to the right of the anchor) touches no option text
        beside = {"x": chip["anchor"]["x"] + chip["anchor"]["w"], "y": chip["anchor"]["y"], "w": 340, "h": 120}
        for sel in labels:
            assert overlap(beside, await rect_of(page, f"label[for={sel[1:]}]")) <= 20, (name, sel, beside, await rect_of(page, f"label[for={sel[1:]}]"))  # (rounding: <1 px)
    # the field AFTER a group keeps the group's options (and the previous text field its box) in its list of things to stay off
    await page.focus("input[name=salary]")
    chip = await chip_for(page, "expected salary")
    german = await rect_of(page, "label[for=l_de]")
    assert any(inside(german, a, 2) for a in chip["avoid"]), (german, chip["avoid"])


# ------------------------------------------------------------------------------------------------------------------------
# UXR-03 / UXR-11: the chip's numbered actions need a chord that types no character; Alt+Shift+M follows the key that is printed


async def numbered_chip(ctx: Ctx, platform: str) -> tuple[Any, Bg]:
    page, bg = await ctx.open("greenhouse.html", GH, init=PLATFORM[platform])
    await page.focus("input[name='job_application[first_name]']")
    await chip_for(page, "first name")
    await watch_keys(page)
    await page.evaluate("window.__meridianChipActions = [1]")  # the chip on screen has a button numbered 1 and no other
    return page, bg


async def chip_action_calls(page: Any) -> list[int]:
    return [c for c in await ui_calls(page, "chipAction")]


@scenario("chip_actions_use_chords_that_type_nothing_alt_shift_digit_elsewhere_and_control_option_digit_on_a_mac")
async def chip_chords(ctx: Ctx) -> None:
    # --- not a Mac: Alt+Shift+digit; plain Alt+digit and Ctrl+Alt+digit (AltGr on many layouts) are nobody's shortcut
    page, bg = await numbered_chip(ctx, "other")
    await page.keyboard.press("Alt+Shift+1")
    assert await chip_action_calls(page) == [1]
    assert await reached(page) == [], "the chord ran an action: the key was swallowed"
    await page.keyboard.press("Alt+Shift+2")  # the chip has no button 2: nothing ran, so the key is left alone
    assert await chip_action_calls(page) == [1, 2]
    seen = await reached(page)
    assert len(seen) == 1 and seen[0][1] is False, ("a key that did nothing is not swallowed", seen)
    for chord in ("Alt+1", "Control+Alt+1", "Alt+Control+Shift+1", "Shift+1"):
        await page.keyboard.press(chord)
    assert await chip_action_calls(page) == [1, 2], "none of these is a chip chord"
    await page.keyboard.press("Alt+Shift+8")  # only 1..7 are numbered
    assert await chip_action_calls(page) == [1, 2]
    # --- a Mac: Control+Option+digit; Option+digit and Option+Shift+digit type characters there
    page2, bg2 = await numbered_chip(ctx, "mac")
    await page2.keyboard.press("Control+Alt+1")
    assert await chip_action_calls(page2) == [1]
    assert await reached(page2) == [], "swallowed: it ran the action"
    for chord in ("Alt+1", "Alt+Shift+1", "Control+Alt+Shift+1", "Meta+Alt+1"):
        await page2.keyboard.press(chord)
    assert await chip_action_calls(page2) == [1], "on a Mac Option+digit and Option+Shift+digit are characters, not chords"
    # the chip tells the UI which glyphs to print
    chip = await last_chip(page2)
    assert chip["keyHints"] == {"chipActionPrefix": "mac"}
    chip = await last_chip(page)
    assert chip["keyHints"] == {"chipActionPrefix": "other"}


@scenario("option_digit_characters_are_typed_not_swallowed_while_a_chip_button_is_numbered", webkit=False)
async def option_digit_types(ctx: Ctx) -> None:
    """UXR-03: Option+1 types "¡" on a Mac keyboard. With the chip showing "Replace 1" the old Alt+digit shortcut swallowed it and replaced what
    the person had typed. Now it is a character like any other; only Control+Option+1 is the chord."""
    first = "input[name='job_application[first_name]']"
    page, bg = await numbered_chip(ctx, "mac")
    await page.fill(first, "Zed")
    await page.focus(first)
    await cdp_key(page, "¡", "Digit1", ALT, text="¡", vk=49)  # what Option+1 delivers on a US Mac layout
    assert await value_of(page, first) == "Zed¡", "the character was typed"
    assert await chip_action_calls(page) == [], "and no chip action was asked for"
    await cdp_key(page, "¡", "Digit1", ALT | CTRL, vk=49)  # Control+Option+1: the chord (no text is produced)
    assert await chip_action_calls(page) == [1]
    assert await value_of(page, first) == "Zed¡"


@scenario("alt_shift_m_follows_the_key_the_person_pressed_whatever_the_layout", webkit=False)
async def alt_shift_m_layouts(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.focus("input[name='job_application[first_name]']")
    await chip_for(page, "first name")
    cases = [
        ("QWERTY: M", dict(key="M", code="KeyM"), True),
        ("AZERTY: the key printed M sits where a US keyboard has the semicolon", dict(key="m", code="Semicolon"), True),
        ("AZERTY: the key at the US M position prints a question mark", dict(key="?", code="KeyM"), False),
        ("Dvorak: the key printed M", dict(key="M", code="KeyM"), True),
        ("a Mac: Option+Shift+M prints a symbol, so the physical key decides", dict(key="Â", code="KeyM"), True),
        ("a Cyrillic layout: the physical key", dict(key="Ь", code="KeyM"), True),
        ("another letter", dict(key="N", code="KeyN"), False),
    ]
    for name, event, panel in cases:
        before = len(await ui_calls(page, "focusPanel"))
        await cdp_key(page, event["key"], event["code"], ALT | SHIFT, vk=77)
        after = len(await ui_calls(page, "focusPanel"))
        assert (after - before == 1) == panel, f"{name}: the panel shortcut {'must' if panel else 'must not'} fire"


# ------------------------------------------------------------------------------------------------------------------------
# UXR-04: Tab / Shift+Tab into a dialog or a scrolling pane scrolls the field into ITS box, and the chip follows


DIALOG_ANSWERS = {
    "first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"),
    "linkedin": T("https://linkedin.example.test/alex"), "github": T("https://github.example.test/alex"),
    "portfolio": T("https://alex.example.test"), "expected salary": T("55000"),
}
DIALOG_FIELDS = ["first", "last", "email", "phone", "linkedin", "github", "portfolio", "salary"]


async def in_dialog(page: Any, name: str) -> bool:
    return inside(await rect_of(page, f"input[name={name}]"), await dialog_box(page))


async def dialog_box(page: Any) -> dict[str, float]:
    return await page.evaluate("(() => { const d = document.getElementById('dlg'); const r = d.getBoundingClientRect(); "
                               "return {x: r.left + d.clientLeft, y: r.top + d.clientTop, w: d.clientWidth, h: d.clientHeight}; })()")


@scenario("tab_and_shift_tab_scroll_the_next_field_into_its_dialog_and_the_chip_follows_it")
async def dialog_walk(ctx: Ctx) -> None:
    page, bg = await ctx.open("dialog_scroll.html", DIALOG_ANSWERS, viewport=(1280, 900))
    await page.focus("input[name=first]")
    await chip_for(page, "first name")
    for index in range(1, len(DIALOG_FIELDS)):
        await tab(page)
        name = DIALOG_FIELDS[index]
        assert await active_name(page) == name
        # the field is where the person can see it: inside the box that clips it (the browser's own Tab would have scrolled it there)
        assert await in_dialog(page, name), (name, await rect_of(page, f"input[name={name}]"), await dialog_box(page))
        # and its chip is drawn there, not over the page behind the dialog (a chip anchored to the field's old place would be far from it)
        async def chip_follows() -> bool:
            chip = await last_chip(page)
            now = await rect_of(page, f"input[name={name}]")
            return bool(chip) and abs(chip["anchor"]["y"] - now["y"]) <= 2 and abs(chip["anchor"]["x"] - now["x"]) <= 2

        await eventually(chip_follows, 4, f"the chip of {name} is placed where the field is now")
    assert await page.evaluate("document.getElementById('dlg').scrollTop") > 0, "the dialog scrolled"
    # and back up with Shift+Tab
    for index in range(len(DIALOG_FIELDS) - 2, -1, -1):
        await tab(page, shift=True)
        name = DIALOG_FIELDS[index]
        assert await active_name(page) == name
        assert await in_dialog(page, name), (name, await rect_of(page, f"input[name={name}]"), await dialog_box(page))
    assert await value_of(page, "input[name=first]") == "Alex" and await value_of(page, "input[name=salary]") == "", \
        "the walk inserted on the way down (the last field was only entered) and never on the way back"


# ------------------------------------------------------------------------------------------------------------------------
# UXR-05: an engine that raises no `focusin` for a native date / time input (Safari's) still gets the field's chip


NO_FOCUSIN = "window.addEventListener('focusin', (e) => e.stopImmediatePropagation(), true);"  # what WebKit does for <input type=date>


DATE_ANSWERS = {"expected salary": T("55000"), "when can you start": T("2026-11-02"), "preferred call time": T("09:30"), "nickname": T("Al")}


@scenario("a_native_date_input_gets_its_chip_in_the_engine_as_it_is")
async def date_input_chip_as_is(ctx: Ctx) -> None:
    """The same walk with no help: Safari's engine really raises no `focusin` there, Chromium does; both must end on the date field's chip."""
    page, bg = await ctx.open("date_fields.html", DATE_ANSWERS)
    await page.focus("input[name=salary]")
    await tab(page)
    assert await active_name(page) == "start"

    async def date_chip() -> Any:
        chip = await last_chip(page)
        return chip if chip and "start" in normalize(chip["fieldLabel"]) else None

    await eventually(date_chip, 4, "the chip of the date field")


@scenario("a_native_date_input_gets_its_chip_even_when_the_engine_raises_no_focusin")
async def date_input_chip(ctx: Ctx) -> None:
    answers = DATE_ANSWERS
    page, bg = await ctx.open("date_fields.html", answers, init=NO_FOCUSIN)
    await page.focus("input[name=salary]")
    await page.evaluate("new Promise((r) => setTimeout(r, 50))")
    await tab(page)  # inserts the salary and walks on: the date input takes the focus, and the engine says nothing about it
    assert await value_of(page, "input[name=salary]") == "55000"
    assert await active_name(page) == "start"

    async def date_chip() -> Any:
        chip = await last_chip(page)
        return chip if chip and "start" in normalize(chip["fieldLabel"]) else None

    chip = await eventually(date_chip, 4, "the chip of the date field (the salary's stays no longer)")
    assert chip["value"] == "2026-11-02" and chip["insert"] == "tab"
    assert (await debug(page))["current"] == (await field_info(page, "start"))["key"], "the assistant is on the date field"
    # the person sees the answer before Tab puts it in
    assert await value_of(page, "input[name=start]") == ""
    await tab(page)
    assert await value_of(page, "input[name=start]") == "2026-11-02"
    # a time input is no different
    assert await active_name(page) == "slot"

    async def time_chip() -> Any:
        chip = await last_chip(page)
        return chip if chip and "call time" in normalize(chip["fieldLabel"]) else None

    await eventually(time_chip, 4, "the chip of the time field")


# ------------------------------------------------------------------------------------------------------------------------
# SEC-01 / UXR-06: "Fill verified" lists what it will fill and fills exactly that; from the keyboard the focus comes back to the form


FIRST, LAST, EMAIL = ("input[name='job_application[%s]']" % n for n in ("first_name", "last_name", "email"))

FAKE_PANEL = """
(() => {
  const host = document.createElement('div');
  host.setAttribute('data-meridian-assistant', '');
  host.innerHTML = '<button id="fake-panel-button">Fill verified</button>';
  document.body.append(host);
})()
"""  # a stand-in for the panel in the page (the headless UI draws nothing): focus in it is "the keyboard is in the panel"


@scenario("fill_verified_lists_every_field_before_filling_and_fills_exactly_those")
async def fill_preview(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", {**GH, "phone": T("+44 7700 900123")})
    assert (await last_panel(page))["fillPreview"] is None
    # the first press writes nothing: the confirmation row says what the button will do
    row = await press_fill_verified(page, confirm=False)
    assert row is not None and row["count"] == 5, row
    assert [(i["label"], i["value"]) for i in row["items"]] == [
        ("First name", "Alex"), ("Last name", "Newport"), ("Email", "alex@example.test"), ("Country", "United Kingdom"), ("Willing to relocate", "Yes"),
    ], row
    await asyncio.sleep(0.3)
    assert [await value_of(page, s) for s in (FIRST, LAST, EMAIL)] == ["", "", ""], "nothing was written before the confirmation"
    # cancelling closes the row and still writes nothing
    await emit(page, {"type": "fill-cancel"})
    assert (await last_panel(page))["fillPreview"] is None
    await asyncio.sleep(0.2)
    assert await value_of(page, FIRST) == ""
    # a field that becomes fillable AFTER the row was shown is not part of what the person confirmed
    row = await press_fill_verified(page, confirm=False)
    assert row and row["count"] == 5
    await page.evaluate("""(() => { const l = document.createElement('label'); l.textContent = 'Phone '; const i = document.createElement('input'); i.name = 'phone';
        l.append(i); document.querySelector('form').prepend(l); })()""")
    await eventually(lambda: field_info(page, "phone"), 4, "the new field is scanned")
    await eventually(lambda: _has_entry(page, "phone"), 6, "and answered")
    await emit(page, {"type": "fill-confirm"})
    await eventually(lambda: _filled(page, [FIRST, LAST, EMAIL]), 5, "the three listed fields were filled")
    await asyncio.sleep(0.4)
    assert await page.evaluate("document.querySelector('input[name=phone]').value") == "", "the field that appeared afterwards was not confirmed, so it was not filled"


async def _has_entry(page: Any, label: str) -> bool:
    return bool((await field_info(page, label))["hasEntry"])


async def _filled(page: Any, selectors: list[str]) -> bool:
    return all([await value_of(page, s) for s in selectors])


@scenario("a_ui_without_the_confirmation_row_keeps_the_one_click_fill")
async def fill_without_preview(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, init="window.__MERIDIAN_NO_FILL_PREVIEW__ = true;")
    await emit(page, {"type": "fill-verified"})
    await eventually(lambda: _filled(page, [FIRST, LAST, EMAIL]), 5, "a UI that cannot show the row would strand the person: it fills at once")
    assert (await last_panel(page))["fillPreview"] is None


@scenario("fill_verified_in_frames_lists_their_fields_too_and_fills_only_the_confirmed_ones")
async def fill_preview_frames(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test"), "nickname": T("Al")}
    page, bg = await ctx.open("frames_top.html", answers, wait_frames=1)
    child = await child_frame(page)
    await eventually(lambda: _panel_fillable(page, 4), 6, "the panel counts the page's field and the frame's three")
    row = await press_fill_verified(page, confirm=False)
    assert row and row["count"] == 4, row
    assert sorted((i["label"], i["value"]) for i in row["items"]) == sorted(
        [("First name", "Alex"), ("Email", "alex@example.test"), ("Phone", "+44 7700 900123"), ("Website", "https://alex.example.test")]), row
    assert await child.evaluate("[...document.querySelectorAll('input')].map((i) => i.value).join('')") == ""
    # a field of the frame that appears AFTER the row was shown is not part of what the person confirmed
    await child.evaluate("""(() => { const l = document.createElement('label'); l.textContent = 'Nickname '; const i = document.createElement('input'); i.name = 'nickname';
        l.append(i); document.querySelector('form').append(l); })()""")
    await eventually(lambda: _has_entry(child, "nickname"), 6, "the frame scans and asks about the new field")
    await emit(page, {"type": "fill-confirm"})
    await eventually(lambda: child.evaluate("document.querySelector('input[name=email]').value === 'alex@example.test'"), 6, "the frame's listed fields are filled")
    assert await page.evaluate("document.querySelector('input[name=first_name]').value") == "Alex"
    await asyncio.sleep(0.5)
    assert await child.evaluate("document.querySelector('input[name=nickname]').value") == "", "the frame fills the fields that were listed, not whatever became fillable since"


async def _panel_fillable(page: Any, count: int) -> bool:
    panel = await last_panel(page)
    return bool(panel) and panel["fillableCount"] == count


@scenario("fill_verified_from_the_panel_with_the_keyboard_returns_to_the_field_the_person_was_on_and_alt_shift_z_takes_the_batch_back")
async def fill_from_keyboard(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH)
    await page.focus(LAST)
    await chip_for(page, "last name")  # the person is on the last name field ...
    await page.evaluate(FAKE_PANEL)
    await page.focus("#fake-panel-button")  # ... and has moved the keyboard into the panel
    assert await page.evaluate("document.activeElement.id") == "fake-panel-button"
    await press_fill_verified(page)
    await eventually(lambda: _filled(page, [FIRST, LAST, EMAIL]), 5, "filled")
    await eventually(lambda: active_name(page), 3, "focus")
    assert await active_name(page) == "job_application[last_name]", "the keyboard is back on the form field the person was on, not on <body>"
    # the keyboard way to "Undo all": Alt+Shift+Z, from the field or from the panel
    await watch_keys(page)
    await page.keyboard.press("Alt+Shift+Z")
    await eventually(lambda: _all_empty(page, [FIRST, LAST, EMAIL]), 4, "every answer of the batch is back")
    assert await reached(page) == [], "the chord took an action: swallowed"
    assert any(t.get("params", {}).get("count") == 5 for t in await toasts(page) if t["kind"] == "undone"), await toasts(page)
    await page.keyboard.press("Alt+Shift+Z")  # nothing left to take back: the key is left alone
    seen = await reached(page)
    assert len(seen) == 1 and seen[0][1] is False, seen
    # from inside the panel as well (an answer the person took back is held until they are on the field again: they go back to each one)
    for selector in (FIRST, LAST, EMAIL, "select[name=country]", "input[name=relocation][value=yes]"):
        await page.focus(selector)
    await page.focus("#fake-panel-button")
    await press_fill_verified(page)
    await eventually(lambda: _filled(page, [FIRST, LAST, EMAIL]), 5, "filled again")
    await page.focus("#fake-panel-button")
    await page.keyboard.press("Alt+Shift+Z")
    await eventually(lambda: _all_empty(page, [FIRST, LAST, EMAIL]), 4, "undone from the panel")


async def _all_empty(page: Any, selectors: list[str]) -> bool:
    return not any([await value_of(page, s) for s in selectors])


# ------------------------------------------------------------------------------------------------------------------------
# UXR-07: Alt+Shift+M pressed inside an iframe reaches the panel of the top frame


@scenario("alt_shift_m_inside_an_iframe_moves_the_keyboard_into_the_top_frames_panel_and_back")
async def panel_key_in_frame(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test")}
    page, bg = await ctx.open("frames_top.html", answers, wait_frames=1)
    child = await child_frame(page)
    await eventually(lambda: _child_knows_panel(child), 6, "the frame was told a panel is on screen")
    await child.focus("input[name=email]")
    await asyncio.sleep(0.3)
    await watch_keys(child)
    before = len(await ui_calls(page, "focusPanel"))
    await page.keyboard.press("Alt+Shift+M")
    await eventually(lambda: _panel_asked(page, before + 1), 4, "the top frame was asked to focus its panel")
    assert await reached(child) == [], "the frame swallowed the key: the panel is where the keyboard goes"
    # back: with the keyboard in the panel (stand-in), the same key returns it to the frame's field the person was on
    await page.evaluate(FAKE_PANEL)
    await page.focus("#fake-panel-button")
    await page.keyboard.press("Alt+Shift+M")
    await eventually(lambda: child.evaluate("document.activeElement && document.activeElement.name === 'email'"), 5, "the keyboard is back in the frame's email field")


async def _child_knows_panel(child: Any) -> bool:
    state = await debug(child)
    return bool(state) and state["topPanelAvailable"] is True


async def _panel_asked(page: Any, count: int) -> bool:
    return len(await ui_calls(page, "focusPanel")) >= count


@scenario("alt_shift_m_in_a_frame_is_left_alone_when_nobody_said_a_panel_is_there_to_enter")
async def panel_key_in_frame_without_panel(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test")}
    bg = Bg(answers)
    bg.relay_drop = lambda payload: payload.get("k") == "panel-state"  # the frame never hears that the top frame has a panel
    page, bg = await ctx.open("frames_top.html", bg=bg, wait_frames=1)
    child = await child_frame(page)
    await child.focus("input[name=email]")
    await asyncio.sleep(0.4)
    await watch_keys(child)
    await page.keyboard.press("Alt+Shift+M")
    await asyncio.sleep(0.3)
    seen = await reached(child)
    assert seen and all(prevented is False for _, prevented in seen), ("a key that went nowhere is not swallowed", seen)
    assert not await ui_calls(page, "focusPanel"), "and nothing was relayed"


# ------------------------------------------------------------------------------------------------------------------------
# UXR-08: the notice for an application already recorded says what became of it, and "I won't insert anything" is true


JOB = {"company": "Example Company", "title": "Software Engineer", "location": "London", "job_id": "j1", "application_id": "a1", "tracked": "applied"}
PROTECTED = {**GH, "country": SELECT("United Kingdom", "gb", insert="explicit")}


@scenario("the_duplicate_notice_carries_the_date_and_the_status_and_nothing_is_inserted_by_any_route_until_continue")
async def duplicate_everywhere(ctx: Ctx) -> None:
    job = {**JOB, "status": "REJECTED", "since": "2026-03-01T10:00:00+00:00", "tracked_at": "2026-03-01T10:00:00+00:00"}
    page, bg = await ctx.open("greenhouse.html", bg=Bg(PROTECTED, job=job))
    panel = await last_panel(page)
    assert panel["duplicate"] == {"state": "applied", "since": "2026-03-01T10:00:00+00:00", "status": "rejected"}, panel["duplicate"]
    await page.focus(FIRST)
    chip = await chip_for(page, "first name")
    # the chip itself says why, offers no Insert, and no key that would insert
    assert chip["insert"] == "never" and chip["duplicateBlocksInsert"] is True
    assert chip["reasonCode"] == "duplicate" and "rejected" in chip["reasonText"], chip["reasonText"]
    assert "tab" not in chip["keys"] and "enter" not in chip["keys"]
    # Tab, a click on Insert, a click on Replace and Enter on a protected answer: none writes anything
    await tab(page)
    assert await value_of(page, FIRST) == ""
    await emit(page, {"type": "accept", "key": chip["key"]})
    await emit(page, {"type": "replace", "key": chip["key"]})
    await page.focus("select[name=country]")
    protected = await chip_for(page, "country")
    assert protected["insert"] == "never" and protected["duplicateBlocksInsert"] is True
    await page.keyboard.press("Enter")
    await emit(page, {"type": "accept", "key": protected["key"]})
    await asyncio.sleep(0.4)
    assert await value_of(page, "select[name=country]") == "" and await value_of(page, FIRST) == ""
    # "Fill verified" refuses too: no confirmation row opens either
    await emit(page, {"type": "fill-verified"})
    await emit(page, {"type": "fill-confirm"})
    await asyncio.sleep(0.4)
    assert (await last_panel(page))["fillPreview"] is None and await value_of(page, FIRST) == ""
    # Continue: the same routes work
    await emit(page, {"type": "duplicate-continue"})
    await eventually(lambda: _banner_gone(page), 3, "acknowledged")
    await page.focus(FIRST)
    chip = await chip_for(page, "first name")
    assert chip["insert"] == "tab" and "duplicateBlocksInsert" not in chip
    await emit(page, {"type": "accept", "key": chip["key"]})
    await eventually(lambda: _filled(page, [FIRST]), 3, "a click on Insert works again")
    # the other statuses are worded as what they are
    for status, want in (("WITHDRAWN", "withdrawn"), ("GHOSTED", "ghosted"), ("OFFER", "offer"), ("INTERVIEW", "interview"), ("APPLIED", "applied"), (None, "applied")):
        p, _ = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "status": status}))
        assert (await last_panel(p))["duplicate"]["status"] == want, (status, (await last_panel(p))["duplicate"])
    # `tracked_at` is the older name of `since`: a server that only sends one of them still gets its date on the banner
    p, _ = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "tracked_at": "2026-02-02"}))
    assert (await last_panel(p))["duplicate"]["since"] == "2026-02-02"
    p, _ = await ctx.open("greenhouse.html", bg=Bg(GH, job={**JOB, "since": "2026-02-03"}))
    assert (await last_panel(p))["duplicate"]["since"] == "2026-02-03"


async def _banner_gone(page: Any) -> bool:
    return (await last_panel(page))["duplicate"] is None


@scenario("a_posting_that_turns_out_to_be_sent_elsewhere_raises_the_notice_with_its_status")
async def duplicate_event(ctx: Ctx) -> None:
    prepared = {**JOB, "tracked": "preparing", "status": "PREPARING"}
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, job=prepared))
    assert (await last_panel(page))["duplicate"] == {"state": "preparing"}
    bg.push_event({"type": "tracked", "tracked": "applied", "status": "WITHDRAWN", "since": "2026-05-05"})

    async def raised() -> bool:
        dup = (await last_panel(page))["duplicate"]
        return bool(dup) and dup["state"] == "applied" and dup.get("status") == "withdrawn" and dup.get("since") == "2026-05-05"

    await eventually(raised, 5, "the notice follows the event")
    await page.focus(FIRST)
    chip = await chip_for(page, "first name")
    assert chip["duplicateBlocksInsert"] is True and "withdrew" in chip["reasonText"]


# ------------------------------------------------------------------------------------------------------------------------
# UXR-10: the first-use card is for good once the person has had their first answer inserted (or said "Got it")


@scenario("the_first_use_card_is_remembered_by_the_worker_after_the_first_insertion_and_never_comes_back")
async def first_use_persisted(ctx: Ctx) -> None:
    bg = Bg(GH)
    bg.ui_prefs = {"dock": "left", "dismissed": {"budget": 1700000000000}}
    page, bg = await ctx.open("greenhouse.html", bg=bg)
    assert (await last_panel(page))["firstUse"] is True
    await page.focus(FIRST)
    await chip_for(page, "first name")
    assert (await last_panel(page))["firstUse"] is False, "touching a field ends the card for this page"
    assert not bg.msgs("ui:set"), "…but only an insertion ends it for good"
    await tab(page)
    assert await value_of(page, FIRST) == "Alex"
    await eventually(lambda: bg.msgs("ui:set"), 4, "the dismissal went to the worker")
    saved = bg.ui_prefs
    assert saved["dock"] == "left" and saved["dismissed"]["budget"] == 1700000000000, "the rest of the remembered look is kept"
    assert abs(saved["dismissed"]["first_use"] - time.time() * 1000) < 120000
    # the next application, in a new page: the card is not shown, not even before a field is touched
    again = Bg(GH)
    again.ui_prefs = saved
    page2, _ = await ctx.open("greenhouse.html", bg=again)
    assert (await last_panel(page2))["firstUse"] is False
    # a dismissal older than the card's memory (400 days) is forgotten: the three steps come back
    old = Bg(GH)
    old.ui_prefs = {"dock": "right", "dismissed": {"first_use": int((time.time() - 401 * 86400) * 1000)}}
    page3, _ = await ctx.open("greenhouse.html", bg=old)
    assert (await last_panel(page3))["firstUse"] is True


@scenario("a_ui_that_keeps_the_first_use_note_itself_is_asked_to_and_the_logic_writes_nothing")
async def first_use_ui_note(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", GH, init="window.__MERIDIAN_DISMISS_NOTE__ = true;")
    await page.focus(FIRST)
    await chip_for(page, "first name")
    await tab(page)
    await eventually(lambda: ui_calls(page, "dismissNote"), 4, "the UI was asked to remember the note")
    assert await ui_calls(page, "dismissNote") == ["first_use"]
    assert not bg.msgs("ui:set"), "the UI owns that entry then"


# ------------------------------------------------------------------------------------------------------------------------
# UXR-14: what the person reads and hears has no required-marker glyphs; what Meridian is asked has the page's own text


@scenario("labels_for_the_person_have_no_required_marker_glyphs_while_the_bridge_still_gets_the_page_text")
async def required_marks(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "full name": T("Alex Newport"), "phone": T("+44 7700 900123"), "email": T("alex@example.test"),
               "legally authorized": RADIO("Yes", "yes")}
    page, bg = await ctx.open("required_marks.html", answers)
    labels = {f["label"] for f in (await debug(page))["fields"]}
    assert labels >= {"First Name", "Full name", "Phone", "Email", "Are you legally authorized to work in the United Kingdom?"}, labels
    assert not [lbl for lbl in labels if any(g in lbl for g in "*✱†") or "(required)" in lbl], labels
    panel = await last_panel(page)
    assert not [i["label"] for i in panel["progress"]["items"] if "*" in i["label"] or "✱" in i["label"]], panel["progress"]["items"]
    await page.focus("input[name=first_name]")
    chip = await chip_for(page, "first name")
    assert chip["fieldLabel"] == "First Name" and chip["questionText"] == "First Name"
    await tab(page)
    toast_labels = [t.get("params", {}).get("label", "") for t in await toasts(page)]
    assert not [lbl for lbl in toast_labels if "*" in lbl], toast_labels
    # Meridian is asked the page's own text, stars included (what it classifies and remembers by)
    sent = {e["label"] for r in bg.suggest_requests() for e in r["elements"]}
    assert any("*" in lbl for lbl in sent) and any("✱" in lbl for lbl in sent), sent


# ------------------------------------------------------------------------------------------------------------------------
# SEC-01: the assistant asks again whether a person can see the control the moment it writes to it, and an invisible input is no tab stop


@scenario("a_field_covered_after_the_scan_is_not_written_by_tab_a_click_or_enter_and_is_written_again_once_uncovered")
async def covered_after_scan(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123")}
    page, bg = await ctx.open("cover_later.html", answers)
    await page.focus("input[name=email]")
    chip = await chip_for(page, "email")  # seen by the person (it passed a hit test)
    await page.evaluate("coverEmail()")
    await asyncio.sleep(1.5)  # the scanner may rescan; a control it saw before stays in the walk for a while
    assert await page.evaluate("document.elementFromPoint(...(() => { const r = document.querySelector('input[name=email]').getBoundingClientRect(); return [r.left + 20, r.top + 8]; })()).id") == "cover"
    await page.focus("input[name=email]")
    await tab(page)
    assert await value_of(page, "input[name=email]") == "", "Tab does not write into a control nobody can see"
    await emit(page, {"type": "accept", "key": chip["key"]})
    await emit(page, {"type": "replace", "key": chip["key"]})
    await asyncio.sleep(0.4)
    assert await value_of(page, "input[name=email]") == "", "nor does a click on Insert / Replace"
    assert any(t["kind"] == "error" for t in await toasts(page)), "and the person is told, calmly, that it was left alone"
    # "Fill verified" leaves it out of the writing (what the row lists is what was fillable at the scan: the write-time check is the guard)
    await press_fill_verified(page)
    await asyncio.sleep(0.5)
    assert await value_of(page, "input[name=email]") == ""
    # the layer goes: the field is written again
    await page.evaluate("uncover()")
    await asyncio.sleep(0.6)
    await page.focus("input[name=email]")
    chip = await chip_for(page, "email")
    await emit(page, {"type": "accept", "key": chip["key"]})
    await eventually(lambda: _filled(page, ["input[name=email]"]), 4, "written once a person can see it again")


@scenario("the_popup_of_the_question_the_person_just_left_does_not_stop_the_next_write_but_a_layer_of_nobodys_does")
async def popup_of_the_neighbour(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "city": T("London")}
    page, bg = await ctx.open("popup_cover.html", answers)
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await page.evaluate("showPopup()")  # a calendar of the first question lies over the email field (it was seen before)
    await asyncio.sleep(1.4)
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex" and await active_name(page) == "email"
    await chip_for(page, "email")
    await tab(page)
    assert await value_of(page, "input[name=email]") == "alex@example.test", "a question's own popup over the next one is what a date picker does: the walk goes on"
    assert await active_name(page) == "phone"
    await chip_for(page, "phone")
    await page.evaluate("showStray()")  # a layer that belongs to no question lies over the phone field
    await asyncio.sleep(1.4)
    before = len([t for t in await toasts(page) if t["kind"] == "error"])
    await tab(page)
    assert await value_of(page, "input[name=phone]") == "", "nobody can see the phone field: nothing is written into it"
    assert len([t for t in await toasts(page) if t["kind"] == "error"]) > before


@scenario("an_input_nobody_can_see_is_neither_an_assist_field_nor_a_stop_the_walk_hands_the_focus_to")
async def invisible_stops(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "last name": T("Newport"), "email": T("alex@example.test")}
    page, bg = await ctx.open("invisible_stops.html", answers)
    labels = [f["label"] for f in (await debug(page))["fields"]]
    assert labels == ["First name", "Last name", "Email"], labels
    await page.focus("input[name=first_name]")
    await chip_for(page, "first name")
    await tab(page)
    assert await value_of(page, "input[name=first_name]") == "Alex"
    # the browser's own Tab would stop on each invisible input in between; the walk is not a walk into the dark
    assert await active_name(page) == "last_name", await active_name(page)
    await tab(page)
    assert await active_name(page) == "email"
    await tab(page, shift=True)
    await tab(page, shift=True)
    assert await active_name(page) == "first_name", "and back"


# ------------------------------------------------------------------------------------------------------------------------
# hostile DOM (D1c hand-offs): the scanner's own listeners, timers and node reads never throw into the page


async def page_errors(page: Any) -> list[str]:
    try:
        return await page.evaluate("window.__errors || []")
    except Exception:
        return []


@scenario("a_node_whose_matches_throws_does_not_make_the_scanners_change_listener_an_error_of_the_page", webkit=False)
async def hostile_matches(ctx: Ctx) -> None:
    from scenarios_wave2c import HOSTILE, PageErrors, poke

    page, bg = await ctx.open("hostile.html", HOSTILE, ready=False)
    watch = PageErrors(page)
    await eventually(lambda: bg.suggest_requests(), 6, "first request")
    await asyncio.sleep(0.4)
    await page.evaluate("hazard.throwing('last', ['matches'])")
    await page.focus("input[name=last]")
    await page.keyboard.type("ab")
    await page.evaluate("document.querySelector('[name=last]').dispatchEvent(new Event('change', { bubbles: true }))")
    await poke(page)
    assert not await watch.all(), await watch.all()
    state = await debug(page)
    assert state and state["active"]


@scenario("a_control_whose_isconnected_throws_before_the_assistant_starts_costs_the_page_only_that_control", webkit=False)
async def hostile_is_connected_before_activation(ctx: Ctx) -> None:
    from scenarios_wave2c import HOSTILE, PageErrors

    page, bg = await ctx.open("hostile.html", HOSTILE, activate=False, ready=False)
    watch = PageErrors(page)
    await page.evaluate("Object.defineProperty(document.querySelector('[name=last]'), 'isConnected', { configurable: true, get() { throw new Error('hostile isConnected'); } })")
    await ctx.rig.activate(page, bg)

    async def found() -> bool:
        state = await debug(page)
        return bool(state) and state["active"] and any(f["label"] == "Email" for f in state["fields"])

    await eventually(found, 8, "the rest of the form is found")
    state = await debug(page)
    labels = [f["label"] for f in state["fields"]]
    assert "First name" in labels and "Email" in labels and "Phone" in labels, labels
    await page.focus("input[name=email]")
    await chip_for(page, "email")
    await tab(page)
    assert await page.evaluate("document.querySelector('[name=email]').value") == "alex@example.test"
    assert not await watch.all(), await watch.all()


# ------------------------------------------------------------------------------------------------------------------------
# UX-13 / always_on_sites: the switch tells the truth about what the browser has


@scenario("the_always_on_switch_follows_the_apps_site_list_and_a_refused_permission_shows_one_calm_line_instead_of_a_stale_switch")
async def always_on(ctx: Ctx) -> None:
    # a site Meridian's own setting lists starts the assistant by itself: the panel's switch says so (subdomain rules included)
    host = "127.0.0.1"
    page, bg = await ctx.open("greenhouse.html", bg=Bg(GH, settings={"always_on_sites": [host]}))
    assert (await last_panel(page))["site"] == {"host": host, "alwaysOn": True, "hintHidden": False}
    page2, bg2 = await ctx.open("greenhouse.html", bg=Bg(GH, prefs={"language": "auto", "alwaysOn": ["jobs.example.test"], "hintOff": [], "settings": None, "welcomed": True}))
    assert (await last_panel(page2))["site"]["alwaysOn"] is False, "a rule for another site says nothing about this one"
    # switching ON without the browser's permission: the worker refuses with `permission`; the switch stays OFF and the menu says why
    page3, bg3 = await ctx.open("greenhouse.html", GH, init="window.__MERIDIAN_ALWAYS_ON_POLL_MS = 150;")
    bg3.overrides["site:always-on"] = lambda msg, frame: err("permission", "Meridian needs your permission to start automatically on this site.")
    assert (await last_panel(page3))["site"]["alwaysOn"] is False
    await emit(page3, {"type": "always-on-site"})

    async def noted() -> bool:
        site = (await last_panel(page3))["site"]
        return site.get("alwaysOnNeedsPermission") is True and site["alwaysOn"] is False

    await eventually(noted, 4, "one calm line instead of a switch that says ON")
    # the person allows the site in the window that opened: the rule appears in the browser's list and the line goes by itself
    bg3.prefs = {**bg3.prefs, "alwaysOn": [host]}

    async def settled() -> bool:
        site = (await last_panel(page3))["site"]
        return site["alwaysOn"] is True and "alwaysOnNeedsPermission" not in site

    await eventually(settled, 6, "the switch is ON, the line is gone")


# ------------------------------------------------------------------------------------------------------------------------
# TEST-01: the rig does not call an empty page "ready"


@scenario("the_rig_does_not_call_a_page_that_came_up_empty_ready")
async def ready_needs_a_field(ctx: Ctx) -> None:
    page, bg = await ctx.rig.open("no_form.html", Bg({}), ready=False)
    await ctx.rig.ready(page, bg, expect_fields=0)  # a page that has no form says so
    failed = False
    try:
        await ctx.rig.ready(page, bg, timeout=1.0)
    except AssertionError:
        failed = True
    assert failed, "no field was scanned: that is not ready"


# ------------------------------------------------------------------------------------------------------------------------
# SEC-01 (frames): a frame nobody can see is given no session, and the keyboard is never sent into it


@scenario("a_frame_nobody_can_see_is_given_no_session_asks_meridian_nothing_and_is_never_focused_from_the_panel")
async def hidden_frame_gets_nothing(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "phone": T("+44 7700 900123"), "website": T("https://alex.example.test")}
    page, bg = await ctx.open("hidden_frames_top.html", answers, wait_frames=7)

    async def seen_asked() -> bool:
        frame = next(f for f in page.frames if f.url.endswith("v=seen"))
        return bool(bg.suggest_requests(f"f{bg.frame_id(frame)}"))

    await eventually(seen_asked, 8, "the frame a person can see asks Meridian about its fields")
    await asyncio.sleep(1.5)  # ... and the hidden ones had every chance to
    for variant in ("transparent", "tiny", "inert", "away", "covered"):
        frame = next(f for f in page.frames if f.url.endswith(f"v={variant}"))
        assert not bg.suggest_requests(f"f{bg.frame_id(frame)}"), f"the {variant} frame asked Meridian about its fields"
        state = await debug(frame)
        assert state["sessionId"] is None and state["frameHidden"] is True, (variant, state["sessionId"], state["frameHidden"])
    # a panel item (or the end of the panel's Esc) that names a field of a hidden frame never moves the keyboard there
    transparent = next(f for f in page.frames if f.url.endswith("v=transparent"))
    key = (await debug(transparent))["fields"][0]["key"]
    await emit(page, {"type": "focus-item", "key": key})
    await asyncio.sleep(0.6)
    assert await page.evaluate("document.activeElement && document.activeElement.id") != "transparent", "the keyboard stayed out of the frame nobody can see"
    assert not await transparent.evaluate("document.hasFocus() && document.activeElement && document.activeElement.tagName === 'INPUT'")


@scenario("a_form_that_only_a_frame_nobody_can_see_holds_does_not_wake_an_assistant_that_waits_for_a_form")
async def hidden_frame_does_not_wake(ctx: Ctx) -> None:
    page, bg = await ctx.open("hidden_frame_only.html", {"email": T("alex@example.test")}, auto=True, ready=False, wait_frames=1)
    await asyncio.sleep(3.0)  # the frame scanned itself, said hello and sent its digests long ago
    state = await debug(page)
    assert state["quiet"] is True and state["mounted"] is False, "the page still looks like a page without a form"
    assert bg.session_open_count == 0 and not bg.msgs("session:open"), "nothing left the page for it"
    assert not await ui_calls(page, "setPanel")
    # the same when the frame never hears that it is hidden and reports its fields all the same: the top frame does not take them for a form
    bg2 = Bg({"email": T("alex@example.test")})
    bg2.relay_drop = lambda payload: payload.get("k") == "frame-hints"
    page2, bg2 = await ctx.open("hidden_frame_only.html", bg=bg2, auto=True, ready=False, wait_frames=1)
    await asyncio.sleep(3.0)
    child = next(f for f in page2.frames if f != page2.main_frame)
    assert (await debug(child))["fields"], "the frame did scan its form and reported it"
    state = await debug(page2)
    assert state["quiet"] is True and bg2.session_open_count == 0, "a form nobody can see does not make the page one"


# ------------------------------------------------------------------------------------------------------------------------
# the real visual layer honours the contract of this wave (the chords, the confirmation row, the duplicate note)


@scenario("real_visual_layer_runs_the_numbered_chip_button_with_the_chord_and_never_with_option_digit")
async def real_chip_chord(ctx: Ctx) -> None:
    first = "input[name='job_application[first_name]']"
    for platform, chord, loose in (("other", "Alt+Shift+1", ("Alt+1", "Control+Alt+1")), ("mac", "Control+Alt+1", ("Alt+1", "Alt+Shift+1"))):
        page, bg = await ctx.open("greenhouse.html", GH, headless_ui=False, init=PLATFORM[platform])
        await page.fill(first, "Smith")  # the chip offers Replace (button 1); the person's own text is never overwritten by anything else
        await page.focus(first)
        replace = page.locator("[data-testid=chip-replace]")
        await replace.wait_for(state="visible", timeout=6000)
        for key in loose:
            await page.keyboard.press(key)
        await asyncio.sleep(0.3)
        assert await value_of(page, first) == "Smith", f"{platform}: {loose} is not the chord"
        await page.keyboard.press(chord)
        await eventually(lambda: _is(page, first, "Alex"), 4, f"{platform}: {chord} pressed the numbered button")


async def _is(page: Any, selector: str, expected: str) -> bool:
    return (await value_of(page, selector)) == expected


@scenario("real_visual_layer_lists_what_fill_verified_will_fill_and_fills_only_on_the_confirmation")
async def real_fill_preview(ctx: Ctx) -> None:
    page, bg = await ctx.open("greenhouse.html", {**GH, "phone": T("+44 7700 900123")}, headless_ui=False, viewport=(1280, 900))
    button = page.locator("[data-testid=panel-fill-verified]")
    await button.wait_for(state="visible", timeout=6000)
    await eventually(lambda: _enabled(button), 6, "the panel counts what is fillable")
    await button.click()
    row = page.locator("[data-testid=panel-fill-preview]")
    await row.wait_for(state="visible", timeout=4000)
    text = await row.inner_text()
    for label, value in (("First name", "Alex"), ("Last name", "Newport"), ("Email", "alex@example.test")):
        assert label in text and value in text, (label, value, text)
    await asyncio.sleep(0.3)
    assert await value_of(page, FIRST) == "", "listing is not filling"
    await page.locator("[data-testid=fill-confirm]").click()
    await eventually(lambda: _filled(page, [FIRST, LAST, EMAIL]), 5, "the confirmation fills")
    await row.wait_for(state="hidden", timeout=4000)


async def _enabled(locator: Any) -> bool:
    return await locator.is_enabled()


@scenario("an_answer_the_server_never_saves_gets_no_save_prompt_in_any_save_mode")
async def never_saved(ctx: Ctx) -> None:
    for mode in ("ask", "always"):
        answers = {**GH, "first name": T("Alex", flags={"never_saved": "card"})}
        page, bg = await ctx.open("greenhouse.html", bg=Bg(answers, settings={"save_corrections": mode}))
        await page.focus(FIRST)
        await chip_for(page, "first name")
        await page.click(FIRST)  # typed by the person: a pointer, then keys (text that arrives with no key or pointer before it is not theirs: LOW-1)
        await page.keyboard.type("Zed")
        await tab(page)
        await asyncio.sleep(0.6)
        assert not [c for c in await ui_log(page, "askSave") if c["arg"]], f"{mode}: a prompt for an answer that is never saved"
        assert not bg.msgs("feedback"), f"{mode}: and nothing was sent"
        # the same page, a field the server did not mark: it asks (or saves) as before
        await page.focus(LAST)
        await chip_for(page, "last name")
        await page.click(LAST)
        await page.keyboard.type("Smith")
        await tab(page)
        await eventually(lambda: _asked_or_saved(page, bg, mode), 4, f"{mode}: the other field is offered for saving as before")


async def _asked_or_saved(page: Any, bg: Bg, mode: str) -> bool:
    if mode == "always":
        return bool(bg.msgs("feedback"))
    return bool([c for c in await ui_log(page, "askSave") if c["arg"]])
