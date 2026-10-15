"""Reading page-supplied HTML without letting it cost more than it is worth.

A job description comes from the page's own JSON-LD, a company page from a site the page names: both are attacker-chosen
bytes. BeautifulSoup builds a tree and the old reader appended a newline to every block tag, which is quadratic in the
nesting depth: ``"<div>" * 38000`` (190 KB) kept a CPU busy for 30 s, and it ran ON the event loop that also serves the
desktop app. Three rules fix that for good:

* :func:`html_to_text` reads in ONE linear pass (``html.parser`` events, no tree, no repeated ``append``), and stops at a
  tag or depth budget instead of following the markup to its end;
* :func:`bounded_html` cuts a document at the same budgets before BeautifulSoup is asked to build a tree of it;
* :func:`off_loop` runs any such parse in a worker thread under a hard timeout, so the event loop never waits for one.

Budgets alone are not enough against a hostile site: ``html.parser`` is quadratic on a few constructs that stay inside
every budget (``"<![CDATA[" * 40000`` is 2.5 s, 38 s at the 1.5 MB cap; ``"<a b" * N`` with no ``>`` likewise) and a thread
cannot be cancelled, so a page that cost seconds kept a core busy long after the caller gave up. Two more rules:

* :func:`refuses` rejects, in one C-speed pass, the documents that are made of such constructs (a high share of
  ``<![``/``<!`` declarations, or of ``<`` that never close); they are skipped, never parsed;
* :func:`parse_isolated` runs the parse in a KILLABLE worker process (``parse_worker``) under a hard CPU limit and a hard
  wall limit, at most :data:`MAX_WORKERS` at once, niced below the sidecar. Past either limit the process is killed and
  the caller gets its default (the page is skipped): nothing a site sends can hold a core for longer than the limit.

The budgets are far above any real page (a 1.5 MB page has a few thousand tags, nested a few dozen deep).
"""

from __future__ import annotations

import asyncio
import atexit
import html as html_module
import json
import logging
import os
import re
import selectors
import struct
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

MAX_INPUT_CHARS = 400_000  # the markup of ONE description read as text (JSON-LD allows 200,000 characters per script)
MAX_TAGS = 50_000  # tags read before the rest of a document is ignored
MAX_DEPTH = 400  # nesting of unclosed tags before the rest of a document is ignored
PARSE_TIMEOUT = 10.0  # seconds the event loop waits for ONE parse (the budgets bound the work itself)

MAX_WORKERS = 2  # parse worker processes alive (and so parses running) at once, however many sessions are researching
WALL_LIMIT = 15.0  # seconds one isolated parse may take before its process is killed (a real 1.5 MB page: < 1 s of CPU;
# the wall limit is for a worker that hangs or starves, the CPU limit is the one that measures the work)
CPU_LIMIT = 4  # CPU seconds one isolated parse may use before the kernel kills its process (RLIMIT_CPU)
QUEUE_WAIT = 20.0  # seconds a parse waits for a free worker before it is skipped
START_WAIT = 30.0  # seconds a new worker (a Python start-up and its imports; a frozen sidecar is slower) may take to be ready
RECYCLE_AFTER = 200  # parses one worker serves before it is replaced (memory of a long-running process)
MAX_REPLY = 16_000_000  # bytes of one worker reply accepted
NICE = 10  # the worker yields the CPU to the sidecar that serves the desktop app
# Share of a document that may be made of the constructs html.parser handles in quadratic time (see :func:`refuses`).
MAX_BOGUS_DECLARATIONS = 100  # ``<![CDATA[`` / ``<!x`` that are neither comments nor a doctype: real pages have a handful
MAX_UNTERMINATED_SHARE = 0.25  # of the ``<`` of a document that may have no ``>`` anywhere after them

SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
BLOCKS = frozenset({"p", "div", "li", "tr", "section", "ul", "ol"})
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
                  "wbr", "command", "keygen", "menuitem"})
