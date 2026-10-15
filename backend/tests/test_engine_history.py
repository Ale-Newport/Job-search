"""A field inside ONE entry of a work or education history is about THAT entry.

'Start date', 'Date started', 'Location', 'City' and 'Country' under 'Work Experience 1' were answered, green and on Tab, with the
candidate's availability date (a future date for a past job) and current city; 'School or University', 'Degree' and 'Field of
Study' gave the same qualification in 'Education 1' and 'Education 2'. The heading names the entry (``Work Experience 2``,
``Education 1``, or the 2nd of two sections under one heading): answered from that entry's record, or not at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import JOB_UK, ask, engine_for  # noqa: E402
from jobagent.intelligence.semantics import entry_attribute, history_block, interpret  # noqa: E402

JOBS = ("Work Experience 1", "Work Experience 2", "Work Experience", "Employment History", "Previous employment",
        "Work history", "Voluntary work", "Professional experience", "Experiencia laboral", "Experiencia laboral 1", "Experience")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    return engine_for(tmp_path_factory.mktemp("history"), "student")


@pytest.mark.parametrize("heading", JOBS)
@pytest.mark.parametrize("label", ["Start date", "Date started", "Employment start date", "Location", "City", "Country", "Desde",
                                   "Fecha de inicio", "Ubicación", "Ciudad", "País"])
def test_a_personal_fact_is_never_the_answer_of_a_field_of_a_job_entry(engine, heading, label):
    """Never the availability date or the city the candidate lives in: those are not when and where a past job was."""
    result = ask(engine, label, JOB_UK, section_label=heading)
    assert result["answer"] not in {"2027-09-28", "28 September 2027", "London", "United Kingdom", "London, United Kingdom"}, result
    if heading not in {"Work Experience 1", "Experiencia laboral 1"} or label in {"Location", "City", "Country", "Ubicación", "Ciudad", "País"}:
        assert result["answer"] is None and result["root_cause"] in {"MISSING_HISTORY_ENTRY", "MISSING_FACT"}, result
        assert result["verified"] is False and result["fact_ids"] == [], result


def test_the_first_job_entry_is_the_most_recent_job_and_every_answer_is_derived_from_it(engine):
    expected = {"Job Title": "Developer", "Company": "Example Studio", "Position": "Developer", "Employer": "Example Studio",
                "Start date": "2025-07-01", "End date": "2025-09-30", "Cargo": "Developer", "Empresa": "Example Studio"}
    for label, want in expected.items():
        field = {"type": "date"} if "date" in label else {}
        result = ask(engine, label, JOB_UK, section_label="Work Experience 1", **field)
        assert result["answer"] == want, (label, result["answer"])
        assert result["confidence_level"] == "DERIVED_HIGH" and result["verified"] is False and result["fact_ids"], label
        assert result["rules_used"][0].startswith("work_entry_1:"), result["rules_used"]


def test_dates_are_written_the_way_the_field_asks(engine):
    asked = {"From (MM/YYYY)": "07/2025", "To (MM/YYYY)": "09/2025", "Start date (YYYY-MM)": "2025-07"}
    for label, want in asked.items():
        assert ask(engine, label, JOB_UK, section_label="Work Experience 1")["answer"] == want, label
    assert ask(engine, "From", JOB_UK, section_label="Work Experience 1", type="date")["answer"] == "2025-07-01"


def test_a_job_the_profile_does_not_have_is_not_invented_and_is_not_the_first_ones(engine):
    for label in ("Job Title", "Company", "From", "To", "Start date"):
        result = ask(engine, label, JOB_UK, section_label="Work Experience 2")
        assert result["answer"] is None and result["root_cause"] == "MISSING_HISTORY_ENTRY", (label, result)
        assert "holds 1" in result["explanation"]


def test_a_detail_the_entry_does_not_hold_is_not_taken_from_somewhere_else(engine):
    """Example Studio has no location in the profile: the city the candidate LIVES in is not where that job was."""
    for label in ("Location", "City", "Country"):
        result = ask(engine, label, JOB_UK, section_label="Work Experience 1")
        assert result["answer"] is None and result["root_cause"] == "MISSING_HISTORY_ENTRY", (label, result)


def test_a_heading_that_does_not_say_which_job_is_not_guessed(engine):
    for heading in ("Work Experience", "Employment History", "Previous employment"):
        result = ask(engine, "Job Title", JOB_UK, section_label=heading)
        assert result["answer"] is None and "cannot tell which entry" in result["explanation"], (heading, result)


def test_a_narrative_inside_a_job_entry_is_still_a_narrative(engine):
    for label in ("Role description", "Responsibilities", "Describe your achievements"):
        result = ask(engine, label, JOB_UK, section_label="Work Experience 1", tag="textarea")
        assert result["root_cause"] != "MISSING_HISTORY_ENTRY", (label, result)


# ------------------------------------------------------------------------------------------------------- education entries
def test_two_education_entries_are_two_records_the_most_advanced_first(engine):
    first = {"School or University": "South University", "Degree": "MSc Artificial Intelligence", "Field of Study": "Artificial Intelligence"}
    second = {"School or University": "North University", "Degree": "BSc Computer Science", "Field of Study": "Computer Science"}
    for label, want in first.items():
        assert ask(engine, label, JOB_UK, section_label="Education 1")["answer"] == want, label
    for label, want in second.items():
        result = ask(engine, label, JOB_UK, section_label="Education 2")
        assert result["answer"] == want and "education_entry_2" in result["rules_used"], (label, result["answer"])
    assert ask(engine, "Start date", JOB_UK, section_label="Education 2")["answer"] == "01 September 2023"
    assert ask(engine, "Start date", JOB_UK, section_label="Education 1")["answer"] == "28 September 2026"


def test_a_third_education_entry_is_not_the_first(engine):
    for label in ("School or University", "Degree", "Field of Study", "Start date"):
        result = ask(engine, label, JOB_UK, section_label="Education 3")
        assert result["answer"] is None and result["root_cause"] == "MISSING_EDUCATION_ENTRY", (label, result)


def test_an_education_heading_without_a_number_keeps_the_current_study(engine):
    assert ask(engine, "School or University", JOB_UK, section_label="Education")["answer"] == "South University"


def test_a_place_in_an_education_entry_is_not_where_the_candidate_lives(engine):
    for heading in ("Education", "Education 1", "Education 2", "Educación 1"):
        for label in ("Location", "City", "Country"):
            result = ask(engine, label, JOB_UK, section_label=heading)
            assert result["answer"] is None and result["root_cause"] == "MISSING_HISTORY_ENTRY", (heading, label, result["answer"])


def test_a_block_the_page_repeats_under_one_heading_is_counted_by_the_server_not_the_client(engine):
    field = {"section_label": "Employment", "_block_index": 2}
    assert ask(engine, "Job Title", JOB_UK, **field)["root_cause"] == "MISSING_HISTORY_ENTRY"
    assert ask(engine, "Job Title", JOB_UK, section_label="Employment", _block_index=1)["answer"] == "Developer"


# ---------------------------------------------------------------------------------------------------- the parts, one by one
@pytest.mark.parametrize("heading,kind,ordinal", [
    ("Work Experience 1", "experience", 1), ("Work Experience 2", "experience", 2), ("Employment History", "experience", None),
    ("Education #1", "education", 1), ("Education 2", "education", 2), ("Experience (2)", "experience", 2),
    ("Voluntary work", "experience", None), ("Experiencia laboral 2", "experience", 2), ("Educación", "education", None),
    ("Education & Experience", "mixed", None), ("Personal information", None, None), ("Application details", None, None),
    ("Skills and experience", None, None), ("Second job", None, None), ("Work Experience 27", "experience", None),
    # 'Voluntary self-identification' is the equal-opportunities survey, not a volunteering history (wave 5)
    ("Voluntary self-identification", None, None), ("Voluntary Self-Identification of Disability", None, None),
    ("Volunteering", "experience", None), ("Volunteer experience", "experience", None)])
def test_which_entry_a_heading_names(heading, kind, ordinal):
    block = history_block({"section_label": heading})
    assert (block["kind"], block["ordinal"]) == (kind, ordinal) if kind else block is None, heading


@pytest.mark.parametrize("label,attribute", [
    ("From", "start"), ("To", "end"), ("Job Title", "title"), ("Company", "company"), ("Location", "location"),
    ("Start date", "start"), ("Date started", "start"), ("Employment start date", "start"), ("End date (required)", "end"),
    ("I currently work here", "current"), ("School or University", "school"), ("Desde", "start"), ("Hasta", "end"),
    # a bare 'Salary' inside an entry is what THAT job paid (RS-06: never the salary expected of the next one)
    ("Role description", None), ("Salary", "salary"), ("First name", None)])
def test_what_a_label_inside_an_entry_asks(label, attribute):
    assert entry_attribute(label) == attribute, label


def test_a_label_is_read_the_same_outside_any_heading(engine):
    """The entry logic never touches a field that is not inside a history: 'Start date' alone is the availability."""
    assert interpret("Start date", {})["history"] is None
    assert ask(engine, "Start date", JOB_UK, section_label="Application details", type="date")["answer"] == "2027-09-28"
    assert ask(engine, "Location", JOB_UK, section_label="Personal information")["answer"] == "London, United Kingdom"


# ---------------------------------------------------------------------------------------------------- the Profile never revives an entry
@pytest.mark.parametrize("heading", ["Job 1", "Position", "Employer", "Studies", "Awards", "Referee", "Emergency contact", "Trabajo 1"])
@pytest.mark.parametrize("label,key", [("Location", "location"), ("City", "location"), ("Start date", "availability"),
                                       ("Address", "address"), ("Postcode", "postcode"), ("Zip code", "postcode"), ("Address line 1", "address")])
def test_W5_a_profile_fact_never_revives_a_label_inside_an_entry_of_a_history(tmp_path, heading, label, key):
    """A person who has filled in the Profile (their location, availability...) is the usual case: the engine refuses 'Location' under 'Job 1'
    and the confirmed-answer fallback must not then answer it with the PROFILE's location (the same 'Location' of every block of the page)."""
    import asyncio

    from jobagent.application_profile import application_knowledge, write_profile

    engine = engine_for(tmp_path, "student")
    write_profile(engine.db, key, {"availability": "2027-09-28", "address": "1 Test Street", "postcode": "N1 1AA"}.get(key, "London, United Kingdom"), True)
    rows = engine.db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    facts, _skipped = application_knowledge(engine.db, {"id": "", "company": "Acme", "location": "London, UK"}, rows)
    field = {"type": "text", "tag": "input", "role": "textbox", "section_label": heading}
    result = asyncio.run(engine.answer(label, field, dict(JOB_UK), audit=False, generate=False))
    result = engine.confirmed_fallback(label, result, facts, {})
    # (an address under 'Referee' / 'Employer' / 'Emergency contact' is read as that PARTY's: refused all the same)
    assert result["answer"] is None and result["canonical_intent"] in {"HISTORY_ENTRY", "OTHER_PARTY"}, (heading, label, result["answer"], result["canonical_intent"])
    if key not in {"address", "postcode"} or heading not in {"Referee", "Employer", "Emergency contact"}:
        assert result["canonical_intent"] == "HISTORY_ENTRY", (heading, label, result["canonical_intent"])
    # the same label under no heading is the applicant's own, and the Profile may answer it
    plain_field = {"type": "text", "tag": "input", "role": "textbox"}
    own = engine.confirmed_fallback(label, asyncio.run(engine.answer(label, plain_field, dict(JOB_UK), audit=False, generate=False)), facts, {})
    assert own["answer"] is not None, (label, own)


@pytest.mark.parametrize("heading", ["Academic background", "Formación académica", "Estudios", "Educación"])
@pytest.mark.parametrize("label", ["Start date", "End date", "Fecha de inicio"])
def test_W5_a_bare_date_under_any_education_heading_reads_the_education_block_like_the_english_one(engine, heading, label):
    """'Academic background' was not read as an education block ('academic' was missing from the words that make one), so its 'Start date'
    was the candidate's AVAILABILITY; 'Formación académica' as a HEADING decided that the question asked for the highest degree, so its
    'Start date' was that degree's. The heading names the block: the same label reads the same under every heading that says 'education'."""
    answer = ask(engine, label, JOB_UK, type="text", section_label=heading)["answer"]
    assert answer == ask(engine, label, JOB_UK, type="text", section_label="Education")["answer"], (heading, label, answer)
    assert answer not in {"28 September 2027", "2027-09-28"}, (heading, label, answer)  # the availability of this candidate
