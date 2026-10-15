"""Controls -> suggestions: the state table, insertion tiers and operations of docs/EXTENSION_DESIGN.md 5.3.

``SuggestService.suggest`` takes the controls a content script scanned, resolves each assist field with the
:class:`~jobagent.extension.resolver.FieldResolver` (the same pipeline Auto Apply uses) and turns every result into a
``Suggestion``:

=================  ==========================================================================  ==================
state              condition (first match wins)                                                insert
=================  ==========================================================================  ==================
SKIP               password / hidden / file without document / readonly / disabled / the page  never
                   is a challenge, MFA or login
NOT_APPLICABLE     a conditional question whose parent answer excludes it (or is not given yet)  never
SENSITIVE_BLANK    leave_blank and no answer (personal-disclosure policy, optional demographics)  never
CONSENT            no answer and explicit confirmation required / a consent-type intent         never
GENERATING         a long answer is being drafted in the background                              never
GENERATED_DRAFT    validated draft (always requires review)                                      tab
VERIFIED           verified fact or policy (EXACT / POLICY)                                      tab (explicit
                                                                                                 when protected)
INFERRED           derived from verified facts (DERIVED_*)                                       tab (explicit
                                                                                                 when protected)
NEEDS_YOU          anything else without a safe answer                                           never
=================  ==========================================================================  ==================

The classifier never trusts one flag: the engine's result dicts are inconsistent across paths, so the result is
normalised first and every rule reads the combination (answer, root cause, level, verified/inferred).
Nothing the user typed is ever received here: text controls only report ``has_value``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from ..application_profile import question_field
from ..automation.answers import SENSITIVE, classify_question, dropdown_search_value, normalize
from ..automation.disclosures import personal_disclosure_kind
from ..intelligence import local_assist as assist
from ..intelligence.formatting import choice as option_for
from ..intelligence.semantics import history_block, signature_field
from . import draft_basis, draft_limits, documents_pick  # ASSIST2: SAFE-09/11, CPL-06/09/14, SEC-06
from .documents_pick import (CV_KINDS, MAX_DOCUMENT, approved_documents, document_content,  # noqa: F401  (re-exported)
                             looks_like_cv_field)
from .feedback import never_saved_reason, scope_of
from .generation import ACTIVE, GenerationQueue, Job, Refused
from .local_queue import ACTIVE as LOCAL_ACTIVE
from .research import tail_key
from .resolver import Environment, FieldResolver, Resolution, load_environment, prepare_elements
from .schemas import RegenerateRequest, SuggestRequest

logger = logging.getLogger("meridian.extension")

ASYNC = "ASYNC_GENERATION_REQUIRED"
CONSENT_INTENTS = {"CONSENT", "GDPR", "CRIMINAL_HISTORY", "SECURITY_CLEARANCE", "BACKGROUND_CHECK",
                   "CONFLICT_OF_INTEREST", "ROLE_CONFIRMATION"}
# Somebody else's details and a fact of ONE entry of a work or education history are no field of the applicant's Profile and no
# model may write them: no 'open your Profile', no draft, no reuse.
NO_PROFILE_INTENTS = {"OTHER_PARTY", "HISTORY_ENTRY"}
NOT_FIELD_ROLES ={"button", "link", "option", "treeitem", "menuitem", "tab", "listitem", "heading", "alert", "img"}
NOT_FIELD_TYPES = {"submit", "reset", "button", "image", "range", "color"}
# What a local model may be asked about a control with options: the generic "no confirmed value", an answer that did not map to the menu,
# and a question the engine could only defer to a writing model (a menu cannot be written). Every other "no answer" is a decision of the
# engine (a plain-question refusal, a consent, an education fact that no record establishes...) and a model never second-guesses it.
LOCAL_CHOICE_CAUSES = {"MISSING_FACT", "BAD_ENUM_MAPPING", ASYNC}  # ASYNC: a question the engine only knows as "something to write"
DRAFT_WAIT = 8.0
SLICE_SECONDS = 0.02  # the longest the answers of one scan may run before the event loop gets a turn
HEAVY_TEXT = 1500  # characters of label + context + hint + placeholder past which a control is read under a deadline
FIELD_BUDGET = 2.0  # seconds a heavy control may take before it is left to the user
# ASSIST2 (SAFE-11 / CPL-09): why a finished draft is no longer offered
OUTDATED = {"facts_changed": "Your profile changed after this draft was written, so it may say something that is no longer "
                             "true. Write a new draft from your current facts.",
            "company_rejected": "You said this is not {company}, so this draft (written for the company Meridian first "
                                "assumed) is out of date. Write a new draft."}
MESSAGES = {
    "missing_fact": "Meridian has no confirmed value for this yet.",
    "format_error": "Your value does not fit this field's format or length.",
    "no_matching_option": "None of the choices matches your confirmed answer.",
    "setup_required": "Long answers need a writing model (Settings > AI).",
    "budget": "Monthly AI budget reached; short answers still work.",
    "model_error": "The writing model is unavailable right now. Try again in a moment.",
    "validation_failed": "Meridian could not write a grounded answer to this question.",
    "missing_candidate_fact": "Add this to your Meridian profile so a draft can be grounded in it.",
    "draft_outdated": OUTDATED["facts_changed"],
}


# --------------------------------------------------------------------------------------------- field rules
def skip_reason(element: dict, request) -> str | None:
    """Why an assist-capable control is never answered, or None. Order matters: the password rule first."""
    if str(element.get("type") or "").lower() == "password":
        return "password"
    if str(element.get("type") or "").lower() == "hidden":
        return "hidden"
    if request.challenge:
        return "challenge"
    if request.mfa:
        return "mfa"
    if request.login:
        return "login"
    if not element.get("enabled", True):
        return "disabled"
    if element.get("readonly"):
        return "readonly"
    return None


def is_field(element: dict) -> bool:
    """Does this observed control belong to an application form at all (vs buttons, links, menu options)?"""
    role, kind = element.get("role"), str(element.get("type") or "").lower()
    if element.get("auxiliary_control") or role in NOT_FIELD_ROLES:
        return False
    if role in {"textbox", "combobox", "radio", "checkbox", "switch", "upload"} or kind in {"file", "password", "hidden"}:
        return kind not in NOT_FIELD_TYPES or role in {"radio", "checkbox", "combobox", "switch", "upload"}
    return False


def group_fields(elements: list[dict]) -> list[tuple[int, list[int]]]:
    """[(representative index, member indexes)] in DOM order. Radio sets and multi-checkbox groups are ONE assist
    field represented by their first control; everything else is its own field."""
    seen: dict[tuple, int] = {}
    fields: list[tuple[int, list[int]]] = []
    for index, element in enumerate(elements):
        if not is_field(element) and str(element.get("type") or "").lower() not in {"password", "hidden"}:
            continue
        grouped = element["role"] == "radio" or (element["role"] == "checkbox" and element["multiple"])
        if grouped and element.get("group"):
            key = (element["role"], element.get("form_id"), element["group"])
            if key in seen:
                fields[seen[key]][1].append(index)
                continue
            seen[key] = len(fields)
        fields.append((index, [index]))
    return fields


def is_long_form(element: dict) -> bool:
    if element.get("options") or element["role"] != "textbox":
        return False
    return (element["tag"] in {"textarea", "div", "span", "p"} or element["type"] == "textarea"
            or bool(element.get("max_length") and element["max_length"] >= 150))


def can_draft(question: str, element: dict) -> bool:
    """May a model be asked to write this on the user's request? Never sensitive, legal, salary or personal data."""
    kind = classify_question(question)
    if kind in {*SENSITIVE, "work_authorization", "salary"} or personal_disclosure_kind(question, element):
        return False
    if element["role"] != "textbox" or "TYPE_TEXT" not in element.get("operations", ["TYPE_TEXT"]):
        return False
    return element["type"] not in {"password", "hidden", "file", "checkbox", "radio", "number", "date", "month", "email",
                                   "tel", "url"} and element.get("tag") != "select"


