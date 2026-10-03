from datetime import date
import pytest

from jobagent.db import Database
from jobagent.intelligence.store import entity, ingest_fact, link, policy, update_fact
from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.temporal import exposure
from jobagent.intelligence.formatting import choice


@pytest.fixture(scope="module")
def knowledge(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("intelligence") / "db.sqlite")

    def record(kind, name, fields):
        eid = entity(db, kind, name)
        ids = {}
        for key, value in fields.items():
            vt = (
                "boolean"
                if isinstance(value, bool)
                else "number"
                if isinstance(value, (int, float))
                else "list"
                if isinstance(value, list)
                else "text"
            )
            ids[key] = ingest_fact(
                db,
                eid,
                key,
                value,
                value_type=vt,
                source="Synthetic verified test record",
                evidence={"excerpt": str(value)},
                verified=True,
            )
        return eid, ids

    record(
        "person",
        "Test Candidate",
        dict(
            full_name="Test Candidate",
            first_name="Test",
            last_name="Candidate",
            email="test@example.test",
            phone="+441234567890",
            city="London",
            location="London, United Kingdom",
            country="United Kingdom",
            country_code="GB",
            nationality="Spanish",
            linkedin="https://example.test/linkedin",
            portfolio="https://example.test/portfolio",
            github="https://example.test/github",
            employment_contract_active=False,
            professional_interests="AI and software engineering",
        ),
    )
    record(
        "education",
        "North University",
        dict(
            institution="North University",
            degree="BSc Computer Science",
            level="Bachelor's degree",
            rank=1,
            subject="Computer Science",
            status="completed",
            start_date="2023-09-01",
            end_date="2026-06-30",
            classification="First Class Honours",
            average=79.94,
        ),
    )
    record(
        "education",
        "South University",
        dict(
            institution="South University",
            degree="MSc Artificial Intelligence",
            level="Master's degree",
            rank=2,
            subject="Artificial Intelligence",
            status="studying",
            start_date="2026-09-28",
            end_date="2027-09-27",
            full_time=True,
        ),
    )
    record("language", "English", dict(proficiency="Fluent"))
    record("language", "Spanish", dict(proficiency="Native"))
    record("immigration", "UK Student", dict(country="United Kingdom", route="Student"))
    record(
        "experience",
        "Example Studio",
        dict(
            role="Developer",
            team_size=6,
            start_date="2025-07-01",
            end_date="2025-09-30",
            story="I led six engineers to deliver a responsive website.",
            tags=["leadership", "frontend", "teamwork"],
        ),
    )
    a, fa = record(
        "project",
        "First Project",
        dict(
            start_date="2024-01-01",
            end_date="2024-12-31",
            technologies=["Python"],
            story="I built a vision pipeline using Python.",
            tags=["ai", "machine learning", "ownership"],
        ),
    )
    b, fb = record(
        "project",
        "Second Project",
        dict(
            start_date="2024-06-01",
            end_date="2025-05-31",
            technologies=["Python"],
            story="I built a Python API.",
            tags=["backend"],
        ),
    )
    skill = entity(db, "skill", "Python")
    link(db, skill, "used_in", a, [fa["technologies"]])
    link(db, skill, "used_in", b, [fb["technologies"]])
    policy(db, "optional_demographics", "decline_or_blank")
    policy(db, "salary_expected", {"target": 60000, "currency": "GBP"}, {"country": "United Kingdom"})
    return db


