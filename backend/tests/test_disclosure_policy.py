import pytest

from jobagent.automation.answers import resolve_answer
from jobagent.automation.disclosures import (
    personal_disclosure_kind,
    disclosure_resolution,
    disclosure_clear_operation,
    has_disclosure_value,
    inherit_disclosure_context,
)

POLICIES = {"personal_disclosures": {"action": "leave_blank"}}


@pytest.mark.parametrize(
    "question",
    [
        "Gender",
        "What is your gender identity?",
        "Please state your sex assigned at birth",
        "Preferred pronouns",
        "Which pronouns do you use?",
        "Please provide your personal pronouns",
        "Sexual orientation",
        "How do you describe your sexual orientation?",
        "Do you identify as LGBTQ?",
        "Ethnic origin",
        "What is your ethnicity?",
        "Which racial group do you identify with?",
        "Are you Hispanic or Latino?",
        "What is your national origin?",
        "Do you identify as an Indigenous person?",
        "Religion or belief",
        "What is your religious affiliation?",
        "Please state your faith",
        "Do you have a disability?",
        "Disability status",
        "Are you disabled?",
        "How has developing software affected your mental health?",
        "Health",
        "Tell us about your mental health",
        "Do you have any physical health conditions?",
        "Your medical history",
        "Do you consider yourself neurodivergent?",
        "Please describe your neurodiversity",
        "Do you need reasonable adjustments?",
        "Please describe your reasonable accommodations",
        "Date of birth",
        "What is your age?",
        "Marital status",
        "Veteran status",
        "Do you need accommodations during the application process?",
        "Will you require adjustments for your interview?",
        "Please state any accommodations you need for this assessment",
        "We are an equal opportunity employer regardless of gender or race. What is your gender?",
    ],
)
def test_applicant_disclosure_paraphrases_are_left_blank(question):
    result = disclosure_resolution(question, {"required": True}, POLICIES)
    assert result and result["leave_blank"] and result["answer"] is None
    assert result["skip_inline_question"]
    assert "required" in result["reason"]


@pytest.mark.parametrize(
    "question",
    [
        "Describe software you built for mental health services",
        "How would you design a platform for people with disabilities?",
        "Tell us about your experience improving gender fairness in machine learning models",
        "Why are you interested in our mental health product?",
        "How would you develop accessibility software for disabled users?",
        "Explain how you would test software for patients with a health condition",
        "We are an equal opportunity employer regardless of gender, race or religion. Why do you want this job?",
        "We do not discriminate based on age or disability. What are your salary expectations?",
        "We are committed to a diverse workforce. Tell us about your work experience.",
        "Are you authorised to work in the UK?",
        "What is your current visa type?",
        "What is your country of citizenship?",
        "What skills would you bring to our team?",
        "What are your beliefs about AI?",
        "What gives you faith in technology?",
        "We welcome candidates of every ethnicity. Why this role?",
        "We welcome people of every religion. What is your notice period?",
        "Our values celebrate diversity of gender and race. Describe your work experience.",
        "Do you need cloud infrastructure adjustments to deploy a model?",
        "Are you interested in the mental health sector?",
        "How would you approach gender inclusion in teams?",
        "Do you have experience working with mental health data?",
    ],
)
def test_work_context_and_employer_statements_are_not_disclosures(question):
    assert personal_disclosure_kind(question) is None
    assert disclosure_resolution(question, policies=POLICIES) is None


def test_weak_labels_use_local_description_and_section_not_unrelated_boilerplate():
    assert personal_disclosure_kind("Please select", {"section_label": "Gender identity"}) == "gender"
    assert (
        personal_disclosure_kind("Please specify", {"description": "Please describe your disability"}) == "disability"
    )
    assert personal_disclosure_kind("Email", {"section_label": "Gender identity"}) is None
    assert (
        personal_disclosure_kind("Why this company?", {"description": "We welcome all genders and ethnicities."})
        is None
    )


def test_policy_is_explicit_and_overrides_saved_exact_answer_and_decline_policy():
    question = "What is your gender identity?"
    fact = {
        "id": "saved",
        "key": question,
        "value": "Male",
        "verification_status": "verified",
        "profile_explicit": True,
    }
    assert resolve_answer(question, [fact])["answer"] == "Male"
    result = resolve_answer(question, [fact], policies={**POLICIES, "gender": {"action": "prefer_not_to_say"}})
    assert result["answer"] is None and result["leave_blank"]
    assert result["fact_ids"] == []


def test_only_observed_blank_or_clear_is_used_never_decline_surrogate():
    selected = {
        "role": "combobox",
        "value": "male",
        "operations": ["SELECT"],
        "options": [{"value": "", "label": "Select"}, {"value": "decline", "label": "Prefer not to say"}],
    }
    assert disclosure_clear_operation(selected) == ("SELECT", "")
    selected["options"][0]["disabled"] = True
    assert disclosure_clear_operation(selected) is None
    selected["operations"].append("CLEAR")
    assert disclosure_clear_operation(selected) == ("CLEAR", None)
    assert not has_disclosure_value({"role": "combobox", "selected_text": "Select…", "value": ""})
    assert not has_disclosure_value({"role": "radio", "value": "yes", "checked": False})


def test_conditional_disclosure_details_remain_blank_even_if_parent_unanswered():
    from jobagent.automation.adapters.base import ATSAdapter

    adapter = ATSAdapter()
    parent = {"document_id": "doc", "form_id": "form", "node_id": 1, "role": "combobox", "label": "Gender identity"}
    detail = {
        "document_id": "doc",
        "form_id": "form",
        "node_id": 2,
        "role": "textbox",
        "label": "If you answered Other, please specify",
        "required": True,
    }
    inherit_disclosure_context(detail, [parent, detail], adapter)
    assert disclosure_resolution(adapter.question(detail), detail, POLICIES)["leave_blank"]
    parent["label"] = "Notice period"
    inherit_disclosure_context(detail, [parent, detail], adapter)
    assert disclosure_resolution(adapter.question(detail), detail, POLICIES) is None


@pytest.mark.parametrize(
    "label",
    [
        "Additional information",
        "Further details",
        "Your response",
        "Tell us more about yourself",
        "Anything else you would like to share?",
        "Supporting information",
        "Please elaborate",
    ],
)
def test_generic_prompt_uses_candidate_disclosure_in_description(label):
    result = disclosure_resolution(
        label, {"description": "Please describe your current disability or medical condition"}, POLICIES
    )
    assert result and result["leave_blank"]
    assert (
        disclosure_resolution(
            label, {"description": "Tell us about software you developed for mental health users"}, POLICIES
        )
        is None
    )


@pytest.mark.parametrize(
    "question", ["Anything else you would like to share?", "Supporting information", "Please elaborate"]
)
def test_engine_generic_disclosure_cannot_fall_back_to_old_decline_policy(tmp_path, question):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.store import entity, policy

    db = Database(tmp_path / "privacy.sqlite")
    entity(db, "person", "Test applicant")
    policy(db, "personal_disclosures", "leave_blank")
    policy(db, "optional_demographics", "decline_or_blank")
    result = CandidateEngine(db).answer_question(
        question,
        {
            "description": "What is your gender identity?",
            "required": True,
            "options": [{"label": "Male", "value": "m"}, {"label": "Prefer not to say", "value": "decline"}],
            "role": "combobox",
        },
    )
    assert result["leave_blank"] and result["answer"] is None
    assert result["root_cause"] == "PERSONAL_DISCLOSURE_POLICY"
