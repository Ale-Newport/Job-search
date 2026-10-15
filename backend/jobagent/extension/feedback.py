"""What the user finally did with a suggestion, and what Meridian may remember of it (design 5.7).

Persistence rules, in order of importance:

* **Typed values are only ever received here** (``final_value`` / ``final_values``) and only to be saved on the user's
  explicit confirmation. Nothing is written for password-like, hidden or file controls, ever.
* A corrected or typed answer is saved exactly like a direct edit in Auto Apply's browser
  (``remember_answer(..., direct_edit=True)`` + the ``application_answers`` / ``application_answer_targets`` /
  superseded bookkeeping of ``orchestrator.on_user_edit``), so the next suggestion, Auto Apply and the Profile all
  agree.
* **Scope.** Narratives, company-specific, protected (personal-disclosure, work authorisation, consent), legal and
  salary answers are forced to THIS application, whatever the client asked for: they must never leak to another
  employer. ``reuse`` is honoured only for factual questions that map to a Profile field; it then promotes the value to
  that field (``write_profile``), which needs the explicit ``remember: "reuse"`` flag.
* **Clearing** (an empty ``final_value``) only counts when it is sent explicitly, and is application-scoped unless the
  user explicitly chose ``reuse`` for a profile-mappable question.
* An answer the user left unchanged (``accepted``) or dismissed (``rejected``/``skipped``) teaches nothing: audit only.
* **Never saved, whatever the client sends**: a one-time code, a card, bank or ID number, a security answer, a password
  (:func:`never_saved_reason`); the details of ANOTHER person under a bare label ('Name' under 'Emergency contact'), and
  anything under a label that names two questions of the page (a saved answer is remembered by its label alone, so it
  would come back, VERIFIED, in the field it was not typed into). A value saved to the Profile is checked the way the
  Profile's own field would check it. Every refusal says why with a precise ``code``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from ..application_profile import BY_KEY, question_field, remember_answer
from ..automation.answers import SENSITIVE, classify_question, normalize
from ..automation.disclosures import personal_disclosure_kind
from ..db import now, uid
from ..intelligence.engine import NARRATIVE_INTENTS
from ..intelligence.semantics import PEOPLE, SUBJECT_KEYS
from .text import strip_controls

NEVER_SAVED_TYPES = {"password", "hidden", "file"}
PROTECTED_INTENTS = {"DEMOGRAPHIC", "WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "REASONABLE_ADJUSTMENTS", "GDPR",
                     "CONSENT", "CRIMINAL_HISTORY", "SECURITY_CLEARANCE", "BACKGROUND_CHECK",
                     "CONFLICT_OF_INTEREST", "ROLE_CONFIRMATION"}
COMPANY_WORDS = re.compile(
    r"\b(?:this|our|the) (?:company|role|position|job|team|opportunity|vacancy|organi[sz]ation|programme|program|"
    r"internship|graduate scheme)\b|\bwork(?:ing)? (?:here|for us|with us|at)\b|\bjoin (?:us|our|the team)\b|"
    r"\bwhy (?:us|here|our)\b|\bour (?:mission|values|culture|policy)\b|\bhybrid working policy\b", re.I)


# --------------------------------------------------------------------------------------------- what is never saved
# Each pattern is applied to the question and to the field's own names (label, name, id, autocomplete, placeholder, help), after
# ``normalize`` (lower case, accents and punctuation gone). The vocabulary is English and Spanish and a SUPERSET of the scanner's
# (``extension/src/content/sensitive.ts``: the scanner leaves such controls alone, this is the second wall for a value that reaches
# /feedback anyway). A look-alike that is an ordinary question ('Years of experience with Swift', 'Do you hold a valid passport?')
# stays savable: words that name a number are gated by what is typed, never by the word alone.
NEVER_SAVED = {
    "otp": re.compile(
        r"\b(?:one time (?:pass ?word|pass ?code|code|pin)|otp|2fa|mfa|two factor|two step|2 step|verificacion en dos pasos|"
        r"(?:verification|verify|authentication|authenticator|confirmation|activation|login|log in|sign in|sms|text message|"
        r"email|recovery|backup|single use|access) code|authenticator app|pass ?code|(?:\d+|six|four|eight) digit code|"
        r"code (?:that )?(?:we|you|i) (?:sent|emailed|texted|received|got)|security code|"
        r"codigo (?:de )?(?:verificacion|seguridad|confirmacion|autenticacion|acceso|sms|un solo uso|de seis digitos)|"
        r"codigo que (?:te|le|os) (?:hemos|han|he) (?:enviado|mandado)|introduce el codigo|"
        r"(?:contrasena|clave) (?:de un solo uso|de verificacion|unica|temporal))\b"),
    "card": re.compile(
        r"\b(?:card (?:number|no|num|verification|security|expir\w*|details)|card ?holder|(?:credit|debit|payment|bank) card|"
        r"cardnumber|cc (?:num|number|exp|expiry|expiration|csc|cvv|cvc)|ccnum|cvv2?|cvc2?|ccv|csc|name on (?:the )?card|"
        r"expir\w* date of (?:the )?card|numero de tarjeta|tarjeta (?:de )?(?:credito|debito|bancaria)|"
        r"titular de la tarjeta|fecha de (?:caducidad|vencimiento|expiracion) de la tarjeta)\b"),
    "bank": re.compile(
        r"\b(?:iban|swift (?:code|bic|number)|swift bic|bic swift|bic code|routing (?:number|no)|aba (?:number|routing)|"
        r"sort code|(?:bank )?account (?:number|no|num)|bank account|bank details|bank name|account holder|"
        r"beneficiary (?:name|account)|clabe|numero de cuenta|cuenta (?:bancaria|corriente|de ahorros?)|datos bancarios|"
        r"codigo (?:iban|swift|bic)|titular de la cuenta)\b"),
    "id": re.compile(
        r"\b(?:ssn|nino|nin|nss|itin|utr|aadhaar|aadhar|curp|cpf|cnpj|bsn|personnummer|steuer ?id\w*|"
        r"sozialversicherungsnummer|share code|(?:national insurance|social security|social insurance|national id(?:entity)?|"
        r"identity(?: card)?|identification|passport|driv(?:ing|er s?) licen[cs]e|tax(?:payer)?(?: id(?:entification)?)?|"
        r"tax file|ni|sin|id|pan|nif|nie|dni|residence permit|brp) (?:number|no|num)|"
        r"tax(?:payer)? (?:id|identification|reference|file number)|"
        r"(?:numero|n o|nro|num) (?:de |del |de la )?(?:documento|identificacion|identidad|seguridad social|pasaporte|"
        r"afiliacion|permiso de conducir|carne de conducir|licencia de conducir|tarjeta de residencia)|"
        r"documento (?:nacional )?(?:de )?identidad)\b"),
    "security_answer": re.compile(
        r"\b(?:security (?:question|answer)|secret (?:question|answer)|mother s maiden name|memorable (?:word|information|name|"
        r"date|place)|first pet|favou?rite pet|pet s name|name of your first (?:pet|school|teacher)|"
        r"pregunta de seguridad|respuesta de seguridad|pregunta secreta|respuesta secreta|apellido de soltera de (?:tu|su) madre|"
        r"primera mascota|nombre de tu mascota)\b"),
}
# Bare words that name an identifier only when what is TYPED looks like one ('Passport: Yes' is a yes/no question, 'Passport:
# 123456789' is the number).
GATED = {"id": re.compile(
    r"\b(?:passport|pasaporte|dni|nie|nif|cif|tin|visa number|permit number|social security|seguridad social|"
    r"national insurance|social insurance|documento (?:de )?identidad|(?:driv(?:ing|er s?) )?licen[cs]e|"
    r"carne de conducir|permiso de conducir|licencia de conducir)\b"),
    "bank": re.compile(r"\b(?:swift|bic)\b")}
SWIFT_CODE = re.compile(r"[A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}(?:[A-Za-z0-9]{3})?")
PASSWORD_WORDS = re.compile(r"\b(?:password|passwd|passphrase|contrasena|clave de acceso)\b")
MAX_NAME_TEXT = 160  # a longer text is a sentence (a question, an essay prompt), not the name of a field
MAX_PASSWORD_LABEL = 40  # 'Password' names a field only when the label is about nothing else ('Create a password')
NEVER_SAVED_AUTOCOMPLETE = {"cc-number": "card", "cc-csc": "card", "cc-exp": "card", "cc-exp-month": "card",
                            "cc-exp-year": "card", "cc-name": "card", "cc-type": "card", "cc-given-name": "card",
                            "cc-family-name": "card", "cc-additional-name": "card", "one-time-code": "otp",
                            "new-password": "password", "current-password": "password"}
NEVER_SAVED_MESSAGES = {
    "password": "Passwords are never saved.", "hidden": "Hidden fields are never saved.", "file": "Files are never saved.",
    "otp": "Codes sent to you for verification are never saved.",
    "card": "Payment card details are never saved.", "bank": "Bank details are never saved.",
    "id": "Identity numbers (passport, tax, social security) are never saved.",
    "security_answer": "Answers to security questions are never saved.",
}
LOOKS_LIKE_A_NUMBER = re.compile(r"(?=[A-Za-z0-9 \-]*\d)[A-Za-z0-9][A-Za-z0-9 \-]{4,}")
# Subjects the user may be asked about whose details Meridian keeps only on the application, and only on explicit confirmation.
HEALTH_ADJACENT = re.compile(
    r"\b(?:pregnan\w*|maternity|children|child|dependants?|dependents?|political|trade union|union member\w*|"
    r"sexual orientation|embarazo|embarazada|hijos|politic[ao]s?|afiliacion sindical)\b")


def looks_like_an_identifier(value: str) -> bool:
    """Could this typed text BE an identity number ('QQ 12 34 56 C', 'W12 345 678', '1234')? It has a digit and no sentence
    around it ('Yes, since 2019' is an answer about the identifier, not the identifier)."""
    text = (value or "").strip()
    return (0 < len(text) <= 40 and bool(re.search(r"\d", text)) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-/.]*", text))
            and len(re.findall(r"[A-Za-z]{3,}", text)) <= 1)


# What a person types when the question is ABOUT an identifier and not the identifier itself ('Do you hold a National Insurance
# number?': 'Yes', 'No', 'Not yet', 'Prefer not to say'). Anything else typed in such a field is treated as the identifier.
PLAIN_ANSWER = re.compile(r"(?:yes|no|y|n|true|false|none|nil|n a|na|not applicable|not yet|unknown|prefer not to say|"
                          r"i prefer not to say|do not know|dont know|si|ninguno|ninguna|no aplica|prefiero no decirlo|no lo se)\b")


def is_a_plain_answer(value: str) -> bool:
    return bool(PLAIN_ANSWER.match(normalize(value or ""))) and not looks_like_an_identifier(value)


def never_saved_reason(question: str, element: dict, value: str | None = None) -> str | None:
    """Why a typed value in this control must never be remembered (a precise code), or None when it may be. Pure.

    ``password`` / ``hidden`` / ``file`` by the control's type; ``card`` / ``otp`` by the browser's own ``autocomplete``
    token (``cc-number``, ``one-time-code``); ``otp`` / ``card`` / ``bank`` / ``id`` / ``security_answer`` / ``password`` by what
    the field is called, in English and Spanish; some identifiers (a passport, a DNI) only when the typed value looks like one,
    and a plain answer ('Yes' to 'Do you hold a National Insurance number?') is an ordinary answer."""
    kind = str(element.get("type") or "").lower()
    if kind in {"password", "hidden", "file"}:
        return kind
    if element.get("role") == "upload":
        return "file"
    for token in re.split(r"\s+", str(element.get("autocomplete") or "").lower()):
        if token in NEVER_SAVED_AUTOCOMPLETE:
            return NEVER_SAVED_AUTOCOMPLETE[token]
    parts = [str(element.get(k) or "") for k in ("name", "id", "placeholder", "description", "label")]
    # a field called 'cardNumber' / 'one_time_code' says so in camelCase or snake_case: read the words
    spaced = [re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", part) for part in parts]
    shown = [normalize(text) for text in (question or "", *spaced) if text and len(text) <= MAX_NAME_TEXT]
    text = " ".join(shown)
    for code, pattern in NEVER_SAVED.items():
        if pattern.search(text):
            if code == "otp" and NEVER_SAVED["card"].search(text):
                return "card"  # 'security code' on a payment form
            if code == "id" and value is not None and is_a_plain_answer(value):
                continue  # 'Yes' to 'Do you hold a National Insurance number?'
            return code
    if any(PASSWORD_WORDS.search(normalize(t)) for t in (question or "", *spaced[-1:]) if t and len(t) <= MAX_PASSWORD_LABEL):
        return "password"
    if value is not None:
        typed = value.strip()
        if GATED["id"].search(text) and LOOKS_LIKE_A_NUMBER.fullmatch(typed):
            return "id"
        if GATED["bank"].search(text) and SWIFT_CODE.fullmatch(typed):
            return "bank"
    return None


def other_party_label(question: str, element: dict, intent: str) -> str | None:
    """'named' when the question's own words name somebody else ('Emergency contact name'), 'bare' when only the form around
    it does (a bare 'Name' under 'Emergency contact'), None when the question is about the applicant. A person's details
    are never the applicant's Profile, and the engine holds no fact for them."""
    heading = normalize(" ".join(str(element.get(key) or "") for key in SUBJECT_KEYS))
    if PEOPLE.search(normalize(question or "")):
        return "named"
    if intent == "OTHER_PARTY" or PEOPLE.search(heading):
        return "bare"
    return None


