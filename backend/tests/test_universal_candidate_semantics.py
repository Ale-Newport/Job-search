"""Factual regression against independent evidence, not production overrides.

Paraphrases, conflicting date instructions, education levels and jurisdiction
changes are exercised together. Unknown facts are asserted unknown explicitly.
"""

import json
import pytest

from jobagent.db import Database
from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.formatting import AnswerFormatResolver, adapt
from jobagent.intelligence.semantics import interpret, education_level
from jobagent.intelligence.store import entity, ingest_fact, Knowledge, update_fact, policy
from jobagent.intelligence.candidate_updates import apply_authoritative_candidate_updates


@pytest.fixture(scope="module")
def verified_db(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("universal-semantics") / "db.sqlite3")

    def record(kind, name, facts):
        eid = entity(db, kind, name)
        for concept, value in facts.items():
            vt = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "list" if isinstance(value, list) else "text"
            ingest_fact(db, eid, concept, value, value_type=vt, source="Independent verified test evidence", evidence={"excerpt": str(value)}, verified=True)

    record("person", "Morgan Example", {
        "full_name": "Morgan Example", "first_name": "Morgan", "last_name": "Example", "preferred_name": "Mo",
        "email": "morgan@example.test", "phone": "+447700900123", "city": "London", "country": "United Kingdom",
        "country_code": "GB", "location": "London, United Kingdom", "nationality": "Spanish",
        "nationality_country": "Spain", "github": "https://github.com/test-profile", "portfolio": "https://example.test",
        "linkedin": "https://linkedin.com/in/test-profile", "earliest_full_time_start_date": "2027-09",
        "current_employment_notice_days": 14, "professional_interests": "AI and backend software engineering",
    })
    record("education", "North University", {
        "institution": "North University", "degree": "BSc Computer Science", "level": "Bachelor's degree", "rank": 1,
        "status": "completed", "subject": "Computer Science", "start_date": "2023-09", "end_date": "2026-06",
        "classification": "First Class Honours", "average": 79.94,
    })
    record("education", "South University", {
        "institution": "South University", "degree": "MSc Data Engineering", "level": "MASTER", "rank": 2,
        "status": "studying", "subject": "Data Engineering", "start_date": "2026-09", "end_date": "2027-09",
    })
    record("language", "English", {"proficiency": "Fluent", "native": False})
    record("language", "Spanish", {"proficiency": "Native", "native": True})
    record("immigration", "UK authorization", {"country": "United Kingdom", "current_right_to_work": True,
                                                "sponsorship_now_or_future": True, "route": "Student"})
    policy(db, "salary_expected", {"target": 60000, "currency": "GBP"}, {"country": "United Kingdom"})
    return db


# The expected intent/value pairs distinguish topics that previously selected
# the same university, date or narrative. No production profile is imported.
FACTUAL_CASES = [
    ("full name", "IDENTITY", "Morgan Example"), ("given name", "IDENTITY", "Morgan"),
    ("first name", "IDENTITY", "Morgan"), ("surname", "IDENTITY", "Example"),
    ("family name", "IDENTITY", "Example"), ("preferred name", "IDENTITY", "Mo"),
    ("email address", "CONTACT", "morgan@example.test"), ("phone number", "CONTACT", "+447700900123"),
    ("mobile telephone", "CONTACT", "+447700900123"), ("GitHub profile", "GITHUB", "https://github.com/test-profile"),
    ("personal website", "PORTFOLIO", "https://example.test"), ("portfolio URL", "PORTFOLIO", "https://example.test"),
    ("LinkedIn profile", "LINKEDIN", "https://linkedin.com/in/test-profile"), ("current city", "LOCATION", "London"),
    ("country of residence", "LOCATION", "United Kingdom"), ("country code", "LOCATION", "GB"),
    ("nationality", "NATIONALITY", "Spanish"), ("country of citizenship", "NATIONALITY", "Spain"),
    ("current university", "EDUCATION", "South University"), ("university currently attended", "EDUCATION", "South University"),
    ("current postgraduate institution", "EDUCATION", "South University"), ("master institution", "EDUCATION", "South University"),
    ("undergraduate institution", "EDUCATION", "North University"), ("bachelor institution", "EDUCATION", "North University"),
    ("highest completed degree", "DEGREE", "Bachelor's degree"), ("completed undergraduate qualification", "DEGREE", "BSc Computer Science"),
    ("degree currently pursuing", "DEGREE", "MSc Data Engineering"), ("current master degree", "DEGREE", "MSc Data Engineering"),
    ("current degree subject", "DEGREE", "Data Engineering"), ("bachelor major", "DEGREE", "Computer Science"),
    ("undergraduate degree classification", "GRADE", "First Class Honours"), ("weighted degree average", "GRADE", "79.94"),
    ("expected graduation year", "GRADUATION", "2027"), ("undergraduate graduation year", "GRADUATION", "2026"),
    ("master graduation year", "GRADUATION", "2027"), ("expected graduation month", "GRADUATION", "September"),
    ("anticipated start date (Month/Year)", "START_DATE", "09/2027"), ("earliest joining date (YYYY-MM)", "START_DATE", "2027-09"),
    ("current contractual notice", "NOTICE_PERIOD", "14 days"), ("employment notice period", "NOTICE_PERIOD", "14 days"),
    ("native language", "LANGUAGE", "Spanish"), ("mother tongue", "LANGUAGE", "Spanish"),
    ("English proficiency", "LANGUAGE", "Fluent"), ("Spanish proficiency", "LANGUAGE", "Native"),
    ("high school name", "EDUCATION", None), ("secondary school name", "EDUCATION", None),
    ("sixth form school", "EDUCATION", None), ("primary school name", "EDUCATION", None),
    ("elementary school name", "EDUCATION", None), ("bachillerato school", "EDUCATION", None),
    ("year of high school graduation", "GRADUATION", None), ("secondary school graduation year", "GRADUATION", None),
    ("sixth form graduation year", "GRADUATION", None), ("baccalaureate graduation year", "GRADUATION", None),
    ("current salary", "SALARY", None), ("previous compensation", "SALARY", None),
    ("expected annual salary", "SALARY", "60000"), ("minimum salary", "SALARY", None),
    ("name pronunciation", "NAME_PRONUNCIATION", None), ("phonetic name pronunciation", "NAME_PRONUNCIATION", None),
    ("upcoming employment offer deadlines", "OFFER_DEADLINE", None), ("competing offer expiration date", "OFFER_DEADLINE", None),
    ("AI interview transcription consent", "CONSENT", None), ("future recruiting contact consent", "CONSENT", None),
    ("privacy processing acknowledgment", "CONSENT", None), ("terms agreement", "CONSENT", None),
    ("current PhD institution", "EDUCATION", None), ("doctoral qualification", "DEGREE", None),
    ("street address", "ADDRESS", None), ("postcode", "ADDRESS", None),
    ("postal code", "ADDRESS", None), ("date of birth", "IDENTITY", None),
    ("current employer", "WORK_EXPERIENCE", None), ("most recent employer", "WORK_EXPERIENCE", None),
    ("criminal history", "CRIMINAL_HISTORY", None), ("security clearance", "SECURITY_CLEARANCE", None),
]
FRAMES = ["Please provide your {}.", "State the {}.", "What is your {}?"]


