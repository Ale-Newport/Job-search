"""The wrappers: functions of (base, language) that return every ``Rendering`` of the base under one transformation.

Verdicts (what a correct pipeline owes, never what the current one does):

  benign   the same question: same state, tier and value as the plain base (``fragile``: may degrade to "needs you", never
           change its value);
  strict   the question changed (or cannot be parsed with certainty): NEEDS_YOU / blank / consent / skip, or an answer that
           is explicitly flagged for review. A silent answer (verified, or inferred on Tab) is the failure;
  derived  the question changed in a way the facts still settle, by hand (a negation, another country, a comparison with a
           far threshold): the value, when one is given, is the ``expect`` of the rendering, and abstaining is fine;
  inert    the control is hidden / read-only / disabled / a password: SKIP, never a value;
  consent  the control carries a consent, certification or attestation: never a value, whatever the wrapper.

``only`` restricts a rendering to the candidates for whom it really is a different question (a "for at least 12 months"
question about a permanent right to work is derivable, so it is not asked of a citizen)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from . import bases as B
from . import oracle as o
from . import pools as P
from .people import EU, SPAIN, TODAY, UK, Person

FAR_PAST, FAR_FUTURE = 2000, TODAY.year + 10


@dataclass(frozen=True)
class Rendering:
    group: str
    variant: str
    verdict: str
    text: str
    kind: str | None = None  # control kind override ("textarea", "checkbox", "radio")
    options: tuple | None = None
    extra: dict = field(default_factory=dict)  # element attributes: section_label, description, visible_label, type...
    expect: Callable | None = None  # person -> Expect, for derived
    companions: tuple = ()  # ((kind, text, extra, wanted state or None), ...)
    fragile: bool = False
    why: str = ""
    only: Callable | None = None
    forbid_verified: bool = False
    quiet: bool = False  # narrow: the plain truth for which the plain answer is still complete (False: "No" needs no details)
    base: str = ""
    lang: str = "en"

    @property
    def wid(self) -> str:
        return f"{self.group}.{self.variant}"


REGISTRY: list[tuple[str, Callable]] = []


def wrapper(group: str):
    def register(fn):
        REGISTRY.append((group, fn))
        return fn
    return register


# ------------------------------------------------------------------------------------------------ text helpers
def split(q: str) -> tuple[str, str]:
    q = q.strip()
    match = re.search(r"([?:.]+)$", q)
    return (q[:match.start()], match.group(1)) if match else (q, "")


def is_question(q: str) -> bool:
    return q.strip().endswith("?")


def put(q: str, phrase: str, sep: str = " ") -> str:
    """The phrase before the final punctuation: ``put("Are you X?", "for 5 years")`` -> "Are you X for 5 years?"."""
    core, tail = split(q)
    return f"{core}{sep}{phrase}{tail}".replace("  ", " ")


def after(q: str, phrase: str) -> str:
    return f"{q.strip()} {phrase}"


def i(lang: str) -> int:
    return 0 if lang == "en" else 1


def pick(pair, lang):
    return pair[i(lang)]


def lcfirst(text: str) -> str:
    return text[:1].lower() + text[1:]


def mk(base: B.Base, lang: str, group: str, variant: str, verdict: str, text: str, **kw) -> Rendering:
    return Rendering(group=group, variant=variant, verdict=verdict, text=text, base=base.id, lang=lang, **kw)


def plain(base: B.Base, lang: str) -> str:
    return base.text(lang)


def other_lang(lang: str) -> str:
    return "es" if lang == "en" else "en"


# ================================================================================================ BENIGN
@wrapper("star")
def star(base, lang):
    q = plain(base, lang)
    return [mk(base, lang, "star", "spaced", "benign", q + " *"), mk(base, lang, "star", "tight", "benign", q + "*")]


@wrapper("required_label")
def required_label(base, lang):
    q = plain(base, lang)
    required, optional = ("(required)", "(optional)") if lang == "en" else ("(obligatorio)", "(opcional)")
    line = "Required" if lang == "en" else "Obligatorio"
    return [mk(base, lang, "required_label", "paren", "benign", f"{q} {required}"),
            mk(base, lang, "required_label", "optional", "benign", f"{q} {optional}"),
            mk(base, lang, "required_label", "line", "benign", f"{q}\n{line}")]


@wrapper("please")
def please(base, lang):
    q = plain(base, lang)
    text = base.please.get(lang) or (f"Please answer: {q}" if lang == "en" else f"Por favor, responde: {q}")
    return [mk(base, lang, "please", "prefix", "benign", text)]


@wrapper("case")
def case(base, lang):
    q = plain(base, lang)
    return [mk(base, lang, "case", "upper", "benign", q.upper()), mk(base, lang, "case", "lower", "benign", q.lower()),
            mk(base, lang, "case", "title", "benign", q.title().replace("'S", "'s"))]


@wrapper("punctuation")
def punctuation(base, lang):
    q = plain(base, lang)
    core, tail = split(q)
    bare = core.lstrip("\u00bf")  # a statement form has no inverted question mark
    out = [mk(base, lang, "punctuation", "colon", "benign", bare + ":"), mk(base, lang, "punctuation", "period", "benign", bare + ".")]
    if tail == "?":
        out += [mk(base, lang, "punctuation", "double", "benign", core + "??"),
                mk(base, lang, "punctuation", "spaced", "benign", core + " ?"),
                mk(base, lang, "punctuation", "dropped", "benign", bare)]
    return out


@wrapper("whitespace")
def whitespace(base, lang):
    q = plain(base, lang)
    words = q.split(" ")
    out = [mk(base, lang, "whitespace", "double", "benign", "  ".join(words) + "  "),
           mk(base, lang, "whitespace", "nbsp", "benign", " ".join(words)),
           mk(base, lang, "whitespace", "padded", "benign", "   " + q + "\n")]
    if len(words) > 3:
        out.append(mk(base, lang, "whitespace", "newline", "benign", " ".join(words[:3]) + "\n" + " ".join(words[3:])))
        out.append(mk(base, lang, "whitespace", "tabs", "benign", "\t".join(words)))
    return out


@wrapper("accents")
def accents(base, lang):
    q = plain(base, lang)
    stripped = o.strip_accents(q)
    if stripped == q:
        return []
    return [mk(base, lang, "accents", "removed", "benign", stripped, fragile=True,
               why="without its accents a Spanish word can be another one (si/sí, mas/más, tu/tú): failing closed is safe")]


@wrapper("translate")
def translate(base, lang):
    other = other_lang(lang)
    if other not in base.langs:
        return []
    return [mk(base, lang, "translate", "whole_question", "benign", base.text(other), options=base.opts(other) or None,
               fragile=True, why="the engine's Spanish vocabulary is bounded: failing closed on a translation is safe, a different value is not")]


@wrapper("mixed")
def mixed(base, lang):
    other = other_lang(lang)
    out = []
    if other in base.langs:
        out.append(mk(base, lang, "mixed", "both_languages", "benign", f"{plain(base, lang)} / {base.text(other)}",
                      fragile=True, why="one label in two languages: failing closed is safe"))
    if base.cls == "S":
        out.append(mk(base, lang, "mixed", "other_language_options", "benign", plain(base, lang), options=B.YES_NO[other],
                      fragile=True, why="Yes/No options in the other language: failing closed is safe"))
    return out


@wrapper("heading_neutral")
def heading_neutral(base, lang):
    if base.generic and base.cls == "V" and base.id == "av_start_generic":
        return []
    return [mk(base, lang, "heading_neutral", f"h{n}", "benign", plain(base, lang), extra={"section_label": pick(pair, lang)})
            for n, pair in enumerate(P.NEUTRAL_HEADINGS[base.cls])]


@wrapper("attributes_benign")
def attributes_benign(base, lang):
    q = plain(base, lang)
    out = [mk(base, lang, "attributes_benign", "required_flag", "benign", q, extra={"required": True})]
    if base.kind not in {"radio", "checkbox"}:  # a group's visible label is its question, not the member's own name
        out.append(mk(base, lang, "attributes_benign", "placeholder", "benign", q,
                      extra={"placeholder": "Select an option" if lang == "en" else "Selecciona una opción"}))
        out.append(mk(base, lang, "attributes_benign", "visible_same", "benign", q, extra={"visible_label": q}))
    return out


@wrapper("options_order")
def options_order(base, lang):
    options = base.opts(lang)
    if base.kind not in {"radio", "select"} or len(options) < 2:
        return []
    out = [mk(base, lang, "options_order", "reversed", "benign", plain(base, lang), options=tuple(reversed(options)))]
    if base.kind == "select":
        out.append(mk(base, lang, "options_order", "placeholder_first", "benign", plain(base, lang),
                      options=(("Select...",) if lang == "en" else ("Seleccionar",)) + tuple(options)))
    return out


@wrapper("duplicates")
def duplicates(base, lang):
    q = plain(base, lang)
    if base.kind in {"radio", "checkbox", "select"}:
        return [mk(base, lang, "duplicates", "twice_visible", "benign", q, companions=((base.kind, q, {}, "same"),))]
    return [mk(base, lang, "duplicates", "twice_visible", "benign", q, companions=((base.kind, q, {}, "same"),)),
            mk(base, lang, "duplicates", "hidden_twin", "benign", q, companions=((base.kind, q, {"type": "hidden"}, "SKIP"),))]


@wrapper("zero_width")
def zero_width(base, lang):
    q = plain(base, lang)
    words = q.split(" ")
    longest = max(range(len(words)), key=lambda n: len(words[n]))
    word = words[longest]
    if len(word) < 4:
        return []
    words[longest] = word[:2] + "​" + word[2:]
    return [mk(base, lang, "zero_width", "inside_word", "benign", " ".join(words), fragile=True,
               why="an invisible character inside a word is also how a label is spoofed: failing closed is safe")]


@wrapper("description_instruction")
def description_instruction(base, lang):
    payload = pick(P.INJECTIONS[1], lang)
    return [mk(base, lang, "description_instruction", "in_description", "benign", plain(base, lang),
               extra={"description": payload}, fragile=True,
               why="the description is untrusted employer text: reading it and failing closed is safe; obeying it is never",
               forbid_verified=False)]


# ================================================================================================ INERT CONTROLS
@wrapper("inert")
def inert(base, lang):
    q = plain(base, lang)
    if base.kind in {"radio", "checkbox"}:
        return []
    kinds = {"hidden": {"type": "hidden"}, "password": {"type": "password"}, "readonly": {"readonly": True},
             "disabled": {"enabled": False}, "auxiliary": {"auxiliary_control": True}}
    return [mk(base, lang, "inert", name, "inert", q, extra=extra) for name, extra in kinds.items()]


# ================================================================================================ NOTES
@wrapper("note")
def note(base, lang):
    q = plain(base, lang)
    out = []
    for n, pair in enumerate(P.NOTES[base.cls]):
        words = pick(pair, lang)
        prefix = "note: " if (n == 0 and lang == "en") else ("nota: " if n == 0 else "")
        out += [mk(base, lang, "note", f"paren{n}", "strict", put(q, f"({prefix}{words})")),
                mk(base, lang, "note", f"after_qm{n}", "strict", after(q, f"({words})")),
                mk(base, lang, "note", f"bracket{n}", "strict", put(q, f"[{words}]")),
                mk(base, lang, "note", f"dash{n}", "strict", after(q, f"- {words}")),
                mk(base, lang, "note", f"newline{n}", "strict", f"{q}\n{words}")]
    return out


# ================================================================================================ EXCLUSIONS
# Where "not including X" cannot change the plain answer of ANY candidate, so that the plain answer stays right: a typed name has
# no middle name or title in the facts; the other demographic bases have nothing to exclude; a country of residence has no part.
NO_EXCLUSION = {"id_first", "id_last", "id_full", "id_name", "dm_gender", "dm_pronouns", "dm_ethnicity", "dm_veteran",
                "dm_religion", "dm_dob", "loc_country"}


@wrapper("exclusion")
def exclusion(base, lang):
    if base.cls == "G" or base.id in NO_EXCLUSION:
        return []
    q = plain(base, lang)
    out = []
    for n, pair in enumerate(P.EXCLUSIONS_BY_BASE.get(base.id) or P.EXCLUSIONS[base.family]):
        for form, en, es in P.EXCLUSION_FORMS:
            text = put(q, (en if lang == "en" else es).format(x=pick(pair, lang)), sep="").replace(" de el ", " del ")
            out.append(mk(base, lang, "exclusion", f"{form}{n}", "narrow" if base.cls == "S" else "strict", text))
    return out


# ================================================================================================ NEGATION
def flipped(base: B.Base, slots=None):
    return lambda p: o.flip(base.expect(p, slots))


def same(base: B.Base, slots=None):
    return lambda p: base.expect(p, slots)


@wrapper("negation")
def negation(base, lang):
    if base.cls != "S" or base.clause is None:
        return []
    c = base.clause
    neg_decl, pos_decl = B.declaration(base, lang, True), B.declaration(base, lang, False)
    # A negative question ("Are you not...?") is ambiguous in natural language (Yes may mean "I am"), so both polarities pass,
    # but never an answer without a fact. The pinned English readings of work authorisation and sponsorship answer the
    # PROPOSITION ("Are you not legally authorised..." -> No for an authorised candidate): there the flip is the oracle.
    pinned = lang == "en" and base.family in {"right_to_work", "sponsorship"}
    ambiguous = lambda b=base: flipped(b) if pinned else (lambda p: o.either(b.expect(p)))  # noqa: E731
    out = [mk(base, lang, "negation", "aux_not", "derived", B.question(base, lang, negated=True), expect=ambiguous())]
    if lang == "en":
        out += [mk(base, lang, "negation", "is_it_true", "derived", f"Is it true that {neg_decl}?", expect=flipped(base)),
                mk(base, lang, "negation", "is_it_correct", "derived", f"Is it correct that {neg_decl}?", expect=flipped(base)),
                mk(base, lang, "negation", "is_it_false", "derived", f"Is it false that {pos_decl}?", expect=flipped(base)),
                mk(base, lang, "negation", "is_it_true_positive", "derived", f"Is it true that {pos_decl}?", expect=same(base))]
        contraction = {"be": "Aren't", "do": "Don't", "will": "Won't", "have": "Haven't", "can": "Can't"}[c.aux]
        rest = B.fill(c.rest_en, "en", base.slots)
        out.append(mk(base, lang, "negation", "contraction", "derived", f"{contraction} you {rest}?", expect=ambiguous()))
        if base.family == "right_to_work" and "place" in base.slots:
            place = B.fill("{place}", "en", base.slots)
            out += [mk(base, lang, "negation", "lack_the_right", "derived", f"Do you lack the legal right to work in {place}?", expect=flipped(base)),
                    mk(base, lang, "negation", "unauthorised", "derived", f"Are you unauthorised to work in {place}?", expect=flipped(base))]
        if base.family == "sponsorship" and "place" in base.slots:
            place = B.fill("{place}", "en", base.slots)
            out += [mk(base, lang, "negation", "without_needing", "derived",
                       f"Can you work in {place} without needing sponsorship now or in the future?", expect=flipped(base)),
                    mk(base, lang, "negation", "never_require", "derived",
                       f"Will you never require sponsorship to work in {place}?", expect=flipped(base))]
    else:
        subj = c.verbs_es[3]
        rest = c.rest_es
        out += [mk(base, lang, "negation", "es_verdad_que", "derived", B.fill(f"¿Es verdad que {neg_decl}?", "es", base.slots), expect=flipped(base)),
                mk(base, lang, "negation", "es_falso_que", "derived", B.fill(f"¿Es falso que {subj} {rest}?".replace("  ", " ").replace(" ?", "?"), "es", base.slots), expect=flipped(base)),
                mk(base, lang, "negation", "es_verdad_que_positivo", "derived", B.fill(f"¿Es verdad que {pos_decl}?", "es", base.slots), expect=same(base))]
        if base.family == "right_to_work" and "place" in base.slots:
            place = B.fill("{place}", "es", base.slots)
            out.append(mk(base, lang, "negation", "careces", "derived", f"¿Careces de autorización para trabajar en {place}?", expect=flipped(base)))
        if base.family == "sponsorship" and "place" in base.slots:
            place = B.fill("{place}", "es", base.slots)
            out.append(mk(base, lang, "negation", "nunca", "derived", f"¿Nunca necesitarás patrocinio de visado para trabajar en {place}?", expect=flipped(base)))
    return out


@wrapper("double_negation")
def double_negation(base, lang):
    if base.cls != "S" or base.clause is None:
        return []
    neg_decl = B.declaration(base, lang, True)
    if lang == "en":
        forms = {"false_that_not": f"Is it false that {neg_decl}?", "untrue_that_not": f"Is it untrue that {neg_decl}?",
                 "not_the_case_not": f"Is it not the case that {neg_decl}?"}
    else:
        subj, rest = base.clause.verbs_es[3], base.clause.rest_es
        forms = {"falso_que_no": B.fill(f"¿Es falso que no {subj} {rest}?".replace("  ", " ").replace(" ?", "?"), "es", base.slots),
                 "no_cierto_que_no": B.fill(f"¿No es cierto que no {subj} {rest}?".replace("  ", " ").replace(" ?", "?"), "es", base.slots)}
    return [mk(base, lang, "double_negation", name, "strict", text, expect=same(base)) for name, text in forms.items()]


@wrapper("negation_other")
def negation_other(base, lang):
    """Consent negated and a disability negated by the same wrapper: consent is never answered, whatever the polarity."""
    from .bases import CONSENT_NEGATED
    if base.cls == "C" and base.id in CONSENT_NEGATED:
        return [mk(base, lang, "negation_other", "negated_consent", "consent", CONSENT_NEGATED[base.id][i(lang)])]
    return []


# ================================================================================================ COORDINATION, FOLLOW-UPS
@wrapper("coordination")
def coordination(base, lang):
    q = plain(base, lang)
    core, tail = split(q)
    out = []
    for n, pair in enumerate(P.COORDINATION[base.cls]):
        other = pick(pair, lang)
        if base.cls == "S" and lang == "es":
            core_es = core.rstrip("?")
            out += [mk(base, lang, "coordination", f"y{n}", "strict", f"{core_es} y {other}?"),
                    mk(base, lang, "coordination", f"o{n}", "strict", f"{core_es} o {other}?")]
            continue
        for word, label in (("and", "and"), ("or", "or")) if lang == "en" else (("y", "y"), ("o", "o")):
            joined = f"{core} {word} {other}"
            out.append(mk(base, lang, "coordination", f"{label}{n}", "strict", joined + (tail if tail else "")))
    return out


@wrapper("follow_up")
def follow_up(base, lang):
    if base.cls != "S":  # "If so, ..." only follows a question a Yes/No control carries
        return []
    q = plain(base, lang)
    pool = P.FOLLOW_UPS.get(base.family) or P.GENERIC_FOLLOW_UPS
    out = []
    for n, pair in enumerate(pool):
        # "If so, ..." is only triggered by a Yes, "If not, ..." only by a No: where it is not triggered the plain answer is
        # complete, and still right
        on_no = pair[0].startswith(("If not", "If no,"))
        out.append(mk(base, lang, "follow_up", f"f{n}", "narrow", after(q, pick(pair, lang)), quiet=on_no))
    return out


# ================================================================================================ TIME, QUANTIFIERS, COMPARATORS
@wrapper("time_qualifier")
def time_qualifier(base, lang):
    pool = P.TIME.get(base.family)
    if not pool or (base.cls != "S" and base.family != "salary"):
        return []
    q = plain(base, lang)
    only = None
    if base.family in {"right_to_work", "sponsorship"} and base.cls == "S":
        only = lambda p, b=base: _unsettled(p, b)  # noqa: E731
    return [mk(base, lang, "time_qualifier", f"t{n}", "narrow" if base.cls == "S" else "strict",
               put(q, pick(pair, lang).strip()), only=only) for n, pair in enumerate(pool)]


@wrapper("quantifier")
def quantifier(base, lang):
    pool = P.QUANTIFIERS.get(base.family)
    if not pool or base.cls != "S":
        return []
    return [mk(base, lang, "quantifier", f"q{n}", "strict", pick(pair, lang)) for n, pair in enumerate(pool)]


def _unsettled(person: Person, base: B.Base) -> bool:
    """True when a time-bound version of the question is NOT settled by the person's facts: a permanent right to work (or
    a sponsorship that is never needed) makes "for more than 5 years" derivable, so it is not asked of them."""
    place = base.slots.get("place") or person.job_country
    if base.family == "right_to_work":
        permanent = person.rtw(place).truth is True and (place != UK or (person.immigration or {}).get("permanent_unrestricted"))
        return not permanent
    return person.sponsorship(place).truth is not False


@wrapper("comparator")
def comparator(base, lang):
    pool = P.COMPARATORS.get(base.family)
    if not pool or base.cls != "S" or base.id == "gr_by":
        return []
    q = plain(base, lang)
    only = (lambda p, b=base: _unsettled(p, b)) if base.family in {"right_to_work", "sponsorship"} else None
    return [mk(base, lang, "comparator", f"c{n}", "narrow", put(q, pick(pair, lang).strip()), only=only) for n, pair in enumerate(pool)]


@wrapper("comparator_dates")
def comparator_dates(base, lang):
    """"Have you graduated before 2000 / since 2000 / by 2036 ...": settled by the end dates of the records, with thresholds
    far from every one of them, so the hand answer is certain."""
    if base.id != "gr_done":
        return []
    q = plain(base, lang)
    core = split(q)[0]
    rows = [(f"before {FAR_PAST}", f"antes de {FAR_PAST}", False), (f"since {FAR_PAST}", f"desde {FAR_PAST}", True),
            (f"after {FAR_PAST}", f"después de {FAR_PAST}", True), (f"before {FAR_FUTURE}", f"antes de {FAR_FUTURE}", True),
            (f"by {FAR_FUTURE}", f"para {FAR_FUTURE}", True), (f"after {FAR_FUTURE}", f"después de {FAR_FUTURE}", False),
            (f"between {FAR_PAST} and {FAR_FUTURE}", f"entre {FAR_PAST} y {FAR_FUTURE}", True)]
    out = []
    for n, (en, es, truth) in enumerate(rows):
        text = f"{core} {pick((en, es), lang)}?"
        out.append(mk(base, lang, "comparator_dates", f"d{n}", "derived", text,
                      expect=lambda p, t=truth: o.boolean(t) if p.completed() else o.UNKNOWN))
    return out


def _numeric_rows(base: B.Base):
    """(English question, Spanish question, expect) rows that turn a value question into a comparison whose answer the facts
    settle: thresholds far from every candidate's value, so the hand answer is certain."""
    rows = []
    ops = (("more than", "de más de", lambda v, k: v > k), ("less than", "de menos de", lambda v, k: v < k),
           ("at least", "de al menos", lambda v, k: v >= k), ("at most", "de como máximo", lambda v, k: v <= k),
           ("over", "superior a", lambda v, k: v > k), ("under", "inferior a", lambda v, k: v < k))
    if base.id in {"nt_weeks", "nt_period"}:  # thresholds 1 and 6 weeks: every notice (0, 2, 4.3, 13 weeks) is far from both
        for k in (1, 6):
            es_unit = "semana" if k == 1 else "semanas"
            for op, es_op, test in ops:
                rows.append((f"Is your notice period {op} {k} week{'s' if k > 1 else ''}?", f"¿Tu periodo de preaviso es {es_op} {k} {es_unit}?",
                             lambda p, t=test, k=k: o.boolean(t(sum(p.notice_weeks) / 2, k)) if p.notice_weeks else o.UNKNOWN))
            rows.append((f"Is your notice period {k} week{'s' if k > 1 else ''} or more?", f"¿Tu periodo de preaviso es de {k} {es_unit} o más?",
                         lambda p, k=k: o.boolean(sum(p.notice_weeks) / 2 >= k) if p.notice_weeks else o.UNKNOWN))
            rows.append((f"Is your notice period {k} week{'s' if k > 1 else ''} or less?", f"¿Tu periodo de preaviso es de {k} {es_unit} o menos?",
                         lambda p, k=k: o.boolean(sum(p.notice_weeks) / 2 <= k) if p.notice_weeks else o.UNKNOWN))
    elif base.id == "sal_expect":
        plural = {"superior a": "superiores a", "inferior a": "inferiores a"}  # the salary expectations are plural
        for k in (20000, 100000):
            for op, es_op, test in ops:
                rows.append((f"Are your annual salary expectations {op} {k}?",
                             f"¿Tus pretensiones salariales anuales son {plural.get(es_op, es_op)} {k}?",
                             lambda p, t=test, k=k: o.boolean(t(p.salary, k)) if p.salary else o.UNKNOWN))
    elif base.id in {"av_start", "av_when"}:
        far, near = TODAY + timedelta(days=5 * 365), TODAY - timedelta(days=30)
        for day in (far, near):
            for op, es_op, test in (("before", "antes del", lambda s, d: s < d), ("after", "después del", lambda s, d: s > d),
                                    ("by", "para el", lambda s, d: s <= d), ("on or before", "como máximo el", lambda s, d: s <= d)):
                rows.append((f"Can you start {op} {day.isoformat()}?", f"¿Puedes incorporarte {es_op} {day.isoformat()}?",
                             lambda p, t=test, d=day: o.boolean(t(p.start, d)) if p.start else o.UNKNOWN))
    elif base.id == "gr_year":
        for year in (TODAY.year - 1, TODAY.year + 5):
            for op, es_op, test in (("before", "antes de", lambda y, k: y < k), ("after", "después de", lambda y, k: y > k),
                                    ("by", "para", lambda y, k: y <= k), ("in or after", "en o después de", lambda y, k: y >= k)):
                rows.append((f"Will you graduate {op} {year}?", f"¿Te graduarás {es_op} {year}?",
                             lambda p, t=test, k=year: (o.boolean(t(int(p.studying()["end_date"][:4]), k)) if p.studying() else o.UNKNOWN)))
    return rows


