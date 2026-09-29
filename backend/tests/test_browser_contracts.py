import json
import math

import httpx
import pytest

from jobagent.automation.adapters import get_adapter, capabilities
from jobagent.automation.answers import resolve_answer
from jobagent.automation.engines import SystemOneEngine, benchmark_snapshot, validate_choice
from jobagent.automation.types import Decision, HumanRequired, InvalidDecision, Operation


@pytest.mark.parametrize("answer", [
    {"choice": "a", "confidence": 1, "probabilities": {"a": math.nan, "b": 0}},
    {"choice": "a", "confidence": 1, "probabilities": {"a": .1, "b": .9}},
    {"choice": "#selector", "confidence": 1, "probabilities": {"a": 1, "b": 0}},
    {"choice": "a", "confidence": 1, "probabilities": {"a": 1}},
    {"choice": "a", "confidence": True, "probabilities": {"a": 1, "b": 0}},
])
def test_invalid_probability_contract_rejected(answer):
    with pytest.raises(InvalidDecision):
        validate_choice(answer, {"a", "b"})


def test_decision_rejects_executable_fields_and_missing_target():
    with pytest.raises(InvalidDecision):
        Decision.from_dict({"operation": "CLICK", "target": 1, "selector": "#submit"})
    with pytest.raises(InvalidDecision):
        Decision(Operation.CLICK)
    with pytest.raises(InvalidDecision):
        Decision(Operation.TYPE_TEXT, True)


async def test_systemone_request_and_selected_head_validation():
    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/systemone"
        assert body["questions"]["operation"]["type"] == "choice"
        answers = {}
        for key, question in body["questions"].items():
            selected = "TYPE_TEXT" if key == "operation" else next(iter(question["criteria"]))
            answers[key] = {"choice": selected, "confidence": .99,
                            "probabilities": {option: int(option == selected) for option in question["criteria"]}}
        return httpx.Response(200, json={"answers": answers, "model": "fixture-model"})
    engine = SystemOneEngine(transport=httpx.MockTransport(handle))
    decision = await engine.decide(benchmark_snapshot(), "Fill email")
    assert decision.operation == Operation.TYPE_TEXT
    assert decision.target == 1
    assert decision.metadata["model"] == "fixture-model"


async def test_cloud_requires_key_and_local_requires_loopback():
    with pytest.raises(ValueError):
        SystemOneEngine("laya", endpoint="https://example.com")
    with pytest.raises(HumanRequired):
        await SystemOneEngine("jev").decide(benchmark_snapshot(), "Fill email")


def test_adapters_do_not_allow_spoofed_domain_or_override_manual_source():
    assert get_adapter("https://boards.greenhouse.io/example").name == "greenhouse"
    assert get_adapter("https://greenhouse.io.evil.test/example").name == "generic"
    assert get_adapter("https://www.linkedin.com/jobs/1", "greenhouse").manual_only
    assert len(capabilities()) == 12


def test_unknown_contradictory_or_sensitive_answers_are_not_invented():
    facts = [{"id": "1", "key": "Email address", "value": "a@example.test", "verification_status": "proposed"}]
    assert resolve_answer("Email", facts)["answer"] is None
    facts[0]["verification_status"] = "verified"
    assert resolve_answer("Email", facts)["answer"] == "a@example.test"
    facts.append({**facts[0], "id": "2", "value": "b@example.test"})
    assert resolve_answer("Email", facts)["answer"] is None
    assert resolve_answer("Gender", [{"id": "g", "key": "gender", "value": "example", "verification_status": "verified"}])["answer"] is None


def test_exact_saved_question_answer_with_provenance():
    answer = resolve_answer("Why this company?", [{"id": "saved", "key": "why this company", "value": "Verified personal answer",
                                                  "category": "saved_answer", "verification_status": "verified"}])
    assert answer["answer"] == "Verified personal answer"
    assert answer["fact_ids"] == ["saved"]


def test_explicit_sensitive_question_policy_does_not_authorize_other_questions():
    facts = [{"id": "application-privacy-answer", "key": "I agree to the privacy policy", "value": "Yes",
              "category": "saved_answer", "verification_status": "verified"}]
    policies = {"legal_certification": "ask_me", "questions": {
        "i agree to the privacy policy": {"action": "saved_answer", "fact_id": "application-privacy-answer"},
    }}
    privacy = resolve_answer("I agree to the privacy policy", facts, policies=policies)
    assert privacy["answer"] == "Yes"
    assert privacy["verified"] is True
    assert privacy["kind"] == "legal_certification"
    assert privacy["fact_ids"] == ["application-privacy-answer"]
    assert resolve_answer("I certify all statements are accurate", facts, policies=policies)["answer"] is None
    facts[0]["verification_status"] = "proposed"
    assert resolve_answer("I agree to the privacy policy", facts, policies=policies)["answer"] is None


async def test_jev_budget_gate_runs_before_network():
    calls = []
    def request_handler(request):
        calls.append(request)
        return httpx.Response(500)
    engine = SystemOneEngine("jev", api_key="fixture-key", authorize=lambda: False,
                             transport=httpx.MockTransport(request_handler))
    with pytest.raises(HumanRequired, match="budget"):
        await engine.decide(benchmark_snapshot(), "Fill email")
    assert calls == []


async def test_jev_records_valid_usage_even_when_decision_is_invalid():
    recorded = []
    engine = SystemOneEngine("jev", api_key="fixture-key", authorize=lambda: True, record=recorded.append,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "answers": {}, "usage": {"input_tokens": 100, "output_tokens": 5}})))
    with pytest.raises(InvalidDecision):
        await engine.decide(benchmark_snapshot(), "Fill email")
    assert recorded == [{"input_tokens": 100, "output_tokens": 5}]
