"""FieldResolver fidelity: the browser assistant and Auto Apply must never disagree about what the profile says.

The PARITY tests open the shared ATS fixtures in a real browser, let ``BrowserManager.fill_application`` (the runner's
``resolve_raw`` + ``resolve``) decide every answer, then ask the extension's resolver about the very same observed
controls and require the same answers. The rest pins the resolver's own contracts."""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, el, radios, seed_candidate, select  # noqa: E402
from jobagent.application_profile import application_knowledge, remember_answer  # noqa: E402
from jobagent.automation import BrowserManager  # noqa: E402
from jobagent.db import Database, now, uid  # noqa: E402
from jobagent.extension.resolver import (FieldResolver, SnapshotEngine, data_token, load_environment,  # noqa: E402
                                         prepare_elements)
from jobagent.extension.schemas import ElementIn  # noqa: E402
from jobagent.extension.suggest import group_fields  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402
from jobagent.intelligence.store import policy  # noqa: E402

FIXTURES = Path(__file__).parent / "browser" / "fixtures"
JOB = {"country": "United Kingdom", "company": "Acme", "location": "London, UK"}
APPLICATION = {"id": "parity", "company": "Acme", "location": "London, UK"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture(scope="module")
def fixture_server():
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(FIXTURES)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def db(tmp_path):
    return seed_candidate(Database(tmp_path / "meridian.sqlite3"))


def flat(db, key, value, category="identity"):
    db.execute("INSERT INTO facts(id,candidate_id,category,key,value,source,verification_status,locked,notes,created_at,"
               "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (uid(), db.one("SELECT id FROM candidates LIMIT 1")["id"], category, key, value, "manual", "verified", 0, "",
                now(), now()))


def prune(raw: dict) -> dict:
    """What the content script would send: the control without what the user typed into text fields."""
    pruned = {k: v for k, v in raw.items() if k in ElementIn.model_fields}
    if pruned.get("role") == "textbox":
        pruned["has_value"], pruned["value"] = bool(pruned.get("value")), ""
    return pruned


def env_for(db, job=JOB, application=APPLICATION, settings=None):
    return load_environment(db, settings or {"sensitive_policies": {}}, job=dict(job), application=dict(application))


# --------------------------------------------------------------------------------------------- parity
def trainline_facts(db, application_id):
    for key, value in (("notice_period", "Immediately available"), ("expected_salary", "45000"),
                       ("sponsorship_required", "No"), ("location", "London, UK"), ("relocation", "Yes"),
                       ("privacy", "Yes"), ("Are you able to commit to our hybrid working policy?", "Yes")):
        flat(db, key, value)


def basics(db, application_id):
    for key, value in (("relocation", "Yes"), ("privacy", "Yes")):
        flat(db, key, value)


def corrected(db, application_id):
    basics(db, application_id)
    application = {**APPLICATION, "id": application_id}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES(?,'Engineer','Acme','London','https://x.example/1','https://x.example/1','x1',?,?)",
               ("job-" + application_id, now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,'manual','PREPARING',?,?)",
               (application_id, "job-" + application_id, now(), now()))
    remember_answer(db, application, "What is your current notice period?", "2 weeks", reusable=False, direct_edit=True)
    remember_answer(db, application, "Name", "Chosen Name", reusable=False, direct_edit=True)
    remember_answer(db, application, "Describe your gender", "Prefer to say nothing", reusable=False, direct_edit=True)


def saved(db, application_id):
    """A confirmed (not direct-edit) answer to a question typed knowledge cannot answer: only confirmed_fallback finds it."""
    basics(db, application_id)
    application = {**APPLICATION, "id": application_id}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES(?,'Engineer','Acme','London','https://x.example/2','https://x.example/2','x2',?,?)",
               ("job-" + application_id, now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,'manual','PREPARING',?,?)",
               (application_id, "job-" + application_id, now(), now()))
    remember_answer(db, application, "Willing to relocate", "No", reusable=False, direct_edit=False)


def leave_blank(db, application_id):
    basics(db, application_id)
    policy(db, "personal_disclosures", "leave_blank")


