from __future__ import annotations

from datetime import date
import hashlib
import re

from ..db import dumps, now, uid
from ..automation.answers import classify_question, normalize
from ..automation.disclosures import (
    disclosure_action,
    disclosure_memory_key,
    disclosure_profile_key,
    disclosure_resolution,
    personal_disclosure_kind,
)
from .narratives import compose_narrative
from .store import Knowledge
from .semantics import interpret, semantic_interpret, education_level
from .temporal import interval, exposure, requested_date
from .formatting import adapt, choice, format_date, infer_format

NARRATIVE_INTENTS = {
    "MOTIVATION",
    "COMPANY_INTEREST",
    "ROLE_INTEREST",
    "CAREER_GOALS",
    "ADDITIONAL_CONTEXT",
    "COVER_LETTER",
    "AI_USAGE",
    "LLM_USAGE",
    "PROJECT",
    "OWNERSHIP",
    "LEADERSHIP",
    "TEAMWORK",
    "CHALLENGE",
    "FAILURE",
    "PROBLEM_SOLVING",
    "ACHIEVEMENT",
    "WORK_EXPERIENCE",
    "HYPOTHETICAL", "NOT_ON_RESUME", "ROLE_PREFERENCE", "PRODUCT_PREFERENCE", "CONFLICT",
    "CAREER_INTEREST", "PROFILE_SUMMARY",
}


def country_name(value):
    value = normalize(value or "")
    aliases = {
        "United Kingdom": {"uk", "gb", "gbr", "united kingdom", "great britain"},
        "United States": {"us", "usa", "united states", "united states of america"},
        "Spain": {"es", "esp", "spain", "espana"},
        "France": {"fr", "fra", "france"},
        "Germany": {"de", "deu", "germany"},
        "Canada": {"ca", "can", "canada"},
    }
    return next((country for country, names in aliases.items() if value in names), value.title() if value else None)


def target_country(job, question=""):
    """Use the vacancy's jurisdiction before generic visa examples or residency."""
    if job.get("country"):
        return country_name(job["country"])
    location = normalize(str(job.get("location") or ""))
    for pattern, country in (
        (r"\blondon|\bunited kingdom\b|\buk\b|\bengland\b|\bscotland\b|\bwales\b", "United Kingdom"),
        (r"\bunited states\b|\busa\b|\bnew york\b|\bsan francisco\b", "United States"),
        (r"\bspain\b|\bmadrid\b|\bbarcelona\b", "Spain"),
        (r"\bfrance\b|\bparis\b", "France"),
        (r"\bgermany\b|\bberlin\b|\bmunich\b", "Germany"),
        (r"\bcanada\b|\btoronto\b|\bvancouver\b", "Canada"),
    ):
        if re.search(pattern, location):
            return country
    # A country explicitly named as the place of work is useful even when job
    # metadata is absent. H-1B etc. are examples, never jurisdiction evidence.
    text = normalize(question)
    match = re.search(r"(?:work|employment|employed) (?:legally )?(?:in|within) (?:the )?(united kingdom|uk|united states|usa|spain|france|germany|canada)\b", text)
    if match:
        return country_name(match[1])
    outside_examples = normalize(re.sub(r"\([^)]*\)", "", question))
    named = re.findall(r"\b(united kingdom|uk|united states|usa|spain|france|germany|canada)\b", outside_examples)
    return country_name(named[0]) if len(set(named)) == 1 else None


def val(record, key, default=None):
    return record.get("fields", {}).get(key, {}).get("value", default)


