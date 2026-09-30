from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from jobagent.db import Database
from jobagent.feeds import parse_github_tables, parse_trackr
from jobagent.main import create_app
from jobagent.mail import ingest_message, parse_message
from jobagent.tracking import daily_data, refresh_daily


class MemorySecrets:
    def get(self, name):
        return None

    def set(self, name, value):
        pass

    def delete(self, name):
        pass


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, token="test", secret_store=MemorySecrets(), start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        yield client


def job(api, title="Graduate Software Engineer", company="Example Labs", suffix="one"):
    response = api.post(
        "/api/jobs", json={"title": title, "company": company, "url": "https://example.test/jobs/" + suffix}
    )
    assert response.status_code == 201
    return response.json()


def message(subject, body, external_id="mail-one"):
    return {
        "subject": subject,
        "body": body,
        "sender": "careers@example.test",
        "external_id": external_id,
        "received_at": "2026-09-28T10:00:00+00:00",
    }


def test_manual_record_without_cv_is_idempotent_and_preserves_progress(api):
    j = job(api)
    assert api.get("/api/tracker").json()["items"][0]["submitted"] is False
    body = {"applied_at": "2026-09-27T12:00:00Z", "notes": "Referred by contact"}
    result = api.post(f"/api/jobs/{j['id']}/record-application", json=body)
    assert result.status_code == 200, result.text
    a = result.json()
    detail = api.get(f"/api/applications/{a['id']}").json()
    assert detail["status"] == "APPLIED" and detail["mode"] == "manual"
    assert detail["runs"] == [] and detail["documents"] == [] and detail["tasks"] == []
    api.patch(f"/api/applications/{a['id']}", json={"status": "INTERVIEW"})
    again = api.post(f"/api/jobs/{j['id']}/record-application", json=body).json()
    assert again["already_recorded"] and again["status"] == "INTERVIEW"
    rows = api.get("/api/tracker?scope=applied").json()
    assert rows["total"] == 1 and rows["items"][0]["applied_at"].startswith("2026-09-27")
    assert api.get("/api/daily").json()["applied"] == 1
    assert api.get("/api/daily").json()["items"] == []


