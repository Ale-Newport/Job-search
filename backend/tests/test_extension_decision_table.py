"""The decision table of the browser assistant (``extension/suggest.py``, ``extension/resolver.py``) for what the answer-engine
review found, driven through the real bridge routes with the seeded synthetic candidate. No network, no real model.

* a typed signature is an attestation: Enter, never Tab, whatever the protected-Tab switch says (CPL-02, SAFE-16);
* the page's own name for a control is never a verified reason to answer it (SAFE-22);
* a block the page repeats under one heading is entry 1, entry 2... (CPL-04);
* a draft is written in the language of its question and held to its limits in that language (CPL-03), is checked without a
  model for the employers and numbers it states (SAFE-21), and is never inserted on a Tab when only those checks ran;
* a control made of a huge text cannot hold the event loop (SEC-02).
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import (ScriptedModel, bridge, el, http, make_app, posting, put_setting, radios,  # noqa: E402
                     suggest_body)
from jobagent.application_profile import write_profile  # noqa: E402
from jobagent.extension import suggest as suggest_module  # noqa: E402
from jobagent.extension.resolver import FieldResolver  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


class RecordingModel(ScriptedModel):
    """The scripted model that also keeps what it was TOLD (the system message of each request)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.systems = []

    async def _handle(self, request):
        body = json.loads(request.content)
        self.systems.append(body["messages"][0]["content"])
        return await super()._handle(request)


async def open_page(tmp_path, page=None, model=None, settings=()):
    app = make_app(tmp_path)
    for key, value in settings:
        put_setting(app.state.db, key, value)
    model = (model or RecordingModel()).install(app)
    client = http(bridge(app))
    await client.__aenter__()
    created = (await client.post("/v1/sessions", json=page or posting())).json()
    return SimpleNamespace(app=app, client=client, model=model, sid=created["session_id"], created=created)


async def close_page(ctx):
    await ctx.client.__aexit__(None, None, None)
    await ctx.app.state.extension_sessions.close()
    await ctx.app.state.extension_generation.close()
    await ctx.app.state.extension_research.close()


@pytest.fixture
async def ctx(tmp_path):
    page = await open_page(tmp_path)
    yield page
    await close_page(page)


async def scan(ctx, elements, **overrides):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body(elements, **overrides))
    assert response.status_code == 200, response.text
    return {s["node_id"]: s for s in response.json()["results"]}


async def idle(ctx):
    for _ in range(6000):
        await asyncio.sleep(0.01)
        if ctx.app.state.extension_generation.active_total() == 0:
            return
    raise AssertionError("the generation queue never became idle")


# -------------------------------------------------------------------------------- CPL-02 / SAFE-16: a typed signature
SIGNATURES = [
    ("Signature (type your full name)", {}), ("Please type your full name to sign this declaration", {}),
    ("Type your name to electronically sign", {}), ("I confirm the information above is true. Full name", {}),
    ("Firma electrónica (escribe tu nombre completo)", {}), ("Print name", {}), ("Sign here (full name)", {}),
    ("Name", {"section_label": "Declaration"}), ("Nombre", {"section_label": "Declaración"}),
    ("Your name", {"description": "Typing my name below counts as my signature", "name": "signature"})]


@pytest.mark.parametrize("label,extra", SIGNATURES)
async def test_a_typed_signature_is_inserted_on_enter_never_on_tab(ctx, label, extra):
    got = (await scan(ctx, [el(1, "textbox", label, **extra)]))[1]
    assert got["state"] == "VERIFIED" and got["insert"] == "explicit", got
    assert got["suggestion"]["text"] == ("Test" if label == "Nombre" else "Test Candidate")  # what the field asks for, as ever
    assert got["flags"]["protected"] is True and got["flags"]["legal"] is True
    assert got["flags"]["reusable"] is False and "Enter" in got["why"]["summary"]
    put_setting(ctx.app.state.db, "extension_tab_inserts_protected", True)
    again = (await scan(ctx, [el(1, "textbox", label, **extra)]))[1]
    assert again["insert"] == "explicit", "the protected-Tab switch must not make a signature a Tab insertion"
    put_setting(ctx.app.state.db, "extension_tab_inserts_protected", False)


async def test_an_ordinary_name_is_still_a_tab_insertion(ctx):
    results = await scan(ctx, [el(1, "textbox", "Full name"), el(3, "textbox", "First name"),
                               el(5, "textbox", "Name", section_label="Personal information")])
    assert all(r["state"] == "VERIFIED" and r["insert"] == "tab" for r in results.values()), results


@pytest.mark.parametrize("label,wanted", [("Legal first name *", "Test"), ("Legal last name *", "Candidate"), ("Legal name", "Test Candidate"),
                                          ("Given name(s)", "Test"), ("Family name", "Candidate"), ("First name", "Test"), ("Last name", "Candidate")])
