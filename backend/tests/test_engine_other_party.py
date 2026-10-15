"""Whose details does a question ask for? The answer engine holds the applicant's facts and nobody else's.

'Emergency contact phone number', 'Mother's maiden name', 'Company name', 'Spouse name', 'Reference email', 'Employer
location' and 'Spouse nationality' were answered with the applicant's own phone, name, city and nationality: the engine read
the head noun ('phone', 'name') and never asked WHOSE. The subject is now read in ``intelligence/semantics.py`` (from the
question and from the heading, group and field names around it), so Auto Apply and the browser assistant, which both ask
the engine, stop filling them; the assistant's own resolver guard it replaced is gone, and the cross-check below proves it
covered nothing the engine does not."""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import seed_candidate  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402
from jobagent.intelligence.semantics import interpret  # noqa: E402
from jobagent.intelligence.store import entity, ingest_fact  # noqa: E402

JOB = {"country": "United Kingdom", "company": "Acme", "location": "London, UK"}
ABOUT_SOMEONE_ELSE = [
    ("text", "Emergency contact name"), ("tel", "Emergency contact phone number"), ("email", "Reference email"),
    ("text", "Referee 1 - Full name"), ("text", "Current manager's name"), ("email", "Supervisor email"),
    ("text", "Spouse name"), ("tel", "Next of kin phone"), ("text", "Parent or guardian name"), ("text", "Recruiter name"),
    ("text", "Hiring manager name"), ("text", "Mother's maiden name"), ("text", "Company name"),
    ("email", "Email address of your previous employer"), ("url", "Reference's LinkedIn"), ("text", "Employer location"),
    ("text", "Spouse nationality"), ("text", "Company city"), ("text", "Emergency contact address"),
    ("tel", "Partner's phone number"), ("text", "Father's name"), ("text", "Sibling's name"), ("text", "Line manager phone"),
    ("text", "Witness name"), ("text", "Beneficiary name"), ("text", "Dependant's date of birth"),
    ("text", "Position name"), ("text", "Project name"), ("text", "Organisation address"), ("tel", "Interviewer phone"),
    # Spanish
    ("text", "Nombre del contacto de emergencia"), ("tel", "Teléfono del contacto de emergencia"),
    ("text", "Nombre de la empresa"), ("text", "Nombre de la referencia"), ("email", "Correo de la referencia"),
    ("text", "Nombre del cónyuge"), ("text", "Ciudad de la empresa"), ("text", "Nombre del puesto"),
    ("tel", "Teléfono del responsable"), ("text", "Nombre de tu madre"),
    ("text", "Nombre del jefe directo"), ("email", "Correo electrónico del supervisor"), ("text", "Nombre de usuario")]
THE_APPLICANT = [
    ("text", "First name", "Test"), ("text", "Full name", "Test Candidate"), ("text", "Contact name", "Test Candidate"),
    ("email", "Email", "test@example.test"), ("email", "Personal email", "test@example.test"),
    ("tel", "Phone", "+441234567890"), ("url", "LinkedIn Profile", "https://example.test/linkedin"),
    ("text", "Your signature (type your full name)", "Test Candidate"), ("text", "Nationality", "Spanish"),
    ("text", "City", "London"), ("text", "Country", "United Kingdom"), ("url", "GitHub", "https://example.test/github"),
    ("text", "Nombre", "Test"), ("text", "Apellidos", "Candidate"), ("email", "Correo electrónico", "test@example.test"),
    ("tel", "Teléfono móvil", "+441234567890"), ("text", "País de residencia", "United Kingdom"),
    ("text", "Nombre completo", "Test Candidate"), ("text", "Nacionalidad", "Spanish")]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    return seed_candidate(Database(tmp_path / "meridian.sqlite3"))


@pytest.fixture
def engine(db):
    return CandidateEngine(db, as_of="2026-10-14")


def ask(engine, label, kind="text", job=JOB, **field):
    return engine.answer_question(label, {"type": kind, **field}, dict(job))