SCENARIOS = [
    ("greenhouse.html", "greenhouse", basics), ("lever.html", "lever", basics),
    ("ashby_trainline.html", "ashby", trainline_facts), ("ashby_overview.html", "ashby", trainline_facts),
    ("generic.html", None, basics), ("candidate_intelligence.html", None, basics),
    ("profile_reasoning.html", None, basics), ("inline_questions.html", None, corrected),
    ("profile_reasoning.html", None, corrected), ("greenhouse.html", "greenhouse", saved),
    ("lever.html", "lever", saved), ("sections.html", None, basics), ("workday.html", "workday", basics),
    ("disclosure_policy.html", None, leave_blank),
    ("confirmed_disclosures.html", None, basics), ("search_location.html", None, basics),
    ("engine_subjects.html", None, basics),
]


async def compare_with_runner(browser, db, fixture_server, name, ats, application_id) -> tuple[int, list]:
    url = f"{fixture_server}/{name}"
    application = {**APPLICATION, "id": application_id, "url": url, "application_url": url, "ats": ats}
    rows = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    facts, skipped = application_knowledge(db, application, rows)
    result = await browser.fill_application(application, facts, [], settings={
        "_candidate_engine": CandidateEngine(db), "_job_context": dict(JOB), "assisted_autofill": True,
        "skip_optional_unknown": True, "_skipped_questions": skipped})
    # The runner's last decision for every question was taken on the filled form: compare on that same state.
    observed = [dict(e) for e in (await browser.observe())["elements"]]
    env = env_for(db, application={**APPLICATION, "id": application_id})
    runner = {a["question"]: a for a in result["answers"]}
    omitted = {o["question"] for o in result["omitted"]}
    by_document: dict = {}
    for element in observed:
        by_document.setdefault(element["document_id"], []).append(element)
    compared, problems = 0, []
    for document_id, elements in by_document.items():
        prepared = prepare_elements([ElementIn.model_validate(prune(e)) for e in elements], document_id,
                                    elements[0]["frame_url"])
        resolver = FieldResolver(env, url=url, ats=ats, scope="session", frame_key="top", document_id=document_id,
                                 elements=prepared)
        for representative, _ in group_fields(prepared):
            element = prepared[representative]
            if element["role"] == "upload" or element["type"] == "file":
                continue
            question = resolver.question(element)
            condition = resolver.context(element)
            if condition and condition.get("applies") is not True:
                continue  # the assistant never answers a question its parent excludes (NOT_APPLICABLE)
            mine = (await resolver.resolve(element, condition=condition)).result
            expected = runner.get(question)
            if expected is not None:
                compared += 1
                if (mine["answer"], mine.get("form_value"), mine["leave_blank"]) != (
                        expected.get("answer"), expected.get("form_value"), bool(expected.get("leave_blank"))):
                    problems.append((question, expected.get("answer"), mine["answer"]))
                if expected.get("verified") is not None and bool(expected["verified"]) != mine["verified"]:
                    problems.append((question, "verified", expected["verified"], mine["verified"]))
            elif mine["answer"] is not None and not mine["leave_blank"] and question not in omitted:
                # The runner may decline to fill a dynamic dropdown whose real options only exist once it is open;
                # the assistant suggests the verified value and the page verifies it. Nothing else may differ.
                if not (element["role"] == "combobox" and element["tag"] != "select"):
                    problems.append((question, "answered only by the resolver", mine["answer"]))
            elif question in omitted:
                compared += 1  # left blank by policy / optional personal question: nothing may be offered for insertion
                if mine["answer"] is not None and not mine["leave_blank"] and element["role"] != "checkbox":
                    problems.append((question, "omitted by the runner", mine["answer"]))
    return compared, problems


async def test_resolver_matches_the_runners_answers_on_shared_ats_fixtures(tmp_path, fixture_server):
    """One real browser, every shared ATS fixture, a fresh synthetic candidate for each scenario."""
    browser = BrowserManager(tmp_path / "browser")
    await browser.start(headless=True)
    report, answered = {}, 0
    try:
        for index, (name, ats, setup) in enumerate(SCENARIOS):
            db = seed_candidate(Database(tmp_path / f"scenario-{index}.sqlite3"))
            application_id = f"parity-{index}"  # a new application id makes the runner open the new page
            setup(db, application_id)
            compared, problems = await compare_with_runner(browser, db, fixture_server, name, ats, application_id)
            report[f"{name} / {setup.__name__}"] = (compared, problems)
            answered += compared
    finally:
        await browser.close()
    assert {k: v[1] for k, v in report.items() if v[1]} == {}, report
    assert all(v[0] >= 1 for v in report.values()), {k: v[0] for k, v in report.items()}
    assert answered >= 40


