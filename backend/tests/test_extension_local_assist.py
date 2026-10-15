"""The browser assistant with local models behind it: a menu the engine found no option for, and a value that has to be written
the way the field asks. Real routes, real engine, real queue; Laya and the local writing model are scripted at the HTTP layer.

What these pin down is the shape of the whole thing: the page's first answer never waits for a model; a field that a model works
on says so and is told when it ends; what a model may do is fenced (never a protected question, never an option nobody offered,
never a value that changed); and with the models gone, the setting off, or the model silent, the field is what it always was."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import bridge, el, http, make_app, posting, put_setting, radios, select, suggest_body  # noqa: E402
from jobagent.intelligence.local_models import LocalModels  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


class Engines:
    """Laya and Ollama as the HTTP services they are, answering what the test scripts."""

    def __init__(self, laya=None, ollama=None, *, up=True, gate=None):
        self.laya, self.ollama, self.up, self.gate = laya, ollama, up, gate
        self.calls: list[tuple[str, dict]] = []

    def transport(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            if not self.up:
                raise httpx.ConnectError("refused")
            if self.gate is not None and request.url.path in {"/v1/systemone", "/api/chat"}:
                await self.gate.wait()  # a model that takes its time: the field is "working on it" meanwhile
            path = request.url.path
            if path == "/healthz":
                return httpx.Response(200, json={"ok": True, "backend": "laya-mlx"})
            if path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct-q4_K_M"}]})
            body = json.loads(request.content)
            if path == "/v1/systemone":
                criteria = body["questions"]["option"]["criteria"]
                labels = {key: label for key, label in criteria.items() if key != "HUMAN_REQUIRED"}
                self.calls.append(("laya", {"question": body["state"]["question"], "options": list(labels.values()),
                                            "facts": body["state"]["candidate_facts"]}))
                wanted = self.laya(list(labels.values())) if callable(self.laya) else self.laya
                key = next((k for k, label in labels.items() if label == wanted), "HUMAN_REQUIRED")
                return httpx.Response(200, json={"model": "laya", "answers": {"option": {
                    "choice": key, "probabilities": {k: (1.0 if k == key else 0.0) for k in criteria}, "confidence": 0.9}}})
            if path == "/api/chat":
                payload = json.loads(body["messages"][1]["content"])
                self.calls.append(("ollama", payload))
                reply = self.ollama(payload) if callable(self.ollama) else self.ollama
                return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
            return httpx.Response(404)
        return httpx.MockTransport(handle)

    def install(self, app):
        app.state.local_models = LocalModels(settings=lambda: {"browser_engine": "laya"}, transport=self.transport())
        return self


@pytest.fixture
async def ctx(tmp_path):
    app = make_app(tmp_path)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        yield SimpleNamespace(app=app, client=client, sid=created["session_id"], cursor=created["cursor"])
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_local.close()
    await app.state.extension_research.close()


async def scan(ctx, elements, **overrides):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body(elements, **overrides))
    assert response.status_code == 200, response.text
    return {s["node_id"]: s for s in response.json()["results"]}


async def events_until(ctx, wanted, timeout=10):
    seen = []
    for _ in range(int(timeout / 0.4) + 2):
        body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": ctx.cursor, "wait_ms": 400})).json()
        ctx.cursor = body["cursor"]
        seen.extend(event["suggestion"] for event in body["events"] if event["type"] == "suggestion")
        if any(wanted(s) for s in seen):
            return seen
    raise AssertionError("the field was never told: " + str([(s["node_id"], s["state"]) for s in seen]))


INTERESTS = "Which area of engineering interests you most?"
AREAS = ["Select", "Web development", "Data science", "Mobile apps"]


async def test_a_menu_the_engine_could_not_answer_is_finished_by_the_two_models_and_taken_with_enter(ctx):
    gate = asyncio.Event()
    engines = Engines(laya="Data science", ollama={"reason": "r", "choice": "Data science", "facts": [1]}, gate=gate).install(ctx.app)
    first = (await scan(ctx, [select(1, INTERESTS, AREAS)]))[1]
    assert first["state"] == "GENERATING" and first["insert"] == "never" and first["generation"]["kind"] == "local"
    gate.set()
    done = await events_until(ctx, lambda s: s["state"] == "INFERRED")
    answer = done[-1]
    assert answer["insert"] == "explicit" and answer["suggestion"]["operation"] == "SELECT_OPTION"
    assert answer["suggestion"]["option"]["label"] == "Data science"          # an option the page offered, nothing else
    assert answer["flags"]["requires_review"] is True and answer["flags"]["read_in"] is True and answer["generation"] is None
    assert answer["why"]["code"] == "derived" and "no fact says it" in answer["why"]["summary"]
    assert answer["why"]["facts"] and all(set(f) == {"id", "concept", "entity", "verified"} for f in answer["why"]["facts"])
    assert [kind for kind, _ in engines.calls] == ["laya", "ollama"]
    laya = engines.calls[0][1]
    assert laya["options"] == ["Web development", "Data science", "Mobile apps"]  # the placeholder is not an option
    # the models read short statements of the profile: no ids, no name, no email, no phone
    text = json.dumps(laya["facts"]).lower()
    assert "interests" in text and "test@example" not in text and "+44" not in text
    again = (await scan(ctx, [select(1, INTERESTS, AREAS)]))[1]                      # a rescan is answered at once, from the job
    assert again["state"] == "INFERRED" and again["suggestion"]["option"]["label"] == "Data science"
    assert len(engines.calls) == 2


async def test_models_that_disagree_leave_the_field_exactly_as_the_engine_left_it(ctx):
    gate = asyncio.Event()
    Engines(laya="Web development", ollama={"reason": "r", "choice": "Data science", "facts": [1]}, gate=gate).install(ctx.app)
    assert (await scan(ctx, [select(1, INTERESTS, AREAS)]))[1]["state"] == "GENERATING"
    gate.set()
    events = await events_until(ctx, lambda s: s["state"] == "NEEDS_YOU")
    final = events[-1]
    assert final["suggestion"] is None and final["insert"] == "never" and final["needs"]["code"] == "missing_fact"
    # and it is not asked again for the same question: the failure is remembered
    again = (await scan(ctx, [select(1, INTERESTS, AREAS)]))[1]
    assert again["state"] == "NEEDS_YOU"


async def test_a_protected_question_is_never_given_to_a_model(ctx):
    engines = Engines(laya="Yes", ollama={"reason": "r", "choice": "Yes", "facts": [1]}).install(ctx.app)
    elements = radios(1, "Do you have any disability or health condition we should know about?", ["Yes", "No"], group="d") \
        + radios(5, "Will you need reasonable adjustments at interview?", ["Yes", "No"], group="a") \
        + [select(9, "Your religion or belief", ["Select", "Christian", "No religion"])]
    results = await scan(ctx, elements)
    assert all(result["state"] != "GENERATING" for result in results.values()), results
    await asyncio.sleep(0.2)
    assert engines.calls == []


async def test_nothing_is_asked_when_no_fact_touches_the_question_or_the_setting_is_off(ctx):
    engines = Engines(laya="Friend", ollama={"reason": "r", "choice": "Friend", "facts": [1]}).install(ctx.app)
    first = (await scan(ctx, [select(1, "How did you hear about this role?", ["Select", "LinkedIn", "Friend", "Other"])]))[1]
    assert first["state"] == "NEEDS_YOU" and engines.calls == []
    put_setting(ctx.app.state.db, "extension_local_assist", False)
    off = (await scan(ctx, [select(3, INTERESTS, AREAS)]))[3]
    assert off["state"] == "NEEDS_YOU" and engines.calls == []


async def test_without_the_engines_the_field_is_what_it_always_was(ctx):
    Engines(up=False).install(ctx.app)
    result = (await scan(ctx, [select(1, INTERESTS, AREAS)]))[1]
    assert result["state"] == "NEEDS_YOU" and result["needs"]["code"] == "missing_fact"
    assert ctx.app.state.extension_local.waiting() == 0


async def test_a_value_is_written_the_way_the_field_asks_and_stays_the_same_value(ctx):
    question = "Expected graduation date (month and year in words)"
    # the engine alone refuses this label; with the instruction taken out it knows the date (27 September 2027)
    before = (await scan(ctx, [el(1, "textbox", question)]))[1]
    assert before["state"] == "NEEDS_YOU"
    gate = asyncio.Event()
    engines = Engines(ollama={"value": "September 2027"}, gate=gate).install(ctx.app)
    assert (await scan(ctx, [el(3, "textbox", question)]))[3]["state"] == "GENERATING"
    gate.set()
    done = (await events_until(ctx, lambda s: s["node_id"] == 3 and s["state"] in {"VERIFIED", "INFERRED"}))[-1]
    assert done["suggestion"]["text"] == "September 2027" and done["insert"] == "tab"
    assert done["why"]["code"] in {"derived", "profile_fact"} and "local_format" in done["why"]["rules"]
    sent = [payload for kind, payload in engines.calls if kind == "ollama"][0]
    assert sent["question"] == "Expected graduation date" and sent["how_to_write"] == ["month and year in words"]
    assert "2027-09-27" in sent["value"] or "September 2027" in sent["value"]


async def test_a_rewriting_that_changes_the_value_is_discarded_and_the_field_keeps_what_it_had(ctx):
    gate = asyncio.Event()
    engines = Engines(ollama={"value": "June 2027"}, gate=gate).install(ctx.app)   # the example of the label copied instead of the date
    question = "Graduation date (e.g. June 2027)"
    assert (await scan(ctx, [el(1, "textbox", question)]))[1]["state"] == "GENERATING"
    gate.set()
    final = (await events_until(ctx, lambda s: s["state"] != "GENERATING"))[-1]
    # the field keeps what the engine said before any model was asked (its own, unrewritten answer), never the copied example
    assert final["state"] in {"VERIFIED", "INFERRED"} and final["suggestion"]["text"] == "27 September 2027"
    assert engines.calls
    refused = Engines(ollama={"value": "June 2027"}).install(ctx.app)
    summer = (await scan(ctx, [el(3, "textbox", "Expected graduation date (e.g. Summer 2027)")]))[3]
    assert summer["state"] in {"GENERATING", "NEEDS_YOU"}
    final = (await events_until(ctx, lambda s: s["node_id"] == 3 and s["state"] != "GENERATING"))[-1] if summer["state"] == "GENERATING" else summer
    assert final["state"] == "NEEDS_YOU" and final["suggestion"] is None and refused.calls


async def test_a_known_contract_needs_no_model_at_all(ctx):
    engines = Engines(ollama={"value": "x"}).install(ctx.app)
    result = (await scan(ctx, [el(1, "textbox", "Expected graduation date (DD-MM-YYYY)")]))[1]
    assert result["state"] in {"VERIFIED", "INFERRED"} and result["suggestion"]["text"] == "27-09-2027"
    assert engines.calls == []


async def test_no_model_works_on_an_answer_it_could_not_improve(ctx):
    engines = Engines(ollama={"value": "x"}).install(ctx.app)
    results = await scan(ctx, [el(1, "textbox", "First name (e.g. Maria)"), el(3, "textbox", "City", placeholder="e.g. Manchester")])
    # the bracket is an example, not a fact: the engine's answer to the plain question stands (and no model is asked)
    assert results[1]["state"] == "VERIFIED" and results[1]["suggestion"]["text"] == "Test"
    assert "instruction_split" in results[1]["why"]["rules"]
    assert results[3]["state"] == "VERIFIED"
    await asyncio.sleep(0.3)
    assert engines.calls == [] and ctx.app.state.extension_local.waiting() == 0