# 25 concept families x 5 noun constructions x 4 natural request frames = 500
# distinct questions. Expected values come from the independent synthetic record.
FAMILIES = [
    (
        "current university",
        "current academic institution",
        "university currently attended",
        "university where I am studying",
        "institution at which I am enrolled",
        "South University",
    ),
    (
        "most recent completed university",
        "university from which I graduated",
        "latest university where I completed a degree",
        "institution where I earned my completed degree",
        "completed university institution",
        "North University",
    ),
    (
        "highest completed degree level",
        "level of my completed qualification",
        "highest awarded qualification level",
        "degree level already achieved",
        "completed undergraduate degree level",
        "Bachelor's degree",
    ),
    (
        "highest degree currently pursuing",
        "highest qualification in progress",
        "level of my current degree",
        "level of my postgraduate degree",
        "current master degree level",
        "Master's degree",
    ),
    (
        "undergraduate degree classification",
        "classification of my completed degree",
        "final classification achieved",
        "honours classification",
        "bachelor degree classification",
        "First Class Honours",
    ),
    (
        "undergraduate degree average",
        "weighted degree average",
        "degree percentage",
        "academic result average",
        "overall degree average",
        "79.94",
    ),
    (
        "expected graduation year",
        "year of expected graduation",
        "master graduation year",
        "year when I graduate",
        "anticipated graduation year",
        "2027",
    ),
    (
        "earliest start date",
        "available start date",
        "employment commencement availability",
        "date I can join",
        "earliest employment availability",
        "28 September 2027",
    ),
    (
        "current notice period",
        "contractual notice period",
        "employment notice",
        "notice required to resign",
        "current contractual notice",
        "None",
    ),
    ("first name", "given name", "legal first name", "first personal name", "first registered name", "Test"),
    ("family name", "surname", "last name", "legal last name", "family surname", "Candidate"),
    ("full name", "legal name", "complete name", "registered name", "candidate name", "Test Candidate"),
    ("email", "email address", "contact email", "preferred email address", "personal email", "test@example.test"),
    ("phone", "telephone", "mobile number", "phone number", "contact telephone", "+441234567890"),
    ("current city", "city of residence", "city where I live", "home city", "current town", "London"),
    (
        "current country",
        "country of residence",
        "country where I live",
        "residential country",
        "present country",
        "United Kingdom",
    ),
    (
        "country code",
        "current country code",
        "residence country code",
        "two letter country code",
        "country code for my location",
        "GB",
    ),
    ("nationality", "citizenship", "national citizenship", "current nationality", "candidate nationality", "Spanish"),
    (
        "current visa type",
        "immigration route",
        "visa route",
        "current immigration status",
        "type of visa currently held",
        "Student",
    ),
    (
        "English proficiency",
        "English level",
        "English language ability",
        "English fluency",
        "proficiency in English",
        "Fluent",
    ),
    (
        "Spanish proficiency",
        "Spanish level",
        "Spanish language ability",
        "Spanish fluency",
        "proficiency in Spanish",
        "Native",
    ),
    (
        "LinkedIn profile",
        "LinkedIn URL",
        "LinkedIn",
        "link to LinkedIn",
        "professional LinkedIn page",
        "https://example.test/linkedin",
    ),
    (
        "portfolio",
        "portfolio URL",
        "personal website",
        "portfolio website",
        "portfolio link",
        "https://example.test/portfolio",
    ),
    ("GitHub profile", "GitHub URL", "GitHub", "GitHub account link", "GitHub page", "https://example.test/github"),
    (
        "largest team led",
        "largest team size managed",
        "number of engineers led",
        "largest number of people led",
        "largest technical team managed",
        "6",
    ),
]
FRAMES = ["Please state {}.", "Please provide {}.", "What is my {}?", "Specify {}."]
CASES = [(frame.format(noun), family[-1]) for family in FAMILIES for noun in family[:-1] for frame in FRAMES]
assert len(CASES) == 500 and len({q for q, _ in CASES}) == 500


@pytest.mark.parametrize("question,expected", CASES)
def test_semantic_paraphrases(knowledge, question, expected):
    r = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    assert r["answer"] == expected, (question, r)
    assert r["facts_used"] or r["rules_used"]


def test_adversarial_pairs_and_no_current_employer_invention(knowledge):
    engine = CandidateEngine(knowledge, as_of="2026-10-03")
    job = {"location": "London", "country": "United Kingdom"}
    expected = [
        ("Highest completed degree?", "Bachelor's degree"),
        ("Highest degree pursuing?", "Master's degree"),
        ("Current university?", "South University"),
        ("University where I completed my bachelor degree?", "North University"),
        ("Current notice period?", "None"),
        ("Days until graduation?", "359"),
        ("Current salary?", None),
        ("Salary expectations?", "60000"),
        ("Current employer?", None),
        ("Most recent employer?", "Example Studio"),
    ]
    for q, value in expected:
        assert engine.answer_question(q, {}, job)["answer"] == value, q
    results = [
        engine.answer_question(q, {}, job)
        for q in [
            "Do you currently require sponsorship?",
            "Will you ever require sponsorship?",
            "Do you permanently have the right to work in the UK?",
        ]
    ]
    assert all(r["answer"] is None for r in results)
    assert [r["semantic"]["qualifier"] for r in results] == ["current", "future", "permanent"]


