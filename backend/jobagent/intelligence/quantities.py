"""Which of a menu's ranges a quantity falls in: "4 years" -> "3-5 years", "18 months" -> "1-2 years".

Deterministic and conservative: a quantity answers a menu only when the menu is made of ranges (at least two of its options
parse as one), the quantity has a unit that converts to the menu's, and exactly ONE range contains it. A value on the border of
two ranges ("3" between "1-3 years" and "3-5 years"), a unit that cannot be converted (months against "weeks") or a menu that is
not a set of ranges answers nothing: the caller leaves the question to the person or to another stage.

``bucket_option`` is what ``formatting.choice`` falls back on, so the same reading serves the browser assistant and Auto Apply.
"""

from __future__ import annotations

import re

# One unit of each in days (a month is 30.4375 days: 365.25 / 12), the common ground between "weeks" and "months".
UNIT_DAYS = {"day": 1.0, "week": 7.0, "month": 365.25 / 12, "year": 365.25}
UNIT_WORDS = {  # whole words only: a bare "m" or "y" could be anything
    "day": r"days?",
    "week": r"weeks?|wks?",
    "month": r"months?|mos?|mths?",
    "year": r"years?|yrs?",
}
UNIT = "(?:" + "|".join(UNIT_WORDS.values()) + ")"
NUMBER = r"\d+(?:[.,]\d+)?"
WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "an": 1, "half a": 0.5, "half an": 0.5}


def unit_name(word: str | None) -> str | None:
    """'yrs' -> 'year'."""
    if not word:
        return None
    return next((name for name, words in UNIT_WORDS.items() if re.fullmatch(words, word.strip(), re.I)), None)


QUANTITY = re.compile(rf"^(?:about|around|approximately|approx\.?|roughly|circa|c\.|~|almost|nearly)?\s*(?P<n>{NUMBER}|{'|'.join(sorted(WORD_NUMBERS, key=len, reverse=True))})"
                      rf"\s*(?:\+)?\s*(?P<unit>{UNIT})?(?:\s+of\s+experience)?\s*\.?$", re.I)