async def test_auto_apply_fills_the_applicants_own_details_and_never_somebody_elses_or_another_unit(tmp_path, fixture_server):
    """The answer engine decides these for Auto Apply as much as for the assistant: the runner is asked, on a real page.
    The applicant's name, number and salary go in; the emergency contact's, the employer's, the manager's, the middle name
    and a salary in another period, currency or shape stay empty; 'Country Phone Code' is a COUNTRY, not the number."""
    browser = BrowserManager(tmp_path / "browser")
    await browser.start(headless=True)
    try:
        db = seed_candidate(Database(tmp_path / "subjects.sqlite3"))
        basics(db, "subjects")
        url = f"{fixture_server}/engine_subjects.html"
        application = {**APPLICATION, "id": "subjects", "url": url, "application_url": url, "ats": None}
        facts, skipped = application_knowledge(db, application, db.query(
            "SELECT * FROM facts WHERE verification_status='verified' OR locked=1"))
        await browser.fill_application(application, facts, [], settings={
            "_candidate_engine": CandidateEngine(db), "_job_context": dict(JOB), "assisted_autofill": True,
            "skip_optional_unknown": True, "_skipped_questions": skipped})
        observed = (await browser.observe())["elements"]
    finally:
        await browser.close()
    shown = {e["name"]: (e.get("selected_text") or {o["value"]: o["label"] for o in e.get("options", [])}.get(e.get("value"))
                         or e.get("value") or "") for e in observed if e["role"] in {"textbox", "combobox"} and e.get("name")}
    assert shown["first_name"] == "Test" and shown["email"] == "test@example.test" and shown["phone"] == "+441234567890"
    assert shown["phone_code"] == "United Kingdom (+44)"  # the country of the number, not the number
    assert shown["salary"] == "60000"
    for name in ("middle_name", "ec_name", "ec_phone", "company_name", "manager_email", "salary_monthly", "salary_eur", "salary_range",
                 "phone_prefix"):  # a typed 'Phone prefix' cannot be opened and asked again: it is the user's, never the number
        assert shown[name] == "", (name, shown[name])


# --------------------------------------------------------------------------------------------- unit contracts
def make_resolver(db, elements, *, url="https://boards.greenhouse.io/acme/jobs/1", ats="greenhouse", env=None):
    models = [ElementIn.model_validate(e) for e in elements]
    prepared = prepare_elements(models, "doc", url)
    return FieldResolver(env or env_for(db), url=url, ats=ats, scope="s", frame_key="top", document_id="doc",
                         elements=prepared), prepared


async def test_a_manual_correction_outranks_the_engine_and_a_clear_stays_blank(tmp_path, db):
    application = {**APPLICATION, "id": "parity"}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES('j1','Engineer','Acme','London','https://x.example/1','https://x.example/1','x1',?,?)", (now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('parity','j1','manual','PREPARING',?,?)",
               (now(), now()))
    remember_answer(db, application, "First name", "Corrected", reusable=False, direct_edit=True)
    remember_answer(db, application, "Phone", "", reusable=False, skip=True, direct_edit=True)
    resolver, prepared = make_resolver(db, [el(1, "textbox", "First name"), el(3, "textbox", "Phone", type="tel")],
                                       env=env_for(db))
    first = (await resolver.resolve(prepared[0])).result
    assert first["answer"] == "Corrected" and first["verified"] and first.get("source") == "user_edit"
    phone = (await resolver.resolve(prepared[1])).result
    assert phone["answer"] is None and phone["leave_blank"] is True