@pytest.mark.parametrize("noun,intent,expected", FACTUAL_CASES)
@pytest.mark.parametrize("frame", FRAMES)
def test_question_fact_semantics(verified_db, noun, intent, expected, frame):
    question = frame.format(noun)
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question(question, {}, {"country": "United Kingdom"})
    assert result["canonical_intent"] == intent, (question, result)
    assert result["answer"] == expected, (question, result)
    if expected is None:
        assert result["root_cause"] is not None and not result["verified"]
    else:
        assert result["fact_ids"] or result["rules_used"]


@pytest.mark.parametrize("question", ["What year of your studies are you in?", "Which year of your degree are you in?", "Current academic year of study"])
def test_active_study_year_uses_current_postgraduate_start(verified_db, question):
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question(question,
        {"tag": "select", "options": [{"label": f"Year {i}", "value": str(i)} for i in range(1, 5)]})
    assert result['canonical_intent'] == 'STUDY_YEAR'
    assert result['answer'] == 'Year 1'


def test_course_finish_checkbox_group_selects_one_verified_year(verified_db):
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question('What year does your course finish?',
        {'role': 'checkbox', 'type': 'checkbox', 'multiple': True,
         'options': [{'label': str(year), 'value': str(year)} for year in range(2027, 2031)]})
    assert result['canonical_intent'] == 'GRADUATION'
    assert result['form_value'] == ['2027']


def test_future_location_does_not_reuse_current_city(verified_db):
    result = CandidateEngine(verified_db).answer_question('Where will you be living during this internship?',
        {'tag': 'textarea'}, {'id': 'specific-internship', 'country': 'United Kingdom'})
    assert result['answer'] is None
    assert result['root_cause'] == 'MISSING_VERIFIED_RESIDENCE_PLAN'


def test_coding_explanation_is_not_an_optional_github_link(verified_db):
    question = 'Do you have prior coding experience? If so, please provide details about languages, projects or experiences that demonstrate your coding skills. Github or portfolio links welcomed.'
    result = CandidateEngine(verified_db).answer_question(question, {'tag': 'textarea'})
    assert result['canonical_intent'] == 'WORK_EXPERIENCE'
    assert result['answer'] is None


def test_conditional_future_sponsorship_preserves_exact_qualifier(verified_db):
    result = CandidateEngine(verified_db).answer_question('If you are offered a permanent graduate role after completing your internship, would you require visa sponsorship to work in the UK?',
        {'options': [{'label': 'Yes'}, {'label': 'No'}]}, {'country': 'United Kingdom'})
    assert result['canonical_intent'] == 'SPONSORSHIP'
    assert result['semantic']['qualifier'] == 'future'
    assert result['answer'] is None


def test_legacy_month_availability_does_not_invent_day():
    from jobagent.automation.reasoning import profile_resolution
    result = profile_resolution('When can you start?',
        [{'id': 'month', 'key': 'earliest_start_month', 'value': '2027-09', 'verification_status': 'verified', 'locked': 1}],
        element={'type': 'date'})
    assert result['answer'] is None
    assert result['root_cause'] == 'MISSING_EXACT_START_DAY'


FORMATS = {"YYYY": "2027", "MM/YYYY": "09/2027", "MM/YY": "09/27", "YYYY-MM": "2027-09",
           "Month Year": "September 2027", "Month/Year": "09/2027"}
DATE_QUESTIONS = ["Anticipated start date", "Earliest joining date", "Expected graduation", "Master graduation date",
                  "Date you are available for work", "Degree completion date"]
