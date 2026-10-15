"""How a form asks for its answer to be WRITTEN, apart from what it asks.

'Graduation date (MM/YYYY)', 'Degree subject (e.g. Computer Science)', 'Phone number (include country code, no spaces)' and a
placeholder that reads 'e.g. June 2027' are one question each, plus an instruction about the shape of the answer. The answer
engine answers the QUESTION, and refuses a label whose brackets say more than it knows (``plain.py``); this module takes the
instruction out of the label so the question can be answered on its own, and says whether the answer already has the shape the
instruction asks for. What it cannot decide is left to a model that only rewrites a value that is already known
(``local_assist.py``), and whose rewriting is checked against that value (``same_value``): the facts always come from the engine.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

# What a bracket must say to be a writing instruction (English and Spanish). Anything that narrows the QUESTION ("excluding
# internships", "in the UK", "current", "most recent", "full-time") is no instruction and keeps the label as it was.
FORMAT_CUES = re.compile(
    r"\b(?:e\.?\s?g\.?|for example|for instance|such as|example|format|formato|ejemplo|p\.?\s?ej\.?|por ejemplo|"
    r"dd|mm|yy|yyyy|aaaa|digits?|d[ií]gitos?|numeric|num[eé]ric[oa]|numbers? only|solo n[uú]meros|no spaces?|without spaces?|sin espacios|"
    r"no dashes?|no hyphens?|without (?:dashes|hyphens|symbols|punctuation)|sin guiones|country code|c[oó]digo de pa[ií]s|in words|in letters|en letra|"
    r"upper ?case|lower ?case|capital letters|may[uú]sculas|min[uú]sculas|as it appears|as shown|as written|exactly as|tal (?:y )?como|"
    r"abbreviat\w+|abrevia\w+|initials|iniciales|separated by|separad[oa]s por|comma[- ]separated|month and year|mes y a[nñ]o|"
    r"day(?:/| and )month|dd/mm|mm/dd|one word|una palabra|short form|forma corta|full name as|in the form)\b",
    re.I)
# What narrows the question: a bracket with any of these stays part of it.
SCOPE_CUES = re.compile(
    r"\b(?:exclud\w+|except|not including|only if|unless|current(?:ly)?|previous|past|most recent|latest|former|full[- ]time|part[- ]time|"
    r"internships?|placements?|paid|unpaid|uk|u\.k\.|us|u\.s\.|eu|europe\w*|in the|at the|for the|outside|within|if applicable|if any|"
    r"excluyendo|excepto|actual|anterior|[uú]ltim[oa])\b", re.I)
BRACKET = re.compile(r"\s*[\(\[]([^()\[\]]{1,160})[\)\]]\s*")


def instruction_of(label: str) -> str:
    """The text of every bracket of ``label`` that is a writing instruction (joined by '; '), or ''."""
    found = [text.strip() for text in BRACKET.findall(str(label or "")) if _is_instruction(text)]
    return "; ".join(found)


def _is_instruction(text: str) -> bool:
    return bool(FORMAT_CUES.search(text)) and not SCOPE_CUES.search(text)


_DATE_TOKEN = re.compile(r"\b(?:dd|mm|yyyy|yy|aaaa|aa)\b", re.I)
_FILLER = {"format", "formato", "use", "usa", "the", "in", "as", "a", "an", "of", "el", "la", "en", "como", "e", "g", "eg"}


def explained(hint: str) -> bool:
    """Is the hint nothing but date tokens, separators and filler ('MM/YYYY', 'Use the format DD-MM-YYYY')? Then the deterministic
    formatter has already written the answer exactly so, and there is nothing left for a model to read."""
    rest = _DATE_TOKEN.sub(" ", str(hint or "").lower())
    words = re.findall(r"[a-zñáéíóú]+", rest)
    return bool(_DATE_TOKEN.search(str(hint or ""))) and all(word in _FILLER for word in words)


def split_instruction(label: str) -> tuple[str, str]:
    """``(question without its writing instructions, the instructions)``; the label itself and '' when it has none."""
    label = str(label or "")
    instructions = []

    def drop(match):
        if _is_instruction(match.group(1)):
            instructions.append(match.group(1).strip())
            return " "
        return match.group(0)

    core = re.sub(r"\s+", " ", BRACKET.sub(drop, label)).strip(" \t-:–—")
    return (core, "; ".join(instructions)) if instructions and core else (label, "")


# ---- what a field's own hints say --------------------------------------------------------------------------------------
EXAMPLE_PLACEHOLDER = re.compile(r"^\s*(?:e\.?\s?g\.?|ex\.?|example|ejemplo|p\.?\s?ej\.?)[\s:.\-]*(.+)$", re.I)
GENERIC_PLACEHOLDER = re.compile(
    r"^\s*(?:type|enter|write|select|choose|search|your|add|escribe|introduce|selecciona|elige|busca)\b.*|^\s*\.{3}\s*$|^\s*$", re.I)
SHAPE_MONTHS = ("jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october "
                "november december ene abr ago dic enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()


def shape(value: str) -> str:
    """What an example looks like with its letters and digits taken out: '06/2027' and '11/2026' share a shape, 'June 2027' and
    '06/2027' do not. Month names count as digits, so 'June 2027' and 'September 2027' agree."""
    text = str(value or "").strip()
    text = re.sub(r"\b(?:" + "|".join(SHAPE_MONTHS) + r")\.?\b", "9", text, flags=re.I)
    text = re.sub(r"\d+", "9", text)
    text = re.sub(r"[^\W\d_]+", "a", text)
    return re.sub(r"\s+", " ", text)


def example_of(field: dict) -> str:
    """A value the field shows as an example (a placeholder that is one), or ''."""
    placeholder = str((field or {}).get("placeholder") or "").strip()
    if not placeholder or GENERIC_PLACEHOLDER.match(placeholder):
        return ""
    stated = EXAMPLE_PLACEHOLDER.match(placeholder)
    if stated:
        return stated.group(1).strip()
    # a bare example has a digit, a month or a plus sign ("+44 7700 900123", "06/2027", "June 2027"); a bare descriptor does not
    # tell the shape of a value ("Full name", "City")
    return placeholder if re.search(r"\d|\+|\b(?:" + "|".join(SHAPE_MONTHS) + r")\b", placeholder, re.I) else ""


def hints(label: str, field: dict) -> list[str]:
    """Every writing instruction or example the control carries: in the brackets of its label, its description or help text and
    its placeholder (when that is an example)."""
    found = []
    inside = instruction_of(label)
    if inside:
        found.append(inside)
    for key in ("description", "help_text", "instructions", "hint"):
        text = re.sub(r"\s+", " ", str((field or {}).get(key) or "")).strip()
        if text and len(text) <= 240 and FORMAT_CUES.search(text) and not SCOPE_CUES.search(text):
            found.append(text)
    example = example_of(field or {})
    if example:
        found.append("e.g. " + example)
    return list(dict.fromkeys(found))


def needs_rewriting(answer: str, label: str, field: dict, *, known_contract: bool = False) -> list[str]:
    """The hints the answer may not satisfy yet, or [] when it is written as the control asks. A hint that is only an example is
    met when the answer has the example's shape; an instruction in words can only be judged by reading it."""
    if known_contract:
        return []
    pending = []
    for hint in hints(label, field):
        shown = EXAMPLE_PLACEHOLDER.match(hint)
        if shown and shape(shown.group(1)) == shape(answer):
            continue
        pending.append(hint)
    return [hint for hint in pending if not explained(hint)]


