"""Synthetic candidates of the answer-engine studies (never real data): the same person with a different legal status, a
different language level or a different education, so that the SAME question can be asked of each and its answer compared
with what that candidate's facts say (the differential tests of ``test_engine_legal.py`` and its siblings)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extension"))
from harness import seed_candidate  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402
from jobagent.intelligence.store import entity, ingest_fact, policy  # noqa: E402

JOB_UK = {"country": "United Kingdom", "company": "Acme", "location": "London, UK"}
JOB_ES = {"country": "Spain", "company": "Acme", "location": "Madrid, Spain"}
FIELD = {"type": "text", "tag": "input", "role": "textbox"}
RADIO = {"type": "radio", "role": "radio", "options": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]}
CHECKBOX = {"type": "checkbox", "role": "checkbox", "tag": "input"}


def add(db, kind, name, **fields):
    entity_id = entity(db, kind, name)
    for key, value in fields.items():
        value_type = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else \
            "list" if isinstance(value, list) else "text"
        ingest_fact(db, entity_id, key, value, value_type=value_type, source="Synthetic verified test record",
                    evidence={"excerpt": str(value)}, verified=True)
    return entity_id


def base(db):
    add(db, "person", "Test Candidate", full_name="Test Candidate", first_name="Test", last_name="Candidate",
        email="test@example.test", phone="+441234567890", city="London", location="London, United Kingdom",
        country="United Kingdom", country_code="GB", nationality="British", employment_contract_active=False,
        earliest_full_time_start_date="2027-09-28")
    policy(db, "salary_expected", {"target": 60000, "currency": "GBP"}, {"country": "United Kingdom"})
    return db


def employed(db, days=14):
    """A UK citizen in a job with a notice of ``days`` days (the notice-period questions of ``test_engine_plain.py``)."""
    add(db, "person", "Test Candidate", full_name="Test Candidate", first_name="Test", last_name="Candidate",
        email="test@example.test", phone="+441234567890", city="London", location="London, United Kingdom",
        country="United Kingdom", country_code="GB", nationality="British", employment_contract_active=True,
        current_employment_notice_days=days, earliest_full_time_start_date="2027-09-28")
    policy(db, "salary_expected", {"target": 60000, "currency": "GBP"}, {"country": "United Kingdom"})
    add(db, "immigration", "UK Citizen", country="United Kingdom", route="Citizen", current_right_to_work=True,
        permanent_unrestricted=True, unrestricted_right_to_work=True, full_time_right_to_work=True, future_right_to_work=True,
        sponsorship_now_or_future=False, sponsorship_current=False, sponsorship_future=False)
    return db


def citizen(db):
    """A UK citizen: a permanent, unrestricted right to work, no sponsorship ever."""
    base(db)
    add(db, "immigration", "UK Citizen", country="United Kingdom", route="Citizen", current_right_to_work=True,
        permanent_unrestricted=True, unrestricted_right_to_work=True, full_time_right_to_work=True, future_right_to_work=True,
        sponsorship_now_or_future=False, sponsorship_current=False, sponsorship_future=False)
    add(db, "education", "North University", institution="North University", degree="BSc Computer Science",
        level="Bachelor's degree", rank=1, subject="Computer Science", status="completed", start_date="2023-09-01",
        end_date="2026-06-30")
    add(db, "language", "English", proficiency="Native", native=True)
    return db


def needs_sponsorship(db):
    """No right to work now, and a need of sponsorship now and in the future."""
    base(db)
    add(db, "immigration", "UK Visa", country="United Kingdom", route="Other", current_right_to_work=False,
        sponsorship_now_or_future=True, sponsorship_current=True, sponsorship_future=True)
    return db


def student(db):
    """The synthetic candidate of the browser-assistant tests: a Spanish national on a UK Student route."""
    return seed_candidate(db)


def engine_for(tmp_path, kind="student", as_of="2026-10-14"):
    db = Database(Path(tmp_path) / f"{kind}.sqlite3")
    {"student": student, "citizen": citizen, "needs_sponsorship": needs_sponsorship, "employed": employed}[kind](db)
    return CandidateEngine(db, as_of=as_of)


def ask(engine, question, job=JOB_UK, **field):
    return engine.answer_question(question, {**FIELD, **field}, dict(job))


def speaker(db, english=("Fluent", None), spanish=("Native", True), french=None):
    """The candidate with the given languages: (level, native flag or None) for each."""
    base(db)
    for name, spec in (("English", english), ("Spanish", spanish), ("French", french)):
        if spec:
            level, flag = spec
            add(db, "language", name, proficiency=level, **({} if flag is None else {"native": flag}))
    return db


def speaker_engine(tmp_path, tag, **languages):
    db = Database(Path(tmp_path) / f"{tag}.sqlite3")
    speaker(db, **languages)
    return CandidateEngine(db, as_of="2026-10-14")


def graduate(db):
    """Both degrees COMPLETED: a BSc in Computer Science (2026) and an MSc in Artificial Intelligence (2025)."""
    base(db)
    add(db, "education", "North University", institution="North University", degree="BSc Computer Science",
        level="Bachelor's degree", rank=1, subject="Computer Science", status="completed", start_date="2023-09-01",
        end_date="2026-06-30", classification="First Class Honours")
    add(db, "education", "Westbridge University", institution="Westbridge University", degree="MSc Artificial Intelligence",
        level="Master's degree", rank=2, subject="Artificial Intelligence", status="completed", start_date="2024-09-01",
        end_date="2025-09-30", classification="Merit")
    return db


def months(db):
    """The student, with every date at month precision."""
    base(db)
    add(db, "education", "North University", institution="North University", degree="BSc Computer Science",
        level="Bachelor's degree", rank=1, subject="Computer Science", status="completed", start_date="2023-09",
        end_date="2026-06")
    add(db, "education", "South University", institution="South University", degree="MSc Artificial Intelligence",
        level="Master's degree", rank=2, subject="Artificial Intelligence", status="studying", start_date="2026-09",
        end_date="2027-09")
    return db


def education_engine(tmp_path, kind, as_of="2026-10-14"):
    db = Database(Path(tmp_path) / f"edu-{kind}.sqlite3")
    {"student": student, "citizen": citizen, "graduate": graduate, "months": months}[kind](db)
    return CandidateEngine(db, as_of=as_of)