# --------------------------------------------------------------------------------------------- documents
# ASSIST2 (SAFE-09 / CPL-14): which CV or cover letter may be offered, and the content endpoint, live in documents_pick.py
# (``approved_documents``, ``document_content``, ``looks_like_cv_field`` are imported above and re-exported from here).


# --------------------------------------------------------------------------------------------- results
def cause_group(result: dict) -> str:
    """Root cause -> the `needs.code` vocabulary of the design."""
    cause = str(result.get("root_cause") or result.get("state") or "")
    reason = str(result.get("reason") or "").lower()
    if cause in {"LLM_SETUP_REQUIRED", "LLM_CONFIGURATION_REQUIRED", "setup_required"}:
        return "setup_required"
    if cause == "AI_BUDGET_EXCEEDED" or (cause == "ValueError" and "budget" in reason) or cause == "AI_DRAFT_BUDGET":
        return "budget"
    if cause == "MODEL_API_ERROR":
        return "model_error"
    if cause in {"ANSWER_VALIDATION_FAILED", "ANSWER_FAILED", "INTERNAL_ERROR"}:
        return "validation_failed"
    if cause == "DRAFT_OUTDATED":
        return "draft_outdated"
    if cause == "MISSING_CANDIDATE_FACT":
        return "missing_candidate_fact"
    if cause in {"ANSWER_FORMAT_ERROR", "UNSUPPORTED_FIELD_PATTERN", "INVALID_STORED_DATE"}:
        return "format_error"
    if cause in {"BAD_ENUM_MAPPING", "BAD_MULTISELECT_MAPPING"}:
        return "no_matching_option"
    return "missing_fact"


def decide(result: dict) -> str:
    """The pure state table for a normalised result (SKIP / NOT_APPLICABLE / GENERATING are decided by the caller)."""
    answer, cause, level = result["answer"], result.get("root_cause"), result["confidence_level"]
    if result["leave_blank"] and answer is None:
        return "SENSITIVE_BLANK"
    if answer is None and (cause == "EXPLICIT_CONFIRMATION_REQUIRED" or result["canonical_intent"] in CONSENT_INTENTS):
        return "CONSENT"
    if answer is None or cause or result.get("requires_human_input"):
        return "NEEDS_YOU"
    if level == "GENERATED":
        quality = result.get("quality") or {}
        return "GENERATED_DRAFT" if quality.get("passed") is True and result.get("state") == "ANSWER_VALIDATED" else "NEEDS_YOU"
    if result["verified"] and level in {"EXACT", "POLICY"}:
        return "VERIFIED"
    if level in {"DERIVED_HIGH", "DERIVED_MEDIUM"} or result["inferred"] or result.get("requires_review"):
        return "INFERRED"
    return "NEEDS_YOU"