async def test_unknown_questions_fall_back_to_the_legacy_profile_but_keep_a_drafting_deferral(db):
    flat(db, "Favourite colour", "Green")
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Favourite colour"), el(3, "textbox", "Something unusual"),
                                            el(5, "textbox", "Why do you want to work here?", tag="textarea",
                                               type="textarea")])
    known = (await resolver.resolve(prepared[0])).result
    assert known["answer"] == "Green" and known["verified"] and known["confidence_level"] == "EXACT"
    unknown = (await resolver.resolve(prepared[1])).result
    assert unknown["answer"] is None and unknown["canonical_intent"] == "OTHER"
    narrative = (await resolver.resolve(prepared[2])).result
    assert narrative["answer"] is None and narrative["root_cause"] == "ASYNC_GENERATION_REQUIRED"
    assert narrative["canonical_intent"] == "OTHER" and narrative["state"] == "generation_required"
    motivated, prepared = make_resolver(db, [el(1, "textbox", "Why do you want to work at Acme?", tag="textarea", type="textarea")])
    assert (await motivated.resolve(prepared[0])).result["canonical_intent"] == "MOTIVATION"


async def test_the_deterministic_phase_never_calls_a_model(db):
    called = []

    class Boom:
        async def answer(self, *args, **kwargs):
            called.append(args)
            raise AssertionError("a model call happened")

    env = env_for(db)
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Why do you want to work here?", tag="textarea", type="textarea"),
                                            el(3, "textbox", "Favourite colour")], env=env)
    engine = env.engine_for(Boom(), {"text_provider": "ollama"})
    for element in prepared:
        await resolver.resolve(element)  # default engine: deterministic, no model attached
    result = await engine.answer("Why do you want to work here?", {"type": "textarea"}, dict(JOB), audit=False, generate=False)
    assert result["root_cause"] == "ASYNC_GENERATION_REQUIRED" and result["answer"] is None and not called


@pytest.mark.parametrize("kind", ["password", "hidden"])
async def test_credential_like_controls_never_reach_a_model_even_when_generation_is_on(db, kind):
    model = ScriptedModel()
    llm = __import__("jobagent.application_answer_llm", fromlist=["ApplicationAnswerLLM"]).ApplicationAnswerLLM(
        db, type("Keys", (), {"get": lambda self, name: "k"})(), transport=model.transport)
    settings = {"text_provider": "ollama", "text_model": "m", "monthly_budget": 0, "text_retry_attempts": 0}
    env = env_for(db)
    engine = env.engine_for(llm, settings)
    for label in ("Password", "Why do you want to work at Acme?"):
        result = await engine.answer(label, {"type": kind, "role": "textbox", "tag": "input"}, dict(JOB), audit=False)
        assert result["answer"] is None
    assert model.calls == []
    with pytest.raises(Exception) as caught:  # defence in depth in the model layer itself
        await llm.generate(settings, "Why do you want to work at Acme?", dict(JOB), field={"type": kind})
    assert "never sent to a model" in str(caught.value)
    assert model.calls == []


async def test_group_options_come_from_the_observed_peers_or_the_client(db):
    resolver, prepared = make_resolver(db, [*radios(1, "Will you now or in the future require visa sponsorship?",
                                                    ["Yes", "No"], group="sp")])
    field, from_client = resolver.choice_field(prepared[0])
    assert [o["label"] for o in field["options"]] == ["Yes", "No"] and not from_client
    pregrouped = el(1, "radio", "Yes", context="Will you now or in the future require visa sponsorship?", group="sp",
                    options=[{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}])
    resolver, prepared = make_resolver(db, [pregrouped])
    field, from_client = resolver.choice_field(prepared[0])
    assert from_client and [o["value"] for o in field["options"]] == ["y", "n"]
    answer = (await resolver.resolve(prepared[0])).result
    assert answer["answer"] == "No"


@pytest.mark.parametrize("label,options,question", [
    ("Are you legally authorized to work in the UK?\u2731 YesNo", ["Yes", "No"], "Are you legally authorized to work in the UK?\u2731"),
    ("Do you require visa sponsorship?\nYes\nNo", ["Yes", "No"], "Do you require visa sponsorship?"),
    ("Will you need sponsorship? Yes No", ["Yes", "No"], "Will you need sponsorship?"),
    ("Gender MaleFemaleNon-binary", ["Male", "Female", "Non-binary"], "Gender"),
    # an ending that is not exactly the options in their order is the question's own text
    ("Do you speak Yes or No", ["Yes", "No"], "Do you speak Yes or No"), ("Is it Yes No maybe", ["Yes", "No"], "Is it Yes No maybe"),
    ("Yes No", ["Yes", "No"], "Yes No")])
