import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobagent.core import router
from jobagent.db import Database


@pytest.fixture
def analytics_client(tmp_path):
    db = Database(tmp_path / "analytics.sqlite")
    app = FastAPI()
    app.state.db = db
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        yield db, client


def application(
    db, identifier, source="Alpha", role="Engineer", company="One", created="2025-01-05", status="PREPARING"
):
    stamp = created + "T12:00:00+00:00"
    db.execute(
        "INSERT INTO jobs(id,title,company,url,canonical_url,identity_key,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            role,
            company,
            f"https://example.test/{identifier}",
            f"https://example.test/{identifier}",
            identifier,
            source,
            stamp,
            stamp,
        ),
    )
    db.execute(
        "INSERT INTO applications(id,job_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
        (identifier, identifier, status, stamp, stamp),
    )


def event(db, application_id, status, date, origin="manual"):
    identifier = f"{application_id}-{status}-{date}"
    db.execute(
        "INSERT INTO application_events VALUES(?,?,?,?,?,?)",
        (identifier, application_id, status, "Fixture milestone", origin, date),
    )


def score(db, application_id, value, date):
    db.execute(
        "INSERT INTO job_matches VALUES(?,?,?,?,?,?)",
        (f"{application_id}-{date}", application_id, None, value, "{}", date),
    )


def test_analytics_uses_submission_history_and_retains_interviews_after_rejection(analytics_client):
    db, client = analytics_client
    application(db, "a1", status="REJECTED")
    application(db, "a2", company="Two", created="2025-01-06", status="OFFER")
    application(db, "a3", source="Beta", role="Designer", created="2025-01-07", status="APPLIED")
    application(db, "a4", source="Beta", role="Designer", created="2025-01-08")
    application(db, "a5", source="Beta", role="PM", created="2025-01-13", status="REJECTED")
    application(db, "a6", role="Designer", created="2025-01-14", status="INTERVIEW")
    for app_id, statuses in {
        "a1": [
            ("APPLIED", "06"),
            ("CONFIRMED", "06"),
            ("RECRUITER_SCREEN", "08"),
            ("INTERVIEW", "10"),
            ("REJECTED", "15"),
        ],
        "a2": [("APPLIED", "07"), ("ASSESSMENT", "10"), ("OFFER", "20")],
        "a3": [("APPLIED", "08")],
        "a5": [("REJECTED", "16")],
        "a6": [("RECRUITER_SCREEN", "08"), ("APPLIED", "10")],
    }.items():
        for status, day in statuses:
            event(db, app_id, status, f"2025-01-{day}T00:00:00Z")
    # A late email import must retain the actual response date, not its import date.
    event(db, "a6", "INTERVIEW", "2025-01-30T00:00:00Z", "email")
    event(db, "a6", "INTERVIEW", "2025-01-31T00:00:00Z", "email")
    db.execute(
        "INSERT INTO email_messages(id,provider,external_id,received_at,classification,confidence,application_id) VALUES(?,?,?,?,?,?,?)",
        ("email", "fixture", "message", "2025-01-12T01:00:00+01:00", "INTERVIEW_REQUEST", 0.95, "a6"),
    )
    # Earlier outreach of the same category cannot conceal a later actual response.
    db.execute(
        "INSERT INTO email_messages(id,provider,external_id,received_at,classification,confidence,application_id) VALUES(?,?,?,?,?,?,?)",
        ("earlier-email", "fixture", "earlier-message", "2025-01-08T00:00:00Z", "INTERVIEW_REQUEST", 0.95, "a6"),
    )
    for app_id, value in [("a1", 85), ("a2", 65), ("a4", 40), ("a5", 50), ("a6", 90)]:
        score(db, app_id, value, "2025-01-01T00:00:00Z")
    score(db, "a1", 10, "2025-01-31T00:00:00Z")  # Rescoring later must not rewrite the sent cohort.
    db.execute(
        "INSERT INTO documents(id,name,kind,created_at,updated_at) VALUES('cv','Engineering CV','cv','2025-01-01','2025-01-01')"
    )
    for version in [1, 2]:
        db.execute(
            "INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,approved,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"cv{version}",
                "cv",
                version,
                f"/fixture/cv{version}.pdf",
                f"cv{version}.pdf",
                "application/pdf",
                f"hash{version}",
                1,
                "2025-01-01",
            ),
        )
    for app_id, version in [("a1", "cv1"), ("a2", "cv1"), ("a3", "cv2")]:
        db.execute("INSERT INTO application_documents VALUES(?,?,?)", (app_id, version, "cv"))

    response = client.get("/api/analytics")
    assert response.status_code == 200
    result = response.json()
    assert result["applications"] == 6
    assert result["submitted"] == 4  # Neither a rejected-only nor a prepared application invents submission.
    assert result["responded"] == 3
    assert result["response_rate"] == 75
    assert result["rejection_rate"] == 25
    assert result["assessment_rate"] == 25
    assert result["interview_rate"] == 50
    assert result["offer_rate"] == 25
    assert result["small_sample"] is True
    assert result["time_to_response"] == {
        "mean_days": 2.33,
        "median_days": 2.0,
        "min_days": 2.0,
        "max_days": 3.0,
        "sample_size": 3,
        "small_sample": True,
        "pending_applications": 1,
    }
    assert result["data_quality"]["responses_without_submission"] == 1
    assert result["data_quality"]["responses_before_submission"] == 1
    assert result["data_quality"]["missing_match_score"] == 1
    sources = {row["source"]: row for row in result["source_performance"]}
    assert sources["Alpha"]["response_rate"] == 100
    assert sources["Alpha"]["interview_rate"] == 66.7
    assert sources["Beta"]["submitted"] == 1
    assert sources["Beta"]["response_rate"] == 0
    assert result["applications_by_source"] == result["source_performance"]
    roles = {row["role"]: row for row in result["applications_by_role"]}
    assert roles["Engineer"]["responded"] == 2
    assert roles["PM"]["response_rate"] is None
    cv = {row["document_version_id"]: row for row in result["cv_version_performance"]}
    assert cv["cv1"]["submitted"] == 2
    assert cv["cv1"]["response_rate"] == 100
    assert cv["cv2"]["response_rate"] == 0
    assert cv[None]["applications"] == 3
    buckets = {row["bucket"]: row for row in result["match_score_performance"]}
    assert buckets["80–100"]["submitted"] == 2
    assert buckets["80–100"]["interviews"] == 2
    assert "0–39" not in buckets
    assert buckets["Unknown"]["submitted"] == 1
    assert result["applications_by_week"] == [
        {"week_start": "2024-12-30", "count": 1},
        {"week_start": "2025-01-06", "count": 3},
        {"week_start": "2025-01-13", "count": 2},
    ]
    assert [item["count"] for item in result["funnel"]] == [6, 4, 3, 1, 2, 1]
    assert result["funnel"][-1]["denominator"] == 4
    assert "small samples" in result["note"]
    dashboard = client.get("/api/dashboard").json()["stats"]
    assert dashboard["applied"] == 4
    assert dashboard["interviews"] == 2
    assert dashboard["offers"] == 1


