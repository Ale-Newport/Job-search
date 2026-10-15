"""The bridge routes through the real bridge application: authentication, validation, errors, long-poll and the
lifecycle of the services. Offline; tokens are minted through the same helpers pairing uses."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, add_cv, bridge, el, http, make_app, posting, suggest_body  # noqa: E402
from jobagent.db import now  # noqa: E402
from jobagent.extension import session as S  # noqa: E402
from jobagent.extension.app import create_bridge_app  # noqa: E402
from jobagent.extension.auth import new_token  # noqa: E402
from jobagent.extension.schemas import (DocumentContent, DocumentInfo, FeedbackResponse, PollResponse,  # noqa: E402
                                        SuggestResponse, TrackResponse)
from jobagent.extension.state import close_extension_state, install_extension_state  # noqa: E402

ORIGIN = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc"
SNAPSHOT_KEYS = {"run_id", "state", "version", "ready_for_answers", "quality", "stale", "headline", "needs", "company",
                 "site", "role", "brief", "brief_source", "stages", "sources", "notes", "updated_at"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def pair(app, origin=ORIGIN, browser="chrome"):
    """A paired client exactly as pairing leaves it: only sha256(secret) is stored."""
    client_id = str(uuid.uuid4())
    token, digest = new_token(client_id)
    app.state.db.execute("INSERT INTO extension_clients(id,name,browser,origin,token_hash,version,created_at) VALUES(?,?,?,?,?,?,?)",
                         (client_id, "Test browser", browser, origin, digest, "1.0.0", now()))
    return client_id, {"Authorization": f"Bearer {token}", "Origin": origin}


@pytest.fixture
async def real(tmp_path):
    app = make_app(tmp_path)
    ScriptedModel().install(app)
    bridge_app = create_bridge_app(app)
    assert bridge_app.state.routes_loaded is True, bridge_app.state.routes_error
    _, headers = pair(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app), base_url="http://127.0.0.1",
                                 headers=headers, timeout=60) as client:
        yield SimpleNamespace(app=app, client=client, headers=headers, bridge=bridge_app)
    await close_extension_state(app)


# --------------------------------------------------------------------------------------------- authentication
ROUTES = [("POST", "/v1/sessions", {}), ("DELETE", "/v1/sessions/ses_x", None), ("POST", "/v1/sessions/ses_x/suggest", {}),
          ("POST", "/v1/sessions/ses_x/poll", {}), ("POST", "/v1/sessions/ses_x/regenerate", {}),
          ("POST", "/v1/sessions/ses_x/feedback", {}), ("POST", "/v1/sessions/ses_x/track", {}),
          ("POST", "/v1/sessions/ses_x/research/company", {}), ("POST", "/v1/sessions/ses_x/research/site", {}),
          ("POST", "/v1/sessions/ses_x/research/refresh", None), ("GET", "/v1/documents", None),
          ("GET", "/v1/documents/x/content", None)]


@pytest.mark.parametrize("method,path,body", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
async def test_every_route_needs_a_paired_browser(real, method, path, body):
    anonymous = {"Origin": ORIGIN}
    response = await real.client.request(method, path, json=body, headers={**anonymous, "Authorization": ""})
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"
    other = await real.client.request(method, path, json=body, headers={
        "Origin": ORIGIN, "Authorization": "Bearer mrd1." + str(uuid.uuid4()) + "." + "A" * 43})
    assert other.status_code == 401 and other.json() == {"detail": other.json()["detail"], "code": "unauthorized"}


async def test_a_browser_can_only_reach_its_own_sessions(real):
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    sid = created["session_id"]
    _, stranger = pair(real.app, origin="safari-web-extension://abcdef12-0000", browser="safari")
    for method, path, body in (("POST", f"/v1/sessions/{sid}/suggest", suggest_body([el(1, "textbox", "First name")])),
                               ("POST", f"/v1/sessions/{sid}/poll", {"cursor": 0, "wait_ms": 0}),
                               ("DELETE", f"/v1/sessions/{sid}", None)):
        response = await real.client.request(method, path, json=body, headers=stranger)
        assert response.status_code == 404 and response.json()["code"] == "not_found", path
    still = await real.client.post(f"/v1/sessions/{sid}/poll", json={"cursor": 0, "wait_ms": 0})
    assert still.status_code == 200
    mine = await real.client.post("/v1/sessions", json=posting(), headers=stranger)
    assert mine.json()["session_id"] != sid  # the same page in another browser is another session


# --------------------------------------------------------------------------------------------- the happy path
async def test_a_whole_page_visit_has_the_documented_shapes(real):
    created = await real.client.post("/v1/sessions", json=posting())
    assert created.status_code == 200
    body = created.json()
    assert set(body) == {"session_id", "protocol", "knowledge_version", "job", "research", "generation", "settings", "cursor"}
    assert body["protocol"] == 1 and set(body["research"]) == SNAPSHOT_KEYS
    assert set(body["job"]) == {"company", "title", "location", "job_id", "application_id", "tracked"}
    assert body["job"]["company"] == "Acme Robotics" and body["job"]["tracked"] == "none"
    assert set(body["generation"]) == {"available", "tested", "provider", "local", "message", "monthly_limit_usd", "spent_usd"}
    assert set(body["settings"]) == {"enabled", "language", "auto_advance", "skip_filled", "tab_inserts_protected",
                                     "save_corrections", "background_research", "background_drafts", "local_assist", "web_lookup",
                                     "hint_on_ats", "draft_budget_usd", "always_on_sites"}
    sid = body["session_id"]
    add_cv(real.app, filename="cv.pdf")
    suggested = await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
        el(1, "textbox", "First name"), el(3, "upload", "Resume"), el(5, "textbox", "Password", type="password"),
        el(7, "checkbox", "I agree to the privacy policy", group="p"),
        el(9, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea")]))
    assert suggested.status_code == 200
    parsed = SuggestResponse.model_validate(suggested.json())
    states = {r.state for r in parsed.results}
    # the draft may already be written when the response is sent (the response is never older than what has happened)
    assert states - {"GENERATING", "GENERATED_DRAFT"} == {"VERIFIED", "SKIP", "CONSENT"} and len(states) == 4
    assert (await real.client.get("/v1/documents")).status_code == 200
    documents = [DocumentInfo.model_validate(d) for d in (await real.client.get("/v1/documents")).json()]
    content = DocumentContent.model_validate((await real.client.get(f"/v1/documents/{documents[0].id}/content")).json())
    assert content.filename == "cv.pdf"
    await asyncio.sleep(1.0)
    polled = await real.client.post(f"/v1/sessions/{sid}/poll", json={"cursor": body["cursor"], "wait_ms": 0})
    events = PollResponse.model_validate(polled.json())
    assert {e["type"] for e in events.events} <= {"research", "suggestion", "knowledge", "tracked", "generation_budget"}
    first = parsed.results[0]
    saved = await real.client.post(f"/v1/sessions/{sid}/feedback", json={
        "question_id": first.question_id, "frame_key": "top", "outcome": "accepted"})
    assert FeedbackResponse.model_validate(saved.json()).saved is False
    noted = await real.client.post(f"/v1/sessions/{sid}/track", json={"event": "submit_clicked"})
    assert TrackResponse.model_validate(noted.json()).state == "noted"
    released = await real.client.delete(f"/v1/sessions/{sid}")
    assert released.status_code == 200 and released.json() == {"released": True}
    await asyncio.sleep(0.05)


async def test_a_page_that_reloads_gets_the_same_session_and_a_restart_says_expired(real):
    first = (await real.client.post("/v1/sessions", json=posting())).json()
    again = (await real.client.post("/v1/sessions", json=posting(url=posting()["page"]["url"] + "/application"))).json()
    assert again["session_id"] == first["session_id"]  # the application sub-page is the same posting
    registry = real.app.state.extension_sessions
    registry.sessions.clear()  # what a restarted sidecar looks like
    registry.by_page.clear()
    gone = await real.client.post(f"/v1/sessions/{first['session_id']}/suggest", json=suggest_body([el(1, "textbox", "First name")]))
    assert gone.status_code == 410 and gone.json()["code"] == "expired"
    reopened = (await real.client.post("/v1/sessions", json=posting())).json()
    assert reopened["session_id"] == first["session_id"]  # the stored row keeps the id: question ids stay valid
    rows = real.app.state.db.query("SELECT company,title,host,research_state FROM extension_sessions")
    assert len(rows) == 1 and rows[0]["company"] == "Acme Robotics" and rows[0]["host"] == "boards.greenhouse.io"
    unknown = await real.client.post("/v1/sessions/ses_unknown/suggest", json=suggest_body([el(1, "textbox", "First name")]))
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"


# --------------------------------------------------------------------------------------------- validation
async def test_bad_requests_are_rejected_calmly_without_echoing_page_content(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    bad = [("/v1/sessions", {"page": {"url": 5}}), ("/v1/sessions", {"page": {"url": "ftp://x.example/job"}}),
           ("/v1/sessions", {"page": {"url": "https://user:pw@x.example/job"}}), ("/v1/sessions", {}),
           (f"/v1/sessions/{sid}/suggest", {"elements": [{"role": "textbox"}]}),
           (f"/v1/sessions/{sid}/suggest", suggest_body([el(i, "textbox", "x") for i in range(401)])),
           (f"/v1/sessions/{sid}/poll", {"cursor": 0, "wait_ms": 25001}),
           (f"/v1/sessions/{sid}/feedback", {"question_id": "q", "outcome": "bogus"}),
           (f"/v1/sessions/{sid}/track", {"event": "bogus"}),
           (f"/v1/sessions/{sid}/research/site", {"domain": "x" * 400})]
    for path, payload in bad:
        response = await real.client.post(path, json=payload)
        assert response.status_code in {422, 400}, (path, response.status_code, response.text[:200])
        assert response.json()["code"] == "invalid" and "user:pw" not in response.text
    not_json = await real.client.post(f"/v1/sessions/{sid}/suggest", content=b"nope",
                                      headers={**real.headers, "Content-Type": "application/json"})
    assert not_json.status_code == 422 and not_json.json()["code"] == "invalid"


async def test_unknown_keys_are_dropped_so_a_page_cannot_smuggle_engine_inputs(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    element = el(1, "textbox", "First name")
    element.update({"_related_answers": [{"answer": "evil"}], "_condition": {"applies": True}, "question_id": "mine",
                    "_education_reference": "x", "_disclosure_parent_question": "x", "value": "typed secret"})
    body = suggest_body([element]) | {"admin": True, "_job": {"company": "evil"}}
    response = await real.client.post(f"/v1/sessions/{sid}/suggest", json=body)
    assert response.status_code == 200
    stored = real.app.state.extension_sessions.sessions[sid].frames["top"].elements[0]
    assert "_related_answers" not in stored and "_education_reference" not in stored and "question_id" not in stored
    assert stored["_condition"] == {} and "_disclosure_parent_question" not in stored  # only what the server computed
    assert response.json()["results"][0]["question_id"] != "mine"


async def test_bad_research_corrections_are_explained(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    for domain in ("boards.greenhouse.io", "https://www.linkedin.com/company/acme", "10.0.0.1", "localhost", "acme",
                   "https://acme.example:8443/"):
        response = await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": domain})
        assert response.status_code == 422 and response.json()["code"] == "invalid", domain
        assert len(response.json()["detail"]) > 10
    refused = await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Greenhouse"})
    assert refused.status_code == 422
    ok = await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Acme Robotics Ltd"})
    assert ok.status_code == 200 and set(ok.json()) == SNAPSHOT_KEYS and ok.json()["company"]["name"] == "Acme Robotics"
    refreshed = await real.client.post(f"/v1/sessions/{sid}/research/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["run_id"] != ok.json()["run_id"]


# --------------------------------------------------------------------------------------------- long poll
@pytest.fixture
async def session(tmp_path):
    app = make_app(tmp_path)
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        await app.state.extension_research.wait(created["research"]["run_id"], 60)  # its last research event is out
        registry = app.state.extension_sessions
        yield SimpleNamespace(app=app, client=client, sid=created["session_id"], cursor=created["cursor"],
                              state=registry.sessions[created["session_id"]])
    await close_extension_state(app)


async def poll(s, cursor, wait=0):
    response = await s.client.post(f"/v1/sessions/{s.sid}/poll", json={"cursor": cursor, "wait_ms": wait})
    assert response.status_code == 200
    return response.json()


async def waiting_on(session, older=None, timeout=60.0):
    """Wait until a poll (a newer one than ``older``) really is waiting for this page: no guessing with sleeps."""
    end = asyncio.get_running_loop().time() + timeout
    while session.state.poll in (None, older):
        assert asyncio.get_running_loop().time() < end, "the poll never started waiting"
        await asyncio.sleep(0.01)
    return session.state.poll


async def test_a_poll_waits_for_the_first_event_and_returns_immediately_with_it(session, monkeypatch):
    monkeypatch.setattr(S, "POLL_TICK", 60)  # only the event itself can end a 25 s wait early: a missed wake takes 25 s
    now_cursor = (await poll(session, 0))["cursor"]
    waiting = asyncio.create_task(poll(session, now_cursor, wait=25000))
    await waiting_on(session)
    assert not waiting.done()
    session.state.emit({"type": "tracked", "tracked": "preparing"})
    body = await asyncio.wait_for(waiting, 20)
    assert body["events"] == [{"type": "tracked", "tracked": "preparing"}] and body["cursor"] == now_cursor + 1
    empty = await poll(session, body["cursor"], wait=300)
    assert empty == {"cursor": body["cursor"], "events": []}  # nothing new: an empty answer after the wait


async def test_only_one_poll_per_session_is_outstanding(session, monkeypatch):
    monkeypatch.setattr(S, "POLL_TICK", 60)
    cursor = (await poll(session, 0))["cursor"]
    first = asyncio.create_task(poll(session, cursor, wait=25000))
    slot = await waiting_on(session)
    second = asyncio.create_task(poll(session, cursor, wait=25000))
    await waiting_on(session, older=slot)
    older = await asyncio.wait_for(first, 20)  # the newer poll made the older one return at once, empty
    assert older["events"] == [] and not second.done()
    session.state.emit({"type": "knowledge", "knowledge_version": "v2"})
    assert (await asyncio.wait_for(second, 20))["events"] == [{"type": "knowledge", "knowledge_version": "v2"}]


async def test_a_client_ahead_of_a_rebuilt_session_is_told_to_rescan(session):
    body = await poll(session, 999)
    assert body["cursor"] == session.state.cursor and [e["type"] for e in body["events"]] == ["knowledge"]


async def test_events_are_capped_and_a_slow_client_is_told_to_rescan(session):
    for index in range(S.MAX_EVENTS + 40):
        session.state.emit({"type": "tracked", "tracked": "none", "n": index})
    body = await poll(session, 0)
    assert len(session.state.events) == S.MAX_EVENTS
    assert body["events"][0]["type"] == "knowledge"  # something was dropped before this client read it
    assert len(body["events"]) == S.MAX_EVENTS + 1 and body["cursor"] == session.state.cursor
    caught_up = await poll(session, body["cursor"])
    assert caught_up["events"] == []


async def test_profile_changes_made_in_meridian_reach_an_open_page_as_a_knowledge_event(session):
    cursor = (await poll(session, 0))["cursor"]
    session.app.state.db.execute("UPDATE facts SET value='\"Changed\"',updated_at='2999-01-01' WHERE key='first_name' "
                                 "AND source LIKE 'Synthetic%'")
    body = await poll(session, cursor, wait=5000)
    knowledge = [e for e in body["events"] if e["type"] == "knowledge"]
    assert knowledge and knowledge[0]["knowledge_version"] != session.state.report.get("x")
    again = await session.client.post(f"/v1/sessions/{session.sid}/suggest", json=suggest_body([el(1, "textbox", "First name")]))
    assert again.json()["knowledge_version"] == knowledge[-1]["knowledge_version"]


# --------------------------------------------------------------------------------------------- lifecycle of the services
async def test_the_services_exist_on_every_app_and_are_inert_until_used(tmp_path):
    app = make_app(tmp_path)
    for name in ("extension_research", "extension_sessions", "extension_generation"):
        assert getattr(app.state, name) is not None
    sessions = app.state.extension_sessions
    assert sessions.sessions == {} and app.state.extension_research._tasks == set()
    assert app.state.text_ai.answer_llm.on_progress is None  # the model hook is only touched when a draft is queued
    before = (app.state.extension_sessions, app.state.extension_research)
    install_extension_state(app)  # idempotent
    assert (app.state.extension_sessions, app.state.extension_research) == before
    await close_extension_state(app)
    await close_extension_state(app)  # closing twice is harmless


async def test_closing_cancels_queued_drafts_and_restores_the_answer_model_hook(tmp_path):
    app = make_app(tmp_path)
    model = ScriptedModel(delay=0.5).install(app)
    sentinel = lambda update: None  # noqa: E731
    app.state.text_ai.answer_llm.on_progress = sentinel
    async with http(bridge(app)) as client:
        sid = (await client.post("/v1/sessions", json=posting())).json()["session_id"]
        await client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea"),
            el(3, "textbox", "Describe a project you are proud of and what you learned.", tag="textarea", type="textarea")]))
        await asyncio.sleep(0.1)
        assert app.state.text_ai.answer_llm.on_progress != sentinel  # chained router while drafting
        await close_extension_state(app)
        assert app.state.text_ai.answer_llm.on_progress == sentinel
        await asyncio.sleep(0.8)
        assert len(model.drafts) <= 1  # the queued draft never started


async def test_the_bridge_reports_extension_events_for_the_desktop_activity_view(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
        el(1, "textbox", "First name"), el(3, "checkbox", "I agree to the privacy policy", group="p")]))
    from jobagent.extension.api import activity_items, tracker_assistant
    messages = [i["message"] for i in activity_items(real.app.state.db)]
    assert any("Helped with" in m for m in messages) and any("sensitive question" in m for m in messages)
    assert tracker_assistant(real.app.state.db) == {}  # nothing was saved: no job, so no tracker row


# --------------------------------------------------------------------------------------------- research corrections (wave 2)
class SiteWeb:
    """A scripted public web: a few pages by URL, everything else is a 404. Calls are recorded."""

    def __init__(self, pages=None):
        self.pages, self.calls = dict(pages or {}), []

    async def __call__(self, url, **kwargs):
        from jobagent.extension.research import FetchedPage, FetchError
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError("http_error", "HTTP 404", 404)
        return FetchedPage(url, url, 200, "text/html", self.pages[url].encode())


def homepage(name, domain):
    body = f"{name} builds robots for warehouses and the people who work in them every day. " * 6
    return (f"<html><head><title>{name} | Robots</title><meta property='og:site_name' content='{name}'></head><body><main>"
            f"<h1>{name}</h1><p>{body}</p><nav><a href='https://{domain}/about'>About us</a></nav></main></body></html>")


def pins(app):
    row = app.state.db.one("SELECT value FROM settings WHERE key='extension_company_sites'")
    return json.loads(row["value"]) if row else {}


async def settle(app, snapshot):
    return await app.state.extension_research.wait(snapshot["run_id"], 10)


async def test_the_research_correction_routes_take_the_workers_bodies_and_answer_with_a_snapshot(real):
    real.app.state.extension_research._fetcher = SiteWeb({"https://acme-robotics.example/": homepage("Acme Robotics",
                                                                                                   "acme-robotics.example")})
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    site = await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": "https://www.acme-robotics.example/jobs?x=1"
                                                                                .replace("www.", "")})
    company = await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Acme Robotics Ltd"})
    refresh_json = await real.client.post(f"/v1/sessions/{sid}/research/refresh", json={})
    refresh_empty = await real.client.post(f"/v1/sessions/{sid}/research/refresh")
    runs = []
    for response in (site, company, refresh_json, refresh_empty):
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert set(snapshot) == SNAPSHOT_KEYS and snapshot["state"] in {"queued", "running", "done", "partial", "failed",
                                                                          "needs_input", "cancelled"}
        assert isinstance(snapshot["version"], int) and isinstance(snapshot["stages"], list) and isinstance(snapshot["notes"], list)
        assert [s["id"] for s in snapshot["stages"]] == ["identify", "role", "site", "pages"]
        runs.append(snapshot["run_id"])
    assert len(set(runs)) == 4  # each correction is a new run
    # the bodies are exactly {domain} / {company} / {}: another key is dropped, a missing one is refused
    assert (await real.client.post(f"/v1/sessions/{sid}/research/site", json={"company": "x"})).status_code == 422
    assert (await real.client.post(f"/v1/sessions/{sid}/research/company", json={"domain": "x.example"})).status_code == 422
    extra = await real.client.post(f"/v1/sessions/{sid}/research/refresh", json={"company": "ignored", "domain": "ignored"})
    assert extra.status_code == 200 and extra.json()["company"]["name"] == "Acme Robotics"
    assert (await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": ""})).status_code == 422


async def test_a_pinned_website_is_remembered_only_after_it_was_read(real):
    web = SiteWeb({"https://acme-robotics.example/": homepage("Acme Robotics", "acme-robotics.example")})
    real.app.state.extension_research._fetcher = web
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    # a website nobody can read is pinned for this page but never saved for the company
    first = await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": "acme-robotics.typo"})
    assert first.status_code == 200
    unreadable = await settle(real.app, first.json())
    assert unreadable["site"]["origin"] == "user_pin" and pins(real.app) == {}
    # an address that is not a company website at all is refused before anything runs
    for bad in ("https://www.linkedin.com/company/acme", "boards.greenhouse.io", "10.0.0.5", "acme"):
        refused = await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": bad})
        assert refused.status_code == 422 and pins(real.app) == {}
    # the real one is read first, then remembered under the company
    second = await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": "acme-robotics.example"})
    done = await settle(real.app, second.json())
    assert done["site"]["domain"] == "acme-robotics.example" and done["site"]["verified"] is True
    assert "https://acme-robotics.example/" in web.calls
    saved = pins(real.app)
    assert list(saved) == ["acmerobotics"] and saved["acmerobotics"]["domain"] == "https://acme-robotics.example/"
    later = await real.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/4099"))
    run = await settle(real.app, later.json()["research"])
    assert run["site"]["origin"] == "user_pin"  # the next posting of the company needs no telling


async def test_the_users_corrections_survive_refreshes_reloads_and_a_restart(real):
    real.app.state.extension_research._fetcher = SiteWeb({
        "https://beta-labs.example/": homepage("Beta Labs", "beta-labs.example")})
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    sid = created["session_id"]
    fixed = (await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Beta Labs"})).json()
    assert fixed["company"]["name"] == "Beta Labs" and fixed["company"]["name_source"] == "user"
    pinned = (await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": "beta-labs.example"})).json()
    await settle(real.app, pinned)

    async def now():
        session = real.app.state.extension_sessions.sessions[sid]
        snapshot = await settle(real.app, real.app.state.extension_research.snapshot(session.research_run_id) | {"run_id": session.research_run_id})
        return snapshot["company"]["name"], snapshot["company"]["name_source"], (snapshot["site"] or {}).get("origin")

    assert await now() == ("Beta Labs", "user", "user_pin")
    again = (await real.client.post(f"/v1/sessions/{sid}/research/refresh")).json()  # a re-run
    await settle(real.app, again)
    assert await now() == ("Beta Labs", "user", "user_pin")
    reload = (await real.client.post("/v1/sessions", json=posting())).json()  # the page was reloaded
    assert reload["session_id"] == sid and reload["research"]["company"]["name"] == "Beta Labs"
    await settle(real.app, reload["research"])
    assert await now() == ("Beta Labs", "user", "user_pin")
    registry = real.app.state.extension_sessions
    registry.sessions.clear()  # Meridian restarted
    registry.by_page.clear()
    reopened = (await real.client.post("/v1/sessions", json=posting())).json()
    assert reopened["session_id"] == sid and reopened["research"]["company"]["name"] == "Beta Labs"
    assert reopened["job"]["company"] == "Beta Labs"
    stored = json.loads(real.app.state.db.one("SELECT metadata FROM extension_sessions WHERE id=?", (sid,))["metadata"])
    assert stored["user"]["company"] == "Beta Labs" and stored["user"]["site"] == "https://beta-labs.example/"


async def test_a_new_company_forgets_the_website_given_for_the_rejected_one(real):
    real.app.state.extension_research._fetcher = SiteWeb({
        "https://acme-robotics.example/": homepage("Acme Robotics", "acme-robotics.example")})
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    await settle(real.app, (await real.client.post(f"/v1/sessions/{sid}/research/site",
                                                   json={"domain": "acme-robotics.example"})).json())
    other = (await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Gamma Works"})).json()
    done = await settle(real.app, other)
    assert done["company"]["name"] == "Gamma Works"
    assert (done["site"] or {}).get("domain") != "acme-robotics.example"
    session = real.app.state.extension_sessions.sessions[sid]
    assert session.user == {"company": "Gamma Works"} and "site" not in session.report["user"]


# --------------------------------------------------------------------------------------------- one poll for a whole browser (wave 2)
POLL_MANY = "/v1/poll"


async def open_pages(real, count, headers=None, base="https://boards.greenhouse.io/acmerobotics/jobs/"):
    ids = []
    for number in range(count):
        response = await real.client.post("/v1/sessions", json=posting(url=f"{base}{6000 + number}"), headers=headers or real.headers)
        assert response.status_code == 200, response.text
        ids.append(response.json()["session_id"])
        await real.app.state.extension_research.wait(response.json()["research"]["run_id"], 10)
    await asyncio.sleep(0.4)  # research reported its last state: from here on only the test makes events
    return ids


def rearm(real):
    """Start the abandoned-page janitor again with the (patched) tick."""
    registry = real.app.state.extension_sessions
    if registry._janitor_handle is not None:
        registry._janitor_handle.cancel()
        registry._janitor_handle = None
    registry._arm_janitor()


async def attached(real, *sids, timeout=5.0):
    """Wait until a poll is really waiting for each of these pages (no guessing with sleeps on a busy machine)."""
    end = asyncio.get_running_loop().time() + timeout
    while any(live(real, sid).poll is None for sid in sids):
        assert asyncio.get_running_loop().time() < end, "the poll never started waiting"
        await asyncio.sleep(0.01)


async def replaced(real, sid, old, timeout=5.0):
    """Wait until another poll has taken the page over from ``old``."""
    end = asyncio.get_running_loop().time() + timeout
    while live(real, sid).poll in (None, old):
        assert asyncio.get_running_loop().time() < end, "no newer poll took the page over"
        await asyncio.sleep(0.01)


def live(real, sid):
    return real.app.state.extension_sessions.sessions[sid]


async def multi(real, targets, wait=0, headers=None):
    body = {"sessions": [{"session_id": sid, "cursor": cursor} for sid, cursor in targets], "wait_ms": wait}
    return await real.client.post(POLL_MANY, json=body, headers=headers or real.headers)


async def test_one_poll_waits_for_any_of_the_listed_pages_and_groups_events_by_page(real, monkeypatch):
    a, b, c = await open_pages(real, 3)
    monkeypatch.setattr(S, "POLL_TICK", 60)  # only the event can end this 25 s wait early: it ended with the first event
    cursors = {sid: live(real, sid).cursor for sid in (a, b, c)}
    waiting = asyncio.create_task(multi(real, [(sid, cursors[sid]) for sid in (a, b, c)], wait=25000))
    await attached(real, a, b, c)
    assert not waiting.done()
    live(real, b).emit({"type": "tracked", "tracked": "preparing"})
    response = await asyncio.wait_for(waiting, 20)
    body = response.json()
    from jobagent.extension.schemas import PollManyResponse
    PollManyResponse.model_validate(body)
    assert [e["session_id"] for e in body["sessions"]] == [a, b, c]  # request order, one entry per page
    by = {e["session_id"]: e for e in body["sessions"]}
    assert [e["status"] for e in body["sessions"]] == ["ok"] * 3
    assert by[b]["events"] == [{"type": "tracked", "tracked": "preparing"}] and by[b]["cursor"] == cursors[b] + 1
    assert by[a]["events"] == [] and by[a]["cursor"] == cursors[a] and by[c]["events"] == []
    assert all(live(real, sid).poll is None for sid in (a, b, c))  # nothing stays registered after the answer


async def test_the_fast_path_answers_at_once_and_a_quiet_wait_ends_empty_after_the_wait(real, monkeypatch):
    a, b = await open_pages(real, 2)
    cursors = [(sid, live(real, sid).cursor) for sid in (a, b)]
    with monkeypatch.context() as patched:
        patched.setattr(S, "QUICK_POLL_MS", 24000)  # a check of up to 24 s counts as a quick one: any waiting would show
        quick = (await asyncio.wait_for(multi(real, cursors, wait=24000), 20)).json()
    assert [e["events"] for e in quick["sessions"]] == [[], []] and all(e["cursor"] == c for e, (_, c) in zip(quick["sessions"], cursors))
    live(real, a).emit({"type": "tracked", "tracked": "preparing"})
    ready = (await multi(real, cursors, wait=0)).json()["sessions"]
    assert ready[0]["events"] == [{"type": "tracked", "tracked": "preparing"}] and ready[1]["events"] == []
    started = asyncio.get_running_loop().time()
    quiet = (await multi(real, [(sid, live(real, sid).cursor) for sid in (a, b)], wait=400)).json()["sessions"]
    waited = asyncio.get_running_loop().time() - started
    assert waited >= 0.3 and [e["events"] for e in quiet] == [[], []]  # it did wait (the upper end is the machine's business)


@pytest.mark.parametrize("body", [
    {"sessions": [], "wait_ms": 0}, {"wait_ms": 0}, {"sessions": [{"session_id": "x", "cursor": -1}]},
    {"sessions": [{"session_id": "x"}] * 2}, {"sessions": [{"session_id": f"s{i}"} for i in range(17)]},
    {"sessions": [{"session_id": "x"}], "wait_ms": 25001}, {"sessions": [{"session_id": "x"}], "wait_ms": -1},
    {"sessions": [{"cursor": 1}]}, {"sessions": "nope"}, {"sessions": [{"session_id": "y" * 81}]}],
    ids=["no page", "no list", "negative cursor", "a page twice", "17 pages", "26 s", "negative wait", "no id", "not a list",
         "oversized id"])
async def test_the_multiplexed_poll_refuses_malformed_requests_calmly(real, body):
    response = await real.client.post(POLL_MANY, json=body)
    assert response.status_code == 422 and response.json()["code"] == "invalid"


async def test_sixteen_pages_can_share_one_poll_and_stay_independent(real):
    ids = await open_pages(real, 16)
    cursors = [(sid, live(real, sid).cursor) for sid in ids]
    live(real, ids[15]).emit({"type": "tracked", "tracked": "applied"})
    body = (await multi(real, cursors, wait=0)).json()["sessions"]
    assert [e["session_id"] for e in body] == ids and [bool(e["events"]) for e in body] == [False] * 15 + [True]


async def test_a_browser_can_only_poll_its_own_pages_and_a_stranger_cannot_disturb_them(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    _, stranger = pair(real.app, origin="safari-web-extension://abcdef12-0000", browser="safari")
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor), (b, live(real, b).cursor)], wait=25000))
    await attached(real, a, b)
    own = live(real, a).poll
    assert own is not None
    live(real, a).emit({"type": "tracked", "tracked": "preparing"})  # something the stranger must never learn
    foreign = (await multi(real, [(a, 0), ("ses_never_existed", 0)], wait=0, headers=stranger)).json()["sessions"]
    assert foreign[0] == {"session_id": a, "status": "unknown", "cursor": 0, "events": []}
    assert foreign[1] == {**foreign[0], "session_id": "ses_never_existed"}  # a stranger's page looks like no page at all
    # the stranger's poll must not have taken the page over from its owner: the owner's wait ended on ITS event only
    answer = (await asyncio.wait_for(waiting, 20)).json()["sessions"]
    assert answer[0]["status"] == "ok" and answer[0]["events"] == [{"type": "tracked", "tracked": "preparing"}]
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor)], wait=25000))
    await attached(real, a)
    slot = live(real, a).poll
    await multi(real, [(a, 0)], wait=500, headers=stranger)  # a stranger waiting for a page that is not theirs
    assert live(real, a).poll is slot and not waiting.done()
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    assert (await asyncio.wait_for(waiting, 20)).json()["sessions"][0]["status"] == "ok"
    # the stranger's own page of the same URL is a different session and is theirs alone
    mine = (await real.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/6000"),
                                   headers=stranger)).json()["session_id"]
    assert mine != a
    assert (await multi(real, [(mine, 0)], headers=stranger)).json()["sessions"][0]["status"] == "ok"
    assert (await multi(real, [(mine, 0)], headers=real.headers)).json()["sessions"][0]["status"] == "unknown"


async def test_every_page_listed_is_authorised_before_anything_is_read_or_cancelled(real):
    a, = await open_pages(real, 1)
    _, stranger = pair(real.app, origin="safari-web-extension://abcdef12-0000", browser="safari")
    mine, = [(await real.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/6100"),
                                    headers=stranger)).json()["session_id"]]
    live(real, a).emit({"type": "knowledge", "knowledge_version": "secret-version"})
    mixed = (await multi(real, [(mine, 0), (a, 0)], wait=0, headers=stranger)).json()["sessions"]
    assert mixed[0]["status"] == "ok" and mixed[1]["status"] == "unknown" and "secret-version" not in str(mixed)


async def test_a_newer_poll_takes_a_page_over_and_an_older_one_keeps_the_pages_it_still_has(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    ca, cb = live(real, a).cursor, live(real, b).cursor
    first = asyncio.create_task(multi(real, [(a, ca), (b, cb)], wait=25000))
    await attached(real, a, b)
    first_slot = live(real, a).poll
    second = asyncio.create_task(multi(real, [(a, ca)], wait=25000))  # takes only page A
    await replaced(real, a, first_slot)
    await asyncio.sleep(0.05)
    assert not first.done() and not second.done()  # the first still waits for B
    live(real, b).emit({"type": "tracked", "tracked": "preparing"})
    answer = (await asyncio.wait_for(first, 20)).json()["sessions"]
    assert [(e["session_id"], e["status"]) for e in answer] == [(a, "superseded"), (b, "ok")]
    assert answer[0]["events"] == [] and answer[0]["cursor"] == ca  # cursor untouched: the newer poll owns the page now
    assert not second.done()
    second_slot = live(real, a).poll
    third = asyncio.create_task(multi(real, [(a, ca)], wait=25000))  # the same page again: the second is cancelled
    await replaced(real, a, second_slot)
    assert [(e["status"], e["events"]) for e in (await asyncio.wait_for(second, 20)).json()["sessions"]] == [("superseded", [])]
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    assert (await asyncio.wait_for(third, 20)).json()["sessions"][0]["events"] == [{"type": "tracked", "tracked": "applied"}]
    assert live(real, a).poll is None and live(real, b).poll is None


async def test_a_whole_browser_poll_replaced_by_a_new_one_ends_at_once(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    targets = [(a, live(real, a).cursor), (b, live(real, b).cursor)]
    old = asyncio.create_task(multi(real, targets, wait=25000))
    await attached(real, a, b)
    new = asyncio.create_task(multi(real, targets, wait=25000))  # a tab opened: the worker re-issues its one poll
    entries = (await asyncio.wait_for(old, 20)).json()["sessions"]
    assert [e["status"] for e in entries] == ["superseded", "superseded"] and not new.done()
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    assert (await asyncio.wait_for(new, 20)).json()["sessions"][0]["status"] == "ok"


async def test_releasing_a_page_ends_the_poll_waiting_for_it_and_later_polls_do_not_wait(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)  # released, not timed out: only the release can end these 25 s waits early
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor), (b, live(real, b).cursor)], wait=25000))
    await attached(real, a, b)
    first_b = live(real, b).poll
    single = asyncio.create_task(real.client.post(f"/v1/sessions/{b}/poll", json={"cursor": live(real, b).cursor, "wait_ms": 25000}))
    await replaced(real, b, first_b)
    assert (await real.client.delete(f"/v1/sessions/{a}")).status_code == 200
    answer = (await asyncio.wait_for(waiting, 20)).json()["sessions"]
    assert not single.done()  # page B has its own, newer poll: releasing A does not touch it
    live(real, b).emit({"type": "tracked", "tracked": "preparing"})
    got = await asyncio.wait_for(single, 20)
    assert got.status_code == 200 and got.json()["events"] == [{"type": "tracked", "tracked": "preparing"}]
    assert [e["status"] for e in answer] == ["released", "superseded"]  # B was taken over by the single-page poll
    again = (await asyncio.wait_for(multi(real, [(a, 0)], wait=25000), 20)).json()["sessions"][0]  # nothing to wait for
    assert again["status"] == "released"
    single_after = await asyncio.wait_for(real.client.post(f"/v1/sessions/{a}/poll", json={"cursor": 0, "wait_ms": 25000}), 20)
    assert single_after.status_code == 200 and single_after.json()["events"] == []


async def test_a_reloaded_page_is_polled_again_after_its_release(real):
    a, = await open_pages(real, 1)
    await real.client.delete(f"/v1/sessions/{a}")
    assert (await multi(real, [(a, 0)])).json()["sessions"][0]["status"] == "released"
    again = (await real.client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/6000"))).json()
    assert again["session_id"] == a  # the same page re-attaches: the release is cancelled
    assert (await multi(real, [(a, 0)])).json()["sessions"][0]["status"] == "ok"


async def test_a_page_dropped_while_polled_says_expired_and_the_others_carry_on(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor), (b, live(real, b).cursor)], wait=25000))
    await attached(real, a, b)
    registry = real.app.state.extension_sessions
    registry._drop(live(real, a))
    await asyncio.sleep(0.1)
    assert not waiting.done()  # B is still being waited for
    live(real, b).emit({"type": "tracked", "tracked": "applied"})
    answer = (await asyncio.wait_for(waiting, 20)).json()["sessions"]
    assert [e["status"] for e in answer] == ["expired", "ok"]
    assert (await multi(real, [(a, 0)])).json()["sessions"][0]["status"] == "expired"  # its row is still this browser's


async def test_a_browser_that_hangs_up_frees_its_pages(real, monkeypatch):
    monkeypatch.setattr(S, "POLL_TICK", 0.05)
    a, = await open_pages(real, 1)
    registry = real.app.state.extension_sessions
    from jobagent.extension.auth import Client
    client = Client(id=next(iter(registry.sessions.values())).client_id, name="x", browser="chrome", origin=ORIGIN, version="1")
    gone = {"yes": False}

    async def disconnected():
        return gone["yes"]

    waiting = asyncio.create_task(registry.poll_many(client, [(a, live(real, a).cursor)], 25000, disconnected=disconnected))
    await attached(real, a)
    assert not waiting.done()
    gone["yes"] = True
    await asyncio.wait_for(waiting, 20)  # noticed within a tick (50 ms), not when the 25 s wait ends
    assert live(real, a).poll is None
    cancelled = asyncio.create_task(registry.poll_many(client, [(a, live(real, a).cursor)], 25000))
    await attached(real, a)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert live(real, a).poll is None  # a cancelled request leaves nothing behind


async def test_polling_is_cheap_the_profile_fingerprint_is_read_once_for_every_waiting_page(real, monkeypatch):
    monkeypatch.setattr(S, "POLL_TICK", 0.05)
    calls = []
    original = S.data_token
    monkeypatch.setattr(S, "data_token", lambda db: calls.append(1) or original(db))
    ids = await open_pages(real, 8)
    calls.clear()
    started = asyncio.get_running_loop().time()
    answer = (await multi(real, [(sid, live(real, sid).cursor) for sid in ids], wait=1000)).json()["sessions"]
    elapsed = asyncio.get_running_loop().time() - started
    assert all(e["events"] == [] for e in answer)
    assert len(calls) <= int(elapsed / S.TOKEN_TTL) + 2, (len(calls), elapsed)  # one read per TTL, not one per page per tick


async def test_the_single_page_poll_is_the_same_poll_with_one_page(real, monkeypatch):
    a, = await open_pages(real, 1)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    cursor = live(real, a).cursor
    waiting = asyncio.create_task(real.client.post(f"/v1/sessions/{a}/poll", json={"cursor": cursor, "wait_ms": 25000}))
    await attached(real, a)
    single_slot = live(real, a).poll
    newer = asyncio.create_task(multi(real, [(a, cursor)], wait=25000))  # the multiplexed poll takes the page over
    await replaced(real, a, single_slot)
    older = await asyncio.wait_for(waiting, 20)
    assert older.json() == {"cursor": cursor, "events": []}
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    assert (await asyncio.wait_for(newer, 20)).json()["sessions"][0]["events"] == [{"type": "tracked", "tracked": "applied"}]


# --------------------------------------------------------------------------------------------- robustness (wave 2)
class IdleClock:
    """What the sessions take for 'now' (their idle clock, ``time.monotonic``): the test decides when ten minutes pass."""

    def __init__(self):
        self.now = 1_000_000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def idle_clock(monkeypatch):
    clock = IdleClock()
    monkeypatch.setattr(S, "time", SimpleNamespace(monotonic=clock.monotonic))
    return clock


SESSION_ROUTES = [
    ("POST", "/suggest", lambda: suggest_body([el(1, "textbox", "First name")])),
    ("POST", "/poll", lambda: {"cursor": 0, "wait_ms": 0}),
    ("POST", "/regenerate", lambda: {"question_id": "q1"}),
    ("POST", "/feedback", lambda: {"question_id": "q1", "outcome": "accepted"}),
    ("POST", "/track", lambda: {"event": "submit_clicked"}),
    ("POST", "/research/company", lambda: {"company": "Acme"}),
    ("POST", "/research/site", lambda: {"domain": "acme.example"}),
    ("POST", "/research/refresh", lambda: {}),
    ("DELETE", "", lambda: None),
]


@pytest.mark.parametrize("method,path,body", SESSION_ROUTES, ids=[f"{m} {p or '(release)'}" for m, p, _ in SESSION_ROUTES])
async def test_after_a_restart_every_session_route_says_expired_and_never_anything_else(real, method, path, body):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    registry = real.app.state.extension_sessions
    registry.sessions.clear()  # a restarted sidecar: the page row survives, the in-memory page does not
    registry.by_page.clear()
    gone = await real.client.request(method, f"/v1/sessions/{sid}{path}", json=body())
    assert gone.status_code == 410 and gone.json()["code"] == "expired", (gone.status_code, gone.text)
    unknown = await real.client.request(method, f"/v1/sessions/ses_never_existed{path}", json=body())
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"
    _, stranger = pair(real.app, origin="safari-web-extension://abcdef12-0000", browser="safari")
    foreign = await real.client.request(method, f"/v1/sessions/{sid}{path}", json=body(), headers=stranger)
    assert foreign.status_code == 404 and foreign.json()["code"] == "not_found"  # not even "expired": it is not theirs
    reopened = (await real.client.post("/v1/sessions", json=posting())).json()
    assert reopened["session_id"] == sid  # the client recreates the session and carries on with the same id


async def test_the_events_of_a_page_stay_bounded_whatever_happens(real):
    sid = (await real.client.post("/v1/sessions", json=posting()))
    sid = sid.json()["session_id"]
    session = live(real, sid)
    for number in range(3 * S.MAX_EVENTS):
        session.emit({"type": "tracked", "tracked": "none", "n": number})
    assert len(session.events) == S.MAX_EVENTS and session.lost == 2 * S.MAX_EVENTS
    big = {"run_id": "r", "state": "running", "role": {"sections": {"about": "x" * 20000}}}
    for number in range(500):
        session.emit({"type": "research", "research": {**big, "version": number}})
    assert len(session.events) <= S.MAX_EVENTS
    assert [e["research"]["version"] for _, e in session.events if e["type"] == "research"] == [499]  # only the newest
    behind = (await multi(real, [(sid, 0)], wait=0)).json()["sessions"][0]
    assert behind["events"][0]["type"] == "knowledge"  # a client that fell behind is told to re-scan, not fed history
    caught_up = (await multi(real, [(sid, behind["cursor"])], wait=0)).json()["sessions"][0]
    assert caught_up["events"] == [] and caught_up["cursor"] == session.cursor
    # a client that read everything the page dropped does not need a re-scan
    up_to_date = (await multi(real, [(sid, session.lost)], wait=0)).json()["sessions"][0]
    assert all(e["type"] != "knowledge" for e in up_to_date["events"])


async def test_requests_for_one_frame_are_served_one_at_a_time_and_different_frames_in_parallel(real, monkeypatch):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    service = real.app.state.extension_sessions.service
    original = service.suggest
    running, peak, per_frame = {}, {"all": 0}, {}

    async def watched(session, request):
        key = request.frame_key
        running[key] = running.get(key, 0) + 1
        per_frame[key] = max(per_frame.get(key, 0), running[key])
        peak["all"] = max(peak["all"], sum(running.values()))
        try:
            await asyncio.sleep(0.05)
            return await original(session, request)
        finally:
            running[key] -= 1

    monkeypatch.setattr(service, "suggest", watched)
    labels = ["First name", "Last name", "Phone"]
    jobs = [real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body(
        [el(1, "textbox", labels[n]), el(3, "textbox", "Email", type="email")], frame_key=key, document_id=f"d-{key}-{n}"))
        for n in range(3) for key in ("top", "f12")]
    answers = await asyncio.gather(*jobs)
    assert [a.status_code for a in answers] == [200] * 6
    assert per_frame == {"top": 1, "f12": 1} and peak["all"] == 2  # one frame never overlaps itself; two frames do
    session = live(real, sid)
    for key in ("top", "f12"):
        document = session.frames[key].document_id
        wanted = {labels[int(document[-1])], "Email"}  # whichever request was served last, the frame is ITS page, whole
        assert {e["label"] for e in session.frames[key].elements} == wanted
        assert {f.question for f in session.fields.values() if f.frame_key == key} == wanted
    assert sorted(f.key for f in session.fields.values()) == ["f12:1", "f12:3", "top:1", "top:3"]


async def test_overlapping_scans_of_one_page_never_mix_two_documents(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    old = suggest_body([el(1, "textbox", "First name"), el(3, "textbox", "Email", type="email")], document_id="doc-old")
    new = suggest_body([el(1, "textbox", "Last name"), el(3, "textbox", "Phone", type="tel"),
                        el(5, "textbox", "LinkedIn Profile")], document_id="doc-new")
    first, second = await asyncio.gather(real.client.post(f"/v1/sessions/{sid}/suggest", json=old),
                                         real.client.post(f"/v1/sessions/{sid}/suggest", json=new))
    assert first.status_code == second.status_code == 200
    session = live(real, sid)
    documents = {"doc-old": {"First name", "Email"}, "doc-new": {"Last name", "Phone", "LinkedIn Profile"}}
    last = session.frames["top"].document_id  # whichever request was served last wins, WHOLE
    assert last in documents and {e["label"] for e in session.frames["top"].elements} == documents[last]
    assert {f.question for f in session.fields.values()} == documents[last]  # nothing of the other document is left


async def test_work_of_an_abandoned_page_is_released_even_when_its_browser_never_says_goodbye(real, idle_clock):
    model = ScriptedModel(delay=0.4).install(real.app)
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    registry, generation = real.app.state.extension_sessions, real.app.state.extension_generation
    narratives = [el(1 + 2 * n, "textbox", question, tag="textarea", type="textarea") for n, question in enumerate(
        ["Why do you want to work at Acme Robotics?", "Describe a project you are proud of and what you learned.",
         "Tell us about a time you led a team.", "What motivates you to apply for this role?"])]
    await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body(narratives))
    assert generation.pending(sid) >= 2 and sid in generation._stop_listeners
    idle_clock.advance(S.ABANDON_SECONDS - 1)  # quiet, but not for ten minutes yet
    registry._janitor()
    assert sid in registry.sessions and generation.pending(sid) >= 2
    idle_clock.advance(2)  # the browser vanished: no poll, no request, for ten minutes
    registry._janitor()
    assert sid not in registry.sessions and generation.pending(sid) <= 1  # queued drafts cancelled; one may still be running
    assert sid not in generation._stop_listeners and not registry.research._by_session.get(sid)
    for _ in range(6000):  # the running draft is allowed to finish; then the lane itself is gone
        if generation.pending(sid) == 0:
            break
        await asyncio.sleep(0.01)
    assert generation.pending(sid) == 0 and sid not in generation._lanes
    assert len(model.drafts) <= 1  # nothing queued ever started
    gone = await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body(narratives))
    assert gone.status_code == 410 and gone.json()["code"] == "expired"


async def test_the_janitor_runs_on_its_own_timer(real, idle_clock, monkeypatch):
    monkeypatch.setattr(S, "JANITOR_TICK", 0.01)
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    registry = real.app.state.extension_sessions
    rearm(real)
    idle_clock.advance(S.ABANDON_SECONDS + 1)
    for _ in range(6000):
        if sid not in registry.sessions:
            break
        await asyncio.sleep(0.01)
    assert sid not in registry.sessions and registry._janitor_handle is None  # nothing left to watch: the timer rests


async def test_a_page_that_keeps_polling_is_never_taken_for_abandoned(real, idle_clock, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 0.02)
    registry = real.app.state.extension_sessions
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor)], wait=25000))  # only A is being polled
    await attached(real, a)
    for _ in range(2):  # twenty minutes in all: B is silent throughout, A is polled throughout
        idle_clock.advance(S.ABANDON_SECONDS * 0.7)
        for _ in range(6000):  # the poll ticks and touches A (it has the new 'now') before the janitor looks
            if live(real, a).touched == idle_clock.now:
                break
            await asyncio.sleep(0.01)
        registry._janitor()
    assert a in registry.sessions and b not in registry.sessions  # B was silent for longer than the limit
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    assert (await asyncio.wait_for(waiting, 20)).json()["sessions"][0]["status"] == "ok"


async def test_a_request_that_was_waiting_when_its_page_was_dropped_starts_nothing(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    registry = real.app.state.extension_sessions
    session = live(real, sid)
    started, gate = [], asyncio.Event()
    original = registry.service.suggest

    async def slow(session_, request):
        started.append(request.document_id)
        await gate.wait()
        return await original(session_, request)

    registry.service.suggest = slow
    try:
        body = suggest_body([el(1, "textbox", "First name")])
        first = asyncio.create_task(real.client.post(f"/v1/sessions/{sid}/suggest", json={**body, "document_id": "one"}))
        for _ in range(6000):  # the first request is inside the service, holding the frame
            if started == ["one"]:
                break
            await asyncio.sleep(0.01)
        second = asyncio.create_task(real.client.post(f"/v1/sessions/{sid}/suggest", json={**body, "document_id": "two"}))
        for _ in range(6000):  # and the second waits behind it for the frame's lock
            if session.frame_lock("top")._waiters:
                break
            await asyncio.sleep(0.01)
        assert session.frame_lock("top")._waiters and started == ["one"]
        registry._drop(session)  # the page goes away while the second request waits behind the first
        gate.set()
        answers = await asyncio.gather(first, second)
    finally:
        registry.service.suggest = original
    assert started == ["one"] and answers[1].status_code == 410 and answers[1].json()["code"] == "expired"


async def test_a_draft_is_never_queued_for_a_page_dropped_while_its_request_was_running(real):
    ScriptedModel().install(real.app)
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    registry, generation = real.app.state.extension_sessions, real.app.state.extension_generation
    session = live(real, sid)
    original = registry.service._one

    async def dropped_midway(*args, **kwargs):
        registry._drop(session)  # the browser went away in the middle of the request
        return await original(*args, **kwargs)

    registry.service._one = dropped_midway
    try:
        await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea")]))
    finally:
        registry.service._one = original
    assert generation.pending(sid) == 0 and sid not in generation._lanes and not generation._jobs


async def test_a_poll_for_a_page_that_is_dropped_returns_at_once_not_at_the_next_tick(real, monkeypatch):
    a, = await open_pages(real, 1)
    monkeypatch.setattr(S, "POLL_TICK", 60)  # no tick can notice the drop: only the drop's own wake can end the 25 s wait
    waiting = asyncio.create_task(multi(real, [(a, live(real, a).cursor)], wait=25000))
    await attached(real, a)
    real.app.state.extension_sessions._drop(live(real, a))
    answer = (await asyncio.wait_for(waiting, 20)).json()["sessions"][0]
    assert answer["status"] == "expired"


def test_the_typescript_contract_mirrors_the_models_added_for_polling_and_places():
    """schemas.py and extension/src/shared/protocol.ts describe the same wire: every field of what wave 2 added is named
    in its TypeScript interface (the shared contract files are edited by several owners, additions must be mirrored)."""
    import re
    from jobagent.extension.schemas import ElementIn, PageReport, PollManyEntry, PollManyRequest, PollManyResponse, PollTarget
    source = (Path(__file__).resolve().parents[2] / "extension" / "src" / "shared" / "protocol.ts").read_text()

    def block(name):
        match = re.search(rf"export (?:interface|type) {name}\b(.*?)(?=\nexport |\Z)", source, re.S)
        assert match, f"{name} is missing from protocol.ts"
        return match.group(1)

    for model in (PollTarget, PollManyRequest, PollManyEntry, PollManyResponse):
        for field in model.model_fields:
            assert re.search(rf"\b{field}\b", block(model.__name__)), (model.__name__, field)
    assert re.search(r"\bposition\b", block("ElementIn")) and re.search(r"\blocation_hint\b", block("PageReport"))
    assert set(PollManyEntry.model_fields["status"].annotation.__args__) == set(re.findall(r'"(\w+)"', block("PollStatus")))
    assert re.search(r"\bcountry\b", block("ResearchSnapshot")) and re.search(r"\bremote\b", block("ResearchSnapshot"))
    assert "position" in ElementIn.model_fields and "location_hint" in PageReport.model_fields


async def test_the_guard_gives_the_multiplexed_poll_the_long_poll_deadline(real, monkeypatch):
    """BridgeGuard cuts ordinary requests at 30 s and polls at 45 s; POST /v1/poll must be treated as a poll."""
    from jobagent.extension import app as bridge_module
    a, = await open_pages(real, 1)  # an ordinary request (opening the page) is not what is being cut short here
    monkeypatch.setattr(bridge_module, "DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(bridge_module, "POLL_DEADLINE_SECONDS", 60)
    quiet = await multi(real, [(a, live(real, a).cursor)], wait=900)
    assert quiet.status_code == 200 and quiet.json()["sessions"][0]["events"] == []


async def test_the_page_may_say_where_the_job_is_and_the_session_knows_at_once(real):
    page = posting(jsonld=[], location_hint="Lisbon, Portugal", description_text="Build things for customers every day. " * 20,
                   title="Software Engineer - Acme Robotics")
    created = (await real.client.post("/v1/sessions", json=page)).json()
    assert created["job"]["location"] == "Lisbon, Portugal"
    session = live(real, created["session_id"])
    assert (session.location, session.country, session.countries) == ("Lisbon, Portugal", "Portugal", ["Portugal"])
    done = await real.app.state.extension_research.wait(created["research"]["run_id"], 10)
    assert (done["role"]["location"], done["role"]["country"]) == ("Lisbon, Portugal", "Portugal")
    assert "location_hint" not in str(real.app.state.db.query("SELECT metadata FROM extension_sessions"))  # page text is not stored


async def test_a_quick_check_never_takes_a_page_over_from_the_long_poll_waiting_for_it(real, monkeypatch):
    a, b = await open_pages(real, 2)
    monkeypatch.setattr(S, "POLL_TICK", 60)
    long_poll = asyncio.create_task(multi(real, [(a, live(real, a).cursor), (b, live(real, b).cursor)], wait=25000))
    await attached(real, a, b)
    slot = live(real, a).poll
    for wait in (0, 50, 250):
        quick = (await multi(real, [(a, live(real, a).cursor)], wait=wait)).json()["sessions"][0]
        assert quick["status"] == "ok" and quick["events"] == []
        single = await real.client.post(f"/v1/sessions/{a}/poll", json={"cursor": live(real, a).cursor, "wait_ms": wait})
        assert single.status_code == 200 and single.json()["events"] == []
        assert live(real, a).poll is slot and not long_poll.done()  # the long poll still owns page A
    live(real, a).emit({"type": "tracked", "tracked": "applied"})
    answer = (await asyncio.wait_for(long_poll, 20)).json()["sessions"]
    assert [e["status"] for e in answer] == ["ok", "ok"] and answer[0]["events"] == [{"type": "tracked", "tracked": "applied"}]
    # and a quick check still SEES events that are already there
    seen = (await multi(real, [(a, 0)], wait=100)).json()["sessions"][0]
    assert {"type": "tracked", "tracked": "applied"} in seen["events"]


# --------------------------------------------------------------------------------------------- hygiene (wave 2b)
async def test_a_page_with_a_malformed_link_and_a_tab_in_its_address_still_opens_its_session(real):
    """One bad link among the page's 80 (``http://[::1``) used to answer 400 and leave the page without an assistant; a
    tab inside the address (a parser ignores it) was stored as it came."""
    page = posting(url="ht\ttps://boards.greenhouse.io/acmerobotics/jobs/4001",
                   links=[{"href": "https://acmerobotics.example/about", "text": "About"},
                          {"href": "http://[::1", "text": "broken"}, {"href": "//[", "text": ""}])
    created = await real.client.post("/v1/sessions", json=page)
    assert created.status_code == 200, created.text
    session = live(real, created.json()["session_id"])
    assert session.report["url_clean"] == "https://boards.greenhouse.io/acmerobotics/jobs/4001"
    assert [link["href"] for link in session.report["links"]] == ["https://acmerobotics.example/about"]


async def test_a_page_that_invents_frames_cannot_grow_the_locks_of_its_session(real):
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    session = live(real, sid)
    for number in range(300):  # a frame the page never scanned: the answer is 404, and nothing may pile up
        gone = await real.client.post(f"/v1/sessions/{sid}/regenerate", json={"question_id": "q", "frame_key": f"f{number}"})
        assert gone.status_code == 404
    from jobagent.extension.suggest import MAX_FRAMES
    assert len(session.frame_locks) <= 2 * MAX_FRAMES
    answered = await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([el(1, "textbox", "First name")]))
    assert answered.status_code == 200 and "top" in session.frame_locks


# --- a locked database ----------------------------------------------------------------------------------------------
@pytest.fixture
def writer_held(real):
    """Another writer (discovery, an import, the UI) holds the database's single write lock: every write here waits a
    moment and fails with 'database is locked'. Reads still work (WAL)."""
    import contextlib
    import sqlite3

    db = real.app.state.db
    original = db._connect

    @contextlib.contextmanager
    def hold():
        blocker = sqlite3.connect(str(db.path), timeout=0.1, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")

        def quick():
            connection = original()
            connection.execute("PRAGMA busy_timeout=50")
            return connection

        db._connect = quick
        try:
            yield
        finally:
            del db._connect
            blocker.execute("ROLLBACK")
            blocker.close()

    return hold


def counts(real, *names):
    return {name: real.app.state.db.one(f"SELECT COUNT(*) AS n FROM {name}")["n"] for name in names}


async def test_a_busy_database_never_breaks_browsing_only_what_must_be_saved_says_so(real, writer_held):
    """Bookkeeping (last seen, the page row, the audit lines) is best-effort: the page keeps working. Saving an answer or
    recording an application is not: it answers 503 ``unavailable`` and has written nothing, and the same request
    works once the database is free."""
    tables = ("jobs", "applications", "application_answers", "browser_answers", "extension_events")
    with writer_held():
        created = await real.client.post("/v1/sessions", json=posting())
        assert created.status_code == 200, created.text  # the first request of this browser also stamps its last-seen time
        sid = created.json()["session_id"]
        await real.app.state.extension_research.wait(created.json()["research"]["run_id"], 30)
        scanned = await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([
            el(1, "textbox", "First name"), el(3, "textbox", "Favourite colour")]))
        assert scanned.status_code == 200 and len(scanned.json()["results"]) == 2
        polled = await real.client.post(f"/v1/sessions/{sid}/poll", json={"cursor": 0, "wait_ms": 0})
        assert polled.status_code == 200
        question = {r["node_id"]: r for r in scanned.json()["results"]}[3]
        save = {"question_id": question["question_id"], "frame_key": "top", "outcome": "edited", "final_value": "Green",
                "remember": "application", "request_id": question["request_id"], "suggestion_state": question["state"]}
        refused = await real.client.post(f"/v1/sessions/{sid}/feedback", json=save)
        assert refused.status_code == 503 and refused.json()["code"] == "unavailable", refused.text
        recorded = await real.client.post(f"/v1/sessions/{sid}/track", json={"event": "record"})
        assert recorded.status_code == 503 and recorded.json()["code"] == "unavailable", recorded.text
        assert counts(real, *tables) == {name: 0 for name in tables}  # nothing half-written, not even the audit lines
        assert real.app.state.db.one("SELECT COUNT(*) AS n FROM facts WHERE category='application_answer'")["n"] == 0
    again = await real.client.post(f"/v1/sessions/{sid}/feedback", json=save)
    assert again.status_code == 200 and again.json()["saved"] is True, again.text
    assert counts(real, "applications", "application_answers", "browser_answers") == {
        "applications": 1, "application_answers": 1, "browser_answers": 1}  # saved once
    after = await real.client.post(f"/v1/sessions/{sid}/track", json={"event": "record"})
    assert after.status_code == 200 and after.json()["state"] == "recorded"


async def test_a_busy_database_during_the_last_seen_stamp_does_not_fail_the_request(real, writer_held):
    from jobagent.extension import auth
    state = real.app.state
    state.__dict__.pop("extension_touch", None)  # the first request of a browser writes its last-seen time
    with writer_held():
        assert (await real.client.get("/v1/documents")).status_code == 200
    stamps = getattr(state, "extension_touch", {})
    assert stamps == {}  # the failed write is forgotten: the next request tries again instead of waiting a minute
    assert (await real.client.get("/v1/documents")).status_code == 200
    assert list(stamps) == [next(iter(stamps))] and len(stamps) == 1 and auth.TOUCH_INTERVAL_SECONDS == 60.0


async def test_a_save_that_fails_halfway_leaves_nothing_and_can_simply_be_sent_again(real, monkeypatch):
    """The 'no strong-match notice for this one' receipt fails (a busy database) after it was written: the job, the
    receipt and the application are ONE transaction, so the answer is 503 and NOTHING of the application exists (no job
    row waiting for its receipt either); sending the same request again creates each piece exactly once."""
    from sqlite3 import OperationalError
    db = real.app.state.db
    sid = (await real.client.post("/v1/sessions", json=posting())).json()["session_id"]
    await real.app.state.extension_research.wait(live(real, sid).research_run_id, 30)
    scanned = await real.client.post(f"/v1/sessions/{sid}/suggest", json=suggest_body([el(3, "textbox", "Favourite colour")]))
    question = scanned.json()["results"][0]
    save = {"question_id": question["question_id"], "frame_key": "top", "outcome": "edited", "final_value": "Green",
            "remember": "application"}
    original = S.seed_application
    failures = []

    def flaky(conn, job_id):
        if not failures:
            original(conn, job_id)  # the receipt really was written inside the transaction ...
            failures.append(job_id)
            raise OperationalError("database is locked")  # ... and then the statement after it failed
        return original(conn, job_id)

    monkeypatch.setattr(S, "seed_application", flaky)
    refused = await real.client.post(f"/v1/sessions/{sid}/feedback", json=save)
    assert refused.status_code == 503 and failures
    assert counts(real, "jobs", "applications", "application_answers", "browser_answers", "notification_receipts") == {
        "jobs": 0, "applications": 0, "application_answers": 0, "browser_answers": 0, "notification_receipts": 0}
    assert live(real, sid).application_id is None  # the page was not told it is tracked
    from jobagent.notifications import collect_notifications
    again = await real.client.post(f"/v1/sessions/{sid}/feedback", json=save)
    assert again.status_code == 200 and again.json()["saved"] is True
    assert counts(real, "jobs", "applications", "application_answers", "browser_answers", "notification_receipts") == {
        "jobs": 1, "applications": 1, "application_answers": 1, "browser_answers": 1, "notification_receipts": 1}
    assert collect_notifications(db) == []


async def test_closing_the_services_leaves_no_task_timer_or_waiting_poll_behind(tmp_path, monkeypatch):
    """Shutdown (and an app restart in tests) with drafts queued, research running, a page waiting out its release grace
    and a browser long-polling: nothing the assistant started may outlive ``close_extension_state``."""
    app = make_app(tmp_path)
    ScriptedModel(delay=0.5).install(app)
    async with http(bridge(app)) as client:
        pages = []
        for number in range(3):
            created = (await client.post("/v1/sessions", json=posting(
                url=f"https://boards.greenhouse.io/acmerobotics/jobs/{4000 + number}"))).json()
            pages.append(created["session_id"])
            await app.state.extension_research.wait(created["research"]["run_id"], 30)  # its last research event is out
            await client.post(f"/v1/sessions/{created['session_id']}/suggest", json=suggest_body([
                el(1, "textbox", "Why do you want to work at Acme Robotics?", tag="textarea", type="textarea"),
                el(3, "textbox", "Describe a project you are proud of and what you learned.", tag="textarea", type="textarea")]))
        await client.delete(f"/v1/sessions/{pages[0]}")  # waits out its release grace
        registry, research = app.state.extension_sessions, app.state.extension_research

        async def never(url, **kwargs):
            await asyncio.sleep(60)

        research._fetcher = never  # one more page whose research is still running when the app closes
        busy = (await client.post("/v1/sessions", json=posting(url="https://boards.greenhouse.io/acmerobotics/jobs/4999",
                                                               jsonld=[], user={"company": "Acme Robotics"})))
        assert any(t.get_name() == "research:" + busy.json()["research"]["run_id"] for t in asyncio.all_tasks())
        monkeypatch.setattr(S, "POLL_TICK", 60)  # only the shutdown itself can end the poll early
        waiting = asyncio.create_task(client.post("/v1/poll", json={
            "sessions": [{"session_id": sid, "cursor": registry.sessions[sid].cursor} for sid in pages[1:]], "wait_ms": 25000}))
        for _ in range(6000):  # the poll is really waiting, the drafts really running
            if all(registry.sessions[sid].poll is not None for sid in pages[1:]) and app.state.extension_generation.active_total():
                break
            await asyncio.sleep(0.01)
        assert all(registry.sessions[sid].poll is not None for sid in pages[1:]) and not waiting.done()
        handles = [s.release_handle for s in registry.sessions.values() if s.release_handle] + [registry._janitor_handle]
        handles += [h for run in research._runs.values() for h in (run.cancel_handle, run.notify_handle, run.hard_handle) if h]
        assert len(handles) >= 2 and all(handles)
        await close_extension_state(app)
        assert all(handle.cancelled() for handle in handles)  # every timer the services armed
        mine = [t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
                and t.get_name().startswith(("draft:", "research:"))]
        assert mine == []
        answer = await asyncio.wait_for(waiting, 20)  # the long poll ended with the shutdown, it was not left hanging
        assert answer.status_code == 200 and [e["events"] for e in answer.json()["sessions"]] == [[], []]
        assert app.state.text_ai.answer_llm.on_progress is None and registry.sessions == {} and registry.by_page == {}
        with pytest.raises(RuntimeError):
            research.start("late", posting()["page"])  # nothing new can be started once closed


async def test_a_cv_is_read_hashed_and_encoded_in_a_worker_thread_not_in_the_event_loop(real, monkeypatch):
    """Up to 10 MiB is read, hashed and base64-encoded for one attach: that must not hold the loop the main API shares."""
    import threading
    add_cv(real.app, filename="cv.pdf")
    registry = real.app.state.extension_sessions
    threads = []

    def watch(name):
        original = getattr(registry, name)

        def wrapper(*args, **kwargs):
            threads.append((name, threading.current_thread() is not threading.main_thread()))
            return original(*args, **kwargs)

        monkeypatch.setattr(registry, name, wrapper)

    watch("documents")
    watch("document")
    listed = await real.client.get("/v1/documents")
    version = listed.json()[0]["id"]
    content = await real.client.get(f"/v1/documents/{version}/content")
    assert content.status_code == 200 and content.json()["filename"] == "cv.pdf"
    assert threads == [("documents", True), ("document", True)]


async def test_every_other_route_also_survives_a_busy_database_without_a_server_error(real, writer_held):
    created = (await real.client.post("/v1/sessions", json=posting())).json()
    sid = created["session_id"]
    await real.app.state.extension_research.wait(created["research"]["run_id"], 30)
    add_cv(real.app, filename="cv.pdf")
    with writer_held():
        answers = {
            "company": await real.client.post(f"/v1/sessions/{sid}/research/company", json={"company": "Acme Robotics Ltd"}),
            "site": await real.client.post(f"/v1/sessions/{sid}/research/site", json={"domain": "acmerobotics.example"}),
            "refresh": await real.client.post(f"/v1/sessions/{sid}/research/refresh"),
            "regenerate": await real.client.post(f"/v1/sessions/{sid}/regenerate", json={"question_id": "nothing"}),
            "noted": await real.client.post(f"/v1/sessions/{sid}/track", json={"event": "submit_clicked"}),
            "documents": await real.client.get("/v1/documents"),
            "poll": await real.client.post(f"/v1/sessions/{sid}/poll", json={"cursor": 0, "wait_ms": 0}),
            "many": await real.client.post("/v1/poll", json={"sessions": [{"session_id": sid, "cursor": 0}], "wait_ms": 0}),
            "release": await real.client.delete(f"/v1/sessions/{sid}"),
        }
    codes = {name: response.status_code for name, response in answers.items()}
    assert codes == {"company": 200, "site": 200, "refresh": 200, "regenerate": 404, "noted": 200, "documents": 200,
                     "poll": 200, "many": 200, "release": 200}, codes
    assert counts(real, "jobs", "applications", "application_answers") == {"jobs": 0, "applications": 0, "application_answers": 0}


# --------------------------------------------------------------------------------------------- session caps (wave 2c)
SAFARI_ORIGIN = "safari-web-extension://abcdef12-0000"


def job_page(number, **overrides):
    return posting(url=f"https://boards.greenhouse.io/acmerobotics/jobs/{number}", **overrides)


async def open_job_pages(real, numbers, headers=None):
    """Open one page per number as the browser with ``headers`` (default: the fixture's) and return the session ids."""
    ids = []
    for number in numbers:
        response = await real.client.post("/v1/sessions", json=job_page(number), headers=headers or real.headers)
        assert response.status_code == 200, response.text
        ids.append(response.json()["session_id"])
    return ids


def held(real, ids):
    return [sid for sid in ids if sid in real.app.state.extension_sessions.sessions]


async def test_one_browser_opening_many_pages_never_evicts_another_browsers_pages(real, monkeypatch):
    """MAX_SESSIONS was one number for everybody: a loop that opened pages in one browser pushed the least recently
    used ones out, and those were the OTHER browser's (every call after that answered 410 and it had to start again)."""
    monkeypatch.setattr(S, "MAX_SESSIONS_PER_CLIENT", 5, raising=False)
    monkeypatch.setattr(S, "MAX_SESSIONS", 8)
    _, safari_headers = pair(real.app, origin=SAFARI_ORIGIN, browser="safari")
    safari = await open_job_pages(real, range(100, 103), safari_headers)  # the oldest pages of all: first in line to go
    chrome = await open_job_pages(real, range(200, 212))  # twelve pages in the other browser
    assert held(real, safari) == safari  # all three are still there
    assert held(real, chrome) == chrome[-5:]  # the loop's own oldest pages were the ones that left
    assert len(real.app.state.extension_sessions.sessions) == 8


async def test_a_browser_over_its_share_gives_up_its_own_least_recently_used_page(real, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS_PER_CLIENT", 4, raising=False)
    monkeypatch.setattr(S, "MAX_SESSIONS", 50)
    first, second, third, fourth = await open_job_pages(real, range(300, 304))
    polled = await real.client.post(f"/v1/sessions/{first}/poll", json={"cursor": 0, "wait_ms": 0})
    assert polled.status_code == 200  # using a page makes it the most recently used
    (fifth,) = await open_job_pages(real, [304])
    assert held(real, [first, second, third, fourth, fifth]) == [first, third, fourth, fifth]
    gone = await real.client.post(f"/v1/sessions/{second}/suggest", json=suggest_body([el(1, "textbox", "First name")]))
    assert gone.status_code == 410 and gone.json()["code"] == "expired"  # its owner is told to open it again


async def test_the_global_ceiling_is_paid_by_the_browser_holding_the_most_pages(real, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS_PER_CLIENT", 6, raising=False)
    monkeypatch.setattr(S, "MAX_SESSIONS", 9)
    _, safari_headers = pair(real.app, origin=SAFARI_ORIGIN, browser="safari")
    _, third_headers = pair(real.app, origin="chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    safari = await open_job_pages(real, range(500, 503), safari_headers)  # the OLDEST pages of all ...
    chrome = await open_job_pages(real, range(400, 406))  # ... and the bigger share: 9 pages, the ceiling
    newcomer = await open_job_pages(real, [600], third_headers)  # a third browser needs room: the biggest gives one up
    assert held(real, chrome) == chrome[1:] and held(real, safari) == safari and held(real, newcomer) == newcomer
    again = await open_job_pages(real, [501 + 10], safari_headers)  # now chrome 5, safari 3 (+1), third 1: chrome pays again
    assert held(real, chrome) == chrome[2:] and held(real, safari) == safari and held(real, again) == again
    assert len(real.app.state.extension_sessions.sessions) <= 9


async def test_reloading_an_open_page_at_the_limit_evicts_nobody_and_keeps_the_session(real, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS_PER_CLIENT", 4, raising=False)
    monkeypatch.setattr(S, "MAX_SESSIONS", 4)
    ids = await open_job_pages(real, range(700, 704))
    registry = real.app.state.extension_sessions
    oldest = registry.sessions[ids[0]]
    again = await open_job_pages(real, [700])  # the page of the least recently used session is opened again (a reload)
    assert again == [ids[0]] and registry.sessions[ids[0]] is oldest  # the very same session, with its state
    assert held(real, ids) == ids


async def test_a_browser_that_is_not_over_its_share_loses_nothing_when_another_is(real, monkeypatch):
    """The ceiling is for a whole Meridian: a small browser keeps what it has however large the other one grows."""
    monkeypatch.setattr(S, "MAX_SESSIONS_PER_CLIENT", 50, raising=False)
    monkeypatch.setattr(S, "MAX_SESSIONS", 6)
    _, safari_headers = pair(real.app, origin=SAFARI_ORIGIN, browser="safari")
    safari = await open_job_pages(real, [800, 801], safari_headers)
    chrome = await open_job_pages(real, range(810, 820))  # ten pages against a ceiling of six
    assert held(real, safari) == safari and held(real, chrome) == chrome[-4:]