def test_W5_the_options_a_scanner_joined_to_a_groups_question_are_not_part_of_the_question(label, options, question):
    """Lever-like pages put the question and its answer buttons in one container: the scanner reads 'Question?\u2731 YesNo'. The options say
    what they are; the question is what stands before them (and only an ending that is EXACTLY the options is taken off)."""
    prepared = prepare_elements([ElementIn(**el(1, "radio", label, context=label, group="g", options=[{"label": o, "value": o} for o in options]))],
                                "doc", "https://x.example/")
    assert prepared[0]["label"] == question and prepared[0]["context"] == question


async def test_W5_a_group_whose_label_carries_its_option_texts_is_answered(db):
    resolver, prepared = make_resolver(db, [el(1, "radio", "Are you legally authorized to work in the United Kingdom?\u2731 YesNo",
                                               context="Are you legally authorized to work in the United Kingdom?\u2731 YesNo", group="g",
                                               options=[{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}])])
    result = (await resolver.resolve(prepared[0])).result
    assert result["answer"] == "Yes" and result["root_cause"] is None, result


async def test_a_scan_without_operations_still_resolves_choice_groups(db):
    bare = radios(1, "Will you now or in the future require visa sponsorship?", ["Yes", "No"], group="sp")
    for element in bare:
        element["operations"] = []
    resolver, prepared = make_resolver(db, bare)
    assert all(e["operations"] == ["CHECK"] for e in prepared)
    assert (await resolver.resolve(prepared[0])).result["answer"] == "No"


async def test_phone_country_code_selects_use_the_verified_number_and_the_observed_options(db):
    resolver, prepared = make_resolver(db, [select(1, "Phone country code", [("Spain (+34)", "es"), ("United Kingdom (+44)", "gb")])])
    result = (await resolver.resolve(prepared[0])).result
    assert result["answer"] == "United Kingdom (+44)" and result["verified"] is True


SALARY_AS_SAVED = ["Salary expectations", "What are your salary expectations?", "Expected annual salary", "Salary expectations (GBP)",
                   "Desired salary (£)", "Expected salary per annum", "Salary expectation (per year)", "Salary (annual, in pounds)"]
SALARY_ASKED_DIFFERENTLY = [
    ("Expected monthly salary", "period"), ("Salary (per month)", "period"), ("Salary expectations / week", "period"),
    ("Desired salary in EUR", "currency"), ("Expected salary (annual, USD)", "currency"), ("Salary expectation in $", "currency"),
    ("Salary expectations (CAD)", "currency"), ("Salary expectations (in thousands)", "unit"),
    ("Expected salary (e.g. 60k)", "unit"), ("Salary band expected (e.g. 50-60k)", "unit"),
    ("Expected salary range", "range"), ("Expected total compensation (base + bonus + equity)", "package"),
    ("Salary expectations including bonus", "package")]


@pytest.mark.parametrize("wording", SALARY_AS_SAVED)
async def test_the_saved_salary_is_offered_when_the_field_asks_for_exactly_that(db, wording):
    resolver, prepared = make_resolver(db, [el(1, "textbox", wording)])
    result = (await resolver.resolve(prepared[0])).result
    assert str(result["answer"]) == "60000" and result["confidence_level"] == "POLICY" and result["verified"] is True


@pytest.mark.parametrize("wording,problem", SALARY_ASKED_DIFFERENTLY)
async def test_a_saved_salary_is_never_offered_for_another_currency_period_unit_range_or_package(db, wording, problem):
    """The policy is ONE annual figure in pounds. 60000 in a field that wants a monthly figure is twelve times too much,
    in a field that wants euros or dollars it is the wrong number: the figure is shown for the user to check, never
    offered for insertion."""
    resolver, prepared = make_resolver(db, [el(1, "textbox", wording)])
    result = (await resolver.resolve(prepared[0])).result
    assert result["answer"] is None and result["root_cause"] == "ANSWER_FORMAT_ERROR" and result["verified"] is False
    assert str(result["candidate_value"]) == "60000" and problem in result["reason"], result
    assert result["confidence_level"] == "UNKNOWN" and result["requires_review"] is True


