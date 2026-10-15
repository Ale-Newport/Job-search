"""The event stream of a page (docs/EXTENSION_DESIGN.md 5.2) under concurrency: nothing that happens while a suggest request
is running may be lost between its response and the next poll, a response is never staler than an event that was already
emitted when it was sent, and events of a draft whose question has changed never repaint the field.

The draft model is scripted and GATED (drafting calls wait until the test opens the gate), so a draft can be made to finish
at an exact point INSIDE a running ``suggest`` request: while another field of the same scan is being resolved, while the
scan's audit lines are written (a thread), or after the response was built but before the poll. An oracle sits on
``Session.emit`` and records every event with its number; the tests compare what the poll delivers with what was emitted.

The "client" here is the one of the design: it applies a suggest response, then the events of ``poll(cursor)``, newest
last (and, in the reordered variant, the poll's events first and the response after: two HTTP requests may overtake each
other on their way back)."""
import asyncio
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, bridge, el, http, make_app, posting, request_payload, suggest_body  # noqa: E402
from jobagent.extension import resolver as R  # noqa: E402
from jobagent.extension import session as S  # noqa: E402

QUESTION = "Why do you want to work at Acme Robotics?"
OTHER_QUESTION = "Describe a project you are proud of and what you learned."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


class GatedModel(ScriptedModel):
    """Drafting calls wait for ``gate``; the independent review of a finished draft goes straight through."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gate = asyncio.Event()

    async def _handle(self, request):
        if "generated_answer" not in request_payload(request):
            await self.gate.wait()
        return await super()._handle(request)


class RandomModel(GatedModel):
    """Drafting calls take a random time (seeded): drafts finish at every possible moment of the page's requests."""

    def __init__(self, seed):
        super().__init__()
        self.rng = random.Random(seed)
        self.gate.set()

    async def _handle(self, request):
        if "generated_answer" not in request_payload(request):
            await asyncio.sleep(self.rng.uniform(0, 0.12))
        return await super()._handle(request)


async def open_page(tmp_path, monkeypatch, model):
    app = make_app(tmp_path)
    model.install(app)
    emitted = []
    original = S.Session.emit

    def recording(self, event):
        original(self, event)
        emitted.append((self.id, self.cursor, event))  # the number the event got (the cursor after it was appended)

    monkeypatch.setattr(S.Session, "emit", recording)
    return app, emitted


@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    model = GatedModel()
    app, emitted = await open_page(tmp_path, monkeypatch, model)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        sid = created["session_id"]
        await app.state.extension_research.wait(created["research"]["run_id"], 10)
        await asyncio.sleep(0.2)  # research reported its last state: from here on only the test makes events
        yield SimpleNamespace(app=app, client=client, model=model, sid=sid, emitted=emitted,
                              registry=app.state.extension_sessions)
    model.gate.set()
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


@pytest.fixture
async def random_ctx(request, tmp_path, monkeypatch):
    model = RandomModel(request.param)
    app, emitted = await open_page(tmp_path, monkeypatch, model)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        sid = created["session_id"]
        await app.state.extension_research.wait(created["research"]["run_id"], 10)
        await asyncio.sleep(0.2)
        yield SimpleNamespace(app=app, client=client, model=model, sid=sid, emitted=emitted,
                              registry=app.state.extension_sessions)
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


def narrative(node_id, question=QUESTION, **kwargs):
    kwargs.setdefault("max_length", 800)
    return el(node_id, "textbox", question, tag="textarea", type="textarea", **kwargs)


def session_of(ctx):
    return ctx.registry.sessions[ctx.sid]


async def scan(ctx, elements, **kwargs):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body(elements, **kwargs))
    assert response.status_code == 200, response.text
    return response.json()


async def poll(ctx, cursor, wait=0):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": cursor, "wait_ms": wait})
    assert response.status_code == 200, response.text
    return response.json()


async def drained(ctx, timeout=30):
    queue = ctx.app.state.extension_generation
    for _ in range(int(timeout / 0.01)):
        if queue.active_total() == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the drafts did not finish")


