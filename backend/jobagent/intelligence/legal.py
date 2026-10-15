"""The legal reading of a work-authorisation, sponsorship or visa question: WHAT is asked, with WHICH polarity, about WHICH place.

The answer engine used to hold one boolean per concept ('has a right to work now', 'needs sponsorship') and return it for
any question that contained the words: 'Do you lack the legal right to work?', 'Is your right to work time-limited?' and
'Have you ever been refused permission to work?' were all answered 'Yes' for a person who has the right; 'I do not require
sponsorship' was answered 'Yes' for a person who needs it. A wrong 'Yes' to a legal declaration, labelled verified.

Here a question is READ before it is answered. Three things are parsed, in English and Spanish, and anything that cannot be
parsed is not answered (the user answers it):

* the PREDICATE: a plain 'do you have the right / are you authorised / will you require sponsorship' is answered; a
  restriction, an expiry, a history of refusals, a lack, a need for permission, an exception, a request to explain, or a
  request for a value (a visa type, a number) is a different question the verified facts do not state;
* the POLARITY: 'without', 'do not require', 'not required', 'no necesito', 'sin' turn the proposition around; the
  answer is whether the proposition is TRUE for the candidate, which is what a checkbox that states it asks;
* the PLACE (``jurisdiction.py``): the right to work in the UK says nothing about Australia.

Pure functions over strings; the text is bounded first and every pattern is linear, so a hostile label costs nothing.
"""

from __future__ import annotations

import re

from ..automation.answers import normalize
from . import jurisdiction

MAX_TEXT = 1200

# What a question may ADD to 'do you have the right to work' without changing what it asks: a facet, each of which the
# engine checks against its own fact (``semantics.interpret`` lists them in ``authorization_requirements``).
SPONSORSHIP_FREE = re.compile(
    r"\bwithout (?:(?:any|a|the) )?(?:(?:need|requirement) for |needing |requiring )?"
    r"(?:(?:any|a|the) )?(?:(?:visa|employer|work) )?sponsor(?:ship)?\b|"
    r"\bno (?:visa |employer |work )?sponsor(?:ship)?\b|"
    r"\bnot (?:need|require|needing|requiring).{0,20}sponsor|"
    r"\bsin (?:necesidad de |requerir |necesitar |precisar )?(?:(?:un|el|ningun) )?(?:patrocinio|sponsor)|"
    r"\bno (?:necesit|requier|preci)\w* (?:de )?(?:(?:un|el|ningun) )?(?:patrocinio|sponsor)"
)
UNRESTRICTED = (r"permanent|unrestricted|without (?:any |the )?(?:restrictions|limitations)|unlimited|permanente|"
                r"sin restricciones|sin limitaciones|ilimitad")
FACET_PATTERNS = {
    "permanent": r"permanent|indefinite|permanente|indefinid",
    "unrestricted": r"unrestricted|without (?:any |the )?(?:restrictions|limitations)|unlimited|sin restricciones|"
                    r"sin limitaciones|ilimitad",
    "full_time": r"full time|fulltime|(?:35|40) hours|tiempo completo|jornada completa|(?:35|40) horas",
    "visa_free": r"without (?:a |any )?visa\b(?! sponsor)|sin (?:un )?visad[oa]\b|sin visa\b(?! sponsor)",
}
# The facets of the above, as one pattern: they are taken out of a proposition before its polarity and predicate are read.
FACETS = re.compile("|".join(r"\b(?:" + pattern + r")\b" for pattern in
                             (SPONSORSHIP_FREE.pattern, *FACET_PATTERNS.values())))
# The words 'Yes' / 'No' of a choice hint are no negation: 'Select Yes / No', 'Sí/No'.
CHOICE_HINT = re.compile(r"\(?\s*\b(?:select |choose |pick |answer |elige |selecciona )?(?:yes|s[i\u00ed])\s*(?:/|or|o)?\s*no\b\s*\)?", re.I)
OPTION_PREFIX = re.compile(r"^\s*(?:yes|no|si|sí)\s*[,.:;–—-]\s*(?=\S)", re.I)

NEGATOR = re.compile(
    r"(?<![a-z])(?:not|no|never|without|none|neither|nor|cannot|nunca|jamas|ningun[oa]?|sin|ni|tampoco|unable|ineligible|"
    r"unauthori[sz]ed|(?:don|doesn|didn|isn|aren|wasn|weren|won|wouldn|shouldn|couldn|haven|hasn|hadn|can) t)(?![a-z])")
