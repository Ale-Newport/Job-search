import pytest
from jobagent.automation.reasoning import profile_resolution, conditional_state
from jobagent.automation.adapters.base import ATSAdapter
from jobagent.application_profile import write_profile
from jobagent.db import Database


def fact(key, value):
    return dict(id=key, key=key, value=value, verification_status="verified", category="application_profile")


@pytest.fixture
def facts():
    return [fact("earliest_start_month", "2027-09"), fact("masters_completion_month", "2027-09")]


def test_general_months_produce_different_answers_without_saved_questions(facts):
    notice = profile_resolution(
        "What is your current notice period?",
        facts,
        element={
            "role": "combobox",
            "options": [{"label": "Other", "value": "other"}, {"label": "1 month", "value": "one"}],
        },
    )
    assert notice["answer"] == "Other"
    assert not notice["verified"] and notice["inferred"] and notice["requires_review"]
    assert "contractual notice" in notice["reason"]
    detail = profile_resolution(
        "If you selected other to the above, please specify:",
        facts,
        element={"_condition": {"applies": True, "parent": "What is your current notice period?"}},
    )
    assert "September 2027" in detail["answer"]
    assert "immediately" not in detail["answer"].lower()
    assert profile_resolution("When could you join us?", facts)["answer"].startswith("I can start from September 2027")
    assert profile_resolution("What is your graduation year?", facts)["answer"] == "2027"
    assert profile_resolution("Can you start in August 2027?", facts)["answer"] == "No"
    assert profile_resolution("Can you start in October 2027?", facts)["answer"] == "Yes"
    day = profile_resolution("Earliest start date", facts, element={"type": "date"})
    assert day["answer"] == "2027-09-01" and "placeholder" in day["reason"] and not day["verified"]


def test_notice_is_not_misrepresented_as_immediate_or_rounded_duration(facts):
    result = profile_resolution(
        "What is your current notice period?",
        facts,
        element={
            "role": "combobox",
            "options": [{"label": "Immediately available", "value": "now"}, {"label": "3 months", "value": "3"}],
        },
    )
    assert result["answer"] is None
    assert profile_resolution("Notice period", facts + [fact("notice_period", "2 weeks")])["answer"] == "2 weeks"
    assert profile_resolution("Notice period", [fact("masters_completion_month", "2027-09")])["answer"] is None
    for question in ["What is your gender?", "Do you require sponsorship?", "What are your salary expectations?"]:
        assert profile_resolution(question, facts)["answer"] is None
    assert (
        profile_resolution(
            "Earliest start date", facts + [dict(fact("earliest_start_month", "2028-01"), id="conflict")]
        )["answer"]
        is None
    )


def element(node, label, role="textbox", **kwargs):
    return dict(node_id=node, document_id="d", form_id="f", label=label, role=role, value="", **kwargs)


def test_condition_follows_observed_value_not_fact_and_honours_explicit_reference():
    adapter = ATSAdapter()
    parent = element(
        "1",
        "Notice period",
        "combobox",
        options=[{"label": "Other", "value": "o"}, {"label": "2 weeks", "value": "two"}],
    )
    unrelated = element("2", "Preferred workplace", "combobox")
    child = element("3", 'If you answered "Other" to "Notice period", please explain:')
    parent["value"] = "two"
    unrelated["value"] = "Other"
    state = conditional_state(child, [parent, unrelated, child], adapter)
    assert state["applies"] is False and state["parent"] == "Notice period"
    parent["value"] = "o"
    assert conditional_state(child, [parent, unrelated, child], adapter)["applies"] is True
    parent["value"] = ""
    assert conditional_state(child, [parent, unrelated, child], adapter)["applies"] is None
    parent["form_id"] = "different"
    assert conditional_state(child, [parent, unrelated, child], adapter)["applies"] is None


def test_conditional_yes_no_radios_and_quoted_arbitrary_options():
    adapter = ATSAdapter()
    yes = element("1", "Yes", "radio", context="Need sponsorship?", group="visa", checked=False)
    no = element("2", "No", "radio", context="Need sponsorship?", group="visa", checked=True)
    child = element("3", "If you answered yes to the above, confirm your visa status:")
    assert conditional_state(child, [yes, no, child], adapter)["applies"] is False
    no["checked"] = False
    assert conditional_state(child, [yes, no, child], adapter)["applies"] is None
    yes["checked"] = True
    assert conditional_state(child, [yes, no, child], adapter)["applies"] is True
    parent = element("4", "Workplace", "combobox")
    parent["value"] = "Remote"
    follow = element("5", 'If you selected "On-site", provide your commuting preference:')
    assert conditional_state(follow, [parent, follow], adapter)["applies"] is False


def test_profile_month_validation_and_explicit_general_fields(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    write_profile(db, "earliest_start_month", "2027-09", True)
    with pytest.raises(ValueError):
        write_profile(db, "masters_completion_month", "September 2027", True)
    assert db.one("select value from facts where key='earliest_start_month'")["value"] == "2027-09"


async def test_semantic_drafts_use_professional_facts_and_remain_unverified(tmp_path):
    from jobagent.orchestrator import Orchestrator

    db = Database(tmp_path / "test.sqlite")
    orchestrator = Orchestrator(db, tmp_path)

    class Model:
        async def infer_field(self, config, question, job, ids, field):
            assert ids == ["p"]
            return {"answer": "I developed a document retrieval system.", "facts_used": ["p"], "confidence": 0.6}

    orchestrator.text_service = Model()
    reason = orchestrator.field_reasoner({}, [dict(fact("project", "Built retrieval"), id="p", category="project")], {})
    answer = await reason("Tell us about a technically challenging project.", {"tag": "textarea"})
    assert answer["inferred"] and not answer["verified"] and answer["confidence"] == 0.6
    for q in [
        "Do you require sponsorship?",
        "Are you willing to attend the office?",
        "What is your sexual orientation?",
        "What is your current employer?",
    ]:
        assert await reason(q, {"tag": "textarea"}) is None
