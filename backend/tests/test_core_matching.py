import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobagent.core import SearchConfig, router
from jobagent.db import Database
from jobagent.discovery import ingest_job, normalize_job, parse_ashby, parse_jsonld, parse_lever
from jobagent.matching import contract_types, match_job


def posting(**overrides):
    return {
        "title": "Software Engineer",
        "company": "Example",
        "url": "https://jobs.example.test/advanced",
        "description": "Build Python services",
        **overrides,
    }


def fact(identifier, category, key, value, verified=True):
    return {
        "id": identifier,
        "category": category,
        "key": key,
        "value": value,
        "verification_status": "verified" if verified else "unverified",
        "locked": False,
    }


def test_profile_validation_normalizes_weights_and_preserves_optional_unknowns():
    config = SearchConfig(
        weights={"skills": 3, "education": 1},
        requires_sponsorship=None,
        max_posting_age_days=None,
        salary_currency=None,
    )
    assert config.weights == {"skills": 75, "education": 25}
    assert SearchConfig().weights == {}
    for invalid in ({"skills": -1}, {"skills": 0}, {"skills": float("nan")}, {"skills": float("inf")}, {"invented": 1}):
        with pytest.raises(ValidationError):
            SearchConfig(weights=invalid)
    for invalid in (0, -1, 3651):
        with pytest.raises(ValidationError):
            SearchConfig(max_posting_age_days=invalid)
    with pytest.raises(ValidationError):
        SearchConfig(work_arrangements=["flexible"])


def test_custom_weights_change_score_using_only_selected_evidence():
    facts = [fact("python", "skill", "Python", "Python")]
    result = match_job(posting(), facts, {"config": {"weights": {"skills": 3, "education": 1}}})
    assert result["score"] == 87.5  # Verified skills 75 + unknown education 12.5.
    assert sum(part["weight"] for part in result["components"].values()) == pytest.approx(100)
    assert result["components"]["role"]["weight"] == 0
    assert result["components"]["education"]["known"] is False


def test_filters_exclude_only_explicit_mismatch_and_report_unknowns():
    config = {
        "industries": ["Finance"],
        "work_arrangements": ["remote"],
        "contract_types": ["full_time"],
        "max_posting_age_days": 10,
        "requires_sponsorship": True,
        "locations": ["London"],
    }
    unknown = match_job(posting(remote=False), [], {"config": config})
    assert not unknown["exclusions"]  # remote=False does not distinguish hybrid from on-site.
    assert all(item["status"] == "unknown" for item in unknown["filters"].values())
    assert {"industries", "work_arrangements", "contract_types", "posting_age", "sponsorship", "location"} <= set(
        unknown["unknowns"]
    )
    explicit = posting(
        metadata={
            "industries": ["Healthcare"],
            "work_arrangement": "hybrid",
            "contract_types": ["part_time"],
            "sponsorship_available": False,
        },
        posted_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
    )
    excluded = match_job(explicit, [], {"config": config})
    assert excluded["eligible"] is False
    assert len(excluded["exclusions"]) == 5
    assert all(item["status"] == "excluded" for item in excluded["filters"].values())
    assert "work_authorization" in excluded["unknowns"]


def test_sponsorship_available_never_asserts_legal_work_authorization():
    result = match_job(
        posting(metadata={"sponsorship_available": True}), [], {"config": {"requires_sponsorship": True}}
    )
    assert result["filters"]["sponsorship"]["status"] == "pass"
    assert "work_authorization" in result["unknowns"]
    unrestricted = match_job(
        posting(metadata={"sponsorship_available": True}), [], {"config": {"requires_sponsorship": False}}
    )
    assert unrestricted["filters"]["sponsorship"]["status"] == "not_requested"


def test_contract_duration_does_not_imply_hours_or_internship():
    assert contract_types("Permanent") == []
    assert contract_types("Regular") == []
    assert contract_types("Apprenticeship") == []


@pytest.mark.parametrize("date", [None, "not a date", "2099-01-01T00:00:00Z"])
def test_unusable_posting_date_is_unknown(date):
    result = match_job(posting(posted_at=date), [], {"config": {"max_posting_age_days": 3}})
    assert result["filters"]["posting_age"]["status"] == "unknown"
    assert not result["exclusions"]


def test_education_and_experience_require_verified_comparable_facts():
    job = posting(
        metadata={"education_requirements": "Bachelor's degree in Computer Science", "experience_years_required": 4}
    )
    facts = [
        fact("degree", "education", "degree", "BSc Computer Science"),
        fact("years", "experience", "professional_experience_years", "2 years"),
        fact("project", "project", "years", "10 years building projects"),
    ]
    result = match_job(job, facts)
    assert result["components"]["education"]["value"] == 1
    assert result["components"]["experience"]["value"] == 0.5
    assert result["components"]["experience"]["known"] is True
    assert set(result["fact_ids"]) == {"degree", "years"}
    unverified = [dict(item, verification_status="unverified") for item in facts]
    result = match_job(job, unverified)
    assert result["components"]["education"]["known"] is False
    assert result["components"]["experience"]["known"] is False
    assert result["fact_ids"] == []


