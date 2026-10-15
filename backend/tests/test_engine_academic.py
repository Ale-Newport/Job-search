"""Yes/no questions about studies: every facet asked must be confirmed by ONE record, and a date question is a RELATION
(``intelligence/academic.py``).

'Do you have a Master's degree in Mathematics?', 'a degree in Medicine', 'Are you studying at Harvard?', 'a degree from a US
university' and 'an accredited engineering qualification (CEng)' were answered 'Yes' from any master's record (or from the
degree in progress), and 'Do you hold a Master's degree?' from a master's still being studied. 'Will you have graduated by 2027?',
'before 2028', 'since 2020' and 'in 2025 or 2026' compared the YEAR FOR EQUALITY with the first year of the question.

Asked of four candidates: the Student (BSc finished 2026, MSc in progress to 2027-09-27), a graduate with a BSc (2026) only, one
with both degrees finished, and the Student with every date at month precision. Today is 2026-10-14.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import JOB_UK, RADIO, ask, education_engine  # noqa: E402
from jobagent.intelligence import academic  # noqa: E402

KINDS = ("student", "citizen", "graduate", "months")
Y, N = "Yes", "No"


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    path = tmp_path_factory.mktemp("academic")
    return {kind: education_engine(path, kind) for kind in KINDS}


def row(engines, label):
    return tuple(ask(engines[kind], label, JOB_UK, **RADIO)["answer"] for kind in KINDS)


#                                                                          student citizen graduate months
DEGREES = [
    ("Do you have a Master's degree in Mathematics?",                      (None,   None,   None,    None)),
    ("Do you hold a degree in Medicine?",                                  (None,   None,   None,    None)),
    ("Do you have a Bachelor's degree in Nursing?",                        (None,   None,   None,    None)),
    ("Do you have a doctorate in Physics?",                                (None,   None,   None,    None)),
    ("Do you have a PhD?",                                                 (None,   None,   None,    None)),
    ("Do you hold a Master's degree?",                                     (None,   None,   Y,       None)),
    ("Do you have a Master's degree?",                                     (None,   None,   Y,       None)),
    ("Do you have a postgraduate degree?",                                 (None,   None,   Y,       None)),
    ("Have you completed a Master's degree?",                              (None,   None,   Y,       None)),
    ("Do you have a Master's degree in Artificial Intelligence?",          (None,   None,   Y,       None)),
    ("Do you have a Master's degree in Computer Science?",                 (None,   None,   None,    None)),
    ("Do you have a degree in Computer Science?",                          (Y,      Y,      Y,       Y)),
    ("Do you have a Bachelor's degree in Computer Science?",               (Y,      Y,      Y,       Y)),
    ("Do you have a degree?",                                              (Y,      Y,      Y,       Y)),
    ("Do you have a Bachelor's degree from North University?",             (Y,      Y,      Y,       Y)),
    ("Do you have a degree in Computer Science or a related field?",       (Y,      Y,      Y,       Y)),
    ("Do you have a degree in Mathematics, Physics or Computer Science?",  (Y,      Y,      Y,       Y)),
    ("Do you have a degree in a related field?",                           (None,   None,   None,    None)),
    ("Do you have a degree from South University?",                        (None,   None,   None,    None)),
    # what no record establishes: a country, an accreditation, a grade
    ("Do you have a degree from a US university?",                         (None,   None,   None,    None)),
    ("Do you have a degree from a UK university?",                         (None,   None,   None,    None)),
    ("Do you hold an accredited engineering qualification (CEng)?",        (None,   None,   None,    None)),
    ("Do you have a first class honours degree?",                          (None,   None,   None,    None)),
    ("Do you have a degree from a Russell Group university?",              (None,   None,   None,    None)),
    # studying: in progress, and where
    ("Are you currently a student?",                                       (Y,      None,   None,    Y)),
    ("Are you pursuing a Master's degree in Artificial Intelligence?",     (Y,      None,   None,    Y)),
    ("Are you studying at South University?",                              (Y,      None,   None,    Y)),
    ("Are you studying at Harvard University?",                            (None,   None,   None,    None)),
    ("Are you currently a student at Oxford University?",                  (None,   None,   None,    None)),
    ("Are you currently enrolled at a UK university?",                     (None,   None,   None,    None)),
]


@pytest.mark.parametrize("label,expected", DEGREES)
def test_a_degree_question_is_yes_only_when_one_record_confirms_everything_it_asks(engines, label, expected):
    assert row(engines, label) == expected, label


def test_a_degree_in_progress_is_not_a_degree_held(engines):
    """The case the review found: 'Do you hold a Master's degree?' was 'Yes' while the MSc was still being studied."""
    result = ask(engines["student"], "Do you hold a Master's degree?", JOB_UK, **RADIO)
    assert result["answer"] is None and result["root_cause"] == "MISSING_COMPLETED_QUALIFICATION", result
    detail = ask(engines["student"], "Do you have a degree from a US university?", JOB_UK, **RADIO)
    assert detail["root_cause"] == "MISSING_EDUCATION_EVIDENCE" and "country of institution" in detail["explanation"]


