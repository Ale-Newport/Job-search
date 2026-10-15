"""'Which country's dial code?': one reading of those questions for Auto Apply and the browser assistant.

``Phone country code``, ``Country Phone Code`` (Workday), ``Dialling code``, ``Phone prefix``, ``Prefijo telefónico`` ... all
ask for the CODE OF A COUNTRY in a menu of countries (``United Kingdom (+44)``), never for the number. The answer engine
reads any of them as a ``CONTACT`` question and answers with the whole verified number, so every caller must take these
questions out of the engine's hands: the option that carries the dial code of the verified number (and, when several
could, the one of the country the candidate lives in) is the answer, and without such an option there is NO answer.
"""

from __future__ import annotations

import re

from ..intelligence.formatting import choice as option_for
from .answers import normalize

# Wordings of the question, on text that went through ``normalize`` (ASCII, lower case). A bare 'country code' is NOT here:
# it asks for the ISO code of the country (GB) and the engine answers it as such.
DIAL_CODE = re.compile(
    r"\bcountry (?:phone|telephone|calling|dialling|dialing|mobile|cell) code\b"
    r"|\b(?:phone|telephone|mobile|cell)(?: number)? country code\b|\b(?:mobile|cell) country code\b"
    r"|\bcountry code (?:for |of |in )?(?:the |my |your )?(?:phone|telephone|mobile|cell)\b"
    r"|\b(?:calling|dialling|dialing) code\b|\b(?:international )?dial code\b"
    r"|\b(?:phone|telephone|mobile|cell|tel) (?:code|prefix)\b|\bcountry prefix\b|\binternational prefix\b"
    # Spanish: 'Prefijo telefónico', 'Código de país del teléfono', 'Prefijo del país', 'Código de marcación'
    r"|\bprefijo (?:telefonico|internacional|del pais|de pais|movil|de llamada|de marcacion)\b"
    r"|\bcodigo (?:telefonico|de marcacion|de llamada|internacional)\b"
    r"|\bcodigo (?:de pais |del pais )?(?:del |de )?(?:telefono|movil)\b")


def is_dial_code_question(question: str) -> bool:
    return bool(DIAL_CODE.search(normalize(question)))


def refusal(phone: dict, question: str) -> dict:
    """No option carries the code: the verified number is NOT an answer to a question about a country's code."""
    return {**phone, "answer": None, "form_value": None, "question": question, "fact_ids": [], "verified": False,
            "inferred": False, "confidence": 0, "confidence_level": "UNKNOWN", "requires_review": True,
            "root_cause": "BAD_ENUM_MAPPING", "reason": "No option carries the dial code of your verified phone number",
            "candidate_value": None}


def resolve_dial_code(question: str, options: list[dict], answer_question, job: dict, *, closed: str = "continue") -> dict | None:
    """The resolution of a dial-code question, or None when ``question`` is not one (or, with ``closed="continue"``,
    when the control shows no options yet: a closed custom dropdown, which Auto Apply opens and resolves again).

    ``answer_question(question, field, job)`` is ``CandidateEngine.answer_question``. With ``closed="refuse"`` a control
    without options gets no answer at all (the assistant cannot open a menu to look again, and the whole number is
    never a dial code)."""
    if not is_dial_code_question(question):
        return None
    phone = answer_question("Phone", {"type": "tel"}, job)
    country = answer_question("Country of residence", {"type": "text"}, job)
    digits = re.sub(r"\D", "", str(phone.get("answer") or ""))
    carrying = []
    if str(phone.get("answer") or "").startswith("+"):
        for option in options or []:
            code = re.search(r"\(\+(\d{1,4})\)", option.get("label", ""))
            if code and digits.startswith(code[1]):
                carrying.append(option)
    selected = carrying[0] if len(carrying) == 1 else None
    if not selected and country.get("answer"):
        undecorated = [{**o, "label": re.sub(r"\s*\(\+\d+\)", "", o["label"]), "original": o} for o in carrying]
        match = option_for(str(country["answer"]), undecorated, question="Country of residence")
        selected = match.get("original") if match else None
    if selected:
        return {**phone, "answer": selected["label"], "form_value": selected["label"], "question": question,
                "fact_ids": list(dict.fromkeys(phone.get("fact_ids", []) + country.get("fact_ids", []))),
                "inference_rules": ["verified_phone_prefix_and_observed_country_option"]}
    if options or closed == "refuse":
        return refusal(phone, question)
    return None
