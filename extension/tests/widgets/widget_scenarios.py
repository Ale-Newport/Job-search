"""Scenarios of the real-widget suite. Each page is a REAL widget library (or a faithful hand-rolled replica of an ATS widget) and
every widget is driven the way a person uses the product: focus the field, press the real Tab key, and the assistant inserts
the canned suggestion and moves on. What is asserted is the widget's OWN state (React/Vue state, the hidden inputs the
form would post, the displayed selection, FormData) and where focus went, never only the assistant's view of it."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from widget_rig import (
    BOX, BOXES, COMBO, FILE, RADIO, SELECT, T, Bg, Recorder, Soft, WidgetRig, debug, eventually, press_fill_verified,
    make_document, normalize,
)


@dataclass
class Scenario:
    name: str
    fn: Callable[["Ctx"], Awaitable[None]]
    webkit: bool = True
    world: str = "isolated"


SCENARIOS: list[Scenario] = []


def scenario(name: str | None = None, *, webkit: bool = True) -> Callable[[Callable[["Ctx"], Awaitable[None]]], Callable[["Ctx"], Awaitable[None]]]:
    def register(fn: Callable[["Ctx"], Awaitable[None]]) -> Callable[["Ctx"], Awaitable[None]]:
        SCENARIOS.append(Scenario(name or fn.__name__, fn, webkit))
        return fn

    return register


class Ctx(Soft):
    def __init__(self, rig: WidgetRig, recorder: Recorder, scenario_name: str, world: str) -> None:
        super().__init__(recorder, scenario_name)
        self.rig = rig
        self.world = world

    async def open(self, path: str, answers: dict[str, Any] | None = None, *, bg: Bg | None = None, **kw: Any):
        kw.setdefault("world", self.world)
        return await self.rig.open(path, bg or Bg(answers), **kw)


# ------------------------------------------------------------------------------------------------------ driving helpers


async def deep_active(page: Any) -> dict[str, Any]:
    """The element holding focus (through open shadow roots): id / name / tag / aria-label."""
    return await page.evaluate("""() => { let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
        return a ? {id: a.id || '', name: a.getAttribute('name') || '', tag: a.tagName.toLowerCase(), label: a.getAttribute('aria-label') || '',
                    role: a.getAttribute('role') || '', type: a.getAttribute('type') || ''} : null; }""")


async def idle(page: Any, timeout: float = 10.0) -> None:
    """Waits for the assistant's asynchronous fill (custom comboboxes, file attach) to finish."""
    await asyncio.sleep(0.05)

    async def done() -> bool:
        state = await debug(page)
        return bool(state) and not state["busy"]

    await eventually(done, timeout, "the assistant finished filling")


async def field_by(page: Any, label_part: str) -> dict[str, Any]:
    state = await debug(page)
    assert state is not None, "the assistant is not running in this frame"
    for f in state["fields"]:
        if normalize(label_part) in normalize(f["label"]):
            return f
    raise AssertionError(f"no assist field labelled {label_part!r}; scanned: {[f['label'] for f in state['fields']]}")


async def ready_field(page: Any, label_part: str, timeout: float = 5.0) -> dict[str, Any]:
    """The scanned field plus its suggestion has arrived (the Tab decision is made from this cache)."""

    async def has() -> Any:
        try:
            f = await field_by(page, label_part)
        except AssertionError:
            return None
        return f if f.get("hasEntry") else None

    return await eventually(has, timeout, f"a suggestion for {label_part!r}")


async def fill_with_tab(page: Any, selector: str, label_part: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """focus -> (suggestion cached) -> real Tab -> wait for the fill. Returns the assistant's record of the field afterwards
    (`gone: True` when a widget that hides its control after a selection dropped out of the scan) plus `seconds` and the
    error toasts shown meanwhile."""
    await ready_field(page, label_part)
    await page.focus(selector)
    await eventually(lambda: _is_active(page, selector), 3, f"focus on {selector}")
    toasts_before = len(await error_toasts(page))
    started = time.time()
    await page.keyboard.press("Tab")
    await idle(page, timeout)
    seconds = time.time() - started
    await asyncio.sleep(0.34)  # the assistant re-reads the field 280 ms after every insertion and reports a value the page took back
    try:
        record = dict(await field_by(page, label_part))
    except AssertionError:
        record = {"gone": True, "failed": None, "inserted": None}
    record["seconds"] = round(seconds, 2)
    record["errors"] = (await error_toasts(page))[toasts_before:]
    return record


async def error_toasts(page: Any) -> list[str]:
    from widget_rig import ui_log

    return [str(c["arg"].get("params")) for c in await ui_log(page, "toast") if c["arg"].get("kind") == "error"]


async def _is_active(page: Any, selector: str) -> bool:
    return await page.evaluate(
        """(s) => { let a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
                    return !!a && a.matches(s); }""", selector)


async def focused_id(page: Any) -> str:
    return (await deep_active(page))["id"]


async def state_of(page: Any) -> dict[str, Any]:
    return await page.evaluate("window.__state()")


async def form_data(page: Any, selector: str | None = None) -> dict[str, Any]:
    return await page.evaluate("(s) => window.__formData(s)", selector)


def expect(cond: Any, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def eq(actual: Any, wanted: Any, what: str) -> None:
    if actual != wanted:
        raise AssertionError(f"{what}: wanted {json.dumps(wanted)}, got {json.dumps(actual)}")


async def wait_eq(read: Callable[[], Awaitable[Any]], wanted: Any, what: str, timeout: float = 4.0) -> None:
    last: list[Any] = []

    async def check() -> bool:
        last[:] = [await read()]
        return last[0] == wanted

    try:
        await eventually(check, timeout, what)
    except AssertionError:
        raise AssertionError(f"{what}: wanted {json.dumps(wanted)}, got {json.dumps(last[0] if last else None)}") from None


# ------------------------------------------------------------------------------------------------------ react-select


async def combo_case(page: Any, selector: str, label: str, next_id: str | None, *, max_seconds: float = 8.0) -> dict[str, Any]:
    """Tab on a custom combobox: the fill must succeed (no failure, no error toast), be quick, and focus must move on to `next_id`."""
    field = await fill_with_tab(page, selector, label)
    expect(not field["failed"], f"the assistant reported a failed fill: {field['failed']}")
    expect(not field["errors"], f"error toast shown: {field['errors']}")
    expect(field.get("gone") or field["inserted"], f"the assistant did not record an insertion for {label}: {field}")
    print(f"      {label}: filled in {field['seconds']}s", flush=True)
    expect(field["seconds"] <= max_seconds, f"the fill took {field['seconds']}s (limit {max_seconds}s)")
    if next_id is not None:
        await wait_eq(lambda: focused_id(page), next_id, f"focus after Tab on {label}", 3)
    return field


RS_ANSWERS: dict[str, Any] = {
    "preferred name": T("Alex"),
    "country": COMBO("United Kingdom", "United Kingdom"),
    "legally authorized": COMBO("Yes", "Yes"),
    "languages you speak": COMBO("Spanish", "Spanish"),
    "current city": COMBO("Madrid, Spain", "Madrid"),
    "where are you based": COMBO("London, England, United Kingdom", "London"),
    "nationality": COMBO("United Kingdom", "United Kingdom"),
    "preferred office": COMBO("Madrid office", "Madrid office"),
    "seniority": COMBO("Senior", "Senior"),
    "time zone": COMBO("Europe/Madrid", "Europe/Madrid"),
    "how did you hear": COMBO("Referral", "Referral"),
}


@scenario("react_select_greenhouse_job_boards")
async def react_select_greenhouse(ctx: Ctx) -> None:
    page, bg = await ctx.open("react_select.html", RS_ANSWERS)
    async with ctx.widget("react-select 5 single (Greenhouse markup, classNamePrefix=select)"):
        await combo_case(page, "#country", "Country", "auth")
        await wait_eq(lambda: state_of(page), {"country": "gb"}, "react-select state after Country", 2)
        eq((await form_data(page))["country"], "gb", "hidden input value posted for country")
        eq(await page.evaluate("document.querySelector('.select__single-value').textContent"), "United Kingdom", "displayed selection")
    async with ctx.widget("react-select 5 single Yes/No (required)"):
        await combo_case(page, "#auth", "legally authorized", "languages")
        eq((await form_data(page))["auth"], "yes", "hidden input value posted for auth")
    async with ctx.widget("react-select 5 multi"):
        await combo_case(page, "#languages", "Languages you speak", "city")
        eq((await state_of(page))["languages"], ["spanish"], "react-select multi state")
    async with ctx.widget("react-select 5 creatable (existing option)"):
        await combo_case(page, "#city", "Current city", "based")
        eq((await form_data(page))["city"], "madrid-spain", "creatable hidden input")
    async with ctx.widget("react-select 5 async (type to load options)"):
        await combo_case(page, "#based", "Where are you based", "nationality")
        eq((await form_data(page))["based"], "london-england-united-kingdom", "async hidden input")
    async with ctx.widget("react-select 5 + react-window virtualised 1,000 options"):
        await combo_case(page, "#nationality", "Nationality", "office")
        eq((await form_data(page))["nationality"], "united-kingdom", "virtualised hidden input")
    async with ctx.widget("react-select 5 grouped options"):
        await combo_case(page, "#office", "Preferred office", "seniority")
        eq((await form_data(page))["office"], "madrid-office", "grouped hidden input")
    async with ctx.widget("react-select 5 default markup (no classNamePrefix)"):
        await combo_case(page, "#seniority", "Seniority", None)
        eq((await form_data(page))["seniority"], "senior", "unprefixed hidden input")
    async with ctx.widget("react-select 5 options loaded after the menu opened"):
        await combo_case(page, "#timezone", "Time zone", "source")
        eq((await form_data(page))["timezone"], "europe-madrid", "late options hidden input")
    async with ctx.widget("react-select 5 options re-created while open"):
        await combo_case(page, "#source", "How did you hear", None)
        eq((await form_data(page))["source"], "referral", "rotating options hidden input")


# ------------------------------------------------------------------------------------------------------ Headless UI


@scenario("headless_ui_combobox_listbox_radio_cards_checkbox_switch")
async def headless_ui(ctx: Ctx) -> None:
    answers = {
        "preferred name": T("Alex"), "country of residence": COMBO("Spain", "Spain"), "preferred work mode": COMBO("Hybrid", "Hybrid"),
        "seniority": COMBO("Senior", "Senior"), "employment type": RADIO("Part-time", "Part-time"), "send me job alerts": BOX(True),
        "open to remote work": BOX(True),
    }
    page, bg = await ctx.open("headless.html", answers)
    async with ctx.widget("Headless UI 2 Combobox (type to filter, options in a portal)"):
        await combo_case(page, "#country", "Country of residence", None)
        eq((await state_of(page)).get("country"), "Spain", "Headless UI Combobox value")
        eq(await page.evaluate("document.querySelector('#country').value"), "Spain", "displayed input value")
        eq((await form_data(page)).get("country"), "Spain", "hidden input posted by the Combobox")
    async with ctx.widget("Headless UI 2 Listbox (button + popup)"):
        await combo_case(page, "#mode", "Preferred work mode", None)
        eq((await state_of(page)).get("mode"), "Hybrid", "Listbox value")
    async with ctx.widget("Headless UI 2 RadioGroup as selectable cards"):
        await fill_with_tab(page, "[role=radio]", "Employment type")
        await wait_eq(lambda: state_of(page), {**await state_of(page), "plan": "part"}, "RadioGroup state", 2)
        eq((await form_data(page)).get("plan"), "part", "hidden radio input posted by the RadioGroup")
    async with ctx.widget("Headless UI 2 Checkbox (span role=checkbox)"):
        field = await fill_with_tab(page, "[role=checkbox]", "Send me job alerts")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["alerts"], True, "Checkbox state")
        eq((await form_data(page)).get("alerts"), "on", "hidden checkbox input posted")
    async with ctx.widget("Headless UI 2 Switch"):
        field = await fill_with_tab(page, "[role=switch]", "Open to remote work")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["remote"], True, "Switch state")