async def finish_drafts(ctx):
    """Open the gate and wait until every draft is written: events are emitted right here, on the event loop."""
    ctx.model.gate.set()
    await drained(ctx)


async def started(ctx, timeout=10):
    """Wait until the draft model has been asked (the job is running and blocked on the gate)."""
    for _ in range(int(timeout / 0.01)):
        if ctx.app.state.extension_generation.active_total() and any(
                j.status == "GENERATING" for j in ctx.app.state.extension_generation._jobs.values()):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no draft started")


def mine(ctx, after=0):
    """Every event this page emitted with a number above ``after``, in order: (number, event)."""
    return [(n, e) for sid, n, e in ctx.emitted if sid == ctx.sid and n > after]


def suggestions_of(events, question_id=None):
    return [e["suggestion"] for e in events if e["type"] == "suggestion"
            and (question_id is None or e["suggestion"]["question_id"] == question_id)]


def client_view(*batches):
    """What a content script ends up holding per question after applying these suggestions in this order."""
    view = {}
    for batch in batches:
        for suggestion in batch:
            view[suggestion["question_id"]] = suggestion
    return view


def truth(ctx):
    """What the server holds right now, per question: the newest suggestion of every stored field."""
    return {f.question_id: f.suggestion for f in session_of(ctx).fields.values() if f.suggestion}


def key_facts(suggestion):
    generation = suggestion.get("generation") or {}
    return suggestion["state"], generation.get("id"), generation.get("status"), (suggestion.get("suggestion") or {}).get("text")


def hook_at(monkeypatch, where, action):
    """Run ``action`` (an async function) at a point INSIDE a running suggest request."""
    if where == "other_field":
        original = R.FieldResolver.resolve

        async def resolving(self, element, **kwargs):
            if element.get("label") == "First name":
                await action()
            return await original(self, element, **kwargs)

        monkeypatch.setattr(R.FieldResolver, "resolve", resolving)
    elif where == "audit":
        original_audit = S.Session.audit_suggestions

        async def auditing(self, results, elements, frame_key):
            await action()
            await original_audit(self, results, elements, frame_key)

        monkeypatch.setattr(S.Session, "audit_suggestions", auditing)
    else:
        raise AssertionError(where)


# --------------------------------------------------------------------------------------------- nothing is lost
POINTS = ["other_field", "audit", "after_response"]


async def run_race(ctx, monkeypatch, where):
    """A draft is running (blocked); a second scan of the page is answered while it finishes at ``where``.
    Returns (cursor before the request, the response)."""
    await scan(ctx, [narrative(1)])
    await started(ctx)
    before = session_of(ctx).cursor
    if where != "after_response":
        hook_at(monkeypatch, where, lambda: finish_drafts(ctx))
    response = await scan(ctx, [narrative(1), el(3, "textbox", "First name")])
    if where == "after_response":
        await finish_drafts(ctx)
    return before, response


@pytest.mark.parametrize("where", POINTS)
async def test_every_event_emitted_while_a_suggest_request_runs_is_delivered_by_a_poll_from_its_cursor(ctx, monkeypatch, where):
    before, response = await run_race(ctx, monkeypatch, where)
    cursor = response["cursor"]
    assert before <= cursor <= session_of(ctx).cursor  # read when the request STARTED, never later than it ended
    after = mine(ctx, cursor)
    assert after, "the draft must have produced events after the request began (the race this test is about)"
    polled = await poll(ctx, cursor)
    assert polled["events"] == [event for _, event in after]  # exactly what was emitted, nothing lost, in order
    assert polled["cursor"] == session_of(ctx).cursor
    numbers = [n for sid, n, _event in ctx.emitted if sid == ctx.sid]
    assert numbers == sorted(numbers) and len(numbers) == len(set(numbers))  # strictly increasing, no duplicates
    assert (await poll(ctx, polled["cursor"]))["events"] == []  # and then it is quiet: nothing is delivered twice