@pytest.mark.parametrize("kind,label", ABOUT_SOMEONE_ELSE)
def test_a_question_about_someone_else_never_gets_the_applicants_details(engine, kind, label):
    result = ask(engine, label, kind)
    assert result["answer"] is None and result["verified"] is False and result["form_value"] is None, result
    assert result["canonical_intent"] == "OTHER_PARTY" and result["root_cause"] == "MISSING_OTHER_PARTY_DETAILS"
    assert result["fact_ids"] == [] and result["leave_blank"] is False
    assert result["reason"] == result["explanation"] and "other than you" in result["reason"]
    assert interpret(label, {"type": kind})["subject"] == "other_party"


@pytest.mark.parametrize("kind,label,answer", THE_APPLICANT)
def test_a_question_about_the_applicant_is_still_answered(engine, kind, label, answer):
    result = ask(engine, label, kind)
    assert result["answer"] == answer and result["verified"] is True, result
    assert interpret(label, {"type": kind})["subject"] == "applicant"


def test_the_form_says_whose_details_in_its_heading_group_and_field_names(engine):
    """The label is just 'Name' or 'Email': the heading above it, its group and the field's own names say whose."""
    cases = [({"section_label": "Emergency contact"}, "Name", None), ({"name": "emergency_contact_email"}, "Email", None),
             ({"context": "Referee details"}, "Phone", None), ({"group": "spouse"}, "Name", None),
             ({"autocomplete": "organization"}, "Name", None), ({"id": "manager_name"}, "Name", None),
             ({"section_label": "Personal information"}, "Name", "Test Candidate"),
             ({"name": "job_application[email]"}, "Email", "test@example.test"),
             ({"section_label": "Application details", "autocomplete": "name"}, "Name", "Test Candidate")]
    for context, label, answer in cases:
        assert ask(engine, label, **context)["answer"] == answer, (context, label)


@pytest.mark.parametrize("hint", [
    "Used by the recruiter to contact you", "The recruiter will contact you on this number", "Contact details for our recruiters",
    "So that the hiring manager can reach you", "Our recruiters and partner agencies never see it", "Shared with your future manager and team",
    "The email our recruiter will use", "We will call you; a recruiter may text you"])
def test_W5_a_hint_that_mentions_a_person_is_no_evidence_that_the_field_asks_for_theirs(engine, hint):
    """A help text for the applicant's OWN email, phone or name that names a recruiter or a manager ('Used by the recruiter to contact you')
    is not 'the recruiter's contact': a person word is evidence only when it is tied to the attribute asked ('Your manager's email',
    'Email for your manager', 'Referee details')."""
    for label, kind, answer in (("Email", "email", "test@example.test"), ("Phone", "tel", "+441234567890"), ("Name", "text", "Test Candidate")):
        assert ask(engine, label, kind, description=hint)["answer"] == answer, (label, hint)


@pytest.mark.parametrize("hint", ["Your emergency contact's name", "Email for your manager", "Referee details", "Name of your supervisor",
                                  "Referee's email address", "Details of your line manager", "Enter your manager's details here"])
def test_W5_a_hint_that_ties_a_person_to_the_attribute_still_names_somebody_else(engine, hint):
    for label, kind in (("Email", "email"), ("Phone", "tel"), ("Name", "text")):
        result = ask(engine, label, kind, description=hint)
        assert result["answer"] is None and result["canonical_intent"] == "OTHER_PARTY", (label, hint, result["answer"])


def test_the_applicants_own_other_names_are_never_the_full_name(engine, db):
    """'Middle name' is not 'Test Candidate'. The profile holds one full, one first and one last name; another name is
    answered only when that very fact exists."""
    for label in ("Middle name", "Maiden name", "Previous name", "Former name", "Nickname", "Other names", "Name at birth",
                  "Birth name", "Segundo nombre", "Apodo", "Nombre de soltera", "Apellido de soltera", "Primer apellido",
                  "Segundo apellido", "Apellidos y nombre", "Nombre legal"):
        result = ask(engine, label)
        assert result["answer"] is None and result["canonical_intent"] in {"IDENTITY", "OTHER"}, (label, result["answer"])
    person = entity(db, "person", "Test Candidate")
    ingest_fact(db, person, "middle_name", "Quinn", source="Synthetic verified test record", evidence={"excerpt": "Quinn"},
                verified=True)
    engine = CandidateEngine(db, as_of="2026-10-14")
    assert ask(engine, "Middle name")["answer"] == "Quinn" and ask(engine, "Segundo nombre")["answer"] == "Quinn"
    assert ask(engine, "Maiden name")["answer"] is None and ask(engine, "Mother's maiden name")["answer"] is None