# ------------------------------------------------------------------------------------------------------ Material UI


@scenario("mui_select_autocomplete_textfield_checkbox_radio_switch")
async def mui(ctx: Ctx) -> None:
    answers = {
        "first name": T("Alex"), "email address": T("alex@example.test"), "country": COMBO("United Kingdom", "United Kingdom"),
        "where are you based": COMBO("Madrid, Spain", "Madrid"), "notice period": SELECT("1 month", "1m"),
        "legally authorised": RADIO("Yes", "yes"), "send me job alerts": BOX(True), "open to remote work": BOX(True),
        "short bio": T("Backend engineer who likes data and clear APIs."),
    }
    page, bg = await ctx.open("mui.html", answers)
    async with ctx.widget("MUI 5 TextField (controlled)"):
        field = await fill_with_tab(page, "#first", "First name")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["first"], "Alex", "React state of the controlled TextField")
        eq(await focused_id(page), "email", "focus after Tab")
        field = await fill_with_tab(page, "#email", "Email address")
        eq((await state_of(page))["email"], "alex@example.test", "React state of the email TextField")
    async with ctx.widget("MUI 5 Select (div role=combobox + Menu popover portal)"):
        await combo_case(page, "#country", "Country", None)
        eq((await state_of(page))["country"], "united-kingdom", "MUI Select value")
        eq((await form_data(page)).get("country"), "united-kingdom", "hidden input posted by MUI Select")
        eq(await page.evaluate("document.querySelector('#country').textContent"), "United Kingdom", "displayed selection")
    async with ctx.widget("MUI 5 Autocomplete (typing filters, popper listbox)"):
        await combo_case(page, "#city", "Where are you based", None)
        eq((await state_of(page))["city"], "Madrid, Spain", "Autocomplete value")
        eq(await page.evaluate("document.querySelector('#city').value"), "Madrid, Spain", "displayed input value")
    async with ctx.widget("MUI 5 NativeSelect"):
        field = await fill_with_tab(page, "#notice", "Notice period")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["notice"], "1m", "React state of the NativeSelect")
    async with ctx.widget("MUI 5 RadioGroup (visually hidden native radios)"):
        field = await fill_with_tab(page, "input[name=auth]", "legally authorised")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["auth"], "yes", "React state of the RadioGroup")
    async with ctx.widget("MUI 5 Checkbox (controlled)"):
        field = await fill_with_tab(page, "input[name=alerts]", "Send me job alerts")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["alerts"], True, "React state of the Checkbox")
    async with ctx.widget("MUI 5 Switch (controlled)"):
        field = await fill_with_tab(page, "input[name=remote]", "Open to remote work")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["remote"], True, "React state of the Switch")
    async with ctx.widget("MUI 5 consent checkbox is never ticked by Tab, even when the server offers a value"):
        answers_page, _ = await ctx.open("mui.html", {"privacy policy": BOX(True), "send me job alerts": BOX(True)})
        field = await fill_with_tab(answers_page, "input[name=consent]", "privacy policy")
        expect(not field["inserted"] and not field["failed"], f"consent must be left alone: {field}")
        eq((await state_of(answers_page))["consent"], False, "React state of the consent checkbox")
        eq(await answers_page.evaluate("document.querySelector('input[name=consent]').checked"), False, "checked in the DOM")
    async with ctx.widget("MUI 5 multiline TextField with character counter and maxLength"):
        field = await fill_with_tab(page, "#bio", "Short bio")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page))["bio"], "Backend engineer who likes data and clear APIs.", "React state of the multiline TextField")
        helper = await page.evaluate("document.querySelector('#bio').closest('.MuiFormControl-root').querySelector('.MuiFormHelperText-root').textContent")
        eq(helper, f"{len('Backend engineer who likes data and clear APIs.')}/300", "the counter re-rendered from the inserted text")


# ------------------------------------------------------------------------------------------------------ Downshift


@scenario("downshift_use_combobox_and_use_select")
async def downshift(ctx: Ctx) -> None:
    answers = {"preferred name": T("Alex"), "country of residence": COMBO("Germany", "Germany"), "seniority level": COMBO("Senior", "Senior")}
    page, bg = await ctx.open("downshift.html", answers)
    async with ctx.widget("Downshift 9 useCombobox (input + toggle, listbox only while open)"):
        await combo_case(page, "input[name=country]", "Country of residence", None)
        eq((await state_of(page)).get("country"), "Germany", "Downshift selectedItem")
        eq(await page.evaluate("document.querySelector('input[name=country]').value"), "Germany", "displayed input value")
    async with ctx.widget("Downshift 9 useSelect (<button role=combobox>)"):
        await combo_case(page, "#level", "Seniority level", None)
        eq((await state_of(page)).get("level"), "Senior", "Downshift useSelect selectedItem")


# ------------------------------------------------------------------------------------------------------ intl-tel-input