@pytest.mark.parametrize("where", POINTS)
async def test_a_client_applying_the_response_and_then_the_events_ends_with_what_the_server_holds(ctx, monkeypatch, where):
    _, response = await run_race(ctx, monkeypatch, where)
    polled = await poll(ctx, response["cursor"])
    view = client_view(response["results"], suggestions_of(polled["events"]))
    expected = truth(ctx)
    assert {q: key_facts(s) for q, s in view.items()} == {q: key_facts(s) for q, s in expected.items()}
    draft = next(s for s in view.values() if s["question"] == QUESTION)
    assert draft["state"] == "GENERATED_DRAFT" and draft["suggestion"]["text"]  # the draft is not lost on the way


@pytest.mark.parametrize("where", ["other_field", "audit"])
async def test_a_response_is_never_older_than_an_event_that_was_already_emitted_when_it_was_sent(ctx, monkeypatch, where):
    """Two HTTP requests overtake each other: the poll's events may reach the page BEFORE the suggest response, which then
    overwrites them. The response must therefore already show what the events said (the draft was finished before the
    response was sent), so that applying events first and the response after still ends with the draft."""
    before, response = await run_race(ctx, monkeypatch, where)
    polled = await poll(ctx, before)  # a page whose poll cursor was behind the request: it gets the events first
    reordered = client_view(suggestions_of(polled["events"]), response["results"])
    expected = truth(ctx)
    assert {q: key_facts(s) for q, s in reordered.items()} == {q: key_facts(s) for q, s in expected.items()}
    draft = next(s for s in reordered.values() if s["question"] == QUESTION)
    assert draft["state"] == "GENERATED_DRAFT" and draft["generation"]["status"] == "DONE"
    # the response itself carries the finished draft (what the server holds when it answers), the cursor stays early
    in_response = next(s for s in response["results"] if s["question"] == QUESTION)
    assert in_response["state"] == "GENERATED_DRAFT"


async def test_a_draft_that_finishes_after_the_response_is_left_to_the_poll(ctx, monkeypatch):
    before, response = await run_race(ctx, monkeypatch, "after_response")
    in_response = next(s for s in response["results"] if s["question"] == QUESTION)
    assert in_response["state"] == "GENERATING"  # it was still being written when the response was sent
    polled = await poll(ctx, response["cursor"])
    final = suggestions_of(polled["events"], in_response["question_id"])[-1]
    assert final["state"] == "GENERATED_DRAFT" and final["generation"]["id"] == in_response["generation"]["id"]


# --------------------------------------------------------------------------------------------- a page that polls while it scans
RANK = {"QUEUED": 0, "GENERATING": 1, "REGENERATING": 2, "VALIDATING": 3, "DONE": 4, "FAILED": 4}
QUESTIONS = ["Why do you want to work at Acme Robotics?", OTHER_QUESTION, "What is your greatest strength?",
             "Where do you see yourself in five years?"]


def progress(suggestion):
    generation = suggestion.get("generation")
    return (generation["attempt"], RANK[generation["status"]]) if generation else None


def apply_guarded(view, suggestion):
    """The rule a page applies to whatever arrives, in whatever order: a suggestion of the draft it already holds, but at an
    earlier point of that draft's progress, is old news (the events and a response are separate requests)."""
    current = view.get(suggestion["question_id"])
    if current and current.get("generation") and suggestion.get("generation") \
            and current["generation"]["id"] == suggestion["generation"]["id"] and progress(suggestion) < progress(current):
        return
    view[suggestion["question_id"]] = suggestion