METADATA = ["question", "description", "help_text", "placeholder", "label", "instructions", "validation_message", "question_parentheses", "question_suffix"]


@pytest.mark.parametrize("question", DATE_QUESTIONS)
@pytest.mark.parametrize("format_name,expected", FORMATS.items())
@pytest.mark.parametrize("metadata", METADATA)
def test_date_format_from_all_visible_instructions(verified_db, question, format_name, expected, metadata):
    field = {"type": "text"}
    if metadata == "question":
        question += " — " + format_name
    elif metadata == "question_parentheses":
        question += " (" + format_name + ")"
    elif metadata == "question_suffix":
        question += ", use " + format_name
    else:
        field[metadata] = format_name
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question(question, field)
    assert result["answer"] == expected, result
    assert AnswerFormatResolver.validate(result["form_value"], question, field) is None


@pytest.mark.parametrize("level,expected", [
    ("SECONDARY", "secondary"), ("HIGH_SCHOOL", "secondary"), ("SIXTH_FORM", "secondary"),
    ("Secondary education", "secondary"), ("Primary education", "primary"),
    ("BACCALAUREATE", "secondary"), ("Bachillerato", "secondary"), ("ESO", "secondary"),
    ("UNDERGRADUATE", "bachelor"), ("BACHELOR", "bachelor"), ("Bachelor's degree", "bachelor"),
    ("POSTGRADUATE", "master"), ("MASTER", "master"), ("MSc", "master"), ("DOCTORATE", "doctorate"),
])
def test_education_level_vocabulary(level, expected):
    assert education_level(level) == expected


@pytest.mark.parametrize("level_fields", [
    {"education_level": "SECONDARY"},
    {"level": "Secondary education"},
    {"education_level": "SECONDARY", "level": "Secondary education"},
])
def test_verified_secondary_level_fields_select_school_and_native_graduation_year(tmp_path, level_fields):
    db = Database(tmp_path / "education.sqlite3")
    evidence_ids = {}
    for name, fields in [
        ("Harbor School", {"institution": "Harbor School", "status": "completed", "end_date": "2023",
                           "graduation_year": 2023, **level_fields}),
        ("Later University", {"institution": "Later University", "level": "MASTER", "status": "studying",
                              "start_date": "2026-09", "end_date": "2027-09", "rank": 2}),
    ]:
        identifier = entity(db, "education", name)
        for concept, value in fields.items():
            fact_id = ingest_fact(db, identifier, concept, value,
                                  value_type="integer" if isinstance(value, int) else "text",
                                  source="Verified school record", evidence={"excerpt": str(value)}, verified=True)
            if name == "Harbor School":
                evidence_ids[concept] = fact_id
    engine = CandidateEngine(db, as_of="2026-10-10")
    for question in ("High School Name✱", "Secondary school name"):
        result = engine.answer_question(question, {"type": "text", "required": True})  # (a long-text field is never answered from one line of a fact)
        assert result["answer"] == "Harbor School", result
        assert result["root_cause"] is None
        assert evidence_ids["institution"] in result["fact_ids"]
        assert evidence_ids.get("education_level", evidence_ids.get("level")) in result["fact_ids"]
    question = "Year of High School Graduation"
    result = engine.answer_question(question, {"type": "select-one", "tag": "select", "required": True,
        "options": [{"label": "Select...", "value": ""}, *[
            {"label": str(year), "value": str(year)} for year in range(2020, 2031)],
            {"label": "Other", "value": "Other"}]})
    assert result["answer"] == result["form_value"] == "2023", result
    assert result["root_cause"] is None
    assert evidence_ids["end_date"] in result["fact_ids"]


@pytest.mark.parametrize("country", ["United Kingdom", "UK", "GB", "Spain", "United States", "France", "Germany", "Canada"])
@pytest.mark.parametrize("question", [
    "Are you legally authorized to work in the country for which you are applying?",
    "Do you have a current right to work?", "Are you currently authorised to work?",
    "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B, etc.)?",
    "Do you need employment visa sponsorship now or later?",
])
def test_country_scoping_and_visa_examples(verified_db, country, question):
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question(question, {}, {"country": country, "location": "London"})
    assert result["answer"] == ("Yes" if country in {"United Kingdom", "UK", "GB"} else None)
    if country not in {"United Kingdom", "UK", "GB"}:
        assert "EVIDENCE" in result["root_cause"]


@pytest.mark.parametrize("question", ["Are you permanently authorised to work?", "Do you have an unrestricted right to work?",
                                      "Do you currently require sponsorship?", "Will you require sponsorship in the future?"])
def test_broad_sponsorship_policy_does_not_prove_specific_immigration_status(verified_db, question):
    assert CandidateEngine(verified_db).answer_question(question, {}, {"country": "United Kingdom"})["answer"] is None


@pytest.mark.parametrize("label", [
    "Yes, unrestricted right to work without sponsorship",
    "Yes, I have permanent permission to work",
    "Yes, I hold a Graduate visa",
    "Yes, I can work full-time for the entire internship",
    "Yes, I am also authorised to work in the United States",
])
def test_boolean_enum_code_cannot_add_legal_assertions(verified_db, label):
    from jobagent.automation.answers import option_for
    from jobagent.intelligence.formatting import choice
    question = "Are you currently legally authorised to work in the UK?"
    options = [{"label": label, "value": "Yes"}, {"label": "No", "value": "No"}]
    assert option_for("Yes", options, question=question) is None
    assert choice("Yes", options, question=question) is None
    result = CandidateEngine(verified_db).answer_question(question, {"options": options}, {"country": "United Kingdom"})
    assert result["answer"] is None
    assert result["root_cause"] == "BAD_ENUM_MAPPING"


