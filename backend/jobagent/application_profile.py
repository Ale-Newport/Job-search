"""Editable application knowledge, with explicit provenance and question-level memory."""

from __future__ import annotations

import re
from .db import now, uid
from .automation.answers import canonical_key, normalize, resolve_answer


def field(key, label, group, *, options=None, sensitive=False, aliases=(), scope=None, multiline=False, kind="text"):
    return dict(
        key=key,
        label=label,
        group=group,
        options=options or [],
        sensitive=sensitive,
        aliases=list(aliases),
        scope=scope,
        multiline=multiline,
        type=kind,
    )


YES_NO = ["Yes", "No"]
FIELDS = [
    *[
        field(k, label, "Contact")
        for k, label in [
            ("full_name", "Full name"),
            ("first_name", "First name"),
            ("last_name", "Last name"),
            ("preferred_name", "Preferred name"),
            ("email", "Email"),
            ("phone", "Phone"),
            ("location", "Current location"),
            ("address", "Street address"),
            ("postcode", "Postcode"),
            ("linkedin", "LinkedIn"),
            ("github", "GitHub"),
            ("portfolio", "Portfolio"),
        ]
    ],
    field(
        "date_of_birth",
        "Date of birth",
        "Personal details",
        sensitive=True,
        kind="date",
        aliases=["date of birth", "birth date"],
    ),
    field("age", "Age", "Personal details", sensitive=True, kind="number", aliases=["age", "your age"]),
    field(
        "nationality",
        "Nationality / citizenship",
        "Personal details",
        sensitive=True,
        aliases=["nationality", "citizenship"],
    ),
    field("gender", "Gender", "Personal details", sensitive=True, aliases=["gender", "what is your gender"]),
    field("sex", "Sex", "Personal details", sensitive=True, aliases=["sex", "sex assigned at birth"]),
    field(
        "pronouns",
        "Pronouns",
        "Personal details",
        sensitive=True,
        options=["she/her/hers", "he/him/his", "they/them/theirs", "other", "Prefer not to say"],
        aliases=["pronouns", "what are your pronouns", "what are your preferred pronouns"],
    ),
    field(
        "notice_period",
        "Notice period",
        "Availability & compensation",
        options=[
            "Immediately available",
            "1 week",
            "2 weeks",
            "1 month",
            "6 weeks",
            "2 months",
            "3 months",
            "6 months",
            "Other",
        ],
    ),
    field(
        "notice_other",
        "Notice period — other",
        "Availability & compensation",
        aliases=["if you selected other please specify", "if you selected other to the above please specify"],
    ),
    field("availability", "Exact earliest start date (if agreed)", "Availability & compensation", kind="date"),
    field("earliest_start_month", "Earliest start month (YYYY-MM)", "Availability & compensation", kind="month"),
    field("masters_completion_month", "Master’s completion month (YYYY-MM)", "Education & experience", kind="month"),
    field("employment_status", "Current employment / study situation", "Education & experience", multiline=True),
    field("general_context", "General professional context and preferences", "Written answers", multiline=True),
    field(
        "expected_salary_uk",
        "Expected annual salary (GBP, UK roles)",
        "Availability & compensation",
        kind="number",
        scope="UK",
        aliases=["expected salary", "salary expectations", "what are your salary expectations"],
    ),
    field(
        "salary_notes",
        "Salary preferences for other countries / currencies",
        "Availability & compensation",
        multiline=True,
    ),
    field(
        "work_authorization_uk",
        "Right to work in the UK",
        "Work eligibility",
        sensitive=True,
        scope="UK",
        options=YES_NO,
        aliases=["are you legally authorized to work in the united kingdom", "right to work", "work authorization"],
    ),
    field(
        "sponsorship_uk",
        "Need sponsorship now or later (UK)",
        "Work eligibility",
        sensitive=True,
        scope="UK",
        options=YES_NO,
        aliases=[
            "do you require sponsorship now or in the future to work in the job s location",
            "do you require sponsorship",
            "will you require visa sponsorship",
        ],
    ),
    field(
        "visa_uk",
        "UK visa / immigration status",
        "Work eligibility",
        sensitive=True,
        scope="UK",
        aliases=[
            "current visa status",
            "visa status",
            "if you answered yes to the above can you please confirm your current visa type and status",
        ],
    ),
    field(
        "work_eligibility_other", "Work rights in other countries", "Work eligibility", sensitive=True, multiline=True
    ),
    field("preferred_roles", "Preferred roles", "Job preferences", multiline=True),
    field("preferred_locations", "Preferred locations", "Job preferences", multiline=True),
    field(
        "employment_type",
        "Employment type",
        "Job preferences",
        options=["Full-time", "Part-time", "Internship", "Contract", "Flexible"],
    ),
    field(
        "workplace_preference",
        "Workplace preference",
        "Job preferences",
        options=["Hybrid", "Remote", "On-site", "Flexible"],
    ),
    field("relocation", "Willing to relocate", "Job preferences", options=YES_NO),
    field("travel", "Travel preferences", "Job preferences"),
    field(
        "hybrid_trainline",
        "Trainline: commit to 60% office attendance",
        "Job preferences",
        options=YES_NO,
        scope="Trainline",
        aliases=["are you able to commit to our hybrid working policy"],
    ),
    *[
        field(k, label, "Education & experience", multiline=True)
        for k, label in [
            ("education_summary", "Education"),
            ("experience_summary", "Experience"),
            ("project_summary", "Projects"),
            ("skills_summary", "Skills"),
            ("languages", "Languages"),
            ("current_company", "Current employer"),
            ("current_title", "Current job title"),
        ]
    ],
    field(
        "ai_workflow",
        "How I use AI",
        "Written answers",
        multiline=True,
        aliases=["how are you currently using ai or thinking about using it to improve your day to day work"],
    ),
    field(
        "professional_summary",
        "Professional summary / additional information",
        "Written answers",
        multiline=True,
        aliases=["anything else you d like to share", "tell us about yourself"],
    ),
    field("motivation", "Career motivation", "Written answers", multiline=True),
    field("achievement", "Achievement or challenge", "Written answers", multiline=True),
    field(
        "adjustments",
        "Reasonable adjustments",
        "Optional disclosures",
        sensitive=True,
        multiline=True,
        aliases=[
            "reasonable adjustments",
            "do you require any reasonable adjustments",
            "do you need any reasonable adjustments for our recruitment process",
        ],
    ),
    *[
        field(k, label, "Optional disclosures", sensitive=True, aliases=aliases)
        for k, label, aliases in [
            ("sexual_orientation", "Sexual orientation", ["sexual orientation"]),
            ("ethnicity", "Ethnicity", ["ethnicity", "ethnic background"]),
            ("religion", "Religion / belief", ["religion", "religion or belief"]),
        ]
    ],
    *[
        field(k, label, "Optional disclosures", sensitive=True, options=[*YES_NO, "Prefer not to say"], aliases=aliases)
        for k, label, aliases in [
            ("disability", "Disability", ["do you have a disability"]),
            ("neurodivergence", "Neurodivergence", ["do you consider yourself to be neurodivergent"]),
            ("mental_health", "Mental health disclosure", ["do you have a mental health condition"]),
            ("veteran", "Veteran status", ["veteran status"]),
        ]
    ],
]
BY_KEY = {f["key"]: f for f in FIELDS}
for key, alias in {
    "gender": "describe your gender",
    "sexual_orientation": "describe your sexual orientation",
    "ethnicity": "describe your ethnic background",
    "religion": "describe your religion or belief",
    "neurodivergence": "are you neurodivergent",
}.items():
    BY_KEY[key]["aliases"].append(alias)


