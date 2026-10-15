"""Page sessions of the browser assistant: identity, events, lazy persistence, feedback and tracking (design 5.1-5.8).

A *session* is the assistant's local state for one page of one paired browser: ``extension_sessions`` holds only
company/title/host (no page text, no typed values); everything else (frames, fields, drafts, the event cursor) lives in
memory and is rebuilt by the extension when it re-scans. **Browsing creates no job and no application.** Rows are made
lazily by :meth:`SessionRegistry.ensure_application` on the first persisting event (a saved answer or an explicit
"record"), never through ``prepare_application``: no human task, no notification, no draft CV, no browser run, and an
application that is already submitted is never reopened.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import Counter, OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone

from ..automation.answers import normalize
from ..db import now, uid
from ..discovery import canonical_url, ingest_job, normalize_job
from ..tracking import SUBMITTED, record_application
from .auth import BridgeError, Client
from .feedback import field_hash, persist, plan, record_event, record_events
from .generation import GenerationQueue
from . import documents_pick
from .research import ASSISTED_NOTE, ResearchManager, normalize_report, tail_key
from .resolver import data_token
from .schemas import (FeedbackRequest, PollManyRequest, PollRequest, RegenerateRequest, SessionCreateRequest,
                      SuggestRequest, TrackRequest)
from .suggest import MAX_FRAMES, SuggestService, approved_documents, document_content

logger = logging.getLogger("meridian.extension")
MAX_EVENTS = 500
MAX_SESSIONS_PER_CLIENT = 48  # pages one browser keeps (it polls 16 per request): beyond that ITS least recently used go
MAX_SESSIONS = 96  # the global ceiling (memory): when it is reached the browser holding the most pages gives one up
MAX_BOOKKEEPING = 4000  # entries of the per-page "already audited / already counted" sets
IDLE_SECONDS = 6 * 3600
RELEASE_GRACE = 20.0
POLL_TICK = 2.0
TOKEN_TTL = 0.25  # one profile-fingerprint read serves every waiting poll for this long
QUICK_POLL_MS = 250  # a poll asking to wait this little is an "anything now?" check: it never takes a page over
ABANDON_SECONDS = 600.0  # a page that neither polled nor asked for this long is gone (a visible page polls every <= 25 s)
JANITOR_TICK = 30.0
RESEARCH_STATE = {"queued": "queued", "needs_input": "queued", "running": "running", "done": "ready",
                  "partial": "ready", "failed": "failed", "cancelled": "failed"}
AUDIT_OUTCOME = {"VERIFIED": "answered", "GENERATED_DRAFT": "answered", "INFERRED": "needs_review",
                 "NEEDS_YOU": "unknown", "CONSENT": "sensitive_blocked", "SENSITIVE_BLANK": "sensitive_blocked"}
GENERATION_KEYS = ("available", "tested", "provider", "local", "message", "monthly_limit_usd", "spent_usd")
# Job rows whose ``source`` is not a discovery claim: the user's own import, or a row this assistant created itself.
NOT_DISCOVERY = {"", "manual", "unknown", "extension", "browser"}


def discovery_source(row: dict | None) -> str:
    """How Meridian itself found this posting (a feed, a board, an email alert), from the stored job row; '' when it
    did not. The assistant never claims a discovery source of its own: 'How did you hear about this job?' is answered
    only from what Meridian really knows."""
    source = str((row or {}).get("source") or "").strip()
    return "" if source.casefold() in NOT_DISCOVERY else source[:200]


def event_key(event: dict):
    """Events where only the newest one matters: a research snapshot replaces the previous one (it carries the whole
    state and the job text), so a long run cannot fill the 500-event window with copies of it."""
    return "research" if event.get("type") == "research" else None


class PollSlot:
    """One outstanding long-poll of one browser: a single page's, or the multiplexed one for all of its pages. A session
    is covered by at most one slot (a newer poll takes the session over); ``sids`` maps each session to None while it is
    covered, else to why it no longer is ('superseded', 'released', 'expired')."""

    __slots__ = ("sids", "wake")

    def __init__(self) -> None:
        self.sids: dict[str, str | None] = {}
        self.wake = asyncio.Event()

    def drop(self, sid: str, why: str) -> None:
        if self.sids.get(sid, why) is None:
            self.sids[sid] = why
            self.wake.set()


class _AtEpoch:
    """The session as it was while an earlier basis of its drafts was current (the draft cache key reads ``epoch``)."""

    def __init__(self, session: "Session", epoch: int):
        self.epoch, self._session = epoch, session

    def job_digest(self) -> str:
        return self._session.job_digest()


class Session:
    """One page of one browser. All mutation happens on the event loop."""

    def __init__(self, registry: "SessionRegistry", sid: str, client: Client, report: dict, canonical: str):
        self.registry, self.id = registry, sid
        self.client_id, self.browser = client.id, client.browser
        self.report, self.url, self.canonical, self.host = report, report["url_clean"], canonical, report["host"]
        self.ats = report["ats"].get("ats") if report["ats"].get("ats") not in {None, "generic"} else None
        self.tab_key = ""
        self.company = self.title = self.location = ""
        self.country = ""  # the country the job is in, as research read it ('' = unknown or several)
        self.countries: list[str] = []
        self.discovery_source = ""  # from the stored job row only (never 'extension')
        self.user: dict = {}  # the user's own research corrections ({company, site}): they outlive reloads and re-runs
        self.research_run_id: str | None = None
        self.research_state = "none"
        self.job_id: str | None = None
        self.application_id: str | None = None
        self.tracked = "none"
        self.status = ""  # the application's own status ('' = no application): 'applied' covers REJECTED and WITHDRAWN too
        self.since = ""  # when a SENT application was sent ('' = not sent or unknown)
        self.frames: dict = {}
        self.fields: dict = {}
        self.events: list[tuple[int, dict]] = []
        self.cursor = 0
        self.lost = 0  # events up to this number were dropped to bound memory (a client behind it must re-scan)
        self.poll: PollSlot | None = None
        self.dead = False  # dropped from the registry: nothing more may be started for it
        self.frame_locks: dict[str, asyncio.Lock] = {}
        self.epoch = 0
        self.frozen: dict | None = None
        self.regen: dict = {}
        self.drafts: dict = {}  # draft base -> job keys, oldest first
        self.draft_spend = 0.0
        self.counted: set = set()
        self.known_version = ""
        self.last_token = ""
        self.audit_seen: set = set()
        self.touched = time.monotonic()
        self.release_handle = None
        self.persisted_state = ""
        self.stop_seen: str | None = None
        self.limit_seen: str | None = None
        self.rejected_companies: set[str] = set()  # companies the user said this page is NOT about (SAFE-11 / CPL-09)
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------------------- events
    def emit(self, event: dict) -> None:
        self.cursor += 1
        key = event_key(event)
        if key is not None:
            self.events = [(n, e) for n, e in self.events if event_key(e) != key]
        self.events.append((self.cursor, event))
        if len(self.events) > MAX_EVENTS:
            extra = len(self.events) - MAX_EVENTS
            # The research strip is only ever updated by research snapshots: a client that fell behind the window is told
            # to re-scan, but a re-scan does not bring the snapshot back, so the newest one is never trimmed away (the
            # window then drops the oldest OTHER events instead, and stays exactly ``MAX_EVENTS`` long).
            snapshot = next((item for item in reversed(self.events) if event_key(item[1]) == "research"), None)
            if snapshot is not None and any(item is snapshot for item in self.events[:extra]):
                others = [item for item in self.events if item is not snapshot]
                self.lost = others[extra - 1][0]
                self.events = [snapshot, *others[extra:]]
            else:
                self.lost = self.events[extra - 1][0]
                del self.events[:extra]
        if self.poll is not None:
            self.poll.wake.set()

    def frame_lock(self, frame_key: str) -> asyncio.Lock:
        """Requests for ONE frame are served one at a time (a scan and a refresh arriving together would otherwise
        interleave their writes to the frame and to the stored fields)."""
        lock = self.frame_locks.get(frame_key)
        if lock is None:
            if len(self.frame_locks) >= 2 * MAX_FRAMES:  # locks of frames that came and went (and are not being waited on)
                for key in [k for k, held in self.frame_locks.items() if not held.locked() and k not in self.frames]:
                    del self.frame_locks[key]
            lock = self.frame_locks[frame_key] = asyncio.Lock()
        return lock

    def touch(self) -> None:
        self.touched = time.monotonic()

    # ------------------------------------------------------------------------------- tracking
    def tracking_mark(self) -> tuple:
        """What the page was last told about the application. ``tracked`` alone is not enough: a rejected application is still
        'applied', yet the banner words it differently, so a sent application's status and date are part of the mark."""
        return (self.tracked, self.status if self.tracked == "applied" else "", self.since)

    def tracked_event(self) -> dict:
        event = {"type": "tracked", "tracked": self.tracked}
        if self.status:
            event["status"] = self.status
        if self.since:
            event["since"] = self.since
        return event

    # ------------------------------------------------------------------------------- drafts
    def job_digest(self) -> str:
        """Identity of the posting for the draft cache key: the page itself, never what research learns about it later
        (a company or title that firms up must not make a second paid draft of the same question)."""
        return hashlib.sha256(json.dumps([self.report["job_key"]]).encode()).hexdigest()

    def freeze(self, provider) -> dict:
        """The job context of every draft of this page: read once (after research had its 8 s) and kept, so later
        research can never silently change a draft or the cache key of another one."""
        if self.frozen is None:
            self.frozen = provider()
        return self.frozen

    def refresh_drafts(self) -> None:
        self.epoch += 1
        self.frozen = None

    def drafts_this_field(self, service: SuggestService, key: str, job) -> bool:
        """Is ``job`` a draft of the question the field holds NOW? A page that shows the same question under other
        constraints (a limit that changes, a follow-up that appears and goes) asks for another draft; when the old one
        finishes late its progress is not the field's, and painting it over what the field shows now would be a wrong
        answer. Revisions of one question (regenerate) share its base and all count, and so do drafts begun under an
        earlier basis of the research (the user corrected the company meanwhile): that changes what FUTURE requests
        draft from, never which question the field is asking."""
        stored = self.fields.get(key)
        if stored is None:
            return True  # nothing to paint over: ``rebuild`` finds no field either
        keys: set = set()
        for epoch in range(self.epoch + 1):
            base = service._draft_base(_AtEpoch(self, epoch), stored)
            keys |= {*self.drafts.get(base, ()), service._draft_key(base, "", 0)}
        return job.key in keys

    def job_changed(self, service: SuggestService, key: str, job) -> None:
        suggestion = None
        try:
            current = self.drafts_this_field(service, key, job)
        except Exception as exc:  # when in doubt the field hears about its draft: never leave it pulsing for ever
            logger.warning("extension_draft_event_failed %s", type(exc).__name__)
            current = True
        try:
            if current:
                suggestion = service.rebuild(self, key, job)
        except Exception as exc:  # never leave a field pulsing forever because a rebuild failed
            logger.warning("extension_draft_event_failed %s", type(exc).__name__)
        if suggestion is not None:
            self.emit({"type": "suggestion", "suggestion": suggestion})
        if job.status in {"DONE", "FAILED"} and job.id not in self.counted:
            if len(self.counted) >= MAX_BOOKKEEPING:
                self.counted.clear()
            self.counted.add(job.id)
            self.draft_spend += float(job.cost or 0)
            stored = self.fields.get(key)
            element = stored.resolution.element if stored else {}
            outcome = "answered" if job.status == "DONE" else "unknown"
            code = (job.error or {}).get("code", "") if job.status == "FAILED" else ""
            kind = "draft_ready" if job.status == "DONE" else "error"
            self.registry.audit(self, kind, element, outcome, {"status": job.status, "cache_hit": job.cache_hit,
                                                               "cost": round(float(job.cost or 0), 6), "code": code,
                                                               "message": (job.error or {}).get("message", "")[:160]})
            self.registry.persist(self)

    def local_changed(self, service: SuggestService, key: str, job) -> None:
        """A local-model job of a field started, finished or found nothing: the field says so (a draft's bookkeeping does not apply)."""
        try:
            suggestion = service.rebuild_local(self, key, job)
        except Exception as exc:  # never leave a field pulsing forever because a rebuild failed
            logger.warning("extension_local_event_failed %s", type(exc).__name__)
            return
        if suggestion is not None:
            self.emit({"type": "suggestion", "suggestion": suggestion})

    def on_generation_stop(self, code: str, message: str) -> None:
        if self.stop_seen == code:
            return
        self.stop_seen = code
        self.emit({"type": "generation_budget", "message": message, "code": code})

    def on_draft_limit(self, code: str, message: str) -> None:
        """This page used up the drafts Meridian writes on its own (SEC-06): said once, as its own event (it is NOT a budget
        stop: drafts the user asks for still work, so the page must not mark writing as unavailable)."""
        if self.limit_seen == code:
            return
        self.limit_seen = code
        self.emit({"type": "drafts_paused", "message": message, "code": code})

    def reassess_drafts(self, service: SuggestService) -> None:
        """Judge every finished draft of this page again against the profile and the research as they are NOW, and announce
        the ones whose standing changed (a company the user rejected, research that learned more, a corrected fact): the
        page replaces what it shows, so nothing outdated stays Tab-insertable (SAFE-11 / CPL-09). Never calls a model."""
        for key, stored in list(self.fields.items()):
            if not stored.draft_key:
                continue
            job = service.generation.get(stored.draft_key)
            if job is None or job.status != "DONE":
                continue
            before = self._standing_mark(stored.suggestion)
            try:
                suggestion = service.rebuild(self, key, job)
            except Exception as exc:  # one broken field must not hide the others
                logger.warning("extension_draft_reassess_failed %s", type(exc).__name__)
                continue
            if suggestion is not None and self._standing_mark(suggestion) != before:
                self.emit({"type": "suggestion", "suggestion": suggestion})

    @staticmethod
    def _standing_mark(suggestion: dict | None) -> tuple:
        based = (suggestion or {}).get("based_on") or {}
        return (suggestion or {}).get("state"), bool(based.get("stale")), based.get("reason")

    # ------------------------------------------------------------------------------- research
    def research_listener(self, snapshot: dict) -> None:
        self.registry.apply_research(self, snapshot)
        self.emit({"type": "research", "research": snapshot})
        if snapshot.get("state") in {"done", "partial", "failed"}:
            # research learned more: a draft written from an older bundle says so (it is never rewritten silently)
            self.reassess_drafts(self.registry.service)

    async def audit_suggestions(self, results: list[dict], elements: list[dict], frame_key: str) -> None:
        by_node = {e["node_id"]: e for e in elements}
        rows = []
        for suggestion in results:
            state = suggestion["state"]
            if state not in AUDIT_OUTCOME:
                continue
            element = by_node.get(suggestion["node_id"], {})
            mark = (field_hash(element), state)
            if mark in self.audit_seen:
                continue
            if len(self.audit_seen) >= MAX_BOOKKEEPING:
                self.audit_seen.clear()  # a very long-lived page: at worst one line is audited twice
            self.audit_seen.add(mark)
            kind = "policy_block" if state in {"CONSENT", "SENSITIVE_BLANK"} else "suggest"
            rows.append((kind, element, AUDIT_OUTCOME[state],
                         {"state": state, "code": (suggestion.get("why") or {}).get("code", "")}))
        if rows:  # one transaction in a worker thread: waiting for the database's writer never stalls the event loop
            await asyncio.to_thread(self.registry.audit_many, self, rows)