def profile_value_problem(key: str, value: str) -> tuple[str, str] | None:
    """Would the Profile's own field accept this value? -> (strictness, message) when it would not, else None.
    ``strictness`` is ``'refuse'`` for a value that is plainly not what the field holds (an email without an @) and
    ``'format'`` for one an employer's own format may legitimately differ in (a date, a number): it is then kept for the
    application only."""
    spec = BY_KEY.get(key)
    text = (value or "").strip()
    if not spec or not text:
        return None
    if key == "email" and (len(text) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", text)):
        return "refuse", "That does not look like an email address."
    if key == "phone":
        digits = re.sub(r"\D", "", text)
        if not 7 <= len(digits) <= 17 or "@" in text or re.search(r"[^\w\s+()./#:\-]", text):
            return "refuse", "That does not look like a phone number."
    if key in {"linkedin", "github", "portfolio"} and not re.fullmatch(
            r"(?:https?://)?[A-Za-z0-9][\w.-]*\.[A-Za-z]{2,}(?:[/?#]\S*)?", text):
        return "refuse", "That does not look like a web address."
    if key == "postcode" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]{1,10}", text):
        return "refuse", "That does not look like a postcode."
    if key in {"full_name", "first_name", "last_name", "preferred_name"} and (
            len(text) > 100 or re.search(r"[\d@]", text) or not re.search(r"[^\W\d_]", text)):
        return "refuse", "That does not look like a name."
    kind = spec.get("type")
    if kind == "number" and (not re.fullmatch(r"\d+(?:\.\d+)?", text) or float(text) > 1e9):
        return "format", "It is not a plain number, as the Profile field needs."
    if kind == "month" and not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", text):
        return "format", "It is not a month written YYYY-MM, as the Profile field needs."
    if kind == "date":
        try:
            date.fromisoformat(text)
        except ValueError:
            return "format", "It is not a date written YYYY-MM-DD, as the Profile field needs."
    options = spec.get("options") or []
    if options and normalize(text) not in {normalize(o) for o in options}:
        return "format", "It is not one of the choices the Profile field offers."
    return None