def test_the_place_of_birth_is_not_the_place_of_residence(engine):
    """'Country of birth' used to be answered with the country the candidate LIVES in (a Spanish national in London)."""
    for label in ("Country of birth", "City of birth", "Place of birth", "Where were you born?", "Birthplace", "País de nacimiento",
                  "Lugar de nacimiento", "Ciudad de nacimiento"):
        result = ask(engine, label)
        assert result["answer"] is None and result["canonical_intent"] == "IDENTITY", (label, result["answer"])
    assert ask(engine, "Country of residence")["answer"] == "United Kingdom"


def test_a_referee_is_not_the_person_who_referred_the_applicant(engine):
    job = {**JOB, "referrer": "Jane Doe"}
    assert ask(engine, "Referred by (name)", job=job)["answer"] == "Jane Doe"
    assert ask(engine, "Employee referral", job=job)["answer"] == "Jane Doe"
    for label in ("Referee 1 - Full name", "Referee details", "Name of referee"):
        assert ask(engine, label, job=job)["answer"] is None, label


def test_an_employer_question_asks_for_the_employer_and_a_persons_employer_is_not_theirs(engine, db):
    person = entity(db, "experience", "Acme Studio")
    ingest_fact(db, person, "ongoing", True, value_type="boolean", source="Synthetic verified test record",
                evidence={"excerpt": "ongoing"}, verified=True)
    ingest_fact(db, person, "role", "Developer", source="Synthetic verified test record", evidence={"excerpt": "Developer"},
                verified=True)
    engine = CandidateEngine(db, as_of="2026-10-14")
    assert ask(engine, "Current employer")["answer"] == "Acme Studio"
    assert ask(engine, "Empresa actual")["answer"] == "Acme Studio"
    for label, context in (("Current employer", {"section_label": "Referee details"}), ("Referee's current employer", {}),
                           ("Manager's current company", {})):
        assert ask(engine, label, **context)["answer"] is None, (label, context)


def test_the_candidate_who_describes_their_experience_is_still_asked_for_a_narrative(engine):
    for label in ("Describe your experience working with managers", "Tell us about your relationship with your manager"):
        assert interpret(label, {"type": "textarea", "tag": "textarea"})["subject"] == "applicant", label


def test_a_saved_answer_for_the_very_question_is_reused_and_a_profile_alias_never_is(engine):
    """'Emergency contact phone number' typed once is still the user's answer to that question: but 'Name' under the
    heading 'Emergency contact' must not find the applicant's own name through the Profile's alias for 'name'."""
    aliased = {"id": "f1", "key": "full_name", "value": "Test Candidate", "category": "identity", "source": "application_profile:full_name",
               "profile_explicit": True, "profile_aliases": ["name", "full name"], "verification_status": "verified",
               "answer_priority": 1}
    saved = {"id": "f2", "key": "emergency contact phone number", "value": "+34600000000", "category": "application_answer",
             "source": "browser_answer:a", "profile_explicit": True, "verification_status": "verified", "answer_priority": 3}
    same_key_as_the_applicants = {"id": "f3", "key": "name", "value": "Test Candidate", "category": "application_answer",
                                  "source": "browser_answer:b", "profile_explicit": True, "verification_status": "verified",
                                  "answer_priority": 3}
    facts = [aliased, saved, same_key_as_the_applicants]
    asked = ask(engine, "Emergency contact phone number", "tel")
    assert engine.confirmed_fallback("Emergency contact phone number", asked, facts, {})["answer"] == "+34600000000"
    heading = ask(engine, "Name", section_label="Emergency contact")
    assert heading["canonical_intent"] == "OTHER_PARTY"
    assert engine.confirmed_fallback("Name", heading, facts, {})["answer"] is None
    assert engine.confirmed_fallback("Emergency contact name", ask(engine, "Emergency contact name"), facts, {})["answer"] is None


