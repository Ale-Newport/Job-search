from __future__ import annotations

import asyncio
import base64
import email
import hashlib
import imaplib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from dateutil import parser as dateparser

from .security import SecretStore

CATEGORIES = {
    "REJECTION": (
        "REJECTED",
        [
            r"not (?:to )?(?:move|moving|progress|proceed)",
            r"(?:selected|proceeding with|moving forward with) other candidates",
            r"not been (?:successful|selected)",
            r"won't be progressing",
            r"unable to offer you",
        ],
    ),
    "OFFER": ("OFFER", [r"offer of employment", r"pleased to offer", r"offer letter"]),
    "CODING_TEST": (
        "TECHNICAL_TEST",
        [r"coding (?:test|challenge|assessment)", r"hackerrank", r"codility", r"technical (?:test|assessment)"],
    ),
    "ASSESSMENT_INVITATION": (
        "ASSESSMENT",
        [r"online assessment", r"complete.{0,30}assessment", r"assessment invitation"],
    ),
    "INTERVIEW_CONFIRMATION": (
        "INTERVIEW",
        [r"interview.{0,20}confirmed", r"confirm.{0,30}interview", r"interview is scheduled"],
    ),
    "INTERVIEW_REQUEST": (
        "INTERVIEW",
        [r"invite.{0,60}interview", r"schedule.{0,30}interview", r"availability.{0,40}interview"],
    ),
    "APPLICATION_CONFIRMATION": (
        "CONFIRMED",
        [
            r"thank you for (?:applying|your application)",
            r"application (?:has been )?received",
            r"received your application",
        ],
    ),
    "BACKGROUND_CHECK": (None, [r"background check", r"background screening"]),
    "DOCUMENT_REQUEST": (None, [r"please (?:send|provide|upload).{0,30}(?:document|transcript|certificate)"]),
    "FOLLOW_UP": (None, [r"following up", r"follow.up on"]),
    "RECRUITER_MESSAGE": (
        "RECRUITER_SCREEN",
        [r"recruiter", r"recruitment", r"talent acquisition", r"discuss.{0,30}(?:role|opportunity)"],
    ),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_message(raw: bytes) -> dict:
    message = email.message_from_bytes(raw, policy=policy.default)
    body = message.get_body(preferencelist=("plain", "html")) if message.is_multipart() else message
    content = body.get_content() if body else ""
    if not isinstance(content, str):
        content = content.decode("utf-8", errors="replace")
    if body and body.get_content_type() == "text/html":
        from bs4 import BeautifulSoup

        content = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
    try:
        received = parsedate_to_datetime(str(message["Date"]))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    return {
        "external_id": str(message.get("Message-ID") or hashlib.sha256(raw).hexdigest()),
        "subject": str(message.get("Subject", "(No subject)")),
        "sender": str(message.get("From", "")),
        "body": content[:200000],
        "received_at": received.isoformat(),
        "metadata": {"thread_id": str(message.get("In-Reply-To", ""))},
    }


def classify_message(subject: str, body: str, received_at: str | None = None) -> dict:
    # Quoted earlier emails do not determine the newest message's status.
    current_body = re.split(r"\n(?:On .{5,200}wrote:|From:|[- ]*Original Message[- ]*)", body, maxsplit=1)[0]
    content = f"{subject}\n{current_body}".lower()
    result: dict[str, Any] = {
        "category": "OTHER",
        "status": None,
        "confidence": 0.35,
        "evidence": "No recruitment pattern found",
        "deadline": None,
        "urls": [],
    }
    for category, (status, patterns) in CATEGORIES.items():
        match = next((re.search(pattern, content) for pattern in patterns if re.search(pattern, content)), None)
        if match:
            result.update(
                category=category,
                status=status,
                confidence=0.94 if category != "RECRUITER_MESSAGE" else 0.75,
                evidence=match.group(0),
            )
            break
    received = datetime.fromisoformat(received_at) if received_at else datetime.now(timezone.utc)
    relative = re.search(r"(?:within|in|next)\s+(\d{1,2})\s+(?:calendar\s+)?days?", content)
    absolute = re.search(
        r"(?:by|before|deadline[:\s]+|due[:\s]+)\s*(\d{1,2}\s+[a-z]+\s*\d{0,4}|[a-z]+\s+\d{1,2}(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2})",
        content,
    )
    if relative and result["category"] in ("CODING_TEST", "ASSESSMENT_INVITATION", "DOCUMENT_REQUEST"):
        result["deadline"] = (received + timedelta(days=int(relative.group(1)))).isoformat()
        result["deadline_evidence"] = relative.group(0)
    elif absolute:
        try:
            parsed = dateparser.parse(absolute.group(1), default=received, fuzzy=False)
            if parsed:
                result["deadline"] = parsed.isoformat()
                result["deadline_evidence"] = absolute.group(0)
        except (ValueError, OverflowError):
            pass
    result["urls"] = re.findall(r"https?://[^\s<>\"]+", body)[:20]
    result["application_reference"] = next(
        iter(
            re.findall(
                r"(?:application|reference|requisition|job)\s*(?:id|number|#|ref)?\s*[:#]\s*([\w-]{4,})", content
            )
        ),
        None,
    )
    return result


def link_candidate(db, message: dict) -> tuple[str | None, float]:
    metadata = message.get("metadata", {})
    thread_id = metadata.get("thread_id")
    if thread_id:
        linked = db.query(
            "SELECT DISTINCT application_id FROM email_messages WHERE (external_id=? OR json_extract(metadata,'$.thread_id')=?) AND application_id IS NOT NULL",
            (thread_id, thread_id),
        )
        if len(linked) == 1:
            return linked[0]["application_id"], 0.99
    candidates = db.query(
        "SELECT a.id,j.company,j.title FROM applications a JOIN jobs j ON a.job_id=j.id ORDER BY a.created_at DESC LIMIT 1000"
    )
    text = f"{message['subject']} {message['body']} {message['sender']}".lower()
    scores = []
    for candidate in candidates:
        company = candidate["company"].strip().lower()
        if len(company) < 3 or not re.search(r"\b" + re.escape(company) + r"\b", text):
            continue
        words = [x for x in re.findall(r"\w+", candidate["title"].lower()) if len(x) > 2]
        title_fit = sum(bool(re.search(r"\b" + re.escape(word) + r"\b", text)) for word in words) / max(1, len(words))
        score = 0.6 + title_fit * 0.35
        scores.append((score, candidate["id"]))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < 0.84 or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.12):
        return None, scores[0][0] if scores else 0
    return scores[0][1], scores[0][0]