def application_row(db, application_id: str) -> dict | None:
    return db.one("SELECT a.*,j.company,j.location,j.title,j.url,j.application_url,j.ats,j.description FROM applications a "
                  "JOIN jobs j ON a.job_id=j.id WHERE a.id=?", (application_id,))


class _SeedingDatabase:
    """The database ``ingest_job`` writes through: ``db`` itself, except that the FIRST transaction (the one in which the job
    row is written) also writes the job's notification receipt and its bare application before it commits.

    Why: the receipt (``match:<job_id>``) is what keeps the daily "new strong matches" notice from announcing a job the
    user is already applying to, but the job id is only born inside ``ingest_job``'s own transaction, so a receipt written
    afterwards leaves a window (and, if that write fails, a job that stays without one for good) in which the notice
    scheduler can see the job. Here the job, its receipt and its application are ONE commit: either all exist or none.

    ``ingest_job`` finds an existing row by ``canonical_url OR identity_key``; this asks the same question, with the same
    SQL, before the commit. A job that ingestion matched by another route (an identical cross-post) is not found, and
    :func:`ensure_extension_application` completes it afterwards (an existing job is not a new one: nothing was created).
    Everything else (``query``, ``one``, ``execute`` ...) is the real database's, so no later write joins this one."""

    def __init__(self, db, raw: dict):
        job = normalize_job({**raw, "description": ""})  # (the address and the identity do not depend on the text)
        self._db, self._keys, self._armed = db, (job["canonical_url"], job["identity_key"]), True
        self.application_id: str | None = None

    def __getattr__(self, name):
        return getattr(self._db, name)

    @contextmanager
    def transaction(self):
        with self._db.transaction() as conn:
            yield conn
            if self._armed:  # the body of ingest_job finished without an error: complete it before the commit
                self._armed = False
                row = conn.execute("SELECT id FROM jobs WHERE canonical_url=? OR identity_key=?", self._keys).fetchone()
                if row is not None:
                    self.application_id = seed_application(conn, row["id"])


