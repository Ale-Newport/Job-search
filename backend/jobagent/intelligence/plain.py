"""The plain-question gate: a fact answers a question only when the question is a PLAIN one.

THE PROBLEM. The answer engine reads a question by keyword ("right to work", "degree", "notice", "based"). A keyword says WHICH
fact a question is about; it says nothing about WHAT the question asks of it. Every earlier fix (legal predicates, education
facets, graduation relations...) added a list of qualifiers that turn a question round, and the next wording slipped through:
an employer note in brackets, 'not including', 'for the duration of', 'if so, where?', a second predicate after 'and', a
school type, a notice threshold, a direction ('outside', 'away from'). All of them are ways of asking a DIFFERENT question
than the one the fact answers, and a blacklist can never list them all.

THE GATE. A question is answered from a stored fact only when it is FULLY ACCOUNTED FOR by a small set of canonical forms
of its family: every word of it is consumed by the form, or by a slot of the form (a place, a language, a date, a number with
its unit, a subject, an institution). The reading goes in this order, and any step that cannot account for a word stops it:

1. STRUCTURE (raw text): at most one sentence/clause carries the question; what stands beside it is a neutral hint ('Select
   Yes or No', 'required', a format such as MM/YYYY) or nothing; text in brackets is read with the vocabulary of the family
   (a unit, a format, an abbreviation, an example list of visa routes) and anything else in them is unconsumed; a follow-up
   written into the label ('If so, where?') is unconsumed for EVERY control (a yes/no cannot carry it either). What a person
   reads is read: ``label_core`` takes out an invisible character and the neutral hints around a label ('Please answer:',
   'Por favor, responde:', a 'Required' / 'Obligatorio' line, 'Select one'), never a hint about HOW to write the answer.
2. SLOTS (normalised text): places, languages, dates and numbers-with-units are written as tokens (``zzplace`` ...), the
   family's own modifiers (time, hours, 'without sponsorship', 'permanent') are taken out and RECORDED, because the engine
   checks each of them against its own fact.
3. FORM: what is left must be fully matched by one canonical template of the family (an anchored regular expression made of
   the family's closed vocabulary), or, for a bare label ('Notice period', 'Country of residence'), consist only of the
   family's own words with at least one head noun. A negator, a comparator, a coordination between two predicates, a
   quantifier, an unknown attribute, a heading word or any other content word is not in any form: it is RESIDUE.

THE FAMILIES. Right to work, sponsorship, visa, language, education/degree/graduation, notice period, availability, location,
relocation, salary, name/birth/age, nationality, contact, address, a LINK (LinkedIn, GitHub, a portfolio) and a PERSONAL ATTRIBUTE
(gender, pronouns, ethnicity, religion, disability, veteran status, date of birth, sexual orientation...: the confirmed value
answers a plain question about exactly ONE attribute, and the engine asks the gate where that value would be given). A typed
signature is a label of the signature's own words (``SIGNATURE_LABELS``): 'Signature, or your address' is not one. Both languages
are read: a Spanish form is as plain as the English one, and a wording that is in the grammar of one and not of the other is a bug
(the metamorphic suite compares a question with its translation).

Residue is never guessed at: the question is NEEDS_YOU with the code ``PLAIN_QUESTION_REQUIRED`` and the unconsumed words
(``Reading['residue']``), and the engine, the extension and Auto Apply all say precisely why. A long-text field (textarea) and a
narrative prompt ('Describe...', 'Tell us...', 'How do you...') are never answered from a one-line fact at all.

Pure functions over strings, bounded and linear (the text is capped before any pattern runs, every template is anchored over
single-spaced words), cached per question.

DESIGN NOTE (what to change, and what never to). To teach the gate a new plain wording add it to the family's vocabulary or
template list in this file and add the wording to ``tests/test_engine_plain.py``; do NOT add a qualifier to a blacklist anywhere
in the engine. A word that is in no template is already refused, which is the point. The held-out metamorphic suite
(``tests/test_engine_metamorphic.py``) wraps plain questions in notes, brackets, negators, comparators, time expressions,
follow-ups, conjunctions and renamed headings and requires 'the same answer, or NEEDS_YOU': the gate satisfies it by
construction, because a wrapper that adds words adds residue.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..automation.answers import normalize
from . import academic, jurisdiction, languages

MAX_CHARS = 420  # a plain question is a sentence; the rest of a page's label is padding or an attack
MAX_WORDS = 48
NOT_GATED = {"ok": True, "gated": False, "family": "", "form": "", "template": "", "residue": [], "reason": "", "slots": {},
             "narrative": False}

# ------------------------------------------------------------------------------------------------ the families
FAMILY_OF = {
    "WORK_AUTHORIZATION": "work_right", "SPONSORSHIP": "sponsorship", "VISA": "visa", "LANGUAGE": "language",
    "EDUCATION": "education", "DEGREE": "education", "GRADE": "education", "GPA": "education", "MODULE": "education",
    "ACADEMIC_RESULT": "education", "STUDY_YEAR": "education", "GRADUATION": "education", "NOTICE_PERIOD": "notice",
    "START_DATE": "availability", "AVAILABILITY": "availability", "LOCATION": "location", "RELOCATION": "relocation",
    "SALARY": "salary", "IDENTITY": "identity", "NATIONALITY": "nationality", "CONTACT": "contact", "ADDRESS": "address",
    "DEMOGRAPHIC": "demographic", "LINKEDIN": "link", "GITHUB": "link", "PORTFOLIO": "link",
}
LEGAL = {"work_right", "sponsorship", "visa"}
LINKS = {"LINKEDIN", "GITHUB", "PORTFOLIO"}  # a public link: a model may read an unusual wording as one (the answer is flagged for review)
# kinds of question whose keyword is a topic a narrative can merely mention: such a prompt is not a question about the fact
MISROUTABLE = {"LOCATION", "CONTACT", "ADDRESS", "IDENTITY", "LANGUAGE", "EDUCATION", "DEGREE", "GRADUATION", "GRADE", "GPA",
               "NATIONALITY", "START_DATE", "AVAILABILITY", "MODULE", "STUDY_YEAR", "LINKEDIN", "GITHUB", "PORTFOLIO"}


def family_of(intent) -> str | None:
    return FAMILY_OF.get(intent)


# ------------------------------------------------------------------------------------------------ a tiny grammar kit
def expand(phrase: str) -> list[str]:
    """'authori{s,z}ed' -> ['authorised', 'authorized']; one {a,b} group per phrase is enough here."""
    found = re.search(r"\{([^{}]*)\}", phrase)
    if not found:
        return [phrase]
    return [alternative for choice in found.group(1).split(",")
            for alternative in expand(phrase[: found.start()] + choice + phrase[found.end():])]


class Grammar:
    """The closed vocabulary of one family and its templates. ``A`` is an alternation of phrases (and registers their words as
    vocabulary), ``T`` compiles a template: a sequence of required fragments, ``opt(...)`` and ``star(...)``, all matched over
    single-spaced lowercase words."""

    def __init__(self, name: str):
        self.name = name
        self.vocab: set[str] = set()
        self.templates: list[tuple[str, re.Pattern]] = []

    def A(self, *phrases: str) -> str:
        found: list[str] = []
        for phrase in phrases:
            for variant in expand(phrase):
                self.vocab.update(variant.split())
                found.append(re.escape(variant).replace("\\ ", " "))
        return "(?:" + "|".join(sorted(set(found), key=len, reverse=True)) + ")"

    def T(self, label: str, *items) -> None:
        body = ""
        for item in items:
            if isinstance(item, tuple):
                kind, fragment = item
                body += f"(?:{fragment} )" + ("?" if kind == "?" else "*")
            else:
                body += item + " "
        self.templates.append((label, re.compile(body)))

    def match(self, text: str) -> str | None:
        padded = text + " "
        for label, pattern in self.templates:
            if pattern.fullmatch(padded):
                return label
        return None


def opt(fragment: str):
    return ("?", fragment)


def star(fragment: str):
    return ("*", fragment)


ASK_WORDS = ["answer with", "respond with", "please", "kindly", "tell", "us", "me", "give", "provide", "state", "enter", "specify", "indicate", "list", "write", "type",
             "input", "fill", "confirm", "select", "choose", "your", "the", "my", "a", "an", "what", "which", "is", "are", "por", "favor",
             "indica", "escribe", "introduce", "dinos", "cual", "es", "tu", "su", "mi", "tus", "sus", "mis"]
# how a date is asked for: 'Start date (MM/YYYY)', 'Expected graduation - YYYY', 'Graduation year, use Month/Year'
DATE_FORMAT_WORDS = ["yyyy", "mm", "yy", "dd", "month", "year", "format", "use", "as", "e", "g", "iso", "dd mm yyyy", "mm yyyy", "yyyy mm",
                     "formato", "mes", "ano", "usa"]
# 'X or do you have Y': a conjunction followed by a verb or a pronoun starts a SECOND question, in any language
SECOND_PREDICATE = re.compile(r"(?:^| )(?:and|or|but|y|o|e|pero|u|nor|plus)(?: also)? (?:do|does|did|are|is|am|was|were|have|has|had|will|would|can|could|"
                              r"should|shall|must|may|might|you|i|we|they|he|she|it|your|tu|tienes|eres|estas|puedes|necesitas|has|ha|hay|"
                              r"hablas|resides|vives|dispones|posees|cuentas)(?: |$)")


class Labels:
    """The bare-label form: the label is made only of the family's own phrases (modifiers and heads), at least one of them a
    head noun. Order does not matter for a name of a thing ('Current notice period', 'Notice period (weeks)'); a form that
    asks something of the fact needs a template instead."""

    def __init__(self, heads: list[str], mods: list[str]):
        self.heads, self.mods = [], []
        mods = list(mods) + ASK_WORDS
        for source, target in ((heads, self.heads), (mods, self.mods)):
            for phrase in source:
                target.extend(expand(phrase))
        every = sorted(set(self.heads + self.mods), key=lambda p: (-len(p.split()), -len(p)))
        self.vocab = {word for phrase in every for word in phrase.split()}
        self._all = re.compile("(?:(?:" + "|".join(re.escape(p) for p in every) + ") )+")
        self._head = re.compile("(?:^| )(?:" + "|".join(re.escape(p) for p in sorted(set(self.heads), key=len, reverse=True))
                                + ")(?: |$)")

    def match(self, text: str) -> bool:
        return bool(self._all.fullmatch(text + " ")) and bool(self._head.search(text)) and not SECOND_PREDICATE.search(text)


# ------------------------------------------------------------------------------------------------ slot tokens
PLACE, LANG, DATE, QTY, NUM, SUBJ, INST = "zzplace", "zzlang", "zzdate", "zzqty", "zznum", "zzsubj", "zzinst"
SLOT_TOKENS = {PLACE, LANG, DATE, QTY, NUM, SUBJ, INST}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
                "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}
UNITS = {"day": "days", "days": "days", "week": "weeks", "weeks": "weeks", "month": "months", "months": "months",
         "year": "years", "years": "years", "dia": "days", "dias": "days", "semana": "weeks", "semanas": "weeks",
         "mes": "months", "meses": "months", "ano": "years", "anos": "years"}
QUANTITY = re.compile(r"(?<![a-z0-9.])(\d+(?:[.,]\d+)?|" + "|".join(NUMBER_WORDS) + r") ?-? ?(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")(?![a-z0-9])")
BARE_MONTH = re.compile(r"(?<![a-z0-9])(?:january|february|april|june|july|august|september|october|november|december|enero|febrero|"
                        r"marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)(?![a-z0-9])")
BARE_NUMBER = re.compile(r"(?<![a-z0-9])\d+(?:[.,]\d+)?(?:k)?(?![a-z0-9])")


def mask_slots(text: str, folded: str | None = None) -> tuple[str, dict]:
    """A question's text -> (the text, folded, with its slots written as tokens, what they were). Places first (they need the
    raw case of 'US'), then languages, dates and quantities; numbers are masked later (``mask_numbers``), after a family has
    taken out its own '40 hours'. ``folded``: the text already folded and with its places masked."""
    slots: dict = {"places": [], "languages": [], "dates": [], "quantities": [], "numbers": []}
    folded = jurisdiction.mask(text) if folded is None else folded
    slots["places"] = [None] * folded.count(PLACE)  # which places they are is jurisdiction.named_places's business
    folded, slots["languages"] = languages.mask(folded)
    found: list = []
    folded = mask_dates(folded, found)

    def quantity(match):
        number = match.group(1)
        value = NUMBER_WORDS.get(number)
        value = value if value is not None else float(number.replace(",", "."))
        slots["quantities"].append((value, UNITS[match.group(2)]))
        return " " + QTY + " "

    folded = QUANTITY.sub(quantity, folded)
    slots["dates"] = found
    return re.sub(r"\s+", " ", folded).strip(), slots


def mask_dates(folded: str, found: list) -> str:
    def date(match):
        found.append(match.group(0))
        return " " + DATE + " "

    return BARE_MONTH.sub(date, academic.DATE_EXPRESSION.sub(date, folded))


def mask_numbers(text: str, slots: dict) -> str:
    """The numbers that are left (after a family has taken out its own '40 hours', '3 months') written as ``zznum``."""
    def number(match):
        slots["numbers"].append(match.group(0))
        return " " + NUM + " "

    return re.sub(r"\s+", " ", BARE_NUMBER.sub(number, text)).strip()


# ------------------------------------------------------------------------------------------------ structure (raw text)
ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|u\.s\.a|u\.s|u\.k|e\.u|etc|vs|approx|incl|dept|ltd|inc|co|dr|mr|mrs|ms|st)\.", re.I)
BRACKETS = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]|\{([^{}]*)\}")
STRAY = re.compile(r"[()\[\]{}]")
# a dash or a bar between two phrases is a breadcrumb ('Education - School'), not the end of a sentence: the words decide
SEGMENTS = re.compile(r"(?<=[.!?;:])\s+|\s*[\n\r]+\s*")
PLURAL_MARK = re.compile(r"(?<=[A-Za-z])\((?:s|es)\)")
GENDER_MARK = re.compile(r"(?i)(?<=[A-Za-z])(?:/[ao]s?\b|\([ao]s?\)|\(@\)|@)")  # 'dispuesto/a', 'dispuesto(a)': the same word
ABBREVIATED = re.compile(r"[A-Z][A-Z0-9&.]{1,6}")
SYMBOLS = re.compile(r"[£$€¥₹%+/\- ]+")
OPTION_PREFIX = re.compile(r"^\s*(?:yes|no|si|sí)\s*[,.:;–—-]+\s*(?=\S)", re.I)
# Words that carry no meaning of their own in a bracket or beside a question: a hint to the person filling the form.
NEUTRAL = {"yes", "no", "si", "or", "o", "select", "choose", "pick", "answer", "tick", "check", "mark", "one", "option", "options",
           "required", "optional", "mandatory", "obligatorio", "opcional", "requerido", "please", "an", "a", "the", "from",
           "list", "dropdown", "below", "above", "applicable", "if", "e", "g", "etc", "eg", "ie", "i", "example", "examples",
           "such", "as", "for", "like", "all", "that", "apply", "field", "this", "is", "your", "response", "selecciona",
           "elige", "responde", "una", "opcion", "todas", "las", "que", "correspondan", "campo", "texto", "text", "type",
           "enter", "here", "only", "box", "button", "click", "tap", "multiple", "choices", "answers"}
_HINT_NEUTRAL = (
    # an instruction to the person filling the form ('Please answer:', 'Por favor, responde:') is no part of the question it introduces
    r"(?:(?:please|kindly|por favor) )*(?:(?:select|choose|pick|answer|tick|check|mark|click|respond|reply|selecciona|elige|responde|"
    r"contesta|marca)(?: |$))"
    r"(?:(?:one|an option|an answer|your answer|yes or no|yes no|yes|no|si o no|si no|all that apply|all|the (?:right|correct|best) "
    r"(?:option|answer)|one of the (?:following|options|below)|una opcion|una respuesta|si o no|todas las que correspondan|"
    r"below|here|from the list|from the dropdown|as appropriate|as applicable|where applicable|"
    r"(?:the|this) (?:following )?questions?|the following|las siguientes preguntas|la siguiente pregunta|a continuacion|"
    r"to the following|to this question|a las siguientes preguntas|a esta pregunta) ?)*|"
    r"(?:yes|si|no)(?: (?:or|o|/)? ?(?:yes|si|no))?|(?:this )?(?:field|question)? ?(?:is )?(?:required|optional|mandatory|"
    r"obligatorio|opcional|requerido)|(?:campo|pregunta) (?:obligatori[oa]|opcional)|(?:required|optional) field|"
    r"(?:please )?(?:answer|respond) (?:yes or no|honestly|truthfully)|(?:yes|no) (?:or|/) (?:yes|no)|n a|na")
# how the answer is to be WRITTEN ('Please answer with digits only', 'Use the national format'): never what is asked, but the engine reads it
_HINT_FORMAT = (
    r"(?:(?:please|kindly) )?(?:(?:answer|respond|enter|write|type|use|input|provide|escribe|usa) )?(?:(?:with|in|as|using|con|en) )?"
    r"(?:the )?(?:digits only|only digits|numbers only|only numbers|numeric(?: characters)? only|national format|international format|"
    r"e 164 format|no symbols|without symbols|plain digits|solo numeros|solo digitos|formato nacional|formato internacional)")
HINT_SEGMENT = re.compile(_HINT_NEUTRAL + "|" + _HINT_FORMAT)
NEUTRAL_SEGMENT = re.compile(_HINT_NEUTRAL)  # what ``label_core`` takes out: the hints that change nothing about the question
FOLLOW_UP = re.compile(
    r"(?:if|when|where|in case|should) (?:yes|so|not|applicable|required|necessary|appropriate|you (?:answered|selected|said|chose|"
    r"ticked|checked|replied|have answered|have selected)(?: yes| no)?(?: above| to the above| to the previous question| "
    r"previously)?)|(?:si|en caso de)(?: la respuesta es| has respondido| respondiste| asi es| es asi| aplica| es afirmativo)(?: si| no)?")


def collapse(value) -> str:
    return " ".join(str(value or "").split())


def collapse_lines(value) -> str:
    """Whitespace collapsed inside each line; the line breaks stay, because a label's second line ('Required') is a segment of its own."""
    return "\n".join(line for line in (collapse(part) for part in re.split(r"[\r\n\u2028\u2029]+", str(value or ""))) if line)


