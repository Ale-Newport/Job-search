"""Candidate disclosure boundaries, independent of stored answers or model output.

Only questions requesting the applicant's attributes are covered. Employer equal
opportunity statements and professional work on related subjects are context,
not requests for the candidate's own health or demographic information.
"""

from __future__ import annotations

import re
import unicodedata


POLICY_SUBJECT = "personal_disclosures"


def disclosure_action(policy):
    return policy.get("action") if isinstance(policy, dict) else policy


def disclosure_memory_key(question: str, element: dict | None = None) -> str:
    """Bind a saved personal response to the observed meaning of its prompt.

    Generic labels such as Please specify occur under many unrelated personal
    questions. Their descriptions and conditional parent must travel with an
    exact question memory; they never supply the candidate's answer themselves.
    """
    primary = _normal(question)
    element = element or {}
    if not personal_disclosure_kind(question, element):
        return primary
    context = []
    seen = {primary}
    for key, label in (
        ("description", "description"),
        ("context", "question context"),
        ("section_label", "section"),
        ("_disclosure_parent_question", "conditional parent"),
    ):
        value = _normal(element.get(key))
        if value and value not in seen:
            context.append(label + " " + value)
            seen.add(value)
    return primary + " meridian disclosure context " + " ".join(context) if context else primary


def disclosure_profile_key(question: str, element: dict | None = None) -> str | None:
    """Identify one explicitly requested attribute, never a related attribute.

    Broad categories are enough to suppress a disclosure, but not to answer it:
    gender does not establish sex or pronouns, and disability does not establish
    neurodivergence, mental health or a request for recruitment adjustments.
    """
    element = element or {}
    if not personal_disclosure_kind(question, element) or element.get("_disclosure_parent_question"):
        return None
    text = _normal(question)
    if "meridian disclosure context" in text and "conditional parent" in text:
        return None  # A saved conditional-detail response is not a general attribute.
    if not text or _AMBIGUOUS.fullmatch(text):
        text = " ".join(_normal(element.get(key)) for key in ("description", "context", "section_label"))
    keys = []
    patterns = (
        ("pronouns", r"\bpronouns?\b"),
        ("sexual_orientation", r"\bsexual(?:ity| orientation| identity| preference)\b"),
        ("sex", r"\bsex(?: assigned at birth)?\b"),
        ("gender", r"\bgender\b"),
        ("ethnicity", r"\bethnic\w*\b|\brace\b|\bracial\b"),
        ("religion", r"\breligi\w*\b|\bfaith\b|\bbeliefs?\b"),
        ("neurodivergence", r"\bneurodiver\w*\b"),
        ("mental_health", r"\bmental health\b"),
        ("disability", r"\bdisabilit\w*\b|\bdisabled\b"),
        ("adjustments", r"\badjustments?\b|\baccommodations?\b"),
        ("veteran", r"\bveteran\b"),
        ("date_of_birth", r"\bdate of birth\b|\bbirth ?date\b|\bdob\b"),
        ("age", r"\bage\b"),
    )
    for key, pattern in patterns:
        if re.search(pattern, text):
            keys.append(key)
    # The label can name the attribute while its hint changes the time frame
    # or expands the scope. A current self-description does not establish any
    # previous identity, past diagnosis, upbringing or assigned gender.
    qualified = " ".join((text, _normal(element.get("description"))))
    if keys != ["date_of_birth"] and re.search(
        r"\b(?:previous(?:ly)?|former(?:ly)?|histor(?:y|ical)|ever|in the past|used to|"
        r"grew up|growing up|raised|upbringing|childhood|as a child)\b",
        qualified,
    ):
        return None
    if "gender" in keys and re.search(r"\b(?:at birth|birth gender|assigned)\b", qualified):
        return None
    # A No for one kind of condition cannot answer No to a broader collection
    # of health conditions. Remove the supported mental-health phrase before
    # looking for an additional physical, general-health or illness clause.
    health_scope = re.sub(
        r"\bmental health(?: (?:conditions?|issues?|illness(?:es)?|disorders?))?\b", "", qualified
    )
    if any(key in keys for key in ("mental_health", "disability", "neurodivergence")) and re.search(
        r"\b(?:physical|medical|health (?:conditions?|status|information)|"
        r"(?:long term|chronic) (?:illness(?:es)?|conditions?)|illness(?:es)?|impairments?)\b",
        health_scope,
    ):
        return None
    if "adjustments" in keys:
        return "adjustments"  # A disability accommodation request asks for the request, not its cause.
    # Do not turn a broad identity into answers to narrower legal definitions
    # or diagnostic details. These require their own explicit response.
    if re.search(r"transgender|cisgender|hispanic|latino|latina|latinx|diagnos|describe.*condition|details.*condition", text):
        return None
    if re.search(r"\b(?:physical health|medical|health (?:conditions?|history|status))\b", text) and "mental health" not in text:
        return None
    return keys[0] if len(keys) == 1 else None


