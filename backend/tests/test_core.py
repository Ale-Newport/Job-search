import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from jobagent.core import prepare_application, router
from jobagent.db import Database, now, uid
from jobagent.discovery import (
    ats_identity,
    canonical_url,
    ingest_job,
    parse_ashby,
    parse_greenhouse,
    parse_jsonld,
    parse_lever,
    run_sources,
    validate_public_url,
)
from jobagent.documents import document_path, extract_text, generate_document, import_document
from jobagent.matching import extract_skills, match_job


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "database" / "meridian.sqlite")


@pytest.fixture
def client(db, tmp_path):
    app = FastAPI()
    app.state.db, app.state.data_dir = db, tmp_path
    app.include_router(router, prefix="/api")
    return TestClient(app)


def job(db, **overrides):
    return ingest_job(
        db,
        {
            "title": "Graduate Software Engineer",
            "company": "Example",
            "url": "https://jobs.example.test/1",
            "description": "Build Python services with SQL and AWS",
            "location": "London",
            **overrides,
        },
    )[0]


def fact(db, value="Python", status="verified", category="skill", key="python"):
    identifier, stamp = uid(), now()
    db.execute(
        "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            db.one("SELECT id FROM candidates")["id"],
            category,
            key,
            value,
            "test",
            status,
            0,
            "",
            stamp,
            stamp,
        ),
    )
    return db.one("SELECT * FROM facts WHERE id=?", (identifier,))


def test_database_migrates_reopens_foreign_keys_and_rolls_back(db):
    assert db.one("PRAGMA journal_mode")["journal_mode"] == "wal"
    assert db.one("SELECT version_num FROM alembic_version")["version_num"] == "0004_section_consent"
    with pytest.raises(Exception):
        with db.transaction() as conn:
            conn.execute("INSERT INTO settings VALUES('example','true')")
            conn.execute("INSERT INTO relationships VALUES('x','missing','also-missing','uses','','today')")
    assert db.one("SELECT * FROM settings WHERE key='example'") is None
    Database(db.path)
    assert db.one("SELECT COUNT(*) AS n FROM candidates")["n"] == 1


def test_normalize_and_ats_identity_dedupe(db):
    assert (
        canonical_url("https://EXAMPLE.com/jobs/42/?utm_source=abc&jobId=42#apply")
        == "https://example.com/jobs/42?jobId=42"
    )
    first = job(db, url="https://boards.greenhouse.io/acme/jobs/123?gh_src=a")
    second = job(db, url="https://job-boards.greenhouse.io/acme/jobs/123?utm_medium=x")
    assert first["id"] == second["id"]
    assert db.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM job_sources")["n"] == 2
    assert (
        ats_identity("https://jobs.lever.co/acme/abcd/apply")[1] == ats_identity("https://jobs.lever.co/acme/abcd")[1]
    )


def test_same_title_different_posting_not_merged_without_description(db):
    one = job(db, url="https://jobs.example.test/first", description="")
    two = job(db, url="https://jobs.example.test/second", description="")
    assert one["id"] != two["id"]


def test_identical_cross_post_merged_and_snapshot_preserved(db):
    description = "We are seeking a graduate engineer to build backend services with Python and SQL. " * 8
    first = job(db, description=description)
    second = job(db, url="https://other.example.test/position", description=description)
    assert first["id"] == second["id"]
    assert db.one("SELECT COUNT(*) AS n FROM job_snapshots")["n"] == 2


def test_matching_excludes_unverified_and_explains_unknowns(db):
    profile = {"id": "test", "config": {"roles": ["Software Engineer"], "locations": ["London"], "min_match": 60}}
    candidate = fact(db, "Python SQL AWS", "unverified")
    posting = job(db)
    unverified = match_job(posting, [candidate], profile)
    candidate["verification_status"] = "verified"
    verified = match_job(posting, [candidate], profile)
    assert verified["score"] > unverified["score"]
    assert verified["matched_skills"] == ["aws", "python", "sql"]
    assert "salary" in verified["unknowns"] and "work_authorization" in verified["unknowns"]
    assert candidate["id"] in verified["fact_ids"]
    assert "java" not in extract_skills("JavaScript developer")