def test_a_confirmed_degree_is_derived_from_its_record_and_cites_it(engines):
    result = ask(engines["graduate"], "Do you have a Master's degree in Artificial Intelligence?", JOB_UK, **RADIO)
    assert result["confidence_level"] == "DERIVED_HIGH" and result["verified"] is False and result["fact_ids"]
    assert result["rules_used"] == ["education_facets_confirmed"]


#                                                                           student citizen graduate months
GRADUATION = [
    # 'by' / 'no later than' / 'until': on or before the end of what is named
    ("Will you have graduated by 2027?",                                    (Y,      Y,      Y,       Y)),
    ("Will you have graduated by 2026?",                                    (N,      Y,      Y,       N)),
    ("Have you graduated by 2026?",                                         (Y,      Y,      Y,       Y)),
    ("Will you graduate no later than 2029?",                               (Y,      Y,      Y,       Y)),
    ("Will you have graduated by 30 June 2028?",                            (Y,      Y,      Y,       Y)),
    ("Will you have graduated by 30 September 2027?",                       (Y,      Y,      Y,       Y)),
    ("Will you have graduated by September 2027?",                          (Y,      Y,      Y,       Y)),
    ("Will you have graduated by June 2027?",                               (N,      Y,      Y,       N)),
    # 'before' / 'after' / 'since'
    ("Have you graduated before 2028?",                                     (Y,      Y,      Y,       Y)),
    ("Have you graduated before 2025?",                                     (N,      N,      N,       N)),
    ("Have you graduated after 2024?",                                      (Y,      Y,      Y,       Y)),
    ("Have you graduated since 2020?",                                      (Y,      Y,      Y,       Y)),
    ("Have you graduated since 2026?",                                      (Y,      Y,      None,    Y)),
    # 'in' / 'between' / 'or'
    ("Will you graduate in 2027?",                                          (Y,      N,      N,       Y)),
    ("Are you graduating in 2027?",                                         (Y,      N,      N,       Y)),
    ("Are you graduating in 2025 or 2026?",                                 (N,      Y,      Y,       N)),
    ("Will you graduate between 2026 and 2027?",                            (Y,      Y,      None,    Y)),
    ("Did you graduate in 2026?",                                           (Y,      Y,      None,    Y)),
    ("Have you graduated in 2025?",                                         (N,      N,      None,    N)),
    # Spanish
    ("¿Te habrás graduado antes de 2028?",                                  (Y,      Y,      Y,       Y)),
    ("¿Te graduarás en 2027?",                                              (Y,      N,      N,       Y)),
    ("¿Te has graduado desde 2020?",                                        (Y,      Y,      Y,       Y)),
]


@pytest.mark.parametrize("label,expected", GRADUATION)
def test_a_graduation_question_is_a_relation_between_the_graduation_date_and_the_date_asked(engines, label, expected):
    assert row(engines, label) == expected, label


@pytest.mark.parametrize("label", [
    "Will you have graduated by early 2027?", "Will you have graduated by the start of 2027?", "Will you have graduated by 2027 or 2028 or 2029 or 2030?",
    "Will you graduate around 2027?", "Will you graduate about 2027?", "Will you have graduated by late 2027?"])