@pytest.mark.parametrize("random_ctx", [11, 12, 13], indirect=True)
async def test_a_page_that_keeps_polling_while_it_scans_ends_with_every_draft_whatever_the_interleaving(random_ctx):
    """The real page: one poll loop running all the time and several requests (scan, focus, refresh, another scan) in
    flight at once, each applied when it comes back, after a random delay (responses overtaking events and the other way
    round), while drafts finish at random moments. Every draft ends up on the page, exactly as the server holds it."""
    ctx = random_ctx
    fields = [narrative(1 + 2 * n, question) for n, question in enumerate(QUESTIONS)] + [el(11, "textbox", "First name")]
    rng = random.Random(7)
    view, state = {}, {"cursor": session_of(ctx).cursor, "stop": False}

    async def poller():
        while not state["stop"]:
            body = await poll(ctx, state["cursor"], wait=250)
            state["cursor"] = body["cursor"]
            for event in body["events"]:
                if event["type"] == "suggestion":
                    apply_guarded(view, event["suggestion"])

    async def request(delay, elements, **kwargs):
        await asyncio.sleep(delay)
        response = await scan(ctx, elements, **kwargs)
        await asyncio.sleep(rng.uniform(0, 0.05))  # the answer takes its time to reach the page
        for suggestion in response["results"]:
            apply_guarded(view, suggestion)

    watching = asyncio.create_task(poller())
    await asyncio.gather(
        request(0.0, fields),
        request(rng.uniform(0, 0.1), fields, mode="focus", focus_node_id=3),
        request(rng.uniform(0, 0.1), fields[2:4], mode="refresh"),
        request(rng.uniform(0, 0.2), fields, mode="focus", focus_node_id=7),
        request(rng.uniform(0, 0.2), fields),
        request(rng.uniform(0, 0.3), fields[:2], mode="refresh"),
    )
    await drained(ctx)
    for _ in range(100):  # the poll loop catches up with the last event
        if state["cursor"] == session_of(ctx).cursor:
            break
        await asyncio.sleep(0.05)
    state["stop"] = True
    await watching
    expected = truth(ctx)
    assert set(view) == set(expected) and len(expected) == len(fields)
    assert {q: key_facts(s) for q, s in view.items()} == {q: key_facts(s) for q, s in expected.items()}
    drafts = [s for s in view.values() if s["question"] in QUESTIONS]
    assert len(drafts) == len(QUESTIONS) and all(s["state"] == "GENERATED_DRAFT" for s in drafts)
    assert len(ctx.model.drafts) == len(QUESTIONS)  # every question paid for exactly once, however often it was asked
    numbers = [n for sid, n, _event in ctx.emitted if sid == ctx.sid]
    assert numbers == list(range(numbers[0], numbers[-1] + 1))  # no number skipped, none repeated


# --------------------------------------------------------------------------------------------- ordering across modes
async def test_progress_of_one_draft_only_moves_forward_whatever_mode_asked_for_the_field(ctx, monkeypatch):
    """Scan, focus, refresh and a conditional follow-up all touch the page while one draft is written: the events of that
    draft are numbered in order and its status never goes back; the page ends with the finished draft."""
    first = await scan(ctx, [narrative(1), el(3, "textbox", "First name")])
    await started(ctx)
    start = first["cursor"]
    await scan(ctx, [narrative(1), el(3, "textbox", "First name")], mode="focus", focus_node_id=1)
    await scan(ctx, [el(3, "textbox", "First name")], mode="refresh")
    await scan(ctx, [narrative(1)], mode="refresh")
    await scan(ctx, [narrative(1), el(3, "textbox", "First name")], mode="scan")
    await finish_drafts(ctx)
    events = (await poll(ctx, start))["events"]
    numbers = [n for n, _ in mine(ctx, start)]
    assert numbers == list(range(start + 1, session_of(ctx).cursor + 1))  # contiguous: every number was delivered
    rank = {"QUEUED": 0, "GENERATING": 1, "REGENERATING": 2, "VALIDATING": 3, "DONE": 4, "FAILED": 4}
    by_job = {}
    for suggestion in suggestions_of(events):
        generation = suggestion.get("generation")
        if generation:
            by_job.setdefault(generation["id"], []).append(generation)
    assert by_job and all(
        [(g["attempt"], rank[g["status"]]) for g in seq] == sorted((g["attempt"], rank[g["status"]]) for g in seq)
        for seq in by_job.values()), by_job
    final = client_view(suggestions_of(events))
    assert [s["state"] for s in final.values() if s["question"] == QUESTION] == ["GENERATED_DRAFT"]


