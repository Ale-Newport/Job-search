"""What a yes/no question about studies asks, and what a candidate's education records establish.

Two classes of wrong answer came from reading only a degree word or a year:

* 'Do you have a Master's degree in Mathematics?' ('degree in Medicine', 'studying at Harvard', 'a degree from a US
  university', 'an accredited engineering qualification') was answered 'Yes' from any master's record, whatever its subject,
  institution, country or status, and 'Do you hold a Master's degree?' from a master's still IN PROGRESS;
* 'Will you have graduated by 2027?' ('before 2028', 'since 2020', 'in 2025 or 2026') compared the year for EQUALITY with the
  first year in the question, so every relation but 'in' was wrong ('by 2027' for a 2026 graduate was 'No').

Here a question is parsed into FACETS (level, status, subjects, institutions, what it cannot be checked against) or into a
RELATION between a graduation date and the dates it names, each date an interval of its own precision (a year, a month or
a day); the records are compared facet by facet. 'Yes' only where every asked facet is confirmed by one record, 'No' only
where a relation is certain for every relevant record, and anything else is left to the user. English and Spanish.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from ..automation.answers import normalize
from . import jurisdiction

MONTHS = {name.lower(): number for number in range(1, 13) for name in (calendar.month_name[number], calendar.month_abbr[number])}
MONTHS.update({"sept": 9, "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
               "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12})
_MONTH = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
DATE_EXPRESSION = re.compile(
    r"(?P<iso>(?P<iy>20\d\d) (?P<im>0[1-9]|1[0-2]) (?P<id>0[1-9]|[12]\d|3[01]))\b|"
    r"(?P<dmy>(?P<dd>\d{1,2})(?:st|nd|rd|th)? (?:de |of )?(?P<dm>" + _MONTH + r") (?:de |of |del )?(?P<dy>20\d\d))\b|"
    r"(?P<mdy>(?P<mm>" + _MONTH + r") (?P<md>\d{1,2})(?:st|nd|rd|th)? (?P<my>20\d\d))\b|"
    r"(?P<my2>(?P<m2>" + _MONTH + r") (?:de |of |del )?(?P<y2>20\d\d))\b|"
    r"(?<![a-z0-9])(?P<num>(?P<nm>0?[1-9]|1[0-2]) (?P<ny>20\d\d))\b|(?P<year>\b20\d\d)\b")

# The relation a question puts between a graduation date and the date it names: the words that come just before the date.
RELATIONS = (
    ("between", r"between|entre"),
    ("by", r"by|no later than|not later than|on or before|not after|up to|until|through|at the latest|a mas tardar|"
           r"hasta|para|como maximo"),
    ("before", r"before|prior to|earlier than|antes de|antes del|con anterioridad a"),
    ("after", r"after|later than|following|past|posterior a|despues de|despues del|tras"),
    ("since", r"since|from|no earlier than|not before|not earlier than|as of|desde|a partir de|a partir del"),
    ("in", r"in|during|on|within|en|durante|el"),
)
MODIFIERS = re.compile(r"\b(?:start|beginning|middle|mid|early|late|around|about|circa|roughly|approximately|approx|finales|"
                       r"principios|mediados|comienzos|inicio|aproximadamente|alrededor|hacia|sobre|"
                       # a season, a half, a quarter or a term is a stretch of the year, never the year itself
                       r"summer|spring|autumn|fall|winter|verano|primavera|otono|invierno|half|quarter|q ?[1-4]|h ?[12]|term|semester|"
                       r"trimester|semestre|trimestre|(?:first|second|third|fourth|last) (?:half|quarter|term|semester)|"
                       r"(?:primer|segundo|tercer|ultimo) (?:semestre|trimestre|cuatrimestre)) (?:of |de |del )?$")
FUTURE = re.compile(r"\b(?:will|would|expect\w*|going to|graduating|due|anticipat\w*|plan\w*|prevista?|esperas|graduaras|"
                    r"terminaras|acabaras|finalizaras|te graduaras)\b")
PAST = re.compile(r"\b(?:graduated|completed|finished|obtained|earned|did you|were you|have you|graduaste|terminaste|acabaste|"
                  r"finalizaste|te has graduado|has terminado|has completado|te graduaste)\b")
LEVELS = {"bachelor", "master", "doctorate"}

# Facets of a degree question -------------------------------------------------------------------------------------------
COMPLETED_WORDS = re.compile(r"\b(?:have|has|hold|holds|holding|possess|completed|complete|obtained|earned|awarded|graduated|"
                             r"received|achieved|tienes|tiene|posees|completaste|has completado|has obtenido|titulad[oa]|"
                             r"graduad[oa]|obtenid[oa]|finalizad[oa])\b")
CURRENT_WORDS = re.compile(r"\b(?:studying|pursuing|enrolled|in progress|working towards|currently|student|undertaking|doing|"
                           r"cursando|estudiando|en curso|matriculad[oa]|actualmente|estudias|estudiante)\b")
SUBJECT_AFTER = re.compile(
    r"\b(?:degree|degrees|bachelor|bachelors|master|masters|msc|bsc|ba|ma|phd|doctorate|diploma|qualification|major|majoring|"
    r"licenciatura|grado|maestria|master|doctorado|titulacion|titulo|posgrado|postgrado|carrera|ingenieria|certificado)"
    r"(?: s)?(?: degree)? (?:in|of|on|en|de|sobre) (?P<subject>[a-z0-9 ,/]+?)(?= from | at | with | por | que | where | which | that | and also |"
    r" accredited| desde | para | por la | at the |$)")
BLOCKERS = re.compile(r"\b(?:accredited|chartered|ceng|ieng|abet|aacsb|recogni[sz]ed|approved|regulated|"
                      r"acreditad[oa]|homologad[oa]|reconocid[oa]|colegiad[oa]|professional body|stem|top|ranked|russell group|"
                      r"ivy league|with honou?rs|honou?rs|first class|2 1|2:1|distinction|merit|gpa|grade|nota|calificacion)\b")
# 'X or a related field' is satisfied by X: the alternative changes nothing. 'A degree in a related field' names no subject.
ALTERNATIVE = re.compile(r"\b(?:or|o) (?:a |an |un |una )?(?:related|relevant|similar|equivalent|relacionad\w+|equivalente|"
                         r"comparable)(?: (?:field|subject|discipline|area|degree|qualification|campo|disciplina|titulacion))?\b")
UNNAMED_FIELD = re.compile(r"\b(?:in|en) (?:a |an |un |una )?(?:related|relevant|relacionad\w+|similar|stem|technical|scientific|"
                           r"quantitative|numerical) (?:field|subject|discipline|area|campo|disciplina)\b")
SUBJECT_SYNONYMS = {
    "cs": "computer science", "computing": "computer science", "computer sciences": "computer science",
    "informatica": "computer science", "ciencias de la computacion": "computer science", "ai": "artificial intelligence",
    "inteligencia artificial": "artificial intelligence", "ml": "machine learning", "maths": "mathematics",
    "math": "mathematics", "matematicas": "mathematics", "ingenieria": "engineering", "derecho": "law",
    "medicina": "medicine", "fisica": "physics", "quimica": "chemistry", "economia": "economics", "biologia": "biology",
    "administracion de empresas": "business administration", "psicologia": "psychology", "historia": "history",
    "software engineering": "software engineering", "ingenieria informatica": "computer engineering",
    "ingenieria del software": "software engineering", "ciencia de datos": "data science", "estadistica": "statistics",
    "enfermeria": "nursing", "arquitectura": "architecture", "filosofia": "philosophy", "educacion": "education",
    "geografia": "geography", "sociologia": "sociology", "literatura": "literature", "lenguas": "languages", "linguistica": "linguistics",
    "ciencias politicas": "political science", "relaciones internacionales": "international relations", "contabilidad": "accounting",
    "finanzas": "finance", "marketing": "marketing", "periodismo": "journalism", "comunicacion": "communication", "diseno": "design",
    "geologia": "geology", "farmacia": "pharmacy", "odontologia": "dentistry", "veterinaria": "veterinary medicine",
    "antropologia": "anthropology", "musica": "music", "bellas artes": "fine arts", "turismo": "tourism", "trabajo social": "social work",
    "criminologia": "criminology", "teologia": "theology", "biotecnologia": "biotechnology", "ingenieria civil": "civil engineering",
    "ingenieria mecanica": "mechanical engineering", "ingenieria electrica": "electrical engineering",
    "ingenieria electronica": "electronic engineering", "ingenieria quimica": "chemical engineering",
    "ingenieria industrial": "industrial engineering", "ingenieria aeroespacial": "aerospace engineering",
    "ciencias ambientales": "environmental science", "ciencias de la salud": "health sciences", "robotica": "robotics",
    "telecomunicaciones": "telecommunications", "administracion y direccion de empresas": "business administration",
    "gestion de empresas": "business management", "ciencias economicas": "economics", "matematicas aplicadas": "applied mathematics",
}
INSTITUTION_WORD = re.compile(
    r"((?:[A-Z][\w&'’-]*\s+){0,3}(?:University|College|Institute|Polytechnic|School|Universidad|Instituto|Escuela|Facultad)"
    r"(?:\s+(?:of|de|del|de la|in|for)\s+[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,2})?)")
INSTITUTION_AFTER = re.compile(r"\b(?:at|from|by|en|de la|de|por)\s+(?:the |la |el )?((?:[A-Z][\w&'’-]*)(?:\s+[A-Z][\w&'’-]*){0,3})")
INSTITUTION_STOP = {"I", "A", "An", "The", "My", "Master", "Bachelor", "PhD", "MSc", "BSc", "Masters", "Bachelors", "English",
                    "Spanish", "Do", "Are", "Have", "Is", "Yes", "No", "Computer", "Mathematics", "Engineering"}


def _canonical_subject(text: str) -> str:
    cleaned = re.sub(r"^(?:a |an |the |un |una |el |la )", "", normalize(text)).strip()
    return SUBJECT_SYNONYMS.get(cleaned, cleaned)


def facets(question: str) -> dict:
    """What a yes/no question about a degree or about studying asks: the level, whether it is held or in progress, the
    subjects and institutions it names, and ``blockers``: what the records cannot establish (a country, an accreditation, a
    grade, 'a related field')."""
    text = ALTERNATIVE.sub(" ", normalize(str(question or "")[:1500]))
    raw = str(question or "")[:1500]
    status = "completed" if COMPLETED_WORDS.search(text) else "current" if CURRENT_WORDS.search(text) else "completed"
    subjects: list[str] = []
    match = SUBJECT_AFTER.search(text)
    if match:
        clause = match["subject"]
        parts = [p.strip() for p in re.split(r",| or | o | / ", clause) if p.strip()]
        subjects = [_canonical_subject(p) for p in parts if not re.search(r"\b(?:and|y)\b", p)]
        if any(re.search(r"\b(?:and|y)\b", p) for p in parts):
            subjects = []  # 'Mathematics and Physics': a joint degree, which no record is read as
            blockers = ["joint_subjects"]
        else:
            blockers = []
    else:
        blockers = []
    institutions = []
    for found in INSTITUTION_WORD.findall(raw):
        name = found.strip()
        if name and name not in institutions:
            institutions.append(name)
    if not institutions and re.search(r"\b(?:studying|student|enrolled|studied|estudias|estudiando|graduated|graduate)\b", text) \
            or re.search(r"\b(?:attend|study|studied|studying)\b.{0,12}\b(?:at|in)\b", text):
        for found in INSTITUTION_AFTER.findall(raw):
            words = found.split()
            if words and words[0] not in INSTITUTION_STOP and not jurisdiction.named_places(found)["countries"] \
                    and found not in institutions:
                institutions.append(found)
    named = jurisdiction.named_places(question)
    if named["countries"] or named["regions"] or named["places"]:
        blockers.append("country_of_institution")
    if BLOCKERS.search(text):
        blockers.append("grade_or_accreditation")
    if UNNAMED_FIELD.search(text):
        blockers.append("an unnamed field")
    return {"status": status, "subjects": subjects, "institutions": institutions, "blockers": blockers}


def subject_matches(asked: str, record_texts: list[str]) -> bool:
    """Is the asked subject the subject of the record? Whole words, in order, after the synonyms: 'Computer Science' is in
    'BSc Computer Science'; 'Mathematics' is not in 'Artificial Intelligence'."""
    wanted = _canonical_subject(asked)
    if not wanted:
        return False
    for value in record_texts:
        have = _canonical_subject(str(value or ""))
        if have == wanted or re.search(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])", have):
            return True
    return False


def institution_matches(asked: str, names: list[str]) -> bool:
    wanted = normalize(asked)
    for name in names:
        have = normalize(name)
        if wanted and have and (wanted == have or re.search(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])", have)
                                or re.search(r"(?<![a-z0-9])" + re.escape(have) + r"(?![a-z0-9])", wanted)):
            return True
    return False


# Graduation relations --------------------------------------------------------------------------------------------------
def span(match: re.Match) -> tuple[date, date] | None:
    """The interval a date expression covers, at its own precision (a year, a month or a day)."""
    try:
        if match["iso"]:
            day = date(int(match["iy"]), int(match["im"]), int(match["id"]))
            return day, day
        if match["dmy"]:
            day = date(int(match["dy"]), MONTHS[match["dm"]], int(match["dd"]))
            return day, day
        if match["mdy"]:
            day = date(int(match["my"]), MONTHS[match["mm"]], int(match["md"]))
            return day, day
        if match["my2"] or match["num"]:
            year, month = (int(match["y2"]), MONTHS[match["m2"]]) if match["my2"] else (int(match["ny"]), int(match["nm"]))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        year = int(match["year"])
        return date(year, 1, 1), date(year, 12, 31)
    except (ValueError, KeyError):
        return None


def stored_span(value) -> tuple[date, date] | None:
    """The interval a stored date covers: 'YYYY' a year, 'YYYY-MM' a month, 'YYYY-MM-DD' one day."""
    text = str(value or "")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            return day, day
        if re.fullmatch(r"\d{4}-\d{2}", text):
            year, month = int(text[:4]), int(text[5:])
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        if re.fullmatch(r"\d{4}", text):
            return date(int(text), 1, 1), date(int(text), 12, 31)
    except ValueError:
        return None
    return None


def relation_of(question: str) -> dict | None:
    """The dates a question names and how it relates them to the graduation date, or None when that cannot be read safely.
    ``{"relation": by | before | after | since | in | between | either, "bounds": [(lo, hi), ...]}``"""
    text = normalize(question)
    found = []
    for match in DATE_EXPRESSION.finditer(text):
        interval = span(match)
        if interval is None:
            return None
        found.append((match.start(), match.end(), interval))
    if not found or len(found) > 3:
        return None
    labels = []
    ends = []
    for start, _, _ in found:
        window = text[max(0, start - 40): start]
        at_end = bool(re.search(r"(?:end of|finales de|fin de|final de) $", window))
        if MODIFIERS.search(window) and not at_end:
            return None  # 'by early 2027', 'around 2027': a date with a margin is not one this can compare
        before = re.sub(r"(?:the |el |la |year of |ano de |month of |mes de |end of |finales de |fin de |final de )+$", "", window).rstrip()
        label = None
        for name, words in RELATIONS:
            if re.search(r"(?:^|\b)(?:" + words + r")$", before):
                label = name
                break
        labels.append(label)
        ends.append(at_end)
    bounds = [interval for _, _, interval in found]
    if any(ends):
        # 'before / by / after / since THE END OF 2026' is a relation to the LAST day of 2026, not to the year's first day; 'in the end
        # of 2026' and 'between ... the end of ...' are a stretch of it: not read
        if len(found) != 1 or (labels[0] or "in") not in {"by", "before", "after", "since"}:
            return None
        if labels[0] in {"before", "since"}:  # 'by' and 'after' already read the right edge of the year
            bounds = [(bounds[0][1], bounds[0][1])]
    if len(found) == 1:
        return {"relation": labels[0] or "in", "bounds": bounds}
    if labels[0] == "between" or re.search(r"\b(?:between|entre)\b", text):
        return {"relation": "between", "bounds": [bounds[0], bounds[-1]]} if len(found) == 2 else None
    between = text[found[0][1]: found[1][0]]
    if len(found) == 2 and re.fullmatch(r"\s*(?:or|o|u)\s*(?:in |en )?", between) and labels[0] in {"in", None} and labels[1] in {"in", None}:
        return {"relation": "either", "bounds": bounds}
    if len(found) == 2 and re.fullmatch(r"\s*(?:to|and|y|a|-)\s*", between) and labels[0] == "since":
        return {"relation": "between", "bounds": bounds}
    return None


def check(relation: str, bounds: list, graduated: tuple[date, date], today: date) -> bool | None:
    """True / False when the relation holds for every day the graduation date may be on, or for none of them; None when the
    precision of either date leaves it open."""
    lo, hi = graduated
    if relation == "by":
        edge = bounds[0][1]
        return True if hi <= edge else False if lo > edge else None
    if relation == "before":
        edge = bounds[0][0]
        return True if hi < edge else False if lo >= edge else None
    if relation == "after":
        edge = bounds[0][1]
        return True if lo > edge else False if hi <= edge else None
    if relation == "since":
        edge = bounds[0][0]
        return True if lo >= edge and hi <= today else False if hi < edge else None
    if relation in {"in", "between"}:
        first, last = (bounds[0][0], bounds[-1][1])
        return True if lo >= first and hi <= last else False if hi < first or lo > last else None
    if relation == "either":
        inside = [lo >= a and hi <= b for a, b in bounds]
        outside = [hi < a or lo > b for a, b in bounds]
        return True if any(inside) else False if all(outside) else None
    return None


def graduation_verdict(question: str, records: list[dict], today: date) -> tuple[bool | None, list[str], list[dict], str]:
    """Is the relation the question states true of the candidate's graduation date(s)?
    -> (answer, rules, the records it read, why not when the answer is None).

    Which records: 'Have you graduated ...' reads the COMPLETED degrees, 'Will you graduate / have graduated ...' the ones in
    progress (or, when there are none, the completed ones); a question that says neither reads all of them. The answer is
    certain only when every record read says the same; a degree that ends in June and one that ends in September are not
    'by July'."""
    text = normalize(question)
    parsed = relation_of(question)
    if parsed is None:
        return None, [], [], "the dates or the relation of this question cannot be read with certainty"
    degrees = []
    for record in records:
        interval = stored_span(record.get("end_date"))
        if interval is None or record.get("level") not in LEVELS:
            continue
        degrees.append({**record, "span": interval, "done": record.get("status") == "completed" or interval[1] <= today})
    future, past = bool(FUTURE.search(text)), bool(PAST.search(text))
    if future:  # 'will have graduated by' is about the degree still to come, whatever tense its verb is in
        relevant = [d for d in degrees if not d["done"]] or [d for d in degrees if d["done"]]
    elif past:
        relevant = [d for d in degrees if d["done"]]
    else:
        relevant = degrees
    if not relevant:
        return None, [], [], "no degree of yours has a graduation date this question can be compared with"
    verdicts = [check(parsed["relation"], parsed["bounds"], d["span"], today) for d in relevant]
    if all(v is True for v in verdicts):
        answer = True
    elif all(v is False for v in verdicts):
        answer = False
    else:
        return None, [], relevant, "your degrees do not all give the same answer to this question"
    return answer, ["graduation_relation:" + parsed["relation"]], relevant, ""
