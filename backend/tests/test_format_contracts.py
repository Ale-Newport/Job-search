"""A date asked for as tokens (dd-mm-yyyy, MM.YYYY, YYYY/MM/DD) is written exactly so, in any order and with any separator."""
import pytest

import jobagent.automation  # noqa: F401  (the package imports the engine in the order its modules need)
from jobagent.intelligence.formatting import format_date, infer_format, validate_format


@pytest.mark.parametrize("question,contract", [
    ("Graduation date (DD/MM/YYYY)", "DD/MM/YYYY"), ("Graduation date (MM/YYYY)", "MM/YYYY"), ("Date (YYYY-MM-DD)", "ISO_DATE"),
    ("Graduation date (DD-MM-YYYY)", "DATE:%d-%m-%Y"), ("Graduation date (dd.mm.yyyy)", "DATE:%d.%m.%Y"),
    ("Start (MM-YYYY)", "DATE:%m-%Y"), ("Start (YYYY/MM)", "DATE:%Y/%m"), ("Start (yyyy.mm.dd)", "DATE:%Y.%m.%d"),
    ("Fecha de inicio (DD/MM/AAAA)", "DD/MM/YYYY"), ("Fecha (dd-mm-aaaa)", "DATE:%d-%m-%Y"), ("Start (MM/DD/YY)", "DATE:%m/%d/%y"),
    ("Graduation year (YYYY)", "YYYY"), ("Notice period (in weeks)", "SHORT_TEXT"),
])
def test_the_tokens_of_a_date_are_its_contract(question, contract):
    assert infer_format(question, {}) == contract


@pytest.mark.parametrize("value,question,expected", [
    ("2027-09-27", "Graduation date (DD-MM-YYYY)", "27-09-2027"),
    ("2027-09-27", "Date (dd.mm.yyyy)", "27.09.2027"),
    ("2027-09", "Start (MM-YYYY)", "09-2027"),
    ("2027-09-27", "Start (YYYY/MM)", "2027/09"),
    ("2027-09-27", "Start (MM/DD/YY)", "09/27/27"),
    ("2027", "Graduation (YYYY)", "2027"),
])
def test_a_date_is_written_with_the_tokens_of_the_question(value, question, expected):
    assert format_date(value, question, {}) == (expected, None)


@pytest.mark.parametrize("value,question,error", [
    ("2027-09", "Graduation date (DD-MM-YYYY)", "MISSING_EXACT_DATE"),   # a month is not a day: never one made up
    ("2027", "Start (MM-YYYY)", "MISSING_DATE_MONTH"),
    ("2027", "Start (YYYY/MM)", "MISSING_DATE_MONTH"),
])
def test_precision_is_never_manufactured(value, question, error):
    assert format_date(value, question, {}) == (None, error)


@pytest.mark.parametrize("value,question,ok", [
    ("27-09-2027", "Graduation date (DD-MM-YYYY)", True), ("27/09/2027", "Graduation date (DD-MM-YYYY)", False),
    ("31-02-2027", "Graduation date (DD-MM-YYYY)", False), ("09-2027", "Start (MM-YYYY)", True), ("13-2027", "Start (MM-YYYY)", False),
    ("2027/09", "Start (YYYY/MM)", True), ("27.09.2027", "Date (dd.mm.yyyy)", True),
])
def test_a_written_date_must_follow_its_tokens(value, question, ok):
    assert (validate_format(value, question, {}) is None) is ok
