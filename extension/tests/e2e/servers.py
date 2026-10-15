"""Loopback servers for the extension e2e suite. Nothing here talks to the real internet.

* ``FixtureServers``   the application-form pages on TWO origins (``127.0.0.1:<p1>`` and ``localhost:<p2>``) so a
                       form can sit in a cross-origin (out-of-process) iframe. Serves extension/tests/fixtures first
                       and falls back to the read-only backend/tests/browser/fixtures. ``{{TOKEN}}`` placeholders in
                       .html files are filled per request (see ``tokens``).
* ``CompanySite``      a fake employer website (home/about/mission/values/careers + a JSON-LD job page + robots.txt)
                       on ``http://127.0.0.1:<port>`` for the research scenarios. The backend only reaches loopback when
                       started with ``MERIDIAN_EXTENSION_RESEARCH_LOOPBACK=1`` under ``MERIDIAN_TEST`` (see
                       ``research_hosts_env``; the hook is owned by workstream B, jobagent/extension/research.py).
* ``DelayProxy``       a TCP proxy in front of the real bridge that holds every answer back for a while (a slow
                       bridge). The worker must refuse it: the bridge's hello names the port it listens on, and a
                       proxy cannot answer for that port, so nothing credentialed ever goes through.
* ``Squatter``         a process pretending to be Meridian on a bridge port without knowing any secret: it must never
                       be sent a token.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "extension" / "tests" / "fixtures"
BACKEND_FIXTURES = REPO / "backend" / "tests" / "browser" / "fixtures"

COMPANY_NAME = "Acme Robotics"
# What the greenhouse fixture's JSON-LD names as the employer's website until a CompanySite is started (then its real
# loopback origin is used). Nothing resolves this name: without a CompanySite research simply finds nothing.
COMPANY_SITE = "https://acme-robotics.test"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".json": "application/json", ".pdf": "application/pdf",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D401 - silence
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # The default listen backlog is 5. A page makes dozens of requests at once (scripts, styles, frames, polls) and a loaded
    # machine (this suite next to other work) then RESETS or drops some of them: the page comes up empty and the scenario fails
    # for a reason that is not the product's (TEST-01). Every loopback server of the suite uses this class.
    request_queue_size = 256


def serve(handler_cls, port: int = 0) -> _Server:
    server = _Server(("127.0.0.1", port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server: ThreadingHTTPServer | None) -> None:
    if server is not None:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------------------------- fixture pages
def route_for(path: str, query: str) -> str | None:
    """Fixture name for a virtual URL (CAPTCHA widgets are served from local stand-ins, never from a real provider)."""
    if path == "/recaptcha/api2/anchor":
        return "recaptcha_badge.html" if "size=invisible" in query else "recaptcha_anchor.html"
    return None


class FixtureServers:
    """Two origins serving the same pages; ``top`` is 127.0.0.1, ``frame`` is localhost (a different site)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []  # every request: {origin, method, path, headers}
        self.generated: dict[str, str] = {}  # pages made by a scenario (pages.py): served under their name before any file of that name
        self.tokens: dict[str, str] = {}
        self._servers: list[_Server] = []
        self.top = self._start("127.0.0.1")
        self.frame = self._start("localhost")
        self.tokens.update(FRAME_ORIGIN=self.frame, TOP_ORIGIN=self.top, COMPANY_SITE=COMPANY_SITE,
                           COMPANY_NAME=COMPANY_NAME)

    def _start(self, hostname: str) -> str:
        owner = self

        class Handler(QuietHandler):
            def do_GET(self):  # noqa: N802
                self._reply(send_body=True)

            def do_HEAD(self):  # noqa: N802
                self._reply(send_body=False)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                owner.requests.append({"origin": hostname, "method": "POST", "path": self.path,
                                       "headers": dict(self.headers)})
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _reply(self, send_body: bool) -> None:
                owner.requests.append({"origin": hostname, "method": self.command, "path": self.path,
                                       "headers": dict(self.headers)})
                parts = urlsplit(self.path)
                name = route_for(parts.path, parts.query) or parts.path.lstrip("/") or "decoy.html"
                made = owner.generated.get(name)
                located = None if made is not None else owner.locate(name)
                if located is None and made is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = made.encode() if made is not None else located.read_bytes()
                suffix = Path(name).suffix if made is not None else located.suffix
                if suffix == ".html":
                    text = body.decode()
                    for key, value in owner.tokens.items():
                        text = text.replace("{{" + key + "}}", value)
                    text = text.replace("{{SELF}}", f"http://{hostname}:{self.server.server_address[1]}")
                    # {{QUERY}}: this request's own query string (with its '?'), so a page can hand its variant to a frame it embeds
                    body = text.replace("{{QUERY}}", f"?{parts.query}" if parts.query else "").encode()
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPES.get(suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if send_body:
                    self.wfile.write(body)

        server = serve(Handler)
        self._servers.append(server)
        return f"http://{hostname}:{server.server_address[1]}"

    @staticmethod
    def locate(name: str) -> Path | None:
        for root in (FIXTURES, BACKEND_FIXTURES):
            candidate = (root / name).resolve()
            if candidate.is_file() and candidate.is_relative_to(root.resolve()) and not candidate.name.startswith("."):
                return candidate
        return None

    def url(self, name: str, query: str = "") -> str:
        return f"{self.top}/{name}" + (f"?{query}" if query else "")

    def requests_with_header(self, header: str) -> list[dict]:
        needle = header.lower()
        return [r for r in self.requests if any(k.lower() == needle for k in r["headers"])]

    def close(self) -> None:
        for server in self._servers:
            stop(server)
        self._servers.clear()


# ---------------------------------------------------------------------------------------------- employer website
COMPANY_PAGES = {
    "/": "index.html", "/about": "about.html", "/mission": "mission.html", "/values": "values.html",
    "/careers": "careers.html", "/careers/software-engineer": "job_software_engineer.html",
    "/careers/backend-engineer": "job_backend_engineer.html",
    "/robots.txt": "robots.txt",
}


class CompanySite:
    """The fake employer. ``requests`` records everything the research code sends, for privacy assertions."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status_overrides: dict[str, int] = {}
        owner = self

        class Handler(QuietHandler):
            def do_GET(self):  # noqa: N802
                owner.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                path = urlsplit(self.path).path.rstrip("/") or "/"
                status = owner.status_overrides.get(path)
                name = COMPANY_PAGES.get(path)
                if status or not name:
                    self.send_response(status or 404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = (FIXTURES / "company" / name).read_bytes()
                if name.endswith(".html"):
                    body = body.replace(b"{{COMPANY_SITE}}", owner.origin.encode())
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPES.get(Path(name).suffix, "text/html; charset=utf-8"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = serve(Handler)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @staticmethod
    def research_hosts_env() -> dict[str, str]:
        """Environment that lets the backend's research fetcher reach loopback (honoured only under MERIDIAN_TEST)."""
        return {"MERIDIAN_EXTENSION_RESEARCH_LOOPBACK": "1"}

    def paths(self) -> list[str]:
        return [urlsplit(r["path"]).path for r in self.requests]

    def close(self) -> None:
        stop(self.server)


# ---------------------------------------------------------------------------------------------- bridge impostor
class Squatter:
    """Answers like Meridian's bridge on ``port`` but cannot prove it (no secret). Logs every request."""

    def __init__(self, port: int = 0, *, proof: str | None = None) -> None:
        self.requests: list[dict] = []
        self.proof = proof
        owner = self

        class Handler(QuietHandler):
            # No keep-alive: a pooled connection to a squatter that "went away" would keep answering for it.
            protocol_version = "HTTP/1.0"

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                owner.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body.decode("utf-8", "replace")})
                payload = {"detail": "unauthorized", "code": "unauthorized"}
                status = 401
                if urlsplit(self.path).path == "/v1/hello":
                    status = 200
                    payload = {"app": "Meridian", "bridge": 1, "app_version": "0.0.0-squatter",
                               "latest_extension_version": "0.0.0", "port": self.server.server_address[1]}
                    if owner.proof is not None:
                        payload["proof"] = owner.proof
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = do_DELETE = _handle  # noqa: N815

            def do_OPTIONS(self):  # noqa: N802
                owner.requests.append({"method": "OPTIONS", "path": self.path, "headers": dict(self.headers), "body": ""})
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = serve(Handler, port)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def credentialed_requests(self) -> list[dict]:
        """Requests that carried an Authorization header (there must never be one)."""
        return [r for r in self.requests if any(k.lower() == "authorization" for k in r["headers"])]

    def close(self) -> None:
        stop(self.server)