async def test_W5_a_legal_first_or_last_name_in_a_block_of_the_applicants_own_details_is_a_tab_insertion(ctx, label, wanted):
    """Workday's step 'My Information' (the label is also the question's context, the attribute is legalName--firstName): the applicant's
    own name, not a signature. ('legal' in the LABEL says nothing about a declaration.)"""
    for heading in ("My Information", "Legal name", "Identification", "Datos personales"):
        got = (await scan(ctx, [el(1, "textbox", label, context=label, section_id="section-2", section_label=heading,
                                   name="legalName--firstName")]))[1]
        assert got["state"] == "VERIFIED" and got["insert"] == "tab" and got["suggestion"]["text"] == wanted, (heading, got)
        assert got["flags"]["legal"] is False and got["flags"]["protected"] is False, (heading, got["flags"])


async def test_W5_a_consent_box_far_above_a_name_on_a_page_without_sections_does_not_sign_it(ctx):
    """One block for the whole page (the scanner's 'page'): the name is the applicant's unless the box right before it is what it signs."""
    far = await scan(ctx, [el(1, "checkbox", "I agree to the terms and conditions"), el(3, "textbox", "Email"), el(5, "textbox", "Full name")])
    assert far[5]["state"] == "VERIFIED" and far[5]["insert"] == "tab" and far[5]["flags"]["legal"] is False, far[5]
    near = await scan(ctx, [el(1, "textbox", "Email"), el(3, "checkbox", "I agree to the terms and conditions"), el(5, "textbox", "Full name")])
    assert near[5]["state"] == "VERIFIED" and near[5]["insert"] == "explicit" and near[5]["flags"]["legal"] is True, near[5]
    # in a section of its own the box signs every name below it, whatever stands between them
    section = await scan(ctx, [el(1, "checkbox", "I agree to the terms and conditions", section_id="s7", section_label="Before you submit"),
                               el(3, "textbox", "Email", section_id="s7", section_label="Before you submit"),
                               el(5, "textbox", "Full name", section_id="s7", section_label="Before you submit")])
    assert section[5]["insert"] == "explicit" and section[5]["flags"]["legal"] is True, section[5]


async def test_W5_a_heading_that_is_the_jobs_own_title_is_no_declaration(tmp_path):
    """A job called 'Declaration Analyst' or 'Consent Manager' on a page headed with its own title: 'declaration' and 'consent' are the page's words."""
    for title in ("Declaration Analyst", "Consent Manager", "Authorisation Officer"):
        page = posting()
        job = json.loads(page["page"]["jsonld"][0])
        job["title"] = title
        page["page"]["jsonld"] = [json.dumps(job)]
        page["page"]["title"], page["page"]["h1"] = f"{title} - Acme Robotics", title
        context = await open_page(tmp_path / title.replace(" ", "_"), page=page)
        try:
            got = (await scan(context, [el(1, "textbox", "Full name", section_id="s1", section_label=title),
                                        el(3, "textbox", "Email", section_id="s1", section_label=f"{title} - Acme Robotics")]))
            assert [got[1]["insert"], got[3]["insert"]] == ["tab", "tab"], (title, got)
            assert got[1]["flags"]["legal"] is False, (title, got[1]["flags"])
        finally:
            await close_page(context)


# -------------------------------------------------------------------------------- SAFE-22: the name a control has
async def test_a_visible_label_that_differs_from_the_accessible_name_is_what_is_answered_and_never_as_verified(ctx):
    """'Favourite colour' with aria-label 'Email address' is not an email field to the person who reads it."""
    results = await scan(ctx, [
        el(1, "textbox", "Email address", visible_label="Favourite colour"),
        el(3, "textbox", "Salary expectations", visible_label="Email"),
        el(5, "textbox", "Hobbies Email", visible_label="Hobbies"),
        el(7, "textbox", "Email address", visible_label="Email"),
        el(9, "textbox", "Email", visible_label="")])
    assert results[1]["state"] == "NEEDS_YOU" and results[1]["question"] == "Favourite colour"
    assert results[3]["state"] == "INFERRED" and results[3]["suggestion"]["text"] == "test@example.test"
    assert results[3]["question"] == "Email" and results[3]["insert"] == "tab" and results[3]["flags"]["requires_review"] is True
    assert "differs from the label you see" in results[3]["why"]["summary"]
    assert results[5]["state"] == "NEEDS_YOU"
    for node in (7, 9):  # two names of the same question: unchanged
        assert results[node]["state"] == "VERIFIED" and results[node]["suggestion"]["text"] == "test@example.test", node


