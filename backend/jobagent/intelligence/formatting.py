from __future__ import annotations
import calendar
from datetime import date, datetime
from functools import lru_cache
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlparse
from ..automation.answers import normalize, option_for
from .quantities import bucket_option

EQUIVALENTS = [
    {"first", "first class", "first class honours", "1st", "1", "first 1st", "1st class honours"},
    {"bachelor", "bachelor s", "bachelor s degree", "bachelors degree", "bsc", "bs", "undergraduate",
     "grado", "grado universitario", "licenciatura", "graduado universitario"},
    {"master", "master s", "master s degree", "masters degree", "msc", "ms", "postgraduate",
     "master universitario", "maestria", "posgrado", "postgrado"},
    {"united kingdom", "uk", "gb", "great britain", "reino unido", "gran bretana"},
    {"spain", "es", "esp", "espana"},
    {"spanish", "spanish citizenship", "espanol", "espanola", "nacionalidad espanola", "ciudadania espanola"},
    {"none", "not applicable", "n a", "0", "no notice", "zero", "0 days", "ninguno", "ninguna", "no aplica", "sin preaviso"},
    {"other", "otro", "otra", "otros"},
    {
        "fluent",
        "professional working proficiency",
        "fluent professional",
        "full professional proficiency",
        "fluent professional working proficiency",
        "fluent native",
        "fluent or native",
    },
    {"native", "nativo", "nativa", "lengua materna", "idioma materno"},
    {
        "prefer not to say",
        "decline to answer",
        "i do not wish to answer",
        "prefer not to disclose",
        "i don t wish to answer",
        "prefiero no decirlo",
        "prefiero no responder",
        "prefiero no contestar",
        "prefiero no indicarlo",
        "prefiero no especificar",
        "no deseo responder",
        "no deseo contestar",
        "no quiero responder",
    },
]


DATE_TOKENS = {"DD": "%d", "MM": "%m", "YYYY": "%Y", "AAAA": "%Y", "YY": "%y", "AA": "%y"}
_TOKEN = r"(?:DD|MM|YYYY|AAAA|YY|AA)"
GENERAL_DATE = re.compile(rf"(?<![A-Za-z]){_TOKEN}(?:\s*[/\-.]\s*{_TOKEN}){{1,2}}(?![A-Za-z])", re.I)
NAMED_DATES = {"%d/%m/%Y": "DD/MM/YYYY", "%m/%d/%Y": "MM/DD/YYYY", "%Y-%m-%d": "ISO_DATE", "%m/%Y": "MM/YYYY",
               "%m/%y": "MM/YY", "%Y-%m": "YYYY-MM"}
_DATE_PARTS = {"%d": r"(?:0[1-9]|[12]\d|3[01])", "%m": r"(?:0[1-9]|1[0-2])", "%Y": r"\d{4}", "%y": r"\d{2}"}


def written_date_pattern(strftime_format: str) -> str:
    """The text a ``DATE:<strftime>`` contract allows (zero-padded numbers, the separators as written)."""
    return "".join(_DATE_PARTS.get(part, re.escape(part)) for part in re.split(r"(%[dmYy])", strftime_format) if part)


