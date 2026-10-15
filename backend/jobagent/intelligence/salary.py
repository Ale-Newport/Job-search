"""Does a salary question ask for the figure the user saved?

The salary policy (``salary_expected`` ...) is ONE number: an annual gross figure in ONE currency. A field that asks for
another currency ('Desired salary in EUR'), another period ('Expected monthly salary'), another unit ('in thousands'),
a range, a whole package ('total compensation incl. bonus and equity') or a net figure would be handed the wrong number
(60000 into a monthly field is twelve times too much), and nothing here converts: no exchange rate, no 12, no guess. The
answer is then "needs you" with the exact reason, and the saved figure is shown only as a candidate for the user to check.

The wording is read from the question and from what the field itself says about its value (its hint, placeholder and
instructions). It is read as text, never executed, in English and Spanish.
"""

from __future__ import annotations

import re

from ..automation.answers import fold

CURRENCIES = {
    "GBP": r"\bgbp\b|£|\bpounds?\b|\bsterling\b|\blibras?\b", "EUR": r"\beur\b|€|\beuros?\b",
    "USD": r"\busd\b|\bus ?\$|\bus dollars?\b|\bdolares? (?:estadounidenses?|americanos?)\b",
    "CAD": r"\bcad\b|\bcanadian dollars?\b|\bdolares? canadienses?\b", "AUD": r"\baud\b|\baustralian dollars?\b",
    "CHF": r"\bchf\b|\bfrancs?\b|\bfrancos?\b", "SEK": r"\bsek\b|\bkronor\b", "NOK": r"\bnok\b", "DKK": r"\bdkk\b",
    "PLN": r"\bpln\b|\bzloty\b", "INR": r"\binr\b|₹|\brupees?\b|\brupias?\b", "JPY": r"\bjpy\b|¥|\byen\b",
    "CNY": r"\bcny\b|\brmb\b|\byuan\b|\byuanes\b", "BRL": r"\bbrl\b|\breais\b|\breales\b", "MXN": r"\bmxn\b|\bpesos? mexicanos?\b",
    "SGD": r"\bsgd\b", "HKD": r"\bhkd\b", "NZD": r"\bnzd\b", "AED": r"\baed\b|\bdirhams?\b|\bdirhames\b",
}
# 'dollars' and '$' do not say which dollar: a policy in any dollar currency may answer them, one in pounds may not.
DOLLARS = {"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN"}
ANY_DOLLAR = re.compile(r"\$|\bdollars?\b|\bdolares?\b")

PERIODS = {
    "annual": re.compile(r"\b(?:annual(?:ly)?|yearly|per (?:annum|year|yr)|(?:a|each|every|per) (?:year|annum)|anual(?:es)?|"
                         r"al ano|por ano|cada ano)\b|(?<!\w)p\.? ?a\b\.?|/\s*(?:year|yr|annum|ano)\b"),
    "monthly": re.compile(r"\b(?:monthly|per month|(?:a|each|every|per) month|pcm|mensual(?:es)?|al mes|por mes|cada mes)\b|"
                          r"/\s*(?:month|mo|mes)\b"),
    "fortnightly": re.compile(r"\b(?:fortnightly|bi-?weekly|per fortnight|(?:a|each|every) fortnight|quincenal(?:es)?|cada quincena)\b"),
    "weekly": re.compile(r"\b(?:weekly|per week|(?:a|each|every) week|semanal(?:es)?|a la semana|por semana|cada semana)\b|"
                         r"/\s*(?:week|wk|semana)\b"),
    "daily": re.compile(r"\b(?:daily|day rate|per diem|per day|(?:a|each|every) day|diari[oa]s?|al dia|por dia|cada dia)\b|"
                        r"/\s*(?:day|dia)\b"),
    "hourly": re.compile(r"\b(?:hourly|per hour|(?:an|each|every) hour|por hora|a la hora|cada hora|por horas?)\b|"
                         r"/\s*(?:hour|hr|h|hora)\b"),
}
UNIT = re.compile(r"\bin (?:thousands|millions|k|m)\b|\((?:in |en )?[£$€]?(?:k|m|000s?|thousands|millions|miles|millones)\)|\d\s*k\b|\bthousands\b|\bmillions\b"
                  r"|[£$€]\s*k\b|\bk\s*[£$€]|[£$€]\s*000s?\b|\b000s\b|\(\s*[£$€]\s*(?:000s?|k|m)\s*\)|\ben (?:miles|millones|k)\b|"
                  r"\((?:en )?(?:miles|millones)\)|\b(?:miles|millones) de\b|\bin (?:£|\$|€)?000s?\b")
# a figure for part of a job, not for the whole one: pro rata, part-time, a full-time equivalent, a day rate of a contractor
BASIS = re.compile(r"\bpro[- ]?rata\b|\bpart[- ]?time\b|\bfte\b|\bfull[- ]?time equivalent\b|\bequivalent to full[- ]?time\b|\bprorrateado\b|"
                   r"\bjornada parcial\b|\btiempo parcial\b|\bmedia jornada\b|\bproporcional\b")