@scenario("intl_tel_input_country_flag_dropdown_and_national_number")
async def intl_tel_input(ctx: Ctx) -> None:
    answers = {"preferred name": T("Alex"), "mobile phone": T("+44 7700 900123"), "contact phone": T("+34 612 345 678"),
               "international format": T("+1 202 555 0123"), "country kept in a hidden field": T("+44 7700 900123")}
    page, bg = await ctx.open("intl_tel_input.html", answers)

    def dial_codes(shown: str, code: str) -> int:
        return len(__import__("re").findall(r"\+\s*" + code, shown))

    async def phone(selector: str, label: str, e164: str, country: str, digits: str, target: Any = None) -> None:
        target = target or page
        field = await fill_with_tab(target, selector, label)
        expect(not field["failed"] and not field["errors"], f"the assistant reported a failed fill: {field['failed']} {field['errors']}")
        state = (await state_of(target))[selector.lstrip("#")]
        eq(state["e164"], e164, "intl-tel-input getNumber() (what the form would submit)")
        eq(state["country"], country, "selected flag")
        expect(state["shown"].replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "").lstrip("0").endswith(digits),
               f"visible number {state['shown']!r} must end with the national digits {digits}")
        expect(dial_codes(state["shown"], state["dial"]) <= 1, f"dial code duplicated in {state['shown']!r}")

    async with ctx.widget("intl-tel-input 29 national mode (+44 typed, flag GB, trunk 0 formatting)"):
        await phone("#phone_default", "Mobile phone", "+447700900123", "gb", "7700900123")
        eq(await focused_id(page), "phone_separate", "focus after Tab")
    async with ctx.widget("intl-tel-input 29 separateDialCode (dial code displayed outside the input)"):
        await phone("#phone_separate", "Contact phone", "+34612345678", "es", "612345678")
    async with ctx.widget("intl-tel-input 29 international format (nationalMode off)"):
        await phone("#phone_international", "international format", "+12025550123", "us", "2025550123")
    async with ctx.widget("intl-tel-input 29 + hidden phone_country field: national-only input, dial code dropped, not duplicated"):
        await ready_field(page, "country kept in a hidden field")
        info = [e for r in bg.suggest_requests() for e in r["elements"] if "hidden field" in e["label"]][0]
        eq((info["phone_national_only"], info["phone_country_code"]), (True, "44"), "what the scanner told the bridge about the field")
        field = await fill_with_tab(page, "#phone_hidden_country", "country kept in a hidden field")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = (await state_of(page))["phone_hidden_country"]
        eq((state["e164"], state["country"], state["shown"], state["hidden"]), ("+447700900123", "gb", "7700 900123", "United Kingdom (+44)"), "widget state")
    page2, _ = await ctx.open("intl_tel_input.html", {"mobile phone": T("+34 612 345 678")})
    async with ctx.widget("intl-tel-input 29 typed dial code differs from the flag (flag switches, nothing duplicated)"):
        await phone("#phone_default", "Mobile phone", "+34612345678", "es", "612345678", page2)


# ------------------------------------------------------------------------------------------------------ dates


@scenario("react_datepicker_native_date_and_month_inputs")
async def datepickers(ctx: Ctx) -> None:
    answers = {"earliest start": T("2026-11-01"), "graduation month": T("06/2024"), "date of birth": T("15/03/1990"),
               "available from": T("2026-11-01"), "working in the field": T("2024-06")}
    page, bg = await ctx.open("datepicker.html", answers)
    async with ctx.widget("react-datepicker 9 typed yyyy-MM-dd"):
        field = await fill_with_tab(page, "#start", "Earliest start")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        await wait_eq(lambda: state_of(page), {**await state_of(page), "start": "2026-11-01"}, "react-datepicker selected date", 2)
        eq(await focused_id(page), "graduated", "focus after Tab")
    async with ctx.widget("react-datepicker 9 month picker, day-less answer (MM/yyyy)"):
        field = await fill_with_tab(page, "#graduated", "Graduation month")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["graduated"], "2024-06", "react-datepicker month value")
    async with ctx.widget("react-datepicker 9 typed dd/MM/yyyy"):
        field = await fill_with_tab(page, "#birth", "Date of birth")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["birth"], "1990-03-15", "react-datepicker selected date")
        await asyncio.sleep(0.6)  # blur happened; the picker must still hold the value after the focus moved on
        eq((await state_of(page))["birth"], "1990-03-15", "react-datepicker selected date after blur")
        eq(await page.evaluate("document.querySelector('#birth').value"), "15/03/1990", "displayed value after blur")
    async with ctx.widget("native <input type=date>"):
        field = await fill_with_tab(page, "#available", "Available from")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["native"]["available"], "2026-11-01", "React state of the native date input")
    async with ctx.widget("native <input type=month> (day-less answer)"):
        field = await fill_with_tab(page, "#since", "working in the field")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["native"]["since"], "2024-06", "React state of the native month input")
    # a day-less answer for a field that needs a day (or the reverse) is never claimed: it is refused and reported
    page2, _ = await ctx.open("datepicker.html", {"graduation month": T("2024-06"), "available from": T("2026-11")})
    async with ctx.widget("day-less answer in the wrong format is reported, never silently lost (month picker)"):
        field = await fill_with_tab(page2, "#graduated", "Graduation month")
        await eventually(lambda: _reported(page2, field), 3, "the assistant reports the lost value")
        eq((await state_of(page2))["graduated"], None, "nothing was selected")
        eq(await page2.evaluate("document.querySelector('#graduated').value"), "", "the field is left empty")
    async with ctx.widget("day-less answer on a native date input is refused and reported"):
        field = await fill_with_tab(page2, "#available", "Available from")
        expect(field["failed"] or field["errors"], "the refusal must be reported")
        eq(await page2.evaluate("document.querySelector('#available').value"), "", "native date left empty")


@scenario("react_datepicker_gets_its_own_tab_so_the_calendar_closes_and_never_stays_over_the_next_field")
async def datepicker_calendar_closes(ctx: Ctx) -> None:
    """The assistant used to swallow the Tab a date picker closes its calendar on: the calendar then stayed open over the NEXT field, which was
    written into under it. Now a popup the page opened next to the field gets the key: the answer goes in, the widget closes itself on the Tab it
    sees and the browser moves on."""
    answers = {"earliest start": T("2026-11-01"), "graduation month": T("06/2024"), "date of birth": T("15/03/1990"), "preferred name": T("Alex")}
    page, bg = await ctx.open("datepicker.html", answers)
    day_calendars = "document.querySelectorAll('.react-datepicker__day-names').length"
    poppers = "document.querySelectorAll('.react-datepicker-popper').length"
    async with ctx.widget("react-datepicker 9: Tab inserts the date, the field's own calendar closes, and only the next field's calendar is open"):
        await ready_field(page, "Earliest start")
        await page.focus("#start")
        await eventually(lambda: page.evaluate(f"{day_calendars} === 1"), 3, "the calendar opens when the date field takes the focus")
        toasts_before = len(await error_toasts(page))
        await page.keyboard.press("Tab")
        await idle(page)
        await asyncio.sleep(0.4)
        eq((await state_of(page))["start"], "2026-11-01", "react-datepicker selected date")
        eq(await focused_id(page), "graduated", "focus after Tab")
        eq(await page.evaluate(day_calendars), 0, "the day calendar of the first date field is gone")
        eq(await page.evaluate(poppers), 1, "the calendars open on the page (the month picker of the field the person is on)")
        eq(len(await error_toasts(page)), toasts_before, "no error toast")
    async with ctx.widget("react-datepicker 9: walking the three date pickers with Tab alone leaves nothing open and nothing unwritten"):
        await ready_field(page, "Graduation month")
        for expected in ("birth", "available"):
            await page.keyboard.press("Tab")
            await idle(page)
            await asyncio.sleep(0.4)
            eq(await focused_id(page), expected, "focus after Tab")
        state = await state_of(page)
        eq((state["start"], state["graduated"], state["birth"]), ("2026-11-01", "2024-06", "1990-03-15"), "react-datepicker selected dates")
        eq(await page.evaluate(poppers), 0, "no calendar is left open when the person reaches the native date input")


async def _reported(page: Any, field: dict[str, Any]) -> bool:
    """True once the assistant reports a failed insertion for this field (immediately or by the delayed re-check)."""
    if field["failed"] or field["errors"]:
        return True
    return bool(await error_toasts(page)) or bool((await field_by(page, field["label"]))["failed"])


# ------------------------------------------------------------------------------------------------------ react-dropzone