def worth_rewriting(answer: str, hint_list: list[str]) -> bool:
    """Can a rewriting of an answer the engine already gave be told right from wrong, and is the answer perhaps in the wrong form?
    A date, a phone number or a quantity can (``same_value``), and so can a name when a hint speaks of case or order; a plain phrase
    with an example ("e.g. Computer Science") says nothing a model could improve, so the engine's answer stands and no model works."""
    if parse_date(answer) or looks_like_phone(answer):
        return True
    from .quantities import parse_quantity
    if parse_quantity(answer):
        return True
    said = " ".join(hint_list).lower()
    if violations(answer, hint_list):
        return True
    return bool(re.search(r"\b(?:surname|last name first|family name first|initials?|capital|upper ?case|lower ?case|may[uú]sculas|min[uú]sculas|apellido)\b", said))


# ---- is it the same value? ---------------------------------------------------------------------------------------------
MONTHS = {name: number for number, names in enumerate([
    ("january", "jan", "enero", "ene"), ("february", "feb", "febrero"), ("march", "mar", "marzo"), ("april", "apr", "abril", "abr"),
    ("may", "mayo"), ("june", "jun", "junio"), ("july", "jul", "julio"), ("august", "aug", "agosto", "ago"),
    ("september", "sep", "sept", "septiembre"), ("october", "oct", "octubre"), ("november", "nov", "noviembre"),
    ("december", "dec", "diciembre", "dic")], start=1) for name in names}