def ingest_message(db, message: dict, provider: str = "import") -> dict:
    existing = db.one(
        "SELECT * FROM email_messages WHERE provider=? AND external_id=?", (provider, message["external_id"])
    )
    if existing:
        return {"id": existing["id"], "duplicate": True}
    classification = classify_message(message["subject"], message["body"], message.get("received_at"))
    application_id, link_confidence = link_candidate(db, message)
    message_id = str(uuid4())
    metadata = message.get("metadata", {}) | {"analysis": classification, "link_confidence": link_confidence}
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM email_messages WHERE provider=? AND external_id=?", (provider, message["external_id"])
        ).fetchone()
        if existing:
            return {"id": existing["id"], "duplicate": True}
        conn.execute(
            "INSERT INTO email_messages(id,provider,external_id,subject,sender,body,received_at,classification,confidence,application_id,deadline,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                provider,
                message["external_id"],
                message["subject"],
                message["sender"],
                message["body"],
                message.get("received_at", utcnow()),
                classification["category"],
                classification["confidence"],
                application_id,
                classification["deadline"],
                json.dumps(metadata),
            ),
        )
        if application_id and classification["status"] and classification["confidence"] >= 0.9:
            db.event(
                application_id,
                classification["status"],
                f"Email: {message['subject']} — {classification['evidence']}",
                origin="email",
                conn=conn,
            )
            if classification["category"] == "APPLICATION_CONFIRMATION":
                conn.execute(
                    "UPDATE automation_runs SET status='confirmed',checkpoint=json_set(checkpoint,'$.email_confirmation',?),updated_at=? WHERE application_id=? AND status IN ('interrupted','unconfirmed')",
                    (message_id, utcnow(), application_id),
                )
                conn.execute(
                    "UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE application_id=? AND kind IN ('PREPARATION_REVIEW','SUBMISSION_UNCONFIRMED','INTERRUPTED')",
                    (f"Confirmation email: {message_id}", utcnow(), application_id),
                )
        elif classification["category"] != "OTHER" and not application_id:
            conn.execute(
                "INSERT INTO human_tasks(id,application_id,kind,question,status,created_at,updated_at) VALUES(?,NULL,'EMAIL_LINK',?,'OPEN',?,?)",
                (str(uuid4()), f"Link recruitment email {message_id}: {message['subject']}", utcnow(), utcnow()),
            )
        conn.execute(
            "INSERT INTO email_classifications(id,email_id,classification,confidence,evidence,created_at) VALUES(?,?,?,?,?,?)",
            (
                str(uuid4()),
                message_id,
                classification["category"],
                classification["confidence"],
                json.dumps([classification["evidence"]]),
                utcnow(),
            ),
        )
        if application_id:
            conn.execute(
                "INSERT OR IGNORE INTO application_emails(application_id,email_id) VALUES(?,?)",
                (application_id, message_id),
            )
    return {"id": message_id, "duplicate": False, "classification": classification, "application_id": application_id}