INVISIBLE = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_SHIELD = {".": "\ue000", "!": "\ue001", "?": "\ue002", ";": "\ue003", ":": "\ue004", "\n": "\ue005"}
_UNSHIELD = {ord(v): k for k, v in _SHIELD.items()}


@lru_cache(maxsize=1 << 14)
def _label_core(text: str) -> str:
    text = INVISIBLE.sub("", text)  # what a person reads has no zero-width character in it: neither does what is read here
    lines = collapse_lines(text)
    if not re.search(r"[.!?;:\n]", lines):
        return lines
    shielded = BRACKETS.sub(lambda m: re.sub(r"[.!?;:\n]", lambda c: _SHIELD[c.group(0)], m.group(0)), lines)
    shielded = ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\u2024", shielded)
    parts = [part for part in SEGMENTS.split(shielded) if normalize(part.translate(_UNSHIELD))]
    kept = [part for part in parts if not NEUTRAL_SEGMENT.fullmatch(normalize(part.translate(_UNSHIELD)))]
    if len(parts) < 2 or not kept or len(kept) == len(parts):
        return lines
    return collapse(" ".join(kept).replace("\u2024", ".").translate(_UNSHIELD))


ATTESTATION = re.compile(
    r"(?:(?:i|we) (?:hereby )?(?:confirm|declare|certify|attest|affirm|swear|state|acknowledge|agree|accept|understand|consent|have read|warrant)|"
    r"by (?:typing|entering|signing|checking|ticking|submitting|clicking|selecting)|(?:yo )?(?:declaro|certifico|confirmo|acepto|reconozco|juro)|"
    r"al (?:escribir|firmar|marcar|enviar|hacer clic))\b.*")


def without_attestation(text: str) -> str:
    """A typed signature's label with the declaration sentence beside it taken out ('I confirm the information above is true. Full
    name' -> 'Full name'): the sentence is what is signed, the rest is what is asked. Unchanged when nothing else is left."""
    lines = collapse_lines(INVISIBLE.sub("", text))
    shielded = BRACKETS.sub(lambda m: re.sub(r"[.!?;:\n]", lambda c: _SHIELD[c.group(0)], m.group(0)), lines)
    shielded = ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\u2024", shielded)
    parts = [part for part in SEGMENTS.split(shielded) if normalize(part.translate(_UNSHIELD))]
    kept = [part for part in parts if not ATTESTATION.fullmatch(normalize(part.translate(_UNSHIELD)))]
    if len(parts) < 2 or not kept or len(kept) == len(parts):
        return lines
    return collapse(" ".join(kept).replace("\u2024", ".").translate(_UNSHIELD))


def label_core(text):
    """The label as the person reads it: no invisible character, and no neutral hint the page wrote around it (an instruction such as
    'Please answer:' or 'Por favor, responde:', a 'Required' / 'Obligatorio' line, 'Select one'). Every other word is kept, so the
    text is returned unchanged when there is nothing to take out; a label of nothing but a hint is returned as it is."""
    if not isinstance(text, str):
        return text
    return _label_core(text) if len(text) <= 1024 else _label_core.__wrapped__(text)


def structure(text: str, vocab: set[str], choice_control: bool, strip=None) -> dict:
    """The raw text of a question -> {'core': the one clause that asks, 'prefix': a leading 'Yes,'/'No,' was dropped,
    'brackets': [(raw content, accepted)], 'hints': [neutral hint segments], 'follow_up': a follow-up was seen, 'residue':
    [unconsumed words], 'reason': why}. ``vocab`` is the vocabulary a bracket may be made of (the family's own words; the
    neutral hints are always allowed)."""
    out = {"core": "", "segments": [], "prefix": False, "brackets": [], "hints": [], "follow_up": False, "residue": [], "reason": ""}
    raw = GENDER_MARK.sub("", PLURAL_MARK.sub("", collapse_lines(text)))  # 'Skill(s)', 'dispuesto/a'
    if OPTION_PREFIX.match(raw):
        out["prefix"] = True
        raw = OPTION_PREFIX.sub("", raw, count=1)

    def group(match):
        content = collapse(next(g for g in match.groups() if g is not None))
        if ABBREVIATED.fullmatch(content) and normalize(content) not in {"yes", "no"}:
            out["brackets"].append((content, True))  # an abbreviation of the name before it: '(UCL)', '(ENG)'
            return " "
        words = brackets_words(strip(content) if strip else content)
        if not words and (SYMBOLS.fullmatch(content) or (strip and content.strip())):
            out["brackets"].append((content, True))  # '(£)', '($)', '(%)': a currency or a unit written as a symbol
            return " "
        unknown = [w for w in words if not (w in vocab or w in NEUTRAL or w in SLOT_TOKENS)]
        out["brackets"].append((content, bool(words) and not unknown))
        if unknown or not words:
            out["residue"].extend(unknown or ["()"])
            out["reason"] = out["reason"] or "text in brackets says more than a unit, a format or a plain example"
        return " "

    raw = BRACKETS.sub(group, raw)
    raw = STRAY.sub(" ", raw)
    masked = ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\u2024", raw)
    parts = [collapse(part.replace("\u2024", ".")) for part in SEGMENTS.split(masked)]
    parts = [part for part in parts if normalize(part)]
    content: list[str] = []
    for part in parts:
        if HINT_SEGMENT.fullmatch(normalize(part)) and len(parts) > 1:
            out["hints"].append(part)
        else:
            content.append(part)
    # a leading conditional ('If yes, please describe...') belongs to the page's conditional logic, not to the question
    if content and FOLLOW_UP.match(normalize(content[0])):
        comma = re.search(r"[,;:]", content[0])
        if comma and normalize(content[0][comma.end():]):
            out["follow_up"] = True
            content[0] = collapse(content[0][comma.end():])
    follow = [part for part in content if FOLLOW_UP.match(normalize(part))]
    if follow and len(follow) < len(content) + 1:
        out["follow_up"] = True
        content = [part for part in content if part not in follow]
        # a control that carries the yes or no cannot carry the follow-up that is written into the same label ('If so, please state where')
        out["residue"].extend(normalize(" ".join(follow)).split()[:5])
        out["reason"] = out["reason"] or "it asks for more than the yes or no (a follow-up such as 'if so, where?')"
    out["segments"] = content
    out["core"] = content[0] if content else ""
    return out


def brackets_words(content: str) -> list[str]:
    """The words of the text inside a bracket, with the slots of the text written as tokens."""
    slots: dict = {}
    folded, slots = mask_slots(content)
    return mask_numbers(folded, slots).split()


# ------------------------------------------------------------------------------------------------ phrases of the slots
def words_re(*patterns: str) -> re.Pattern:
    """A pattern of whole words over single-spaced text (alternatives are regex fragments)."""
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(patterns) + r")(?![a-z0-9])")


def take(text: str, pattern: re.Pattern, found: list | None = None, tag=None) -> str:
    """The text without the matches of ``pattern`` (one space left), recording each match (or ``tag``) in ``found``."""
    def drop(match):
        if found is not None:
            found.append(tag if tag is not None else match.group(0))
        return " "
    return re.sub(r"\s+", " ", pattern.sub(drop, text)).strip()


_PREP = r"(?:(?:in|within|across|throughout|inside|into|to|for|at|en|dentro de|a|para) )?"
_ART = r"(?:(?:the|el|la|los|las) )?"
_ONE_PLACE = _PREP + _ART + PLACE + "(?: " + PLACE + ")*"
PLACE_PHRASE = re.compile(r"(?<![a-z0-9])" + _ONE_PLACE + r"(?: (?:or|and|y|o|e|either|both) " + _ONE_PLACE + r")*(?![a-z0-9])")

# What a work-right question may add to 'do you have the right to work': a modifier each of which the engine checks against its
# own stored fact (``engine``: sponsorship, permanent / unrestricted / full-time / future right, a period policy).
FACET_PHRASES = {
    "sponsorship_free": words_re(
        r"without (?:(?:any|a|the) )?(?:(?:need|requirement) for |needing |requiring )?(?:(?:any|a|the) )?(?:(?:visa|employer|work) )?sponsor(?:ship)?",
        r"no (?:(?:visa|employer|work) )?sponsor(?:ship)?(?: (?:required|needed|necessary))?",
        r"not (?:needing|requiring|need|require)(?: (?:a|any|the))?(?: (?:visa|employer|work))? sponsor(?:ship)?",
        r"sin (?:(?:la )?necesidad de |requerir |necesitar |precisar )?(?:(?:un|el|ningun) )?(?:patrocinio|sponsor)(?: de visado)?",
        r"no (?:necesit|requier|preci)\w* (?:de )?(?:(?:un|el|ningun) )?(?:patrocinio|sponsor)(?: de visado)?"),
    "visa_free": words_re(r"without (?:a |any |the )?visa", r"sin (?:un |el )?visad[oa]", r"sin visa"),
    "permanent": words_re(r"permanent(?:ly)?", r"indefinite(?:ly)?", r"permanente(?:mente)?", r"indefinid[oa]", r"de forma indefinida",
                          r"indefinidamente"),
    "unrestricted": words_re(r"unrestricted", r"unlimited", r"without (?:any |the |all )?(?:restrictions|limitations|limits)",
                             r"sin (?:restricciones|limitaciones)", r"ilimitad[oa]", r"sin restriccion"),
    "full_time": words_re(r"full ?time", r"(?:35|40) hours(?: (?:a|per|each|every) week)?", r"tiempo completo", r"jornada completa",
                          r"(?:35|40) horas(?: (?:a la|por) semana)?"),
    "future": words_re(
        r"in (?:the )?future", r"going forward", r"when you (?:start|join|begin|commence|are hired|take up)",
        r"at (?:the |your )?(?:start|beginning|commencement)(?: of (?:your|the|this) (?:employment|role|job|internship|placement|"
        r"contract|position|assignment|programme|program))?",
        r"(?:on|from|at|by) (?:the |your |this )?(?:start|starting|joining|commencement|hire|hiring|first day|employment start)(?: date| day)?",
        r"(?:on|upon|at) (?:the )?(?:time of )?(?:starting|joining|hire|hiring|commencing|beginning)",
        r"at the (?:time|point) of (?:starting|joining|hire|hiring|employment)",
        r"upon (?:graduation|graduating|completion of (?:your|the) (?:degree|studies|course|programme|program))",
        r"after (?:you )?(?:graduat\w*|(?:finish|complete|completing|finishing|completed|finished)(?: your| the)? "
        r"(?:studies|degree|course|programme|program|education|masters?|master s|bachelors?))",
        r"after (?:your|the) (?:studies|degree|course|graduation)", r"post ?graduation", r"following (?:your |the )?(?:graduation|studies|degree)",
        r"on completion of (?:your|the) (?:degree|studies|course)", r"once you (?:graduate|have graduated|finish|complete)",
        r"en el futuro", r"cuando (?:empieces|comiences|te incorpores|inicies)", r"al (?:empezar|comenzar|incorporarte|iniciar)",
        r"en la fecha de (?:incorporacion|inicio)", r"(?:despues de|tras) (?:graduarte|tus estudios|terminar \w+|finalizar \w+)",
        r"una vez (?:terminado|finalizado|graduado)(?: el (?:master|grado|curso))?", r"a partir de la fecha de (?:incorporacion|inicio)"),
    "period": words_re(
        r"(?:for|throughout|during|over) (?:the )?(?:full |entire |whole |complete )?(?:duration|length|term|period|life) of (?:your|the|this|our) "
        r"(?:employment|contract|internship|placement|role|job|programme|program|assignment|position|engagement)",
        r"throughout (?:the |your |this )?(?:entire |full |whole )?(?:internship|placement|employment|contract|role|period|programme|program)",
        r"during (?:the |this |your )(?:internship|placement|programme|program|employment|contract)",
        r"for (?:the )?(?:full |entire |whole )?" + QTY, r"for as long as you (?:work|are employed) (?:here|with us|for us)",
        r"while (?:you are )?employed (?:with|by|at) us", r"(?:the )?(?:full|entire|whole) (?:duration|period)",
        r"durante (?:todo|toda|el|la|las|los|tu) \w+(?: \w+)?", r"toda la duracion", r"(?:periodo|plazo) completo",
        r"durante " + QTY),
}
# the order they are taken in: the longest and most specific first
FACET_ORDER = ("sponsorship_free", "visa_free", "period", "future", "full_time", "unrestricted", "permanent")

