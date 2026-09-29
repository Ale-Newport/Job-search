"""Actionable, persistent notification deduplication without candidate content."""

from datetime import datetime, timedelta, timezone

from .core import get_settings
from .db import now


def collect_notifications(db, at=None):
    settings = get_settings(db)
    if not settings.get("notifications_enabled", True):
        return []
    at = at or datetime.now(timezone.utc)
    stamp = at.isoformat()
    upcoming = (at + timedelta(hours=48)).isoformat()
    recent = (at - timedelta(days=7)).isoformat()
    categories = [
        (
            "tasks",
            db.query("SELECT id FROM human_tasks WHERE lower(status)='open'"),
            "items need your review in Meridian",
        ),
        (
            "deadline",
            db.query(
                "SELECT e.id || ':' || e.deadline AS id FROM email_messages e JOIN applications a ON a.id=e.application_id "
                "WHERE e.deadline>=? AND e.deadline<=? AND a.status NOT IN ('REJECTED','WITHDRAWN','OFFER')",
                (stamp, upcoming),
            ),
            "application deadlines fall within the next 48 hours",
        ),
        (
            "match",
            db.query(
                "SELECT id FROM jobs WHERE created_at>=? AND match_score>=? "
                "AND json_extract(match_details,'$.eligible')=1 AND status NOT IN ('IGNORED','REJECTED','WITHDRAWN')",
                (recent, float(settings.get("min_match", 85))),
            ),
            "new strong matches are ready to inspect",
        ),
        (
            "update",
            db.query(
                "SELECT id FROM application_events WHERE created_at>=? AND origin='email' "
                "AND status IN ('OFFER','ASSESSMENT','TECHNICAL_TEST','INTERVIEW','FINAL_INTERVIEW','RECRUITER_SCREEN','REJECTED')",
                (recent,),
            ),
            "recruitment updates are waiting in your timeline",
        ),
    ]
    messages = []
    with db.transaction() as conn:
        for category, records, description in categories:
            count = 0
            for record in records:
                count += conn.execute(
                    "INSERT OR IGNORE INTO notification_receipts(key,created_at) VALUES(?,?)",
                    (f"{category}:{record['id']}", now()),
                ).rowcount
            if count:
                messages.append(f"{count} {description}.")
    return messages
