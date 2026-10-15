"""Conservative field resolution with source fact IDs, never invented candidate values."""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from functools import lru_cache

from .disclosures import disclosure_resolution


def question_identity(application_id, element, question):
    """Keep identically named questions in separate forms/education sections apart."""
    grouped = element.get('role') == 'radio' or (element.get('role') == 'checkbox' and element.get('multiple'))
    control = (element.get('field_path') or element.get('group')) if grouped else None
    identity = [application_id, element.get('document_id'), element.get('form_id'),
                element.get('section_label'), control or element.get('name') or element.get('field_path') or
                element.get('group') or element.get('node_id'), question]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=1 << 16)
def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# Longer than any label, option or question of a form: a description or a pasted paragraph is normalised once and not kept.
NORMALIZE_CACHED_LENGTH = 512


def normalize(value: str) -> str:
    """ASCII-fold, lower-case and squash every other character to one space. Pure, and the hottest function of the answer
    engine (a form of 400 controls x 60 options asks for it more than a million times): the short strings it is
    called with are memoised."""
    text = value if type(value) is str else str(value)
    return _normalized(text) if len(text) <= NORMALIZE_CACHED_LENGTH else _normalized.__wrapped__(text)


def fold(value) -> str:
    """Lower case with the accents taken off; every other character is kept (``normalize`` also drops brackets, symbols
    and punctuation, which is wrong where '(weeks)', '£' or '€' are the point)."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(value or "")))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


ALIASES = {
    "first_name": ["first name", "given name", "legal first name", "nombre", "nombre de pila", "primer nombre"],
    "last_name": ["last name", "family name", "surname", "legal last name", "apellido", "apellidos"],
    "full_name": ["full name", "name", "legal name", "your name", "nombre completo", "nombre y apellidos"],
    "email": ["email", "email address", "your email", "e mail", "correo", "correo electronico", "direccion de correo electronico",
              "tu correo electronico"],
    "phone": ["phone", "phone number", "mobile", "telephone", "mobile phone", "telefono", "telefono movil", "movil",
              "numero de telefono", "numero de movil", "celular"],
    "country": ["country", "country of residence", "current country", "pais", "pais de residencia"],
    "city": ["city", "current city", "town", "ciudad", "ciudad de residencia", "localidad"],
    "location": ["location", "current location", "where are you based", "where are you currently located", "ubicacion",
                 "ubicacion actual", "donde resides", "donde vives"],
    "address": ["address", "street address", "address line 1", "direccion", "direccion postal", "domicilio"],
    "postcode": ["postcode", "post code", "postal code", "zip code", "zip", "zip postal code", "codigo postal"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url", "perfil de linkedin", "url de linkedin"],
    "github": ["github", "github url", "github profile", "perfil de github", "url de github"],
    "portfolio": ["portfolio", "portfolio url", "website", "personal website", "portafolio", "sitio web", "pagina web", "web personal"],
    "university": ["university", "school", "school name", "institution", "universidad", "centro de estudios"],
    "degree": ["degree", "degree type", "qualification", "titulacion", "grado", "titulo"],
    "field_of_study": ["field of study", "major", "subject", "especialidad", "campo de estudio"],
    "graduation_year": ["graduation year", "year of graduation", "what is your graduation year", "expected graduation year",
                        "ano de graduacion", "ano de finalizacion"],
    "current_company": ["current company", "current employer", "current organisation", "current organization", "present employer",
                        "present company", "empresa actual", "empleador actual"],
    "current_title": ["current title", "current job title", "current position", "current role", "present job title", "present position",
                      "job title", "puesto actual", "cargo actual"],
    "expected_salary": ["expected salary", "salary expectation", "salary expectations", "desired salary", "what are your salary expectations",
                        "pretensiones salariales", "expectativas salariales", "salario deseado", "salario esperado"],
    "availability": ["availability", "available from", "earliest start date", "start date", "disponibilidad",
                     "fecha de incorporacion", "fecha de inicio"],
    "notice_period": ["notice period", "what is your notice period", "what is your current notice period", "preaviso",
                      "periodo de preaviso", "cual es tu periodo de preaviso"],
    "work_authorization": ["work authorization", "are you legally authorized to work", "right to work", "autorizacion para trabajar",
                           "permiso de trabajo"],
    "sponsorship_required": ["sponsorship required", "do you require sponsorship", "will you require visa sponsorship",
                             "do you require sponsorship now or in the future to work in the job s location",
                             "necesitas patrocinio de visado", "patrocinio de visado"],
    "relocation": ["relocation", "willing to relocate", "are you willing to relocate", "disponibilidad para reubicarte",
                   "estas dispuesto a reubicarte"],
}

# The second line of each pattern is its Spanish wording (the text is ASCII-folded first: 'género' is 'genero').
SENSITIVE = {
    "disability": r"disabilit|medical|health condition|neurodivergen|reasonable adjustments"
                  r"|discapacidad|minusvalia|problema de salud|salud mental|condicion (?:medica|de salud)|neurodiversidad|ajustes razonables|adaptaciones razonables",
    "ethnicity": r"ethnic|racial|race\b"
                 r"|\betnia\b|etnic|origen racial|\braza\b",
    "gender": r"gender|\bsex\b|sexual orientation|pronoun"
              r"|\bgenero\b|\bsexo\b|orientacion sexual|pronombre|identidad de genero",
    "personal_details": r"\bage\b|date of birth|birth date"
                        r"|\bedad\b|fecha de nacimiento|\bnacimiento\b|\bdni\b|\bnie\b|\bnif\b|\bcif\b|pasaporte|seguridad social"
                        r"|documento (?:de identidad|nacional)|numero de identificacion|identificacion fiscal|tarjeta de residencia",
    "criminal_record": r"criminal|convict|arrest"
                       r"|antecedentes (?:penales|judiciales|policiales)|certificado de antecedentes|condena|\bdelito|\bpenal(?:es)?\b|detenid[oa]|procesad[oa]",
    "demographic": r"veteran|demographic|religio|marital"
                   r"|veterano|demograf|religi|estado civil|creencias|afiliacion (?:politica|sindical)|servicio militar",
    "legal_certification": r"certif|attest|agree|consent|privacy|terms (?:and|of)|acknowledge"
                           r"|certifico|atesto|consentimiento|consiento|\bacepto\b|privacidad|proteccion de datos|\brgpd\b|\blopd\b"
                           r"|terminos y condiciones|terminos de uso|condiciones de uso|aviso legal|\bautorizo\b|\bdeclaro\b|confirmo que|he leido|estoy de acuerdo|veracidad",
}


def classify_question(question: str) -> str:
    text = normalize(question)
    for category, pattern in SENSITIVE.items():
        if re.search(pattern, text):
            return category
    if re.search(r"visa|sponsor|authoriz|right to work|eligible to work|nationality|citizen"
                 r"|patrocin|autorizacion para trabajar|derecho a trabajar|permiso de trabajo|nacionalidad|ciudadania", text):
        return "work_authorization"
    if re.search(r"salary|compensation|pay expectation|salari(?:o|os|al|ales)\b|\bsueldo|pretension(?:es)? salarial|retribucion", text):
        return "salary"
    if re.search(r"why |motivat|tell us|describe|cover letter|how are you.*using ai|anything else.*share"
                 r"|por que |motivacion|cuentanos|describe|carta de presentacion|algo mas que", text):
        return "free_text"
    if re.search(r"email|phone|address|postcode|correo|telefon|\bmovil\b|direccion|codigo postal|domicilio", text):
        return "contact"
    return "identity" if re.search(r"name|\bnombre\b|apellido", text) else "unknown"


def fact_value(fact: dict) -> str:
    value = fact.get("value", "")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        # The core API returns decoded values; raw DB adapters can return JSON strings.
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return json.dumps(value, ensure_ascii=False) if value else ""


def approved_facts(facts: list[dict]) -> list[dict]:
    # Typed knowledge is resolved by CandidateEngine, never by flat-key matching.
    return [f for f in facts if f.get('category') != 'knowledge' and (f.get("verification_status") == "verified" or f.get("locked") is True or
                                f.get("locked") == 1) and fact_value(f)]


@lru_cache(maxsize=1 << 14)
def _canonical_key(label: str) -> str | None:
    # One space for any run of whitespace, before any pattern: the 'required' suffix below is quadratic on a long run of
    # spaces (a hostile label of 20,000 of them held the whole process for half a minute).
    from ..intelligence.plain import label_core  # (lazy: the intelligence package imports this module)
    label = " ".join(label_core(label[:4000]).split())  # a non-string is still a TypeError, as it always was
    norm = normalize(re.sub(r"(?:\s+\(?(?:required|optional)\)?|\((?:required|optional)\))\s*$", "", label, flags=re.I))
    for key, aliases in ALIASES.items():
        if norm == normalize(key) or norm in aliases:
            return key
    # A fact that has no entity of its own (a notice period, a willingness to relocate) is found by what the label ASKS, not by one
    # wording of it: any plain question about it, in either language, is the same fact
    if re.search(r"relocat|reubic|traslad|mudar|notice|preaviso|aviso previo", norm):
        from ..intelligence import plain  # (lazy, as above)
        from ..intelligence.semantics import interpret
        intent = interpret(label, {})["intent"]
        reading = plain.read(label, {}, intent) if intent in FLAT_FACT_INTENTS else None
        # (a willingness to relocate answers a plain yes/no that names no place; a notice period answers the label that asks for it, not a
        # yes/no about a threshold: those are compared with the stored days by the engine)
        if reading and reading["ok"] and (reading["form"] == "label" and not UNIT_WORDS.search(norm) if intent == "NOTICE_PERIOD"
                                          else not reading["slots"].get("place_phrases")):
            return FLAT_FACT_INTENTS[intent]
    return None


FLAT_FACT_INTENTS = {"RELOCATION": "relocation", "NOTICE_PERIOD": "notice_period"}
# a notice period asked in a UNIT ('Notice period (weeks)') is a number of that unit: the stored '3 months' is not one, and is never converted
UNIT_WORDS = re.compile(r"\b(?:days?|weeks?|months?|years?|dias?|semanas?|meses|mes|anos?)\b")


def canonical_key(label: str) -> str | None:
    return _canonical_key(label) if type(label) is str and len(label) <= NORMALIZE_CACHED_LENGTH \
        else _canonical_key.__wrapped__(label)


def resolve_answer(question: str, facts: list[dict], *, hints: list[str] | None = None,
                   policies: dict | None = None) -> dict:
    corrections = [f for f in facts if f.get('manual_correction') and f.get('profile_explicit') and
                   f.get('verification_status') == 'verified' and
                   (normalize(question) == normalize(f.get('key', '')) or
                    normalize(question) in f.get('profile_aliases', []) or
                    (canonical_key(question) and canonical_key(question) == canonical_key(f.get('key', ''))))]
    if corrections:
        priority = max(f.get('answer_priority', 0) for f in corrections)
        corrections = [f for f in corrections if f.get('answer_priority', 0) == priority]
        if len({fact_value(f) for f in corrections}) == 1:
            answer = fact_value(corrections[0])
            return {'question': question, 'kind': classify_question(question), 'answer': answer or None,
                    'leave_blank': not bool(answer), 'fact_ids': [f['id'] for f in corrections],
                    'verified': True, 'confidence': 1.0, 'source': 'user_edit'}
    disclosure = disclosure_resolution(question, policies=policies)
    if disclosure:
        return disclosure
    category = classify_question(question)
    result = {"question": question, "kind": category, "answer": None, "fact_ids": [], "verified": False,
              "confidence": 0.0, "leave_blank": False}
    allowed = approved_facts(facts)
    # These markers are added only by the trusted, application-scoped knowledge
    # loader, never read from employer DOM or a model response.
    explicit = [f for f in allowed if f.get("profile_explicit") and
                (normalize(question) == normalize(f.get("key", "")) or
                 normalize(question) in f.get("profile_aliases", []) or
                 (canonical_key(question) and canonical_key(question) == canonical_key(f.get("key", ""))))]
    if explicit:
        priority = max(f.get("answer_priority", 0) for f in explicit)
        explicit = [f for f in explicit if f.get("answer_priority", 0) == priority]
        if len({fact_value(f) for f in explicit}) == 1:
            return {**result, "answer": fact_value(explicit[0]), "fact_ids": [f["id"] for f in explicit],
                    "verified": True, "confidence": 1.0}
    policies = policies or {}
    question_policies = policies.get("questions", {})
    policy = (question_policies.get(normalize(question), policies.get(category, {}))
              if isinstance(question_policies, dict) else policies.get(category, {}))
    if isinstance(policy, str):
        policy = {"action": policy}
    if category in SENSITIVE:
        action = policy.get("action", "ask_me")
        if action == "leave_blank":
            return {**result, "leave_blank": True, "verified": True, "confidence": 1.0}
        if action == "prefer_not_to_say" and category != "legal_certification":
            return {**result, "answer": "Prefer not to say", "verified": True, "confidence": 1.0,
                    "policy": "prefer_not_to_say"}
        if action != "saved_answer":
            return result
        allowed = [f for f in allowed if str(f.get("id")) == str(policy.get("fact_id"))]
        if len(allowed) != 1:
            return result
        fact = allowed[0]
        return {**result, "answer": fact_value(fact), "fact_ids": [fact["id"]], "verified": True, "confidence": 1.0}
    labels = [question, *(hints or [])]
    norms = {normalize(s) for s in labels if s}
    keys = {canonical_key(s) for s in labels if s} - {None}
    exact = [f for f in allowed if normalize(f.get("key", "")) in norms or
             normalize(f.get("question", "")) in norms or canonical_key(f.get("key", "")) in keys]
    # Contradictory or repeated education/employment entries require explicit selection.
    values = {fact_value(f) for f in exact}
    if len(values) == 1:
        return {**result, "answer": next(iter(values)), "fact_ids": [f["id"] for f in exact],
                "verified": True, "confidence": 1.0}
    if keys == {"full_name"}:
        first = resolve_answer("first name", allowed)
        last = resolve_answer("last name", allowed)
        if first["answer"] and last["answer"]:
            return {**result, "answer": first["answer"] + " " + last["answer"],
                    "fact_ids": first["fact_ids"] + last["fact_ids"], "verified": True, "confidence": 1.0}
    return result


def phone_matches(expected, element):
    wanted = re.sub(r'\D', '', str(expected or ''))
    actual = re.sub(r'\D', '', str(element.get('value') or ''))
    if not 7 <= len(wanted) <= 15 or not actual:
        return False
    code = str(element.get('phone_country_code') or '')
    if wanted == actual:
        return not code or not str(expected).startswith('+') or wanted.startswith(code)
    # A national number is equivalent only with an observed selected dial code
    # belonging to this specific telephone widget. A suffix alone is insufficient.
    return bool(str(expected).startswith('+') and re.fullmatch(r'\d{1,4}', code)
                and (wanted == code + actual or (actual.startswith('0') and wanted == code + actual[1:])))


def option_for(answer: str, options: list[dict], *, question: str = "") -> dict | None:
    norm = normalize(answer)
    # An enum's machine value does not establish the meaning of its label.
    # In particular, code "Yes" can represent an unrestricted right to work,
    # a particular visa route, or a separate consent. A Boolean fact supports
    # the exact question, never those additional assertions. Full supported
    # label answers can still match directly; plain Yes/No controls retain
    # their normal mapping.
    labels = [o for o in options if not o.get("disabled")]
    exact = [o for o in labels if norm == normalize(o.get("label", ""))]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # 'Sí' is the Spanish yes: normalised, 'si'. A form in Spanish answers 'Yes' with its own word, never with a guess.
    boolean_labels = {"yes": {"yes", "true", "si"}, "true": {"yes", "true", "si"},
                      "no": {"no", "false"}, "false": {"no", "false"}}
    if norm in boolean_labels:
        equivalents = [o for o in labels if normalize(o.get("label", "")) in boolean_labels[norm]]
        # These labels repeat the consent decision already resolved for the
        # exact question. Extra purposes or eligibility assertions still fail.
        if not equivalents and re.search(r"\bconsent\b|\bopt in\b|\bopt out\b|consentimiento|\bacepto\b|\bautorizo\b", normalize(question)):
            consent_labels = {"yes": {"yes i consent", "i consent", "si acepto", "acepto", "si doy mi consentimiento",
                                      "doy mi consentimiento", "si consiento", "consiento"},
                              "no": {"no i do not consent", "i do not consent", "no acepto", "no doy mi consentimiento",
                                     "no consiento"}}
            polarity = "yes" if norm in {"yes", "true"} else "no"
            equivalents = [o for o in labels if normalize(o.get("label", "")) in consent_labels[polarity]]
        return equivalents[0] if len(equivalents) == 1 else None
    matches = [o for o in labels if norm == normalize(o.get("value", ""))]
    if len(matches) == 1:
        return matches[0]
    if canonical_key(question) in {"location", "city"} or re.search(r'\b(?:location|city|town|residence)\b', normalize(question)):
        # Geocoders expand a verified city/country into city/county/region/country.
        # Require every supplied component, including country, and a unique result.
        aliases = {"uk": "united kingdom", "gb": "united kingdom", "u k": "united kingdom"}
        parts = [aliases.get(normalize(p), normalize(p)) for p in answer.split(',') if normalize(p)]
        if len(parts) >= 2:
            matches = [o for o in options if not o.get("disabled") and set(parts).issubset({
                aliases.get(normalize(p), normalize(p)) for p in o.get("label", "").split(',')})]
            if len(matches) == 1:
                return matches[0]
        city_text = answer.split(',', 1)[0]
        city_text = re.sub(r'\s*(?:\([^)]*\)|\b(?:UK|GB|United Kingdom|Great Britain))\s*$', '', city_text, flags=re.I)
        city = normalize(city_text)
        country = next((aliases.get(normalize(p), normalize(p)) for p in answer.split(',')[1:]
                        if normalize(p) in {'uk', 'gb', 'u k', 'united kingdom', 'great britain'}), None)
        if not country and re.search(r'\b(?:UK|GB|United Kingdom|Great Britain)\b', answer, re.I):
            country = 'united kingdom'
        if city and country:
            matching = [o for o in options if not o.get('disabled') and
                        re.match(r'^' + re.escape(city) + r'(?:\b|$)', normalize(o.get('label', '')))]
            # A geocoder may offer the enclosing metropolitan region rather
            # than repeat the city. Country evidence remains mandatory.
            matching += [o for o in options if not o.get('disabled') and o not in matching and
                         re.match(r'^greater ' + re.escape(city) + r'(?:\b|$)', normalize(o.get('label', '')))]
            uk = [o for o in matching if re.search(r'\b(?:united kingdom|uk|great britain|england|scotland|wales)\b',
                                                  normalize(o.get('label', '')))]
            if uk:
                return uk[0]  # Observed order among equivalent city/country results.
            plain = [o for o in matching if normalize(o.get('label', '')) == city]
            if len(plain) == 1:
                return plain[0]
    if norm == "prefer not to say":
        matches = [o for o in options if not o.get("disabled") and
                   normalize(o.get("label", "")) in {
                       "decline to self identify", "i do not wish to answer", "decline to answer",
                       "prefiero no decirlo", "prefiero no responder", "prefiero no contestar", "prefiero no indicarlo",
                       "prefiero no especificar", "no deseo responder", "no deseo contestar", "no quiero responder"}]
        if len(matches) == 1:
            return matches[0]
    return None


def selected_choice_matches(question: str, answer: str, value: str) -> bool:
    from ..intelligence.formatting import choice
    return choice(answer, [{"label": value, "value": value}], question=question) is not None


def dropdown_search_value(resolution: dict) -> str:
    answer = resolution['answer']
    if resolution.get('canonical_intent') == 'LANGUAGE' and re.match(r'^fluent\b', answer, re.I):
        return 'Fluent'
    if resolution.get('canonical_intent') == 'LOCATION' or canonical_key(resolution.get('question', '')) in {'location', 'city'}:
        # Search the locality; use the full verified city/country to choose
        # among results. Some geocoders reject a comma-separated search query.
        return re.sub(r'\s*(?:\([^)]*\)|\b(?:UK|GB|United Kingdom|Great Britain))\s*$', '', answer.split(',', 1)[0], flags=re.I).strip()
    return answer