LACK = re.compile(r"(?<![a-z])(?:lack(?:s|ed|ing)?|carece\w*|careces|no dispon\w+|no posee\w*)(?![a-z])")
EXPLAIN = re.compile(
    r"(?<![a-z])(?:explain\w*|describe\w*|elaborat\w*|clarif\w*|justif\w*|details?|specify|expand|outline|summari[sz]e|"
    r"circumstances|situation|reasons?|why|how come|tell us (?:about|more|how|why)|let us know|more (?:information|detail)|"
    r"further (?:information|detail)|provide (?:us )?(?:with )?(?:more |further |additional |any )?"
    r"(?:details?|information|evidence|proof|documents?|documentation|an explanation|a (?:brief )?(?:description|summary))|"
    r"give (?:us )?(?:more )?(?:details?|reasons?|an explanation|a (?:brief )?(?:description|summary))|"
    r"explica\w*|describ\w+|detalla\w*|detalles?|especifica\w*|aclara\w*|justifica\w*|circunstancias|situacion|motivos?|"
    r"razon(?:es)?|por que|cuentanos|cuentame|mas informacion|proporciona\w* (?:mas )?(?:detalles|informacion|documentos?))"
    r"(?![a-z])")
YES_NO_START = re.compile(r"^(?:are|is|am|do|does|did|have|has|can|could|will|would|was|were|must|should)\b|"
                          r"^(?:tienes|tiene|eres|estas|necesitas|necesita|puedes|puede|has|hay|posees|dispones|requieres|debes)\b")
WH_START = re.compile(r"^(?:what|which|when|where|how|who|whom|whose|cual|cuales|que|como|cuando|donde|quien|cuanto|cuantos)\b")
CONDITIONAL_START = re.compile(r"^(?:if|when|should|in case|en caso de|si)\b")
VALUE = re.compile(
    r"(?<![a-z])(?:type of|kind of|category|categories|route|basis|reference number|ref number|number|code|document|passport|"
    r"brp|share code|visa type|tipo de|categoria|numero|codigo|documento|pasaporte)(?![a-z])")
RESTRICTION = re.compile(
    r"(?<![a-z])(?:restrict\w*|limitation\w*|limited|limits?|conditions?|conditional|subject to|time ?limit\w*|temporar\w*|"
    r"fixed ?term|expir\w*|valid (?:until|for|through|to)|end ?date|end of|ends?|until|till|lapse\w*|renew\w*|extend\w*|"
    r"extension|curtail\w*|tied to|dependent|depend\w*|contingent|reliant|"
    r"restriccion\w*|restringid\w*|limitacion\w*|limitad[oa]s?|limites?|condicion\w*|sujet[oa] a|temporal(?:es)?|plazo fijo|"
    r"caduc\w*|vence\w*|vencimiento|expira\w*|valid[oa] (?:hasta|durante)|fecha de (?:fin|caducidad|vencimiento|expiracion)|"
    r"hasta|renov\w*|prorrog\w*|vinculad[oa] a|supeditad\w*)(?![a-z])")
HISTORY = re.compile(
    r"(?<![a-z])(?:refus\w*|den(?:y|ied|ial)|reject\w*|declin\w*|revok\w*|revoc\w*|cancel\w*|withdrawn|breach\w*|violat\w*|"
    r"overstay\w*|deport\w*|remov(?:ed|al)|banned?|prohibit\w*|forbid\w*|sanction\w*|denegad\w*|rechazad\w*|revocad\w*|"
    r"cancelad\w*|expulsad\w*|deportad\w*|prohibid\w*|incumpl\w*|infraccion\w*|sancion\w*|previously|in the past|history|"
    r"anteriormente|en el pasado|historial)(?![a-z])")
HISTORY_W_ONLY = re.compile(r"(?<![a-z])(?:ever|alguna vez)(?![a-z])")
NEED = re.compile(r"(?<![a-z])(?:need\w*|require\w*|must|have to|necesit\w*|requier\w*|precis\w*|obligad\w*|tienes que|debes)(?![a-z])")
EXCEPTION = re.compile(r"(?<![a-z])(?:except|unless|other than|apart from|excluding|besides|aside from|save for|but|however|"
                       r"although|though|whereas|salvo|excepto|a menos que|excluyendo|pero|aunque)(?![a-z])")
ANOTHER_QUESTION = re.compile(r"(?<![a-z])(?:and|or|y|o) (?:(?:do|are|will|can|have|would|is|did|does) (?:you|the applicant)|"
                              r"(?:tienes|eres|necesitas|puedes|requieres|estas))(?![a-z])")
