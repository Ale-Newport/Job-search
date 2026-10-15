"""SEC-09, the reverse order: a job the assistant made from a page never absorbs a posting Meridian discovers itself.

A hostile page saved first becomes a job with ``source='extension'`` whose address is the hostile one. When discovery later
finds the REAL posting with the same company, title, location and text, ``ingest_job`` used to take it for a cross-post of
that job: it answered ``duplicate`` for the hostile job, demoted the real address to ``job_duplicates`` and left the hostile one
as the job's ``application_url``. What a page SAYS about itself is never evidence that it is another posting; only an address
(canonical URL or ATS identity) is."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import make_app  # noqa: E402
from jobagent.discovery import ingest_job  # noqa: E402
from jobagent.extension.session import ensure_extension_application  # noqa: E402

TEXT = "Build and run reliable robot fleet services in Python, review code and work with product and hardware teams. " * 4
TEXT = TEXT.strip()
REAL = {"url": "https://boards.greenhouse.io/acmerobotics/jobs/4001", "application_url": "https://boards.greenhouse.io/acmerobotics/jobs/4001",
        "title": "Software Engineer", "company": "Acme Robotics", "location": "London", "description": TEXT, "source": "greenhouse"}
HOSTILE = {**REAL, "url": "https://acme-careers-portal.example/apply/4001", "application_url": "https://acme-careers-portal.example/apply/4001",
           "source": "extension"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
def db(tmp_path):
    return make_app(tmp_path).state.db


def jobs(db):
    return db.query("SELECT id,url,application_url,source FROM jobs ORDER BY created_at")


def test_a_hostile_page_saved_first_does_not_absorb_the_real_posting_found_later(db):
    hostile, _ = ingest_job(db, HOSTILE)  # a page that copied the real posting word for word, saved by the assistant
    real, duplicate = ingest_job(db, REAL)
    assert duplicate is False and real["id"] != hostile["id"]
    assert real["url"] == REAL["url"] and real["application_url"] == REAL["application_url"]
    assert {j["url"] for j in jobs(db)} == {HOSTILE["url"], REAL["url"]}
    assert db.query("SELECT * FROM job_duplicates") == []  # the real address was demoted to nobody's alias
    untouched = db.one("SELECT url,application_url,source FROM jobs WHERE id=?", (hostile["id"],))
    assert untouched == {"url": HOSTILE["url"], "application_url": HOSTILE["application_url"], "source": "extension"}


def test_the_same_through_the_assistants_own_path(db):
    """The assistant stores page text with a provenance note; whatever it stores, discovery still finds its own job."""
    page = dict(url=HOSTILE["url"], application_url=HOSTILE["application_url"], title=REAL["title"], company=REAL["company"],
                location=REAL["location"], description=TEXT, ats="generic")
    made = ensure_extension_application(db, **page)
    real, duplicate = ingest_job(db, REAL)
    assert duplicate is False and real["id"] != made["job_id"] and real["url"] == REAL["url"]
    assert db.query("SELECT * FROM job_duplicates") == []


def test_a_posting_the_assistant_adds_later_is_not_merged_into_a_discovered_one_by_its_text(db):
    real, _ = ingest_job(db, REAL)
    later, duplicate = ingest_job(db, HOSTILE)
    assert duplicate is False and later["id"] != real["id"]
    assert db.query("SELECT * FROM job_duplicates") == []
    assert db.one("SELECT url FROM jobs WHERE id=?", (real["id"],))["url"] == REAL["url"]


def test_the_same_address_is_still_the_same_job(db):
    """The user opened the real posting itself: discovery finding that address later completes the SAME job."""
    mine, _ = ingest_job(db, {**REAL, "source": "extension", "description": ""})
    found, duplicate = ingest_job(db, REAL)
    assert duplicate is True and found["id"] == mine["id"] and len(jobs(db)) == 1
    assert found["description"] == TEXT  # the discovered text completes the job the page started


def test_the_same_ats_posting_found_under_another_address_is_still_the_same_job(db):
    mine, _ = ingest_job(db, {**REAL, "url": "https://boards.greenhouse.io/acmerobotics/jobs/4001?gh_src=social", "source": "extension",
                              "description": ""})
    found, duplicate = ingest_job(db, REAL)
    assert duplicate is True and found["id"] == mine["id"]


def test_two_discovered_cross_posts_are_still_merged(db):
    """The rule is about the assistant's rows only: Meridian's own cross-post detection is exactly what it was."""
    first, _ = ingest_job(db, REAL)
    second, duplicate = ingest_job(db, {**REAL, "url": "https://jobs.example-board.example/acme/software-engineer-london",
                                        "application_url": "https://jobs.example-board.example/acme/software-engineer-london",
                                        "source": "feed"})
    assert duplicate is True and second["id"] == first["id"]
    assert db.query("SELECT duplicate_url FROM job_duplicates") == [
        {"duplicate_url": "https://jobs.example-board.example/acme/software-engineer-london"}]
