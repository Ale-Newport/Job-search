"""The process that reads page-supplied markup for :func:`jobagent.extension.markup.parse_isolated`.

It exists so that a hostile page can be given up on: ``html.parser`` is quadratic on a few constructs, a thread cannot be
cancelled, but a process can be killed. The parent writes one request at a time (``4-byte length + JSON``) and reads one
reply; the parent enforces the wall limit by killing this process, the kernel enforces the CPU limit set here
(``RLIMIT_CPU``: ``SIGXCPU`` ends the process, the parent sees the pipe close and skips the page).

* run as ``python -m jobagent.extension.parse_worker`` (development, tests) or, in the frozen sidecar,
  ``<sidecar> --meridian-parse`` (``scripts/sidecar_entry.py``);
* it holds no credentials and opens no file or socket: the parent gives it a minimal environment;
* it exits when its input closes, so it never outlives the sidecar that started it;
* only the functions :func:`_functions` lists can be asked for (names, never code).
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time

READY = b'{"ready": true}'
HEADER = struct.Struct(">I")
MAX_REQUEST = 24_000_000


def _functions() -> dict:
    from . import markup, research
    table = {"html_to_text": markup.html_to_text, "page": research.read_page, "ats_job": research.parse_ats_job}
    if os.environ.get("MERIDIAN_TEST"):  # lets the tests prove the limits without a page that happens to be slow
        table["selftest_spin"] = lambda seconds: _spin(float(seconds))
        table["selftest_echo"] = lambda value: value
        table["selftest_sleep"] = lambda seconds: time.sleep(float(seconds))
    return table


def _spin(seconds: float) -> str:
    end, total = time.process_time() + seconds, 0
    while time.process_time() < end:
        for number in range(200_000):  # real arithmetic: a loop of clock reads is mostly system time and is not what is limited
            total += number
    return "spun"


def _read(stream, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _cpu_used() -> float:
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def _limit_cpu(seconds: int) -> None:
    """The kernel ends this process once it has spent ``seconds`` more CPU: a limit that holds even if the parent is busy."""
    try:
        import resource
        soft = int(_cpu_used()) + max(1, int(seconds)) + 1
        hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
        resource.setrlimit(resource.RLIMIT_CPU, (soft if hard == resource.RLIM_INFINITY else min(soft, hard), hard))
    except (ImportError, OSError, ValueError):
        pass


def main() -> int:
    source = sys.stdin.buffer
    sink = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)  # a stray print can never corrupt the reply stream
    try:
        os.nice(int(os.environ.get("MERIDIAN_PARSE_NICE", "10")))
    except (OSError, ValueError):
        pass
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass
    functions = _functions()

    def send(payload: bytes) -> None:
        sink.write(HEADER.pack(len(payload)) + payload)

    send(READY)
    while True:
        header = _read(source, HEADER.size)
        if header is None:
            return 0
        (size,) = HEADER.unpack(header)
        if size > MAX_REQUEST:
            return 2
        body = _read(source, size)
        if body is None:
            return 0
        try:
            request = json.loads(body)
            function = functions[request["fn"]]
            _limit_cpu(int(request.get("cpu") or 4))
            reply = {"ok": True, "result": function(*request.get("args", []))}
        except Exception as exc:  # a pathological document is "no answer", never a crash that costs the next request
            reply = {"ok": False, "error": type(exc).__name__}
        send(json.dumps(reply, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