@pytest.mark.parametrize(
    "value",
    [
        "Currently studying MSc Computer Science",
        "MSc Computer Science expected 2099",
        "No master's degree",
        "BSc Physics",
    ],
)
def test_incomplete_or_different_education_is_unknown(value):
    result = match_job(
        posting(metadata={"education_requirements": "Master's in Computer Science"}),
        [fact("degree", "education", "degree", value)],
    )
    assert result["components"]["education"]["known"] is False
    assert result["components"]["education"]["value"] == 0.5


def test_conflicting_experience_claims_are_unknown():
    result = match_job(
        posting(metadata={"experience_years_required": 2}),
        [
            fact("one", "experience", "years_of_experience", "1"),
            fact("two", "experience", "total_professional_years", "3"),
        ],
    )
    assert result["components"]["experience"]["known"] is False
    assert "Conflicting" in result["components"]["experience"]["reason"]


def test_public_formats_preserve_explicit_matching_metadata():
    data = {
        "@type": "JobPosting",
        "title": "Engineer",
        "hiringOrganization": {"name": "Example"},
        "industry": "Finance",
        "employmentType": "FULL_TIME",
        "jobLocationType": "TELECOMMUTE",
        "educationRequirements": {"credentialCategory": "bachelor degree"},
        "experienceRequirements": {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 120},
    }
    jobs = parse_jsonld(
        '<script type="application/ld+json">' + json.dumps(data) + "</script>", "https://example.test/job"
    )
    job = normalize_job(jobs[0])
    assert job["metadata"]["industries"] == ["Finance"]
    assert job["metadata"]["contract_types"] == ["full_time"]
    assert job["metadata"]["work_arrangement"] == "remote"
    assert job["metadata"]["experience_years_required"] == 10
    lever = parse_lever(
        [
            {
                "id": "one",
                "text": "Engineer",
                "hostedUrl": "https://example.test/lever",
                "workplaceType": "hybrid",
                "categories": {"commitment": "Part-time"},
            }
        ],
        "Example",
    )
    ashby = parse_ashby(
        {
            "jobs": [
                {
                    "title": "Engineer",
                    "jobUrl": "https://example.test/ashby",
                    "workplaceType": "OnSite",
                    "employmentType": "Intern",
                }
            ]
        },
        "Example",
    )
    assert normalize_job(lever[0])["metadata"]["work_arrangement"] == "hybrid"
    assert normalize_job(lever[0])["metadata"]["contract_types"] == ["part_time"]
    assert normalize_job(ashby[0])["metadata"]["work_arrangement"] == "onsite"
    assert normalize_job(ashby[0])["metadata"]["contract_types"] == ["internship"]


def test_api_profile_and_job_metadata_persist_and_rescore_on_refresh(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    app = FastAPI()
    app.state.db, app.state.data_dir = db, tmp_path
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        profile = client.post(
            "/api/search-profiles",
            json={
                "name": "Remote finance",
                "config": {
                    "industries": ["Finance"],
                    "work_arrangements": ["remote"],
                    "contract_types": ["full_time"],
                    "max_posting_age_days": None,
                    "requires_sponsorship": True,
                    "weights": {"skills": 3, "education": 1},
                    "min_match": 0,
                },
            },
        )
        assert profile.status_code == 201, profile.text
        assert profile.json()["config"]["weights"] == {"skills": 75, "education": 25}
        response = client.post(
            "/api/jobs",
            json=posting(
                industries=["Finance"],
                work_arrangement="hybrid",
                contract_types=["full_time"],
                sponsorship_available=True,
                salary_max=50000,
                currency="GBP",
                salary_unit="YEAR",
            ),
        )
        assert response.status_code == 201, response.text
        assert response.json()["match_details"]["eligible"] is False
        refreshed, _ = ingest_job(
            db,
            posting(
                industries=["Finance"],
                work_arrangement="remote",
                contract_types=["full_time"],
                sponsorship_available=True,
                salary_max=30,
                currency="USD",
                salary_unit="hour",
            ),
        )
        assert refreshed["match_details"]["eligible"] is True
        assert refreshed["metadata"]["work_arrangement"] == "remote"
        assert refreshed["currency"] == "USD"
        assert refreshed["metadata"]["salary_unit"] == "hour"
        detail = client.get("/api/jobs/" + refreshed["id"]).json()
        assert detail["match_details"]["filters"]["industries"]["status"] == "pass"
        refreshed, _ = ingest_job(db, posting(remote=False))
        assert refreshed["metadata"]["work_arrangement"] is None
        assert refreshed["match_details"]["filters"]["work_arrangements"]["status"] == "unknown"
