"""Transparent, deterministic semantic matching; candidate facts are never inferred."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timezone

VOCABULARY = {
    "python": ["python", "django", "fastapi", "flask"],
    "javascript": ["javascript", "js", "ecmascript"],
    "typescript": ["typescript", "ts"],
    "java": ["java", "jvm", "spring boot"],
    "react": ["react", "reactjs", "react.js"],
    "vue": ["vue", "vuejs", "vue.js"],
    "sql": ["sql", "postgresql", "postgres", "mysql", "sqlite"],
    "aws": ["aws", "amazon web services"],
    "azure": ["azure"],
    "gcp": ["gcp", "google cloud"],
    "machine learning": ["machine learning", "ml", "pytorch", "tensorflow", "scikit-learn"],
    "artificial intelligence": ["artificial intelligence", "ai", "llm", "generative ai"],
    "data engineering": ["data engineering", "etl", "data pipeline", "apache spark", "airflow"],
    "docker": ["docker", "containerization", "containers"],
    "kubernetes": ["kubernetes", "k8s"],
    "git": ["git", "version control"],
    "c++": ["c++", "cpp"],
    "c#": ["c#", "csharp", ".net"],
    "go": ["golang", "go language"],
    "rust": ["rust"],
    "swift": ["swift", "swiftui"],
}
ROLE_FAMILIES = {
    "software": [
        "software engineer",
        "software developer",
        "backend engineer",
        "frontend engineer",
        "full stack",
        "fullstack",
        "web developer",
    ],
    "ai": [
        "ai engineer",
        "artificial intelligence",
        "machine learning",
        "ml engineer",
        "research engineer",
        "applied ai",
    ],
    "data": ["data engineer", "data engineering", "analytics engineer", "ai/data"],
    "cloud": ["cloud engineer", "platform engineer", "devops", "infrastructure engineer"],
}
DEFAULT_WEIGHTS = {
    "skills": 30,
    "role": 20,
    "location": 10,
    "seniority": 10,
    "salary": 10,
    "technology_preference": 0,
    "education": 10,
    "experience": 10,
}
CONTRACT_ALIASES = {
    "full_time": {"fulltime"},
    "part_time": {"parttime"},
    "contract": {"contract", "contractor", "freelance"},
    "temporary": {"temporary", "fixedterm", "seasonal"},
    "internship": {"internship", "intern"},
    "volunteer": {"volunteer", "voluntary"},
    "other": {"other"},
}


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def contains(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", normalized(text)) is not None


def extract_skills(text: str) -> list[str]:
    return sorted(key for key, aliases in VOCABULARY.items() if any(contains(text, alias) for alias in aliases))


def work_arrangement(value) -> str | None:
    token = re.sub(r"[\s_-]", "", normalized(value or ""))
    return {"remote": "remote", "telecommute": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(token)


def contract_types(value) -> list[str]:
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or value.get("id")
    values = value if isinstance(value, list) else [value]
    tokens = {re.sub(r"[\s_-]", "", normalized(item or "")) for item in values}
    return sorted(key for key, aliases in CONTRACT_ALIASES.items() if tokens & aliases)


def text_values(value) -> list[str]:
    """Preserve explicit structured labels without inferring industry from an employer."""
    if isinstance(value, list):
        return [item for part in value for item in text_values(part)]
    if isinstance(value, dict):
        return text_values(
            value.get("name") or value.get("label") or value.get("credentialCategory") or value.get("description")
        )
    return [str(value).strip()] if isinstance(value, str) and value.strip() else []


def numeric_years(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:years?|años?)?\s*", str(value), re.I)
    if not match:
        return None
    result = float(match[1])
    return result if math.isfinite(result) and 0 <= result <= 100 else None


def _degree(text):
    for level, aliases in (
        (3, ["phd", "ph.d", "ph.d.", "doctorate", "doctoral", "doctorado"]),
        (2, ["master", "master's", "masters", "msc", "m.sc", "m.sc.", "meng", "máster"]),
        (1, ["bachelor", "bachelor's", "bachelors", "bsc", "b.sc", "b.sc.", "beng", "licenciatura"]),
    ):
        if any(contains(text, alias) for alias in aliases):
            return level
    return None


def qualification_components(metadata, trusted):
    """Use only explicit qualification claims and comparable numeric experience facts."""
    education = {
        "value": 0.5,
        "known": False,
        "fact_ids": [],
        "reason": "Education comparison unknown: explicit requirement and verified completed qualification needed",
    }
    required_text = " ".join(text_values(metadata.get("education_requirements")))
    required_level = _degree(required_text)
    subjects = [
        subject
        for subject in (
            "computer science",
            "software engineering",
            "data science",
            "mathematics",
            "physics",
            "electrical engineering",
            "mechanical engineering",
            "business",
            "artificial intelligence",
        )
        if contains(required_text, subject)
    ]
    unrecognized_subject = bool(re.search(r"\bin\s+\w", required_text, re.I)) and not subjects
    for fact in trusted:
        value = fact.get("value", "")
        if fact.get("category") != "education" or not required_level or unrecognized_subject:
            continue
        if re.search(
            r"\b(currently|pursuing|student|enrolled|expected|in progress|candidate|incomplete|unfinished|without)\b",
            value,
            re.I,
        ):
            continue
        if re.search(r"\bno\s+(?:degree|bachelor|master|phd)", value, re.I):
            continue
        if any(int(year) > datetime.now(timezone.utc).year for year in re.findall(r"\b20\d{2}\b", value)):
            continue
        level = _degree(value)
        if level and level >= required_level and (not subjects or any(contains(value, s) for s in subjects)):
            education.update(
                value=1.0,
                known=True,
                fact_ids=[fact["id"]],
                reason="Advertised degree level and detected subject supported by a verified completed qualification; equivalence requires review",
            )
            break
    required_years = numeric_years(metadata.get("experience_years_required"))
    experience = {
        "value": 0.5,
        "known": False,
        "fact_ids": [],
        "reason": "Experience comparison unknown: explicit total professional years on both sides needed; projects are not employment",
    }
    year_evidence = []
    for fact in trusted:
        key = normalized(fact.get("key", "")).replace(" ", "_").replace("-", "_")
        if fact.get("category") != "experience" or key not in {
            "years_of_experience",
            "professional_experience_years",
            "total_professional_years",
        }:
            continue
        candidate_years = numeric_years(fact.get("value"))
        if required_years is not None and candidate_years is not None:
            year_evidence.append((candidate_years, fact["id"]))
    if len({years for years, _ in year_evidence}) == 1:
        candidate_years = year_evidence[0][0]
        experience.update(
            value=1.0 if required_years == 0 else min(1.0, candidate_years / required_years),
            known=True,
            fact_ids=[identifier for _, identifier in year_evidence],
            reason=f"{candidate_years:g} verified total professional years against {required_years:g} explicitly required total years; role-specific depth is not inferred",
        )
    elif year_evidence:
        experience["reason"] = "Conflicting verified total professional years require review"
    return education, experience


def role_similarity(title: str, wanted: list[str]) -> float:
    if not wanted:
        return 0.5
    if any(contains(title, role) or contains(role, title) for role in wanted):
        return 1.0
    actual_families = {key for key, aliases in ROLE_FAMILIES.items() if any(contains(title, a) for a in aliases)}
    requested_families = {
        key for key, aliases in ROLE_FAMILIES.items() if any(contains(role, a) for a in aliases for role in wanted)
    }
    return 0.8 if actual_families & requested_families else 0.0


def _config(profile):
    result = profile.get("config", {}) if profile else {}
    return json.loads(result) if isinstance(result, str) else result


def match_job(job: dict, facts: list[dict], profile: dict | None = None, feedback: list[dict] | None = None) -> dict:
    cfg = _config(profile)
    trusted = [f for f in facts if f.get("verification_status") == "verified" or f.get("locked")]
    professional = [
        f
        for f in trusted
        if f.get("category")
        not in {
            "answer",
            "saved_answer",
            "personal",
            "contact",
            "preference",
            "salary",
            "sensitive",
            "work_authorization",
        }
        and normalized(f.get("value", "")) not in {"no", "false", "none", "n/a"}
    ]
    candidate_text = " ".join(f"{f.get('key', '')} {f.get('value', '')}" for f in professional)
    candidate_skills = set(extract_skills(candidate_text))
    explicit_skills = {normalized(f["value"]) for f in professional if f.get("category") == "skill"}
    raw_skills = job.get("skills", [])
    if isinstance(raw_skills, str):
        raw_skills = json.loads(raw_skills)
    required = set(raw_skills) | set(extract_skills(job.get("description", "")))
    matched = sorted(required & (candidate_skills | explicit_skills))
    missing = sorted(required - set(matched))
    skill_score = len(matched) / len(required) if required else (0.5 if trusted else 0)
    role_score = role_similarity(job.get("title", ""), cfg.get("roles", []))
    locations = cfg.get("locations", [])
    location_known = bool(job.get("location"))
    location_fit = not locations or any(contains(job.get("location", ""), loc) for loc in locations)
    if cfg.get("remote") and job.get("remote"):
        location_fit = True
        location_known = True
    location_score = float(location_fit) if location_known else 0.5
    min_salary = cfg.get("minimum_salary")
    salary_known = job.get("salary_max") is not None or job.get("salary_min") is not None
    salary_value = job.get("salary_max") if job.get("salary_max") is not None else job.get("salary_min")
    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    salary_unit = normalized(metadata.get("salary_unit") or "").replace("-", " ")
    annual = salary_unit in {"year", "yearly", "annual", "annually", "per year", "1 year", "yr", "annum"}
    preference_currency = str(cfg.get("salary_currency") or "").upper()
    advertised_currency = str(job.get("currency") or "").upper()
    salary_comparable = bool(
        salary_known and annual and preference_currency and advertised_currency == preference_currency
    )
    salary_fit = not min_salary or (salary_comparable and salary_value >= min_salary)
    salary_score = float(salary_fit) if min_salary and salary_comparable else 0.5
    salary_reason = (
        "Annual advertised salary compared in the same explicitly configured currency"
        if min_salary and salary_comparable
        else "No annual salary minimum configured"
        if not min_salary
        else "Salary comparison unknown: a known annual period and matching configured currency are required"
    )
    title = normalized(job.get("title", ""))
    experience = job.get("experience_level") or next(
        (x for x in ("principal", "staff", "senior", "graduate", "junior", "intern", "entry") if contains(title, x)),
        None,
    )
    levels = cfg.get("experience_levels", [])
    experience_score = (
        1.0
        if not levels or (experience and any(contains(experience, x) for x in levels))
        else 0.5
        if experience is None
        else 0.0
    )
    components = {
        "skills": {
            "weight": 30,
            "value": round(skill_score, 3),
            "reason": f"{len(matched)} of {len(required)} detected skill concepts supported by verified facts",
        },
        "role": {
            "weight": 20,
            "value": role_score,
            "reason": "Exact title or explicit role-family vocabulary against your preferences",
        },
        "location": {
            "weight": 10,
            "value": location_score,
            "reason": "Location preference fit" if location_known else "Location unknown",
        },
        "seniority": {"weight": 10, "value": experience_score, "reason": experience or "Seniority unknown"},
        "salary": {
            "weight": 10,
            "value": salary_score,
            "reason": salary_reason,
        },
    }
    education_component, experience_component = qualification_components(metadata, trusted)
    components["education"] = education_component
    components["experience"] = experience_component
    exclusions = []
    if cfg.get("region") == "europe":
        from .geography import location_evidence

        if not location_evidence(job.get("location"))["europe"]:
            exclusions.append("A European job location must be explicitly identifiable")
    job_text = " ".join(str(job.get(k, "")) for k in ("title", "description", "company", "location"))
    technologies = cfg.get("technologies", [])
    components["technology_preference"] = {
        "value": sum(contains(job_text, technology) for technology in technologies) / len(technologies)
        if technologies
        else 0.5,
        "reason": "Advertised technologies compared with your editable technology preferences"
        if technologies
        else "No technology preference configured",
    }
    weights = dict(DEFAULT_WEIGHTS)
    if technologies:
        weights.update(skills=20, technology_preference=10)
    configured_weights = cfg.get("weights") or {}
    if configured_weights:
        weights = {key: float(configured_weights.get(key, 0)) for key in components}
    weight_total = sum(weights.values())
    for key, component in components.items():
        component["weight"] = weights[key] / weight_total * 100

    arrangement = work_arrangement(metadata.get("work_arrangement"))
    if arrangement is None and job.get("remote") in (True, 1):
        arrangement = "remote"
    contracts = contract_types(metadata.get("contract_types") or metadata.get("employment_type"))
    industries = text_values(metadata.get("industries") or metadata.get("industry"))
    sponsorship = metadata.get("sponsorship_available")
    sponsorship = sponsorship if isinstance(sponsorship, bool) else None
    age = None
    if job.get("posted_at"):
        try:
            posted = datetime.fromisoformat(job["posted_at"].replace("Z", "+00:00"))
            days = (
                datetime.now(timezone.utc) - posted.replace(tzinfo=posted.tzinfo or timezone.utc)
            ).total_seconds() / 86400
            age = int(days) if days >= 0 else None
        except (TypeError, ValueError):
            pass
    filters = {}
    for name, actual, wanted, reason in (
        ("industries", industries, cfg.get("industries"), "Industry is outside this profile"),
        (
            "work_arrangements",
            [arrangement] if arrangement else [],
            cfg.get("work_arrangements"),
            "Work arrangement is outside this profile",
        ),
        ("contract_types", contracts, cfg.get("contract_types"), "Contract type is outside this profile"),
    ):
        fits = bool({normalized(value) for value in actual} & {normalized(value) for value in wanted or []})
        status = "not_requested" if not wanted else "unknown" if not actual else "pass" if fits else "excluded"
        filters[name] = {"status": status, "advertised": actual, "requested": wanted or []}
        if status == "excluded":
            exclusions.append(reason)
    max_age = cfg.get("max_posting_age_days")
    age_status = (
        "not_requested" if max_age is None else "unknown" if age is None else "pass" if age <= max_age else "excluded"
    )
    filters["posting_age"] = {"status": age_status, "days": age, "maximum_days": max_age}
    if age_status == "excluded":
        exclusions.append("Posting is older than this profile's maximum age")
    sponsorship_status = (
        "not_requested"
        if cfg.get("requires_sponsorship") is not True
        else "unknown"
        if sponsorship is None
        else "pass"
        if sponsorship
        else "excluded"
    )
    filters["sponsorship"] = {
        "status": sponsorship_status,
        "advertised_available": sponsorship,
        "required": cfg.get("requires_sponsorship"),
        "reason": "Employer sponsorship availability does not establish your legal work authorization",
    }
    if sponsorship_status == "excluded":
        exclusions.append("Employer explicitly does not offer the required sponsorship")
    for term in cfg.get("negative_keywords", []):
        if contains(job_text, term):
            exclusions.append(f"Excluded keyword: {term}")
    if any(normalized(job.get("company", "")) == normalized(c) for c in cfg.get("excluded_companies", [])):
        exclusions.append("Company is excluded by this search profile")
    if cfg.get("companies") and not any(normalized(job.get("company", "")) == normalized(c) for c in cfg["companies"]):
        exclusions.append("Company is outside this profile's company list")
    if locations and location_known and not location_fit:
        exclusions.append("Location is outside this profile")
    if min_salary and salary_comparable and not salary_fit:
        exclusions.append("Advertised salary is below the minimum")
    if cfg.get("remote") is True and arrangement in {"hybrid", "onsite"}:
        exclusions.append("Role explicitly requires workplace attendance")
    if cfg.get("remote") is False and arrangement in {"remote", "hybrid"}:
        exclusions.append("Role is remote or hybrid but this profile requests on-site work")
    if levels and experience and experience_score == 0:
        exclusions.append("Advertised seniority is outside this profile")
    for term in job.get("company_blacklist_keywords", []):
        if contains(job_text, term):
            exclusions.append(f"Company watchlist excludes keyword: {term}")
    keywords = cfg.get("keywords", [])
    if keywords and not any(contains(job_text, k) for k in keywords):
        exclusions.append("None of the required keywords are present")
    score = sum(c["weight"] * c["value"] for c in components.values())
    adjustment = 0
    reasons = []
    for item in (feedback or [])[-30:]:
        if (
            normalized(item.get("company", "")) == normalized(job.get("company", ""))
            and item.get("reason") == "company"
        ):
            adjustment += 3 if item.get("interested") else -5
            reasons.append("Your prior company feedback")
        elif (
            item.get("reason") == "wrong role" and role_similarity(job.get("title", ""), [item.get("title", "")]) > 0.7
        ):
            adjustment += 2 if item.get("interested") else -3
            reasons.append("Your prior role feedback")
    adjustment = max(-15, min(10, adjustment))
    score = round(max(0, min(100, score + adjustment)), 1)
    freshness = max(0, 8 - age / 3) if age is not None else 0
    priority = round(min(100, score * 0.85 + freshness + float(job.get("company_priority", 50)) * 0.1), 1)
    effective_threshold = round(float(cfg.get("min_match", 0)) * (1 - float(cfg.get("stretch_factor", 0))), 1)
    return {
        "score": score,
        "priority_score": priority,
        "profile_id": profile.get("id") if profile else None,
        "eligible": not exclusions and score >= effective_threshold,
        "effective_threshold": effective_threshold,
        "salary_comparison": {
            "comparable": salary_comparable,
            "advertised_currency": advertised_currency or None,
            "advertised_unit": salary_unit or None,
            "preference_currency": preference_currency or None,
            "preference_unit": "year",
            "status": "compared" if min_salary and salary_comparable else "unknown" if min_salary else "not_requested",
        },
        "matched_skills": matched,
        "missing_skills": missing,
        "components": components,
        "filters": filters,
        "exclusions": exclusions,
        "verified_fact_count": len(trusted),
        "fact_ids": sorted(
            set(
                [f["id"] for f in trusted if any(contains(f.get("value", ""), s) for s in matched)]
                + education_component["fact_ids"]
                + experience_component["fact_ids"]
            )
        ),
        "feedback_adjustment": adjustment,
        "feedback_reasons": sorted(set(reasons)),
        "unknowns": [
            name
            for name, known in (
                ("salary", salary_known),
                ("salary_comparison", not min_salary or salary_comparable),
                ("location", location_known),
                ("seniority", bool(experience)),
                ("education", education_component["known"]),
                ("experience", experience_component["known"]),
                ("work_authorization", False),
                *((name, value["status"] != "unknown") for name, value in filters.items()),
            )
            if not known
        ],
        "method": "Explicit semantic vocabulary and weighted evidence; not a prediction of hiring probability",
    }