def test_explicit_exclusion_never_silently_changes_facts(db):
    facts = [fact(db)]
    posting = job(
        db,
        title="Senior Software Engineer",
        salary_min=30000,
        salary_max=40000,
        currency="GBP",
        metadata={"salary_unit": "YEAR"},
    )
    match = match_job(
        posting,
        facts,
        {"id": "x", "config": {"negative_keywords": ["Senior"], "minimum_salary": 50000, "salary_currency": "GBP"}},
    )
    assert match["eligible"] is False
    assert len(match["exclusions"]) == 2
    assert db.one("SELECT COUNT(*) AS n FROM facts")["n"] == 1


@pytest.mark.parametrize(
    "unit,currency,preference_currency",
    [("hour", "USD", "USD"), ("YEAR", "USD", "GBP"), (None, "GBP", "GBP"), ("YEAR", "GBP", None)],
)
def test_incompatible_salary_is_unknown_and_never_excluded(unit, currency, preference_currency):
    posting = {
        "title": "Software Engineer",
        "company": "Example",
        "description": "Python",
        "salary_min": 80,
        "salary_max": 100,
        "currency": currency,
        "metadata": {"salary_unit": unit},
    }
    result = match_job(
        posting, [], {"id": "profile", "config": {"minimum_salary": 50000, "salary_currency": preference_currency}}
    )
    assert result["eligible"] is True
    assert result["salary_comparison"]["status"] == "unknown"
    assert result["components"]["salary"]["value"] == 0.5
    assert "salary_comparison" in result["unknowns"]
    assert not result["exclusions"]


def test_annual_same_currency_salary_is_compared_and_profile_validates_currency(client):
    posting = {
        "title": "Engineer",
        "company": "Example",
        "description": "",
        "salary_max": 45000,
        "currency": "GBP",
        "metadata": {"salary_unit": "per-year"},
    }
    result = match_job(posting, [], {"id": "p", "config": {"minimum_salary": 50000, "salary_currency": "GBP"}})
    assert result["salary_comparison"]["status"] == "compared"
    assert "Advertised salary is below the minimum" in result["exclusions"]
    assert (
        client.post(
            "/api/search-profiles",
            json={"name": "Annual GBP", "config": {"minimum_salary": 50000, "salary_currency": "GBP"}},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/search-profiles", json={"name": "No currency", "config": {"salary_currency": None}}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/search-profiles", json={"name": "Invalid", "config": {"salary_currency": "pounds"}}
        ).status_code
        == 422
    )


def test_document_import_unverified_and_docx_extraction(db, tmp_path):
    text = b"Candidate Name\ncandidate@example.test\nEducation\nBSc Computer Science\nExperience\nBuilt a Python data platform"
    imported = import_document(db, tmp_path, text, "../cv.txt")
    assert imported["document"]["name"] == "cv.txt"
    assert all(f["verification_status"] == "unverified" for f in imported["proposed_facts"])
    path, _ = document_path(db, tmp_path, imported["version"]["id"])
    assert path.is_relative_to(tmp_path / "documents")
    doc, output = Document(), io.BytesIO()
    doc.add_paragraph("Actual verified sentence")
    doc.save(output)
    assert "Actual verified sentence" in extract_text(output.getvalue(), "cv.docx")[0]


def test_document_generation_provenance_pdf_diff_and_immutability(db, tmp_path):
    posting = job(db)
    unsupported = fact(db, "Invented achievement", status="unverified")
    with pytest.raises(ValueError, match="verified"):
        generate_document(db, tmp_path, posting["id"], "cv", [unsupported["id"]], True)
    evidence = fact(db, "Built a Python project", category="project")
    draft = generate_document(db, tmp_path, posting["id"], "cv", [evidence["id"]], False)
    assert not db.one("SELECT id FROM documents")
    assert "Built a Python project" in draft["text"]
    assert "Invented achievement" not in draft["text"]
    saved = generate_document(db, tmp_path, posting["id"], "cv", [evidence["id"]], True)
    version = saved["version"]
    assert version["fact_ids"] == [evidence["id"]]
    path, _ = document_path(db, tmp_path, version["id"])
    assert "Built a Python project" in PdfReader(path).pages[0].extract_text()
    another = generate_document(db, tmp_path, posting["id"], "cv", [evidence["id"]], True)
    assert another["version"]["version"] == 2
    assert another["version"]["path"] != str(path)
    assert another["diff"] == ""
    path.write_bytes(b"altered")
    with pytest.raises(ValueError, match="immutable"):
        document_path(db, tmp_path, version["id"])