# Tags whose end tag HTML lets a page leave out: a second <li> closes the first. Counting them as nested would make a long,
# perfectly ordinary list look like a document nested hundreds of levels deep.
IMPLIED_END = frozenset({"li", "p", "tr", "td", "th", "dt", "dd", "option", "thead", "tbody", "tfoot"})
TAG_START = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]{0,40})")
MAX_TAG_LENGTH = 2000  # characters a tag may span (the old reader's ``[^>]{0,2000}``)


def _enter(stack: list[str], name: str) -> None:
    if name in IMPLIED_END and stack and stack[-1] == name:
        stack.pop()
    stack.append(name)


def _leave(stack: list[str], name: str) -> None:
    if name in stack:  # an end tag with no start tag is ignored; otherwise everything opened inside it is closed too
        while stack and stack.pop() != name:
            pass


class _OutOfBudget(Exception):
    """The document is larger or deeper than any real page: what was read so far is the answer."""


class _TextReader(HTMLParser):
    """Readable text with the block structure kept as newlines and headings as '## ', in a single pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping: list[str] = []
        self.open: list[str] = []
        self.tags = 0
        self._newline = True  # nothing written yet, or the last thing written ended a line

    def _write(self, text: str) -> None:
        if text:
            self.parts.append(text)
            self._newline = text.endswith("\n")

    def _break(self) -> None:
        if not self._newline:
            self._write("\n")

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if self.tags > MAX_TAGS:
            raise _OutOfBudget
        if tag in VOID:
            if tag == "br" and not self.skipping:
                self._write("\n")
            return
        _enter(self.open, tag)
        if len(self.open) > MAX_DEPTH:
            raise _OutOfBudget
        if tag in SKIP_TAGS:
            self.skipping.append(tag)
            return
        if self.skipping:
            return
        if tag in HEADINGS:
            self._break()
            self._write("## ")
        elif tag in BLOCKS:
            self._break()

    def handle_startendtag(self, tag, attrs):
        self.tags += 1
        if self.tags > MAX_TAGS:
            raise _OutOfBudget
        if tag == "br" and not self.skipping:
            self._write("\n")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        _leave(self.open, tag)
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
            return
        if tag in HEADINGS or tag in BLOCKS:
            self._write("\n")

    def handle_data(self, data):
        if not self.skipping and data:
            self._write(data)


def html_to_text(value) -> str:
    """Readable text of (possibly entity-escaped) HTML; block structure is kept as newlines, headings as '## '.
    Linear in the size of the input and bounded in tags and depth (see the module docstring)."""
    text = html_module.unescape(str(value or "")[:MAX_INPUT_CHARS])
    if "<" not in text:
        return re.sub(r"[ \t]+", " ", text).strip()
    if refuses(text):
        return ""
    reader = _TextReader()
    try:
        reader.feed(text)
        reader.close()
    except _OutOfBudget:
        pass
    except Exception:  # a pathological document must end as 'what was read so far', never as an error
        pass
    plain = "".join(reader.parts)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in plain.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _tags(markup: str):
    """``(start, closing, name, selfclosing)`` of every tag of ``markup``, in ONE linear pass: the next ``>`` is looked up
    once and reused by every ``<`` before it (a regex that scans ahead for each ``<`` is quadratic on ``"<a" * 700000``:
    23 s). A ``<`` with no ``>`` within :data:`MAX_TAG_LENGTH` characters is not a tag; with no ``>`` left at all, nothing
    after it is."""
    position, gt, size = 0, -2, len(markup)
    while True:
        position = markup.find("<", position)
        if position < 0:
            return
        if gt < position:
            gt = markup.find(">", position)
            if gt < 0:
                return
        match = TAG_START.match(markup, position)
        if match and gt - position <= MAX_TAG_LENGTH:
            yield position, match.group(1), match.group(2).lower(), markup[gt - 1] == "/" and gt - 1 >= match.end()
            position = gt + 1
        else:
            position += 1
        if position >= size:
            return


def bounded_html(markup: str, *, max_tags: int = MAX_TAGS, max_depth: int = MAX_DEPTH) -> str:
    """The document, cut where it passes the tag or nesting budget (everything before the cut is kept). A cheap scan of
    the tag names alone: no tree is built, so it costs the same however the document nests. A document :func:`refuses`
    is cut to nothing."""
    if refuses(markup):
        return ""
    stack: list[str] = []
    for count, (start, closing, name, selfclosing) in enumerate(_tags(markup), start=1):
        if closing:
            _leave(stack, name)
        elif name not in VOID and not selfclosing:
            _enter(stack, name)
        if count > max_tags or len(stack) > max_depth:
            return markup[:start]
    return markup


def refuses(markup: str) -> bool:
    """Is this document made of the constructs ``html.parser`` reads in quadratic time? Three counts, no parse:

    * ``<![`` and ``<!x`` declarations that are not comments or a doctype (``<![CDATA[`` repeated: 2.5 s per 360 KB,
      38 s at 1.5 MB). A page has a handful (``<![endif]``); one made of them is an attack, not a page.
    * ``<`` that never close: a document in which a quarter of the ``<`` have no ``>`` after them (``"<a b" * N``).

    ``<!--`` comments are not counted (server-rendered pages put thousands of ``<!-- -->`` between text nodes)."""
    if "<" not in markup:
        return False
    declarations = markup.count("<!")
    if declarations > MAX_BOGUS_DECLARATIONS:
        ordinary = markup.count("<!--") + markup.count("<!DOCTYPE") + markup.count("<!doctype")
        if declarations - ordinary > MAX_BOGUS_DECLARATIONS:
            return True
    opened = markup.count("<")
    if opened > 1000 and opened - markup.count(">") > MAX_UNTERMINATED_SHARE * opened:
        return True
    return False


async def off_loop(function, *args, default=None, timeout: float = PARSE_TIMEOUT):
    """Run a (CPU-bound) parse in a worker thread and wait for it at most ``timeout`` seconds: past that the caller gets
    ``default`` and the event loop has never been held. The budgets above bound the thread's own work."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(function, *args), timeout)
    except TimeoutError:
        return default


# --------------------------------------------------------------------------------------------- killable parse workers
logger = logging.getLogger("jobagent.extension.markup")
_HEADER = struct.Struct(">I")


class ParseFailed(Exception):
    """The parse was refused, timed out, was killed or could not run: the page is skipped. ``healthy`` says the worker
    answered (an error inside the parse), so the process may be reused; otherwise it is killed."""

    def __init__(self, message: str, healthy: bool = False) -> None:
        super().__init__(message)
        self.healthy = healthy


def worker_command() -> list[str]:
    """How to start a parse worker: a module of this package, or (frozen sidecar) the sidecar itself in its parse mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--meridian-parse"]
    return [sys.executable, "-m", "jobagent.extension.parse_worker"]


def worker_environment() -> dict[str, str]:
    """What the worker may see: no token, key or setting of the sidecar (``MERIDIAN_*`` apart from the test flag)."""
    keep = {key: value for key, value in os.environ.items()
            if not key.startswith("MERIDIAN_") and not any(word in key.upper() for word in ("TOKEN", "SECRET", "KEY", "PASS"))}
    keep["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent) + os.pathsep + keep.get("PYTHONPATH", "")
    keep["PYTHONUNBUFFERED"] = "1"
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    if os.environ.get("MERIDIAN_TEST"):
        keep["MERIDIAN_TEST"] = "1"
    return keep


class _Worker:
    """One child process and the pipes to it. Used by ONE thread at a time (see :class:`_Pool`)."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.served = 0
        self.buffer = b""

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def kill(self) -> None:
        process, self.process, self.buffer = self.process, None, b""
        if process is None:
            return
        for pipe in (process.stdin, process.stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        _REGISTRY.discard(self)

    def start(self) -> None:
        self.kill()
        self.process = subprocess.Popen(worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=worker_environment(), close_fds=True,
                                        start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent.parent))
        _REGISTRY.add(self)
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.served = 0
        reply = self._receive(time.monotonic() + START_WAIT)
        if reply != {"ready": True}:
            raise ParseFailed("worker did not start")

    def _send(self, payload: bytes, deadline: float) -> None:
        view, fd = memoryview(_HEADER.pack(len(payload)) + payload), self.process.stdin.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            while view:
                if not selector.select(max(0.0, deadline - time.monotonic())):
                    raise ParseFailed("timeout")
                try:
                    view = view[os.write(fd, view[:65536]):]
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise ParseFailed("worker closed") from exc

    def _receive(self, deadline: float):
        fd = self.process.stdout.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                if len(self.buffer) >= _HEADER.size:
                    (size,) = _HEADER.unpack_from(self.buffer)
                    if size > MAX_REPLY:
                        raise ParseFailed("reply too large")
                    if len(self.buffer) >= _HEADER.size + size:
                        body, self.buffer = self.buffer[_HEADER.size:_HEADER.size + size], self.buffer[_HEADER.size + size:]
                        return json.loads(body)
                if not selector.select(max(0.0, deadline - time.monotonic())):
                    raise ParseFailed("timeout")
                try:
                    chunk = os.read(fd, 262144)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise ParseFailed("worker closed") from exc
                if not chunk:
                    raise ParseFailed("worker exited")
                self.buffer += chunk

    def call(self, function: str, args: tuple, wall: float, cpu: int):
        if not self.alive() or self.served >= RECYCLE_AFTER:
            self.start()
        deadline = time.monotonic() + wall
        payload = json.dumps({"fn": function, "args": list(args), "cpu": cpu}, ensure_ascii=False).encode("utf-8")
        self._send(payload, deadline)
        reply = self._receive(deadline)
        self.served += 1
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise ParseFailed(str((reply or {}).get("error") if isinstance(reply, dict) else "reply"), healthy=True)
        return reply.get("result")


_REGISTRY: set[_Worker] = set()  # every live worker, so that exit (or a test) can end them all
_LOCAL = threading.local()


def _run(function: str, args: tuple, wall: float, cpu: int, queued_at: float):
    """Runs in one of the :data:`MAX_WORKERS` threads of the pool, each of which owns one worker process."""
    if time.monotonic() - queued_at > QUEUE_WAIT:
        raise ParseFailed("busy")
    worker = getattr(_LOCAL, "worker", None)
    if worker is None:
        worker = _LOCAL.worker = _Worker()
    try:
        return worker.call(function, args, wall, cpu)
    except ParseFailed as exc:
        if not exc.healthy:
            worker.kill()  # past the wall or CPU limit, or dead: a process that may be stuck in a parse is never reused
        raise
    except (ValueError, OSError) as exc:
        worker.kill()
        raise ParseFailed(type(exc).__name__) from exc


_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            from concurrent.futures import ThreadPoolExecutor
            _EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="meridian-parse")
        return _EXECUTOR


def stop_workers() -> None:
    """End every parse worker process now (exit, tests). A later parse starts a new one."""
    for worker in list(_REGISTRY):
        worker.kill()


atexit.register(stop_workers)


def live_workers() -> int:
    return sum(1 for worker in list(_REGISTRY) if worker.alive())


async def parse_isolated(function: str, *args, default=None, wall: float = WALL_LIMIT, cpu: int = CPU_LIMIT):
    """``function`` (a name in ``parse_worker``) applied to ``args`` in a killable worker process.

    Returns ``default`` when the document :func:`refuses`, the pool is busy for :data:`QUEUE_WAIT`, or the parse passes
    ``wall`` seconds of wall time or ``cpu`` seconds of CPU (the process is then killed). The event loop only awaits a
    future: it is never held, and neither a thread nor a core is left running behind a page that was given up on."""
    for argument in args:
        if isinstance(argument, str) and refuses(argument):
            return default
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_executor(), _run, function, args, wall, cpu, time.monotonic())
    except ParseFailed as exc:
        logger.info("a page was skipped: %s", exc)
        return default
