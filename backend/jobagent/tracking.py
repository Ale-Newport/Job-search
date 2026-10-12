"""Daily discovery and manual application tracking, independent of submission automation."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .db import decode_row, dumps, get_db, now, uid

router = APIRouter()
SUBMITTED = {
    "APPLIED",
    "CONFIRMED",
    "RECRUITER_SCREEN",
    "ASSESSMENT",
    "TECHNICAL_TEST",
    "INTERVIEW",
    "FINAL_INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
    "GHOSTED",
}


class RecordApplication(BaseModel):
    applied_at: datetime | None = None
    notes: str = Field(default="", max_length=20000)


def record_application(db, job_id, applied_at=None, notes="", origin="manual"):
    stamp = now()
    submitted_at = applied_at or stamp
    with db.transaction() as conn:
        if not conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
            raise HTTPException(404, "Job not found")
        app = conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        if app and app["status"] in SUBMITTED:
            return dict(app) | {"already_recorded": True}
        identifier = app["id"] if app else uid()
        if not app:
            conn.execute(
                "INSERT INTO applications(id,job_id,mode,status,notes,created_at,updated_at) VALUES(?,?,'manual','APPLIED',?,?,?)",
                (identifier, job_id, notes, stamp, stamp),
            )
        else:
            conn.execute(
                "UPDATE applications SET mode='manual',status='APPLIED',notes=CASE WHEN ?='' THEN notes ELSE ? END,updated_at=? WHERE id=?",
                (notes, notes, stamp, identifier),
            )
        conn.execute(
            "INSERT INTO application_events VALUES(?,?,?,?,?,?)",
            (
                uid(),
                identifier,
                "APPLIED",
                "Application submitted externally; recorded by you."
                if origin == "manual"
                else "Application identified from confirmation email.",
                origin,
                submitted_at,
            ),
        )
        conn.execute("UPDATE jobs SET status='APPLIED',updated_at=? WHERE id=?", (stamp, job_id))
        conn.execute(
            "UPDATE human_tasks SET status='resolved',answer='Recorded as applied externally',updated_at=? WHERE application_id=? AND kind='PREPARATION_REVIEW'",
            (stamp, identifier),
        )
    # Reconcile emails received before the user recorded the application.
    from .mail import reconcile_unlinked

    reconcile_unlinked(db)
    return decode_row(db.one("SELECT * FROM applications WHERE id=?", (identifier,))) | {"already_recorded": False}


@router.post("/jobs/{job_id}/record-application")
def record(job_id: str, payload: RecordApplication, request: Request):
    submitted = payload.applied_at
    if submitted:
        if submitted.tzinfo is None:
            submitted = submitted.replace(tzinfo=timezone.utc)
        if submitted > datetime.now(timezone.utc):
            raise HTTPException(422, "The application date cannot be in the future")
    return record_application(get_db(request), job_id, submitted.isoformat() if submitted else None, payload.notes)


def tracker_rows(db):
    rows = db.query("""SELECT j.*,a.id AS application_id,a.status AS application_status,a.notes AS application_notes,
        a.updated_at AS application_updated_at,
        (SELECT MIN(created_at) FROM application_events e WHERE e.application_id=a.id AND e.status IN ('APPLIED','CONFIRMED')) AS applied_at,
        c.notes AS company_notes,c.careers_url AS careers_url
        FROM jobs j LEFT JOIN applications a ON a.job_id=j.id LEFT JOIN companies c ON c.id=j.company_id OR (j.company_id IS NULL AND c.name=j.company COLLATE NOCASE)
        ORDER BY COALESCE(a.updated_at,j.created_at) DESC""")
    emails = {}
    for row in db.query(
        "SELECT id,application_id,subject,received_at,deadline,classification FROM email_messages WHERE application_id IS NOT NULL ORDER BY received_at DESC,rowid DESC"
    ):
        emails.setdefault(row["application_id"], []).append(dict(row))
    result = []
    for raw in rows:
        j = decode_row(raw)
        related = emails.get(j["application_id"], [])
        j["last_email"] = related[0] if related else None
        j["email_count"] = len(related)
        j["tracking_status"] = j["application_status"] or j["status"]
        j["submitted"] = j["application_status"] in SUBMITTED
        current_email = related[0] if related else {}
        deadline_stage = {
            "CODING_TEST": "TECHNICAL_TEST",
            "ASSESSMENT_INVITATION": "ASSESSMENT",
            "INTERVIEW_REQUEST": "INTERVIEW",
            "INTERVIEW_CONFIRMATION": "INTERVIEW",
        }.get(current_email.get("classification"))
        deadline_current = (
            current_email.get("classification") == "DOCUMENT_REQUEST" or deadline_stage == j["tracking_status"]
        )
        j["deadline"] = (
            current_email.get("deadline")
            if j["submitted"]
            and deadline_current
            and j["tracking_status"] not in {"REJECTED", "WITHDRAWN", "OFFER", "GHOSTED"}
            else None
            if j["submitted"]
            else j.get('application_deadline') or j["metadata"].get("application_deadline")
        )
        j["deadline_kind"] = "Recruitment task" if j["submitted"] else "Apply before"
        result.append(j)
    return result


@router.get("/tracker")
def tracker(
    request: Request,
    q: str = "",
    scope: Literal["all", "saved", "applied", "pending", "ignored"] = "all",
    status: str = "",
    source: str = "",
    confirmed_open: bool = False,
    deadline_days: int | None = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from .job_validity import eligible
    rows = tracker_rows(get_db(request))
    def within_deadline(job):
        if deadline_days is None:
            return True
        value = job.get('application_deadline')
        if not value:
            return False
        try:
            from dateutil.parser import isoparse
            from zoneinfo import ZoneInfo
            deadline = isoparse(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=ZoneInfo(job.get('deadline_timezone') or 'Europe/London'))
            seconds = (deadline-datetime.now(timezone.utc)).total_seconds()
            return 0 <= seconds <= deadline_days*86400
        except (ValueError, TypeError):
            return False
    filtered = [
        j
        for j in rows
        if (not q or q.casefold() in f"{j['title']} {j['company']} {j['location']}".casefold())
        and (not status or j["tracking_status"] == status)
        and (not source or j["source"] == source)
        and (not confirmed_open or j['availability']=='OPEN')
        and within_deadline(j)
        and (scope != "saved" or j["status"] == "SHORTLISTED" and not j["submitted"])
        and (scope != "applied" or j["submitted"])
        and (scope != "pending" or not j["submitted"] and j["status"] != "IGNORED" and eligible(j))
        and (scope != "ignored" or j["status"] == "IGNORED")
        and (scope == "ignored" or j["status"] != "IGNORED")
    ]
    return {
        "items": filtered[offset : offset + limit],
        "total": len(filtered),
        "sources": sorted({j["source"] for j in rows if j["source"]}),
    }


def daily_data(db):
    from .core import get_settings
    from .discovery import discovery_busy, source_health
    from .geography import location_evidence, recommendable

    settings = get_settings(db)
    from zoneinfo import ZoneInfo
    local_zone = ZoneInfo(settings.get('discovery_timezone', 'Europe/London'))
    today = datetime.now(local_zone).date().isoformat()
    rows = tracker_rows(db)
    for j in rows:
        try:
            j["found_today"] = datetime.fromisoformat(j["created_at"]).astimezone(local_zone).date().isoformat() == today
        except (ValueError, TypeError):
            j["found_today"] = False
    from .job_validity import eligible
    pending = [
        j
        for j in rows
        if eligible(j) and not j["submitted"]
        and j["status"] != "IGNORED"
        and not j["metadata"].get("closed")
        and (
            not j["metadata"].get("application_deadline")
            or j["metadata"].get("rolling")
            or j["metadata"]["application_deadline"][:10] >= today
        )
        and (not j["metadata"].get("opening_date") or j["metadata"]["opening_date"][:10] <= today)
    ]

    def suitable(j):
        if not recommendable(j, settings):
            return False
        if j.get("posted_at") and j["status"] != "SHORTLISTED" and not j["metadata"].get("application_deadline"):
            try:
                posted = datetime.fromisoformat(j["posted_at"].replace("Z", "+00:00"))
                if posted.replace(tzinfo=posted.tzinfo or timezone.utc) < datetime.now(timezone.utc) - timedelta(
                    days=90
                ):
                    return False
            except ValueError:
                pass
        details = j["match_details"] or {}
        role = details.get("components", {}).get("role", {}).get("value", 1)
        return (
            not details.get("exclusions")
            and role >= 0.5
            and (j.get("match_score") or 0) >= settings.get("daily_min_match", 50)
        )

    recommended = [j for j in pending if suitable(j)]
    recommended.sort(
        key=lambda j: (
            settings.get("preferred_city") == "London" and location_evidence(j.get("location"))["london"],
            j["found_today"],
            bool(re.search(r"graduate|new grad|junior|early career|entry.level|associate", j["title"], re.I)),
            j["status"] == "SHORTLISTED",
            j.get("priority_score") or 0,
            j["created_at"],
        ),
        reverse=True,
    )
    integrations = [
        dict(r)
        for r in db.query(
            "SELECT provider,status,last_sync,last_error FROM integrations WHERE provider IN ('gmail','outlook','imap')"
        )
    ]
    # Older saved URLs can identify the same posting through different ATS
    # account aliases. Keep every history row, but recommend each vacancy once
    # and respect an application recorded against any equivalent URL.
    from .discovery import ats_identity, canonical_url
    def posting_key(job):
        url = job.get('application_url') or job.get('url') or ''
        try:
            return ats_identity(url)[1] or canonical_url(url)
        except ValueError:
            return job['id']
    seen_postings = {posting_key(j) for j in rows if j['submitted']}
    distinct = []
    for job in recommended:
        key = posting_key(job)
        if key not in seen_postings:
            distinct.append(job)
            seen_postings.add(key)
    recommended = distinct
    return {
        "date": today,
        "region": settings.get("suggested_region", "any"),
        "preferred_city": settings.get("preferred_city", ""),
        "items": recommended[:50],
        "total": len(recommended),
        "new_today": sum(j["found_today"] for j in recommended),
        "applied": sum(j["submitted"] for j in rows),
        "saved": sum(j["status"] == "SHORTLISTED" for j in pending),
        "last_refresh": settings.get("daily_last_refresh"),
        "refreshing": discovery_busy(db),
        "scheduler_enabled": settings.get("scheduler_enabled", False),
        "sources": [source_health(db, r) for r in db.query("SELECT * FROM sources ORDER BY name")],
        "email": integrations,
    }


@router.get("/daily")
def daily(request: Request):
    result = daily_data(get_db(request))
    if hasattr(request.app.state.automation, 'enqueue'):
        result['refreshing'] = bool(get_db(request).one("SELECT id FROM automation_tasks WHERE kind='discovery' AND state NOT IN ('COMPLETED','FAILED','CANCELLED')"))
    return result


async def refresh_daily(db, data_dir):
    from .discovery import run_sources

    result = await run_sources(db, data_dir)
    if result.get("status") != "already_running":
        # Jobs can disclose a verified employer board that was unavailable at
        # startup. Add and check those boards in this cycle, preserving every
        # existing source and its enable/disable choice.
        from .career_sources import import_observed_boards
        known = {row['id'] for row in db.query('SELECT id FROM sources')}
        await asyncio.to_thread(import_observed_boards,db)
        added = {row['id'] for row in db.query('SELECT id FROM sources WHERE enabled=1')} - known
        if added:
            followup = await run_sources(db,data_dir,due_only=True)
            fresh = [item for item in followup['items'] if item.get('source_id') in added]
            result['items'].extend(fresh)
            result['total'] = len(result['items'])
            result['new_jobs'] += sum(item['new_jobs'] for item in fresh)
            result['jobs_found'] = sum(item.get('jobs_found',0) for item in result['items'])
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        setting = db.one("SELECT value FROM settings WHERE key='discovery_timezone'")
        try:
            zone = ZoneInfo(json.loads(setting['value']) if setting else 'Europe/London')
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            zone = ZoneInfo('Europe/London')
        value = {
            "at": now(),
            "day": datetime.now(zone).date().isoformat(),
            "new_jobs": result["new_jobs"],
            "errors": sum(bool(r.get("error")) for r in result["items"]),
            "sources_checked": sum(r.get('state') != 'EMAIL_DELIVERY' for r in result['items']),
            "complete_sources": sum(r.get('state') in ('HEALTHY', 'EMPTY') for r in result['items']),
            "partial_sources": sum(r.get('state') == 'PARTIAL' for r in result['items']),
        }
        db.execute("INSERT OR REPLACE INTO settings VALUES('daily_last_refresh',?)", (dumps(value),))
    return result


@router.post("/daily/refresh")
async def daily_refresh(request: Request):
    app = request.app
    if hasattr(app.state.automation, 'enqueue'):
        return app.state.automation.enqueue('discovery', dedupe_key='discovery:manual')
    task = getattr(app.state, "daily_refresh_task", None)
    if task and not task.done():
        return {"status": "already_running"}

    async def run():
        try:
            await refresh_daily(app.state.db, app.state.data_dir)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("daily_refresh_failed")

    app.state.daily_refresh_task = asyncio.create_task(run())
    return {"status": "started"}