def test_concurrent_prepare_creates_one_application(db, tmp_path):
    posting = job(db)
    with ThreadPoolExecutor(max_workers=6) as pool:
        applications = list(
            pool.map(lambda _: prepare_application(db, posting["id"], "review", data_dir=tmp_path), range(10))
        )
    assert len({a["id"] for a in applications}) == 1
    assert db.one("SELECT COUNT(*) AS n FROM applications")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM human_tasks")["n"] == 1


def test_email_event_does_not_regress_interview(db, tmp_path):
    application = prepare_application(db, job(db)["id"], data_dir=tmp_path)
    db.event(application["id"], "INTERVIEW", "Interview", "email")
    db.event(application["id"], "CONFIRMED", "Late confirmation", "email")
    assert db.one("SELECT status FROM applications")["status"] == "INTERVIEW"
    assert db.query("SELECT status FROM application_events")[-1]["status"] == "CONFIRMED"
    db.event(application["id"], "REJECTED", "Decision", "email")
    db.event(application["id"], "ASSESSMENT", "Delayed assessment", "email")
    assert db.one("SELECT status FROM applications")["status"] == "REJECTED"


def test_company_watchlist_creates_live_source(client):
    response = client.post(
        "/api/companies", json={"name": "Example", "careers_url": "https://example.test/careers", "priority": 80}
    )
    assert response.status_code == 201, response.text
    sources = client.get("/api/sources").json()["items"]
    assert len(sources) == 1
    assert sources[0]["config"]["company_id"] == response.json()["id"]
    client.patch("/api/companies/" + response.json()["id"], json={"careers_url": "https://example.test/new-careers"})
    assert client.get("/api/sources").json()["items"][0]["url"].endswith("/new-careers")
    assert client.delete("/api/companies/" + response.json()["id"]).status_code == 200
    assert client.get("/api/sources").json()["total"] == 0


def test_fts_search_filters_and_updates(client, db):
    posting = job(db)
    assert client.get("/api/jobs?q=Python").json()["total"] == 1
    assert client.get('/api/jobs?q=" OR *').status_code == 200
    client.patch("/api/jobs/" + posting["id"], json={"description": "Rust services"})
    assert client.get("/api/jobs?q=Python").json()["total"] == 0
    assert client.get("/api/search?q=Example").json()["items"][0]["type"] == "job"


def test_fact_lock_and_explicit_verification(client):
    response = client.post(
        "/api/facts", json={"category": "personal", "key": "email", "value": "candidate@example.test", "locked": True}
    )
    identifier = response.json()["id"]
    assert client.patch("/api/facts/" + identifier, json={"value": "other@example.test"}).status_code == 409
    assert client.delete("/api/facts/" + identifier).status_code == 409
    assert (
        client.patch("/api/facts/" + identifier, json={"locked": False, "verification_status": "verified"}).status_code
        == 200
    )
    assert client.delete("/api/facts/" + identifier).status_code == 200


def test_human_answer_saved_with_provenance(client, db, tmp_path):
    application = prepare_application(db, job(db)["id"], data_dir=tmp_path)
    identifier = uid()
    db.execute(
        "INSERT INTO human_tasks(id,application_id,kind,question,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (identifier, application["id"], "UNKNOWN_QUESTION", "What is your start date?", "open", now(), now()),
    )
    response = client.post(f"/api/human-tasks/{identifier}/resolve", json={"answer": "2026-11-01", "save": True})
    assert response.status_code == 200
    assert db.one("SELECT answer FROM saved_answers")["answer"] == "2026-11-01"
    saved = db.one("SELECT * FROM facts WHERE category='saved_answer'")
    assert saved["verification_status"] == "verified"
    assert saved["source"].startswith("manual_answer:")
    assert "human_task:" + identifier in saved["notes"]
    assert client.post(f"/api/human-tasks/{identifier}/resolve", json={"answer": "different"}).status_code == 409


def test_settings_rules_and_validation(client):
    invalid = client.patch("/api/settings", json={"rules": [{"name": "Bad", "action": "execute_javascript"}]})
    assert invalid.status_code == 422
    assert client.patch("/api/settings", json={"max_applications_day": -3}).status_code == 422
    assert (
        client.patch(
            "/api/settings",
            json={"rules": [{"name": "Good", "conditions": {"min_match": 85}, "action": "prepare_application"}]},
        ).status_code
        == 200
    )
    assert client.get("/api/settings").json()["automation_paused"] is True
    assert client.patch("/api/settings", json={"custom": {"nested": [{"api_key": "never-store"}]}}).status_code == 422