# --------------------------------------------------------------------------------------------- the guard it replaced
OLD_GUARD = re.compile(
    r"\b(?:emergency|next of kin|kin|referees?|references?|referrers?|spouse|husband|wife|parents?|guardian|mother|father|"
    r"mother s|father s|siblings?|relatives?|supervisor|line manager|manager|managers|recruiter|interviewer|employer|"
    r"company|organi[sz]ation|business|colleague|contact person|signatory|witness|beneficiary|dependants?|dependents?)\b")
OLD_NAMES = re.compile(r"\b(?:middle|maiden|nick|alias|former|previous|prior|birth|other) ?names?\b|\bmaiden\b|\bnee\b")
PERSONAL = {"IDENTITY", "CONTACT", "ADDRESS", "LINKEDIN", "GITHUB", "PORTFOLIO", "LOCATION", "NATIONALITY"}
QUESTIONS = ["Name", "Full name", "First name", "Last name", "Email", "Phone", "Phone number", "Mobile", "Address", "City",
             "Country", "Location", "LinkedIn", "GitHub", "Portfolio", "Nationality", "Date of birth", "Contact name",
             "Emergency contact name", "Reference email", "Company name", "Middle name", "Maiden name", "Employer location",
             "Spouse nationality", "Manager's phone", "Your email address", "Personal website"]
CONTEXTS = [{}, {"section_label": "Emergency contact"}, {"section_label": "Referee details"}, {"name": "emergency_contact_email"},
            {"section_label": "Personal information"}, {"context": "Reference 1"}, {"group": "spouse"}, {"autocomplete": "organization"},
            {"id": "company_name"}, {"name": "job_application[phone]"}, {"section_label": "Employment history"}]


def old_guard_blocks(question, intent, field):
    """What ``FieldResolver.guard_other_party`` did: a personal fact is not offered when the question, its heading, group or
    names mention somebody else."""
    if intent not in PERSONAL:
        return False
    parts = [question, *(str(field.get(key) or "") for key in ("section_label", "context", "group", "name", "id", "autocomplete"))]
    text = re.sub(r"[_\-./\[\]()':]+", " ", unicodedata.normalize("NFKC", " ".join(parts)).casefold())
    return bool(OLD_GUARD.search(text) or OLD_NAMES.search(text))


def test_the_engine_refuses_everything_the_resolvers_guard_refused(engine):
    """The guard of the assistant's resolver was deleted: over every label x context, wherever it would have blocked a
    personal fact the engine now gives no answer by itself (and nothing it did not block is blocked for a person's
    own question: those are the differences, listed, all about the applicant's own details under a heading)."""
    leaked, refused_more = [], []
    for question in QUESTIONS:
        for context in CONTEXTS:
            field = {"type": "text", "tag": "input", "role": "textbox", **context}
            intent = interpret(question, {})["intent"]  # what the engine read before it looked at the form around the label
            result = engine.answer_question(question, field, dict(JOB))
            blocked = old_guard_blocks(question, intent, field)
            if blocked and result["answer"] is not None:
                leaked.append((question, context, result["answer"]))
            if not blocked and result["answer"] is None and intent in PERSONAL \
                    and result["canonical_intent"] != "OTHER_PARTY" and question not in {"Address", "Middle name", "Maiden name",
                                                                                         "Date of birth", "Portfolio",
                                                                                         "Personal website"}:
                refused_more.append((question, context))
    assert not leaked, leaked
    # The one deliberate difference: a city, a country or a place under 'Employment history' is that job's, never where the
    # applicant lives (the heading names no one but the entry of a history: ``test_engine_history.py``).
    history = {(q, c["section_label"]) for q, c in refused_more if c.get("section_label") == "Employment history"}
    assert history == {("City", "Employment history"), ("Country", "Employment history"), ("Location", "Employment history")}
    for question, heading in history:
        refusal = engine.answer_question(question, {"type": "text", "tag": "input", "role": "textbox", "section_label": heading},
                                         dict(JOB))
        assert refusal["answer"] is None and refusal["root_cause"] == "MISSING_HISTORY_ENTRY", (question, refusal)
    assert not [(q, c) for q, c in refused_more if c.get("section_label") != "Employment history"], refused_more