# -------------------------------------------------------------------------------- CPL-01 / CPL-04: entries of a history
def block(first, section_id, heading, labels):
    return [el(first + 2 * n, "textbox", label, section_id=section_id, section_label=heading) for n, label in enumerate(labels)]


async def test_two_blocks_under_one_heading_are_two_entries_and_a_numbered_heading_names_its_entry(ctx):
    education = block(1, "section-a", "Education", ["School or University", "Degree"]) + \
        block(11, "section-b", "Education", ["School or University", "Degree"])
    jobs = block(21, "section-c", "Work Experience 1", ["Job Title", "Company", "Start date", "Location"]) + \
        block(31, "section-d", "Work Experience 2", ["Job Title", "Start date"])
    results = await scan(ctx, education + jobs)
    first, second = results[1], results[11]
    assert (first["suggestion"]["text"], second["suggestion"]["text"]) == ("South University", "North University")
    assert (results[3]["suggestion"]["text"], results[13]["suggestion"]["text"]) == ("MSc Artificial Intelligence", "BSc Computer Science")
    assert first["state"] == second["state"] == "INFERRED" and first["question_id"] != second["question_id"]
    assert results[21]["suggestion"]["text"] == "Developer" and results[21]["state"] == "INFERRED"
    assert results[23]["suggestion"]["text"] == "Example Studio" and results[25]["suggestion"]["text"] == "01 July 2025"
    assert results[27]["state"] == "NEEDS_YOU"  # the job holds no location: never where the candidate lives
    assert results[31]["state"] == "NEEDS_YOU" and results[33]["state"] == "NEEDS_YOU"  # there is no second job


async def test_a_heading_that_does_not_say_which_job_leaves_a_job_field_to_the_user(ctx):
    results = await scan(ctx, block(1, "s", "Employment History", ["Start date", "Location", "City", "Country", "Job Title"]))
    assert all(r["state"] == "NEEDS_YOU" and r["insert"] == "never" for r in results.values()), results


# -------------------------------------------------------------------------------- SAFE-24 differential: Spain and the EU
def madrid_posting():
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Software Engineer",
           "description": "<p>" + "Desarrollamos servicios de pago fiables para millones de clientes cada día. " * 8 + "</p>",
           "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics", "url": "https://acmerobotics.example/"},
           "jobLocation": {"address": {"addressLocality": "Madrid", "addressCountry": "ES"}}}
    return posting(lang="es", jsonld=[json.dumps(job)], title="Ingeniero de software - Acme Robotics")


SPANISH_QUESTIONS = [("¿Tienes autorización para trabajar en España?", ("Sí", "No")),
                     ("¿Necesitas patrocinio de visado para trabajar en España?", ("Sí", "No"))]


async def test_a_spanish_role_is_answered_from_the_spanish_facts_the_user_saved_and_never_from_the_uk_ones(tmp_path):
    page = await open_page(tmp_path, madrid_posting())
    try:
        await page.app.state.extension_research.wait(page.created["research"]["run_id"], 120)
        groups = [radios(1 + 10 * n, question, options, group=f"q{n}") for n, (question, options) in enumerate(SPANISH_QUESTIONS)]
        elements = [member for group in groups for member in group]
        salary = el(101, "textbox", "Pretensiones salariales (bruto anual, EUR)")
        before = await scan(page, [*elements, salary])
        assert all(before[g[0]["node_id"]]["state"] == "NEEDS_YOU" for g in groups) and before[101]["state"] == "NEEDS_YOU"
        write_profile(page.app.state.db, "work_authorization_es", "Yes", True)
        write_profile(page.app.state.db, "sponsorship_es", "No", True)
        write_profile(page.app.state.db, "expected_salary_es", "38000", True)
        after = await scan(page, [*elements, salary])
        right, sponsor = after[groups[0][0]["node_id"]], after[groups[1][0]["node_id"]]
        assert (right["state"], right["insert"], right["suggestion"]["option"]["label"]) == ("VERIFIED", "explicit", "Sí")
        assert (sponsor["state"], sponsor["insert"], sponsor["suggestion"]["option"]["label"]) == ("VERIFIED", "explicit", "No")
        assert after[101]["state"] == "VERIFIED" and after[101]["suggestion"]["text"] == "38000"
        assert after[101]["why"]["code"] == "policy" and page.app.state.db.one("SELECT 1 AS x FROM candidate_policies")
    finally:
        await close_page(page)


async def test_the_uk_questions_of_the_same_candidate_are_unchanged_by_the_spanish_facts(ctx):
    write_profile(ctx.app.state.db, "work_authorization_es", "No", True)
    write_profile(ctx.app.state.db, "expected_salary_es", "38000", True)
    results = await scan(ctx, radios(1, "Are you legally authorized to work in the United Kingdom?", ["Yes", "No"], group="uk") +
                         radios(11, "Are you legally authorized to work in Spain?", ["Yes", "No"], group="es") +
                         [el(21, "textbox", "Salary expectations")])
    assert results[1]["suggestion"]["option"]["label"] == "Yes" and results[11]["suggestion"]["option"]["label"] == "No"
    assert results[21]["suggestion"]["text"] == "60000"


