"""The legal questions of an application, READ before they are answered (``intelligence/legal.py``, ``jurisdiction.py``).

A work-authorisation or sponsorship answer is a declaration. Three things decide it, and each used to be skipped:

* WHERE: the right to work in the UK was returned for 'Are you authorised to work in Australia / India / the U.S.?' (only six
  country names were read; any other fell back to the country of the vacancy);
* WHAT: any question containing 'right to work' got the boolean 'has the right now', so 'Do you lack...', 'Is it time-limited?',
  'Have you ever been refused...?' were answered with the same 'Yes' for a citizen and the same 'No' for a person who needs a visa;
* WITH WHICH POLARITY: 'I do not require sponsorship' and 'without sponsorship' got the answer of 'I require sponsorship'.

Every table below is asked of THREE candidates whose facts differ (a UK Student-route holder, a UK citizen, a person who needs
sponsorship), so a wrong answer cannot hide behind a candidate that happens to agree with it. ``None`` is 'the user answers'.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import CHECKBOX, JOB_ES, JOB_UK, RADIO, ask, engine_for  # noqa: E402
from jobagent.intelligence import jurisdiction, legal  # noqa: E402

KINDS = ("student", "citizen", "needs_sponsorship")


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    path = tmp_path_factory.mktemp("legal")
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    return {kind: engine_for(path, kind) for kind in KINDS}


def answers(engines, label, job=JOB_UK, **field):
    return tuple(ask(engines[kind], label, job, **(field or RADIO))["answer"] for kind in KINDS)


# ---------------------------------------------------------------------------------------------- WHERE: the named place
COUNTRIES = ["Australia", "India", "Ireland", "Singapore", "Mexico", "the Netherlands", "Brazil", "Poland", "Switzerland", "Italy",
             "Japan", "China", "New Zealand", "South Africa", "the United Arab Emirates", "the UAE", "Argentina", "Portugal",
             "Sweden", "Norway", "Colombia", "Chile", "Nigeria", "Kenya", "Canada", "the U.S.", "the US", "the USA", "the States",
             "America", "Spain", "France", "Germany", "Hong Kong", "Israel", "Turkey", "Thailand", "Vietnam"]
TEMPLATES = ["Are you authorized to work in {}?", "Are you legally authorized to work in {}?", "Do you have the right to work in {}?",
             "Will you require visa sponsorship to work in {}?", "Do you now or in the future require sponsorship to work in {}?"]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("template", TEMPLATES)
def test_a_country_the_question_names_is_never_answered_with_the_vacancys_rights(engines, template, country):
    """The candidates' work rights are the UK's. A question about ANY other country is theirs to answer."""
    label = template.format(country)
    for kind in KINDS:
        result = ask(engines[kind], label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["verified"] is False and result["fact_ids"] == [], (kind, label, result)
        assert result["root_cause"].startswith(("MISSING_WORK_AUTHORIZATION_EVIDENCE:", "MISSING_SPONSORSHIP_EVIDENCE:")), result
        assert result["root_cause"].split(":")[1] in jurisdiction.NAMES.values() or result["root_cause"].endswith("States"), result


@pytest.mark.parametrize("label", [
    "¿Tienes autorización para trabajar en Australia?", "¿Tienes derecho a trabajar en Irlanda?", "¿Tienes permiso para trabajar en EE.UU.?",
    "¿Estás autorizado para trabajar en Estados Unidos?", "¿Tienes autorización para trabajar en los Países Bajos?",
    "¿Puedes trabajar legalmente en México?", "¿Necesitarás patrocinio de visado para trabajar en Australia?",
    "¿Necesitas patrocinio para trabajar en Canadá?", "¿Tienes autorización para trabajar en Francia?",
    "¿Estás autorizado para trabajar en Alemania?", "¿Tienes derecho a trabajar en Italia?", "¿Puedes trabajar en Suiza?"])
def test_the_same_in_spanish(engines, label):
    assert answers(engines, label) == (None, None, None), label


@pytest.mark.parametrize("country", ["the UK", "the U.K.", "the United Kingdom", "Great Britain", "England", "Scotland", "Wales",
                                     "London", "Britain", "Northern Ireland"])
def test_every_way_to_name_the_uk_is_the_uk(engines, country):
    assert answers(engines, f"Are you legally authorized to work in {country}?", JOB_ES) == ("Yes", "Yes", "No"), country


@pytest.mark.parametrize("label", ["¿Tienes autorización para trabajar en el Reino Unido?", "Are you authorised to work in England?",
                                   "Are you authorised to work in the UK (e.g. on a Student visa)?"])
def test_the_uk_named_on_a_vacancy_elsewhere_is_answered_from_the_uk_record(engines, label):
    assert answers(engines, label, JOB_ES) == ("Yes", "Yes", "No")


@pytest.mark.parametrize("label", [
    "Are you authorised to work in Kyiv?", "Are you authorised to work in Georgia?", "Are you eligible to work in Springfield?",
    "Do you require sponsorship to work in Transylvania?", "Are you authorised to work in the European Union?",
    "Do you have the right to work in Europe?", "¿Tienes derecho a trabajar en la Unión Europea?",
    "Are you authorised to work in Ireland and the UK?", "Are you authorised to work in Spain, France and the UK?",
    "Are you authorised to work worldwide?", "Are you eligible to work in the EEA?"])
def test_a_place_nobody_can_place_a_region_or_several_places_is_not_answered(engines, label):
    for kind in KINDS:
        result = ask(engines[kind], label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["root_cause"] == "MISSING_TARGET_WORK_COUNTRY", (kind, label, result)


@pytest.mark.parametrize("label", ["Are you authorized to work in Ireland or the UK?", "Are you authorised to work in the UK or the EU?",
                                   "Do you have the right to work in the UK or in Ireland?"])
def test_either_of_several_places_is_yes_when_the_vacancys_country_says_yes_and_never_no(engines, label):
    """'Ireland or the UK': a right in the UK answers yes. A missing right in the UK says nothing about Ireland: not a no."""
    assert answers(engines, label) == ("Yes", "Yes", None)
    assert ask(engines["citizen"], label, JOB_UK, **RADIO)["rules_used"][-1] == "true_in_one_of_the_named_places"


def test_a_disjunction_is_never_answered_the_other_way_round(engines):
    for label in ("Do you not have the right to work in Ireland or the UK?", "Do you require sponsorship to work in Ireland or the UK?"):
        assert answers(engines, label)[0] != "Yes" or "not" not in label, label
    assert answers(engines, "Do you not have the right to work in the UK or the EU?") == (None, None, None)


def test_the_country_the_job_is_in_is_used_when_the_question_names_none(engines):
    assert answers(engines, "Are you legally authorized to work in the country where this job is located?") == ("Yes", "Yes", "No")
    assert answers(engines, "Are you legally authorized to work in the country where this job is located?", JOB_ES) == (None,) * 3


# --------------------------------------------------------------------------------------------- WHAT: the predicate
NOT_A_PLAIN_QUESTION = [
    # a lack, a restriction, an expiry, a history, a need, an exception, a dependence, an explanation: other questions
    "Do you lack the legal right to work in the UK?", "Do you lack the right to work in the UK?",
    "Is there any restriction on your right to work in the UK?", "Is your right to work in the UK time-limited?",
    "Do you have any restrictions on your right to work in the UK?", "Is your right to work in the UK subject to any restrictions?",
    "Is your permission to work in the UK time-limited?", "Have you ever been refused permission to work in the UK?",
    "Has your right to work in the UK ever been revoked?", "Have you ever overstayed a visa in the UK?",
    "Does your right to work in the UK expire within the next 12 months?", "Will your right to work in the UK expire in the next 12 months?",
    "Does your right to work in the UK have an end date?", "Is your right to work in the UK temporary?",
    "Do you need permission to work in the UK?", "Do you need a visa or permission to work in the UK?",
    "Do you require a visa to work in the UK?", "Must you have a visa to work in the UK?",
    "Is your right to work dependent on an employer-sponsored visa?", "Is your right to work in the UK tied to your employer?",
    "Are you authorised to work in the UK, except as a contractor?", "Are you authorised to work in the UK unless your visa changes?",
    "Do you have the right to work in the UK until 2028?", "Is your right to work in the UK valid for the whole contract?",
    "Do you have the right to work in the UK or will you require sponsorship?", "Do you have the right to work in the UK and do you have a degree?",
    "Please explain your right to work in the UK", "Please provide details of your right to work in the UK",
    "Please tell us about your work authorisation", "Describe your right to work in the UK", "Why do you have the right to work in the UK?",
    "Explain how your right to work in the UK was obtained",
    # Spanish
    "¿Careces de derecho a trabajar en el Reino Unido?", "¿Tu derecho a trabajar en el Reino Unido está limitado?",
    "¿Tienes alguna restricción en tu derecho a trabajar en el Reino Unido?", "¿Caduca tu autorización para trabajar en el Reino Unido?",
    "¿Tu permiso para trabajar en el Reino Unido es temporal?", "¿Te han denegado alguna vez el permiso para trabajar en el Reino Unido?",
    "¿Necesitas permiso para trabajar en el Reino Unido?", "¿Necesitas un visado para trabajar en el Reino Unido?",
    "Explica tu derecho a trabajar en el Reino Unido", "Describe tu autorización para trabajar en el Reino Unido",
    "¿Tu autorización para trabajar en el Reino Unido depende de tu empleador?",
    # a sponsorship that is not 'do you need it'
    "Sponsor name", "Please explain why you require sponsorship", "Do you require sponsorship for your dependants?",
    "Have you previously been refused sponsorship?", "Are you seeking sponsorship in order to be employed by our company?",
    "If you require sponsorship, please provide details", "¿Por qué necesitas patrocinio de visado?",
    "Explica por qué necesitas patrocinio de visado", "Nombre del patrocinador"]


@pytest.mark.parametrize("label", NOT_A_PLAIN_QUESTION)
def test_a_question_that_is_not_plain_is_never_answered_with_the_plain_fact(engines, label):
    """The same 'Yes' for the citizen and the same 'No' for the person who needs a visa is exactly the defect: none is given."""
    for kind in KINDS:
        result = ask(engines[kind], label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["verified"] is False, (kind, label, result["answer"])
        assert "_PREDICATE:" in result["root_cause"], (label, result)


@pytest.mark.parametrize("label", ["Do you require sponsorship for your dependants?"])
def test_a_sponsorship_for_somebody_else_is_not_the_candidates(engines, label):
    assert answers(engines, label) == (None, None, None)


def test_the_fact_of_a_plain_question_does_not_depend_on_who_asks_it_in_a_text_field(engines):
    """A text input asking 'Right to work (visa type)' wants a type, never 'Yes'; the same words with a Yes/No menu are a yes/no."""
    for kind in KINDS:
        assert ask(engines[kind], "What is your right to work status?", JOB_UK, type="text")["answer"] is None
        assert ask(engines[kind], "Right to work (visa type)", JOB_UK, type="text")["answer"] is None


# ------------------------------------------------------------------------------------- POLARITY: what the proposition says
PLAIN = [  # (label, UK Student, UK citizen, needs sponsorship)
    ("Do you have the right to work in the UK?", "Yes", "Yes", "No"),
    ("Are you legally authorised to work in the UK?", "Yes", "Yes", "No"),
    ("Are you eligible to work in the UK?", "Yes", "Yes", "No"),
    ("Can you legally work in the UK?", "Yes", "Yes", "No"),
    ("I have the right to work in the United Kingdom", "Yes", "Yes", "No"),
    ("Right to work", "Yes", "Yes", "No"),
    ("Do you not have the right to work in the UK?", "No", "No", "Yes"),
    ("Aren't you legally authorised to work in the UK?", "No", "No", "Yes"),
    ("Are you not authorised to work in the UK?", "No", "No", "Yes"),
    ("I do not have the right to work in the UK", "No", "No", "Yes"),
    ("No, I do not have the right to work in the UK", "No", "No", "Yes"),
    ("No work authorization", "No", "No", "Yes"),
    ("¿Tienes derecho a trabajar en el Reino Unido?", "Yes", "Yes", "No"),
    ("¿Estás legalmente autorizado para trabajar en el Reino Unido?", "Yes", "Yes", "No"),
    ("¿No tienes derecho a trabajar en el Reino Unido?", "No", "No", "Yes"),
    ("No tengo derecho a trabajar en el Reino Unido", "No", "No", "Yes"),
    # sponsorship: the question and its mirror images
    ("Do you require visa sponsorship?", "No", "No", "Yes"),
    ("Will you now or in the future require sponsorship for employment visa status?", "No", "No", "Yes"),
    ("Do you now or will you in the future require visa sponsorship?", "No", "No", "Yes"),
    ("Is sponsorship required?", "No", "No", "Yes"),
    ("Do you need visa sponsorship?", "No", "No", "Yes"),
    ("I require visa sponsorship", "No", "No", "Yes"),
    ("I will need sponsorship", "No", "No", "Yes"),
    ("Yes, I will need sponsorship", "No", "No", "Yes"),
    ("Do you not require sponsorship?", "Yes", "Yes", "No"),
    ("Do you NOT need sponsorship?", "Yes", "Yes", "No"),
    ("I do not require sponsorship", "Yes", "Yes", "No"),
    ("I do not require visa sponsorship", "Yes", "Yes", "No"),
    ("I don't require sponsorship", "Yes", "Yes", "No"),
    ("I will not require sponsorship now or in the future", "Yes", "Yes", "No"),
    ("No, I will not need sponsorship", "Yes", "Yes", "No"),
    ("Please confirm that you do not require sponsorship", "Yes", "Yes", "No"),
    ("Is employer sponsorship not required for you?", "Yes", "Yes", "No"),
    ("No sponsorship required", "Yes", "Yes", "No"),
    ("Visa sponsorship is not required", "Yes", "Yes", "No"),
    ("Sponsorship is not needed", "Yes", "Yes", "No"),
    ("without any visa sponsorship", "Yes", "Yes", "No"),
    ("without needing any visa sponsorship", "Yes", "Yes", "No"),
    # 'can you work ... without sponsorship' is the right to work PLUS no need of sponsorship
    ("Can you work in the UK without needing sponsorship?", "Yes", "Yes", "No"),
    ("Are you eligible to work in the UK without sponsorship?", "Yes", "Yes", "No"),
    ("Are you able to work in the UK without visa sponsorship?", "Yes", "Yes", "No"),
    ("Do you have the right to work in the UK without sponsorship?", "Yes", "Yes", "No"),
    ("Are you legally authorised to work in the UK without sponsorship?", "Yes", "Yes", "No"),
    ("Are you authorized to work in the UK without requiring sponsorship?", "Yes", "Yes", "No"),
    # Spanish
    ("¿Necesitas patrocinio de visado?", "No", "No", "Yes"),
    ("¿Necesitarás patrocinio de visado ahora o en el futuro?", "No", "No", "Yes"),
    ("¿No necesitas patrocinio de visado?", "Yes", "Yes", "No"),
    ("No necesito patrocinio de visado", "Yes", "Yes", "No"),
    ("No requiero patrocinio", "Yes", "Yes", "No"),
    ("Necesito patrocinio de visado", "No", "No", "Yes"),
    ("¿Puedes trabajar en el Reino Unido sin necesidad de patrocinio?", "Yes", "Yes", "No"),
    ("¿Tienes derecho a trabajar en el Reino Unido sin patrocinio?", "Yes", "Yes", "No"),
    ("¿Estás autorizado para trabajar en el Reino Unido sin necesitar patrocinio?", "Yes", "Yes", "No"),
]


@pytest.mark.parametrize("label,student,citizen,needs", PLAIN)
def test_a_proposition_is_answered_by_whether_it_is_true_of_the_candidate(engines, label, student, citizen, needs):
    assert answers(engines, label) == (student, citizen, needs), label


@pytest.mark.parametrize("label,student,citizen,needs", [row for row in PLAIN if not row[0].startswith(("Do you", "Are you", "Is ", "Can ",
                                                                                                        "Will ", "Aren't", "¿"))])
def test_a_single_checkbox_that_states_it_is_ticked_when_it_is_true(engines, label, student, citizen, needs):
    """The statement of a checkbox: 'I do not require visa sponsorship' is ticked for the person who does not."""
    got = answers(engines, label, **CHECKBOX)
    assert got == (student, citizen, needs), label


def test_sponsorship_polarity_for_the_candidate_who_needs_it(engines):
    """The checkbox the review found: ticked for a person who needs sponsorship because the polarity was ignored."""
    result = ask(engines["needs_sponsorship"], "I do not require visa sponsorship", JOB_UK, **CHECKBOX)
    assert result["answer"] == "No" and "negated_legal_predicate" in result["rules_used"]
    assert ask(engines["student"], "I do not require visa sponsorship", JOB_UK, **CHECKBOX)["answer"] == "Yes"


def test_the_option_prefix_of_a_label_is_not_a_negation(engines):
    assert answers(engines, "No, I will not need sponsorship") == ("Yes", "Yes", "No")
    assert answers(engines, "Yes, I will need sponsorship") == ("No", "No", "Yes")
    assert answers(engines, "Do you require sponsorship? (Yes/No)") == ("No", "No", "Yes")
    assert answers(engines, "Do you require visa sponsorship now or in the future? Select Yes No") == ("No", "No", "Yes")
    assert answers(engines, "¿Necesitas patrocinio de visado? (Sí/No)") == ("No", "No", "Yes")


def test_too_many_negations_are_not_read(engines):
    assert answers(engines, "Is it not true that you do not never require sponsorship?") == (None, None, None)


def test_the_facets_of_a_right_still_decide_it(engines):
    """Permanent, unrestricted, full-time, future: what the citizen's record states and the student's does not."""
    assert answers(engines, "Do you have permanent unrestricted right to work in the UK?") == (None, "Yes", "No")
    assert answers(engines, "Do you have the right to work in the UK without restrictions?") == (None, "Yes", "No")
    assert answers(engines, "Are you authorised to work full-time in the UK?") == (None, "Yes", "No")
    assert answers(engines, "Do you not have unrestricted right to work in the UK?") == (None, "No", "Yes")


# ------------------------------------------------------------------------------- the parts, one by one (no engine, no data)
@pytest.mark.parametrize("text,countries,regions", [
    ("Are you authorized to work in Australia?", ["Australia"], []),
    ("Are you authorized to work in the U.S.?", ["United States"], []), ("authorized to work in the US", ["United States"], []),
    ("Do you have the right to work for us?", [], []), ("work for Bank of America in the UK", ["United Kingdom"], []),
    ("authorized to work in EE.UU.", ["United States"], []), ("Right to work in the UK or the EU?", ["United Kingdom"], ["European Union"]),
    ("¿Autorizado en la UE?", [], ["European Union"]), ("¿Trabajar en Países Bajos?", ["Netherlands"], []),
    ("Are you authorised to work in New Mexico?", ["United States"], []), ("work in the States", ["United States"], []),
    ("Do you need sponsorship in Texas?", ["United States"], []), ("authorised to work in the United Kingdom (e.g. H-1B)", ["United Kingdom"], []),
    ("authorised to work in the EEA", [], ["European Economic Area"]), ("¿Puedes trabajar en Latinoamérica?", [], ["Latin America"]),
    ("Are you authorised to work in the UAE?", ["United Arab Emirates"], []), ("Right to work in Hong Kong", ["Hong Kong"], [])])
def test_the_places_a_question_names(text, countries, regions):
    named = jurisdiction.named_places(text)
    assert (named["countries"], named["regions"]) == (countries, regions), named


@pytest.mark.parametrize("text,connective", [("Ireland or the UK", "or"), ("the UK and Ireland", "and"), ("the UK, Ireland", ""),
                                             ("Spain y Francia", "and"), ("España o Francia", "or")])
def test_how_several_places_combine(text, connective):
    assert jurisdiction.named_places("work in " + text)["connective"] == connective


@pytest.mark.parametrize("text,kind,sign", [
    ("Do you lack the legal right to work in the UK?", "lack", "affirmative"),
    ("Is your right to work time-limited?", "restriction", "affirmative"),
    ("Have you ever been refused permission to work?", "history", "affirmative"),
    ("Do you need permission to work?", "need", "affirmative"),
    ("Are you authorised to work in the UK, unless you change employer?", "exception", "affirmative"),
    ("Please explain your right to work", "explain", "affirmative"),
    ("Are you authorised to work in the UK?", "plain", "affirmative"),
    ("Aren't you authorised to work in the UK?", "plain", "negated"),
    ("Are you authorised to work in the UK without sponsorship?", "plain", "affirmative"),
    ("¿No tienes derecho a trabajar?", "plain", "negated"), ("¿Tu permiso es temporal?", "restriction", "affirmative")])
def test_the_legal_reading_of_a_work_right_question(text, kind, sign):
    reading = legal.read(text, "WORK_AUTHORIZATION", {})
    assert (reading["kind"], reading["polarity"]) == (kind, sign), reading


@pytest.mark.parametrize("text,kind,sign", [
    ("Do you require sponsorship?", "plain", "affirmative"), ("I do not require sponsorship", "plain", "negated"),
    ("Sponsorship is not required", "plain", "negated"), ("No necesito patrocinio", "plain", "negated"),
    ("Sponsor name", "unparsed", "affirmative"), ("Please explain why you require sponsorship", "explain", "affirmative"),
    ("If you require sponsorship, please state which", "conditional", "affirmative"),
    ("Do you now or will you in the future require sponsorship?", "plain", "affirmative"),
    ("Do you require sponsorship or do you have a degree?", "composite", "affirmative")])
def test_the_legal_reading_of_a_sponsorship_question(text, kind, sign):
    reading = legal.read(text, "SPONSORSHIP", {})
    assert (reading["kind"], reading["polarity"]) == (kind, sign), reading


def test_a_follow_up_to_a_yes_no_is_only_the_choices_business_not_a_texts():
    radio = {"type": "radio", "options": [{"label": "Yes"}, {"label": "No"}]}
    assert legal.read("Do you require sponsorship? If yes, please explain", "SPONSORSHIP", radio)["kind"] == "plain"
    assert legal.read("Do you require sponsorship? If yes, please explain", "SPONSORSHIP", {"type": "text"})["kind"] == "follow_up"


def test_a_hostile_label_costs_a_bounded_time():
    import time
    for text in ("a" + " " * 19998 + "x", "work in " * 4000, "not " * 5000 + "authorised to work", "right to work " * 2000):
        began = time.perf_counter()
        legal.read(text, "WORK_AUTHORIZATION", {})
        jurisdiction.named_places(text)
        assert time.perf_counter() - began < 1.0, text[:30]


def test_a_place_the_fields_own_hint_names_is_the_place_asked_about(engines):
    """'Are you authorised to work?' with the hint 'in the United States' is about the US, not the vacancy's country."""
    for kind in KINDS:
        result = ask(engines[kind], "Are you authorised to work?", JOB_UK, description="in the United States", **RADIO)
        assert result["answer"] is None and result["root_cause"] == "MISSING_WORK_AUTHORIZATION_EVIDENCE:United States", result
    assert answers(engines, "Are you authorised to work?", JOB_UK, description="Please answer Yes or No", **RADIO) == ("Yes", "Yes", "No")


