"""A draft is only offered while what it was written from still stands (SAFE-11, CPL-09), and a page cannot make Meridian write
drafts without limit (SEC-06). The model is scripted: every test counts its calls, because "no money was spent" is the point."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import el  # noqa: E402
from jobagent.extension import api as extension_api  # noqa: E402
from jobagent.extension import draft_basis, draft_limits  # noqa: E402
from jobagent.extension.draft_limits import DraftLedger, stable_text  # noqa: E402
from jobagent.extension.generation import GenerationQueue, Refused  # noqa: E402
from jobagent.intelligence.store import update_fact  # noqa: E402
from test_extension_generation import QUESTION, collect, drained, narrative, offline, scan  # noqa: E402,F401
from test_extension_generation import ctx as generation_ctx  # noqa: E402,F401  (the page + scripted model of those tests)


@pytest.fixture
def ctx(generation_ctx):  # noqa: F811
    return generation_ctx


def session_of(ctx):
    return ctx.app.state.extension_sessions.sessions[ctx.sid]


async def settled(ctx):
    await ctx.app.state.extension_research.wait(session_of(ctx).research_run_id, 10)


async def written(ctx, **kwargs):
    """The page's research is settled and the draft of the question is written; -> its suggestion as the page sees it."""
    await settled(ctx)
    await scan(ctx, [narrative(1, **kwargs)])
    await drained(ctx)
    return (await scan(ctx, [narrative(1, **kwargs)]))[1]


def cited_fact(ctx, needle="vision pipeline"):
    return ctx.app.state.db.one("SELECT f.id,f.value FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id "
                                "WHERE k.concept='story' AND f.value LIKE ?", (f"%{needle}%",))


# --------------------------------------------------------------------------------------------- SAFE-11
async def test_a_draft_that_cites_a_corrected_fact_is_no_longer_offered(ctx):
    """The reviewer's case: the user fixes the fact the draft cites, and the old text came back as GENERATED_DRAFT."""
    draft = await written(ctx)
    assert draft["state"] == "GENERATED_DRAFT" and draft["suggestion"]["text"] == "I built a vision pipeline using Python."
    fact = cited_fact(ctx)
    assert fact["id"] in str(draft["generation"]["claims"])
    update_fact(ctx.app.state.db, fact["id"], "I built a small prototype and never shipped it.", True)
    after = (await scan(ctx, [narrative(1)]))[1]
    assert after["state"] == "NEEDS_YOU" and after["insert"] == "never" and after["suggestion"] is None
    assert after["needs"]["code"] == "draft_outdated" and "profile changed" in after["needs"]["message"]
    assert "draft_with_ai" in {a["type"] for a in after["needs"]["actions"]} and after["why"]["code"] == "draft_outdated"
    assert after["based_on"]["stale"] is True and after["based_on"]["reason"] == "facts_changed"
    assert len(ctx.model.drafts) == 1  # nothing was rewritten (or paid for) on its own


@pytest.mark.parametrize("change", ["unverified", "rejected", "locked_value"])
async def test_every_way_of_withdrawing_a_cited_fact_demotes_the_draft(ctx, change):
    draft = await written(ctx)
    fact = cited_fact(ctx)
    db = ctx.app.state.db
    if change == "unverified":
        update_fact(db, fact["id"], fact["value"], False)
    elif change == "rejected":
        db.execute("UPDATE facts SET verification_status='rejected' WHERE id=?", (fact["id"],))
    else:
        db.execute("UPDATE facts SET value=?,locked=1 WHERE id=?", ('"changed and locked"', fact["id"]))
    after = (await scan(ctx, [narrative(1)]))[1]
    assert draft["state"] == "GENERATED_DRAFT" and after["state"] == "NEEDS_YOU" and after["needs"]["code"] == "draft_outdated"


