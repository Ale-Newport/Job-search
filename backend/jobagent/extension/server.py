"""The loopback listener of the bridge: a second uvicorn server living inside the sidecar's event loop.

* The socket is bound by Meridian itself (``SO_REUSEADDR``) on the first free port of ``47615-47624``, so a port
  conflict can never reach uvicorn's ``sys.exit(3)`` path, and ``serve()`` is additionally wrapped in
  ``except SystemExit`` because inside an asyncio task a ``SystemExit`` would end the primary API's process.
* ``EmbeddedServer`` never touches process signals (the primary server owns SIGINT/SIGTERM).
* ``MERIDIAN_BRIDGE_PORT`` overrides the candidates: ``47700``, ``47700-47709`` or ``0`` (OS assigned, used by tests).
* ``bridge_enabled()``: on for real runs, off under ``MERIDIAN_TEST`` unless ``MERIDIAN_BRIDGE`` or
  ``MERIDIAN_BRIDGE_PORT`` asks for it; ``MERIDIAN_BRIDGE=0`` always disables it.
* ``start()`` never raises; ``status()`` feeds ``GET /api/extension``.
* A connection that has not delivered a COMPLETE request head (request line and headers) within ``HEADER_TIMEOUT_SECONDS``
  is closed, and at most ``MAX_UNFINISHED_PER_PEER`` such connections are held per peer address (the oldest is closed to
  make room). uvicorn only times out the gap BETWEEN requests, so a client that opens a socket and sends nothing, or
  sends the head one byte at a time, would otherwise hold a slot of ``limit_concurrency`` for as long as it liked and 64 of
  them answered every real request 503. Every process on this Mac connects from 127.0.0.1, so "per peer" is in practice
  "all local clients": the cap is on connections still waiting to say what they want, which a real client is for
  milliseconds. It stops counting at the first complete head, so the long poll (up to 25 s) is not affected.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time

import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from . import BRIDGE_PORTS

logger = logging.getLogger("meridian.bridge")
HOST = "127.0.0.1"
HEADER_TIMEOUT_SECONDS = 10.0
MAX_UNFINISHED_PER_PEER = 16


def bridge_ports(value: str | None = None) -> list[int]:
    """Candidate ports from ``MERIDIAN_BRIDGE_PORT``: a port, ``LOW-HIGH`` or ``0``; default 47615-47624."""
    value = (os.environ.get("MERIDIAN_BRIDGE_PORT", "") if value is None else value).strip()
    if not value:
        return list(BRIDGE_PORTS)
    low, _, high = value.partition("-")
    try:
        first, last = int(low), int(high or low)
    except ValueError as exc:
        raise ValueError("MERIDIAN_BRIDGE_PORT must be a port or a LOW-HIGH range") from exc
    if not 0 <= first <= last <= 65535:
        raise ValueError("MERIDIAN_BRIDGE_PORT must be a port or a LOW-HIGH range")
    return [first] if first == 0 else list(range(first, last + 1))


def bridge_enabled(explicit: bool | None = None) -> bool:
    """Whether ``create_app(bridge=...)`` should start the listener."""
    if explicit is not None:
        return explicit
    flag = os.environ.get("MERIDIAN_BRIDGE", "").lower()
    if flag in {"0", "false", "off", "no"}:
        return False
    if flag in {"1", "true", "on", "yes"} or os.environ.get("MERIDIAN_BRIDGE_PORT"):
        return True
    return not os.environ.get("MERIDIAN_TEST")


def guarded_protocol(header_timeout: float | None = None, max_unfinished: int | None = None) -> type[H11Protocol]:
    """An HTTP/1.1 protocol class for one listener: the stock uvicorn one plus the head deadline and the per-peer cap of the
    module docstring. The class owns the registry of unfinished connections, so two listeners never share it."""
    timeout = HEADER_TIMEOUT_SECONDS if header_timeout is None else header_timeout
    limit = MAX_UNFINISHED_PER_PEER if max_unfinished is None else max_unfinished
    unfinished: dict[str, dict[int, "Guarded"]] = {}  # peer address -> connections (oldest first) still waiting for a full head

    class Guarded(H11Protocol):
        guard_timer: asyncio.TimerHandle | None = None
        guard_peer: str | None = None

        def connection_made(self, transport) -> None:  # type: ignore[override]
            super().connection_made(transport)
            self._wait_for_head()

        def data_received(self, data: bytes) -> None:
            super().data_received(data)
            if self.transport is None or self.transport.is_closing():
                return
            if self.conn.their_state is h11.IDLE and self.conn.trailing_data[0]:
                self._wait_for_head()  # part of a head is buffered and the rest has not come: the deadline runs from its first byte
            elif self.conn.their_state is not h11.IDLE:
                self._head_done()  # a complete head arrived: nothing more to watch (an idle keep-alive has its own timer)

        def connection_lost(self, exc) -> None:
            self._head_done()
            super().connection_lost(exc)

        def _wait_for_head(self) -> None:
            if self.guard_timer is not None:
                return
            peer = self.client[0] if self.client else "?"
            mine = unfinished.setdefault(peer, {})
            mine[id(self)] = self
            self.guard_peer = peer
            while len(mine) > limit:
                oldest = next(iter(mine.values()))
                if oldest is self:
                    break
                oldest._expire()
            self.guard_timer = self.loop.call_later(timeout, self._expire)

        def _head_done(self) -> None:
            if self.guard_timer is not None:
                self.guard_timer.cancel()
                self.guard_timer = None
            mine = unfinished.get(self.guard_peer or "")
            if mine is not None:
                mine.pop(id(self), None)
                if not mine:
                    unfinished.pop(self.guard_peer or "", None)
            self.guard_peer = None

        def _expire(self) -> None:
            self._head_done()
            if self.transport is not None and not self.transport.is_closing():
                self.transport.close()

    Guarded.unfinished = unfinished  # type: ignore[attr-defined]  # tests and diagnostics
    return Guarded


class EmbeddedServer(uvicorn.Server):
    """A uvicorn.Server that lives inside another server's event loop and never touches process signals."""

    def install_signal_handlers(self) -> None:  # uvicorn < 0.29
        return None

    @contextlib.contextmanager
    def capture_signals(self):  # uvicorn >= 0.29
        yield


