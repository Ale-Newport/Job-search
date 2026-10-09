"""Evidence-grounded professional contacts and explicit manual LinkedIn handoff."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import decode_row, dumps, get_db, now, uid

router = APIRouter(prefix='/networking')
KINDS = {'recruiter', 'hiring_manager', 'alumni', 'application_followup', 'referral_enquiry', 'interview_followup'}


def profile_url(value):
    parsed = urlparse(value.strip())
    if parsed.scheme != 'https' or parsed.hostname not in ('www.linkedin.com', 'linkedin.com') or not re.fullmatch(r'/in/[A-Za-z0-9_%.-]+/?', parsed.path) or parsed.username:
        raise ValueError('Use the exact public LinkedIn profile URL (https://www.linkedin.com/in/…)')
    return 'https://www.linkedin.com' + parsed.path.rstrip('/')


def relevance(contact, job=None):
    evidence = contact.get('evidence') or {}
    reasons, score = [], 0
    if not contact.get('last_verified'):
        return 0, ['Verify the current role and source before recommending outreach']
    if job and contact['company'].casefold() == job['company'].casefold():
        score += 30
        reasons.append('Verified current company matches this opportunity')
    if evidence.get('recruiting_responsibility'):
        score += 30
        reasons.append(str(evidence['recruiting_responsibility'])[:300])
    if evidence.get('team_relationship'):
        score += 25
        reasons.append(str(evidence['team_relationship'])[:300])
    if evidence.get('shared_background'):
        score += 10
        reasons.append(str(evidence['shared_background'])[:300])
    if evidence.get('existing_connection'):
        score += 5
        reasons.append('User confirmed an existing professional connection')
    return min(100, score), reasons


class ContactPayload(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=300)
    role: str = Field(min_length=1, max_length=300)
    profile_url: str = Field(max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    verified: bool = False
    evidence: dict[str, str] = Field(default_factory=dict)
    notes: str = Field(default='', max_length=5000)
    job_id: str | None = None


@router.get('/contacts')
def contacts(request: Request, job_id: str | None = None, offset: int = 0, limit: int = 50):
    db = get_db(request)
    job = db.one('SELECT * FROM jobs WHERE id=?', (job_id,)) if job_id else None
    rows = [decode_row(r) for r in db.query('SELECT * FROM professional_contacts ORDER BY updated_at DESC LIMIT ? OFFSET ?', (min(100, max(1, limit)), max(0, offset)))]
    if job:
        rows = [r for r in rows if r['company'].casefold() == job['company'].casefold() or db.one('SELECT contact_id FROM contact_job_links WHERE contact_id=? AND job_id=?', (r['id'], job_id))]
    for row in rows:
        row['relevance'], row['reasons'] = relevance(row, job)
        row['messages'] = [decode_row(m) for m in db.query('SELECT * FROM outreach_messages WHERE contact_id=? ORDER BY created_at DESC', (row['id'],))]
        row['followups'] = db.query('SELECT * FROM contact_followups WHERE contact_id=? ORDER BY due_at', (row['id'],))
    rows.sort(key=lambda r: r['relevance'], reverse=True)
    return {'items': rows, 'total': db.one('SELECT count(*) AS n FROM professional_contacts')['n'],
            'capabilities': {'direct_send': False, 'manual_handoff': True, 'conversation_import': True},
            'search_url': 'https://www.linkedin.com/search/results/people/?keywords=' + quote((job['company']+' early careers recruiter') if job else 'early careers recruiter')}


@router.post('/contacts')
def save_contact(payload: ContactPayload, request: Request):
    db = get_db(request)
    url = profile_url(payload.profile_url)
    if payload.job_id and not db.one('SELECT id FROM jobs WHERE id=?', (payload.job_id,)):
        raise ValueError('Related job not found')
    if len(dumps(payload.evidence)) > 10000:
        raise ValueError('Contact evidence exceeds 10 KB')
    with db.transaction() as conn:
        existing = conn.execute('SELECT id FROM professional_contacts WHERE profile_url=?', (url,)).fetchone()
        contact_id = existing['id'] if existing else uid()
        company = conn.execute('SELECT id FROM companies WHERE name=? COLLATE NOCASE', (payload.company,)).fetchone()
        if not existing:
            conn.execute('INSERT INTO professional_contacts(id,full_name,company,company_id,role,profile_url,source,evidence,last_verified,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (contact_id, payload.full_name, payload.company, company['id'] if company else None, payload.role, url, payload.source, dumps(payload.evidence), now() if payload.verified else None, payload.notes, now(), now()))
        else:
            conn.execute('UPDATE professional_contacts SET full_name=?,company=?,role=?,source=?,evidence=?,last_verified=?,notes=?,updated_at=? WHERE id=?',
                         (payload.full_name, payload.company, payload.role, payload.source, dumps(payload.evidence), now() if payload.verified else None, payload.notes, now(), contact_id))
        if payload.job_id:
            conn.execute('INSERT OR IGNORE INTO contact_job_links VALUES(?,?,?)', (contact_id, payload.job_id, payload.source))
    return decode_row(db.one('SELECT * FROM professional_contacts WHERE id=?', (contact_id,)))


class DraftPayload(BaseModel):
    job_id: str
    kind: str = 'recruiter'
    max_characters: int = Field(default=2000, ge=100, le=4000)


@router.post('/contacts/{contact_id}/draft')
def draft(contact_id: str, payload: DraftPayload, request: Request):
    db = get_db(request)
    contact = decode_row(db.one('SELECT * FROM professional_contacts WHERE id=?', (contact_id,)))
    job = decode_row(db.one('SELECT * FROM jobs WHERE id=?', (payload.job_id,)))
    if not contact or not job or not contact['last_verified']:
        raise ValueError('Verify the contact and select an existing job first')
    if payload.kind not in KINDS:
        raise ValueError('Unknown outreach strategy')
    application = db.one('SELECT * FROM applications WHERE job_id=?', (job['id'],))
    if application and application['status'] in ('REJECTED', 'WITHDRAWN', 'CLOSED'):
        raise ValueError('This application is closed; review whether further outreach is appropriate')
    if db.one("SELECT id FROM outreach_messages WHERE contact_id=? AND job_id=? AND state='CONTACTED'", (contact_id, job['id'])):
        raise ValueError('Outreach for this role is already recorded. Review the conversation before following up')
    if payload.kind in ('application_followup', 'interview_followup') and (not application or application['status'] in ('PREPARING', 'NEEDS_REVIEW', 'ERROR')):
        raise ValueError('This strategy requires a recorded submitted application')
    if payload.kind == 'interview_followup' and not (contact['evidence'].get('interview_context') and db.one("SELECT id FROM application_events WHERE application_id=? AND status IN ('INTERVIEW','FINAL_INTERVIEW')", (application['id'],))):
        raise ValueError('Record the actual interview context before an interview follow-up')
    if payload.kind == 'referral_enquiry' and not contact['evidence'].get('existing_connection'):
        raise ValueError('A referral enquiry needs a confirmed existing connection')
    facts = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    # Existing role-aware Candidate Intelligence, with no new invented candidate profile.
    from .intelligence.store import Knowledge
    from .intelligence.narratives import compose_cover_letter
    narrative = compose_cover_letter(Knowledge(db), job, flat_facts=facts)
    used = narrative.get('facts', [])
    professional = [f for f in used if f.get('category') not in ('identity', 'personal', 'contact', 'sensitive')]
    if not professional:
        professional = [f for f in facts if f['category'] in ('project', 'experience', 'education')][:2]
    intro = ' '.join(f['value'] for f in professional[:2])[:600]
    first = contact['full_name'].split()[0]
    role_interest = f"I'm interested in the {job['title']} role at {job['company']}."
    if payload.kind == 'application_followup':
        role_interest = f"I applied for the {job['title']} role at {job['company']} and would appreciate any guidance on the next steps."
    ask = {'recruiter': 'Would you be able to share how this role fits your early careers hiring?',
           'hiring_manager': 'I would be interested to hear about the work this team is prioritising.',
           'alumni': 'I would appreciate any insight into your experience joining the company.',
           'application_followup': 'Please let me know if any further information would help.',
           'referral_enquiry': 'If you think my background fits, would you be comfortable discussing a referral?',
           'interview_followup': 'Thank you for the conversation; I would be glad to answer any further questions.'}[payload.kind]
    shared = contact['evidence'].get('shared_background', '') if payload.kind == 'alumni' else ''
    body = f"Hi {first}, {role_interest} {intro} {shared} {ask} Thanks!"
    if len(body) > payload.max_characters:
        body = f"Hi {first}, {role_interest} {ask}"
    if len(body) > payload.max_characters:
        raise ValueError('The connection-note limit is too short for this role; edit a shorter message manually')
    identifier = uid()
    db.execute('INSERT INTO outreach_messages(id,contact_id,job_id,kind,body,fact_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
               (identifier, contact_id, job['id'], payload.kind, body, dumps([f['id'] for f in professional[:2]]), now(), now()))
    db.execute("UPDATE professional_contacts SET status='MESSAGE_PREPARED',updated_at=? WHERE id=?", (now(), contact_id))
    return decode_row(db.one('SELECT * FROM outreach_messages WHERE id=?', (identifier,)))


class MessagePatch(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.patch('/messages/{message_id}')
def edit_message(message_id: str, payload: MessagePatch, request: Request):
    db = get_db(request)
    if not db.one("SELECT id FROM outreach_messages WHERE id=? AND state='MESSAGE_PREPARED'", (message_id,)):
        raise ValueError('Only an unsent draft can be edited')
    db.execute('UPDATE outreach_messages SET body=?,updated_at=? WHERE id=?', (payload.body, now(), message_id))
    return {'id': message_id}


class HandoffPayload(BaseModel):
    approved: bool = False
    sent_confirmed: bool = False
    evidence: str = Field(default='', max_length=2000)


@router.post('/messages/{message_id}/handoff')
def handoff(message_id: str, payload: HandoffPayload, request: Request):
    if not payload.approved:
        raise ValueError('Review and explicitly approve this exact message first')
    db = get_db(request)
    message = db.one('SELECT m.*,c.profile_url FROM outreach_messages m JOIN professional_contacts c ON c.id=m.contact_id WHERE m.id=?', (message_id,))
    if not message or message['state'] == 'CONTACTED':
        raise ValueError('Message missing or already recorded as sent')
    return {'url': message['profile_url'], 'body': message['body'], 'direct_send': False, 'message_id': message_id}


@router.post('/messages/{message_id}/confirm')
def confirm_sent(message_id: str, payload: HandoffPayload, request: Request):
    if not payload.sent_confirmed or not payload.evidence.strip():
        raise ValueError('Confirm the actual LinkedIn send and record its evidence')
    db = get_db(request)
    with db.transaction() as conn:
        message = conn.execute('SELECT * FROM outreach_messages WHERE id=?', (message_id,)).fetchone()
        if not message:
            raise HTTPException(404, 'Message not found')
        if message['state'] == 'CONTACTED':
            return {'id': message_id, 'duplicate': True}
        conn.execute("UPDATE outreach_messages SET state='CONTACTED',updated_at=? WHERE id=?", (now(), message_id))
        conn.execute("UPDATE professional_contacts SET status='CONTACTED',updated_at=? WHERE id=?", (now(), message['contact_id']))
        conn.execute('INSERT INTO outreach_events VALUES(?,?,?,?,?)', (uid(), message_id, 'CONTACTED', payload.evidence, now()))
    return {'id': message_id, 'state': 'CONTACTED'}


class FollowUpPayload(BaseModel):
    due_at: str
    notes: str = Field(default='', max_length=2000)


@router.post('/contacts/{contact_id}/followups')
def followup(contact_id: str, payload: FollowUpPayload, request: Request):
    datetime.fromisoformat(payload.due_at)
    db = get_db(request)
    if not db.one('SELECT id FROM professional_contacts WHERE id=?', (contact_id,)):
        raise ValueError('Contact not found')
    identifier = uid()
    db.execute('INSERT INTO contact_followups VALUES(?,?,?,?,?,?)', (identifier, contact_id, payload.due_at, payload.notes, 'OPEN', now()))
    return {'id': identifier}


class ConversationPayload(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    received_at: str
    external_id: str = Field(default='', max_length=300)


@router.post('/contacts/{contact_id}/conversation')
def conversation(contact_id: str, payload: ConversationPayload, request: Request):
    db = get_db(request)
    contact = db.one('SELECT * FROM professional_contacts WHERE id=?', (contact_id,))
    if not contact:
        raise ValueError('Contact not found')
    datetime.fromisoformat(payload.received_at)
    from .mail import ingest_message
    message = {'subject': 'LinkedIn conversation with '+contact['full_name'], 'sender': contact['full_name'],
               'body': payload.text, 'received_at': payload.received_at,
               'external_id': payload.external_id or hashlib.sha256((contact_id+payload.received_at+payload.text).encode()).hexdigest(),
               'metadata': {'contact_id': contact_id, 'source_url': contact['profile_url'], 'channel': 'linkedin_import'}}
    result = ingest_message(db, message, provider='linkedin_import')
    db.execute("UPDATE professional_contacts SET status='REPLIED',updated_at=? WHERE id=?", (now(), contact_id))
    return result