async def test_a_change_to_a_fact_the_draft_does_not_cite_leaves_it_alone(ctx):
    await written(ctx)
    other = ctx.app.state.db.one("SELECT f.id,f.value FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id "
                                 "WHERE k.concept='email'")
    update_fact(ctx.app.state.db, other["id"], "other@example.test", True)
    again = (await scan(ctx, [narrative(1)]))[1]
    assert again["state"] == "GENERATED_DRAFT" and "stale" not in (again["based_on"] or {})
    assert len(ctx.model.drafts) == 1


async def test_asking_for_a_new_draft_after_the_change_writes_one_from_the_current_facts(ctx):
    draft = await written(ctx)
    fact = cited_fact(ctx)
    update_fact(ctx.app.state.db, fact["id"], "I built a small prototype and never shipped it.", True)
    outdated = (await scan(ctx, [narrative(1)]))[1]
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
        "question_id": outdated["question_id"], "node_id": 1, "frame_key": "top", "instruction": "again"})
    assert response.status_code == 200
    await drained(ctx)
    fresh = (await scan(ctx, [narrative(1)]))[1]
    assert fresh["state"] == "GENERATED_DRAFT" and fresh["suggestion"]["text"] != draft["suggestion"]["text"]
    assert fresh["generation"]["id"] != draft["generation"]["id"] and "stale" not in (fresh["based_on"] or {})
    assert len(ctx.model.drafts) == 2


def test_the_basis_of_a_draft_pure(tmp_path):
    from jobagent.db import Database
    (tmp_path / "database").mkdir()
    db = Database(tmp_path / "database/meridian.sqlite3")
    candidate = db.one("SELECT id FROM candidates LIMIT 1")
    if candidate is None:
        db.execute("INSERT INTO candidates(id,name,created_at,updated_at) VALUES('c1','x','t','t')")
        candidate = {"id": "c1"}
    db.execute("INSERT INTO facts VALUES('f1',?, 'k','a','one','src','verified',0,'','t','t')", (candidate["id"],))
    db.execute("INSERT INTO facts VALUES('f2',?, 'k','b','two','src','verified',0,'','t','t')", (candidate["id"],))
    before = draft_basis.snapshot(db)
    result = {"fact_ids": ["f1"], "claims": [{"text": "x", "fact_ids": ["f2", "f2"]}, {"fact_ids": []}]}
    assert draft_basis.cited_fact_ids(result) == ["f1", "f2"]
    basis = draft_basis.stamp(result, before, bundle="B1", company_key="acme")
    judge = lambda **kw: draft_basis.verdict(db, basis, **{"bundle": "B1", "bundle_settled": True, "rejected": set(), **kw})  # noqa: E731
    assert judge()["status"] == "fresh" and draft_basis.verdict(db, None, bundle="", bundle_settled=True, rejected=set())["status"] == "fresh"
    assert judge(bundle="B2")["status"] == "stale" and judge(bundle="B2", bundle_settled=False)["status"] == "fresh"
    assert judge(rejected={"acme"})["reason"] == "company_rejected" and judge(rejected={"acme"})["status"] == "outdated"
    db.execute("UPDATE facts SET value='changed' WHERE id='f2'")
    assert judge() == {"status": "outdated", "reason": "facts_changed", "facts": ["f2"]}
    db.execute("DELETE FROM facts WHERE id='f1'")
    assert judge()["facts"] == ["f1", "f2"]  # a removed fact is a changed fact


# --------------------------------------------------------------------------------------------- CPL-09
async def test_a_draft_exposes_the_research_bundle_it_was_written_from(ctx):
    draft = await written(ctx)
    run = session_of(ctx).research_run_id
    bundle = ctx.app.state.extension_research.bundle_hash(run)
    assert bundle and draft["based_on"]["research_bundle"] == bundle and "stale" not in draft["based_on"]