@pytest.mark.parametrize("answer,label,expected", [
    ("Yes", "Yes", True), ("No", "No", True),
    ("No", "No, I will never need sponsorship", False),
    ("Yes", "No, I do not have permission", False),
    ("Yes", "I have read and accepted the privacy notice", False),
    ("I hold a Student visa", "I hold a Student visa", True),
])
def test_option_label_meaning_precedes_boolean_machine_code(answer, label, expected):
    from jobagent.automation.answers import option_for
    assert (option_for(answer, [{"label": label, "value": answer}]) is not None) is expected


@pytest.mark.parametrize("answer,label,value", [("Yes", "True", "1"), ("No", "False", "0"), ("Yes", "Yes", "No")])
def test_plain_boolean_labels_win_over_opaque_storage(answer, label, value):
    from jobagent.automation.answers import option_for
    options = [{"label": label, "value": value}]
    assert option_for(answer, options) == options[0]


def immigration_db(tmp_path, facts):
    db = Database(tmp_path / "immigration.sqlite3")
    identifier = entity(db, "immigration", "Verified permission")
    for concept, value in {"country": "United Kingdom", **facts}.items():
        ingest_fact(db, identifier, concept, value, value_type="boolean" if isinstance(value, bool) else "text",
                    source="Independent permission evidence", evidence={"excerpt": str(value)}, verified=True)
    return db


@pytest.mark.parametrize("question,extra", [
    ("Do you currently have right to work in the UK without restrictions?", {}),
    ("Are you authorised to work full-time without limitations in the UK?", {"full_time_right_to_work": True}),
    ("Do you have unrestricted full-time right to work in the UK?", {"full_time_right_to_work": True}),
    ("Will you have unrestricted right to work in the UK when you start?", {"future_right_to_work": True}),
    ("Do you have unlimited permission to work in the UK?", {"permanent_unrestricted": False}),
    ("Do you have right to work in the UK without sponsorship?", {"sponsorship_now_or_future": True}),
    ("Are you authorised to work in the UK without a visa?", {}),
    ("Can you legally work in the UK for the full 12 weeks?", {}),
])
def test_every_requested_work_authorization_facet_requires_evidence(tmp_path, question, extra):
    db = immigration_db(tmp_path, {"current_right_to_work": True, **extra})
    result = CandidateEngine(db).answer_question(question, {"options": [{"label": "Yes"}, {"label": "No"}]}, {"country": "United Kingdom"})
    assert result["canonical_intent"] == "WORK_AUTHORIZATION"
    assert result["answer"] is None
    assert result["missing_authorization_facets"]


@pytest.mark.parametrize("authorized,expected", [(True, "No"), (False, "Yes")])
@pytest.mark.parametrize("question", ["Are you not legally authorised to work in the UK?", "Do you not have right to work in the UK?", "Aren't you legally authorised to work in the UK?"])
def test_negative_legal_predicate_preserves_polarity(tmp_path, authorized, expected, question):
    db = immigration_db(tmp_path, {"current_right_to_work": authorized})
    result = CandidateEngine(db).answer_question(question, {"options": [{"label": "Yes"}, {"label": "No"}]}, {"country": "United Kingdom"})
    assert result["answer"] == expected, result
    assert "negated_legal_predicate" in result["rules_used"]


def test_composite_authorization_accepts_all_independently_verified_facets(tmp_path):
    db = immigration_db(tmp_path, {"current_right_to_work": True, "unrestricted_right_to_work": True,
        "full_time_right_to_work": True, "sponsorship_current": False})
    result = CandidateEngine(db).answer_question("Do you currently have unrestricted full-time right to work in the UK without sponsorship?",
        {"options": [{"label": "Yes"}, {"label": "No"}]}, {"country": "United Kingdom"})
    assert result["answer"] == "Yes"
    assert "conjunction_of_verified_work_authorization_facets" in result["rules_used"]


@pytest.mark.parametrize("phrase", ["without requiring sponsorship", "without any visa sponsorship",
    "without the need for employer sponsorship", "without needing any visa sponsorship", "without a requirement for sponsorship"])
@pytest.mark.parametrize("sponsor,expected", [(None, None), (True, "No"), (False, "Yes")])
def test_sponsorship_free_permission_preserves_all_phrase_facets(tmp_path, phrase, sponsor, expected):
    facts = {"current_right_to_work": True}
    if sponsor is not None:
        facts["sponsorship_current"] = sponsor
    db = immigration_db(tmp_path, facts)
    result = CandidateEngine(db).answer_question(f"Are you currently authorised to work in the UK {phrase}?",
        {"options": [{"label": "Yes"}, {"label": "No"}]}, {"country": "United Kingdom"})
    assert result["canonical_intent"] == "WORK_AUTHORIZATION"
    assert "sponsorship_free" in result["semantic"]["authorization_requirements"]
    assert result["answer"] == expected, result