def memory_superseded(db, question, updated_at):
    """Concrete newer profile constraints supersede old form-specific guesses/skips.

    A saved general contractual notice remains authoritative. We do not delete
    history or turn an old question response into a global employment fact.
    """
    key = canonical_key(question)
    norm = normalize(question)
    if norm in BY_KEY['notice_other']['aliases']:
        key = 'notice_other'
    dependencies = {
        'notice_period': ['earliest_start_month', 'notice_period'],
        'notice_other': ['earliest_start_month', 'notice_period', 'notice_other'],
        'availability': ['earliest_start_month', 'availability'],
        'graduation_year': ['masters_completion_month'],
    }.get(key, [])
    for dependency in dependencies:
        row = db.one('SELECT updated_at FROM facts WHERE source=?', ('application_profile:'+dependency,))
        if row and row['updated_at'] > updated_at:
            return True
    return False


def profile(db):
    facts = db.query("SELECT * FROM facts ORDER BY updated_at")
    rows = []
    for f in FIELDS:
        explicit = next((x for x in facts if x["source"] == "application_profile:" + f["key"]), None)
        if explicit:
            value = explicit["value"]
            state = (
                "confirmed"
                if explicit["verification_status"] == "verified" and value
                else "suggested"
                if value
                else "unknown"
            )
            source, ids = explicit["notes"], [explicit["id"]]
        else:
            answer = resolve_answer(
                f["key"], [x for x in facts if not x["source"].startswith(("browser_answer:", "application_profile:"))]
            )
            value, ids = answer["answer"] or "", answer["fact_ids"]
            category = {
                "education_summary": "education",
                "experience_summary": "experience",
                "project_summary": "project",
                "skills_summary": "skill",
                "languages": "language",
            }.get(f["key"])
            if category:
                evidence = [
                    x
                    for x in facts
                    if x["category"] == category and (x["verification_status"] == "verified" or x["locked"])
                ]
                value = "\n\n".join(x["value"] for x in evidence)
                ids = [x["id"] for x in evidence]
            state, source = ("confirmed", "Verified CV / profile facts") if value else ("unknown", "Not provided")
        rows.append({**f, "value": value, "state": state, "source": source, "fact_ids": ids})
    memory = db.query(
        "SELECT m.*,f.value AS answer,f.verification_status FROM browser_answers m JOIN facts f ON m.fact_id=f.id ORDER BY m.updated_at DESC"
    )
    for row in memory:
        row['superseded'] = memory_superseded(db, row['question'], row['updated_at'])
    return {"fields": rows, "answers": memory}