@scenario("react_dropzone_hidden_file_input_and_visible_button")
async def dropzone(ctx: Ctx) -> None:
    doc = make_document()
    ref = {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}
    answers = {"cover letter": FILE(ref), "resume": FILE(ref), "preferred name": T("Alex")}
    page, bg = await ctx.open("dropzone.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    async with ctx.widget("react-dropzone 20 (label root, hidden input, visible 'Choose file' button)"):
        field = await fill_with_tab(page, "input[name=cover]", "Cover letter")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        await wait_eq(lambda: state_of(page), {"resume": [], "cover": ["Alex-CV.pdf"]}, "react-dropzone onDrop state", 3)
        eq((await form_data(page))["cover"], f"file:Alex-CV.pdf:{doc['size']}", "file posted by the form")
        eq(await page.evaluate("document.querySelector('.files li').textContent"), "Alex-CV.pdf", "file name shown by the widget")
    async with ctx.widget("react-dropzone 20 (div root, hidden input): the visible drop area is the proxy of the file field"):
        names = [f["label"] for f in (await debug(page))["fields"]]
        expect(any("resume" in n.lower() for n in names), f"the Resume / CV file field is not an assist field: {names}")
        field = await fill_with_tab(page, "input[name=resume]", "resume")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        await wait_eq(lambda: state_of(page), {"resume": ["Alex-CV.pdf"], "cover": ["Alex-CV.pdf"]}, "react-dropzone onDrop state of both zones", 3)
        eq((await form_data(page))["resume"], f"file:Alex-CV.pdf:{doc['size']}", "file posted by the form")


# ------------------------------------------------------------------------------------------------------ Vue


@scenario("vue3_v_model_inputs_selects_radios_checkboxes")
async def vue3(ctx: Ctx) -> None:
    answers = {
        "first name": T("Alex"), "email": T("alex@example.test"), "years of experience": T("7"), "reference code": T("REF-123"),
        "short bio": T("Backend engineer."), "country": SELECT("Spain", "es"), "skills": BOXES("Python", "Go"),
        "authorised to work": RADIO("Yes", "yes"), "send me job alerts": BOX(True), "languages": BOXES("English", "Spanish"),
        "nickname": T("Al"),
    }
    page, bg = await ctx.open("vue_app.html", answers)

    async def tab_field(selector: str, label: str) -> dict[str, Any]:
        field = await fill_with_tab(page, selector, label)
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        return field

    async with ctx.widget("Vue 3 v-model text + v-model.trim email"):
        await tab_field("#v_first", "First name")
        await tab_field("#v_email", "Email")
        state = await state_of(page)
        eq((state["first"], state["email"]), ("Alex", "alex@example.test"), "Vue model")
    async with ctx.widget("Vue 3 v-model.number"):
        await tab_field("#v_years", "Years of experience")
        eq((await state_of(page))["years"], 7, "Vue numeric model")
    async with ctx.widget("Vue 3 v-model.lazy (updates on the change event)"):
        await tab_field("#v_lazy", "Reference code")
        eq((await state_of(page))["lazy"], "REF-123", "Vue lazy model")
    async with ctx.widget("Vue 3 v-model textarea, select and multiple select"):
        await tab_field("#v_bio", "Short bio")
        await tab_field("#v_country", "Country")
        await tab_field("#v_skills", "Skills")
        state = await state_of(page)
        eq((state["bio"], state["country"], state["skills"]), ("Backend engineer.", "es", ["py", "go"]), "Vue model")
    async with ctx.widget("Vue 3 v-model radio, checkbox and checkbox group (array)"):
        await tab_field("input[name=auth]", "authorised to work")
        await tab_field("input[name=alerts]", "Send me job alerts")
        await tab_field("input[name=langs]", "Languages")
        state = await state_of(page)
        eq((state["auth"], state["alerts"], state["langs"]), ("yes", True, ["en", "es"]), "Vue model")
    async with ctx.widget("Vue 3 custom component with v-model"):
        await tab_field("#v_fancy", "Nickname")
        eq((await state_of(page))["fancy"], "Al", "Vue component model")


# ------------------------------------------------------------------------------------------------------ form libraries and masks


@scenario("react_hook_form_formik_masks_counters_and_rerendering_inputs")
async def forms(ctx: Ctx) -> None:
    bio = "Backend engineer who builds reliable data pipelines and clear APIs for small teams."
    answers = {
        "preferred name": T("Alex"), "first name": T("Alex"), "last name": T("Newport"), "about you": T("I build things."), "expected salary": T("85000"),
        "postcode": T("sw1a 1aa"), "initials": T("A.N.D"), "years of experience": T("7"), "short bio": T(bio), "website": T("example.test"),
        "email": T("alex@example.test"), "city": T("London"),
    }
    page, bg = await ctx.open("forms.html", answers)

    async def tab_field(selector: str, label: str) -> dict[str, Any]:
        field = await fill_with_tab(page, selector, label)
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        return field

    async with ctx.widget("react-hook-form 7 (uncontrolled inputs registered by ref)"):
        await tab_field("#rhf_first", "First name")
        await tab_field("#rhf_last", "Last name")
        await tab_field("#rhf_about", "About you")
        values = (await state_of(page))["rhf"]
        eq(values, {"first": "Alex", "last": "Newport", "about": "I build things."}, "react-hook-form getValues()")
    async with ctx.widget("Formik 2 controlled Field"):
        await tab_field("#fk_email", "Email")
        await tab_field("#fk_city", "City")
        eq((await state_of(page))["formik"], {"email": "alex@example.test", "city": "London"}, "Formik values")
    async with ctx.widget("thousands-separator mask (85000 -> 85,000)"):
        await tab_field("#salary", "Expected salary")
        eq((await state_of(page))["masks"]["salary"], "85,000", "mask output held in React state")
        eq(await page.evaluate("document.querySelector('#salary').value"), "85,000", "displayed value")
    async with ctx.widget("input that upper-cases on every input"):
        await tab_field("#postcode", "Postcode")
        eq((await state_of(page))["masks"]["postcode"], "SW1A 1AA", "React state")
    async with ctx.widget("input that strips characters on input (A.N.D -> AND)"):
        await tab_field("#initials", "Initials")
        eq((await state_of(page))["masks"]["initials"], "AND", "React state")
    async with ctx.widget("controlled number input"):
        await tab_field("#years", "Years of experience")
        eq((await state_of(page))["masks"]["years"], "7", "React state")
    async with ctx.widget("textarea with live character counter and maxLength"):
        await tab_field("#bio", "Short bio")
        eq(await page.evaluate("document.querySelector('#bio_counter').textContent"), f"{len(bio)}/140", "counter re-rendered from the inserted text")
        eq((await state_of(page))["masks"]["bio"], bio, "React state")
    async with ctx.widget("input validated on blur that re-renders (remounts) itself when invalid"):
        await tab_field("#site", "Website")
        await eventually(lambda: page.evaluate("document.querySelector('#site_error').textContent !== ''"), 2, "the page validated on blur")
        eq(await page.evaluate("document.querySelector('#site').value"), "example.test", "the remounted input still shows the inserted text")
        await asyncio.sleep(0.6)  # the assistant's delayed re-check ran against the replaced element: no false failure
        eq(await error_toasts(page), [], "no error toast")
    page2, _ = await ctx.open("forms.html", {"initials": T("Alexander"), "short bio": T("x" * 200), "postcode": T("sw1a 1aa")})
    async with ctx.widget("a value the page truncates is refused, restored and reported (never claimed)"):
        field = await fill_with_tab(page2, "#initials", "Initials")
        expect(field["failed"] or field["errors"], "a truncated value must be reported")
        eq((await state_of(page2))["masks"]["initials"], "", "React state is back to the previous (empty) value")
        eq(await page2.evaluate("document.querySelector('#initials').value"), "", "displayed value is back to the previous (empty) value")
    async with ctx.widget("only a phone field may lose a country-code-sized prefix: the same loss in a customer number is a rewrite and is refused"):
        page4, _ = await ctx.open("forms.html", {"customer number": T("4912345678")})
        field = await fill_with_tab(page4, "#cust", "Customer number")
        expect(field["failed"] or field["errors"], f"a number that lost its first digits must be reported: {field}")
        eq((await state_of(page4))["masks"]["cust"], "", "React state is back to the previous (empty) value")
    async with ctx.widget("text longer than maxLength is refused, never cut"):
        field = await fill_with_tab(page2, "#bio", "Short bio")
        expect(field["failed"] or field["errors"], "too long must be reported")
        eq(await page2.evaluate("document.querySelector('#bio').value"), "", "textarea untouched")


# ------------------------------------------------------------------------------------------------------ rich text editors


@scenario("quill_and_contenteditable_editors")
async def editors(ctx: Ctx) -> None:
    letter = "Dear team, I would like to join you.\nI have built data pipelines for five years."
    answers = {"cover letter": T(letter), "why this role": T("Because the mission is clear and the team is small."), "anything else": T("Available from March.")}
    page, bg = await ctx.open("editors.html", answers)
    async with ctx.widget("Quill 2 (.ql-editor, multi-line plain text)"):
        field = await fill_with_tab(page, ".ql-editor", "Cover letter")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq(state["quill"].replace("\n\n", "\n"), letter, "Quill document text (what getText() returns)")
        expect("<script" not in state["quillHtml"] and "<b>" not in state["quillHtml"], "plain text only in the editor's HTML")
        eq(await page.evaluate("document.querySelector('.ql-editor').classList.contains('ql-blank')"), False, "Quill no longer shows its placeholder")
    async with ctx.widget("contenteditable mirrored into React state on input"):
        field = await fill_with_tab(page, "#mirror", "Why this role")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["mirror"], "Because the mission is clear and the team is small.", "React state")
    async with ctx.widget('contenteditable="plaintext-only"'):
        field = await fill_with_tab(page, "#plain", "Anything else")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["plain"], "Available from March.", "React state")