def test_union_exposure_and_temporal_change(knowledge):
    x = CandidateEngine(knowledge, as_of="2026-10-03")
    r = x.answer_question("How many years of Python experience do you have?", {"type": "number"})
    assert r["answer"] == "1" and r["calculation"]["calendar"]["days"] == 517
    assert r["calculation"]["professional"]["days"] == 0
    assert (
        x.answer_question("How many years of professional Python experience do you have?", {"type": "number"})["answer"]
        is None
    )
    a = x.answer_question("Days until graduation?")["answer"]
    b = CandidateEngine(knowledge, as_of="2026-10-04").answer_question("Days until graduation?")["answer"]
    assert int(a) - int(b) == 1
    assert CandidateEngine(knowledge, as_of="2028-01-01").answer_question("Current university?")["answer"] is None


def test_enum_numeric_and_disclosure_format(knowledge):
    x = CandidateEngine(knowledge, as_of="2026-10-03")
    assert x.answer_question("Degree average", {"type": "number", "step": "1"})["answer"] == "80"
    assert x.answer_question("Degree average", {"options": [{"label": "75–84%", "value": "x"}]})["answer"] == "75–84%"
    assert (
        x.answer_question("Degree classification", {"options": [{"label": "First / 1st", "value": "1"}]})["answer"]
        == "First / 1st"
    )
    assert x.answer_question("Numeric GPA", {"type": "number"})["answer"] is None
    assert "No official US GPA" in x.answer_question("GPA", {"tag": "textarea"})["answer"]
    assert x.answer_question("Describe your gender", {"required": False})["leave_blank"]
    assert (
        x.answer_question("Describe your gender", {"options": [{"label": "Prefer not to say", "value": "decline"}]})[
            "answer"
        ]
        == "Prefer not to say"
    )
    assert x.answer_question("Do you consent to all terms?", {"type": "checkbox"})["answer"] is None
    assert choice("First Class Honours", [{"label": "2:1", "value": "2"}]) is None


def test_contextual_story_and_no_unsupported_claims(knowledge):
    x = CandidateEngine(knowledge, as_of="2026-10-03")
    a = x.answer_question("Describe a project", {"tag": "textarea"}, {"title": "Machine Learning AI Engineer"})
    b = x.answer_question("Describe a project", {"tag": "textarea"}, {"title": "Backend Engineer"})
    assert "vision" in a["answer"] and "API" in b["answer"]
    assert x.answer_question("Describe a failure you overcame", {"tag": "textarea"})["answer"] is None
    assert x.answer_question("Describe a project", {"tag": "textarea", "max_length": 10})["answer"] is None


def test_conflicts_expiry_edit_and_priority(tmp_path):
    db = Database(tmp_path / "db")
    e = entity(db, "person", "Test")
    f = ingest_fact(db, e, "full_name", "A", source="one", evidence={}, verified=True)
    ingest_fact(db, e, "full_name", "B", source="two", evidence={}, verified=True)
    assert CandidateEngine(db).answer_question("Full name")["answer"] is None
    update_fact(db, f, "C", True)
    assert CandidateEngine(db).answer_question("Full name")["answer"] == "C"
    assert exposure([(date(2024, 1, 1), date(2024, 12, 31))] * 3)["days"] == 366


@pytest.mark.parametrize("question", ["Please give your email and phone", "Email and also phone", "Email ; phone"])
def test_multi_intent_contact_is_composed(knowledge, question):
    result = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    assert result["answer"] == "test@example.test; +441234567890"
    assert len(result["facts_used"]) == 2


