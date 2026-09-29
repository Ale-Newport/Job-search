"""Transparent, deterministic semantic matching; candidate facts are never inferred."""

from __future__ import annotations

import json
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


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def contains(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", normalized(text)) is not None


def extract_skills(text: str) -> list[str]:
    return sorted(key for key, aliases in VOCABULARY.items() if any(contains(text, alias) for alias in aliases))


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
    location_known = bool(job.get("location")) or job.get("remote") is not None
    location_fit = not locations or any(contains(job.get("location", ""), loc) for loc in locations)
    if cfg.get("remote") and job.get("remote"):
        location_fit = True
    location_score = float(location_fit) if location_known else 0.5
    min_salary = cfg.get("minimum_salary")
    salary_known = job.get("salary_max") is not None or job.get("salary_min") is not None
    salary_value = job.get("salary_max") or job.get("salary_min")
    salary_fit = not min_salary or (salary_known and salary_value >= min_salary)
    salary_score = float(salary_fit) if salary_known else 0.5
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
            "weight": 40,
            "value": round(skill_score, 3),
            "reason": f"{len(matched)} of {len(required)} detected skill concepts supported by verified facts",
        },
        "role": {
            "weight": 25,
            "value": role_score,
            "reason": "Exact title or explicit role-family vocabulary against your preferences",
        },
        "location": {
            "weight": 15,
            "value": location_score,
            "reason": "Location preference fit" if location_known else "Location unknown",
        },
        "seniority": {"weight": 10, "value": experience_score, "reason": experience or "Seniority unknown"},
        "salary": {
            "weight": 10,
            "value": salary_score,
            "reason": "Advertised salary compared with your preference"
            if salary_known
            else "Salary unknown; no salary inferred",
        },
    }
    exclusions = []
    job_text = " ".join(str(job.get(k, "")) for k in ("title", "description", "company", "location"))
    technologies = cfg.get("technologies", [])
    if technologies:
        components["skills"]["weight"] = 30
        components["technology_preference"] = {
            "weight": 10,
            "value": sum(contains(job_text, technology) for technology in technologies) / len(technologies),
            "reason": "Advertised technologies compared with your editable technology preferences",
        }
    for term in cfg.get("negative_keywords", []):
        if contains(job_text, term):
            exclusions.append(f"Excluded keyword: {term}")
    if any(normalized(job.get("company", "")) == normalized(c) for c in cfg.get("excluded_companies", [])):
        exclusions.append("Company is excluded by this search profile")
    if cfg.get("companies") and not any(normalized(job.get("company", "")) == normalized(c) for c in cfg["companies"]):
        exclusions.append("Company is outside this profile's company list")
    if locations and location_known and not location_fit:
        exclusions.append("Location is outside this profile")
    if min_salary and salary_known and not salary_fit:
        exclusions.append("Advertised salary is below the minimum")
    if cfg.get("remote") is True and job.get("remote") is False:
        exclusions.append("Role is explicitly on-site")
    if cfg.get("remote") is False and job.get("remote") is True:
        exclusions.append("Role is remote but this profile requests on-site work")
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
    freshness = 0
    if job.get("posted_at"):
        try:
            posted = datetime.fromisoformat(job["posted_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - posted.replace(tzinfo=posted.tzinfo or timezone.utc)).days
            freshness = max(0, 8 - max(0, age) / 3)
        except (TypeError, ValueError):
            pass
    priority = round(min(100, score * 0.85 + freshness + float(job.get("company_priority", 50)) * 0.1), 1)
    effective_threshold = round(float(cfg.get("min_match", 0)) * (1 - float(cfg.get("stretch_factor", 0))), 1)
    return {
        "score": score,
        "priority_score": priority,
        "profile_id": profile.get("id") if profile else None,
        "eligible": not exclusions and score >= effective_threshold,
        "effective_threshold": effective_threshold,
        "matched_skills": matched,
        "missing_skills": missing,
        "components": components,
        "exclusions": exclusions,
        "verified_fact_count": len(trusted),
        "fact_ids": [f["id"] for f in trusted if any(contains(f.get("value", ""), s) for s in matched)],
        "feedback_adjustment": adjustment,
        "feedback_reasons": sorted(set(reasons)),
        "unknowns": [
            name
            for name, known in (
                ("salary", salary_known),
                ("location", location_known),
                ("seniority", bool(experience)),
                ("work_authorization", False),
            )
            if not known
        ],
        "method": "Explicit semantic vocabulary and weighted evidence; not a prediction of hiring probability",
    }
