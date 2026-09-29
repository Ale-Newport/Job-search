"""Conservative field resolution with source fact IDs, never invented candidate values."""

from __future__ import annotations

import json
import re
import unicodedata


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


ALIASES = {
    "first_name": ["first name", "given name", "legal first name", "nombre"],
    "last_name": ["last name", "family name", "surname", "legal last name", "apellido"],
    "full_name": ["full name", "name", "legal name", "your name"],
    "email": ["email", "email address", "your email", "e mail"],
    "phone": ["phone", "phone number", "mobile", "telephone", "mobile phone"],
    "country": ["country", "country of residence", "current country"],
    "city": ["city", "current city", "town"],
    "location": ["location", "current location", "where are you based"],
    "address": ["address", "street address", "address line 1"],
    "postcode": ["postcode", "postal code", "zip code", "zip postal code"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url"],
    "github": ["github", "github url", "github profile"],
    "portfolio": ["portfolio", "portfolio url", "website", "personal website"],
    "university": ["university", "school", "school name", "institution"],
    "degree": ["degree", "degree type", "qualification"],
    "field_of_study": ["field of study", "major", "subject"],
    "graduation_year": ["graduation year", "year of graduation"],
    "current_company": ["current company", "current employer"],
    "current_title": ["current title", "current job title", "job title"],
    "expected_salary": ["expected salary", "salary expectation", "salary expectations", "desired salary"],
    "availability": ["availability", "available from", "earliest start date", "start date"],
    "notice_period": ["notice period", "what is your notice period"],
    "work_authorization": ["work authorization", "are you legally authorized to work", "right to work"],
    "sponsorship_required": ["sponsorship required", "do you require sponsorship", "will you require visa sponsorship"],
    "relocation": ["relocation", "willing to relocate", "are you willing to relocate"],
}

SENSITIVE = {
    "disability": r"disabilit|medical|health condition",
    "ethnicity": r"ethnic|racial|race\b",
    "gender": r"gender|\bsex\b|sexual orientation|pronoun",
    "criminal_record": r"criminal|convict|arrest",
    "demographic": r"veteran|demographic|religio|marital",
    "legal_certification": r"certif|attest|agree|consent|privacy|terms (?:and|of)|acknowledge",
}


def classify_question(question: str) -> str:
    text = normalize(question)
    for category, pattern in SENSITIVE.items():
        if re.search(pattern, text):
            return category
    if re.search(r"visa|sponsor|authoriz|right to work|eligible to work|nationality|citizen", text):
        return "work_authorization"
    if re.search(r"salary|compensation|pay expectation", text):
        return "salary"
    if re.search(r"why |motivat|tell us|describe|cover letter", text):
        return "free_text"
    if re.search(r"email|phone|address|postcode", text):
        return "contact"
    return "identity" if "name" in text else "unknown"


def fact_value(fact: dict) -> str:
    value = fact.get("value", "")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        # The core API returns decoded values; raw DB adapters can return JSON strings.
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return json.dumps(value, ensure_ascii=False) if value else ""


def approved_facts(facts: list[dict]) -> list[dict]:
    return [f for f in facts if (f.get("verification_status") == "verified" or f.get("locked") is True or
                                f.get("locked") == 1) and fact_value(f)]


def canonical_key(label: str) -> str | None:
    norm = normalize(re.sub(r"\s*\(?(?:required|optional)\)?\s*$", "", label, flags=re.I))
    for key, aliases in ALIASES.items():
        if norm == normalize(key) or norm in aliases:
            return key
    return None


def resolve_answer(question: str, facts: list[dict], *, hints: list[str] | None = None,
                   policies: dict | None = None) -> dict:
    category = classify_question(question)
    result = {"question": question, "kind": category, "answer": None, "fact_ids": [], "verified": False,
              "confidence": 0.0, "leave_blank": False}
    allowed = approved_facts(facts)
    policies = policies or {}
    question_policies = policies.get("questions", {})
    policy = (question_policies.get(normalize(question), policies.get(category, {}))
              if isinstance(question_policies, dict) else policies.get(category, {}))
    if isinstance(policy, str):
        policy = {"action": policy}
    if category in SENSITIVE:
        action = policy.get("action", "ask_me")
        if action == "leave_blank":
            return {**result, "leave_blank": True, "verified": True, "confidence": 1.0}
        if action == "prefer_not_to_say" and category != "legal_certification":
            return {**result, "answer": "Prefer not to say", "verified": True, "confidence": 1.0,
                    "policy": "prefer_not_to_say"}
        if action != "saved_answer":
            return result
        allowed = [f for f in allowed if str(f.get("id")) == str(policy.get("fact_id"))]
        if len(allowed) != 1:
            return result
        fact = allowed[0]
        return {**result, "answer": fact_value(fact), "fact_ids": [fact["id"]], "verified": True, "confidence": 1.0}
    labels = [question, *(hints or [])]
    norms = {normalize(s) for s in labels if s}
    keys = {canonical_key(s) for s in labels if s} - {None}
    exact = [f for f in allowed if normalize(f.get("key", "")) in norms or
             normalize(f.get("question", "")) in norms or canonical_key(f.get("key", "")) in keys]
    # Contradictory or repeated education/employment entries require explicit selection.
    values = {fact_value(f) for f in exact}
    if len(values) == 1:
        return {**result, "answer": next(iter(values)), "fact_ids": [f["id"] for f in exact],
                "verified": True, "confidence": 1.0}
    if keys == {"full_name"}:
        first = resolve_answer("first name", allowed)
        last = resolve_answer("last name", allowed)
        if first["answer"] and last["answer"]:
            return {**result, "answer": first["answer"] + " " + last["answer"],
                    "fact_ids": first["fact_ids"] + last["fact_ids"], "verified": True, "confidence": 1.0}
    return result


def option_for(answer: str, options: list[dict]) -> dict | None:
    norm = normalize(answer)
    matches = [o for o in options if not o.get("disabled") and
               norm in {normalize(o.get("label", "")), normalize(o.get("value", ""))}]
    if len(matches) == 1:
        return matches[0]
    if norm == "prefer not to say":
        matches = [o for o in options if not o.get("disabled") and
                   normalize(o.get("label", "")) in {"decline to self identify", "i do not wish to answer", "decline to answer"}]
        if len(matches) == 1:
            return matches[0]
    return None
