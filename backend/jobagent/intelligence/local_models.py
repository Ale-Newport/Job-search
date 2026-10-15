"""The two local engines the browser assistant may ask for help, and nothing else.

* **Laya** (``localdecide``, loopback port 8791): a small model that picks ONE of the choices it is shown. It never writes text.
* **Ollama** (loopback port 11434, the model Meridian manages): writes a few words, constrained to a JSON schema.

Both are HTTP to this Mac: no model code runs in this process, no request leaves it, nothing is sent to any other address (the
endpoints are refused unless they are loopback). Both may be installed, stopped or slow: every call is bounded by a deadline and
fails as :class:`LocalUnavailable`, which the caller reads as "ask the person". A runtime that is installed but not running is
started on demand, at most once a minute, and only when somebody actually needs it (the settings choose which engines exist).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

LAYA_ENDPOINT = "http://127.0.0.1:8791"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
HEALTH_TTL = 8.0
START_COOLDOWN = 60.0
GIVE_UP_MEMORY = 60.0
HUMAN = "HUMAN_REQUIRED"


class LocalUnavailable(Exception):
    """The engine is not installed, not running, not allowed by the settings, too slow, or its answer is not usable."""


@dataclass(frozen=True)
class LayaVote:
    index: int | None  # position among the options it was shown; None = it asked for a person
    confidence: float
    latency_ms: int
    model: str = ""


def loopback(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK or parsed.username or parsed.password:
        raise LocalUnavailable("Local engines must be reached on this Mac's loopback address")
    return url.rstrip("/")


class LocalModels:
    """Availability and calls of the two engines. One instance serves the whole app; it holds no data about any person."""

    def __init__(self, *, laya_runtime=None, ollama_runtime=None, settings=lambda: {}, transport=None, clock=time.monotonic):
        # a runtime may be given as a function that finds it (the app creates them after this object)
        self._runtimes = {"laya": laya_runtime, "ollama": ollama_runtime}
        self._settings, self.transport, self.clock = settings, transport, clock
        self._settings_seen: tuple[float, dict] | None = None
        self._health: dict[str, tuple[float, bool]] = {}
        self._started: dict[str, float] = {}
        self._warm: dict[str, bool] = {}
        self._starting: dict[str, asyncio.Task] = {}
        self._gave_up: dict[str, float] = {}

    def runtime(self, engine: str):
        found = self._runtimes.get(engine)
        return found() if callable(found) else found

    def settings(self) -> dict:
        """The settings, read from the database at most every few seconds (every probe asks what is allowed)."""
        if self._settings_seen is None or self.clock() - self._settings_seen[0] > 5:
            self._settings_seen = (self.clock(), dict(self._settings() or {}))
        return self._settings_seen[1]

    # ------------------------------------------------------------------------------- what the settings allow
    def hermetic(self) -> bool:
        """Under test the machine's own engines are never used: a test that wants one hands in a transport, and a run that is
        meant to meet the real ones (the end-to-end harness against Laya and Ollama) says so with MERIDIAN_LOCAL_ENGINES=1."""
        return bool(os.environ.get("MERIDIAN_TEST")) and self.transport is None and not os.environ.get("MERIDIAN_LOCAL_ENGINES")

    def laya_wanted(self) -> bool:
        return not self.hermetic() and self.settings().get("browser_engine", "laya") in {"laya", "hybrid"}

    def ollama_wanted(self) -> bool:
        return not self.hermetic() and self.settings().get("local_text_assist", True) is not False

    def laya_endpoint(self) -> str:
        return loopback(str(self.settings().get("laya_endpoint") or LAYA_ENDPOINT))

    def ollama_endpoint(self) -> str:
        return loopback(str(self.settings().get("local_assist_ollama_endpoint") or OLLAMA_ENDPOINT))

    def ollama_model(self) -> str:
        from ..automation.text_runtime import OLLAMA_MODEL
        return str(self.settings().get("local_assist_model") or OLLAMA_MODEL)

    def client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False,
                                 **({"transport": self.transport} if self.transport is not None else {}))

    # ------------------------------------------------------------------------------- availability
    async def _probe_laya(self) -> bool:
        try:
            async with self.client(1.5) as client:
                response = await client.get(self.laya_endpoint() + "/healthz")
            data = response.json() if response.status_code == 200 else {}
            return isinstance(data, dict) and data.get("ok") is True and data.get("backend") in {"laya-mlx", "laya-torch"}
        except (httpx.HTTPError, ValueError, LocalUnavailable):
            return False

    async def _probe_ollama(self) -> bool:
        try:
            async with self.client(1.5) as client:
                response = await client.get(self.ollama_endpoint() + "/api/tags")
            names = {str(m.get("name") or m.get("model")) for m in (response.json().get("models") or [])} \
                if response.status_code == 200 else set()
            return self.ollama_model() in names
        except (httpx.HTTPError, ValueError, AttributeError, LocalUnavailable):
            return False

    async def available(self, engine: str, *, wait: float = 0.0) -> bool:
        """Is the engine answering (waiting up to ``wait`` seconds for one that had to be started)? Cheap when it is: one probe
        every few seconds. Starting is attempted at most once a minute and only for an engine the settings allow."""
        wanted = self.laya_wanted() if engine == "laya" else self.ollama_wanted()
        if not wanted:
            return False
        probe = self._probe_laya if engine == "laya" else self._probe_ollama
        if wait > 0 and self.clock() - self._gave_up.get(engine, -GIVE_UP_MEMORY) < GIVE_UP_MEMORY:
            wait = 0.0  # it did not come up when it was last waited for: the jobs behind this one do not each wait again
        deadline = self.clock() + max(0.0, wait)
        while True:
            seen = self._health.get(engine)
            if seen is not None and self.clock() - seen[0] < HEALTH_TTL and (seen[1] or wait == 0):
                return seen[1]  # a recent answer (a "no" too, for a caller that does not wait: a scan must not probe once per field)
            ok = await probe()
            self._health[engine] = (self.clock(), ok)
            if ok:
                self._gave_up.pop(engine, None)
                return True
            self._start_in_background(engine)
            if self.clock() >= deadline:
                if wait > 0:
                    self._gave_up[engine] = self.clock()
                return False
            await asyncio.sleep(0.5)

    async def usable(self, engine: str) -> bool:
        """Is the engine answering, or installed with its model so that it will be shortly (it is started if it is not)? This is
        what decides whether a field is shown as "working on it": an engine that does not exist never makes a field wait."""
        if not await self.available(engine):
            runtime = self.runtime(engine)
            wanted = self.laya_wanted() if engine == "laya" else self.ollama_wanted()
            if runtime is None or not wanted:
                return False
            seen = self._health.get("installed:" + engine)
            if seen is None or self.clock() - seen[0] > 30:
                try:
                    status = await runtime.status()
                    ready = bool(status.get("installed") and status.get("model_downloaded") and not status.get("foreign_service"))
                except Exception:  # noqa: BLE001
                    ready = False
                seen = self._health["installed:" + engine] = (self.clock(), ready)
            return seen[1]
        return True

    def _start_in_background(self, engine: str) -> None:
        runtime = self.runtime(engine)
        if runtime is None or self._starting.get(engine) is not None and not self._starting[engine].done():
            return
        if self.clock() - self._started.get(engine, -START_COOLDOWN) < START_COOLDOWN:
            return
        self._started[engine] = self.clock()

        async def start():
            try:
                status = await runtime.status()
                if status.get("installed") and not status.get("running") and not status.get("foreign_service"):
                    await runtime.start()
            except Exception:  # noqa: BLE001 - an engine that cannot start is simply not there
                return

        self._starting[engine] = asyncio.get_running_loop().create_task(start())

    async def close(self) -> None:
        for task in self._starting.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._starting.values(), return_exceptions=True)

    # ------------------------------------------------------------------------------- Laya
    async def laya_choose(self, question: str, options: list[str], facts: list[str], *, goal: str, rules: str,
                          context: dict | None = None) -> LayaVote:
        """Laya's pick among ``options`` (labels), or its request for a person. Its confidence is advisory only."""
        from ..automation.engines import validate_choice

        if not 2 <= len(options) <= 19:
            raise LocalUnavailable("Laya reads between two and nineteen choices")
        criteria = {f"o{i}": label for i, label in enumerate(options)}
        criteria[HUMAN] = "No offered option is supported by the candidate's facts."
        body = {"model": "localdecide", "state": {"question": question, "candidate_facts": facts, **(context or {})},
                "questions": {"option": {"type": "choice", "criteria": criteria, "instructions": {"goal": goal, "rules": rules}}}}
        # the first decision loads the model (seconds); later ones take milliseconds
        timeout = 20.0 if self._warm.get("laya") else 75.0
        started = time.perf_counter()
        try:
            async with self.client(timeout) as client:
                response = await client.post(self.laya_endpoint() + "/v1/systemone", json=body, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
            selected, confidence = validate_choice(payload["answers"]["option"], set(criteria))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise LocalUnavailable("Laya did not give a usable answer") from error
        except Exception as error:  # InvalidDecision from validate_choice
            raise LocalUnavailable("Laya did not give a usable answer") from error
        self._warm["laya"] = True
        index = None if selected == HUMAN else int(selected[1:])
        return LayaVote(index, float(confidence), round((time.perf_counter() - started) * 1000), str(payload.get("model") or ""))

    # ------------------------------------------------------------------------------- Ollama
    async def ollama_json(self, system: str, payload: dict, schema: dict, *, max_tokens: int = 160) -> dict:
        """One JSON object that satisfies ``schema``, written by the local model at temperature 0."""
        body = {"model": self.ollama_model(), "stream": False, "format": schema, "keep_alive": "10m",
                "options": {"temperature": 0, "seed": 7, "num_predict": max_tokens, "num_ctx": 4096},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        timeout = 40.0 if self._warm.get("ollama") else 120.0
        try:
            async with self.client(timeout) as client:
                response = await client.post(self.ollama_endpoint() + "/api/chat", json=body)
            response.raise_for_status()
            reply = json.loads(response.json()["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise LocalUnavailable("The local writing model did not give a usable answer") from error
        if not isinstance(reply, dict):
            raise LocalUnavailable("The local writing model did not return an object")
        self._warm["ollama"] = True
        return reply


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
