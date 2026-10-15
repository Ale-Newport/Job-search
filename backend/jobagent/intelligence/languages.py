"""Languages the candidate speaks, and how well: what a question asks and what a stored level establishes.

'Are you a native English speaker?', 'Is English your first language?' and 'Do you speak English at C2 level?' were answered
'Yes' for a Spanish native whose English is 'Fluent': any question that names a language the candidate has, at any level,
got a yes. A level is an order, not a presence. The ladder below is explicit; a question is compared with the stored level
only where the comparison is certain, and otherwise it is left to the user:

* the stored level reaches the asked one (Native for anything; Fluent for 'fluent', 'advanced', 'professional', 'C1') -> yes;
* the stored level is at least two steps below the asked one (Basic for 'fluent') -> no;
* an adjacent step ('Fluent' for 'C2') or a level the ladder does not know -> not answered;
* 'native', 'first language', 'mother tongue' and 'bilingual' need the stored fact to say so (an explicit native flag, or the
  level Native); an explicit ``native: false`` is a no; a 'Fluent' level alone is not evidence either way.

English and Spanish names of languages and levels are read; the languages themselves are English names ('Spanish') because
that is how the profile holds them.
"""

from __future__ import annotations

import re

from ..automation.answers import normalize

# Spanish (and a few more) names of languages -> the English name the profile holds.
SPANISH_NAMES = {
    "ingles": "english", "espanol": "spanish", "castellano": "spanish", "frances": "french", "aleman": "german",
    "portugues": "portuguese", "italiano": "italian", "neerlandes": "dutch", "holandes": "dutch", "arabe": "arabic",
    "chino": "chinese", "mandarin": "mandarin", "japones": "japanese", "coreano": "korean", "ruso": "russian",
    "hindi": "hindi", "bengali": "bengali", "polaco": "polish", "turco": "turkish", "ucraniano": "ukrainian",
    "catalan": "catalan", "gallego": "galician", "euskera": "basque", "vasco": "basque", "valenciano": "catalan",
}
# The ladder: a class of levels. Each entry is a set of words that, written in a level or in a question, belong to it.
CLASSES = {
    1: {"basic", "elementary", "elementary proficiency", "beginner", "a1", "a2", "notions", "basico", "elemental",
        "principiante", "nociones"},
    2: {"conversational", "intermediate", "b1", "working", "limited working", "limited working proficiency", "intermedio",
        "conversacional"},
    3: {"upper intermediate", "upper-intermediate", "b2", "intermedio alto", "intermedio avanzado"},
    4: {"fluent", "fluently", "advanced", "professional", "professional working", "professional working proficiency",
        "full professional", "full professional proficiency", "c1", "avanzado", "fluido", "fluida", "fluidez", "fluidamente",
        "fluentemente", "profesional"},
    5: {"c2", "mastery", "near native", "near-native", "maestria"},
    6: {"native", "bilingual", "mother tongue", "first language", "native or bilingual", "native or bilingual proficiency",
        "nativo", "nativa", "bilingue", "lengua materna", "idioma materno", "lengua nativa", "primera lengua", "native speaker"},
}
NATIVE_CLASS = 6
# A bare 'proficient' or 'proficiency' is no level ('are you proficient in English?'): only the phrases above are.
_PHRASES = sorted(((phrase, rank) for rank, words in CLASSES.items() for phrase in words), key=lambda item: -len(item[0]))
_PATTERN = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(normalize(phrase)) for phrase, _ in _PHRASES) + r")(?![a-z0-9])")
_RANK = {normalize(phrase): rank for phrase, rank in _PHRASES}


# The English names of the languages a form asks about (the names the profile holds them under).
ENGLISH_NAMES = {"english", "spanish", "french", "german", "portuguese", "italian", "dutch", "arabic", "mandarin", "chinese",
                 "japanese", "korean", "russian", "hindi", "bengali", "polish", "turkish", "ukrainian", "catalan", "galician",
                 "basque", "cantonese", "urdu", "punjabi", "swedish", "norwegian", "danish", "finnish", "greek", "hebrew",
                 "romanian", "czech", "hungarian", "vietnamese", "thai", "indonesian", "malay", "persian", "farsi", "swahili",
                 "welsh", "irish", "gaelic", "latin", "tagalog", "bulgarian", "croatian", "serbian", "slovak", "slovenian"}
MASK = "zzlang"


def canonical(name) -> str:
    """'ingles' / 'English' -> 'english'."""
    text = normalize(name)
    return SPANISH_NAMES.get(text, text)


_NAME_POOL = {**{value: value for value in SPANISH_NAMES.values()}, **SPANISH_NAMES, **{n: n for n in ENGLISH_NAMES}}
_NAME_PATTERN = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in sorted(_NAME_POOL, key=len, reverse=True)) + r")(?![a-z0-9])")


