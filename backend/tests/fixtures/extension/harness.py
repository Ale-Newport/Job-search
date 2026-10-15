"""Shared offline harness of the browser-assistant backend tests: a seeded candidate, a real main app (temp data
directory, in-memory secrets), a mini bridge app around the real routes and a scripted answer model."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jobagent.application_answer_llm import SCORE_NAMES
from jobagent.db import now, uid
from jobagent.extension import routes
from jobagent.extension.app import install_error_handlers
from jobagent.extension.auth import Client, current_client
from jobagent.intelligence.store import entity, ingest_fact, link, policy
from jobagent.main import create_app

ROOT = Path(__file__).resolve().parents[4]


def chrome_manifest(version="1.0.0", **extra):
    """A Chrome manifest as the production build writes it: pinned to the Meridian Assistant id by the public key (the
    installer refuses a folder whose key does not derive CHROME_EXTENSION_IDS)."""
    key = (ROOT / "extension" / "manifest-key.txt").read_text().strip()
    return {"manifest_version": 3, "name": "Meridian Assistant", "version": version, "key": key, **extra}


CLIENT = Client(id="11111111-1111-4111-8111-111111111111", name="Chrome on this Mac", browser="chrome",
                origin="chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc", version="1.0.0")
OTHER_CLIENT = Client(id="22222222-2222-4222-8222-222222222222", name="Safari on this Mac", browser="safari",
                      origin="safari-web-extension://abcdef12-0000", version="1.0.0")


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def seed_candidate(db, *, with_policies=True):
    """The synthetic verified candidate of the answer-engine studies (never real data)."""
    def record(kind, name, fields):
        entity_id = entity(db, kind, name)
        ids = {}
        for key, value in fields.items():
            kind_of = ("boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float))
                       else "list" if isinstance(value, list) else "text")
            ids[key] = ingest_fact(db, entity_id, key, value, value_type=kind_of, source="Synthetic verified test record",
                                   evidence={"excerpt": str(value)}, verified=True)
        return entity_id, ids

    record("person", "Test Candidate", dict(
        full_name="Test Candidate", first_name="Test", last_name="Candidate", email="test@example.test",
        phone="+441234567890", city="London", location="London, United Kingdom", country="United Kingdom",
        country_code="GB", nationality="Spanish", linkedin="https://example.test/linkedin",
        portfolio="https://example.test/portfolio", github="https://example.test/github",
        employment_contract_active=False, earliest_full_time_start_date="2027-09-28",
        professional_interests="AI and software engineering"))
    record("education", "North University", dict(
        institution="North University", degree="BSc Computer Science", level="Bachelor's degree", rank=1,
        subject="Computer Science", status="completed", start_date="2023-09-01", end_date="2026-06-30",
        classification="First Class Honours", average=79.94))
    record("education", "South University", dict(
        institution="South University", degree="MSc Artificial Intelligence", level="Master's degree", rank=2,
        subject="Artificial Intelligence", status="studying", start_date="2026-09-28", end_date="2027-09-27",
        full_time=True))
    record("language", "English", dict(proficiency="Fluent"))
    record("language", "Spanish", dict(proficiency="Native"))
    record("immigration", "UK Student", dict(country="United Kingdom", route="Student", current_right_to_work=True,
                                              sponsorship_now_or_future=False))
    record("experience", "Example Studio", dict(
        role="Developer", team_size=6, start_date="2025-07-01", end_date="2025-09-30",
        story="I led six engineers to deliver a responsive website.", tags=["leadership", "frontend", "teamwork"]))
    project, facts = record("project", "First Project", dict(
        start_date="2024-01-01", end_date="2024-12-31", technologies=["Python"],
        story="I built a vision pipeline using Python.", tags=["ai", "machine learning", "ownership"]))
    skill = entity(db, "skill", "Python")
    link(db, skill, "used_in", project, [facts["technologies"]])
    if with_policies:
        policy(db, "optional_demographics", "decline_or_blank")
        policy(db, "salary_expected", {"target": 60000, "currency": "GBP"}, {"country": "United Kingdom"})
    return db


_TEMPLATES: dict = {}


def _build(root, seed, text_model):
    app = create_app(root, token="test-only-token", secret_store=MemorySecrets(), start_scheduler=False,
                     worker_isolation=False)
    db = app.state.db
    for client in (CLIENT, OTHER_CLIENT):
        db.execute("INSERT OR IGNORE INTO extension_clients(id,name,browser,origin,token_hash,version,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", (client.id, client.name, client.browser, client.origin, uid(), "1.0.0", now()))
    if seed:
        seed_candidate(db)
    if text_model:
        configure_text_model(db)
    return app


def make_app(tmp_path, *, seed=True, text_model=True):
    """A real Meridian app (no scheduler, no automation worker, no bridge listener) with the assistant's services,
    started from a cached, already-migrated and seeded data directory (copied, never shared between tests)."""
    import atexit
    import shutil
    import tempfile

    key = (seed, text_model)
    if key not in _TEMPLATES:
        directory = Path(tempfile.mkdtemp(prefix="meridian-ext-template-"))
        atexit.register(shutil.rmtree, directory, ignore_errors=True)
        _build(directory, seed, text_model)
        _TEMPLATES[key] = directory
    target = Path(tmp_path) / "data"
    shutil.copytree(_TEMPLATES[key], target)
    return create_app(target, token="test-only-token", secret_store=MemorySecrets(), start_scheduler=False,
                      worker_isolation=False)


def put_setting(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))


def configure_text_model(db, *, provider="ollama", tested=True, budget=0, model="test-model"):
    """A local (free) writing model, connection-tested, so background drafting is available."""
    from jobagent.onboarding import TEXT_AI, fingerprint, record
    put_setting(db, "text_provider", provider)
    put_setting(db, "text_model", model)
    put_setting(db, "text_retry_attempts", 0)
    put_setting(db, "monthly_budget", budget)
    if tested:
        record(db, "ai_text", {"success": True, "checked_at": now(), "fingerprint": fingerprint(db, TEXT_AI),
                               "provider": provider})


def bridge(app, client=CLIENT) -> FastAPI:
    """The real routes in a tiny app: auth is replaced by a fixed paired client, errors render as {detail, code}."""
    mini = FastAPI()
    mini.state.main = app
    install_error_handlers(mini)
    mini.include_router(routes.router, prefix="/v1")
    mini.dependency_overrides[current_client] = lambda: client
    return mini


def http(mini: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mini), base_url="http://bridge", timeout=60)


# ------------------------------------------------------------------------------------------------ elements
def el(node_id, role="textbox", label="", **kwargs):
    """A control as the content script reports it (observation.js shape, pruned)."""
    tag = kwargs.pop("tag", {"combobox": "input", "textbox": "input", "radio": "input", "checkbox": "input",
                             "upload": "input"}.get(role, "input"))
    kind = kwargs.pop("type", {"radio": "radio", "checkbox": "checkbox", "upload": "file"}.get(role, "text"))
    operations = kwargs.pop("operations", {"textbox": ["TYPE_TEXT", "CLEAR"], "radio": ["CHECK", "CLEAR"],
                                           "checkbox": ["CHECK", "UNCHECK", "CLEAR"], "upload": ["UPLOAD"],
                                           "combobox": ["TYPE_TEXT", "CLICK"]}.get(role, []))
    base = {"node_id": node_id, "role": role, "tag": tag, "type": kind, "label": label, "context": "", "description": "",
            "placeholder": "", "section_id": "page", "section_label": "Application details", "form_id": 1, "group": "",
            "field_path": "", "name": "", "id": "", "autocomplete": "", "required": False, "enabled": True,
            "readonly": False, "multiple": False, "checked": False, "expanded": False, "has_value": False, "value": "",
            "selected_text": "", "selected_values": [], "operations": operations, "options": []}
    base.update(kwargs)
    return base


def select(node_id, label, options, **kwargs):
    kwargs.setdefault("type", "select-multiple" if kwargs.get("multiple") else "select-one")
    return el(node_id, "combobox", label, tag="select", operations=["SELECT"],
              options=[{"label": o[0], "value": o[1]} if isinstance(o, tuple) else {"label": o, "value": o} for o in options],
              **kwargs)


def radios(first_id, question, labels, group="g", **kwargs):
    return [el(first_id + 2 * i, "radio", label, context=question, group=group, value=label.lower(), name=group,
               **kwargs) for i, label in enumerate(labels)]


def posting(**overrides):
    """A page report for a Greenhouse-style posting with JSON-LD (no network is needed to understand it)."""
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Software Engineer",
           "description": "<p>" + "Build and run reliable payment services used by millions of customers every day. " * 8
                          + "</p>",
           "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics", "url": "https://acmerobotics.example/"},
           "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "UK"}}}
    page = {"url": "https://boards.greenhouse.io/acmerobotics/jobs/4001", "title": "Software Engineer - Acme Robotics",
            "h1": "Software Engineer", "lang": "en", "company_hint": None, "meta": {}, "canonical": None,
            "jsonld": [json.dumps(job)], "description_text": "", "links": [], "user": {}}
    page.update(overrides)
    return {"page": page, "tab_key": "tab-1", "locale": "en-GB"}


def suggest_body(elements, **overrides):
    body = {"document_id": "doc-1", "frame_url": "https://boards.greenhouse.io/acmerobotics/jobs/4001",
            "frame_key": "top", "elements": elements, "mode": "scan", "challenge": False, "mfa": False, "login": False}
    body.update(overrides)
    return body


# ------------------------------------------------------------------------------------------------ scripted model
def provider_response(request, generated):
    """The reply shape of the provider the request went to (local ollama or an OpenAI-compatible endpoint)."""
    text = json.dumps(generated)
    if request.url.path.endswith("/api/chat"):
        return httpx.Response(200, json={"message": {"content": text}, "prompt_eval_count": 15, "eval_count": 30})
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                     "usage": {"prompt_tokens": 15, "completion_tokens": 30}})


def request_payload(request):
    body = json.loads(request.content)
    return json.loads(body["messages"][-1]["content"])


VISION = ("I built a vision pipeline using Python", "vision pipeline")
TEAM = ("I led six engineers to deliver a responsive website", "six engineers")
NORTH = ("I studied Computer Science at North University", "North University")
SOUTH = ("I am studying Artificial Intelligence at South University", "South University")
ANSWERS = [  # (answer, verbatim claim, substring identifying the supporting fact); every claim is stated by a fact
    ("I built a vision pipeline using Python.", *VISION),
    ("I led six engineers to deliver a responsive website.", *TEAM),
    ("I studied Computer Science at North University.", *NORTH),
    ("I am studying Artificial Intelligence at South University.", *SOUTH),
]


class ScriptedModel:
    """An offline answer model: drafts a grounded sentence from a verified fact and approves it in review.

    ``calls`` holds every request payload; ``drafts`` only the drafting ones. Each draft returns a DIFFERENT
    answer (the model layer rejects near-duplicates of earlier answers on the same page)."""

    def __init__(self, *, answer=None, fail_with=None, delay=0.0, answers=None):
        self.calls, self.drafts = [], []
        self.answers = list(answers or ANSWERS)
        self.answer = answer
        self.fail_with = fail_with
        self.delay = delay
        self.running = self.peak = 0
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request):
        import asyncio
        payload = request_payload(request)
        self.calls.append(payload)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.running -= 1
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"error": "scripted"})
        if "generated_answer" in payload:
            return provider_response(request, {"question_id": payload["question_id"], "passed": True,
                                               "scores": {name: 0.95 for name in SCORE_NAMES}, "unsupported_claims": [],
                                               "issues": [], "reason": "Grounded."})
        self.drafts.append(payload)
        text, claim, needle = (self.answers[(len(self.drafts) - 1) % len(self.answers)] if self.answer is None
                               else (self.answer, *VISION))
        fact = next((f for f in payload["verified_facts"] if needle in str(f["value"])), payload["verified_facts"][0])
        return provider_response(request, {
            "question_id": payload["question_id"], "intent": payload["intent"], "answer": text, "facts_used": [fact["id"]],
            "selected_options": [], "selected_roles": [], "company_claims": [], "claims": [
                {"text": claim, "fact_ids": [fact["id"]]}],
            "evidence_quality": "HIGH", "word_count": len(text.split()), "follows_instructions": True,
            "answers_question": True, "unsupported_claims": [], "requires_human_input": False, "missing_facts": []})

    def install(self, app):
        app.state.text_ai.answer_llm.transport = self.transport
        return self


def counted(app):
    return SimpleNamespace(events=app.state.db.query("SELECT kind,outcome FROM extension_events"))


def new_id():
    return uid()


def add_cv(app, *, approved=True, filename="cv.pdf", kind="import", content=b"%PDF-1.4 synthetic test cv", text="Test Candidate CV"):
    """Register a CV version exactly as the document vault does. Returns the document_versions row."""
    from jobagent.documents import save_version
    db = app.state.db
    document = {"id": uid(), "name": filename, "kind": kind, "job_id": None, "created_at": now(), "updated_at": now()}
    db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", tuple(document.values()))
    mime = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[
        filename.rsplit(".", 1)[1]]
    return save_version(db, Path(app.state.data_dir), document, content, filename, mime, text, approved=approved)