def test_scoped_current_policy_cannot_answer_unrestricted_question(tmp_path):
    db = Database(tmp_path / "policy.sqlite3")
    policy(db, "work_authorization_current", True, {"country": "United Kingdom"})
    result = CandidateEngine(db).answer_question("Are you legally authorised to work in the UK without restrictions?", {}, {"country": "United Kingdom"})
    assert result["answer"] is None
    assert "unrestricted" in result["missing_authorization_facets"]


def test_explicit_other_work_country_cannot_reuse_vacancy_authorization(verified_db):
    result = CandidateEngine(verified_db).answer_question("Are you legally authorised to work in the United States?", {}, {"country": "United Kingdom"})
    assert result["answer"] is None
    assert result["missing_fact"]["country"] == "United States"


@pytest.mark.parametrize("question", [
    "Please take a look at our Candidate Data Privacy Notice (linked below) and confirm that you have.",
    "Review our privacy policy and confirm you have done so.",
    "Please confirm that you have read our candidate privacy notice.",
])
def test_notice_read_acknowledgement_does_not_reuse_processing_consent(tmp_path, question):
    db = Database(tmp_path / "privacy.sqlite3")
    policy(db, "consent_data_processing", True)
    assert interpret(question)["attribute"] == "privacy_acknowledgement"
    result = CandidateEngine(db).answer_question(question, {"options": [{"label": "Yes"}, {"label": "No"}]})
    assert result["answer"] is None
    assert result["missing_fact"]["concept"] == "consent_privacy_acknowledgement"


def test_data_processing_consent_remains_separate_from_notice_reading(tmp_path):
    db = Database(tmp_path / "processing.sqlite3")
    policy(db, "consent_privacy_acknowledgement", True)
    question = "I consent to collecting, storing and processing my personal data."
    assert interpret(question)["attribute"] == "data_processing"
    result = CandidateEngine(db).answer_question(question, {"type": "checkbox"})
    assert result["answer"] is None
    assert result["missing_fact"]["concept"] == "consent_data_processing"


@pytest.mark.parametrize("question,intent", [
    ("If this company didn't exist, what work would excite you?", "HYPOTHETICAL"),
    ("Imagine you could choose any kind of work. What would you build?", "HYPOTHETICAL"),
    ("Describe the hardest technical challenge you faced in a project.", "CHALLENGE"),
    ("Tell us one thing not on your resume that you are proud of.", "NOT_ON_RESUME"),
    ("Describe an achievement outside your CV.", "NOT_ON_RESUME"),
    ("Why Software Engineer rather than FDSE?", "ROLE_PREFERENCE"),
    ("Which engineering role resonates most with your job search and why?", "ROLE_PREFERENCE"),
    ("What are your preferred products? Pick 1-2.", "PRODUCT_PREFERENCE"),
    ("Explain your Foundry or Apollo preference.", "PRODUCT_PREFERENCE"),
    ("Describe a team conflict you resolved.", "CONFLICT"),
    ("Describe your career interests.", "CAREER_INTEREST"),
    ("Why are you interested in AI?", "CAREER_INTEREST"),
    ("Have you built an AI application?", "PROJECT"),
    ("Tell us about your CV.", "PROFILE_SUMMARY"),
    ("Why do you want to work at this company?", "MOTIVATION"),
    ("Describe a leadership experience.", "LEADERSHIP"),
    ("Describe a project you built from scratch.", "OWNERSHIP"),
    ("Describe a difficult bug.", "CHALLENGE"),
    ("Describe a time you made a mistake.", "FAILURE"),
    ("If you answered yes above, confirm your current visa type and status.", "VISA"),
])
def test_narrative_purpose_is_distinct(question, intent):
    assert interpret(question)["intent"] == intent


@pytest.mark.parametrize("question", ["Language Skill(s) (Check all that apply)", "Which languages do you speak? Select all.",
                                      "Spoken languages: choose all that apply"])
def test_true_language_multiselect(verified_db, question):
    options = [{"label": label, "value": label[:3]} for label in ["English (ENG)", "Spanish (SPA)", "French (FRA)"]]
    result = CandidateEngine(verified_db).answer_question(question, {"options": options, "multiple": True})
    assert result["form_value"] == ["English (ENG)", "Spanish (SPA)"]
    assert [option["label"] for option in result["selected_options"]] == result["form_value"]
    assert "French" not in result["answer"]


@pytest.mark.parametrize("source,options,expected", [
    ("LinkedIn", ["Indeed", "LinkedIn", "Other"], "LinkedIn"),
    ("Indeed search", ["Indeed", "Referral", "Other"], "Indeed"),
    ("Specialised graduate jobs board", ["LinkedIn", "Referral", "Other"], "Other"),
    ("Specialised graduate jobs board", ["Referral", "Recruiter"], None),
    ("unknown", ["LinkedIn", "Other"], None),
])
def test_discovery_uses_exact_job_provenance(verified_db, source, options, expected):
    field = {"options": [{"label": x, "value": x} for x in options]}
    result = CandidateEngine(verified_db).answer_question("How did you hear about this internship opportunity?", field, {"id": "job-1", "source": source})
    assert result["answer"] == expected


@pytest.mark.parametrize("fmt", ["DD/MM/YYYY", "MM/DD/YYYY", "ISO date"])
def test_month_evidence_cannot_invent_day(verified_db, fmt):
    result = CandidateEngine(verified_db).answer_question("Anticipated start date (" + fmt + ")")
    assert result["answer"] is None and result["root_cause"] == "MISSING_EXACT_DATE"