async def test_a_draft_written_from_older_research_says_so_and_stays_usable(ctx, monkeypatch):
    draft = await written(ctx)
    research = ctx.app.state.extension_research
    start = session_of(ctx).cursor
    monkeypatch.setattr(research, "bundle_hash", lambda run: "a-newer-bundle")
    session_of(ctx).research_listener(research.snapshot(session_of(ctx).research_run_id))  # a settled snapshot arrives
    events = (await collect(ctx, lambda seen: any(e["type"] == "suggestion" for e in seen)))
    marked = [e["suggestion"] for e in events if e["type"] == "suggestion"][-1]
    assert marked["state"] == "GENERATED_DRAFT" and marked["insert"] == "tab"  # late research never takes a draft away
    assert marked["based_on"]["stale"] is True and marked["based_on"]["reason"] == "research_changed"
    assert marked["based_on"]["research_bundle"] == draft["based_on"]["research_bundle"]  # what it WAS written from
    assert len(ctx.model.drafts) == 1 and start < session_of(ctx).cursor


async def test_rejecting_the_company_makes_its_draft_unusable_and_the_page_is_told(ctx):
    draft = await written(ctx)
    assert draft["state"] == "GENERATED_DRAFT"
    start = session_of(ctx).cursor
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/research/company", json={"company": "Zeta Dynamics"})
    assert response.status_code == 200
    assert session_of(ctx).rejected_companies == {"acmerobotics"}
    body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": start, "wait_ms": 0})).json()
    told = [e["suggestion"] for e in body["events"] if e["type"] == "suggestion"]
    assert told and told[-1]["state"] == "NEEDS_YOU" and told[-1]["insert"] == "never" and told[-1]["suggestion"] is None
    assert told[-1]["needs"]["code"] == "draft_outdated" and told[-1]["based_on"]["reason"] == "company_rejected"
    assert "You said this is not Acme Robotics" in told[-1]["needs"]["message"]  # the company that was rejected, by name
    assert len(ctx.model.drafts) == 1  # the correction itself spent nothing


async def test_the_page_is_told_at_once_even_while_the_new_research_is_still_running(ctx, monkeypatch):
    """The research of the corrected company takes seconds; the draft about the rejected one must not stay Tab-insertable
    meanwhile (the settled-research notice that follows would be too late)."""
    from jobagent.extension import session as S
    draft = await written(ctx)
    assert draft["state"] == "GENERATED_DRAFT"
    monkeypatch.setattr(S.Session, "research_listener", lambda self, snapshot: None)  # the new run has not settled yet
    start = session_of(ctx).cursor
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/research/company", json={"company": "Zeta Dynamics"})
    assert response.status_code == 200
    body = (await ctx.client.post(f"/v1/sessions/{ctx.sid}/poll", json={"cursor": start, "wait_ms": 0})).json()
    told = [e["suggestion"] for e in body["events"] if e["type"] == "suggestion"]
    assert told and told[-1]["state"] == "NEEDS_YOU" and told[-1]["based_on"]["reason"] == "company_rejected"


async def test_correcting_the_company_to_its_own_longer_name_rejects_nothing(ctx):
    draft = await written(ctx)
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/research/company", json={"company": "Acme Robotics Ltd"})
    assert response.status_code == 200 and session_of(ctx).rejected_companies == set()
    assert (await scan(ctx, [narrative(1)]))[1]["state"] in {"GENERATED_DRAFT", "GENERATING"} and draft["state"] == "GENERATED_DRAFT"


# --------------------------------------------------------------------------------------------- SEC-06
def test_counters_and_reference_numbers_are_not_part_of_a_question():
    base = stable_text("Why do you want to work at Acme Robotics?")
    for variant in ("Why do you want to work at Acme Robotics? (reference 7)", "WHY do you want   to work at Acme Robotics?  [ref: A12]",
                    "Why do you want to work at Acme Robotics? (Attempt 3)", "Why do you want to work at Acme Robotics? ref 9"):
        assert stable_text(variant) == base, variant
    counted = stable_text("Why Acme? (0/500)")  # a counter keeps its LIMIT: a question limited to 300 is another question
    for variant in ("Why Acme? (186/500)", "why acme? (499/500)", "WHY ACME?   ( 7 / 500 )"):
        assert stable_text(variant) == counted or stable_text(variant) == stable_text("Why Acme? ( 0 / 500 )"), variant
    assert stable_text("Why Acme? 12 / 500") == stable_text("Why Acme? 3/500")
    assert stable_text("Why Acme? (0/500)") != stable_text("Why Acme? (0/300)")
    assert stable_text("Why Acme? 120 characters left") == stable_text("Why Acme? 7 characters left")
    assert stable_text("Describe project 1 in detail") != stable_text("Describe project 2 in detail")  # a number can matter
    assert stable_text("Write at most 500 words") != stable_text("Write at most 300 words")
    assert stable_text(None) == ""


