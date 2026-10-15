"""Languages: a LEVEL is an order, not a presence (``intelligence/languages.py``).

'Are you a native English speaker?', 'Is English your first language?', 'Do you speak English at C2 level?' and 'Are you fluent
in English?' were all answered 'Yes' for a Spanish native whose English is 'Fluent' (and for one whose English is 'Basic'): any
boolean question that named a language the candidate has, at any level, got a yes. Here every row is asked of candidates whose
English differs, and the answer is certain or it is the user's.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import JOB_UK, RADIO, ask, speaker_engine  # noqa: E402
from jobagent.intelligence import languages  # noqa: E402

VARIANTS = {  # (English level, native flag), French
    "fluent": dict(), "denied": dict(english=("Fluent", False)), "basic": dict(english=("Basic", None)),
    "native": dict(english=("Native", True)), "c1": dict(english=("C1", None)), "intermediate": dict(english=("Intermediate", None)),
    "with_french": dict(french=("Intermediate", None))}
ORDER = tuple(VARIANTS)


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    path = tmp_path_factory.mktemp("language")
    return {name: speaker_engine(path, name, **spec) for name, spec in VARIANTS.items()}


def row(engines, label, **field):
    return tuple(ask(engines[name], label, JOB_UK, **(field or RADIO))["answer"] for name in ORDER)


#                                                            fluent  denied  basic  native   c1    interm  +french
TABLE = [
    ("Are you a native English speaker?",                     (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Is English your first language?",                       (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Is English your mother tongue?",                        (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Is English your native language?",                      (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Do you speak English at a native level?",               (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Do you have native or bilingual proficiency in English?", (None, "No",  None,  "Yes",  None,  None,  None)),
    ("Are you bilingual in English and Spanish?",             (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("Do you speak English at C2 level?",                     (None,  None,   "No",  "Yes",  None,  "No",  None)),
    # a BASIC level is not a certain 'yes' to 'do you speak it' (the held-out metamorphic suite): whether it counts is the user's to say
    ("Do you speak English?",                                 ("Yes", "Yes",  None,  "Yes",  "Yes", "Yes", "Yes")),
    ("Can you speak English?",                                ("Yes", "Yes",  None,  "Yes",  "Yes", "Yes", "Yes")),
    ("Are you fluent in English?",                            ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    ("Do you speak English fluently?",                        ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    ("Do you have professional working proficiency in English?", ("Yes", "Yes", "No", "Yes", "Yes", "No", "Yes")),
    ("Do you speak English at an advanced level?",            ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    ("Do you speak English at C1 level?",                     ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    ("Do you speak English at B2 level?",                     ("Yes", "Yes",  "No",  "Yes",  "Yes", None,  "Yes")),
    ("Do you speak English at an intermediate level?",        ("Yes", "Yes",  None,  "Yes",  "Yes", "Yes", "Yes")),
    ("Are you fluent in English and Spanish?",                ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    ("Are you a native Spanish speaker?",                     ("Yes",) * 7),
    ("Is Spanish your first language?",                       ("Yes",) * 7),
    ("Do you speak Spanish at a native level?",               ("Yes",) * 7),
    # Spanish
    ("¿Hablas inglés?",                                       ("Yes", "Yes",  None,  "Yes",  "Yes", "Yes", "Yes")),
    ("¿Eres nativo de inglés?",                               (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("¿Hablas inglés con nivel nativo?",                      (None,  "No",   None,  "Yes",  None,  None,  None)),
    ("¿Hablas español de forma nativa?",                      ("Yes",) * 7),
    ("¿Hablas inglés con fluidez?",                           ("Yes", "Yes",  "No",  "Yes",  "Yes", "No",  "Yes")),
    # a language the candidate has no record of is never invented
    ("Do you speak German?",                                  (None,) * 7),
    ("Do you speak French?",                                  (None,) * 6 + ("Yes",)),
    ("Do you speak French at a conversational level?",        (None,) * 6 + ("Yes",)),
]


@pytest.mark.parametrize("label,expected", TABLE)
def test_a_level_is_compared_with_the_stored_level_and_answered_only_where_it_is_certain(engines, label, expected):
    assert row(engines, label) == expected, label


def test_a_comparison_is_inferred_and_a_presence_is_not(engines):
    """'Do you speak English?' is a fact of the profile; 'Are you fluent?' is derived from the level: to be checked."""
    assert ask(engines["fluent"], "Do you speak English?", JOB_UK, **RADIO)["confidence_level"] == "EXACT"
    fluent = ask(engines["fluent"], "Are you fluent in English?", JOB_UK, **RADIO)
    assert fluent["confidence_level"] == "DERIVED_HIGH" and fluent["verified"] is False and fluent["fact_ids"]
    unknown = ask(engines["fluent"], "Are you a native English speaker?", JOB_UK, **RADIO)
    assert unknown["answer"] is None and unknown["root_cause"] == "MISSING_LANGUAGE_LEVEL_EVIDENCE:English"
    assert "your level in English" in unknown["explanation"]


def test_the_native_flag_of_the_record_beats_the_words_of_the_level(engines):
    """English 'Fluent' with native=False: the profile says it is not the first language. 'Native' with no flag: it is."""
    assert ask(engines["denied"], "Is English your first language?", JOB_UK, **RADIO)["answer"] == "No"
    only_level = speaker_engine(Path(engines["fluent"].db.path).parent, "level-only", english=("Native", None))
    assert ask(only_level, "Is English your first language?", JOB_UK, **RADIO)["answer"] == "Yes"


def test_which_language_is_the_native_one_is_still_answered(engines):
    for label in ("What is your native language?", "Native language", "Mother tongue", "¿Cuál es tu lengua materna?"):
        assert ask(engines["fluent"], label, JOB_UK, type="text")["answer"] == "Spanish", label


def test_a_proficiency_question_still_gets_the_stored_level(engines):
    for label in ("English proficiency", "English level", "Nivel de inglés", "What is your level of English?"):
        assert ask(engines["fluent"], label, JOB_UK, type="text")["answer"] == "Fluent", label
    assert ask(engines["fluent"], "Spanish proficiency", JOB_UK, type="text")["answer"] == "Native"


# ------------------------------------------------------------------------------------------ menus with their OWN words
def menu(*labels):
    return {"type": "select", "tag": "select", "role": "combobox", "options": [{"label": x, "value": x.lower()} for x in labels]}


@pytest.mark.parametrize("label,options,wanted,exact", [
    ("English level", ("Basic", "Intermediate", "Advanced", "Native"), "Advanced", False),
    ("Nivel de inglés", ("A1", "A2", "B1", "B2", "C1", "C2"), "C1", False),
    ("English CEFR level", ("B1", "B2", "C1", "C2"), "C1", False),
    ("Spanish proficiency", ("Basic", "Intermediate", "Advanced", "Native or bilingual"), "Native or bilingual", False),
    ("Nivel de español", ("Básico", "Intermedio", "Avanzado", "Nativo"), "Nativo", True),
    ("Spanish level", ("A1", "A2", "B1", "B2", "C1", "C2"), "C2", False),
    ("English level", ("Basic", "Intermediate", "Fluent", "Native"), "Fluent", True),
    ("Nivel de inglés", ("Básico", "Intermedio", "Avanzado", "Nativo"), "Avanzado", False)])
def test_a_menu_that_says_the_level_in_other_words_gets_the_option_on_the_same_step_of_the_ladder(engines, label, options, wanted, exact):
    result = ask(engines["fluent"], label, JOB_UK, **menu(*options))
    assert result["answer"] == wanted, result
    assert result["confidence_level"] == ("EXACT" if exact else "DERIVED_HIGH") and result["verified"] is exact
    if not exact:
        assert "language_level_on_the_menus_own_ladder" in result["rules_used"]


@pytest.mark.parametrize("label,options", [
    ("English level", ("Basic", "Intermediate")), ("English level", ("Advanced", "Upper advanced")),
    ("English level", ("Yes", "No")), ("English level", ("Good", "Very good", "Excellent"))])
def test_a_menu_with_no_single_option_on_the_step_is_not_answered(engines, label, options):
    """Two options on one step of the ladder are a choice nobody made; a step the menu does not have is not rounded to."""
    assert ask(engines["fluent"], label, JOB_UK, **menu(*options))["answer"] is None, options


def test_a_menu_that_already_says_fluent_or_native_keeps_its_meaning(engines):
    """'Fluent or native' is the profile's own equivalence of 'Fluent' (formatting.EQUIVALENTS): unchanged."""
    assert ask(engines["fluent"], "English level", JOB_UK, **menu("Fluent or native", "Beginner"))["answer"] == "Fluent or native"
    assert ask(engines["fluent"], "English level", JOB_UK, **menu("Advanced", "Proficient", "Native"))["answer"] == "Advanced"


