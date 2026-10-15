"""Pairing state machine, rate limits and the hello proof of the Meridian bridge.

Pairing (docs/EXTENSION_DESIGN.md 4.2). One row per attempt in ``extension_pairings``; states::

    pending --approve--> approved --first same-origin poll--> claimed
       |  \\--deny--> denied
       \\--120 s / cancel / superseded / restart--> expired        (approved + token never claimed also -> expired)

* ``start`` creates a pending pairing with a random six-digit code. The extension shows the code; the user types it
  into the native dialog (``approval.py``) or presses Approve in the app. The first decision wins.
* One pending pairing per origin (partial unique index); a second ``start`` from the same origin supersedes the first.
  At most five starts per minute per origin (the origin is compared case-insensitively, so a spelling variant does not
  earn a new budget) and at most twenty per minute overall (in memory, so the tracking table stays tiny whatever
  arrives), at most eight pending pairings overall.
* Wrong codes typed into the dialog are counted in ``attempts``; the third wrong code denies the pairing.
* The plaintext token ``mrd1.<client_id>.<secret>`` exists only in ``PairingManager._issued`` for two minutes and is
  popped by the first successful poll of the pairing (same ``Origin`` whenever the browser sends one; Chrome and Safari
  omit Origin on GET, so the unguessable 192-bit pairing id is the capability in that case). Only ``sha256(secret)`` is
  ever stored. A restart of the sidecar expires every open pairing (the user simply connects again).

Hello proof (docs/EXTENSION_DESIGN.md 4.3). Exact byte format, identical in Python and WebCrypto::

    key   = SHA-256 digest (32 raw bytes) of the secret, the third dot-separated part of the token, as UTF-8
            (the server keeps the same value as hex in extension_clients.token_hash)
    msg   = UTF-8 bytes of  "meridian-bridge-v1|" + nonce + "|" + port
            nonce = the ``nonce`` query value exactly as sent (base64url alphabet, 16-64 characters, NOT decoded)
            port  = decimal TCP port of the listener that answered
    proof = base64url(HMAC-SHA256(key, msg)) without "=" padding

    // WebCrypto
    const key = await crypto.subtle.importKey("raw", await crypto.subtle.digest("SHA-256", enc.encode(secret)),
        {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
    const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`meridian-bridge-v1|${nonce}|${port}`));
    proof = base64url(new Uint8Array(mac)).replace(/=+$/, "")

The server only produces ``proof`` for an active (not revoked) client. A client that was revoked gets a DIFFERENT
statement of the same kind, so that "revoked" is something Meridian proves and never something inferred from silence::

    revoked_proof = base64url(HMAC-SHA256(key, "meridian-bridge-v1-revoked|" + nonce + "|" + port))   (same key, same encoding)

answered together with ``revoked: true`` (the flag alone proves nothing: a listener that does not know the secret cannot
produce the proof). The two messages have different prefixes, so a revoked proof can never pass for an active one. An
unknown client id (a database that never knew it, or was restored from an older backup) gets neither: an extension
that holds a credential and hears neither a ``proof`` nor a ``revoked_proof`` cannot tell Meridian from an impostor and
treats the listener as unknown (it asks the user before pairing with it, and keeps its credential until it recognises
the real app again).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import CHROME_EXTENSION_IDS
from .auth import BridgeError, extension_enabled, new_token

logger = logging.getLogger("meridian.bridge")

PAIRING_TTL_SECONDS = 120
TOKEN_TTL_SECONDS = 120
POLL_AFTER_MS = 1500
MAX_STARTS_PER_MINUTE = 5
MAX_STARTS_PER_MINUTE_TOTAL = 20
MAX_PENDING = 8
MAX_ATTEMPTS = 3
DIALOG_SECONDS = 90
DETECTED_WINDOW_SECONDS = 15 * 60
STALE_CLIENT_SECONDS = 24 * 3600
PAIRING_RETENTION_DAYS = 7
PROOF_PREFIX = b"meridian-bridge-v1|"
REVOKED_PROOF_PREFIX = b"meridian-bridge-v1-revoked|"
ORIGIN_RE = re.compile(r"^(chrome|safari-web)-extension://[A-Za-z0-9-]{8,64}$")
PAIRING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
BROWSER_LABELS = {"chrome": "Chrome", "safari": "Safari"}
_manager_lock = threading.Lock()


# ----------------------------------------------------------------------------------------------- origin helpers


def browser_for_origin(origin: str | None) -> str | None:
    """'chrome' for chrome-extension://, 'safari' for safari-web-extension://; never trusted from a request body."""
    if not origin or not ORIGIN_RE.fullmatch(origin):
        return None
    return "safari" if origin.startswith("safari-web-extension://") else "chrome"