# -------------------------------------------------------------------------------- CPL-03: a draft in the question's language
class SpanishModel(RecordingModel):
    def __init__(self, answer="Construí una canalización de visión con Python.", claim="I built a vision pipeline using Python",
                 needle="vision pipeline", **kwargs):
        super().__init__(answers=[(answer, claim, needle)], **kwargs)


async def test_a_draft_on_a_spanish_form_is_asked_for_in_spanish_with_its_limits_and_is_accepted_in_spanish(tmp_path):
    page = await open_page(tmp_path, madrid_posting(), model=SpanishModel())
    try:
        await page.app.state.extension_research.wait(page.created["research"]["run_id"], 120)
        question = el(1, "textbox", "¿Por qué quieres trabajar en Acme Robotics?", tag="textarea", type="textarea",
                      description="Máximo 120 palabras. Leemos todas las respuestas.")
        await scan(page, [question])
        await idle(page)
        draft = (await scan(page, [question]))[1]
        assert draft["state"] == "GENERATED_DRAFT", draft
        assert draft["suggestion"]["text"] == "Construí una canalización de visión con Python."
        sent = page.model.drafts[0]
        assert sent["output_requirements"]["language"] == "Spanish" and sent["output_requirements"]["max_words"] == 120
        system = page.model.systems[0]
        assert "Write the answer in Spanish" in system and "quote the English evidence verbatim" in system
        assert draft["generation"]["quality_passed"] is True
    finally:
        await close_page(page)


async def test_a_spanish_answer_over_its_word_limit_is_refused(tmp_path):
    long_answer = "Construí una canalización de visión con Python. " + "Me gusta resolver problemas difíciles. " * 40
    page = await open_page(tmp_path, madrid_posting(), model=SpanishModel(answer=long_answer))
    try:
        await page.app.state.extension_research.wait(page.created["research"]["run_id"], 120)
        question = el(1, "textbox", "¿Por qué quieres trabajar en Acme Robotics? (máximo 20 palabras)", tag="textarea", type="textarea")
        await scan(page, [question])
        await idle(page)
        refused = (await scan(page, [question]))[1]
        assert refused["state"] == "NEEDS_YOU" and refused["needs"]["code"] == "validation_failed", refused
        assert refused["needs"]["root_cause"] == "ANSWER_VALIDATION_FAILED" and refused["suggestion"] is None
        assert page.model.drafts[0]["output_requirements"]["max_words"] == 20  # the limit was read from the Spanish question
    finally:
        await close_page(page)


async def test_the_language_of_the_page_decides_when_the_question_does_not(tmp_path):
    page = await open_page(tmp_path, posting(lang="es"), model=SpanishModel())
    try:
        await page.app.state.extension_research.wait(page.created["research"]["run_id"], 120)
        question = el(1, "textbox", "Carta de presentación", tag="textarea", type="textarea")
        await scan(page, [question])
        await idle(page)
        assert page.model.drafts and page.model.drafts[0]["output_requirements"]["language"] == "Spanish"
    finally:
        await close_page(page)


async def test_an_english_form_is_still_drafted_in_english(ctx):
    question = el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea")
    await scan(ctx, [question])
    await idle(ctx)
    assert ctx.model.drafts[0]["output_requirements"]["language"] == "English"
    assert "Write the answer in" not in ctx.model.systems[0]


# -------------------------------------------------------------------------------- SAFE-21: what a draft may not invent
@pytest.mark.parametrize("answer", ["I worked at Google for ten years.", "At Globex Corporation I built a platform.",
                                    "I currently work at Example Studio as a Developer.", "I led a team of fifty engineers at a bank."])
async def test_a_draft_that_invents_an_employer_or_a_number_never_reaches_the_field_even_when_the_reviewer_approves(tmp_path, answer):
    page = await open_page(tmp_path, model=ScriptedModel(answer=answer))
    try:
        question = el(1, "textbox", "Tell us about a project you are proud of", tag="textarea", type="textarea")
        await scan(page, [question])
        await idle(page)
        got = (await scan(page, [question]))[1]
        assert got["state"] == "NEEDS_YOU" and got["suggestion"] is None and got["insert"] == "never", got
        assert got["needs"]["code"] == "validation_failed"
    finally:
        await close_page(page)