def _normal(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_TOPICS = (
    ("pronouns", r"\bpronouns?\b"),
    ("sexual_orientation", r"\bsexual(?:ity| orientation| identity| preference)\b|\blgbtq?\+?\b"),
    ("gender", r"\bgender\b|\bsex(?: assigned at birth)?\b|\b(?:transgender|cisgender)\b"),
    (
        "ethnicity",
        r"\bethnic(?:ity|ities| origin| background| group)?\b|\brace\b|\bracial\b|\b(?:hispanic|latino|latina|latinx|aboriginal|indigenous|national origin)\b",
    ),
    ("religion", r"\breligi(?:on|ous)\b|\bfaith\b|\b(?:religious|philosophical) beliefs?\b"),
    (
        "disability",
        r"\bdisabilit(?:y|ies)\b|\bdisabled\b|\bneurodivergen\w*\b|\bneurodivers\w*\b|\breasonable (?:adjustments?|accommodations?)\b|\b(?:need|require|request)\w*\b.{0,45}\b(?:adjustments?|accommodations?)\b|\b(?:adjustments?|accommodations?)\b.{0,25}\b(?:need|require|request)\w*\b",
    ),
    (
        "health",
        r"^health$|\b(?:mental|physical) health\b|\b(?:medical|health) (?:history|conditions?|status|information)\b|\b(?:chronic illness|long term (?:illness|condition)|diagnos\w*|impairments?|medical treatment)\b",
    ),
    (
        "demographic",
        r"\b(?:marital|civil partnership|parental|pregnancy|veteran|military service|socioeconomic)(?: (?:status|background|history))?\b|\bdemographics?\b|\b(?:caring responsibilities|underrepresented group)\b",
    ),
    ("birth", r"\b(?:date of birth|birth date|birthdate|age|dob)\b"),
)
_PERSONAL = re.compile(
    r"\b(?:your|yours|you|applicant|candidate|self identif\w*|identify as|identifies as|i (?:am|have|identify)|my)\b"
)
_PROFESSIONAL = re.compile(
    r"\b(?:(?:build|built)\w*|test\w*|deploy\w*|develop\w*|design\w*|implement\w*|research\w*|improv\w*|experienc\w*|work\w*|approach\w*)\b.*"
    r"\b(?:software|applications?|products?|platforms?|models?|systems?|services?|tools?|datasets?|data|teams?|technolog\w*|patients?|users?|clients?|customers?)\b"
    r"|\b(?:software|product|platform|model|system|dataset|data|team|technology|patient|user|client|customer)\w*\b.*"
    r"\b(?:(?:build|built)\w*|test\w*|deploy\w*|develop\w*|design\w*|improv\w*|experienc\w*|research\w*)\b"
)
_BOILERPLATE = re.compile(
    r"\b(?:equal opportunity|we (?:do not|don t) discriminate|regardless of|without regard to|"
    r"protected characteristics|all qualified applicants|diverse (?:team|workforce))\b"
)
_AMBIGUOUS = re.compile(
    r"^(?:(?:please )?(?:select|choose)(?: all that apply| an? (?:option|answer))?|"
    r"please (?:specify|elaborate)|additional (?:information|context|details)|further (?:information|details)|supporting information|.*anything else.*|your response|your answer|response|answer|tell us more about yourself|other(?: please specify)?|how (?:would|do) you (?:describe|identify) yourself|"
    r"do you identify with any of the following|yes|no|prefer not to (?:say|answer)|decline to (?:answer|self identify))$"
)


def _topic(text):
    return next((kind for kind, pattern in _TOPICS if re.search(pattern, text)), None)


def _candidate_clause(text):
    """Identify an actual attribute prompt, excluding hiring/product context."""
    normalized = _normal(text)
    topic = _topic(normalized)
    if not topic or _BOILERPLATE.search(normalized):
        return None
    if re.match(r"^(?:we|our|the (?:company|employer|organisation|organization))\b", normalized) and not re.search(
        r"\byou(?:r|rs)?\b", normalized
    ):
        # An employer describing its inclusive culture is not asking for an
        # applicant attribute, even when the statement is a short clause.
        return None
    if re.search(
        r"\b(?:your|my) (?:current |personal |preferred )?(?:gender|sex(?:ual)?|ethnic\w*|race|religio\w*|pronouns?|disabilit\w*|(?:mental|physical) health|medical (?:history|condition)|date of birth|age)\b",
        normalized,
    ):
        return topic
    if re.search(r"\bfaith in (?:ai|technology|science|software|the product)\b", normalized):
        return None
    if _PROFESSIONAL.search(normalized) or (
        re.search(r"\b(?:interested|motivat\w*|apply|applying)\b", normalized)
        and re.search(r"\b(?:product|mission|company|role|team|platform|sector|field|health)\b", normalized)
    ):
        return None
    # A faith in technology, model bias, and customer's protected attributes
    # are not the applicant's own religious or demographic disclosures.
    if re.search(
        r"\b(?:customers?|users?|patients?|clients?|employees?|models?|datasets?) (?:s )?(?:age|gender|race|health|disabilit|religio)",
        normalized,
    ):
        return None
    if _PERSONAL.search(normalized):
        return topic
    # ATS controls commonly use bare labels: Gender, Ethnicity, Date of birth.
    if len(normalized.split()) <= 8 and not re.search(
        r"\b(?:company|mission|product|organisation|organization|team|bias|fairness|inclusion)\b", normalized
    ):
        return topic
    return None


def inherit_disclosure_context(element: dict, elements: list[dict], adapter) -> None:
    """Carry an observed disclosure boundary to its conditional detail field."""
    element.pop("_disclosure_parent_question", None)
    text = " ".join((adapter.question(element), element.get("description", "")))
    conditional = re.search(
        r"\bif\s+(?:(?:you|your)\s+)?(?:(?:have|answer|the answer)\s+)?(?:(?:selected|select|answered|answer|chose|choose|is|was)\s+)?['\"]?(?:other|yes|no)\b|^\s*other\s*[,:(—-]?\s*please specify",
        text,
        re.I,
    )
    if not conditional:
        return
    preceding = []
    for candidate in elements:
        if candidate.get("node_id") == element.get("node_id") and candidate.get("document_id") == element.get(
            "document_id"
        ):
            break
        if (
            candidate.get("document_id") == element.get("document_id")
            and candidate.get("form_id") == element.get("form_id")
            and candidate.get("role") in {"combobox", "radio", "checkbox"}
        ):
            preceding.append(candidate)
    if preceding:
        parent = preceding[-1]
        parent_question = adapter.question(parent)
        if personal_disclosure_kind(parent_question, parent):
            element["_disclosure_parent_question"] = parent_question


def personal_disclosure_kind(question: str, element: dict | None = None) -> str | None:
    element = element or {}
    parent = element.get("_disclosure_parent_question")
    if parent:
        kind = _candidate_clause(parent)
        if kind:
            return kind
    # Split long labels so an EEO paragraph cannot obscure the real question,
    # nor make a following unrelated application question sensitive.
    clauses = [part.strip() for part in re.split(r"[\n.!?;]+", str(question or "")) if part.strip()]
    for clause in reversed(clauses):
        kind = _candidate_clause(clause)
        if kind:
            return kind
    primary = _normal(question)
    # Context is used for a weak label only. A large section's diversity text
    # must never suppress an ordinary name, salary or motivation question.
    if not primary or _AMBIGUOUS.fullmatch(primary):
        for key in ("description", "context", "section_label"):
            context = element.get(key, "")
            kind = _candidate_clause(context)
            if kind:
                return kind
    return None


def disclosure_resolution(question: str, element: dict | None = None, policies: dict | None = None) -> dict | None:
    policy = (policies or {}).get(POLICY_SUBJECT, {})
    action = disclosure_action(policy)
    if action != "leave_blank":
        return None
    kind = personal_disclosure_kind(question, element)
    if not kind:
        return None
    required = bool((element or {}).get("required"))
    reason = "Your personal-disclosure policy leaves this question blank."
    if required:
        reason += " The employer marks it required; automatic completion stops at this boundary."
    return {
        "question": question,
        "kind": kind,
        "answer": None,
        "fact_ids": [],
        "verified": True,
        "confidence": 1.0,
        "leave_blank": True,
        "canonical_intent": "DEMOGRAPHIC",
        "confidence_class": "POLICY",
        "policy": POLICY_SUBJECT,
        "reason": reason,
        "root_cause": "PERSONAL_DISCLOSURE_POLICY",
        "skip_inline_question": True,
        "requires_review": False,
    }


def has_disclosure_value(element: dict) -> bool:
    if element.get("role") in {"radio", "checkbox", "switch"}:
        return bool(element.get("checked"))
    if element.get("role") == "combobox":
        value = str(element.get("selected_text") or element.get("value") or "").strip()
        return bool(
            value
            and not re.fullmatch(
                r"(?:please )?(?:select|choose)(?:\s+(?:an?\s+)?(?:option|answer))?\s*[.\u2026]*", value, re.I
            )
        )
    return bool(element.get("value"))


def disclosure_clear_operation(element: dict) -> tuple[str, str | None] | None:
    """Use observed clear operations only; never substitute a decline answer."""
    ops = element.get("operations", [])
    if not has_disclosure_value(element):
        return None
    if "CLEAR" in ops:
        return "CLEAR", None
    if element.get("role") in {"checkbox", "switch"} and "UNCHECK" in ops:
        return "UNCHECK", None
    if "SELECT" in ops:
        empty = [o for o in element.get("options", []) if o.get("value") == "" and not o.get("disabled")]
        return ("SELECT", "") if len(empty) == 1 else None
    if "TYPE_TEXT" in ops and element.get("role") != "combobox":
        return "TYPE_TEXT", ""
    return None