# 'Do you now or will you in the future require sponsorship?': WHEN a sponsorship is needed
NOW_WORDS = r"now|currently|at present|at the moment|ahora|actualmente|en este momento"
FUTURE_WORDS = (r"in the future|in future|later|ever|at any time|at any point(?: in the future)?|down the line|"
                r"en el futuro|mas adelante|en algun momento|alguna vez|posteriormente")
TIMEFRAME = words_re(
    r"(?:" + NOW_WORDS + r")(?: (?:or|and|/|o|y|e) (?:will you |would you )?(?:" + FUTURE_WORDS + r"))?",
    r"(?:" + NOW_WORDS + r") (?:or|and|/|o|y|e) (?:will you|would you) (?:" + FUTURE_WORDS + r")",
    r"(?:" + FUTURE_WORDS + r")")
PURPOSE = words_re(
    r"(?:for|to obtain|to get|in order to (?:obtain|get))(?: a| an| the| your| my)?(?: (?:employment|work|working|skilled worker|new|"
    r"temporary|permanent|tier 2|h 1b))* visa(?: status| category| type| route)?",
    r"(?:for|in order for you to|in order to|to) (?:work|be employed|start work|start working|take up (?:this |the )?(?:role|position|job))",
    r"for (?:employment|work|this (?:role|position|job|vacancy|opportunity)|your (?:employment|role|job|position))",
    r"(?:in order )?to be employed(?: by (?:us|the company|an employer))?",
    r"para (?:trabajar|el puesto|este puesto|la vacante|el empleo)", r"de (?:visado|visa)(?: de trabajo)?")
# routes (and the words around them) a list of examples in a bracket may name: 'e.g. H-1B, on a Student visa'
ROUTE_WORDS = {"student", "graduate", "skilled", "worker", "tier", "2", "4", "5", "h", "1b", "h1b", "l", "1", "o", "tn", "e", "3",
               "f", "j", "opt", "cpt", "stem", "green", "card", "permanent", "resident", "settled", "pre", "status", "indefinite",
               "leave", "to", "remain", "ilr", "citizen", "british", "irish", "dependant", "dependent", "family", "spouse",
               "partner", "global", "talent", "innovator", "founder", "high", "potential", "individual", "scale", "up", "health",
               "care", "youth", "mobility", "temporary", "work", "working", "holiday", "other", "blue", "job", "seeker", "self",
               "employed", "entrepreneur", "investor", "visa", "visas", "on", "a", "an", "the", "of", "and", "or", "type", "types",
               "category", "route", "sponsorship", "employment", "uk", "us", "etc", "estudiante", "trabajo", "residencia",
               "visado", "tarjeta", "azul", "de", "y", "o", "con", "derechos", "rights", "right"}

# ------------------------------------------------------------------------------------------------ work right
W = Grammar("work_right")
ADV_W = W.A("currently", "now", "presently", "already", "legally", "lawfully", "actually", "personally", "right now", "at present",
            "at the moment", "today", "actualmente", "ahora", "legalmente", "ya", "en este momento", "hoy")
DET_W = W.A("a", "an", "the", "un", "una", "el", "la", "your", "my", "tu", "su", "mi")
MOD_W = W.A("current", "valid", "legal", "lawful", "full", "existing", "ongoing", "present", "actual", "active", "vigente", "valido",
            "valida", "legal", "actual")
RIGHT = W.A("right to work", "right to live and work", "right to be employed", "authori{s,z}ation to work", "permission to work",
            "eligibility to work", "entitlement to work", "work authori{s,z}ation", "work eligibility", "work entitlement",
            "work permission", "derecho a trabajar", "derecho a vivir y trabajar", "autorizacion para trabajar",
            "autorizacion de trabajo", "autorizacion laboral", "permiso para trabajar", "permiso de trabajo",
            "elegibilidad para trabajar")
AUTH = W.A("authori{s,z}ed", "eligible", "able", "allowed", "permitted", "entitled", "cleared")
AUTH_ES = W.A("autorizado", "autorizada", "habilitado", "habilitada", "elegible", "legitimado", "legitimada")
WORK_TO = W.A("to work")
# the aux words of a present and of a future question ('will you have the right to work' asks the FUTURE right: the engine reads
# the facet from ``tense``)
PRESENT_AUX, FUTURE_AUX = W.A("do", "does"), W.A("will", "would")
for _aux, _label in ((PRESENT_AUX, "do"), (FUTURE_AUX, "will")):
    W.T("have_" + _label, _aux, "you", star(ADV_W), opt("not"), star(ADV_W), W.A("have", "hold", "possess"), opt(DET_W),
        star(MOD_W), RIGHT, star(ADV_W))
W.T("have_nt", W.A("don t", "doesn t", "won t"), "you", star(ADV_W), W.A("have", "hold", "possess"), opt(DET_W), star(MOD_W), RIGHT)
W.T("be", W.A("are", "am"), "you", star(ADV_W), opt("not"), star(ADV_W), AUTH, WORK_TO)
W.T("be_will", "will", "you", "be", star(ADV_W), opt("not"), star(ADV_W), AUTH, WORK_TO)
W.T("be_nt", W.A("aren t", "isn t"), "you", star(ADV_W), AUTH, WORK_TO)
# the label that leaves the subject out ('Authorised to work in the UK?', 'Legally eligible to work in the UK'): no negation, no aux
W.T("be_bare", star(ADV_W), AUTH, WORK_TO, star(ADV_W))
W.T("can", W.A("can", "could"), "you", star(ADV_W), opt("not"), star(ADV_W), "work", star(ADV_W))
W.T("can_nt", W.A("can t", "couldn t"), "you", star(ADV_W), "work", star(ADV_W))
W.T("i_have", "i", star(ADV_W), opt(W.A("do not", "don t")), star(ADV_W), W.A("have", "hold", "possess"), opt("no"), opt(DET_W),
    star(MOD_W), RIGHT)
W.T("i_am", "i", W.A("am", "m"), star(ADV_W), opt("not"), star(ADV_W), AUTH, WORK_TO)
W.T("i_can", "i", W.A("can", "cannot", "can t"), star(ADV_W), "work", star(ADV_W))
W.T("no_right", "no", star(MOD_W), RIGHT)
W.T("you_have", "you", star(ADV_W), opt(W.A("do not", "don t")), star(ADV_W), W.A("have", "hold", "possess"), opt("no"), opt(DET_W), star(MOD_W), RIGHT)
W.T("you_are", "you", W.A("are", "re"), star(ADV_W), opt("not"), star(ADV_W), AUTH, WORK_TO)
W.T("you_can", "you", W.A("can", "cannot", "can t"), star(ADV_W), "work", star(ADV_W))
W.T("es_have", opt("no"), W.A("tienes", "tiene", "dispones de", "dispone de", "cuentas con", "cuenta con", "posees", "tendras", "tendria"),
    star(ADV_W), opt(DET_W),
    star(MOD_W), RIGHT, star(ADV_W))
W.T("es_be", opt("no"), W.A("estas", "esta", "eres", "estaras", "seras", "serias"), star(ADV_W), AUTH_ES, W.A("para", "a"), "trabajar",
    star(ADV_W))
W.T("es_can", opt("no"), W.A("puedes", "puede", "podrias", "podras"), star(ADV_W), "trabajar", star(ADV_W))
W.T("es_be_bare", star(ADV_W), AUTH_ES, W.A("para", "a"), "trabajar", star(ADV_W))
W.T("es_i_have", W.A("no tengo", "tengo"), star(ADV_W), opt(DET_W), star(MOD_W), RIGHT)
W.T("es_i_am", W.A("no estoy", "estoy"), star(ADV_W), AUTH_ES, W.A("para", "a"), "trabajar", star(ADV_W))
W.T("es_i_can", W.A("no puedo", "puedo"), star(ADV_W), "trabajar", star(ADV_W))
WORK_LABELS = Labels(
    heads=["right to work", "right to live and work", "authori{s,z}ation to work", "permission to work", "eligibility to work",
           "work authori{s,z}ation", "work eligibility", "work entitlement", "work permission", "derecho a trabajar",
           "autorizacion para trabajar", "autorizacion de trabajo", "autorizacion laboral", "permiso para trabajar",
           "permiso de trabajo"],
    mods=["current", "valid", "legal", "status", "check", "confirmation", "your", "the", "my", "please", "confirm", "state",
          "indicate", "select", "choose", "provide", "enter", "specify", "verify", "actual", "vigente", "estado", "tu", "su",
          "what", "is", "are", "of", "for", "confirma", "indica", "selecciona", "cual", "es", "tu"])
WORK_BRACKETS = W.vocab | WORK_LABELS.vocab | ROUTE_WORDS | {"with", "work", "rights", "right"}

# ------------------------------------------------------------------------------------------------ sponsorship
S = Grammar("sponsorship")
S_MOD = S.A("visa", "employer", "work", "employment", "company", "immigration", "skilled worker", "h 1b", "h1b", "tier 2", "l 1",
            "o 1", "tn", "e 3", "work permit", "permit", "work visa", "employment visa")
NP = "(?:(?:a|an|the|any|your|my) )?(?:" + "(?:" + S_MOD[3:-1] + ") )*sponsorship"
S.vocab.update({"a", "an", "the", "any", "your", "my", "sponsorship"})
NEEDV = S.A("need", "require")
REQD = S.A("required", "needed", "necessary")
NP_ES = S.A("patrocinio", "sponsorship", "patrocinio de visado", "patrocinio de visa", "patrocinio laboral", "patrocinio de empleo")
SP_EMPLOYER = S.A("employer", "company", "organisation", "organization", "us", "the employer", "the company", "an employer",
                  "the organisation", "the organization", "el empleador", "la empresa")
for _aux in ("do", "will", "would", "does"):
    S.T("need_" + _aux, _aux, "you", opt("not"), NEEDV, NP)
    S.T("employer_" + _aux, _aux, "you", opt("not"), NEEDV, opt(S.A("the", "an", "a")), SP_EMPLOYER, "to", "sponsor",
        opt(S.A("you", "your", "my", "a", "the")), star(S_MOD), opt("visa"))
    S.T("sponsored_" + _aux, _aux, "you", opt("not"), NEEDV, "to", "be", "sponsored")
S.T("going_to", "are", "you", opt("not"), "going", "to", NEEDV, NP)
S.T("need_nt", S.A("don t", "won t", "wouldn t", "doesn t"), "you", NEEDV, NP)
S.T("is_required", "is", NP, opt("not"), REQD, opt(S.A("for you", "by you")))
S.T("will_be_required", "will", NP, "be", opt("not"), REQD, opt(S.A("for you", "by you")))
S.T("i_need", "i", opt(S.A("will", "would", "do", "d", "ll", "shall")), opt(S.A("not", "never")), NEEDV, NP)
S.T("i_need_nt", "i", S.A("don t", "won t", "wouldn t"), NEEDV, NP)
S.T("i_need_employer", "i", opt(S.A("d", "would", "will", "ll", "do")), opt(S.A("not", "never")), NEEDV, opt(S.A("the", "an", "a")),
    SP_EMPLOYER, "to", "sponsor", opt(S.A("my", "your", "a", "the")), star(S_MOD), opt("visa"))
S.T("statement_required", NP, opt(S.A("is", "will be")), opt("not"), REQD)
S.T("no_required", "no", NP, opt(REQD))
S.T("need_bare", S.A("need", "needs", "require", "requires"), NP)
S.T("without", "without", opt(S.A("any", "the", "a")), opt(S.A("needing", "requiring", "the need for", "a need for", "need for", "the requirement for",
    "a requirement for", "requirement for", "needing any", "requiring any")), opt(S.A("any", "the", "a")), star(S_MOD), "sponsorship")
S.T("es_without", "sin", opt(S.A("necesidad de", "requerir", "necesitar", "precisar")), opt(S.A("un", "el", "ningun")), NP_ES)
S.T("you_need", "you", opt(S.A("will", "do", "would")), opt(S.A("not", "never")), NEEDV, NP)
S.T("you_need_nt", "you", S.A("don t", "won t", "wouldn t"), NEEDV, NP)
S.T("es_need", opt("no"), S.A("necesitas", "necesitaras", "requieres", "requeriras", "precisas", "necesitarias", "necesita", "requiere"),
    opt(S.A("un", "el", "algun")), NP_ES)
S.T("es_i_need", opt("no"), S.A("necesito", "requiero", "preciso", "necesitare", "requerire"), opt(S.A("un", "el", "algun")), NP_ES)
S.T("es_required", NP_ES, opt("es"), opt("no"), S.A("necesario", "requerido", "obligatorio"))
SPONSOR_LABELS = Labels(heads=["sponsorship", "patrocinio", "sponsor"],
                        mods=["visa", "employer", "work", "employment", "required", "needed", "need", "require", "requires",
                              "your", "the", "a", "my", "will", "you", "do", "is", "now", "future", "necesario", "requerido",
                              "de", "visado", "para", "trabajar", "or", "and", "status", "type", "category"])
SPONSOR_BRACKETS = S.vocab | SPONSOR_LABELS.vocab | ROUTE_WORDS | {"with", "work", "rights"}

# ------------------------------------------------------------------------------------------------ visa
V = Grammar("visa")
ROUTE_NAMES = V.A("student", "graduate", "skilled worker", "tier 2", "tier 4", "tier 5", "h 1b", "h1b", "l 1", "o 1", "tn", "e 3",
                  "f 1", "j 1", "opt", "cpt", "stem opt", "green card", "permanent resident", "settled", "pre settled",
                  "indefinite leave to remain", "ilr", "citizen", "british citizen", "irish citizen", "dependant", "dependent",
                  "family", "spouse", "partner", "global talent", "innovator founder", "high potential individual", "scale up",
                  "health and care", "youth mobility", "temporary", "work", "working holiday", "other", "blue card", "job seeker",
                  "self employed", "entrepreneur", "investor", "estudiante", "trabajo", "residencia")
V.T("i_hold", "i", star(V.A("currently", "now", "presently", "actualmente")), V.A("hold", "have", "am on", "m on", "am in", "m in",
    "am a holder of"), opt(V.A("a", "an", "the", "my")), star(V.A("valid", "current")), star(ROUTE_NAMES + r"(?: (?:or|and|/))?"),
    opt(V.A("visa", "status")))
