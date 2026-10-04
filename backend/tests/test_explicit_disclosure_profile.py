"""Explicit personal facts stay distinct, editable, and scoped by user choice."""

import pytest

from jobagent.application_profile import (
    application_knowledge,
    memory_superseded,
    profile,
    question_field,
    remember_answer,
    write_profile,
)
from jobagent.core import prepare_application
from jobagent.db import Database, dumps, now, uid
from jobagent.discovery import ingest_job
from jobagent.intelligence.store import Knowledge, entity, ingest_fact, policy


@pytest.fixture
def candidate(tmp_path):
    db = Database(tmp_path / "disclosures.sqlite")
    person = entity(db, "person", "Example candidate")
    return db, person


def application(db, company="Example"):
    job = ingest_job(db, {
        "title": "Engineer", "company": company, "location": "London",
        "url": f"https://jobs.example.test/{company}",
    })[0]
    return {**prepare_application(db, job["id"]), "company": company, "location": "London"}


@pytest.mark.parametrize("key,value", [
    ("gender", "Woman"), ("sex", "Female"), ("pronouns", "she/her/hers"),
    ("sexual_orientation", "Bisexual"), ("ethnicity", "Example ethnicity"),
    ("religion", "Example belief"), ("disability", "Yes"),
    ("neurodivergence", "No"), ("mental_health", "Prefer not to say"),
    ("veteran", "No"), ("adjustments", "Step-free interview access"), ("age", "25"),
])
def test_profile_confirmation_creates_only_the_precise_personal_concept(candidate, key, value):
    db, person = candidate
    policy(db, "personal_disclosures", "leave_blank")
    write_profile(db, key, value, False)
    assert Knowledge(db).fact(person, key) is None
    write_profile(db, key, value, True)
    known = Knowledge(db)
    fact = known.fact(person, key)
    assert fact["value"] == value
    assert fact["source"] == "application_profile:" + key
    assert fact["source_priority"] == 100
    assert fact["sensitivity"] == "personal"
    assert fact["allowed_usage"] == ["application"]
    assert {row["concept"] for row in known.rows} == {key}
    assert known.get_policy("personal_disclosures", {})["value"] == "leave_blank"


@pytest.mark.parametrize("key,old,new", [
    ("pronouns", "she/her/hers", "they/them/theirs"),
    ("disability", "No", "Yes"),
    ("mental_health", "Yes", "Prefer not to say"),
])
def test_profile_edit_draft_clear_and_restore_cannot_revive_old_disclosures(candidate, key, old, new):
    db, person = candidate
    ingest_fact(db, person, key, old, source="Older explicit answer", evidence={}, verified=True)
    write_profile(db, key, new, True)
    assert Knowledge(db).fact(person, key)["value"] == new
    shown = next(row for row in profile(db)["fields"] if row["key"] == key)
    assert shown["value"] == new and shown["state"] == "confirmed"
    write_profile(db, key, old, False)
    assert Knowledge(db).fact(person, key) is None
    write_profile(db, key, "", False)
    assert Knowledge(db).fact(person, key) is None
    shown = next(row for row in profile(db)["fields"] if row["key"] == key)
    assert shown["value"] == "" and shown["state"] == "unknown"
    write_profile(db, key, old, True)
    assert Knowledge(db).fact(person, key)["value"] == old


@pytest.mark.parametrize("question,key", [
    ("Which pronouns do you use?", "pronouns"),
    ("What is your sex assigned at birth?", "sex"),
    ("How would you describe your gender?", "gender"),
    ("Do you identify as neurodivergent?", "neurodivergence"),
    ("Do you have a mental health condition?", "mental_health"),
    ("Do you have a disability?", "disability"),
    ("Please describe your religion or belief", "religion"),
])
def test_inline_question_matches_a_precise_general_profile_concept(question, key):
    assert question_field(question, {"company": "Example", "location": "London"})["key"] == key


def test_inline_reuse_is_required_before_promoting_sensitive_memory(candidate):
    db, person = candidate
    first = application(db)
    second = application(db, "Other")
    question = "Which pronouns do you use?"
    remember_answer(db, first, question, "they/them/theirs")
    assert Knowledge(db).fact(person, "pronouns") is None
    row = db.one("SELECT * FROM browser_answers")
    assert not row["reusable"]
    second_facts, _ = application_knowledge(db, second, db.query("SELECT * FROM facts"))
    assert not second_facts
    remember_answer(db, first, question, "they/them/theirs", reusable=True)
    assert Knowledge(db).fact(person, "pronouns")["value"] == "they/them/theirs"
    row = db.one("SELECT * FROM browser_answers")
    assert row["reusable"]
    assert not memory_superseded(db, question, row["updated_at"])
    write_profile(db, "pronouns", "", False)
    assert memory_superseded(db, question, row["updated_at"])
    second_facts, _ = application_knowledge(db, second, db.query("SELECT * FROM facts"))
    assert not any(f["source"].startswith("browser_answer:") for f in second_facts)


def test_observed_description_can_identify_profile_without_guessing_other_details(candidate):
    db, person = candidate
    app = application(db)
    context = {"description": "Which pronouns do you use?"}
    remember_answer(db, app, "Please select", "they/them/theirs", reusable=True, element=context)
    assert Knowledge(db).fact(person, "pronouns")["value"] == "they/them/theirs"
    assert question_field("If Other, please specify", app) is None
    assert question_field("Describe your work on mental health software", app) is None


def test_skip_does_not_become_a_general_personal_fact(candidate):
    db, person = candidate
    app = application(db)
    remember_answer(db, app, "Which pronouns do you use?", "", reusable=True, skip=True)
    assert Knowledge(db).fact(person, "pronouns") is None


@pytest.mark.parametrize("replacement,confirmed", [("she/her/hers", True), ("", False)])
def test_profile_change_invalidates_answers_using_every_historical_typed_source(candidate, replacement, confirmed):
    db, person = candidate
    app = application(db)
    oldest = ingest_fact(db, person, "pronouns", "they/them/theirs", source="Earlier explicit answer",
                         evidence={}, verified=True, sensitivity="personal")
    write_profile(db, "pronouns", "they/them/theirs", True)
    current = Knowledge(db).fact(person, "pronouns")["id"]
    for fact_id in (oldest, current):
        db.execute("INSERT INTO application_answers VALUES(?,?,?,?,?,?,?)",
                   (uid(), app["id"], "Pronouns", "they/them/theirs", dumps([fact_id]), 1, now()))
        db.execute("INSERT INTO saved_answers VALUES(?,?,?,?,?,?,?)",
                   (uid(), "Pronouns", "they/them/theirs", dumps([fact_id]), 1, now(), now()))
    write_profile(db, "pronouns", replacement, confirmed)
    assert all(not row["verified"] for row in db.query("SELECT verified FROM application_answers"))
    assert all(not row["verified"] for row in db.query("SELECT verified FROM saved_answers"))
    history = {row["fact_id"] for row in db.query("SELECT fact_id FROM fact_history")}
    assert {oldest, current}.issubset(history)
