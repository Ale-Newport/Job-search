"""The widget pages against the WHOLE product: the real extension (service worker, isolated content script, real UI), the real
backend (answer engine, resolver, bridge) on a temp data dir, and the real widget libraries, driven with the real keyboard.

    cd extension && ../.venv/bin/python tests/widgets/real_run.py [-k substring] [--jobs N] [--recipe chromium|chrome]

It reuses the e2e suite (tests/e2e: harness, framework, backend, seed) unchanged: only the widget fixture folder is added to its
fixture servers and these scenarios are registered next to the others. tests/widgets/run.py (canned suggestions through the content
rig) covers the whole widget matrix; this file proves the same fills with the engine's own answers on the most important pages.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
E2E = HERE.parent / "e2e"
FIXTURES = HERE / "fixtures"
sys.dont_write_bytecode = True
sys.path.insert(0, str(E2E))

import servers as e2e_servers  # noqa: E402

_original_locate = e2e_servers.FixtureServers.locate


def _locate(name: str) -> Path | None:
    """The widget pages first (their names never collide with the e2e fixtures), then the e2e suite's own lookup."""
    candidate = (FIXTURES / name).resolve()
    if candidate.is_file() and candidate.is_relative_to(FIXTURES.resolve()) and not candidate.name.startswith("."):
        return candidate
    return _original_locate(name)


e2e_servers.FixtureServers.locate = staticmethod(_locate)  # type: ignore[method-assign]
e2e_servers.CONTENT_TYPES.setdefault(".mjs", "text/javascript; charset=utf-8")

import flows  # noqa: E402
import seed  # noqa: E402
import test_extension_e2e as e2e  # noqa: E402  (registers the e2e suite's scenarios; -k selects ours)
import ui  # noqa: E402
from framework import Env, check, scenario  # noqa: E402
from ui import Chip, Panel  # noqa: E402

SUGGEST = ("bridge", "worker", "e2e_ports", "content", "ui", "bridge_routes", "resolver")
# The whole stack (browser, backend, answer engine) shares the machine with whatever else is running: generous waits, short only when asserting "nothing".
SLOW = 60_000


async def ready_page(env: Env, name: str, query: str = ""):
    rig = await env.connected()
    page = await env.open(name, query, activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=SLOW)
    return rig, page, panel


async def state(page) -> dict:
    return await page.evaluate("window.__state()")


async def tab_and_wait(page, selector_after: str | None = None, timeout: float = 60.0) -> None:
    """Press the real Tab key; an asynchronous fill (menus, files) keeps focus until it is done, so wait for the next field."""
    await ui.tab(page)
    if selector_after:
        await flows.poll_until(lambda: _focused(page, selector_after), timeout=timeout, message=f"focus on {selector_after}")


async def _focused(page, selector: str) -> bool:
    return await page.evaluate("(s) => !!document.activeElement && document.activeElement.matches(s)", selector)


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_react_select_greenhouse_style(env: Env) -> None:
    """react-select (Greenhouse job-boards markup): the engine's own answer goes in through Tab; a protected question is left alone."""
    _, page, panel = await ready_page(env, "react_select.html")
    chip = Chip(page)
    await flows.focus_first(page, "#preferred_name")
    await chip.expect_none()
    await ui.tab(page)  # no verified answer for "Preferred name" in this profile: nothing is inserted, focus moves on
    await ui.expect_focus(page, "#country", timeout=SLOW)
    await chip.expect_chip(state="VERIFIED", insert="tab", value="United Kingdom", timeout=SLOW)
    await tab_and_wait(page, "#auth")
    st = await state(page)
    check("react-select holds the country the engine answered (its own state and the posted hidden input)", st.get("country") == "gb"
          and (await page.evaluate("window.__formData()"))["country"] == "gb", st)
    check("the person's own text in 'Preferred name' was never typed over", await page.input_value("#preferred_name") == "")
    # work authorisation is a protected question (and this page has no job location): whatever the chip offers, Tab never inserts it
    await ui.tab(page)
    await ui.expect_focus(page, "#languages", timeout=SLOW)
    check("Tab did not insert an answer for the protected question", (await state(page)).get("auth") is None, await state(page))
    await ui.assert_never_submitted(page)


