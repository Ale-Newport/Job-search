"""Synthetic candidate for the extension end-to-end suite. Every value is invented (example.test, drama phone range).

Two layers, because that is how Meridian itself stores a profile:

* the typed knowledge layer (entities, facts, policies) is what the answer engine reads. There is no HTTP endpoint
  that creates typed entities (the app builds them from imported CVs and profile edits), so ``seed_typed`` writes the
  same bundle format the application's own ``jobagent.intelligence.ingestion.import_bundle`` accepts, in the backend
  process, before the app starts serving;
* the flat ``facts`` table is seeded through the real HTTP API (``POST /api/facts``) exactly as the Facts page does.

``EXPECTED_VALUES`` is the single place that scenarios and the engine oracle read candidate values from.
"""

from __future__ import annotations

SOURCE = "E2E synthetic candidate (explicit user confirmation)"

NAME = "Alex Morgan"
FIRST = "Alex"
LAST = "Morgan"
EMAIL = "alex.morgan@example.test"
PHONE = "+44 7700 900123"
PHONE_NATIONAL = "7700 900123"
LINKEDIN = "https://www.linkedin.com/in/alex-morgan-example"
PORTFOLIO = "https://alexmorgan.example.test"
GITHUB = "https://github.com/alex-morgan-example"
LOCATION = "London, United Kingdom"
CV_FILENAME = "Alex-Morgan-CV.pdf"
PROJECT_STORY = "I built a route planner for warehouse robots using Python and graph search algorithms."
EXPERIENCE_STORY = "I developed backend services for a logistics analytics product during a summer internship."
SALARY = 55000
START_DATE = "2026-11-02"

EXPECTED_VALUES = {
    "first_name": FIRST,
    "last_name": LAST,
    "full_name": NAME,
    "email": EMAIL,
    "phone": PHONE,
    "linkedin": LINKEDIN,
    "portfolio": PORTFOLIO,
    "github": GITHUB,
    "salary": SALARY,
    "start_date": START_DATE,
}


def bundle() -> dict:
    """Typed knowledge bundle in the format of ``jobagent.intelligence.ingestion.import_bundle``."""
    professional = {"sensitivity": "professional"}
    return {
        "source": SOURCE,
        "entities": [
            {"key": "person", "kind": "person", "name": NAME, "facts": {
                "full_name": NAME, "first_name": FIRST, "last_name": LAST, "email": EMAIL, "phone": PHONE,
                "city": "London", "country": "United Kingdom", "country_code": "GB", "location": LOCATION,
                "linkedin": LINKEDIN, "portfolio": PORTFOLIO, "github": GITHUB,
                "earliest_full_time_start_date": START_DATE,
                "professional_interests": {"value": "Robotics software, path planning and backend engineering",
                                           **professional},
            }},
            {"key": "bsc", "kind": "education", "name": "Northfield University", "facts": {
                "institution": "Northfield University", "degree": "BSc Computer Science",
                "level": "Bachelor's degree", "rank": 1, "subject": "Computer Science", "status": "completed",
                "start_date": "2021-09-01", "end_date": "2024-06-30", "classification": "First Class Honours"}},
            {"key": "msc", "kind": "education", "name": "Westbridge University", "facts": {
                "institution": "Westbridge University", "degree": "MSc Artificial Intelligence",
                "level": "Master's degree", "rank": 2, "subject": "Artificial Intelligence", "status": "completed",
                "start_date": "2024-09-01", "end_date": "2025-09-30", "classification": "Merit"}},
            {"key": "english", "kind": "language", "name": "English", "facts": {"proficiency": "Fluent"}},
            {"key": "spanish", "kind": "language", "name": "Spanish", "facts": {"proficiency": "Conversational"}},
            {"key": "right_to_work", "kind": "immigration", "name": "UK Right to Work", "facts": {
                "country": "United Kingdom", "route": "Settled status", "current_right_to_work": True,
                "sponsorship_now_or_future": False}},
            {"key": "intern", "kind": "experience", "name": "Brightwave Labs", "facts": {
                "role": "Software Engineering Intern", "start_date": "2023-06-01", "end_date": "2023-09-30",
                "story": EXPERIENCE_STORY}},
            {"key": "planner", "kind": "project", "name": "Warehouse Route Planner", "facts": {
                "start_date": "2024-01-01", "end_date": "2024-05-31", "technologies": ["Python", "Graph search"],
                "story": PROJECT_STORY,
                "contribution": "I implemented the planner and the warehouse simulator."}, "skills": ["Python"]},
        ],
        "policies": [
            {"subject": "optional_demographics", "value": "decline_or_blank"},
            {"subject": "personal_disclosures", "value": "leave_blank"},
            {"subject": "salary_expected", "value": {"target": SALARY, "currency": "GBP"},
             "scope": {"country": "United Kingdom"}},
        ],
    }


def seed_typed(db) -> dict:
    """Write the typed layer into an open ``jobagent.db.Database`` (call before the app serves requests)."""
    from jobagent.intelligence.ingestion import import_bundle

    return import_bundle(db, bundle(), trusted_user_input=True)


# Flat facts created through POST /api/facts (the Facts page). category/key/value as the app's own forms send them.
FLAT_FACTS = [
    ("contact", "full_name", NAME), ("contact", "first_name", FIRST), ("contact", "last_name", LAST),
    ("contact", "email", EMAIL), ("contact", "phone", PHONE), ("contact", "linkedin", LINKEDIN),
    ("contact", "portfolio", PORTFOLIO), ("contact", "github", GITHUB), ("contact", "location", LOCATION),
    ("skill", "python", "Python"), ("skill", "typescript", "TypeScript"),
]