V.T("es_i_hold", V.A("tengo", "poseo", "estoy con"), opt(V.A("un", "una", "el")), star(ROUTE_NAMES), opt(V.A("visado", "visa", "estatus")))
VISA_LABELS = Labels(
    heads=["visa", "visas", "visado", "immigration", "inmigracion", "extranjeria", "migratoria", "migratorio"],
    mods=["type", "route", "status", "category", "held", "current", "currently", "your", "the", "a", "my", "what", "which", "is", "are",
          "do", "you", "hold", "have", "details", "detail", "give", "describe", "explain", "confirm", "please", "state", "specify",
          "situation", "of", "and", "or", "with work rights", "tipo", "estado", "actual", "tu", "cual", "es", "su", "situacion", "que",
          "tienes", "tiene", "posees", "mantienes",
          "estatus", "legal", "explica", "detalles", "de", "del", "permit", "permits", "for", "enter", "select", "choose",
          "provide", "kind", "that", "to", "student", "graduate", "skilled worker", "tier 2", "tier 4", "tier 5", "h 1b", "h1b", "l 1",
          "o 1", "tn", "e 3", "f 1", "j 1", "opt", "cpt", "stem opt", "green card", "settled", "pre settled", "indefinite leave to remain",
          "ilr", "citizen", "british citizen", "irish citizen", "dependant", "dependent", "global talent", "innovator founder",
          "high potential individual", "scale up", "health and care", "youth mobility", "working holiday", "blue card", "job seeker",
          "self employed", "entrepreneur", "investor", "estudiante", "residencia", "other", "temporary", "family", "spouse", "partner"])
VISA_BRACKETS = V.vocab | VISA_LABELS.vocab | ROUTE_WORDS | {"with"}

# ------------------------------------------------------------------------------------------------ language
Lg = Grammar("language")
LEVEL_WORDS = sorted({re.sub(r"[^a-z0-9 ]+", " ", w).strip() for words in languages.CLASSES.values() for w in words} - {""})
LEVEL_ANY = Lg.A(*LEVEL_WORDS, "proficient", "competent", "comfortable", "good", "skilled", "excellent", "full", "limited", "working",
                 "professional working proficiency", "fluent or native", "native or fluent")
LANGS = LANG + "(?: (?:(?:and|or|y|o|e) )?" + LANG + ")*"
LEVEL_NOUN = Lg.A("level", "standard", "proficiency", "fluency", "nivel", "dominio", "competence", "ability", "knowledge", "command",
                  "skills", "cefr level", "cefr")
DET_L = Lg.A("a", "an", "the", "un", "una")
TAIL_L = Lg.A("fluently", "well", "natively", "proficiently", "professionally", "conversationally", "comfortably", "con fluidez",
              "con fluencia", "de forma nativa", "nativamente", "bien", "fluidamente", "perfectly", "at work", "in writing",
              "verbally", "orally", "en el trabajo", "por escrito")
LEVEL_PHRASE = opt(Lg.A("at", "to", "with", "con", "a", "en", "de") + "(?: " + DET_L + ")?" + " " + LEVEL_ANY + "(?: " + LEVEL_NOUN + ")?")
LEVEL_PHRASE_ES = opt(Lg.A("con", "de", "en", "a") + " (?:nivel )?" + LEVEL_ANY)
ADV_L = Lg.A("currently", "actually", "already", "really", "actualmente", "ya")
SPEAK = Lg.A("speak", "speak and write", "write", "read", "understand", "use", "communicate in", "work in", "hablar")
for _aux in ("do", "can", "could", "will", "would"):
    Lg.T("speak_" + _aux, _aux, "you", star(ADV_L), SPEAK, LANGS, star(TAIL_L), LEVEL_PHRASE, star(TAIL_L))
Lg.T("speak_ability", Lg.A("are", "will"), "you", opt("be"), "able", "to", SPEAK, LANGS, star(TAIL_L), LEVEL_PHRASE)
Lg.T("be_adj", Lg.A("are", "am"), "you", star(ADV_L), opt(DET_L), LEVEL_ANY, opt(Lg.A("in", "at", "with")), opt(LANGS), opt(Lg.A("speaker", "speakers")),
     star(TAIL_L), LEVEL_PHRASE)
Lg.T("be_speaker", Lg.A("are", "am"), "you", opt(DET_L), LEVEL_ANY, LANGS, Lg.A("speaker", "speakers"))
Lg.T("is_first", "is", LANGS, "your", Lg.A("first", "native", "mother", "primary", "main", "home"), Lg.A("language", "tongue"))
Lg.T("is_first_b", "is", "your", Lg.A("first", "native", "mother", "primary", "main", "home"), Lg.A("language", "tongue"), LANGS)
Lg.T("have_prof", Lg.A("do", "does"), "you", "have", opt(DET_L), star(LEVEL_ANY), LEVEL_NOUN, Lg.A("in", "of"), LANGS, LEVEL_PHRASE)
Lg.T("have_level", Lg.A("do", "does"), "you", "have", opt(DET_L), LEVEL_ANY, opt(LEVEL_NOUN), Lg.A("in", "of"), LANGS)
Lg.T("what_level", Lg.A("what", "which"), "is", "your", opt(Lg.A("current", "spoken", "written")), LEVEL_NOUN, Lg.A("of", "in"), LANGS)
Lg.T("es_speak", opt("no"), Lg.A("hablas", "habla", "sabes", "dominas", "puedes hablar", "hablaras"), LANGS, star(TAIL_L), LEVEL_PHRASE_ES,
     star(TAIL_L))
Lg.T("es_be", opt("no"), Lg.A("eres", "es", "eres de", "estas"), opt(DET_L), LEVEL_ANY, opt(Lg.A("de", "en")), opt(LANGS), star(TAIL_L))
Lg.T("es_speaker", Lg.A("eres", "es"), opt(DET_L), Lg.A("hablante", "hablantes"), opt(LEVEL_ANY), Lg.A("de", "en"), LANGS, star(TAIL_L))
Lg.T("es_is_first", "es", LANGS, "tu", Lg.A("primera lengua", "lengua materna", "idioma nativo", "idioma materno", "lengua nativa",
                                          "primer idioma", "idioma principal"))
Lg.T("es_level", Lg.A("cual", "que"), "es", "tu", LEVEL_NOUN, "de", LANGS)
LANGUAGE_LABELS = Labels(
    heads=[LANG, "language", "languages", "idioma", "idiomas", "lengua", "lenguas", "mother tongue", "native language",
           "first language", "proficiency", "fluency", "level", "lengua materna", "idioma materno", "competencia", "dominio",
           "nivel", "ability"],
    mods=[*LEVEL_WORDS, "language", "languages", "skill", "skills", "spoken", "written", "native", "mother", "tongue", "first",
          "primary", "your", "my", "the", "of", "in", "what", "which", "is", "are", "do", "you", "speak", "select", "all", "that",
          "apply", "choose", "check", "current", "nivel", "de", "dominio", "competencia", "tu", "cual", "es", "que", "hablas",
          "idiomas", "level", "proficiency", "fluency", "ability", "a", "an", "and", "or", "y", "o", "e", "mastery", "knowledge",
          "command", "per", "each", "please", "enter", "state", "main", "home", "fluente", "cefr", "common european framework", "scale"])
LANGUAGE_BRACKETS = Lg.vocab | LANGUAGE_LABELS.vocab | {LANG}

# ------------------------------------------------------------------------------------------------ education
E = Grammar("education")
LEVEL_PHRASES = ["primary", "elementary", "secondary", "high school", "sixth form", "baccalaureate", "bachillerato", "eso", "gcse",
                 "gcses", "a level", "a levels", "bachelor", "bachelors", "bachelor s", "undergraduate", "bsc", "ba", "beng", "llb",
                 "master", "masters", "master s", "postgraduate", "post graduate", "msc", "ma", "meng", "mba", "llm", "doctorate",
                 "doctoral", "phd", "dphil", "doctorado", "associate", "associate s", "grado", "licenciatura", "posgrado", "postgrado",
                 "maestria", "bachelor of science", "bachelor of arts", "master of science", "master of arts", "master of engineering",
                 "bachelor of engineering", "master of business administration", "master s degree", "bachelor s degree",
                 "foundation", "higher national diploma", "hnd", "hnc", "diploma", "doctor", "professional", "secondary school",
                 "secondary education", "high school education", "primary education", "primary school", "elementary school"]
LEVEL_E = E.A(*LEVEL_PHRASES)
DEGW = E.A("degree", "degrees", "qualification", "qualifications", "diploma", "diplomas", "titulo", "titulacion", "grado", "carrera",
           "studies", "education", "estudios", "formacion")
DET_E = E.A("a", "an", "the", "any", "your", "my", "un", "una", "el", "la", "tu", "su")
MODE = E.A("completed", "finished", "university", "college", "first", "higher education", "further", "academic", "full", "universitario",
           "universitaria", "primer", "primera")
ADV_E = E.A("currently", "already", "now", "actually", "successfully", "fully", "actualmente", "ya", "ahora")
INSTN = E.A("university", "college", "school", "institution", "universidad", "colegio", "escuela", "facultad", "instituto", "institute")
INSTSLOT = "(?:(?:at|in|from|by|en|de|de la|por) )?(?:" + INST + "|(?:" + DET_E[3:-1] + " )?(?:" + PLACE + " )*" + INSTN + ")"
SUBJSLOT = "(?:in|of|on|en|de|sobre) " + SUBJ
TAIL = (opt(SUBJSLOT), opt(INSTSLOT))
DEGREE_TARGET = (opt(DET_E), star(MODE), opt(LEVEL_E), opt(DEGW))
for _aux in ("do", "does", "did"):
    E.T("have_" + _aux, _aux, "you", star(ADV_E), E.A("have", "hold", "possess", "own", "get", "obtain", "complete", "finish", "earn",
        "graduate from", "graduate with", "graduate in", "study"), opt(DET_E), star(MODE), opt(LEVEL_E), DEGW, *TAIL)
    E.T("have_level_" + _aux, _aux, "you", star(ADV_E), E.A("have", "hold", "possess", "own", "get", "obtain", "complete", "finish",
        "earn", "graduate from"), opt(DET_E), star(MODE), LEVEL_E, *TAIL)
E.T("completed", E.A("have", "has"), "you", star(ADV_E), E.A("completed", "finished", "obtained", "earned", "received", "achieved",
    "gained", "attained", "graduated from", "graduated with", "graduated in", "graduated", "done", "studied", "got"), opt(DET_E),
    star(MODE), opt(LEVEL_E), opt(DEGW), *TAIL)
E.T("studying", E.A("are", "am"), "you", star(ADV_E), E.A("studying", "pursuing", "enrolled", "undertaking", "doing", "working towards",
    "taking", "attending", "a student", "a university student", "studying for",
    "enrolled in", "enrolled at", "registered", "reading"), opt(DET_E), star(MODE), opt(LEVEL_E), opt(DEGW), *TAIL)
E.T("will_study", E.A("will", "would"), "you", opt("be"), E.A("studying", "pursuing", "enrolled", "a student", "graduating",
    "graduate", "finish", "complete", "have completed", "have graduated", "have finished"), opt(DET_E), star(MODE), opt(LEVEL_E),
    opt(DEGW), *TAIL)
E.T("recent", E.A("are", "am", "have"), "you", star(ADV_E), opt(DET_E), E.A("recent graduate", "recently graduated", "graduate",
    "graduated recently"), opt(E.A("in", "with", "from") + " (?:" + DET_E[3:-1] + " )?(?:" + SUBJ + "|" + INST + ")(?: (?:or|and) " + SUBJ
    + ")?(?: " + DEGW + ")?"))
# statements ('I am studying ... at ...', 'I hold a Master's degree')
E.T("i_have", "i", star(ADV_E), E.A("have", "hold", "possess", "own", "have completed", "have finished", "have obtained", "have earned",
    "completed", "finished", "obtained", "earned", "graduated from"), opt(DET_E), star(MODE), opt(LEVEL_E), DEGW, *TAIL)
E.T("i_am_studying", "i", E.A("am", "m"), star(ADV_E), E.A("studying", "pursuing", "enrolled", "undertaking", "doing", "working towards",
    "taking", "attending", "a student", "a university student", "studying for",
    "enrolled in", "enrolled at"), opt(DET_E), star(MODE), opt(LEVEL_E), opt(DEGW), *TAIL)
E.T("es_have", opt("no"), E.A("tienes", "tiene", "posees", "has completado", "has obtenido", "has terminado", "has finalizado",
    "completaste", "terminaste", "estas en posesion de", "cuentas con", "te has graduado", "te graduaste", "ya te has graduado",
    "has cursado", "has estudiado"), opt(DET_E), star(MODE), opt(LEVEL_E), opt(DEGW), star(MODE), *TAIL)
E.T("where_study", "where", E.A("are", "do", "did"), "you", star(ADV_E), E.A("studying", "study", "studied", "enrolled", "a student"))
E.T("es_where_study", E.A("donde", "en que universidad", "en que centro"), E.A("estudias", "estudiaste", "cursas", "estas estudiando", "estas matriculado",
    "estas matriculada"), star(ADV_E))
E.T("es_studying", opt("no"), E.A("estas", "estudias", "estudiaste", "eres", "cursas", "estas cursando"), star(ADV_E),
    opt(E.A("estudiando", "estudiante", "matriculado", "matriculada", "cursando", "un estudiante", "una estudiante")), star(ADV_E), opt(DET_E),
    star(MODE), opt(LEVEL_E), opt(DEGW), *TAIL)
# graduation: a RELATION between the graduation date and the date(s) asked (``academic.relation_of`` reads which)
DATES_G = DATE + "(?: (?:or|and|to|y|o|a|e) " + DATE + "){0,2}"
REL_WORDS = ("by", "before", "after", "since", "in", "between", "until", "no later than", "not later than", "on or before", "prior to",
             "from", "during", "within", "on", "through", "up to", "at the latest", "as of", "following", "later than", "earlier than",
             "not after", "no earlier than", "not before", "not earlier than", "past", "entre", "hasta", "para", "a mas tardar",
             "como maximo", "antes de", "antes del", "con anterioridad a", "despues de", "despues del", "tras", "desde",
             "a partir de", "a partir del", "en", "durante", "el", "posterior a")
REL = E.A(*REL_WORDS)
END_OF = E.A("the end of", "end of", "el final de", "finales de", "final de")
GRADV = E.A("graduate", "graduated", "graduating", "finish", "finished", "finishing", "complete", "completed", "completing", "conclude",
            "concluded", "qualify", "qualified", "graduation", "graduaste", "graduaras", "te gradues", "terminaras", "terminas", "acabas",
            "acabaras", "finalizas", "finalizaras", "te graduas", "te graduaste", "te has graduado", "has terminado", "has completado",
            "te habras graduado", "te graduaras", "habras terminado", "terminaste", "acabaste", "finalizaste")
STUDIES = E.A("your studies", "your degree", "your course", "your programme", "your program", "your master s", "your bachelor s",
              "your masters", "your education", "your undergraduate degree", "your postgraduate degree", "your msc", "your bsc", "your phd",
              "studies", "the course", "the degree", "your undergraduate studies", "your postgraduate studies", "tus estudios",
              "tu grado", "tu master", "tu carrera", "tu titulacion", "el grado", "el master", "la carrera", "los estudios")