async def test_a_salary_the_user_typed_for_that_very_question_is_theirs(db):
    application = {**APPLICATION, "id": "parity"}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES('j1','Engineer','Acme','London','https://x.example/1','https://x.example/1','x1',?,?)", (now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('parity','j1','manual','PREPARING',?,?)",
               (now(), now()))
    remember_answer(db, application, "Desired salary in EUR", "70000", reusable=False, direct_edit=True)
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Desired salary in EUR")], env=env_for(db))
    result = (await resolver.resolve(prepared[0])).result
    assert str(result["answer"]) == "70000" and result.get("source") == "user_edit"


async def test_a_salary_policy_without_a_currency_is_not_offered_where_a_currency_is_named(db):
    policy(db, "salary_expected", {"target": 55000}, {"country": "United Kingdom"})
    named, prepared = make_resolver(db, [el(1, "textbox", "Salary expectations (GBP)"), el(3, "textbox", "Salary expectations")])
    pounds = (await named.resolve(prepared[0])).result
    plain = (await named.resolve(prepared[1])).result
    assert pounds["answer"] is None and str(pounds["candidate_value"]) == "55000"  # which currency is it? nobody said
    assert str(plain["answer"]) == "55000"  # nothing in the question contradicts it


OTHER_PARTIES = [
    ("text", "Emergency contact name"), ("tel", "Emergency contact phone number"), ("email", "Reference email"),
    ("text", "Referee 1 - Full name"), ("text", "Current manager's name"), ("email", "Supervisor email"),
    ("text", "Spouse name"), ("tel", "Next of kin phone"), ("text", "Parent or guardian name"), ("text", "Recruiter name"),
    ("text", "Hiring manager name"), ("text", "Mother's maiden name"), ("text", "Middle name"), ("text", "Maiden name"),
    ("text", "Previous name"), ("text", "Company name"), ("email", "Email address of your previous employer"),
    ("url", "Reference's LinkedIn"), ("text", "Employer location"), ("text", "Spouse nationality"), ("text", "Company city"),
    ("text", "Emergency contact address")]
THE_CANDIDATE = [
    ("text", "First name", "Test"), ("text", "Full name", "Test Candidate"), ("text", "Contact name", "Test Candidate"),
    ("email", "Email", "test@example.test"), ("email", "Personal email", "test@example.test"),
    ("tel", "Phone", "+441234567890"), ("url", "LinkedIn Profile", "https://example.test/linkedin"),
    ("text", "Your signature (type your full name)", "Test Candidate"), ("text", "Nationality", "Spanish"),
    ("text", "City", "London")]


@pytest.mark.parametrize("kind,label", OTHER_PARTIES)
async def test_questions_about_someone_else_never_get_the_candidates_own_details(db, kind, label):
    """'Emergency contact phone number' used to get the candidate's own number, 'Company name' their own name, and
    'Mother's maiden name' too: the engine answers every name, number and address question with the candidate's."""
    resolver, prepared = make_resolver(db, [el(1, "textbox", label, **{"type": kind})])
    result = (await resolver.resolve(prepared[0])).result
    assert result["answer"] is None and result["verified"] is False and result["root_cause"], result
    assert result["fact_ids"] == [] and result.get("candidate_value") is None


@pytest.mark.parametrize("kind,label,answer", THE_CANDIDATE)
async def test_questions_about_the_candidate_are_still_answered(db, kind, label, answer):
    resolver, prepared = make_resolver(db, [el(1, "textbox", label, **{"type": kind})])
    assert (await resolver.resolve(prepared[0])).result["answer"] == answer