MONTH_WORD = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")"
ORDER_WORDS = {"day": re.compile(r"\b(?:day|dd|d[ií]a)\b", re.I), "month": re.compile(r"\b(?:month|mm|mes)\b", re.I),
               "year": re.compile(r"\b(?:year|yyyy|yy|a[nñ]o|aaaa)\b", re.I)}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().casefold()


def parse_date(text: str, *, order: str | None = None) -> tuple[int, int | None, int | None] | None:
    """``(year, month|None, day|None)`` of a date as people write it, or None. ``order`` ('dmy', 'mdy', 'ymd') settles a numeric
    date whose day and month could be either; without it, such a date is read only when it is unambiguous."""
    value = _ascii(text).strip().strip(".")
    value = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", value)
    value = re.sub(r"\b(?:of|de|del)\b", " ", value)
    value = re.sub(r"\s+", " ", value)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if match:
        return _checked(int(match[1]), int(match[2]), int(match[3]))
    match = re.fullmatch(r"(\d{4})[/.\-](\d{1,2})", value)
    if match:
        return _checked(int(match[1]), int(match[2]), None)
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{4})", value)
    if match:
        return _checked(int(match[2]), int(match[1]), None)
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{2})", value)  # 06/27 is a month and a two-digit year only when the form says so
    if match and order == "my":
        return _checked(2000 + int(match[2]), int(match[1]), None)
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})", value)
    if match:
        first, second, year = int(match[1]), int(match[2]), int(match[3]) if len(match[3]) == 4 else 2000 + int(match[3])
        if order == "mdy" or (order is None and first <= 12 and second > 12):
            return _checked(year, first, second)
        if order == "dmy" or (order is None and first > 12 and second <= 12):
            return _checked(year, second, first)
        if order is None and first == second:
            return _checked(year, first, second)
        return None
    match = re.fullmatch(rf"(\d{{1,2}}) ?({MONTH_WORD}),? (\d{{4}})", value)
    if match:
        return _checked(int(match[3]), MONTHS[match[2]], int(match[1]))
    match = re.fullmatch(rf"({MONTH_WORD}),? (\d{{1,2}}),? (\d{{4}})", value)
    if match:
        return _checked(int(match[3]), MONTHS[match[1]], int(match[2]))
    match = re.fullmatch(rf"({MONTH_WORD}),?[ ./\-']*(\d{{4}}|\d{{2}})", value)
    if match:
        year = int(match[2]) if len(match[2]) == 4 else 2000 + int(match[2])
        return _checked(year, MONTHS[match[1]], None)
    match = re.fullmatch(rf"(\d{{4}})[ ./\-]*({MONTH_WORD})", value)
    if match:
        return _checked(int(match[1]), MONTHS[match[2]], None)
    match = re.fullmatch(r"(\d{4})", value)
    if match and 1900 <= int(match[1]) <= 2200:
        return int(match[1]), None, None
    return None


def _checked(year: int, month: int | None, day: int | None):
    if not 1900 <= year <= 2200 or (month is not None and not 1 <= month <= 12):
        return None
    if day is not None:
        try:
            date(year, month or 1, day)
        except ValueError:
            return None
    return year, month, day


TOKEN_RUN = re.compile(r"\b(dd|mm|yyyy|yy|aaaa|aa)\s*[/.\-]\s*(dd|mm|yyyy|yy|aaaa|aa)(?:\s*[/.\-]\s*(dd|mm|yyyy|yy|aaaa|aa))?\b", re.I)


