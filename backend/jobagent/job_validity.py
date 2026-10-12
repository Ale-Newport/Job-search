"""Evidence-based deadlines: posted dates never imply an application deadline."""
from datetime import datetime, time, timezone
import re
from zoneinfo import ZoneInfo

from dateutil import parser

from .db import now


def deadline_evidence(raw):
    metadata = raw.get('metadata') or {}
    value = raw.get('application_deadline') or metadata.get('application_deadline')
    source = metadata.get('deadline_source') or raw.get('url')
    text = raw.get('description') or ''
    if not value:
        match = re.search(r'(?:application deadline|closing date|applications close)\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})', text, re.I)
        if match:
            value = match.group(1)
    posting_only = not value and bool(metadata.get('valid_through'))
    if posting_only:
        value = metadata['valid_through']
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
        if posting_only:
            return {'application_deadline': None, 'deadline_timezone': None, 'deadline_source': source,
                    'deadline_state': 'EXPIRED' if state == 'EXPIRED' else 'POSTING_VALIDITY', 'deadline_verified_at': now()}
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
        content, _, final = await asyncio.wait_for(fetch(job.get('application_url') or job['url']), 30)
        from .career_pages import parse_html
        from .discovery import parse_jsonld
        soup = parse_html(content.decode(errors='replace'))
        for node in soup.select('header,footer,nav,script,style'):
            node.decompose()
        main = soup.find('main') or soup.find(attrs={'role': 'main'}) or soup
        text = clean_text(str(main))
        closed = bool(re.search(r'(?:this (?:job|position)|job posting).{0,40}(?:closed|no longer available)|no longer accepting applications', text, re.I))
        # A successful HTTP response alone cannot attest an active application.
        title_words = set(re.findall(r'\w+', job.get('title', '').lower()))
        page_words = set(re.findall(r'\w+', text.lower()))
        same_role = bool(title_words) and len(title_words & page_words) / len(title_words) >= .8
        structured = parse_jsonld(content.decode(errors='replace'), final)
        same_role = same_role or any(clean_text(post.get('title')).casefold() == clean_text(job.get('title')).casefold() for post in structured)
        opened = same_role and bool(re.search(r'apply (?:now|for|to)|submit application|application form', text, re.I))
        availability = 'CLOSED' if closed else 'OPEN' if opened else 'UNKNOWN'
    except Exception as exc:
        import httpx
        availability = 'CLOSED' if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (404, 410) else 'TEMPORARILY_UNAVAILABLE'
    db.execute('UPDATE jobs SET availability=?,last_verified=? WHERE id=?', (availability, now(), job.get('job_id') or job['id']))
    return availability