async def test_a_draft_that_only_the_deterministic_checks_passed_is_never_a_tab_insertion(tmp_path):
    page = await open_page(tmp_path, settings=[("text_quality_review", False)])
    try:
        question = el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea")
        await scan(page, [question])
        await idle(page)
        draft = (await scan(page, [question]))[1]
        assert draft["state"] == "GENERATED_DRAFT" and draft["insert"] == "explicit", draft
    finally:
        await close_page(page)


# -------------------------------------------------------------------------------- SEC-02: a hostile page
async def test_a_page_of_huge_labels_costs_a_bounded_time_and_leaves_the_loop_free(ctx):
    hostile = [el(1 + 2 * n, "textbox", "a" + " " * 19990 + "x") for n in range(30)]
    began = time.perf_counter()
    results = await scan(ctx, hostile)
    assert time.perf_counter() - began < 10.0 and len(results) == 30
    assert all(r["state"] == "NEEDS_YOU" and len(r["question"]) <= 2000 for r in results.values())
    assert all(len(f.question) <= 1500 for f in ctx.app.state.extension_sessions.sessions[ctx.sid].fields.values())


async def test_a_control_whose_resolution_overruns_its_budget_is_left_to_the_user_without_holding_the_loop(ctx, monkeypatch):
    original = FieldResolver.resolve

    async def slow(self, element, **kwargs):
        if len(element.get("label") or "") > 100:
            time.sleep(0.6)  # synchronous CPU work: the loop could not run anything meanwhile
        return await original(self, element, **kwargs)

    monkeypatch.setattr(FieldResolver, "resolve", slow)
    monkeypatch.setattr(suggest_module, "FIELD_BUDGET", 0.1)
    monkeypatch.setattr(suggest_module, "HEAVY_TEXT", 200)
    stalls = []

    async def watch():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            stalls.append(now - last)
            last = now

    watcher = asyncio.create_task(watch())
    results = await scan(ctx, [el(1, "textbox", "Email"), el(3, "textbox", "Q " * 120)])
    watcher.cancel()
    assert results[1]["state"] == "VERIFIED"
    assert results[3]["state"] == "NEEDS_YOU" and "too long to read safely" in results[3]["needs"]["message"]
    assert max(stalls) < 0.4, max(stalls)


async def test_no_draft_for_a_history_entry_no_profile_field_for_it_and_no_reuse(ctx):
    """A job title, a company or a start date of ONE entry is a fact the profile holds or does not: no model may write it, and
    nothing typed into it may become the candidate's own availability or city."""
    elements = block(1, "section-a", "Work Experience 2", ["Job Title", "Start date", "Location"]) + \
        block(11, "section-b", "Education 1", ["Location"])
    results = await scan(ctx, elements)
    for node in (1, 3, 5, 11):
        got = results[node]
        assert got["state"] == "NEEDS_YOU" and got["flags"]["reusable"] is False, got
        assert got["needs"]["root_cause"] == "MISSING_HISTORY_ENTRY"
        assert {a["type"] for a in got["needs"]["actions"]} == {"answer_here"}, got["needs"]["actions"]
    answered = (await scan(ctx, block(21, "section-c", "Work Experience 1", ["Job Title"])))[21]
    assert answered["state"] == "INFERRED" and answered["flags"]["reusable"] is False


# ------------------------------------------------------------------------ RS-10 / RS-11 / RS-06: what the rest of the form says
async def test_RS10_the_applicants_own_name_email_and_phone_are_offered_in_the_first_block_only(ctx):
    """One form: 'Personal information', 'Guarantor details' (a heading no word of the engine knows) and 'Contact 2' each ask 'Name',
    'Email' and 'Phone number': the applicant's own details fill the first block and no other."""
    elements = [
        el(1, "textbox", "Name", section_id="s1", section_label="Personal information"),
        el(3, "textbox", "Email", section_id="s1", section_label="Personal information", type="email"),
        el(5, "textbox", "Phone number", section_id="s1", section_label="Personal information", type="tel"),
        el(7, "textbox", "Confirm email", section_id="s1", section_label="Personal information", type="email"),
        el(9, "textbox", "Name", section_id="s2", section_label="Guarantor details"),
        el(11, "textbox", "Email", section_id="s2", section_label="Guarantor details", type="email"),
        el(13, "textbox", "Phone number", section_id="s3", section_label="Details of the guarantor", type="tel"),
    ]
    results = await scan(ctx, elements)
    assert [results[n]["state"] for n in (1, 3, 5)] == ["VERIFIED"] * 3
    assert [results[n]["suggestion"]["text"] for n in (1, 3, 5)] == ["Test Candidate", "test@example.test", "+441234567890"]
    for node in (9, 11, 13):
        assert results[node]["state"] == "NEEDS_YOU" and results[node]["needs"]["root_cause"] == "MISSING_OTHER_PARTY_DETAILS", results[node]
        assert results[node]["suggestion"] is None and results[node]["insert"] == "never"