G_AUX = E.A("will", "would", "do", "did", "have", "has", "are", "were", "am", "does", "habras", "has")
E.T("grad_relation", G_AUX, "you", opt("have"), star(ADV_E), GRADV, opt(STUDIES), opt(REL), opt(END_OF), DATES_G)
E.T("grad_relation_b", G_AUX, "you", opt("be"), star(ADV_E), GRADV, opt(STUDIES), opt(REL), opt(END_OF), DATES_G)
E.T("grad_relation_es", opt(E.A("te", "ya", "ya te")), E.A("graduaras", "gradues", "graduaste", "has graduado", "habras graduado", "terminas",
    "terminaras", "acabas", "has terminado", "habras terminado", "finalizas", "finalizaras", "te graduaras", "te has graduado",
    "te habras graduado"), opt(STUDIES), opt(REL), opt(END_OF), DATES_G)
E.T("grad_when", "when", E.A("do", "will", "did", "are", "would", "can"), "you", opt(E.A("expect to", "plan to", "intend to", "hope to")),
    opt("be"), GRADV, opt(STUDIES), opt(DATES_G))
E.T("grad_what_year", E.A("what", "which"), E.A("year", "month", "date"), E.A("does", "will", "do", "is", "did", "are"), opt(E.A("your", "the")),
    opt(E.A("course", "degree", "studies", "programme", "program", "master s", "masters", "bachelor s", "study", "education")),
    opt("you"), GRADV, opt(STUDIES))
E.T("grad_what_year_b", E.A("what", "which"), E.A("year", "month", "date"), "do", "you", GRADV, opt(STUDIES))
E.T("grad_when_es", E.A("cuando", "en que ano", "en que mes"), E.A("terminas", "acabas", "finalizas", "te graduas", "te graduaras", "terminaras",
    "acabaras", "finalizaras", "terminaste", "te graduaste"), opt(STUDIES))
E.T("study_year", E.A("what", "which"), "year", "of", E.A("your", "the"), E.A("studies", "degree", "course", "programme", "program", "study"),
    E.A("are", "is"), "you", opt("in"))
EDU_HEADS = ["university", "universities", "college", "school", "institution", "universidad", "facultad", "centro de estudios",
             "escuela", "education", "educacion", "degree", "qualification", "qualifications", "diploma", "titulo", "titulacion", "grado",
             "discipline", "subject", "major", "field of study", "level of education", "level of study", "gpa", "grade", "grades",
             "classification", "average", "honours", "result", "percentage", "module", "modules", "course", "coursework",
             "year of study", "graduation", "completion", "formacion", "estudios", "carrera", "especialidad", "nota", "nota media",
             "calificacion", "expediente", "promedio", "clasificacion", "disciplina", "asignatura", "graduacion", "finalizacion",
             "student", "estudiante", "studies", "study", "enrolled", "enrol", "matricula", "class", "predicted", "graduate", "graduated",
             "graduating", "nivel educativo", "nivel de estudios", "degrees", "other degrees", "other qualifications", "additional degrees",
             "additional qualifications", "start date",
             "end date", "start year", "end year", "start month", "end month", "dates", "date range", "from", "to"]
EDU_MODS = ["current", "currently", "most", "recent", "latest", "last", "highest", "completed", "finished", "attended", "enrolled",
            "graduated", "studying", "studied", "pursuing", "undergraduate", "postgraduate", "post graduate", "bachelor", "bachelors",
            "bachelor s", "master", "masters", "master s", "doctorate", "phd", "secondary", "primary", "high", "first", "second",
            "third", "final", "overall", "official", "level", "type", "name", "of", "in", "at", "from", "the", "a", "an", "your", "my",
            "i", "you", "are", "is", "was", "did", "do", "have", "has", "what", "which", "where", "whom", "academic", "cumulative",
            "weighted", "numeric", "result", "class", "honours", "year", "already", "earned", "awarded", "achieved", "obtained", "above",
            "or", "select", "enter", "please", "state", "give", "provide", "specify", "confirm", "list", "choose", "date", "month",
            "expected", "anticipated", "intended", "planned", "predicted", "forecast", "target", "projected", "degree", "average",
            "bsc", "msc", "ba", "ma", "mba", "phd", "gcse", "a level", "a levels", "high school", "sixth form", "days", "until",
            "optional", "post", "graduate", "which", "that", "mi", "tu", "su", "el", "la", "los", "las", "de", "del", "en", "actual",
            "ultimo", "ultima", "mas", "reciente", "alto", "alta", "completado", "completada", "finalizado", "cursando", "estudias",
            "estudiando", "tipo", "nivel", "nombre", "cual", "que", "donde", "cuando", "ano", "mes", "fecha", "esperada", "prevista",
            "previsto", "final", "global", "ponderada", "numerica", "es", "obtenida", "obtenido", "titulo", "year 1", "year 2", "year 3",
            "year 4", "1st", "2nd", "3rd", "4th", "range", "am", "year", "graduate", "for", "i", "when", "first class", "second class", "upper second", "lower second", "third class",
            "pass", "merit", "distinction", "zznum", "zzsubj", "zzinst", "zzdate", "zzplace", "zzqty", "when", "how", "many", "much", "long",
            "does", "will", "finish", "end", "complete", "begin", "start", "study", "course", "program", "programme", "with", "work",
            "time", "full", "part", "status", "university", "institution", "school", "college"]
EDU_LABELS = Labels(heads=EDU_HEADS + [DATE, SUBJ, INST], mods=EDU_MODS + DATE_FORMAT_WORDS + LEVEL_PHRASES + [
    "start and end", "other degrees", "other qualifications", "additional degrees", "additional qualifications", "educativo", "educativa",
    "nivel educativo", "nivel de estudios", "answer with", "respond with"])
EDUCATION_BRACKETS = E.vocab | EDU_LABELS.vocab | {"optional", "required", "gpa", "uk", "us", "zzplace", "zzdate"}

# ------------------------------------------------------------------------------------------------ notice period
N = Grammar("notice")
MOD_N = N.A("current", "contractual", "employment", "present", "actual", "actual", "contractual")
N_NOUN = "(?:" + N.A("notice", "preaviso")[3:-1] + ")"
NP_N = "(?:" + MOD_N + " )*notice(?: period)?"
THRESHOLD = "zzthr"
SLOT_TOKENS.add(THRESHOLD)
N.T("have_notice", N.A("do", "does"), "you", star(N.A("currently", "now", "actually", "presently", "actualmente", "ahora")),
    "have", opt(N.A("a", "an", "any", "your", "the")), star(MOD_N), "notice", opt("period"))
N.T("have_notice_of", N.A("do", "does"), "you", "have", opt(N.A("a", "an", "any")), star(MOD_N), "notice", opt("period"),
    opt("of"), THRESHOLD)
N.T("have_threshold_notice", N.A("do", "does"), "you", "have", opt(N.A("a", "an")), THRESHOLD, star(MOD_N), "notice", opt("period"))
N.T("subject_to", N.A("are", "am"), "you", star(N.A("currently", "now", "actually", "presently")), "subject to",
    opt(N.A("a", "an", "any", "your")), star(MOD_N), "notice", opt("period"), opt("of"), opt(THRESHOLD))
N.T("is_notice", N.A("is", "will"), N.A("your", "the"), star(MOD_N), "notice", opt("period"), opt("be"), opt("of"), THRESHOLD)
N.T("es_have_notice", opt("no"), N.A("tienes", "tiene", "cuentas con", "debes dar"), opt(N.A("un", "el", "algun")), star(N.A("actual", "contractual")),
    "preaviso", opt("de"), opt(THRESHOLD))
N.T("es_is_notice", N.A("tu", "el"), star(N.A("actual", "contractual")), N.A("preaviso", "periodo de preaviso"), N.A("es", "sera"), opt("de"),
    THRESHOLD)
NOTICE_LABELS = Labels(
    heads=["notice", "notice period", "preaviso", "periodo de preaviso", "aviso previo", "plazo de preaviso", "tiempo de aviso",
           "period of notice", "periodo de aviso", "plazo de aviso"],
    mods=["current", "contractual", "employment", "present", "your", "the", "a", "what", "is", "are", "how", "much", "length", "of",
          "required", "to", "give", "resign", "please", "describe", "state", "enter", "confirm", "provide", "specify", "period",
          "days", "weeks", "months", "day", "week", "month", "dias", "semanas", "meses", "in", "en", "number", "expressed", "cual",
          "es", "tu", "actual", "contractual", "laboral", "de", "del", "mi", "su", "que", "or", "o", "need", "do", "you", "long",
          "tiempo", "periodo", "plazo", "cuanto", "debes", "dar", "details", "detalles", "none", "ninguno", "optional", "other",
          "otro", "otra", "in days", "years", "year", "unit", "units"])
NOTICE_BRACKETS = N.vocab | NOTICE_LABELS.vocab | {"weeks", "days", "months", "e", "g"}
COMPARATORS = {
    ">": ("more than", "longer than", "greater than", "over", "exceeding", "above", "in excess of", "mas de", "superior a", "mayor que",
          "mayor a", "mas largo que"),
    "<": ("less than", "shorter than", "fewer than", "under", "below", "menos de", "inferior a", "menor que", "menor a", "mas corto que"),
    "<=": ("no more than", "up to", "at most", "a maximum of", "maximum of", "max of", "within", "como maximo", "hasta", "maximo",
           "no mas de", "a maximum", "maximum"),
    ">=": ("at least", "a minimum of", "minimum of", "no less than", "al menos", "como minimo", "minimo", "no menos de", "a minimum",
           "minimum"),
    "=": ("exactly", "equal to", "exactamente", "igual a"),
}
SUFFIXES = {">=": ("or more", "or longer", "or greater", "and above", "and over", "or above", "plus", "o mas", "o mayor", "o superior",
                   "y mas"),
            "<=": ("or less", "or shorter", "or fewer", "or under", "or below", "o menos", "o menor", "o inferior")}
CMP_PREFIX = {phrase: sign for sign, phrases in COMPARATORS.items() for phrase in phrases}
CMP_SUFFIX = {phrase: sign for sign, phrases in SUFFIXES.items() for phrase in phrases}
THRESHOLD_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:(?P<pre>" + "|".join(sorted(map(re.escape, CMP_PREFIX), key=len, reverse=True)) + r") )?" + QTY +
    r"(?: (?P<post>" + "|".join(sorted(map(re.escape, CMP_SUFFIX), key=len, reverse=True)) + r"))?(?![a-z0-9])")

# ------------------------------------------------------------------------------------------------ availability
AV = Grammar("availability")
AV_REL = AV.A("on", "by", "from", "as of", "starting", "no later than", "not later than", "on or before", "el", "para", "desde",
              "a partir de", "como muy tarde")
DATES_A = DATE + "(?: (?:or|and|to|y|o) " + DATE + ")?"
for _aux in ("are", "am", "will"):
    AV.T("avail_" + _aux, _aux, "you", opt("be"), star(AV.A("currently", "now", "actually", "presently")), AV.A("available", "able", "free",
         "ready"), opt(AV.A("to start", "to join", "to begin", "to commence", "to start work", "to start working")), opt(AV_REL), DATES_A)
for _aux in ("can", "could", "would", "will"):
    AV.T("start_" + _aux, _aux, "you", opt(AV.A("be able to", "possibly")), AV.A("start", "join", "begin", "commence", "start work",
         "start working", "join us", "start with us"), opt(AV_REL), DATES_A)
AV.T("es_avail", AV.A("puedes", "podrias", "podras", "estas", "estarias", "estaras"), opt(AV.A("estar", "incorporarte", "empezar", "comenzar")),
     opt(AV.A("disponible", "disponibles")), opt(AV.A("para empezar", "para incorporarte", "para comenzar")), opt(AV_REL), DATES_A)
AVAIL_LABELS = Labels(
    heads=["start date", "starting date", "joining date", "commencement date", "availability", "available", "start", "join",
           "incorporacion", "fecha de incorporacion", "fecha de inicio", "disponibilidad", "begin", "commence", "inicio",
           "empezar", "comenzar", "incorporarte", "incorporarse", "starting", "joining", "fecha de comienzo"],
    mods=["earliest", "anticipated", "expected", "preferred", "planned", "possible", "available", "employment", "work", "your", "my",
          "the", "date", "month", "year", "from", "for", "to", "when", "can", "could", "you", "us", "how", "soon", "early", "is", "are",
          "what", "i", "am", "commencement", "please", "enter", "select", "state", "specify", "provide", "fecha", "de", "inicio",
          "comienzo", "cuando", "podrias", "puedes", "tu", "su", "mas", "pronto", "cual", "es", "mi", "estar", "en", "disponible",
          "immediately", "inmediatamente", "would", "like", "be", "able", "full", "time", "tiempo", "completo", "yyyy", "mm", "dd",
          "month year", "empleo", "trabajo", "al", "el", "la", "en", "que", "podria", "podriamos", "able", "ready", "free", "new",
          "role", "job", "position", "puesto", "rol", "un", "una", "a", "an", "of", "on", "at", "by", "after", "notice", "yyyy mm",
          "dd mm yyyy", "mm yyyy", "month year", "date format", *DATE_FORMAT_WORDS])
AVAIL_BRACKETS = AV.vocab | AVAIL_LABELS.vocab | {"mm", "yyyy", "dd", "month", "year", "format", "e", "g", "date"}

# ------------------------------------------------------------------------------------------------ place
Lc = Grammar("location")
ADV_LC = Lc.A("currently", "now", "actually", "presently", "actualmente", "ahora", "permanently", "normally", "usually", "at present")
BASED = Lc.A("based", "located", "living", "resident", "residing", "situated", "staying", "domiciled", "radicado", "radicada", "ubicado",
             "ubicada", "residente", "viviendo", "residiendo")
for _aux in ("are", "am"):
    Lc.T("based_" + _aux, _aux, "you", star(ADV_LC), BASED, opt(Lc.A("of", "in", "at")), opt("a resident"))
Lc.T("is_resident", Lc.A("are", "am"), "you", opt("a"), star(ADV_LC), "resident", opt("of"))
Lc.T("live", Lc.A("do", "does"), "you", star(ADV_LC), Lc.A("live", "reside", "stay", "currently live", "currently reside"), opt(Lc.A("in", "at")))
Lc.T("is_your", "is", "your", star(Lc.A("current", "present", "home", "residential", "permanent")), Lc.A("city", "country", "location", "town",
     "place of residence", "country of residence", "city of residence", "home", "region", "state", "province", "county"))
Lc.T("es_live", opt("no"), Lc.A("resides", "vives", "estas ubicado", "estas ubicada", "te encuentras", "estas radicado", "estas radicada",
     "estas viviendo", "estas residiendo", "eres residente", "estas domiciliado", "estas domiciliada", "vives actualmente"), star(ADV_LC))
