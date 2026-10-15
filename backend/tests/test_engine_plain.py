"""The plain-question gate (``intelligence/plain.py``): a fact answers a question only when the question is a PLAIN one.

The engine used to read a question by its keywords and then check a list of qualifiers that turn it round (a restriction, a
time, a negation, a second predicate...). Every list was one wording short: 'Do you require sponsorship (we are unable to offer
it)?' was 'Yes' for a person who needs none, 'Is your notice period longer than 1 month?' was 'Yes' for anybody who has one. Here a
question is accepted only when ALL of it is accounted for by a canonical form of its family (a template, with places, languages,
dates and numbers as slots); any word nothing accounts for is residue, and the question is the user's (``PLAIN_QUESTION_REQUIRED``).

This file tests the GATE (pure functions over strings) and, in the second half, the answers of the engine for the wordings of the
re-review findings RS-01 ... RS-19, asked of candidates whose facts differ so that a wrong answer cannot hide behind one that happens
to agree with it. The held-out ``test_engine_metamorphic.py`` wraps plain questions in every kind of extra text; ``WRAPPERS`` below is
this file's own, deliberately independent, version of the same idea.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import CHECKBOX, JOB_UK, RADIO, ask, engine_for  # noqa: E402
from jobagent.intelligence import plain  # noqa: E402

# ---------------------------------------------------------------------------------------------- what is a plain question
# Every wording below is accepted by the gate: a question of its family that asks for the fact and nothing else.
PLAIN = {
    'WORK_AUTHORIZATION': [
        "Do you have the right to work in the UK?", "Do you currently have the right to work in the United Kingdom?",
        "Do you have a valid right to work in the UK?", "Do you have the legal right to work in the UK?",
        "Are you legally authorised to work in the UK?", "Are you authorized to work in the United States?",
        "Are you eligible to work in the UK?", "Are you currently eligible to work in the UK?",
        "Are you allowed to work in the UK?", "Are you permitted to work in the UK?",
        "Are you legally entitled to work in the UK?", "Are you able to work in the UK without sponsorship?",
        "Can you work in the UK?", "Can you legally work in the UK?", "Do you have the right to live and work in the UK?",
        "Do you have work authorization?", "Do you have the right to work in the UK without sponsorship?",
        "Are you authorised to work full-time in the UK?", "Do you have permanent right to work in the UK?",
        "Do you have unrestricted right to work in the UK?",
        "Do you have the right to work in the UK for the duration of this internship?",
        "Will you have the right to work in the UK when you start?",
        "Will you be authorised to work in the UK after graduation?",
        "Are you authorised to work in the UK, Ireland or the EU?",
        "Are you authorised to work in the country where this job is located?",
        "Are you legally authorized to work in the country for which you are applying?", "Are you authorised to work?",
        "Right to work", "Right to work in the UK", "Work authorisation", "Do you not have the right to work in the UK?",
        "Aren't you authorised to work in the UK?", "I have the right to work in the UK", "I am authorised to work in the UK",
        "I do not have the right to work in the UK", "No right to work", "Please confirm your right to work status",
        "Please select your current UK work authorization status", "¿Tienes derecho a trabajar en el Reino Unido?",
        "¿Tienes autorización para trabajar en el Reino Unido?", "¿Estás autorizado para trabajar en España?",
        "¿Puedes trabajar legalmente en el Reino Unido?", "¿Tienes permiso de trabajo?",
        "¿Estás legalmente autorizado para trabajar en el Reino Unido?", "Derecho a trabajar", "Autorización para trabajar",
    ],
    'SPONSORSHIP': [
        "Do you require visa sponsorship?", "Do you require sponsorship?", "Will you require sponsorship to work in the UK?",
        "Do you now or in the future require sponsorship?",
        "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status, etc.)?",
        "Do you need visa sponsorship?", "Do you need sponsorship to work in the UK?",
        "Will you need sponsorship now or in the future?", "Will you require visa sponsorship now or in the future?",
        "Do you require employer sponsorship?", "Are you going to need sponsorship?",
        "Do you need an employer to sponsor your visa?", "Will you need to be sponsored?", "Is sponsorship required?",
        "Is visa sponsorship required for you?", "Do you not require sponsorship?", "I require visa sponsorship",
        "I do not require visa sponsorship", "I will need sponsorship", "I will not require sponsorship now or in the future",
        "No sponsorship required", "Sponsorship required", "Need sponsorship?", "Visa sponsorship", "Sponsorship needed",
        "Do you require visa sponsorship now or in the future? Select Yes No", "Do you require sponsorship? (Yes/No)",
        "Do you now or will you in the future require visa sponsorship?", "Please confirm that you do not require sponsorship",
        "¿Necesitas patrocinio de visado?", "¿Necesitarás patrocinio de visado ahora o en el futuro?",
        "No necesito patrocinio de visado", "¿Requieres patrocinio?", "¿Necesitas patrocinio para trabajar en el Reino Unido?",
        "Patrocinio de visado",
    ],
    'VISA': [
        "Visa type", "Current visa type", "What is your current visa type?", "Visa route", "Immigration status",
        "Current immigration status", "What is your visa status?", "Which visa do you currently hold?",
        "Please describe your UK immigration status.", "Give details of the visa you currently hold.",
        "Please explain your current visa type and status.", "If yes, please describe your visa situation",
        "I hold a Student visa", "I am on a Student visa.", "Student visa", "Graduate Visa (with work rights)",
        "Skilled Worker Visa", "type of visa currently held", "immigration route", "Tipo de visado", "Situación migratoria",
    ],
    'LANGUAGE': [
        "Do you speak English?", "Can you speak Spanish?", "Are you fluent in English?",
        "Are you fluent in English and Spanish?", "Do you speak English fluently?", "Are you a native Spanish speaker?",
        "Is Spanish your first language?", "Is English your mother tongue?", "Is English your native language?",
        "Are you bilingual in English and Spanish?", "Do you speak English at C1 level?",
        "Do you speak English at a native level?", "Do you have professional working proficiency in English?",
        "Do you have native or bilingual proficiency in English?", "What is your level of English?", "English level",
        "English proficiency", "Spanish fluency", "Which languages do you speak?", "What languages do you speak?", "Languages",
        "Spoken languages", "Native language", "Mother tongue", "Language skills", "Proficiency in English", "English (ENG)",
        "¿Hablas inglés?", "¿Eres nativo de inglés?", "¿Hablas inglés con nivel nativo?", "¿Hablas español de forma nativa?",
        "¿Hablas inglés con fluidez?", "Nivel de inglés", "¿Cuál es tu nivel de inglés?", "¿Cuál es tu lengua materna?",
        "Idiomas",
    ],
    'DEGREE': [
        "Do you have a degree?", "Do you have a Master's degree?", "Do you hold a Bachelor's degree in Computer Science?",
        "Do you have a PhD?", "Have you completed a Master's degree?",
        "Do you have a degree in Computer Science or a related field?", "Do you have a degree from North University?",
        "Do you have a Bachelor's degree from North University?", "Highest level of education", "Highest completed degree",
        "Degree", "Field of study", "Discipline", "What degree did you complete at the above university?",
        "Which degree have you completed?", "State your highest completed degree", "Bachelor's degree", "Nivel de estudios",
        "Titulación", "Grado", "¿Tienes un grado en Informática?", "¿Tienes un máster?", "Especialidad",
    ],
    'EDUCATION': [
        "Are you currently studying?", "Are you currently a student?", "Are you studying at South University?",
        "Are you currently enrolled at South University?", "University", "School or University", "Current university",
        "Which university are you currently enrolled at?", "Most recent university attended", "Institution name",
        "Universidad", "¿Estás estudiando actualmente?", "¿Eres estudiante?", "Centro de estudios",
    ],
    'GRADUATION': [
        "Graduation year", "Expected graduation year", "Expected graduation date", "When do you graduate?",
        "What is your graduation year?", "Will you have graduated by 2027?", "Have you graduated before 2028?",
        "Have you graduated since 2020?", "Are you graduating in 2025 or 2026?", "Will you graduate between 2026 and 2027?",
        "Did you graduate in 2026?", "Will you have graduated by September 2027?", "Will you have graduated by 30 June 2028?",
        "What year does your course finish?", "Degree completion date", "Año de graduación", "¿Cuándo terminas tus estudios?",
        "¿Te has graduado desde 2020?", "¿Te graduarás en 2027?", "Are you a recent graduate in Computer Science?",
        "Have you recently graduated with a STEM qualification?",
    ],
    'GRADE': [
        "GPA", "Grade", "Degree classification", "Degree average", "Overall degree average", "Expected degree classification",
        "Predicted grade", "Class of degree", "Nota media",
    ],
    'NOTICE_PERIOD': [
        "Notice period", "What is your notice period?", "What is your current notice period?", "Current notice period",
        "Contractual notice", "How much notice is required?", "Notice period (weeks)", "Do you have a notice period?",
        "Is your notice period more than 4 weeks?", "Do you have a notice period of 30 days or less?",
        "Is your notice period 2 weeks or more?", "Preaviso", "¿Cuál es tu periodo de preaviso?",
        "¿Tienes un preaviso de más de 4 semanas?",
    ],
    'START_DATE': [
        "Start date", "Earliest start date", "Anticipated start date", "Anticipated start date (MM/YYYY)",
        "When can you start?", "When could you join us?", "How soon can you join us?", "What is your availability?",
        "Availability", "Preferred start date", "Date you are available for work",
        "Are you available to start on 28 September 2027?", "Can you start by 1 October 2027?", "Fecha de incorporación",
        "¿Cuándo podrías incorporarte?", "Disponibilidad",
    ],
    'LOCATION': [
        "Location", "City", "Country", "Current location", "Country of residence", "Location (City)", "Where are you located?",
        "Where are you currently based?", "Where do you live?", "Current city", "City of residence",
        "Are you based in London?", "Are you currently based in the UK?", "Do you live in London?",
        "Do you currently reside in the United Kingdom?", "Are you a resident of the UK?", "Is London your current city?",
        "País de residencia", "Ciudad de residencia", "¿Dónde resides?", "¿Resides en el Reino Unido?", "Ubicación",
        "Country Select United Kingdom",
    ],
    'RELOCATION': [
        "Are you willing to relocate?", "Are you willing to relocate to London?", "Would you be willing to relocate?",
        "Are you open to relocation?", "Are you able to relocate?", "Relocation", "¿Estás dispuesto a reubicarte?",
        "¿Estarías dispuesto a mudarte de ciudad?",
    ],
    'SALARY': [
        "Salary expectations", "Expected salary", "What are your salary expectations?", "Expected annual salary",
        "Desired salary", "Salary expectation (per year)", "Salary expectations (GBP)", "Salary (annual, in pounds)",
        "My salary", "Pretensiones salariales", "Expectativas salariales", "Salario deseado", "Salario bruto anual",
        "Salary expectations?", "What is your expected salary?",
    ],
    'IDENTITY': [
        "Name", "Full name", "First name", "Last name", "Legal first name", "Surname", "Given name", "Preferred name",
        "Your name", "Date of birth", "Age", "Nombre", "Apellidos", "Nombre completo", "Fecha de nacimiento", "Edad",
        "Country of birth", "Place of birth",
    ],
    'NATIONALITY': [
        "Nationality", "What is your nationality?", "Citizenship", "Country of citizenship", "Nacionalidad", "Ciudadanía",
    ],
    'CONTACT': [
        "Email", "Email address", "Phone", "Phone number", "Mobile", "Mobile number", "Telephone", "Personal email",
        "Correo electrónico", "Teléfono móvil", "Teléfono", "Móvil", "Contact email",
    ],
    'ADDRESS': [
        "Address", "Address Line 1", "Postcode", "Postal code", "Zip code", "Street address", "Dirección", "Código postal",
        "Domicilio",
    ],
    'DEMOGRAPHIC': [
        "Gender", "Gender identity", "What is your gender?", "What gender do you identify as?", "Which gender do you identify with?",
        "Pronouns", "Preferred pronouns", "What are your pronouns?", "Which pronouns do you use?", "Race", "Ethnicity",
        "What is your race or ethnicity?", "Race/Ethnicity", "Ethnic origin", "Which ethnic group do you belong to?", "Religion",
        "Religion or belief", "Veteran status", "Are you a protected veteran?", "Sexual orientation", "Disability", "Disability status",
        "Do you have a disability?", "Do you have any disabilities?", "Do you consider yourself to have a disability?",
        "Do you identify as having a disability?", "Are you disabled?", "Are you a person with a disability?",
        "Voluntary self-identification of disability", "Date of birth", "Género", "Identidad de género", "¿Cuál es tu género?",
        "Pronombres", "¿Cuáles son tus pronombres?", "¿Qué pronombres usas?", "Origen racial o étnico", "Etnia",
        "¿A qué grupo étnico perteneces?", "Religión", "Condición de veterano", "Orientación sexual", "¿Tienes alguna discapacidad?",
        "¿Tienes una discapacidad?", "¿Te consideras una persona con discapacidad?", "Discapacidad", "Fecha de nacimiento",
    ],
    'LINKEDIN': ["LinkedIn", "LinkedIn Profile", "LinkedIn URL", "LinkedIn profile URL (optional)", "What is your LinkedIn profile?",
                 "link to LinkedIn", "professional LinkedIn page", "LinkedIn URL (e.g. linkedin.com/in/yourname)", "Perfil de LinkedIn",
                 "URL de LinkedIn"],
    'GITHUB': ["GitHub", "GitHub URL", "GitHub profile", "GitHub account link", "GitHub page", "Perfil de GitHub"],
    'PORTFOLIO': ["Portfolio URL", "portfolio link", "portfolio website", "personal website", "Portfolio / Website",
                  "Link to your portfolio", "Personal website or portfolio URL", "Portafolio", "Sitio web", "URL del portfolio"],
}

CASES = [(intent, question) for intent, questions in PLAIN.items() for question in questions]


@pytest.mark.parametrize("intent,question", CASES)
def test_a_plain_question_of_its_family_is_accepted(intent, question):
    reading = plain.read(question, {}, intent)
    assert reading["ok"] is True and reading["gated"] is True and reading["residue"] == [], (question, reading["residue"])
    assert reading["template"], question


def test_every_family_of_fact_is_represented_in_both_languages():
    assert set(PLAIN) >= {"WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "LANGUAGE", "DEGREE", "EDUCATION", "GRADUATION", "GRADE",
                          "NOTICE_PERIOD", "START_DATE", "LOCATION", "RELOCATION", "SALARY", "IDENTITY", "NATIONALITY", "CONTACT",
                          "ADDRESS", "DEMOGRAPHIC", "LINKEDIN", "GITHUB", "PORTFOLIO"}
    assert len(CASES) >= 300 and sum(any(ord(c) > 127 or c == "¿" for c in q) for _, q in CASES) >= 40
    assert {plain.family_of(i) for i in PLAIN} == set(plain.GRAMMARS)


# ------------------------------------------------------------------------------------------- what is NOT a plain question
# (intent, question, a word the reading says nothing accounted for). Each is a wording the earlier fixes missed.
NOT_PLAIN = [
    # an employer's note, a parenthetical, a second sentence or clause (RS-01)
    ("SPONSORSHIP", "Do you require visa sponsorship (we are unable to offer sponsorship for this role)?", "unable"),
    ("SPONSORSHIP", "Do you require sponsorship to work in the UK. We cannot sponsor visas.", "cannot"),
    ("SPONSORSHIP", "We do not sponsor visas - do you require sponsorship", "visas"),
    ("SPONSORSHIP", "Will you require sponsorship now or in the future (not including a Graduate visa)?", "including"),
    ("SPONSORSHIP", "Do you require visa sponsorship (no exceptions)?", "exceptions"),
    ("SPONSORSHIP", "Not a UK national: will you require sponsorship?", "national"),
    ("WORK_AUTHORIZATION", "Are you authorised to work in the UK (not including Northern Ireland)?", "including"),
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK (not just a visitor visa)?", "just"),
    ("SPONSORSHIP", "Please confirm that you do not require sponsorship (we cannot sponsor)", "cannot"),
    ("SPONSORSHIP", "Are you exempt from needing sponsorship?", "exempt"),
    ("SPONSORSHIP", "Are you sponsor-free?", "free"),
    ("SPONSORSHIP", "I am not a UK citizen and require sponsorship", "citizen"),
    ("SPONSORSHIP", "I am not a UK citizen and do not need sponsorship", "citizen"),
    ("SPONSORSHIP", "¿Necesitas patrocinio de visado? (no podemos patrocinar)", "podemos"),
    ("SPONSORSHIP", "¿Necesitas patrocinio? (no incluye visados de estudiante)", "incluye"),
    # a time, a duration, an hours qualifier on a right that is time-limited (RS-02)
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK once your visa is renewed?", "renewed"),
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK beyond 2027?", "beyond"),
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK from September 2027?", "from"),
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK in 2028?", "in"),
    ("WORK_AUTHORIZATION", "Are you authorised to work in the UK for more than 20 hours per week?", "more"),
    ("WORK_AUTHORIZATION", "Are you authorised to work in the UK up to 20 hours a week?", "up"),
    ("WORK_AUTHORIZATION", "Are you authorised to work in the UK during term time?", "term"),
    ("WORK_AUTHORIZATION", "Are you authorised to work in the UK during university holidays?", "holidays"),
    ("WORK_AUTHORIZATION", "Do you have the right to work in the UK for as long as you want?", "want"),
    ("WORK_AUTHORIZATION", "¿Tienes derecho a trabajar en el Reino Unido a partir de septiembre de 2027?", "partir"),
    ("WORK_AUTHORIZATION", "¿Tienes derecho a trabajar en el Reino Unido más de 20 horas semanales?", "horas"),
    # studies: school type, parents, bursary, dropped out, excluded, mature, part-time, a subject nobody confirmed (RS-03)
    ("EDUCATION", "Did you attend a state school?", "attend"),
    ("EDUCATION", "Did you go to a private school?", "go"),
    ("EDUCATION", "Were you eligible for free school meals?", "eligible"),
    ("EDUCATION", "Were you the first in your family to attend university?", "family"),
    ("EDUCATION", "Did your parents go to university?", "parents"),
    ("EDUCATION", "Do your parents have a university degree?", "parents"),
    ("EDUCATION", "Did you drop out of university?", "drop"),
    ("EDUCATION", "Have you been excluded from school?", "excluded"),
    ("EDUCATION", "Do you receive a bursary at university?", "bursary"),
    ("EDUCATION", "Did you repeat a year at university?", "repeat"),
    ("EDUCATION", "Are you a mature student?", "mature"),
    ("EDUCATION", "Are you studying part-time?", None),
    ("EDUCATION", "Are you studying mathematics?", "mathematics"),
    ("EDUCATION", "Do you attend North University?", "attend"),
    ("EDUCATION", "Do you have academic experience with Python?", "experience"),
    # a language: every predicate that is not 'do you speak / how well' (RS-04)
    ("LANGUAGE", "Is English your second language?", "second"),
    ("LANGUAGE", "Is English your only language?", "only"),
    ("LANGUAGE", "Is English a foreign language for you?", "foreign"),
    ("LANGUAGE", "Do you need English language support?", "need"),
    ("LANGUAGE", "Do you hold an English language qualification?", "hold"),
    ("LANGUAGE", "Have you taken an English language test?", "taken"),
    ("LANGUAGE", "Was your degree taught in English?", "taught"),
    ("LANGUAGE", "Can you teach English?", "teach"),
    ("LANGUAGE", "Do you speak English with customers?", "customers"),
    ("LANGUAGE", "Do you hold a Spanish passport?", "passport"),
    ("LANGUAGE", "Do you have a Cambridge English certificate?", "cambridge"),
    ("LANGUAGE", "¿Tienes algún certificado de inglés?", "certificado"),
    ("LANGUAGE", "¿Tienes un título oficial de inglés (B2 o superior)?", "superior"),
    # a notice that is negotiable, flexible, bought out
    ("NOTICE_PERIOD", "Is your notice period negotiable?", "negotiable"),
    ("NOTICE_PERIOD", "Is your notice period flexible?", "flexible"),
    ("NOTICE_PERIOD", "Can your notice period be bought out?", "bought"),
    # a place, a direction, a negation (RS-08)
    ("LOCATION", "Are you not based in London?", "not"),
    ("LOCATION", "Do you live outside London?", "outside"),
    ("LOCATION", "Do you live outside of the United Kingdom?", "outside"),
    ("LOCATION", "Are you based outside London?", "outside"),
    ("RELOCATION", "Are you willing to relocate from London?", "from"),
    ("RELOCATION", "Are you willing to relocate away from London?", "away"),
    ("RELOCATION", "Will you relocate from London to Madrid?", "from"),
    # a composite (RS-13)
    ("WORK_AUTHORIZATION", "Are you legally allowed to work in the UK, and will you need sponsorship?", "need"),
    ("SPONSORSHIP", "Will you require sponsorship and do you hold a valid visa?", "hold"),
    ("WORK_AUTHORIZATION", "Are you eligible to work in the UK and willing to travel?", "willing"),
    ("WORK_AUTHORIZATION", "Are you legally able to work in the UK and available to start in September 2027?", "available"),
    ("DEGREE", "Do you hold a Master's degree and speak English fluently?", "speak"),
    # attributes of the same topic that the fact is not (RS-12)
    ("LOCATION", "Biometric residence permit number", "biometric"),
    ("IDENTITY", "Street name", "street"),
    ("IDENTITY", "House name", "house"),
    ("IDENTITY", "What was the name of your first pet?", "pet"),
    ("VISA", "Visa issue date", "issue"),
    ("VISA", "Visa duration", "duration"),
    ("VISA", "Name as it appears on your visa", "appears"),
    ("VISA", "Country that issued your visa", "issued"),
    ("VISA", "How do you plan to obtain a visa?", "plan"),
    ("VISA", "Describe any immigration issues", "issues"),
    # salary: another basis or a figure for something else (RS-17)
    ("SALARY", "Lowest salary you would accept", "lowest"),
    ("SALARY", "Salary history", "history"),
    # a narrative never reads as a fact (RS-05)
    ("LOCATION", "Describe a time you lived abroad", "describe"),
    ("LANGUAGE", "Tell us about your studies in English", "studies"),
    ("CONTACT", "Tell us about a mobile project", "about"),
    # a follow-up in a text field (RS-15)
    ("EDUCATION", "Are you currently studying? If so, where?", "where"),
    ("DEGREE", "Do you have a degree? If so, in what subject?", "subject"),
    ("LANGUAGE", "Do you speak French? If so, at what level?", "level"),
    ("LANGUAGE", "Do you speak English? Please indicate your level", "indicate"),
    ("LOCATION", "Are you currently based in London? If not, where are you based?", "where"),
    ("DEGREE", "Do you hold a Master's degree? If so, from which university?", "university"),
    # relations of a graduation date that are not a plain year (RS-09)
    ("GRADUATION", "Will you graduate by summer 2027?", "summer"),
    ("GRADUATION", "Will you graduate in the first half of 2027?", None),
    ("GRADUATION", "Will you graduate by Q2 2027?", "q2"),
    ("GRADUATION", "Will you have graduated by early 2027?", "early"),
]


@pytest.mark.parametrize("intent,question,word", NOT_PLAIN)
def test_a_question_that_is_more_than_the_plain_one_is_refused_and_says_what_is_left(intent, question, word):
    reading = plain.read(question, {}, intent)
    assert reading["ok"] is False and reading["family"] == plain.family_of(intent), (question, reading)
    assert word is None or word in " ".join(reading["residue"]).split() or word in reading["reason"], (question, word, reading["residue"])
    assert reading["reason"].startswith("Meridian answers a plain ") and reading["reason"].endswith("Only you can answer it.")


def test_a_long_text_field_is_never_answered_from_a_one_line_fact():
    for intent, question in (("LOCATION", "Where are you based?"), ("LANGUAGE", "Which languages do you speak?"),
                             ("NOTICE_PERIOD", "What is your notice period?"), ("SALARY", "Salary expectations")):
        assert plain.read(question, {"tag": "textarea", "type": "textarea"}, intent)["ok"] is False, question
        assert plain.read(question, {"type": "text"}, intent)["ok"] is True, question


def test_W5_an_address_in_a_textarea_is_answered_a_textarea_is_its_ordinary_control():
    """'Address' / 'Home address' / 'Dirección' are multi-line texts by nature (a textarea is how a form asks for them); what turns an address
    prompt into a narrative is its wording ('Describe your address history'), which the grammar refuses on any control."""
    area = {"tag": "textarea", "type": "textarea"}
    for question in ("Address", "Home address", "Street address", "Current address", "Dirección"):
        assert plain.read(question, area, "ADDRESS")["ok"] is True, question
    for question in ("Describe your address history", "Tell us your address in London and why you chose it"):
        assert plain.read(question, area, "ADDRESS")["ok"] is False, question


def test_W5_authorised_to_work_in_the_uk_without_a_subject_is_the_plain_question():
    """Forms write the right to work as a bare label ('Authorised to work in the UK?', 'Eligible to work in the UK'): a plain question
    (the wave-4 grammar needed 'Are you ...'), never with a negation, a number or a second predicate."""
    for question in ("Authorised to work in the UK?", "Authorized to work in the United States", "Legally eligible to work in the UK",
                     "Eligible to work in the UK?", "Authorised to work in the UK without sponsorship?", "¿Dispone de permiso de trabajo?",
                     "¿Autorizado para trabajar en el Reino Unido?"):
        assert plain.read(question, {"type": "radio"}, "WORK_AUTHORIZATION")["ok"] is True, question
    for question in ("Not authorised to work in the UK", "Authorised to work in the UK for 20 hours a week", "Authorised to work and need sponsorship",
                     "Authorised to work in the UK (excluding Northern Ireland)", "Authorised to work in the UK or the EU and 5 years of experience"):
        assert plain.read(question, {"type": "radio"}, "WORK_AUTHORIZATION")["ok"] is False, question


def test_an_address_in_a_long_text_box_is_still_a_plain_question():
    """'Address' is the one fact whose ordinary control is a multi-line box: the box does not make it a narrative; the wording does."""
    for question in ("Address", "Home address", "Street address", "Direccion"):
        assert plain.read(question, {"tag": "textarea", "type": "textarea"}, "ADDRESS")["ok"] is True, question
    for question in ("Describe your address history", "Tell us your address in London"):
        assert plain.read(question, {"tag": "textarea", "type": "textarea"}, "ADDRESS")["ok"] is False, question


def test_a_question_of_another_kind_is_not_the_gates_business():
    reading = plain.read("Why do you want to work here?", {}, "MOTIVATION")
    assert reading["gated"] is False and reading["ok"] is True


# ------------------------------------------------------------------------------------------------- wrappers (our own)
# What a page, an employer or a careless author can put around a plain question. Each ADDS words, so each adds residue: the
# gate refuses the result (the engine then answers 'needs you'), and never reads the question as the plain one.
WRAPPERS = {
    "a note in brackets": lambda q: q.rstrip("?") + " (we cannot sponsor visas)?",
    "not including": lambda q: q.rstrip("?") + " (not including a Graduate visa)?",
    "a note before": lambda q: "Note: this role is full time. " + q,
    "a note after": lambda q: q + " We cannot sponsor visas.",
    "a follow-up": lambda q: q.rstrip("?") + "? If so, please specify.",
    "a second predicate": lambda q: q.rstrip("?") + " and are you over 18?",
    "an alternative predicate": lambda q: q.rstrip("?") + " or do you have a degree?",
    "a duration": lambda q: q.rstrip("?") + " for the next 5 years?",
    "an hours qualifier": lambda q: q.rstrip("?") + " for more than 20 hours per week?",
    "a quantifier": lambda q: q.rstrip("?") + " for every employer?",
    "an exception": lambda q: q.rstrip("?") + ", except as a contractor?",
    "a condition": lambda q: q.rstrip("?") + " unless you are a student?",
    "a clause after a dash": lambda q: q.rstrip("?") + " - this excludes graduates?",
    "a lead clause": lambda q: "Not a UK national: " + q,
    "a heading word": lambda q: q.rstrip("?") + " at the previous employer?",
    "a second heading": lambda q: "Job 1 - " + q,
}
# What changes nothing: case, whitespace, a polite word, the hint that says how to answer, a required marker.
NEUTRAL = {
    "whitespace": lambda q: "  " + q.replace(" ", "   ") + " ",
    "a polite word": lambda q: "Please " + q[0].lower() + q[1:],
    "an answer hint": lambda q: q + " Please select Yes or No.",
    "a required marker": lambda q: q.rstrip("?") + " *",
    "required in brackets": lambda q: q.rstrip("?") + " (required)?",
}
QUESTIONS = [(i, q) for i, q in CASES if q.endswith("?") and len(q.split()) >= 4]


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_a_wrapped_plain_question_is_never_read_as_the_plain_one(name):
    leaked = [(intent, WRAPPERS[name](q)) for intent, q in QUESTIONS if plain.read(WRAPPERS[name](q), {}, intent)["ok"]]
    assert len(QUESTIONS) >= 100 and not leaked, leaked[:5]


@pytest.mark.parametrize("name", sorted(NEUTRAL))
def test_a_neutral_change_of_a_plain_question_is_still_plain(name):
    refused = [(intent, NEUTRAL[name](q)) for intent, q in QUESTIONS
               if plain.read(q, {}, intent)["ok"] and not plain.read(NEUTRAL[name](q), {}, intent)["ok"]]
    assert not refused, refused[:5]


def test_a_follow_up_written_into_the_label_is_unconsumed_for_every_control():
    """A yes or no cannot carry the 'If so, please say where' of its own label: not a text field, and not the radio group either (the
    held-out metamorphic suite found 'Are you based in the UK? If so, please state where.' answered Yes on a radio)."""
    question = "Do you require sponsorship? If yes, please explain"
    for field in (RADIO, {"type": "text"}, {"type": "radio", "options": [{"label": "Yes"}, {"label": "No"}]}):
        reading = plain.read(question, field, "SPONSORSHIP")
        assert reading["ok"] is False and {"if", "yes"} <= set(reading["residue"]), (field, reading)


def test_the_option_prefix_of_a_label_and_a_leading_conditional_are_consumed():
    assert plain.read("Yes, I hold a Graduate visa", {}, "VISA")["slots"]["prefix"] is True
    reading = plain.read("If yes, please describe your visa situation", {}, "VISA")
    assert reading["ok"] is True and reading["slots"]["follow_up"] is True


def test_an_example_list_in_brackets_is_read_with_the_vocabulary_of_the_family():
    ok = "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status, etc.)?"
    assert plain.read(ok, {}, "SPONSORSHIP")["ok"] is True
    assert plain.read(ok.replace("H-1B visa status, etc.", "not for students"), {}, "SPONSORSHIP")["ok"] is False
    assert plain.read("Notice period (weeks)", {}, "NOTICE_PERIOD")["ok"] is True
    assert plain.read("Notice period (negotiable)", {}, "NOTICE_PERIOD")["ok"] is False
    assert plain.read("South University (SU)", {}, "EDUCATION")["ok"] is True  # an abbreviation of the name before it


def test_the_slots_the_engine_reads_are_recorded():
    reading = plain.read("Do you have permanent right to work in the UK without sponsorship?", {}, "WORK_AUTHORIZATION")
    assert set(reading["slots"]["facets"]) == {"permanent", "sponsorship_free"} and reading["slots"]["place_phrases"] == 1
    assert plain.read("Will you require sponsorship now or in the future?", {}, "SPONSORSHIP")["slots"]["timeframe"] == "now_or_future"
    assert plain.read("Do you require sponsorship?", {}, "SPONSORSHIP")["slots"]["timeframe"] == "unspecified"
    threshold = plain.read("Is your notice period more than 4 weeks?", {}, "NOTICE_PERIOD")["slots"]["threshold"]
    assert threshold == {"comparator": ">", "value": 4, "unit": "weeks"}
    threshold = plain.read("Do you have a notice period of 30 days or less?", {}, "NOTICE_PERIOD")["slots"]["threshold"]
    assert threshold == {"comparator": "<=", "value": 30, "unit": "days"}
    degree = plain.read("Do you have a Master's degree in Mathematics from North University?", {}, "DEGREE")
    assert degree["slots"]["subjects"] == ["mathematics"] and len(degree["slots"]["institutions"]) == 1
    assert plain.read("Will you have the right to work in the UK?", {}, "WORK_AUTHORIZATION")["slots"]["facets"] == ["future"]


def test_a_narrative_prompt_is_recognised():
    for text in ("Describe a project you delivered", "Tell us about a mobile project", "How do you make decisions?", "What did you learn from it?",
                 "Walk us through your approach", "Explain why", "Cuéntanos tu experiencia"):
        assert plain.is_narrative(text), text
    for text in ("Where do you live?", "Do you speak English?", "Notice period", "What is your notice period?"):
        assert not plain.is_narrative(text), text
    assert plain.is_narrative("Where do you live?", {"tag": "textarea"})


def test_a_hostile_label_costs_a_bounded_time():
    import time
    for text in ("a" + " " * 19998 + "x", "do you have the right to work " * 400, "(" * 5000 + "right to work" + ")" * 5000, "not " * 5000,
                 "Do you " + "and do you " * 2000 + "speak English?", "work in " * 4000):
        began = time.perf_counter()
        for intent in ("WORK_AUTHORIZATION", "LANGUAGE", "EDUCATION", "NOTICE_PERIOD", "SALARY"):
            plain.read(text, {}, intent)
        assert time.perf_counter() - began < 2.0, text[:30]


# =============================================================================================================== the engine
# The re-review findings, asked of candidates whose facts differ: T the Spanish national on a UK Student route (a right NOW, sponsorship
# later, a BSc finished in 2026 and an MSc in progress to 2027), N a UK citizen, S a person who needs sponsorship, E a citizen with 14
# days of notice. ``None`` is 'the user answers'.
LEGAL = ("student", "citizen", "needs_sponsorship")


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    path = tmp_path_factory.mktemp("plain")
    return {kind: engine_for(path, kind) for kind in (*LEGAL, "employed")}


@pytest.fixture(scope="module")
def graduates(tmp_path_factory):
    import os
    from candidates import education_engine
    os.environ["MERIDIAN_TEST"] = "1"
    path = tmp_path_factory.mktemp("plain-education")
    return {kind: education_engine(path, kind) for kind in ("student", "citizen", "graduate", "months")}


def answers(engines, label, kinds=LEGAL, job=JOB_UK, **field):
    return tuple(ask(engines[kind], label, job, **(field or RADIO))["answer"] for kind in kinds)


def refused(engines, label, kinds=LEGAL, job=JOB_UK, cause="PLAIN_QUESTION_REQUIRED", **field):
    for kind in kinds:
        result = ask(engines[kind], label, job, **(field or RADIO))
        assert result["answer"] is None and result["verified"] is False and result["fact_ids"] == [], (kind, label, result["answer"])
        # (a more precise reading of the same refusal keeps its own reason: 'an exception', 'a restriction', 'a lack')
        assert result["root_cause"] == cause or "_PREDICATE:" in str(result["root_cause"]), (kind, label, result["root_cause"])


RS01 = [
    "Do you require visa sponsorship (we are unable to offer sponsorship for this role)?",
    "Do you require sponsorship to work in the UK. We cannot sponsor visas.", "We do not sponsor visas - do you require sponsorship",
    "Will you require sponsorship now or in the future (not including a Graduate visa)?", "Do you require visa sponsorship (no exceptions)?",
    "Not a UK national: will you require sponsorship?", "Are you authorised to work in the UK (not including Northern Ireland)?",
    "Do you have the right to work in the UK (not just a visitor visa)?", "Please confirm that you do not require sponsorship (we cannot sponsor)",
    "Are you exempt from needing sponsorship?", "Are you sponsor-free?", "Do you require sponsorship, no matter the route?",
    "¿Necesitas patrocinio de visado? (no podemos patrocinar)", "¿Tienes derecho a trabajar en el Reino Unido? (no incluye visados de estudiante)",
    "Are you authorised to work in the UK (not including a visitor visa)?", "Do you require sponsorship (we do not sponsor visas)?",
    "Do you require visa sponsorship (note: this role cannot be sponsored)?", "Is employer sponsorship not required for you (unless you are a student)?"]


@pytest.mark.parametrize("label", RS01)
def test_RS01_an_employer_note_or_an_exception_never_turns_a_legal_answer_round(engines, label):
    """The answer of a work-right or sponsorship question used to be the parity of EVERY negator of the label, notes included."""
    assert answers(engines, label) == (None, None, None), label
    refused(engines, label)


@pytest.mark.parametrize("label", ["I am not a UK citizen and require sponsorship", "I am not a UK citizen and do not need sponsorship"])
def test_RS01_a_checkbox_that_states_two_clauses_is_not_ticked_for_anyone(engines, label):
    assert answers(engines, label, **CHECKBOX) == (None, None, None)


@pytest.mark.parametrize("label,expected", [
    ("Do you require visa sponsorship?", ("No", "No", "Yes")), ("Do you not require sponsorship?", ("Yes", "Yes", "No")),
    ("Do you have the right to work in the UK?", ("Yes", "Yes", "No")), ("Do you not have the right to work in the UK?", ("No", "No", "Yes")),
    ("¿Necesitas patrocinio de visado?", ("No", "No", "Yes")), ("¿Tienes derecho a trabajar en el Reino Unido?", ("Yes", "Yes", "No")),
    ("Do you now or will you in the future require visa sponsorship?", ("No", "No", "Yes")),
    ("Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status, etc.)?", ("No", "No", "Yes"))])
def test_RS01_the_plain_questions_are_still_answered_with_their_polarity(engines, label, expected):
    assert answers(engines, label) == expected, label


RS02 = [
    "Do you have the right to work in the UK for the duration of this internship?", "Are you eligible to work in the UK after you complete your studies?",
    "Are you authorised to work in the UK post-graduation?", "Do you have the right to work in the UK on the start date?",
    "Do you have the right to work in the UK beyond 2027?", "Will you be eligible to work in the UK upon graduation?",
    "Are you authorised to work in the UK for more than 20 hours per week?", "Do you have the right to work in the UK from September 2027?",
    "Do you have the right to work in the UK in 2028?", "Do you have the right to work in the UK for as long as you work here?",
    "Do you have the right to work in the UK following your studies?", "Do you have the right to work in the UK at the time of joining?",
    "Do you have the right to work in the UK up to 20 hours a week?", "Do you have the right to work in the UK during term time?",
    "Do you have the right to work in the UK during university holidays?", "Do you have the right to work in the UK from 2027 onwards?",
    "¿Tienes derecho a trabajar en el Reino Unido después de tus estudios?", "¿Tienes derecho a trabajar en el Reino Unido a partir de septiembre de 2027?",
    "¿Tienes derecho a trabajar en el Reino Unido más de 20 horas semanales?", "¿Tienes derecho a trabajar en el Reino Unido en la fecha de incorporación?"]


@pytest.mark.parametrize("label", RS02)
def test_RS02_a_time_limited_right_is_never_answered_yes_for_a_time_it_does_not_state(engines, label):
    """For the Student route the right to work is NOW and ends with the course: every time, duration and hours qualifier is a facet
    with a fact of its own (future right, a period policy) or the user's, never the plain 'yes'."""
    assert ask(engines["student"], label, JOB_UK, **RADIO)["answer"] is None, label
    assert ask(engines["needs_sponsorship"], label, JOB_UK, **RADIO)["answer"] != "Yes", label