async def test_RS10_a_second_block_under_a_general_heading_is_somebody_elses_when_the_first_took_the_applicants_own_details(ctx):
    """No heading tells them apart ('Personal information' and 'Contact details' are both general): the same label that already took the
    applicant's own name, email and phone in an earlier block is the next person's."""
    results = await scan(ctx, [
        el(1, "textbox", "Name", section_id="s1", section_label="Personal information"),
        el(3, "textbox", "Email", section_id="s1", section_label="Personal information", type="email"),
        el(5, "textbox", "Name", section_id="s2", section_label="Contact details"),
        el(7, "textbox", "Email", section_id="s2", section_label="Contact details", type="email")])
    assert [results[n]["state"] for n in (1, 3)] == ["VERIFIED"] * 2
    for node in (5, 7):
        assert results[node]["state"] == "NEEDS_YOU" and results[node]["needs"]["root_cause"] == "MISSING_OTHER_PARTY_DETAILS", results[node]


async def test_RS10_a_hint_that_says_whose_details_are_asked_is_read_as_the_heading_is(ctx):
    results = await scan(ctx, [el(1, "textbox", "Name", description="Your emergency contact's name"),
                               el(3, "textbox", "Email", description="Referee's email address"),
                               el(5, "textbox", "Phone number", placeholder="Emergency contact phone"),
                               el(7, "textbox", "Email", description="Our recruiter will write to this address")])
    assert [results[n]["state"] for n in (1, 3, 5)] == ["NEEDS_YOU"] * 3
    assert results[7]["state"] == "VERIFIED" and results[7]["suggestion"]["text"] == "test@example.test"


async def test_RS06_a_label_of_a_block_the_form_repeats_is_not_the_applicants_start_date_or_city(ctx):
    results = await scan(ctx, [
        el(1, "textbox", "Start date", section_id="a", section_label="Details"), el(3, "textbox", "City", section_id="a", section_label="Details"),
        el(5, "textbox", "Start date", section_id="b", section_label="Details"), el(7, "textbox", "City", section_id="b", section_label="Details"),
        el(9, "textbox", "City", section_id="c", section_label="Job 1"), el(11, "textbox", "Start date", section_id="d", section_label="Awards"),
        el(13, "textbox", "City", section_id="e", section_label="Contact information")])
    for node in (1, 3, 5, 7, 9, 11):
        assert results[node]["state"] == "NEEDS_YOU" and results[node]["needs"]["root_cause"] == "MISSING_HISTORY_ENTRY", (node, results[node])
    assert results[13]["state"] == "VERIFIED" and results[13]["suggestion"]["text"] == "London"


async def test_RS06_a_form_that_does_not_repeat_a_label_answers_it_under_a_general_heading(ctx):
    results = await scan(ctx, [el(1, "textbox", "Start date", section_id="a", section_label="Application"),
                               el(3, "textbox", "City", section_id="a", section_label="Application")])
    assert results[1]["state"] == "VERIFIED" and results[3]["suggestion"]["text"] == "London"


SIGNED_SECTIONS = [
    # a name typed BELOW a consent or declaration box of its own section is the attestation itself (RS-11)
    ("I certify that the information I have provided is true and complete", "Full name"),
    ("I agree to the terms and conditions", "Please type your name"), ("I consent to the processing of my personal data", "Print your name"),
    ("I declare that the above is correct", "Enter your full name"), ("Declaro que la información es veraz", "Nombre completo"),
    ("I have read and accept the privacy notice", "Applicant name")]


@pytest.mark.parametrize("consent,name", SIGNED_SECTIONS)
async def test_RS11_a_name_typed_under_a_consent_box_of_its_section_is_inserted_on_enter_never_on_tab(ctx, consent, name):
    # (the heading says nothing: only the box above the name makes it a signature)
    results = await scan(ctx, [el(1, "checkbox", consent, section_id="s9", section_label="Application questions"),
                               el(3, "textbox", name, section_id="s9", section_label="Application questions")])
    assert results[3]["state"] == "VERIFIED" and results[3]["insert"] == "explicit" and results[3]["flags"]["legal"] is True, results[3]
    put_setting(ctx.app.state.db, "extension_tab_inserts_protected", True)
    again = await scan(ctx, [el(1, "checkbox", consent, section_id="s9", section_label="Application questions"),
                             el(3, "textbox", name, section_id="s9", section_label="Application questions")])
    assert again[3]["insert"] == "explicit", "the protected-Tab switch must not make a signature a Tab insertion"
    put_setting(ctx.app.state.db, "extension_tab_inserts_protected", False)


