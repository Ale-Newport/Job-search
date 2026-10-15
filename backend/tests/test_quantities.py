"""A quantity answers a menu of ranges only when exactly one range holds it (docs/EXTENSION_DESIGN.md 5.4)."""
import pytest

import jobagent.automation  # noqa: F401  (the package imports the engine in the order its modules need)
from jobagent.intelligence.formatting import choice
from jobagent.intelligence.quantities import bucket_option, menu_unit, parse_quantity, parse_range


def menu(*labels):
    return [{"label": label, "value": label.lower()} for label in labels]


YEARS = menu("Less than 1 year", "1-3 years", "3-5 years", "5+ years")


@pytest.mark.parametrize("value,expected", [
    ("4 years", "3-5 years"), ("4", "3-5 years"), ("about 4 years", "3-5 years"), ("4.5 years", "3-5 years"),
    ("18 months", "1-3 years"), ("6 months", "Less than 1 year"), ("a year", "1-3 years"), ("one year", "1-3 years"),
    ("half a year", "Less than 1 year"), ("8 years", "5+ years"), ("6 yrs", "5+ years"),
    ("1 year", "1-3 years"), ("1", "1-3 years"),  # "less than 1 year" leaves 1 out: only "1-3 years" holds it
])
def test_a_quantity_goes_to_the_range_that_holds_it(value, expected):
    assert bucket_option(value, YEARS)["label"] == expected


@pytest.mark.parametrize("value", ["3 years", "3", "5 years", "5 yrs"])  # "1-3" and "3-5" both hold 3; "3-5" and "5+" both hold 5
def test_a_border_between_two_ranges_is_left_to_the_person(value):
    assert bucket_option(value, YEARS) is None


@pytest.mark.parametrize("value", ["", "Yes", "years", "four years of Python and Java", "3-5 years", "2 meters", "1,000,000 years"])
def test_what_is_not_a_plain_quantity_answers_nothing(value):
    assert bucket_option(value, YEARS) is None


def test_open_and_closed_ends_are_read_as_the_menu_writes_them():
    labels = menu("Under 2 years", "2 to 4 years", "More than 4 years")
    assert bucket_option("2", labels)["label"] == "2 to 4 years"
    assert bucket_option("4", labels)["label"] == "2 to 4 years"          # "more than 4" leaves 4 out
    assert bucket_option("4.5", labels)["label"] == "More than 4 years"
    assert bucket_option("1.9", labels)["label"] == "Under 2 years"       # "under 2" leaves 2 out
    labels = menu("Up to 3 months", "3 months or more")
    assert bucket_option("3 months", labels) is None                       # both hold it: never a guess
    assert bucket_option("2 months", labels)["label"] == "Up to 3 months"


def test_units_are_converted_and_a_unit_that_cannot_be_compared_answers_nothing():
    weeks = menu("Immediate", "1 week", "2 weeks", "1 month", "2+ months")
    assert bucket_option("2 weeks", weeks)["label"] == "2 weeks"
    assert bucket_option("1 month", weeks)["label"] == "1 month"
    assert bucket_option("3 weeks", weeks) is None                          # between "2 weeks" and "1 month": no exact label
    assert bucket_option("8 weeks", weeks) is None                          # 8 weeks is a little under "2+ months"
    assert bucket_option("9 weeks", weeks)["label"] == "2+ months"
    # a menu without unit and a quantity with one is not comparable unless the question names the unit
    plain = menu("0-3", "4-6", "7+")
    assert bucket_option("5 weeks", plain) is None
    assert bucket_option("5 weeks", plain, question="Notice period in weeks")["label"] == "4-6"
    assert bucket_option("2 months", plain, question="Notice period in weeks")["label"] == "7+"


def test_a_menu_that_is_not_made_of_ranges_is_not_read_as_one():
    assert bucket_option("3 years", menu("Yes", "No")) is None
    assert bucket_option("3", menu("Student", "Employed", "10")) is None
    assert bucket_option("2027", menu("2025", "2026", "2027"))["label"] == "2027"
    assert bucket_option("4 years", menu("5 years")) is None


def test_placeholders_and_disabled_options_are_not_ranges():
    labels = [{"label": "Select an option"}, {"label": "1-3 years", "disabled": True}, {"label": "3-5 years"}, {"label": "5+ years"}]
    assert bucket_option("2 years", labels) is None
    assert bucket_option("4 years", labels)["label"] == "3-5 years"
    assert parse_range("— select —") is None and parse_range("Please choose") is None


def test_parsers():
    assert parse_quantity("4 years") == (4.0, "year") and parse_quantity("six") == (6.0, None)
    assert parse_quantity("4 years of experience") == (4.0, "year")
    assert parse_range("1 - 3 years")["hi"] == 3.0 and parse_range("5+")["lo"] == 5.0
    assert parse_range("Over 5 years")["lo_open"] is True and parse_range("Less than 1 year")["hi_open"] is True
    assert parse_range("1-3 hours") is None
    assert menu_unit("", YEARS) == "year" and menu_unit("Notice period in weeks", menu("0-3")) == "week" and menu_unit("", menu("0-3")) is None


def test_choice_uses_the_bucket_when_the_answer_is_a_quantity():
    assert choice("4 years", YEARS, question="Years of experience")["label"] == "3-5 years"
    assert choice("4", YEARS, question="How many years of Python experience do you have?")["label"] == "3-5 years"
    assert choice("4 years", [{"label": "3-5 years"}], question="Experience")["label"] == "3-5 years"   # what the second stage asks
    assert choice("7 years", [{"label": "3-5 years"}], question="Experience") is None
    # the old exact readings are untouched
    assert choice("65", menu("50%-70%", "70%-100%"), question="Grade")["label"] == "50%-70%"
    assert choice("Yes", menu("Yes", "No"), question="Are you over 18?")["label"] == "Yes"
