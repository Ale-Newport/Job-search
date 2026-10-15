"""observation.js as the Playwright filler calls it (no arguments) and as the browser extension calls it ({widgets: true}).

The script is shared: the filler's upload and dropdown paths validate only the proxies they know, so everything that would make it
act on a control it never saw before is opt-in (`widgets`), while strictly better NAMES (a custom radio is named by its own text, a
radio group in a table layout by the cell before it) are on for everybody. These tests pin both halves."""

import pytest

from jobagent.automation import BrowserManager
from jobagent.automation.browser import OBSERVATION_SCRIPT

WIDGET_PAGE = """
<style>.dropzone{border:2px dashed #888;padding:14px;width:300px}</style>
<form>
  <div id="lb" role="presentation">Preferred work mode</div>
  <button type="button" id="mode" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="lb mode">Select...</button>
  <div id="relocate-label">Are you willing to relocate?</div>
  <div role="group" aria-labelledby="relocate-label"><button type="button" id="yes" aria-pressed="false">Yes</button><button type="button" id="no" aria-pressed="false">No</button></div>
  <div class="dropzone" role="presentation"><input type="file" id="portfolio" name="portfolio" style="display:none"><p>Drag a file here</p></div>
</form>"""


@pytest.fixture
async def observer_browser(tmp_path):
    browser = BrowserManager(tmp_path)
    await browser.start(headless=True)
    yield browser
    await browser.close()


async def observe_with(browser, options):
    """The extension's call: the same script, with options."""
    return await browser.page.evaluate("([src, options]) => (0, eval)('(' + src + ')')(options)", [OBSERVATION_SCRIPT, options])


async def test_the_filler_does_not_see_patterns_it_cannot_drive(observer_browser):
    await observer_browser.page.set_content(WIDGET_PAGE)
    snapshot = await observer_browser.observe()  # exactly what the filler gets: no options
    roles = {e["id"]: e["role"] for e in snapshot["elements"] if e["id"]}
    assert roles["mode"] == "button" and roles["yes"] == "button" and roles["no"] == "button"
    assert "portfolio" not in roles  # a hidden file input in a plain drop area: the filler's upload path could not validate its proxy
    assert not any(e.get("upload_proxy_id") for e in snapshot["elements"])


async def test_the_extension_sees_them_with_widgets_on(observer_browser):
    await observer_browser.page.set_content(WIDGET_PAGE)
    snapshot = await observe_with(observer_browser, {"widgets": True})
    by_id = {e["id"]: e for e in snapshot["elements"] if e["id"]}
    assert by_id["mode"]["role"] == "combobox" and by_id["mode"]["label"] == "Preferred work mode"  # not "Preferred work mode Select..."
    assert by_id["yes"]["role"] == by_id["no"]["role"] == "radio" and by_id["yes"]["group"] == by_id["no"]["group"] != ""
    assert by_id["yes"]["operations"] == ["CHECK"] and by_id["yes"]["context"] == "Are you willing to relocate?"
    assert by_id["portfolio"]["role"] == "upload" and by_id["portfolio"]["upload_proxy_id"]
    # "no options" and "widgets off" are the same call
    assert await observe_with(observer_browser, None) == await observe_with(observer_browser, {"widgets": False})


async def test_menu_submit_and_navigation_buttons_never_become_dropdowns(observer_browser):
    await observer_browser.page.set_content("""
      <header><button type="button" id="lang" aria-haspopup="listbox" aria-label="Language">English</button></header>
      <form>
        <button type="button" id="menu" aria-haspopup="menu" aria-label="Row actions">...</button>
        <label for="implicit">Pick one</label><button id="implicit" aria-haspopup="listbox">Pick</button>
        <button type="submit" id="explicit" aria-haspopup="listbox" aria-label="Send">Send</button>
        <button type="button" id="unnamed" aria-haspopup="listbox">Newest first</button>
        <input id="city" type="text" role="combobox" aria-label="City" aria-controls="popup">
        <button type="button" id="chevron" tabindex="-1" aria-haspopup="listbox" aria-label="Show cities" aria-controls="popup">v</button>
      </form>""")
    snapshot = await observe_with(observer_browser, {"widgets": True})
    roles = {e["id"]: e["role"] for e in snapshot["elements"] if e["id"]}
    for decoy in ("lang", "menu", "implicit", "explicit", "unnamed", "chevron"):
        assert roles[decoy] == "button", decoy
    assert roles["city"] == "combobox"


