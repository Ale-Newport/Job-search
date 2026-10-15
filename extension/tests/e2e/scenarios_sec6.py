"""Scenarios of the final security review of the content layer, through the REAL worker, bridge and backend:

* ``covers_that_take_no_click``  a field (and the approved CV's file input) under an opaque layer that a pointer falls through is no assist
                                 field: Tab writes nothing into it and attaches nothing
* ``exec_command``               a script's ``document.execCommand("insertText")`` raises TRUSTED input events: with "always save" it is
                                 still never stored as the person's correction
* ``typed_choices``              what a person types into a tag select or a creatable combobox never reaches the bridge
"""

from __future__ import annotations

import asyncio
import json

import pages
import ui
from framework import Env, check, scenario
from scale import millis
from scenarios_forms import SUGGEST, ready_form
from ui import Chip, Panel

COVER = "position:absolute;left:0;top:0;width:100%;height:100%;background:#fff;pointer-events:none;z-index:5"


def _with(fields: str, script: str = "") -> str:
    """The plain application page of the suite with extra markup in its form (and a script after it)."""
    html = pages.form_page([]).replace("</form>", fields + "\n</form>", 1)
    return html.replace("</body>", f"<script>{script}</script></body>", 1) if script else html


async def _walk(page, steps: int = 7) -> None:
    for _ in range(steps):
        await ui.tab(page)
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------------------------- HIGH-1
@scenario(needs=SUGGEST, timeout=240)
async def scenario_covers_that_take_no_click_hide_a_field_and_the_cv_input_from_tab(env: Env) -> None:
    """The reviewer's page: an email, a phone and the CV's file input, each under an opaque layer with ``pointer-events: none`` (and an
    ``::after`` layer). A pointer would fall through every one of them, a person cannot see any of them: Tab fills the fields a person sees
    and writes nothing into (and attaches nothing to) the others."""
    fields = f"""
<style>.sec-rel{{position:relative;height:34px;width:320px}}.sec-rel input{{position:absolute;left:0;top:0;width:300px;height:30px;box-sizing:border-box}}
.sec-after::after{{content:"";{COVER}}}</style>
<div><label for="sec_first">First name</label><div class="sec-rel"><input id="sec_first" name="sec_first"></div></div>
<div><label for="sec_email">Email address</label><div class="sec-rel"><input id="sec_email" name="sec_email" type="email"><div style="{COVER}"></div></div></div>
<div><label for="sec_phone">Phone</label><div class="sec-rel sec-after"><input id="sec_phone" name="sec_phone" type="tel"></div></div>
<div><label for="sec_cv">Resume/CV</label><div class="sec-rel"><input id="sec_cv" name="sec_cv" type="file"><div style="{COVER}"></div></div></div>
<div><label for="sec_last">Last name</label><div class="sec-rel"><input id="sec_last" name="sec_last"></div></div>"""
    env.fixtures.generated["sec_covers.html"] = _with(fields)
    await env.connected()
    page = await env.open("sec_covers.html", activate=True)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    chip = Chip(page)
    await page.locator("#sec_first").focus()
    await chip.expect_chip(state="VERIFIED", timeout=millis(30_000))
    await _walk(page)
    first = await ui.field_value(page, "#sec_first")
    check("the field a person sees is filled as always", first != "", first)
    for dom_id in ("#sec_email", "#sec_phone", "#sec_cv"):
        value = await ui.field_value(page, dom_id)
        check(f"{dom_id} under a cover that takes no click is left empty (no value, no file)", value in ("", None), value)
    wrote = {event.get("id") or event.get("name") for event in await ui.synthetic_events(page)}
    check("the extension wrote into none of the covered controls", not wrote & {"sec_email", "sec_phone", "sec_cv"}, sorted(wrote))
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- LOW-1
@scenario(needs=SUGGEST, timeout=180)
async def scenario_exec_command_text_is_never_stored_as_the_persons_correction(env: Env) -> None:
    """With "always save" a value the person types is stored without a prompt; a value a PAGE SCRIPT types with execCommand (trusted
    input events, no key) is not: nothing is stored and no application row is made."""
    backend = await env.start_backend()
    await backend.patch("/extension/settings", {"save_corrections": "always"})
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.evaluate("""() => { window.__ev = []; for (const t of ['beforeinput', 'input']) document.addEventListener(t, (e) => window.__ev.push([t, e.isTrusted]), true);
        const input = document.querySelector('#colour'); input.focus(); document.execCommand('insertText', false, 'PWNED-BY-THE-PAGE');
        setTimeout(() => document.querySelector('#employer').focus(), 100); }""")
    await asyncio.sleep(millis(3_000) / 1000)
    seen = await page.evaluate("window.__ev")
    check("the premise: the page's own insertText raised TRUSTED input events", ["input", True] in seen, seen)
    check("a value the page typed is not stored", backend.db_rows("SELECT COUNT(*) AS n FROM application_answers")[0]["n"] == 0)
    check("and no application row was made for it", backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 0)
    # what the person types with the keyboard is stored, without a prompt, as always
    await page.locator("#colour").focus()
    await chip.expect_chip(timeout=millis(20_000))
    await page.locator("#colour").fill("")
    await page.keyboard.type("Forest green")
    await ui.tab(page)
    for _ in range(40):
        if backend.db_rows("SELECT COUNT(*) AS n FROM application_answers WHERE answer LIKE '%Forest green%'")[0]["n"]:
            break
        await asyncio.sleep(0.5)
    check("what the person typed is stored", backend.db_rows("SELECT COUNT(*) AS n FROM application_answers WHERE answer LIKE '%Forest green%'")[0]["n"] == 1)
    check("the page's value never was", backend.db_rows("SELECT COUNT(*) AS n FROM application_answers WHERE answer LIKE '%PWNED%'")[0]["n"] == 0)


