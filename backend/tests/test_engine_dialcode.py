"""'Which country's dial code?' has ONE reading, shared by Auto Apply and the browser assistant.

Workday asks 'Country Phone Code'; the engine reads any question about a phone as 'the phone' and answers with the whole
number (+441234567890), which was offered for a menu of countries. ``automation/dialcode.py`` takes every wording of that
question out of the engine's hands: the option that carries the dial code of the verified number is the answer, and
without such an option there is no answer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import seed_candidate  # noqa: E402
from jobagent.automation import dialcode, runner  # noqa: E402
from jobagent.automation.dialcode import is_dial_code_question, resolve_dial_code  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.extension import resolver  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402

JOB = {"country": "United Kingdom"}
OPTIONS = [{"label": "Select...", "value": ""}, {"label": "Spain (+34)", "value": "es"},
           {"label": "United Kingdom (+44)", "value": "gb"}, {"label": "United States (+1)", "value": "us"}]
DIAL_CODE_WORDINGS = [
    "Phone country code", "Telephone country code", "Country Phone Code", "Country code (phone)", "Mobile country code",
    "Dialling code", "Dialing code", "Calling code", "Dial code", "International dial code", "Phone prefix", "Telephone prefix",
    "Mobile prefix", "Country prefix", "International prefix", "Phone code", "Phone number country code",
    "Prefijo telefónico", "Prefijo internacional", "Prefijo del país", "Código de marcación", "Código de país del teléfono",
    "Código telefónico", "Código del teléfono"]
NOT_DIAL_CODE = ["Country code", "Country", "Phone", "Phone number", "Country of residence", "Two letter country code", "Código de país",
                 "Teléfono móvil", "Prefijo", "Nombre", "Postcode", "Phone type", "Device type"]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    return CandidateEngine(seed_candidate(Database(tmp_path / "meridian.sqlite3")), as_of="2026-10-14")


@pytest.mark.parametrize("wording", DIAL_CODE_WORDINGS)
def test_every_wording_of_the_question_picks_the_option_that_carries_the_code_of_the_number(engine, wording):
    assert is_dial_code_question(wording)
    result = resolve_dial_code(wording, OPTIONS, engine.answer_question, JOB)
    assert result["answer"] == "United Kingdom (+44)" and result["form_value"] == "United Kingdom (+44)"
    assert result["question"] == wording and result["fact_ids"] and result["inference_rules"]
    assert "+441234567890" not in str(result["answer"])


@pytest.mark.parametrize("wording", NOT_DIAL_CODE)
def test_other_questions_are_not_dial_code_questions(engine, wording):
    assert not is_dial_code_question(wording)
    assert resolve_dial_code(wording, OPTIONS, engine.answer_question, JOB) is None


def test_the_country_of_residence_decides_between_options_that_share_a_code(engine):
    nanp = [{"label": "United States (+1)", "value": "us"}, {"label": "Canada (+1)", "value": "ca"}]
    assert resolve_dial_code("Country Phone Code", nanp, engine.answer_question, JOB)["answer"] is None  # +44 is not +1
    sharing = [{"label": "Jersey (+44)", "value": "je"}, {"label": "United Kingdom (+44)", "value": "gb"},
               {"label": "Isle of Man (+44)", "value": "im"}]
    assert resolve_dial_code("Country Phone Code", sharing, engine.answer_question, JOB)["answer"] == "United Kingdom (+44)"


def test_no_option_carries_the_code_and_the_whole_number_is_never_the_answer(engine):
    for options in ([{"label": "Spain (+34)", "value": "es"}], [{"label": "Select", "value": ""}], [{"label": "UK", "value": "gb"}]):
        result = resolve_dial_code("Country Phone Code", options, engine.answer_question, JOB)
        assert result["answer"] is None and result["root_cause"] == "BAD_ENUM_MAPPING" and result["verified"] is False, options
        assert result["fact_ids"] == [] and result["candidate_value"] is None
        assert "dial code" in result["reason"]


def test_a_menu_that_is_still_closed_is_the_callers_to_decide(engine):
    """Auto Apply opens a closed dropdown and asks again; the assistant cannot, so it gives no answer."""
    assert resolve_dial_code("Country Phone Code", [], engine.answer_question, JOB) is None
    refused = resolve_dial_code("Country Phone Code", [], engine.answer_question, JOB, closed="refuse")
    assert refused["answer"] is None and refused["root_cause"] == "BAD_ENUM_MAPPING"


def test_a_number_without_a_plus_has_no_dial_code_to_read(engine):
    def national(question, field, job):
        return {"answer": "07700900123", "fact_ids": ["f"]} if question == "Phone" else {"answer": "United Kingdom", "fact_ids": []}
    assert resolve_dial_code("Phone country code", OPTIONS, national, JOB)["answer"] is None


def test_auto_apply_and_the_assistant_share_this_implementation():
    """One reading of the question: neither carries a copy of the regex, the matching or the refusal."""
    assert runner.resolve_dial_code is dialcode.resolve_dial_code
    assert resolver.resolve_dial_code is dialcode.resolve_dial_code
    assert not hasattr(resolver, "DIAL_CODE") and not hasattr(resolver.FieldResolver, "_phone_country_code")