def is_protected(question: str, element: dict, result: dict, peers=None, job=None) -> bool:
    intent = result["canonical_intent"]
    kind = classify_question(question)
    # a typed signature is an attestation, whatever words the label uses for it (and a name typed in a section that has a consent or
    # a declaration box is one: ``peers`` are the other controls of the page). A one-time code, a card, a bank detail, an ID number
    # or a security answer is never offered on a Tab, saved or reused (SAFE-14): "always save" and Reuse read this.
    return bool(never_saved_reason(question, element) is not None
                or personal_disclosure_kind(question, element) or kind in {*SENSITIVE, "work_authorization"}
                or (intent in {"IDENTITY", "OTHER", "OTHER_PARTY"} and signature_field(question, element, peers, job))
                or intent in {"DEMOGRAPHIC", "WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "REASONABLE_ADJUSTMENTS", "GDPR"}
                or (intent in CONSENT_INTENTS and result["answer"] is not None))


def build_value(element: dict, field_el: dict, result: dict) -> tuple[dict | None, str | None]:
    """The operation the content script performs, or (None, needs-code) when the answer cannot be applied safely."""
    answer, form_value, question = result["answer"], result.get("form_value"), result["question"]
    concept = result.get("disclosure_concept")
    role, tag = element["role"], element["tag"]
    if role == "radio":
        option = option_for(str(answer), field_el.get("options") or [], question=question, disclosure_concept=concept)
        if not option:
            return None, "no_matching_option"
        return {"display": option["label"], "operation": "CHECK_RADIO", "form_value": option["label"],
                "option": {"label": option["label"], "value": option.get("value") or option["label"]}}, None
    if role in {"checkbox", "switch"} and element["multiple"]:
        labels = [str(x) for x in (form_value if isinstance(form_value, list) else [answer])]
        options = field_el.get("options") or []
        if options and not all(option_for(label, options, question=question) for label in labels):
            return None, "no_matching_option"
        return {"display": ", ".join(labels), "operation": "SET_CHECKBOXES", "labels": labels, "form_value": labels}, None
    if role in {"checkbox", "switch"}:
        truth = normalize(str(answer))
        if truth not in {"yes", "no", "true", "false", "1", "0"}:
            return None, "format_error"
        checked = truth in {"yes", "true", "1"}
        return {"display": "Yes" if checked else "No", "operation": "SET_CHECKBOX", "checked": checked,
                "form_value": checked}, None
    if tag == "select" or str(element.get("type") or "").startswith("select"):
        values = form_value if element["multiple"] and isinstance(form_value, list) else [answer]
        options = [option_for(str(item), element["options"], question=question, disclosure_concept=concept)
                   for item in values]
        if not options or any(option is None for option in options):
            return None, "no_matching_option"
        if element["multiple"]:
            labels = [option["label"] for option in options]
            return {"display": ", ".join(labels), "operation": "SET_CHECKBOXES", "labels": labels,
                    "form_value": labels}, None
        return {"display": options[0]["label"], "operation": "SELECT_OPTION", "form_value": options[0]["label"],
                "option": {"label": options[0]["label"], "value": options[0].get("value") or options[0]["label"]}}, None
    if role == "combobox":
        label = str(answer)
        search = dropdown_search_value({**result, "answer": label, "question": question})
        return {"display": label, "operation": "OPEN_AND_SELECT", "label": label, "search_text": search,
                "form_value": form_value if form_value is not None else label}, None
    text = str(answer)
    code = str(element.get("phone_country_code") or "")
    digits = re.sub(r"\D", "", text)
    if element.get("phone_national_only") and text.startswith("+") and code and digits.startswith(code):
        text = digits[len(code):]
    return {"display": text, "operation": "TYPE_TEXT", "text": text,
            "form_value": form_value if isinstance(form_value, (str, int, float)) else text}, None


# --------------------------------------------------------------------------------------------- stored state
@dataclass
class Frame:
    key: str
    document_id: str
    url: str
    elements: list
    touched: float = field(default_factory=time.monotonic)


@dataclass
class StoredField:
    key: str
    frame_key: str
    index: int
    node_id: int
    members: list
    order: tuple
    resolution: Resolution
    question: str
    question_id: str
    scope: object
    skipped: str | None = None
    draft_key: str | None = None
    state: str = ""
    suggestion: dict | None = None
    local_key: str | None = None  # the local-model job (``local_queue``) that is finishing this control, if any


def _words(value: str) -> int:
    return len((value or "").split())


MAX_FRAME_ELEMENTS = 800  # what one frame remembers (the extension scans at most 600 controls)
MAX_FRAMES = 32  # frames one page remembers (the least recently used go first)


def document_order(elements: list[dict]) -> list[dict]:
    """The controls of ONE request in document order and numbered (``index``). A request may list them in any order (the
    scan starts at the focused field, a refresh names the follow-up before its parent): ``position`` says where each
    is, else the observer's numbering does (it numbers controls as it meets them, which is document order for a scan)."""
    positioned = all(e.get("position") is not None for e in elements)
    ordered = sorted(elements, key=lambda e: (e["position"] if positioned else 0, e["node_id"]))
    for number, element in enumerate(ordered):
        element["index"] = number
    return ordered


def merge_frame(previous: list[dict], fresh: list[dict]) -> list[dict]:
    """What a frame remembers after a partial request: the controls it knew (a changed control keeps its place) and
    the new ones, newest last; at most ``MAX_FRAME_ELEMENTS`` (the oldest go first)."""
    news = {e["node_id"]: e for e in fresh}
    merged = [news.pop(e["node_id"], e) for e in previous] + sorted(news.values(), key=lambda e: e["node_id"])
    merged = merged[-MAX_FRAME_ELEMENTS:]
    for number, element in enumerate(merged):
        element["index"] = number
    return merged


# --------------------------------------------------------------------------------------------- the service
class SuggestService:
    """Per-process service. Holds no per-session state: sessions own their frames, fields and drafts."""

    def __init__(self, app, research, generation: GenerationQueue):
        self.app, self.research, self.generation = app, research, generation
        self._gen_cache: tuple[float, dict] | None = None
        self._env_cache: dict[str, Environment] = {}

    # ------------------------------------------------------------------------------- shared inputs
    @property
    def db(self):
        return self.app.state.db

    @property
    def data_dir(self):
        return self.app.state.data_dir

    def extension_settings(self) -> dict:
        from .api import read_extension_settings
        return read_extension_settings(self.db)

    def generation_status(self, *, fresh: bool = False) -> dict:
        """Database-only availability of the writing model (cached 20 s; never touches the Keychain)."""
        now = time.monotonic()
        if self._gen_cache and not fresh and now - self._gen_cache[0] < 20:
            return self._gen_cache[1]
        from .api import generation_summary
        summary = generation_summary(self.app)
        self._gen_cache = (now, summary)
        return summary

    def invalidate_status(self) -> None:
        self._gen_cache = None

    def core_settings(self) -> dict:
        """The full settings dict the answer model needs (provider, model, budget, ...), without private caches."""
        from .state import read_settings
        return read_settings(self.db)

    def job_base(self, session) -> dict:
        """The ``job`` dict of the engine for this page (design 5.4), research merged by the caller."""
        documents = approved_documents(self.db, self.data_dir, session.application_id, session.job_id)
        base = {"job_id": session.job_id or "", "application_id": session.application_id or "", "company_id": "",
                "title": session.title or "", "company": session.company or "", "location": session.location or "",
                "country": session.country or "", "countries": list(session.countries or []),
                "url": session.report["posting_url"], "application_url": session.report["url_clean"],
                "ats": (session.report["ats"].get("ats") or "generic"),
                # How the job was FOUND: only what Meridian's own discovery recorded. The assistant is how the user
                # applies, never how they heard about the job; unset leaves "How did you hear" to the user.
                "source": session.discovery_source or "",
                "description": "", "skills": [], "context_sources": [], "company_context": [],
                "resume_version_id": documents[0]["id"] if documents else "",
                "id": session.application_id or ""}
        return self.research.job_context(session.research_run_id, base) if self.research else base

    def environment(self, session, ext: dict | None = None, *, fresh: bool = False) -> Environment:
        """One knowledge snapshot per data version and page (rebuilt when anything an answer depends on changes)."""
        from .resolver import data_token
        ext = ext or self.extension_settings()
        token = data_token(self.db)
        job = self.job_base(session)
        application = {"id": session.application_id or "", "company": job["company"], "location": job["location"]}
        signature = "|".join([token, application["id"], application["company"], application["location"],
                              str(job.get("country") or ""), str(job.get("source") or ""),
                              str(ext["tab_inserts_protected"])])
        cached = self._env_cache.get(session.id)
        if cached is not None and not fresh and cached.token == signature:
            cached.job = job
            return cached
        settings = {**self.core_settings(), "_tab_inserts_protected": ext["tab_inserts_protected"]}
        env = load_environment(self.db, settings, job=job, application=application, token=signature)
        self._env_cache[session.id] = env
        return env

    def forget(self, session_id: str) -> None:
        self._env_cache.pop(session_id, None)
        if self.local_queue is not None:
            self.local_queue.forget_session(session_id)

    # ------------------------------------------------------------------------------- local models
    @property
    def local_queue(self):
        return getattr(self.app.state, "extension_local", None)

    @property
    def local_models(self):
        return getattr(self.app.state, "local_models", None)

    async def _local(self, session, env, ext, frame: Frame, stored: StoredField):
        """Start (or join) the local-model job that may finish this control, or None. Everything here is decided from what the
        resolver left in ``_local`` and from the policy of the control; no model is asked until the job runs."""
        plan = (stored.resolution.result or {}).get("_local")
        queue, models = self.local_queue, self.local_models
        if not plan or queue is None or models is None or ext.get("local_assist") is False or session.dead:
            return None
        result, element = stored.resolution.result, stored.resolution.element
        question = stored.question
        scope = stored.scope
        if (result.get("canonical_intent") in NO_PROFILE_INTENTS or result.get("root_cause") == "UNTRUSTED_INSTRUCTION_IN_QUESTION"
                or (scope and (scope.protected or scope.legal)) or is_protected(question, element, result, frame.elements, env.job)
                or never_saved_reason(question, element) is not None or signature_field(question, element, frame.elements, env.job)):
            return None
        kind = plan["kind"]
        today = env.engine.knowledge.today.isoformat()
        if kind == "choose":
            if str(result.get("root_cause") or "") not in LOCAL_CHOICE_CAUSES or result.get("canonical_intent") in CONSENT_INTENTS:
                return None
            options = assist.offered(stored.resolution.field.get("options") or [])
            if not 2 <= len(options) <= assist.MAX_OPTIONS:
                return None
            evidence = assist.relevant(assist.evidence_index(env), question, [str(o["label"]) for o in options])
            if not evidence:
                return None
            engines = ("laya", "ollama")
            key = hashlib.sha256(json.dumps(["choose", question, [o["label"] for o in options],
                                             [[e.id, e.text] for e in evidence], env.version, today]).encode()).hexdigest()
            runner = self._choose_runner(question, options, evidence, result, today)
        elif kind == "rewrite":
            engines = ("ollama",)
            base = plan["base"]
            fields = {k: element.get(k) for k in ("pattern", "max_length")}
            key = hashlib.sha256(json.dumps(["rewrite", question, str(base["answer"]), plan["hints"], fields, env.version]).encode()).hexdigest()
            runner = self._rewrite_runner(plan, fields, result)
        else:
            return None
        for engine in engines:
            if not await self.local_models.usable(engine):
                return None
        job = queue.submit(session_id=session.id, key=key, order=stored.order, kind=kind, runner=runner, listener_id=stored.key,
                           listener=self._local_listener(session, stored))
        if job is not None:
            stored.local_key = job.key
        return job

    def _choose_runner(self, question: str, options: list, evidence: list, result: dict, today: str):
        models = self.local_models

        async def run():
            if not (await models.available("laya", wait=75) and await models.available("ollama", wait=75)):
                return None
            picked = await assist.choose_option(models, question, options, evidence, today=today)
            return assist.merge_choice(result, picked) if picked is not None else None
        return run

    def _rewrite_runner(self, plan: dict, fields: dict, result: dict):
        models = self.local_models

        async def run():
            if not await models.available("ollama", wait=75):
                return None
            written = await assist.rewrite_value(models, plan["core"], fields, str(plan["base"]["answer"]), plan["hints"])
            return assist.merge_rewrite(result, plan["base"], written.value) if written is not None else None
        return run

    def _local_listener(self, session, stored: StoredField):
        def on_job(job) -> None:
            session.local_changed(self, stored.key, job)
        return on_job

    def rebuild_local(self, session, key: str, job) -> dict | None:
        """The suggestion of a field after its local-model job started or ended."""
        stored = session.fields.get(key)
        if stored is None or stored.local_key != job.key:
            return None
        ext = self.extension_settings()
        env = self.environment(session, ext)
        suggestion = self.build(session, env, ext, self.generation_status(), stored, local=job)
        stored.suggestion, stored.state = suggestion, suggestion["state"]
        return suggestion

    @staticmethod
    def _limit_frames(session) -> None:
        """A page whose iframes come and go (each new frame has a new key) must not remember all of them for ever."""
        while len(session.frames) > MAX_FRAMES:
            oldest = min(session.frames, key=lambda k: session.frames[k].touched)
            session.frames.pop(oldest)
            for key in [k for k, f in session.fields.items() if f.frame_key == oldest]:
                session.fields.pop(key, None)
        locks = getattr(session, "frame_locks", None)
        if locks and len(locks) > MAX_FRAMES:  # the locks of forgotten frames
            for key in [k for k, lock in locks.items() if k not in session.frames and not lock.locked()]:
                locks.pop(key, None)

    @staticmethod
    def _prune_fields(session, frame_key: str, present: set) -> None:
        """Stored resolutions of controls this frame no longer has (a re-rendered page numbers its controls anew):
        without this every re-scan of a long-lived page would keep the previous scan's fields in memory."""
        for key in [k for k, f in session.fields.items() if f.frame_key == frame_key and f.node_id not in present]:
            session.fields.pop(key, None)

    def _uploads(self, session) -> dict:
        """ASSIST2: what this page may attach (CVs eligible for THIS job, a cover letter written for it) and what waits for
        approval: the inputs of ``documents_pick.decide_upload``."""
        return {"cvs": approved_documents(self.db, self.data_dir, session.application_id, session.job_id),
                "letters": documents_pick.cover_letters(self.db, self.data_dir, session.application_id, session.job_id),
                "unapproved": documents_pick.unapproved_counts(self.db, session.job_id)}

    # ------------------------------------------------------------------------------- suggest
    async def suggest(self, session, request: SuggestRequest) -> dict:
        # The cursor the answers are consistent with: read BEFORE anything is computed. A draft that finishes while this
        # request is still running (a yield, the audit write) emits its event after this cursor, so the page polls it;
        # read at the end, the cursor would skip that event and the field would say "writing a draft" for ever.
        cursor = session.cursor
        ext = self.extension_settings()
        env = self.environment(session, ext)
        gen = self.generation_status()
        frame_key = request.frame_key or "top"
        document_id = request.document_id or f"{session.id}:{frame_key}"
        prepared = prepare_elements(request.elements, document_id, request.frame_url)
        requested = {e["node_id"] for e in prepared}
        previous = session.frames.get(frame_key)
        same_document = previous is not None and previous.document_id == document_id
        # A focus request carries only the control (and its neighbours): the scanned form stays as its context. A refresh
        # carries what changed (a conditional follow-up and the control that revealed it); it is read on its own, in
        # document order, because the frame remembered so far cannot tell where a control that appeared later sits. The
        # frame still remembers everything, for the requests that follow.
        focused = request.mode == "focus" and same_document
        remembered = None
        if same_document and request.mode != "scan":
            remembered = merge_frame(previous.elements, prepared if focused else [dict(e) for e in prepared])
        elements = remembered if focused else document_order(prepared)
        remembered = remembered if remembered is not None else elements
        frame = Frame(frame_key, document_id, request.frame_url or session.url, remembered)
        session.frames[frame_key] = frame
        self._limit_frames(session)
        resolver = FieldResolver(env, url=frame.url, ats=session.ats, scope=session.id, frame_key=frame_key,
                                 document_id=document_id, elements=elements)
        fields = group_fields(elements)
        in_group = {i for _, members in fields for i in members}
        representatives = {rep for rep, _ in fields}
        skipped = [{"node_id": e["node_id"], "reason": ("group_member" if i in in_group else "auxiliary"
                                                        if e.get("auxiliary_control") else "button"
                                                        if e.get("role") in {"button", "link"} else "not_a_field")}
                   for i, e in enumerate(elements) if i not in representatives and e["node_id"] in requested]
        focus = request.focus_node_id
        if request.mode == "focus":
            skipped = []
        if request.mode == "focus" and focus is not None:
            fields = [(rep, members) for rep, members in fields if any(elements[i]["node_id"] == focus for i in members)]
        self._prune_fields(session, frame_key, {e["node_id"] for e in remembered})
        documents = self._uploads(session)
        results = []
        slice_began = time.perf_counter()
        for rep, members in fields:
            element = elements[rep]
            order = (list(session.frames).index(frame_key) if frame_key in session.frames else 0, rep)
            try:
                suggestion = await self._one(session, env, ext, gen, resolver, frame, element, rep, members, order,
                                             request, documents)
            except Exception as exc:  # one broken control must never hide the others
                logger.warning("extension_field_error %s", type(exc).__name__)
                suggestion = self._failure(session, env, frame, element, resolver, exc)
            results.append(suggestion)
            if time.perf_counter() - slice_began >= SLICE_SECONDS:
                await asyncio.sleep(0)  # a form of 400 controls must not hold the event loop (and the main API) for seconds
                slice_began = time.perf_counter()
        await session.audit_suggestions(results, elements, frame_key)
        return {"knowledge_version": env.version, "results": results, "skipped": skipped[:400], "cursor": cursor}

    async def _one(self, session, env, ext, gen, resolver: FieldResolver, frame: Frame, element: dict, rep: int,
                   members: list, order: tuple, request, documents: list) -> dict:
        key = f"{frame.key}:{element['node_id']}"
        question = resolver.question(element)
        question_id = resolver.question_id(element, question)
        reason = skip_reason(element, request)
        member_ids = [frame.elements[i]["node_id"] for i in members]
        if reason is None and (element["role"] == "upload" or element["type"] == "file"):
            return self._attach(session, env, frame, element, question, question_id, documents, key, member_ids)
        if reason is not None:
            suggestion = self._skip(env, frame, element, question, question_id, reason)
            session.fields[key] = StoredField(key, frame.key, rep, element["node_id"], member_ids, order,
                                              Resolution(question, question_id, element, element, {}, None), question,
                                              question_id, None, reason, state="SKIP", suggestion=suggestion)
            return suggestion
        condition = resolver.context(element)
        if condition and condition.get("applies") is not True:
            suggestion = self._not_applicable(env, frame, element, question, question_id, condition, member_ids)
            session.fields[key] = StoredField(key, frame.key, rep, element["node_id"], member_ids, order,
                                              Resolution(question, question_id, element, element, {}, condition),
                                              question, question_id, None, state="NOT_APPLICABLE", suggestion=suggestion)
            return suggestion
        resolution = await self._resolve_within_budget(resolver, element, condition, question, question_id)
        scope = scope_of(question, resolution.field, resolution.result["canonical_intent"], env.application)
        stored = StoredField(key, frame.key, rep, element["node_id"], member_ids, order, resolution, question,
                             question_id, scope)
        previous = session.fields.get(key)
        if previous is not None:
            stored.draft_key = previous.draft_key
        session.fields[key] = stored
        job = None
        blocked = None
        if resolution.result.get("root_cause") == ASYNC:
            job, blocked = self._draft(session, env, ext, gen, resolver, frame, stored, explicit=False)
        local = None
        if job is None:
            try:
                local = await self._local(session, env, ext, frame, stored)
            except Exception as exc:  # noqa: BLE001 - the help of a model must never cost the control the answer the engine gave it
                logger.warning("extension_local_failed %s", type(exc).__name__)
        suggestion = self.build(session, env, ext, gen, stored, job=job, blocked=blocked, local=local)
        stored.suggestion, stored.state = suggestion, suggestion["state"]
        return suggestion

    async def _resolve_within_budget(self, resolver: FieldResolver, element: dict, condition, question: str,
                                     question_id: str) -> Resolution:
        """``resolver.resolve``, with a deadline for a control whose text is unusually long. Resolution is synchronous CPU
        work on the loop the desktop API shares: a page made of long labels must not be able to hold it. A normal question
        runs here and now; a heavy one runs in a worker thread that the loop does not wait for beyond ``FIELD_BUDGET``: past
        it the control is left to the user (the worker finishes on its own, bounded by the text caps of ``prepare_elements``)."""
        heavy = sum(len(str(element.get(k) or "")) for k in ("label", "context", "description", "placeholder")) > HEAVY_TEXT
        if not heavy:
            return await resolver.resolve(element, condition=condition)
        try:
            return await asyncio.wait_for(asyncio.to_thread(asyncio.run, resolver.resolve(element, condition=condition)),
                                          FIELD_BUDGET)
        except TimeoutError:
            message = "This field's text is too long to read safely; answer it yourself."
            result = {"question": question, "question_id": question_id, "canonical_intent": "OTHER", "answer": None,
                      "leave_blank": False, "verified": False, "inferred": False, "fact_ids": [], "confidence_level": "UNKNOWN",
                      "root_cause": "MISSING_FACT", "reason": message, "explanation": message}
            return Resolution(question, question_id, element, element, result, condition)

    # ------------------------------------------------------------------------------- simple states
    def _base(self, env: Environment, frame: Frame, element: dict, question: str, question_id: str) -> dict:
        return {"node_id": element["node_id"], "frame_key": frame.key, "question": question[:2000],
                "question_id": question_id, "request_id": "r_" + uuid.uuid4().hex[:12],
                "knowledge_version": env.version, "state": "SKIP", "insert": "never", "suggestion": None,
                "confidence": None, "why": None, "flags": {}, "needs": None, "generation": None, "based_on": None}

    def _skip(self, env, frame, element, question, question_id, reason) -> dict:
        text = {"password": "Passwords are never filled by Meridian.", "hidden": "Not a visible question.",
                "challenge": "This page asks you to prove you are human; Meridian stays out of the way.",
                "mfa": "This is a verification step; Meridian stays out of the way.",
                "login": "This is a sign-in page; Meridian never signs in for you.",
                "disabled": "This field is not available.", "readonly": "This field cannot be edited.",
                "file": "Meridian only attaches your approved CV to a CV upload."}.get(reason, "Not filled by Meridian.")
        out = self._base(env, frame, element, question, question_id)
        out["why"] = {"code": "skip", "summary": text, "rules": [reason], "facts": []}
        return out

    def _not_applicable(self, env, frame, element, question, question_id, condition, member_ids) -> dict:
        out = self._base(env, frame, element, question, question_id)
        out.update(state="NOT_APPLICABLE")
        waiting = condition.get("applies") is None
        out["why"] = {"code": "not_applicable", "summary": str(condition.get("reason") or (
            "Answer the question above first." if waiting else "This does not apply to you.")), "rules": [], "facts": []}
        out["flags"] = {"conditional": {"applies": condition.get("applies"), "parent": condition.get("parent")},
                        **({"members": member_ids} if len(member_ids) > 1 else {})}
        return out

    def _attach(self, session, env, frame, element, question, question_id, documents, key, member_ids) -> dict:
        """ASSIST2: a file field. ``documents_pick.decide_upload`` decides (an eligible approved CV, or a cover letter written
        for this job, else a SKIP that says precisely why); this builds the suggestion."""
        out = self._base(env, frame, element, question, question_id)
        choice = documents_pick.decide_upload(question, element, cvs=documents["cvs"], letters=documents["letters"],
                                              unapproved=documents["unapproved"])
        document = choice["document"]
        stored = dict(key=key, frame_key=frame.key, index=element["index"], node_id=element["node_id"], members=member_ids,
                      order=(0, element["index"]), resolution=Resolution(question, question_id, element, element, {}, None),
                      question=question, question_id=question_id, scope=None)
        if choice["action"] == "attach":
            letter = choice["kind"] == "cover_letter"
            shown = document["name"] if document.get("name") and document["name"] != document["filename"] else document["filename"]
            out.update(state="VERIFIED", insert="tab",
                       suggestion={"display": shown, "operation": "ATTACH_FILE",
                                   "document": {"id": document["id"], "filename": document["filename"],
                                                "name": document.get("name") or document["filename"],
                                                "mime": document["mime"], "size": document["size"],
                                                "sha256": document["sha256"],
                                                # CPL-20: what a click-only "use another CV" menu may swap in (never on Tab)
                                                **({"alternatives": choice["alternatives"]} if choice.get("alternatives") else {})}},
                       confidence={"value": 1.0, "level": "EXACT"},
                       why={"code": "document_attach" if letter else "cv_attach",
                            "summary": "Your approved cover letter for this job" if letter else "Your approved CV",
                            "rules": [], "facts": []},
                       flags={"protected": False, "legal": False, "requires_review": False})
            session.fields[key] = StoredField(**stored, state="VERIFIED", suggestion=out)
            return out
        if choice["action"] == "problem":
            out.update(state="NEEDS_YOU", needs={"root_cause": "ANSWER_FORMAT_ERROR", "code": "format_error",
                                                 "message": choice["message"], "actions": [{"type": "answer_here"}]},
                       why={"code": "format_error", "summary": choice["message"], "rules": [], "facts": []})
            return out
        out = self._skip(env, frame, element, question, question_id, "file")
        out["why"] = {"code": "skip", "summary": choice["message"], "rules": ["file", choice["reason"]], "facts": []}
        session.fields[key] = StoredField(**stored, skipped="file", state="SKIP", suggestion=out)
        return out

    def _failure(self, session, env, frame, element, resolver, exc) -> dict:
        question = resolver.question(element)
        out = self._base(env, frame, element, question, resolver.question_id(element, question))
        out.update(state="NEEDS_YOU", needs={"root_cause": "INTERNAL_ERROR", "code": "missing_fact",
                                             "message": MESSAGES["missing_fact"], "actions": [{"type": "answer_here"}]},
                   why={"code": "missing_fact", "summary": MESSAGES["missing_fact"], "rules": [], "facts": []})
        return out

    # ------------------------------------------------------------------------------- drafting
    @staticmethod
    def _draft_base(session, stored: StoredField) -> str:
        """Identity of the draft of this question on this page: question, constraints and job context. NOT the
        profile version (a profile edit never spends money by itself) and NOT late research (frozen per page)."""
        element = stored.resolution.element
        constraints = {k: element.get(k) for k in ("type", "multiple", "options", "description", "placeholder", "max_length",
                                                   "min_length", "pattern", "section_label", "_education_reference",
                                                   "_condition")}
        # ASSIST2 (SEC-06): a character counter or a reference number in the label or its help text is not another question
        for noisy in ("description", "placeholder", "section_label"):
            constraints[noisy] = draft_limits.stable_text(constraints.get(noisy))
        payload = [draft_limits.stable_text(stored.question), constraints, session.epoch, session.job_digest()]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _draft_key(base: str, instruction: str, nonce: int) -> str:
        return hashlib.sha256(json.dumps([base, instruction, nonce]).encode()).hexdigest()

    def _draft(self, session, env, ext, gen, resolver, frame: Frame, stored: StoredField, *, explicit: bool,
               instruction: str = "", current_text: str | None = None):
        """-> (job, blocked). ``blocked`` is {'code','message'} when no draft will be written for this field."""
        question, element = stored.question, stored.resolution.element
        if session.dead:
            return None, None  # the page was dropped while this request was running: nothing may be queued for it
        base = self._draft_base(session, stored)
        if not explicit:
            # The newest draft that is running or finished wins; a revision that failed review never takes away an
            # earlier valid draft. Only when every attempt failed does the page show why.
            history = [self.generation.get(k) for k in reversed(session.drafts.get(base) or [self._draft_key(base, "", 0)])]
            usable = next((j for j in history if j is not None and (j.active or j.status == "DONE")), None)
            if usable is not None:
                self._attach_listener(session, stored, usable)
                stored.draft_key = usable.key
                return usable, None
            failed = next((j for j in history if j is not None and j.status == "FAILED"), None)
            if failed is not None:
                stored.draft_key = failed.key
                return None, failed.error
        decided = stored.resolution.result or {}
        if not can_draft(question, element) or stored.scope.protected or stored.scope.legal \
                or decided.get("canonical_intent") in NO_PROFILE_INTENTS \
                or decided.get("root_cause") == "UNTRUSTED_INSTRUCTION_IN_QUESTION":
            return None, None  # nothing a model should write: an ordinary "needs you"
        if not gen["available"] or (not gen["tested"] and not explicit):
            return None, {"code": "LLM_CONFIGURATION_REQUIRED", "message": gen["message"] or MESSAGES["setup_required"]}
        if not explicit and not (ext["background_drafts"] and is_long_form(element)):
            return None, None
        stopped = self.generation.stopped()
        if stopped is not None and not explicit:
            return None, {"code": stopped["code"], "message": stopped["message"]}
        nonce = session.regen.get(base, 0) + (1 if explicit else 0)
        key = self._draft_key(base, instruction if explicit else "", nonce)
        runner = self._runner(session, frame, stored, instruction, current_text)
        try:
            job = self.generation.submit(
                session_id=session.id, key=key, order=stored.order, question_id=stored.question_id, runner=runner,
                listener_id=stored.key, listener=self._listener(session, stored), explicit=explicit,
                meta={"instruction": instruction})
        except Refused as refused:
            return None, {"code": refused.code, "message": refused.message}
        stored.draft_key = key
        session.drafts.setdefault(base, []).append(key)
        if explicit:
            session.regen[base] = nonce
        return job, None

    def _listener(self, session, stored: StoredField):
        def on_job(job: Job) -> None:
            session.job_changed(self, stored.key, job)
        return on_job

    def _attach_listener(self, session, stored: StoredField, job: Job) -> None:
        job.listeners[f"{session.id}|{stored.key}"] = self._listener(session, stored)

    def rebuild(self, session, key: str, job: Job) -> dict | None:
        """The suggestion of a field after its draft changed (progress, done, failed)."""
        stored = session.fields.get(key)
        if stored is None:
            return None
        ext = self.extension_settings()
        env = self.environment(session, ext)
        suggestion = self.build(session, env, ext, self.generation_status(), stored, job=job)
        stored.suggestion, stored.state = suggestion, suggestion["state"]
        return suggestion

    def _runner(self, session, frame: Frame, stored: StoredField, instruction: str, current_text: str | None):
        async def run(related: list) -> dict:
            if self.research and session.research_run_id and self.research.exists(session.research_run_id):
                await self.research.wait(session.research_run_id, DRAFT_WAIT)
            # ASSIST2 (SAFE-11): the facts as the model is given them, so the draft can tell later whether they changed
            before = await asyncio.to_thread(draft_basis.snapshot, self.db)
            env = self.environment(session)  # the profile as it is NOW, not as it was when the draft was queued
            frozen = session.freeze(lambda: self.job_base(session))
            job = {**frozen, "application_id": session.application_id or "", "job_id": session.job_id or "",
                   "id": session.application_id or ""}
            element = {**stored.resolution.element}
            # the language of the page (its <html lang>): a draft is written in the language of the question, and where the
            # question's own words do not say, in that of the page
            element["_page_lang"] = str((getattr(session, "report", None) or {}).get("lang") or "")[:16]
            element["description"] = self._instructed(element.get("description", ""), instruction, current_text)
            extra = []
            if instruction and current_text:
                extra = [{"question_id": stored.question_id, "exact_question": stored.question,
                          "intent": stored.resolution.result.get("canonical_intent"), "answer": current_text,
                          "canonical_intent": stored.resolution.result.get("canonical_intent"),
                          "state": "ANSWER_VALIDATED", "quality": {"passed": True}, "sensitivity": "professional"}]
            llm = self.app.state.text_ai.answer_llm
            engine = env.engine_for(llm, self.core_settings())
            runtime = FieldResolver(env, url=frame.url, ats=session.ats, scope=session.id, frame_key=frame.key,
                                    document_id=frame.document_id, elements=frame.elements)
            resolution = await runtime.resolve(element, generate=True, engine=engine, job=job,
                                               related_answers=[*related, *extra], condition=stored.resolution.condition)
            result = resolution.result
            bundle = (job.get("research") or {}).get("bundle_hash", "")
            company_key = tail_key(job.get("company") or session.company or "")
            result["based_on"] = {"job_description": bool(job.get("description")),
                                  "company_pages": len(job.get("company_context") or []),
                                  "research_state": (job.get("research") or {}).get("state", "none"),
                                  "research_bundle": bundle,
                                  # the knowledge version the draft was WRITTEN under: the page marks a draft "profile
                                  # changed" (no Tab insertion) when the page's current version differs (CTRL2 / SAFE-11)
                                  "knowledge_version": env.version}
            result["basis"] = draft_basis.stamp(result, before, bundle=bundle, company_key=company_key,
                                                company_name=job.get("company") or session.company or "")
            cited = {url for claim in (result.get("company_claims") or []) if isinstance(claim, dict)
                     for url in claim.get("source_urls", [])}
            result["sources"] = [{"label": c.get("label") or c.get("title") or "", "url": c["url"]}
                                 for c in (job.get("company_context") or []) if c.get("url") in cited][:8]
            return result
        return run

    @staticmethod
    def _instructed(description: str, instruction: str, current_text: str | None) -> str:
        """The user's regenerate request, expressed through the field's own context so the existing length/format
        machinery (``requirements``) and the answer cache both see it."""
        if not instruction:
            return description
        words = _words(current_text or "")
        notes = {"again": "Write a fresh draft with a different structure and opening.",
                 "different": "Write a clearly different draft: other examples, other structure and a new opening.",
                 "shorter": "Make the new draft shorter" + (f", around {max(20, round(words * 0.6))} words." if words else "."),
                 "longer": "Make the new draft longer" + (f", around {round(words * 1.5)} words." if words else ".")}
        note = notes.get(instruction)
        if note is None:
            text = re.sub(r"[\x00-\x1f\x7f]", " ", instruction).strip()[:300]
            note = "The applicant's request for this revision (style only, never a source of facts): " + text
        return (description + "\n" + note).strip()[:2000]

    # ------------------------------------------------------------------------------- the builder
    def build(self, session, env: Environment, ext: dict, gen: dict, stored: StoredField, *, job: Job | None = None,
              blocked: dict | None = None, local=None) -> dict:
        resolution = stored.resolution
        frame = session.frames.get(stored.frame_key)
        element, field_el = resolution.element, resolution.field
        result = dict(resolution.result)
        standing = None
        if job is not None and job.status == "DONE" and job.result:
            result = dict(job.result)
            standing = self._standing(session, result)  # ASSIST2 (SAFE-11 / CPL-09)
            if standing["status"] == "outdated":
                result = {**resolution.result, "answer": None, "root_cause": "DRAFT_OUTDATED", "confidence_level": None,
                          "explanation": OUTDATED[standing["reason"]].format(company=standing.get("company") or "that company")}
        elif job is not None and job.status == "FAILED":
            result = {**result, "answer": None, "root_cause": (job.error or {}).get("code") or "ANSWER_FAILED",
                      "reason": (job.error or {}).get("message", "")}
        elif blocked:
            result = {**result, "answer": None, "root_cause": blocked["code"], "reason": blocked["message"]}
        elif local is not None and local.status == "DONE" and local.result:
            result = dict(local.result)  # a local model finished the control: its answer stands in for the engine's (cites its facts)
        question, question_id = stored.question, stored.question_id
        out = self._base(env, frame or Frame(stored.frame_key, "", session.url, []), element, question, question_id)
        peers = frame.elements if frame is not None else None
        protected = is_protected(question, element, result, peers, env.job)
        scope = stored.scope
        signature = result["canonical_intent"] in {"IDENTITY", "OTHER", "OTHER_PARTY"} and signature_field(question, element, peers, env.job)
        flags = {"protected": protected, "legal": bool(scope and scope.legal) or signature, "leave_blank": result["leave_blank"],
                 "requires_review": False, "conditional": (resolution.condition or None),
                 # somebody else's details are never "your" profile field, whatever the label alone ('Name') says
                 "reusable": bool(scope and scope.reusable and not protected
                                  and result["canonical_intent"] not in NO_PROFILE_INTENTS and not history_block(element))}
        if len(stored.members) > 1:
            flags["members"] = stored.members
        secret = never_saved_reason(question, element)  # ASSIST2 (SAFE-14): a code, a card, an ID: no "save this answer?" offer
        if secret:
            flags["never_saved"] = secret
        working = (job is not None and job.status in ACTIVE) or (local is not None and local.status in LOCAL_ACTIVE)
        state = "GENERATING" if working else decide(result)
        value, problem = None, None
        if state in {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}:
            value, problem = build_value(element, field_el, result)
            if value is None:
                state, result = "NEEDS_YOU", {**result, "answer": None, "root_cause": {
                    "no_matching_option": "BAD_ENUM_MAPPING", "format_error": "ANSWER_FORMAT_ERROR"}.get(problem, "MISSING_FACT"),
                    "candidate_value": result.get("answer")}
        insert = "never"
        if state in {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}:
            insert = "tab"
            if protected:
                insert = "tab" if (ext["tab_inserts_protected"] and state == "VERIFIED") else "explicit"
            if signature:
                insert = "explicit"  # never on a Tab, whatever the protected-Tab switch says: the user's own Enter or click
            if state == "GENERATED_DRAFT" and (result.get("quality") or {}).get("independent_review") == "disabled":
                insert = "explicit"  # only the deterministic checks ran (the user turned the model review off): never on a Tab
            if result.get("insert_explicit"):
                insert = "explicit"  # a local model read the answer into the facts: only the person's own Enter or click takes it
        out.update(state=state, insert=insert, suggestion=value, flags=flags)
        out["flags"]["requires_review"] = state in {"INFERRED", "GENERATED_DRAFT"}
        if result.get("insert_explicit") and not protected and not signature and state == "INFERRED":
            out["flags"]["read_in"] = True  # a model read the option into the facts (not a protected answer): amber, taken with Enter
        out["why"] = self._why(env, state, result, element, resolution, ext, job)
        if signature and state in {"VERIFIED", "INFERRED"}:
            out["why"]["summary"] = "Your name typed as a signature: a legal act, so Meridian inserts it only when you press Enter"
        if state in {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}:
            out["confidence"] = {"value": round(float(result.get("confidence") or 0), 2), "level": result["confidence_level"]}
        if state == "NEEDS_YOU":
            out["needs"] = self._needs(session, env, gen, stored, result, ext, blocked)
        if local is not None and local.status in LOCAL_ACTIVE:
            # only while it works: a finished local answer is an ordinary answer (it is not a draft and has none of a draft's controls)
            out["generation"] = {"id": local.id, "status": "GENERATING", "attempt": 1, "cache_hit": False, "kind": "local"}
        if job is not None:
            out["generation"] = self._generation_info(job, gen)
            out["based_on"] = job.result.get("based_on") if job.result else self._based_on(session)
            if standing is not None and standing["status"] != "fresh" and out["based_on"] is not None:
                # the draft says what it was written from, and that it no longer matches (the panel offers a rewrite)
                out["based_on"] = {**out["based_on"], "stale": True, "reason": standing["reason"]}
        return out

    def _standing(self, session, result: dict) -> dict:
        """ASSIST2: does a finished draft still hold? (``draft_basis.verdict``: the facts it cites, the company it is about,
        the research it was written from). No model call."""
        run = session.research_run_id
        known = bool(self.research and run and self.research.exists(run))
        return draft_basis.verdict(
            self.db, result.get("basis"), bundle=self.research.bundle_hash(run) if known else "",
            bundle_settled=bool(known and self.research.settled(run)),
            rejected=getattr(session, "rejected_companies", frozenset()))

    def _based_on(self, session) -> dict:
        snapshot = self.research.snapshot(session.research_run_id) if self.research and session.research_run_id and \
            self.research.exists(session.research_run_id) else None
        return {"job_description": bool(snapshot and snapshot["role"] and snapshot["role"]["description_chars"]),
                "company_pages": len([s for s in (snapshot or {}).get("sources", []) if s["status"] == "ok"
                                      and s["kind"].startswith("company_")]),
                "research_state": (snapshot or {}).get("state", "none")}

    def _generation_info(self, job: Job, gen: dict) -> dict:
        result = job.result or {}
        info = {"id": job.id, "status": job.status, "attempt": job.attempt, "cache_hit": job.cache_hit}
        if job.status == "DONE":
            quality = result.get("quality") or {}
            info.update(word_count=int(result.get("word_count") or _words(str(result.get("answer") or ""))),
                        provider=str(result.get("provider") or ""), local=bool(gen.get("local")),
                        cost=round(float(job.cost), 6), requires_review=True, quality_passed=bool(quality.get("passed")),
                        company_claims=[{"text": str(c.get("text", ""))[:400], "source_urls": list(c.get("source_urls", []))[:4]}
                                        for c in (result.get("company_claims") or []) if isinstance(c, dict)][:8],
                        claims=[{"text": str(c.get("text", ""))[:400], "fact_ids": list(c.get("fact_ids", []))[:8]}
                                for c in (result.get("claims") or []) if isinstance(c, dict)][:12])
        return info

    def _why(self, env, state, result, element, resolution, ext, job) -> dict:
        facts = [label for label in (env.fact_label(fid) for fid in result.get("fact_ids", [])[:8]) if label]
        why = {"intent": result["canonical_intent"], "rules": [str(r)[:120] for r in (result.get("rules_used") or [])[:6]],
               "facts": facts, "sensitivity": result.get("sensitivity")}
        saved = bool(set(result.get("fact_ids", [])) & {f["id"] for f in env.facts if f.get("profile_explicit")}) or \
            result.get("source") == "user_edit"
        if state == "VERIFIED":
            level = result["confidence_level"]
            code = "policy" if level == "POLICY" else "saved_answer" if saved else "profile_fact"
            summary = {"policy": "From your saved preference", "saved_answer": "From an answer you saved in Meridian",
                       "profile_fact": "From your verified profile"}[code]
            if level == "POLICY" and result.get("reason") and " " in str(result["reason"]):
                summary = str(result["reason"])[:200]
        elif state == "INFERRED":
            code = "derived"
            reason = str(result.get("reason") or "")
            summary = reason[:200] if " " in reason else "Worked out from your verified facts; please check it"
        elif state == "GENERATED_DRAFT":
            code, summary = "generated", "Drafted from your verified profile and this job"
            why["sources"] = list(result.get("sources") or [])[:8]
        elif state == "CONSENT":
            code, summary = "consent", "Legal or consent question: you decide."
        elif state == "SENSITIVE_BLANK":
            if result.get("source") == "user_edit":
                code, summary = "saved_answer", "You chose to leave this blank"
            else:
                code, summary = "sensitive_blank", "Your personal-disclosure policy leaves this blank."
                if element.get("required"):
                    summary += " The employer marks it required; you decide."
        elif state == "GENERATING":
            code, summary = "generated", "Writing a draft from your verified profile and this job"
        else:
            code = cause_group(result)
            summary = MESSAGES[code] if code in MESSAGES else MESSAGES["missing_fact"]
            if code == "setup_required" and result.get("reason") and " " in str(result["reason"]):
                summary = str(result["reason"])[:200]
            if result.get("explanation"):  # the engine said precisely why it did not answer (a salary in another currency...)
                summary = str(result["explanation"])[:200]
        why.update(code=code, summary=summary)
        return {k: why[k] for k in ("code", "summary", "intent", "rules", "facts", "sensitivity") if k in why} | (
            {"sources": why["sources"]} if "sources" in why else {})

    def _needs(self, session, env, gen, stored: StoredField, result: dict, ext: dict, blocked) -> dict:
        resolution, scope = stored.resolution, stored.scope
        code = cause_group(result)
        actions: list[dict] = []
        # Somebody else's details are not a Profile field of the applicant and no model may invent them: a bare 'Name' under
        # 'Emergency contact' must not send the user to their own full name, nor offer a draft
        stranger = result.get("canonical_intent") in NO_PROFILE_INTENTS
        spec = None if stranger else question_field(stored.question, env.application, resolution.field)
        can_write = can_draft(stored.question, resolution.element) and not (scope and (scope.protected or scope.legal)) \
            and not stranger and result.get("root_cause") != "UNTRUSTED_INSTRUCTION_IN_QUESTION"
        if code in {"setup_required", "budget"}:
            actions.append({"type": "open_meridian"})
        elif code == "missing_candidate_fact":
            actions.extend([{"type": "profile", **({"key": spec["key"]} if spec else {})}, {"type": "answer_here"}])
        else:
            if code in {"missing_fact", "no_matching_option"} and spec:
                actions.append({"type": "profile", "key": spec["key"]})
            actions.append({"type": "answer_here"})
            if can_write and gen.get("available") and code in {"missing_fact", "model_error", "validation_failed",
                                                              "draft_outdated"}:
                actions.append({"type": "draft_with_ai"})
        message = MESSAGES.get(code, MESSAGES["missing_fact"])
        if result.get("explanation"):
            message = str(result["explanation"])[:240]
        boolean_box = resolution.element["role"] in {"checkbox", "switch"} and not resolution.element["multiple"]
        if boolean_box and code == "format_error":
            message = "This is a yes/no box and Meridian has no verified yes/no answer for it."
            result = {**result, "candidate_value": None}
        if blocked and blocked.get("code") == "draft_limit":
            message = blocked["message"]
            code = "missing_fact"
        elif code in {"setup_required", "budget"} and result.get("reason") and " " in str(result["reason"]):
            message = str(result["reason"])[:240]
        needs = {"root_cause": str(result.get("root_cause") or "MISSING_FACT"), "code": code, "message": message,
                 "actions": actions}
        if result.get("candidate_value") is not None:
            needs["candidate_value"] = str(result["candidate_value"])[:400]
        if result.get("missing_facts"):
            needs["missing"] = [str(m)[:160] for m in result["missing_facts"][:6]]
        return needs

    # ------------------------------------------------------------------------------- regenerate
    async def regenerate(self, session, request: RegenerateRequest) -> dict:
        ext = self.extension_settings()
        env = self.environment(session, ext)
        gen = self.generation_status(fresh=True)
        stored = self._locate(session, request.question_id, request.node_id, request.frame_key)
        if stored is None and request.element is not None:
            stored = await self._adopt(session, env, request)
        if stored is None:
            return None
        if not stored.resolution.result:  # a skipped / not-applicable control: nothing was ever resolved to rewrite
            return stored.suggestion
        frame = session.frames[stored.frame_key]
        resolver = FieldResolver(env, url=frame.url, ats=session.ats, scope=session.id, frame_key=frame.key,
                                 document_id=frame.document_id, elements=frame.elements)
        element = stored.resolution.element
        if not can_draft(stored.question, element) or (stored.scope and (stored.scope.protected or stored.scope.legal)):
            blocked = {"code": "MISSING_FACT", "message": "Meridian does not write this kind of answer for you."}
            job = None
        else:
            instruction = (request.instruction or "again").strip()
            job, blocked = self._draft(session, env, ext, gen, resolver, frame, stored, explicit=True,
                                       instruction=instruction, current_text=request.current_text)
        suggestion = self.build(session, env, ext, gen, stored, job=job, blocked=blocked)
        stored.suggestion, stored.state = suggestion, suggestion["state"]
        return suggestion

    def _locate(self, session, question_id: str, node_id: int | None, frame_key: str) -> StoredField | None:
        if node_id is not None:
            found = session.fields.get(f"{frame_key}:{node_id}")
            if found is not None:
                return found
        return next((f for f in session.fields.values() if f.question_id == question_id), None)

    async def _adopt(self, session, env, request: RegenerateRequest) -> StoredField | None:
        """A short field the page scanned but the server never stored (explicit 'Draft with AI')."""
        frame = session.frames.get(request.frame_key)
        if frame is None:
            return None
        from .resolver import prepare_elements as prepare
        element = request.element
        elements = [dict(e) for e in frame.elements]
        match = next((e for e in elements if e["node_id"] == element.node_id), None)
        if match is None:
            match = prepare([element], frame.document_id, frame.url)[0]
            match["index"] = len(elements)
            elements.append(match)
            frame.elements = elements
        resolver = FieldResolver(env, url=frame.url, ats=session.ats, scope=session.id, frame_key=frame.key,
                                 document_id=frame.document_id, elements=frame.elements)
        question = resolver.question(match)
        resolution = await resolver.resolve(match)
        scope = scope_of(question, resolution.field, resolution.result["canonical_intent"], env.application)
        stored = StoredField(f"{frame.key}:{match['node_id']}", frame.key, match["index"], match["node_id"],
                             [match["node_id"]], (0, match["index"]), resolution, question, resolution.question_id, scope)
        session.fields[stored.key] = stored
        return stored