# ---------------------------------------------------------------------------------------------- LOW-2
SENTINELS = ["ZQ77SENTTAGONE", "ZQ78SENTCITYTWO", "ZQ79SENTTEAMTHREE"]


@scenario(needs=SUGGEST, timeout=180)
async def scenario_typed_tags_and_creatable_values_never_reach_the_bridge(env: Env) -> None:
    """A tag input that builds ``<option>``s out of what is typed, and divs that echo typed text: the worker (and so the bridge) hears that the
    field has a value, never the text."""
    fields = """
<div><label for="sec_skills">Your skills</label><select id="sec_skills" name="sec_skills" multiple><option value="py" selected>Python</option><option value="go">Go</option></select>
  <input id="sec_tag" aria-label="Type a skill and press Enter"></div>
<div><label for="sec_city">City</label><div id="sec_city" role="combobox" tabindex="0" aria-label="City" aria-expanded="false"><span id="sec_cityv">Select...</span></div></div>
<div><label for="sec_team">Team</label><div id="sec_team" role="combobox" tabindex="0" aria-label="Team" aria-haspopup="listbox" aria-controls="sec_teaml" aria-expanded="false"><span id="sec_teamv">Select...</span></div>
  <ul id="sec_teaml" role="listbox" aria-label="Team options"><li role="option">Platform</li><li role="option">Data</li></ul></div>
<div><label for="sec_other">Other</label><input id="sec_other" name="sec_other"></div>"""
    script = """
document.getElementById('sec_tag').addEventListener('keydown', (e) => { if (e.key !== 'Enter') return; e.preventDefault();
  const s = document.getElementById('sec_skills'), o = document.createElement('option'); o.value = e.target.value; o.textContent = e.target.value; o.selected = true;
  s.appendChild(o); e.target.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); });
for (const [id, shown] of [['sec_city', 'sec_cityv'], ['sec_team', 'sec_teamv']]) document.getElementById(id).addEventListener('keydown', (e) => {
  if (e.key.length !== 1) return; const v = document.getElementById(shown); if (v.textContent === 'Select...') v.textContent = ''; v.textContent += e.key; });"""
    env.fixtures.generated["sec_typed.html"] = _with(fields, script)
    rig = await env.connected()
    page = await env.open("sec_typed.html", activate=True)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    await rig.sw.evaluate("""() => { globalThis.__cap = []; const f = globalThis.fetch; globalThis.fetch = function (u, init) {
        try { globalThis.__cap.push([String(u), init && typeof init.body === 'string' ? init.body : null]); } catch (e) {} return f.apply(this, arguments); }; }""")
    await page.click("#sec_tag")
    await page.keyboard.type(SENTINELS[0])
    await page.keyboard.press("Enter")
    await page.click("#sec_city")
    await page.keyboard.type(SENTINELS[1])
    await page.click("#sec_team")
    await page.keyboard.type(SENTINELS[2])
    await page.click("#sec_other")
    await page.evaluate("() => { const d = document.createElement('div'); d.innerHTML = '<label for=sec_late>Late</label><input id=sec_late>'; document.querySelector('form').appendChild(d); }")
    await asyncio.sleep(millis(3_000) / 1000)
    captured = json.dumps(await rig.sw.evaluate("() => globalThis.__cap")).lower()
    check("the worker really sent suggestion requests while the person typed", "/suggest" in captured)
    for sentinel in SENTINELS:
        check(f"{sentinel} never reached the bridge", sentinel.lower() not in captured)
