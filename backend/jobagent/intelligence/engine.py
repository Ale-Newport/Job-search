from __future__ import annotations

from datetime import date, timedelta
import hashlib
import re

from ..db import dumps, now, uid
from ..automation.answers import classify_question, normalize
from .store import Knowledge
from .semantics import interpret, semantic_interpret
from .temporal import interval, exposure, requested_date
from .formatting import adapt, choice


def val(record, key, default=None):
    return record.get("fields", {}).get(key, {}).get("value", default)


class CandidateEngine:
    def __init__(self, db, *, as_of=None, settings=None):
        self.db, self.as_of, self.settings = db, as_of, settings or {}
        self.knowledge = Knowledge(db, as_of)
        self.semantic_cache = {}

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
        concept = (
            semantic.get("attribute") if intent in {"IDENTITY", "CONTACT", "ADDRESS", "LOCATION"} else intent.lower()
        )
        if any(
            f["concept"] == concept and self.knowledge.entities[f["entity_id"]]["kind"] == "person"
            for f in self.knowledge.rows
        ):
            return result
        from ..automation.answers import resolve_answer

        confirmed = resolve_answer(question, [f for f in facts if f.get("profile_explicit")], policies=policies)
        return (
            {**confirmed, "canonical_intent": intent}
            if confirmed["answer"] is not None and confirmed["verified"]
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
        if not job.get("country") and re.search(
            r"\b(?:london|united kingdom|uk|england|scotland|wales)\b", normalize(job.get("location", ""))
        ):
            job["country"] = "United Kingdom"

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
            if len(parts) > 1 and len(signatures) > 1 and all(p["intent"] != "OTHER" for p in parsed):
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
                            confidence_level="DERIVED_HIGH",
                            inferred=True,
                            root_cause=None,
                            fact_ids=list(dict.fromkeys(f for c in components for f in c["fact_ids"])),
                            facts_used=list(dict.fromkeys(f for c in components for f in c["facts_used"])),
                            rules_used=["compose_independent_intents"],
                            reason="Each component resolved independently from evidence",
                        )
                return base
        s = semantic or interpret(question, field)
        out = self.unknown(question, s)
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
            rows = k.records("education")
            if selector in {"bachelor", "master"}:
                rows = [e for e in rows if normalize(val(e, "level", "")).startswith(selector)]
            elif selector == "completed":
                rows = [e for e in rows if val(e, "status") == "completed"]
            else:
                rows = [
                    e
                    for e in rows
                    if val(e, "start_date")
                    and val(e, "end_date")
                    and val(e, "start_date") <= k.today.isoformat() <= val(e, "end_date")
                ]
            rows.sort(key=lambda e: (val(e, "rank", 0), val(e, "end_date", "")), reverse=True)
            if not rows:
                return None
            e = rows[0]
            for key in ("status", "start_date", "end_date", "rank"):
                read(e, key)
            return e

        intent, text = s["intent"], s["text"]
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
            if (
                e
                and val(e, "status") != "completed"
                and re.search(r"completed|earned|awarded|obtained|\bdid\b.*\bcomplete\b", text)
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
            e = education("master" if s["selector"] != "bachelor" else "bachelor")
            end = read(e, "end_date") if e else None
            if end:
                if len(end) == 7:
                    if field.get("type") == "date" or intent in {"START_DATE", "AVAILABILITY"}:
                        return self.unknown(question, s, "MISSING_EXACT_DATE")
                    target = date.fromisoformat(end + "-01")
                else:
                    target = date.fromisoformat(end)
                rules.append("education_end_date")
                confidence = "DERIVED_HIGH"
                if intent in {"START_DATE", "AVAILABILITY"}:
                    target += timedelta(days=1)
                    rules.append("day_after_full_time_study")
                    constraint = person("earliest_full_time_start_date")
                    if constraint:
                        target = max(target, date.fromisoformat(constraint))
                requested = requested_date(question)
                if s["boolean"]:
                    year = re.search(r"\b(20\d\d)\b", text)
                    if intent == "GRADUATION" and year:
                        answer = target.year == int(year[1])
                    elif requested:
                        proposed, precision = requested
                        if precision == "month" and target.year == proposed.year and target.month == proposed.month:
                            if field.get("type") != "checkbox" and field.get("role") not in {"radio", "combobox"}:
                                answer = f"Available from {target.strftime('%d %B %Y')}; the exact programme start must be checked."
                            else:
                                out["reason"] = "AMBIGUOUS_QUESTION"
                        else:
                            answer = target <= proposed
                elif re.search(r"how long|time until|months until|days until", text):
                    days = max(0, (target - k.today).days)
                    answer = (
                        days
                        if "days" in text or field.get("type") == "number"
                        else f"{days} days, until {target.strftime('%d %B %Y')}."
                    )
                    rules.append("date_difference:" + k.today.isoformat())
                elif "year" in text and "month" not in text:
                    answer = target.year
                elif field.get("type") == "date":
                    answer = target.isoformat()
                elif field.get("type") == "month":
                    answer = target.strftime("%Y-%m")
                else:
                    answer = target.strftime("%B %Y" if len(end) == 7 else "%d %B %Y")
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
                    else f"{notice} days"
                )
                explanations.append("Contractual notice only; academic availability is a separate constraint")
        elif intent == "LANGUAGE":
            languages = k.records("language")
            named = [r for r in languages if normalize(r["name"]) in text]
            selected = named or languages
            parts = []
            for r in selected:
                level = read(r, "proficiency")
                if level:
                    parts.append(level if len(named) == 1 else r["name"] + ": " + level)
            if parts:
                answer = True if s["boolean"] and named else ", ".join(parts)
        elif intent in {"VISA", "WORK_AUTHORIZATION", "SPONSORSHIP"}:
            records = k.records("immigration")
            country = (
                "United Kingdom"
                if re.search(
                    r"\buk\b|united kingdom|london|england", normalize(job.get("location", "") + " " + question)
                )
                else None
            )
            record = next((r for r in records if val(r, "country") == country), None)
            if record is None and intent == "VISA" and len(records) == 1 and country is None:
                record = records[0]
            if record:
                if intent == "VISA":
                    answer = read(record, "route")
                    if re.search(r"expir|valid until", text):
                        answer = read(record, "valid_until")
                else:
                    key = (
                        ("permanent_unrestricted" if s["qualifier"] == "permanent" else "current_right_to_work")
                        if intent == "WORK_AUTHORIZATION"
                        else "sponsorship_" + s["qualifier"]
                    )
                    # Legal determinations require a current explicit fact with its evidence.
                    answer = read(record, key)
                    if answer is None:
                        out["reason"] = out["root_cause"] = "MISSING_CURRENT_IMMIGRATION_EVIDENCE"
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
        elif intent in {
            "CONSENT",
            "GDPR",
            "CRIMINAL_HISTORY",
            "SECURITY_CLEARANCE",
            "BACKGROUND_CHECK",
            "CONFLICT_OF_INTEREST",
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
            answer = job.get("source")
            if answer and answer not in {"manual", "unknown"}:
                rules.append("job_discovery_provenance:" + str(job.get("id", "")))
                confidence = "DERIVED_HIGH"
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
        elif intent == "WORK_EXPERIENCE" and re.search(r"employer|company", text):
            rows = k.records("experience")
            current = s["selector"] == "current"
            if current:
                explicit = person("current_company")
                if explicit:
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
            skill = self.find_skill(text)
            if skill and answer is None:
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
                answer = self.narrative(s, job, used, rules)
        if intent == "WORK_EXPERIENCE" and re.search(r"employer|company", text):
            return out if answer is None else self._finish_evidence_answer(out, answer, used, rules, s, field)
        if (
            intent
            in {
                "PROJECT",
                "OWNERSHIP",
                "LEADERSHIP",
                "TEAMWORK",
                "CHALLENGE",
                "ACHIEVEMENT",
                "MOTIVATION",
                "COMPANY_INTEREST",
                "ROLE_INTEREST",
                "CAREER_GOALS",
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
                answer = self.narrative(s, job, used, rules)
            if answer is not None and not isinstance(answer, (int, bool)):
                confidence = "GENERATED"
        if answer is None:
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
            "answer": formatted,
            "form_value": formatted,
            "confidence": 1 if confidence == "EXACT" else 0.96 if confidence == "DERIVED_HIGH" else 0.85,
            "confidence_level": confidence,
            "verified": confidence == "EXACT",
            "inferred": confidence != "EXACT",
            "fact_ids": ids,
            "facts_used": ids,
            "rules_used": rules,
            "reason": reason,
            "reasoning_summary": reason,
            "requires_confirmation": confidence != "EXACT",
            "requires_review": confidence != "EXACT",
            "root_cause": None,
        }
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

    def narrative(self, s, job, used, rules):
        """Claim-safe generation: compose verified clauses, not unconstrained prose.

        Evidence selection varies by question and role. No model-supplied sentence
        enters autofill; free-form model drafts remain a separate manual workflow.
        """
        intent = s["intent"]
        words = set(normalize(job.get("title", "") + " " + s["text"]).split())
        records = self.knowledge.records("project") + self.knowledge.records("experience")
        targets = {
            "LEADERSHIP": "leadership",
            "OWNERSHIP": "ownership",
            "AI_USAGE": "ai",
            "TEAMWORK": "teamwork",
            "CHALLENGE": "challenge",
            "WORK_EXPERIENCE": "teaching",
        }
        target = targets.get(intent)
        if intent == "CHALLENGE":
            target = None  # describe the technical work without inventing a difficulty or emotion
        if intent == "FAILURE":
            return None
        if intent == "AI_USAGE" and re.search(r"current|day to day|now", s["text"]):
            records = [r for r in records if val(r, "ongoing") is True]
        eligible = []
        for r in records:
            tags = set(normalize(" ".join(val(r, "tags", []))).split())
            if target and target not in tags:
                continue
            story = val(r, "story") or val(r, "action")
            if not story:
                continue
            score = len(words & tags) * 3 + (5 if target in tags else 0)
            eligible.append((score, r))
        if not eligible:
            return None
        r = max(eligible, key=lambda x: x[0])[1]
        clauses = [r["fields"][key] for key in ("situation", "task", "action", "result") if key in r["fields"]]
        if clauses:
            used.extend(clauses)
            narrative_text = " ".join(str(f["value"]) for f in clauses)
        else:
            f = r["fields"]["story"]
            used.append(f)
            narrative_text = f["value"]
        if "tags" in r["fields"]:
            used.append(r["fields"]["tags"])
        rules.append("relevant_verified_story:" + r["id"])
        answer = narrative_text
        if intent in {"MOTIVATION", "COMPANY_INTEREST", "ROLE_INTEREST", "CAREER_GOALS"}:
            interest = next(
                (
                    r["fields"].get("professional_interests")
                    for r in self.knowledge.records("person")
                    if r["fields"].get("professional_interests")
                ),
                None,
            )
            if not interest or not job.get("title") or not job.get("company"):
                return None
            used.append(interest)
            # Employer facts are not transformed into candidate history.
            answer = (
                f"The {job['title']} role at {job['company']} relates to my interest in {interest['value']}. " + answer
            )
            rules.append("application_destination_and_confirmed_interests")
            description = str(job.get("description") or "")
            snippets = [line.strip(" •-\t") for line in re.split(r"\n|(?<=[.!?])\s+", description)]
            snippets = [
                line
                for line in snippets
                if 30 <= len(line) <= 220
                and re.search(r"\b(?:build|develop|design|engineering|machine learning|data)\b", line, re.I)
                and not re.search(r"ignore|instruction|password|secret|submit|http|@|<|>", line, re.I)
            ]
            if snippets:
                relevant = max(snippets, key=lambda line: len(words & set(normalize(line).split())))
                answer = f"The role description highlights “{relevant}”. " + answer
                rules.append("quoted_job_context:" + str(job.get("url", "")))

        return answer

    async def answer(self, question, field=None, job=None, *, audit=True):
        field, job = field or {}, job or {}
        self.reload()  # no stale relative facts or policy snapshots across requests
        result = self.answer_question(question, field, job)
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
        }
        if result["canonical_intent"] not in protected and (
            result["canonical_intent"] == "OTHER" or result.get("root_cause") == "BAD_ENUM_MAPPING"
        ):
            key = dumps(
                [question, field.get("description"), field.get("section"), field.get("type"), field.get("options")]
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