@pytest.mark.parametrize("label", ["Apply", "Apply now", "Finish application", "Submit", "Send application"])
def test_final_boundary(label):
    from jobagent.automation.browser import is_submit

    assert is_submit({"role": "button", "label": label, "type": "button"})


@pytest.mark.parametrize("question", ["Current notice period", "Contractual notice", "How much notice is required?"])
def test_study_completion_does_not_prove_no_notice(tmp_path, question):
    db = Database(tmp_path / "db")
    eid = entity(db, "education", "Postgraduate school")
    for key, value in {"end_date": "2027-09-27", "level": "Master's degree", "start_date": "2026-09-28"}.items():
        ingest_fact(db, eid, key, value, source="verified course", evidence={}, verified=True)
    assert CandidateEngine(db, as_of="2026-10-03").answer_question(question)["answer"] is None


@pytest.mark.parametrize("question", ["GDPR Notice", "Privacy notice agreement", "Consent to data processing notice"])
def test_not_every_notice_is_contractual(knowledge, question):
    r = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    assert r["canonical_intent"] == "CONSENT" and r["answer"] is None


@pytest.mark.parametrize(
    "question",
    [
        "Are you authorised to work permanently in the UK without sponsorship?",
        "Do you have an unrestricted right to work without a visa?",
        "Are you permanently authorised to work without visa sponsorship?",
    ],
)
def test_legal_head_intent_over_modifying_sponsorship(knowledge, question):
    r = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question, {}, {"location": "London"})
    assert r["canonical_intent"] == "WORK_AUTHORIZATION" and r["answer"] is None


def test_profile_edit_and_clear_override_previous_knowledge(tmp_path):
    from jobagent.application_profile import write_profile

    db = Database(tmp_path / "db")
    eid = entity(db, "person", "Example")
    ingest_fact(db, eid, "full_name", "Old", source="old CV", evidence={}, verified=True)
    write_profile(db, "full_name", "New", True)
    assert CandidateEngine(db).answer_question("Full name")["answer"] == "New"
    write_profile(db, "full_name", "Unconfirmed", False)
    assert CandidateEngine(db).answer_question("Full name")["answer"] is None


def test_knowledge_api_coverage_edit_and_explanation(knowledge):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jobagent.core import router

    app = FastAPI()
    app.state.db = knowledge
    app.include_router(router, prefix="/api")
    with TestClient(app) as c:
        result = c.get("/api/intelligence").json()
        assert result["coverage"] and result["relationships"]
        answer = c.post("/api/intelligence/answer", json={"question": "First name"}).json()
        assert answer["facts_used"] and answer["answer"] == "Test"
        row = next(f for f in result["facts"] if f["concept"] == "first_name")
        assert (
            c.put("/api/intelligence/facts/" + row["id"], json={"value": "Test", "confirmed": True}).status_code == 200
        )
        assert c.get("/api/intelligence").json()["version"] != result["version"]


@pytest.mark.parametrize(
    "question",
    ["Which university are you currently enrolled at?", "Most recent university attended", "Current university"],
)
def test_decorated_institution_option_is_stable(knowledge, question):
    from jobagent.automation.answers import selected_choice_matches

    options = [{"label": "South University (SU)", "value": "su"}]
    answer = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question, {"options": options})
    assert answer["answer"] == "South University (SU)"
    assert selected_choice_matches(question, "South University", "South University (SU)")


def test_structured_star_without_saved_prose(tmp_path):
    db = Database(tmp_path / "db")
    eid = entity(db, "project", "Product")
    for key, value in {
        "situation": "The product generates educational videos.",
        "task": "I was the main developer.",
        "action": "I built the frontend and rendering pipeline.",
        "result": "The product rendered videos with captions.",
        "tags": ["ownership", "ai"],
    }.items():
        ingest_fact(
            db,
            eid,
            key,
            value,
            value_type="list" if isinstance(value, list) else "text",
            source="verified",
            evidence={},
            verified=True,
        )
    r = CandidateEngine(db).answer_question("Describe ownership of a product", {"tag": "textarea"})
    assert "main developer" in r["answer"] and "captions" in r["answer"] and len(r["facts_used"]) == 5