def date_order(instruction: str) -> str | None:
    """'dmy', 'mdy' or 'ymd' when the instruction names day, month and year in an order, else None."""
    text = str(instruction or "")
    run = TOKEN_RUN.search(text)
    if run:  # "dd/mm/yy" says it exactly; the words around it ("two-digit year") say nothing of the order
        letters = "".join({"d": "d", "m": "m", "y": "y", "a": "y"}[token[0].lower()] for token in run.groups() if token)
        if letters in {"dmy", "mdy", "ymd", "my", "ym", "dm", "md"}:
            return letters
    spots = {name: match.start() for name, pattern in ORDER_WORDS.items() if (match := pattern.search(text))}
    if len(spots) < 2:
        return None
    ordered = "".join(name[0] for name, _ in sorted(spots.items(), key=lambda item: item[1]))
    return ordered if ordered in {"dmy", "mdy", "ymd", "my", "ym", "dm", "md"} else None


def same_value(base: str, written: str, *, instruction: str = "") -> bool:
    """Is ``written`` the value ``base`` in another form? Only changes of WRITING count: a date in another layout or with fewer
    parts (never more: a month or a day that ``base`` does not have is a fact someone made up), a number in another unit, a phone
    number without its country code (never with one it lacked), letter case, punctuation, spacing, and the order of the words
    of a name. Anything else is a different value, however plausible."""
    base, written = str(base or "").strip(), str(written or "").strip()
    if not base or not written:
        return False
    if _ascii(base) == _ascii(written):
        return True
    order = date_order(instruction)
    wanted = parse_date(base, order=order)
    got = parse_date(written, order=order)
    if wanted and got:
        year, month, day = got
        return year == wanted[0] and (month is None or month == wanted[1]) and (day is None or day == wanted[2]) \
            and (month is not None or day is None)
    if wanted or got:
        return False
    phone_base, phone_written = re.sub(r"\D", "", base), re.sub(r"\D", "", written)
    if looks_like_phone(base) and looks_like_phone(written):
        return same_phone(phone_base, phone_written, base.strip().startswith("+") or base.strip().startswith("00"))
    if re.fullmatch(r"[\d\s.,+\-()]+", written) and re.fullmatch(r"[\d\s.,+\-()]+", base) and phone_base == phone_written:
        return True
    from .quantities import UNIT_DAYS, parse_quantity
    one, two = parse_quantity(base), parse_quantity(written)
    if one and two:
        if one[1] == two[1] or one[1] is None or two[1] is None:
            return abs(one[0] - two[0]) < 1e-9
        return abs(one[0] * UNIT_DAYS[one[1]] - two[0] * UNIT_DAYS[two[1]]) < 1e-6
    words = lambda text: sorted(re.findall(r"[a-z0-9]+", _ascii(text)))  # noqa: E731
    return words(base) == words(written) and bool(words(base))


def looks_like_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", str(text))
    return 7 <= len(digits) <= 15 and bool(re.fullmatch(r"[\d\s()+\-.]+", str(text).strip()))


def same_phone(base_digits: str, written_digits: str, base_has_country_code: bool) -> bool:
    """A number written with or without the code of its country and its trunk zero, never given a code it did not have."""
    base_digits = base_digits[2:] if base_digits.startswith("00") else base_digits
    written_digits = written_digits[2:] if written_digits.startswith("00") else written_digits
    if base_digits == written_digits:
        return True
    if not base_has_country_code:
        return False
    national = written_digits.lstrip("0")
    return len(national) >= 7 and base_digits.endswith(national) and len(base_digits) - len(national) <= 3


ENGLISH_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def parts_of(value: str) -> dict:
    """What a model needs to WRITE the value in another form without having to read it: the numbers of a date, the digits of a phone
    number, the words of a name. (Reading is where a small model goes wrong; composing from parts it does well. Measured: more
    parts, such as a two-digit year or the month in Spanish, made the writing model worse, not better: 24 of 26 instructions
    against 19.)"""
    parsed = parse_date(value)
    if parsed:
        year, month, day = parsed
        found = {"year": year}
        if month:
            found.update(month_number=f"{month:02d}", month_name=ENGLISH_MONTHS[month - 1], month_abbreviation=ENGLISH_MONTHS[month - 1][:3])
        if day:
            found.update(day_number=f"{day:02d}")
        return {"date": found}
    if looks_like_phone(value):
        digits = re.sub(r"\D", "", value)
        return {"phone_digits": digits, "starts_with_plus": value.strip().startswith("+")}
    words = re.findall(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*", str(value))
    if 2 <= len(words) <= 6 and not re.search(r"\d", str(value)):
        return {"words_in_order": words}
    return {}


# ---- the layout an instruction or an example asks for -------------------------------------------------------------------
FULL_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
               "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"}