async def test_custom_radios_and_checkboxes_are_named_by_their_own_text_for_every_caller(observer_browser):
    await observer_browser.page.set_content("""
      <div id="mode-label">Preferred working mode</div>
      <div role="radiogroup" aria-labelledby="mode-label">
        <div role="radio" aria-checked="false" tabindex="0">Remote</div><div role="radio" aria-checked="false" tabindex="-1">Hybrid</div>
      </div>
      <div id="regions-label">Which regions?</div>
      <div role="group" aria-labelledby="regions-label"><div role="checkbox" aria-checked="false">Europe</div><div role="checkbox" aria-checked="false">Americas</div></div>
      <div class="form-group"><label>Contact consent</label><div role="checkbox" aria-checked="false">Yes, contact me</div></div>""")
    snapshot = await observer_browser.observe()
    radios = [e for e in snapshot["elements"] if e["role"] == "radio"]
    assert [e["label"] for e in radios] == ["Remote", "Hybrid"]
    assert {e["context"] for e in radios} == {"Preferred working mode"}  # the question is still the group's
    boxes = [e for e in snapshot["elements"] if e["role"] == "checkbox"]
    assert [e["label"] for e in boxes[:2]] == ["Europe", "Americas"]
    assert boxes[2]["label"] == "Contact consent"  # a lone checkbox keeps the caption of its field as its name


async def test_radio_groups_in_table_layouts_are_asked_with_the_text_of_the_cell_before_them(observer_browser):
    await observer_browser.page.set_content("""
      <table><tbody>
        <tr><th scope="row">Do you hold a driving licence?</th><td><label><input type="radio" name="drive" value="y"> Yes</label></td>
            <td><label><input type="radio" name="drive" value="n"> No</label></td></tr>
        <tr><td>Have you worked for us before?</td></tr>
        <tr><td><label><input type="radio" name="prev" value="y"> Yes</label> <label><input type="radio" name="prev" value="n"> No</label></td></tr>
        <tr><td>Can you travel?</td><td><label><input type="radio" name="travel" value="y"> Yes</label></td>
            <td>Can you relocate?</td><td><label><input type="radio" name="reloc" value="y"> Yes</label></td></tr>
      </tbody></table>
      <div style="display:table"><div style="display:table-row"><div style="display:table-cell"><label>Are you over 18?</label></div>
        <div style="display:table-cell"><label><input type="radio" name="adult" value="y"> Yes</label></div></div></div>
      <div><label><input type="radio" name="lone" value="y"> Yes</label> <label><input type="radio" name="lone" value="n"> No</label></div>""")
    snapshot = await observer_browser.observe()
    question = {e["name"]: e["context"] for e in snapshot["elements"] if e["role"] == "radio"}
    assert question == {"drive": "Do you hold a driving licence?", "prev": "Have you worked for us before?", "travel": "Can you travel?",
                        "reloc": "Can you relocate?", "adult": "Are you over 18?", "lone": ""}


async def test_each_section_heading_is_looked_up_once_and_still_found(observer_browser):
    inputs = "".join(f'<label>Q{i} <input name="q{i}"></label>' for i in range(30))
    await observer_browser.page.set_content(f"""
      <section><h2>First section</h2>{inputs}</section>
      <section>{inputs.replace('name="q', 'name="r')}</section>
      <form><h3>Sibling heading</h3><div><input name="s0"></div></form>""")
    calls = await observer_browser.page.evaluate(
        """([src]) => { const original = Element.prototype.querySelector; let lookups = 0;
            Element.prototype.querySelector = function (selector) { if (String(selector).startsWith('legend,h1')) lookups++; return original.call(this, selector); };
            try { window.__snapshot = (0, eval)('(' + src + ')')(); return lookups; } finally { Element.prototype.querySelector = original; } }""",
        [OBSERVATION_SCRIPT])
    # At most one per section, never one per control (61 controls). The heading of a section is found through ONE list of the section's
    # heading-like blocks (see test_observation_safety.py); the plain `legend,h1..` lookup only runs for a section with no heading in it.
    assert calls <= 2
    snapshot = await observer_browser.page.evaluate("window.__snapshot")
    sections = {e["name"]: e["section_label"] for e in snapshot["elements"] if e["name"]}
    assert sections["q0"] == sections["q29"] == "First section"
    assert sections["r0"] == "Application details"  # a section without a heading keeps the default
    assert sections["s0"] == "Sibling heading"  # the sibling-heading walk is untouched