SHAPE = re.compile(r"\b(?:range|band|bracket|horquilla|rango)\b|\b(?:between|entre) [\d£$€]|"
                   r"\b(?:minimum|maximum|min|max|minimo|maximo) ?(?:and|/|-|&|to|y|a) ?(?:minimum|maximum|min|max|minimo|maximo)\b|"
                   r"\blower and upper\b")
PACKAGE = re.compile(r"\btotal (?:compensation|package|remuneration|comp|pay|reward|earnings)\b|\bote\b|\bon[- ]target\b|"
                     r"\b(?:including|incl\.?|inclusive of|plus|and) (?:a |any |the )?(?:bonus(?:es)?|commissions?|equity|stock|"
                     r"shares|options|benefits|variable)\b|\bbase\b.{0,12}\b(?:bonus|equity|stock|commission)\b|"
                     r"\b(?:retribucion|compensacion|remuneracion|paquete(?: salarial| retributivo)?|salario) total\b|"
                     r"\b(?:fijo|base) (?:mas|\+|y) (?:variable|bonus|comisiones)\b|"
                     r"\b(?:incluyendo|incluido|incluye|mas|y) (?:el |la |los |las )?(?:bonus|bonificacion(?:es)?|variable|"
                     r"comisiones|acciones|beneficios|incentivos)\b")
NET = re.compile(r"\bnet\b|\btake[- ]home\b|\b(?:after|post)[- ]tax\b|\bneto\b|\bliquido\b|\bdespues de impuestos\b|\btras impuestos\b")
# 'What are your salary expectations and why?', 'Describe your salary expectations': a figure is not the explanation asked for.
EXPLAIN = re.compile(r"\b(?:and|y) (?:why|how|por que|como)\b|\b(?:explain|justify|describe|elaborate|rationale|explica\w*|justifica\w*|"
                     r"describ\w+|breakdown|desglose|why|por que)\b|\btell us (?:about|more|how)\b|"
                     r"\b(?:provide|give) (?:us )?(?:details|an explanation|a breakdown|reasons?|more)\b")
QUESTION_KEYS = ("description", "help_text", "instructions", "placeholder", "validation_message")


plain = fold


def named_currencies(asked: str) -> set[str]:
    return {code for code, pattern in CURRENCIES.items() if re.search(pattern, asked)}


def named_periods(asked: str) -> set[str]:
    return {period for period, pattern in PERIODS.items() if pattern.search(asked)}


SYNONYMS = {
    "annual": {"annual", "annually", "yearly", "year", "per year", "per annum", "pa", "p a", "anual", "ano"},
    "monthly": {"monthly", "month", "per month", "pcm", "mensual", "mes"},
    "fortnightly": {"fortnightly", "fortnight", "biweekly", "quincenal"},
    "weekly": {"weekly", "week", "per week", "semanal", "semana"},
    "daily": {"daily", "day", "per day", "diario", "dia"},
    "hourly": {"hourly", "hour", "per hour", "por hora", "hora"},
}


def canonical_period(value) -> str:
    """The period of a stored figure: annual unless the policy says otherwise. A period nobody here knows (quarterly...)
    is kept as written, so that nothing is ever taken to be the same as it."""
    text = re.sub(r"[^a-z ]", "", plain(value)).strip()
    return next((period for period, words in SYNONYMS.items() if text in words), text or "annual")


def mismatch(question: str, field: dict | None, policy) -> str | None:
    """None when the question asks for the figure the policy holds, else the one word that says what differs:
    currency, period, unit, range, package or basis."""
    value = policy if isinstance(policy, dict) else {}
    field = field or {}
    asked = plain(" ".join([str(question or ""), *(str(field.get(key) or "") for key in QUESTION_KEYS)]))
    named = named_currencies(asked)
    held = str(value.get("currency") or "").upper()
    if named - {held} or (named and not held):
        return "currency"  # another currency than the saved one (or the saved one has none: which currency is it?)
    if ANY_DOLLAR.search(asked) and not named & DOLLARS and held not in DOLLARS:
        return "currency"  # '$' or 'dollars' without saying which: any dollar currency will do, a pound will not
    periods = named_periods(asked)
    wanted = canonical_period(value.get("period")) if value.get("period") else "annual"
    if (periods or {"annual"}) != {wanted}:
        return "period"
    if UNIT.search(asked):
        return "unit"
    if SHAPE.search(asked):
        return "range"
    if PACKAGE.search(asked):
        return "package"
    if NET.search(asked) or BASIS.search(asked):
        return "basis"
    if EXPLAIN.search(asked):
        return "explanation"
    return None


def explanation(problem: str, policy) -> str:
    """The sentence the user reads: what was saved, and what this field asks for instead."""
    value = policy if isinstance(policy, dict) else {}
    held = str(value.get("currency") or "").upper()
    period = canonical_period(value.get("period")) if value.get("period") else "annual"
    saved = f"one {period} figure" + (f" in {held}" if held else " in one currency")
    asks = {"basis": "a net (after tax), pro rata or part-time figure", "explanation": "an explanation as well as a figure"}.get(problem, f"another {problem}")
    return f"Your saved salary expectation is {saved}; this field asks for {asks}"