async def test_a_page_that_rewords_its_question_every_time_costs_one_draft(ctx):
    """The reviewer's hostile fixture: a label that changes ('(reference N)') made a new paid draft for every variant."""
    await settled(ctx)
    for number in range(1, 13):
        await scan(ctx, [narrative(1, f"{QUESTION} (reference {number})")])
    await drained(ctx)
    assert len(ctx.model.drafts) == 1


async def test_an_honest_character_counter_in_the_help_text_costs_one_draft(ctx):
    """The honest variant: the user types an answer and the counter in the field's help text changes with every key."""
    await settled(ctx)
    for typed in range(0, 200, 10):
        await scan(ctx, [narrative(1, description=f"{typed}/800 characters")])
    await drained(ctx)
    assert len(ctx.model.drafts) == 1


async def test_genuinely_different_questions_each_get_their_own_draft(ctx):
    await settled(ctx)
    await scan(ctx, [narrative(1, "Describe project 1 in detail and what you learned.")])
    await scan(ctx, [narrative(3, "Describe project 2 in detail and what you learned.")])
    await drained(ctx)
    assert len(ctx.model.drafts) == 2


async def test_a_page_may_have_only_so_many_drafts_written_on_its_own_and_is_told_once(ctx):
    ctx.app.state.extension_generation.ledger.limits["session"] = 2
    await settled(ctx)
    questions = [narrative(1 + 2 * i, f"Question {i}: tell us about topic {i} that matters to you most.") for i in range(5)]
    results = await scan(ctx, questions)
    await drained(ctx)
    again = await scan(ctx, questions)
    assert len(ctx.model.drafts) == 2
    limited = [r for r in again.values() if r["state"] == "NEEDS_YOU"]
    assert len(limited) == 3 and all("already wrote 2 drafts" in r["needs"]["message"] for r in limited)
    assert all("draft_with_ai" in {a["type"] for a in r["needs"]["actions"]} or r["needs"]["code"] == "missing_fact" for r in limited)
    events = await collect(ctx, lambda seen: any(e["type"] == "drafts_paused" for e in seen))
    paused = [e for e in events if e["type"] == "drafts_paused"]
    assert len(paused) == 1 and paused[0]["code"] == "AUTO_DRAFT_LIMIT" and "2 drafts" in paused[0]["message"]
    assert not [e for e in events if e["type"] == "generation_budget"]  # drafts the user asks for are still available
    assert results


async def test_a_draft_the_user_asks_for_is_never_held_back_by_the_automatic_limit(ctx):
    ctx.app.state.extension_generation.ledger.limits["session"] = 1
    await settled(ctx)
    questions = [narrative(1, "Question A: tell us about topic A that matters to you most."),
                 narrative(3, "Question B: tell us about topic B that matters to you most.")]
    first = await scan(ctx, questions)
    await drained(ctx)
    blocked = next(r for r in (await scan(ctx, questions)).values() if r["state"] == "NEEDS_YOU")
    node = 1 if blocked["node_id"] == 1 else 3
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/regenerate", json={
        "question_id": blocked["question_id"], "node_id": node, "frame_key": "top", "instruction": "again"})
    assert response.status_code == 200 and response.json()["state"] == "GENERATING"
    await drained(ctx)
    assert len(ctx.model.drafts) == 2 and first


