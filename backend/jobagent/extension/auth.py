"""Bridge authentication: pairing tokens, the verified ``Client`` and the ``current_client`` dependency.

Token format: ``mrd1.<client_id>.<secret>`` where ``client_id`` is a UUID (not secret) and ``secret`` is 256 bits of
``secrets.token_urlsafe`` randomness. Only ``sha256(secret)`` (hex) is stored in ``extension_clients.token_hash``; the
plaintext exists once, in the pairing response. A request authenticates with ``Authorization: Bearer <token>``:

1. the row is looked up by the non-secret ``client_id`` (no timing channel on the index),
2. ``sha256(secret)`` is compared with ``hmac.compare_digest`` (also for unknown clients, against a dummy hash),
3. a revoked client is rejected exactly like an unknown one (401 ``unauthorized``),
4. when the request carries an ``Origin`` it must equal the origin recorded at pairing (the token is bound to the
   extension that was approved; a missing Origin is only possible on GET, the app guard enforces that),
5. ``last_seen_at`` (and the reported extension version) are written at most once a minute per client.

Routes written by other workstreams depend only on ``Client``, ``current_client`` and ``BridgeError``; tests override
the dependency with ``app.dependency_overrides[current_client] = lambda: Client(...)``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

TOKEN_PREFIX = "mrd1"
TOKEN_RE = re.compile(r"^mrd1\.([0-9a-f-]{36})\.([A-Za-z0-9_-]{43})$")
# Optional request header carrying the extension version (also accepted as ``?v=`` on hello).
VERSION_HEADER = "x-meridian-version"
VERSION_RE = re.compile(r"^[0-9A-Za-z.+_-]{1,32}$")
TOUCH_INTERVAL_SECONDS = 60.0
_DUMMY_HASH = hashlib.sha256(b"meridian-unknown-client").hexdigest()
_touch_lock = threading.Lock()


@dataclass(frozen=True)
class Client:
    id: str  # extension_clients.id (also embedded in the token as mrd1.<id>.<secret>)
    name: str
    browser: str  # 'chrome' | 'safari'
    origin: str  # verbatim Origin of the pairing request
    version: str = ""


class BridgeError(HTTPException):
    """HTTP error that carries the bridge ``ErrorCode`` explicitly (rendered as ``{detail, code}``)."""

    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


# ----------------------------------------------------------------------------------------------- token helpers


def new_token(client_id: str) -> tuple[str, str]:
    """-> (plaintext shown to the extension exactly once, sha256 hex stored in extension_clients.token_hash)."""
    secret = secrets.token_urlsafe(32)
    return f"{TOKEN_PREFIX}.{client_id}.{secret}", hashlib.sha256(secret.encode()).hexdigest()


def split_token(authorization: str | None) -> tuple[str | None, str | None]:
    """'Bearer mrd1.<client_id>.<secret>' -> (client_id, secret); (None, None) for anything else."""
    scheme, _, value = (authorization or "").partition(" ")
    match = TOKEN_RE.fullmatch(value.strip()) if scheme == "Bearer" else None
    return (match.group(1), match.group(2)) if match else (None, None)


def verify(row, secret: str) -> bool:
    """Constant-time check of ``secret`` against an extension_clients row (also false for a revoked client)."""
    candidate = hashlib.sha256(secret.encode()).hexdigest()
    stored = row["token_hash"] if row else _DUMMY_HASH
    matches = hmac.compare_digest(stored, candidate)
    return bool(row) and row["revoked_at"] is None and matches


# ----------------------------------------------------------------------------------------------- dependency


def main_state(request: Request):
    """``app.state`` of the main Meridian application (db, secrets, text_ai, ...), from inside the bridge app."""
    return request.app.state.main.state


def extension_enabled(db) -> bool:
    """The master switch. Read straight from the settings row so the bridge never loads the whole settings table."""
    row = db.one("SELECT value FROM settings WHERE key='extension_enabled'")
    if row is None:
        return True
    return row["value"].strip().lower() != "false"


def _monotonic() -> float:
    return time.monotonic()


def _touch(state, db, client_id: str, version: str) -> None:
    touched = getattr(state, "extension_touch", None)
    if touched is None:
        touched = state.extension_touch = {}
    stamp = _monotonic()
    with _touch_lock:
        if stamp - touched.get(client_id, -TOUCH_INTERVAL_SECONDS) < TOUCH_INTERVAL_SECONDS:
            return
        touched[client_id] = stamp
    from ..db import now

    try:
        db.execute(
            "UPDATE extension_clients SET last_seen_at=?,version=CASE WHEN ?='' THEN version ELSE ? END WHERE id=?",
            (now(), version, version, client_id),
        )
    except sqlite3.Error:
        # Bookkeeping only: a busy database must not fail the request that triggered it. The next request tries again.
        with _touch_lock:
            touched.pop(client_id, None)


def authenticate(request: Request) -> Client:
    """Verify the bearer token of ``request`` against ``extension_clients``; raises ``BridgeError`` otherwise."""
    state = main_state(request)
    client_id, secret = split_token(request.headers.get("authorization"))
    if client_id is None:
        raise BridgeError(401, "Connect this browser to Meridian", "unauthorized")
    row = state.db.one("SELECT * FROM extension_clients WHERE id=?", (client_id,))
    if not verify(row, secret):
        raise BridgeError(401, "Connect this browser to Meridian", "unauthorized")
    origin = request.headers.get("origin")
    if origin and origin != row["origin"]:
        # The credential belongs to the extension that was approved; another extension cannot reuse it.
        raise BridgeError(401, "Connect this browser to Meridian", "unauthorized")
    if not extension_enabled(state.db):
        raise BridgeError(403, "The browser assistant is turned off in Meridian", "disabled")
    version = (request.headers.get(VERSION_HEADER) or "").strip()
    version = version if VERSION_RE.fullmatch(version) else ""
    _touch(state, state.db, client_id, version)
    return Client(
        id=row["id"], name=row["name"], browser=row["browser"], origin=row["origin"], version=version or row["version"]
    )


def current_client(request: Request) -> Client:
    """FastAPI dependency: the verified paired client, or HTTP 401 ``unauthorized`` (403 ``disabled`` when off)."""
    client = getattr(request.state, "client", None)
    if client is None:
        client = request.state.client = authenticate(request)
    return client