class PairingImpostor:
    """A program that answers hello with the right SHAPE but no proof, then approves any pairing at once with a credential of
    its own (and from then on proves THAT one, like any bridge). Final security review, MEDIUM-1: it must never replace the
    credential of the real Meridian. Logs every request."""

    def __init__(self, port: int = 0) -> None:
        self.client_id = "11111111-2222-3333-4444-555555555555"
        self.secret = secrets.token_urlsafe(32)
        self.token = f"mrd1.{self.client_id}.{self.secret}"
        self.key = hashlib.sha256(self.secret.encode()).digest()
        self.paired = False
        self.requests: list[dict] = []
        owner = self

        class Handler(QuietHandler):
            protocol_version = "HTTP/1.0"

            def _send(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, body: bytes = b"") -> None:
                owner.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body.decode("utf-8", "replace")})

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                parts = urlsplit(self.path)
                port = self.server.server_address[1]
                if parts.path == "/v1/hello":
                    query = dict(item.split("=", 1) for item in parts.query.split("&") if "=" in item)
                    proof = None
                    if owner.paired and query.get("client") == owner.client_id and "nonce" in query:
                        message = f"meridian-bridge-v1|{query['nonce']}|{port}".encode()
                        proof = base64.urlsafe_b64encode(hmac.new(owner.key, message, hashlib.sha256).digest()).decode().rstrip("=")
                    return self._send(200, {"app": "Meridian", "bridge": 1, "app_version": "0.1.0", "latest_extension_version": "0.1.0",
                                            "port": port, "proof": proof})
                if parts.path.startswith("/v1/pair/"):
                    owner.paired = True
                    return self._send(200, {"state": "approved", "token": owner.token, "client_id": owner.client_id})
                if self.headers.get("Authorization") == f"Bearer {owner.token}":
                    return self._send(200, {})  # a bridge that honours the credential it issued (the browser works with it)
                self._send(401, {"detail": "unauthorized", "code": "unauthorized"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                self._record(self.rfile.read(length) if length else b"")
                if urlsplit(self.path).path == "/v1/pair/start":
                    return self._send(200, {"pairing_id": "A" * 32, "code": "123456", "expires_in": 120, "poll_after_ms": 1500})
                self._send(200, {})

        self.server = serve(Handler, port)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def posts(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]

    def credentialed_requests(self) -> list[dict]:
        return [r for r in self.requests if any(k.lower() == "authorization" for k in r["headers"])]

    def close(self) -> None:
        stop(self.server)


# ---------------------------------------------------------------------------------------------- slow bridge
class DelayProxy:
    """asyncio TCP proxy on a NEW loopback port towards ``upstream_port``; every chunk coming back is delayed by ``delay``
    seconds. ``seen`` keeps what the client sent (lower-cased request heads) so a scenario can prove no credential passed."""

    def __init__(self, upstream_port: int, delay: float = 3.0) -> None:
        self.upstream_port, self.delay = upstream_port, delay
        self.seen: list[str] = []
        self.connections = 0
        self.server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> "DelayProxy":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    @property
    def credentialed(self) -> list[str]:
        return [head for head in self.seen if "authorization:" in head]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.upstream_port)
        except OSError:
            writer.close()
            return

        async def forward(source, target, *, delayed: bool) -> None:
            try:
                while chunk := await source.read(65536):
                    if delayed:
                        await asyncio.sleep(self.delay)
                    else:
                        self.seen.append(chunk[:2000].decode("latin-1").lower())
                    target.write(chunk)
                    await target.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                target.close()

        await asyncio.gather(forward(reader, up_writer, delayed=False), forward(up_reader, writer, delayed=True),
                             return_exceptions=True)
        if task is not None:
            self._tasks.discard(task)

    def close(self) -> None:
        if self.server is not None:
            self.server.close()
        for task in list(self._tasks):
            task.cancel()