class AnswerFormatResolver:
    """Infer a value contract from field metadata and visible instructions.

    Date precision is evidence: formatting a month must never manufacture a day.
    Native input contracts take precedence because the browser enforces them.
    """

    @staticmethod
    def infer(question, field=None):
        field = field or {}
        kind = str(field.get("type") or "").lower()
        text = " ".join(str(x or "") for x in (
            question, field.get("label"), field.get("description"), field.get("help_text"),
            field.get("placeholder"), field.get("instructions"), field.get("validation_message"),
            field.get("pattern"),
        ))
        # A research link is source material, not an instruction to enter a URL.
        text = re.sub(r"https?://\S+", "", text)
        if kind == "file":
            return "FILE_UPLOAD"
        if field.get("options"):
            return "MULTIPLE_CHOICE" if (field.get("multiple") or field.get("multiselect") or re.search(
                r"check all|select all|choose all|pick 1.?2|select (?:up to )?two", text, re.I
            )) else "SINGLE_CHOICE"
        if kind == "date":
            return "ISO_DATE"
        if kind == "month":
            return "YYYY-MM"
        # A date written as tokens in ANY order and with any of / - . between them (dd-mm-yyyy, MM.YYYY, YYYY/MM/DD, dd.mm.yy): the
        # tokens say how it is to be written, so that is the contract (the named ones below stay what they were).
        written = GENERAL_DATE.search(text)
        if written:
            pattern = "".join(DATE_TOKENS[token.upper()] if token.upper() in DATE_TOKENS else token
                              for token in re.split(r"([/\-.])", re.sub(r"\s+", "", written.group(0))))
            return NAMED_DATES.get(pattern, "DATE:" + pattern)
        # Match the most precise explicit patterns before their substrings.
        for pattern, output in (
            (r"\bDD\s*/\s*MM\s*/\s*(?:YYYY|AAAA)\b", "DD/MM/YYYY"),
            (r"\bMM\s*/\s*DD\s*/\s*YYYY\b", "MM/DD/YYYY"),
            (r"\b(?:YYYY|AAAA)\s*-\s*MM\s*-\s*DD\b|\bISO(?: 8601)?(?: date)?\b", "ISO_DATE"),
            (r"\bMM\s*/\s*(?:YYYY|AAAA)\b|\bmonth\s*/\s*year\b|\bmes\s*/\s*a[nñ]o\b", "MM/YYYY"),
            (r"\bMM\s*/\s*(?:YY|AA)\b", "MM/YY"),
            (r"\b(?:YYYY|AAAA)\s*-\s*MM\b", "YYYY-MM"),
            (r"\bMonth\s+Year\b", "MONTH_YEAR"),
            (r"\bYYYY\b|\bAAAA\b|\b(?:graduation|graduating|completion|leaving) year\b|\byear\b.*\b(?:graduat\w*|finish\w*|complet\w*|start\w*)\b"
             r"|\ba[nñ]o de (?:graduaci[oó]n|finalizaci[oó]n|fin|egreso)\b|\ba[nñ]o\b.*\b(?:graduar\w*|finaliz\w*|termin\w*|inici\w*)\b", "YYYY"),
            (r"(?:graduation|graduating|completion|start|available).*\bmonth\b(?!.*year)", "MONTH"),
        ):
            if re.search(pattern, text, re.I):
                return output
        # HTML patterns often express the same contract without human tokens.
        # Three slash-separated components have ambiguous locale and require
        # an explicit date-order instruction; a two-component month/year does
        # not have that ambiguity.
        pattern = str(field.get("pattern") or "")
        if re.fullmatch(r"\\d\{2\}/\\d\{4\}|\[0-9\]\{2\}/\[0-9\]\{4\}", pattern.strip("^$")):
            return "MM/YYYY"
        if re.fullmatch(r"\\d\{4\}-\\d\{2\}|\[0-9\]\{4\}-\[0-9\]\{2\}", pattern.strip("^$")):
            return "YYYY-MM"
        if kind == "number":
            return "INTEGER" if str(field.get("step")) == "1" or re.search(r"whole number|integer", text, re.I) else "DECIMAL"
        if kind == "url" or (kind != "textarea" and field.get("tag") != "textarea" and re.search(r"\bURL\b", text)):
            return "URL"
        if kind == "email":
            return "EMAIL"
        if re.search(r"\bcountry code\b|\bc[oó]digo de pa[ií]s\b", text, re.I):
            return "COUNTRY_CODE"
        if kind == "tel" or re.search(r"\bphone|mobile|telephone|\btel[eé]fono|\bm[oó]vil\b", text, re.I):
            return "PHONE"
        if re.search(r"numeric|decimal|number only|digits only", text, re.I):
            return "PERCENTAGE_NUMBER" if "%" in text or re.search(r"percent", text, re.I) else "DECIMAL"
        if re.search(r"percent|percentage|\(\s*%\s*\)", text, re.I):
            return "PERCENTAGE"
        if re.search(r"\bcurrency\b|\b(?:GBP|USD|EUR)\b|[£$€]", text):
            return "CURRENCY"
        if re.search(r"\byes\s*/\s*no\b", text, re.I):
            return "YES_NO"
        if kind == "checkbox":
            return "BOOLEAN"
        return "LONG_TEXT" if field.get("tag") == "textarea" else "SHORT_TEXT"

    @classmethod
    def date(cls, value, question, field=None):
        field = field or {}
        contract = cls.infer(question, {**field, "options": []})
        raw = value.isoformat() if isinstance(value, date) else str(value)
        precision = "day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else "month" if re.fullmatch(r"\d{4}-\d{2}", raw) else "year" if re.fullmatch(r"\d{4}", raw) else None
        try:
            parsed = date.fromisoformat(raw + ("-01-01" if precision == "year" else "-01" if precision == "month" else ""))
        except ValueError:
            return None, "ANSWER_FORMAT_ERROR"
        if contract.startswith("DATE:"):
            # a contract made of tokens: a day needs a day, a month needs a month; a year is always there
            written = contract[5:]
            if "%d" in written and precision != "day":
                return None, "MISSING_EXACT_DATE"
            if "%m" in written and precision == "year":
                return None, "MISSING_DATE_MONTH"
            return parsed.strftime(written), None
        if contract in {"ISO_DATE", "DD/MM/YYYY", "MM/DD/YYYY"} and precision != "day":
            return None, "MISSING_EXACT_DATE"
        if contract in {"MM/YYYY", "MM/YY", "YYYY-MM", "MONTH_YEAR", "MONTH"} and precision == "year":
            return None, "MISSING_DATE_MONTH"
        formats = {
            "YYYY": "%Y", "MM/YYYY": "%m/%Y", "MM/YY": "%m/%y", "YYYY-MM": "%Y-%m",
            "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y", "ISO_DATE": "%Y-%m-%d",
            "MONTH_YEAR": "%B %Y", "MONTH": "%B",
        }
        fallback = "%d %B %Y" if precision == "day" else "%B %Y" if precision == "month" else "%Y"
        return parsed.strftime(formats.get(contract, fallback)), None

    @classmethod
    def validate(cls, value, question, field=None):
        field = field or {}
        contract = cls.infer(question, field)
        text = str(value)
        patterns = {
            "YYYY": r"\d{4}", "MM/YYYY": r"(?:0[1-9]|1[0-2])/\d{4}",
            "MM/YY": r"(?:0[1-9]|1[0-2])/\d{2}", "YYYY-MM": r"\d{4}-(?:0[1-9]|1[0-2])",
            "ISO_DATE": r"\d{4}-\d{2}-\d{2}", "DD/MM/YYYY": r"\d{2}/\d{2}/\d{4}",
            "MM/DD/YYYY": r"\d{2}/\d{2}/\d{4}", "INTEGER": r"-?\d+", "DECIMAL": r"-?\d+(?:\.\d+)?",
            "PERCENTAGE_NUMBER": r"\d+(?:\.\d+)?", "PERCENTAGE": r"\d+(?:\.\d+)?%?",
            "EMAIL": r"[^\s@]+@[^\s@]+\.[^\s@]+", "PHONE": r"\+?[\d ()\-\.]{5,}",
            "COUNTRY_CODE": r"(?:[A-Za-z]{2,3}|\+\d{1,4})", "YES_NO": r"(?:Yes|No)",
            "BOOLEAN": r"(?:Yes|No|True|False|true|false)",
            "CURRENCY": r"(?:[£$€]\s*)?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[A-Z]{3})?",
            "MONTH_YEAR": r"(?:" + "|".join(calendar.month_name[1:]) + r") \d{4}",
            "MONTH": r"(?:" + "|".join(calendar.month_name[1:]) + r")",
        }
        if contract in patterns and not re.fullmatch(patterns[contract], text):
            return "ANSWER_FORMAT_ERROR"
        if contract.startswith("DATE:"):
            written = contract[5:]
            if not re.fullmatch(written_date_pattern(written), text):
                return "ANSWER_FORMAT_ERROR"
            if "%d" in written:
                try:
                    datetime.strptime(text, written)
                except ValueError:
                    return "ANSWER_FORMAT_ERROR"
        if contract == "URL":
            parsed = urlparse(text)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or re.search(r"\s", text):
                return "ANSWER_FORMAT_ERROR"
        if contract == "ISO_DATE":
            try:
                date.fromisoformat(text)
            except ValueError:
                return "ANSWER_FORMAT_ERROR"
        if field.get("type") in {"date", "month"}:
            if field.get("min") not in (None, "") and text < str(field["min"]):
                return "ANSWER_FORMAT_ERROR"
            if field.get("max") not in (None, "") and text > str(field["max"]):
                return "ANSWER_FORMAT_ERROR"
        if contract in {"DD/MM/YYYY", "MM/DD/YYYY"}:
            first, second, year = map(int, text.split("/"))
            try:
                date(year, second if contract == "DD/MM/YYYY" else first, first if contract == "DD/MM/YYYY" else second)
            except ValueError:
                return "ANSWER_FORMAT_ERROR"
        if field.get("pattern") and contract not in {"SINGLE_CHOICE", "MULTIPLE_CHOICE"}:
            try:
                if not re.fullmatch(field["pattern"], text):
                    return "ANSWER_FORMAT_ERROR"
            except re.error:
                return "UNSUPPORTED_FIELD_PATTERN"
        if contract in {"INTEGER", "DECIMAL", "PERCENTAGE_NUMBER", "PERCENTAGE"}:
            try:
                number = Decimal(text.rstrip("%"))
                if not number.is_finite():
                    return "ANSWER_FORMAT_ERROR"
                if contract.startswith("PERCENTAGE") and not 0 <= number <= 100:
                    return "ANSWER_FORMAT_ERROR"
                for boundary, comparator in (("min", lambda v: number < v), ("max", lambda v: number > v)):
                    if field.get(boundary) not in ("", None) and comparator(Decimal(str(field[boundary]))):
                        return "ANSWER_FORMAT_ERROR"
            except InvalidOperation:
                return "ANSWER_FORMAT_ERROR"
        return None