def test_future_date_rejected_and_missing_job(api):
    j = job(api)
    assert (
        api.post(
            f"/api/jobs/{j['id']}/record-application",
            json={"applied_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        ).status_code
        == 422
    )
    assert api.post("/api/jobs/absent/record-application", json={}).status_code == 404


def test_confirmation_creates_application_only_with_unique_complete_title(api):
    j = job(api)
    db = api.app.state.db
    ingest_message(
        db,
        message(
            "Application received — Example Labs",
            "Thank you for applying to Graduate Software Engineer at Example Labs.",
        ),
    )
    rows = api.get("/api/tracker?scope=applied").json()["items"]
    assert len(rows) == 1 and rows[0]["id"] == j["id"] and rows[0]["tracking_status"] == "CONFIRMED"
    assert rows[0]["last_email"]["subject"].startswith("Application received")
    ingest_message(
        db,
        message(
            "Coding test — Example Labs",
            "Graduate Software Engineer: complete your coding challenge within 3 days.",
            "mail-two",
        ),
    )
    row = api.get("/api/tracker?scope=applied").json()["items"][0]
    assert row["tracking_status"] == "TECHNICAL_TEST" and row["deadline"].startswith("2026-10-01")
    # Duplicate mail cannot create another timeline event.
    before = len(db.query("SELECT * FROM application_events"))
    ingest_message(
        db,
        message(
            "Coding test — Example Labs",
            "Graduate Software Engineer: complete your coding challenge within 3 days.",
            "mail-two",
        ),
    )
    assert len(db.query("SELECT * FROM application_events")) == before
    api.patch(f"/api/applications/{row['application_id']}", json={"status": "INTERVIEW"})
    assert api.get("/api/tracker?scope=applied").json()["items"][0]["deadline"] is None


def test_ambiguous_email_needs_review_and_late_manual_record_reconciles(api):
    one = job(api)
    job(api, suffix="two")
    db = api.app.state.db
    ingest_message(
        db,
        message(
            "Application received — Example Labs",
            "Thank you for applying to Graduate Software Engineer at Example Labs.",
        ),
    )
    assert api.get("/api/tracker?scope=applied").json()["total"] == 0
    assert db.one("SELECT count(*) AS n FROM human_tasks WHERE kind='EMAIL_LINK' AND status='OPEN'")["n"] == 1
    api.post(f"/api/jobs/{one['id']}/record-application", json={})
    assert api.get("/api/tracker?scope=applied").json()["items"][0]["tracking_status"] == "CONFIRMED"
    assert db.one("SELECT count(*) AS n FROM human_tasks WHERE kind='EMAIL_LINK' AND status='OPEN'")["n"] == 0


def test_github_markdown_and_html_tables_keep_direct_links_and_continuations():
    content = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| **Example** | Graduate Engineer | London | [Apply](https://jobs.example.test/one) | 2d |
| ↳ | AI Engineer | Bristol | <a href="https://jobs.example.test/two"><img alt="Apply"></a> | 1d |
| Closed | Engineer | London | 🔒 | 2d |
<table><tr><th>Company</th><th>Role</th><th>Apply</th></tr><tr><td>Other</td><td>Data Engineer</td><td><a href="https://jobs.example.test/three">Apply</a></td></tr></table>"""
    jobs = parse_github_tables(content, "https://github.com/example/jobs")
    assert [j["company"] for j in jobs] == ["Example", "Example", "Other"]
    assert jobs[1]["url"] == "https://jobs.example.test/two"
    assert jobs[0]["posted_at"]


def test_trackr_skips_unopened_and_preserves_company_and_deadlines():
    jobs = parse_trackr(
        {
            "programmes": [
                {
                    "id": "one",
                    "name": "Software Engineer",
                    "company": {"name": "Example", "description": "Research tools", "sponsorsVisa": "Yes"},
                    "url": "https://example.test/one",
                    "closingDate": "2020-01-01",
                    "locations": ["Europe|UK|London"],
                    "coverLetter": "Optional",
                },
                {"id": "two", "name": "Unopened", "company": {"name": "Example"}, "url": None},
            ]
        },
        "https://app.the-trackr.com/uk-tech/graduate-programmes",
    )
    assert len(jobs) == 1 and jobs[0]["metadata"]["closed"] is True
    assert jobs[0]["location"] == "London, UK, Europe"
    assert jobs[0]["metadata"]["company_description"] == "Research tools"


def test_html_email_alert_imports_job_without_creating_application(api):
    api.post(
        "/api/sources",
        json={
            "name": "LinkedIn alerts",
            "kind": "linkedin",
            "url": "https://www.linkedin.com/jobs/",
            "config": {"delivery": "email"},
        },
    )
    raw = EmailMessage()
    raw["From"] = "jobalerts-noreply@linkedin.com"
    raw["Subject"] = "Your job alert"
    raw["Message-ID"] = "<alert-one>"
    raw.set_content("Your new jobs")
    raw.add_alternative(
        '<table><tr><td><a href="https://www.linkedin.com/jobs/view/123456789?trackingId=example">Graduate Software Engineer</a><p class="company">Example Labs</p></td></tr></table>',
        subtype="html",
    )
    parsed = parse_message(raw.as_bytes())
    assert parsed["metadata"]["job_alert_links"][0]["company"] == "Example Labs"
    ingest_message(api.app.state.db, parsed)
    rows = api.get("/api/tracker").json()["items"]
    assert len(rows) == 1 and rows[0]["url"] == "https://www.linkedin.com/jobs/view/123456789"
    assert rows[0]["submitted"] is False
    ingest_message(api.app.state.db, parsed)
    assert api.get("/api/tracker").json()["total"] == 1


@pytest.mark.asyncio
async def test_daily_refresh_persists_date_and_errors_without_running_application_rules(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.db")

    async def fake_sources(*args, **kwargs):
        return {"new_jobs": 2, "items": [{"error": None}, {"error": "Source unavailable"}]}

    monkeypatch.setattr("jobagent.discovery.run_sources", fake_sources)
    await refresh_daily(db, tmp_path)
    data = daily_data(db)
    assert data["last_refresh"]["day"] == data["date"]
    assert data["last_refresh"]["new_jobs"] == 2 and data["last_refresh"]["errors"] == 1
    assert not db.query("SELECT * FROM applications")


def test_plain_indeed_alerts_keep_employer_location_and_remove_personal_tracking(api):
    from jobagent.job_alerts import reprocess_alerts

    api.post(
        "/api/sources",
        json={
            "name": "Indeed alerts",
            "kind": "indeed",
            "url": "https://www.indeed.com/",
            "config": {"delivery": "email"},
        },
    )
    msg = message(
        "Graduate AI Engineer at Example Labs. 2 more AI jobs in london",
        """Indeed Job Alert
3 new AI jobs in london

Graduate AI Engineer
Example Labs - London
We may use AI in the recruitment process.
Just posted
https://uk.indeed.com/rc/clk/dl?jk=abc123456&token=private-example&alid=personal

Data Engineer
Other Labs - Bristol
https://uk.indeed.com/rc/clk/dl?jk=def123456&token=private-example
""",
    )
    msg["sender"] = "Indeed <donotreply@jobalert.indeed.com>"
    result = ingest_message(api.app.state.db, msg)
    assert result["classification"]["category"] == "OTHER"
    rows = api.get("/api/tracker").json()["items"]
    assert {(r["company"], r["location"]) for r in rows} == {("Example Labs", "London"), ("Other Labs", "Bristol")}
    assert all("token=" not in r["url"] and "alid=" not in r["url"] for r in rows)
    assert api.get("/api/tracker?scope=applied").json()["total"] == 0
    assert reprocess_alerts(api.app.state.db) == 2
    assert reprocess_alerts(api.app.state.db) == 0
    assert api.get("/api/tracker").json()["total"] == 2
    assert api.app.state.db.one("SELECT jobs_found FROM sources WHERE kind='indeed'")["jobs_found"] == 2


def test_daily_excludes_passed_deadline_and_prefers_graduate_roles(api):
    from jobagent.db import dumps

    api.patch("/api/settings", json={"daily_min_match": 0})
    j = job(api)
    senior = job(api, title="Senior Software Engineer", suffix="senior")
    rows = api.get("/api/daily").json()["items"]
    assert rows[0]["id"] == j["id"]
    api.app.state.db.execute("UPDATE jobs SET metadata=? WHERE id=?", (dumps({"closed": True}), j["id"]))
    assert [r["id"] for r in api.get("/api/daily").json()["items"]] == [senior["id"]]


@pytest.mark.parametrize('location,europe,london', [
    ('London', True, True), ('London, United Kingdom', True, True), ('Londres, Reino Unido', True, True),
    ('London, Ontario, Canada', False, False), ('London, ON', False, False),
    ('New York, NY', False, False), ('Paris, Texas, USA', False, False),
    ('Berlin, Germany', True, False), ('Remote - Europe', True, False), ('EMEA', False, False),
    ('Remote worldwide', False, False), ('', False, False), ('Remote US', False, False),
    ('Madrid; London, UK', True, True), ('San Francisco; Dublin, Ireland', True, False),
])
def test_europe_location_evidence(location, europe, london):
    from jobagent.geography import location_evidence
    assert location_evidence(location) == {'europe': europe, 'london': london}


def test_daily_and_opportunities_europe_only_london_first(api):
    api.patch('/api/settings', json={'suggested_region': 'europe', 'preferred_city': 'London', 'daily_min_match': 0})
    rows = []
    for i, place in enumerate(['Berlin, Germany', 'New York, NY', 'Remote', 'London, UK', 'London, Ontario, Canada']):
        rows.append(api.post('/api/jobs', json={'title': 'Graduate Software Engineer', 'company': str(i),
            'url': f'https://example.test/{i}', 'location': place}).json())
    for endpoint in ['/api/daily', '/api/jobs']:
        data = api.get(endpoint).json()
        assert [item['id'] for item in data['items']] == [rows[3]['id'], rows[0]['id']]
        assert data['total'] == 2
    assert api.get('/api/tracker').json()['total'] == 5