LOCATION_LABELS = Labels(
    heads=["location", "city", "town", "country", "region", "state", "province", "county", "residence", "place of residence",
           "ubicacion", "ciudad", "pais", "provincia", "localidad", "municipio", "residencia", "country code", "codigo de pais",
           "comunidad autonoma", "domicilio", "located", "based", "live", "lives", "living", "reside", "resides", "residing", "ubicado",
           "ubicada", "radicado", "radicada", "vives", "resides", "donde"],
    mods=["territory", "territorio", "territories", "two letter", "iso", "alpha 2", "for my location", "for my residence", "current", "currently", "present", "home", "residential", "your", "my", "the", "of", "where", "are", "you", "located",
          "based", "live", "do", "reside", "i", "in", "select", "selected", "code", "name", "ubicado", "ubicada", "resides", "vives",
          "estas", "donde", "cual", "es", "tu", "actual", "de", "residencia", "estoy", "zzplace", "or", "o", "and", "y", "state",
          "province", "region", "county", "city", "town", "country", "optional", "required", "primary", "main", "permanent",
          "actually", "now", "dial", "area", "una", "un", "el", "la", "mi", "su", "viviendo", "residente", "a", "an", "at", "to",
          "what", "which", "is", "please", "enter", "specify", "state", "provide", "confirm", "name", "only"])
LOCATION_BRACKETS = Lc.vocab | LOCATION_LABELS.vocab | {"city", "country", "region", "uk", "zzplace", "e", "g"}
Rl = Grammar("relocation")
WILL_ADJ = Rl.A("willing", "open", "able", "prepared", "happy", "ready", "keen", "comfortable", "dispuesto", "dispuesta", "abierto",
                "abierta", "preparado", "preparada", "interested")
for _aux in ("are", "am", "would", "will", "is"):
    Rl.T("willing_" + _aux, _aux, opt("you"), opt("be"), star(Rl.A("generally", "also", "really", "currently", "potentially")), WILL_ADJ, "to",
         Rl.A("relocate", "relocating", "move", "move home", "move house", "relocation"), opt("to"))
Rl.T("open_reloc", Rl.A("are", "am", "would", "will"), "you", opt("be"), WILL_ADJ, Rl.A("to relocation", "to relocating", "to a relocation"))
Rl.T("would_reloc", Rl.A("would", "will", "can", "could", "do"), "you", opt(Rl.A("be willing to", "be able to", "consider")), Rl.A("relocate", "relocating"))
Rl.T("es_reloc", opt("no"), Rl.A("estas", "estarias", "estaras", "eres"), WILL_ADJ, Rl.A("a reubicarte", "a mudarte", "a reubicarse",
     "a mudarse", "a cambiar de residencia", "a cambiar de ciudad", "a mudarte de ciudad", "a mudarte de residencia", "a trasladarte",
     "a trasladarse", "a trasladarte de ciudad", "a cambiar de pais", "a reubicarte de ciudad"))
RELOCATION_LABELS = Labels(
    heads=["relocation", "relocate", "relocating", "reubicacion", "reubicarte", "mudarte", "movilidad geografica", "movilidad"],
    mods=["willingness", "willing", "open", "to", "your", "you", "are", "the", "a", "disponibilidad", "para", "dispuesto", "estas",
          "a", "geografica", "de", "residencia", "ciudad", "yes", "no", "or", "o", "would", "be", "able", "ready", "prepared"])
RELOCATION_BRACKETS = Rl.vocab | RELOCATION_LABELS.vocab | {"zzplace", "yes", "no"}

# ------------------------------------------------------------------------------------------------ salary
SALARY_HEADS = ["salary", "salaries", "compensation", "remuneration", "pay", "salary expectation", "salary expectations",
                "pay expectation", "pay expectations", "pay requirement", "salario", "salarios", "sueldo", "retribucion",
                "remuneracion", "pretensiones", "pretensiones salariales", "expectativas salariales", "honorarios", "wage", "wages",
                "salarial", "salariales", "expectations", "expectation"]
SALARY_MODS = ["expected", "expectation", "expectations", "desired", "target", "annual", "annually", "yearly", "gross", "base", "your",
               "my", "the", "what", "are", "is", "do", "you", "have", "expect", "want", "require", "requirement", "requirements",
               "please", "enter", "state", "provide", "confirm", "specify", "details", "detail", "per", "a", "an", "year", "annum", "in",
               "pounds", "pound", "gbp", "eur", "euros", "euro", "usd", "dollars", "dollar", "cad", "aud", "chf", "inr", "monthly", "weekly",
               "daily", "hourly", "fortnightly", "month", "week", "day", "hour", "range", "band", "bracket", "net", "take home", "after tax",
               "tax", "bonus", "equity", "total", "package", "including", "plus", "thousands", "thousand", "k", "000s", "000", "m", "million",
               "millions", "minimum", "maximum", "current", "previous", "last", "of", "for", "this role", "this position", "this job", "expected",
               "e", "g", "and", "why", "explain", "justify", "describe", "breakdown", "base", "pay", "comp", "ote", "commission",
               "commissions", "stock", "shares", "options", "benefits", "variable", "only", "annual salary", "per annum", "pa", "p a",
               "deseado", "deseada", "esperado", "esperada", "anual", "anuales", "bruto", "bruta", "neto", "neta", "mensual", "actual",
               "tu", "su", "mi", "tus", "sus", "mis", "las", "los", "la", "el", "cual", "es", "son", "cuales", "de", "del", "al", "ano", "mes", "euros", "libras", "dolares", "en",
               "por", "rango", "total", "variable", "incluyendo", "mas", "y", "o", "anterior", "ultimo", "ultima", "minimo", "maximo",
               "or", "o", "to", "a", "between", "entre", "range", "monthly", "usd", "currency", "cop", "mxn",
               "sek", "nok", "dkk", "pln", "brl", "jpy", "cny", "sgd", "hkd", "nzd", "aed", "details", "en miles", "en millones",
               "miles", "millones", "en k", "in k", "wish", "wishes", "level", "figure", "number", "amount", "cantidad", "cifra",
               "nivel", "optional", "required", "expected annual", "yearly salary", "looking for", "rate", "bound", "bounds", "upper", "lower",
               "figure", "tu", "salary", "estimate", "salario", "asking", "want", "wanted", "currently"]
SALARY_LABELS = Labels(heads=SALARY_HEADS, mods=SALARY_MODS)
SALARY_BRACKETS = SALARY_LABELS.vocab | {"e", "g", "zznum", "zzqty", "k", "000s", "role", "position", "job"}

# ------------------------------------------------------------------------------------------------ demographics (a personal attribute)
# What is asked is ONE attribute of the person ('Gender', 'What are your pronouns?', 'Do you have a disability?'): the confirmed value
# answers a question about that attribute and nothing else. A question about someone else, a past or a degree of it, a negation, a
# second attribute or a sentence the attribute is merely mentioned in is the user's to answer.
DM_ATTRIBUTES = {
    "gender": ["gender", "gender identity", "genero", "identidad de genero"],
    "sex": ["sex", "sexo", "sex assigned at birth", "sexo asignado al nacer"],
    "pronouns": ["pronouns", "pronoun", "pronombres", "pronombre"],
    "ethnicity": ["race", "ethnicity", "ethnic origin", "ethnic background", "ethnic group", "racial origin", "racial background",
                  "race or ethnicity", "race and ethnicity", "race ethnicity", "ethnicity race", "ethnicity or race",
                  "racial or ethnic origin", "racial or ethnic background", "ethnic or racial origin", "ethnic or racial background",
                  "racial and ethnic origin", "etnia", "raza", "origen racial", "origen etnico", "origen racial o etnico",
                  "origen etnico o racial", "origen racial y etnico", "raza o etnia", "raza y etnia", "etnia o raza", "grupo etnico",
                  "pertenencia etnica", "ethnicity or ethnic origin"],
    "religion": ["religion", "religion or belief", "religion or beliefs", "religion or faith", "religion belief", "faith", "belief",
                 "beliefs", "religion y creencias", "religion o creencias", "creencias", "creencias religiosas", "religion o creencia"],
    "disability": ["disability", "disabilities", "disabled", "discapacidad", "discapacidades", "disability status", "estado de discapacidad",
                   "condicion de discapacidad", "discapacitado", "discapacitada"],
    "veteran": ["veteran", "veteran status", "protected veteran", "protected veteran status", "military veteran", "veterano",
                "condicion de veterano", "estado de veterano", "military status"],
    "date_of_birth": ["date of birth", "birth date", "dob", "birthdate", "fecha de nacimiento"],
    "age": ["age", "edad"],
    "sexual_orientation": ["sexual orientation", "orientacion sexual", "sexual identity", "identidad sexual", "sexuality", "sexual preference"],
    "mental_health": ["mental health", "mental health condition", "mental health conditions", "mental health status", "salud mental",
                      "condicion de salud mental", "problema de salud mental"],
    "neurodivergence": ["neurodivergent", "neurodiverse", "neurodivergence", "neurodiversity", "neurodiverso", "neurodivergente",
                        "neurodiversidad"],
}
DM_ALL = sorted({phrase for phrases in DM_ATTRIBUTES.values() for phrase in phrases}, key=lambda p: (-len(p.split()), -len(p)))
Dm = Grammar("demographic")
DM_ATTR = Dm.A(*DM_ALL)
DM_YOUR = Dm.A("your", "my", "the", "tu", "su", "mi", "tus", "sus")
DM_BE = Dm.A("is", "are", "es", "son")
DM_WH = Dm.A("what", "which", "cual", "cuales", "que")
DM_VERBS = Dm.A("use", "go by", "prefer", "usas", "utilizas", "prefieres", "identify as", "identify with", "identify yourself as",
                "self identify as", "te identificas", "perteneces", "belong to")
DM_ADV = Dm.A("currently", "actually", "personally", "actualmente")
DM_PERSON = Dm.A("person with", "persona con", "someone with", "person living with", "persona que vive con", "living with")
DM_HAVE = Dm.A("have", "has", "hold", "tienes", "tiene", "padeces", "presentas", "posees")
DM_ANY = Dm.A("a", "an", "any", "one", "alguna", "algun", "una", "un", "algun tipo de", "alguna clase de", "ninguna")
DM_SELF = Dm.A("consider yourself", "consider yourself to have", "consider yourself to be", "consider that you have", "identify as",
               "identify as having", "identify as being", "self identify as", "self identify as having", "te consideras",
               "te identificas como", "consideras que tienes")
Dm.T("wh_is", DM_WH, DM_BE, opt(DM_YOUR), DM_ATTR)
Dm.T("wh_attr_verb", DM_WH, DM_ATTR, "do you", DM_VERBS)
Dm.T("wh_attr_verb_es", DM_WH, DM_ATTR, DM_VERBS)
Dm.T("with_attr_es", "con", DM_WH, DM_ATTR, DM_VERBS)
Dm.T("to_attr_es", "a", DM_WH, DM_ATTR, DM_VERBS)
Dm.T("select_attr", Dm.A("select", "enter", "state", "provide", "indicate", "specify", "give", "selecciona", "indica", "introduce"),
     opt(DM_YOUR), DM_ATTR)
Dm.T("have_disability", Dm.A("do", "did"), "you", star(DM_ADV), "have", opt(DM_ANY), DM_ATTR)
Dm.T("have_es", star(DM_ADV), DM_HAVE, opt(DM_ANY), DM_ATTR)
Dm.T("are_disabled", Dm.A("are", "is"), "you", star(DM_ADV), opt(Dm.A("a", "an")), opt(DM_PERSON), opt(DM_ANY), DM_ATTR)
Dm.T("consider_self", Dm.A("do", "would"), "you", DM_SELF, opt(DM_ANY), opt(DM_PERSON), opt(DM_ANY), DM_ATTR)
Dm.T("consider_es", DM_SELF, opt(DM_ANY), opt(DM_PERSON), opt(DM_ANY), DM_ATTR)
Dm.T("be_es", Dm.A("eres", "estas"), opt(DM_ANY), opt(DM_PERSON), opt(DM_ANY), DM_ATTR)
Dm.T("veteran_are", "are you", opt("a"), opt("protected"), "veteran")
Dm.T("describe_attr", Dm.A("describe", "define", "describe yourself", "describa", "describe tu", "describe su"), opt(DM_YOUR), DM_ATTR)
Dm.T("how_describe", "how", Dm.A("do", "would", "can", "could"), "you", Dm.A("describe", "identify", "define", "classify"), opt(DM_YOUR), DM_ATTR)
Dm.T("how_describe_es", Dm.A("como", "de que forma"), Dm.A("describirias", "describes", "te describes", "te identificas", "definirias", "te definirias"),
     opt(DM_YOUR), opt(DM_ATTR))
Dm.T("when_born", Dm.A("when were you born", "cuando naciste", "when is your birthday", "cuando es tu cumpleanos"))
Dm.T("how_old", Dm.A("how old are you", "cuantos anos tienes", "que edad tienes"))
DEMOGRAPHIC_LABELS = Labels(
    heads=[*DM_ALL],
    mods=["your", "my", "the", "tu", "su", "mi", "tus", "sus", "current", "legal", "self identified", "self identification", "self identify",
          "voluntary", "voluntaria", "voluntario", "self", "identity", "identification", "of", "de", "optional", "required", "status", "preferred",
          "personal", "personales", "preferidos", "preferidas", "preferido", "preferida", "and", "or", "y", "o", "a", "an", "what",
          "which", "is", "are", "cual", "es", "son", "que", "enter", "select", "please", "provide", "state", "indicate"])
DEMOGRAPHIC_BRACKETS = Dm.vocab | DEMOGRAPHIC_LABELS.vocab | {"e", "g"}

# ------------------------------------------------------------------------------------------------ identity, nationality, contact
IDENTITY_LABELS = Labels(
    heads=["name", "names", "nombre", "apellido", "apellidos", "surname", "date of birth", "birth date", "dob", "age", "edad",
           "fecha de nacimiento", "nacimiento", "birth place", "place of birth", "country of birth", "city of birth", "birthplace",
           "town of birth", "birth country", "birth city", "birth town", "pais de nacimiento", "lugar de nacimiento", "born",
           "firma", "signature"],
    mods=["first", "last", "full", "given", "family", "legal", "preferred", "chosen", "candidate", "applicant", "complete", "official",
          "registered", "contact", "your", "my", "the", "of", "and", "y", "de", "pila", "completo", "tu", "su", "el", "como", "te", "gusta",
          "que", "llamen", "conocido", "known", "as", "go", "by", "what", "is", "are", "how", "do", "you", "would", "like", "to", "be",
          "called", "middle", "maiden", "nick", "nickname", "former", "previous", "prior", "other", "birth", "alias", "aka", "segundo",
          "primer", "soltera", "anterior", "otros", "date", "born", "place", "country", "city", "town", "where", "were", "was", "i", "in",
          "cual", "es", "mi", "que", "a", "an", "state", "province", "region", "nombre legal", "oficial", "real", "obligatorio",
          "optional", "required", "please", "enter", "type", "print", "write", "sign", "signature", "firma", "electronic", "electronica",
          "initials", "iniciales", "typed", "digital", "name at birth", "at", "when", "pais", "ciudad", "lugar", "primero", "primera",
          "own", "personal", "age", "years", "old", "hasta", "mayor", "edad"])