async def test_a_field_that_left_the_page_gets_no_event_when_its_draft_finishes(ctx):
    await scan(ctx, [narrative(1), el(3, "textbox", "First name")])
    await started(ctx)
    start = session_of(ctx).cursor
    await scan(ctx, [el(3, "textbox", "First name")])  # the textarea is gone (the form re-rendered)
    await finish_drafts(ctx)
    assert suggestions_of([e for _, e in mine(ctx, start)]) == []
    audited = ctx.app.state.db.query("SELECT kind FROM extension_events WHERE kind='draft_ready'")
    assert len(audited) == 1  # the money was spent, so the audit trail says so, whether or not a field is waiting for it


async def test_a_suggest_request_never_rolls_back_the_knowledge_version_that_an_event_announced_meanwhile(ctx, monkeypatch):
    """The profile changes in Meridian while a scan is being answered: the page's poll notices it and announces the new
    knowledge version. The scan was computed under the old one; when it ends it must not make the session forget the new
    one (the next poll would announce it again, and a client that fell behind would be told the OLD version)."""
    session = session_of(ctx)
    await scan(ctx, [el(1, "textbox", "First name")])
    old_version = session.known_version

    async def profile_changes():
        ctx.app.state.db.execute("UPDATE facts SET value='\"Changed\"',updated_at='2999-01-01' WHERE key='first_name' "
                                 "AND source LIKE 'Synthetic%'")
        ctx.registry._token_at = float("-inf")  # the fingerprint is read afresh, not served from its 250 ms cache
        await ctx.registry._check_all([session])

    start = session.cursor
    hook_at(monkeypatch, "audit", profile_changes)
    response = await scan(ctx, [el(1, "textbox", "First name")])
    announced = [e["knowledge_version"] for e in (await poll(ctx, start))["events"] if e["type"] == "knowledge"]
    assert len(announced) == 1 and announced[0] != old_version
    assert response["knowledge_version"] == old_version  # answered under what was true when the request began
    assert session.known_version == announced[0]
    assert (await poll(ctx, session.cursor))["events"] == []  # nothing is announced a second time


# --------------------------------------------------------------------------------------------- events of a job that is no longer the field's
async def flip_constraints(ctx):
    """The same question three times: 800 characters (draft finished), 500 (a new draft is being written), 800 again."""
    first = await scan(ctx, [narrative(1, max_length=800)])
    ctx.model.gate.set()
    await drained(ctx)
    ctx.model.gate.clear()
    done = next(s for s in (await scan(ctx, [narrative(1, max_length=800)]))["results"])
    assert done["state"] == "GENERATED_DRAFT"
    shorter = (await scan(ctx, [narrative(1, max_length=500)]))["results"][0]
    assert shorter["state"] == "GENERATING" and shorter["generation"]["id"] != done["generation"]["id"]
    await started(ctx)
    start = session_of(ctx).cursor
    back = (await scan(ctx, [narrative(1, max_length=800)]))["results"][0]
    return first, done, shorter, back, start


async def test_the_events_of_a_draft_written_for_other_constraints_never_repaint_the_field(ctx):
    """A page that shows the same question under other constraints (a limit that changes, a conditional that appears and
    goes) re-asks with a different draft: when the OLD draft finishes late it must not replace what the field shows now."""
    _, done, shorter, back, start = await flip_constraints(ctx)
    assert back["state"] == "GENERATED_DRAFT" and back["generation"]["id"] == done["generation"]["id"]
    await finish_drafts(ctx)  # the draft for 500 characters finishes now, while the field is on 800 again
    events = (await poll(ctx, start))["events"]
    assert [s for s in suggestions_of(events) if s["generation"]["id"] == shorter["generation"]["id"]] == []
    stored = next(iter(session_of(ctx).fields.values())).suggestion
    assert key_facts(stored) == key_facts(back)  # the server still holds the draft for the constraints on the page
    spent = ctx.app.state.db.query("SELECT kind FROM extension_events WHERE kind='draft_ready'")
    assert len(spent) == 2  # both drafts were paid for and audited