def test_selected_cv_replacement_preserves_history_and_blocks_after_submit(client, db, tmp_path):
    posting = job(db)
    first = import_document(db, tmp_path, b"First CV text", "first.txt")["version"]
    second = import_document(db, tmp_path, b"Second CV text", "second.txt")["version"]
    application = prepare_application(db, posting["id"], "review", first["id"], tmp_path)
    prepare_application(db, posting["id"], "review", second["id"], tmp_path)
    selected = db.query(
        "SELECT document_version_id FROM application_documents WHERE application_id=?", (application["id"],)
    )
    assert selected == [{"document_version_id": second["id"]}]
    assert db.one("SELECT id FROM application_events WHERE message LIKE ?", ("%" + first["id"] + "%",))
    db.event(application["id"], "CONFIRMED", "Manual evidence")
    response = client.post(
        f"/api/jobs/{posting['id']}/prepare", json={"mode": "review", "document_version_id": first["id"]}
    )
    assert response.status_code == 409


def test_event_participates_in_callers_transaction(db, tmp_path):
    application = prepare_application(db, job(db)["id"], data_dir=tmp_path)
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            db.event(application["id"], "INTERVIEW", "Atomic test", "email", conn=conn)
            raise RuntimeError("Rollback")
    assert db.one("SELECT status FROM applications")["status"] == "NEEDS_REVIEW"
    assert not db.one("SELECT id FROM application_events WHERE message='Atomic test'")


def test_fact_edits_revoke_reuse_and_preserve_historical_evidence(client, db, tmp_path):
    from jobagent.core import save_answer
    from jobagent.db import historical_facts

    evidence = fact(db, "Original Python experience", category="experience")
    posting = job(db)
    application = prepare_application(db, posting["id"], data_dir=tmp_path)
    saved = generate_document(db, tmp_path, posting["id"], "cv", [evidence["id"]], True)
    save_answer(db, "Describe your experience", evidence["value"], [evidence["id"]], True, application["id"])
    assert (
        client.patch("/api/facts/" + evidence["id"], json={"value": "Corrected Python experience"}).status_code == 200
    )
    assert db.one("SELECT verified FROM saved_answers")["verified"] == 0
    assert db.one("SELECT verified FROM application_answers")["verified"] == 0
    assert db.one("SELECT approved FROM document_versions WHERE id=?", (saved["version"]["id"],))["approved"] == 0
    detail = client.get("/api/applications/" + application["id"]).json()
    assert detail["answers"][0]["evidence"][0]["value"] == "Original Python experience"
    assert (
        historical_facts(db, [evidence["id"]], saved["version"]["created_at"])[0]["value"]
        == "Original Python experience"
    )
    assert client.delete("/api/facts/" + evidence["id"]).status_code == 200
    assert (
        historical_facts(db, [evidence["id"]], saved["version"]["created_at"])[0]["value"]
        == "Original Python experience"
    )


def test_submitted_answer_is_immutable_when_fact_changes(client, db, tmp_path):
    from jobagent.core import save_answer

    evidence = fact(db)
    application = prepare_application(db, job(db)["id"], data_dir=tmp_path)
    save_answer(db, "Skill", "Python", [evidence["id"]], True, application["id"])
    db.event(application["id"], "CONFIRMED", "Confirmed evidence")
    client.patch("/api/facts/" + evidence["id"], json={"value": "Rust"})
    answer = client.get("/api/applications/" + application["id"]).json()["answers"][0]
    assert answer["answer"] == "Python" and answer["verified"] is True
    assert answer["evidence"][0]["value"] == "Python"
    assert db.one("SELECT verified FROM saved_answers")["verified"] == 0


def test_unicode_pdf_preserves_candidate_name():
    from jobagent.documents import render_pdf

    rendered = render_pdf("María García\nEducation\nMSc Artificial Intelligence — King's College London")
    text = PdfReader(io.BytesIO(rendered)).pages[0].extract_text()
    assert "María García" in text


def test_settings_pause_reaches_browser_gate(client):
    class Automation:
        paused = None

        def pause(self):
            self.paused = True

        async def resume(self):
            self.paused = False

    automation = Automation()
    client.app.state.automation = automation
    assert client.patch("/api/settings", json={"automation_paused": True}).status_code == 200
    assert automation.paused is True
    assert client.patch("/api/settings", json={"automation_paused": False}).status_code == 200
    assert automation.paused is False


