"""General-profile derivation and observed conditional form dependencies.

Derived answers stay reviewable; they are never promoted into verified facts.
"""

from __future__ import annotations

from datetime import date
import re

from .answers import approved_facts, canonical_key, classify_question, normalize, option_for, resolve_answer


def unique_fact(facts, key):
    matches = [f for f in approved_facts(facts) if f.get("key") == key]
    return matches[0] if len({f["value"] for f in matches}) == 1 else None


def month_value(fact):
    value = fact.get("value", "") if fact else ""
    return value if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value) else None


def derived(question, answer, sources, reason, confidence=0.9):
    return dict(
        question=question,
        answer=answer,
        fact_ids=[f["id"] for f in sources if f],
        kind=classify_question(question),
        verified=False,
        confidence=confidence,
        leave_blank=False,
        inferred=True,
        requires_review=True,
        reason=reason,
    )


def conditional_state(element, elements, adapter):
    """None = unconditional; dict applies True/False/None = evaluated/pending.

    Only observed values in the same document/form count. Missing parent values
    never mean No. An explicit parent question takes precedence over proximity.
    """
    text = (
        " ".join([adapter.question(element), element.get("description", "")])
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    match = re.search(
        r"\bif\s+(?:you\s+)?(?:have\s+)?(?:selected|select|answered|answer|chose|choose)\s+[\'\"]?(other|yes|no)\b",
        text,
        re.I,
    )
    if not match:
        match = re.search(r"\bif\s+[\'\"]?(yes|no|other)\b", text, re.I)
    if not match:
        match = re.search(r"\bif\s+(?:you\s+)?(?:selected|answered|chose)\s+[\'\"]([^\'\"]+)[\'\"]", text, re.I)
    if not match:
        match = re.search(
            r"\bif\s+(?:your|the)\s+(?:(?:previous|above)\s+)?answer\s+(?:above\s+)?(?:is|was)\s+['\"]?(other|yes|no)\b",
            text,
            re.I,
        )
    if not match:
        match = re.search(r"^\s*(other)\s*[,:(—-]?\s*please specify", text, re.I)
    if not match:
        return None
    expected = normalize(match[1])
    preceding = []
    for candidate in elements:
        if candidate.get("node_id") == element.get("node_id") and candidate.get("document_id") == element.get(
            "document_id"
        ):
            break
        if candidate.get("document_id") != element.get("document_id") or candidate.get("form_id") != element.get(
            "form_id"
        ):
            continue
        if candidate.get("role") not in {"combobox", "radio", "checkbox"}:
            continue
        preceding.append(candidate)
    explicit = [
        p
        for p in preceding
        if len(normalize(adapter.question(p))) > 10 and normalize(adapter.question(p)) in normalize(text)
    ]
    # An explicit quoted reference which cannot be resolved must not silently
    # bind to an unrelated nearby question.
    referenced = re.search(r"\b(?:to|for)\s+(?:the question\s+)?[\'\"]([^\'\"]{10,})[\'\"]", text, re.I)
    if referenced and not explicit:
        return {"applies": None, "reason": "The referenced parent question is not available on this page."}
    parent = (explicit or preceding)[-1] if (explicit or preceding) else None
    if not parent:
        return {"applies": None, "reason": "Waiting for the controlling question."}
    value = parent.get("selected_text") or parent.get("value", "")
    if parent.get("role") == "radio":
        selected = [p for p in preceding if p.get("group") == parent.get("group") and p.get("checked")]
        value = selected[-1]["label"] if selected else ""
    elif parent.get("role") == "checkbox":
        value = "Yes" if parent.get("checked") else "No"
    elif parent.get("options"):
        selected = [o for o in parent["options"] if o.get("value") == value and o.get("value")]
        value = selected[0]["label"] if len(selected) == 1 else ""
    actual = normalize(value)
    if not actual or re.fullmatch(r"(?:please )?(?:select|choose)(?: an? (?:option|answer))?|start typing", actual):
        return {"applies": None, "reason": "Waiting for an answer to " + adapter.question(parent)}
    # “Other (please specify)” is the same trigger as “Other”.
    applies = actual == expected or (expected == "other" and actual.startswith("other "))
    return {
        "applies": applies,
        "parent": adapter.question(parent),
        "expected": expected,
        "actual": value,
        "reason": f"Conditional on {expected}; controlling answer is {value}.",
    }


def profile_resolution(question, facts, *, element=None, policies=None, hints=None):
    element = element or {}
    direct = resolve_answer(question, facts, hints=hints, policies=policies)
    if direct["answer"] is not None or direct["leave_blank"]:
        return direct
    text = normalize(question)
    available = unique_fact(facts, "earliest_start_month")
    completion = unique_fact(facts, "masters_completion_month")
    month = month_value(available)
    end = month_value(completion)
    if end and (
        canonical_key(question) == "graduation_year"
        or re.search(
            r"(?:expected|anticipated) graduat|master.*(?:finish|end|complet|graduat)|(?:finish|complet).*master", text
        )
    ):
        if re.search(r"year", text):
            return derived(
                question, end[:4], [completion], "Year extracted from the confirmed master’s completion month."
            )
        if not re.search(r"why|how|describe", text):
            return derived(
                question,
                date.fromisoformat(end + "-01").strftime("%B %Y"),
                [completion],
                "Confirmed completion month; the exact day is not known.",
            )
    if not month:
        return direct
    display = date.fromisoformat(month + "-01").strftime("%B %Y")
    available_text = f"I can start from {display} at the earliest."
    if end == month:
        available_text += f" I expect to complete my master’s in {display}; the exact start date can be agreed."
    sources = [available, completion] if end == month else [available]
    reason = "Derived from the confirmed earliest start month. This is availability, not a contractual notice period."
    is_notice = canonical_key(question) == "notice_period" or bool(
        re.search(r"notice.*(?:period|required)|(?:how much|how long).*notice", text)
    )
    if is_notice:
        options = element.get("options", [])
        choice = option_for("Other", options)
        if choice:
            return derived(
                question,
                choice["label"],
                sources,
                reason + " Other preserves the distinction; the follow-up explains the date.",
            )
        if element.get("role") == "combobox" and not options:
            # The dropdown path independently requires a real matching option.
            return derived(question, "Other", sources, reason + " Select Other only if the form offers it.")
        if element.get("role") in {"radio", "checkbox"} or options:
            return direct
        return derived(question, available_text + " My contractual notice period is not specified.", sources, reason)
    if re.search(r"if .*other", text) and element.get("_condition", {}).get("applies") is True:
        if canonical_key(element["_condition"].get("parent", "")) == "notice_period":
            return derived(question, available_text, sources, reason)
    if canonical_key(question) == "availability" or re.search(
        r"(?:when|earliest|how soon).*(?:start|join|available)|(?:start|joining|availability) date", text
    ):
        if element.get("type") == "date":
            return derived(
                question,
                month + "-01",
                sources,
                "Only the month is confirmed. Day 1 is a proposed placeholder that must be reviewed.",
                0.65,
            )
        if element.get("type") == "month":
            return derived(question, month, sources, "Confirmed earliest start month.")
        return derived(
            question, available_text, sources, "Answer derived from confirmed availability, preserving month precision."
        )
    if re.search(r"(?:can|could|able|available).*\b(?:start|join)\b", text):
        requested = re.search(
            r"\b(" + "|".join(date(2000, i, 1).strftime("%B").lower() for i in range(1, 13)) + r") (\d{4})\b", text
        )
        if requested:
            target = date.fromisoformat(
                f"{requested[2]}-{list(date(2000, i, 1).strftime('%B').lower() for i in range(1, 13)).index(requested[1]) + 1:02d}-01"
            ).strftime("%Y-%m")
            if target < month:
                return derived(
                    question,
                    "No",
                    [available],
                    f"The proposed start is before the confirmed earliest month, {display}.",
                )
            # Later availability is not a promise to accept a specific offer.
            return derived(
                question,
                "Yes",
                [available],
                f"The proposed month is on or after {display}; confirm the exact date and commitment.",
                0.75,
            )
    return direct


def extractive_narrative(question, facts):
    """A factual fallback for broad narratives, with no generated personal claims."""
    text = normalize(question)
    allowed = approved_facts(facts)
    selected = []
    if re.search(r"how.*(?:using|use) ai", text):
        selected = [f for f in allowed if f.get('key') == 'ai_workflow']
        if not selected:
            selected = [f for f in allowed if f.get('category') == 'project' and re.search(r"\bi am using ai\b", normalize(f['value']))]
        if len(selected) != 1:
            return None
    elif re.search(r"anything else.*share|tell us about yourself|professional (?:summary|background)", text):
        for category in ('education', 'experience', 'project'):
            candidates = [f for f in allowed if f.get('category') == category]
            if candidates:
                selected.append(candidates[0])
    if not selected:
        return None
    answer = '\n\n'.join(f['value'] for f in selected)
    return derived(question, answer, selected, 'Factual draft extracted from the general profile; review its relevance and wording.', .8)