async def test_a_form_says_whose_details_it_wants_in_its_heading_group_and_field_names(db):
    """The label is just 'Name' or 'Email': the heading above it, its group and the field's own name say whose."""
    elements = [el(1, "textbox", "Name", section_label="Emergency contact"), el(3, "textbox", "Email", type="email",
                                                                              name="emergency_contact_email"),
                el(5, "textbox", "Phone", type="tel", context="Referee details"),
                el(7, "textbox", "Name", section_label="Personal information"),
                el(9, "textbox", "Email", type="email", name="job_application[email]")]
    resolver, prepared = make_resolver(db, elements)
    answers = [(await resolver.resolve(e)).result["answer"] for e in prepared]
    assert answers == [None, None, None, "Test Candidate", "test@example.test"]


async def test_a_name_the_user_typed_for_that_very_question_is_theirs(db):
    application = {**APPLICATION, "id": "parity"}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES('j1','Engineer','Acme','London','https://x.example/1','https://x.example/1','x1',?,?)", (now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('parity','j1','manual','PREPARING',?,?)",
               (now(), now()))
    remember_answer(db, application, "Emergency contact name", "Alex Example", reusable=False, direct_edit=True)
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Emergency contact name")], env=env_for(db))
    result = (await resolver.resolve(prepared[0])).result
    assert result["answer"] == "Alex Example" and result.get("source") == "user_edit"


DIAL_CODE_WORDINGS = ["Country Phone Code", "Phone Country Code", "Dialling code", "Calling code", "Phone prefix",
                      "Mobile country code", "Country dialing code"]


@pytest.mark.parametrize("wording", DIAL_CODE_WORDINGS)
async def test_every_wording_of_the_dial_code_question_picks_the_option_never_the_whole_number(db, wording):
    """Workday asks 'Country Phone Code': the engine answered it with the whole verified number (+441234567890), which was
    offered as a VERIFIED answer for a control that takes a country's code."""
    options = [("Spain (+34)", "es"), ("United Kingdom (+44)", "gb")]
    resolver, prepared = make_resolver(db, [select(1, wording, options)])
    assert (await resolver.resolve(prepared[0])).result["answer"] == "United Kingdom (+44)"
    if wording != "Phone Country Code":  # that one is the runner's own wording: its behaviour stays exactly the runner's
        closed, prepared = make_resolver(db, [el(1, "combobox", wording)])  # a dropdown whose options are not shown yet
        result = (await closed.resolve(prepared[0])).result
        assert result["answer"] is None and result["root_cause"] == "BAD_ENUM_MAPPING"
    plain, prepared = make_resolver(db, [el(1, "textbox", "Phone number", type="tel")])
    assert (await plain.resolve(prepared[0])).result["answer"] == "+441234567890"  # the number itself is unchanged


async def test_education_questions_follow_the_institution_chosen_earlier_in_the_form(db):
    elements = [select(1, "University", [("North University", "n"), ("South University", "s")], selected_text="North University",
                       value="n"),
                select(3, "Degree average", [("70", "70")], required=True)]
    elements[0]["section_label"] = elements[1]["section_label"] = "Education"
    resolver, prepared = make_resolver(db, elements)
    resolution = await resolver.resolve(prepared[1])
    assert resolution.field.get("_education_reference") == "North University"


async def test_question_ids_are_stable_scoped_and_unique(db):
    resolver, prepared = make_resolver(db, [el(1, "textbox", "First name"), el(3, "textbox", "Last name"),
                                            el(5, "textbox", "First name", section_label="Other section")])
    ids = [resolver.question_id(e) for e in prepared]
    assert len(set(ids)) == 3 and ids[0] == resolver.question_id(prepared[0])
    other = FieldResolver(env_for(db), url="https://x.example/", ats=None, scope="another", frame_key="top",
                          document_id="doc", elements=prepared)
    assert other.question_id(prepared[0]) != ids[0]
    reloaded = FieldResolver(env_for(db), url="https://x.example/", ats=None, scope="s", frame_key="top",
                             document_id="a-new-document", elements=prepared)
    assert reloaded.question_id(prepared[0]) == ids[0]  # the observation document does not move the id


async def test_adapters_derive_the_question_not_the_client(db):
    resolver, prepared = make_resolver(db, [el(1, "textbox", "x", name="job_application[first_name]")])
    assert resolver.question(prepared[0]) == "First name"
    generic, prepared = make_resolver(db, [el(1, "textbox", "x", name="job_application[first_name]")],
                                      url="https://example.org/apply", ats=None)
    assert generic.question(prepared[0]) == "x"


