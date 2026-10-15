"""Background drafting: coalescing, DOM order, caps, budget stop, cancellation of QUEUED work only, long-poll events.

The queue is tested with scripted runners (fast, exact); the end-to-end behaviour runs the real engine and answer
model layer against a mock transport. Nothing here reaches the Internet or a real model."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import (ScriptedModel, bridge, configure_text_model, el, http, make_app, posting, put_setting,  # noqa: E402
                     suggest_body)
from jobagent.application_answer_llm import ApplicationAnswerLLM  # noqa: E402
from jobagent.db import uid  # noqa: E402
from jobagent.extension import generation as G  # noqa: E402
from jobagent.extension import session as S  # noqa: E402
from jobagent.extension.draft_limits import DraftLedger  # noqa: E402
from jobagent.extension.generation import GenerationQueue, Refused  # noqa: E402
from jobagent.usage import BudgetExceededError, Reservation  # noqa: E402

QUESTION = "Why do you want to work at Acme Robotics?"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def draft(answer="A validated draft.", cost=0.0, **extra):
    return {"answer": answer, "confidence_level": "GENERATED", "state": "ANSWER_VALIDATED", "cost": cost,
            "quality": {"passed": True}, "canonical_intent": "MOTIVATION", "intent": "MOTIVATION", **extra}


def failure(code, reason="failed"):
    return {"answer": None, "root_cause": code, "reason": reason, "state": "failed", "confidence_level": "UNKNOWN"}


def runner_returning(result, *, log=None, tag=None, gate=None):
    async def run(related):
        if log is not None:
            log.append((tag, len(related)))
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(0)
        return result
    return run


async def finish(queue, timeout=60):
    for _ in range(int(timeout / 0.01)):
        if queue.active_total() == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the queue did not drain")


def submit(queue, key, runner, *, session="s1", order=(0, 0), question_id=None, listener=None, field="top:1", explicit=False):
    seen = []
    job = queue.submit(session_id=session, key=key, order=order, question_id=question_id or "q-" + key, runner=runner,
                       listener_id=field, listener=listener or seen.append, explicit=explicit)
    return job, seen


# --------------------------------------------------------------------------------------------- queue behaviour
async def test_identical_questions_share_one_job_and_every_field_hears_about_it():
    queue, calls = GenerationQueue(), []
    first, heard_one = submit(queue, "k", runner_returning(draft(), log=calls, tag="a"), field="top:1")
    second, heard_two = submit(queue, "k", runner_returning(draft(), log=calls, tag="b"), field="top:9")
    other_tab, heard_three = submit(queue, "k", runner_returning(draft()), session="s2", field="top:1")
    assert first is second is other_tab
    await finish(queue)
    assert calls == [("a", 0)]  # one paid draft
    assert first.status == "DONE" and [j.status for j in heard_one][-1] == "DONE"
    assert heard_two and heard_three
    again, _ = submit(queue, "k", runner_returning(draft(), log=calls, tag="c"))
    assert again is first and calls == [("a", 0)]  # a finished draft is reused, not rewritten
    await queue.close()


async def test_a_session_drafts_one_at_a_time_in_dom_order_and_earlier_drafts_feed_later_ones():
    queue, log = GenerationQueue(), []
    gate = asyncio.Event()
    for key, order, answer in (("late", (0, 9), "Late."), ("first", (0, 2), "First."), ("middle", (0, 5), "Middle.")):
        async def run(related, key=key, answer=answer):
            log.append((key, [r["answer"] for r in related]))
            await gate.wait()
            return draft(answer)
        submit(queue, key, run, order=order, field=f"top:{key}")
    await asyncio.sleep(0.02)
    assert [k for k, _ in log] == ["late"]  # started before the others were queued: it is already running
    gate.set()
    await finish(queue)
    assert [k for k, _ in log] == ["late", "first", "middle"]
    assert log[1][1] == ["Late."] and log[2][1] == ["Late.", "First."]
    await queue.close()


async def test_queued_work_is_ordered_by_position_in_the_form():
    queue, order = GenerationQueue(), []
    gate = asyncio.Event()

    async def blocker(related):
        await gate.wait()
        return draft("Blocker.")

    submit(queue, "blocker", blocker, order=(0, 0))
    for key, position in (("c", 30), ("a", 10), ("b", 20)):
        async def run(related, key=key):
            order.append(key)
            return draft(key)
        submit(queue, key, run, order=(0, position), field=f"top:{key}")
    gate.set()
    await finish(queue)
    assert order == ["a", "b", "c"]
    await queue.close()


async def test_sessions_run_in_parallel_but_one_session_never_runs_two_drafts_at_once():
    queue = GenerationQueue()
    running = {"s1": 0, "s2": 0, "peak_total": 0, "peak_s1": 0}
    gate = asyncio.Event()

    def make(session):
        async def run(related):
            running[session] += 1
            running["peak_total"] = max(running["peak_total"], running["s1"] + running["s2"])
            running["peak_s1"] = max(running["peak_s1"], running["s1"])
            await gate.wait()
            running[session] -= 1
            return draft(session)
        return run

    for index in range(3):
        submit(queue, f"a{index}", make("s1"), session="s1", order=(0, index), field=f"top:{index}")
    submit(queue, "b0", make("s2"), session="s2")
    await asyncio.sleep(0.05)
    assert running["peak_total"] == 2 and running["peak_s1"] == 1
    gate.set()
    await finish(queue)
    await queue.close()


async def test_page_cap_applies_to_automatic_drafts_but_not_to_an_explicit_request():
    queue = GenerationQueue()
    gate = asyncio.Event()
    runner = runner_returning(draft(), gate=gate)
    for index in range(G.PAGE_CAP):
        submit(queue, f"p{index}", runner, order=(0, index), field=f"top:{index}")
    with pytest.raises(Refused) as caught:
        submit(queue, "overflow", runner, order=(0, 99))
    assert caught.value.code == "draft_limit" and "this page" in caught.value.message
    explicit, _ = submit(queue, "asked", runner, order=(0, 100), explicit=True)  # the user asked for this one
    assert explicit.explicit and queue.pending("s1") == G.PAGE_CAP + 1
    gate.set()
    await finish(queue)
    await queue.close()


async def test_the_global_cap_protects_the_answer_model_across_pages():
    queue = GenerationQueue()
    gate = asyncio.Event()
    runner = runner_returning(draft(), gate=gate)
    for page in range(3):
        for index in range(G.GLOBAL_CAP // 3):
            submit(queue, f"g{page}-{index}", runner, session=f"s{page}", order=(0, index), field=f"top:{index}")
    assert queue.active_total() == G.GLOBAL_CAP
    with pytest.raises(Refused) as caught:
        submit(queue, "one-more", runner, session="s9")
    assert caught.value.code == "draft_limit" and "busy" in caught.value.message
    with pytest.raises(Refused):
        submit(queue, "and-asked", runner, session="s9", explicit=True)  # even an explicit request waits for capacity
    gate.set()
    await finish(queue)
    await queue.close()


async def test_the_first_configuration_failure_stops_everything_and_tells_every_page_once():
    queue, told = GenerationQueue(), []
    queue.register_session("s1", lambda code, message: told.append(("s1", code)))
    queue.register_session("s2", lambda code, message: told.append(("s2", code)))
    gate = asyncio.Event()

    async def breaks(related):
        await gate.wait()
        return failure("LLM_CONFIGURATION_REQUIRED", "Select a model")

    first, events = submit(queue, "k1", breaks, session="s1", order=(0, 1))
    queued, queued_events = submit(queue, "k2", runner_returning(draft()), session="s1", order=(0, 2), field="top:2")
    elsewhere, _ = submit(queue, "k3", runner_returning(draft()), session="s2")
    await asyncio.sleep(0.02)
    gate.set()
    await finish(queue)
    assert first.status == "FAILED" and first.error["code"] == "LLM_CONFIGURATION_REQUIRED"
    assert queued.status == "FAILED" and queued.error["code"] == "LLM_CONFIGURATION_REQUIRED"  # never run, never retried
    assert queued_events[-1].status == "FAILED"
    assert sorted(told) == [("s1", "LLM_CONFIGURATION_REQUIRED"), ("s2", "LLM_CONFIGURATION_REQUIRED")]
    with pytest.raises(Refused) as refused:
        submit(queue, "k4", runner_returning(draft()), session="s2")
    assert refused.value.code == "LLM_CONFIGURATION_REQUIRED"
    explicit, _ = submit(queue, "k5", runner_returning(draft()), session="s2", explicit=True)  # the user fixed Settings
    await finish(queue)
    assert explicit.status == "DONE" and queue.stopped() is None
    assert elsewhere.status in {"DONE", "FAILED"}
    await queue.close()


async def test_a_budget_failure_stops_the_queue_with_the_budget_code():
    queue = GenerationQueue()
    job, _ = submit(queue, "k", runner_returning(failure("AI_BUDGET_EXCEEDED", "Monthly AI budget would be exceeded")))
    await finish(queue)
    assert job.error["code"] == "AI_BUDGET_EXCEEDED" and queue.stopped()["code"] == "AI_BUDGET_EXCEEDED"
    legacy = GenerationQueue()
    job, _ = submit(legacy, "k", runner_returning(failure("ValueError", "Monthly AI budget would be exceeded")))
    await finish(legacy)
    assert job.error["code"] == "AI_BUDGET_EXCEEDED"  # an older bare ValueError is understood the same way
    await queue.close()
    await legacy.close()


async def test_the_guard_can_stop_one_application_without_stopping_the_others():
    spent = {"s1": True}
    queue = GenerationQueue(guard=lambda session: ("AI_DRAFT_BUDGET", "used up", "session") if spent.get(session) else None)
    with pytest.raises(Refused) as caught:
        submit(queue, "k", runner_returning(draft()), session="s1")
    assert caught.value.code == "AI_DRAFT_BUDGET" and queue.stopped() is None
    job, _ = submit(queue, "k2", runner_returning(draft()), session="s2")
    await finish(queue)
    assert job.status == "DONE"
    global_queue = GenerationQueue(guard=lambda session: ("AI_BUDGET_EXCEEDED", "Monthly AI budget reached", "global"))
    with pytest.raises(Refused):
        submit(global_queue, "k", runner_returning(draft()))
    assert global_queue.stopped()["code"] == "AI_BUDGET_EXCEEDED"
    await queue.close()


async def test_closing_a_page_cancels_only_queued_drafts_never_the_one_in_flight():
    queue, log = GenerationQueue(), []
    gate = asyncio.Event()
    running, _ = submit(queue, "running", runner_returning(draft("Done anyway."), log=log, tag="running", gate=gate),
                        order=(0, 1), field="top:1")
    queued, queued_events = submit(queue, "queued", runner_returning(draft(), log=log, tag="queued"), order=(0, 2),
                                   field="top:2")
    await asyncio.sleep(0.02)
    assert running.status == "GENERATING" and queued.status == "QUEUED"
    assert queue.forget_session("s1") == 1
    assert queued.status == "CANCELLED" and queue.get("queued") is None
    gate.set()
    await finish(queue)
    assert running.status == "DONE" and running.result["answer"] == "Done anyway."  # the paid draft still lands
    assert log == [("running", 0)] and not queued_events
    await queue.close()


async def test_a_draft_another_page_still_waits_for_survives_its_first_pages_closing():
    queue = GenerationQueue()
    gate = asyncio.Event()
    submit(queue, "blocker", runner_returning(draft(), gate=gate), order=(0, 0), field="top:0")
    job, _ = submit(queue, "shared", runner_returning(draft()), order=(0, 1), field="top:1")
    submit(queue, "shared", runner_returning(draft()), session="s2", field="top:7")
    assert queue.forget_session("s1") == 0 and job.status == "QUEUED"  # a second page still wants it
    gate.set()
    await finish(queue)
    assert job.status == "DONE"
    await queue.close()


async def test_progress_updates_reach_the_right_draft_and_the_hook_is_chained_and_restored():
    forwarded = []
    llm = SimpleNamespace(on_progress=lambda update: forwarded.append(update["status"]))
    queue = GenerationQueue(llm_getter=lambda: llm)
    statuses, gate = [], asyncio.Event()

    async def run(related):
        await llm.on_progress({"question_id": "q-key", "intent": "MOTIVATION", "status": "GENERATING", "attempt": 1})
        await llm.on_progress({"question_id": "q-key", "intent": "MOTIVATION", "status": "REGENERATING", "attempt": 2})
        await llm.on_progress({"question_id": "q-key", "intent": "MOTIVATION", "status": "VALIDATING", "attempt": 2})
        await llm.on_progress({"question_id": "someone-else", "intent": "MOTIVATION", "status": "GENERATING", "attempt": 1})
        await gate.wait()
        return draft()

    job, _ = submit(queue, "key", run, listener=lambda j: statuses.append((j.status, j.attempt)))
    await asyncio.sleep(0.02)
    assert statuses == [("GENERATING", 1), ("REGENERATING", 2), ("VALIDATING", 2)]
    gate.set()
    await finish(queue)
    assert forwarded == ["GENERATING", "REGENERATING", "VALIDATING", "GENERATING"] and statuses[-1] == ("DONE", 2)
    await queue.close()
    assert not hasattr(llm.on_progress, "__self__") or llm.on_progress.__self__ is not queue  # restored


async def test_a_crashing_runner_fails_one_draft_calmly_and_the_next_one_runs():
    queue = GenerationQueue()

    async def crash(related):
        raise RuntimeError("secret detail that must not be shown")

    broken, _ = submit(queue, "bad", crash, order=(0, 1), field="top:1")
    fine, _ = submit(queue, "good", runner_returning(draft()), order=(0, 2), field="top:2")
    await finish(queue)
    assert broken.status == "FAILED" and "secret" not in broken.error["message"] and fine.status == "DONE"
    retry, _ = submit(queue, "bad", runner_returning(draft()), explicit=True)
    assert retry is not broken  # only an explicit request tries a failed draft again
    await finish(queue)
    await queue.close()


async def test_closing_the_queue_cancels_what_waits_and_restores_the_model_hook():
    llm = SimpleNamespace(on_progress=None)
    queue = GenerationQueue(llm_getter=lambda: llm)
    gate = asyncio.Event()
    submit(queue, "a", runner_returning(draft(), gate=gate), order=(0, 1))
    queued, _ = submit(queue, "b", runner_returning(draft()), order=(0, 2), field="top:2")
    assert llm.on_progress is not None
    await queue.close()
    assert queued.status == "CANCELLED" and llm.on_progress is None
    with pytest.raises(Refused):
        submit(queue, "c", runner_returning(draft()))


# --------------------------------------------------------------------------------------------- engine / usage patches
def test_the_budget_error_is_typed_but_still_a_value_error(tmp_path):
    from jobagent.db import Database
    db = Database(tmp_path / "usage.sqlite3")
    with pytest.raises(BudgetExceededError, match="budget would be exceeded") as caught:
        Reservation(db, 5.0, 1.0, "openai")
    assert isinstance(caught.value, ValueError) and caught.value.code == "AI_BUDGET_EXCEEDED"


async def test_engine_reports_the_budget_code_and_defers_drafts_without_a_model(tmp_path):
    app = make_app(tmp_path, text_model=False)
    db = app.state.db
    put_setting(db, "text_provider", "openai-compatible")
    put_setting(db, "text_base_url", "https://compatible.example/v1")
    put_setting(db, "text_model", "test-model")
    put_setting(db, "monthly_budget", 0.000001)
    db.execute("INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)", (uid(), "openai-compatible", json.dumps(
        {"answer_model_prices": {"test-model": {"input_cost_per_million": 1, "output_cost_per_million": 2}}}), "configured"))
    app.state.secrets.set("openai-compatible:secret", "k")
    from jobagent.core import get_settings
    from jobagent.extension.resolver import load_environment
    env = load_environment(db, get_settings(db), job={"country": "United Kingdom"}, application={"id": "", "company": "", "location": ""})
    model = ScriptedModel()
    llm = ApplicationAnswerLLM(db, app.state.secrets, transport=model.transport)
    engine = env.engine_for(llm, get_settings(db))
    deferred = await engine.answer(QUESTION, {"type": "textarea"}, {}, audit=False, generate=False)
    assert deferred["root_cause"] == "ASYNC_GENERATION_REQUIRED" and model.calls == []
    result = await engine.answer(QUESTION, {"type": "textarea"}, {}, audit=False)
    assert result["root_cause"] == "AI_BUDGET_EXCEEDED" and result["state"] == "failed" and model.calls == []


# --------------------------------------------------------------------------------------------- end to end
@pytest.fixture
async def ctx(tmp_path):
    app = make_app(tmp_path)
    model = ScriptedModel().install(app)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        yield SimpleNamespace(app=app, client=client, model=model, sid=created["session_id"], cursor=created["cursor"],
                              created=created)
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


def narrative(node_id, question=QUESTION, **kwargs):
    return el(node_id, "textbox", question, tag="textarea", type="textarea", max_length=800, **kwargs)


async def scan(ctx, elements, **kwargs):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body(elements, **kwargs))
    assert response.status_code == 200, response.text
    return {s["node_id"]: s for s in response.json()["results"]}


async def drained(ctx, timeout=10):
    await finish(ctx.app.state.extension_generation, timeout)


async def collect(ctx, until, timeout=10):
    """Long-poll until ``until(events)`` (events seen so far); returns every event."""
    seen = []
    for _ in range(int(timeout / 0.5) + 2):
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": ctx.cursor, "wait_ms": 500})
        body = response.json()
        ctx.cursor = body["cursor"]
        seen.extend(body["events"])
        if until(seen):
            return seen
    raise AssertionError(f"expected event never arrived: {[e['type'] for e in seen]}")


async def test_duplicate_questions_on_one_page_cost_one_draft(ctx):
    results = await scan(ctx, [narrative(1), narrative(3)])
    assert results[1]["generation"]["id"] == results[3]["generation"]["id"]
    await drained(ctx)
    assert len(ctx.model.drafts) == 1 and len(ctx.model.calls) == 2  # one draft + one independent review
    again = await scan(ctx, [narrative(1), narrative(3)])
    assert again[1]["state"] == again[3]["state"] == "GENERATED_DRAFT"
    assert again[1]["suggestion"]["text"] == again[3]["suggestion"]["text"] and len(ctx.model.calls) == 2


async def test_a_second_tab_on_the_same_job_reuses_the_validated_draft(ctx):
    await scan(ctx, [narrative(1)])
    await drained(ctx)
    other = (await ctx.client.post("/v1/sessions", json=posting(tab_key="x") | {"tab_key": "tab-2"})).json()
    assert other["session_id"] == ctx.sid  # the same page of the same browser is the same session
    assert len(ctx.model.calls) == 2


async def test_drafts_arrive_as_events_with_progress_and_a_quiet_final_state(ctx):
    await scan(ctx, [narrative(1)])
    events = await collect(ctx, lambda seen: any(e["type"] == "suggestion" and e["suggestion"]["state"] == "GENERATED_DRAFT"
                                                 for e in seen))
    suggestions = [e["suggestion"] for e in events if e["type"] == "suggestion"]
    assert [s["state"] for s in suggestions][-1] == "GENERATED_DRAFT"
    assert {s["generation"]["status"] for s in suggestions} >= {"GENERATING", "DONE"}
    final = suggestions[-1]
    assert final["generation"]["quality_passed"] is True and final["generation"]["cost"] == 0.0
    assert final["based_on"]["job_description"] is True and final["flags"]["requires_review"] is True
    assert final["insert"] == "tab" and final["suggestion"]["operation"] == "TYPE_TEXT"
    kinds = [r["kind"] for r in ctx.app.state.db.query("SELECT kind FROM extension_events")]
    assert "draft_ready" in kinds


async def test_two_questions_are_drafted_in_order_and_the_second_sees_the_first(ctx):
    results = await scan(ctx, [narrative(1, "Why do you want to work at Acme Robotics?"),
                               narrative(3, "Describe a project you are proud of and what you learned.")])
    assert results[1]["state"] == results[3]["state"] == "GENERATING"
    await drained(ctx)
    assert ctx.model.peak == 1  # sequential inside a page
    first, second = ctx.model.drafts
    assert first["exact_question"].startswith("Why do you want") and not first["previous_answers"]
    assert [p["exact_question"] for p in second["previous_answers"]] == [first["exact_question"]]


async def test_a_draft_survives_a_late_research_update_and_nothing_is_regenerated_silently(ctx):
    await scan(ctx, [narrative(1)])
    await drained(ctx)
    drafts = len(ctx.model.drafts)
    refreshed = await ctx.client.post(f"/v1/sessions/{ctx.sid}/research/refresh")
    assert refreshed.status_code == 200
    await asyncio.sleep(0.2)
    assert len(ctx.model.drafts) == drafts  # research alone never spends money
    again = await scan(ctx, [narrative(1)])
    assert again[1]["state"] in {"GENERATING", "GENERATED_DRAFT"}  # the page asked for fresh drafts: explicit refresh


async def test_the_draft_waits_for_research_but_never_longer_than_the_limit(tmp_path, monkeypatch):
    from jobagent.extension import suggest as SG
    monkeypatch.setattr(SG, "DRAFT_WAIT", 0.3)
    app = make_app(tmp_path)
    model = ScriptedModel().install(app)

    async def never(url, **kwargs):
        await asyncio.sleep(30)

    app.state.extension_research._fetcher = never
    page = posting()
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json=page)).json()["session_id"]
        started = asyncio.get_running_loop().time()
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([narrative(1)]))
        await finish(app.state.extension_generation)
        assert asyncio.get_running_loop().time() - started < 25  # the page never answers: waiting for it would take 30 s
        item = (await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([narrative(1)]))).json()["results"][0]
        assert item["state"] == "GENERATED_DRAFT" and len(model.drafts) == 1
        assert item["based_on"]["research_state"] in {"running", "queued", "partial", "done"}
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_closing_the_page_cancels_queued_drafts_but_lets_the_running_one_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "RELEASE_GRACE", 0.05)
    app = make_app(tmp_path)
    model = ScriptedModel(delay=0.4).install(app)
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json=posting())).json()["session_id"]
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            narrative(1, "Why do you want to work at Acme Robotics?"),
            narrative(3, "Describe a project you are proud of and what you learned.")]))
        generation = app.state.extension_generation
        for _ in range(6000):  # the first draft is really running
            if generation.active_total() and model.calls:
                break
            await asyncio.sleep(0.01)
        assert (await client.delete(f"/v1/sessions/{sid}")).json() == {"released": True}
        for _ in range(6000):  # the page is dropped after its grace; the running draft is allowed to finish
            if sid not in app.state.extension_sessions.sessions and generation.active_total() == 0:
                break
            await asyncio.sleep(0.01)
        assert len(model.drafts) == 1  # the queued second draft never started
        cached = app.state.db.query("SELECT key FROM settings WHERE key LIKE 'validated_answer_cache:%'")
        assert len(cached) == 1  # the in-flight draft was allowed to finish: paid for once, free next time
        assert sid not in app.state.extension_sessions.sessions
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_a_reload_inside_the_grace_period_keeps_the_queued_work(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "RELEASE_GRACE", 30)  # however slow the machine, the reload is inside it
    app = make_app(tmp_path)
    model = ScriptedModel(delay=0.2).install(app)
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json=posting())).json()["session_id"]
        questions = [narrative(1, "Why do you want to work at Acme Robotics?"),
                     narrative(3, "Describe a project you are proud of and what you learned.")]
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body(questions))
        await client.delete(f"/v1/sessions/{sid}")
        again = (await client.post("/v1/sessions", json=posting())).json()
        assert again["session_id"] == sid
        await finish(app.state.extension_generation)
        session = app.state.extension_sessions.sessions[sid]
        assert len(model.drafts) == 2 and session.release_handle is None  # the reload cancelled the pending release
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_a_spent_monthly_budget_stops_drafting_and_says_so(tmp_path):
    app = make_app(tmp_path, text_model=False)
    db = app.state.db
    put_setting(db, "text_provider", "openai-compatible")
    put_setting(db, "text_base_url", "https://compatible.example/v1")
    put_setting(db, "text_model", "test-model")
    put_setting(db, "text_retry_attempts", 0)
    put_setting(db, "monthly_budget", 0.000001)
    db.execute("INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)", (uid(), "openai-compatible", json.dumps(
        {"answer_model_prices": {"test-model": {"input_cost_per_million": 1, "output_cost_per_million": 2}}}), "configured"))
    app.state.secrets.set("openai-compatible:secret", "k")
    from jobagent.onboarding import TEXT_AI, fingerprint, record
    from jobagent.db import now
    record(db, "ai_text", {"success": True, "checked_at": now(), "fingerprint": fingerprint(db, TEXT_AI), "provider": "x"})
    model = ScriptedModel().install(app)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        sid, cursor = created["session_id"], created["cursor"]
        assert created["generation"]["available"] is True and created["generation"]["local"] is False
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([narrative(1)]))
        seen = []
        for _ in range(20):
            body = (await client.post(f"/v1/sessions/{sid}/poll", json={"cursor": cursor, "wait_ms": 500})).json()
            cursor = body["cursor"]
            seen.extend(body["events"])
            if any(e["type"] == "generation_budget" for e in seen):
                break
        budget = next(e for e in seen if e["type"] == "generation_budget")
        assert budget["code"] == "AI_BUDGET_EXCEEDED" and "budget" in budget["message"].lower()
        failed = [e["suggestion"] for e in seen if e["type"] == "suggestion" and e["suggestion"]["state"] == "NEEDS_YOU"]
        assert failed and failed[-1]["needs"]["code"] == "budget" and [a["type"] for a in failed[-1]["needs"]["actions"]] == ["open_meridian"]
        later = (await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            narrative(5, "Describe a project you are proud of and what you learned.")]))).json()["results"][0]
        assert later["state"] == "NEEDS_YOU" and later["needs"]["code"] == "budget"
        assert model.calls == []  # not one provider request was ever sent
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_regenerate_instructions_change_the_request(ctx):
    first = (await scan(ctx, [narrative(1)]))[1]
    await drained(ctx)
    current = (await scan(ctx, [narrative(1)]))[1]["suggestion"]["text"]
    assert current == "I built a vision pipeline using Python."
    words = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen " \
            "eighteen nineteen twenty"

    async def regenerate(instruction):
        before = len(ctx.model.drafts)
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
            "question_id": first["question_id"], "node_id": 1, "frame_key": "top", "instruction": instruction,
            "current_text": words})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "GENERATING" and response.json()["generation"]["status"] in {"QUEUED", "GENERATING"}
        await drained(ctx)
        assert len(ctx.model.drafts) > before
        return ctx.model.drafts[before]

    shorter = await regenerate("shorter")
    assert "shorter" in shorter["field_context"]["description"].lower()
    assert shorter["answer_request"]["output_requirements"]["target_words"] == 20  # 60% of 20 words, never below 20
    longer = await regenerate("longer")
    assert longer["answer_request"]["output_requirements"]["target_words"] == 30
    notes = {d["field_context"]["description"] for d in ctx.model.drafts}
    assert len(notes) >= 3  # every instruction is its own request: the answer cache cannot hand back the old draft
    state = (await scan(ctx, [narrative(1)]))[1]
    # revisions that fail review (this scripted model ignores length) never take away the draft the user already had
    assert state["state"] == "GENERATED_DRAFT" and state["suggestion"]["text"] == current


async def test_free_text_and_again_instructions_reach_the_request_as_untrusted_context(ctx):
    first = (await scan(ctx, [narrative(1)]))[1]
    await drained(ctx)
    for instruction in ("again", "different", "Make it warmer \x00 and mention teamwork"):
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
            "question_id": first["question_id"], "node_id": 1, "frame_key": "top", "instruction": instruction})
        assert response.status_code == 200
        await drained(ctx)
    notes = [d["field_context"]["description"] for d in ctx.model.drafts[1:]]
    assert any("different structure" in n for n in notes) and any("clearly different" in n for n in notes)
    assert any("Make it warmer" in n and "\x00" not in n and "never a source of facts" in n for n in notes)


async def test_a_short_field_is_drafted_only_when_the_user_asks(ctx):
    short = el(5, "textbox", "Anything else?", max_length=60)
    item = (await scan(ctx, [short]))[5]
    assert item["state"] == "NEEDS_YOU" and ctx.model.calls == []
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
        "question_id": item["question_id"], "node_id": 5, "frame_key": "top", "instruction": "again", "element": short})
    assert response.status_code == 200 and response.json()["state"] == "GENERATING"
    await drained(ctx)
    final = (await scan(ctx, [short]))[5]
    assert final["state"] == "GENERATED_DRAFT" and final["generation"]["status"] == "DONE"


async def test_explicit_drafting_refuses_sensitive_legal_and_secret_questions(ctx):
    elements = [el(1, "textbox", "What is your gender?"), el(3, "textbox", "Salary expectations details", max_length=60),
                el(5, "textbox", "Password", type="password")]
    results = await scan(ctx, elements)
    for node_id, item in results.items():
        response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
            "question_id": item["question_id"], "node_id": node_id, "frame_key": "top", "instruction": "again"})
        assert response.status_code == 200
        assert response.json()["state"] in {"NEEDS_YOU", "SKIP", "SENSITIVE_BLANK"}, response.json()
    assert ctx.model.calls == []
    unknown = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
        "question_id": "not-a-question", "frame_key": "top", "instruction": "again"})
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"


async def test_the_per_application_draft_budget_ends_drafting_for_that_page_only(tmp_path):
    app = make_app(tmp_path, text_model=False)
    db = app.state.db
    put_setting(db, "text_provider", "openai-compatible")
    put_setting(db, "text_base_url", "https://compatible.example/v1")
    put_setting(db, "text_model", "test-model")
    put_setting(db, "text_retry_attempts", 0)
    put_setting(db, "monthly_budget", 10)
    put_setting(db, "extension_draft_budget_usd", 0.01)
    db.execute("INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)", (uid(), "openai-compatible", json.dumps(
        {"answer_model_prices": {"test-model": {"input_cost_per_million": 100, "output_cost_per_million": 200}}}), "configured"))
    app.state.secrets.set("openai-compatible:secret", "k")
    from jobagent.db import now
    from jobagent.onboarding import TEXT_AI, fingerprint, record
    record(db, "ai_text", {"success": True, "checked_at": now(), "fingerprint": fingerprint(db, TEXT_AI), "provider": "x"})
    model = ScriptedModel().install(app)
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json=posting())).json()["session_id"]
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([narrative(1)]))
        await finish(app.state.extension_generation)
        assert len(model.drafts) == 1
        session = app.state.extension_sessions.sessions[sid]
        assert session.draft_spend > 0.01  # two provider calls at these prices exceed the application's cap
        second = (await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            narrative(5, "Describe a project you are proud of and what you learned.")]))).json()["results"][0]
        assert second["state"] == "NEEDS_YOU" and second["needs"]["code"] == "budget"
        assert len(model.drafts) == 1 and app.state.extension_generation.stopped() is None
        row = db.one("SELECT metadata FROM extension_sessions WHERE id=?", (sid,))
        assert json.loads(row["metadata"])["draft_spend_usd"] > 0.01
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_text_concurrency_is_shared_with_the_rest_of_meridian(tmp_path):
    app = make_app(tmp_path)
    configure_text_model(app.state.db)
    put_setting(app.state.db, "text_concurrency", 1)
    model = ScriptedModel(delay=0.05).install(app)
    async with http(bridge(app)) as client:
        first = (await client.post("/v1/sessions", json=posting())).json()["session_id"]
        second = (await client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/4002",
                                                                 title="Data Engineer - Acme Robotics",
                                                                 h1="Data Engineer"))).json()["session_id"]
        for sid in (first, second):
            await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([narrative(1)]))
        await finish(app.state.extension_generation, 20)
        assert model.peak == 1  # two pages, but the shared answer model runs one request at a time
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def test_a_company_or_title_that_firms_up_never_triggers_a_second_paid_draft(ctx):
    await scan(ctx, [narrative(1)])
    await drained(ctx)
    drafts = len(ctx.model.drafts)
    session = ctx.app.state.extension_sessions.sessions[ctx.sid]
    session.title, session.company = "Senior Software Engineer (London)", "Acme Robotics Ltd"
    again = await scan(ctx, [narrative(1)])
    assert again[1]["state"] == "GENERATED_DRAFT" and len(ctx.model.drafts) == drafts


async def test_an_answer_found_without_a_model_finishes_the_job_without_pretending_to_be_a_draft():
    queue = GenerationQueue()
    found = {"answer": "Green", "confidence_level": "EXACT", "verified": True, "state": None, "cost": 0.0}
    job, heard = submit(queue, "k", runner_returning(found))
    await finish(queue)
    assert job.status == "DONE" and job.result["answer"] == "Green" and job not in [h for h in heard if h.status == "FAILED"]
    unreviewed = {"answer": "Draft", "confidence_level": "GENERATED", "quality": {"passed": False}, "cost": 0.0}
    rejected, _ = submit(queue, "k2", runner_returning(unreviewed), order=(0, 1), field="top:2")
    await finish(queue)
    assert rejected.status == "FAILED"  # a draft that failed review is never offered
    await queue.close()


async def test_finished_drafts_are_not_kept_for_ever():
    # This test is about pruning 480 finished drafts, not about the hourly limit of drafts started on their own (SEC-06).
    queue = GenerationQueue(ledger=DraftLedger(per_session=10**6, per_client=10**6, total=10**6))
    for number in range(G.GLOBAL_CAP * 40):
        submit(queue, f"k{number}", runner_returning(draft(f"Draft {number}.")), session=f"s{number % 5}", order=(0, number))
        await finish(queue)
    assert len(queue._jobs) <= 301 and queue.active_total() == 0
    assert len(queue._lanes) <= 5 and all(len(lane.done) <= G.KEEP_DONE for lane in queue._lanes.values())
    assert queue._by_qid == {}
    await queue.close()