# ------------------------------------------------------------------------------------------------------ Angular-style value accessors


@scenario("angular_style_value_accessors_update_on_change_blur_submit")
async def angular_style(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "reference": T("REF-77"), "country": SELECT("Spain", "2: es"),
               "authorised to work": RADIO("Yes", "yes"), "send me job alerts": BOX(True), "short bio": T("Backend engineer.")}
    page, bg = await ctx.open("angular_cva.html", answers)

    async def tab_field(selector: str, label: str) -> None:
        field = await fill_with_tab(page, selector, label)
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")

    async with ctx.widget("Angular-style DefaultValueAccessor, updateOn change (input event)"):
        await tab_field("#ng_first", "First name")
        eq((await state_of(page)).get("first"), "Alex", "form model")
    async with ctx.widget("Angular-style DefaultValueAccessor, updateOn blur (committed when focus leaves)"):
        await tab_field("#ng_email", "Email")
        await wait_eq(lambda: _ng(page, "email"), "alex@example.test", "form model after the assistant moved on (blur)", 2)
    async with ctx.widget("Angular-style DefaultValueAccessor, updateOn submit (pending until the form is submitted)"):
        await tab_field("#ng_ref", "Reference")
        eq((await page.evaluate("window.__pending()")).get("ref"), "REF-77", "pending view value")
        await page.evaluate("document.getElementById('ng-form').requestSubmit()")
        eq((await state_of(page)).get("ref"), "REF-77", "form model after submit")
    async with ctx.widget("Angular-style SelectControlValueAccessor (option ids like '2: es')"):
        await tab_field("#ng_country", "Country")
        eq((await state_of(page)).get("country"), "es", "form model")
    async with ctx.widget("Angular-style Radio and Checkbox accessors (change event)"):
        await tab_field("input[name=auth]", "Authorised to work")
        await tab_field("input[name=alerts]", "Send me job alerts")
        state = await state_of(page)
        eq((state.get("auth"), state.get("alerts")), ("yes", True), "form model")
    async with ctx.widget("Angular-style textarea"):
        await tab_field("#ng_bio", "Short bio")
        eq((await state_of(page)).get("bio"), "Backend engineer.", "form model")


async def _ng(page: Any, key: str) -> Any:
    return (await state_of(page)).get(key)


# ------------------------------------------------------------------------------------------------------ Workday-style


@scenario("workday_style_listbox_popups_in_a_portal")
async def workday_style(ctx: Ctx) -> None:
    answers = {
        "given name": T("Alex"), "family name": T("Newport"), "how did you hear": COMBO("Referral", "Referral"), "country": COMBO("United Kingdom", "United Kingdom"),
        "phone device type": COMBO("Mobile"), "county": COMBO("Surrey"), "phone number": T("7700 900123"), "preferred contact": COMBO("Mobile"),
        "best time": COMBO("Afternoon"), "nationality": COMBO("Spain", "Spain"),
    }
    page, bg = await ctx.open("workday_widgets.html", answers)
    async with ctx.widget("Workday-style text inputs (data-automation-id)"):
        field = await fill_with_tab(page, "#name--legalName--firstName", "Given Name")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("firstName"), "Alex", "page state")
    async with ctx.widget("Workday-style <button aria-haspopup=listbox> dropdown + <ul role=listbox> popup portal (the vendor's own markup)"):
        field = await fill_with_tab(page, "#country--country", "Country")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page)).get("country--country"), "United Kingdom", "page state")
    async with ctx.widget("Workday-style popup on a <div role=combobox> trigger (portal listbox, no typing, nothing to filter)"):
        await combo_case(page, "#phone--device", "Phone Device Type", None)
        eq((await state_of(page)).get("phone--device"), "Mobile", "page state")
        eq(await page.evaluate("document.querySelector('#phone--device').textContent"), "Mobile", "trigger text")
    async with ctx.widget("Workday-style popup whose options are re-created every 120 ms while open"):
        await combo_case(page, "#county", "County", None)
        eq((await state_of(page)).get("county"), "Surrey", "page state")
        eq(await page.evaluate("document.querySelector('#county').textContent"), "Surrey", "trigger text")
        eq(await page.evaluate("document.querySelectorAll('[data-automation-widget=wd-popup]').length"), 0, "popup closed afterwards")
    async with ctx.widget("another widget's popup that is still open is never used (this combobox has no aria-controls, both lists offer 'Mobile')"):
        await page.click("#phone--device")  # the person opened this one: it stays open because focus moves without a mouse press
        await eventually(lambda: page.evaluate("document.querySelectorAll('[data-automation-widget=wd-popup]').length === 1"), 2, "the other popup is open")
        field = await fill_with_tab(page, "#contact", "Preferred contact")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq(state.get("contact"), "Mobile", "the combobox that was asked")
        eq(state.get("phone--device"), "Mobile", "the other widget keeps what it had (set earlier through the assistant)")
        eq(await page.evaluate("document.querySelector('#contact').textContent"), "Mobile", "displayed selection")
    async with ctx.widget("combobox input whose chosen value is displayed in a sibling <span> the widget library chose (no adapter for it): verified by the visible text changing"):
        await combo_case(page, "#nat", "Nationality", None)
        eq((await state_of(page)).get("nat"), "Spain", "page state")
        eq(await page.evaluate("document.querySelector('#nat-shown').textContent"), "Spain", "displayed selection")
    async with ctx.widget("an option list that ignores clicks during its open animation (350 ms): the click is repeated, never given up"):
        await combo_case(page, "#callback", "Best time", None, max_seconds=6.0)
        eq((await state_of(page)).get("callback"), "Afternoon", "page state")


# ------------------------------------------------------------------------------------------------------ SmartRecruiters-style (shadow DOM)


@scenario("smartrecruiters_style_nested_open_shadow_web_components")
async def smartrecruiters_style(ctx: Ctx) -> None:
    answers = {"first name": T("Alex"), "email": T("alex@example.test"), "country of residence": COMBO("Spain", "Spain"), "phone number": T("+34 612 345 678"),
               "send me job alerts": BOX(True)}
    page, bg = await ctx.open("smartrecruiters_widgets.html", answers)
    deep = "(function f(root, sel) { const hit = root.querySelector(sel); if (hit) return hit; for (const n of root.querySelectorAll('*')) { if (n.shadowRoot) { const r = f(n.shadowRoot, sel); if (r) return r; } } return null; })"

    async with ctx.widget("open shadow root <oc-text-field> (nested two deep): text, email"):
        await focus_text(page, deep, "oc-text-field[name=firstName]", "First name")
        await focus_text(page, deep, "oc-text-field[name=email]", "Email")
        eq((await state_of(page)).get("firstName"), "Alex", "component model")
        eq((await state_of(page)).get("email"), "alex@example.test", "component model")
    async with ctx.widget("open shadow root <oc-select> (role=combobox inside the shadow tree, listbox in the same tree)"):
        await ready_field(page, "Country of residence")
        await page.evaluate(f"() => {deep}(document, 'oc-select[name=country]').shadowRoot.querySelector('.trigger').focus()")
        await page.keyboard.press("Tab")
        await idle(page)
        field = await field_by(page, "Country of residence")
        expect(not field["failed"] and not await error_toasts(page), f"failed: {field['failed']}")
        eq((await state_of(page)).get("country"), "Spain", "component model")
    async with ctx.widget("open shadow root <oc-text-field type=tel> phone"):
        await focus_text(page, deep, "oc-text-field[name=phone]", "Phone number")
        eq((await state_of(page)).get("phone"), "+34 612 345 678", "component model")
    async with ctx.widget("open shadow root <oc-checkbox>"):
        await ready_field(page, "Send me job alerts")
        await page.evaluate(f"() => {deep}(document, 'oc-checkbox[name=alerts]').shadowRoot.querySelector('input').focus()")
        await page.keyboard.press("Tab")
        await idle(page)
        eq((await state_of(page)).get("alerts"), True, "component model")