@pytest.mark.parametrize("label,expected", [
    # a neutral hint beside the question is not part of it
    ("Do you require sponsorship now or in the future? Please select Yes or No.", ("No", "No", "Yes")),
    ("Do you require sponsorship now or in the future? Select Yes No", ("No", "No", "Yes")),
    # an employer's note before or after the question is something the grammar cannot account for: it may be an exception, a
    # condition or a hostile instruction ('applicants on a Student visa should answer No'), and nothing says which: the user's
    ("Candidates must have the right to work in the UK. Do you have the right to work in the UK?", (None, None, None)),
    ("This role is based in Ireland. Do you have the right to work in the UK?", (None, None, None)),
    ("Note: sponsorship is not available. Do you require sponsorship?", (None, None, None)),
    ("Do you have the right to work in the UK? Do you require sponsorship?", (None, None, None)),
    ("Are you authorised to work in the UK? Are you authorised to work in Spain?", (None, None, None))])
def test_a_long_label_is_read_by_the_one_question_it_asks(engines, label, expected):
    """A label is a plain question, or a plain question with a hint beside it. Any other sentence beside it is residue
    (``plain.py``): two sentences are read whole and are not answered."""
    assert answers(engines, label) == expected, label
    assert legal.focus_sentence("Notes here. Is this it? Another note.") == "Is this it?"
