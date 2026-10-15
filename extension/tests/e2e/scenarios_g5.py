"""Scenarios found by running the whole suite on a loaded machine (phase G5).

* ``ashby_fixture_*``      the fixture itself: a combobox whose results arrive late must not re-open its list after a pick
                           (a self-made race that put a list over the next question and made two journeys fail under load)
* ``enter_*``              the product: an Enter pressed while a page popover covers the field writes nothing under it and says so
"""

from __future__ import annotations

import asyncio

from playwright.async_api import expect

import ui
from framework import Env, check, scenario
from scenarios_forms import SUGGEST, drafts_finished, ready_form
from ui import Chip


# ---------------------------------------------------------------------------------------------- the fixture
@scenario(timeout=60)
async def scenario_ashby_fixture_list_stays_closed_after_a_pick_when_late_results_arrive(env: Env) -> None:
    """ashby_apply.html models a combobox whose results arrive 120 ms after the last typed character. Results that arrive AFTER an
    option was picked belong to a search nobody is waiting for: they must not open the list again, or it covers the next question
    (the sponsorship buttons) and a loaded machine fails the walk for a reason that is the fixture's own. A search nobody has
    answered yet still opens the list."""
    browser = await env.p.chromium.launch(channel="chromium", headless=True)
    try:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        await page.goto(env.fixtures.url("ashby_apply.html"), wait_until="load")
        await page.wait_for_selector("#ashby_form", state="attached", timeout=10_000)
        # typing starts a search (a timer); picking an option in the SAME task, before any timer can fire, is the loaded machine's order
        picked = await page.evaluate("""() => {
            const input = document.getElementById('location'), list = document.getElementById('location-list');
            input.focus(); input.click();
            input.value = 'Lon'; input.dispatchEvent(new Event('input', { bubbles: true }));
            list.querySelector('li[role=option]').click();
            return { value: input.value, hidden: list.hidden, expanded: input.getAttribute('aria-expanded') };
        }""")
        check("the pick itself closes the list and sets the value", picked["hidden"] and picked["expanded"] == "false"
              and picked["value"].startswith("London"), picked)
        await asyncio.sleep(0.6)  # far more than the 120 ms the late results take
        later = await page.evaluate("""() => ({ hidden: document.getElementById('location-list').hidden,
            expanded: document.getElementById('location').getAttribute('aria-expanded'), value: document.getElementById('location').value })""")
        check("results that arrive after the pick leave the list closed", later["hidden"] and later["expanded"] == "false", later)
        check("and the picked value stays", later["value"].startswith("London"), later)
        # the other half: a search nobody has answered still shows its results (the late-results behaviour the fixture exists for)
        await page.evaluate("""() => { const input = document.getElementById('notice'); input.focus();
            input.value = 'week'; input.dispatchEvent(new Event('input', { bubbles: true })); }""")
        await asyncio.sleep(0.6)
        shown = await page.evaluate("""() => ({ hidden: document.getElementById('notice-list').hidden,
            options: [...document.querySelectorAll('#notice-list li')].map((li) => li.textContent) })""")
        check("a search nobody answered yet opens its list with the late results", not shown["hidden"] and "2 weeks" in shown["options"], shown)
    finally:
        await browser.close()


# ---------------------------------------------------------------------------------------------- the product
async def enter_while_a_popover_covers_the_buttons(env: Env):
    """The page puts a suggestions popover over the sponsorship buttons for 2.5 s once a location is chosen (``?overlay=``: a popover with
    a slow exit animation or a late "did you mean" list; a loaded machine makes every popover late), just as the chip for the buttons
    appears. Returns (page, toasts shown before the Enter) after the person pressed Enter under the popover."""
    backend = await env.start_backend()
    await backend.profile_answer("notice_period", "1 month")
    rig, page, panel = await ready_form(env, "ashby_apply.html", "overlay=2500")
    await page.wait_for_selector("#ashby_form", state="attached")
    await drafts_finished(env, page)
    await expect(page.locator("[data-testid=toast]")).to_have_count(0, timeout=20_000)  # the 'draft ready' notes are over: what comes next is news
    await page.locator("#location").focus()
    chip = Chip(page)
    await chip.expect_chip(state="VERIFIED", value="London")
    await ui.tab(page)  # picks the option; the page opens its popover over the next question
    await expect(page.locator("#sponsor_yes")).to_be_focused(timeout=15_000)
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No", timeout=15_000)
    if not await page.evaluate("!!document.getElementById('late-popover')"):  # a problem of the test (the popover is over), never of the product
        raise RuntimeError("setup: the popover was already gone when the chip for the sponsorship buttons appeared (2.5 s): widen overlay=")
    noted = set(await page.locator("[data-testid=toast]").all_inner_texts())
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.3)
    return page, noted


@scenario(needs=SUGGEST, timeout=150)
async def scenario_enter_while_a_popover_covers_the_buttons_writes_nothing_under_it_and_says_so(env: Env) -> None:
    """SEC-01 for the Enter key: the assistant writes only into what a person can see. While another question's popover lies over the
    sponsorship buttons an Enter presses neither of them (nor the popover's own option), never submits anything, and is not silent:
    the person gets a toast of the error kind. (That a loaded machine opens this window by itself is how two journeys failed in phase G5.)"""
    page, noted = await enter_while_a_popover_covers_the_buttons(env)
    check("nothing is pressed under the popover",
          await page.get_attribute("#sponsor_no", "aria-pressed") == "false" and await page.get_attribute("#sponsor_yes", "aria-pressed") == "false"
          and await page.evaluate("window.selections.sponsor === undefined"))
    check("the popover's own option was not clicked either", await page.evaluate("document.getElementById('late-popover') !== null"))
    await expect(page.locator("[data-testid=toast][data-kind=error]").first).to_be_attached(timeout=5_000)  # and the person is told
    await ui.assert_never_submitted(page)