def test_empty_analytics_and_unknown_timestamps_never_claim_zero_response_time(analytics_client):
    db, client = analytics_client
    empty = client.get("/api/analytics").json()
    assert empty["response_rate"] is None
    assert empty["time_to_response"]["mean_days"] is None
    application(db, "broken", status="REJECTED")
    event(db, "broken", "APPLIED", "not-a-date")
    event(db, "broken", "REJECTED", "2025-01-10")
    result = client.get("/api/analytics").json()
    assert result["submitted"] == 1
    assert result["responded"] == 1  # Milestone known; duration unknown.
    assert result["response_rate"] == 100
    assert result["time_to_response"]["sample_size"] == 0
    assert result["time_to_response"]["mean_days"] is None
    assert result["data_quality"]["invalid_timestamps"] == 1
    assert result["data_quality"]["submitted_without_valid_timestamp"] == 1


def test_dashboard_strong_matches_respects_eligibility_ignore_and_configured_threshold(analytics_client):
    db, client = analytics_client
    for identifier, value, details, status in [
        ("qualified", 92, '{"eligible":true}', "DISCOVERED"),
        ("below_threshold", 85, '{"eligible":true}', "DISCOVERED"),
        ("excluded", 98, '{"eligible":false}', "DISCOVERED"),
        ("unassessed", 99, "{}", "DISCOVERED"),
        ("ignored", 97, '{"eligible":true}', "IGNORED"),
    ]:
        application(db, identifier)
        db.execute(
            "UPDATE jobs SET match_score=?,match_details=?,status=? WHERE id=?", (value, details, status, identifier)
        )
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('min_match','90')")
    assert client.get("/api/dashboard").json()["stats"]["strong_matches"] == 1
    db.execute("UPDATE settings SET value='80' WHERE key='min_match'")
    assert client.get("/api/dashboard").json()["stats"]["strong_matches"] == 2