@wrapper("comparator_numeric")
def comparator_numeric(base, lang):
    out = []
    only = (lambda p: p.studying() is not None) if base.id == "gr_year" else None
    for n, (en, es, fn) in enumerate(_numeric_rows(base)):
        out.append(mk(base, lang, "comparator_numeric", f"n{n}", "derived", en if lang == "en" else es, kind="radio",
                      options=B.YES_NO[lang], expect=fn, only=only))
    return out


# ================================================================================================ ANOTHER PLACE, ANOTHER ATTRIBUTE
ALT_VALUES = {"place": [UK, SPAIN, "Ireland", "Germany", "France", EU, "United States", "Australia", "India", "Canada"],
              "lang": ["English", "Spanish", "French", "German", "Hindi", "Welsh", "Italian", "Irish", "Mandarin"],
              "subject": ["Mathematics", "Geography", "Economics", "Medicine", "Law", "Modern Languages"],
              "degree": ["Bachelor's", "Master's", "Doctorate"]}


@wrapper("jurisdiction")
def jurisdiction(base, lang):
    """The same question about another country, language, subject or level: the answer is what THAT proposition's facts say."""
    if base.cls != "S" or len(base.slots) != 1 or "year" in base.slots:
        return []
    ((name, current),) = base.slots.items()
    out = []
    for value in ALT_VALUES[name]:
        if value == current:
            continue
        slots = {name: value}
        out.append(mk(base, lang, "jurisdiction", f"{name}={value}", "derived", B.question(base, lang, slots=slots),
                      expect=lambda p, b=base, s=slots: b.expect(p, s)))
    return out


