"""Evidence-based deadlines: posted dates never imply an application deadline."""
from datetime import datetime, time, timezone
import re
from zoneinfo import ZoneInfo

from dateutil import parser

from .db import now


def deadline_evidence(raw):
    metadata = raw.get('metadata') or {}
    value = raw.get('application_deadline') or metadata.get('application_deadline') or metadata.get('valid_through')
    source = metadata.get('deadline_source') or raw.get('url')
    text = raw.get('description') or ''
    if not value:
        match = re.search(r'(?:application deadline|closing date|applications close)\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})', text, re.I)
        if match:
            value = match.group(1)
    if not value:
        return {'deadline_state': 'NO_DEADLINE_PUBLISHED' if text else 'UNKNOWN', 'application_deadline': None,
                'deadline_source': source, 'deadline_timezone': None, 'deadline_verified_at': now()}
    try:
        # Incomplete dates are ambiguous: never fill an unspecified year from today.
        if not re.search(r'\b\d{4}\b', str(value)):
            raise ValueError('Deadline year unspecified')
        zone = metadata.get('deadline_timezone') or 'Europe/London'
        parsed = parser.parse(str(value), fuzzy=False)
        if not re.search(r'\d{1,2}:\d{2}', str(value)):
            parsed = datetime.combine(parsed.date(), time.max)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        state = 'EXPIRED' if parsed < datetime.now(timezone.utc) else 'EXPLICIT_DEADLINE'
        return {'application_deadline': parsed.isoformat(), 'deadline_timezone': zone,
                'deadline_source': source, 'deadline_state': state, 'deadline_verified_at': now()}
    except (ValueError, TypeError, OverflowError, KeyError):
        return {'application_deadline': None, 'deadline_state': 'UNKNOWN', 'deadline_source': source,
                'deadline_timezone': None, 'deadline_verified_at': now()}


def eligible(job, *, at=None):
    if job.get('availability') == 'CLOSED' or (job.get('metadata') or {}).get('closed'):
        return False
    deadline = job.get('application_deadline') or (job.get('metadata') or {}).get('application_deadline') or (job.get('metadata') or {}).get('valid_through')
    if deadline:
        try:
            value = parser.isoparse(deadline)
            if len(deadline) == 10:
                value = datetime.combine(value.date(), time.max)
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo(job.get('deadline_timezone') or 'Europe/London'))
            if value < (at or datetime.now(timezone.utc)):
                return False
        except (ValueError, TypeError):
            pass
    return job.get('deadline_state') != 'EXPIRED'


async def verify_job(db, job):
    import asyncio
    from .discovery import fetch, clean_text
    try:
        content, _, _ = await asyncio.wait_for(fetch(job.get('application_url') or job['url']), 30)
        text = clean_text(content.decode(errors='replace'))
        closed = bool(re.search(r'(?:this (?:job|position)|job posting).{0,40}(?:closed|no longer available)|no longer accepting applications', text, re.I))
        # A successful HTTP response alone cannot attest an active application.
        opened = bool(re.search(r'apply (?:now|for|to)|submit application|application form', text, re.I))
        availability = 'CLOSED' if closed else 'OPEN' if opened else 'UNKNOWN'
    except Exception as exc:
        import httpx
        availability = 'CLOSED' if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (404, 410) else 'TEMPORARILY_UNAVAILABLE'
    db.execute('UPDATE jobs SET availability=?,last_verified=? WHERE id=?', (availability, now(), job.get('job_id') or job['id']))
    return availability