def test_the_data_token_moves_when_anything_an_answer_depends_on_changes(db):
    before = data_token(db)
    assert data_token(db) == before
    flat(db, "relocation", "Yes")
    after_fact = data_token(db)
    assert after_fact != before
    policy(db, "personal_disclosures", "leave_blank")
    after_policy = data_token(db)
    assert after_policy != after_fact
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('sensitive_policies','{\"x\":1}')")
    assert data_token(db) != after_policy


def test_one_snapshot_serves_many_questions_without_reloading_knowledge(db):
    env = env_for(db)
    assert isinstance(env.engine, SnapshotEngine)
    knowledge = env.engine.knowledge
    env.engine.reload()
    assert env.engine.knowledge is knowledge
    assert env.engine_for(None, {}).knowledge is knowledge and env.version == env_for(db).version
    flat(db, "relocation", "Yes")
    assert env_for(db).version != env.version


async def test_scoped_application_policies_apply_to_the_job_of_the_session(db):
    policy(db, "salary_expected", {"target": 70000, "currency": "EUR"}, {"country": "Spain"})
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Salary expectations")])
    assert (await resolver.resolve(prepared[0])).result["answer"] == "60000"
    spain = env_for(db, job={**JOB, "country": "Spain", "location": "Madrid, Spain"})
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Salary expectations")], env=spain)
    assert (await resolver.resolve(prepared[0])).result["answer"] == "70000"


async def test_mock_transport_is_not_needed_for_deterministic_resolution(db):
    """The deterministic phase must work with every network path dead (httpx would raise on any use)."""
    def refuse(request):
        raise httpx.ConnectError("no network in this test")

    resolver, prepared = make_resolver(db, [el(1, "textbox", "First name"), el(3, "textbox", "Email", type="email")])
    for element in prepared:
        assert (await resolver.resolve(element)).result["answer"] is not None
    del refuse


async def test_second_stage_maps_the_verified_answer_to_the_real_option_label(db):
    policy(db, "consent_terms_acknowledgement", True)
    resolver, prepared = make_resolver(db, [
        select(1, "Gender", ["Male", "Female", "Decline to self-identify"]),
        *radios(3, "I consent to the terms and conditions", ["Yes, I consent", "No, I do not consent"], group="terms"),
        select(7, "Will you now or in the future require visa sponsorship?", ["Select", "No", "Yes"])])
    assert (await resolver.resolve(prepared[0])).result["answer"] == "Decline to self-identify"
    consent = (await resolver.resolve(prepared[1])).result
    assert consent["answer"] == "Yes, I consent" and consent["verified"] and consent["confidence_level"] == "POLICY"
    sponsorship = (await resolver.resolve(prepared[3])).result
    assert sponsorship["answer"] == "No" and sponsorship["confirmed_answer"] == "No"
    assert sponsorship["option_selection"][0]["engine"] in {"exact", "verified_equivalence"}


async def test_a_confirmed_answer_fills_a_question_typed_knowledge_cannot_answer(db):
    application = {**APPLICATION, "id": "app-1"}
    db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
               "VALUES('j9','Engineer','Acme','London','https://x.example/9','https://x.example/9','x9',?,?)", (now(), now()))
    db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES('app-1','j9','manual','PREPARING',?,?)",
               (now(), now()))
    remember_answer(db, application, "Are you willing to relocate?", "Yes", reusable=False, direct_edit=False)
    env = env_for(db, application=application)
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Are you willing to relocate?"),
                                            select(3, "Are you willing to relocate?", ["Choose", "No", "Yes"])], env=env)
    for element in prepared:  # free text (first stage) and a real choice (second stage)
        result = (await resolver.resolve(element)).result
        assert result["answer"] == "Yes" and result["verified"] and result["canonical_intent"] == "RELOCATION"
    stranger = env_for(db, application={**APPLICATION, "id": "someone-else"})
    resolver, prepared = make_resolver(db, [el(1, "textbox", "Are you willing to relocate?")], env=stranger)
    assert (await resolver.resolve(prepared[0])).result["answer"] is None  # saved answers never leak between applications
