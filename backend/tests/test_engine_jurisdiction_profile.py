"""Spain and the EU in the Application profile (CPL-07): what a Spanish role asks again and again (the right to work, sponsorship,
the pay in euros) is saved ONCE, per place, and answered from that place's facts only.

The UK fields are untouched and flat; the new ones are a confirmed record of the place (an ``immigration`` entity with its country,
or a salary policy scoped to it), which is what the answer engine reads, so a Spanish vacancy is answered and a British one is
never answered with Spain's rights (and the reverse).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import JOB_ES, JOB_UK, RADIO, ask, engine_for  # noqa: E402
from jobagent.application_profile import BY_KEY, profile, question_field, scope_matches, write_profile  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402

MADRID = {"id": "", "company": "Acme", "location": "Madrid, Spain"}
LONDON = {"id": "", "company": "Acme", "location": "London, UK"}
PARIS = {"id": "", "company": "Acme", "location": "Paris, France"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    return engine_for(tmp_path, "student").db


def fresh(db):
    return CandidateEngine(db, as_of="2026-10-14")


def test_the_fields_exist_in_the_work_eligibility_group_with_their_scope(db):
    for key, scope in (("work_authorization_es", "ES"), ("sponsorship_es", "ES"), ("expected_salary_es", "ES"),
                       ("work_authorization_eu", "EU"), ("sponsorship_eu", "EU")):
        assert BY_KEY[key]["scope"] == scope and BY_KEY[key]["sensitive"] == (key != "expected_salary_es"), key
    assert [f["key"] for f in profile(db)["fields"] if f["key"].endswith(("_es", "_eu"))].__len__() == 5
    assert BY_KEY["work_authorization_uk"]["scope"] == "UK" and BY_KEY["expected_salary_uk"]["scope"] == "UK"  # unchanged


@pytest.mark.parametrize("location,spain,eu,uk", [("Madrid, Spain", True, True, False), ("Barcelona", True, True, False),
                                                  ("Valencia, España", True, True, False), ("London", False, False, True),
                                                  ("Paris, France", False, True, False), ("Berlin", False, True, False),
                                                  ("New York", False, False, False), ("Remote", False, False, False),
                                                  ("Madrid; Paris", False, True, False), ("", False, False, False)])
def test_which_application_each_scope_applies_to(location, spain, eu, uk):
    application = {"location": location, "company": "Acme"}
    assert (scope_matches("ES", application), scope_matches("EU", application), scope_matches("UK", application)) == (spain, eu, uk)


def test_a_spanish_question_finds_the_spanish_field_only_on_a_spanish_application():
    for label, key in (("¿Tienes autorización para trabajar en España?", "work_authorization_es"),
                       ("Pretensiones salariales", "expected_salary_es"), ("Expectativas salariales", "expected_salary_es"),
                       ("¿Necesitas patrocinio de visado para trabajar en España?", "sponsorship_es")):
        assert question_field(label, MADRID)["key"] == key, label
        assert (question_field(label, LONDON) or {}).get("key") != key, label
    assert question_field("Salary expectations", LONDON)["key"] == "expected_salary_uk"
    assert question_field("Salary expectations", MADRID)["key"] == "expected_salary_es"


def test_nothing_is_answered_before_the_user_has_confirmed_it(db):
    assert ask(fresh(db), "¿Tienes autorización para trabajar en España?", JOB_ES, **RADIO)["answer"] is None
    write_profile(db, "work_authorization_es", "Yes", False)  # saved as a draft, not confirmed
    assert ask(fresh(db), "¿Tienes autorización para trabajar en España?", JOB_ES, **RADIO)["answer"] is None


def test_a_confirmed_spanish_right_and_sponsorship_answer_a_spanish_role_and_nothing_else(db):
    write_profile(db, "work_authorization_es", "Yes", True)
    write_profile(db, "sponsorship_es", "No", True)
    engine = fresh(db)
    right, sponsor = (ask(engine, "¿Tienes autorización para trabajar en España?", JOB_ES, **RADIO),
                      ask(engine, "¿Necesitas patrocinio de visado para trabajar en España?", JOB_ES, **RADIO))
    assert (right["answer"], sponsor["answer"]) == ("Yes", "No") and right["verified"] and sponsor["verified"]
    assert right["fact_ids"] and sponsor["fact_ids"]
    # asked of a British vacancy, or about another country, it is still not answered with Spain's rights...
    assert ask(engine, "Are you authorised to work in Spain?", JOB_UK, **RADIO)["answer"] == "Yes"  # the question NAMES Spain
    assert ask(engine, "Are you authorised to work in France?", JOB_ES, **RADIO)["answer"] is None
    assert ask(engine, "Are you authorised to work in Portugal?", JOB_ES, **RADIO)["answer"] is None
    # ... and the UK facts are still the UK's
    assert ask(engine, "Are you legally authorized to work in the United Kingdom?", JOB_UK, **RADIO)["answer"] == "Yes"
    assert ask(engine, "Are you legally authorized to work in the country where this job is located?", JOB_ES, **RADIO)["answer"] == "Yes"
    assert ask(engine, "Are you legally authorized to work in the country where this job is located?", JOB_UK, **RADIO)["answer"] == "Yes"


def test_no_sponsorship_needed_is_no_sponsorship_now_nor_later(db):
    write_profile(db, "sponsorship_es", "No", True)
    engine = fresh(db)
    for label in ("Will you now or in the future require sponsorship to work in Spain?", "Do you currently require sponsorship to work in Spain?",
                  "Will you ever require sponsorship to work in Spain?", "I do not require sponsorship to work in Spain"):
        assert ask(engine, label, JOB_ES, **RADIO)["answer"] == ("Yes" if label.startswith("I do not") else "No"), label
    write_profile(db, "sponsorship_es", "Yes", True)
    engine = fresh(db)
    assert ask(engine, "Will you now or in the future require sponsorship to work in Spain?", JOB_ES, **RADIO)["answer"] == "Yes"
    assert ask(engine, "Do you currently require sponsorship to work in Spain?", JOB_ES, **RADIO)["answer"] is None  # now OR later: not which


def test_a_changed_or_cleared_answer_is_the_new_one_and_never_a_contradiction(db):
    write_profile(db, "work_authorization_es", "Yes", True)
    assert ask(fresh(db), "Are you authorised to work in Spain?", JOB_ES, **RADIO)["answer"] == "Yes"
    write_profile(db, "work_authorization_es", "No", True)
    assert ask(fresh(db), "Are you authorised to work in Spain?", JOB_ES, **RADIO)["answer"] == "No"
    write_profile(db, "work_authorization_es", "", False)
    assert ask(fresh(db), "Are you authorised to work in Spain?", JOB_ES, **RADIO)["answer"] is None
    write_profile(db, "work_authorization_es", "Yes", True)
    assert ask(fresh(db), "Are you authorised to work in Spain?", JOB_ES, **RADIO)["answer"] == "Yes"


def test_the_eu_is_answered_from_a_record_of_exactly_the_eu(db):
    write_profile(db, "work_authorization_es", "Yes", True)
    engine = fresh(db)
    for label in ("Are you authorized to work in the European Union?", "¿Tienes derecho a trabajar en la Unión Europea?"):
        assert ask(engine, label, JOB_ES, **RADIO)["root_cause"] == "MISSING_TARGET_WORK_COUNTRY", label  # Spain is not the EU
    write_profile(db, "work_authorization_eu", "Yes", True)
    engine = fresh(db)
    for label in ("Are you authorized to work in the European Union?", "Do you have the right to work in the EU?",
                  "¿Tienes derecho a trabajar en la Unión Europea?"):
        assert ask(engine, label, JOB_ES, **RADIO)["answer"] == "Yes", label
    assert ask(engine, "Are you authorized to work in Europe?", JOB_ES, **RADIO)["answer"] is None  # Europe is not the EU
    assert ask(engine, "Are you authorized to work in the EEA?", JOB_ES, **RADIO)["answer"] is None


def test_the_spanish_salary_is_one_annual_figure_in_euros_for_spanish_roles_only(db):
    write_profile(db, "expected_salary_es", "38000", True)
    engine = fresh(db)
    assert ask(engine, "Pretensiones salariales", JOB_ES)["answer"] == "38000"
    assert ask(engine, "Pretensiones salariales (bruto anual, EUR)", JOB_ES)["answer"] == "38000"
    assert ask(engine, "Expected salary", JOB_ES)["answer"] == "38000"
    assert ask(engine, "Salary expectations", JOB_UK)["answer"] == "60000"  # the UK figure, in pounds: unchanged
    for label in ("Pretensiones salariales (en dólares)", "Salario mensual", "Rango salarial", "Pretensiones salariales (GBP)"):
        assert ask(engine, label, JOB_ES)["answer"] is None, label
    assert ask(engine, "Expected salary", {**JOB_ES, "country": "France", "location": "Paris"})["answer"] is None
    write_profile(db, "expected_salary_es", "", False)
    assert ask(fresh(db), "Pretensiones salariales", JOB_ES)["answer"] is None


@pytest.mark.parametrize("key,value", [("work_authorization_es", "Maybe"), ("sponsorship_es", "Quizá"), ("work_authorization_eu", "yes"),
                                       ("expected_salary_es", "38,000 EUR"), ("expected_salary_es", "NaN")])
def test_a_value_the_field_cannot_hold_is_refused_before_anything_is_saved(db, key, value):
    with pytest.raises(ValueError):
        write_profile(db, key, value, True)
    assert not db.query("SELECT 1 FROM facts WHERE source=?", ("application_profile:" + key,))


def test_the_profile_lists_the_new_fields_with_their_confirmed_state(db):
    write_profile(db, "work_authorization_es", "Yes", True)
    fields = {f["key"]: f for f in profile(db)["fields"]}
    assert fields["work_authorization_es"]["value"] == "Yes" and fields["work_authorization_es"]["state"] == "confirmed"
    assert fields["work_authorization_eu"]["value"] == "" and fields["work_authorization_eu"]["state"] == "unknown"