# ... but 'now or will you in the future require sponsorship' repeats the same topic: only another TOPIC is another question.
LEGAL_TOPIC = re.compile(r"(?<![a-z])(?:sponsor\w*|patrocin\w*|right|authori\w*|eligib\w*|permission|permit|visa|visad\w*|work|"
                         r"trabaj\w*|derecho|autoriz\w*|empleo|employ\w*)(?![a-z])")
# 'if yes, please explain': the follow-up is another question; the proposition before it is the question of a choice control.
FOLLOW_UP = re.compile(r"(?<![a-z])(?:if|when|where|in case) (?:yes|so|not|applicable|you (?:answered|selected|said|chose|ticked|"
                       r"checked|replied) (?:yes|no)|required|necessary|appropriate)(?![a-z]).*$|"
                       r"(?<![a-z])si (?:la respuesta es|has respondido|respondiste|asi es|es asi|aplica)(?![a-z]).*$")
UNSUPPORTED_VISA = re.compile(
    r"(?<![a-z])(?:restriction\w*|conditions?|hours|working hours|limits?|limitations?|dependants?|dependents?|number|code|"
    r"reference|document|passport|brp|share code|biometric\w*|licen[sc]e|sponsor\w*|employer|numero|codigo|documento|"
    r"pasaporte|restriccion\w*|horas|horario|condicion\w*|patrocin\w*)(?![a-z])")
VISA_EXPIRY = re.compile(r"(?<![a-z])(?:expir\w*|valid until|vence\w*|caduc\w*)")
NEED_FRAME = re.compile(
    r"(?<![a-z])(?:need\w*|require\w*|requirement|necesit\w*|requier\w*|requerimiento|preci\w*)(?![a-z])(?:\W+\w+){0,5}?\W+"
    r"(?:sponsor\w*|patrocin\w*)|(?<![a-z])(?:sponsor\w*|patrocin\w*)(?:\W+\w+){0,4}?\W+"
    r"(?:is |are |be )?(?:required|needed|necessary|necesari[oa]\w*|requerid[oa]|obligatori[oa])|"
    r"(?<![a-z])(?:be|being|get|getting) sponsored(?![a-z])")
SPONSOR_WORD = re.compile(r"(?<![a-z])(?:sponsor\w*|patrocin\w*)(?![a-z])")
SPONSOR_PERSON = re.compile(r"(?<![a-z])(?:sponsor (?:name|details?|email|phone|address|company|organi[sz]ation|contact|number)|"
                            r"(?:name|details|email|phone|address) of (?:the |your )?sponsor|patrocinador\w*|"
                            r"nombre del patrocinador)(?![a-z])")
WORK_RIGHT_HEAD = re.compile(r"(?<![a-z])(?:right to work|authori[sz]\w* (?:to )?work|eligible to work|permission to work|"
                              r"derecho a trabajar|autorizacion para trabajar)(?![a-z])")
MAX_NEGATIONS = 2


SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u00bf\u00a1\"'(\[])")
ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|U\.S\.A|U\.S|U\.K|E\.U|etc|vs|approx|incl|no|nos|dept|ltd|inc|co|dr|mr|mrs|ms|st)\.", re.I)


def focus_sentence(question) -> str:
    """The sentence a long label actually asks. Employers put notes before and after the question ('Candidates must have the
    right to work in the UK. Do you have the right to work in the UK?', 'Do you require sponsorship? Please select Yes or
    No'): when exactly ONE sentence is a question it is what is read; a label with none, or with several, is read whole."""
    raw = re.sub(r"\s+", " ", str(question or "")[: MAX_TEXT * 2]).strip()[:MAX_TEXT]
    masked = ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\u2024", raw)  # 'e.g.' and 'U.S.' do not end a sentence
    parts = [part for part in SENTENCE_END.split(masked) if part.strip()]
    questions = [part for part in parts if part.rstrip().endswith("?")]
    return questions[0].replace("\u2024", ".") if len(parts) > 1 and len(questions) == 1 else raw


def prepare(question) -> tuple[str, bool]:
    """-> (the proposition, whether a leading 'Yes,' / 'No,' option prefix was dropped), normalised for matching."""
    raw = re.sub(r"\s+", " ", str(question or "")[: MAX_TEXT * 2]).strip()[:MAX_TEXT]
    prefixed = bool(OPTION_PREFIX.match(raw))
    raw = OPTION_PREFIX.sub("", raw, count=1) if prefixed else raw
    raw = CHOICE_HINT.sub(" ", raw)
    return normalize(raw), prefixed


def polarity(text: str) -> tuple[str, str | None]:
    """('affirmative' | 'negated', problem): the parity of the negations of a proposition. Facets are already removed."""
    count = len(NEGATOR.findall(text))
    if count > MAX_NEGATIONS:
        return "affirmative", "unparsed"
    return ("negated" if count % 2 else "affirmative"), None