def bind_first_free(host: str, ports) -> socket.socket:
    """Bind a listening socket on the first port that is free. Raises OSError when none is."""
    last: OSError | None = None
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # restart straight after a crash/TIME_WAIT
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            last = exc
            continue
        sock.listen(128)
        sock.setblocking(False)
        return sock
    raise OSError(f"no free bridge port: {last}")


async def listener_name(port: int) -> str | None:
    """Best-effort NAME (never the command line) of the process listening on ``port``; None when unknown."""
    if os.environ.get("MERIDIAN_TEST") or not os.path.exists("/usr/sbin/lsof"):
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fc",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), 2)
    except (OSError, asyncio.TimeoutError):
        return None
    for line in out.decode(errors="replace").splitlines():
        if line.startswith("c") and len(line) > 1:
            return "".join(ch for ch in line[1:] if ch.isprintable())[:40] or None
    return None


class BridgeServer:
    """Owns the bridge listener. ``start()`` never raises; ``status()`` describes the outcome for the app UI."""

    def __init__(self, bridge_app, ports=None, host: str = HOST):
        self.app, self.host = bridge_app, host
        self.ports = list(ports) if ports is not None else bridge_ports()
        self.server = self.task = self.sock = None
        self.port: int | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.conflict: str | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        async with self._lock:
            return await self._start()

    async def _start(self) -> bool:
        if self.server is not None and self.server.started and not (self.task and self.task.done()):
            return True
        self.error = self.conflict = None
        try:
            self.sock = bind_first_free(self.host, self.ports)
        except OSError as exc:
            self.error = str(exc)
            self.port = None
            logger.warning("bridge_unavailable")
            return False
        self.port = self.sock.getsockname()[1]
        with contextlib.suppress(AttributeError):
            self.app.state.port = self.port  # the hello proof binds the answer to the port that served it
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_config=None, access_log=False, lifespan="off",
            server_header=False, date_header=False, proxy_headers=False, timeout_keep_alive=5, http=guarded_protocol(),
            timeout_graceful_shutdown=3, limit_concurrency=64, h11_max_incomplete_event_size=64 * 1024,
        )
        self.server = EmbeddedServer(config)
        self.task = asyncio.create_task(self._run(self.server))
        for _ in range(150):  # at most 3 s
            if self.server.started or self.task.done():
                break
            await asyncio.sleep(0.02)
        if not self.server.started:
            self.error = self.error or "bridge failed to start"
            await self._stop()
            return False
        self.started_at = time.time()
        registry = self._registry()
        if registry is not None:
            registry.end_shutdown()  # a restart: polls wait again
        return True

    async def _run(self, server) -> None:
        try:
            await server.serve(sockets=[self.sock])
        except SystemExit as exc:  # uvicorn calls sys.exit() on startup failure; never let it end the process
            self.error = f"bridge stopped (exit {exc.code})"
        except asyncio.CancelledError:
            raise
        except Exception:
            self.error = "bridge crashed"
            logger.warning("bridge_crashed", exc_info=False)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    def _registry(self):
        """The assistant's session registry of the main application (None when there is none, e.g. a bare test app)."""
        main = getattr(getattr(self.app, "state", None), "main", None)
        return getattr(getattr(main, "state", None), "extension_sessions", None)

    async def _stop(self) -> None:
        # Long-polling pages hold their request open for up to 25 s. uvicorn's graceful stop waits for open requests (and
        # then cancels them with an error log), so they are answered FIRST: the quit takes milliseconds, not seconds.
        registry = self._registry()
        if registry is not None and self.server is not None:
            registry.begin_shutdown()
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 6)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.task
        if self.sock is not None:
            with contextlib.suppress(OSError):
                self.sock.close()
        self.server = self.task = self.sock = None
        self.started_at = None

    async def restart(self) -> bool:
        """Stop and start again (the app's "Try again" button after a port conflict)."""
        async with self._lock:
            await self._stop()
            return await self._start()

    async def explain_failure(self) -> None:
        """Fill ``conflict`` with the name of the process that holds the first candidate port (best effort)."""
        if self.error and self.conflict is None and self.ports and self.ports[0]:
            self.conflict = await listener_name(self.ports[0]) or ""

    def status(self) -> dict:
        running = bool(self.server and self.server.started)
        return {
            "running": running,
            "host": self.host,
            "port": self.port if running else None,
            "ports": list(self.ports),
            "error": self.error,
            "conflict_process": self.conflict or None,
            "started_at": self.started_at,
        }
