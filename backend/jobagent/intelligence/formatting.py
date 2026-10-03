from __future__ import annotations
import re
from ..automation.answers import normalize, option_for

EQUIVALENTS = [
    {"first", "first class", "first class honours", "1st", "1", "first 1st", "1st class honours"},
    {"bachelor", "bachelor s", "bachelor s degree", "bachelors degree", "bsc", "bs", "undergraduate"},
    {"master", "master s", "master s degree", "masters degree", "msc", "ms", "postgraduate"},
    {"united kingdom", "uk", "gb", "great britain"},
    {"spain", "es", "esp"},
    {"spanish", "spanish citizenship"},
    {"none", "not applicable", "n a", "0", "no notice", "zero", "0 days"},
    {
        "fluent",
        "professional working proficiency",
        "fluent professional",
        "full professional proficiency",
        "fluent professional working proficiency",
        "fluent native",
        "fluent or native",
    },
    {
        "prefer not to say",
        "decline to answer",
        "i do not wish to answer",
        "prefer not to disclose",
        "i don t wish to answer",
    },
]


def choice(value, options, **kwargs):
    result = option_for(str(value), options, **kwargs)
    if result:
        return result
    norm = normalize(value)
    labels = [
        o
        for o in options
        if not o.get("disabled") and (o.get("role") == "option" or o.get("value", o.get("label")) not in ("", None))
    ]
    # Phone country menus decorate country names with international dial codes.
    countries = [o for o in labels if normalize(re.sub(r"\s+\+\d[\d\s-]*$", "", o["label"])) == norm]
    if len(countries) == 1:
        return countries[0]
    # A displayed institutional acronym is decoration, not a different entity.
    if len(norm) > 3:
        matches = [o for o in labels if normalize(re.sub(r"\s*\([A-Z]{2,8}\)\s*$", "", o["label"])) == norm]
        if len(matches) == 1:
            return matches[0]
    for group in EQUIVALENTS:
        if norm in group:
            matches = [o for o in labels if normalize(o["label"]) in group]
            if len(matches) == 1:
                return matches[0]
    # Decorated choices may add explanations without changing the qualification.
    patterns = {
        "first class honours": r"\b(?:first class|1st class)\b",
        "bachelor s degree": r"\b(?:undergraduate|bachelor)\b",
        "master s degree": r"\b(?:postgraduate|master)\b",
        "student": r"^student(?: visa)?(?: tier 4)?$",
    }
    pattern = patterns.get(norm)
    if pattern:
        matches = [o for o in labels if re.search(pattern, normalize(o["label"]))]
        if len(matches) == 1:
            return matches[0]
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    matches = []
    for o in labels:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[%+]?\s*[-–—]\s*(\d+(?:\.\d+)?)\s*%?\s*", o["label"])
        if m and float(m[1]) <= number <= float(m[2]):
            matches.append(o)
    return matches[0] if len(matches) == 1 else None


def adapt(value, semantic, field):
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    if isinstance(value, (float, int)):
        if str(field.get("step")) == "1":
            value = int(float(value) + 0.5)
        value = str(value).removesuffix(".0")
    value = str(value)
    if field.get("options"):
        option = choice(value, field["options"])
        if not option:
            return None, "BAD_ENUM_MAPPING"
        value = option["label"]
    if field.get("type") == "number" and not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return None, "ANSWER_FORMAT_ERROR"
    from datetime import date

    if field.get("type") == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return None, "ANSWER_FORMAT_ERROR"
    if field.get("type") == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        return None, "ANSWER_FORMAT_ERROR"
    if field.get("type") == "url" and not re.match(r"^https?://", value):
        return None, "ANSWER_FORMAT_ERROR"
    if field.get("type") == "number":
        number = float(value)
        if field.get("min") not in ("", None) and number < float(field["min"]):
            return None, "ANSWER_FORMAT_ERROR"
        if field.get("max") not in ("", None) and number > float(field["max"]):
            return None, "ANSWER_FORMAT_ERROR"
    word_limit = re.search(
        r"(?:max(?:imum)?(?: of)?|up to|limit(?: of)?)\s*(\d+)\s*words", str(field.get("description", "")), re.I
    )
    if word_limit and len(value.split()) > int(word_limit[1]):
        kept = []
        for statement in re.split(r"(?<=[.!?])\s+|\n+", value):
            if len(" ".join(kept + [statement]).split()) <= int(word_limit[1]):
                kept.append(statement)
        if not kept:
            return None, "ANSWER_FORMAT_ERROR"
        value = " ".join(kept)
    if field.get("max_length") and len(value) > field["max_length"]:
        # Whole supported statements, never blind character truncation.
        statements = re.split(r"(?<=[.!?])\s+|\n+", value)
        selected = []
        for statement in statements:
            if len(" ".join(selected + [statement])) <= field["max_length"]:
                selected.append(statement)
        if not selected:
            return None, "ANSWER_FORMAT_ERROR"
        value = " ".join(selected)
    return value, None