@pytest.mark.parametrize("value,question,field", [
    ("27 September 2027", "Start date (MM/YYYY)", {}), ("09/2027", "Start date (YYYY)", {}),
    ("13/2027", "Start date (MM/YYYY)", {}), ("31/02/2027", "Date (DD/MM/YYYY)", {}),
    ("79.94 percent", "Numeric percentage", {}), ("101%", "Percentage", {}),
    ("https://", "Website URL", {}), ("garbage", "Phone", {"type": "tel"}),
    ("one", "Count", {"type": "number"}),
])
def test_invalid_value_is_rejected_before_browser_insertion(value, question, field):
    assert adapt(value, interpret(question, field), field)[1] == "ANSWER_FORMAT_ERROR"


async def test_no_provider_never_returns_extractive_project_prose(verified_db):
    for question in ("Describe a technical challenge", "What work would you choose if this company didn't exist?", "Why this company?"):
        result = await CandidateEngine(verified_db).answer(question, {"tag": "textarea"}, audit=False)
        assert result["answer"] is None and result["root_cause"] == "LLM_SETUP_REQUIRED"


def test_override_preserves_contacts_and_cannot_replay_after_new_profile_edit(tmp_path):
    db = Database(tmp_path / "db")
    person = entity(db, "person", "Synthetic Candidate")
    for concept, value in {"portfolio": "https://old.example.test", "email": "stable@example.test", "phone": "+447700900123"}.items():
        ingest_fact(db, person, concept, value, source="Old verified CV", evidence={}, verified=True)
    bundle = {"source": "explicit-user-override-test", "entities": [{"kind": "person", "name": "Synthetic Candidate",
              "facts": {"portfolio": "https://new.example.test", "professional_interests": "AI and backend engineering"}}]}
    first = apply_authoritative_candidate_updates(db, bundle)
    assert first["facts_added"] == 2
    assert apply_authoritative_candidate_updates(db, bundle)["facts_added"] == 0
    person_record = Knowledge(db).records("person")[0]
    assert person_record["fields"]["email"]["value"] == "stable@example.test"
    assert person_record["fields"]["phone"]["value"] == "+447700900123"
    assert person_record["fields"]["professional_interests"]["sensitivity"] == "professional"
    portfolio = person_record["fields"]["portfolio"]
    update_fact(db, portfolio["id"], "https://later.example.test", True)
    apply_authoritative_candidate_updates(db, bundle)
    assert Knowledge(db).fact(person, "portfolio")["value"] == "https://later.example.test"
    assert any(json.loads(row["value"]) == "https://old.example.test" for row in db.query("SELECT value FROM facts WHERE key='portfolio'"))


@pytest.mark.parametrize("question", ["Do you speak French?", "French language proficiency", "Are you fluent in German?",
                                      "Japanese conversational level", "Can you speak Portuguese?"])
def test_unverified_language_is_missing_not_a_negated_fact(verified_db, question):
    result = CandidateEngine(verified_db).answer_question(question)
    assert result["answer"] is None and result["root_cause"].startswith("MISSING_LANGUAGE_PROFICIENCY")


@pytest.mark.parametrize("question", ["Are you legally authorized to work full time?", "Will you have a right to work when you start?",
                                      "Do you have an unrestricted right to work without sponsorship?"])
def test_current_right_to_work_does_not_imply_unrestricted_fulltime_or_future(verified_db, question):
    result = CandidateEngine(verified_db).answer_question(question, {}, {"country": "United Kingdom"})
    assert result["answer"] is None


@pytest.mark.parametrize("question,expected", [
    ("High school name", "West Secondary School"), ("Secondary school name", "West Secondary School"),
    ("Year of high school graduation", "2023"), ("Baccalaureate graduation year", "2023"),
    ("Highest completed qualification", "Bachelor's degree"), ("Current postgraduate institution", "East University"),
])
def test_verified_secondary_record_is_separate_from_university(tmp_path, question, expected):
    db = Database(tmp_path / "db")
    for name, facts in [
        ("West Secondary School", {"institution": "West Secondary School", "level": "BACCALAUREATE", "status": "completed", "start_date": "2021-09", "end_date": "2023", "rank": 0}),
        ("North University", {"institution": "North University", "level": "Bachelor's degree", "status": "completed", "start_date": "2023-09", "end_date": "2026-06", "rank": 1}),
        ("East University", {"institution": "East University", "level": "Master's degree", "status": "studying", "start_date": "2026-09", "end_date": "2027-09", "rank": 2}),
    ]:
        eid = entity(db, "education", name)
        for concept, value in facts.items():
            ingest_fact(db, eid, concept, value, source="Verified source", evidence={}, verified=True,
                        value_type="number" if isinstance(value, int) else "text")
    assert CandidateEngine(db, as_of="2026-10-10").answer_question(question)["answer"] == expected


def test_optional_current_employer_can_be_explicitly_left_blank(tmp_path):
    db = Database(tmp_path / "db")
    eid = entity(db, "person", "Synthetic Candidate")
    ingest_fact(db, eid, "current_company", "", source="Explicit profile choice", evidence={}, verified=True)
    result = CandidateEngine(db).answer_question("Current company", {"required": False})
    assert result["answer"] is None and result["leave_blank"] and result["fact_ids"]
    assert CandidateEngine(db).answer_question("Current company", {"required": True})["root_cause"] == "MISSING_CURRENT_EMPLOYER"