async def test_the_limit_of_a_browser_holds_across_its_pages(ctx):
    from harness import posting
    ctx.app.state.extension_generation.ledger.limits["client"] = 2
    other = (await ctx.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/4002"))).json()
    await settled(ctx)
    await scan(ctx, [narrative(1, "Question A: tell us about topic A that matters to you most."),
                     narrative(3, "Question B: tell us about topic B that matters to you most.")])
    response = await ctx.client.post(f"/v1/sessions/{other['session_id']}/suggest", json={
        "document_id": "doc-2", "frame_url": "https://boards.greenhouse.io/acmerobotics/jobs/4002", "frame_key": "top",
        "elements": [narrative(1, "Question C: tell us about topic C that matters to you most.")], "mode": "scan"})
    assert response.status_code == 200 and response.json()["results"][0]["state"] == "NEEDS_YOU"
    await drained(ctx)
    assert len(ctx.model.drafts) == 2


def test_the_ledger_counts_per_page_browser_and_overall_in_a_rolling_hour():
    clock = [1000.0]
    ledger = DraftLedger(clock=lambda: clock[0], per_session=2, per_client=3, total=4, window=100)
    assert ledger.check("s1", "c1") is None
    ledger.record("s1", "c1")
    ledger.record("s1", "c1")
    assert ledger.check("s1", "c1")[0] == "session" and ledger.check("s2", "c1") is None and ledger.used("s1") == 2
    ledger.record("s2", "c1")
    assert ledger.check("s3", "c1")[0] == "client" and ledger.check("s3", "c2") is None
    ledger.record("s3", "c2")
    assert ledger.check("s4", "c9")[0] == "total"
    clock[0] += 101  # an hour later (here 100 s) everything is allowed again
    assert ledger.check("s1", "c1") is None and ledger.used("s1") == 0


async def test_the_queue_refuses_with_a_calm_reason_and_hears_about_the_limit():
    heard = []
    queue = GenerationQueue(ledger=DraftLedger(per_session=2))
    queue.register_session("s1", lambda *a: None, client_id="c1", on_limit=lambda code, message: heard.append((code, message)))

    async def run(related):
        return {"answer": "A.", "confidence_level": "GENERATED", "state": "ANSWER_VALIDATED", "quality": {"passed": True},
                "cost": 0.0}

    for number in range(2):
        queue.submit(session_id="s1", key=f"k{number}", order=(0, number), question_id=f"q{number}", runner=run,
                     listener_id=f"f{number}", listener=lambda job: None)
    with pytest.raises(Refused) as caught:
        queue.submit(session_id="s1", key="k9", order=(0, 9), question_id="q9", runner=run, listener_id="f9",
                     listener=lambda job: None)
    assert caught.value.code == "draft_limit" and heard and heard[0][0] == "AUTO_DRAFT_LIMIT"
    queue.submit(session_id="s1", key="k9", order=(0, 9), question_id="q9", runner=run, listener_id="f9",
                 listener=lambda job: None, explicit=True)  # the user asked: never held back
    await queue.close()


async def test_a_page_has_a_draft_budget_by_default(ctx):
    assert extension_api.read_extension_settings(ctx.app.state.db)["draft_budget_usd"] == 1.0
    registry = ctx.app.state.extension_sessions
    session = session_of(ctx)
    assert registry.guard(session.id) is None
    session.draft_spend = 1.0
    code, message, scope = registry.guard(session.id)
    assert code == "AI_DRAFT_BUDGET" and scope == "session" and "$1.00" in message
    from harness import put_setting
    put_setting(ctx.app.state.db, "extension_draft_budget_usd", 5.0)
    assert registry.guard(session.id) is None  # the user's own amount replaces the default


def test_the_constants_are_sane():
    assert draft_limits.SESSION_AUTO_DRAFTS >= 6  # one form's worth of essay questions
    assert draft_limits.SESSION_AUTO_DRAFTS < draft_limits.CLIENT_AUTO_DRAFTS < draft_limits.TOTAL_AUTO_DRAFTS
    assert el(1, "textbox", "x")["node_id"] == 1