@dataclass(frozen=True)
class Scope:
    """How far an answer to this question may travel."""

    kind: str  # narrative | company_specific | protected | legal | salary | factual | other_party
    protected: bool
    legal: bool
    reusable: bool  # a Profile-mappable factual answer the user may choose to reuse
    profile_key: str | None


def company_specific(question: str, company: str) -> bool:
    text = normalize(question)
    key = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if COMPANY_WORDS.search(question or ""):
        return True
    squashed = re.sub(r"[^a-z0-9]", "", text)
    return bool(len(key) >= 4 and key in squashed)


def scope_of(question: str, element: dict, intent: str, application: dict) -> Scope:
    """Classify a question for persistence and for the 'remember this?' offer. Pure and conservative."""
    kind = classify_question(question)
    disclosure = personal_disclosure_kind(question, element)
    protected = bool(disclosure) or kind in {*SENSITIVE, "work_authorization"} or intent in PROTECTED_INTENTS \
        or bool(HEALTH_ADJACENT.search(normalize(question or "")))
    legal = kind in {"legal_certification", "work_authorization", "criminal_record"} or intent in {
        "CONSENT", "GDPR", "CRIMINAL_HISTORY", "SECURITY_CLEARANCE", "BACKGROUND_CHECK", "CONFLICT_OF_INTEREST",
        "ROLE_CONFIRMATION", "WORK_AUTHORIZATION", "SPONSORSHIP", "VISA"}
    if never_saved_reason(question, element) is not None:
        # A code, a card, a bank detail, an ID number, a security answer: protected whatever else the label resembles ('Phone' on
        # a control the browser says is a card number), never reusable, never promoted to a Profile field (SAFE-14).
        return Scope("protected", True, legal, False, None)
    if other_party_label(question, element, intent) is not None:
        # Somebody else's name, number or address: never the applicant's Profile field the bare label happens to match
        # ('Name' under 'Emergency contact' is not full_name), never reusable; whatever else it is (a question about the
        # applicant's children is protected too) stays as classified.
        return Scope("other_party", protected, legal, False, None)
    narrative = intent in NARRATIVE_INTENTS or kind == "free_text"
    specific = company_specific(question, application.get("company", ""))
    spec = question_field(question, application, element)
    label = ("narrative" if narrative else "company_specific" if specific else "protected" if protected
             else "legal" if legal else "salary" if kind == "salary" else "factual")
    reusable = label == "factual" and spec is not None
    return Scope(label, protected, legal, reusable, spec["key"] if spec else None)