@pytest.mark.parametrize("actual,anticipated,expected", [([], True, "Yes"), ([], False, "No"), ([], None, None), (["2026-11-15"], None, "Yes")])
def test_offer_deadline_compound_question_requires_actual_or_anticipated_evidence(tmp_path, actual, anticipated, expected):
    db = Database(tmp_path / "db")
    eid = entity(db, "person", "Synthetic Candidate")
    ingest_fact(db, eid, "upcoming_offer_deadlines", actual, source="Explicit offer fact", evidence={}, verified=True, value_type="list")
    if anticipated is not None:
        ingest_fact(db, eid, "anticipated_offer_deadline", anticipated, source="Explicit offer policy", evidence={}, verified=True, value_type="boolean")
    question = "Do you have any, or anticipate any upcoming offer deadlines?"
    assert CandidateEngine(db).answer_question(question)["answer"] == expected


@pytest.mark.parametrize("format_name,expected", [("DD/MM/YYYY", "27/09/2027"), ("MM/DD/YYYY", "09/27/2027"), ("ISO date", "2027-09-27")])
def test_exact_day_evidence_formats_without_swapping_locale(format_name, expected):
    assert AnswerFormatResolver.date("2027-09-27", "Date (" + format_name + ")") == (expected, None)


@pytest.mark.parametrize("name", ["University College London (UCL)", "University College London - UCL", "UCL — University College London"])
def test_institution_acronym_decoration_requires_unique_observed_match(name):
    from jobagent.intelligence.formatting import choice
    assert choice("University College London", [{"label": name, "value": "ucl"}])["label"] == name
    assert choice("University College London", [{"label": name, "value": "ucl"}, {"label": "University College London", "value": "duplicate"}])["label"] == "University College London"


def test_graduation_year_is_formatted_before_dropdown_mapping(verified_db):
    field = {"options": [{"label": year, "value": year} for year in ["2026", "2027", "2028"]]}
    result = CandidateEngine(verified_db, as_of="2026-10-10").answer_question("Intended graduation year for degree currently pursuing or completed", field)
    assert result["answer"] == "2027"


@pytest.mark.parametrize("selected,passed,applied,expected", [
    (["Software Engineer"], True, "Software Engineer, New Grad", "Yes"),
    (["Forward Deployed Software Engineer"], True, "Software Engineer, New Grad", None),
    (["Software Engineer"], False, "Software Engineer, New Grad", None),
    ([], True, "Software Engineer, New Grad", None),
    (["Software Engineer", "Forward Deployed Software Engineer"], True, "Software Engineer, New Grad", None),
])
def test_role_affirmation_requires_validated_structured_selection(verified_db, selected, passed, applied, expected):
    related = {"question_id": "role-preference", "canonical_intent": "ROLE_PREFERENCE",
               "answer": "Both roles involve software engineering; my preference is explained here.",
               "selected_roles": selected, "quality": {"passed": passed}}
    question = "By checking this box I confirm I am applying to the role I expressed interest in above."
    result = CandidateEngine(verified_db).answer_question(question, {"type": "checkbox", "_related_answers": [related]}, {"title": applied})
    assert result["canonical_intent"] == "ROLE_CONFIRMATION" and result["answer"] == expected


def test_sync_narrative_preview_requires_generation(verified_db):
    result = CandidateEngine(verified_db).answer_question("Describe a project", {"tag": "textarea"})
    assert result["answer"] is None and result["root_cause"] == "LLM_SETUP_REQUIRED"


def test_narrative_project_request_cannot_fall_back_to_single_skill(verified_db):
    result = CandidateEngine(verified_db).answer_question("Describe a project you developed with Python.", {"tag": "textarea"})
    assert result["answer"] is None and result["root_cause"] == "LLM_SETUP_REQUIRED"


def test_compound_question_keeps_factual_component_when_narrative_needs_provider(verified_db):
    result = CandidateEngine(verified_db).answer_question("Why do you want to join us and what is your current visa type?", {"tag": "textarea"}, {"country": "United Kingdom"})
    assert result["answer"] is None
    assert result["canonical_intents"] == ["MOTIVATION", "VISA"]
    assert result["components"][1]["answer"].startswith("I currently hold a Student visa")


@pytest.mark.parametrize("question", ["High school name", "High school graduation year", "Are you legally authorized to work in the US?"])
def test_untyped_legacy_memory_cannot_bypass_school_level_or_country(verified_db, question):
    engine = CandidateEngine(verified_db)
    result = engine.answer_question(question, {}, {"country": "United States"})
    memory = {"id": "stale-memory", "key": question, "value": "South University", "verification_status": "verified",
              "category": "application_answer", "profile_explicit": True}
    assert engine.confirmed_fallback(question, result, [memory], {})["answer"] is None


def test_course_end_is_not_an_explicit_employment_start_date(tmp_path):
    db = Database(tmp_path / "db")
    eid = entity(db, "education", "Synthetic University")
    for key, value in {"level": "Master's degree", "start_date": "2026-09-28", "end_date": "2027-09-27", "full_time": True}.items():
        ingest_fact(db, eid, key, value, value_type="boolean" if isinstance(value, bool) else "text", source="Verified academic dates", evidence={}, verified=True)
    result = CandidateEngine(db, as_of="2026-10-10").answer_question("When can you start?", {"type": "date"})
    assert result["answer"] is None and result["root_cause"] == "MISSING_ANTICIPATED_START_DATE"