async def _state_is(page, key: str, value) -> bool:
    return (await state(page)).get(key) == value


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_mui_form(env: Env) -> None:
    """Material UI controlled form: text fields reach React state, the Select (zero-width placeholder) and Autocomplete take the answer."""
    _, page, panel = await ready_page(env, "mui.html")
    chip = Chip(page)
    await flows.focus_first(page, "#first")
    await chip.expect_chip(state="VERIFIED", insert="tab", value=seed.FIRST, timeout=SLOW)
    await tab_and_wait(page, "#email")
    await chip.expect_chip(state="VERIFIED", insert="tab", value=seed.EMAIL, timeout=SLOW)
    await tab_and_wait(page, "#country")
    st = await state(page)
    check("React state of the controlled TextFields", (st["first"], st["email"]) == (seed.FIRST, seed.EMAIL), st)
    await chip.expect_chip(state="VERIFIED", insert="tab", value="United Kingdom", timeout=SLOW)
    await ui.tab(page)  # MUI Select: opens its popover, picks the option, closes, focus moves on
    await flows.poll_until(lambda: _state_is(page, "country", "united-kingdom"), timeout=60, message="MUI Select value")
    check("the Select shows the chosen country", (await page.text_content("#country")).strip() == "United Kingdom")
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_intl_tel_input_phone(env: Env) -> None:
    """intl-tel-input: the engine's international number goes in; the widget moves the dial code into the flag; nothing is duplicated."""
    _, page, panel = await ready_page(env, "intl_tel_input.html")
    chip = Chip(page)
    await flows.focus_first(page, "#phone_default")
    await chip.expect_chip(state="VERIFIED", insert="tab", timeout=SLOW)
    await ui.tab(page)
    await flows.poll_until(lambda: _phone_ok(page), timeout=60, message="intl-tel-input holds the profile's number")
    st = (await state(page))["phone_default"]
    check("the number the form would submit is the profile's number, shown once", st["e164"] == "+447700900123" and st["country"] == "gb"
          and st["shown"].count("44") <= 1, st)
    await ui.expect_focus(page, "#phone_separate", timeout=SLOW)
    await ui.assert_never_submitted(page)


async def _phone_ok(page) -> bool:
    return (await state(page))["phone_default"]["e164"] == "+447700900123"


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_vue_form(env: Env) -> None:
    """Vue 3 v-model: name, email and a native select reach the Vue model through the engine's answers."""
    _, page, panel = await ready_page(env, "vue_app.html")
    chip = Chip(page)
    await flows.focus_first(page, "#v_first")
    await chip.expect_chip(state="VERIFIED", insert="tab", value=seed.FIRST, timeout=SLOW)
    await ui.tab(page)
    await ui.expect_focus(page, "#v_email", timeout=SLOW)
    await ui.tab(page)
    await flows.poll_until(lambda: _vue_email(page), timeout=60, message="Vue model holds first name and email")
    st = await state(page)
    check("Vue's own model holds the inserted values", (st["first"], st["email"]) == (seed.FIRST, seed.EMAIL), st)
    await ui.assert_never_submitted(page)


async def _vue_email(page) -> bool:
    st = await state(page)
    return st["first"] == seed.FIRST and st["email"] == seed.EMAIL


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_workday_button_dropdown(env: Env) -> None:
    """Workday's own markup: a <button aria-haspopup=listbox> dropdown named by its label. The real bridge accepts it as a combobox and the
    engine's answer is picked from the popup (portal) through Tab."""
    _, page, panel = await ready_page(env, "workday_widgets.html")
    chip = Chip(page)
    await flows.focus_first(page, "#name--legalName--firstName")
    await chip.expect_chip(state="VERIFIED", insert="tab", value=seed.FIRST, timeout=SLOW)
    await tab_and_wait(page, "#name--legalName--lastName")
    await ui.tab(page)  # the family name goes in, focus lands on the first button dropdown ("How Did You Hear About Us?": no verified answer)
    await ui.expect_focus(page, "#source--source", timeout=SLOW)
    await ui.tab(page)  # nothing to insert: focus moves on to the Country dropdown
    await ui.expect_focus(page, "#country--country", timeout=SLOW)
    await chip.expect_chip(state="VERIFIED", insert="tab", value="United Kingdom", timeout=SLOW)
    await ui.tab(page)
    await flows.poll_until(lambda: _state_is(page, "country--country", "United Kingdom"), timeout=60, message="the Workday-style dropdown value")
    check("the button shows the chosen country", (await page.text_content("#country--country")).strip() == "United Kingdom")
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=420)
async def scenario_widgets_real_dropzone_resume(env: Env) -> None:
    """react-dropzone with a div root: the file input is display:none inside the drop area. The approved CV is attached through it."""
    _, page, panel = await ready_page(env, "dropzone.html")
    chip = Chip(page)
    await flows.focus_first(page, "input[name=resume]")
    await chip.expect_chip(state="VERIFIED", insert="tab", timeout=SLOW)
    await ui.tab(page)
    await flows.poll_until(lambda: _resume_attached(page), timeout=60, message="react-dropzone holds the approved CV")
    check("the form posts the approved CV", (await page.evaluate("window.__formData()"))["resume"].startswith(f"file:{seed.CV_FILENAME}:"))
    await ui.assert_never_submitted(page)


async def _resume_attached(page) -> bool:
    return (await state(page))["resume"] == [seed.CV_FILENAME]


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if not any(a == "-k" or a.startswith("-k=") for a in sys.argv[1:]):
        sys.argv += ["-k", "widgets_real"]
    sys.exit(asyncio.run(e2e.main()))