def mask(text: str) -> tuple[str, list[str]]:
    """A normalised text with every language it names written as ``zzlang`` -> (that text, the languages: English names, in order,
    repeats kept). 'Spanish' and 'espanol' are one slot of a grammar; ``mentioned`` says which language it was."""
    found = [_NAME_POOL[m.group(1)] for m in _NAME_PATTERN.finditer(text)]
    return _NAME_PATTERN.sub(" " + MASK + " ", text), found


def mentioned(text: str, names=None) -> list[str]:
    """The languages a normalised text names, as English names in lower case, without repeats, in order."""
    pool = {**{value: value for value in SPANISH_NAMES.values()}, **SPANISH_NAMES, **{n: n for n in (names or [])}}
    found = []
    for match in re.finditer(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in sorted(pool, key=len, reverse=True)) + r")(?![a-z0-9])", text):
        language = SPANISH_NAMES.get(match.group(1), match.group(1))
        if language not in found:
            found.append(language)
    return found


def classes_in(text) -> set[int]:
    """The ladder classes a text names ('C1 - Advanced' is class 4; 'Fluent or native' is 4 and 6)."""
    return {_RANK[m.group(1)] for m in _PATTERN.finditer(normalize(text))}


def level_class(text) -> int | None:
    """The one class a stored level or an option label names, else None (unknown, or several: not comparable)."""
    found = classes_in(text)
    return next(iter(found)) if len(found) == 1 else None


def asked_class(question: str) -> int | None:
    """The level a question asks for, from its own words: the HIGHEST level it names ('native or fluent' asks for fluent
    or better; a question that names two different levels is read by the stricter one)."""
    found = classes_in(question)
    return max(found) if found else None


def record_native(record_fields: dict) -> bool | None:
    """True / False when the stored language says (an explicit flag, or the level Native); None when it does not."""
    flag = record_fields.get("native")
    if flag is True or flag is False:
        return flag
    return True if level_class(record_fields.get("proficiency") or "") == NATIVE_CLASS else None


def compare(stored_level: str, native_flag, asked: int) -> tuple[bool | None, str]:
    """(answer, rule): does the stored level satisfy the asked class? None when it cannot be told."""
    stored = level_class(stored_level)
    if asked == NATIVE_CLASS:
        if native_flag is True or stored == NATIVE_CLASS:
            return True, "language_native_confirmed"
        if native_flag is False:
            return False, "language_native_denied_by_profile"
        return None, "language_native_not_stated"
    if stored is None and native_flag is True:
        stored = NATIVE_CLASS
    if stored is None:
        return None, "language_level_unknown"
    if stored >= asked:
        return True, "language_level_reaches_asked"
    if stored <= asked - 2:
        return False, "language_level_far_below_asked"
    return None, "language_level_adjacent_to_asked"


# the same level in the other language ('Fluent' is 'Fluido'): a translation of the stored word, not a guess on the ladder
TRANSLATIONS = {"basic": "basico", "elementary": "elemental", "beginner": "principiante", "intermediate": "intermedio",
                "conversational": "conversacional", "upper intermediate": "intermedio alto", "advanced": "avanzado", "fluent": "fluido",
                "professional": "profesional", "native": "nativo", "bilingual": "bilingue", "mother tongue": "lengua materna"}
_BOTH_WAYS = {**{normalize(k): normalize(v) for k, v in TRANSLATIONS.items()}, **{normalize(v): normalize(k) for k, v in TRANSLATIONS.items()}}


def translated_option(level: str, options: list[dict]) -> dict | None:
    """The one option of a menu that is the stored level written in the other language ('Fluent' -> 'Fluido'), else None."""
    other = _BOTH_WAYS.get(normalize(level))
    if not other:
        return None
    hits = [o for o in options if not o.get("disabled") and normalize(o.get("label", "")) == other]
    return hits[0] if len(hits) == 1 else None


def option_for_level(level: str, native_flag, options: list[dict]) -> dict | None:
    """The one option of a menu that is the stored level on the ladder ('Fluent' -> 'Advanced (C1)'), else None. Native may
    stand in the class below it (C2) when no option says native; nothing else is ever rounded."""
    wanted = level_class(level) or (NATIVE_CLASS if native_flag is True else None)
    if wanted is None:
        return None
    live = [o for o in options if not o.get("disabled")]
    for target in (wanted, 5 if wanted == NATIVE_CLASS else None):
        if target is None:
            continue
        hits = [o for o in live if level_class(o.get("label", "")) == target]
        if len(hits) == 1:
            return hits[0]
    return None