def write_profile(db, key, value, confirmed, note="Entered in application profile"):
    from .core import invalidate_fact_evidence

    if key not in BY_KEY:
        raise ValueError("Unknown profile field")
    value = value.strip()
    f = BY_KEY[key]
    if f["type"] == "number" and value and (not re.fullmatch(r"\d+(?:\.\d+)?", value) or float(value) > 1e9):
        raise ValueError("Enter a nonnegative number without a currency symbol")
    if f["type"] == "month" and value and not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("Enter a month as YYYY-MM")
    if f["type"] == "date" and value:
        from datetime import date

        date.fromisoformat(value)
    status = "verified" if confirmed and value else "unverified"
    source = "application_profile:" + key
    with db.transaction() as conn:
        old = conn.execute("SELECT * FROM facts WHERE source=?", (source,)).fetchone()
        identifier = old["id"] if old else uid()
        if old:
            invalidate_fact_evidence(db, conn, dict(old))
            conn.execute(
                "UPDATE facts SET value=?,verification_status=?,locked=0,notes=?,updated_at=? WHERE id=?",
                (value, status, note, now(), identifier),
            )
        else:
            candidate = conn.execute("SELECT id FROM candidates LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, candidate, "application_profile", key, value, source, status, 0, note, now(), now()),
            )
    return identifier


def scope_matches(scope, application):
    if not scope:
        return True
    if scope == "Trainline":
        return normalize(application.get("company", "")) == "trainline"
    if scope == "UK":
        return bool(
            re.search(
                r"\b(?:london|united kingdom|uk|great britain|england|scotland|wales|northern ireland)\b",
                normalize(application.get("location", "")),
            )
        )
    return False


def question_field(question, application):
    norm, key = normalize(question), canonical_key(question)
    for f in FIELDS:
        if not scope_matches(f["scope"], application):
            continue
        if norm in {normalize(f["key"]), normalize(f["label"]), *f["aliases"]} or (key and key == f["key"]):
            return f
    return None