@pytest.mark.parametrize("name,heading", [("Print your name", "Declaration"), ("Please type your name", "Declaration"), ("Enter your full name", "Declaration"),
                                          ("Full legal name", "Declaration"), ("Applicant name", "Declaration"), ("Full name", "Statement of truth"),
                                          ("Full name", "Authorisation"), ("Full name", "Consent"), ("Full name", "Privacy notice"),
                                          ("Full name", "Review and submit"), ("Full name", "Disclaimer"), ("Full name", "Condiciones")])
async def test_RS11_a_name_under_a_declaration_or_authorisation_heading_is_never_a_tab_insertion(ctx, name, heading):
    got = (await scan(ctx, [el(1, "textbox", name, section_id="h", section_label=heading)]))[1]
    assert got["state"] == "VERIFIED" and got["insert"] == "explicit", (name, heading, got)


async def test_RS11_a_name_above_the_consent_box_or_in_another_section_is_still_a_tab_insertion(ctx):
    results = await scan(ctx, [el(1, "textbox", "Full name", section_id="p", section_label="Personal information"),
                               el(3, "checkbox", "I agree to the terms and conditions", section_id="p", section_label="Personal information"),
                               el(5, "textbox", "Last name", section_id="q", section_label="About you")])
    assert results[1]["insert"] == "tab" and results[5]["insert"] == "tab", results


async def test_SIG5_workdays_legal_first_and_last_name_are_the_applicants_own_name_on_tab(ctx):
    """The e2e failures cross_origin_iframe_handoff and keyboard_only_journey_workday: the scanner gives a Workday control its legend
    ('context') equal to its label, and the word 'legal' in it once made 'Legal first name' a typed signature (explicit, never Tab)."""
    results = await scan(ctx, [
        el(1, "textbox", "Legal first name *", context="Legal first name *", section_id="section-2", section_label="My Information",
           name="legalName--firstName", id="legal_first", required=True),
        el(3, "textbox", "Legal last name *", context="Legal last name *", section_id="section-2", section_label="My Information",
           name="legalName--lastName", id="legal_last", required=True),
        el(5, "textbox", "Phone number *", context="Phone number *", section_id="section-2", section_label="My Information",
           name="phoneNumber", id="phone_number", type="tel", required=True)])
    for node, text in ((1, "Test"), (3, "Candidate")):
        assert results[node]["state"] == "VERIFIED" and results[node]["insert"] == "tab", results[node]
        assert results[node]["suggestion"]["text"] == text and results[node]["flags"]["legal"] is False, results[node]
    assert results[5]["insert"] == "tab"


@pytest.mark.parametrize("heading", ["Submit your application", "Review", "Final details", "Apply for this job", "Solicitar este puesto",
                                     "Legal Name", "My Information", "Revisión", "Datos personales", "Identification"])
async def test_SIG5_the_name_fields_of_a_form_that_is_headed_like_a_step_or_the_whole_form_are_a_tab_insertion(ctx, heading):
    """'Submit your application' heads Lever's and Ashby's whole form: a name typed there is the applicant's name, not a signature."""
    results = await scan(ctx, [el(1, "textbox", "Full name", section_id="h", section_label=heading),
                               el(3, "textbox", "First name", section_id="h", section_label=heading),
                               el(5, "textbox", "Last name", section_id="h", section_label=heading)])
    for node in (1, 3, 5):
        assert results[node]["state"] == "VERIFIED" and results[node]["insert"] == "tab", (heading, node, results[node])
        assert results[node]["flags"]["legal"] is False, (heading, node)


# ------------------------------------------------------------------------ the held-out metamorphic suite: neutral text and bare labels
async def test_MM_an_instruction_a_required_line_and_a_hidden_character_change_nothing_about_what_a_label_asks(ctx):
    """'Please answer: Country', 'Start date' + a 'Required' line and a zero-width space inside 'Expected graduation year' are the plain
    questions: the page keeps the line break of a label (it is a hint of its own), the engine reads what a person reads."""
    plain_labels = ["Country", "Start date", "Expected graduation year"]
    variants = ["Please answer: Country", "Start date\nRequired", "Expected gr​aduation year"]
    plain_results = await scan(ctx, [el(1 + 2 * n, "textbox", label) for n, label in enumerate(plain_labels)])
    variant_results = await scan(ctx, [el(1 + 2 * n, "textbox", label) for n, label in enumerate(variants)])
    for node in (1, 3, 5):
        assert plain_results[node]["state"] in {"VERIFIED", "INFERRED"}, plain_results[node]
        assert variant_results[node]["state"] == plain_results[node]["state"], (variants, variant_results[node])
        assert variant_results[node]["suggestion"] == plain_results[node]["suggestion"], (variants, variant_results[node])
    assert "\n" in variant_results[3]["question"]  # (the question text keeps its line break; the panel shows it as one line)