def seed_application(conn, job_id: str) -> str:
    """Inside the caller's transaction: the job's notification receipt and its bare application (found or created).
    Returns the application id. Idempotent: an existing application, even a submitted one, is left exactly as it is."""
    conn.execute("INSERT OR IGNORE INTO notification_receipts(key,created_at) VALUES(?,?)", ("match:" + job_id, now()))
    existing = conn.execute("SELECT id FROM applications WHERE job_id=?", (job_id,)).fetchone()
    if existing is not None:
        return existing["id"]
    stamp, identifier = now(), uid()
    # Mode 'review', the mode Prepare creates: a row in 'manual' made "Prepare application" answer with a mode the user never
    # chose and Auto Apply refuse to start ("Manual mode prepares documents only"). 'review' fills nothing by itself: Meridian
    # only opens and fills a form when the user presses Apply, and the assistant never submits (INT-01).
    conn.execute("INSERT INTO applications(id,job_id,mode,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                 (identifier, job_id, "review", "PREPARING",
                  "Assisted by the browser extension; submission is always manual", stamp, stamp))
    conn.execute("INSERT INTO application_events VALUES(?,?,?,?,?,?)",
                 (uid(), identifier, "PREPARING", "Assisted by the browser extension", "extension", stamp))
    return identifier


def ensure_extension_application(db, *, url: str, application_url: str, title: str, company: str, location: str = "",
                                 description: str = "", ats: str = "generic") -> dict:
    """Create (or find) the job and a BARE application row for a page the user is applying to.

    Deliberately NOT ``prepare_application``: no human task, no notification, no draft CV, no browser run, no
    ``SHORTLISTED`` status. The job is ingested with ``source='extension'`` and a ``match:<job_id>`` notification
    receipt is seeded so the daily "new strong matches" notice never fires for it. The job, the receipt and the
    application are written in ONE transaction (see :class:`_SeedingDatabase`): there is no moment at which the job
    exists without its receipt, and a failure anywhere leaves nothing behind. An existing application (including one
    that is already submitted) is returned untouched: the assistant never reopens or restarts it.

    **A page is matched to a job by its ADDRESS only** (the canonical URL or the ATS identity), never by its text (SEC-09).
    The company, title, location and description of a page are what the page SAYS; a cloned posting that copied them must
    not be merged into a real job (its answers would land in the real application, its address would become an alias of
    the real job). So an existing job is left exactly as it is (no merge of the page's text, no alias), and a page that is
    not a known job gets a job of its own, whatever other jobs read the same: ingestion is given NO text to match on, and
    the text is stored afterwards, marked as captured from the page (a later discovery of the real posting must not take it
    for the same advertisement either)."""
    raw = {"url": url, "application_url": application_url, "title": title, "company": company, "location": location,
           "description": "", "source": "extension", "ats": ats}
    keys = normalize_job(raw)
    with db.transaction() as conn:  # a job Meridian already has for this address: only its application is completed
        known = conn.execute("SELECT id FROM jobs WHERE canonical_url=? OR identity_key=?",
                             (keys["canonical_url"], keys["identity_key"])).fetchone()
        identifier = seed_application(conn, known["id"]) if known is not None else None
    if identifier is not None:  # (read after the commit: another connection must see the rows)
        return application_row(db, identifier)
    seeding = _SeedingDatabase(db, raw)
    job, duplicate = ingest_job(seeding, raw)
    identifier = seeding.application_id
    if identifier is None:  # a job that appeared between the check and the ingestion: complete it, nothing is merged into it
        with db.transaction() as conn:
            identifier = seed_application(conn, job["id"])
    text = (description or "")[:250000].strip()
    if text and not duplicate:
        stored = normalize_job({**raw, "description": text + "\n\n" + ASSISTED_NOTE})
        with db.transaction() as conn:
            conn.execute("UPDATE jobs SET description=?,skills=? WHERE id=? AND source='extension'",
                         (stored["description"], json.dumps(stored["skills"]), job["id"]))
            for skill in stored["skills"]:
                conn.execute("INSERT OR IGNORE INTO skills(id,name) VALUES(?,?)", (uid(), skill))
                found = conn.execute("SELECT id FROM skills WHERE name=?", (skill,)).fetchone()
                conn.execute("INSERT OR IGNORE INTO job_skills VALUES(?,?,0)", (job["id"], found["id"]))
    return application_row(db, identifier)


