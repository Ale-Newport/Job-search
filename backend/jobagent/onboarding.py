"""Derive setup progress from persisted evidence instead of a dismissible checkbox."""

import hashlib
import json
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .core import get_settings
from .db import now

router = APIRouter()
BOUNDARIES = (
    "default_mode",
    "auto_apply",
    "allowed_domains",
    "max_applications_day",
    "max_applications_company",
    "minimum_interval_seconds",
    "min_match",
    "min_confidence",
    "sensitive_policies",
    "scheduler_enabled",
    "discovery_interval_minutes",
    "email_interval_minutes",
    "rules",
    "default_document_version_id",
)
BROWSER_AI = (
    "browser_engine",
    "browser_confidence_threshold",
    "laya_endpoint",
    "jev_endpoint",
    "jev_model",
    "jev_fallback_enabled",
)
TEXT_AI = ("text_provider", "text_base_url", "text_model")


def read_record(db, name):
    row = db.one("SELECT value FROM settings WHERE key=?", (f"onboarding:{name}",))
    return json.loads(row["value"]) if row else None


def record(db, name, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"onboarding:{name}", json.dumps(value)))


def fingerprint(db, fields):
    settings = get_settings(db)
    return hashlib.sha256(json.dumps({key: settings.get(key) for key in fields}, sort_keys=True).encode()).hexdigest()


def record_ai_probe(db, kind, result):
    success = (
        bool(result.get("available") and result.get("inference_verified") and result.get("correct_target"))
        if kind == "browser"
        else bool(result.get("answer"))
    )
    record(
        db,
        f"ai_{kind}",
        {
            "success": success,
            "checked_at": now(),
            "fingerprint": fingerprint(db, BROWSER_AI if kind == "browser" else TEXT_AI),
            "provider": result.get("engine", result.get("provider")),
        },
    )