def test_profile_can_restore_a_previously_used_value(tmp_path):
    from jobagent.application_profile import write_profile

    db = Database(tmp_path / "db")
    entity(db, "person", "Profile")
    write_profile(db, "full_name", "First", True)
    write_profile(db, "full_name", "Second", True)
    write_profile(db, "full_name", "First", True)
    assert CandidateEngine(db).answer_question("Full name")["answer"] == "First"


@pytest.mark.parametrize(
    "key,initial,new",
    [
        ("full_name", "Original Name", "New Name"),
        ("email", "old@example.test", "new@example.test"),
        ("phone", "+44 7700900123", "+44 7700900456"),
    ],
)
def test_profile_reads_typed_values_and_edits_remain_consistent(tmp_path, key, initial, new):
    from jobagent.application_profile import profile, write_profile
    from jobagent.intelligence.store import update_fact

    db = Database(tmp_path / "db")
    eid = entity(db, "person", "Example")
    fid = ingest_fact(db, eid, key, initial, source="verified import", evidence={}, verified=True)

    def shown():
        return next(f for f in profile(db)["fields"] if f["key"] == key)

    assert shown()["value"] == initial and shown()["state"] == "confirmed"
    update_fact(db, fid, new, True)
    assert shown()["value"] == new
    write_profile(db, key, initial, True)
    assert shown()["value"] == initial
    write_profile(db, key, "", False)
    assert shown()["value"] == "" and shown()["state"] == "unknown"
    assert CandidateEngine(db).answer_question(key.replace("_", " "))["answer"] is None


def test_legacy_fact_endpoints_cannot_corrupt_typed_storage(knowledge):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jobagent.core import router

    app = FastAPI()
    app.state.db = knowledge
    app.include_router(router, prefix="/api")
    with TestClient(app) as c:
        typed = c.get("/api/intelligence").json()["facts"][0]
        assert all(f["category"] != "knowledge" for f in c.get("/api/facts").json()["items"])
        assert c.patch("/api/facts/" + typed["id"], json={"value": "untyped mutation"}).status_code == 409
        assert c.get("/api/intelligence").status_code == 200


@pytest.mark.parametrize("question,concept", [("Full name", "full_name"), ("Email", "email"), ("Phone", "phone")])
def test_cleared_typed_fact_cannot_revive_older_question_answer(tmp_path, question, concept):
    from jobagent.intelligence.store import update_fact

    db = Database(tmp_path / "db")
    person = entity(db, "person", "Example")
    fid = ingest_fact(db, person, concept, "Old", source="import", evidence={}, verified=True)
    update_fact(db, fid, "", False)
    engine = CandidateEngine(db)
    result = engine.answer_question(question)
    memory = {
        "id": "memory",
        "key": question,
        "value": "Old",
        "verification_status": "verified",
        "category": "application_answer",
        "profile_explicit": True,
    }
    assert engine.confirmed_fallback(question, result, [memory], {})["answer"] is None


@pytest.mark.parametrize(
    "question",
    [
        "What degree did you complete at the above university?",
        "Which qualification did you complete at the above institution?",
        "State the degree you completed at the above school.",
    ],
)
def test_completed_qualification_never_uses_current_institution(knowledge, question):
    engine = CandidateEngine(knowledge, as_of="2026-10-03")
    answer = engine.answer_question(question, {"_education_reference": "South University"})
    assert answer["answer"] is None
    assert answer["root_cause"] == "CONFLICT_WITH_SELECTED_INSTITUTION"


@pytest.mark.parametrize("label", ["United Kingdom +44", "Spain +34", "France +33"])
def test_dom_country_option_dial_code_decoration(label):
    country = label.rsplit(" +", 1)[0]
    options = [{"role": "option", "label": label, "value": ""}, {"role": "option", "label": "Unknown +99", "value": ""}]
    assert choice(country, options) == options[0]


@pytest.mark.parametrize(
    "question",
    [
        "Describe a project and also demonstrate ownership.",
        "Explain your project and also show your ownership.",
        "Talk about a project and also describe ownership.",
    ],
)
def test_multi_intent_narrative_does_not_repeat_same_evidence(knowledge, question):
    result = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    if result["answer"]:
        assert result["answer"].count("I built a vision pipeline using Python.") <= 1