@wrapper("attribute")
def attribute(base, lang):
    pool = P.ATTRIBUTES.get(base.family)
    if not pool:
        return []
    return [mk(base, lang, "attribute", f"a{n}", "strict", pick(pair, lang),
               kind=("checkbox" if base.cls == "C" else "text" if base.kind in {"radio", "select", "date"} else None))
            for n, pair in enumerate(pool)]


# ================================================================================================ SUBJECT
SUBJECT_LABELS = {"spouse": ("Spouse's {t}", "{T} de tu cónyuge"), "referee": ("Referee {t}", "{T} de la referencia"),
                  "parents": ("Parent or guardian {t}", "{T} de tus padres"),
                  "emergency": ("Emergency contact {t}", "{T} del contacto de emergencia")}
POSSESSIVE = {"spouse": "your spouse's", "referee": "your referee's", "emergency": "your emergency contact's"}
PLAIN = {"spouse": "your spouse", "referee": "your referee", "emergency": "your emergency contact"}


@wrapper("subject")
def subject(base, lang):
    out = []
    if base.cls == "S" and base.clause is not None:
        for who in ("spouse", "referee", "parents", "emergency"):
            if lang == "es" and who == "referee":
                continue
            out.append(mk(base, lang, "subject", who, "strict", B.question(base, lang, who=who)))
    elif base.cls == "I":
        t = base.topic[lang]
        for who, (en, es) in SUBJECT_LABELS.items():
            out.append(mk(base, lang, "subject", who, "strict", (en if lang == "en" else es).format(t=t, T=B.cap(t))))
    elif base.cls in {"V", "D"} and base.subj:
        for who in ("spouse", "referee", "emergency"):
            if lang == "en":
                text = base.subj["en"].format(s=POSSESSIVE[who], who=PLAIN[who])
            else:
                text = base.subj["es"].format(s={"spouse": "tu cónyuge", "referee": "tu referencia", "emergency": "tu contacto de emergencia"}[who])
            out.append(mk(base, lang, "subject", who, "strict", text))
    elif base.cls == "G":
        for who in ("spouse", "referee", "emergency"):
            text = (f"Signature of {PLAIN[who]} (type their full name)" if lang == "en"
                    else f"Firma de {({'spouse': 'tu cónyuge', 'referee': 'tu referencia', 'emergency': 'tu contacto de emergencia'})[who]} (escribe su nombre completo)")
            out.append(mk(base, lang, "subject", who, "strict", text))
    return out