OAUTH = {
    "gmail": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    },
    "outlook": {
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "offline_access https://graph.microsoft.com/Mail.Read",
    },
}


class MailService:
    def __init__(self, db, secrets_store: SecretStore):
        self.db, self.secrets = db, secrets_store
        self.pending: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    def config(self, provider):
        row = self.db.one("SELECT * FROM integrations WHERE provider=?", (provider,))
        if not row:
            raise ValueError("Configure this integration first")
        return json.loads(row["config"] or "{}")

    def connect(self, provider, redirect_uri):
        if provider not in OAUTH:
            raise ValueError("OAuth is available for Gmail and Outlook")
        config = self.config(provider)
        if not config.get("client_id"):
            raise ValueError("An OAuth client ID registered for a desktop application is required")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        self.pending = {key: value for key, value in self.pending.items() if value["expires"] > time.time()}
        self.pending[state] = {
            "provider": provider,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "expires": time.time() + 600,
        }
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "scope": OAUTH[provider]["scope"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if provider == "gmail":
            params.update(access_type="offline", prompt="consent")
        return OAUTH[provider]["authorize"] + "?" + urlencode(params)

    async def callback(self, state, code):
        pending = self.pending.pop(state, None)
        if not pending or pending["expires"] < time.time():
            raise ValueError("OAuth state is invalid or expired. Start connection again.")
        provider = pending["provider"]
        config = self.config(provider)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config["client_id"],
            "code_verifier": pending["verifier"],
            "redirect_uri": pending["redirect_uri"],
        }
        client_secret = self.secrets.get(f"{provider}:client_secret")
        if client_secret:
            data["client_secret"] = client_secret
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(OAUTH[provider]["token"], data=data)
            if response.status_code != 200:
                raise ValueError("OAuth token exchange failed. Check the registered client and redirect URI.")
            tokens = response.json()
        tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
        self.secrets.set(f"{provider}:tokens", json.dumps(tokens))
        self.db.execute("UPDATE integrations SET status='connected',last_error=NULL WHERE provider=?", (provider,))
        return provider

    async def token(self, provider):
        raw = self.secrets.get(f"{provider}:tokens")
        if not raw:
            raise ValueError("Connect this email account first")
        tokens = json.loads(raw)
        if tokens.get("expires_at", 0) > time.time() + 60:
            return tokens["access_token"]
        config = self.config(provider)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens.get("refresh_token", ""),
            "client_id": config["client_id"],
        }
        client_secret = self.secrets.get(f"{provider}:client_secret")
        if client_secret:
            data["client_secret"] = client_secret
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(OAUTH[provider]["token"], data=data)
            if response.status_code != 200:
                raise ValueError("Email authorization expired. Reconnect the account.")
            tokens.update(response.json())
        tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
        self.secrets.set(f"{provider}:tokens", json.dumps(tokens))
        return tokens["access_token"]

    async def fetch(self, provider):
        config = self.config(provider)
        if provider == "imap":
            password = self.secrets.get("imap:secret")
            if not password:
                raise ValueError("Store the IMAP app password in Keychain first")
            return await asyncio.to_thread(self._imap, config, password)
        token = await self.token(provider)
        async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}"}) as client:
            if provider == "gmail":
                row = self.db.one("SELECT last_sync FROM integrations WHERE provider=?", (provider,))
                query = "newer_than:30d"
                if row and row["last_sync"]:
                    query = f"after:{int(datetime.fromisoformat(row['last_sync']).timestamp()) - 300}"
                messages, page_token = [], None
                for _ in range(20):
                    params = {"maxResults": 100, "q": query}
                    if page_token:
                        params["pageToken"] = page_token
                    response = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages", params=params
                    )
                    response.raise_for_status()
                    payload = response.json()
                    for item in payload.get("messages", []):
                        if self.db.one(
                            "SELECT id FROM email_messages WHERE provider='gmail' AND external_id=?", (item["id"],)
                        ):
                            continue
                        detail = await client.get(
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",
                            params={"format": "raw"},
                        )
                        detail.raise_for_status()
                        record = detail.json()
                        raw = record["raw"]
                        message = parse_message(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                        message.update(external_id=item["id"])
                        message["metadata"]["thread_id"] = record.get("threadId")
                        messages.append(message)
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        return messages
                raise ValueError("Gmail sync exceeded 2000 messages; narrow the mailbox or sync window")
            if provider == "outlook":
                row = self.db.one("SELECT last_sync FROM integrations WHERE provider=?", (provider,))
                since = (
                    row["last_sync"]
                    if row and row["last_sync"]
                    else (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                )
                url = "https://graph.microsoft.com/v1.0/me/messages?" + urlencode(
                    {
                        "$top": 100,
                        "$filter": f"receivedDateTime ge {since}",
                        "$select": "id,subject,from,body,receivedDateTime,conversationId",
                    }
                )
                messages = []
                for _ in range(20):
                    if not url.startswith("https://graph.microsoft.com/"):
                        raise ValueError("Invalid Microsoft pagination URL")
                    response = await client.get(url, headers={"Prefer": 'outlook.body-content-type="text"'})
                    response.raise_for_status()
                    payload = response.json()
                    for item in payload.get("value", []):
                        messages.append(
                            {
                                "external_id": item["id"],
                                "subject": item.get("subject", ""),
                                "sender": item.get("from", {}).get("emailAddress", {}).get("address", ""),
                                "body": item.get("body", {}).get("content", ""),
                                "received_at": item["receivedDateTime"],
                                "metadata": {"thread_id": item.get("conversationId")},
                            }
                        )
                    url = payload.get("@odata.nextLink")
                    if not url:
                        return messages
                raise ValueError("Outlook sync exceeded 2000 messages")
        raise ValueError("Unsupported email provider")

    def _imap(self, config, password):
        host, username = config.get("host"), config.get("username")
        if not host or not username:
            raise ValueError("IMAP host and username are required")
        if any(char in host for char in "/\\\r\n"):
            raise ValueError("Invalid IMAP host")
        with imaplib.IMAP4_SSL(host, int(config.get("port", 993)), timeout=30) as client:
            client.login(username, password)
            if client.select(config.get("folder", "INBOX"), readonly=True)[0] != "OK":
                raise ValueError("Could not open IMAP folder")
            validity = client.response("UIDVALIDITY")[1]
            generation = validity[0].decode() if validity and validity[0] else "unknown"
            last_uid = int(config.get("last_uid", 0)) if config.get("uidvalidity") == generation else 0
            result, data = client.uid("search", None, "UID", f"{last_uid + 1}:*")
            if result != "OK":
                raise ValueError("IMAP search failed")
            uids = [x for x in data[0].split() if int(x) > last_uid][:200]
            messages = []
            for uid in uids:
                result, payload = client.uid("fetch", uid, "(BODY.PEEK[])")
                if result != "OK":
                    raise ValueError("IMAP fetch failed")
                raw = next((part[1] for part in payload if isinstance(part, tuple)), None)
                if raw:
                    message = parse_message(raw)
                    message["external_id"] = (
                        f"{host}:{username}:{config.get('folder', 'INBOX')}:{generation}:{uid.decode()}"
                    )
                    messages.append(message)
            return messages

    async def sync(self):
        if self.lock.locked():
            return {"status": "already_running", "imported": 0, "errors": []}
        async with self.lock:
            results, errors = [], []
            integrations = self.db.query(
                "SELECT provider FROM integrations WHERE provider IN ('gmail','outlook','imap') AND status='connected'"
            )
            for row in integrations:
                provider = row["provider"]
                started = utcnow()
                try:
                    messages = await self.fetch(provider)
                    for message in messages:
                        results.append(ingest_message(self.db, message, provider))
                    if provider == "imap" and messages:
                        config = self.config(provider)
                        generation, uid = messages[-1]["external_id"].rsplit(":", 2)[-2:]
                        config.update(uidvalidity=generation, last_uid=int(uid))
                        self.db.execute(
                            "UPDATE integrations SET config=? WHERE provider=?", (json.dumps(config), provider)
                        )
                    self.db.execute(
                        "UPDATE integrations SET last_sync=?,last_error=NULL WHERE provider=?", (started, provider)
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: sync failed; check account authorization and server settings"
                    errors.append({"provider": provider, "error": error})
                    self.db.execute("UPDATE integrations SET last_error=? WHERE provider=?", (error, provider))
            return {"imported": sum(not r.get("duplicate") for r in results), "results": results, "errors": errors}