def test_the_ladder_never_rounds_a_level_upwards(engines):
    """'Fluent' is not 'Native', nor 'C2': a menu with only those is left to the user."""
    for options in (("Basic", "Intermediate", "Native"), ("A1", "A2", "B1", "B2", "C2")):
        assert ask(engines["fluent"], "English level", JOB_UK, **menu(*options))["answer"] is None, options


# ----------------------------------------------------------------------------------------- the ladder, without an engine
@pytest.mark.parametrize("text,rank", [("Basic", 1), ("Elementary proficiency", 1), ("A2", 1), ("Intermediate", 2), ("B1", 2),
                                       ("Upper-intermediate", 3), ("B2", 3), ("Fluent", 4), ("C1", 4), ("Advanced (C1)", 4),
                                       ("Professional working proficiency", 4), ("C2", 5), ("Native", 6),
                                       ("Native or bilingual proficiency", 6), ("Nativo", 6), ("Lengua materna", 6),
                                       ("Fluent or native", None), ("Proficient", None), ("Good", None), ("", None)])
def test_the_ladder_of_levels(text, rank):
    assert languages.level_class(text) == rank, text


@pytest.mark.parametrize("stored,flag,asked,verdict", [
    ("Native", None, 6, True), ("Fluent", None, 6, None), ("Fluent", False, 6, False), ("Fluent", None, 5, None), ("Fluent", None, 4, True),
    ("Basic", None, 4, False), ("Basic", None, 2, None), ("Intermediate", None, 2, True), ("Good", None, 2, None), ("", True, 5, True)])
def test_comparing_a_stored_level_with_an_asked_one(stored, flag, asked, verdict):
    assert languages.compare(stored, flag, asked)[0] is verdict, (stored, flag, asked)


@pytest.mark.parametrize("name,english", [("inglés", "english"), ("español", "spanish"), ("castellano", "spanish"), ("francés", "french"),
                                          ("English", "english"), ("catalán", "catalan")])
def test_spanish_names_of_languages_are_the_profiles_english_ones(name, english):
    assert languages.canonical(name) == english