def application_knowledge(db, application, facts):
    """Scoped memories never leak into another application's candidate answers."""
    base = [f for f in facts if not f["source"].startswith(("browser_answer:", "application_profile:"))]
    # An explicit edit/clear in Profile overrides a CV value, including when
    # the replacement is still a draft. Do not silently resurrect the old value.
    overrides = {
        canonical_key(f["key"]) or normalize(f["key"])
        for f in db.query("SELECT key FROM facts WHERE source LIKE 'application_profile:%'")
    }
    base = [f for f in base if (canonical_key(f["key"]) or normalize(f["key"])) not in overrides]
    for f in facts:
        if f["source"].startswith("application_profile:"):
            spec = BY_KEY.get(f["key"])
            if spec and scope_matches(spec["scope"], application):
                base.append(
                    {
                        **f,
                        "profile_aliases": [normalize(spec["key"]), normalize(spec["label"]), *spec["aliases"]],
                        "profile_explicit": True,
                        "answer_priority": 1,
                    }
                )
    skipped = []
    for row in db.query(
        "SELECT m.*,f.* FROM browser_answers m JOIN facts f ON f.id=m.fact_id WHERE m.application_id=? OR m.reusable=1",
        (application["id"],),
    ):
        if memory_superseded(db, row['question'], row['updated_at']):
            continue
        if row["verification_status"] != "verified" or not scope_matches(row["scope"], application):
            continue
        if row["action"] == "skip":
            skipped.append(normalize(row["question"]))
        else:
            base.append(
                {
                    **row,
                    "id": row["fact_id"],
                    "key": row["question"],
                    "profile_explicit": True,
                    "answer_priority": 3 if row["application_id"] == application["id"] else 2,
                }
            )
    return base, skipped


def remember_answer(db, application, question, answer, *, reusable=False, skip=False):
    from .core import invalidate_fact_evidence
    from .automation.answers import classify_question

    spec = question_field(question, application)
    # Employer wording and regulated/disclosure answers stay local unless the user explicitly opts in.
    # Even then, unknown legal and country-specific questions cannot gain global reuse.
    kind = classify_question(question)
    if kind in {"legal_certification", "work_authorization", "salary"} and (not spec or not spec.get("scope")):
        reusable = False
    norm = normalize(question)
    with db.transaction() as conn:
        previous = conn.execute(
            "SELECT * FROM browser_answers WHERE application_id=? AND question=?", (application["id"], norm)
        ).fetchone()
        identifier, fact_id = (previous["id"], previous["fact_id"]) if previous else (uid(), uid())
        if previous:
            old = dict(conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone())
            invalidate_fact_evidence(db, conn, old)
            conn.execute(
                "UPDATE facts SET value=?,verification_status='verified',locked=0,updated_at=? WHERE id=?",
                (answer, now(), fact_id),
            )
        else:
            candidate = conn.execute("SELECT id FROM candidates LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    candidate,
                    "application_answer",
                    norm,
                    answer,
                    "browser_answer:" + identifier,
                    "verified",
                    0,
                    "Confirmed in the browser for " + application["company"],
                    now(),
                    now(),
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO browser_answers VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                application["id"],
                norm,
                fact_id,
                int(reusable),
                spec.get("scope") if spec else None,
                "skip" if skip else "answer",
                now(),
                now(),
            ),
        )
        conn.execute(
            "UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE application_id=? AND question=?",
            (answer or "Left blank by user", now(), application["id"], question),
        )
    if reusable and spec and not skip:
        write_profile(db, spec["key"], answer, True, "Confirmed in browser: " + application["company"])
    return fact_id


def draft_fact_ids(facts, question):
    """Do not send demographic, legal, salary or unrelated personal facts to text models."""
    safe = [
        f
        for f in facts
        if f["category"] in {"experience", "project", "education", "skill", "language"}
        or f["key"] in {"ai_workflow", "professional_summary", "motivation", "achievement", "general_context", "employment_status", "earliest_start_month", "masters_completion_month", "preferred_roles", "preferred_locations", "employment_type", "workplace_preference", "current_company", "current_title"}
    ]
    tokens = set(normalize(question).split()) - {"what", "your", "you", "are", "the", "and", "how", "work"}
    return [
        f["id"]
        for f in sorted(
            safe,
            key=lambda f: (
                len(tokens & set(normalize(f["key"] + " " + f["value"]).split())),
                f["category"] in {"experience", "project"},
            ),
            reverse=True,
        )[:20]
    ]
