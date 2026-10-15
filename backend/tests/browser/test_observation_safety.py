"""observation.js: what a question is called, and what is NEVER part of it.

Three findings of the extension review live in this shared observer (the Playwright filler uses it too, so the default call must keep its
output unless the output was plainly wrong):

* SEC-07: what a person types into a rich-text editor (contenteditable / role=textbox) is an ANSWER. It must never become part of a label,
  a context, a description or the employer text, whatever the markup around the editor looks like.
* SAFE-05: the section of a control is the nearest PRECEDING heading-like block that still includes it, not the first heading of its
  container: the "Emergency contact" fields of a form must not be titled "Personal information".
* UX-07 / CPL-17: a file input is named by its visible label ("Resume/CV"), not by its name/id ("job application[resume] resume"). This one is
  opt-in ({widgets: true}, the extension): the filler's engine keeps reading the hint it always read.

`MERIDIAN_OBSERVATION_JS=<file>` runs the same tests against another copy of the script (the mutation checks)."""

import json
import os
from pathlib import Path

import pytest

from jobagent.automation import BrowserManager
from jobagent.automation.browser import OBSERVATION_SCRIPT

SCRIPT = Path(os.environ["MERIDIAN_OBSERVATION_JS"]).read_text() if os.environ.get("MERIDIAN_OBSERVATION_JS") else OBSERVATION_SCRIPT
MODES = [None, {"widgets": True}]
MODE_IDS = ["filler", "extension"]


@pytest.fixture
async def observer(tmp_path):
    browser = BrowserManager(tmp_path)
    await browser.start(headless=True)
    yield browser
    await browser.close()


async def observe(browser, html, options=None, typed=None):
    """What the observer says about `html`. `typed` is a list of (selector, text) typed into editors BEFORE it looks."""
    await browser.page.set_content(html)
    for selector, text in typed or []:
        await browser.page.evaluate("([sel, text]) => { const el = document.querySelector(sel); el.textContent = text; }", [selector, text])
    return await browser.page.evaluate("([src, options]) => (0, eval)('(' + src + ')')(options)", [SCRIPT, options])


def by_id(snapshot):
    return {e["id"]: e for e in snapshot["elements"] if e["id"]}


# ------------------------------------------------------------------------------------------------------ SEC-07: typed text

SECRET = "MY_PRIVATE_COVER_LETTER_TEXT salary 123456"


