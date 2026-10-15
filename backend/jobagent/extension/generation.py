"""Background drafting of long answers for the browser assistant (docs/EXTENSION_DESIGN.md 5.5).

The queue never talks to a model itself: each job carries a ``runner`` coroutine (built by the suggestion service) that
resolves ONE question through ``CandidateEngine.answer(..., generate=True)`` on the shared answer model. The queue owns:

* **coalescing** - one job per content key (question, constraints, knowledge version, job context): duplicate focus
  events, reloads and a second tab share one paid draft;
* **order** - per session, jobs run one at a time in DOM order so earlier validated drafts feed the next one as
  ``_related_answers`` (coherence, no repeated paragraphs); sessions run in parallel, bounded by the answer model's own
  semaphore (``text_concurrency``);
* **caps** - 6 queued/running jobs per page, 12 overall (explicit user requests are exempt from the page cap), and a
  cumulative limit of drafts started on their own per page, per browser and overall in a rolling hour
  (:class:`~jobagent.extension.draft_limits.DraftLedger`; a draft the user asks for is never counted);
* **cost control** - a guard consulted before every job, a global stop on the first configuration/budget error (every
  page gets a ``generation_budget`` notice and nothing else is retried automatically) and an optional per-application
  cap;
* **cancellation** - only QUEUED work is ever cancelled (page closed/navigated). A provider call in flight is never
  cancelled: its upper-bound reservation would stay charged and the validated answer lands in the answer cache anyway;
* **progress** - one router on ``answer_llm.on_progress`` (chained, restored on close) maps the model layer's status
  updates back to the jobs by question id.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from .draft_limits import DraftLedger

PAGE_CAP = 6
GLOBAL_CAP = 12
STOP_TTL = 600.0
KEEP_DONE = 8
ACTIVE = {"QUEUED", "GENERATING", "REGENERATING", "VALIDATING"}
# A model-layer failure that will not fix itself by trying the next question.
STOP_CODES = {"LLM_CONFIGURATION_REQUIRED", "AI_BUDGET_EXCEEDED", "LLM_SETUP_REQUIRED"}
PROGRESS = {"GENERATING": "GENERATING", "REGENERATING": "REGENERATING", "VALIDATING": "VALIDATING"}


class Refused(Exception):
    """The queue will not take this job (``code`` explains why; the page shows a calm sentence)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Job:
    id: str
    key: str
    session_id: str
    order: tuple
    question_id: str
    runner: object = field(repr=False)
    explicit: bool = False
    status: str = "QUEUED"
    attempt: int = 1
    result: dict | None = None
    error: dict | None = None
    cache_hit: bool = False
    cost: float = 0.0
    meta: dict = field(default_factory=dict)
    created: float = field(default_factory=time.monotonic)
    started: float | None = None
    finished: float | None = None
    listeners: dict = field(default_factory=dict, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


@dataclass
class Lane:
    """One session's ordered work. At most one job of a lane runs at a time."""

    session_id: str
    pending: list = field(default_factory=list)
    running: Job | None = None
    done: "OrderedDict[str, Job]" = field(default_factory=OrderedDict)
    task: asyncio.Task | None = None
    closed: bool = False  # the page is gone: drop the lane once its running job finishes


class GenerationQueue:
    def __init__(self, *, guard=None, llm_getter=None, clock=time.monotonic, ledger: DraftLedger | None = None):
        self._guard = guard  # (session_id) -> None | (code, message, scope in {'global','session'})
        self.ledger = ledger if ledger is not None else DraftLedger(clock=clock)
        self._client_of: dict[str, str] = {}
        self._limit_listeners: dict[str, object] = {}
        self._llm_getter = llm_getter  # the shared answer model; the progress router is installed on first use
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self._lanes: dict[str, Lane] = {}
        self._by_qid: dict[str, Job] = {}
        self._stopped: dict | None = None
        self._stop_listeners: dict[str, object] = {}
        self._llm = None
        self._previous = None
        self._closed = False
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------------------- wiring
    def attach_llm(self, llm) -> None:
        """Install the progress router on the shared answer model, chaining whatever was there."""
        if llm is None or self._llm is llm:
            return
        self.detach_llm()
        self._llm, self._previous = llm, getattr(llm, "on_progress", None)
        llm.on_progress = self._route

    def detach_llm(self) -> None:
        if self._llm is not None and getattr(self._llm, "on_progress", None) == self._route:
            self._llm.on_progress = self._previous
        self._llm = self._previous = None

    def set_guard(self, guard) -> None:
        self._guard = guard

    def register_session(self, session_id: str, on_stop, *, client_id: str | None = None, on_limit=None) -> None:
        """``on_stop(code, message)`` hears a global stop; ``on_limit(code, message)`` hears that THIS page used up the drafts
        it may have written on its own; ``client_id`` is the browser the page belongs to (the per-browser limit)."""
        self._stop_listeners[session_id] = on_stop
        if client_id:
            self._client_of[session_id] = client_id
        if on_limit is not None:
            self._limit_listeners[session_id] = on_limit

    def forget_session(self, session_id: str) -> int:
        """The page is gone: drop its listeners and cancel its QUEUED jobs (never one that is running)."""
        self._stop_listeners.pop(session_id, None)
        self._limit_listeners.pop(session_id, None)
        self._client_of.pop(session_id, None)
        lane = self._lanes.get(session_id)
        cancelled = 0
        for job in list(self._jobs.values()):
            job.listeners = {k: v for k, v in job.listeners.items() if not k.startswith(session_id + "|")}
        if lane:
            for job in list(lane.pending):
                if not job.listeners:
                    lane.pending.remove(job)
                    self._finish(job, "CANCELLED", emit=False)
                    cancelled += 1
            lane.closed = True
            if lane.running is None and not lane.pending:
                self._lanes.pop(session_id, None)
        return cancelled

    async def close(self) -> None:
        self._closed = True
        for lane in self._lanes.values():
            for job in lane.pending:
                self._finish(job, "CANCELLED", emit=False)
            lane.pending.clear()
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.detach_llm()

    # ------------------------------------------------------------------------------- queries
    def get(self, key: str) -> Job | None:
        return self._jobs.get(key)

    def stopped(self) -> dict | None:
        if self._stopped and self._clock() - self._stopped["at"] > STOP_TTL:
            self._stopped = None
        return self._stopped

    def clear_stop(self) -> None:
        self._stopped = None

    def pending(self, session_id: str) -> int:
        lane = self._lanes.get(session_id)
        return (len(lane.pending) + (1 if lane.running else 0)) if lane else 0

    def active_total(self) -> int:
        return sum(self.pending(sid) for sid in self._lanes)

    # ------------------------------------------------------------------------------- submit
    def submit(self, *, session_id: str, key: str, order: tuple, question_id: str, runner, listener_id: str,
               listener, explicit: bool = False, meta: dict | None = None) -> Job:
        """Queue (or join) the draft for ``key``. Raises :class:`Refused` when it must not run now."""
        if self._closed:
            raise Refused("closed", "Meridian is shutting down")
        if self._llm_getter is not None:
            self.attach_llm(self._llm_getter())
        existing = self._jobs.get(key)
        if existing is not None and existing.status in ACTIVE | {"DONE"}:
            existing.listeners[f"{session_id}|{listener_id}"] = listener  # coalesce: one paid draft, many fields
            return existing
        stopped = self.stopped()
        if stopped is not None:
            if not explicit:
                raise Refused(stopped["code"], stopped["message"])
            self._stopped = None  # the user asked: maybe they fixed the setup, try again
        guard = self._guard(session_id) if self._guard else None
        if guard is not None:
            code, message, scope = guard
            if scope == "global":
                self._stop(code, message)
            raise Refused(code, message)
        self._prune()
        if not explicit:
            limited = self.ledger.check(session_id, self._client_of.get(session_id))
            if limited is not None:
                # The circuit breaker: this page (or browser) has had its share of drafts written on their own. It is told
                # once, and the field says why; the user's own request ("Draft with AI") still works.
                message = limited[1]
                callback = self._limit_listeners.get(session_id)
                if callback is not None:
                    with contextlib.suppress(Exception):
                        callback("AUTO_DRAFT_LIMIT", message)
                raise Refused("draft_limit", message)
        if not explicit and self.pending(session_id) >= PAGE_CAP:
            raise Refused("draft_limit", "Drafts for other questions on this page are still being written")
        if self.active_total() >= GLOBAL_CAP:
            raise Refused("draft_limit", "Meridian is busy writing other drafts; try again in a moment")
        job = Job(id="gen_" + uuid.uuid4().hex[:12], key=key, session_id=session_id, order=order,
                  question_id=question_id, runner=runner, explicit=explicit, meta=dict(meta or {}))
        job.listeners[f"{session_id}|{listener_id}"] = listener
        if not explicit:
            self.ledger.record(session_id, self._client_of.get(session_id))
        self._jobs[key] = job
        lane = self._lanes.setdefault(session_id, Lane(session_id))
        lane.pending.append(job)
        lane.pending.sort(key=lambda j: j.order)
        self._pump(lane)
        return job

    def _prune(self) -> None:
        """Finished drafts are kept an hour (re-focusing a field shows them again) and at most 300 overall."""
        horizon = self._clock() - 3600
        finished = [j for j in self._jobs.values() if not j.active and (j.finished or 0) < horizon]
        extra = len(self._jobs) - 300
        if extra > 0:
            finished += sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.finished or 0)[:extra]
        for job in finished:
            self._jobs.pop(job.key, None)

    # ------------------------------------------------------------------------------- execution
    def _pump(self, lane: Lane) -> None:
        if self._closed or lane.running is not None or not lane.pending:
            return
        job = lane.pending.pop(0)
        lane.running = job
        task = asyncio.get_running_loop().create_task(self._run(lane, job), name=f"draft:{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        lane.task = task

    async def _run(self, lane: Lane, job: Job) -> None:
        try:
            guard = self._guard(job.session_id) if self._guard else None
            if guard is not None:
                code, message, scope = guard
                if scope == "global":
                    self._stop(code, message)
                self._fail(job, code, message)
                return
            job.status, job.started = "GENERATING", self._clock()
            self._by_qid[job.question_id] = job
            self._emit(job)
            related = [j.result for j in lane.done.values() if j.result]
            try:
                result = await job.runner(related)
            except asyncio.CancelledError:
                self._finish(job, "CANCELLED", emit=False)
                raise
            except Exception as exc:  # the engine reports provider problems as results; this is a bug or a crash
                self._fail(job, "INTERNAL_ERROR", f"The draft could not be written ({type(exc).__name__})")
                return
            self._settle(lane, job, result)
        finally:
            self._by_qid.pop(job.question_id, None)
            lane.running = None
            if lane.pending and not self._closed:
                self._pump(lane)
            elif not lane.pending and (lane.closed or not lane.done):
                self._lanes.pop(lane.session_id, None)

    def _settle(self, lane: Lane, job: Job, result: dict) -> None:
        job.result = result
        job.cost = float(result.get("cost") or 0)
        job.cache_hit = bool(result.get("reused_validated_answer"))
        quality = result.get("quality") or {}
        cause = result.get("root_cause")
        generated = result.get("confidence_level") == "GENERATED"
        # A validated draft, or an answer the pipeline found without a model (a fact saved while the draft waited):
        # either is a finished job; the suggestion builder decides what state it is. Failed review is never "done".
        if result.get("answer") is not None and not cause and (not generated or quality.get("passed") is True):
            self._finish(job, "DONE")
            lane.done[job.key] = job
            while len(lane.done) > KEEP_DONE:
                lane.done.popitem(last=False)
            return
        code = str(cause or result.get("state") or "ANSWER_FAILED")
        if code == "ValueError" and "budget" in str(result.get("reason", "")).lower():
            code = "AI_BUDGET_EXCEEDED"
        message = str(result.get("reason") or "The draft could not be written")[:300]
        if code in STOP_CODES:
            self._stop(code, message)
        self._fail(job, code, message)

    def _fail(self, job: Job, code: str, message: str) -> None:
        job.error = {"code": code, "message": message}
        self._finish(job, "FAILED")

    def _finish(self, job: Job, status: str, *, emit: bool = True) -> None:
        job.status, job.finished = status, self._clock()
        if status in {"FAILED", "CANCELLED"} and self._jobs.get(job.key) is job:
            # a failed or cancelled draft is retried by the next explicit request, never silently
            if status == "CANCELLED":
                self._jobs.pop(job.key, None)
        if emit:
            self._emit(job)

    def _stop(self, code: str, message: str) -> None:
        """First configuration/budget failure: nothing else is attempted automatically; every page is told once."""
        if self._stopped is not None and self._stopped["code"] == code:
            return
        self._stopped = {"code": code, "message": message, "at": self._clock()}
        for lane in list(self._lanes.values()):
            for job in list(lane.pending):
                lane.pending.remove(job)
                self._fail(job, code, message)
        for callback in list(self._stop_listeners.values()):
            with contextlib.suppress(Exception):
                callback(code, message)

    def _emit(self, job: Job) -> None:
        for callback in list(job.listeners.values()):
            with contextlib.suppress(Exception):
                callback(job)

    # ------------------------------------------------------------------------------- progress
    async def _route(self, update: dict):
        job = self._by_qid.get(str(update.get("question_id")))
        if job is not None and job.status in ACTIVE:
            status = update.get("status")
            attempt = int(update.get("attempt") or job.attempt)
            changed = False
            if status in PROGRESS and (job.status != PROGRESS[status] or attempt != job.attempt):
                job.status, job.attempt, changed = PROGRESS[status], attempt, True
            elif status == "RETRYING" and attempt != job.attempt:
                job.attempt, changed = attempt, True
            if changed:
                self._emit(job)
        if self._previous is not None:
            outcome = self._previous(update)
            if inspect.isawaitable(outcome):
                await outcome