def test_RS02_a_right_that_is_permanent_covers_any_time_and_a_time_limited_one_is_asked_for_the_time(engines):
    assert answers(engines, "Do you have the right to work in the UK for the duration of this internship?", kinds=("citizen",)) == ("Yes",)
    assert answers(engines, "Will you have the right to work in the UK when you start?", kinds=("citizen",)) == ("Yes",)
    assert answers(engines, "Do you have the right to work in the UK for the duration of this internship?", kinds=("student",)) == (None,)
    assert answers(engines, "Do you have the right to work in the UK?", kinds=("student",)) == ("Yes",)  # the plain question stays plain


RS03 = ["Did you attend a state school?", "Did you go to a private school?", "Were you eligible for free school meals?",
        "Were you the first in your family to attend university?", "Did your parents go to university?", "Do your parents have a university degree?",
        "Did you drop out of university?", "Have you been excluded from school?", "Have you ever been suspended from university?",
        "Do you receive a bursary at university?", "Did you repeat a year at university?", "Do you attend North University?",
        "Are you studying mathematics?", "Are you studying engineering?", "Are you a mature student?", "Are you a part-time student?",
        "Are you a home student?", "Are you studying part-time?", "Do you have academic experience with Python?", "Are you at North University?"]


@pytest.mark.parametrize("label", RS03)
def test_RS03_a_question_about_studies_is_never_yes_from_the_mere_existence_of_a_record(graduates, label):
    for kind, engine in graduates.items():
        result = ask(engine, label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["verified"] is False, (kind, label, result["answer"])


@pytest.mark.parametrize("label,expected", [("Are you currently studying?", ("Yes", None, None, "Yes")), ("Do you have a degree?", ("Yes",) * 4),
                                            ("Do you hold a Master's degree?", (None, None, "Yes", None))])
def test_RS03_the_plain_questions_about_studies_are_still_answered(graduates, label, expected):
    assert tuple(ask(graduates[kind], label, JOB_UK, **RADIO)["answer"] for kind in graduates) == expected


RS04 = ["Is English your second language?", "Is English your only language?", "Is English a foreign language for you?", "Do you need English language support?",
        "Do you hold an English language qualification?", "Have you taken an English language test?", "Was your degree taught in English?", "Can you teach English?",
        "Do you speak English with customers?", "Is French your second language?", "Do you hold a Spanish passport?", "Do you have a Cambridge English certificate?",
        "¿Tienes algún certificado de inglés?", "¿Tienes un título oficial de inglés (B2 o superior)?", "Do you speak English as a second language?"]


@pytest.mark.parametrize("label", RS04)
def test_RS04_a_language_question_that_is_not_about_speaking_it_is_never_answered_from_the_presence_of_the_language(tmp_path, label):
    from candidates import speaker_engine
    for tag, spec in {"fluent": {}, "native": dict(english=("Native", True))}.items():
        engine = speaker_engine(tmp_path, tag, **spec)
        result = ask(engine, label, JOB_UK, **RADIO)
        assert result["answer"] is None and result["verified"] is False, (tag, label, result["answer"])


def test_RS05_a_prompt_for_a_written_answer_is_never_read_from_the_profile(engines):
    from jobagent.intelligence import semantics
    prompts = ["Describe a project you delivered end-to-end", "Describe a time when you delivered a project on time",
               "What was your role in the last project you delivered?", "How do you make decisions based on data?",
               "Describe a time you improved a process based on data", "Describe your experience as a student president",
               "Tell us about your experience of allocating resources", "What did you learn from living abroad?",
               "Tell us about a mobile project", "Walk us through a mobile app you shipped", "Describe the automobile project you worked on",
               "Tell us about your studies in English", "How do you use English in your daily work?",
               "Describe a time you used your language skills at work"]
    for prompt in prompts:
        for field in ({"tag": "textarea", "type": "textarea"}, {"type": "text"}):
            assert semantics.interpret(prompt, field)["intent"] not in plain.FAMILY_OF, (prompt, field)
            result = ask(engines["citizen"], prompt, JOB_UK, **field)
            assert result["verified"] is False and not (result["answer"] and str(result["answer"]) in
                                                       {"London, United Kingdom", "London", "+441234567890", "Native", "Fluent"}), (prompt, result["answer"])


async def test_RS05_such_a_prompt_is_drafted_not_answered_with_a_fact(engines):
    asked = []

    class Model:
        async def answer(self, question, field, job, knowledge, semantic, settings=None):
            asked.append(question)
            return {"answer": None, "facts_used": [], "claims": [], "root_cause": "MISSING_CANDIDATE_FACT"}

    from jobagent.intelligence.engine import CandidateEngine
    local = CandidateEngine(engines["citizen"].db, as_of="2026-10-14", answer_llm=Model())
    for prompt in ("Describe the automobile project you worked on", "How do you make decisions based on data?", "What did you learn from living abroad?"):
        result = await local.answer(prompt, {"tag": "textarea", "type": "textarea"}, dict(JOB_UK), audit=False)
        assert result["answer"] is None, (prompt, result["answer"])
    assert len(asked) == 3, asked  # each was the writing model's business, none a fact of the profile


HEADINGS_THAT_ARE_NOT_THE_APPLICANTS = ["Job 1", "Position 1", "Employer", "Company", "Most recent role", "Placement", "Relevant experience", "Studies",
                                        "Degree 1", "Awards", "Certifications", "Trabajo anterior", "Puesto 1", "Referee", "Previous employment 2"]


@pytest.mark.parametrize("heading", HEADINGS_THAT_ARE_NOT_THE_APPLICANTS)
def test_RS06_a_bare_label_under_any_heading_that_is_not_about_the_applicant_is_never_the_availability_or_the_city(engines, heading):
    for label in ("Start date", "End date", "Location", "City", "Country", "Employer", "Company", "Title"):
        result = ask(engines["citizen"], label, JOB_UK, type="text", section_label=heading)
        assert result["answer"] is None and result["verified"] is False, (heading, label, result["answer"])


@pytest.mark.parametrize("heading", ["", "Application", "Personal information", "Contact details", "About you", "Availability", "Location",
                                     "Your details", "Datos personales", "Application details",
                                     # a heading the engine does not know is no evidence of anything (wave 5): the page's title, the
                                     # job's own title, a name of the form, a section of another kind
                                     "Apply for this job", "My Information", "Senior Engineer at Acme", "Background", "Resume",
                                     "Identification", "Candidate", "Eligibility and compensation", "Material UI demo application"])
def test_RS06_the_same_labels_under_no_heading_or_a_general_one_are_the_applicants(engines, heading):
    assert ask(engines["citizen"], "Start date", JOB_UK, type="text", section_label=heading)["answer"] == "28 September 2027"
    assert ask(engines["citizen"], "City", JOB_UK, type="text", section_label=heading)["answer"] == "London"


def test_RS06_a_block_the_page_repeats_is_not_the_applicants_whatever_it_is_called(engines):
    for label in ("Start date", "Location", "City"):
        result = ask(engines["citizen"], label, JOB_UK, type="text", section_label="Details", _repeated_block=True)
        assert result["answer"] is None and result["root_cause"] == "MISSING_HISTORY_ENTRY", (label, result)


@pytest.mark.parametrize("label,days_14,no_notice", [
    ("Do you have a notice period of more than 4 weeks?", "No", "No"), ("Is your notice period longer than 1 month?", "No", "No"),
    ("Are you subject to a notice period of at least 2 months?", "No", "No"), ("Is your notice period one month or more?", "No", "No"),
    ("Is your notice period 2 weeks or more?", "Yes", "No"), ("Is your notice period less than 3 months?", "Yes", "Yes"),
    ("Do you have a notice period of 30 days or less?", "Yes", "Yes"), ("Is your notice period more than 1 week?", "Yes", "No"),
    ("Is your notice period 14 days?", "Yes", "No"), ("Do you have a notice period of 2 weeks?", "Yes", "No"),
    ("Do you have a notice period?", "Yes", "No"), ("¿Tienes un preaviso de más de 4 semanas?", "No", "No"),
    ("¿Tienes un preaviso de menos de 3 meses?", "Yes", "Yes"), ("Is your notice period negotiable?", None, None),
    ("Is your notice period flexible?", None, None), ("Is your notice period at most one month and also negotiable?", None, None)])
def test_RS07_a_notice_threshold_is_compared_with_the_stored_days_never_answered_from_having_a_notice(engines, label, days_14, no_notice):
    got = (ask(engines["employed"], label, JOB_UK, **RADIO)["answer"], ask(engines["citizen"], label, JOB_UK, **RADIO)["answer"])
    assert got == (days_14, no_notice), label


def test_RS07_a_month_that_could_be_28_to_31_days_never_decides_a_notice_of_30_days(tmp_path):
    from candidates import employed
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    engine = CandidateEngine(employed(Database(tmp_path / "thirty.sqlite3"), days=30), as_of="2026-10-14")
    assert ask(engine, "Is your notice period more than 1 month?", JOB_UK, **RADIO)["answer"] is None
    assert ask(engine, "Is your notice period 30 days or less?", JOB_UK, **RADIO)["answer"] == "Yes"
    assert ask(engine, "Is your notice period more than 2 months?", JOB_UK, **RADIO)["answer"] == "No"
    assert ask(engine, "Is your notice period less than 1 week?", JOB_UK, **RADIO)["answer"] == "No"


@pytest.mark.parametrize("label", ["Are you not based in London?", "Do you live outside London?", "Do you live outside of the United Kingdom?",
                                   "Are you based outside London?", "Are you willing to relocate from London?", "Are you willing to relocate away from London?",
                                   "Will you relocate from London to Madrid?", "Are you based in London or willing to relocate from it?",
                                   "Do you live somewhere other than London?", "Are you a non-resident of the UK?", "Are you based in London only?"])
def test_RS08_a_location_question_that_turns_the_proposition_round_is_never_yes_for_a_person_who_lives_there(engines, label):
    assert answers(engines, label, kinds=("citizen",)) == (None,), label


@pytest.mark.parametrize("label", ["Are you based in London?", "Are you currently based in the UK?", "Do you live in London?", "Do you currently reside in the United Kingdom?"])
def test_RS08_the_plain_location_questions_are_still_yes(engines, label):
    assert answers(engines, label, kinds=("citizen",)) == ("Yes",), label
    assert answers(engines, "Are you based in Madrid?", kinds=("citizen",)) == (None,)  # (a place the profile does not hold is never a 'no')


GRAD = [  # (label, student, citizen[no education: N], graduate, months)
    ("Have you graduated before the end of 2026?", "Yes", "Yes", "Yes", "Yes"), ("Will you have graduated before the end of 2027?", "Yes", "Yes", "Yes", "Yes"),
    ("Have you graduated by the end of 2025?", "No", "No", None, "No"), ("Will you graduate by summer 2027?", None, None, None, None),
    ("Will you graduate in the first half of 2027?", None, None, None, None), ("Have you graduated by summer 2026?", None, None, None, None),
    ("Will you graduate by Q2 2027?", None, None, None, None), ("Will you graduate in the second term of 2027?", None, None, None, None),
    ("Have you completed your degree before 2020?", "No", "No", "No", "No"), ("Do you hold a degree awarded after 2025?", None, None, None, None),
    ("Will you have completed your degree by 2025?", "No", "No", None, "No"),
    ("Will you have graduated by the end of 2027?", "Yes", "Yes", "Yes", "Yes")]


@pytest.mark.parametrize("label,student,citizen,graduate,months", GRAD)
def test_RS09_a_graduation_relation_is_read_to_the_end_of_the_year_and_never_to_a_season_a_half_or_a_quarter(graduates, label, student, citizen, graduate, months):
    got = tuple(ask(graduates[kind], label, JOB_UK, **RADIO)["answer"] for kind in ("student", "citizen", "graduate", "months"))
    assert got == (student, citizen, graduate, months), label


# ---------------------------------------------------------------------------------------------------- RS-10: whose details
@pytest.mark.parametrize("label,field", [
    ("Name", {"description": "Your emergency contact's name"}), ("Email", {"description": "Referee's email address"}),
    ("Phone number", {"placeholder": "Emergency contact phone"}), ("Email", {"description": "The email of your manager"}),
    ("Name", {"section_label": "Recommender"}), ("Name", {"section_label": "Sponsor"}), ("Name", {"section_label": "Second contact"}),
    ("Name", {"section_label": "Person to notify"}), ("Name", {"section_label": "Referral details"}), ("Name", {"section_label": "Peer reviewer"}),
    ("Name", {"section_label": "Co-applicant"}), ("Name", {"section_label": "Co-signer"}), ("Name", {"section_label": "Solicitor"}),
    ("Name", {"section_label": "Additional contact"}), ("Name", {"section_label": "Contact 2"}), ("Name", {"section_label": "Person 2"}),
    ("Email", {"section_label": "Recommender"}), ("Phone", {"section_label": "Contact 3"}), ("Nombre", {"section_label": "Segunda persona de contacto"}),
    # the label itself names the person (no heading is needed to say whose it is)
    ("Recommender's email address", {}), ("Solicitor name", {}), ("Co-signer email", {}), ("Peer reviewer phone number", {}), ("Attorney name", {}),
    ("Nombre", {"section_label": "Abogado"})])
def test_RS10_a_bare_name_email_or_phone_that_the_form_says_is_somebody_elses_is_never_the_applicants(engines, label, field):
    result = ask(engines["citizen"], label, JOB_UK, type="text", **field)
    assert result["answer"] is None and result["canonical_intent"] == "OTHER_PARTY" and result["root_cause"] == "MISSING_OTHER_PARTY_DETAILS", (label, field, result)


@pytest.mark.parametrize("label,field", [
    ("Email", {"description": "We will email you updates; our recruiter will review it"}), ("Name", {"section_label": "Personal information"}),
    ("Name", {"description": "Full name as in your passport"}), ("Phone", {"section_label": "Contact details"}),
    ("Email", {"description": "Please use your personal email"})])
def test_RS10_a_hint_that_only_mentions_somebody_does_not_make_the_field_theirs(engines, label, field):
    assert ask(engines["citizen"], label, JOB_UK, type="text", **field)["canonical_intent"] in {"IDENTITY", "CONTACT"}


# -------------------------------------------------------------------------------------------------------- RS-12: attributes
@pytest.mark.parametrize("label", [
    "Biometric residence permit number", "Street name", "House name", "Building name", "Account name", "Display name", "Profile name",
    "What was the name of your first pet?", "Name of the city where you were born", "Country where you were born", "Where were you born?",
    "Name as it appears on your visa", "Visa issue date", "Visa duration", "Country that issued your visa", "Visa expiry date",
    "How do you plan to obtain a visa?", "Describe any immigration issues", "Visa number", "Visa conditions"])
def test_RS12_a_label_that_asks_for_another_attribute_of_the_same_topic_is_never_answered_with_the_fact(engines, label):
    for kind in ("citizen", "student"):
        result = ask(engines[kind], label, JOB_UK, type="text")
        assert result["answer"] is None, (kind, label, result["answer"])


def test_RS12_no_sentence_about_a_visa_is_written_for_a_route_that_is_not_one(engines):
    """'I currently hold a Citizen visa in the United Kingdom.' is false of a citizen."""
    for label in ("Please explain your visa situation", "Please describe your UK immigration status."):
        assert ask(engines["citizen"], label, JOB_UK, type="text")["answer"] is None, label
        assert "Student visa" in ask(engines["student"], label, JOB_UK, type="text")["answer"], label


# ------------------------------------------------------------------------------------------------------ RS-13 composites
@pytest.mark.parametrize("label", [
    "Are you legally allowed to work in the UK, and will you need sponsorship?", "Will you require sponsorship and do you hold a valid visa?",
    "Are you eligible to work in the UK and willing to travel?", "Are you legally able to work in the UK and available to start in September 2027?",
    "Are you over 18 and authorised to work in the UK?", "Do you require sponsorship or relocation support?",
    "Are you authorised to work in the UK and do you speak English?",
    # two plain halves of a kind that is no work right: composing them would join two answers into one
    "Is your email address and your phone number?", "Are your LinkedIn and your GitHub?"])
def test_RS13_one_yes_no_is_never_the_answer_of_two_predicates(engines, label):
    assert answers(engines, label) == (None, None, None), label


@pytest.mark.parametrize("label", ["Is your email address and your phone number?", "Are your LinkedIn and your GitHub?",
                                   # each half is a plain question of a kind that is no work right, degree or language
                                   "Are you currently based in London and are you available from September 2027?",
                                   "Are you based in the UK and do you have a notice period?"])
def test_RS13_two_plain_halves_in_a_text_field_are_never_joined_into_one_answer(engines, label):
    """A text field takes the composition path ('A; B') for two independent questions, but never for ONE yes/no of two halves."""
    assert answers(engines, label, kinds=("citizen",), type="text") == (None,), label


def test_RS13_one_yes_no_of_two_degree_and_language_predicates_is_not_read_from_one_half(graduates):
    for kind, engine in graduates.items():
        assert ask(engine, "Do you hold a Master's degree and speak English fluently?", JOB_UK, **RADIO)["answer"] is None, kind


# --------------------------------------------------------------------------------------------------------- RS-15 follow-ups
@pytest.mark.parametrize("label,intent", [
    ("Are you currently studying? If so, where?", "EDUCATION"), ("Do you have a degree? If so, in what subject?", "DEGREE"),
    ("Do you speak French? If so, at what level?", "LANGUAGE"), ("Do you speak English? Please indicate your level", "LANGUAGE"),
    ("Are you currently based in London? If not, where are you based?", "LOCATION"),
    ("Do you hold a Master's degree? If so, from which university?", "DEGREE")])
def test_RS15_a_yes_no_with_a_follow_up_is_not_answered_with_a_bare_yes_in_a_text_field(graduates, label, intent):
    engine = graduates["graduate"]
    assert ask(engine, label, JOB_UK, type="text")["answer"] is None, label
    assert plain.read(label, RADIO, intent)["ok"] is False  # nor is a choice control's: it cannot carry the follow-up either


# ---------------------------------------------------------------------------------------------------- RS-16: expected grades
def test_RS16_an_expected_or_predicted_result_is_never_the_finished_result_of_another_degree(graduates):
    for label in ("Expected degree classification", "Predicted grade", "Expected grade", "Predicted degree average", "Expected overall result"):
        for kind in ("student", "graduate"):  # (the graduate has two FINISHED degrees, with grades: neither is a prediction)
            result = ask(graduates[kind], label, JOB_UK, type="text")
            assert result["answer"] is None, (kind, label, result["answer"])
            if result["canonical_intent"] == "GRADE":  # and the user is told why: a prediction is not a fact the profile holds
                assert result["root_cause"] == "MISSING_PREDICTED_GRADE" and "predicted" in result["reason"], (kind, label, result["root_cause"])
    assert ask(graduates["student"], "Degree classification", JOB_UK, type="text")["answer"] == "First Class Honours"  # the finished one stays
    result = ask(graduates["student"], "Class of degree", JOB_UK, type="text")
    assert result["answer"] == "First Class Honours" and result["canonical_intent"] == "GRADE"


# ----------------------------------------------------------------------------------------------------------- RS-17: salary
@pytest.mark.parametrize("label", ["Lowest salary you would accept", "Salary history", "Salary drawn in your last job", "Walk-away salary", "Minimum acceptable salary floor"])
def test_RS17_a_figure_the_policy_is_not_is_never_given_the_expected_salary(engines, label):
    assert ask(engines["citizen"], label, JOB_UK, type="text")["answer"] is None, label


@pytest.mark.parametrize("label", ["Expected salary pro rata", "Part-time salary expectation", "Expected salary in k", "Salary (£000s)", "Expected salary (in £k)",
                                   "Expected salary (full-time equivalent)", "Salary expectation, pro rata"])
def test_RS17_another_basis_or_unit_is_a_format_error_never_the_annual_figure(engines, label):
    result = ask(engines["citizen"], label, JOB_UK, type="text")
    assert result["answer"] is None and result["root_cause"] == "ANSWER_FORMAT_ERROR", (label, result)
    assert ask(engines["citizen"], "Expected salary", JOB_UK, type="text")["answer"] == "60000"


# ------------------------------------------------------------------------------------------------ RS-18: the local model
async def test_RS18_a_local_model_never_moves_a_question_into_a_legal_intent_without_the_legal_reading(engines, monkeypatch):
    from jobagent.intelligence import engine as module
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.semantics import interpret

    async def classifier(*args):
        return {**interpret("Are you authorised to work in the UK?"), "parser": "local_semantic"}

    monkeypatch.setattr(module, "semantic_interpret", classifier)
    local = CandidateEngine(engines["student"].db, as_of="2026-10-14")
    result = await local.answer("Are you permitted to work more than 20 hours a week in the UK?", dict(RADIO), dict(JOB_UK), audit=False)
    assert result["answer"] is None, result["answer"]
    assert result["canonical_intent"] == "OTHER"  # the model's switch into a work right was refused, not the right answered


def test_RS18_a_legal_intent_forced_on_the_engine_runs_the_gate_and_the_legal_reading_anyway(engines):
    from jobagent.intelligence.semantics import interpret
    forced = {**interpret("Are you permitted to work more than 20 hours a week in the UK?"), "intent": "WORK_AUTHORIZATION",
              "parser": "local_semantic", "legal": None}
    result = engines["student"].answer_question("Are you permitted to work more than 20 hours a week in the UK?", dict(RADIO), dict(JOB_UK), semantic=forced)
    assert result["answer"] is None