async def test_a_revision_does_not_silence_the_earlier_draft_of_the_same_question(ctx):
    """'Regenerate' while the first draft is still being written: both are drafts of THIS question, so the first one's
    events still reach the field (an earlier valid draft is never taken away by a revision that is still running)."""
    first = (await scan(ctx, [narrative(1)]))["results"][0]
    await started(ctx)
    start = session_of(ctx).cursor
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
        "question_id": first["question_id"], "node_id": 1, "frame_key": "top", "instruction": "shorter"})
    assert response.status_code == 200 and response.json()["generation"]["id"] != first["generation"]["id"]
    await finish_drafts(ctx)
    events = (await poll(ctx, start))["events"]
    ids = [s["generation"]["id"] for s in suggestions_of(events) if s["generation"]]
    assert first["generation"]["id"] in ids and response.json()["generation"]["id"] in ids
    assert key_facts(suggestions_of(events)[-1])[0] == "GENERATED_DRAFT"


async def test_a_draft_still_being_written_when_the_user_corrects_the_research_still_reaches_its_field(ctx):
    """'Not this company' changes the basis of the drafts of FUTURE requests (nothing is regenerated silently), but the
    draft that is already being written is a draft of the very same question: when it finishes, the field gets it (a field
    that never hears about it would say "writing a draft" for ever, as the page does not re-scan after a correction)."""
    first = (await scan(ctx, [narrative(1)]))["results"][0]
    await started(ctx)
    start = session_of(ctx).cursor
    corrected = await ctx.client.post(f"/v1/sessions/{ctx.sid}/research/company", json={"company": "Acme Robotics Ltd"})
    assert corrected.status_code == 200 and session_of(ctx).epoch == 1
    await finish_drafts(ctx)
    events = (await poll(ctx, start))["events"]
    mine_ = [s for s in suggestions_of(events) if s["question_id"] == first["question_id"]]
    assert mine_ and mine_[-1]["state"] == "GENERATED_DRAFT" and mine_[-1]["generation"]["id"] == first["generation"]["id"]


async def test_when_the_check_for_an_old_draft_cannot_decide_the_field_still_hears_about_its_draft(ctx, monkeypatch):
    def broken(self, service, key, job):
        raise RuntimeError("cannot tell")

    monkeypatch.setattr(S.Session, "drafts_this_field", broken)
    first = await scan(ctx, [narrative(1)])
    await finish_drafts(ctx)
    events = (await poll(ctx, first["cursor"]))["events"]
    assert suggestions_of(events)[-1]["state"] == "GENERATED_DRAFT"  # never left pulsing "writing a draft" for ever


# --------------------------------------------------------------------------------------------- the window is bounded, not silent
async def test_a_client_that_fell_behind_the_window_still_gets_the_newest_research_snapshot(ctx):
    """Past ``MAX_EVENTS`` the oldest events go and the client is told to re-scan (``knowledge``), but the research strip
    is only ever updated by research snapshots: the newest one must survive the trim, or the strip stays stale for good."""
    session = session_of(ctx)
    session.emit({"type": "research", "research": {"run_id": "r1", "state": "done", "marker": "newest"}})
    for number in range(S.MAX_EVENTS + 25):
        session.emit({"type": "tracked", "tracked": "preparing" if number % 2 else "none"})
    body = await poll(ctx, 0)
    types = [e["type"] for e in body["events"]]
    assert types[0] == "knowledge" and types.count("research") == 1
    assert [e for e in body["events"] if e["type"] == "research"][0]["research"]["marker"] == "newest"
    assert body["cursor"] == session.cursor
    assert (await poll(ctx, body["cursor"]))["events"] == []
    newer = {"type": "research", "research": {"run_id": "r1", "state": "done", "marker": "even newer"}}
    session.emit(newer)
    assert [e for e in (await poll(ctx, 0))["events"] if e["type"] == "research"] == [newer]  # replaced, never doubled