def onboarding_status(db):
    settings = get_settings(db)
    counts = db.one(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN verification_status='verified' OR locked=1 THEN 1 ELSE 0 END) AS verified, "
        "SUM(CASE WHEN verification_status='unverified' AND locked=0 THEN 1 ELSE 0 END) AS unverified FROM facts"
    )
    counts = {key: value or 0 for key, value in counts.items()}
    documents = db.one("SELECT COUNT(*) AS n FROM document_versions WHERE length(trim(text))>0")["n"]
    profiles = db.one("SELECT COUNT(*) AS n FROM search_profiles WHERE json_array_length(config,'$.roles')>0")["n"]
    browser = read_record(db, "browser_account")
    connected = db.query(
        "SELECT provider,last_sync,last_error FROM integrations WHERE provider IN ('gmail','outlook','imap') AND status='connected'"
    )
    mail_ready = any(row["last_sync"] and not row["last_error"] for row in connected)
    browser_probe, text_probe = read_record(db, "ai_browser"), read_record(db, "ai_text")
    browser_ready = settings.get("browser_engine") == "deterministic" or bool(
        browser_probe
        and browser_probe.get("success")
        and browser_probe.get("fingerprint") == fingerprint(db, BROWSER_AI)
    )
    text_ready = settings.get("text_provider", "none") == "none" or bool(
        text_probe and text_probe.get("success") and text_probe.get("fingerprint") == fingerprint(db, TEXT_AI)
    )
    boundaries = read_record(db, "boundaries")
    boundaries_ready = bool(boundaries and boundaries.get("fingerprint") == fingerprint(db, BOUNDARIES))
    sources = db.query("SELECT id,name,last_checked,last_error,jobs_found FROM sources WHERE last_checked IS NOT NULL")
    successful = [row for row in sources if not row["last_error"]]

    def step(identifier, complete, detail, started=False, attention=False, evidence=None):
        return {
            "id": identifier,
            "status": "complete"
            if complete
            else "needs_attention"
            if attention
            else "in_progress"
            if started
            else "pending",
            "detail": detail,
            "evidence": evidence or {},
        }

    steps = [
        step(
            "documents",
            documents > 0,
            f"{documents} readable document versions imported.",
            evidence={"versions": documents},
        ),
        step(
            "profile",
            counts["verified"] > 0 and counts["unverified"] == 0,
            f"{counts['verified']} verified facts; {counts['unverified']} still require review.",
            started=counts["total"] > 0,
            attention=counts["unverified"] > 0,
            evidence=counts,
        ),
        step(
            "search_profile",
            profiles > 0,
            f"{profiles} search profiles with roles configured.",
            evidence={"profiles": profiles},
        ),
        step(
            "browser_account",
            bool(browser and browser.get("confirmed_at")),
            "Account login explicitly confirmed by you; session expiry can still require sign-in."
            if browser
            else "Open the dedicated browser, sign in, then confirm the account.",
            evidence=browser,
        ),
        step(
            "email",
            mail_ready,
            "Connected email has completed a successful sync."
            if mail_ready
            else "Email account connected. Complete the first sync in Email."
            if connected
            else "Authorize an email account and complete its first sync.",
            started=bool(connected),
            attention=any(row["last_error"] for row in connected),
        ),
        step(
            "ai",
            browser_ready and text_ready,
            "Browser decisions and text configuration are ready."
            if browser_ready and text_ready
            else "Test the selected browser engine and any configured text provider.",
            started=bool(browser_probe or text_probe),
            evidence={"browser_ready": browser_ready, "text_ready": text_ready},
        ),
        step(
            "boundaries",
            boundaries_ready,
            "Current automation settings explicitly reviewed."
            if boundaries_ready
            else "Review and confirm the current submission and scheduling limits.",
            started=bool(boundaries),
        ),
        step(
            "search_run",
            bool(successful),
            f"{len(successful)} sources completed discovery successfully.",
            started=bool(sources),
            attention=bool(sources) and not successful,
            evidence={"successful_sources": successful},
        ),
    ]
    return {
        "hidden": bool(read_record(db, "hidden")),
        "completed": sum(s["status"] == "complete" for s in steps),
        "total": len(steps),
        "steps": steps,
        "browser_confirmation": browser,
        "boundaries_confirmed_at": boundaries.get("confirmed_at") if boundaries_ready else None,
    }


@router.get("/onboarding")
def status(request: Request):
    return onboarding_status(request.app.state.db)


class Visibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hidden: bool


@router.patch("/onboarding")
def visibility(payload: Visibility, request: Request):
    record(request.app.state.db, "hidden", payload.hidden)
    return onboarding_status(request.app.state.db)


class BrowserConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=100)
    confirmed: bool


@router.post("/onboarding/browser-account")
def browser_account(payload: BrowserConfirmation, request: Request):
    db = request.app.state.db
    if not payload.confirmed:
        record(db, "browser_account", None)
    else:
        state = request.app.state.automation.browser.status()
        if not state.get("running") or not state.get("current_url"):
            raise HTTPException(409, "Open your dedicated browser and sign in before confirming the account")
        host = urlparse(state["current_url"]).hostname
        if not host or host in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(409, "Confirm a signed-in employment website, not a local test page")
        expected = {"linkedin": "linkedin.com", "indeed": "indeed.com"}.get(payload.provider.strip().casefold())
        if expected and host != expected and not host.endswith("." + expected):
            raise HTTPException(409, "The open browser page does not belong to the account being confirmed")
        record(
            db,
            "browser_account",
            {
                "provider": payload.provider.strip(),
                "label": payload.provider.strip(),
                "confirmed_at": now(),
                "host": host,
                "evidence": "user_confirmation",
            },
        )
    return onboarding_status(db)


@router.post("/onboarding/boundaries/confirm")
def boundaries_confirm(request: Request):
    db = request.app.state.db
    record(db, "boundaries", {"confirmed_at": now(), "fingerprint": fingerprint(db, BOUNDARIES)})
    return onboarding_status(db)