@pytest.mark.parametrize("question", ["Location (City)", "Current city", "City of residence"])
def test_geocoder_uses_country_to_disambiguate_city(knowledge, question):
    result = CandidateEngine(knowledge).answer_question(question, {"role": "combobox"})
    assert result["answer"] == "London, United Kingdom"
    assert "geocoder_city_country_disambiguation" in result["rules_used"]
    from jobagent.automation.answers import option_for

    options = [{"label": "London, Ontario, Canada"}, {"label": "London, England, United Kingdom"}]
    assert option_for(result["answer"], options, question=question) == options[1]


@pytest.mark.parametrize(
    "question",
    [
        "What degree did you complete at the above university?",
        "Which qualification did you complete at the above institution?",
        "State the degree you completed at the above school.",
    ],
)
def test_text_field_explains_in_progress_degree_without_misattribution(knowledge, question):
    result = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(
        question, {"type": "text", "_education_reference": "South University"}
    )
    assert "MSc Artificial Intelligence at South University is in progress" in result["answer"]
    assert "2027-09-27" in result["answer"]
    assert "BSc Computer Science at North University" in result["answer"]
    assert result["requires_review"] and not result["verified"]
    assert result["facts_used"] and result["rules_used"]


@pytest.mark.parametrize("label", ["Fluent", "Fluent/Native", "Fluent or native"])
def test_fluent_combined_option_does_not_assert_native_language(label):
    options = [
        {"role": "option", "label": label, "value": ""},
        {"role": "option", "label": "Intermediate (B1-B2)", "value": ""},
    ]
    assert choice("Fluent / professional working proficiency", options) == options[0]
    assert choice("Fluent / professional working proficiency", [{"label": "Native", "value": "native"}]) is None


@pytest.mark.parametrize(
    "question",
    [
        "Are you a recent graduate with a Computer Engineering or STEM degree?",
        "Have you recently graduated with a STEM qualification?",
        "Are you a recent graduate in Computer Science?",
    ],
)
def test_recent_degree_is_a_reviewable_temporal_inference(knowledge, question):
    result = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    assert result["answer"] == "Yes"
    assert result["confidence_level"] == "DERIVED_MEDIUM" and result["requires_review"]
    assert any("recency_definition_not_supplied" in r for r in result["rules_used"])
    assert CandidateEngine(knowledge, as_of="2030-10-03").answer_question(question)["answer"] is None


@pytest.mark.parametrize(
    "question",
    [
        "Have you completed a masters degree?",
        "Have you earned a postgraduate qualification?",
        "Did you complete your MSc degree?",
    ],
)
def test_named_degree_level_does_not_override_completion(knowledge, question):
    result = CandidateEngine(knowledge, as_of="2026-10-03").answer_question(question)
    assert result["answer"] is None


@pytest.mark.parametrize(
    "question",
    [
        "Are you a recent graduate with a Computer Engineering or STEM degree?",
        "Have you recently graduated with a STEM qualification?",
        "Are you a recent graduate in Computer Science?",
    ],
)
async def test_model_cannot_replace_grounded_boolean_with_unrelated_grade(knowledge, question, monkeypatch):
    from jobagent.intelligence import engine as module

    async def wrong_classifier(*args):
        raise AssertionError("A grounded answer must not be reclassified")

    monkeypatch.setattr(module, "semantic_interpret", wrong_classifier)
    result = await CandidateEngine(knowledge, as_of="2026-10-03").answer(question)
    assert result["answer"] == "Yes"
    assert result["reasoning_summary"] != "MISSING_FACT"


async def test_model_still_interprets_an_unknown_concept(knowledge, monkeypatch):
    from jobagent.intelligence import engine as module
    from jobagent.intelligence.semantics import interpret

    async def classifier(*args):
        return {**interpret("Current university"), "parser": "local_semantic"}

    monkeypatch.setattr(module, "semantic_interpret", classifier)
    result = await CandidateEngine(knowledge, as_of="2026-10-03").answer("Which alma mater is mine nowadays?")
    assert result["answer"] == "South University"
    assert result["requires_review"] and not result["verified"]