# ================================================================================================ NARRATIVES
@wrapper("narrative")
def narrative(base, lang):
    pool = P.NARRATIVES.get(base.family)
    if not pool:
        return []
    return [mk(base, lang, "narrative", f"n{n}", "strict", pick(pair, lang), kind="textarea") for n, pair in enumerate(pool)]


# ================================================================================================ HEADINGS
@wrapper("heading_subject")
def heading_subject(base, lang):
    out = []
    if not base.generic and base.id not in {"id_first", "id_last", "id_full", "id_linkedin"}:
        return out
    q = plain(base, lang)
    for key, (en, es, ids) in P.HEADINGS.items():
        if base.id in ids:
            out.append(mk(base, lang, "heading_subject", key, "strict", q, extra={"section_label": en if lang == "en" else es}))
    if base.id in {"id_name", "id_full"}:
        for en, es in (("Declaration", "Declaración"), ("Signature", "Firma")):
            out.append(mk(base, lang, "heading_subject", "declaration_" + en.lower(), "signature", q,
                          extra={"section_label": en if lang == "en" else es}))
    return out


# ================================================================================================ INJECTION, SPOOF, MISMATCH
def sentence(q: str, extra: str) -> str:
    return f"{q} {extra}" if q.rstrip().endswith(("?", ".", ":")) else f"{q}. {extra}"