async def focus_text(page: Any, deep: str, host_selector: str, label: str) -> dict[str, Any]:
    """Focus the native input inside a (nested) shadow root and press Tab; asserts a clean fill."""
    await ready_field(page, label)
    await page.evaluate(f"(sel) => {deep}(document, sel).shadowRoot.querySelector('input').focus()", host_selector)
    await page.keyboard.press("Tab")
    await idle(page)
    field = await field_by(page, label)
    expect(not field["failed"], f"failed: {field['failed']}")
    expect(not await error_toasts(page), f"error toast shown: {await error_toasts(page)}")
    return field


# ------------------------------------------------------------------------------------------------------ Workable-style


@scenario("workable_style_custom_select_radio_cards_yes_no_and_counter")
async def workable_style(ctx: Ctx) -> None:
    answers = {
        "first name": T("Alex"), "email": T("alex@example.test"), "right to work": COMBO("Yes", "Yes"), "preferred working mode": RADIO("Hybrid", "hybrid"),
        "willing to relocate": RADIO("No", "no"), "summary": T("Backend engineer with five years of experience."),
    }
    page, bg = await ctx.open("workable_widgets.html", answers)
    async with ctx.widget("Workable-style text inputs with required markers"):
        for selector, label in (("#firstname", "First name"), ("#email", "Email")):
            field = await fill_with_tab(page, selector, label)
            expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq((state["firstname"], state["email"]), ("Alex", "alex@example.test"), "page state")
    async with ctx.widget("Workable-style custom select (div role=combobox + inline listbox + hidden backing input)"):
        await combo_case(page, "#qa-trigger", "right to work", None)
        eq((await state_of(page)).get("QA_8821"), "yes", "page state")
        eq(await page.evaluate("document.querySelector('input[name=QA_8821]').value"), "yes", "hidden backing input")
        eq(await page.evaluate("document.querySelector('#qa-trigger').textContent"), "Yes", "displayed selection")
    async with ctx.widget("Workable-style radio cards (div role=radio, no inputs, label = the card's own text)"):
        field = await fill_with_tab(page, "[role=radio]", "Preferred working mode")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("mode"), "hybrid", "page state")
        eq(await page.evaluate("document.querySelector('.card[aria-checked=true]').textContent"), "Hybrid", "the checked card")
    async with ctx.widget("Workable-style Yes/No buttons (aria-pressed)"):
        field = await fill_with_tab(page, ".yesno button", "willing to relocate")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page)).get("relocate"), "no", "page state")
    async with ctx.widget("Workable-style textarea with live counter"):
        field = await fill_with_tab(page, "#summary", "Summary")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq(await page.evaluate("document.getElementById('summary-count').textContent"), f"{len('Backend engineer with five years of experience.')} / 200", "counter")


# ------------------------------------------------------------------------------------------------------ iCIMS-style


@scenario("icims_style_native_selects_dependent_options_and_label_buttons")
async def icims_style(ctx: Ctx) -> None:
    doc = make_document()
    ref = {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}
    answers = {"first name": T("Alex"), "last name": T("Newport"), "country": SELECT("United Kingdom", "GB"), "county": SELECT("England", "ENG"),
               "at least 18": RADIO("Yes", "Y"), "resume": FILE(ref)}
    page, bg = await ctx.open("icims_widgets.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    async with ctx.widget("iCIMS-style table-layout text inputs with blur validation"):
        for selector, label in (("#first_name", "First Name"), ("#last_name", "Last Name")):
            field = await fill_with_tab(page, selector, label)
            expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq((state["first_name"], state["last_name"]), ("Alex", "Newport"), "page state")
        eq(await page.evaluate("document.getElementById('first_name_err').textContent"), "", "no validation error shown")
    async with ctx.widget("iCIMS-style native select that loads a dependent select after a delay"):
        field = await fill_with_tab(page, "#country", "Country")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page)).get("country"), "GB", "page state")
        await eventually(lambda: page.evaluate("!document.getElementById('state').disabled"), 4, "the dependent options arrived")
        await eventually(lambda: field_by(page, "County"), 4, "the assistant noticed the new options")
        await asyncio.sleep(0.6)  # let the rescan deliver a fresh suggestion for the new option list
        field = await fill_with_tab(page, "#state", "County")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page)).get("state"), "ENG", "page state of the dependent select")
    async with ctx.widget("iCIMS-style radio buttons whose question sits in the previous table cell"):
        field = await fill_with_tab(page, "input[name=adult]", "at least 18")
        expect(not field["failed"], f"failed: {field['failed']}")
        eq((await state_of(page)).get("adult"), "Y", "page state")
    async with ctx.widget("iCIMS-style file input behind a visible label button"):
        field = await fill_with_tab(page, "#resume_file", "resume")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page)).get("resume"), ["Alex-CV.pdf"], "page state")


# ------------------------------------------------------------------------------------------------------ Teamtailor-style


@scenario("teamtailor_style_rails_names_and_dropzone_js_hidden_input")
async def teamtailor_style(ctx: Ctx) -> None:
    doc = make_document()
    ref = {k: doc[k] for k in ("id", "filename", "mime", "size", "sha256")}
    answers = {"first name": T("Alex"), "last name": T("Newport"), "phone": T("+44 7700 900123"), "preferred language": SELECT("English", "en"),
               "driving licence": RADIO("Yes", "yes"), "resume": FILE(ref), "cover letter": T("Hello team.")}
    page, bg = await ctx.open("teamtailor_widgets.html", answers, bg=Bg(answers, documents={"doc-1": doc}))
    async with ctx.widget("Teamtailor-style Rails-named inputs, select and radios"):
        for selector, label in (("#candidate_first_name", "First name"), ("#candidate_last_name", "Last name"), ("#candidate_phone", "Phone"),
                                ("#candidate_language", "Preferred language"), ("input[name='answers[1][value]']", "driving licence")):
            field = await fill_with_tab(page, selector, label)
            expect(not field["failed"] and not field["errors"], f"{label}: failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq((state["candidate[first_name]"], state["candidate[phone]"], state["candidate[language]"], state["answers[1][value]"]),
           ("Alex", "+44 7700 900123", "en", "yes"), "page state")
        eq((await form_data(page))["candidate[language]"], "en", "FormData")
    async with ctx.widget("Teamtailor-style Dropzone.js file area (visibility:hidden 0x0 input)"):
        names = [f["label"] for f in (await debug(page))["fields"]]
        expect(any("resume" in n.lower() for n in names), f"the Resume file field is not an assist field: {names}")
        # a visibility:hidden input cannot take keyboard focus (the person reaches it through the visible drop area): the field must
        # still be scanned with a real suggestion, and the panel's "Fill verified" must attach the CV through it
        field = await ready_field(page, "resume")
        eq((field["state"], field["insert"]), ("VERIFIED", "tab"), "the suggestion for the Dropzone.js file field")
        await press_fill_verified(page)

        async def attached() -> Any:
            return (await state_of(page)).get("resume")

        await wait_eq(attached, ["Alex-CV.pdf"], "Dropzone.js change handler state after Fill verified", 6)
    async with ctx.widget("Teamtailor-style cover letter textarea"):
        field = await fill_with_tab(page, "#candidate_cover", "Cover letter")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")


# ------------------------------------------------------------------------------------------------------ Lever-style


