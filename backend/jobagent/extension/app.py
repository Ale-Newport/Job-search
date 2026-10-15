"""The bridge application served on the loopback ports (docs/EXTENSION_DESIGN.md sections 4 and 5).

``create_bridge_app(main_app)`` returns a FastAPI app that is entirely separate from the desktop API: its own guard,
its own credential (``auth.current_client``) and its own error format ``{detail, code}``. It reads Meridian's state
through ``app.state.main.state`` (database, secrets, text_ai, ...).

Guard rules (``BridgeGuard``, a pure ASGI middleware so request bodies can be limited while they stream):

* Host must be exactly ``127.0.0.1`` or ``localhost`` with an optional numeric port, in any letter case (DNS-rebinding
  defence; no trailing dot, IPv6 literal, list or lookalike) -> 400 ``host``.
* If ``Origin`` is present it must be ``chrome-extension://<id>`` or ``safari-web-extension://<id>`` -> 403 ``origin``.
  A request without Origin is only accepted for GET (Chrome and Safari send no Origin on extension GETs); every
  mutating request must carry one.
* While Meridian restores a backup every endpoint answers 503 ``restoring``. Bridge requests are NOT counted in
  ``app.state.active_requests`` (polling would otherwise block backup restore).
* Bodies are JSON only and at most 1 MiB (also while streaming; Content-Length must be plain ASCII digits) -> 413
  ``too_large``; 30 s deadline per request (``/poll`` may wait up to 25 s and gets 45 s).
* OPTIONS answers 204 and echoes a valid extension origin (Safari without a host grant needs a preflight).
* No access log; responses are ``Cache-Control: no-store`` and ``nosniff``.

Unauthenticated: ``GET /v1/hello`` and the pairing routes. Everything else uses ``Depends(auth.current_client)``.
``POST /v1/disconnect`` (additive to the table in the design) lets the extension's own "Disconnect" revoke its credential.
Routes of workstream B (sessions, suggest, poll, ...) live in ``routes.py`` and are mounted under ``/v1``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from . import PROTOCOL, assets
from .api import (
    BRIDGE_EDITABLE, bridge_status, read_extension_settings, validate_patch, write_extension_settings,
)
from .auth import VERSION_HEADER, VERSION_RE, Client, current_client
from .pairing import ORIGIN_RE, browser_for_origin, manager_for
from .schemas import PairStartRequest, StatusResponse

logger = logging.getLogger("meridian.bridge")

MAX_BODY_BYTES = 1024 * 1024
DEADLINE_SECONDS = 30
POLL_DEADLINE_SECONDS = 45
ALLOWED_METHODS = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
ALLOWED_HEADERS = "Authorization, Content-Type, X-Meridian-Version"
STATUS_CODES = {
    400: "invalid", 401: "unauthorized", 403: "origin", 404: "not_found", 405: "invalid", 409: "conflict",
    410: "expired", 413: "too_large", 415: "invalid", 422: "invalid", 429: "rate_limited", 500: "internal",
    501: "unavailable", 502: "unavailable", 503: "unavailable", 504: "unavailable",
}


class _TooLarge(BaseException):
    """Raised from ``receive`` when a body exceeds the limit. A BaseException so that FastAPI's own body parsing
    (``except Exception`` -> 400) cannot swallow it: the guard answers 413 ``too_large``."""


HOST_RE = re.compile(r"(127\.0\.0\.1|localhost)(:[0-9]{1,5})?")
TEST_HOST_RE = re.compile(r"testserver(:[0-9]{1,5})?")  # Starlette's TestClient, only under MERIDIAN_TEST


def host_allowed(host: str) -> bool:
    """The Host header must be exactly ``127.0.0.1`` or ``localhost`` with an optional numeric port (any case).

    Everything else is refused: a name that merely ends or starts like loopback, a trailing dot, an IPv6 literal (the
    listener is IPv4 only), a port that is not a number, a comma list or whitespace inside the value."""
    name = host.strip().lower()
    if HOST_RE.fullmatch(name):
        return True
    return bool(os.environ.get("MERIDIAN_TEST")) and TEST_HOST_RE.fullmatch(name) is not None


class BridgeGuard:
    """Pure ASGI middleware implementing the guard rules of the module docstring."""

    def __init__(self, app, main_app=None):
        self.app = app
        self.main_app = main_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        method = scope["method"]
        cors = self._cors(origin, headers)
        if not host_allowed(headers.get("host", "")):
            return await self._reply(scope, receive, send, 400, "This address is not served by the Meridian bridge", "host", cors)
        if origin is not None and not ORIGIN_RE.fullmatch(origin):
            return await self._reply(scope, receive, send, 403, "Origin is not permitted", "origin", cors)
        if method == "OPTIONS":
            if origin is None:
                return await self._reply(scope, receive, send, 403, "Origin is not permitted", "origin", cors)
            return await self._reply(scope, receive, send, 204, None, None, cors)
        if self.main_app is not None and getattr(self.main_app.state, "restoring", False):
            return await self._reply(scope, receive, send, 503, "Meridian is restoring a backup", "restoring", cors)
        if origin is None and method not in {"GET", "HEAD"}:
            return await self._reply(scope, receive, send, 403, "Origin is required", "origin", cors)
        length = headers.get("content-length")
        declared = 0
        if length is not None:
            length = length.strip()
            # ASCII digits only: int() would also accept "-5", "+5", "1_0" and non-ASCII digits.
            if not (length.isascii() and length.isdigit()):
                return await self._reply(scope, receive, send, 400, "Invalid content length", "invalid", cors)
            declared = int(length)
            if declared > MAX_BODY_BYTES:
                return await self._reply(scope, receive, send, 413, "The request is too large", "too_large", cors)
        has_body = declared > 0 or "transfer-encoding" in headers
        if method in {"POST", "PUT", "PATCH"} and has_body:
            kind = headers.get("content-type", "").split(";")[0].strip().lower()
            if kind != "application/json":
                return await self._reply(scope, receive, send, 415, "Send JSON (application/json)", "invalid", cors)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    raise _TooLarge()
            return message

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                raw = list(message.get("headers", []))
                present = {name.lower() for name, _ in raw}
                for name, value in {**self._security(), **cors}.items():
                    if name.lower().encode() not in present:
                        raw.append((name.lower().encode(), value.encode()))
                message = {**message, "headers": raw}
            await send(message)

        deadline = POLL_DEADLINE_SECONDS if scope["path"].endswith("/poll") else DEADLINE_SECONDS
        try:
            async with asyncio.timeout(deadline):
                await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            if not started:
                await self._reply(scope, receive, send, 413, "The request is too large", "too_large", cors)
        except TimeoutError:
            if not started:
                await self._reply(scope, receive, send, 504, "Meridian took too long to answer", "unavailable", cors)
        except Exception as exc:
            logger.error("bridge_internal_error %s", type(exc).__name__)
            if not started:
                await self._reply(scope, receive, send, 500, "Meridian hit an unexpected problem", "internal", cors)

    @staticmethod
    def _security() -> dict[str, str]:
        return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}

    @staticmethod
    def _cors(origin: str | None, headers: Headers) -> dict[str, str]:
        if not origin or not ORIGIN_RE.fullmatch(origin):
            return {}
        result = {
            "Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Methods": ALLOWED_METHODS,
            "Access-Control-Allow-Headers": ALLOWED_HEADERS, "Access-Control-Max-Age": "600",
        }
        if headers.get("access-control-request-private-network", "").lower() == "true":
            result["Access-Control-Allow-Private-Network"] = "true"
        return result

    async def _reply(self, scope, receive, send, status: int, detail: str | None, code: str | None, cors: dict):
        headers = {**self._security(), **cors}
        if status == 204:
            response = Response(status_code=204, headers=headers)
        else:
            response = JSONResponse({"detail": detail, "code": code}, status_code=status, headers=headers)
        await response(scope, receive, send)


# ----------------------------------------------------------------------------------------------- errors


def error_response(status: int, detail: str, code: str | None = None, headers=None) -> JSONResponse:
    code = code or STATUS_CODES.get(status, "internal" if status >= 500 else "invalid")
    return JSONResponse({"detail": detail, "code": code}, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "The request could not be completed"
        return error_response(exc.status_code, detail, getattr(exc, "code", None), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Only the location is reported: the offending value may be page content.
        errors = exc.errors()
        where = ".".join(str(part) for part in errors[0]["loc"][1:]) if errors else ""
        return error_response(422, f"Invalid request{': ' + where if where else ''}", "invalid")

    @app.exception_handler(ValidationError)
    async def model_error(request: Request, exc: ValidationError):
        return error_response(422, "Invalid request", "invalid")

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return error_response(400, str(exc)[:300] or "Invalid request", "invalid")

    import sqlite3

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request: Request, exc: sqlite3.OperationalError):
        return error_response(503, "Meridian's local database is busy. Try again in a moment.", "unavailable")

    @app.exception_handler(sqlite3.IntegrityError)
    async def database_conflict(request: Request, exc: sqlite3.IntegrityError):
        return error_response(409, "This conflicts with something Meridian already has", "conflict")


# ----------------------------------------------------------------------------------------------- application


def _browser_from_agent(agent: str) -> str | None:
    if "Chrome/" in agent or "Chromium/" in agent or "CriOS/" in agent:
        return "chrome"
    if "Safari/" in agent:
        return "safari"
    return None


def create_bridge_app(main_app) -> FastAPI:
    app = FastAPI(title="Meridian bridge", version=str(PROTOCOL), docs_url=None, redoc_url=None, openapi_url=None)
    app.state.main = main_app
    app.state.port = None
    app.state.routes_loaded = False
    app.state.routes_error = None
    app.add_middleware(BridgeGuard, main_app=main_app)
    install_error_handlers(app)

    def request_port(request: Request) -> int:
        if app.state.port:
            return int(app.state.port)
        host = request.headers.get("host", "")
        tail = host.rsplit(":", 1)[-1] if ":" in host else ""
        return int(tail) if tail.isdigit() else 0

    @app.get("/v1/hello")
    async def hello(request: Request, client: str | None = Query(None, max_length=64), nonce: str | None = Query(None, max_length=128)):
        manager = manager_for(main_app)
        port = request_port(request)
        proof, revoked_proof = manager.proofs(client, nonce, port)
        version = (request.headers.get(VERSION_HEADER) or "").strip()
        if proof is None and VERSION_RE.fullmatch(version):
            # An unpaired extension (the version header cannot be sent by a web page without a preflight that this
            # bridge refuses) lets the app say "found the extension, connect it".
            origin = request.headers.get("origin")
            browser = browser_for_origin(origin) or _browser_from_agent(request.headers.get("user-agent", ""))
            manager.note_contact(browser, origin, version)
        return {
            "app": "Meridian", "bridge": PROTOCOL, "app_version": main_app.version,
            "latest_extension_version": assets.bundled_version(), "port": port, "proof": proof,
            # additive: a revoked client is told so with a proof it can verify (silence never means "revoked"), and the
            # digest of the bundled build lets a rebuilt extension with an unchanged version number be noticed
            "revoked": revoked_proof is not None, "revoked_proof": revoked_proof,
            "latest_extension_digest": assets.bundled_digest(),
        }

    @app.post("/v1/pair/start")
    async def pair_start(request: Request, payload: PairStartRequest | None = None):
        payload = payload or PairStartRequest()
        return manager_for(main_app).start(request.headers.get("origin"), payload.client_name, payload.extension_version)

    @app.get("/v1/pair/{pairing_id}")
    async def pair_status(pairing_id: str, request: Request):
        return manager_for(main_app).status(pairing_id, request.headers.get("origin"))

    @app.post("/v1/pair/{pairing_id}/cancel")
    async def pair_cancel(pairing_id: str, request: Request):
        return manager_for(main_app).cancel(pairing_id, request.headers.get("origin"))

    @app.post("/v1/disconnect")
    async def disconnect(client: Client = Depends(current_client)):
        """The extension's own "Disconnect": revoke this browser's credential too, so the app does not list it."""
        manager_for(main_app).revoke(client.id)
        return {"disconnected": True}

    @app.get("/v1/status")
    async def status(client: Client = Depends(current_client)):
        data = await asyncio.to_thread(bridge_status, main_app)
        return StatusResponse(**data).model_dump()

    @app.patch("/v1/settings")
    async def settings(patch: dict[str, Any] = Body(...), client: Client = Depends(current_client)):
        clean = validate_patch(patch, BRIDGE_EDITABLE)
        await asyncio.to_thread(write_extension_settings, main_app, clean)
        return read_extension_settings(main_app.state.db)

    try:
        from .routes import router as routes_router
    except Exception as exc:  # ImportError until workstream B lands; any other failure is just as loud
        app.state.routes_error = f"{type(exc).__name__}: {exc}"
        logger.error("bridge_routes_unavailable %s", app.state.routes_error)
    else:
        app.include_router(routes_router, prefix="/v1")
        app.state.routes_loaded = True
    return app