# a typed signature: its own words, the name that is typed, and how (and where) it is written; nothing else may stand beside it
SIGNATURE_LABELS = Labels(
    heads=["signature", "sign", "signed", "signing", "firma", "firmar", "firmado", "firmante", "signatory", "initials", "iniciales",
           "rubrica", "name", "nombre", "apellidos", "names", "e signature", "esignature", "declaration of truth"],
    mods=["electronic", "electronica", "electronico", "electronically", "digitally", "electronicamente", "e", "typed", "digital", "type", "print", "printed", "enter", "write", "escribe",
          "teclea", "introduce", "full", "legal", "complete", "completo", "your", "my", "the", "tu", "su", "mi", "of", "de", "and", "y",
          "to", "para", "here", "below", "above", "aqui", "abajo", "debajo", "this", "these", "esta", "este", "declaration", "declaracion",
          "form", "application", "terms", "statement", "please", "kindly", "optional", "required", "block", "capitals", "letters",
          "capital", "uppercase", "mayusculas", "letra", "letras", "imprenta", "en", "in", "as", "name as", "signature of", "i", "confirm",
          "certify", "accept", "agree", "acknowledge", "attest", "sign this", "as it appears", "official", "applicant", "candidate",
          "first", "last", "given", "family", "surname", "yo", "solicitante", "candidato", "confirmar", "certificar", "aceptar", "acepto",
          "declaro", "confirmo", "certifico", "firma electronica", "con", "a", "an", "un", "una", "e sign", "esign", "nombre y apellidos"])
NATIONALITY_LABELS = Labels(
    heads=["nationality", "citizenship", "nacionalidad", "ciudadania", "country of citizenship", "citizen", "ciudadano", "ciudadana"],
    mods=["your", "my", "the", "what", "is", "are", "of", "country", "current", "candidate", "national", "select", "de", "tu", "cual",
          "es", "zzplace", "zzlang", "optional", "required", "please", "enter", "state", "primary", "dual", "second", "first", "other",
          "or", "and", "y", "o", "a", "an", "i", "am", "mi", "su", "actual", "what", "which", "do", "you", "hold", "have"])
CONTACT_LABELS = Labels(
    heads=["email", "e mail", "mail", "phone", "telephone", "mobile", "cell", "correo", "telefono", "movil", "celular", "phone number",
           "email address", "correo electronico", "numero de telefono", "mobile number", "mobile phone", "cell phone", "contact number",
           "contact email", "contact telephone"],
    mods=["address", "number", "your", "my", "the", "personal", "preferred", "contact", "primary", "main", "electronico", "de", "direccion",
          "numero", "tu", "su", "mi", "what", "is", "are", "please", "enter", "provide", "state", "confirm", "optional", "required",
          "and", "y", "or", "o", "a", "an", "of", "for", "us", "can", "reach", "you", "at", "best", "to", "contact", "where", "movil",
          "tel", "no", "num", "nr", "digits", "only", "numbers", "format", "international", "national", "with", "country", "code",
          "without", "plus", "zznum", "internacional", "nacional", "phone", "email", "mobile", "telephone", "cell", "mail"])
LINK_LABELS = Labels(
    heads=["linkedin", "github", "portfolio", "website", "web site", "homepage", "home page", "personal website", "personal site",
           "portafolio", "sitio web", "pagina web", "web personal", "sitio personal", "perfil de linkedin", "perfil de github",
           "git hub", "linked in", "gitlab", "url", "link", "enlace"],
    mods=["profile", "profiles", "url", "urls", "link", "links", "page", "account", "your", "my", "the", "personal", "professional", "public",
          "online", "tu", "su", "mi", "de", "del", "perfil", "enlace", "address", "enter", "please", "optional", "required", "to", "of", "what",
          "is", "are", "and", "or", "y", "o", "a", "an", "provide", "paste", "add", "cual", "es", "handle", "username", "work", "samples",
          "web", "site", "direccion", "pagina", "sitio", "de tu", "https", "www", "http"])
ADDRESS_LABELS = Labels(
    heads=["address", "postcode", "postal code", "zip code", "zip", "address line", "street address", "domicilio", "direccion",
           "codigo postal", "post code", "street", "calle", "cp"],
    mods=["line", "1", "2", "3", "street", "home", "current", "residential", "your", "the", "my", "mailing", "postal", "de", "calle",
          "linea", "completa", "principal", "zznum", "tu", "su", "mi", "what", "is", "are", "please", "enter", "optional", "required",
          "first", "second", "address", "of", "residencia", "casa", "vivienda", "actual", "particular", "billing", "permanent",
          "primary", "main", "post", "code", "zip", "number", "numero", "piso", "planta", "ciudad", "city", "town", "county", "state",
          "country", "pais", "provincia", "region", "zzplace", "full", "complete"])
# how a phone number or an email may be asked to be written: only in brackets or in a hint sentence beside the label
FORMAT_HINT_WORDS = {"digits", "digit", "only", "numbers", "number", "numeric", "plus", "spaces", "space", "symbols", "symbol", "no",
                     "or", "solo", "numeros", "digitos", "simbolos", "sin", "caracteres", "especiales", "special", "characters",
                     "national", "international", "format", "formato", "nacional", "internacional", "without", "the", "country", "code",
                     "prefix", "prefijo", "a", "e", "g", "dial", "dialling", "dialing", "e164", "spaces", "dashes", "hyphens", "guiones",
                     "espacios", "with", "include", "including", "zznum", "plain", "characters"}
GRAMMARS = {"work_right": (W, WORK_LABELS, WORK_BRACKETS), "sponsorship": (S, SPONSOR_LABELS, SPONSOR_BRACKETS),
            "visa": (V, VISA_LABELS, VISA_BRACKETS), "language": (Lg, LANGUAGE_LABELS, LANGUAGE_BRACKETS),
            "education": (E, EDU_LABELS, EDUCATION_BRACKETS), "notice": (N, NOTICE_LABELS, NOTICE_BRACKETS),
            "availability": (AV, AVAIL_LABELS, AVAIL_BRACKETS), "location": (Lc, LOCATION_LABELS, LOCATION_BRACKETS),
            "relocation": (Rl, RELOCATION_LABELS, RELOCATION_BRACKETS), "salary": (None, SALARY_LABELS, SALARY_BRACKETS),
            "identity": (None, IDENTITY_LABELS, IDENTITY_LABELS.vocab), "nationality": (None, NATIONALITY_LABELS, NATIONALITY_LABELS.vocab),
            "demographic": (Dm, DEMOGRAPHIC_LABELS, DEMOGRAPHIC_BRACKETS), "link": (None, LINK_LABELS, LINK_LABELS.vocab),
            "contact": (None, CONTACT_LABELS, CONTACT_LABELS.vocab), "address": (None, ADDRESS_LABELS, ADDRESS_LABELS.vocab)}


# ------------------------------------------------------------------------------------------------ the reading
STARTERS = {"do", "does", "did", "are", "is", "am", "was", "were", "have", "has", "had", "will", "would", "can", "could", "shall",
            "should", "may", "might", "must", "i", "we", "no", "yes", "if", "tienes", "tiene", "eres", "es", "estas", "esta",
            "puedes", "puede", "podrias", "podria", "necesitas", "necesita", "requieres", "requiere", "hay", "hablas", "habla",
            "resides", "vives", "dispones", "posees", "cuentas", "estaria", "estarias", "serias", "quieres", "sabes", "estoy",
            "tengo", "soy", "necesito", "puedo", "requiero", "podras", "tendras", "estaras", "seras", "ha", "haz", "precisas",
            "has", "te", "ya", "hemos", "he", "sera"}
RISKY = {"not", "no", "never", "without", "nor", "neither", "cannot", "nunca", "jamas", "sin", "ni", "tampoco", "or", "and", "but", "y",
         "o", "except", "unless", "excluding", "including", "apart", "other", "than", "more", "less", "least", "most", "over", "under",
         "every", "all", "any", "only", "before", "after", "until", "since", "during", "within", "outside", "away", "if", "when",
         "while", "whether", "although", "though", "however", "salvo", "excepto", "pero", "antes", "despues", "hasta", "desde",
         "durante", "fuera", "solo", "ever", "once", "twice", "again", "each", "both", "either", "same", "also", "plus", "per",
         "than", "between", "beyond", "around", "about", "approximately", "roughly"}
NARRATIVE = re.compile(
    r"(?:please )?(?:describe|tell (?:us|me)(?: about| more| how| why| what)|explain|walk (?:us|me) through|talk (?:us )?(?:through|about)|"
    r"share|give (?:us )?(?:an? )?(?:example|overview|summary|brief)|outline|elaborate|summari[sz]e|discuss|"
    r"how (?:do|did|would|have|can|will|does|has) (?:you|your|this|the)|what (?:did you|have you|was your|were your|is your "
    r"(?:experience|approach|role|biggest|greatest|proudest)|are your (?:strengths|goals|biggest)|motivat|attract|excit|interest)|"
    r"why|in what way|cuentanos|cuentame|describe|explica|como (?:has|haces|harias|lo)|que (?:aprendiste|has aprendido)|por que|"
    r"hablanos|comparte)\b")
def long_text(field) -> bool:
    return str(field.get("tag") or "").lower() == "textarea" or str(field.get("type") or "").lower() in {"textarea", "longtext"}


def choice_control(field) -> bool:
    return bool(field.get("options")) or str(field.get("role") or "") in {"radio", "checkbox", "switch"} \
        or str(field.get("type") or "") in {"radio", "checkbox"}


def is_narrative(text, field=None) -> bool:
    """Is this a prompt for a written answer ('Describe...', 'Tell us about...', 'How do you...'), or a long-text field?"""
    return bool(NARRATIVE.match(normalize(str(text or "")[:300]))) or bool(field and long_text(field))


SUBJ_STOP = {"with", "without", "from", "at", "by", "that", "which", "who", "whom", "where", "when", "if", "not", "no", "and", "y", "but",
             "than", "before", "after", "since", "until", "during", "for", "to", "as", "on", "of", "per", "via", "into", "over", "under",
             "within", "between", "among", "because", "so", "then", "in", "en", "con", "sin", "por", "para", "que", "del", "al", "se",
             "is", "are", "was", "were", "do", "does", "did", "have", "has", "had", "will", "would", "can", "could", "you", "your", "my",
             "i", "we", "it", "this", "these", "those", "any", "all", "every", "each", "also", "too", "yes", "ever", "once", "already",
             SUBJ, INST, PLACE, LANG, DATE, QTY, NUM}
_SW = r"(?!(?:" + "|".join(sorted(SUBJ_STOP)) + r")(?: |$))[a-z][a-z0-9+#.-]*"
SUBJECT_LEAD = (r"degrees?|bachelors?(?: s)?|masters?(?: s)?|msc|bsc|ba|ma|beng|meng|phd|doctorate|diploma|qualifications?|major(?:ing)?|"
                r"licenciatura|grado|maestria|doctorado|titulacion|titulos?|posgrado|postgrado|carrera|certificado|graduate|graduated|graduating")
SUBJECT_SLOT = re.compile(r"(?<![a-z0-9])(?P<lead>(?:" + SUBJECT_LEAD + r")(?: degrees?| qualifications?)?) (?P<prep>in|of|on|en|de|sobre) (?P<subj>(?:a |an |un |una )?"
                          + _SW + r"(?: (?:or |o )?" + _SW + r"){0,5})(?= |$)")
SUBJECT_WITH = re.compile(r"(?<![a-z0-9])(?P<lead>with|having) (?P<det>a |an )?(?P<subj>" + _SW + r"(?: (?:or |and )?" + _SW + r"){0,4})"
                          r"(?= (?:degree|qualification|diploma)(?: |$))")
RELATED = re.compile(r"(?: (?:or|o) (?:a |an |un |una )?(?:related|relevant|similar|equivalent|relacionad\w+|equivalente|comparable)"
                     r"(?: (?:field|subject|discipline|area|degree|qualification|campo|disciplina|titulacion))?)")


NOT_INSTITUTIONS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
                    "december", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                    "noviembre", "diciembre", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "summer",
                    "winter", "spring", "autumn", "fall", "easter", "christmas", "the", "end", "start", "beginning", "q1", "q2", "q3", "q4",
                    "h1", "h2", "mid", "early", "late", "verano", "invierno", "primavera", "otono"}


def education_slots(core: str, slots: dict) -> tuple[str, dict]:
    """The institutions (read in the raw case of the text) and the subjects of a question about studies, written as slots and
    recorded. Order matters: institutions (their names contain places), then fold, dates, subjects, then the other slots."""
    institutions: list[str] = []

    def institution(match):
        institutions.append(match.group(0))
        return " " + INST + " "

    raw = collapse(core)[:MAX_CHARS]
    if raw.isupper() or raw.islower():
        # a text with no capitals to tell a NAME from a word ('ARE YOU STUDYING AT SOUTH UNIVERSITY?'): only a name right after a
        # preposition and ending in a word for a school is read as one
        raw = re.sub(r"(?i)\b(?:at|from|by|in|en|de la|de|por)\s+(?:the\s+|la\s+|el\s+)?(?:[a-z][\w&'’-]*\s+){0,3}"
                     r"(?:university|college|institute|polytechnic|school|universidad|instituto|escuela|facultad)"
                     r"(?:\s+(?:of|de|del|de la|in|for)\s+[a-z][\w'’-]*(?:\s+[a-z][\w'’-]*){0,2})?", institution, raw)
    else:
        raw = re.sub(r"(?:\b(?:at|from|by|in|en|de la|de|por)\s+(?:the\s+|la\s+|el\s+)?)?" + academic.INSTITUTION_WORD.pattern, institution, raw)
    if not institutions and re.search(r"(?i)\b(?:studying|student|enrolled|studied|estudias|estudiando|graduated|graduate|attend\w*)\b", raw):
        def after(match):
            words = match.group(1).split()
            if words and words[0] not in academic.INSTITUTION_STOP and words[0].lower() not in NOT_INSTITUTIONS \
                    and not jurisdiction.named_places(match.group(1))["countries"]:
                institutions.append(match.group(1))
                return " " + INST + " "
            return match.group(0)
        raw = re.sub(r"\b(?:at|from|en|de la)\s+(?:the\s+|la\s+|el\s+)?((?:[A-Z][\w&'’-]*)(?:\s+[A-Z][\w&'’-]*){0,3})", after, raw)
    folded = jurisdiction.mask_folded(jurisdiction_fold(raw, keep=INST))
    return folded, {"institutions": institutions}


def jurisdiction_fold(raw: str, keep: str = "") -> str:
    """Folded text (as ``jurisdiction.mask`` writes it) of a raw text, with 'US' and 'EU' written out first."""
    from ..extension import places
    return places.fold(jurisdiction.clean(raw))


def mask_education(core: str) -> tuple[str, dict]:
    folded, extra = education_slots(core, {})
    found: list = []
    folded = mask_dates(folded, found)
    subjects: list[str] = []

    def subject(match):
        text = match.group("subj")
        subjects.append(re.sub(r"^(?:a |an |un |una )", "", text).strip())
        return match.group("lead") + " " + match.group("prep") + " " + SUBJ

    folded = SUBJECT_SLOT.sub(subject, folded)
    folded = RELATED.sub(" ", folded)

    def subject_with(match):
        subjects.append(match.group("subj"))
        return match.group("lead") + " " + (match.group("det") or "") + SUBJ

    folded = SUBJECT_WITH.sub(subject_with, folded)
    masked, slots = mask_slots(core, folded=jurisdiction.mask_folded(folded))
    slots["dates"] = found + slots["dates"]
    slots.update(extra, subjects=subjects)
    return masked, slots


