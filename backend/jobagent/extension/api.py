"""Desktop-app API of the browser assistant (``/api/extension/*``, authenticated with the per-launch app token) and the
shared status / settings helpers used by the bridge (``/v1/status``, ``PATCH /v1/settings``).

None of these responses ever contains a token, a token hash or a pairing secret. Pairing codes are returned only for an
OPEN pairing (the user must compare them) and only to the app.

Overall state precedence (docs/EXTENSION_DESIGN.md section 6)::

    disabled > bridge_error > pairing > outdated > ready > detected > not_installed

``detected`` means an UNPAIRED extension said hello in the last 15 minutes. A bridge that was deliberately not started
(``create_app(bridge=False)``, tests) is "stopped" and is not an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from . import BRIDGE_PORTS, assets
from .auth import BridgeError
from .pairing import BROWSER_LABELS, manager_for

router = APIRouter(prefix="/extension")
logger = logging.getLogger("meridian.bridge")

CHROME_BUNDLE = "com.google.Chrome"
SAFARI_BUNDLE = "com.apple.Safari"
BROWSER_BUNDLES = {"chrome": CHROME_BUNDLE, "safari": SAFARI_BUNDLE}
STATUS_CACHE_SECONDS = 60.0
# A field counts as "inserted by the assistant" when the user kept the suggestion (feedback outcome accepted).
INSERTED_EVENT = "(e.kind IN ('accept','insert','inserted') OR (e.kind='feedback' AND e.outcome='accepted'))"
# The audit line session.feedback writes when a correction really was saved (``details`` carries ``"saved": true``).
SAVED_EDIT = "kind='edit' AND CASE WHEN json_valid(details) THEN json_extract(details,'$.saved') ELSE 0 END=1"
# An answer row and the audit line that follows it are written a moment apart (a loaded machine may stretch that).
ANSWER_MATCH_SECONDS = 120

# ExtensionSettings field -> core settings key (docs/EXTENSION_DESIGN.md 5.10)
SETTING_KEYS = {
    "enabled": "extension_enabled",
    "language": "extension_language",
    "auto_advance": "extension_auto_advance",
    "skip_filled": "extension_skip_filled",
    "tab_inserts_protected": "extension_tab_inserts_protected",
    "save_corrections": "extension_save_corrections",
    "background_research": "extension_research_enabled",
    "background_drafts": "extension_background_drafts",
    "web_lookup": "extension_web_lookup",
    "hint_on_ats": "extension_hint_on_ats",
    "draft_budget_usd": "extension_draft_budget_usd",
    "always_on_sites": "extension_always_on_sites",
}
BOOLEAN_SETTINGS = {
    "enabled", "auto_advance", "skip_filled", "tab_inserts_protected", "background_research", "background_drafts",
    "web_lookup", "hint_on_ats",
}
CHOICES = {"language": ("auto", "en", "es"), "save_corrections": ("ask", "always", "never")}
# What the browser extension itself may change through PATCH /v1/settings.
# What one page may spend on drafts when the user has set nothing: a page can make many questions, and the monthly cap is shared
# with Auto Apply. "Empty" in the settings means this default, not "no limit" (set a large amount for that).
DEFAULT_DRAFT_BUDGET_USD = 1.0
BRIDGE_EDITABLE = {"language", "auto_advance", "skip_filled", "tab_inserts_protected", "save_corrections", "hint_on_ats"}
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
GENERATION_KEYS = (
    "text_provider", "text_base_url", "text_model", "text_fallback_model", "monthly_budget", "ai_budget_unlimited",
)


# ----------------------------------------------------------------------------------------------- settings


def translate_settings(values: dict, *, to_core: bool) -> dict:
    """The single mapping between ``ExtensionSettings`` fields and ``core.DEFAULT_SETTINGS`` keys.

    ``to_core=True``: {"background_research": True} -> {"extension_research_enabled": True}
    ``to_core=False``: the reverse. Keys without a counterpart are dropped."""
    mapping = SETTING_KEYS if to_core else {core: field for field, core in SETTING_KEYS.items()}
    return {mapping[key]: value for key, value in values.items() if key in mapping}


def validate_patch(patch: dict, allowed=None) -> dict:
    """Validate and normalise an ExtensionSettings patch. Raises BridgeError(422) with a plain sentence."""
    allowed = set(SETTING_KEYS) if allowed is None else set(allowed)
    if not isinstance(patch, dict) or not patch:
        raise BridgeError(422, "Send at least one setting to change", "invalid")
    clean: dict = {}
    for key, value in patch.items():
        if key not in allowed:
            raise BridgeError(422, f"{key} cannot be changed here" if key in SETTING_KEYS else f"Unknown setting {key}", "invalid")
        if key in BOOLEAN_SETTINGS:
            if not isinstance(value, bool):
                raise BridgeError(422, f"{key} must be true or false", "invalid")
        elif key in CHOICES:
            if value not in CHOICES[key]:
                raise BridgeError(422, f"{key} must be one of {', '.join(CHOICES[key])}", "invalid")
        elif key == "draft_budget_usd":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1000
            ):
                raise BridgeError(422, "draft_budget_usd must be empty or an amount between 0 and 1000 USD", "invalid")
            value = None if value is None else round(float(value), 2)
        elif key == "always_on_sites":
            if not isinstance(value, list) or len(value) > 100:
                raise BridgeError(422, "always_on_sites must be a list of at most 100 hostnames", "invalid")
            hosts = []
            for item in value:
                host = item.strip().lower().rstrip(".") if isinstance(item, str) else ""
                if not HOSTNAME_RE.fullmatch(host):
                    raise BridgeError(422, "always_on_sites accepts hostnames such as jobs.example.com", "invalid")
                if host not in hosts:
                    hosts.append(host)
            value = hosts
        clean[key] = value
    return clean


def _coerce(field: str, value, default):
    """A stored value that does not satisfy the contract (e.g. written through the generic settings API) is ignored."""
    try:
        return validate_patch({field: value}, {field})[field]
    except BridgeError:
        return default


def light_settings(db, keys) -> dict:
    """Defaults overlaid with the stored values of ``keys`` only (no full-table read, no private answer cache)."""
    from ..core import DEFAULT_SETTINGS

    keys = tuple(keys)
    result = {key: DEFAULT_SETTINGS.get(key) for key in keys}
    marks = ",".join("?" * len(keys))
    for row in db.query(f"SELECT key,value FROM settings WHERE key IN ({marks})", keys):
        try:
            result[row["key"]] = json.loads(row["value"])
        except ValueError:
            result[row["key"]] = row["value"]
    return result


def read_extension_settings(db) -> dict:
    """The current ``ExtensionSettings`` as a dict, from the core settings table (invalid stored values -> defaults)."""
    from ..core import DEFAULT_SETTINGS

    core = light_settings(db, SETTING_KEYS.values())
    values = translate_settings(core, to_core=False)
    settings = {
        field: _coerce(field, values[field], DEFAULT_SETTINGS[SETTING_KEYS[field]]) for field in SETTING_KEYS
    }
    if settings["draft_budget_usd"] is None:
        settings["draft_budget_usd"] = DEFAULT_DRAFT_BUDGET_USD
    return settings


def write_extension_settings(app, patch: dict) -> dict:
    """Persist a validated patch (already ``validate_patch``-ed) and return the new ExtensionSettings."""
    from ..db import dumps

    stored = translate_settings(patch, to_core=True)
    with app.state.db.transaction() as conn:
        for key, value in stored.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, dumps(value)),
            )
    app.state.extension_status_cache = None
    return read_extension_settings(app.state.db)


# ----------------------------------------------------------------------------------------------- bridge status


def base_cv_versions(db, *, approved: bool) -> int:
    """How many CV versions the user owns (a base CV: ``job_id`` empty; one Meridian wrote for a single job is offered to that
    job only) are approved, or still wait for approval. The ONE definition of "the user has a CV": the panel's ``has_cv`` /
    ``unapproved_cvs`` and the setup step "documents" both read it, so they cannot disagree about whether setup is done."""
    from .documents_pick import CV_KINDS

    kinds = ",".join("?" * len(CV_KINDS))
    return int(db.one(
        "SELECT COUNT(*) AS n FROM document_versions v JOIN documents d ON d.id=v.document_id "
        f"WHERE v.approved=? AND d.kind IN ({kinds}) AND (d.job_id IS NULL OR d.job_id='')", (1 if approved else 0, *CV_KINDS)
    )["n"])


def profile_summary(db) -> dict:
    """{verified_facts, has_cv, unapproved_cvs, display_name}. ``has_cv``: the user has an approved CV of their own (a base
    CV: one written for a single job does not count). ``unapproved_cvs``: how many of their CV versions wait for approval, so
    the panel can say once "approve your CV in Meridian" instead of leaving every upload field silent (CPL-06)."""
    verified = db.one("SELECT COUNT(*) AS n FROM facts WHERE verification_status='verified' OR locked=1")["n"]
    has_cv = base_cv_versions(db, approved=True) > 0
    unapproved = base_cv_versions(db, approved=False)
    name = db.one(
        "SELECT value FROM facts WHERE source IN ('application_profile:preferred_name','application_profile:full_name') "
        "AND verification_status='verified' AND length(trim(value))>0 "
        "ORDER BY CASE source WHEN 'application_profile:preferred_name' THEN 0 ELSE 1 END LIMIT 1"
    )
    return {
        "verified_facts": int(verified),
        "has_cv": has_cv,
        "unapproved_cvs": unapproved,
        "display_name": (name["value"].strip()[:80] if name else None),
    }


def _fingerprint(settings: dict, fields) -> str:
    # identical to onboarding.fingerprint, without loading every settings row
    return hashlib.sha256(json.dumps({key: settings.get(key) for key in fields}, sort_keys=True).encode()).hexdigest()


# What the cached ``generation`` block is derived from: the writing model, its budget and the result of its connection test
# (``onboarding:ai_text``, written by ``POST /api/ai/text/test``). The cache (60 s) is dropped the moment any of them changes,
# whichever code path changed it, so the panel never says "tested" or "no budget" about a model that has since been replaced.
GENERATION_STAMP_KEYS = GENERATION_KEYS + ("ai_budget_unlimited_until", "onboarding:ai_text")


def generation_stamp(db) -> str:
    marks = ",".join("?" * len(GENERATION_STAMP_KEYS))
    rows = db.query(f"SELECT key,value FROM settings WHERE key IN ({marks}) ORDER BY key", GENERATION_STAMP_KEYS)
    return hashlib.sha256(json.dumps([[row["key"], row["value"]] for row in rows]).encode()).hexdigest()


def generation_summary(app) -> dict:
    """Can long answers be drafted? Database-only: no Keychain access, no network, no model call.

    {available, tested, provider, local, message, monthly_limit_usd, spent_usd, model, month, calls, unlimited}"""
    from ..application_answer_llm import ConfigurationError, provider_config
    from ..onboarding import TEXT_AI, read_record
    from ..usage import unlimited_authorized

    db = app.state.db
    llm = app.state.text_ai.answer_llm
    settings = light_settings(db, GENERATION_KEYS)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = llm.usage_db.one("SELECT value FROM settings WHERE key=?", ("ai_usage:" + month,))
    try:
        usage = json.loads(row["value"]) if row else {}
    except ValueError:
        usage = {}
    base = {
        "available": False, "tested": False, "provider": settings.get("text_provider", "none"), "local": False,
        "message": "", "model": str(settings.get("text_model") or ""), "month": month,
        "spent_usd": round(float(usage.get("cost") or 0), 6), "calls": int(usage.get("calls") or 0),
        "monthly_limit_usd": None, "unlimited": False,
    }
    try:
        provider, _, local = provider_config(settings)
        _, budget = llm._billing(settings, provider, local)
        if not base["model"].strip():
            raise ConfigurationError("Select an available application answer model and test the connection")
    except ConfigurationError as exc:
        return base | {"message": str(exc)}
    unlimited = settings.get("ai_budget_unlimited") is True
    if unlimited:
        connection = db._connect()
        try:
            unlimited = unlimited_authorized(connection)
        finally:
            connection.close()
    probe = read_record(db, "ai_text")
    tested = bool(probe and probe.get("success") and probe.get("fingerprint") == _fingerprint(settings, TEXT_AI))
    return base | {
        "available": True, "tested": tested, "provider": provider, "local": bool(local),
        "monthly_limit_usd": None if local or unlimited else float(budget), "unlimited": bool(unlimited),
        "message": "" if tested else "Test your writing model in Settings > AI to enable long answers.",
    }


def bridge_status(app) -> dict:
    """The ``StatusResponse`` of ``GET /v1/status``. Profile and generation parts are cached for 60 seconds."""
    from . import PROTOCOL

    cache = getattr(app.state, "extension_status_cache", None)
    stamp = time.monotonic()
    watched = generation_stamp(app.state.db)  # the model, its budget or its test changed: the block is out of date at once
    if cache is None or stamp - cache[0] >= STATUS_CACHE_SECONDS or len(cache) < 3 or cache[2] != watched:
        generation = generation_summary(app)
        cache = (
            stamp,
            {
                "profile": profile_summary(app.state.db),
                "generation": {
                    key: generation[key]
                    for key in ("available", "tested", "provider", "local", "message", "monthly_limit_usd", "spent_usd")
                },
            },
            watched,
        )
        app.state.extension_status_cache = cache
    settings = read_extension_settings(app.state.db)
    return {
        "protocol": PROTOCOL,
        "app_version": app.version,
        "latest_extension_version": assets.bundled_version(),
        "language": settings["language"],
        "profile": dict(cache[1]["profile"]),
        "generation": dict(cache[1]["generation"]),
        "settings": settings,
    }


# ----------------------------------------------------------------------------------------------- desktop status


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _first_existing(*paths: str) -> bool:
    return any(Path(path).expanduser().exists() for path in paths)


def _chrome_installed() -> bool:
    """Google Chrome itself (Brave, Edge and other Chromium browsers load the same folder but are not what ``open -b`` opens)."""
    return _first_existing("/Applications/Google Chrome.app", "~/Applications/Google Chrome.app")


def _safari_bundled() -> bool:
    bundle = os.environ.get("MERIDIAN_APP_BUNDLE", "")
    plugins = Path(bundle) / "Contents" / "PlugIns" if bundle else None
    return bool(plugins and plugins.is_dir() and any(plugins.glob("*.appex")))


def _bridge_block(app) -> dict:
    bridge = getattr(app.state, "bridge", None)
    candidates = list(bridge.ports) if bridge is not None and bridge.ports != [0] else list(BRIDGE_PORTS)
    if bridge is None:
        return {
            "state": "stopped", "port": None, "candidate_ports": candidates, "conflict_process": None,
            "message": "The browser connection is not running in this session.",
        }
    status = bridge.status()
    if status["running"]:
        return {
            "state": "listening", "port": status["port"], "candidate_ports": candidates, "conflict_process": None,
            "message": None,
        }
    failed_to_bind = "no free bridge port" in (status["error"] or "")
    return {
        "state": "port_in_use" if failed_to_bind else "error" if status["error"] else "stopped",
        "port": candidates[0] if candidates else None,
        "candidate_ports": candidates,
        "conflict_process": status["conflict_process"],
        "message": (
            f"Meridian tried ports {candidates[0]} to {candidates[-1]}." if failed_to_bind and candidates
            else status["error"]
        ),
    }


# Both indexes are created by migration 0013 and, for a database that already ran it, by ``ensure_event_indexes`` at startup.
# The first covers every counter and the "newest error" lookup of the status without reading the events themselves; the
# second is partial: only the few "a correction was saved" lines, which Auto Apply's Profile reads for every question.
EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_extension_event_kind ON extension_events(session_id,kind,created_at,outcome)",
    "CREATE INDEX IF NOT EXISTS idx_extension_event_edit ON extension_events(created_at) WHERE kind='edit'",
)
# One pass over the events of every page, answered from the covering index (never the event rows).
CLIENT_COUNTERS_SQL = (
    "SELECT s.client_id AS client_id,"
    " COALESCE(SUM(CASE WHEN e.kind='suggest' THEN 1 ELSE 0 END),0) AS suggestions,"
    f" COALESCE(SUM(CASE WHEN {INSERTED_EVENT} THEN 1 ELSE 0 END),0) AS insertions "
    "FROM extension_sessions s JOIN extension_events e ON e.session_id=s.id GROUP BY s.client_id"
)
# The id of the newest error of one client (index only); the row itself is then read once.
LATEST_ERROR_ID_SQL = (
    "SELECT e.id AS id FROM extension_sessions s JOIN extension_events e ON e.session_id=s.id "
    "WHERE s.client_id=? AND e.kind='error' ORDER BY e.created_at DESC LIMIT 1"
)


def ensure_event_indexes(db) -> None:
    """Idempotent: a database that ran migration 0013 before these indexes were added gets them here. Never raises."""
    try:
        for statement in EVENT_INDEXES:
            db.execute(statement)
    except sqlite3.Error:
        logger.warning("extension_event_indexes_unavailable")


def _client_errors(db, client_ids) -> dict:
    """Last recorded problem per client (from the audit trail written by the bridge); None when there is none."""
    result = {}
    for client_id in client_ids:
        found = db.one(LATEST_ERROR_ID_SQL, (client_id,))
        row = db.one("SELECT details,outcome FROM extension_events WHERE id=?", (found["id"],)) if found else None
        if row:
            details = row["details"]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = {}
            text = details.get("message") if isinstance(details, dict) else None
            result[client_id] = str(text or row["outcome"] or "")[:200] or None
    return result


def _clients(db, bundled: str) -> list[dict]:
    rows = db.query(
        "SELECT c.*,(SELECT COUNT(*) FROM extension_sessions s WHERE s.client_id=c.id) AS pages "
        "FROM extension_clients c WHERE c.revoked_at IS NULL ORDER BY c.created_at"
    )
    counters = {row["client_id"]: row for row in db.query(CLIENT_COUNTERS_SQL)}
    errors = _client_errors(db, [row["id"] for row in rows])
    return [
        {
            "id": row["id"],
            "browser": row["browser"],
            "label": f"{BROWSER_LABELS.get(row['browser'], 'Browser')} on this Mac",
            "version": row["version"],
            "paired_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "last_error": errors.get(row["id"]),
            "outdated": bool(row["version"]) and assets.is_older(row["version"], bundled),
            "stats": {
                "pages": row["pages"],
                "suggestions": int((counters.get(row["id"]) or {}).get("suggestions") or 0),
                "insertions": int((counters.get(row["id"]) or {}).get("insertions") or 0),
            },
        }
        for row in rows
    ]


def build_status(app, bridge_block: dict | None = None) -> dict:
    """The ``ExtensionStatus`` returned by GET /api/extension (never any secret)."""
    db = app.state.db
    manager = manager_for(app)
    settings = read_extension_settings(db)
    bundled = assets.bundled_version()
    clients = _clients(db, bundled)
    pending = manager.pending()
    bridge = bridge_block or _bridge_block(app)
    installed = assets.installed_info(app.state.data_dir)
    generation = generation_summary(app)
    browsers = {}
    for browser in ("chrome", "safari"):
        mine = [client for client in clients if client["browser"] == browser]
        contact = manager.contact(browser)
        waiting = any(item["browser"] == browser for item in pending)
        if any(client["outdated"] for client in mine):
            state = "outdated"
        elif mine:
            state = "connected"
        elif contact or waiting:
            state = "detected"
        else:
            state = "not_installed"
        browsers[browser] = {
            "browser_installed": _chrome_installed()
            if browser == "chrome"
            else _first_existing("/Applications/Safari.app", "/System/Cryptexes/App/System/Applications/Safari.app"),
            "extension_path": installed["path"] if browser == "chrome" and installed["installed"] else None,
            "bundled_version": bundled,
            "bundled": (assets.extension_source() is not None) if browser == "chrome" else _safari_bundled(),
            "state": state,
            "seen_unpaired_at": _iso(contact["seen_at"]) if contact else None,
            "seen_version": (contact.get("version") or None) if contact else None,
        }
    browsers["safari"]["needs_unsigned_toggle"] = not os.environ.get("MERIDIAN_SAFARI_NOTARIZED")
    first = pending[0] if pending else None
    if not settings["enabled"]:
        overall = "disabled"
    elif bridge["state"] in {"port_in_use", "error"}:
        overall = "bridge_error"
    elif first:
        overall = "pairing"
    elif any(client["outdated"] for client in clients):
        overall = "outdated"
    elif clients:
        overall = "ready"
    elif any(manager.contact(browser) for browser in BROWSER_LABELS):
        overall = "detected"
    else:
        overall = "not_installed"
    return {
        "state": overall,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "app_version": app.version,
        "bridge": bridge,
        "browsers": browsers,
        "clients": clients,
        "pending_pairing": {
            "id": first["id"],
            "browser": first["browser"],
            "code": first["code"],
            "extension_version": first["version"],
            "requested_at": first["created_at"],
            "expires_at": first["expires_at"],
            "recognized": first["recognized"],
        }
        if first
        else None,
        "settings": settings,
        "drafts": {
            "configured": generation["available"],
            "provider": generation["provider"],
            "model": generation["model"],
            "message": generation["message"],
            "month": generation["month"],
            "spent_usd": generation["spent_usd"],
            "calls": generation["calls"],
            "monthly_limit_usd": generation["monthly_limit_usd"],
            "unlimited": generation["unlimited"],
        },
    }


async def current_status(app) -> dict:
    bridge = getattr(app.state, "bridge", None)
    if bridge is not None:
        await bridge.explain_failure()
    return await asyncio.to_thread(build_status, app)


def onboarding_evidence(db, app_state) -> dict:
    """The 9th setup step: complete when one browser is connected; started when one is detected or pairing."""
    active = db.one("SELECT COUNT(*) AS n FROM extension_clients WHERE revoked_at IS NULL")["n"]
    pairing = db.one("SELECT COUNT(*) AS n FROM extension_pairings WHERE state='pending'")["n"]
    manager = getattr(app_state, "extension_pairing", None)
    detected = bool(manager and any(manager.contact(browser) for browser in BROWSER_LABELS))
    bridge = getattr(app_state, "bridge", None)
    bridge_status_ = bridge.status() if bridge is not None else None
    bridge_error = bool(bridge_status_ and not bridge_status_["running"] and bridge_status_["error"])
    bundled = assets.bundled_version()
    outdated = any(
        assets.is_older(row["version"], bundled)
        for row in db.query("SELECT version FROM extension_clients WHERE revoked_at IS NULL AND version!=''")
    )
    if active:
        detail = (
            "A browser assistant is connected, but its extension is older than this Meridian: reload it."
            if outdated
            else f"{active} browser{'s' if active != 1 else ''} connected to Meridian."
        )
    elif bridge_error:
        detail = "Meridian could not open its browser connection. Open Browser assistant settings to fix it."
    elif pairing:
        detail = "A browser is waiting for you to approve its connection."
    elif detected:
        detail = "The extension was found. Click its toolbar icon and choose Connect."
    else:
        detail = "Install the Chrome or Safari assistant to fill applications in your own browser."
    return {
        "complete": bool(active) and not (bridge_error or outdated),
        "started": bool(pairing or detected or active),
        "attention": bool(bridge_error or (active and outdated)),
        "detail": detail,
        "evidence": {"clients": active, "pairing": bool(pairing), "detected": detected, "outdated": outdated},
    }


def prune_audit(db, days: int = 90, session_days: int = 365) -> int:
    """Forget field-level audit events older than ``days`` (they never hold typed values). -> rows deleted.

    The line that says "this answer was saved through the assistant" is kept: it is what labels the answer in the
    application's detail (``extension_answer_ids``) and it holds no value, only a state, a scope and a field hash.
    A page that was only ever browsed (no job, no application) is forgotten after ``session_days``: the table of pages
    would otherwise grow with every posting ever opened; a page tied to a job or an application stays."""
    from datetime import timedelta

    ensure_event_indexes(db)
    moment = datetime.now(timezone.utc)
    cutoff = (moment - timedelta(days=days)).isoformat()
    with db.transaction() as conn:
        deleted = conn.execute(f"DELETE FROM extension_events WHERE created_at<? AND NOT ({SAVED_EDIT})", (cutoff,)).rowcount
        conn.execute("DELETE FROM extension_sessions WHERE updated_at<? AND job_id IS NULL AND application_id IS NULL",
                     ((moment - timedelta(days=session_days)).isoformat(),))
    return deleted


def tracker_assistant(db, job_id: str | None = None) -> dict:
    """job_id -> {browser, fields_inserted, last_used_at} for every posting the assistant has been used on.

    ``job_id`` limits the lookup to one posting (the application detail); the computation is the same as the tracker's."""
    scope, arguments = ("AND s.job_id=? ", (job_id,)) if job_id else ("", ())
    try:
        sessions = db.query(
            "SELECT s.job_id AS job_id,c.browser AS browser,MAX(s.updated_at) AS last_used_at "
            "FROM extension_sessions s JOIN extension_clients c ON c.id=s.client_id "
            f"WHERE s.job_id IS NOT NULL {scope}GROUP BY s.job_id,c.browser",
            arguments,
        )
        inserted = {
            row["job_id"]: row
            for row in db.query(
                "SELECT s.job_id AS job_id,COUNT(*) AS n,MAX(e.created_at) AS last "
                "FROM extension_events e JOIN extension_sessions s ON s.id=e.session_id "
                f"WHERE s.job_id IS NOT NULL {scope}AND {INSERTED_EVENT} GROUP BY s.job_id",
                arguments,
            )
        }
    except sqlite3.OperationalError:
        return {}
    result: dict = {}
    for row in sessions:
        best = result.get(row["job_id"])
        if best is None or row["last_used_at"] > best["last_used_at"]:
            result[row["job_id"]] = {"browser": row["browser"], "last_used_at": row["last_used_at"]}
    for posting, value in result.items():
        count = inserted.get(posting)
        value["fields_inserted"] = count["n"] if count else 0
        if count and count["last"] and count["last"] > value["last_used_at"]:
            value["last_used_at"] = count["last"]
    return {posting: {"browser": v["browser"], "fields_inserted": v["fields_inserted"], "last_used_at": v["last_used_at"]}
            for posting, v in result.items()}