def recognised_ids() -> set[str]:
    extra = {item.strip() for item in os.environ.get("MERIDIAN_EXTENSION_IDS", "").split(",") if item.strip()}
    return set(CHROME_EXTENSION_IDS) | extra


def is_recognised_origin(origin: str | None) -> bool:
    """False for a chrome-extension:// origin that is not the pinned Meridian id (unpacked/other builds).

    Safari origins carry a per-install UUID and cannot be recognised: they rely on the pairing approval alone."""
    browser = browser_for_origin(origin)
    if browser is None:
        return False
    if browser == "safari":
        return True
    return origin.split("://", 1)[1] in recognised_ids()


def clean_text(value: str, limit: int = 60) -> str:
    """Control characters stripped, whitespace collapsed, truncated: attacker-controlled display text."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value or "")).strip()[:limit]


# ----------------------------------------------------------------------------------------------- hello proof


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def hello_proof(token_hash_hex: str, nonce: str, port: int) -> str:
    """The proof of the module docstring. ``token_hash_hex`` is extension_clients.token_hash (sha256 of the secret)."""
    key = bytes.fromhex(token_hash_hex)
    message = PROOF_PREFIX + nonce.encode() + b"|" + str(int(port)).encode()
    return b64url(hmac.new(key, message, hashlib.sha256).digest())


def revoked_proof(token_hash_hex: str, nonce: str, port: int) -> str:
    """The proof that this client WAS known and is now revoked (module docstring): same key, its own message prefix."""
    key = bytes.fromhex(token_hash_hex)
    message = REVOKED_PROOF_PREFIX + nonce.encode() + b"|" + str(int(port)).encode()
    return b64url(hmac.new(key, message, hashlib.sha256).digest())


# ----------------------------------------------------------------------------------------------- manager


@dataclass
class _Issued:
    token: str = field(repr=False)  # a repr (log line, assertion message, debugger) must never show the credential
    client_id: str
    origin: str
    deadline: datetime


def manager_for(app) -> "PairingManager":
    """The single PairingManager of a main application (created on first use, shared by the app API and the bridge)."""
    manager = getattr(app.state, "extension_pairing", None)
    if manager is None:
        with _manager_lock:
            manager = getattr(app.state, "extension_pairing", None)
            if manager is None:
                manager = app.state.extension_pairing = PairingManager(app)
    return manager


def _cancel_task(task: asyncio.Task) -> None:
    loop = task.get_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if loop is running:
        task.cancel()
    elif not loop.is_closed():
        loop.call_soon_threadsafe(task.cancel)


class PairingManager:
    def __init__(self, app, clock=None):
        self.app = app
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._issued: dict[str, _Issued] = {}
        self._starts: dict[str, deque] = {}
        self._dialogs: dict[str, asyncio.Task] = {}
        self._notices: set[asyncio.Task] = set()
        self._contacts: dict[str, dict] = {}
        self.recover()

    @property
    def db(self):
        return self.app.state.db

    # ------------------------------------------------------------------------------------------- bookkeeping

    def recover(self) -> None:
        """Plaintext tokens do not survive a restart, so every unfinished pairing ends here."""
        stamp = self.clock().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE extension_clients SET revoked_at=? WHERE revoked_at IS NULL AND id IN "
                "(SELECT client_id FROM extension_pairings WHERE state='approved' AND client_id IS NOT NULL)",
                (stamp,),
            )
            conn.execute(
                "UPDATE extension_pairings SET state='expired',decided_at=? WHERE state IN ('pending','approved')",
                (stamp,),
            )
            cutoff = (self.clock() - timedelta(days=PAIRING_RETENTION_DAYS)).isoformat()
            conn.execute("DELETE FROM extension_pairings WHERE created_at<?", (cutoff,))

    def expire_stale(self) -> None:
        current = self.clock()
        stamp = current.isoformat()
        expired = [
            row["id"]
            for row in self.db.query("SELECT id FROM extension_pairings WHERE state='pending' AND expires_at<=?", (stamp,))
        ]
        if expired:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE extension_pairings SET state='expired',decided_at=? WHERE state='pending' AND expires_at<=?",
                    (stamp, stamp),
                )
            for pairing_id in expired:
                self._stop_dialog(pairing_id)
        with self._lock:
            lapsed = [key for key, issued in self._issued.items() if issued.deadline <= current]
            lapsed = [(key, self._issued.pop(key)) for key in lapsed]
        for pairing_id, issued in lapsed:
            self._expire_unclaimed(pairing_id, issued.client_id)

    def _expire_unclaimed(self, pairing_id: str, client_id: str | None) -> None:
        stamp = self.clock().isoformat()
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE extension_pairings SET state='expired',decided_at=? WHERE id=? AND state='approved'",
                (stamp, pairing_id),
            ).rowcount
            if changed and client_id:
                conn.execute("UPDATE extension_clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (stamp, client_id))

    def _row(self, pairing_id: str):
        if not isinstance(pairing_id, str) or not PAIRING_ID_RE.fullmatch(pairing_id):
            return None
        return self.db.one("SELECT * FROM extension_pairings WHERE id=?", (pairing_id,))

    def _stop_dialog(self, pairing_id: str) -> None:
        with self._lock:
            task = self._dialogs.pop(pairing_id, None)
        if task is not None and task is not _current_task():
            _cancel_task(task)

    def _rate_limit(self, origin: str) -> None:
        """Five starts a minute per origin and twenty overall. The key is the lower-cased origin, so spelling variants
        of one extension share a budget, and the overall cap keeps the table (and the dialogs it would trigger) small
        however many distinct origins a local process invents."""
        key, current = origin.lower(), self.clock()
        horizon = current - timedelta(seconds=60)
        with self._lock:
            for name in list(self._starts):
                recent = self._starts[name]
                while recent and recent[0] < horizon:
                    recent.popleft()
                if not recent:
                    del self._starts[name]
            own = self._starts.get(key)
            if (own is not None and len(own) >= MAX_STARTS_PER_MINUTE) or sum(
                len(recent) for recent in self._starts.values()
            ) >= MAX_STARTS_PER_MINUTE_TOTAL:
                raise BridgeError(
                    429, "Too many connection attempts. Wait a minute and try again.", "rate_limited", {"Retry-After": "60"}
                )
            self._starts.setdefault(key, deque()).append(current)

    # ------------------------------------------------------------------------------------------- extension side

    def start(self, origin: str | None, client_name: str = "", extension_version: str = "") -> dict:
        browser = browser_for_origin(origin)
        if browser is None:
            raise BridgeError(403, "Pairing must come from the Meridian browser extension", "origin")
        if not extension_enabled(self.db):
            raise BridgeError(403, "The browser assistant is turned off in Meridian", "disabled")
        self.expire_stale()
        self._rate_limit(origin)
        self.note_contact(browser, origin, clean_text(extension_version, 32))
        name = clean_text(client_name) or f"{BROWSER_LABELS[browser]} on this Mac"
        version = clean_text(extension_version, 32)
        pairing_id, code = secrets.token_urlsafe(24), f"{secrets.randbelow(1_000_000):06d}"
        created = self.clock()
        with self.db.transaction() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM extension_pairings WHERE state='pending'").fetchone()[0]
            superseded = [
                row["id"] for row in conn.execute("SELECT id FROM extension_pairings WHERE origin=? AND state='pending'", (origin,))
            ]
            if pending - len(superseded) >= MAX_PENDING:
                raise BridgeError(429, "Too many pending connection requests. Try again in a moment.", "rate_limited")
            conn.execute(
                "UPDATE extension_pairings SET state='expired',decided_at=? WHERE origin=? AND state='pending'",
                (created.isoformat(), origin),
            )
            conn.execute(
                "INSERT INTO extension_pairings(id,code,client_name,browser,origin,version,state,attempts,client_id,"
                "created_at,expires_at,decided_at) VALUES(?,?,?,?,?,?,'pending',0,NULL,?,?,NULL)",
                (
                    pairing_id, code, name, browser, origin, version, created.isoformat(),
                    (created + timedelta(seconds=PAIRING_TTL_SECONDS)).isoformat(),
                ),
            )
        for old in superseded:
            self._stop_dialog(old)
        self._announce(pairing_id)
        return {"pairing_id": pairing_id, "code": code, "expires_in": PAIRING_TTL_SECONDS, "poll_after_ms": POLL_AFTER_MS}

    def status(self, pairing_id: str, origin: str | None) -> dict:
        self.expire_stale()
        row = self._row(pairing_id)
        if row is None:
            raise BridgeError(404, "This connection request does not exist", "not_found")
        if origin is not None and origin != row["origin"]:
            raise BridgeError(403, "This connection request belongs to another extension", "origin")
        state = row["state"]
        if state != "approved":
            return {"state": state}
        with self._lock:
            issued = self._issued.pop(pairing_id, None)
        if issued is None:
            self._expire_unclaimed(pairing_id, row["client_id"])
            return {"state": "expired"}
        with self.db.transaction() as conn:
            conn.execute("UPDATE extension_pairings SET state='claimed' WHERE id=? AND state='approved'", (pairing_id,))
        return {"state": "approved", "token": issued.token, "client_id": issued.client_id}

    def cancel(self, pairing_id: str, origin: str | None) -> dict:
        self.expire_stale()
        row = self._row(pairing_id)
        if row is None:
            raise BridgeError(404, "This connection request does not exist", "not_found")
        if origin != row["origin"]:
            raise BridgeError(403, "This connection request belongs to another extension", "origin")
        if row["state"] == "pending":
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE extension_pairings SET state='expired',decided_at=? WHERE id=? AND state='pending'",
                    (self.clock().isoformat(), pairing_id),
                )
            self._stop_dialog(pairing_id)
        elif row["state"] == "approved":
            with self._lock:
                self._issued.pop(pairing_id, None)
            self._expire_unclaimed(pairing_id, row["client_id"])
        return {"state": self._row(pairing_id)["state"]}

    # ------------------------------------------------------------------------------------------- user side

    def pending(self) -> list[dict]:
        """Open pairings for the desktop UI (they include the code the user must compare)."""
        self.expire_stale()
        rows = self.db.query("SELECT * FROM extension_pairings WHERE state='pending' ORDER BY created_at")
        return [dict(row, recognized=is_recognised_origin(row["origin"])) for row in rows]

    def approve(self, pairing_id: str, via: str = "app") -> dict:
        """Create the client and release the token to the polling extension. First decision wins."""
        self.expire_stale()
        row = self._row(pairing_id)
        self._require_open(row)
        if row["attempts"] >= MAX_ATTEMPTS:
            raise BridgeError(409, "Too many wrong codes. Connect again from the extension.", "conflict")
        client_id = str(uuid4())
        token, token_hash = new_token(client_id)
        current = self.clock()
        stamp = current.isoformat()
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE extension_pairings SET state='approved',decided_at=? WHERE id=? AND state='pending' AND expires_at>?",
                (stamp, pairing_id, stamp),
            ).rowcount
            if changed != 1:
                raise BridgeError(409, "This connection request was already answered", "conflict")
            conn.execute(
                "INSERT INTO extension_clients(id,name,browser,origin,token_hash,version,created_at,last_seen_at,revoked_at) "
                "VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                (client_id, row["client_name"], row["browser"], row["origin"], token_hash, row["version"], stamp),
            )
            conn.execute("UPDATE extension_pairings SET client_id=? WHERE id=?", (client_id, pairing_id))
            # Reinstalling the extension (or clearing its storage) leaves an unusable credential behind: retire the
            # ones of this origin that have been idle for a day, never a recently used one (another profile).
            idle = (current - timedelta(seconds=STALE_CLIENT_SECONDS)).isoformat()
            conn.execute(
                "UPDATE extension_clients SET revoked_at=? WHERE origin=? AND id!=? AND revoked_at IS NULL "
                "AND COALESCE(last_seen_at,created_at)<?",
                (stamp, row["origin"], client_id, idle),
            )
        with self._lock:
            self._issued[pairing_id] = _Issued(
                token, client_id, row["origin"], current + timedelta(seconds=TOKEN_TTL_SECONDS)
            )
        self._stop_dialog(pairing_id)
        logger.info("extension_pairing_approved via=%s browser=%s", via, row["browser"])
        return {"id": pairing_id, "client_id": client_id, "browser": row["browser"], "state": "approved"}

    def deny(self, pairing_id: str, via: str = "app") -> dict:
        self.expire_stale()
        row = self._row(pairing_id)
        self._require_open(row)
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE extension_pairings SET state='denied',decided_at=? WHERE id=? AND state='pending'",
                (self.clock().isoformat(), pairing_id),
            ).rowcount
        if changed != 1:
            raise BridgeError(409, "This connection request was already answered", "conflict")
        self._stop_dialog(pairing_id)
        logger.info("extension_pairing_denied via=%s", via)
        return {"id": pairing_id, "state": "denied"}

    @staticmethod
    def _require_open(row) -> None:
        if row is None:
            raise BridgeError(404, "This connection request does not exist", "not_found")
        if row["state"] == "expired":
            raise BridgeError(410, "This request expired. Click Connect in the extension to start again.", "expired")
        if row["state"] != "pending":
            raise BridgeError(409, "This connection request was already answered", "conflict")

    def wrong_code(self, pairing_id: str) -> int:
        """Count a wrong code typed into the dialog; the third one denies the pairing. -> attempts so far."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE extension_pairings SET attempts=attempts+1 WHERE id=? AND state='pending'", (pairing_id,))
            row = conn.execute("SELECT attempts FROM extension_pairings WHERE id=?", (pairing_id,)).fetchone()
        attempts = row["attempts"] if row else MAX_ATTEMPTS
        if attempts >= MAX_ATTEMPTS:
            try:
                self.deny(pairing_id, via="attempts")
            except BridgeError:
                pass
        return attempts

    def revoke(self, client_id: str) -> bool:
        """Disconnect a browser: its next request is answered 401 and the extension returns to 'unpaired'."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id,revoked_at FROM extension_clients WHERE id=?", (client_id,)).fetchone()
            if row is None:
                raise BridgeError(404, "This browser is not connected", "not_found")
            if row["revoked_at"] is None:
                conn.execute("UPDATE extension_clients SET revoked_at=? WHERE id=?", (self.clock().isoformat(), client_id))
        with self._lock:
            for key in [key for key, issued in self._issued.items() if issued.client_id == client_id]:
                del self._issued[key]
        return row["revoked_at"] is None

    # ------------------------------------------------------------------------------------------- hello

    def proofs(self, client_id: str | None, nonce: str | None, port: int | None) -> tuple[str | None, str | None]:
        """``(proof, revoked_proof)``: the first for an ACTIVE client, the second for a REVOKED one, neither for an unknown
        client or a malformed request. Exactly one of them is ever set."""
        if not client_id or not nonce or not port or not NONCE_RE.fullmatch(nonce):
            return None, None
        row = self.db.one("SELECT token_hash,revoked_at FROM extension_clients WHERE id=?", (client_id,))
        if row is None:
            return None, None
        if row["revoked_at"] is not None:
            return None, revoked_proof(row["token_hash"], nonce, port)
        return hello_proof(row["token_hash"], nonce, port), None

    def proof(self, client_id: str | None, nonce: str | None, port: int | None) -> str | None:
        """The hello proof for an ACTIVE client, otherwise None (a revoked client gets ``revoked_proof`` instead)."""
        return self.proofs(client_id, nonce, port)[0]

    def note_contact(self, browser: str | None, origin: str | None, version: str = "") -> None:
        """An UNPAIRED extension said hello: the app may show 'Almost there, connect the extension'."""
        if browser not in BROWSER_LABELS:
            return
        with self._lock:
            self._contacts[browser] = {"seen_at": self.clock(), "origin": origin, "version": version}

    def contact(self, browser: str) -> dict | None:
        with self._lock:
            seen = self._contacts.get(browser)
        if seen and (self.clock() - seen["seen_at"]).total_seconds() <= DETECTED_WINDOW_SECONDS:
            return dict(seen)
        return None

    # ------------------------------------------------------------------------------------------- prompts

    def _announce(self, pairing_id: str) -> None:
        """Show the macOS notification and the native dialog for a new pairing (needs a running event loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        notice = loop.create_task(self._notice(pairing_id))
        task = loop.create_task(self._prompt(pairing_id))
        with self._lock:
            self._dialogs[pairing_id] = task
            self._notices.add(notice)
        notice.add_done_callback(self._notices.discard)
        task.add_done_callback(lambda finished: self._forget(pairing_id, finished))

    def _forget(self, pairing_id: str, task: asyncio.Task) -> None:
        with self._lock:
            if self._dialogs.get(pairing_id) is task:
                del self._dialogs[pairing_id]

    async def close(self) -> None:
        """Shutdown: close any open dialog and stop pending notifications."""
        with self._lock:
            tasks = [*self._dialogs.values(), *self._notices]
            self._dialogs.clear()
        for task in tasks:
            _cancel_task(task)
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                if task.get_loop() is asyncio.get_running_loop():
                    await task

    async def _notice(self, pairing_id: str) -> None:
        from . import approval

        row = self._row(pairing_id)
        if row is None or row["state"] != "pending":
            return
        try:
            await approval.announce(self.db, row["browser"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("pairing_notification_failed")

    async def _prompt(self, pairing_id: str) -> None:
        from . import approval

        row = self._row(pairing_id)
        if row is None or row["state"] != "pending":
            return
        setting = self.db.one("SELECT value FROM settings WHERE key='extension_pairing_dialog'")
        if not approval.dialog_allowed(setting is None or setting["value"].strip().lower() != "false"):
            return
        remaining = (datetime.fromisoformat(row["expires_at"]) - self.clock()).total_seconds()
        seconds = int(min(DIALOG_SECONDS, remaining))
        if seconds < 5:
            return
        try:
            result = await approval.ask_for_code(
                row["client_name"], row["browser"], row["origin"], row["code"], seconds=seconds,
                recognised=is_recognised_origin(row["origin"]), on_wrong=lambda: self.wrong_code(pairing_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("extension_pairing_dialog_failed", exc_info=False)
            return
        try:
            if result == "approved":
                self.approve(pairing_id, via="dialog")
            elif result == "denied":
                self.deny(pairing_id, via="dialog")
        except BridgeError:
            pass  # the in-app decision won the race


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None
