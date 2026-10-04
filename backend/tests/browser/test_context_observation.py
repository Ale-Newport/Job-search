import pytest

from jobagent.automation import BrowserManager, Decision, Operation
from jobagent.automation.types import StaleState


@pytest.fixture
async def context_browser(tmp_path):
    browser = BrowserManager(tmp_path)
    await browser.start(headless=True)
    yield browser
    await browser.close()


async def test_description_aria_help_and_section_are_retained_without_candidate_values(context_browser):
    await context_browser.page.set_content("""
      <section aria-label="Motivation and working style"><h2>Motivation and working style</h2>
      <div data-field-path="motivation" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="motivation">Anything else you'd like to share?</label>
        <p class="ashby-application-form-question-description">Such as your motivation to apply or additional context for your application?</p>
        <p id="limit">Use no more than 200 words. <span data-candidate-answer>Private cached draft</span></p>
        <p id="error" role="alert">Private validation echo: person@example.test</p>
        <textarea id="motivation" aria-describedby="limit error">This is my private answer</textarea>
      </div></section>""")
    snapshot = await context_browser.observe()
    field = next(e for e in snapshot["elements"] if e["id"] == "motivation")
    assert "motivation to apply" in field["description"]
    assert "200 words" in field["description"]
    assert field["section_label"] == "Motivation and working style"
    assert "private answer" not in field["description"]
    assert "Private" not in field["description"] and "person@example.test" not in field["description"]
    assert field["value"] == "This is my private answer"


async def test_generic_and_shadow_field_descriptions_are_kept(context_browser):
    await context_browser.page.set_content("""
      <div class="form-field"><label for="fit">Additional information</label>
        <p class="help-text">Explain how your experience relates to the advertised position.</p>
        <textarea id="fit" placeholder="Your motivation"></textarea></div><div id="shadow"></div>
      <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML =
        '<label for="visa">Visa status</label><p id="help">Include the route and current study status.</p>' +
        '<input id="visa" aria-describedby="help">';</script>""")
    snapshot = await context_browser.observe()
    generic = next(e for e in snapshot["elements"] if e["id"] == "fit")
    shadow = next(e for e in snapshot["elements"] if e["id"] == "visa")
    assert "advertised position" in generic["description"]
    assert generic["placeholder"] == "Your motivation"
    assert shadow["description"] == "Include the route and current study status."


async def test_employer_context_excludes_all_candidate_widgets_and_errors(context_browser):
    await context_browser.page.set_content("""
      <h1>Machine Learning Engineer</h1><div id="content">
        <div class="job-description"><p>Build reliable recommendation services with Python, evaluate models and improve passenger journeys.</p></div>
        <form><label>Name<input value="Private Person"></label>
          <textarea>Private motivation draft</textarea><p>Private saved application history</p></form>
        <div contenteditable="true">Private applicant text</div>
        <div data-candidate-answer>Private applicant summary</div>
        <div role="alert">Private email rejected: personal@example.test</div>
        <div class="select__container">Private selected ethnicity</div>
        <div class="application-question">Private question<input value="Secret answer"></div>
      </div>""")
    snapshot = await context_browser.observe()
    observed = snapshot["frames"][0]["job_context"]
    assert observed["title"] == "Machine Learning Engineer"
    assert "recommendation services" in str(observed)
    assert "Private" not in str(observed) and "Secret" not in str(observed)
    assert "personal@example.test" not in str(observed)


async def test_observed_clear_operations_require_supported_controls(context_browser):
    await context_browser.page.set_content("""
      <label>Pronouns<input id="plain" value="test"></label>
      <input id="readonly" readonly value="test"><input id="file" type="file">
      <select id="native"><option value="">Choose</option><option selected>test</option></select>
      <div class="select__control"><input id="combo" role="combobox" value="Search"></div>
      <div class="select__control"><input id="clearable" role="combobox" value="Search">
        <span class="select__clear-indicator">Clear selection</span></div>""")
    fields = {e["id"]: e for e in (await context_browser.observe())["elements"] if e["id"]}
    assert "CLEAR" in fields["plain"]["operations"] and "CLEAR" in fields["native"]["operations"]
    for key in ("readonly", "file", "combo"):
        assert "CLEAR" not in fields[key]["operations"]
    assert "CLEAR" in fields["clearable"]["operations"]
    assert fields["clearable"]["clear_proxy_id"] is not None


HIDDEN_TOGGLES = """
  <style>.invisible-toggle{opacity:0;position:absolute;width:18px;height:18px}
  .option{padding:8px}label{display:inline-block;padding:5px}</style>
  <form onsubmit="event.preventDefault();window.submissions++">
    <div data-field-path="pronouns"><div class="ashby-application-form-question-title">Preferred pronouns</div>
      <div class="option"><span><span class="circle"></span><input class="invisible-toggle" type="radio" id="pronoun-a" name="pronouns" checked></span>
        <label for="pronoun-a">he/him/his</label></div>
      <div class="option"><span><span class="circle"></span><input class="invisible-toggle" type="radio" id="pronoun-b" name="pronouns"></span>
        <label for="pronoun-b">they/them/theirs</label></div>
    </div>
    <div data-field-path="disability"><div class="ashby-application-form-question-title">Disability status</div>
      <input type="checkbox" id="disability" style="display:none" checked><label for="disability">I have a disability</label>
    </div>
    <fieldset><legend>Can you work in London?</legend>
      <input class="invisible-toggle" id="work-yes" type="radio" name="location"><label for="work-yes">Yes</label>
      <input class="invisible-toggle" id="work-no" type="radio" name="location"><label for="work-no">No</label>
    </fieldset><button type="submit">Submit application</button>
  </form><script>window.submissions=0;</script>
"""