def test_manual_answers_are_stable_reusable_facts_and_revoke_with_sources(db):
    from jobagent.automation.answers import resolve_answer
    from jobagent.core import save_answer

    evidence = fact(db, "Available from November", category="availability")
    answer = save_answer(db, "When can you start?", "November", [evidence["id"]], True)
    manual = db.one("SELECT * FROM facts WHERE id=?", (answer["candidate_fact_id"],))
    assert manual["category"] == "saved_answer"
    assert manual["source"] == "manual_answer:" + answer["saved_answer_id"]
    resolved = resolve_answer("When can you start?", [manual])
    assert resolved["answer"] == "November" and resolved["verified"] is True
    updated = save_answer(db, "When can you start?", "December", [], True)
    assert updated["candidate_fact_id"] == manual["id"]
    assert db.one("SELECT COUNT(*) AS n FROM facts WHERE category='saved_answer'")["n"] == 1


def test_jsonld_and_ats_fixtures():
    ld = {
        "@graph": [
            {
                "@type": "JobPosting",
                "title": "Graduate Engineer",
                "hiringOrganization": {"name": "Example"},
                "description": "<p>Python</p>",
                "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "UK"}},
                "baseSalary": {"currency": "GBP", "value": {"minValue": 40000, "maxValue": 50000}},
            }
        ]
    }
    parsed = parse_jsonld(
        '<script type="application/ld+json">' + json.dumps(ld) + "</script>", "https://example.test/job"
    )
    assert parsed[0]["location"] == "London, UK"
    assert parsed[0]["remote"] is None
    assert parsed[0]["salary_min"] == 40000
    gh = parse_greenhouse(
        {
            "jobs": [
                {
                    "id": 1,
                    "title": "Engineer",
                    "absolute_url": "https://boards.greenhouse.io/ex/jobs/1",
                    "content": "Python",
                    "location": {"name": "London"},
                }
            ]
        },
        "Example",
    )
    assert gh[0]["external_id"] == "1"
    lever = parse_lever(
        [
            {
                "id": "id",
                "text": "Engineer",
                "hostedUrl": "https://jobs.lever.co/ex/id",
                "descriptionPlain": "Python",
                "lists": [{"text": "Skills", "content": "SQL"}],
            }
        ],
        "Example",
    )
    assert "SQL" in lever[0]["description"]
    ashby = parse_ashby(
        {"jobs": [{"title": "Engineer", "jobUrl": "https://jobs.ashbyhq.com/ex/id", "isListed": False}]}, "Example"
    )
    assert ashby == []


@pytest.mark.asyncio
async def test_private_network_discovery_rejected():
    for url in (
        "http://127.0.0.1/private",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
    ):
        with pytest.raises(ValueError):
            await validate_public_url(url)


@pytest.mark.asyncio
async def test_source_run_health_dedupe_and_poll_interval(db, monkeypatch):
    import jobagent.discovery as discovery

    identifier = uid()
    db.execute(
        "INSERT INTO sources(id,name,kind,url,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (identifier, "Public fixture", "json", "https://example.test/jobs.json", now(), now()),
    )

    async def source(_):
        return [
            {"title": "Engineer", "company": "Example", "url": "https://example.test/jobs/2", "description": "Python"}
        ]

    monkeypatch.setattr(discovery, "discover", source)
    first = await run_sources(db)
    second = await run_sources(db)
    assert first["new_jobs"] == 1 and second["new_jobs"] == 0
    assert (await run_sources(db, due_only=True))["total"] == 0

    async def failed(_):
        raise ValueError("Network unavailable")

    monkeypatch.setattr(discovery, "discover", failed)
    assert (await run_sources(db))["items"][0]["error"]
    assert "Network unavailable" in db.one("SELECT last_error FROM sources")["last_error"]


@pytest.mark.asyncio
async def test_source_run_single_flight_and_cancel_cleanup(db, monkeypatch):
    import asyncio
    import jobagent.discovery as discovery

    identifier = uid()
    db.execute(
        "INSERT INTO sources(id,name,kind,url,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (identifier, "Fixture", "json", "https://example.test/feed", now(), now()),
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_source(_):
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(discovery, "discover", slow_source)
    task = asyncio.create_task(run_sources(db))
    await started.wait()
    assert discovery.discovery_busy(db)
    assert (await run_sources(db))["status"] == "already_running"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not discovery.discovery_busy(db)