def _moment(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _saved_edit_lines(db, where: str, params: tuple) -> list[dict]:
    """The audit lines that say 'a correction was saved through the assistant', each with its decoded ``details``."""
    try:
        rows = db.query(
            f"SELECT created_at,details FROM extension_events WHERE {where} AND {SAVED_EDIT}", params)
    except sqlite3.OperationalError:
        return []
    lines = []
    for row in rows:
        try:
            details = json.loads(row["details"])
        except (TypeError, ValueError):
            details = {}
        lines.append({"created_at": row["created_at"], "details": details if isinstance(details, dict) else {}})
    return lines


def extension_answer_ids(db, application_id: str) -> set[str]:
    """ids of the ``application_answers`` of an application that the user saved through the browser assistant.

    Neither ``application_answers`` nor ``facts`` say who saved an answer: the assistant stores a correction exactly as
    a direct edit in Auto Apply's browser is stored. The one durable trace is the audit line ``session.feedback`` writes
    in the transaction of the save (kind ``edit``, ``saved: true``; ``prune_audit`` keeps it), and that line NAMES the
    answer it describes (``details.answer_id``): the label is exact, whatever else is typed into Meridian meanwhile.

    A line written before lines carried the id has no ``answer_id``: it claims the newest answer of its application
    that no line named, written at or before it, within ``ANSWER_MATCH_SECONDS`` (an answer is claimed by at most one
    line, so two pages saving at the same moment label both). Only that old fallback can mislabel a non-assistant answer
    that landed between the save and its line. Backups drop the audit tables, so a restored database labels nothing
    (like ``tracker_assistant``)."""
    lines = _saved_edit_lines(
        db, "session_id IN (SELECT id FROM extension_sessions WHERE application_id=?)", (application_id,))
    if not lines:
        return set()
    answers = [
        (moment, row["id"])
        for row in db.query(
            "SELECT id,created_at FROM application_answers WHERE application_id=? ORDER BY created_at,rowid",
            (application_id,),
        )
        if (moment := _moment(row["created_at"])) is not None
    ]
    existing = {answer_id for _, answer_id in answers}
    claimed: set[str] = set()
    unnamed = []
    for line in lines:
        named = line["details"].get("answer_id")
        if isinstance(named, str) and named:
            if named in existing:  # an id of another application, or of an answer since deleted, labels nothing
                claimed.add(named)
        else:
            unnamed.append(line)
    for when in sorted(moment for line in unnamed if (moment := _moment(line["created_at"])) is not None):
        for written, answer_id in reversed(answers):
            if answer_id in claimed or written > when:
                continue
            if (when - written).total_seconds() <= ANSWER_MATCH_SECONDS:
                claimed.add(answer_id)
            break
    return claimed


def extension_memory_stamps(db) -> set[tuple[str, str]]:
    """``(fact_id, memory_at)`` of every Profile memory (a ``browser_answers`` row) the assistant last wrote: the audit
    line of a saved correction names the memory's fact and the ``updated_at`` the memory had right after the save. A
    memory that was edited since (in Meridian, or in Auto Apply's browser) has a newer ``updated_at`` and no label."""
    stamps = set()
    for line in _saved_edit_lines(db, "1=1", ()):
        fact_id, stamp = line["details"].get("fact_id"), line["details"].get("memory_at")
        if isinstance(fact_id, str) and fact_id and isinstance(stamp, str) and stamp:
            stamps.add((fact_id, stamp))
    return stamps


# ----------------------------------------------------------------------------------------------- OS actions


async def run_os(app, *args: str, stdin: bytes | None = None, timeout: float = 10) -> tuple[bool, str]:
    """Run a fixed macOS helper (``open``, ``pbcopy``). Under MERIDIAN_TEST the command is only recorded."""
    if os.environ.get("MERIDIAN_TEST"):
        actions = getattr(app.state, "extension_os_actions", None)
        if actions is None:
            actions = app.state.extension_os_actions = []
        actions.append({"args": list(args), "stdin": stdin.decode() if stdin else None})
        return True, ""
    if sys.platform != "darwin":
        return False, "This action is only available on macOS."
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, error = await asyncio.wait_for(process.communicate(stdin), timeout)
    except (OSError, asyncio.TimeoutError):
        if process is not None and process.returncode is None:  # a helper that never answers must not linger
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        return False, "macOS did not respond to that request."
    if process.returncode != 0:
        return False, (error or b"").decode(errors="replace").strip()[:200] or "macOS could not complete that request."
    return True, ""


class InstallRequest(BaseModel):
    """``install`` is the whole job in one call (prepare, copy the path, show it in Finder, open the browser's page); the other
    actions are its steps, kept for the buttons that repeat one of them. ``update`` shortens ``install`` to what an update
    needs (the folder is refreshed and the page opened: the path is already known and the extension already loaded)."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["prepare", "reveal", "open_browser_page", "copy_path", "install"]
    update: StrictBool = False

    @model_validator(mode="after")
    def _update_belongs_to_install(self):
        if self.update and self.action != "install":
            raise ValueError("update only applies to the install action")
        return self


def _extension_folder(app) -> dict:
    """Make sure the Chrome folder is installed; raises 409 when nothing is bundled or the folder cannot be prepared."""
    try:
        info = assets.install_chrome_extension(app.state.data_dir)
    except OSError as exc:  # unwritable data folder, unreadable bundle
        logger.info("extension_folder_unavailable %s", type(exc).__name__)
        raise BridgeError(
            409, "Meridian could not prepare the extension folder. Check that its data folder can be written to.",
            "unavailable",
        ) from exc
    except assets.UntrustedExtension as exc:  # not the pinned Meridian Assistant (a foreign or keyless folder)
        logger.warning("extension_folder_untrusted")
        raise BridgeError(
            409, "The browser extension found with this Meridian is not the genuine Meridian Assistant, so Meridian "
                 "will not install it. Reinstall Meridian.", "unavailable"
        ) from exc
    except ValueError as exc:  # a manifest that is not JSON
        logger.info("extension_folder_unavailable %s", type(exc).__name__)
        raise BridgeError(
            409, "The browser extension bundled with this Meridian is damaged. Reinstall Meridian.", "unavailable"
        ) from exc
    if not info.get("installed"):
        raise BridgeError(409, info.get("reason", "The browser extension is not bundled with this build"), "unavailable")
    return info


# ----------------------------------------------------------------------------------------------- routes


@router.get("")
async def extension_status(request: Request):
    return await current_status(request.app)


@router.post("/pairings/{pairing_id}/approve")
async def pairing_approve(pairing_id: str, request: Request):
    manager_for(request.app).approve(pairing_id, via="app")
    return await current_status(request.app)


@router.post("/pairings/{pairing_id}/deny")
async def pairing_deny(pairing_id: str, request: Request):
    manager_for(request.app).deny(pairing_id, via="app")
    return await current_status(request.app)


@router.delete("/clients/{client_id}")
async def client_revoke(client_id: str, request: Request):
    manager_for(request.app).revoke(client_id)
    return await current_status(request.app)


@router.patch("/settings")
async def settings_patch(request: Request, patch: dict = Body(...)):
    clean = validate_patch(patch)
    write_extension_settings(request.app, clean)
    return await current_status(request.app)


# The order matters: the path is copied while Meridian is in front, and the browser is the last window to come forward.
CHROME_INSTALL_STEPS = ("prepare", "copy_path", "reveal", "open_browser_page")
CHROME_UPDATE_STEPS = ("prepare", "open_browser_page")
STEP_FAILURES = {
    "copy_path": "Meridian could not copy the folder path. Use the Copy path again button below.",
    "reveal": "Meridian could not show the folder in Finder. Use the Show in Finder button below.",
}


async def _chrome_step(app, action: str, path: str) -> tuple[bool, str]:
    """One step of the Chrome install -> (ok, what the person reads)."""
    if action == "prepare":
        return True, "The Chrome extension folder is ready."
    if action == "reveal":
        ok, error = await run_os(app, "/usr/bin/open", "-R", path)
        return ok, "Showing the extension folder in Finder." if ok else error
    if action == "open_browser_page":
        ok, error = await run_os(app, "/usr/bin/open", "-b", CHROME_BUNDLE, "chrome://extensions")
        return ok, "Opened Chrome's extensions page." if ok else error or "Chrome is not installed."
    ok, error = await run_os(app, "/usr/bin/pbcopy", stdin=path.encode())
    return ok, "Folder path copied." if ok else error


async def _install_chrome(app, path: str, update: bool) -> dict:
    """Every step in order. A step that fails does not stop the others (the folder is ready, so showing it and opening the page
    still help); the answer names the first failure. Chrome not found is not a failure: Brave, Edge and the other Chromium
    browsers load the same folder, so the page is skipped and the answer says so."""
    steps, failure, no_chrome = [], "", False
    for action in CHROME_UPDATE_STEPS if update else CHROME_INSTALL_STEPS:
        if action == "open_browser_page" and not _chrome_installed():
            no_chrome = True
            steps.append({"action": action, "ok": True, "skipped": True,
                          "message": "Google Chrome was not found, so its extensions page was not opened."})
            continue
        ok, message = await _chrome_step(app, action, path)
        steps.append({"action": action, "ok": ok, "message": message})
        if not ok and not failure:
            failure = STEP_FAILURES.get(action) or message
    if update:
        done = "Meridian refreshed the extension folder"
        opened = "Meridian refreshed the extension folder and opened Chrome's extensions page."
    else:
        done = "Meridian prepared the extension folder, copied its path and showed it in Finder"
        opened = "Meridian prepared the extension folder, copied its path, showed it in Finder and opened Chrome's extensions page."
    if failure:
        message = failure
    elif no_chrome:
        message = (f"{done}. Google Chrome was not found: open your Chromium browser's extensions page "
                   "(chrome://extensions) yourself.")
    else:
        message = opened
    return {"ok": not failure, "message": message, "path": path, "steps": steps}


def _safari_prepare() -> tuple[bool, str]:
    bundled = _safari_bundled()
    return bundled, ("The Safari extension is built into Meridian." if bundled
                     else "This build of Meridian does not include the Safari extension.")


async def _safari_open(app) -> tuple[bool, str]:
    ok, error = await run_os(app, "/usr/bin/open", "-b", SAFARI_BUNDLE)
    return ok, "Opened Safari. Choose Safari > Settings > Extensions." if ok else error or "Safari is not installed."


async def _install_safari(app) -> dict:
    """Safari has no folder to hand over: the extension is part of Meridian, so the install is a check and opening Safari. A build
    without the extension never opens Safari for something that is not there."""
    ok, message = _safari_prepare()
    steps = [{"action": "prepare", "ok": ok, "message": message}]
    if not ok:
        return {"ok": False, "message": message, "steps": steps}
    ok, message = await _safari_open(app)
    steps.append({"action": "open_browser_page", "ok": ok, "message": message})
    return {"ok": ok, "message": message, "steps": steps}


@router.post("/install/{browser}")
async def install(browser: Literal["chrome", "safari"], payload: InstallRequest, request: Request):
    app, action = request.app, payload.action
    if browser == "chrome":
        info = await asyncio.to_thread(_extension_folder, app)
        path = info["path"]
        if action == "install":
            return await _install_chrome(app, path, payload.update)
        ok, message = await _chrome_step(app, action, path)
        return {"ok": ok, "message": message} if action == "open_browser_page" else {"ok": ok, "message": message, "path": path}
    bundle = os.environ.get("MERIDIAN_APP_BUNDLE", "")
    if action == "install":
        return await _install_safari(app)
    if action == "prepare":
        ok, message = _safari_prepare()
        return {"ok": ok, "message": message}
    if action == "open_browser_page":
        ok, message = await _safari_open(app)
        return {"ok": ok, "message": message}
    if action == "reveal" and bundle and Path(bundle).suffix == ".app" and Path(bundle).is_dir():
        ok, error = await run_os(app, "/usr/bin/open", "-R", bundle)
        return {"ok": ok, "message": "Showing Meridian in Finder." if ok else error, "path": bundle}
    return {"ok": False, "message": "Safari has no folder to show: enable the extension in Safari's settings."}


@router.get("/activity")
def activity(request: Request, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = activity_items(request.app.state.db)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/bridge/restart")
async def bridge_restart(request: Request):
    bridge = getattr(request.app.state, "bridge", None)
    if bridge is None:
        raise BridgeError(409, "The browser connection is not enabled in this session", "unavailable")
    await bridge.restart()
    return await current_status(request.app)


def _safe_url(url: str) -> str:
    """The exact application URL, only when it is a plain http(s) address (it is passed as one argv entry)."""
    url = (url or "").strip()
    if not re.fullmatch(r"https?://[^\s\x00-\x1f\x7f]{3,3900}", url):
        raise BridgeError(422, "This job has no web address Meridian can open", "invalid")
    return url


def _paired_browser(db) -> str | None:
    row = db.one(
        "SELECT browser FROM extension_clients WHERE revoked_at IS NULL "
        "ORDER BY COALESCE(last_seen_at,created_at) DESC LIMIT 1"
    )
    return row["browser"] if row else None


@router.post("/jobs/{job_id}/open")
async def job_open(job_id: str, request: Request):
    app = request.app
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if job is None:
        raise BridgeError(404, "Job not found", "not_found")
    settings = read_extension_settings(app.state.db)
    if not settings["enabled"]:
        raise BridgeError(409, "The browser assistant is turned off", "disabled")
    browser = _paired_browser(app.state.db)
    if browser is None:
        raise BridgeError(409, "Connect a browser to Meridian first (Settings > Browser assistant)", "unavailable")
    url = _safe_url(job["application_url"] or job["url"])
    primed = False
    if settings["background_research"]:
        from .research import prime_research  # workstream B's helper; never raises

        primed = prime_research(app, job_id) is not None
    ok, error = await run_os(app, "/usr/bin/open", "-b", BROWSER_BUNDLES[browser], url)
    if not ok:
        raise BridgeError(409, error or "Could not open the browser", "unavailable")
    return {
        "ok": True, "browser": browser,
        "message": f"Opened in {BROWSER_LABELS[browser]}. Click the Meridian icon when the form is open."
        + (" Researching the company in the background." if primed else ""),
    }


# ----------------------------------------------------------------------------------------------- activity feed


def _text(value, limit=120) -> str | None:
    cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))).strip()[:limit]
    return cleaned or None


def _event_kind(kind: str, outcome: str) -> str | None:
    if kind in {"accept", "insert", "inserted"} or (kind == "feedback" and outcome == "accepted"):
        return "inserted"
    if kind in {"draft", "draft_ready", "generated"}:
        return "draft_ready"
    if kind == "policy_block" or outcome == "sensitive_blocked":
        return "skipped_sensitive"
    if kind in {"error", "blocked"}:
        return "blocked"
    return None


def activity_items(db) -> list[dict]:
    """Timeline-compatible assistant activity. Answer text and typed values are never part of an item."""
    items: list[dict] = []
    for row in db.query("SELECT * FROM extension_clients ORDER BY created_at DESC LIMIT 200"):
        label = BROWSER_LABELS.get(row["browser"], "Browser")
        base = {"origin": "assistant", "browser": row["browser"], "host": None, "company": None, "title": None}
        items.append(base | {
            "id": f"client:{row['id']}:paired", "created_at": row["created_at"], "kind": "paired",
            "message": f"{label} connected to Meridian.",
        })
        if row["revoked_at"]:
            items.append(base | {
                "id": f"client:{row['id']}:revoked", "created_at": row["revoked_at"], "kind": "revoked",
                "message": f"{label} was disconnected from Meridian.",
            })
    sessions = {
        row["id"]: row
        for row in db.query(
            "SELECT s.*,c.browser AS browser FROM extension_sessions s JOIN extension_clients c ON c.id=s.client_id "
            "ORDER BY s.updated_at DESC LIMIT 500"
        )
    }
    for row in sessions.values():
        company, title = _text(row["company"]), _text(row["title"])
        where = " at ".join(part for part in (title, company) if part) or "a job application"
        items.append({
            "id": f"session:{row['id']}", "created_at": row["created_at"], "kind": "page_assisted",
            "message": f"Helped with {where}.", "origin": "assistant", "browser": row["browser"],
            "host": _text(row["host"], 80), "company": company, "title": title,
        })
    for row in db.query(
        "SELECT session_id,kind,outcome,COUNT(*) AS n,MAX(created_at) AS last FROM extension_events "
        "GROUP BY session_id,kind,outcome ORDER BY last DESC LIMIT 500"
    ):
        kind, session = _event_kind(row["kind"], row["outcome"]), sessions.get(row["session_id"])
        if kind is None or session is None:
            continue
        n = row["n"]
        message = {
            "inserted": f"Filled {n} field{'s' if n != 1 else ''}.",
            "draft_ready": f"{n} draft{'s' if n != 1 else ''} ready to review.",
            "skipped_sensitive": f"Left {n} sensitive question{'s' if n != 1 else ''} for you.",
            "blocked": f"{n} answer{'s' if n != 1 else ''} could not be prepared.",
        }[kind]
        items.append({
            "id": f"events:{row['session_id']}:{row['kind']}:{row['outcome']}", "created_at": row["last"], "kind": kind,
            "message": message, "origin": "assistant", "browser": session["browser"], "host": _text(session["host"], 80),
            "company": _text(session["company"]), "title": _text(session["title"]),
        })
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


__all__ = [
    "router", "build_status", "bridge_status", "current_status", "onboarding_evidence", "tracker_assistant",
    "translate_settings", "validate_patch", "read_extension_settings", "write_extension_settings", "generation_summary",
    "profile_summary", "prune_audit", "extension_answer_ids",
]
