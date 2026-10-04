from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from jobagent.automation import BrowserManager
from jobagent.automation.consent import radio_group, resolution_element
from jobagent.automation.disclosures import disclosure_memory_key
from jobagent.db import Database
from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.store import entity, ingest_fact, policy


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def fixture_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(Path(__file__).parent / "fixtures"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
async def browser(tmp_path):
    manager = BrowserManager(tmp_path)
    await manager.start(headless=True)
    yield manager
    await manager.close()


@pytest.fixture
def confirmed_engine(tmp_path):
    db = Database(tmp_path / "confirmed-disclosures.sqlite")
    person = entity(db, "person", "Fixture candidate")
    for key, value in {
        "pronouns": "He", "gender": "Man", "sexual_orientation": "Heterosexual",
        "ethnicity": "White - Other", "religion": "Atheist", "mental_health": "None",
        "neurodivergence": "No", "disability": "No",
    }.items():
        ingest_fact(db, person, key, value, source="Fixture confirmation", evidence={}, verified=True)
    # Old policies must not substitute a decline or infer recruitment adjustments.
    policy(db, "optional_demographics", "decline_or_blank")
    policy(db, "adjustments_request", "none_requested")
    policy(db, "personal_disclosures", "use_confirmed")
    return CandidateEngine(db)


def settings(engine, **extra):
    return {"_candidate_engine": engine, "assisted_autofill": True,
            "skip_optional_unknown": True, "browser_max_steps": 60, **extra}


async def assert_confirmed_values(browser):
    for name, value in {
        "pronouns": "he", "orientation": "straight", "ethnicity": "white_other",
        "religion": "none", "mental_health": "None", "sex": "", "adjustments": "",
        "gender_other": "",
    }.items():
        assert await browser.page.locator(f"[name={name}]").input_value() == value, name
    for name, value in {"gender": "g0", "neurodivergence": "no", "disability": "no"}.items():
        assert await browser.page.locator(f"[name={name}]:checked").input_value() == value, name
    assert await browser.page.evaluate("window.submissions") == 0


async def test_confirmed_disclosures_fill_native_choices_and_keep_unconfirmed_concepts_blank(
    browser, fixture_server, confirmed_engine
):
    result = await browser.fill_application(
        {"id": "confirmed", "url": fixture_server + "/confirmed_disclosures.html"}, [], [],
        settings=settings(confirmed_engine),
    )
    assert result["status"] == "needs_review", result
    await assert_confirmed_values(browser)
    assert not any(a.get("answer") == "Prefer not to say" for a in result["answers"])
    assert all(a.get("verified") for a in result["answers"])


async def test_confirmed_radio_section_consent_previews_exact_mapped_choice(
    browser, fixture_server, confirmed_engine
):
    app = {"id": "review-confirmed", "url": fixture_server + "/confirmed_disclosures.html"}
    options = settings(confirmed_engine, section_consent=True)
    reviewed = []
    for _ in range(12):
        result = await browser.fill_application(app, [], [], settings=options)
        if result["status"] != "section_review":
            break
        reviewed.extend(result["section"]["fields"])
        if any(f["question"] == "What is your gender identity?" for f in result["section"]["fields"]):
            gender = [f for f in result["section"]["fields"] if f["question"] == "What is your gender identity?"]
            assert len(gender) == 1 and gender[0]["answer"] == "Man" and gender[0]["operation"] == "CHECK"
            assert not await browser.page.locator("[name=gender]:checked").count()
        options["_section_grant"] = await browser.authorize_section(app["id"], result["snapshot_id"])
    assert result["status"] == "needs_review", result
    await assert_confirmed_values(browser)
    assert reviewed and not any(f["question"] == "Sex assigned at birth" for f in reviewed)


async def test_unsupported_prefilled_disclosure_is_reviewed_and_not_reused_from_gender(
    browser, fixture_server, confirmed_engine
):
    app = {"id": "prefilled", "url": fixture_server + "/confirmed_disclosures.html"}
    await browser.open(app["url"])
    browser._active_application_id = app["id"]
    await browser.page.locator("[name=sex]").select_option("f")
    result = await browser.fill_application(app, [], [], settings=settings(confirmed_engine))
    assert result["status"] == "human_required", result
    assert any(q["question"] == "Sex assigned at birth" and q["current_value"] == "f" for q in result["questions"])
    assert await browser.page.locator("[name=sex]").input_value() == "f"
    assert await browser.page.locator("[name=gender]:checked").input_value() == "g0"
    assert await browser.page.evaluate("window.submissions") == 0


@pytest.mark.parametrize("missing", ["neurodivergence", "disability", "mental_health"])
async def test_related_health_answers_do_not_supply_an_unconfirmed_attribute(
    browser, fixture_server, confirmed_engine, missing
):
    confirmed_engine.db.execute(
        "UPDATE facts SET verification_status='unverified' WHERE id IN "
        "(SELECT fact_id FROM knowledge_facts WHERE concept=?)", (missing,)
    )
    result = await browser.fill_application(
        {"id": "missing-" + missing, "url": fixture_server + "/confirmed_disclosures.html"}, [], [],
        settings=settings(confirmed_engine),
    )
    assert result["status"] == "needs_review", result
    if missing == "mental_health":
        assert await browser.page.locator("[name=mental_health]").input_value() == ""
    else:
        assert not await browser.page.locator(f"[name={missing}]:checked").count()
    assert await browser.page.evaluate("window.submissions") == 0


async def test_required_unknown_disclosure_stops_at_browser_question(
    browser, fixture_server, confirmed_engine
):
    app = {"id": "required-unknown", "url": fixture_server + "/confirmed_disclosures.html"}
    await browser.open(app["url"])
    browser._active_application_id = app["id"]
    await browser.page.locator("[name=sex]").evaluate("node => node.required = true")
    result = await browser.fill_application(app, [], [], settings=settings(confirmed_engine))
    assert result["status"] == "human_required", result
    question = next(q for q in result["questions"] if q["question"] == "Sex assigned at birth")
    assert question["required"] and question["options"]
    assert await browser.page.locator("[name=sex]").input_value() == ""
    assert await browser.page.evaluate("window.submissions") == 0


@pytest.mark.parametrize("section_consent", [False, True])
@pytest.mark.parametrize("contextual_label", [False, True])
async def test_observed_menu_toggle_reveals_alias_before_search_can_hide_it(
    browser, fixture_server, confirmed_engine, section_consent, contextual_label
):
    app = {"id": "menu-toggle", "url": fixture_server + "/disclosure_menu_toggle.html"}
    options = settings(confirmed_engine, section_consent=section_consent)
    question = "Describe your gender"
    if contextual_label:
        await browser.open(app["url"])
        browser._active_application_id = app["id"]
        await browser.page.evaluate("""() => {
            document.querySelector('label').textContent='Please select';
            const description=document.createElement('p');description.id='meaning';
            description.textContent='Describe your gender';document.querySelector('fieldset').prepend(description);
            document.querySelector('#gender').setAttribute('aria-describedby','meaning');
        }""")
        question = "Please select"
    result = await browser.fill_application(app, [], [], settings=options)
    if section_consent:
        assert result["status"] == "section_review", result
        assert len(result["section"]["fields"]) == 1
        preview = result["section"]["fields"][0]
        assert (preview["question"], preview["answer"], preview["operation"]) == (question, "Man", "CLICK")
        assert preview["fact_ids"] == [confirmed_engine.knowledge.records("person")[0]["fields"]["gender"]["id"]]
        assert await browser.page.locator("#gender").input_value() == ""
        assert await browser.page.locator("[role=option]").count() == 0
        options["_section_grant"] = await browser.authorize_section(app["id"], result["snapshot_id"])
        result = await browser.fill_application(app, [], [], settings=options)
    assert result["status"] == "needs_review", result
    assert await browser.page.locator("#gender").input_value() == "Male"
    assert await browser.page.evaluate("window.searches") == []
    assert await browser.page.evaluate("window.submissions") == 0
    assert all(step["operation"] != "TYPE_TEXT" for step in result["steps"])


async def test_generic_inline_question_retains_disclosure_meaning_for_saved_response(
    browser, fixture_server, confirmed_engine
):
    app = {"id": "context-memory", "url": fixture_server + "/confirmed_disclosures.html"}
    await browser.open(app["url"])
    browser._active_application_id = app["id"]
    await browser.page.locator("[name=sex]").select_option("f")
    await browser.page.locator("[name=sex]").evaluate("""node => {
        node.parentElement.firstChild.textContent='Please select ';
        const hint=document.createElement('p');hint.id='meaning';hint.textContent='Sex assigned at birth';
        node.parentElement.append(hint);node.setAttribute('aria-describedby','meaning');
    }""")
    result = await browser.fill_application(app, [], [], settings=settings(confirmed_engine))
    assert result["status"] == "human_required", result
    question = next(q for q in result["questions"] if q["question"] == "Please select Sex assigned at birth")
    snapshot = await browser.observe()
    control = next(e for e in snapshot["elements"] if e.get("name") == "sex")
    for key in ("context", "section_label", "description"):
        assert question[key] == control[key]
    assert disclosure_memory_key(question["question"], question) == disclosure_memory_key(question["question"], control)
    assert await browser.page.evaluate("window.submissions") == 0


@pytest.mark.parametrize("required", [False, True])
async def test_duplicate_generic_questions_keep_their_own_targets_and_personal_context(
    browser, fixture_server, confirmed_engine, required
):
    app = {"id": "duplicate-context", "url": fixture_server + "/confirmed_disclosures.html"}
    await browser.open(app["url"])
    browser._active_application_id = app["id"]
    await browser.page.set_content("""<form onsubmit="event.preventDefault();window.submissions++">
      <section><h2>Diversity survey</h2>
      <label for="sex">Please select</label><p id="sex_hint">Sex assigned at birth</p>
      <select id="sex" aria-describedby="sex_hint"><option value="">Choose</option><option value="f">Female</option><option value="m">Male</option></select>
      <label for="veteran">Please select</label><p id="veteran_hint">Veteran status</p>
      <select id="veteran" aria-describedby="veteran_hint"><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select>
      </section><button type="submit">Submit application</button></form><script>window.submissions=0</script>""")
    if required:
        await browser.page.locator("select").evaluate_all("nodes => nodes.forEach(node => node.required = true)")
    else:
        await browser.page.locator("#sex").select_option("f")
        await browser.page.locator("#veteran").select_option("yes")
    result = await browser.fill_application(app, [], [], settings=settings(confirmed_engine))
    assert result["status"] == "human_required", result
    questions = [q for q in result["questions"] if q["question"] == "Please select"]
    assert len(questions) == 2, result
    assert len({q["target"] for q in questions}) == 2
    assert {q["description"] for q in questions} == {"Sex assigned at birth", "Veteran status"}
    assert len({disclosure_memory_key(q["question"], q) for q in questions}) == 2
    snapshot = await browser.observe()
    for question in questions:
        control = next(e for e in snapshot["elements"] if e["node_id"] == question["node_id"])
        assert question["description"] == control["description"]
        assert question["current_value"] == control["value"]
        assert question["options"] == control["options"]
        assert disclosure_memory_key(question["question"], question) == disclosure_memory_key(question["question"], control)
    assert await browser.page.evaluate("window.submissions") == 0


async def test_unrelated_or_submit_buttons_are_not_observed_as_menu_toggles(browser, fixture_server):
    await browser.open(fixture_server + "/disclosure_menu_toggle.html")
    cases = [
        '<input role="combobox" aria-haspopup="listbox"><button type="submit"><svg></svg></button>',
        '<input role="combobox" aria-haspopup="listbox"><button><svg></svg></button>',
        '<input role="combobox" aria-haspopup="listbox"><button type="button">Send</button>',
        '<input role="combobox" aria-haspopup="listbox"><button type="button"><svg></svg></button><input>',
        '<input role="combobox" aria-haspopup="listbox"><button type="button" aria-label="Submit application"><svg></svg></button>',
    ]
    for markup in cases:
        await browser.page.set_content('<form><div>' + markup + '</div></form>')
        snapshot = await browser.observe()
        control = next(e for e in snapshot["elements"] if e["role"] == "combobox")
        assert control.get("dropdown_trigger_id") is None


def test_radio_options_are_scoped_to_document_form_and_group():
    first = {"role": "radio", "document_id": "main", "form_id": "one", "group": "identity",
             "node_id": "1", "label": "Woman", "value": "f", "operations": ["CHECK"]}
    man = {**first, "node_id": "2", "label": "Man", "value": "m"}
    unrelated = [{**man, "document_id": "frame"}, {**man, "form_id": "two"}, {**man, "group": "sex"}]
    assert radio_group(first, [first, man, *unrelated]) == [first, man]
    assert [o["label"] for o in resolution_element(first, [first, man, *unrelated])["options"]] == ["Woman", "Man"]
    ungrouped = {**first, "group": ""}
    assert radio_group(ungrouped, [ungrouped, {**man, "group": ""}]) == [ungrouped]