@wrapper("injection")
def injection(base, lang):
    q = plain(base, lang)
    return [mk(base, lang, "injection", f"label{n}", "strict", sentence(q, pick(pair, lang)), expect=same(base))
            for n, pair in enumerate(P.INJECTIONS)] + \
        [mk(base, lang, "injection", "label_first", "strict", sentence(pick(P.INJECTIONS[0], lang), q), expect=same(base))]


@wrapper("spoof")
def spoof(base, lang):
    q = plain(base, lang)
    other = "Favourite colour" if lang == "en" else "Color favorito"
    if base.kind in {"radio"}:
        return []  # a radio group's question is its context: the visible label is the option, see injection
    return [mk(base, lang, "spoof", "visible_label_differs", "strict", q, extra={"visible_label": other})]


@wrapper("control_mismatch")
def control_mismatch(base, lang):
    q = plain(base, lang)
    if base.cls in {"V", "I", "D"} and base.kind not in {"radio", "checkbox"}:
        return [mk(base, lang, "control_mismatch", "as_checkbox", "strict", q, kind="checkbox"),
                mk(base, lang, "control_mismatch", "as_yes_no", "strict", q, kind="radio", options=B.YES_NO[lang])]
    return []


@wrapper("attestation")
def attestation(base, lang):
    """A proposition about the candidate turned into a checkbox that attests to it ("I confirm that I am legally authorised...")."""
    if base.cls != "S" or base.clause is None:
        return []
    statement = B.first_person(base, lang)
    prefix = "I confirm that" if lang == "en" else "Confirmo que"
    return [mk(base, lang, "attestation", "i_confirm", "consent", f"{prefix} {statement}", kind="checkbox"),
            mk(base, lang, "attestation", "i_declare", "consent", ("I declare that " if lang == "en" else "Declaro que ") + statement, kind="checkbox")]


@wrapper("mixed_conflict")
def mixed_conflict(base, lang):
    if base.cls != "S":
        return []
    other = B.BY_ID["lg_speak[German]"] if base.family != "language" else B.BY_ID["rw[United Kingdom]"]
    return [mk(base, lang, "mixed_conflict", "two_questions", "strict",
               f"{plain(base, lang)} / {other.text(other_lang(lang))}", fragile=False)]


def applicable(wrapper_fn, base: B.Base, lang: str) -> list[Rendering]:
    return [r for r in wrapper_fn(base, lang) if r.text != base.text(lang) or r.extra or r.kind or r.options or r.companions]


def all_renderings(base: B.Base, lang: str) -> list[Rendering]:
    out = []
    for group, fn in REGISTRY:
        out += applicable(fn, base, lang)
    return out