def read(question, intent: str, field: dict | None = None) -> dict:
    """The legal reading of one question. ``kind`` is ``plain`` when it can be answered from a boolean fact, else the reason
    it cannot ('explain', 'value', 'restriction', 'history', 'lack', 'need', 'exception', 'composite', 'unparsed',
    'unsupported', 'follow_up', 'conditional'). ``polarity`` is 'negated' when the proposition says the opposite of the
    fact (a question 'do you NOT require sponsorship', a statement 'I do not require sponsorship')."""
    field = field or {}
    follow_up = bool(FOLLOW_UP.search(prepare(question)[0]))  # 'if yes, please explain' may stand after the question
    focus = focus_sentence(question)
    proposition, prefixed = prepare(focus)
    proposition = FOLLOW_UP.sub("", proposition).strip() or proposition
    options = bool(field.get("options"))
    choice_control = options or str(field.get("role") or "") in {"radio", "checkbox", "switch"} \
        or str(field.get("type") or "") in {"radio", "checkbox"}
    result = {"kind": "plain", "polarity": "affirmative", "jurisdiction": jurisdiction.named_places(focus),
              "option_prefix": prefixed, "follow_up": follow_up, "detail": ""}

    def refuse(kind: str, detail: str) -> dict:
        return {**result, "kind": kind, "detail": detail}

    if intent == "VISA":
        if YES_NO_START.match(proposition) and not VISA_EXPIRY.search(proposition):
            # 'Do you need a visa to work in the UK?' is not 'Student': the profile holds the ROUTE of a visa, not whether one
            # is needed, held or required for a purpose
            return refuse("value", "This asks a yes or no about a visa; your profile holds the route of your visa, not that")
        if UNSUPPORTED_VISA.search(proposition) and not VISA_EXPIRY.search(proposition):
            return refuse("unsupported", "This asks about a detail of a visa (hours, conditions, a number or a document) that "
                                         "your profile does not hold")
        return result
    if follow_up and not choice_control:
        return refuse("follow_up", "This asks for details as well as the yes or no answer; only you can write them")
    if CONDITIONAL_START.match(proposition):
        return refuse("conditional", "This question depends on an earlier answer and asks for more than a yes or no")
    if SPONSOR_PERSON.search(proposition):
        return refuse("unparsed", "This asks for the details of a sponsor, not whether you need one")
    remainder = FACETS.sub(" ", proposition) if intent == "WORK_AUTHORIZATION" else proposition
    if intent == "SPONSORSHIP" and not NEED_FRAME.search(proposition) and len(proposition.split()) > 4:
        return refuse("unparsed", "This mentions sponsorship but does not ask whether you need it")
    other = ANOTHER_QUESTION.search(remainder)
    if (intent == "WORK_AUTHORIZATION" and SPONSOR_WORD.search(remainder)) or (
            intent == "SPONSORSHIP" and WORK_RIGHT_HEAD.search(proposition)) or (
            other and not LEGAL_TOPIC.search(remainder[other.end():])):
        return refuse("composite", "This asks more than one question at once")
    if LACK.search(remainder):
        return refuse("lack", "This asks whether you lack a right, which your profile does not state")
    if EXCEPTION.search(remainder):
        return refuse("exception", "This adds a condition or an exception that your profile does not state")
    if HISTORY.search(remainder) or (intent == "WORK_AUTHORIZATION" and HISTORY_W_ONLY.search(remainder)):
        return refuse("history", "This asks about your history with immigration decisions, which your profile does not hold")
    if RESTRICTION.search(remainder):
        return refuse("restriction", "This asks about a restriction, a limit or an expiry of your permission, which your "
                                     "profile does not state")
    if EXPLAIN.search(remainder):
        return refuse("explain", "This asks you to explain or give details; a yes or no is not that answer")
    if WH_START.match(proposition) and not options:
        # a choice of statuses is a menu the engine maps its answer onto; free text asks for a value nobody here holds
        return refuse("value", "This asks for a value (a status, a type or a date), not a yes or no")
    if intent == "WORK_AUTHORIZATION":
        if VALUE.search(remainder) and not options:
            return refuse("value", "This asks for a value (a type, a number or a document), not a yes or no")
        if NEED.search(remainder):
            return refuse("need", "This asks whether you need a permission, a visa or a permit, which is not the same as "
                                  "having a right to work")
    sign, problem = polarity(remainder)
    if problem:
        return refuse(problem, "This is worded with too many negations to read safely")
    return {**result, "polarity": sign}