def field_hash(element: dict) -> str:
    """Identifies a kind of field in the local audit trail without ever storing what was typed."""
    import hashlib
    raw = "|".join(str(element.get(k) or "") for k in ("label", "type", "name"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _event_row(session_id: str, kind: str, element: dict | None, outcome: str, details: dict | None) -> tuple:
    safe = {k: v for k, v in (details or {}).items() if k not in {"value", "final_value", "text", "answer"}}
    return (uid(), session_id, kind, field_hash(element or {}), outcome,
            json.dumps(safe, ensure_ascii=False, default=str)[:2000], now())


EVENT_SQL = "INSERT INTO extension_events(id,session_id,kind,field_hash,outcome,details,created_at) VALUES(?,?,?,?,?,?,?)"


def record_event(db, session_id: str, kind: str, element: dict | None, outcome: str, details: dict | None = None,
                 *, conn=None) -> None:
    """Append one line to the local audit trail (extension_events). Never contains a typed value. With ``conn`` the line
    joins that open transaction (it is committed, or rolled back, together with the rows it describes)."""
    row = _event_row(session_id, kind, element, outcome, details)
    if conn is not None:
        conn.execute(EVENT_SQL, row)
    else:
        db.execute(EVENT_SQL, row)


def record_events(db, session_id: str, rows: list) -> None:
    """Many ``(kind, element, outcome, details)`` lines in ONE transaction: a scan of 400 controls is one write, not 400
    (each write opens a connection and waits for the database's single writer)."""
    lines = [_event_row(session_id, kind, element, outcome, details) for kind, element, outcome, details in rows]
    if lines:
        with db.transaction() as conn:
            conn.executemany(EVENT_SQL, lines)


@dataclass(frozen=True)
class Plan:
    """The decision about one correction, made BEFORE any row exists. ``refusal`` says why nothing is saved, ``code`` says
    it precisely (``never_saved_otp``, ``other_party_label``, ``ambiguous_label``, ``invalid_value`` ...)."""

    refusal: str | None = None
    value: str = ""
    skip: bool = False
    reuse: bool = False
    message: str | None = None
    scope: Scope | None = None
    code: str | None = None


def plan(question: str, element: dict, intent: str, request, explicit_remember: bool, application: dict, *,
         twins: int = 0) -> Plan:
    """What may be saved, and how far it may travel. Pure: no database access.

    ``twins`` is how many OTHER questions on the same page have exactly this label: a saved answer is remembered by its
    label alone, so with a twin it would be offered, VERIFIED, in the field it was not typed into (a second 'Phone')."""
    if request.final_values is not None:
        sent = ", ".join(str(v).strip() for v in request.final_values if str(v).strip())
    elif request.final_value is not None:
        sent = request.final_value.strip()
    else:
        sent = None
    value = strip_controls(sent).strip() if sent is not None else ""  # no NUL, no bidi override: it is typed back later
    secret = never_saved_reason(question, element, value if sent is not None else None)
    if secret is not None:
        text = ("Passwords, hidden fields and files are never saved." if secret in {"password", "hidden", "file"}
                else NEVER_SAVED_MESSAGES[secret])
        return Plan(refusal=text, code="never_saved_" + secret)
    scope = scope_of(question, element, intent, application)
    if sent is None:
        return Plan(refusal="Nothing to save: no value was sent.", scope=scope)
    if sent and not value:
        return Plan(refusal="Nothing to save: the value holds no readable text.", scope=scope)
    if request.remember == "none":
        return Plan(refusal="Not saved, as you chose.", scope=scope)
    party = other_party_label(question, element, intent)
    if party == "bare":
        return Plan(refusal="This asks for another person's details, and its name is the same as one of your own: Meridian "
                            "does not save it.", scope=scope, code="other_party_label")
    if twins:
        return Plan(refusal="Another question on this page has the same name, so Meridian cannot tell which one this answer "
                            "is for. Fill it in yourself.", scope=scope, code="ambiguous_label")
    if scope.protected and not explicit_remember:
        # Sensitive answers are saved only when the user confirmed in the prompt (an explicit `remember`).
        return Plan(refusal="Personal and legal answers are only saved when you confirm it.", scope=scope)
    problem = profile_value_problem(scope.profile_key, value) if scope.profile_key and value else None
    if problem and problem[0] == "refuse":
        return Plan(refusal=problem[1] + " Meridian did not save it.", scope=scope, code="invalid_value")
    reuse = request.remember == "reuse" and explicit_remember and scope.reusable and not problem
    message = None
    if request.remember == "reuse" and not reuse:
        message = ("Saved for this application only: " + (
            problem[1] if problem else "this kind of answer is never reused for other employers." if scope.kind != "factual"
            else "it does not match a Profile field."))
    return Plan(value=value, skip=not value, reuse=reuse, message=message, scope=scope)


def persist(db, application: dict, decision: Plan, *, question: str, question_id: str, element: dict, state: str,
            audit: dict | None = None) -> dict:
    """Save a planned correction exactly as a direct edit in Auto Apply's browser is saved. ``application`` is a real
    applications row joined with its job.

    ``audit`` (``{session_id, kind, outcome, details}``) is the line that says "this answer was saved through the
    assistant". It is written in the SAME transaction as the ``application_answers`` row and names that row
    (``answer_id``) and the question (``question_id``), so the label of an answer never depends on timing: a typed
    answer that lands between this save and any later write cannot take the label. The result also names the saved
    rows for the caller (``answer_id``, ``fact_id``); they are not part of the wire response."""
    value, scope = decision.value, decision.scope
    fact_id = remember_answer(db, application, question, value, reusable=decision.reuse, skip=decision.skip,
                              element=element, direct_edit=True)
    promoted = bool(decision.reuse and scope.profile_key and db.one(
        "SELECT 1 AS n FROM facts WHERE source=? AND value=?", ("application_profile:" + scope.profile_key, value)))
    memory = db.one("SELECT updated_at FROM browser_answers WHERE fact_id=?", (fact_id,))
    answer_id = uid()
    with db.transaction() as conn:
        from ..answer_history import supersede_answers
        supersede_answers(conn, application["id"], question, question_id)
        conn.execute("INSERT INTO application_answers(id,application_id,question,answer,fact_ids,verified,created_at) "
                     "VALUES(?,?,?,?,?,?,?)", (answer_id, application["id"], question, value, json.dumps([fact_id]), 1, now()))
        conn.execute("INSERT INTO application_answer_targets VALUES(?,?)", (answer_id, question_id))
        if audit is not None:
            trace = {"saved": True, "scope": "profile" if promoted else "application", "answer_id": answer_id,
                     "question_id": question_id, "fact_id": fact_id, "memory_at": memory["updated_at"] if memory else None}
            record_event(db, audit["session_id"], audit["kind"], element, audit["outcome"],
                         {**(audit.get("details") or {}), **trace}, conn=conn)
    return {"saved": True, "scope": "profile" if promoted else "application", "promoted_to_profile": promoted,
            "message": decision.message, "application_id": application["id"], "state": state, "answer_id": answer_id,
            "fact_id": fact_id}