def test_a_relation_that_cannot_be_read_with_certainty_is_the_users(engines, label):
    """A date with a margin ('early 2027', 'around 2027', 'the start of 2027') is a word the plain forms of a graduation question do
    not have: the plain-question gate (``plain.py``) leaves it to the user and names the word (it used to be a relation the
    arithmetic could not read: the same answer, a different code)."""
    for kind in KINDS:
        result = ask(engines[kind], label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["root_cause"] == "PLAIN_QUESTION_REQUIRED", (kind, label, result)
        assert result["missing_fact"]["residue"], result


def test_month_precision_never_invents_a_day(engines):
    """The MSc ends in 2027-09 for the 'months' candidate: 'by 15 September 2027' could be either side of it."""
    assert ask(engines["months"], "Will you have graduated by 15 September 2027?", JOB_UK, **RADIO)["answer"] is None
    assert ask(engines["months"], "Will you have graduated by 30 September 2027?", JOB_UK, **RADIO)["answer"] == Y
    assert ask(engines["months"], "Will you have graduated before 1 September 2027?", JOB_UK, **RADIO)["answer"] == N
    assert ask(engines["student"], "Will you have graduated by 15 September 2027?", JOB_UK, **RADIO)["answer"] == N  # 2027-09-27


def test_a_graduation_answer_cites_the_records_it_read_and_is_derived(engines):
    result = ask(engines["student"], "Will you have graduated by 2027?", JOB_UK, **RADIO)
    assert result["confidence_level"] == "DERIVED_HIGH" and result["fact_ids"] and result["rules_used"] == ["graduation_relation:by"]
    unread = ask(engines["graduate"], "Will you graduate between 2026 and 2027?", JOB_UK, **RADIO)
    assert unread["answer"] is None and unread["root_cause"] == "MISSING_GRADUATION_RELATION_EVIDENCE"


def test_a_graduation_date_asked_for_is_still_the_date(engines):
    assert ask(engines["student"], "Graduation date", JOB_UK, type="text")["answer"] == "27 September 2027"
    assert ask(engines["student"], "Expected graduation year", JOB_UK, type="text")["answer"] == "2027"


# ------------------------------------------------------------------------------------------- the parts, without an engine
@pytest.mark.parametrize("text,relation,lows", [
    ("Will you have graduated by 2027?", "by", [date(2027, 1, 1)]), ("before 2028", "before", [date(2028, 1, 1)]),
    ("graduated since 2020", "since", [date(2020, 1, 1)]), ("in 2025 or 2026", "either", [date(2025, 1, 1), date(2026, 1, 1)]),
    ("between 2026 and 2027", "between", [date(2026, 1, 1), date(2027, 1, 1)]), ("no later than 30 June 2028", "by", [date(2028, 6, 30)]),
    ("by the end of 2027", "by", [date(2027, 1, 1)]), ("antes de 2028", "before", [date(2028, 1, 1)]),
    ("desde junio de 2020", "since", [date(2020, 6, 1)]), ("graduating in 2027", "in", [date(2027, 1, 1)])])
def test_the_relation_a_question_states(text, relation, lows):
    parsed = academic.relation_of(text)
    assert parsed["relation"] == relation and [low for low, _ in parsed["bounds"]] == lows, parsed


@pytest.mark.parametrize("text", ["no dates here", "by early 2027", "in 2020 or 2021 or 2022 or 2023", "by 31 February 2027",
                                  # a season, a half, a quarter, a term: a stretch of the year, never the year (RS-09)
                                  "by summer 2027", "by spring 2027", "in the first half of 2027", "by Q2 2027", "by the first semester of 2027",
                                  "antes del verano de 2027", "hasta el primer trimestre de 2027", "by the second term of 2027"])
def test_a_text_without_a_certain_date_has_no_relation(text):
    assert academic.relation_of(text) is None


@pytest.mark.parametrize("relation,bound,graduated,verdict", [
    ("by", (date(2027, 1, 1), date(2027, 12, 31)), (date(2027, 9, 27), date(2027, 9, 27)), True),
    ("by", (date(2027, 6, 1), date(2027, 6, 30)), (date(2027, 9, 1), date(2027, 9, 30)), False),
    ("by", (date(2027, 9, 15), date(2027, 9, 15)), (date(2027, 9, 1), date(2027, 9, 30)), None),
    ("before", (date(2028, 1, 1), date(2028, 12, 31)), (date(2027, 9, 1), date(2027, 9, 30)), True),
    ("after", (date(2024, 1, 1), date(2024, 12, 31)), (date(2026, 6, 30), date(2026, 6, 30)), True)])
def test_a_relation_is_true_false_or_open_by_the_precision_of_both_dates(relation, bound, graduated, verdict):
    assert academic.check(relation, [bound], graduated, date(2026, 10, 14)) is verdict


@pytest.mark.parametrize("text,subjects,blockers", [
    ("Do you have a Master's degree in Mathematics?", ["mathematics"], []),
    ("a degree in Computer Science or a related field", ["computer science"], []),
    ("a degree in Mathematics and Physics", [], ["joint_subjects"]),
    ("Do you have a degree from a US university?", [], ["country_of_institution"]),
    ("an accredited qualification", [], ["grade_or_accreditation"]),
    ("un grado en Informática", ["computer science"], [])])
def test_what_a_degree_question_asks(text, subjects, blockers):
    asked = academic.facets(text)
    assert asked["subjects"] == subjects and asked["blockers"] == blockers, asked


def test_subjects_and_institutions_match_whole_words_in_order():
    assert academic.subject_matches("Computer Science", ["BSc Computer Science", "North University"])
    assert academic.subject_matches("AI", ["Artificial Intelligence"]) and not academic.subject_matches("Mat", ["Mathematics"])
    assert not academic.subject_matches("Mathematics", ["Artificial Intelligence"])
    assert academic.institution_matches("South University", ["South University"]) and not academic.institution_matches("Harvard", ["South University"])