class SessionRegistry:
    """Owner of every session. One per process (``app.state.extension_sessions``)."""

    def __init__(self, app, research: ResearchManager, generation: GenerationQueue):
        self.app, self.research, self.generation = app, research, generation
        self.service = SuggestService(app, research, generation)
        generation.set_guard(self.guard)
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.by_page: dict[tuple[str, str], str] = {}
        self._closed = False
        self._draining = False  # the bridge listener is stopping: no poll waits any more
        self._token_lock = asyncio.Lock()
        self._token_value, self._token_at = "", float("-inf")
        self._janitor_handle = None

    @property
    def db(self):
        return self.app.state.db

    # ------------------------------------------------------------------------------- lifecycle
    async def create(self, client: Client, request: SessionCreateRequest) -> dict:
        try:
            report = normalize_report(request.page.model_dump())
        except ValueError as exc:
            raise BridgeError(422, str(exc), "invalid") from exc
        try:
            canonical = canonical_url(report["posting_url"])
        except ValueError as exc:
            raise BridgeError(422, "This page address cannot be used", "invalid") from exc
        self._sweep()
        self._arm_janitor()
        ext = self.service.extension_settings()
        session = self.sessions.get(self.by_page.get((client.id, canonical), ""))
        if session is not None and session.release_handle is not None:
            session.release_handle.cancel()
            session.release_handle = None
        if session is None:
            self._make_room(client.id)  # only a NEW page needs room: a reload of an open one evicts nobody
            row = self.db.one("SELECT * FROM extension_sessions WHERE client_id=? AND canonical_url=?",
                              (client.id, canonical))
            sid = row["id"] if row else "ses_" + uuid.uuid4().hex[:20]
            session = Session(self, sid, client, report, canonical)
            if row:
                session.job_id, session.application_id = row["job_id"], row["application_id"]
                stored = json.loads(row["metadata"] or "{}")
                session.draft_spend = float(stored.get("draft_spend_usd") or 0)
                session.user = {k: str(v) for k, v in (stored.get("user") or {}).items() if k in {"company", "site"} and v}
            self.sessions[sid] = session
            self.by_page[(client.id, canonical)] = sid
            self.generation.register_session(sid, session.on_generation_stop, client_id=client.id,
                                             on_limit=session.on_draft_limit)
        report["user"] = {**report["user"], **session.user}  # a correction the user made is not forgotten on reload
        session.report, session.url = report, report["url_clean"]
        session.tab_key = request.tab_key
        session.touch()
        self.sessions.move_to_end(session.id)
        snapshot = self.research.start(session.id, report, listener=session.research_listener)
        session.research_run_id = snapshot["run_id"]
        self._link_job(session)
        if not snapshot.get("role"):
            # Research has read nothing yet, but where the job is usually is on the page (JSON-LD) or in the job row:
            # the first suggestions must already know the country (work authorisation, salary, sponsorship).
            self.set_place(session, self.research.local_role_facts(report))
        self.apply_research(session, snapshot)
        env = self.service.environment(session, ext, fresh=True)
        session.known_version, session.last_token = env.version, data_token(self.db)
        self.persist(session, force=True)
        gen = self.service.generation_status()
        return {"session_id": session.id, "protocol": 1, "knowledge_version": env.version,
                "job": self._job_summary(session), "research": snapshot,
                "generation": {k: gen[k] for k in GENERATION_KEYS}, "settings": ext, "cursor": session.cursor}

    def resolve(self, client: Client, sid: str) -> tuple[str, Session | None]:
        """('ok', session) | ('expired', None): this browser's page whose in-memory state is gone (a restart, a drop; the
        stored row proves it was this browser's) | ('unknown', None): never this browser's (or not at all: the two are
        indistinguishable on purpose)."""
        session = self.sessions.get(sid)
        if session is not None and session.client_id == client.id:
            return "ok", session
        row = self.db.one("SELECT client_id FROM extension_sessions WHERE id=?", (sid,))
        return ("expired" if row and row["client_id"] == client.id else "unknown"), None

    def get(self, client: Client, sid: str) -> Session:
        status, session = self.resolve(client, sid)
        if status == "expired":
            raise BridgeError(410, "This page was closed or Meridian restarted; open it again", "expired")
        if status == "unknown" or session is None:
            raise BridgeError(404, "Unknown page session", "not_found")
        session.touch()
        self.sessions.move_to_end(sid)  # least recently used pages are dropped first
        return session

    async def release(self, client: Client, sid: str) -> None:
        """The page was closed or navigated away. Queued work is cancelled and research released after a grace
        period: a reload of the same page re-attaches instead. A poll waiting for the page returns at once."""
        session = self.get(client, sid)
        if session.release_handle is None:
            session.release_handle = asyncio.get_running_loop().call_later(RELEASE_GRACE, self._expire, session)
        if session.poll is not None:
            session.poll.drop(session.id, "released")

    def _expire(self, session: Session) -> None:
        session.release_handle = None
        self._drop(session)

    def _drop(self, session: Session) -> None:
        """Forget a page: its queued drafts are cancelled, its research released, a poll waiting for it is told it is
        gone, and nothing more can be started for it (a request still in flight finds it dead)."""
        session.dead = True
        if session.release_handle is not None:
            session.release_handle.cancel()
            session.release_handle = None
        self.generation.forget_session(session.id)
        self.research.release(session.id)
        self.service.forget(session.id)
        if session.poll is not None:
            session.poll.drop(session.id, "expired")
        if self.sessions.get(session.id) is session:
            self.sessions.pop(session.id, None)
            if self.by_page.get((session.client_id, session.canonical)) == session.id:
                self.by_page.pop((session.client_id, session.canonical), None)

    def _sweep(self) -> None:
        """Pages nobody has touched for hours are forgotten (a browser that vanished never says goodbye)."""
        horizon = time.monotonic() - IDLE_SECONDS
        for session in [s for s in self.sessions.values() if s.touched < horizon]:
            self._drop(session)

    def _make_room(self, client_id: str) -> None:
        """Before a NEW page of ``client_id`` is registered. One browser can never push another's pages out: it is held to
        ``MAX_SESSIONS_PER_CLIENT`` (its own least recently used page goes first), and only when the global ceiling is
        reached does a page leave, from the browser that would then hold the most (the requester counts its new page),
        again its least recently used. The registry is ordered least recently used first."""
        mine = [s for s in self.sessions.values() if s.client_id == client_id]
        while len(mine) >= MAX_SESSIONS_PER_CLIENT:
            self._drop(mine.pop(0))
        while len(self.sessions) >= MAX_SESSIONS:
            size = len(self.sessions)
            held = Counter(s.client_id for s in self.sessions.values())
            held[client_id] += 1
            biggest = max(held.values())
            heaviest = {owner for owner, count in held.items() if count == biggest}
            self._drop(next((s for s in self.sessions.values() if s.client_id in heaviest), next(iter(self.sessions.values()))))
            if len(self.sessions) >= size:  # cannot happen (a dropped page leaves the registry); never loop on it
                break

    def _arm_janitor(self) -> None:
        if self._janitor_handle is None and not self._closed:
            self._janitor_handle = asyncio.get_running_loop().call_later(JANITOR_TICK, self._janitor)

    def _janitor(self) -> None:
        """Pages whose browser vanished (a crash, a discarded tab, an uninstalled extension) never say goodbye. A visible
        page polls at least every 25 s; a hidden tab may be quiet for a while (its drafts keep being written, so they
        are ready when the user comes back), but one silent for ten minutes is gone: its queued drafts must not keep
        running (and spending) for nobody. A page that comes back finds ``expired`` and opens its session again."""
        self._janitor_handle = None
        horizon = time.monotonic() - ABANDON_SECONDS
        for session in [s for s in self.sessions.values() if s.touched < horizon]:
            self._drop(session)
        if self.sessions:
            self._arm_janitor()

    def begin_shutdown(self) -> None:
        """The bridge listener is about to stop. Every waiting poll is answered at once (empty: its page asks again and finds
        the listener gone) and no new poll waits, so the listener's own graceful stop has nothing left to wait for (it
        used to hold the quit for its whole timeout and cancel the polls with an error). Sessions are not touched: the
        listener may come back (``BridgeServer.restart``), see :meth:`end_shutdown`."""
        self._draining = True
        for slot in {id(s.poll): s.poll for s in self.sessions.values() if s.poll is not None}.values():
            slot.wake.set()

    def end_shutdown(self) -> None:
        self._draining = False

    async def close(self) -> None:
        self._closed = True
        if self._janitor_handle is not None:
            self._janitor_handle.cancel()
            self._janitor_handle = None
        for session in list(self.sessions.values()):
            if session.release_handle:
                session.release_handle.cancel()
            if session.poll is not None:
                session.poll.drop(session.id, "released")
        self.sessions.clear()
        self.by_page.clear()

    # ------------------------------------------------------------------------------- state sync
    def apply_research(self, session: Session, snapshot: dict) -> None:
        """Company / title / location as the research understood them; mirrored to the session row."""
        company = (snapshot.get("company") or {}).get("name")
        role = snapshot.get("role") or {}
        session.company = company or session.company
        session.title = role.get("title") or session.title or session.report["h1"] or session.report["title"][:200]
        if role.get("location") or role.get("country"):
            self.set_place(session, role)
        session.research_state = RESEARCH_STATE.get(snapshot["state"], "running")
        if session.persisted_state != session.research_state or snapshot["state"] in {"done", "partial", "failed"}:
            self.persist(session)

    def set_place(self, session: Session, facts: dict) -> None:
        """Where the job is, as research (or the page itself) read it. Answers depend on the COUNTRY (work authorisation,
        sponsorship, salary): when it changes after suggestions were served, the page is told to ask again."""
        before = (session.country, tuple(session.countries))
        if facts.get("location") or facts.get("country"):
            session.location = str(facts.get("location") or "")
            session.country = str(facts.get("country") or "")
            session.countries = list(facts.get("countries") or [])
        if before != (session.country, tuple(session.countries)) and session.known_version:
            env = self.service.environment(session, None, fresh=True)
            if env.version != session.known_version:
                session.known_version = env.version
                session.emit({"type": "knowledge", "knowledge_version": env.version})

    def _link_job(self, session: Session) -> None:
        """Tie the page to an existing job/application (found by URL), never creating one."""
        row = self.research.job_row(session.report)
        if row is not None:
            session.job_id = row["id"]
        session.discovery_source = discovery_source(row)
        self._refresh_tracking(session)

    def _refresh_tracking(self, session: Session) -> tuple:
        """Read the application of this page's job. Returns the mark the page was last told (``Session.tracking_mark``)."""
        previous = session.tracking_mark()
        status = since = ""
        if session.job_id:
            application = self.db.one("SELECT id,status,created_at FROM applications WHERE job_id=?", (session.job_id,))
            if application is None:
                session.tracked = "prospective"
            else:
                session.application_id = application["id"]
                status = application["status"]
                session.tracked = "applied" if status in SUBMITTED else "preparing"
                if session.tracked == "applied":
                    since = self._sent_on(application)
        else:
            session.tracked = "none"
        session.status, session.since = status, since
        return previous

    def _sent_on(self, application: dict) -> str:
        """When the application was SENT (never the day it was rejected or withdrawn): the first event that says it was sent,
        else the day it entered the tracker (an application imported in a later status has no earlier line)."""
        marks = ",".join("?" * len(SUBMITTED))
        row = self.db.one(f"SELECT MIN(created_at) AS day FROM application_events WHERE application_id=? AND status IN ({marks})",
                          (application["id"], *sorted(SUBMITTED)))
        return str((row or {}).get("day") or application.get("created_at") or "")

    async def _sync_tracking(self, session: Session) -> None:
        """Read the application again and tell the page when what it knows changed."""
        previous = await asyncio.to_thread(self._refresh_tracking, session)
        if previous != session.tracking_mark():
            session.emit(session.tracked_event())

    def _job_summary(self, session: Session) -> dict:
        summary = {"company": session.company, "title": session.title, "location": session.location,
                   "job_id": session.job_id, "application_id": session.application_id, "tracked": session.tracked}
        # UXR-08: only when an application exists (a page with none is told nothing about one)
        if session.status:
            summary["status"] = session.status
        if session.since:
            summary["since"] = session.since
            summary["tracked_at"] = session.since  # the name the panel reads today
        return summary

    def persist(self, session: Session, force: bool = False) -> None:
        """Upsert the (client, page) row: company, title, host and research state. No page text, no values."""
        if not force and session.persisted_state == session.research_state and not session.draft_spend:
            return
        stamp = now()
        metadata = json.dumps({"ats": session.ats or "generic", "draft_spend_usd": round(session.draft_spend, 6),
                               "research_run": session.research_run_id, "user": session.user})
        try:
            self.db.execute(
                "INSERT INTO extension_sessions(id,client_id,url,canonical_url,host,job_id,application_id,company,title,"
                "research_state,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(client_id,canonical_url) DO UPDATE SET url=excluded.url,host=excluded.host,job_id=excluded.job_id,"
                "application_id=excluded.application_id,company=excluded.company,title=excluded.title,"
                "research_state=excluded.research_state,metadata=excluded.metadata,updated_at=excluded.updated_at",
                (session.id, session.client_id, session.url[:2000], session.canonical, session.host, session.job_id,
                 session.application_id, session.company[:300], session.title[:400], session.research_state, metadata,
                 stamp, stamp))
            session.persisted_state = session.research_state
        except Exception as exc:  # a locked or restored database must never break a page
            logger.warning("extension_session_persist_failed %s", type(exc).__name__)

    # ------------------------------------------------------------------------------- audit
    def audit(self, session: Session, kind: str, element: dict | None, outcome: str, details: dict | None = None) -> None:
        try:
            record_event(self.db, session.id, kind, element, outcome, details)
        except Exception as exc:
            logger.warning("extension_audit_failed %s", type(exc).__name__)

    def audit_many(self, session: Session, rows: list) -> None:
        """All the lines of one scan in ONE transaction (all or none: never half an audit trail)."""
        try:
            record_events(self.db, session.id, rows)
        except Exception as exc:
            logger.warning("extension_audit_failed %s", type(exc).__name__)

    # ------------------------------------------------------------------------------- budget guard
    def guard(self, session_id: str):
        """Consulted before every draft: (code, message, scope) or None. Cheap: database reads only."""
        gen = self.service.generation_status(fresh=True)
        if not gen["available"]:
            return ("LLM_CONFIGURATION_REQUIRED", gen["message"] or "Long answers need a writing model (Settings > AI).",
                    "global")
        limit, spent = gen.get("monthly_limit_usd"), float(gen.get("spent_usd") or 0)
        if not gen.get("local") and not gen.get("unlimited") and limit is not None and spent >= float(limit) - 1e-9:
            return ("AI_BUDGET_EXCEEDED", "Monthly AI budget reached; short answers still work.", "global")
        session = self.sessions.get(session_id)
        cap = self.service.extension_settings().get("draft_budget_usd")
        if session is not None and cap and session.draft_spend >= float(cap) - 1e-9:
            return ("AI_DRAFT_BUDGET", f"The draft budget for this application (${float(cap):.2f}) is used up.", "session")
        return None

    # ------------------------------------------------------------------------------- operations
    @staticmethod
    def _alive(session: Session) -> None:
        if session.dead:  # dropped while this request waited its turn: nothing may be started for a page that is gone
            raise BridgeError(410, "This page was closed or Meridian restarted; open it again", "expired")

    async def suggest(self, client: Client, sid: str, request: SuggestRequest) -> dict:
        session = self.get(client, sid)
        async with session.frame_lock(request.frame_key or "top"):
            self._alive(session)
            known = session.known_version
            response = await self.service.suggest(session, request)
            self._freshen(session, response)
            if session.known_version == known:  # nobody announced a newer version while the answers were being worked out
                session.known_version = response["knowledge_version"]
        return response

    @staticmethod
    def _freshen(session: Session, response: dict) -> None:
        """A draft can finish while the request is still running (the scan yields to the loop between fields and while its
        audit lines are written): the event it emitted is newer than the result that was built for its field earlier in
        this very request, and the suggestion kept on the field already is the newer one. Answer with that one, so a
        response is never older than an event that was emitted before it was sent: the page's poll and this response are
        two HTTP requests, and either may reach it first. The cursor stays the one from the START of the request (events
        emitted meanwhile are delivered again by the poll: the same state twice, never a state missed)."""
        results = response["results"]
        for index, result in enumerate(results):
            stored = session.fields.get(f"{result['frame_key']}:{result['node_id']}")
            newest = stored.suggestion if stored is not None else None
            if newest is not None and newest is not result and newest.get("question_id") == result["question_id"]:
                results[index] = newest

    async def regenerate(self, client: Client, sid: str, request: RegenerateRequest) -> dict:
        session = self.get(client, sid)
        async with session.frame_lock(request.frame_key or "top"):
            self._alive(session)
            suggestion = await self.service.regenerate(session, request)
        if suggestion is None:
            raise BridgeError(404, "This question is not on the page any more; scan the page again", "not_found")
        return suggestion

    async def poll(self, client: Client, sid: str, request: PollRequest, *, disconnected=None) -> dict:
        """Long-poll one page: events after ``cursor``, waiting up to ``wait_ms`` (<= 25 s) for the first one. A newer
        poll of the same page makes this one return at once, empty; so does releasing the page."""
        entry = (await self.poll_many(client, [(sid, request.cursor)], request.wait_ms, disconnected=disconnected))[0]
        if entry["status"] == "expired":
            raise BridgeError(410, "This page was closed or Meridian restarted; open it again", "expired")
        if entry["status"] == "unknown":
            raise BridgeError(404, "Unknown page session", "not_found")
        return {"cursor": entry["cursor"], "events": entry["events"]}

    async def poll_many(self, client: Client, targets: list[tuple[str, int]], wait_ms: int, *,
                        disconnected=None) -> list[dict]:
        """ONE long-poll for several pages of the same browser (a browser allows only a handful of connections per host:
        a poll per page starves the pages that open last). Returns the first moment ANY listed page has events, or empty
        after ``wait_ms`` (<= 25 s). A wait of 250 ms or less is a quick check: it answers at once and never takes a page
        over from a long poll that is waiting for it. One entry per page, in request order, each
        ``{session_id, status, cursor, events}`` with status:

        * ``ok`` - ``events`` after ``cursor`` (a client behind dropped events, or ahead of a rebuilt page, gets a
          ``knowledge`` event: it must re-scan); ``cursor`` is the one to ask with next
        * ``expired`` - this browser's page whose server state is gone: open it again (``POST /sessions``)
        * ``unknown`` - not a page of this browser (a stranger's page looks exactly like one that never existed)
        * ``released`` - the page was closed or navigated away; nothing to wait for
        * ``superseded`` - a newer poll of this browser took the page over

        Every page is authorised against ``client`` before anything is read or any waiting poll is touched."""
        if len({sid for sid, _ in targets}) != len(targets):
            raise BridgeError(422, "List each page once", "invalid")
        entries: dict[str, dict] = {}
        live: dict[str, tuple[Session, int]] = {}
        for sid, cursor in targets:
            status, session = self.resolve(client, sid)
            if session is None:
                entries[sid] = {"session_id": sid, "status": status, "cursor": cursor, "events": []}
            elif session.release_handle is not None:
                entries[sid] = {"session_id": sid, "status": "released", "cursor": cursor, "events": []}
            else:
                live[sid] = (session, cursor)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_ms / 1000
        slot: PollSlot | None = None
        attached: list[Session] = []
        check = True
        try:
            while True:
                for sid, (session, cursor) in list(live.items()):
                    if session.dead:  # dropped while this poll was busy
                        live.pop(sid)
                        entries[sid] = {"session_id": sid, "status": "expired", "cursor": cursor, "events": []}
                        continue
                    session.touch()
                    if self.sessions.get(sid) is session:
                        self.sessions.move_to_end(sid)
                if check:
                    check = False
                    await self._check_all([session for session, _ in live.values()])
                if (self._collect_all(live, entries) or wait_ms <= QUICK_POLL_MS or self._closed or self._draining
                        or not live):
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                if slot is None:
                    slot = PollSlot()
                    for sid, (session, _) in live.items():
                        if session.poll is not None:
                            session.poll.drop(sid, "superseded")  # one outstanding poll per page: the newest wins
                        session.poll, slot.sids[sid] = slot, None
                        attached.append(session)
                try:
                    await asyncio.wait_for(slot.wake.wait(), min(remaining, POLL_TICK))
                except TimeoutError:
                    check = True
                    if disconnected is not None and await disconnected():
                        break  # the browser hung up: nobody will read the answer
                slot.wake.clear()
                for sid, why in list(slot.sids.items()):
                    if why is not None and sid in live:
                        _, cursor = live.pop(sid)
                        entries[sid] = {"session_id": sid, "status": why, "cursor": cursor, "events": []}
        finally:
            for session in attached:
                if session.poll is slot:
                    session.poll = None
        return [entries[sid] for sid, _ in targets]

    async def poll_batch(self, client: Client, request: PollManyRequest, *, disconnected=None) -> dict:
        """``POST /v1/poll``: the multiplexed long-poll of a browser (see :meth:`poll_many`)."""
        entries = await self.poll_many(client, [(t.session_id, t.cursor) for t in request.sessions], request.wait_ms,
                                       disconnected=disconnected)
        return {"sessions": entries}

    @staticmethod
    def _collect(session: Session, cursor: int) -> tuple[list[dict], int]:
        """Events of ``session`` after ``cursor`` and the cursor to ask with next."""
        if cursor > session.cursor:
            # The client remembers events this session never had (it was rebuilt): everything must be re-scanned.
            return [{"type": "knowledge", "knowledge_version": session.known_version}], session.cursor
        events = [event for number, event in session.events if number > cursor]
        if cursor < session.lost:
            # events were dropped before this client read them: it must re-scan to resynchronise
            events = [{"type": "knowledge", "knowledge_version": session.known_version}, *events]
        return events, session.cursor

    def _collect_all(self, live: dict, entries: dict) -> bool:
        got = False
        for sid, (session, cursor) in live.items():
            events, newest = self._collect(session, cursor)
            entries[sid] = {"session_id": sid, "status": "ok", "cursor": newest, "events": events}
            got = got or bool(events)
        return got

    async def _data_token(self) -> str:
        """The profile fingerprint, read at most every ``TOKEN_TTL`` however many polls are waiting on it."""
        async with self._token_lock:
            if time.monotonic() - self._token_at > TOKEN_TTL:
                self._token_value = await asyncio.to_thread(data_token, self.db)
                self._token_at = time.monotonic()
            return self._token_value

    async def _check_all(self, sessions: list[Session]) -> None:
        if sessions:
            token = await self._data_token()
            for session in sessions:
                await self._check_changes(session, token)

    async def _check_changes(self, session: Session, token: str) -> None:
        """Knowledge (the profile changed in Meridian) and tracking (recorded in Meridian) changes become events."""
        if token != session.last_token:
            session.last_token = token
            env = self.service.environment(session, None, fresh=True)
            if env.version != session.known_version:
                session.known_version = env.version
                session.emit({"type": "knowledge", "knowledge_version": env.version})
        previous = (await asyncio.to_thread(self._refresh_tracking, session) if session.job_id
                    else self._refresh_tracking(session))
        if previous != session.tracking_mark():
            session.emit(session.tracked_event())

    # ------------------------------------------------------------------------------- lazy application
    async def ensure_application(self, session: Session) -> dict | None:
        """The (job, application) rows of this page, created on first need. None when the page's company/role cannot
        be determined yet. A submitted application is returned as is (callers must not touch it)."""
        async with session.lock:
            if session.application_id:
                known = await asyncio.to_thread(application_row, self.db, session.application_id)
                if known is not None:
                    return known
                session.application_id = None  # deleted in Meridian since this page last looked: written again on demand
            company = session.company
            title = session.title or session.report["h1"] or session.report["title"]
            if not company or not title:
                return None
            context = (self.research.job_context(session.research_run_id)
                       if self.research.exists(session.research_run_id) else {})
            created = await asyncio.to_thread(
                ensure_extension_application, self.db, url=session.report["posting_url"],
                application_url=session.report["url_clean"], title=title, company=company, location=session.location,
                description=context.get("description") or session.report["description_text"],
                ats=session.ats or "generic")
            session.job_id, session.application_id = created["job_id"], created["id"]
            self.persist(session, force=True)
            self.service.forget(session.id)
            await self._sync_tracking(session)
            return created

    # ------------------------------------------------------------------------------- feedback
    async def feedback(self, client: Client, sid: str, request: FeedbackRequest) -> dict:
        session = self.get(client, sid)
        stored = next((f for f in session.fields.values() if f.question_id == request.question_id), None)
        if stored is None and request.element is None:
            raise BridgeError(404, "This question is not on the page any more", "not_found")
        element = stored.resolution.element if stored else self._loose_element(session, request)
        question = stored.question if stored else element["_question"]
        intent = (stored.resolution.result.get("canonical_intent") if stored and stored.resolution.result else None) \
            or "OTHER"
        outcome = request.outcome
        kind = {"accepted": "accept", "edited": "edit", "manual": "edit", "rejected": "skip", "skipped": "skip"}[outcome]
        base = {"saved": False, "application_id": session.application_id, "scope": None, "promoted_to_profile": False,
                "message": None}
        details = {"state": request.suggestion_state or (stored.state if stored else ""), "request_id": request.request_id,
                   "outcome": outcome}
        if outcome not in {"edited", "manual"}:
            self.audit(session, kind, element, outcome, details)
            return base
        ext = self.service.extension_settings()
        if ext["save_corrections"] == "never" or (request.final_value is None and request.final_values is None):
            self.audit(session, kind, element, outcome, {**details, "saved": False})
            return {**base, "message": "Not saved."}
        explicit = "remember" in request.model_fields_set
        pseudo = {"id": session.application_id or "", "company": session.company, "location": session.location}
        decision = plan(question, element, intent, request, explicit, pseudo, twins=self._twins(session, stored, question))
        if decision.refusal:  # decided before any row exists: a refused save leaves no trace in the tracker
            self.audit(session, kind, element, outcome, {**details, "saved": False, "reason": "refused",
                                                         **({"code": decision.code} if decision.code else {})})
            return {**base, "message": decision.refusal, **({"code": decision.code} if decision.code else {})}
        application = await self.ensure_application(session)
        if application is None:
            self.audit(session, kind, element, outcome, {**details, "saved": False, "reason": "no_company"})
            return {**base, "message": "Tell Meridian which company this is before saving answers."}
        if application["status"] in SUBMITTED:
            self.audit(session, kind, element, outcome, {**details, "saved": False, "reason": "already_recorded"})
            return {**base, "application_id": application["id"],
                    "message": "This application is already recorded as sent, so Meridian keeps its answers as they are."}
        # The audit line ("saved through the assistant", kind 'edit' + details.saved) is written by persist() in the
        # transaction of the answer itself, and names the answer: the label never depends on timing (api.SAVED_EDIT).
        saved = await asyncio.to_thread(
            persist, self.db, application, decision, question=question, question_id=request.question_id, element=element,
            state=details["state"],
            audit={"session_id": session.id, "kind": kind, "outcome": outcome, "details": details})
        for private in ("state", "answer_id", "fact_id"):
            saved.pop(private, None)  # bookkeeping for the audit line, not part of the wire response
        if saved["saved"]:
            token = await asyncio.to_thread(data_token, self.db)
            session.last_token = token
            env = self.service.environment(session, None, fresh=True)
            if env.version != session.known_version:
                session.known_version = env.version
                session.emit({"type": "knowledge", "knowledge_version": env.version})
        return {**base, **saved}

    @staticmethod
    def _twins(session: Session, stored, question: str) -> int:
        """How many OTHER questions of this page carry exactly this label (a skipped control is nobody's question)."""
        wanted = normalize(question)
        return sum(1 for other in session.fields.values()
                   if other is not stored and other.skipped is None and other.state != "SKIP"
                   and (stored is None or other.question_id != stored.question_id) and normalize(other.question) == wanted)

    def _loose_element(self, session: Session, request: FeedbackRequest) -> dict:
        """A control the server never stored: normalise what the client sent so the memory key matches."""
        from .resolver import FieldResolver, prepare_elements
        element = prepare_elements([request.element], f"{session.id}:{request.frame_key}", session.url)[0]
        resolver = FieldResolver(self.service.environment(session), url=session.url, ats=session.ats, scope=session.id,
                                 frame_key=request.frame_key, document_id=element["document_id"], elements=[element])
        element["_question"] = resolver.question(element)
        return element

    # ------------------------------------------------------------------------------- tracking
    async def track(self, client: Client, sid: str, request: TrackRequest) -> dict:
        session = self.get(client, sid)
        base = {"job_id": session.job_id, "application_id": session.application_id}
        if request.event in {"submit_clicked", "confirmation_seen"}:
            self.audit(session, "track", None, "noted", {"event": request.event, "evidence": request.evidence[:200]})
            return {"state": "noted", **base, "tracked": session.tracked}
        if request.event == "save":
            return await self._save_to_tracker(session)
        if request.event != "record":  # whatever the schema admits later: only an explicit record ever says it was sent
            raise BridgeError(422, "Unknown tracking event", "invalid")
        applied_at = None
        if request.applied_at:
            try:
                parsed = datetime.fromisoformat(request.applied_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise BridgeError(422, "The application date is not valid", "invalid") from exc
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            if parsed > datetime.now(timezone.utc):
                raise BridgeError(422, "The application date cannot be in the future", "invalid")
            applied_at = parsed.isoformat()
        application = await self.ensure_application(session)
        if application is None:
            raise BridgeError(409, "Meridian needs the company and role of this page first", "conflict")
        if application["status"] in SUBMITTED:
            self.audit(session, "track", None, "already_recorded", {"event": "record"})
            return {"state": "already_recorded", "job_id": application["job_id"], "application_id": application["id"],
                    "tracked": "applied"}
        result = await asyncio.to_thread(self._record, application, applied_at, request.notes)
        await self._sync_tracking(session)
        self.audit(session, "track", None, "recorded", {"event": "record"})
        return {"state": "already_recorded" if result.get("already_recorded") else "recorded",
                "job_id": application["job_id"], "application_id": application["id"], "tracked": session.tracked}

    async def _save_to_tracker(self, session: Session) -> dict:
        """"Save to tracker" (UXR-01): the posting goes into the tracker as a bare PREPARING application, exactly what the first
        saved answer would have made, WITHOUT saying it was sent (``record_application`` is never called). An application that
        was already sent is left exactly as it is, as ``record`` leaves it: the page is told so and is not told it was saved."""
        application = await self.ensure_application(session)
        if application is None:
            raise BridgeError(409, "Meridian needs the company and role of this page first", "conflict")
        await self._sync_tracking(session)
        identity = {"job_id": application["job_id"], "application_id": application["id"]}
        if application["status"] in SUBMITTED:
            self.audit(session, "track", None, "already_recorded", {"event": "save"})
            return {"state": "already_recorded", **identity, "tracked": "applied"}
        self.audit(session, "track", None, "saved", {"event": "save"})
        return {"state": "saved", **identity, "tracked": session.tracked}

    def _record(self, application: dict, applied_at: str | None, notes: str) -> dict:
        """Record that the USER submitted (Meridian never assumes a Submit click worked): the event says the assistant
        showed the prompt and the user confirmed it."""
        return record_application(self.db, application["job_id"], applied_at, notes, origin="extension")

    # ------------------------------------------------------------------------------- research corrections
    async def research_company(self, client: Client, sid: str, company: str) -> dict:
        session = self.get(client, sid)
        return self._restart_research(session, company_override=company)

    async def research_site(self, client: Client, sid: str, domain: str) -> dict:
        session = self.get(client, sid)
        return self._restart_research(session, site_override=domain)

    async def research_refresh(self, client: Client, sid: str) -> dict:
        session = self.get(client, sid)
        return self._restart_research(session, refresh=True)

    def _restart_research(self, session: Session, *, company_override=None, site_override=None, refresh=False) -> dict:
        """The user's correction of the research ('not this company', 'this is the website', 'read it again').

        The correction is kept on the session (and its stored row): a reload of the page, a restart of Meridian and every
        later re-run use it, so the same mistake is never made twice. A new company drops the website that was given
        for the rejected one."""
        rejected: set[str] = set()
        try:
            user = dict(session.user)
            if company_override is not None:
                user = {"company": self.research.validate_company(company_override)}
                # What the user just said this page is NOT about: the company research had assumed, and any earlier correction.
                rejected = {tail_key(name) for name in (session.company, session.user.get("company")) if name} - {
                    tail_key(user["company"])}
            if site_override is not None:
                user["site"] = self.research.validate_site(site_override)
            if self.research.exists(session.research_run_id):
                if site_override is not None:
                    snapshot = self.research.set_site(session.research_run_id, user["site"], session_id=session.id,
                                                      listener=session.research_listener)
                elif company_override is not None:
                    snapshot = self.research.set_company(session.research_run_id, user["company"],
                                                         session_id=session.id, listener=session.research_listener)
                else:
                    snapshot = self.research.refresh(session.research_run_id, session_id=session.id,
                                                     listener=session.research_listener)
            else:
                report = {**session.report, "user": {**session.report["user"], **user}}
                if company_override is not None:
                    report["user"].pop("site", None)
                snapshot = self.research.start(session.id, report, refresh=True, listener=session.research_listener)
        except (ValueError, KeyError) as exc:
            raise BridgeError(422, str(exc) if isinstance(exc, ValueError) else "Research is not running", "invalid") from exc
        session.user = user
        reported = {**session.report["user"], **user}
        if company_override is not None and "site" not in user:
            reported.pop("site", None)
        session.report = {**session.report, "user": reported}
        session.research_run_id = snapshot["run_id"]
        session.refresh_drafts()  # the basis of the drafts changed: the next scan may draft again (explicit action)
        session.rejected_companies |= rejected
        if company_override:
            session.company = ""
        self.apply_research(session, snapshot)
        self.persist(session, force=True)
        session.emit({"type": "research", "research": snapshot})
        session.reassess_drafts(self.service)  # a draft about the rejected company is not offered any more
        return snapshot

    # ------------------------------------------------------------------------------- documents
    def documents(self, session: Session | None = None) -> list[dict]:
        """``GET /v1/documents``: the user's own approved CVs, the default first; with a ``session`` (a page of this browser,
        resolved by the caller on the event loop) also the CV written for that very job and the one attached to its
        application, so the panel can offer a choice among exactly the CVs that may go to that employer (SAFE-09)."""
        data_dir = self.app.state.data_dir
        if session is not None:
            docs = approved_documents(self.db, data_dir, session.application_id, session.job_id)
        else:
            docs = documents_pick.base_documents(self.db, data_dir)
        return [{k: v for k, v in doc.items() if k != "sha256"} for doc in docs]

    def document(self, version_id: str) -> dict:
        try:
            return document_content(self.db, self.app.state.data_dir, version_id)
        except LookupError as exc:
            raise BridgeError(404, "Document not found", "not_found") from exc
        except PermissionError as exc:
            raise BridgeError(404, "Only approved CVs and cover letters can be attached", "not_found") from exc
        except OverflowError as exc:
            raise BridgeError(413, str(exc), "too_large") from exc
        except ValueError as exc:
            raise BridgeError(404, "This document is not available", "not_found") from exc
