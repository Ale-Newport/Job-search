"""A fake OpenAI-compatible text model on loopback for the extension e2e suite.

The backend talks to it exactly like to any ``openai-compatible`` provider (``POST {base}/chat/completions``; a
loopback endpoint is free and needs no Keychain secret). It implements what ``ApplicationAnswerLLM`` requires:

* the connection probe (``intent == CONNECTION_TEST``) so ``POST /api/ai/answers/test`` succeeds and records the
  onboarding probe that marks generation as available;
* the generation call (schema ``ANSWER_SCHEMA``): the answer is assembled ONLY from sentences that are verbatim in
  the supplied verified facts and employer research, so the deterministic evidence validator accepts it, and it cites
  those facts and sources. Employer research reaches the draft only when the backend passed it in
  ``company_context``: that is how the suite proves research -> draft end to end;
* the independent review call (the request contains ``generated_answer``).

Controls for scenarios (all thread safe): ``hold()``/``release()`` park generation calls, ``delay`` sleeps before a
generation answer, ``mode`` switches to failure behaviour. ``calls`` records every request.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fake-answer-model"
SCORE_NAMES = ("question_relevance", "specificity", "factual_support", "clarity", "professional_quality",
               "company_relevance", "instruction_compliance", "length_compliance", "redundancy", "naturalness")


class _Server(ThreadingHTTPServer):
    """The listen backlog of the standard server is 5: the backend's burst of concurrent model calls (draft + independent review of
    several questions) must never be refused on a loaded machine (TEST-01)."""

    daemon_threads = True
    request_queue_size = 256


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text or "")) if s.strip()]


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.delay = 0.0
        self.mode = "normal"  # normal | http_500 | malformed | human_input
        self._gate = threading.Event()
        self._gate.set()
        self._lock = threading.Lock()
        self._per_question: dict[str, int] = {}
        self.server: _Server | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "FakeLLM":
        llm = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - silence
                return

            def _send(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    return self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
                self._send(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"error": "not found"})
                status, payload = llm.respond(body)
                self._send(status, payload)

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._gate.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @property
    def base_url(self) -> str:
        assert self.server, "FakeLLM not started"
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    # ------------------------------------------------------------------ scenario controls
    def hold(self) -> None:
        """Park generation calls until ``release()`` (reviews and probes are never held)."""
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    def generation_calls(self) -> list[dict]:
        return [call for call in self.calls if call["kind"] == "generate"]

    def reset_calls(self) -> None:
        with self._lock:
            self.calls.clear()
            self._per_question.clear()

    # ------------------------------------------------------------------ protocol
    def respond(self, body: dict) -> tuple[int, dict]:
        try:
            payload = json.loads(body["messages"][-1]["content"])
        except (KeyError, IndexError, TypeError, ValueError):
            return 400, {"error": "unexpected request"}
        kind = ("review" if "generated_answer" in payload
                else "probe" if payload.get("intent") == "CONNECTION_TEST" else "generate")
        context = payload.get("company_context") or []
        record = {
            "kind": kind, "question": payload.get("exact_question", ""), "question_id": payload.get("question_id"),
            "intent": payload.get("intent"), "company_urls": [c.get("url") for c in context if isinstance(c, dict)],
            "has_company_context": bool(context), "job_title": (payload.get("job") or {}).get("title"),
            "field_label": (payload.get("field_context") or {}).get("label", ""), "time": time.time(),
            "payload_keys": sorted(payload),
        }
        with self._lock:
            self.calls.append(record)
        if kind == "generate":
            self._gate.wait(timeout=60)
            if self.delay:
                time.sleep(self.delay)
            if self.mode == "http_500":
                return 500, {"error": {"message": "fake provider failure"}}
            if self.mode == "malformed":
                return 200, self.envelope("this is not json")
        generated = {"probe": self.probe, "review": self.review, "generate": self.generate}[kind](payload)
        return 200, self.envelope(json.dumps(generated))

    @staticmethod
    def envelope(content: str) -> dict:
        return {"id": "chatcmpl-fake", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}}

    def probe(self, payload: dict) -> dict:
        answer = "Sample Candidate is the sample candidate."
        return {"question_id": payload["question_id"], "intent": payload["intent"], "answer": answer,
                "facts_used": ["connection-probe"], "selected_options": [], "selected_roles": [],
                "company_claims": [], "claims": [{"text": answer, "fact_ids": ["connection-probe"]}],
                "evidence_quality": "HIGH", "word_count": len(answer.split()), "follows_instructions": True,
                "answers_question": True, "unsupported_claims": [], "requires_human_input": False, "missing_facts": []}

    def review(self, payload: dict) -> dict:
        return {"question_id": payload["question_id"], "passed": True, "scores": {name: 0.95 for name in SCORE_NAMES},
                "unsupported_claims": [], "issues": [], "reason": "The answer is grounded in the supplied evidence."}

    def generate(self, payload: dict) -> dict:
        qid, intent = payload["question_id"], payload["intent"]
        base = {"question_id": qid, "intent": intent, "selected_options": [], "selected_roles": [],
                "evidence_quality": "HIGH", "follows_instructions": True, "answers_question": True,
                "unsupported_claims": [], "requires_human_input": False, "missing_facts": []}
        facts = payload.get("verified_facts") or []
        stories = [f for f in facts if f.get("key") in ("story", "contribution") and isinstance(f.get("value"), str)]
        if self.mode == "human_input" or not stories:
            return {**base, "answer": None, "facts_used": [], "company_claims": [], "claims": [], "word_count": 0,
                    "requires_human_input": True, "missing_facts": ["a verified project or experience to describe"]}
        stories.sort(key=lambda f: (f.get("category") != "project", f["key"] != "story", f["id"]))
        story = stories[0]
        with self._lock:
            variant = self._per_question.get(payload["exact_question"], 0) % 3
            self._per_question[payload["exact_question"]] = self._per_question.get(payload["exact_question"], 0) + 1
        job = payload.get("job") or {}
        company = str(job.get("company") or "the company")
        title = str(job.get("title") or "role")
        company_claims, company_sentence = [], None
        for item in payload.get("company_context") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            mission = next((s for s in sentences(item.get("text")) if re.search(r"\bmission\b", s, re.I)), None)
            if mission:
                core = re.sub(r"^Our mission is", "its mission is", mission).rstrip(".")
                company_sentence = f"I am drawn to {company} because {core}."
                company_claims = [{"text": core, "source_urls": [item["url"]]}]
                break
        if company_sentence is None:
            company_sentence = f"I would bring that experience to the {title} role at {company}."
        story_text = story["value"].strip()
        parts = {0: [story_text, company_sentence], 1: [company_sentence, story_text], 2: [story_text]}[variant]
        answer = " ".join(parts)
        if variant == 2:
            company_claims = []
        return {**base, "answer": answer, "facts_used": [story["id"]], "company_claims": company_claims,
                "claims": [{"text": story_text, "fact_ids": [story["id"]]}], "word_count": len(answer.split())}
