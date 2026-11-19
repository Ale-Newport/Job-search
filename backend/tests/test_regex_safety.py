"""Untrusted text (employer pages, form labels, recruitment emails) must never stall Meridian through regex backtracking.

Each case feeds a 100,000-character input in the shape that used to backtrack quadratically (or, for PLAIN_NAME and an
employer's own `pattern` attribute, exponentially) and requires it to finish quickly. The rewritten expressions match
exactly what the originals matched; these tests pin the speed, the behaviour is pinned by the rest of the suite."""
from __future__ import annotations

import asyncio

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import jobagent.automation  # noqa: F401  (the application's import order)
from jobagent import discovery, mail
from jobagent.intelligence import engine, legal, professional_context, semantics
from jobagent.intelligence.formatting import AnswerFormatResolver, page_pattern

N = 100_000
BUDGET = 1.0  # seconds; every case below takes a few milliseconds, the old expressions took seconds to minutes


def fast(call, text):
    started = time.perf_counter()
    call(text)
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET, f"{elapsed:.2f}s on {len(text)} characters"


@pytest.mark.parametrize("call,text", [
    (lambda s: legal.CHOICE_HINT.sub(" ", s), "yes" + " " * N + "x"),
    (lambda s: engine.INSTRUCTION_IN_LABEL.search(s), "(system" + " " * N),
    (lambda s: professional_context.contains_private_candidate_data(s), "+" * N),
    (lambda s: semantics.PLAIN_NAME.match(s), "legal " * (N // 6) + "!"),
    (lambda s: mail.classify_message("Your application", s), "job" + " " * N),
])
def test_untrusted_text_is_handled_in_linear_time(call, text):
    fast(call, text)


def test_plain_name_still_recognises_legal_names():
    for label in ("Full legal name", "Please type your full legal name", "Legal name", "Nombre completo", "Applicant name"):
        assert semantics.PLAIN_NAME.match(" ".join(label.lower().split())), label


@pytest.mark.parametrize("pattern", [r"[0-9]{5}", r"\d{3}-\d{3}-\d{4}", r"[A-Za-z ]+", r"(\+44|0)\d{9,10}",
                                     r"[^@\s]+@[^@\s]+\.[a-z]{2,}", r"https?://.+", r"\d+(\.\d{1,2})?"])
def test_ordinary_field_patterns_are_still_checked(pattern):
    assert page_pattern(pattern) is not None


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a|a)*$", r"(\w+\s?)*$", r"(a*)*b", r"(.*a){20}", r"(\d)\1", "a" * 300])
def test_a_hostile_field_pattern_is_refused(pattern):
    assert page_pattern(pattern) is None


# Without '$' (which the format reader takes as a currency hint), so the answer reaches the pattern check itself.
@pytest.mark.parametrize("pattern", [r"(a+)+b", r"(a|a)*c", r"(\w+\s?)*#", r"(a*)*b", r"(.*a){20}", r"(\d)\1"])
def test_a_hostile_field_pattern_is_reported_unsupported_not_run(pattern):
    started = time.perf_counter()
    result = AnswerFormatResolver.validate("a" * 40 + "!", "Your answer", {"type": "text", "pattern": pattern})
    assert time.perf_counter() - started < BUDGET
    assert result == "UNSUPPORTED_FIELD_PATTERN"


def test_a_field_pattern_still_rejects_a_wrong_format():
    assert AnswerFormatResolver.validate("12ab", "Postcode", {"type": "text", "pattern": r"[0-9]{4}"}) == "ANSWER_FORMAT_ERROR"


def test_workday_requisition_is_the_text_after_the_first_underscore():
    assert discovery.ats_identity("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/London/Engineer_R-1234_5")[1] == \
        "workday:acme.wd3.myworkdayjobs.com:R-1234_5"
    assert discovery.ats_identity("https://acme.wd3.myworkdayjobs.com/careers/job/London/Engineer")[1] is None


def test_discovery_connects_to_the_address_it_validated(monkeypatch):
    """A second DNS answer cannot redirect the request: the connection goes to the checked address, with the original
    Host header (example.invalid never resolves, so reaching the server proves the address was pinned)."""
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers.get("Host")
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    async def validated(url):
        return ["127.0.0.1"]

    monkeypatch.setattr(discovery, "validate_public_url", validated)
    try:
        body, content_type, final = asyncio.run(discovery.fetch(f"http://jobs.example.invalid:{port}/feed?x=1"))
    finally:
        server.shutdown()
    assert body == b'{"ok": true}' and content_type == "application/json"
    assert final == f"http://jobs.example.invalid:{port}/feed?x=1"
    assert seen["host"] == f"jobs.example.invalid:{port}"