def test_profile_month_availability_edit_updates_knowledge_and_clear_does_not_fall_back(tmp_path):
    from jobagent.application_profile import write_profile
    db = Database(tmp_path / "db")
    entity(db, "person", "Synthetic Candidate")
    write_profile(db, "earliest_start_month", "2027-10", True)
    assert CandidateEngine(db).answer_question("Anticipated start date (MM/YYYY)")["answer"] == "10/2027"
    write_profile(db, "earliest_start_month", "", False)
    result = CandidateEngine(db).answer_question("Anticipated start date (MM/YYYY)")
    assert result["answer"] is None and result["root_cause"] == "MISSING_CONFIRMED_AVAILABILITY"


@pytest.mark.parametrize('current,country,extra,expected', [
    (True, 'United Kingdom', '', True),
    (None, 'United Kingdom', '', False),
    (False, 'United Kingdom', '', False),
    (True, 'Canada', '', False),
    (True, 'United Kingdom', ' without restrictions', False),
    (True, 'United Kingdom', ' for full-time work', False),
    (True, 'United Kingdom', ' until 2028', False),
])
def test_temporary_visa_option_needs_confirmed_current_work_permission_and_scoped_route(tmp_path, current, country, extra, expected):
    db = Database(tmp_path / 'status.sqlite3')
    eid = entity(db, 'immigration', 'Synthetic Student permission')
    for key, value in {'country': country, 'route': 'Student', 'current_right_to_work': current}.items():
        if value is not None:
            ingest_fact(db, eid, key, value, value_type='boolean' if isinstance(value, bool) else 'text',
                        source='Independent confirmed synthetic evidence', evidence={}, verified=True)
    label = 'I have a visa which gives me temporary right to work' + extra
    field = {'type': 'select', 'options': [
        {'label': "I'm a citizen or have permanent residency", 'value': 'citizen'},
        {'label': 'I have a visa which gives me the permanent right to work', 'value': 'permanent'},
        {'label': label, 'value': 'temporary'},
        {'label': "I'd need the employer to sponsor my visa to work in this location", 'value': 'sponsored'},
    ]}
    result = CandidateEngine(db).answer_question('What is your right to work status?', field, {'country': country})
    assert (result['answer'] == label) is expected
    if expected:
        assert result['confidence_level'] == 'DERIVED_HIGH'
        assert len(result['facts_used']) >= 2
    full_time = CandidateEngine(db).answer_question('Are you authorized to work full-time?', {}, {'country': country})
    assert full_time['answer'] is None or full_time['answer'] == 'No'


@pytest.mark.parametrize('route,current,record_country,label,expected', [
    ('Student', True, 'United Kingdom', 'Student/Graduate Visa (with work rights)', True),
    ('Student', True, 'United Kingdom', 'Student or Graduate Visa (with right to work)', True),
    ('Skilled Worker', True, 'United Kingdom', 'Skilled Worker Visa', True),
    ('Student', None, 'United Kingdom', 'Student/Graduate Visa (with work rights)', False),
    ('Student', False, 'United Kingdom', 'Student/Graduate Visa (with work rights)', False),
    (None, True, 'United Kingdom', 'Student/Graduate Visa (with work rights)', False),
    ('Student', True, 'Canada', 'Student/Graduate Visa (with work rights)', False),
    ('Student', True, 'United Kingdom', 'Graduate Visa (with work rights)', False),
    ('Student', True, 'United Kingdom', 'Student and Graduate Visa (with work rights)', False),
    ('Student', True, 'United Kingdom', 'Student/Graduate Visa (with unlimited work rights)', False),
    ('Student', True, 'United Kingdom', 'Student/Graduate Visa (with full-time work rights)', False),
    ('Student', True, 'United Kingdom', 'Student/Graduate Visa (with work rights until 2028)', False),
    ('Student', True, 'United Kingdom', 'Student/Graduate Visa (with current and future work rights)', False),
])
def test_route_enum_requires_both_jurisdiction_and_independent_current_permission(
        tmp_path, route, current, record_country, label, expected):
    db = Database(tmp_path / 'route-enum.sqlite3')
    eid = entity(db, 'immigration', 'Confirmed synthetic permission')
    for key, value in {'country': record_country, 'route': route, 'current_right_to_work': current}.items():
        if value is not None:
            ingest_fact(db, eid, key, value, value_type='boolean' if isinstance(value, bool) else 'text',
                        source='Independent confirmed synthetic evidence', evidence={}, verified=True)
    result = CandidateEngine(db).answer_question('Please select your current UK work authorization status',
        {'type': 'select', 'options': [{'label': 'UK /Irish Citizen', 'value': 'citizen'},
                                     {'label': label, 'value': 'route'},
                                     {'label': 'No work authorization', 'value': 'none'}]},
        {'country': 'United Kingdom'})
    assert (result['answer'] == label) is expected
    if expected:
        assert result['confidence_level'] == 'DERIVED_HIGH'
        assert len(result['facts_used']) >= 3


def test_visa_route_enums_prefer_specific_and_refuse_equal_ambiguity():
    from jobagent.intelligence.engine import confirmed_visa_route_options
    options = [{'label': label} for label in ['Student/Graduate Visa (with work rights)', 'Student Visa']]
    assert confirmed_visa_route_options('Student', options) == ['Student Visa']
    assert len(confirmed_visa_route_options('Student', options + [{'label': 'Student Visa (with work rights)'}])) == 2