async def test_css_hidden_labeled_native_toggles_are_observed_with_checked_state(context_browser):
    await context_browser.page.set_content(HIDDEN_TOGGLES)
    fields = {e["id"]: e for e in (await context_browser.observe())["elements"] if e.get("id")}
    assert set(fields) == {"pronoun-a", "pronoun-b", "disability", "work-yes", "work-no"}
    assert fields["pronoun-a"]["checked"] is True and fields["pronoun-b"]["checked"] is False
    assert fields["pronoun-b"]["label"] == "they/them/theirs"
    assert fields["pronoun-a"]["context"] == "Preferred pronouns"
    assert fields["work-yes"]["context"] == "Can you work in London?"
    assert all(field["toggle_proxy_id"] and "CLEAR" in field["operations"] for field in fields.values())


async def test_hidden_toggle_interaction_uses_its_associated_visible_label(context_browser):
    await context_browser.page.set_content(HIDDEN_TOGGLES)
    snapshot = await context_browser.observe()
    target = next(e for e in snapshot["elements"] if e.get("id") == "work-yes")
    await context_browser.execute(Decision(Operation.CHECK, target["index"]), snapshot)
    assert await context_browser.page.locator("#work-yes").is_checked()
    snapshot = await context_browser.observe()
    target = next(e for e in snapshot["elements"] if e.get("id") == "disability")
    await context_browser.execute(Decision(Operation.UNCHECK, target["index"]), snapshot)
    assert not await context_browser.page.locator("#disability").is_checked()
    assert await context_browser.page.evaluate("window.submissions") == 0


async def test_prefilled_hidden_disclosures_clear_without_selecting_another_option(context_browser):
    await context_browser.page.set_content(HIDDEN_TOGGLES)
    context_browser._active_application_id = "hidden-disclosures"
    result = await context_browser.fill_application(
        {"id": "hidden-disclosures", "url": "https://example.test/synthetic"}, [], [],
        settings={"assisted_autofill": True, "skip_optional_unknown": True,
                  "sensitive_policies": {"personal_disclosures": {"action": "leave_blank"}}})
    assert result["status"] == "human_required", result
    assert all(q["question"] == "Can you work in London?" for q in result["questions"])
    assert not await context_browser.page.locator("[name=pronouns]:checked").count()
    assert not await context_browser.page.locator("#disability").is_checked()
    assert any(item["question"] == "Preferred pronouns" for item in result["omitted"])
    assert await context_browser.page.evaluate("window.submissions") == 0


async def test_controlled_hidden_radio_that_restores_selection_is_manual_boundary(context_browser):
    await context_browser.page.set_content(HIDDEN_TOGGLES)
    # Mimics a controlled radio group with no deselection state: the application
    # rejects the cleared value instead of treating DOM state as sufficient.
    await context_browser.page.locator("#pronoun-a").evaluate("node => node.addEventListener('click', () => {node.checked=true})")
    context_browser._active_application_id = "controlled-disclosures"
    result = await context_browser.fill_application(
        {"id": "controlled-disclosures", "url": "https://example.test/synthetic"}, [], [],
        settings={"assisted_autofill": True, "skip_optional_unknown": True,
                  "sensitive_policies": {"personal_disclosures": {"action": "leave_blank"}}})
    assert result["status"] == "human_required", result
    assert await context_browser.page.locator("#pronoun-a").is_checked()
    assert any(question["kind"] == "personal_disclosure_clear" for question in result["questions"])
    assert await context_browser.page.evaluate("window.submissions") == 0


async def test_hidden_toggle_proxy_respects_occlusion(context_browser):
    await context_browser.page.set_content(HIDDEN_TOGGLES + '<div style="position:fixed;inset:0;z-index:999;background:white"></div>')
    snapshot = await context_browser.observe()
    target = next(e for e in snapshot["elements"] if e.get("id") == "work-yes")
    with pytest.raises(StaleState, match="covered"):
        await context_browser.execute(Decision(Operation.CHECK, target["index"]), snapshot)
    assert not await context_browser.page.locator("#work-yes").is_checked()


async def test_unassociated_label_cannot_expose_a_hidden_control(context_browser):
    await context_browser.page.set_content('<input type="radio" name="unresolved" id="hidden" style="opacity:0" required>'
                                           '<label for="different">Visible unrelated answer</label>')
    snapshot = await context_browser.observe()
    assert not any(e.get("id") == "hidden" for e in snapshot["elements"])
    assert snapshot["unresolved_required"]