@scenario("lever_style_application_questions_radio_checkbox_lists_and_location")
async def lever_style(ctx: Ctx) -> None:
    answers = {"full name": T("Alex Newport"), "email": T("alex@example.test"), "legally authorized": RADIO("Yes", "Yes"),
               "which of these languages": BOXES("English", "Spanish"), "additional information": T("Happy to relocate."), "current location": T("London, UK")}
    page, bg = await ctx.open("lever_widgets.html", answers)
    async with ctx.widget("Lever-style .application-question text inputs"):
        for selector, label in (("input[name=name]", "Full name"), ("input[name=email]", "Email")):
            field = await fill_with_tab(page, selector, label)
            expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq(await page.evaluate("[document.querySelector('input[name=name]').value, document.querySelector('input[name=email]').value]"), ["Alex Newport", "alex@example.test"], "values")
    async with ctx.widget("Lever-style radio list (cards[uuid][field0])"):
        field = await fill_with_tab(page, "input[name='cards[1b2c][field0]']", "legally authorized")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await form_data(page)).get("cards[1b2c][field0]"), "Yes", "FormData")
    async with ctx.widget("Lever-style checkbox list (several answers)"):
        field = await fill_with_tab(page, "input[name='cards[3d4e][field1]']", "which of these languages")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await form_data(page)).get("cards[3d4e][field1]"), ["English", "Spanish"], "FormData")
    async with ctx.widget("Lever-style location autocomplete: a typed text without a picked result is NOT a location",
                          xfail="a plain text input that needs a result picked from a non-ARIA dropdown is filled as text only (selectedLocation stays empty)",
                          handoff="observation.js: recognise Lever's .location-input + hidden selectedLocation as a combobox, or fill.ts could pick .dropdown-location results (markup unverified)"):
        field = await fill_with_tab(page, "input.location-input", "Current location")
        expect(not field["failed"], f"failed: {field['failed']}")
        await eventually(lambda: page.evaluate("document.getElementById('selectedLocation').value !== ''"), 2, "the location was picked from the results")


# ------------------------------------------------------------------------------------------------------ more react-select and safety


@scenario("react_select_virtualised_list_without_a_search_text")
async def react_select_virtualised_no_search(ctx: Ctx) -> None:
    """The bridge always sends the answer as search_text, but a widget without a text box (or a null search_text) lists only the
    options in view: the fill has to scroll the list to the answer instead of giving up."""
    answers = {"nationality": COMBO("United Kingdom", None)}
    page, bg = await ctx.open("react_select.html", answers)
    async with ctx.widget("react-select + react-window, 1,000 options, no search text (scroll to the option)"):
        await combo_case(page, "#nationality", "Nationality", "office", max_seconds=8.0)
        eq((await form_data(page))["nationality"], "united-kingdom", "virtualised hidden input")


@scenario("a_different_existing_selection_is_never_overwritten")
async def never_overwrite(ctx: Ctx) -> None:
    async def untouched(page: Any, selector: str, label: str, keep: dict[str, Any]) -> None:
        field = await fill_with_tab(page, selector, label)
        expect(not field["inserted"] and not field["failed"], f"Tab must neither insert over a chosen value nor fail: {field}")
        for key, value in keep.items():
            eq((await state_of(page)).get(key), value, f"{key} must stay what the person chose")

    page, bg = await ctx.open("react_select.html", {"country": COMBO("United Kingdom", "United Kingdom"), "seniority": COMBO("Senior", "Senior")})
    async with ctx.widget("react-select (prefixed markup) keeps the person's own choice"):
        await page.click("#country")
        await page.get_by_role("option", name="Spain", exact=True).click()
        await wait_eq(lambda: state_of(page), {**await state_of(page), "country": "es"}, "the person's choice", 2)
        await untouched(page, "#country", "Country", {"country": "es"})
    async with ctx.widget("react-select (default markup, no classNamePrefix) keeps the person's own choice"):
        await page.click("#seniority")
        await page.get_by_role("option", name="Junior", exact=True).click()
        await wait_eq(lambda: _val(page, "seniority"), "junior", "the person's choice", 2)
        await untouched(page, "#seniority", "Seniority", {"seniority": "junior"})
    page2, _ = await ctx.open("mui.html", {"country": COMBO("United Kingdom", "United Kingdom")})
    async with ctx.widget("MUI Select keeps the person's own choice"):
        await page2.click("#country")
        await page2.get_by_role("option", name="Canada", exact=True).click()
        await wait_eq(lambda: _val(page2, "country"), "canada", "the person's choice", 2)
        await untouched(page2, "#country", "Country", {"country": "canada"})
    page3, _ = await ctx.open("workday_widgets.html", {"phone device type": COMBO("Mobile")})
    async with ctx.widget("div role=combobox showing 'Select One' is empty; showing a choice it keeps it"):
        await page3.click("#phone--device")
        await page3.get_by_role("option", name="Home", exact=True).click()
        await wait_eq(lambda: page3.evaluate("document.querySelector('#phone--device').textContent"), "Home", "the person's choice", 2)
        await untouched(page3, "#phone--device", "Phone Device Type", {"phone--device": "Home"})


async def _val(page: Any, key: str) -> Any:
    return (await form_data(page)).get(key)


@scenario("react_select_form_walked_with_tab_only")
async def tab_walk_react_select(ctx: Ctx) -> None:
    page, bg = await ctx.open("react_select.html", {k: v for k, v in RS_ANSWERS.items() if k != "seniority"})
    order = ["preferred_name", "country", "auth", "languages", "city", "based", "nationality", "office", "seniority", "timezone", "source"]
    async with ctx.widget("walking a whole react-select form with nothing but the Tab key"):
        await ready_field(page, "Preferred name")
        await page.focus("#preferred_name")
        seen = []
        for expected_next in order[1:] + ["(native)"]:
            await page.keyboard.press("Tab")
            await idle(page, 12)
            seen.append(await focused_id(page))
        eq(seen[:-1], order[1:], "the fields focus visited, in order (every Tab inserted and advanced)")
        form = await form_data(page)
        eq({k: form[k] for k in ("preferred_name", "country", "auth", "languages", "city", "based", "nationality", "office", "seniority", "timezone", "source")},
           {"preferred_name": "Alex", "country": "gb", "auth": "yes", "languages": "spanish", "city": "madrid-spain", "based": "london-england-united-kingdom",
            "nationality": "united-kingdom", "office": "madrid-office", "seniority": "", "timezone": "europe-madrid", "source": "referral"}, "FormData after the walk (seniority has no answer: left alone)")
        eq(await error_toasts(page), [], "no error toast during the walk")
        eq(await page.evaluate("window.__submitted.length"), 0, "the assistant submitted nothing")
        posted = await page.evaluate("window.__submit()")  # the test (not the assistant) submits, and reads what the browser would post
        eq({k: posted[k] for k in ("country", "auth", "languages", "city", "based", "nationality", "office", "timezone", "source")},
           {"country": "gb", "auth": "yes", "languages": "spanish", "city": "madrid-spain", "based": "london-england-united-kingdom",
            "nationality": "united-kingdom", "office": "madrid-office", "timezone": "europe-madrid", "source": "referral"}, "the payload the form submission carries")


@scenario("react_select_unmodified_markup_is_scanned")
async def react_select_unmodified(ctx: Ctx) -> None:
    page, bg = await ctx.open("react_select.html", RS_ANSWERS, ready=False)
    await asyncio.sleep(1.0)
    async with ctx.widget("react-select with its real, unmodified layout (empty search input is ~4px wide)"):
        labels = [f["label"] for f in (await debug(page))["fields"]]
        expect(any(name.startswith("Country") for name in labels), f"the Country select is not an assist field: {labels}")


@scenario("react_select_default_markup_focus_advances_after_the_input_hides")
async def react_select_hidden_input_advance(ctx: Ctx) -> None:
    """react-select without classNamePrefix hides its (opacity 0) input once a value is chosen: the field leaves the scan, and the
    keyboard layer then has no position to advance from."""
    page, bg = await ctx.open("react_select.html", {"seniority": COMBO("Senior", "Senior"), "time zone": COMBO("Europe/Madrid", "Europe/Madrid")})
    async with ctx.widget("focus moves on after the selection hid the combobox input (default react-select markup)"):
        await combo_case(page, "#seniority", "Seniority", "timezone")


@scenario("react_phone_number_input_national_and_international")
async def react_phone_number_input(ctx: Ctx) -> None:
    answers = {"mobile number": T("+44 7700 900123"), "contact number": T("+34 612 345 678")}
    page, bg = await ctx.open("phone_libs.html", answers)
    async with ctx.widget("react-phone-number-input 3, national format (+44 typed into a GB input)"):
        field = await fill_with_tab(page, "#rpni_national input[type=tel]", "Mobile number")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq(state["national"], "+447700900123", "the E.164 value the library reports")
        expect(state["nationalShown"].count("44") <= 1 and state["nationalShown"].replace(" ", "").endswith("7700900123"), f"shown {state['nationalShown']!r}")
    async with ctx.widget("react-phone-number-input 3, international format (+34 typed)"):
        field = await fill_with_tab(page, "#rpni_intl input[type=tel]", "Contact number")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        state = await state_of(page)
        eq(state["intl"], "+34612345678", "the E.164 value the library reports")
        eq(state["intlShown"].replace(" ", ""), "+34612345678", "shown")


