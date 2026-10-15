"""The queue of local-model work for the browser assistant (docs/EXTENSION_DESIGN.md 5.4a).

A control the deterministic engine could not finish may be finished by a model on this Mac (``intelligence/local_assist.py``): a
few hundred milliseconds when the model is loaded, many seconds when it has to be started. That work must never hold up the page's
first answers, so it runs here: one job at a time (the models are one process each), in the order the controls were met, with the
same life as a draft: the field shows "working on it" (``GENERATING``) and is told when the job ends (the session's event).

Nothing in here talks to a model: a job carries a ``runner`` coroutine built by the suggestion service. What the queue owns:

* **coalescing** - one job per content key (question, options or hints, facts version): a rescan, a focus event or a second tab
  shares it, and a finished one is kept for an hour so that re-focusing a field answers at once;
* **memory of failure** - a job that found nothing is not run again for five minutes: a page that rescans itself cannot make the
  models work over and over for the same unanswerable question;
* **bounded** - 40 jobs waiting at most, 150 seconds per job (a model that has to be started is given time), and only QUEUED jobs of a
  page that went away are cancelled (a running one finishes: its answer is cached for the next visit).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field

ACTIVE = {"QUEUED", "GENERATING"}
MAX_WAITING = 40
JOB_SECONDS = 150.0
FAILED_MEMORY = 300.0
KEEP = 300
KEEP_SECONDS = 3600.0


@dataclass
class LocalJob:
    id: str
    key: str
    session_id: str
    order: tuple
    kind: str
    runner: object = field(repr=False)
    status: str = "QUEUED"
    result: dict | None = None
    error: dict | None = None
    attempt: int = 1
    cache_hit: bool = False
    cost: float = 0.0
    created: float = 0.0
    finished: float | None = None
    listeners: dict = field(default_factory=dict, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


class LocalQueue:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._jobs: dict[str, LocalJob] = {}
        self._waiting: list[LocalJob] = []
        self._worker: asyncio.Task | None = None
        self._closed = False

    def get(self, key: str) -> LocalJob | None:
        return self._jobs.get(key)

    def waiting(self) -> int:
        return len(self._waiting) + (1 if self._worker is not None and not self._worker.done() else 0)

    def submit(self, *, session_id: str, key: str, order: tuple, kind: str, runner, listener_id: str, listener) -> LocalJob | None:
        """Queue (or join) the job for ``key``. None when the queue is closed or full; a job that failed lately is returned as
        it is (status FAILED): the caller shows what it had."""
        if self._closed:
            return None
        existing = self._jobs.get(key)
        if existing is not None:
            recent_failure = existing.status == "FAILED" and self._clock() - (existing.finished or 0) < FAILED_MEMORY
            if existing.status in ACTIVE or existing.status == "DONE" or recent_failure:
                if existing.status in ACTIVE:
                    existing.listeners[f"{session_id}|{listener_id}"] = listener
                return existing
        if len(self._waiting) >= MAX_WAITING:
            return None
        self._prune()
        job = LocalJob(id="loc_" + uuid.uuid4().hex[:12], key=key, session_id=session_id, order=order, kind=kind, runner=runner,
                       created=self._clock())
        job.listeners[f"{session_id}|{listener_id}"] = listener
        self._jobs[key] = job
        self._waiting.append(job)
        self._waiting.sort(key=lambda item: (item.created, item.order))
        self._wake()
        return job

    def forget_session(self, session_id: str) -> None:
        """The page is gone: its jobs stop reporting to it, and a job nobody else waits for and that has not begun is dropped."""
        for job in list(self._jobs.values()):
            job.listeners = {k: v for k, v in job.listeners.items() if not k.startswith(session_id + "|")}
            if job.status == "QUEUED" and not job.listeners:
                with contextlib.suppress(ValueError):
                    self._waiting.remove(job)
                self._jobs.pop(job.key, None)
                job.status = "CANCELLED"

    async def close(self) -> None:
        self._closed = True
        for job in self._waiting:
            job.status = "CANCELLED"
        self._waiting.clear()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    # ------------------------------------------------------------------------------- execution
    def _wake(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._work(), name="local-assist")

    async def _work(self) -> None:
        while self._waiting and not self._closed:
            job = self._waiting.pop(0)
            if job.status != "QUEUED":
                continue
            job.status = "GENERATING"
            self._emit(job)
            try:
                result = await asyncio.wait_for(job.runner(), JOB_SECONDS)
            except asyncio.CancelledError:
                job.status = "CANCELLED"
                raise
            except Exception as exc:  # noqa: BLE001 - a model that failed is a field left to the person
                job.error = {"code": "LOCAL_" + type(exc).__name__.upper(), "message": "Local AI could not finish this"}
                self._finish(job, "FAILED")
                continue
            if isinstance(result, dict) and result.get("answer") is not None:
                job.result = result
                self._finish(job, "DONE")
            else:
                job.error = {"code": "NO_LOCAL_ANSWER", "message": "Local AI found nothing it could stand behind"}
                self._finish(job, "FAILED")

    def _finish(self, job: LocalJob, status: str) -> None:
        job.status, job.finished = status, self._clock()
        self._emit(job)

    def _emit(self, job: LocalJob) -> None:
        for callback in list(job.listeners.values()):
            with contextlib.suppress(Exception):
                callback(job)

    def _prune(self) -> None:
        horizon = self._clock() - KEEP_SECONDS
        stale = [j for j in self._jobs.values() if not j.active and (j.finished or 0) < horizon]
        extra = len(self._jobs) - KEEP
        if extra > 0:
            stale += sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.finished or 0)[:extra]
        for job in stale:
            self._jobs.pop(job.key, None)