def take_places(text: str, slots: dict, loc: bool = False) -> str:
    """The text without its places (and the preposition before each): the engine reads WHICH place from the question itself."""
    if loc:
        text = re.sub(r"(?<![a-z0-9])(?:resident|residents|citizen|citizens|native|national) of(?= (?:the )?" + PLACE + ")", lambda m: m.group(0)[:-3].strip(), text)
    found: list = []
    text = take(text, PLACE_PHRASE, found, "place")
    slots["place_phrases"] = len(found)
    return text


_FRAME_VERB = r"(?:confirm|state|indicate|advise|let us know|certify|specify)"
FRAME = re.compile(r"^(?:(?:please|kindly) )*(?:(?:do|can|could|will|would) you (?=(?:(?:please|kindly) )*" + _FRAME_VERB + r"\b))?"
                   r"(?:(?:please|kindly) )*(?:" + _FRAME_VERB + r" (?:(?:that|whether|if) )?)?")
TRAIL = re.compile(r"(?: (?:please|kindly|por favor))+$")


RANGE_FIGURES = re.compile(r"\b(?:between|entre) [£$€]?\d[\d,.]*k? (?:and|y|to|a|-) [£$€]?\d[\d,.]*k?")


def salary_qualifiers(core: str) -> tuple[str, list[str]]:
    """The words of a salary label that say WHICH figure is asked (a currency, a period, a unit, a range, a package, a net or pro rata
    basis, an explanation) are taken out and recorded: ``salary.mismatch`` is what knows each of them and answers 'another currency'
    ... itself. What is left must be the salary's own words."""
    from . import salary
    text = salary.plain(core)
    found: list[str] = []
    # 'between 50000 and 60000': the range as a whole (a number anywhere else in the label is a word nothing accounts for)
    text = RANGE_FIGURES.sub(lambda m: (found.append(m.group(0)) or " range "), text)
    patterns = [*salary.CURRENCIES.values(), *salary.PERIODS.values(), salary.UNIT, salary.SHAPE, salary.NET, salary.EXPLAIN, salary.BASIS]
    # 'total compensation' and 'paquete salarial' are the package AND a name of the salary: a head noun stays
    text = salary.PACKAGE.sub(lambda m: (found.append(m.group(0)) or " salary "), text)
    for pattern in patterns:
        compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        text = compiled.sub(lambda m: (found.append(m.group(0)) or " "), text)
    return text, found


URL_TEXT = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?:/\S*)?|https?://\S*|\bwww\b")
CONTACT_FORMAT = words_re(
    r"(?:excluding|without|not including|do not include|don t include|omit|omitting|leave out|no|sin|sin incluir|excluyendo) (?:the |a |any )?"
    r"(?:country|international|dial(?:l?ing)?|area|pais|internacional) (?:code|prefix|prefijo|codigo)",
    r"(?:including|with|incluyendo|con) (?:the )?(?:country|international|pais|internacional) (?:code|prefix|prefijo|codigo)",
    r"(?:national|international|local|nacional|internacional) (?:format|formato)", r"formato (?:nacional|internacional)",
    r"digits only|only digits|numbers? only|only numbers?|numeric only|solo (?:numeros|digitos)|no (?:or )?(?:symbols|special characters|"
    r"plus|spaces|dashes|hyphens|brackets)|no plus or spaces|no or spaces|without (?:symbols|spaces|special characters)|"
    r"sin (?:simbolos|caracteres especiales|espacios|guiones)|e 164|con prefijo|sin prefijo")


def contact_qualifiers(core: str) -> tuple[str, list[str]]:
    """How a phone number may be asked to be WRITTEN ('digits only', 'without the country code', 'national format') is a format the
    engine reads itself (``DIGITS_ONLY``, ``NATIONAL_NUMBER``); the label that is left must be the contact's own words."""
    found: list[str] = []
    return take(normalize(core), CONTACT_FORMAT, found), found


def prepare_core(family: str, core: str) -> tuple[str, dict]:
    """The one clause of a question -> (its text with every slot a token or taken out, the slots)."""
    qualifiers: list[str] = []
    if family == "salary":
        core, qualifiers = salary_qualifiers(core)
    elif family == "contact":
        core, qualifiers = contact_qualifiers(core)
    if family == "education":
        masked, slots = mask_education(core)
    else:
        masked, slots = mask_slots(core)
    slots["qualifiers"] = qualifiers
    slots.setdefault("facets", [])
    slots.setdefault("timeframe", "")
    slots.setdefault("threshold", None)
    text = FRAME.sub("", masked)
    text = TRAIL.sub("", text).strip()
    if family in LEGAL:
        text = take_places(text, slots)
        if family == "work_right":
            for name in FACET_ORDER:
                text = take(text, FACET_PHRASES[name], slots["facets"], name)
            text = text.strip()
        elif family == "sponsorship":
            found: list = []
            text = take(text, TIMEFRAME, found)
            now = any(re.search(NOW_WORDS, f) for f in found)
            future = any(re.search(FUTURE_WORDS, f) for f in found)
            slots["timeframe"] = "now_or_future" if now and future else "current" if now else "future" if future else "unspecified"
            text = take(text, PURPOSE)
    elif family in {"location", "relocation", "nationality", "salary"}:
        text = take_places(text, slots, loc=family == "location")
    elif family == "notice":
        found_t: list = []

        def threshold(match):
            found_t.append((CMP_PREFIX.get(match.group("pre") or "") or CMP_SUFFIX.get(match.group("post") or "") or "=",
                            bool(match.group("pre")), bool(match.group("post"))))
            return " " + THRESHOLD + " "
        text = re.sub(r"\s+", " ", THRESHOLD_PATTERN.sub(threshold, text)).strip()
        if len(found_t) == 1 and len(slots["quantities"]) == 1 and not (found_t[0][1] and found_t[0][2]):
            slots["threshold"] = {"comparator": found_t[0][0], "value": slots["quantities"][0][0], "unit": slots["quantities"][0][1]}
        elif found_t or slots["quantities"]:
            slots["threshold"] = {"comparator": None}
    if family in {"salary", "address", "identity", "contact", "availability", "education", "nationality"}:
        text = mask_numbers(text, slots)
    return text, slots


def tokens_left(text: str) -> list[str]:
    return [w for w in text.split() if w not in {"please", "kindly"}]


def template_words(pattern: re.Pattern) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", pattern.pattern))


def restore(tokens: list[str], slots: dict) -> list[str]:
    """The words of the text a slot token stood for, so that the user reads 'the first half', not 'zzsubj'."""
    supply = {SUBJ: list(slots.get("subjects") or []), INST: list(slots.get("institutions") or []), DATE: list(slots.get("dates") or []),
              LANG: list(slots.get("languages") or []), QTY: [f"{v:g} {u}" for v, u in slots.get("quantities") or []],
              NUM: list(slots.get("numbers") or []), PLACE: ["a place"] * 8}
    out = []
    for token in tokens:
        if token in supply:
            out.extend(str(supply[token].pop(0)).split() if supply[token] else [token])
        else:
            out.append(token)
    return out


def residue_of(text: str, grammar, labels, slots: dict | None = None) -> list[str]:
    """The words of a clause that no form of the family accounts for: the words outside the vocabulary of the family; else, of the
    template it comes closest to, the words that template does not have; else the risky words (a negator, a comparator, a
    coordination...) it contains."""
    slots = slots or {}
    tokens = text.split()
    vocab = (grammar.vocab if grammar else set()) | labels.vocab | SLOT_TOKENS | set(ASK_WORDS)
    unknown = [w for w in tokens if w not in vocab]
    if unknown:
        return list(dict.fromkeys(restore(unknown, slots)))
    if grammar and grammar.templates:
        closest = max((template_words(pattern) | set(ASK_WORDS) for _, pattern in grammar.templates), key=lambda words: sum(w in words for w in tokens))
        outside = [w for w in tokens if w not in closest]
        if outside:
            return list(dict.fromkeys(restore(outside, slots)))
    risky = [w for w in tokens if w in RISKY]
    return list(dict.fromkeys(restore(risky, slots))) or ["the order of its words"]


def demographic_attributes(text: str) -> list[str]:
    """The personal attributes ('gender', 'pronouns', 'disability'...) a clause names: one answer is one attribute."""
    padded = " " + text + " "
    return [name for name, phrases in DM_ATTRIBUTES.items() if any(" " + phrase + " " in padded for phrase in phrases)]


def explain(family: str, residue: list[str], reason: str = "") -> str:
    words = ", ".join(f"'{w}'" for w in residue[:6])
    base = {"work_right": "a plain question about your right to work", "sponsorship": "a plain question about whether you need sponsorship",
            "visa": "a plain question about your visa", "language": "a plain question about a language you speak",
            "education": "a plain question about your studies", "notice": "a plain question about your notice period",
            "availability": "a plain question about when you can start", "location": "a plain question about where you are",
            "relocation": "a plain question about relocating", "salary": "a plain question about your salary expectation",
            "identity": "a plain request for one of your names or dates", "nationality": "a plain question about your nationality",
            "contact": "a plain request for your email or phone", "address": "a plain request for your address",
            "demographic": "a plain question about one personal attribute you have confirmed", "link": "a plain request for one of your links"}.get(family, "a plain question")
    why = reason or ("it contains " + words + ", which Meridian cannot account for" if residue else "its wording is not a plain one")
    return f"Meridian answers {base} from your confirmed facts; this one is not plain: {why}. Only you can answer it."


@lru_cache(maxsize=1 << 14)
def _read(question: str, intent: str, textarea: bool, choice: bool, signature: bool) -> tuple:
    family = FAMILY_OF[intent]
    grammar, labels, brackets = GRAMMARS[family]
    out = {"ok": False, "gated": True, "family": family, "form": "", "template": "", "residue": [], "reason": "", "slots": {},
           "narrative": False}

    def refuse(words, reason=""):
        out.update(ok=False, residue=list(dict.fromkeys(words))[:8], reason=explain(family, list(dict.fromkeys(words)), reason))
        return out

    text = collapse_lines(question)
    if not normalize(text):
        return freeze(refuse([], "there is no question to read"))
    out["narrative"] = is_narrative(text)
    if len(text) > MAX_CHARS or len(text.split()) > MAX_WORDS:
        return freeze(refuse(["a very long text"], "it is too long to be a plain question"))
    # (the answer to a visa status or a US GPA is a SENTENCE the engine composes; an ADDRESS is a multi-line text by nature, and a textarea is
    # the ordinary control for 'Address' / 'Home address': what makes a prompt a narrative is its wording, which the grammar reads)
    if textarea and family not in {"visa", "address"} and intent != "GPA":
        return freeze(refuse(["long-text field"], "a long-text field asks for a written answer, not one line of a fact"))
    if signature and family == "identity":
        # a typed signature is the person's own name: a label of the signature's own words ('Signature (type your full name)', 'Firma
        # (nombre completo en letra de imprenta)', 'Print name'), never one that asks for more ('... or your address')
        grammar, labels, brackets = None, SIGNATURE_LABELS, SIGNATURE_LABELS.vocab
        text = without_attestation(text)
    st = structure(text, brackets | set(ASK_WORDS), choice, strip={"salary": lambda c: salary_qualifiers(c)[0], "contact": lambda c: contact_qualifiers(c)[0],
                                                                    "link": lambda c: URL_TEXT.sub(" ", c)}.get(family))
    out["slots"] = {"prefix": st["prefix"], "follow_up": st["follow_up"], "hints": len(st["hints"])}
    if st["residue"]:
        return freeze(refuse(st["residue"], st["reason"]))
    segments = st["segments"]
    if not segments:
        return freeze(refuse([], "there is no question to read"))
    if len(segments) > 1:
        # one sentence asks and the rest is something else (an employer's note, a second question): which one asks is the one the
        # family reads as a plain question; every other word is unconsumed, and the question is not answered either way
        asking = next((i for i, part in enumerate(segments) if _try_core(family, grammar, labels, part)[0]), 0)
        rest = [w for i, part in enumerate(segments) if i != asking for w in normalize(part).split()]
        return freeze(refuse(rest[:8], "it is more than one sentence or clause (" + ", ".join(f"'{w}'" for w in rest[:6]) + ")"))
    template, form, core_text, slots = _try_core(family, grammar, labels, segments[0])
    if template is None and MARKER_EDGE.search(segments[0]):
        # 'Start date Required' (a page that joins the label and its marker with a space): the marker is no part of what is asked, but a
        # label that IS about being required ('Sponsorship required') is read as it stands first
        template, form, core_text, slots = _try_core(family, grammar, labels, MARKER_EDGE.sub(" ", segments[0]).strip())
    slots.update(prefix=st["prefix"], follow_up=st["follow_up"], hints=len(st["hints"]))
    out["slots"] = slots
    if template is None:
        return freeze(refuse(residue_of(core_text, grammar, labels, slots)) if core_text.split() else refuse([], "there is no question to read"))
    if family == "demographic":
        attributes = demographic_attributes(core_text)
        slots["attributes"] = attributes
        if len(attributes) > 1:
            return freeze(refuse(attributes[1:], "it asks about more than one personal attribute (" + ", ".join(attributes) + ")"))
    out.update(ok=True, template=template, form=form)
    return freeze(out)


MARKER_EDGE = re.compile(r"(?i)^\s*(?:required|optional|mandatory|obligatorio|opcional|requerido)\s*[:\-\u2013]?\s+|\s+[:\-\u2013]?\s*(?:required|optional|mandatory|"
                         r"obligatorio|opcional|requerido)\s*$")


def _try_core(family: str, grammar, labels, core: str) -> tuple:
    """One clause -> (the template or 'label' it is, its form, its text with the slots taken out, the slots); template None when it
    is no plain form of the family."""
    core_text, slots = prepare_core(family, core)
    tokens = core_text.split()
    template = grammar.match(core_text) if grammar and tokens else None
    if template is None and family == "work_right" and slots["facets"] and not tokens_left(core_text):
        template = "facet_only"  # 'without any visa sponsorship': a proposition that is nothing but a modifier of the right
    form = "question" if template else ""
    if template is None and tokens and tokens[0] not in STARTERS and labels.match(core_text):
        template, form = "label", "label"
    if template and family == "work_right" and (template in {"have_will", "be_will"} or re.search(r"\b(?:tendras|estaras|seras|podras)\b", core_text)):
        slots["facets"].append("future")
    return template, form, core_text, slots


def freeze(value: dict) -> tuple:
    import copy
    return (copy.deepcopy(value),)


def read(question, field=None, intent=None, signature: bool = False) -> dict:
    """The reading of a question for the family of ``intent``: ``ok`` when it is a PLAIN question of that family, else the
    ``residue`` (the words nothing accounted for) and the ``reason``. ``{'gated': False, 'ok': True}`` for an intent that is no
    fact family. Never raises; a fresh dict every call."""
    family = FAMILY_OF.get(intent or "")
    if family is None:
        return dict(NOT_GATED, slots={})
    field = field or {}
    import copy
    return copy.deepcopy(_read(str(question or ""), intent, long_text(field), choice_control(field), bool(signature))[0])
