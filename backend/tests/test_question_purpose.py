from datetime import date

import pytest

from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.semantics import interpret
from jobagent.intelligence.store import policy
import test_candidate_intelligence

knowledge = test_candidate_intelligence.knowledge


@pytest.mark.parametrize(
    "question",
    [
        "Why would you like to join us?",
        "Why do you want to work at this company?",
        "What attracts you to this role?",
        "Explain your motivation to apply and how your projects are relevant.",
    ],
)
def test_motivation_purpose_outranks_topic_words(question):
    assert interpret(question, {"tag": "textarea"})["intent"] == "MOTIVATION"


@pytest.mark.parametrize("key", ["description", "section_label", "section"])
@pytest.mark.parametrize(
    "question", ["Anything else you would like to share?", "Additional context", "Further information"]
)
def test_generic_prompt_uses_its_qualifier(question, key):
    assert (
        interpret(question, {key: "Such as your motivation to apply or additional context for your application."})[
            "intent"
        ]
        == "MOTIVATION"
    )


@pytest.mark.parametrize("question", ["Anything else?", "Additional information", "Tell us more about yourself"])
def test_generic_context_is_not_a_project_request(question):
    assert interpret(question)["intent"] == "ADDITIONAL_CONTEXT"


@pytest.mark.parametrize(
    "question",
    [
        "How are you currently using AI to improve your day-to-day work?",
        "Describe ways you use artificial intelligence in your software workflow.",
        "How do you apply LLMs when working with projects and data?",
    ],
)
def test_ai_workflow_purpose_is_preserved(question):
    assert interpret(question)["intent"] == "AI_USAGE"


@pytest.mark.parametrize(
    "question",
    ["When can you start?", "What is your availability?", "How soon can you join us?", "Your earliest start date"],
)
def test_start_date_stays_temporal(question):
    assert interpret(question)["intent"] == "START_DATE"


@pytest.mark.parametrize(
    "question",
    [
        "If you answered yes above, confirm your current visa type and status.",
        "Please describe your UK immigration status.",
        "Give details of the visa you currently hold.",
    ],
)
def test_visa_status_explains_confirmed_context_without_inventing_rights(knowledge, question):
    result = CandidateEngine(knowledge, as_of=date(2026, 10, 4)).answer_question(
        question, {"tag": "textarea"}, {"location": "London"}
    )
    assert result["canonical_intent"] == "VISA"
    assert "Student visa" in result["answer"]
    assert "South University" in result["answer"]
    assert "September 2027" in result["answer"]
    assert "visa_status_with_verified_study_context" in result["rules_used"]
    assert not any(
        word in result["answer"].lower() for word in ["unrestricted", "expiry", "expire", "20 hours", "permanent"]
    )
    assert result["requires_review"]


def test_visa_simple_enum_and_missing_expiry(knowledge):
    engine = CandidateEngine(knowledge, as_of=date(2026, 10, 4))
    assert (
        engine.answer_question("Visa type", {"options": [{"label": "Student", "value": "student"}]})["answer"]
        == "Student"
    )
    assert engine.answer_question("When does your visa expire?", {"type": "date"})["answer"] is None


def test_motivation_is_not_split_into_independent_project_answer(knowledge):
    result = CandidateEngine(knowledge).answer_question(
        "Explain your motivation to apply and how your projects are relevant.",
        {"tag": "textarea"},
        {
            "company": "Example Rail",
            "title": "Software Engineer",
            "description": "Build reliable backend services for a rail platform.",
        },
    )
    assert result["canonical_intent"] == "MOTIVATION"
    assert "compose_independent_intents" not in result["rules_used"]


def test_explicit_privacy_policy_takes_precedence_over_previous_decline_policy(knowledge):
    # Restore afterward because this module uses the shared synthetic fixture.
    policy(knowledge, "personal_disclosures", "leave_blank")
    try:
        engine = CandidateEngine(knowledge)
        for question in [
            "Gender",
            "What are your preferred pronouns?",
            "Do you have a disability?",
            "What is your religion?",
        ]:
            result = engine.answer_question(
                question, {"required": True, "options": [{"label": "Prefer not to say", "value": "decline"}]}
            )
            assert result["answer"] is None
            assert result["leave_blank"] is True
            assert result["confidence_level"] == "POLICY"
    finally:
        policy(knowledge, "personal_disclosures", "leave_blank", confirmed=False)


def test_compound_motivation_does_not_omit_separate_visa_request(knowledge):
    result = CandidateEngine(knowledge).answer_question(
        "Why do you want to join us and what is your current visa type?",
        {"tag": "textarea"},
        {
            "company": "Example Rail",
            "title": "Software Engineer",
            "description": "Build reliable backend services for a rail platform.",
        },
    )
    assert result["canonical_intents"] == ["MOTIVATION", "VISA"]
    assert result["answer"] is None
    assert "Student visa" in result["components"][1]["answer"]


def test_requested_project_example_is_prose_even_when_question_names_skill(knowledge):
    result = CandidateEngine(knowledge).answer_question(
        "Describe a project you developed with Python.", {"tag": "textarea"}
    )
    assert result["canonical_intent"] == "PROJECT"
    assert result["answer"] is None
    assert result["root_cause"] == "LLM_SETUP_REQUIRED"
    assert result["requires_review"]