infer_format = AnswerFormatResolver.infer
format_date = AnswerFormatResolver.date
validate_format = AnswerFormatResolver.validate


@lru_cache(maxsize=1 << 12)
def _question_concept(question):
    from ..automation.disclosures import disclosure_profile_key

    return disclosure_profile_key(question)


def question_concept(question):
    """The one personal attribute a question asks for (``disclosure_profile_key``), memoised by the question text: an
    option menu asks it once per option and the answer never depends on anything else."""
    if type(question) is str and len(question) <= 512:
        return _question_concept(question)
    from ..automation.disclosures import disclosure_profile_key

    return disclosure_profile_key(question)


def choice(value, options, *, question="", disclosure_concept=None):
    concept = disclosure_concept or question_concept(question)
    # A hidden machine code cannot establish an option's meaning.
    # Match the displayed label; preserve the original DOM value for execution.
    labelled = [{**o, 'value': o.get('label', ''), '_original': o} for o in options]
    result = option_for(str(value), labelled, question=question)
    if result:
        return result.get('_original', result)
    norm = normalize(value)
    labels = [
        o
        for o in options
        if not o.get("disabled") and (o.get("role") == "option" or o.get("value", o.get("label")) not in ("", None))
    ]
    aliases = {
        "pronouns": [
            {"he", "he him", "he him his"},
            {"she", "she her", "she her hers"},
            {"they", "they them", "they them theirs"},
        ],
        "gender": [{"man", "male", "hombre"}, {"woman", "female", "mujer"},
                   {"non binary", "nonbinary", "no binario", "no binaria", "no binarie"}],
        "sex": [{"male", "masculino", "hombre"}, {"female", "femenino", "mujer"}],
        "sexual_orientation": [{"heterosexual", "straight", "heterosexual straight", "straight heterosexual"}],
        "disability": [
            {'yes', 'disabled', 'i am disabled', 'i have a disability', 'yes i have a disability',
             'si', 'tengo una discapacidad', 'si tengo una discapacidad', 'si tengo discapacidad'},
            {'no', 'not disabled', 'i am not disabled', 'i do not have a disability',
             'no i do not have a disability', 'i don t have a disability', 'no i don t have a disability',
             'no tengo discapacidad', 'no tengo una discapacidad', 'no tengo ninguna discapacidad'},
        ],
        "ethnicity": [
            {"white", "blanco", "blanca"}, {"black", "negro", "negra"}, {"asian", "asiatico", "asiatica"}, {"mixed", "mixto", "mixta"},
            {
                "white other",
                "other white",
                "white any other white background",
                "white any other white ethnic background",
                "any other white background",
                "any other white ethnic background",
                "other white background",
                "white any other",
                "any other white",
            }
        ],
    }
    # a refusal to answer is one in either language
    declines = {"prefer not to say", "prefer not to answer", "i prefer not to say", "i do not wish to answer", "i don t wish to answer",
                "decline to answer", "decline to self identify", "prefiero no decirlo", "prefiero no responder", "prefiero no contestar",
                "no deseo responder", "no quiero responder", "prefiero no indicarlo"}
    for group in [*aliases.get(concept, []), *([declines] if concept in {"gender", "sex", "ethnicity", "sexual_orientation", "religion", "pronouns",
                                                                           "disability", "veteran"} else [])]:
        if norm in group:
            matches = [o for o in labels if normalize(o["label"]) in group]
            if len(matches) == 1:
                return matches[0]
    # Atheism can be represented by a broader no-religion option. The reverse
    # implication is not valid: no religious affiliation does not imply atheism.
    if concept == "religion" and norm in {"atheist", "atheism", "ateo"}:
        for group in (
            {"atheist", "atheism", "ateo"},
            {"no religion", "no religion or belief", "no religion or atheist", "no religious beliefs",
             "no religious affiliation", "no affiliation", "none"},
        ):
            matches = [o for o in labels if normalize(o["label"]) in group]
            if len(matches) == 1:
                return matches[0]
    if concept == 'religion' and norm in {'no religion', 'no religion or belief', 'no religious affiliation', 'no affiliation'}:
        matches = [o for o in labels if normalize(o['label']) in
                   {'no religion', 'no religion or belief', 'no religious affiliation', 'no affiliation', 'none'}]
        if len(matches) == 1:
            return matches[0]
    # Phone country menus decorate country names with international dial codes.
    if concept == 'ethnicity':
        # A confirmed subgroup can map to its broader displayed category. The
        # reverse direction would invent a subgroup and remains unsupported.
        for broad in ('white','black','asian','mixed'):
            if norm.startswith(broad+' '):
                broad_labels = {broad}
                if broad == 'white':
                    broad_labels.update({'white caucasian', 'white caucasian european or middle eastern'})
                matches = [option for option in labels if normalize(option['label']) in broad_labels]
                if len(matches) == 1:
                    return matches[0]
    if re.search(r'\b(?:what|which)\b.*\bnationality\b', normalize(question)) and norm not in {'unknown','none','prefer not to say','unspecified','undisclosed','undeclared','undetermined','na'}:
        positive = [option for option in labels if normalize(option['label']) == 'british']
        negative = [option for option in labels if normalize(option['label']) == 'non british']
        if len(positive) == len(negative) == 1:
            if norm in {'british','english','scottish','welsh','uk','gb','united kingdom','great britain'}:
                return positive[0]
            if re.fullmatch(r'[a-z]+',norm):
                return negative[0]
    countries = [o for o in labels if normalize(re.sub(r"\s+\+\d[\d\s-]*$", "", o["label"])) == norm]
    if len(countries) == 1:
        return countries[0]
    # A displayed institutional acronym is decoration, not a different entity.
    if len(norm) > 3:
        matches = [o for o in labels if normalize(re.sub(
            r"\s*(?:\([A-Z][A-Z0-9&]{1,9}\)|[-–—:]\s*[A-Z][A-Z0-9&]{1,9})\s*$", "", re.sub(
                r"^\s*[A-Z][A-Z0-9&]{1,9}\s*[-–—:]\s*", "", o["label"]
            ))) == norm]
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
        # "4 years", "about 18 months": the range of a menu of ranges that holds it (quantities.py), else nothing
        return bucket_option(value, labels, question=question)
    matches = []
    for o in labels:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[%+]?\s*[-–—]\s*(\d+(?:\.\d+)?)\s*%?\s*", o["label"])
        if m and float(m[1]) <= number <= float(m[2]):
            matches.append(o)
    return matches[0] if len(matches) == 1 else bucket_option(value, labels, question=question)


