"""The hand oracle: what the FACTS of a synthetic candidate say about a question, written as data and never computed by the
engine under test, plus the normalisation that lets an answer be compared with it.

An ``Expect`` is one of:

* ``none``   the facts do not settle the question: the pipeline must abstain (NEEDS_YOU, blank, consent...), any value is wrong;
* ``bool``   the facts say Yes or No: a value, when one is given, must be that one (abstaining is always allowed);
* ``text``   a value must match one of the (accent-folded, lower-case) regular expressions;
* ``number`` the first number in the value must lie in [low, high];
* ``day``    the value must read as that calendar date.

Abstaining is never wrong for a question the facts settle (fail closed); ``must_answer`` bases are the plain forms whose
absence is itself reported as a regression."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

MONTHS = {name: n for n, names in enumerate(
    ["january jan enero", "february feb febrero", "march mar marzo", "april apr abril", "may mayo", "june jun junio",
     "july jul julio", "august aug agosto", "september sep sept septiembre", "october oct octubre",
     "november nov noviembre", "december dec diciembre"], start=1) for name in names.split()}


def fold(text) -> str:
    """Lower-case, accent-free, single-spaced, without the punctuation a label or a value may be wrapped in."""
    text = unicodedata.normalize("NFKD", str(text if text is not None else ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.casefold()).strip(" .,:;!?*¿¡'\"()[]")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def as_bool(value) -> str | None:
    word = fold(value)
    if word in {"yes", "si", "true", "checked"}:
        return "Yes"
    if word in {"no", "false", "unchecked"}:
        return "No"
    return None


def first_number(value) -> float | None:
    found = re.search(r"\d[\d.,]*\d|\d", str(value or ""))
    if not found:
        return None
    token = found.group(0)
    tail = str(value)[found.end():found.end() + 1].lower()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        number = float(re.sub(r"[.,]", "", token))
    else:
        number = float(token.replace(",", "."))
    return number * 1000 if tail == "k" and number < 1000 else number


def read_date(value) -> date | None:
    """ISO, d/m/y, 'd Month y' and 'Month y' (the first of it) are the readings of a date a form field is given."""
    text = fold(value)
    found = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if found:
        return date(int(found[1]), int(found[2]), int(found[3]))
    found = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", text)
    if found:
        return date(int(found[3]), int(found[2]), int(found[1]))
    found = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})\b", text)
    if found and found[2] in MONTHS:
        return date(int(found[3]), MONTHS[found[2]], int(found[1]))
    return None


@dataclass(frozen=True)
class Expect:
    kind: str = "none"
    truth: bool | None = None
    patterns: tuple = ()
    low: float | None = None
    high: float | None = None
    day: date | None = None
    note: str = ""
    forbid: tuple = ()  # patterns that make an otherwise matching text wrong

    @property
    def known(self) -> bool:  # the facts settle it
        return self.kind != "none"

    def accepts(self, value) -> bool:
        if value is None:
            return True
        if self.kind == "bool":
            return as_bool(value) == ("Yes" if self.truth else "No")
        if self.kind == "either":
            return as_bool(value) is not None
        if self.kind == "text":
            word = fold(value)
            return any(re.search(p, word) for p in self.patterns) and not any(re.search(p, word) for p in self.forbid)
        if self.kind == "number":
            number = first_number(value)
            return number is not None and self.low <= number <= self.high
        if self.kind == "digits":
            digits = re.sub(r"\D", "", str(value))
            return len(digits) >= 9 and digits.endswith(self.patterns[0])
        if self.kind == "day":
            return read_date(value) == self.day
        return False

    def describe(self) -> str:
        if self.kind == "bool":
            return "Yes" if self.truth else "No"
        if self.kind == "either":
            return "Yes or No (an ambiguous reading; the facts settle the proposition)"
        if self.kind == "text":
            return "/" + " | ".join(self.patterns) + "/" + (f" and not /{'|'.join(self.forbid)}/" if self.forbid else "")
        if self.kind == "digits":
            return "a number ending " + self.patterns[0]
        if self.kind == "number":
            return f"{self.low:g}" if self.low == self.high else f"{self.low:g}..{self.high:g}"
        if self.kind == "day":
            return self.day.isoformat()
        return "nothing (the facts do not settle it: abstain)"


UNKNOWN = Expect()
YES = Expect("bool", True)
NO = Expect("bool", False)


def boolean(value: bool | None) -> Expect:
    return UNKNOWN if value is None else YES if value else NO


def text(*patterns: str, note: str = "", forbid: tuple = ()) -> Expect:
    return Expect("text", patterns=tuple(patterns), note=note, forbid=tuple(forbid))


def digits(phone: str) -> Expect:
    return Expect("digits", patterns=(re.sub(r"\D", "", phone)[-9:],))


def number(low: float, high: float | None = None) -> Expect:
    return Expect("number", low=low, high=low if high is None else high)


def on(day: date) -> Expect:
    return Expect("day", day=day)


def flip(expect: Expect) -> Expect:
    """The answer to the negated proposition of a Yes/No question: what the facts settle, negated."""
    return boolean(None if expect.kind != "bool" else not expect.truth)


def either(expect: Expect) -> Expect:
    """An ambiguous reading ("Don't you speak English?"): either polarity is fine when the facts settle the proposition."""
    return Expect("either") if expect.kind == "bool" else UNKNOWN
