"""Spanish application forms: the answer engine understands them in the same architecture as the English ones.

The user applies to jobs from Spain and the UK. Until now every Spanish label ('Nombre', 'Correo electrónico', 'Pretensiones
salariales', '¿Tienes autorización para trabajar...?', 'País de residencia') fell to "needs you" and a consent such as
'He leído y acepto la política de privacidad' was not even recognised as consent. The keyword rules of
``intelligence/semantics.py``, ``automation/answers.py`` (aliases, sensitive categories) and ``automation/disclosures.py``
now carry the Spanish wording next to the English one, with ONE precedence; every reading below was verified by hand
against the synthetic candidate's record (``fixtures/extension/harness.py``)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import seed_candidate  # noqa: E402
from jobagent.automation.answers import canonical_key, classify_question  # noqa: E402
from jobagent.automation.disclosures import disclosure_profile_key, personal_disclosure_kind  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine  # noqa: E402
from jobagent.intelligence.semantics import interpret  # noqa: E402
from jobagent.intelligence.store import policy  # noqa: E402

UK = {"country": "United Kingdom", "company": "Acme", "location": "London, UK"}
SPAIN = {"country": "Spain", "company": "Acme", "location": "Madrid, España"}

# (label, field type, intent, attribute, answer for the synthetic candidate on a UK vacancy)
READINGS = [
    # identity
    ("Nombre", "text", "IDENTITY", "first_name", "Test"), ("Apellidos", "text", "IDENTITY", "last_name", "Candidate"),
    ("Apellido", "text", "IDENTITY", "last_name", "Candidate"), ("Nombre de pila", "text", "IDENTITY", "first_name", "Test"),
    ("Nombre completo", "text", "IDENTITY", "full_name", "Test Candidate"),
    ("Nombre y apellidos", "text", "IDENTITY", "full_name", "Test Candidate"),
    ("Nombre preferido", "text", "IDENTITY", "preferred_name", None), ("Segundo nombre", "text", "IDENTITY", "middle_name", None),
    ("Primer apellido", "text", "IDENTITY", "first_surname", None), ("Segundo apellido", "text", "IDENTITY", "second_surname", None),
    ("Apellidos y nombre", "text", "IDENTITY", "surname_first_name", None), ("Nombre legal", "text", "IDENTITY", "ambiguous_name", None),
    ("Fecha de nacimiento", "date", "IDENTITY", "date_of_birth", None), ("Edad", "number", "IDENTITY", "age", None),
    ("País de nacimiento", "text", "IDENTITY", "birth_place", None),
    # contact and links
    ("Correo electrónico", "email", "CONTACT", "email", "test@example.test"),
    ("Dirección de correo electrónico", "email", "CONTACT", "email", "test@example.test"),
    ("Teléfono", "tel", "CONTACT", "phone", "+441234567890"), ("Teléfono móvil", "tel", "CONTACT", "phone", "+441234567890"),
    ("Número de teléfono", "tel", "CONTACT", "phone", "+441234567890"), ("Móvil", "tel", "CONTACT", "phone", "+441234567890"),
    ("Perfil de LinkedIn", "url", "LINKEDIN", "institution", "https://example.test/linkedin"),
    ("GitHub", "url", "GITHUB", "institution", "https://example.test/github"),
    ("Portafolio", "url", "PORTFOLIO", "institution", "https://example.test/portfolio"),
    ("Sitio web", "url", "PORTFOLIO", "institution", "https://example.test/portfolio"),
    # place
    ("País", "text", "LOCATION", "country", "United Kingdom"), ("País de residencia", "text", "LOCATION", "country", "United Kingdom"),
    ("Ciudad", "text", "LOCATION", "city", "London"), ("Ciudad de residencia", "text", "LOCATION", "city", "London"),
    ("¿Dónde resides?", "text", "LOCATION", "location", "London, United Kingdom"),
    ("¿Dónde estás ubicado/a?", "text", "LOCATION", "location", "London, United Kingdom"),
    ("Ubicación", "text", "LOCATION", "location", "London, United Kingdom"), ("Provincia", "text", "LOCATION", "region", None),
    ("Dirección", "text", "ADDRESS", "address", None), ("Código postal", "text", "ADDRESS", "postcode", None),
    ("Domicilio", "text", "ADDRESS", "address", None), ("Nacionalidad", "text", "NATIONALITY", "institution", "Spanish"),
    ("Ciudadanía", "text", "NATIONALITY", "institution", "Spanish"),
    # education
    ("Universidad", "text", "EDUCATION", "institution", "South University"),
    ("Centro de estudios", "text", "EDUCATION", "institution", "South University"),
    ("Titulación", "text", "DEGREE", "degree", "MSc Artificial Intelligence"), ("Grado", "text", "DEGREE", "degree", "BSc Computer Science"),
    ("Especialidad", "text", "DEGREE", "subject", "Artificial Intelligence"),
    ("Nivel de estudios", "text", "DEGREE", "level", "Bachelor's degree"), ("Nivel educativo", "text", "DEGREE", "level", "Bachelor's degree"),
    ("Año de graduación", "text", "GRADUATION", "institution", "2027"),
    ("¿Cuándo terminas tus estudios?", "text", "GRADUATION", "institution", "27 September 2027"),
    ("Nota media", "text", "GRADE", "institution", "79.94"),
    # availability, notice, salary
    ("Fecha de incorporación", "date", "START_DATE", "institution", "2027-09-28"),
    ("Disponibilidad", "date", "START_DATE", "institution", "2027-09-28"),
    ("¿Cuándo podrías incorporarte?", "date", "START_DATE", "institution", "2027-09-28"),
    ("Preaviso", "text", "NOTICE_PERIOD", "institution", "None"), ("¿Cuál es tu periodo de preaviso?", "text", "NOTICE_PERIOD", "institution", "None"),
    ("Pretensiones salariales", "text", "SALARY", "institution", "60000"), ("Expectativas salariales", "text", "SALARY", "institution", "60000"),
    ("Salario deseado", "text", "SALARY", "institution", "60000"), ("Salario bruto anual", "text", "SALARY", "institution", "60000"),
    ("Salario actual", "text", "SALARY", "institution", None),
    # work authorisation, sponsorship, relocation
    ("¿Tienes autorización para trabajar en el Reino Unido?", "text", "WORK_AUTHORIZATION", "institution", "Yes"),
    ("¿Tienes permiso de trabajo?", "text", "WORK_AUTHORIZATION", "institution", "Yes"),
    ("¿Estás legalmente autorizado para trabajar en el Reino Unido?", "text", "WORK_AUTHORIZATION", "institution", "Yes"),
    ("¿Necesitas patrocinio de visado?", "text", "SPONSORSHIP", "institution", "No"),
    ("¿Necesitarás patrocinio de visado ahora o en el futuro?", "text", "SPONSORSHIP", "institution", "No"),
    ("¿Requieres sponsorship?", "text", "SPONSORSHIP", "institution", "No"),
    ("¿Estás dispuesto a reubicarte?", "text", "RELOCATION", "institution", None),
    ("¿Estarías dispuesto a mudarte de ciudad?", "text", "RELOCATION", "institution", None),
    ("¿Puedes trabajar de forma presencial?", "text", "OFFICE_POLICY", "institution", None),
    ("Disponibilidad para viajar", "text", "TRAVEL", "institution", None),
    ("¿Cómo conociste esta oferta?", "text", "SOURCE_OF_APPLICATION", "institution", None),
    ("¿Te ha recomendado algún empleado?", "text", "EMPLOYEE_REFERRAL", "institution", None),
    ("¿Has trabajado antes en esta empresa?", "text", "PREVIOUS_EMPLOYMENT", "institution", None),
    # protected and sensitive: never answered without a confirmed answer (see below)
    ("Género", "text", "DEMOGRAPHIC", "institution", None), ("Sexo", "text", "DEMOGRAPHIC", "institution", None),
    ("Pronombres", "text", "DEMOGRAPHIC", "institution", None), ("Orientación sexual", "text", "DEMOGRAPHIC", "institution", None),
    ("Origen racial o étnico", "text", "DEMOGRAPHIC", "institution", None), ("Etnia", "text", "DEMOGRAPHIC", "institution", None),
    ("Religión", "text", "DEMOGRAPHIC", "institution", None), ("Estado civil", "text", "DEMOGRAPHIC", "institution", None),
    ("¿Tienes alguna discapacidad?", "text", "DEMOGRAPHIC", "institution", None),
    ("Grado de discapacidad", "text", "DEMOGRAPHIC", "institution", None),
    ("¿Tienes antecedentes penales?", "text", "CRIMINAL_HISTORY", "institution", None),
    ("Certificado de antecedentes penales", "text", "CRIMINAL_HISTORY", "institution", None),
    ("He leído y acepto la política de privacidad", "checkbox", "CONSENT", "privacy_acknowledgement", None),
    ("Acepto el tratamiento de mis datos personales", "checkbox", "CONSENT", "data_processing", None),
    ("Acepto recibir comunicaciones comerciales", "checkbox", "CONSENT", "future_recruiting_contact", None),
    ("Declaro que la información proporcionada es veraz", "checkbox", "CONSENT", "privacy_acknowledgement", None),
    ("Acepto los términos y condiciones", "checkbox", "CONSENT", "terms_acknowledgement", None),
    ("Autorizo la verificación de mis antecedentes", "checkbox", "CONSENT", "consent", None),
    ("Doy mi consentimiento al tratamiento de datos (RGPD)", "checkbox", "CONSENT", "data_processing", None),
    # somebody else
    ("Nombre del contacto de emergencia", "text", "OTHER_PARTY", "institution", None),
    ("Teléfono del contacto de emergencia", "tel", "OTHER_PARTY", "institution", None),
    ("Nombre de la empresa", "text", "OTHER_PARTY", "institution", None), ("Correo de la referencia", "email", "OTHER_PARTY", "institution", None),
    ("Nombre del cónyuge", "text", "OTHER_PARTY", "institution", None), ("Nombre del puesto", "text", "OTHER_PARTY", "institution", None),
    # purpose of a narrative
    ("¿Por qué quieres trabajar en Acme Robotics?", "textarea", "MOTIVATION", "institution", None),
    ("Carta de presentación", "textarea", "COVER_LETTER", "institution", None),
    ("Cuéntanos algo más sobre ti", "textarea", "ADDITIONAL_CONTEXT", "institution", None),
]
YES_NO = ["¿Tienes autorización para trabajar en el Reino Unido?", "¿Necesitas patrocinio de visado?", "¿Tienes alguna discapacidad?",
          "¿Estás dispuesto a reubicarte?", "¿Has trabajado antes en esta empresa?", "¿Tienes antecedentes penales?",
          "¿Puedes trabajar de forma presencial?", "¿Resides en el Reino Unido?", "¿Eres mayor de 18 años?", "Has trabajado antes con nosotros"]
NOT_YES_NO = ["Nombre", "País de residencia", "Pretensiones salariales", "¿Cómo conociste esta oferta?", "¿Dónde resides?",
              "¿Cuándo podrías incorporarte?", "¿Por qué quieres trabajar aquí?", "Esta oferta es para Madrid"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    return seed_candidate(Database(tmp_path / "meridian.sqlite3"))


def ask(db, label, kind="text", job=UK, **field):
    return CandidateEngine(db, as_of="2026-10-14").answer_question(label, {"type": kind, **field}, dict(job))


def options(*labels):
    return [{"label": label, "value": label} for label in labels]


@pytest.mark.parametrize("label,kind,intent,attribute,answer", READINGS)
def test_a_spanish_question_is_understood_and_answered_from_the_verified_profile_or_not_at_all(db, label, kind, intent, attribute, answer):
    reading = interpret(label, {"type": kind})
    assert (reading["intent"], reading["attribute"] if attribute != "institution" else "institution") == (intent, attribute), reading
    result = ask(db, label, kind)
    assert result["canonical_intent"] == intent
    assert result["answer"] == answer, (label, result["answer"], result.get("root_cause"))
    if answer is None:
        assert result["verified"] is False and result["fact_ids"] == []


def test_the_table_is_what_it_claims_to_be():
    assert len(READINGS) >= 95 and len({r[0] for r in READINGS}) == len(READINGS)
    intents = {r[2] for r in READINGS}
    assert intents >= {"IDENTITY", "CONTACT", "LOCATION", "ADDRESS", "NATIONALITY", "EDUCATION", "DEGREE", "GRADUATION", "START_DATE",
                       "NOTICE_PERIOD", "SALARY", "WORK_AUTHORIZATION", "SPONSORSHIP", "RELOCATION", "DEMOGRAPHIC", "CRIMINAL_HISTORY",
                       "CONSENT", "OTHER_PARTY"}


@pytest.mark.parametrize("label", YES_NO)
def test_a_spanish_yes_no_question_is_one(label):
    assert interpret(label, {})["boolean"] is True


@pytest.mark.parametrize("label", NOT_YES_NO)
def test_other_spanish_questions_are_not_yes_no_questions(label):
    assert interpret(label, {})["boolean"] is False


def test_si_is_the_spanish_yes_and_a_form_in_spanish_gets_its_own_word(db):
    for label, wanted in (("¿Tienes autorización para trabajar en el Reino Unido?", "Sí"), ("¿Necesitas patrocinio de visado?", "No")):
        assert ask(db, label, "radio", role="radio", options=options("Sí", "No"))["answer"] == wanted
        assert ask(db, label, "radio", role="radio", options=options("Yes", "No"))["answer"] == ("Yes" if wanted == "Sí" else "No")
    assert ask(db, "País de residencia", "select-one", tag="select", role="combobox",
               options=options("Seleccionar", "España", "Francia", "Reino Unido", "Estados Unidos"))["answer"] == "Reino Unido"
    assert ask(db, "Nacionalidad", options=options("Británica", "Española", "Francesa"))["answer"] == "Española"
    assert ask(db, "Nivel de estudios", options=options("Bachillerato", "Grado universitario", "Máster"))["answer"] == "Grado universitario"
    assert ask(db, "Preaviso", options=options("Ninguno", "1 semana", "1 mes"))["answer"] == "Ninguno"


# --------------------------------------------------------------------------------------------- sensitive and protected
SENSITIVE_QUESTIONS = ["Género", "Sexo", "Pronombres", "Orientación sexual", "Origen racial o étnico", "Etnia", "Raza", "Religión",
                       "¿Tienes alguna discapacidad?", "Discapacidad", "Estado civil", "Fecha de nacimiento", "Edad",
                       "¿Tienes antecedentes penales?", "DNI / NIE", "NIF", "Pasaporte", "Número de la Seguridad Social",
                       "He leído y acepto la política de privacidad", "Acepto el tratamiento de mis datos personales",
                       "Consentimiento RGPD", "Declaro que la información es veraz", "Autorizo la verificación de mis antecedentes"]


@pytest.mark.parametrize("label", SENSITIVE_QUESTIONS)
def test_a_sensitive_spanish_question_is_recognised_as_sensitive_in_every_layer(db, label):
    from jobagent.automation.answers import SENSITIVE
    assert classify_question(label) in SENSITIVE, (label, classify_question(label))
    personal = {"Género", "Sexo", "Pronombres", "Orientación sexual", "Origen racial o étnico", "Etnia", "Raza", "Religión",
                "¿Tienes alguna discapacidad?", "Discapacidad", "Estado civil", "Fecha de nacimiento", "Edad"}
    if label in personal:
        assert personal_disclosure_kind(label), label
    result = ask(db, label)
    assert result["answer"] is None and result["verified"] is False, (label, result["answer"])  # nothing confirmed: nothing offered


def test_the_confirmed_disclosure_of_a_spanish_question_is_the_one_attribute_it_asks_for(db):
    assert [disclosure_profile_key(q) for q in ("Género", "Sexo", "Pronombres", "Orientación sexual", "Origen racial o étnico", "Religión",
                                                "Discapacidad", "Fecha de nacimiento", "Edad", "Salud mental")] == [
        "gender", "sex", "pronouns", "sexual_orientation", "ethnicity", "religion", "disability", "date_of_birth", "age",
        "mental_health"]
    # a broad or historical wording asks for more than the one confirmed attribute: never answered from it
    for label in ("¿Has tenido alguna vez una discapacidad?", "Género al nacer", "Describe tu condición médica",
                  "¿Tienes alguna condición médica o discapacidad?"):
        assert disclosure_profile_key(label) is None, label


def test_a_leave_blank_policy_blanks_every_spanish_personal_question(db):
    policy(db, "personal_disclosures", "leave_blank")
    for label in ("Género", "Fecha de nacimiento", "Estado civil", "Orientación sexual", "¿Tienes alguna discapacidad?",
                  "Origen racial o étnico", "Religión", "Edad"):
        result = ask(db, label)
        assert result["answer"] is None and result["leave_blank"] is True and result["root_cause"] == "PERSONAL_DISCLOSURE_POLICY", label
    assert ask(db, "Nombre")["answer"] == "Test" and ask(db, "Pretensiones salariales")["answer"] == "60000"


def test_a_confirmed_spanish_disclosure_is_offered_in_the_forms_own_words(db):
    from jobagent.intelligence.store import entity, ingest_fact
    person = entity(db, "person", "Test Candidate")
    for concept, value in (("gender", "Non-binary"), ("disability", "No")):
        ingest_fact(db, person, concept, value, source="Synthetic explicit confirmation", evidence={"kind": "user_confirmation"},
                    verified=True, sensitivity="personal")
    policy(db, "personal_disclosures", "use_confirmed")
    result = ask(db, "Género", "select-one", tag="select", role="combobox", options=options("Hombre", "Mujer", "No binario", "Prefiero no decirlo"))
    assert result["answer"] == "No binario" and result["verified"] is True and result["confidence_level"] == "EXACT"
    assert ask(db, "¿Tienes alguna discapacidad?", "radio", role="radio", options=options("Sí", "No"))["answer"] == "No"
    assert ask(db, "Origen racial o étnico", options=options("Asiático", "Blanco", "Prefiero no decirlo"))["answer"] is None  # unconfirmed


def test_a_declined_answer_in_one_language_finds_the_declining_option_of_the_other():
    """Two places know the words (``answers.option_for`` for 'Prefer not to say', ``formatting.EQUIVALENTS`` for any other
    way of saying it): both are pinned."""
    from jobagent.automation.answers import option_for
    from jobagent.intelligence.formatting import choice
    spanish = options("Hombre", "Mujer", "Prefiero no decirlo")
    assert option_for("Prefer not to say", spanish)["label"] == "Prefiero no decirlo"
    english = options("Male", "Female", "Decline to answer")
    assert choice("Prefiero no decirlo", english, question="Gender")["label"] == "Decline to answer"
    assert choice("No deseo responder", options("Male", "Female", "Prefer not to say"), question="Gender")["label"] == "Prefer not to say"
    assert choice("Prefer not to say", options("Hombre", "Mujer", "Prefiero no responder"), question="Gender")["label"] == "Prefiero no responder"


def test_declining_to_say_has_its_spanish_words(db):
    for decline in ("Prefiero no decirlo", "Prefiero no responder", "No deseo responder", "Prefiero no contestar"):
        result = ask(db, "Género", options=options("Hombre", "Mujer", decline))
        assert result["answer"] == decline and result["confidence_level"] == "POLICY", decline


# --------------------------------------------------------------------------------------------- consent
CONSENTS = ["He leído y acepto la política de privacidad", "Acepto los términos y condiciones", "Acepto el tratamiento de mis datos personales",
            "Acepto recibir comunicaciones comerciales", "Doy mi consentimiento al tratamiento de mis datos", "Certifico que la información es correcta",
            "Declaro que los datos son veraces", "Autorizo el tratamiento de mis datos (LOPD)", "Estoy de acuerdo con el aviso legal"]


@pytest.mark.parametrize("label", CONSENTS)
def test_a_spanish_consent_is_recognised_as_consent_and_never_answered_without_the_users_policy(db, label):
    result = ask(db, label, "checkbox")
    assert result["canonical_intent"] == "CONSENT" and result["answer"] is None
    assert result["root_cause"] == "EXPLICIT_CONFIRMATION_REQUIRED" and result["verified"] is False


def test_a_consent_the_user_confirmed_as_a_policy_follows_it_in_spanish_too(db):
    policy(db, "consent_terms_acknowledgement", True)
    policy(db, "consent_privacy_acknowledgement", True)
    assert ask(db, "Acepto los términos y condiciones", "checkbox")["answer"] == "Yes"
    assert ask(db, "He leído y acepto la política de privacidad", "checkbox")["answer"] == "Yes"
    assert ask(db, "He leído y acepto la política de privacidad", "radio", role="radio",
               options=options("Sí, acepto", "No acepto"))["answer"] == "Sí, acepto"
    assert ask(db, "Acepto recibir comunicaciones comerciales", "checkbox")["answer"] is None  # that one was never confirmed


# --------------------------------------------------------------------------------------------- jurisdiction, aliases
def test_a_spanish_work_authorisation_answers_for_the_country_it_names(db):
    """The wrong answer to avoid: 'authorised to work in SPAIN' answered with the UK right to work of this UK vacancy."""
    for label, job, wanted in (("¿Tienes autorización para trabajar en España?", UK, None),
                               ("¿Tienes autorización para trabajar en el Reino Unido?", UK, "Yes"),
                               ("¿Tienes autorización para trabajar en el Reino Unido?", SPAIN, "Yes"),
                               ("¿Tienes autorización para trabajar en España?", SPAIN, None),
                               ("¿Tienes autorización para trabajar legalmente?", SPAIN, None),  # no UK right on a Spanish vacancy
                               ("¿Tienes autorización para trabajar en la Unión Europea?", UK, None),
                               ("¿Necesitas patrocinio de visado para trabajar en España?", UK, None)):
        assert ask(db, label, job=job)["answer"] == wanted, (label, job["country"])


def test_the_qualifiers_of_a_spanish_work_authorisation_are_not_dropped(db):
    """'Unrestricted', 'full time' or 'without sponsorship' are not the same question as 'right to work'."""
    plain = ask(db, "¿Tienes autorización para trabajar en el Reino Unido?")
    assert plain["answer"] == "Yes"
    for label in ("¿Tienes autorización para trabajar en el Reino Unido de forma indefinida?",
                  "¿Tienes autorización para trabajar en el Reino Unido sin restricciones?",
                  "¿Tienes autorización para trabajar a tiempo completo en el Reino Unido?",
                  "¿Tienes autorización para trabajar en el Reino Unido en el futuro?",
                  "¿Tienes autorización para trabajar en el Reino Unido durante todo el periodo de prácticas?"):
        result = ask(db, label)
        assert result["answer"] is None, (label, result["answer"])


def test_spanish_labels_find_the_profile_fields_they_name():
    for label, key in (("Nombre", "first_name"), ("Apellidos", "last_name"), ("Nombre completo", "full_name"),
                       ("Correo electrónico", "email"), ("Teléfono móvil", "phone"), ("País de residencia", "country"),
                       ("Ciudad de residencia", "city"), ("Perfil de LinkedIn", "linkedin"), ("Código postal", "postcode"),
                       ("Pretensiones salariales", "expected_salary"), ("Preaviso", "notice_period"),
                       ("Fecha de incorporación", "availability"), ("Universidad", "university")):
        assert canonical_key(label) == key, label


def test_a_spanish_label_that_merely_contains_an_english_fragment_is_not_read_as_english():
    """'revisar' contains 'visa' and 'supervisar' too: they are verbs, not a visa."""
    for label in ("¿Puedes revisar documentos?", "¿Has tenido que supervisar equipos?", "Por favor, revisa tu información"):
        assert interpret(label, {})["intent"] not in {"VISA", "SPONSORSHIP", "WORK_AUTHORIZATION"}, label
    assert interpret("¿Necesitas visado para trabajar en el Reino Unido?", {})["intent"] == "VISA"
    assert interpret("Do you need a visa to work in the UK?", {})["intent"] == "VISA"


def test_a_generic_spanish_label_takes_its_meaning_from_the_text_around_it(db):
    """'Confirmar' under 'Acepto la política de privacidad' is a consent; under 'Fecha de incorporación' it is not one."""
    for text in ("Acepto la política de privacidad", "Estoy de acuerdo con el aviso legal", "Autorizo el tratamiento de mis datos (RGPD)"):
        assert interpret("Confirmar", {"description": text})["intent"] == "CONSENT", text
    assert interpret("Detalles", {"section_label": "Antecedentes penales"})["intent"] == "CRIMINAL_HISTORY"
    assert interpret("Detalles", {"section_label": "Pretensiones salariales"})["intent"] == "SALARY"
    assert interpret("Detalles", {"section_label": "Datos del contacto de emergencia"})["intent"] == "OTHER"