def adapt(value, semantic, field):
    question = semantic.get("exact_question", semantic.get("text", ""))
    contract = infer_format(question, field)
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if contract == "MULTIPLE_CHOICE" and not isinstance(value, list):
        value = [value]
    if isinstance(value, list):
        if contract == "MULTIPLE_CHOICE":
            selected = [choice(str(item), field["options"], question=question) for item in value]
            if any(option is None for option in selected):
                return None, "BAD_MULTISELECT_MAPPING"
            return list(dict.fromkeys(option["label"] for option in selected)), None
        value = ", ".join(map(str, value))
    if isinstance(value, (float, int)):
        if str(field.get("step")) == "1":
            value = int(float(value) + 0.5)
        value = str(value).removesuffix(".0")
    value = str(value)
    if field.get("options"):
        option = choice(
            value,
            field["options"],
            question=semantic.get("text", ""),
            disclosure_concept=semantic.get("attribute") if semantic.get("intent") == "DEMOGRAPHIC" else None,
        )
        if not option:
            return None, "BAD_ENUM_MAPPING"
        value = option["label"]
    if contract in {"DECIMAL", "INTEGER", "PERCENTAGE_NUMBER"}:
        value = value.rstrip("%")
    error = validate_format(value, question, field)
    if error:
        return None, error
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