# A range as a menu writes it. Each form is anchored on the whole label (an extra word means it is not a range). ``u1`` is a unit written
# right after the first number ("3 months or more"), ``u2`` one written at the end ("3+ months", "1-3 years").
_N = rf"(?P<{{}}>{NUMBER})"
U1 = rf"(?:\s*(?P<u1>{UNIT}))?"
U2 = rf"(?:\s*(?P<u2>{UNIT}))?(?:\s+(?:of\s+)?(?:experience|exp\.?|notice))?"
FORMS = [
    ("between", re.compile(rf"^(?:between\s+)?{_N.format('lo')}{U1}\s*(?:-|–|—|to|and)\s*{_N.format('hi')}{U2}$", re.I)),
    ("plus", re.compile(rf"^{_N.format('lo')}{U1}\s*(?:\+|plus|or\s+more|and\s+(?:above|over|more|up)|or\s+above|or\s+over|or\s+longer|and\s+beyond){U2}$", re.I)),
    ("over", re.compile(rf"^(?:more\s+than|over|above|greater\s+than|longer\s+than|>)\s*{_N.format('lo')}{U1}{U2}$", re.I)),
    ("atleast", re.compile(rf"^(?:at\s+least|minimum(?:\s+of)?|min\.?|≥|>=)\s*{_N.format('lo')}{U1}{U2}$", re.I)),
    ("under", re.compile(rf"^(?:less\s+than|under|below|fewer\s+than|shorter\s+than|<)\s*{_N.format('hi')}{U1}{U2}$", re.I)),
    ("upto", re.compile(rf"^(?:up\s+to|at\s+most|maximum(?:\s+of)?|max\.?|no\s+more\s+than|≤|<=)\s*{_N.format('hi')}{U1}{U2}$", re.I)),
    ("orless", re.compile(rf"^{_N.format('hi')}{U1}\s*(?:or\s+(?:less|fewer|under|below|shorter)|and\s+(?:under|below)){U2}$", re.I)),
    ("exact", re.compile(rf"^{_N.format('lo')}{U1}{U2}$", re.I)),
]
PLACEHOLDER = re.compile(r"^\s*(?:-+|—|select\b.*|choose\b.*|please\s+(?:select|choose)\b.*|pick\b.*|\.\.\.|…)\s*$", re.I)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def parse_quantity(value) -> tuple[float, str | None] | None:
    """``(number, unit)`` of an answer such as ``4 years``, ``about 18 months``, ``6``, ``a year``; None when it is not a plain
    quantity (a sentence, a range, two numbers)."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text or len(text) > 40:
        return None
    match = QUANTITY.match(text)
    if not match:
        return None
    raw = match.group("n").lower()
    number = float(WORD_NUMBERS[raw]) if raw in WORD_NUMBERS else _number(raw)
    return number, unit_name(match.group("unit"))


def parse_range(label) -> dict | None:
    """The interval a menu label stands for: ``{lo, hi, lo_open, hi_open, unit, kind}`` (None bound = unbounded), or None."""
    text = re.sub(r"\s+", " ", str(label or "").strip().strip("()[]"))
    if not text or len(text) > 60 or PLACEHOLDER.match(text):
        return None
    for kind, form in FORMS:
        match = form.match(text)
        if not match:
            continue
        found = match.groupdict()
        lo = _number(found["lo"]) if found.get("lo") else None
        hi = _number(found["hi"]) if found.get("hi") else None
        units = {unit_name(found.get("u1")), unit_name(found.get("u2"))} - {None}
        if len(units) > 1:
            return None  # "1 month to 2 years": a menu that mixes units is no range this stage reads
        if kind == "between" and (lo is None or hi is None or lo > hi):
            return None
        if kind == "exact":
            hi = lo  # "5 years" is that quantity and no other
        return {"lo": lo, "hi": hi, "unit": next(iter(units), None), "kind": kind,
                # "more than 5" and "less than 3" leave their bound out; "5+", "at least 5", "up to 3" and "1-3" keep it
                "lo_open": kind == "over", "hi_open": kind == "under"}
    return None


def _days(number: float, unit: str) -> float:
    return number * UNIT_DAYS[unit]


def contains(interval: dict, number: float, unit: str | None, default_unit: str | None) -> bool | None:
    """Is ``number unit`` inside ``interval``? None when the units cannot be compared."""
    label_unit = interval["unit"] or default_unit
    if unit is None:
        unit = label_unit  # "4" answers "3-5 years" in years: a bare number is read in the menu's unit
    if label_unit is None and unit is None:
        pass  # both unitless: plain numbers
    elif label_unit is None or unit is None:
        return None
    elif unit != label_unit:
        number = _days(number, unit) / UNIT_DAYS[label_unit]
    lo, hi = interval["lo"], interval["hi"]
    epsilon = 1e-9
    if lo is not None and (number < lo - epsilon or (interval["lo_open"] and number <= lo + epsilon)):
        return False
    if hi is not None and (number > hi + epsilon or (interval["hi_open"] and number >= hi - epsilon)):
        return False
    return True


def menu_unit(question: str, options: list[dict]) -> str | None:
    """The unit a menu is in: the one its labels name, else the one its question names ("notice period in weeks")."""
    for option in options:
        interval = parse_range(option.get("label"))
        if interval and interval["unit"]:
            return interval["unit"]
    named = re.search(r"\b(?:in|per|number of|how many)\s+(?P<u>years?|months?|weeks?|days?)\b", str(question or ""), re.I)
    if named:
        return unit_name(named.group("u"))
    return None


def bucket_option(value, options: list[dict], *, question: str = "") -> dict | None:
    """The one option whose range contains the quantity in ``value``, else None. Needs at least two range-shaped options (one
    option on its own is a ``supports`` check: it is the answer only when the quantity is inside it)."""
    quantity = parse_quantity(value)
    if quantity is None:
        return None
    number, unit = quantity
    candidates = [(option, parse_range(option.get("label"))) for option in options if not option.get("disabled")]
    ranges = [(option, interval) for option, interval in candidates if interval is not None]
    # "1 - 3" or "5+" alone could be a menu of one: only exact labels beside others of another shape stay unreadable
    if not ranges or any(interval["kind"] == "exact" for _, interval in ranges) and len(ranges) == 1 and len(candidates) > 1:
        return None
    default_unit = menu_unit(question, options)
    inside = []
    for option, interval in ranges:
        verdict = contains(interval, number, unit, default_unit)
        if verdict is None:
            return None  # a unit that cannot be compared with the menu's: not this stage's to guess
        if verdict:
            inside.append(option)
    return inside[0] if len(inside) == 1 else None
