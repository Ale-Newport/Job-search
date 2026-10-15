"""Laya and Ollama as the HTTP services they are, for the extension end-to-end suite (docs/EXTENSION_DESIGN.md 5.4a).

The backend reaches them exactly as it reaches the real ones: ``GET /healthz`` and ``POST /v1/systemone`` (Laya, a choice among the
offered options) and ``GET /api/tags`` and ``POST /api/chat`` (Ollama, a JSON object that satisfies the schema it is sent). What they
answer is scripted by the scenario: ``laya_picks`` / ``ollama_picks`` map a question to the label to choose, ``ollama_values`` maps a
hint to the value to write. ``hold()`` / ``release()`` park every model call so that a scenario can look at the field while the models
"think". ``calls`` records every request (the question, the options, the facts that were read), which is how a scenario proves what a
model was, and was not, shown.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


class FakeLocalEngines:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.laya_picks: dict[str, str] = {}      # question substring -> label Laya picks ("" = asks for a person)
        self.ollama_picks: dict[str, str] = {}    # question substring -> label the writing model picks ("NONE" = nothing)
        self.ollama_values: dict[str, str] = {}   # a hint substring -> the value the writing model writes
        self._gate = threading.Event()
        self._gate.set()
        self._lock = threading.Lock()
        self.laya: _Server | None = None
        self.ollama: _Server | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "FakeLocalEngines":
        engines = self

        def handler(kind: str):
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
                    if kind == "laya" and self.path.rstrip("/") == "/healthz":
                        return self._send(200, {"ok": True, "backend": "laya-mlx"})
                    if kind == "ollama" and self.path.rstrip("/") == "/api/tags":
                        return self._send(200, {"models": [{"name": "qwen2.5:7b-instruct-q4_K_M"}]})
                    self._send(404, {"error": "not found"})

                def do_POST(self):  # noqa: N802
                    length = int(self.headers.get("Content-Length", "0") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    path = self.path.rstrip("/")
                    if kind == "laya" and path == "/v1/systemone":
                        return self._send(200, engines.laya_answer(body))
                    if kind == "ollama" and path == "/api/chat":
                        return self._send(200, engines.ollama_answer(body))
                    self._send(404, {"error": "not found"})
            return Handler

        self.laya = _Server(("127.0.0.1", 0), handler("laya"))
        self.ollama = _Server(("127.0.0.1", 0), handler("ollama"))
        for server in (self.laya, self.ollama):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._gate.set()
        for server in (self.laya, self.ollama):
            if server:
                server.shutdown()
                server.server_close()
        self.laya = self.ollama = None

    @property
    def laya_url(self) -> str:
        assert self.laya, "FakeLocalEngines not started"
        return f"http://127.0.0.1:{self.laya.server_address[1]}"

    @property
    def ollama_url(self) -> str:
        assert self.ollama, "FakeLocalEngines not started"
        return f"http://127.0.0.1:{self.ollama.server_address[1]}"

    # ------------------------------------------------------------------ scenario controls
    def hold(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    def calls_of(self, kind: str) -> list[dict]:
        return [call for call in self.calls if call["kind"] == kind]

    # ------------------------------------------------------------------ protocol
    @staticmethod
    def _scripted(table: dict[str, str], text: str):
        return next((value for key, value in table.items() if key.lower() in text.lower()), None)

    def laya_answer(self, body: dict) -> dict:
        criteria = body["questions"]["option"]["criteria"]
        labels = {key: label for key, label in criteria.items() if key != "HUMAN_REQUIRED"}
        question = str(body["state"].get("question", ""))
        with self._lock:
            self.calls.append({"kind": "laya", "question": question, "options": list(labels.values()),
                               "facts": list(body["state"].get("candidate_facts", []))})
        self._gate.wait(timeout=60)
        wanted = self._scripted(self.laya_picks, question)
        key = next((k for k, label in labels.items() if label == wanted), "HUMAN_REQUIRED")
        return {"model": "fake-laya", "answers": {"option": {
            "choice": key, "probabilities": {k: (1.0 if k == key else 0.0) for k in criteria}, "confidence": 0.9}}}

    def ollama_answer(self, body: dict) -> dict:
        payload = json.loads(body["messages"][-1]["content"])
        if "options" in payload:
            question = str(payload.get("question", ""))
            with self._lock:
                self.calls.append({"kind": "ollama_choice", "question": question, "options": list(payload["options"]),
                                   "facts": list(payload.get("candidate_facts", []))})
            self._gate.wait(timeout=60)
            wanted = self._scripted(self.ollama_picks, question) or "NONE"
            return {"message": {"content": json.dumps({"reason": "scripted", "choice": wanted if wanted in payload["options"] else "NONE",
                                                       "facts": [1]})}}
        hints = " ".join(payload.get("how_to_write", []))
        with self._lock:
            self.calls.append({"kind": "ollama_write", "question": payload.get("question", ""), "hints": list(payload.get("how_to_write", [])),
                               "value": payload.get("value")})
        self._gate.wait(timeout=60)
        written = self._scripted(self.ollama_values, hints)
        return {"message": {"content": json.dumps({"value": written if written is not None else payload.get("value", "")})}}