class CandidateEngine:
    def __init__(self, db, *, as_of=None, settings=None, answer_llm=None, allow_extractive_narratives=False):
        self.db, self.as_of, self.settings = db, as_of, settings or {}
        self.knowledge = Knowledge(db, as_of)
        self.semantic_cache = {}
        self.answer_llm = answer_llm
        self.allow_extractive_narratives = allow_extractive_narratives

    @property
    def enabled(self):
        return bool(self.knowledge.entities)

    def reload(self):
        self.knowledge = Knowledge(self.db, self.as_of)

    def confirmed_fallback(self, question, result, facts, policies):
        """Use deliberate scoped answers only when structured evidence is absent.

        An edited/cleared/conflicting typed value must never revive an older flat
        value. Nor may question memory bypass a known form-context contradiction.
        """
        if result["answer"] is not None or result["leave_blank"]:
            return result
        reason = result.get("root_cause") or ""
        if not (reason.startswith("MISSING_") or reason == "EXPLICIT_CONFIRMATION_REQUIRED"):
            return result
        semantic = result.get("semantic", {})
        intent = result["canonical_intent"]
        # Legacy question-memory does not encode school level or legal
        # jurisdiction. It cannot override these precise missing concepts.
        if (semantic.get("education_level") in {"primary", "secondary"} and intent in {"EDUCATION", "DEGREE", "GRADUATION"}) or intent in {"WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "ROLE_CONFIRMATION"}:
            return result
        concept = result.get("disclosure_concept") or (
            semantic.get("attribute") if intent in {"IDENTITY", "CONTACT", "ADDRESS", "LOCATION"} else intent.lower()
        )
        if any(
            f["concept"] == concept and self.knowledge.entities[f["entity_id"]]["kind"] == "person"
            for f in self.knowledge.rows
        ):
            return result
        from ..automation.answers import resolve_answer

        explicit_disclosure = result.get("explicit_disclosure")
        memory_question = result.get("disclosure_memory_key") or question
        confirmed = resolve_answer(
            memory_question, [f for f in facts if f.get("profile_explicit")], policies={} if explicit_disclosure else policies
        )
        return (
            {**confirmed, "question": question, "canonical_intent": intent}
            if confirmed["answer"] is not None
            and confirmed["verified"]
            and (not explicit_disclosure or confirmed.get("fact_ids"))
            else result
        )

    def unknown(self, question, semantic, reason="MISSING_FACT"):
        return {
            "question": question,
            "kind": classify_question(question),
            "canonical_intent": semantic["intent"],
            "semantic": semantic,
            "answer": None,
            "form_value": None,
            "answer_type": semantic["output"],
            "requested_format": semantic.get("requested_format") or infer_format(question, {}),
            "confidence": 0,
            "confidence_level": "UNKNOWN",
            "fact_ids": [],
            "facts_used": [],
            "rules_used": [],
            "verified": False,
            "inferred": False,
            "leave_blank": False,
            "requires_review": True,
            "requires_confirmation": True,
            "reason": reason,
            "reasoning_summary": reason,
            "root_cause": reason,
            "sensitivity": "personal"
            if semantic["intent"] in {"VISA", "SPONSORSHIP", "WORK_AUTHORIZATION", "DEMOGRAPHIC"}
            else "professional",
        }

    def answer_question(self, question, form_context=None, job_context=None, *, semantic=None):
        field, job = form_context or {}, dict(job_context or {})
        s = semantic or interpret(question, field)
        s = {**s, "requested_format": infer_format(question, field)}
        country = target_country(job, question)
        if country:
            job["country"] = country
        policy = self.knowledge.get_policy("personal_disclosures", job)
        disclosure = disclosure_resolution(question, field, {"personal_disclosures": policy["value"]} if policy else {})
        if disclosure:
            return {
                **self.unknown(question, s),
                **disclosure,
                "confidence_level": "POLICY",
                "requires_confirmation": False,
                "rules_used": ["policy:" + policy["id"] + ":" + str(policy["revision"])],
                "reasoning_summary": disclosure["reason"],
            }
        if (
            policy
            and disclosure_action(policy["value"]) == "use_confirmed"
            and personal_disclosure_kind(question, field)
        ):
            concept = disclosure_profile_key(question, field)
            semantic = {**s, "intent": "DEMOGRAPHIC", "attribute": concept or "disclosure_detail"}
            out = self.unknown(question, semantic, "MISSING_CONFIRMED_DISCLOSURE")
            out.update(disclosure_concept=concept, explicit_disclosure=True, sensitivity="personal",
                       disclosure_memory_key=disclosure_memory_key(question, field))
            people = self.knowledge.records("person")
            fact = people[0]["fields"].get(concept) if len(people) == 1 and concept else None
            if not fact:
                return out
            value, error = adapt(fact["value"], semantic, field)
            rules = [
                "explicit_personal_disclosure:" + str(concept),
                "policy:" + policy["id"] + ":" + str(policy["revision"]),
            ]
            return {
                **out,
                "answer": value,
                "form_value": value,
                "fact_ids": [fact["id"]],
                "facts_used": [fact["id"]],
                "rules_used": rules,
                "verified": not error,
                "confidence": 1 if not error else 0,
                "confidence_level": "EXACT" if not error else "UNKNOWN",
                "requires_review": bool(error),
                "requires_confirmation": bool(error),
                "reason": error or "Your explicitly confirmed personal-disclosure answer",
                "reasoning_summary": error
                or "Uses only your confirmed "
                + str(concept).replace("_", " ")
                + "; no related personal attributes are inferred.",
                "root_cause": error,
            }
        # Compose independently requested concepts; conjunctions inside degree
        # names or project descriptions are not treated as separate questions.
        if (
            semantic is None
            and not field.get("options")
            and field.get("type") not in {"number", "date", "month", "checkbox", "radio"}
        ):
            parts = re.split(r"\s+(?:and also|and|;)\s+", question, flags=re.I)
            parsed = [interpret(part, field) for part in parts]
            signatures = {(p["intent"], p["attribute"], p["selector"]) for p in parsed}
            cohesive_narrative = s["intent"] in NARRATIVE_INTENTS and all(
                p["intent"] in NARRATIVE_INTENTS for p in parsed
            )
            if (
                len(parts) > 1
                and len(signatures) > 1
                and all(p["intent"] != "OTHER" for p in parsed)
                and not cohesive_narrative
            ):
                components = [self.answer_question(part, field, job, semantic=p) for part, p in zip(parts, parsed)]
                base = self.unknown(question, parsed[0], "MULTI_INTENT_REQUIRES_MORE_EVIDENCE")
                base["canonical_intents"] = [p["intent"] for p in parsed]
                base["components"] = components
                if all(c["answer"] is not None for c in components):
                    text = "; ".join(dict.fromkeys(c["answer"] for c in components))
                    if not field.get("max_length") or len(text) <= field["max_length"]:
                        base.update(
                            answer=text,
                            form_value=text,
                            confidence=0.9,
                            confidence_level="GENERATED"
                            if any(c["confidence_level"] == "GENERATED" for c in components)
                            else "DERIVED_HIGH",
                            inferred=True,
                            root_cause=None,
                            fact_ids=list(dict.fromkeys(f for c in components for f in c["fact_ids"])),
                            facts_used=list(dict.fromkeys(f for c in components for f in c["facts_used"])),
                            rules_used=["compose_independent_intents"],
                            reason="Each component resolved independently from evidence",
                        )
                return base
        out = self.unknown(question, s)
        out["requested_format"] = infer_format(question, field)
        k = self.knowledge
        used = []
        rules = []
        explanations = []
        confidence = "EXACT"

        def read(record, key, default=None):
            f = record.get("fields", {}).get(key)
            if f:
                used.append(f)
                return f["value"]
            return default

        def person(key):
            records = k.records("person")
            return read(records[0], key) if len(records) == 1 else None

        def use_policy(subject):
            p = k.get_policy(subject, job)
            if p:
                rules.append("policy:" + p["id"] + ":" + str(p["revision"]))
                explanations.append("Configured policy: " + subject)
                return p["value"]
            return None

        def education(selector):
            def record_level(record):
                return education_level(val(record, "education_level", "")) or education_level(val(record, "level", ""))

            rows = k.records("education")
            if selector in {"primary", "secondary", "bachelor", "master", "doctorate"}:
                rows = [e for e in rows if record_level(e) == selector]
            elif selector == "latest":
                rows = [e for e in rows if val(e, "start_date") and val(e, "start_date")[:7] <= k.today.isoformat()[:7]]
            elif selector == "completed":
                rows = [e for e in rows if val(e, "status") == "completed"]
            else:
                rows = [
                    e
                    for e in rows
                    if val(e, "start_date")
                    and val(e, "end_date")
                    and val(e, "start_date")[:7] <= k.today.isoformat()[:7] <= val(e, "end_date")[:7]
                ]
            # Completion and level are independent constraints. A completed
            # master's question cannot use an ongoing master's record.
            if s.get("education_level") and selector == "completed":
                rows = [e for e in rows if record_level(e) == s["education_level"]]
            rows.sort(key=lambda e: (val(e, "rank", 0), val(e, "end_date", "")), reverse=True)
            if not rows:
                return None
            e = rows[0]
            for key in ("status", "start_date", "end_date", "rank", "level", "education_level"):
                read(e, key)
            return e

        intent, text = s["intent"], s["text"]
        employer_request = (
            intent == "WORK_EXPERIENCE"
            and re.search(r"employer|company", text)
            and not re.search(r"describe|experience|background|responsibilities|achiev|learn", text)
        )
        answer = None
        if intent in {"IDENTITY", "CONTACT", "ADDRESS", "NATIONALITY", "LINKEDIN", "GITHUB", "PORTFOLIO"}:
            key = s["attribute"] if intent in {"IDENTITY", "CONTACT", "ADDRESS"} else intent.lower()
            if key == "age":
                birth = person("date_of_birth")
                if birth:
                    b = date.fromisoformat(birth)
                    answer = k.today.year - b.year - ((k.today.month, k.today.day) < (b.month, b.day))
                    confidence = "DERIVED_HIGH"
                    rules.append("age_on_date")
            else:
                if intent == "NATIONALITY" and "country" in s["text"]:
                    key = "nationality_country"
                answer = person(key)
        elif intent == "LOCATION":
            answer = person(s["attribute"])
            if answer and s["attribute"] == "city" and field.get("role") == "combobox" and not field.get("options"):
                country = person("country")
                if country:
                    answer = f"{answer}, {country}"
                    rules.append("geocoder_city_country_disambiguation")
            if s["boolean"]:
                city = person("city")
                country = person("country")
                mentioned = [x for x in (city, country) if x and normalize(x) in text]
                answer = True if mentioned else None
        elif intent == "NAME_PRONUNCIATION":
            answer = person("name_pronunciation")
            if answer is None:
                out.update(reason="MISSING_NAME_PRONUNCIATION", root_cause="MISSING_NAME_PRONUNCIATION")
        elif intent == "ROLE_CONFIRMATION":
            related = field.get("_related_answers") or job.get("related_answers") or []
            preferences = [row for row in related if isinstance(row, dict) and (row.get("canonical_intent") or row.get("intent")) == "ROLE_PREFERENCE" and row.get("answer")
                           and not row.get("root_cause") and (row.get("quality", {}).get("passed") is True or row.get("verified") is True)]
            selections = list(dict.fromkeys(str(value.get("label") or value.get("value")) if isinstance(value, dict) else str(value)
                                           for row in preferences for value in (row.get("selected_roles") or row.get("selected_options") or [])))
            applied_role = job.get("application_role") or job.get("role") or job.get("title")

            def role_identity(role):
                # Seniority and graduate qualifiers are different from the
                # occupational role. Never strip e.g. forward deployed/backend.
                value = normalize(str(role or "").split(",", 1)[0])
                return re.sub(r"\b(?:new grad|graduate|junior|senior|entry level|internship|intern)\b", "", value).strip()

            if len(selections) == 1 and applied_role and role_identity(selections[0]) == role_identity(applied_role):
                answer = True
                rules.append("structured_role_preference_matches_actual_vacancy")
                out["related_question_ids"] = [row.get("question_id") for row in preferences]
                confidence = "DERIVED_HIGH"
            else:
                missing = "ROLE_PREFERENCE_MISMATCH" if selections and applied_role else "MISSING_STRUCTURED_ROLE_PREFERENCE"
                out.update(reason=missing, root_cause=missing)
        elif intent in {"GRADUATION", "DEGREE"} and s["boolean"] and re.search(r"\brecent(?:ly)?\b", text):
            e = education("completed")
            end = read(e, "end_date") if e else None
            subject = read(e, "subject") if e else None
            if end and subject:
                # A reviewable conservative interpretation, never a stored fact
                # or a claim about the employer's actual graduate eligibility.
                earliest_end = date.fromisoformat(end + "-01" if len(end) == 7 else end)
                within_year = 0 <= (k.today - earliest_end).days <= 365
                broad_stem = bool(re.search(r"\bstem\b", text))
                stem_subject = bool(
                    re.search(
                        r"computer science|computing|engineering|mathematics|statistics|physics|chemistry|biology",
                        normalize(subject),
                    )
                )
                unspecified_subject = not re.search(r"\b(?:with|in)\b.*\b(?:degree|qualification)\b", text)
                if within_year and (unspecified_subject or (broad_stem and stem_subject) or normalize(subject) in text):
                    result = self._finish_evidence_answer(
                        out,
                        "Yes",
                        used,
                        [
                            "recent_graduate:completed_within_12_months",
                            "review_assumption:employer_recency_definition_not_supplied",
                        ],
                        s,
                        field,
                    )
                    result.update(confidence_level="DERIVED_MEDIUM", confidence=0.85)
                    return result
            return self.unknown(question, s, "MISSING_RECENCY_OR_SUBJECT_EVIDENCE")
        elif intent in {"EDUCATION", "DEGREE", "MODULE", "GRADE", "GPA", "ACADEMIC_RESULT"}:
            selector = s["selector"]
            if intent in {"GRADE", "GPA", "ACADEMIC_RESULT"} and selector == "current":
                selector = "completed"
            e = education(selector)
            if e is None and selector in {"primary", "secondary"}:
                out.update(reason="MISSING_" + selector.upper() + "_INSTITUTION", root_cause="MISSING_" + selector.upper() + "_INSTITUTION")
            if (
                e
                and val(e, "status") != "completed"
                and re.search(r"completed|earned|awarded|obtained|\bdid\b.*\bcomplete\b", text)
                and not re.search(r"pursu|current|in progress", text)
            ):
                return self.unknown(question, s, "MISSING_COMPLETED_QUALIFICATION")
            if e and field.get("_education_reference"):
                reference = normalize(field["_education_reference"])
                if not any(normalize(name) == reference for name in [e["name"], *e["aliases"]]):
                    referenced = next(
                        (
                            r
                            for r in k.records("education")
                            if choice(
                                val(r, "institution", r["name"]),
                                [{"label": field["_education_reference"], "value": reference}],
                            )
                        ),
                        None,
                    )
                    if (
                        referenced
                        and selector == "completed"
                        and intent == "DEGREE"
                        and s["attribute"] == "degree"
                        and val(referenced, "status") == "studying"
                        and not s["boolean"]
                        and not field.get("options")
                        and (field.get("type") == "text" or field.get("tag") == "textarea")
                    ):
                        current_degree, current_institution = (
                            read(referenced, "degree"),
                            read(referenced, "institution"),
                        )
                        completed_degree, completed_institution = read(e, "degree"), read(e, "institution")
                        end = read(referenced, "end_date")
                        read(referenced, "status")
                        if all([current_degree, current_institution, completed_degree, completed_institution, end]):
                            answer = f"My {current_degree} at {current_institution} is in progress (expected completion {end}). My completed qualification is {completed_degree} at {completed_institution}."
                            return self._finish_evidence_answer(
                                out,
                                answer,
                                used,
                                ["clarify_in_progress_qualification_at_selected_institution"],
                                s,
                                field,
                            )
                    return self.unknown(question, s, "CONFLICT_WITH_SELECTED_INSTITUTION")
            if e:
                confidence = "DERIVED_HIGH"
                rules.append("education_selection:" + selector)
                if intent == "EDUCATION":
                    answer = (
                        True if s["boolean"] and re.search(r"student|studying|enroll", text) else read(e, "institution")
                    )
                elif intent == "MODULE":
                    answer = read(e, "modules")
                elif intent in {"GRADE", "ACADEMIC_RESULT"}:
                    answer = read(e, "classification") if re.search(r"class|honour", text) else read(e, "average")
                    if (
                        answer is not None
                        and "%" in question
                        and field.get("type") != "number"
                        and not field.get("options")
                    ):
                        answer = str(answer) + "%"
                elif intent == "GPA":
                    official = read(e, "gpa")
                    if official is not None:
                        answer = official
                    elif field.get("type") == "number":
                        conversion = use_policy("gpa_conversion")
                        average = read(e, "average")
                        if conversion and average is not None:
                            matches = [r for r in conversion.get("ranges", []) if r["min"] <= average <= r["max"]]
                            if len(matches) == 1:
                                answer = matches[0]["gpa"]
                                confidence = "DERIVED_MEDIUM"
                                explanations.append("Policy conversion; not an official university GPA")
                    else:
                        grade, average = read(e, "classification"), read(e, "average")
                        if grade and average is not None:
                            answer = f"UK {grade}; {average}%. No official US GPA."
                else:
                    answer = read(e, s["attribute"])
                    if s["boolean"]:
                        answer = True if answer else None
        elif intent in {"GRADUATION", "START_DATE", "AVAILABILITY"}:
            selector = s["selector"]
            e = education(selector)
            if intent == "GRADUATION" and e is None and selector in {"secondary", "primary"}:
                missing = "MISSING_" + selector.upper() + "_GRADUATION_DATE"
                out.update(reason=missing, root_cause=missing)
            end = read(e, "end_date") if e else None
            availability = person("earliest_full_time_start_date") if intent in {"START_DATE", "AVAILABILITY"} else None
            if intent in {"START_DATE", "AVAILABILITY"} and availability in (None, "") and any(
                fact["concept"] == "earliest_full_time_start_date" and fact["source_priority"] >= 100
                and k.entities[fact["entity_id"]]["kind"] == "person" for fact in k.rows
            ):
                return self.unknown(question, s, "MISSING_CONFIRMED_AVAILABILITY")
            availability = availability or (use_policy("anticipated_start_date") if intent in {"START_DATE", "AVAILABILITY"} else None)
            if intent in {"START_DATE", "AVAILABILITY"} and not availability:
                return self.unknown(question, s, "MISSING_ANTICIPATED_START_DATE")
            raw_date = availability or end
            if raw_date:
                try:
                    raw = str(raw_date)
                    target = date.fromisoformat(raw + "-01-01" if len(raw) == 4 else raw + "-01" if len(raw) == 7 else raw)
                except ValueError:
                    return self.unknown(question, s, "INVALID_STORED_DATE")
                confidence = "EXACT" if availability else "DERIVED_HIGH"
                rules.append("explicit_candidate_availability" if availability else "education_end_date")
                requested = requested_date(question)
                if s["boolean"]:
                    year = re.search(r"\b(20\d\d)\b", text)
                    if intent == "GRADUATION" and year:
                        answer = target.year == int(year[1])
                    elif requested:
                        proposed, precision = requested
                        if precision == "month" and target.year == proposed.year and target.month == proposed.month:
                            if field.get("type") != "checkbox" and field.get("role") not in {"radio", "combobox"}:
                                displayed = target.strftime("%d %B %Y" if len(str(raw_date)) == 10 else "%B %Y")
                                answer = f"Available from {displayed}; the exact programme start must be checked."
                            else:
                                out["reason"] = "AMBIGUOUS_QUESTION"
                        else:
                            answer = target <= proposed
                elif re.search(r"how long|time until|months until|days until", text):
                    if len(str(raw_date)) != 10:
                        return self.unknown(question, s, "MISSING_EXACT_DATE")
                    days = max(0, (target - k.today).days)
                    answer = (
                        days
                        if "days" in text or field.get("type") == "number"
                        else f"{days} days, until {target.strftime('%d %B %Y')}."
                    )
                    rules.append("date_difference:" + k.today.isoformat())
                else:
                    answer, error = format_date(raw_date, question, field)
                    if error:
                        return self.unknown(question, s, error)
        elif intent == "NOTICE_PERIOD":
            notice = person("current_employment_notice_days")
            if notice is None and person("employment_contract_active") is False:
                notice = 0
                confidence = "DERIVED_HIGH"
                rules.append("no_active_employment_contract")
            if notice is not None:
                answer = (
                    bool(notice)
                    if s["boolean"]
                    else notice
                    if field.get("type") == "number"
                    else "None"
                    if notice == 0
                    else f"{float(notice):g} days"
                )
                explanations.append("Contractual notice only; academic availability is a separate constraint")
        elif intent == "LANGUAGE":
            languages = k.records("language")
            named = [r for r in languages if normalize(r["name"]) in text]
            requested = s.get("requested_languages", [])
            unknown_languages = [name for name in requested if not any(normalize(r["name"]) == name for r in languages)]
            if unknown_languages:
                return self.unknown(question, s, "MISSING_LANGUAGE_PROFICIENCY:" + ",".join(unknown_languages))
            selected = named or languages
            if s.get("attribute") == "native_language":
                selected = [r for r in languages if val(r, "native") is True or normalize(val(r, "proficiency", "")) == "native"]
            parts = []
            for r in selected:
                level = read(r, "proficiency")
                if level:
                    parts.append(r["name"] if s["output"] == "MULTIPLE_CHOICE" or s.get("attribute") == "native_language" else level if len(named) == 1 else r["name"] + ": " + level)
            if parts:
                answer = parts if s["output"] == "MULTIPLE_CHOICE" else True if s["boolean"] and named else ", ".join(parts)
        elif intent in {"VISA", "WORK_AUTHORIZATION", "SPONSORSHIP"}:
            records = k.records("immigration")
            record = next((r for r in records if country_name(val(r, "country")) == country), None) if country else None
            if record is None and intent == "VISA" and len(records) == 1 and country is None:
                record = records[0]
            if record:
                if intent == "VISA":
                    answer = read(record, "route")
                    if re.search(r"expir|valid until", text):
                        answer = read(record, "valid_until")
                    elif (
                        answer
                        and not field.get("options")
                        and not s["boolean"]
                        and (
                            field.get("tag") == "textarea" or re.search(r"status|detail|explain|confirm|describe", text)
                        )
                    ):
                        route = str(answer)
                        visa_country = read(record, "country")
                        country_label = (
                            "the " + visa_country
                            if visa_country in {"United Kingdom", "United States"}
                            else visa_country
                        )
                        answer = (
                            f"I currently hold a {route} visa" + (f" in {country_label}" if country_label else "") + "."
                        )
                        if normalize(route) == "student":
                            current_study = education("current")
                            if current_study:
                                degree = read(current_study, "degree")
                                institution = read(current_study, "institution")
                                end = read(current_study, "end_date")
                                if degree and institution:
                                    answer += f" I am studying for an {degree} at {institution}"
                                    if end:
                                        end_date = date.fromisoformat(end + "-01" if len(end) == 7 else end)
                                        answer += f", with my course expected to finish in {end_date.strftime('%B %Y')}"
                                    answer += "."
                        rules.append("visa_status_with_verified_study_context")
                        confidence = "DERIVED_HIGH"
                else:
                    key = (
                        {"permanent": "permanent_unrestricted", "full_time": "full_time_right_to_work", "future": "future_right_to_work"}.get(s["qualifier"], "current_right_to_work")
                        if intent == "WORK_AUTHORIZATION"
                        else "sponsorship_" + ("now_or_future" if s["qualifier"] == "unspecified" else s["qualifier"])
                    )
                    # Legal determinations require a current explicit fact with its evidence.
                    answer = read(record, key)
                    if answer is None:
                        out["reason"] = out["root_cause"] = "MISSING_CURRENT_IMMIGRATION_EVIDENCE"
            if answer is None and country and intent != "VISA":
                policy_subject = (
                    "work_authorization_" + (s["qualifier"] if s["qualifier"] in {"permanent", "full_time", "future"} else "current")
                ) if intent == "WORK_AUTHORIZATION" else "sponsorship_" + ("now_or_future" if s["qualifier"] == "unspecified" else s["qualifier"])
                scoped = k.get_policy(policy_subject, job)
                # An unscoped immigration policy must never leak across countries.
                if scoped and scoped["scope"].get("country"):
                    answer = use_policy(policy_subject)
                    confidence = "POLICY"
            if answer is None:
                missing = "MISSING_TARGET_WORK_COUNTRY" if not country else "MISSING_" + intent + "_EVIDENCE:" + country
                out.update(reason=missing, root_cause=missing, missing_fact={"concept": intent.lower(), "country": country, "qualifier": s["qualifier"]})
            out["sensitivity"] = "immigration"
        elif intent == "DEMOGRAPHIC":
            p = use_policy("optional_demographics")
            if p == "decline_or_blank":
                opt = choice("Prefer not to say", field.get("options", []))
                if opt:
                    answer = opt["label"]
                elif not field.get("required"):
                    out["leave_blank"] = True
                    out["reason"] = "Configured policy: optional disclosure left blank"
                confidence = "POLICY"
        elif intent == "REASONABLE_ADJUSTMENTS":
            p = use_policy("adjustments_request")
            if p == "none_requested" and not field.get("required"):
                answer = (
                    False
                    if s["boolean"] and (field.get("options") or field.get("role") in {"radio", "checkbox"})
                    else "No adjustments requested"
                )
                confidence = "POLICY"
        elif intent == "CONSENT":
            answer = use_policy("consent_" + s["attribute"])
            if answer is None:
                p = use_policy("candidate_consent")
                answer = p.get(s["attribute"]) if isinstance(p, dict) else None
            if answer is None:
                out.update(reason="EXPLICIT_CONFIRMATION_REQUIRED", root_cause="EXPLICIT_CONFIRMATION_REQUIRED",
                           missing_fact={"concept": "consent_" + s["attribute"]})
            else:
                confidence = "POLICY"
        elif intent == "OFFER_DEADLINE":
            deadlines = person("upcoming_offer_deadlines")
            anticipated = person("anticipated_offer_deadline")
            p = use_policy("employment_offer_deadlines")
            if s["boolean"]:
                if re.search(r"anticipat|expect", text):
                    answer = True if deadlines or anticipated is True else False if deadlines is not None and anticipated is False else p if isinstance(p, bool) else None
                else:
                    answer = bool(deadlines) if deadlines is not None else p if isinstance(p, bool) else None
            elif deadlines:
                answer = deadlines
            elif p is not None and not isinstance(p, bool):
                answer = p
            if answer is None:
                out.update(reason="MISSING_EMPLOYMENT_OFFER_DEADLINES", root_cause="MISSING_EMPLOYMENT_OFFER_DEADLINES")
        elif intent in {
            "GDPR",
            "CRIMINAL_HISTORY",
            "SECURITY_CLEARANCE",
            "BACKGROUND_CHECK",
            "CONFLICT_OF_INTEREST",
            "ROLE_CONFIRMATION",
        }:
            out["root_cause"] = out["reason"] = "EXPLICIT_CONFIRMATION_REQUIRED"
        elif intent == "SALARY":
            p = use_policy("salary_" + s["qualifier"])
            if p is not None:
                answer = p.get("target") if isinstance(p, dict) else p
                confidence = "POLICY"
        elif intent in {"RELOCATION", "OFFICE_POLICY", "REMOTE_WORK", "TRAVEL"}:
            city = person("city")
            if intent == "RELOCATION" and city and normalize(city) in text:
                answer = True if s["boolean"] and field.get("tag") != "textarea" else "Already based in " + city
            else:
                p = use_policy(intent.lower())
                if p is not None:
                    answer = p
                    confidence = "POLICY"
        elif intent == "SOURCE_OF_APPLICATION":
            provenance = job.get("discovery_provenance") or {}
            if not isinstance(provenance, dict):
                provenance = {}
            answer = provenance.get("source") or provenance.get("source_name") or job.get("source")
            if answer and answer not in {"manual", "unknown"}:
                rules.append("job_discovery_provenance:" + str(job.get("id", "")))
                confidence = "DERIVED_HIGH"
                options = field.get("options", [])
                if options and not choice(answer, options):
                    # A factual broad category can represent a named board. The
                    # presence of a referral option never establishes a referral.
                    source = normalize(answer)
                    categories = (
                        (r"linkedin", "LinkedIn"), (r"indeed", "Indeed"), (r"github", "GitHub"),
                        (r"google|search engine", "Search engine"), (r"university|careers service", "University careers service"),
                        (r"company careers|company website", "Company website"), (r"recruiter", "Recruiter"),
                    )
                    broad = next((label for pattern, label in categories if re.search(pattern, source)), None)
                    match = choice(broad, options) if broad else None
                    match = match or choice("Other", options)
                    if match:
                        out["discovery_source"] = answer
                        answer = match["label"]
                        rules.append("accurate_discovery_source_category")
            else:
                answer = None
        elif intent == "EMPLOYEE_REFERRAL":
            referral = job.get("referrer")
            answer = referral or (False if job.get("referral_confirmed_absent") else None)
            if answer is not None:
                rules.append("application_referral_provenance")
        elif intent == "PREVIOUS_EMPLOYMENT":
            employer = normalize(job.get("company", ""))
            for r in k.records("experience"):
                aliases = [normalize(r["name"]), *[normalize(x) for x in r["aliases"]]]
                if employer and employer in aliases:
                    answer = True
                    read(r, "role")
                    rules.append("employment_entity_match")
                    break
            if answer is None and person("employment_history_complete") is True:
                answer = False
        elif employer_request:
            rows = k.records("experience")
            current = s["selector"] == "current"
            if current:
                explicit = person("current_company")
                if explicit is not None:
                    if explicit == "":
                        if not field.get("required"):
                            return {**out, "leave_blank": True, "root_cause": None,
                                    "reason": "Explicit candidate profile leaves optional current employer blank",
                                    "fact_ids": [fact["id"] for fact in used], "facts_used": [fact["id"] for fact in used],
                                    "confidence_level": "EXACT", "confidence": 1,
                                    "verified": True,
                                    "requires_review": False, "requires_confirmation": False}
                        return {**out, "root_cause": "MISSING_CURRENT_EMPLOYER", "reason": "MISSING_CURRENT_EMPLOYER"}
                    return self._finish_evidence_answer(out, explicit, used, ["explicit_current_employer"], s, field)
            rows = [r for r in rows if (val(r, "ongoing") is True if current else val(r, "end_date") is not None)]
            rows.sort(key=lambda r: val(r, "end_date", ""), reverse=True)
            if rows:
                for key in ("role", "start_date", "end_date", "ongoing"):
                    read(rows[0], key)
                answer = rows[0]["name"]
                rules.append("employment_selection:" + s["selector"])
                confidence = "DERIVED_HIGH"
        elif intent in {
            "SKILL",
            "SKILL_DURATION",
            "PROGRAMMING_LANGUAGE",
            "FRAMEWORK",
            "CLOUD",
            "DATA",
            "MACHINE_LEARNING",
            "SOFTWARE_ENGINEERING",
            "PROJECT",
        }:
            if (
                intent == "PROJECT"
                and not s["boolean"]
                and not re.search(r"(?:largest|biggest|maximum).*data\s?set", text)
            ):
                answer = self.narrative(s, job, used, rules, out)
                if answer is not None:
                    confidence = "GENERATED"
            if re.search(r"(?:largest|biggest|maximum).*data\s?set", text):
                datasets = [
                    r
                    for kind in ("experience", "project")
                    for r in k.records(kind)
                    if val(r, "dataset_size") is not None
                ]
                if datasets:
                    largest = max(datasets, key=lambda r: val(r, "dataset_size"))
                    answer = read(largest, "dataset_size")
                    rules.append("largest_verified_dataset")
                    confidence = "DERIVED_HIGH"
            skill = self.find_skill(text) if intent != "PROJECT" or s["boolean"] or re.search(r"(?:largest|biggest|maximum).*data\s?set", text) else None
            if skill and answer is None:
                if intent == "PROJECT" and s["boolean"]:
                    project_evidence = [edge for edge in k.links if edge["subject_id"] == skill["id"] and edge["predicate"] == "used_in"
                                        and k.entities.get(edge["object_id"], {}).get("kind") == "project"
                                        and any(fact["id"] in edge["fact_ids"] and k.active(fact) for fact in k.rows)]
                    if not project_evidence:
                        return self.unknown(question, s, "MISSING_VERIFIED_PROJECT_SKILL_EVIDENCE")
                evidence = self.skill_exposure(skill)
                used.extend(evidence.pop("facts"))
                if intent == "SKILL_DURATION":
                    bucket = (
                        "professional"
                        if "professional" in text or "commercial" in text
                        else "academic"
                        if "academic" in text
                        else "project"
                        if "project" in text
                        else "calendar"
                    )
                    metric = evidence[bucket]
                    if metric["intervals"]:
                        answer = (
                            metric["months"]
                            if "month" in text
                            else metric["integer_years"]
                            if field.get("type") == "number"
                            else f"{metric['years']} years of evidenced {bucket} exposure (overlapping periods counted once)."
                        )
                        out["calculation"] = evidence
                        confidence = "DERIVED_HIGH"
                        rules.append("union_of_dated_skill_evidence")
                elif evidence["evidence_count"]:
                    answer = True if s["boolean"] else skill["name"]
                    confidence = "DERIVED_HIGH"
                    rules.append("skill_evidence_exists")
            if answer is None and intent == "PROJECT":
                answer = self.narrative(s, job, used, rules, out)
                if answer is not None:
                    confidence = "GENERATED"
        if employer_request:
            return out if answer is None else self._finish_evidence_answer(out, answer, used, rules, s, field)
        if (
            intent
            in {
                "PROJECT",
                "OWNERSHIP",
                "LEADERSHIP",
                "TEAMWORK",
                "CHALLENGE",
                "FAILURE",
                "PROBLEM_SOLVING",
                "ACHIEVEMENT",
                "MOTIVATION",
                "COMPANY_INTEREST",
                "ROLE_INTEREST",
                "CAREER_GOALS",
                "ADDITIONAL_CONTEXT",
                "COVER_LETTER",
                "AI_USAGE",
                "LLM_USAGE",
                "WORK_EXPERIENCE",
            }
            and answer is None
        ):
            if intent == "LEADERSHIP" and re.search(r"largest|how many|number|size|more than|at least", text):
                rows = [r for r in k.records("experience") if val(r, "team_size") is not None]
                if rows:
                    r = max(rows, key=lambda r: val(r, "team_size"))
                    n = read(r, "team_size")
                    answer = n
                    confidence = "DERIVED_HIGH"
                    rules.append("largest_verified_team")
                    threshold = re.search(r"(?:more than|over|at least|greater than)\s*(\d+)", text)
                    if threshold:
                        answer = n >= int(threshold[1]) if "at least" in text else n > int(threshold[1])
            else:
                answer = self.narrative(s, job, used, rules, out)
            if answer is not None and not isinstance(answer, (int, bool)):
                confidence = "GENERATED"
        if answer is None:
            if intent in NARRATIVE_INTENTS and not s["boolean"] and not employer_request:
                out.update(root_cause="ASYNC_GENERATION_REQUIRED" if self.answer_llm else "LLM_SETUP_REQUIRED",
                           reason="A question-specific generative answer is required", state="generation_required" if self.answer_llm else "setup_required")
            return out
        formatted, error = adapt(answer, s, field)
        if error:
            return {
                **out,
                "root_cause": error,
                "reason": error,
                "candidate_value": answer,
                "fact_ids": list(dict.fromkeys(f["id"] for f in used)),
            }
        ids = list(dict.fromkeys(f["id"] for f in used))
        reason = "; ".join(explanations + rules) or "Direct verified candidate fact"
        result = {
            **out,
            "answer": ", ".join(formatted) if isinstance(formatted, list) else formatted,
            "form_value": formatted,
            "confidence": 1 if confidence in {"EXACT", "POLICY"} else 0.96 if confidence == "DERIVED_HIGH" else 0.85,
            "confidence_level": confidence,
            "verified": confidence in {"EXACT", "POLICY"},
            "inferred": confidence not in {"EXACT", "POLICY"},
            "fact_ids": ids,
            "facts_used": ids,
            "rules_used": rules,
            "reason": reason,
            "reasoning_summary": reason,
            "requires_confirmation": confidence not in {"EXACT", "POLICY"},
            "requires_review": confidence not in {"EXACT", "POLICY"},
            "root_cause": None,
        }
        if isinstance(formatted, list):
            result["selected_options"] = [choice(label, field.get("options", []), question=question) for label in formatted]
            result["answer_type"] = "MULTIPLE_CHOICE"
        result["claims"] = [
            {"text": formatted, "facts": ids, "rules": rules, "construction": "deterministic evidence composition"}
        ]
        return result

    def _finish_evidence_answer(self, out, answer, used, rules, s, field):
        value, error = adapt(answer, s, field)
        ids = list(dict.fromkeys(f["id"] for f in used))
        return {
            **out,
            "answer": value,
            "form_value": value,
            "fact_ids": ids,
            "facts_used": ids,
            "rules_used": rules,
            "confidence_level": "DERIVED_HIGH",
            "confidence": 0.96,
            "inferred": True,
            "root_cause": error,
            "reason": "; ".join(rules),
            "reasoning_summary": "; ".join(rules),
            "claims": [
                {"text": value, "facts": ids, "rules": rules, "construction": "deterministic evidence composition"}
            ]
            if value
            else [],
        }

    def find_skill(self, text):
        candidates = []
        for skill in self.knowledge.records("skill"):
            names = [skill["name"], *skill["aliases"]]
            if any(re.search(r"\b" + re.escape(normalize(name)) + r"\b", text) for name in names):
                candidates.append(skill)
        return max(candidates, key=lambda s: len(s["name"])) if candidates else None

    def skill_exposure(self, skill):
        k = self.knowledge
        intervals = {x: [] for x in ("professional", "academic", "project")}
        facts = []
        count = 0
        for link in k.links:
            if link["subject_id"] != skill["id"] or link["predicate"] != "used_in":
                continue
            e = k.entities.get(link["object_id"])
            if not e:
                continue
            supporting = [f for f in k.rows if f["id"] in link["fact_ids"] and k.active(f)]
            if not supporting:
                continue
            count += 1
            facts.extend(supporting)
            record = next(r for r in k.records(e["kind"]) if r["id"] == e["id"])
            for key in ("start_date", "end_date", "ongoing"):
                if key in record["fields"]:
                    facts.append(record["fields"][key])
            bucket = {"experience": "professional", "education": "academic", "project": "project"}.get(
                e["kind"], "project"
            )
            intervals[bucket].append(
                interval(val(record, "start_date"), val(record, "end_date"), k.today, val(record, "ongoing", False))
            )
        return {
            **{b: exposure(v) for b, v in intervals.items()},
            "calendar": exposure(sum(intervals.values(), [])),
            "facts": facts,
            "evidence_count": count,
        }

    def narrative(self, s, job, used, rules, metadata=None):
        if not self.allow_extractive_narratives:
            return None
        result = compose_narrative(self.knowledge, s, job)
        used.extend(result["facts"])
        rules.extend(result["rules"])
        if metadata is not None and result["answer"]:
            metadata["narrative_context"] = result["context"]
            metadata["job_context_sources"] = job.get("context_sources", [])
        return result["answer"]

    async def answer(self, question, field=None, job=None, *, audit=True):
        field, job = field or {}, job or {}
        self.reload()  # no stale relative facts or policy snapshots across requests
        result = self.answer_question(question, field, job)
        if result["canonical_intent"] == "OTHER":
            cache_key = dumps([question, field])
            if cache_key not in self.semantic_cache:
                self.semantic_cache[cache_key] = await semantic_interpret(question, field, self.settings)
            parsed = self.semantic_cache[cache_key]
            if parsed:
                result = self.answer_question(question, field, job, semantic=parsed)
        semantic = result.get("semantic", interpret(question, field))
        intent = result["canonical_intent"]
        # Explanations, preferences and unusual free-text questions always go
        # through a capable generative provider. Extractive project prose is a
        # diagnostics facility, never a production fallback for these fields.
        narrative = intent in NARRATIVE_INTENTS and not (
            semantic.get("boolean") and intent in {"PROJECT", "WORK_EXPERIENCE"}
        )
        if intent == "WORK_EXPERIENCE" and re.search(r"employer|company", semantic["text"]) and not re.search(
            r"describe|experience|background|responsibilities|achiev|learn", semantic["text"]
        ):
            narrative = False
        if intent == "LEADERSHIP" and result.get("answer") is not None and re.search(
            r"largest|how many|number|size|more than|at least", semantic["text"]
        ):
            narrative = False
        if intent == "OTHER" and field.get("type") not in {"number", "date", "month", "file", "checkbox"}:
            narrative = True
        if narrative:
            base = self.unknown(question, semantic, "LLM_SETUP_REQUIRED")
            base["requested_format"] = infer_format(question, field)
            if self.answer_llm is None:
                base.update(state="setup_required", reason="An LLM is required to generate personalised answers to open-ended application questions.",
                            missing_fact={"concept": "application_answer_llm_configuration"})
                result = base
            else:
                try:
                    generated = await self.answer_llm.answer(
                        question, field, job, self.knowledge, semantic, settings=self.settings
                    )
                    value = generated.get("answer")
                    if generated.get("selected_options") and field.get("options"):
                        value = generated["selected_options"] if semantic["output"] == "MULTIPLE_CHOICE" else generated["selected_options"][0]
                    if value is None:
                        result = {**base, **generated}
                    else:
                        adapted, error = adapt(value, semantic, field)
                        ids = generated.get("facts_used", generated.get("fact_ids", []))
                        result = {
                            **base, **generated, "question": question, "semantic": semantic,
                            "canonical_intent": intent, "answer": None if error else ", ".join(adapted) if isinstance(adapted, list) else adapted,
                            "form_value": adapted, "fact_ids": ids, "facts_used": ids,
                            "answer_type": semantic["output"], "confidence_level": "GENERATED",
                            "verified": False, "inferred": True, "requires_review": True,
                            "requires_confirmation": False, "root_cause": error,
                            "reason": error or "Question-specific generation from cited verified candidate evidence",
                        }
                        if isinstance(adapted, list):
                            result["selected_options"] = [choice(label, field.get("options", []), question=question) for label in adapted]
                except (ValueError, RuntimeError) as error:
                    code = getattr(error, "code", None) or getattr(error, "root_cause", None) or type(error).__name__
                    issues = getattr(error, "issues", [])
                    factual_issue = next((str(issue) for issue in issues if str(issue).startswith("MISSING_CANDIDATE_FACT")), None)
                    if factual_issue:
                        code = "MISSING_CANDIDATE_FACT"
                    result = {**base, "root_cause": code, "reason": str(error), "state": "setup_required" if "CONFIGURATION" in str(code) else "failed",
                              "validation_issues": issues, "missing_facts": [factual_issue] if factual_issue else generated.get("missing_facts", []) if "generated" in locals() else []}
        # An uncertain classifier may interpret an unknown concept or refine
        # an unmappable enum. It cannot replace an already grounded resolution
        # or evade a known missing fact/contradiction by switching concepts.
        protected = {
            "CONSENT",
            "GDPR",
            "DEMOGRAPHIC",
            "WORK_AUTHORIZATION",
            "SPONSORSHIP",
            "VISA",
            "CRIMINAL_HISTORY",
            "SECURITY_CLEARANCE",
            "BACKGROUND_CHECK",
            "CONFLICT_OF_INTEREST",
            "ROLE_CONFIRMATION",
        }
        if not narrative and result["canonical_intent"] not in protected and (
            result["canonical_intent"] == "OTHER" or result.get("root_cause") == "BAD_ENUM_MAPPING"
        ):
            key = dumps(
                [
                    question,
                    field.get("description"),
                    field.get("section_label") or field.get("section"),
                    field.get("type"),
                    field.get("options"),
                ]
            )
            if key not in self.semantic_cache:
                self.semantic_cache[key] = await semantic_interpret(question, field, self.settings)
            semantic = self.semantic_cache[key]
            if semantic and (result["canonical_intent"] == "OTHER" or semantic["intent"] == result["canonical_intent"]):
                result = self.answer_question(question, field, job, semantic=semantic)
        if result.get("semantic", {}).get("parser") == "local_semantic" and result.get("answer") is not None:
            result.update(
                verified=False,
                inferred=True,
                requires_confirmation=True,
                requires_review=True,
                confidence_level="DERIVED_MEDIUM",
                confidence=0.85,
            )
        if audit:
            digest = hashlib.sha256(dumps([self.knowledge.version, question, field, job]).encode()).hexdigest()
            self.db.execute(
                "INSERT INTO answer_provenance VALUES(?,?,?,?,?,?)",
                (uid(), job.get("application_id"), question, digest, dumps(result), now()),
            )
        return result