def strings_of(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_of(item)


# every text of a control the extension sends (scan.ts never sends `value`, `selected_text` or `selected_values` of a text control: has_value only)
SENT_KEYS = ("label", "context", "description", "placeholder", "section_label", "group", "field_path", "name", "id", "manual_key")


def assert_no_secret(snapshot):
    """Everything the extension takes from the observer: the questions of the controls and the employer text. (`text`, the visible page text,
    only feeds the challenge/login checks of the observer itself and is never sent anywhere.)"""
    sent = [{k: e.get(k) for k in SENT_KEYS} | {"options": e.get("options")} for e in snapshot["elements"]] + [snapshot["job_context"]]
    for text in strings_of(sent):
        assert "MY_PRIVATE" not in text and "123456" not in text, f"typed text reached the observation: {text[:160]!r}"


GREENHOUSE_SHAPED = """
<form>
  <div class="application-question"><label>Tell us about yourself</label>
    <div id="bio" contenteditable="true" role="textbox" aria-multiline="true"></div>
    <input type="text" id="linkedin" aria-label="LinkedIn Profile"></div>
  <div class="application-question"><label>Anything else?</label><input type="text" id="extra"></div>
</form>"""

LEVER_SHAPED = """
<form><ul>
  <li class="application-question"><label><div class="application-label">Additional information</div>
    <div class="application-field"><div id="info" contenteditable="plaintext-only"></div></div></label></li>
  <li class="application-question"><label><div class="application-label">Phone</div>
    <div class="application-field"><input type="tel" id="phone"></div></label></li>
</ul></form>"""

ARIA_LABELLED = """
<form><span id="cap">Cover <span id="cap_editor" contenteditable=""></span> letter</span>
  <input id="cover" type="text" aria-labelledby="cap"></form>"""

ROLE_TEXTBOX = """
<form><div class="application-question"><label>Why us?</label><div id="why" role="textbox" tabindex="0"></div>
  <input type="text" id="company_site" aria-label="Website"></div></form>"""


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("html,editor", [
    (GREENHOUSE_SHAPED, "#bio"), (LEVER_SHAPED, "#info"), (ARIA_LABELLED, "#cap_editor"), (ROLE_TEXTBOX, "#why"),
])
async def test_text_typed_in_an_editor_never_reaches_a_label_context_or_description(observer, html, editor, options):
    snapshot = await observe(observer, html, options, typed=[(editor, SECRET)])
    assert len(snapshot["elements"]) >= 1
    assert_no_secret(snapshot)


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_the_questions_themselves_survive_when_the_editor_is_stripped(observer, options):
    snapshot = await observe(observer, GREENHOUSE_SHAPED, options, typed=[("#bio", SECRET)])
    elements = by_id(snapshot)
    assert elements["linkedin"]["label"] == "LinkedIn Profile"
    assert elements["extra"]["label"] == "Anything else?" or "Anything else?" in elements["extra"]["context"]
    lever = by_id(await observe(observer, LEVER_SHAPED, options, typed=[("#info", SECRET)]))
    assert "Phone" in lever["phone"]["label"] and "Phone" in lever["phone"]["context"]


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_an_unlabelled_editor_is_not_named_by_what_was_typed_into_it(observer, options):
    snapshot = await observe(observer, '<form><div contenteditable="true"></div><div role="textbox" tabindex="0"></div><input id="x" aria-label="Name"></form>',
                             options)
    await observer.page.evaluate("() => document.querySelectorAll('[contenteditable],[role=textbox]').forEach((el) => { el.textContent = 'MY_PRIVATE_FREE_TEXT'; })")
    snapshot = await observer.page.evaluate("([src, options]) => (0, eval)('(' + src + ')')(options)", [SCRIPT, options])
    editors = [e for e in snapshot["elements"] if e["tag"] == "div"]
    assert len(editors) == 2
    for editor in editors:
        assert "MY_PRIVATE" not in editor["label"] and "MY_PRIVATE" not in editor["manual_key"], editor["label"]


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_editors_in_the_employer_text_are_never_part_of_the_job_description(observer, options):
    html = ('<div class="posting-description"><p>' + "We build robots for warehouses and we are hiring a platform engineer. " * 3 +
            '</p><div id="notes" contenteditable="plaintext-only"></div><div id="r" role="textbox"></div></div>')
    await observer.page.set_content(html)
    await observer.page.evaluate("() => { document.getElementById('notes').textContent = 'MY_PRIVATE_NOTE 123456'; document.getElementById('r').textContent = 'MY_PRIVATE_ROLE 123456'; }")
    snapshot = await observer.page.evaluate("([src, options]) => (0, eval)('(' + src + ')')(options)", [SCRIPT, options])
    sections = snapshot["job_context"]["sections"]
    assert sections and "robots" in sections[0]["text"]
    assert_no_secret({"elements": [], "job_context": sections})


# ------------------------------------------------------------------------------------------------------ SAFE-05: sections

THREE = '<label for="{p}n">Name</label><input id="{p}n" name="name"><label for="{p}p">Phone</label><input id="{p}p" name="phone" type="tel">' \
        '<label for="{p}e">Email</label><input id="{p}e" name="email" type="email">'


def block(prefix):
    return THREE.format(p=prefix)


STRUCTURES = {
    "div_section_title": '<section><h2>Personal information</h2>' + block("a") + '<div class="section-title">Emergency contact</div>' + block("b") + '</section>',
    "strong_in_paragraph": '<section><h2>Personal information</h2>' + block("a") + '<p><strong>Emergency contact</strong></p>' + block("b") + '</section>',
    "span_heading": '<form><span class="heading">Personal information</span>' + block("a") + '<span class="heading">Emergency contact</span>' + block("b") + '</form>',
    "card_title": '<form><div class="card"><div class="card-title">Personal information</div><div class="card-body">' + block("a") +
                  '</div></div><div class="card"><div class="card-title">Emergency contact</div><div class="card-body">' + block("b") + '</div></div></form>',
    "section_with_second_h3": '<section><h2>Personal information</h2><form>' + block("a") + '<h3>Emergency contact</h3>' + block("b") + '</form></section>',
    "section_wrapping_the_form": '<section><h2>Apply for this job</h2><form><h3>Personal information</h3>' + block("a") +
                                 '<h3>Emergency contact</h3>' + block("b") + '</form></section>',
    "fieldset_legend_then_h3": '<form><fieldset><legend>Application</legend><h3>Personal information</h3>' + block("a") +
                               '<h3>Emergency contact</h3>' + block("b") + '</fieldset></form>',
    "table_header_rows": '<form><table><tr><th colspan="2">Personal information</th></tr>' +
                         ''.join(f'<tr><td>{n}</td><td><input id="a{i}" name="{n.lower()}"></td></tr>' for i, n in enumerate(("Name", "Phone", "Email"))) +
                         '<tr><th colspan="2">Emergency contact</th></tr>' +
                         ''.join(f'<tr><td>{n}</td><td><input id="b{i}" name="{n.lower()}"></td></tr>' for i, n in enumerate(("Name", "Phone", "Email"))) +
                         '</table></form>',
    "bold_div_in_a_wrapper": '<section><div class="hdr"><div style="font-weight:700">Personal information</div></div>' + block("a") +
                             '<div class="hdr"><div style="font-weight:700">Emergency contact</div><p>Who should we call?</p></div>' + block("b") + '</section>',
}


def first_ids(structure):
    if structure == "table_header_rows":
        return ["a0", "a1", "a2"], ["b0", "b1", "b2"]
    return ["an", "ap", "ae"], ["bn", "bp", "be"]


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("structure", sorted(STRUCTURES))
async def test_the_section_of_a_control_is_the_nearest_preceding_heading(observer, structure, options):
    elements = by_id(await observe(observer, STRUCTURES[structure], options))
    first, second = first_ids(structure)
    assert [elements[i]["section_label"] for i in first] == ["Personal information"] * 3, structure
    assert [elements[i]["section_label"] for i in second] == ["Emergency contact"] * 3, structure


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_a_bold_caption_of_the_previous_question_is_not_a_section(observer, options):
    html = ('<form><h2>Your details</h2>'
            '<div class="field"><strong>Phone</strong><input id="p" name="phone"></div>'
            '<div class="field"><b class="question-title">Email</b><input id="e" name="email"></div>'
            '<div class="field"><span class="label">Name <b>(as on your passport)</b></span><input id="n" name="name"></div></form>')
    elements = by_id(await observe(observer, html, options))
    assert {elements[i]["section_label"] for i in "pen"} == {"Your details"}


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_the_section_stays_the_first_heading_when_there_is_only_one(observer, options):
    html = ('<section><h2>Contact details</h2><label for="a">Email</label><input id="a" type="email">'
            '<label for="b">Phone</label><input id="b" type="tel"></section>'
            '<fieldset><legend>Do you need sponsorship?</legend><label><input type="radio" id="r1" name="s" value="y"> Yes</label>'
            '<label><input type="radio" id="r2" name="s" value="n"> No</label></fieldset>')
    elements = by_id(await observe(observer, html, options))
    assert elements["a"]["section_label"] == elements["b"]["section_label"] == "Contact details"
    assert elements["r1"]["section_label"] == "Do you need sponsorship?"
    # the section identity is the container's, as it always was: two titles in one container do not split it
    assert elements["a"]["section_id"] == elements["b"]["section_id"] != elements["r1"]["section_id"]


@pytest.mark.parametrize("options", MODES, ids=MODE_IDS)
async def test_a_heading_of_a_section_that_closed_before_the_control_does_not_title_it(observer, options):
    html = ('<form><h2>Referee</h2><fieldset><legend>Education</legend><label for="u">University</label><input id="u"></fieldset>'
            '<label for="rn">Name</label><input id="rn"></form>')
    elements = by_id(await observe(observer, html, options))
    assert elements["u"]["section_label"] == "Education"
    # the nearest heading ABOVE "Name" is the legend of a fieldset that has closed: it titles nothing after it
    assert elements["rn"]["section_label"] == "Referee"


# ------------------------------------------------------------------------------------------------------ UX-07 / CPL-17: file labels

FILES = """
<form>
  <label for="resume">Resume/CV *</label><input type="file" id="resume" name="job_application[resume]">
  <label for="cover_letter">Cover Letter</label><input type="file" id="cover_letter" name="job_application[cover_letter]">
  <label for="sample">Writing sample</label><input type="file" id="sample" name="sample">
  <input type="file" id="aria" name="cv_upload" aria-label="Your CV (PDF)">
  <div id="t">Academic transcript</div><input type="file" id="transcript" name="transcript" aria-labelledby="t">
  <table><tr><td><label>Resume</label></td><td><label class="btn" for="icims">Browse...</label>
    <input type="file" id="icims" name="resume_file" style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0"></td></tr></table>
  <input type="file" id="bare" name="candidate_resume">
</form>"""


async def test_the_extension_names_a_file_input_by_what_the_page_shows(observer):
    elements = by_id(await observe(observer, FILES, {"widgets": True}))
    assert elements["resume"]["label"] == "Resume/CV"
    assert elements["cover_letter"]["label"] == "Cover Letter"
    assert elements["sample"]["label"] == "Writing sample"
    assert elements["aria"]["label"] == "Your CV (PDF)"
    assert elements["transcript"]["label"] == "Academic transcript"
    # a button caption names no question: the text before it does (table layouts); with nothing at all the engine hint stays
    assert elements["icims"]["label"] == "Resume"
    assert elements["bare"]["label"] == "candidate resume bare"
    for dom_id, element in elements.items():  # the hint is still derivable: name and id travel in their own fields
        assert element["name"] or dom_id == "transcript" or element["id"]


async def test_the_filler_keeps_reading_the_hint_it_always_read(observer):
    elements = by_id(await observe(observer, FILES, None))
    assert elements["resume"]["label"] == "job application[resume] resume"
    assert elements["cover_letter"]["label"] == "job application[cover letter] cover letter"
    assert elements["sample"]["label"] == "Writing sample"
    assert json.dumps(elements["resume"]["operations"]) == '["UPLOAD"]'


async def test_the_headings_of_a_section_are_listed_once_per_section_not_once_per_control(observer):
    inputs = "".join(f'<label>Q{i} <input name="q{i}"></label>' for i in range(30))
    await observer.page.set_content(f"""
      <section><h2>First section</h2>{inputs}<div class="section-title">Second part</div>{inputs.replace('name="q', 'name="p')}</section>
      <section><h2>Other</h2>{inputs.replace('name="q', 'name="r')}</section>""")
    counts = await observer.page.evaluate(
        """([src]) => { const original = Element.prototype.querySelectorAll; let lists = 0, all = 0;
            Element.prototype.querySelectorAll = function (selector) { all++; if (String(selector).startsWith('legend,h1,h2')) lists++; return original.call(this, selector); };
            try { const snapshot = (0, eval)('(' + src + ')')(); return {lists, all, controls: snapshot.elements.length}; }
            finally { Element.prototype.querySelectorAll = original; } }""", [SCRIPT])
    assert counts["controls"] == 90
    assert counts["lists"] == 2, counts  # one per section