ABBREVIATED_MONTHS = {"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "ene", "abr", "ago", "dic"}


def layout(text: str) -> list[tuple]:
    """A value as the shape of its parts: ('num', digits) ('month', 'full'|'abbr') ('word',) and the punctuation between them.
    "2 November 2026" and "15 June 2027" have one layout; "Nov 2026" and "Nov 26" do not (two digits of year against four)."""
    parts = []
    for token in re.findall(r"\d+|[^\W\d_]+|[^\w\s]", str(text or "")):
        if token.isdigit():
            parts.append(("num", len(token)))
        elif token.isalpha():
            lowered = _ascii(token)
            parts.append(("month", "full") if lowered in FULL_MONTHS else ("month", "abbr") if lowered.rstrip(".") in ABBREVIATED_MONTHS
                         else ("word",))
        else:
            parts.append(("punct", token))
    return parts


def layout_matches(example: str, written: str) -> bool:
    """Is ``written`` laid out like ``example``? Numbers may differ by a zero of padding when both are one or two digits (a day or a
    month), never otherwise (a year of two digits is not one of four); month names match by kind (full or abbreviated)."""
    one, two = layout(example), layout(written)
    if len(one) != len(two):
        return False
    for a, b in zip(one, two):
        if a[0] != b[0]:
            return False
        if a[0] == "num" and a[1] != b[1] and not (a[1] <= 2 and b[1] <= 2):
            return False
        if a[0] in {"month", "punct"} and a != b:
            return False
    return True


def has_layout(example: str) -> bool:
    """An example that shows a layout (it holds a number or a month); 'Computer Science' shows only that something is wanted."""
    return any(part[0] in {"num", "month"} for part in layout(example))


def violations(written: str, hint_list: list[str]) -> list[str]:
    """What the written value breaks of what the hints say in words, in the words a second attempt can be told."""
    text, found = str(written), []
    said = " ".join(hint_list).lower()
    shaped = TOKEN_RUN.search(said) or any((m := EXAMPLE_PLACEHOLDER.match(h)) and has_layout(m.group(1)) for h in hint_list)
    if not shaped and re.search(r"\b(?:digits only|only digits|numbers only|only numbers|numeric only|solo n[uú]meros|solo d[ií]gitos)\b", said) \
            and not re.fullmatch(r"\d+", text):
        found.append("it must be digits only (no plus sign, spaces, letters or punctuation)")
    if re.search(r"\b(?:no|without|sin) spaces?\b|\bsin espacios\b", said) and re.search(r"\s", text):
        found.append("it must have no spaces")
    if re.search(r"\b(?:no|without) (?:dashes|hyphens)\b|\bsin guiones\b", said) and "-" in text:
        found.append("it must have no dashes")
    if re.search(r"\b(?:no|without) (?:symbols|special characters|punctuation)\b", said) and re.search(r"[^\w\s]", text):
        found.append("it must have no symbols or punctuation")
    if re.search(r"\b(?:capital letters|upper ?case|may[uú]sculas)\b", said) and text != text.upper():
        found.append("it must be in capital letters")
    if re.search(r"\b(?:lower ?case|min[uú]sculas)\b", said) and text != text.lower():
        found.append("it must be in lower case")
    for run in TOKEN_RUN.finditer(" ".join(hint_list)):
        # "dd/mm/yy" is a layout too: two digits, a slash, two digits, a slash, two digits
        drawn = re.sub(r"yyyy|aaaa", "9999", run.group(0), flags=re.I)
        drawn = re.sub(r"dd|mm|yy|aa", "99", drawn, flags=re.I)
        if not layout_matches(drawn, text):
            found.append(f"it must be laid out as {run.group(0)}")
    for hint in hint_list:
        shown = EXAMPLE_PLACEHOLDER.match(hint)
        if shown and has_layout(shown.group(1)) and not layout_matches(shown.group(1), text):
            found.append(f"it must be laid out like the example {shown.group(1).strip()!r} (same parts, same separators, same number of digits)")
    return found
