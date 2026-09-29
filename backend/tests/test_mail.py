import json
from pathlib import Path

import pytest

from jobagent.db import Database
from jobagent.mail import classify_message, ingest_message, link_candidate, parse_message


@pytest.mark.parametrize(
    "subject,body,category,status",
    [
        ("Application received", "Thank you for applying to Arc Systems.", "APPLICATION_CONFIRMATION", "CONFIRMED"),
        ("Your application", "We have decided not to progress your application.", "REJECTION", "REJECTED"),
        ("Assessment", "Please complete your online assessment within 5 days.", "ASSESSMENT_INVITATION", "ASSESSMENT"),
        ("Technical test", "Complete the HackerRank coding challenge within 5 days.", "CODING_TEST", "TECHNICAL_TEST"),
        ("Interview", "We would like to invite you to an interview.", "INTERVIEW_REQUEST", "INTERVIEW"),
        ("Offer", "We are pleased to offer you the role.", "OFFER", "OFFER"),
        (
            "Opportunity",
            "I am a recruiter and would like to discuss the role.",
            "RECRUITER_MESSAGE",
            "RECRUITER_SCREEN",
        ),
    ],
)
def test_classification(subject, body, category, status):
    result = classify_message(subject, body, "2026-09-29T12:00:00+00:00")
    assert result["category"] == category
    assert result["status"] == status
    if "5 days" in body:
        assert result["deadline"].startswith("2026-10-04")


def add_application(db, title="Graduate Software Engineer", suffix="one"):
    from jobagent.discovery import ingest_job

    job, _ = ingest_job(
        db,
        {
            "title": title,
            "company": "Arc Systems",
            "location": "London",
            "url": f"https://careers.example.org/{suffix}",
            "description": "Python software engineering",
        },
    )
    db.execute(
        "INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,'review','APPLIED','2026-09-29','2026-09-29')",
        (suffix, job["id"]),
    )
    return suffix


def test_conservative_linking_and_no_regression(tmp_path):
    db = Database(tmp_path / "test.db")
    app_id = add_application(db)
    message = {
        "external_id": "m1",
        "subject": "Arc Systems Graduate Software Engineer",
        "sender": "recruitment@arc.example",
        "body": "We would like to invite you to an interview.",
        "received_at": "2026-09-29T12:00:00+00:00",
    }
    assert link_candidate(db, message)[0] == app_id
    result = ingest_message(db, message)
    assert result["application_id"] == app_id
    assert db.one("SELECT status FROM applications WHERE id=?", (app_id,))["status"] == "INTERVIEW"
    assert ingest_message(db, message)["duplicate"] is True
    message.update(external_id="m2", body="Thank you for applying.")
    ingest_message(db, message)
    assert db.one("SELECT status FROM applications WHERE id=?", (app_id,))["status"] == "INTERVIEW"
    assert len(db.query("SELECT * FROM application_events")) == 2


def test_same_company_ambiguity_creates_human_task(tmp_path):
    db = Database(tmp_path / "test.db")
    add_application(db)
    add_application(db, "Graduate Data Engineer", "two")
    message = {
        "external_id": "ambiguous",
        "subject": "Arc Systems application",
        "sender": "recruitment@arc.example",
        "body": "Thank you for applying.",
        "received_at": "2026-09-29T12:00:00+00:00",
    }
    assert link_candidate(db, message)[0] is None
    result = ingest_message(db, message)
    assert result["application_id"] is None
    assert db.one("SELECT kind FROM human_tasks")["kind"] == "EMAIL_LINK"


def test_eml_unicode_html_and_date():
    raw = b"Message-ID: <test@example.test>\r\nFrom: Recruitment <jobs@example.test>\r\nDate: Tue, 29 Sep 2026 12:00:00 +0100\r\nSubject: =?utf-8?b?QW50b25pbyDigJQgaW50ZXJ2aWV3?=\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Interview <strong>confirmed</strong></p>"
    message = parse_message(raw)
    assert "Antonio" in message["subject"]
    assert "<strong>" not in message["body"]
    assert message["received_at"].endswith("+01:00")


def test_anonymized_style_fixtures():
    for path in (Path(__file__).parent / "fixtures/email").glob("*.eml"):
        message = parse_message(path.read_bytes())
        result = classify_message(message["subject"], message["body"], message["received_at"])
        assert result["category"] != "OTHER"
        assert "password" not in json.dumps(message).lower()