async def test_MM_a_second_typed_signature_is_refused_and_is_as_protected_as_the_first(ctx):
    results = await scan(ctx, [el(1, "textbox", "Signature (type your full name)", section_id="a", section_label="Declaration"),
                               el(3, "textbox", "Signature (type your full name)", section_id="b", section_label="Witness")])
    assert results[1]["state"] == "VERIFIED" and results[1]["insert"] == "explicit" and results[1]["flags"]["protected"] is True
    assert results[3]["state"] == "NEEDS_YOU" and results[3]["insert"] == "never" and results[3]["flags"]["protected"] is True, results[3]


async def test_MM_a_bare_name_under_a_heading_that_is_not_about_the_applicant_is_never_filled(ctx):
    results = await scan(ctx, [el(1, "textbox", "Name", section_id="a", section_label="Awards"),
                               el(3, "textbox", "Full name", section_id="b", section_label="Studies"),
                               el(5, "textbox", "Email", section_id="c", section_label="Datos de la referencia", type="email")])
    for node in (1, 3, 5):
        assert results[node]["state"] == "NEEDS_YOU" and results[node]["needs"]["root_cause"] == "MISSING_OTHER_PARTY_DETAILS", results[node]
    # the same labels under the headings of the applicant's own details (one page each: a label that stands in two blocks is read as a
    # repeated one) are the applicant's
    for label, heading in (("Name", "Create your account"), ("Nombre completo", "Datos de contacto"), ("Name", "Basic information")):
        got = (await scan(ctx, [el(1, "textbox", label, section_id="x", section_label=heading)]))[1]
        assert got["state"] == "VERIFIED" and got["suggestion"]["text"] == "Test Candidate", (label, heading, got)


# ---------------------------------------------------------------------------------------------------- final stabilisation
ORDINARY_HEADINGS = [None, "Apply for this job", "My Information", "Datos personales", "Personal information", "Contact information"]


@pytest.mark.parametrize("heading", ORDINARY_HEADINGS)
async def test_FS01_bare_name_email_and_phone_are_the_applicants_under_any_ordinary_heading(ctx, heading):
    """A heading the engine does not know is no evidence of anything: 'First Name', 'Email' and 'Phone' are answered under it, with the
    Profile populated or not (and Workday's 'Legal first/last name' is an identity on Tab, not a signature)."""
    for key, value in (("location", "London, United Kingdom"), ("address", "1 Test Street"), ("postcode", "N1 1AA")):
        write_profile(ctx.app.state.db, key, value, True)
    extra = {"section_id": "x", "section_label": heading} if heading else {}
    elements = [el(1, "textbox", "First Name", **extra), el(3, "textbox", "Email", type="email", **extra),
                el(5, "textbox", "Phone", type="tel", **extra), el(7, "textbox", "Legal first name", **extra),
                el(9, "textbox", "Legal last name", **extra)]
    results = await scan(ctx, elements)
    assert [results[n]["state"] for n in (1, 3, 5, 7, 9)] == ["VERIFIED"] * 5, results
    assert [results[n]["suggestion"]["text"] for n in (1, 3, 5, 7, 9)] == [
        "Test", "test@example.test", "+441234567890", "Test", "Candidate"]
    assert all(results[n]["insert"] != "explicit" for n in (1, 3, 5, 7, 9)), "an identity is a Tab insertion, never a signature"


@pytest.mark.parametrize("heading", ["Emergency contact", "Referee", "Referee 1", "Employment", "Job 1", "Trabajo 1", "Awards"])
async def test_FS02_a_profile_fact_never_answers_a_label_of_an_entry_or_another_party(ctx, heading):
    """'Location' / 'City' / 'Address' / 'Postcode' under a heading that names an entry of a history or another party are not the
    applicant's own, even when the Profile page holds those very fields (the confirmed-answer fallback must not revive them)."""
    for key, value in (("location", "London, United Kingdom"), ("address", "1 Test Street"), ("postcode", "N1 1AA")):
        write_profile(ctx.app.state.db, key, value, True)
    labels = ("Location", "City", "Address", "Postcode", "Zip code")
    elements = [el(1 + 2 * n, "textbox", label, section_id="x", section_label=heading) for n, label in enumerate(labels)]
    results = await scan(ctx, elements)
    for n, label in enumerate(labels):
        got = results[1 + 2 * n]
        assert got["state"] == "NEEDS_YOU" and not got.get("suggestion"), (heading, label, got)
    # the same labels under an ordinary heading are the applicant's own
    plain = await scan(ctx, [el(1, "textbox", "Postcode", section_id="y", section_label="Contact information"),
                             el(3, "textbox", "Address", section_id="y", section_label="Contact information")])
    assert plain[1]["suggestion"]["text"] == "N1 1AA" and plain[3]["suggestion"]["text"] == "1 Test Street"