@scenario("react_select_creatable_new_value_and_refused_unknown_option")
async def react_select_create_and_refuse(ctx: Ctx) -> None:
    page, bg = await ctx.open("react_select.html", {"current city": COMBO("Zaragoza, Spain", "Zaragoza, Spain"), "country": COMBO("Atlantis", "Atlantis")})
    async with ctx.widget("react-select creatable: a value that is not in the list is created through the widget's own 'Create ...' option"):
        await combo_case(page, "#city", "Current city", "based")
        eq((await form_data(page))["city"], "Zaragoza, Spain", "creatable hidden input (the created option uses the typed text as its value)")
        eq(await page.evaluate("document.querySelector('#city').closest('.field').querySelector('.select__single-value').textContent"), "Zaragoza, Spain", "displayed selection")
    async with ctx.widget("react-select: an answer that is not an option is refused cleanly (nothing selected, typed text removed, menu closed, focus kept)"):
        field = await fill_with_tab(page, "#country", "Country")
        expect(field["failed"] == "no_option" or field["errors"], f"the refusal must be reported: {field}")
        expect(field["seconds"] < 3.5, f"the refusal took {field['seconds']}s: the popup said 'No options' right away")
        eq((await state_of(page)).get("country"), None, "nothing was selected")
        eq(await page.evaluate("document.querySelector('#country').value"), "", "the typed search text was removed again")
        eq(await page.evaluate("document.querySelector('#country').getAttribute('aria-expanded')"), "false", "menu closed")
        eq(await focused_id(page), "country", "focus stays on the field that could not be filled")


@scenario("native_select_whose_options_load_after_focus_and_checkbox_group_with_stale_handler")
async def late_select_and_stale_group(ctx: Ctx) -> None:
    page, bg = await ctx.open("forms.html", {"region": SELECT("South", "south")})
    async with ctx.widget("native <select> whose options load 300 ms after focus: Tab right away waits for the list and picks the option"):
        field = await fill_with_tab(page, "#late", "Region", timeout=15)
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["masks"]["late"], "south", "React state of the select")
    page1, _ = await ctx.open("forms.html", {"region": SELECT("Atlantis", "atlantis")})
    async with ctx.widget("a select that never gets that option fails closed once the list is there (no wrong pick, reported)"):
        field = await fill_with_tab(page1, "#late", "Region", timeout=15)
        expect(field["failed"] == "no_option" or field["errors"], f"the refusal must be reported: {field}")
        eq((await state_of(page1))["masks"]["late"], "", "nothing was picked")
    page3, _ = await ctx.open("forms.html?late=4000", {"region": SELECT("South", "south")})
    async with ctx.widget("a failed fill is tried again once the page's options have arrived and a fresh suggestion came in"):
        field = await fill_with_tab(page3, "#late", "Region", timeout=15)
        expect(field["failed"] == "no_option", f"setup: the first attempt must fail while the list is empty: {field}")
        await eventually(lambda: page3.evaluate("document.querySelectorAll('#late option').length > 1"), 6, "options loaded")
        await asyncio.sleep(1.0)  # rescan + a fresh suggestion for the new option list
        field = await fill_with_tab(page3, "#late", "Region", timeout=15)
        expect(not field["failed"], f"the retry is refused: {field}")
        eq((await state_of(page3))["masks"]["late"], "south", "React state of the select")
    page2, _ = await ctx.open("mui.html", {"languages you work in": BOXES("English", "German")})
    async with ctx.widget("MUI checkbox group whose handler builds the next array from the last render (stale unless the page flushed)"):
        field = await fill_with_tab(page2, "input[name=langs]", "Languages you work in")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        await asyncio.sleep(0.5)
        eq(sorted((await state_of(page2))["langs"]), ["English", "German"], "React state of the group")
        eq(await page2.evaluate("[...document.querySelectorAll('input[name=langs]')].filter(i => i.checked).map(i => i.value).sort()"), ["English", "German"], "checked boxes")


# ------------------------------------------------------------------------------------------------------ Select2 on jQuery


@scenario("select2_on_jquery_single_multiple_no_search_and_remote_search")
async def select2(ctx: Ctx) -> None:
    answers = {"country": COMBO("United Kingdom", "United Kingdom"), "skills": COMBO("Python", "Python"), "seniority": COMBO("Senior", "Senior"),
               "where are you based": COMBO("London, UK", "London")}
    page, bg = await ctx.open("select2_jquery.html", answers)
    async with ctx.widget("Select2 4 single (clipped <select> + span role=combobox, results in a container on <body>, chosen on mouseup)"):
        field = await fill_with_tab(page, "#country_s2 + .select2 .select2-selection", "Country")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["country"], "gb", "the underlying <select> value jQuery reports")
        eq(await page.evaluate("document.querySelector('#country_s2 + .select2 .select2-selection__rendered').textContent"), "United Kingdom", "displayed selection")
        eq(await page.evaluate("document.querySelectorAll('.select2-container--open').length"), 0, "dropdown closed")
    async with ctx.widget("Select2 4 multiple (chips)"):
        field = await fill_with_tab(page, "#skills_s2 + .select2 .select2-selection", "Skills")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["skills"], ["py"], "the underlying <select> values jQuery reports")
    async with ctx.widget("Select2 4 with the search box disabled"):
        field = await fill_with_tab(page, "#level_s2 + .select2 .select2-selection", "Seniority")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["level"], "sr", "the underlying <select> value jQuery reports")
    async with ctx.widget("Select2 4 remote search: the search box exists only inside the opened dropdown and the list needs 2 typed characters"):
        field = await fill_with_tab(page, "#city_s2 + .select2 .select2-selection", "where are you based")
        expect(not field["failed"] and not field["errors"], f"failed: {field['failed']} {field['errors']}")
        eq((await state_of(page))["city"], "London, UK", "the underlying <select> value jQuery reports")


@scenario("mui_form_walked_with_tab_only")
async def tab_walk_mui(ctx: Ctx) -> None:
    answers = {
        "first name": T("Alex"), "email address": T("alex@example.test"), "country": COMBO("United Kingdom", "United Kingdom"),
        "where are you based": COMBO("Madrid, Spain", "Madrid"), "notice period": SELECT("1 month", "1m"), "legally authorised": RADIO("Yes", "yes"),
        "send me job alerts": BOX(True), "languages you work in": BOXES("English", "Spanish"), "open to remote work": BOX(True),
        "short bio": T("Backend engineer."),
    }
    page, bg = await ctx.open("mui.html", answers)
    async with ctx.widget("walking a whole Material UI form with nothing but the Tab key (text, select, autocomplete, native select, radios, checkboxes, switch, textarea)"):
        await ready_field(page, "First name")
        await page.focus("#first")
        seen = []
        for _ in range(10):
            await page.keyboard.press("Tab")
            await idle(page, 12)
            active = await deep_active(page)
            seen.append(active["id"] or active["name"])
        eq(seen[:9], ["email", "country", "city", "notice", "auth", "alerts", "langs", "remote", "bio"], "the fields focus visited, in order")
        await asyncio.sleep(0.5)
        st = await state_of(page)
        eq((st["first"], st["email"], st["country"], st["city"], st["notice"], st["auth"], st["alerts"], sorted(st["langs"]), st["remote"], st["bio"]),
           ("Alex", "alex@example.test", "united-kingdom", "Madrid, Spain", "1m", "yes", True, ["English", "Spanish"], True, "Backend engineer."), "React state after the walk")
        eq(await error_toasts(page), [], "no error toast during the walk")


@scenario("a_missing_option_never_scrolls_the_page_container")
async def missing_option_page_scroller(ctx: Ctx) -> None:
    """Looking for an option in a virtualised list scrolls THE LIST; when the page itself scrolls inside a container, that container
    (which holds the combobox) must never be mistaken for the list."""
    page, bg = await ctx.open("react_select.html?pagescroll=1", {"legally authorized": COMBO("Maybe", None)})
    async with ctx.widget("an option that is not in a (non-scrollable) list: the page's own scroll container is never scrolled looking for it"):
        await page.evaluate("document.getElementById('root').scrollTop = 0; window.__scrolls = 0; document.getElementById('root').addEventListener('scroll', () => { window.__scrolls += 1; })")
        field = await fill_with_tab(page, "#auth", "legally authorized", timeout=15)
        expect(field["failed"] == "no_option" or field["errors"], f"the refusal must be reported: {field}")
        eq(await page.evaluate("window.__scrolls"), 0, "scroll events of the page container during the search")


import scanner_scenarios  # noqa: E402,F401  (registers the scanner scenarios)
import visibility_scenarios  # noqa: E402,F401  (registers the visibility / sensitive-field / text-limit scenarios; must stay the last import)
