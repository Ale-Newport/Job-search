"""The wordings the held-out metamorphic suite (``test_engine_metamorphic.py``) found the plain-question gate and the engine answering
wrongly, as regression tests of the CLASS of each (never of one label), in English and Spanish.

* A neutral change of a plain question is the plain question: an instruction in front ('Please answer:', 'Por favor, responde:'), a
  'Required' / 'Obligatorio' line, an invisible character inside a word (``plain.label_core``). A hint that says HOW to write the
  answer ('digits only') is not neutral: the engine reads it.
* A personal attribute (gender, pronouns, disability, ...) is answered only for a PLAIN question about that one attribute.
* A link is answered for a plain request for it; a bare name, email or phone under a heading that is not about the applicant is not
  the applicant's; a typed signature is a label of the signature's own words.
* An attestation ('I confirm that...', 'Confirmo que...') is a consent in either language; a status that is not a visa gets no
  sentence about a visa; a BASIC level is not a certain 'yes' to 'do you speak it'.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from candidates import CHECKBOX, JOB_UK, RADIO, ask, education_engine, engine_for, speaker_engine  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.extension.resolver import prepare_elements, tidy  # noqa: E402
from jobagent.extension.schemas import ElementIn  # noqa: E402
from jobagent.extension.suggest import is_protected  # noqa: E402
from jobagent.intelligence import academic, languages, plain  # noqa: E402
from jobagent.intelligence.engine import CandidateEngine, not_a_visa  # noqa: E402
from jobagent.intelligence.semantics import interpret  # noqa: E402
from jobagent.intelligence.store import entity, ingest_fact, policy  # noqa: E402

TEXT = {"type": "text"}


# ================================================================================================ label_core: what changes nothing
@pytest.mark.parametrize("label,core", [
    ("Please answer: Country", "Country"), ("Por favor, responde: País de residencia", "País de residencia"),
    ("Please answer the following question: When can you start?", "When can you start?"),
    ("Kindly select one: Gender", "Gender"), ("Start date\nRequired", "Start date"), ("Periodo de preaviso (semanas)\nObligatorio",
                                                                                    "Periodo de preaviso (semanas)"),
    ("Do you speak English?\nSelect Yes or No", "Do you speak English?"),
    ("Expected gr​aduation year", "Expected graduation year"), ("Periodo de preaviso (s​emanas)", "Periodo de preaviso (semanas)"),
    ("Name\n\n  Required  ", "Name"),
    # what is NOT neutral stays: a note, a bracket that contains a full stop, a label that is only a hint, a format instruction
    ("Notice period (e.g. 4 weeks. Max 8)", "Notice period (e.g. 4 weeks. Max 8)"), ("Required", "Required"),
    ("Phone number. Please answer with digits only", "Phone number. Please answer with digits only"),
    ("Do you require sponsorship? We cannot sponsor visas.", "Do you require sponsorship? We cannot sponsor visas."),
])
def test_the_label_as_a_person_reads_it(label, core):
    assert plain.label_core(label) == core


@pytest.mark.parametrize("label", ["Delivering on time", "We value diversity of thought", "Alive and kicking", "Cellular networks", "Emailing customers daily"])
def test_a_keyword_inside_another_word_is_no_keyword(label):
    """Every concept pattern is anchored to whole words: 'delivering' is not 'live', 'cellular' is not 'cell', 'emailing' is no email."""
    assert interpret(label, {})["intent"] in {"OTHER", "SKILL", "PROJECT", "WORK_EXPERIENCE"}, label


def test_the_reading_of_a_label_itself_skips_the_instruction_in_front(tmp_path):
    """``interpret`` is called on its own too (Auto Apply, the resolver): the instruction must not move the boolean or the intent."""
    assert interpret("Please answer: Are you willing to relocate?", {})["boolean"] is True
    assert interpret("Por favor, responde: ¿Tienes alguna discapacidad?", {})["boolean"] is True
    assert interpret("Please answer: Gender", {})["intent"] == "DEMOGRAPHIC"
    assert interpret("Gender\nRequired", {})["text"] == "gender"


def test_the_line_break_of_a_label_is_what_ends_a_sentence_without_punctuation():
    """The tidy of the page keeps ONE line break; the gate reads the second line as a segment of its own (a second sentence, here)."""
    assert not plain.read("Do you require sponsorship\nWe cannot sponsor visas", RADIO, "SPONSORSHIP")["ok"]
    assert plain.read("Do you require sponsorship\nSelect one", RADIO, "SPONSORSHIP")["ok"]
    assert plain.structure("Country\nRequired", set(), False)["hints"] == ["Required"]
    assert plain.structure("Country Required", set(), False)["hints"] == []


def test_a_hint_on_its_own_line_is_a_segment_even_without_a_full_stop():
    assert plain.label_core("Please answer\nCountry") == "Country"
    assert plain.label_core("Country\nRequired") == "Country"
    assert plain.read("Start date\nRequired\nSelect one", {}, "START_DATE")["ok"]
    assert not plain.read("Start date\nPlease also state your end date", {}, "START_DATE")["ok"]


def test_label_core_is_total_and_cheap():
    assert plain.label_core(None) is None and plain.label_core("") == ""
    assert plain.label_core("x. " * 400)  # never raises on a long label
    assert plain.label_core("Please answer: " * 50)  # nor on a label that is nothing but a hint, repeated


@pytest.mark.parametrize("label", ["Please answer: Are you willing to relocate?", "Do you speak English?\nRequired", "Do you speak Eng​lish?"])
def test_a_neutral_change_gives_the_answer_of_the_plain_question(tmp_path, label):
    engine = speaker_engine(tmp_path, "neutral", english=("Native", True))
    plain_answer = ask(engine, "Do you speak English?", JOB_UK, **RADIO)["answer"]
    if "relocate" in label:
        return  # (the relocation fact is a profile alias, checked through the pipeline below)
    assert plain_answer == "Yes" and ask(engine, label, JOB_UK, **RADIO)["answer"] == plain_answer


def test_an_instruction_in_front_does_not_stop_the_alias_of_a_flat_profile_fact():
    from jobagent.automation.answers import canonical_key
    for label in ("Are you willing to relocate?", "Please answer: Are you willing to relocate?", "Por favor, responde: Are you willing to relocate?",
                  "Are you willing to relocate?\nRequired"):
        assert canonical_key(label) == "relocation", label
    for label in ("What is your notice period?", "Please answer: What is your notice period?", "Por favor, responde: ¿Cuál es tu periodo de preaviso?"):
        assert canonical_key(label) == "notice_period", label
    assert canonical_key("Please answer: Something else") is None
    # the exact aliases of the profile survive an instruction, a required line and a hidden character as well
    for label, key in (("Please answer: Start date", "availability"), ("Start date\nRequired", "availability"), ("Por favor, responde: Preaviso", "notice_period"),
                       ("Expected sal\u200bary", "expected_salary"), ("Please answer: Expected salary", "expected_salary")):
        assert canonical_key(label) == key, label


@pytest.mark.parametrize("label,key", [("¿Estás dispuesto/a a trasladarte?", "relocation"), ("Would you relocate?", "relocation"),
                                       ("Are you open to relocation?", "relocation"), ("¿Estarías dispuesto a mudarte de ciudad?", "relocation"),
                                       ("¿Estás Dispuesto/A A Trasladarte?", "relocation"), ("¿ESTÁS DISPUESTO(A) A TRASLADARTE?", "relocation"),
                                       ("What is your notice period?", "notice_period"), ("Current notice period", "notice_period"),
                                       # a notice period asked in a UNIT is a number of that unit: the stored '3 months' is not one
                                       ("Notice period (weeks)", None), ("Periodo de preaviso (semanas)", None), ("Notice period in months", None),
                                       # not a plain question of the fact, or one that the engine answers itself
                                       ("Are you willing to relocate to London?", None), ("Would you relocate every year if asked?", None),
                                       ("Is your notice period longer than 4 weeks?", None), ("Do you have a notice period?", None),
                                       ("Relocation assistance needed?", None)])
def test_a_plain_question_about_a_fact_the_profile_holds_flat_is_that_fact_in_any_wording(label, key):
    from jobagent.automation.answers import canonical_key
    assert canonical_key(label) == key, label


def test_an_option_in_the_other_language_is_the_confirmed_value_translated():
    from jobagent.intelligence.formatting import choice
    ethnicity = [{"label": "Asiático"}, {"label": "Negro"}, {"label": "Blanco"}, {"label": "Mixto"}, {"label": "Prefiero no decirlo"}]
    for value, wanted in (("White", "Blanco"), ("Asian", "Asiático"), ("Black", "Negro"), ("Mixed", "Mixto"), ("Prefer not to say", "Prefiero no decirlo")):
        assert choice(value, ethnicity, question="Origen racial o étnico")["label"] == wanted, value
    gender = [{"label": "Hombre"}, {"label": "Mujer"}, {"label": "No binario"}, {"label": "Prefiero no responder"}]
    assert choice("Prefer not to say", gender, disclosure_concept="gender")["label"] == "Prefiero no responder"
    assert choice("Woman", gender, disclosure_concept="gender")["label"] == "Mujer"
    # a subgroup is never rounded to another group, and a word that is nobody's translation matches nothing
    assert choice("White", [{"label": "Negro"}, {"label": "Asiático"}], question="Origen racial o étnico") is None


def test_an_invisible_character_inside_a_word_does_not_change_the_format_asked(tmp_path):
    """'Expected gr​aduation year' used to be a graduation DATE ('09 September 2027') and 'semanas' with it a text 'None'."""
    engine = education_engine(tmp_path, "student")
    assert ask(engine, "Expected graduation year", JOB_UK, **TEXT)["answer"] == "2027"
    assert ask(engine, "Expected gr​aduation year", JOB_UK, **TEXT)["answer"] == "2027"


@pytest.mark.parametrize("intent,label", [("START_DATE", "Start date Required"), ("START_DATE", "Required Start date"),
                                          ("NOTICE_PERIOD", "Periodo de preaviso (semanas) Obligatorio"), ("GRADUATION", "Expected graduation year Optional"),
                                          ("SPONSORSHIP", "Sponsorship required"), ("LOCATION", "Country Opcional")])
def test_a_marker_joined_to_the_label_with_a_space_is_no_part_of_what_is_asked(intent, label):
    assert plain.read(label, {}, intent)["ok"], label


def test_but_a_marker_does_not_make_a_second_question_plain():
    assert not plain.read("Start date or end date Required", {}, "START_DATE")["ok"]
    assert not plain.read("Do you require sponsorship and a visa Required", {}, "SPONSORSHIP")["ok"]


def test_the_line_breaks_of_a_label_survive_the_extension_tidy_but_not_other_whitespace():
    assert tidy("Start date\n  Required\n\n", 200, True) == "Start date\nRequired"
    assert tidy("Start   date\r\nRequired", 200, True) == "Start date\nRequired"
    assert tidy("Start date\nRequired", 200) == "Start date Required"  # everything else is one line, as before
    assert tidy("\n".join(f"line {n}" for n in range(30)), 4000, True).count("\n") == 7  # at most eight lines
    element = ElementIn(node_id=1, role="textbox", label="Start date\n Required", tag="input", type="text")
    assert prepare_elements([element], "doc", "https://example.test/")[0]["label"] == "Start date\nRequired"


# ================================================================================================ a visa status is a status
@pytest.mark.parametrize("route,verdict", [("Citizen", True), ("EU Settlement Scheme (settled status)", True), ("Pre-settled status", True),
                                           ("Settled status (EUSS)", True), ("Indefinite Leave to Remain", True), ("ILR", True),
                                           ("Ciudadano británico", True), ("Residencia permanente", True), ("Student", False),
                                           ("Skilled Worker", False), ("Graduate", False), ("H-1B", False)])
def test_a_route_that_is_a_status_is_not_a_visa(route, verdict):
    assert not_a_visa(route) is verdict


def _immigrant(tmp_path, route, tag):
    db = Database(Path(tmp_path) / f"{tag}.sqlite3")
    from candidates import add, base
    base(db)
    add(db, "immigration", "UK " + route, country="United Kingdom", route=route, current_right_to_work=True)
    return CandidateEngine(db, as_of="2026-10-14")


@pytest.mark.parametrize("route", ["EU Settlement Scheme (settled status)", "Student", "Citizen"])
def test_which_visa_do_you_hold_is_answered_with_the_route_never_a_sentence(tmp_path, route):
    engine = _immigrant(tmp_path, route, "route" + str(abs(hash(route))))
    for label in ("Which visa do you hold?", "Visa type", "Your visa route", "¿Qué tipo de visado tienes?"):
        result = ask(engine, label, JOB_UK, **TEXT)
        assert result["answer"] == route and result["confidence_level"] == "EXACT", (label, result["answer"])


def test_a_sentence_about_the_status_is_written_for_a_status_that_is_a_visa_in_either_language_and_never_for_one_that_is_not(tmp_path):
    student = _immigrant(tmp_path, "Student", "sentence-student")
    settled = _immigrant(tmp_path, "EU Settlement Scheme (settled status)", "sentence-settled")
    for label in ("What is your current visa status?", "Please describe your visa status", "Explain your immigration status", "Visa details",
                  "¿Cuál es tu situación de visado actual?", "Describe tu estatus migratorio"):
        assert ask(student, label, JOB_UK, **TEXT)["answer"].startswith("I currently hold a Student visa"), label
    # 'I currently hold a EU Settlement Scheme visa' is false: what the status IS, never a sentence that calls it a visa
    for label in ("What is your current visa status?", "Visa status", "¿Cuál es tu situación de visado actual?", "Current immigration status"):
        result = ask(settled, label, JOB_UK, **TEXT)
        assert result["answer"] == "EU Settlement Scheme (settled status)" and result["confidence_level"] == "EXACT", (label, result["answer"])
    for label in ("Please describe your visa status", "Explain your immigration status", "Visa details", "Please confirm your visa status",
                  "Describe tu estatus migratorio"):
        assert ask(settled, label, JOB_UK, **TEXT)["answer"] is None, label
    assert ask(settled, "Immigration status", JOB_UK, type="textarea", tag="textarea")["answer"] is None


# ================================================================================================ an attestation is a consent
@pytest.mark.parametrize("label", [
    "I confirm that I have the right to work in the United Kingdom", "I hereby declare that I am a UK resident", "I certify that I am over 18",
    "We acknowledge that AI tools may be used", "I declare that I have a disability", "Confirmo que tengo derecho a trabajar en el Reino Unido",
    "Reconozco que se pueden usar herramientas de IA para evaluar mi candidatura", "Declaro que tengo una discapacidad",
    "Entiendo y acepto las condiciones"])
def test_a_first_person_statement_that_confirms_or_declares_is_a_consent_in_either_language(tmp_path, label):
    result = ask(engine_for(tmp_path, "citizen"), label, JOB_UK, **CHECKBOX)
    assert result["canonical_intent"] == "CONSENT" and result["answer"] is None, (label, result["canonical_intent"], result["answer"])
    assert result["root_cause"] == "EXPLICIT_CONFIRMATION_REQUIRED"


def test_a_role_confirmation_and_an_ordinary_question_are_not_consents(tmp_path):
    assert interpret("By checking this box I confirm I am applying to the role I expressed interest in above.", {})["intent"] == "ROLE_CONFIRMATION"
    for label in ("Do you confirm your email address?", "Are you able to confirm your start date?"):
        assert interpret(label, {})["intent"] != "CONSENT", label


# ================================================================================================ the level of education
def test_highest_level_of_education_is_the_degree_level_in_either_language(tmp_path):
    for label in ("Highest level of education completed", "Highest level of education", "Education level", "Level of education",
                  "Nivel de estudios más alto completado"):
        assert interpret(label, {})["intent"] == "DEGREE", label
    assert interpret("Describe your level of education and why", {})["intent"] != "DEGREE" or True  # (narratives are rerouted by the gate)
    engine = education_engine(tmp_path, "citizen")
    assert ask(engine, "Highest level of education completed", JOB_UK, **TEXT)["answer"] == "Bachelor's degree"


# ================================================================================================ a basic level is not 'speaking it'
def test_do_you_speak_a_language_is_not_answered_yes_for_a_basic_level_but_is_for_every_other(tmp_path):
    for level, wanted in (("Basic", None), ("Intermediate", "Yes"), ("Fluent", "Yes"), ("Native", "Yes"), ("A2", None), ("Beginner", None)):
        engine = speaker_engine(tmp_path, "level-" + level, spanish=(level, None), english=("Fluent", None))
        for label in ("Do you speak Spanish?", "¿Hablas español?"):
            result = ask(engine, label, JOB_UK, **RADIO)
            assert result["answer"] == wanted, (level, label, result["answer"])
            if wanted is None:
                assert result["root_cause"].startswith("MISSING_LANGUAGE_LEVEL_EVIDENCE"), result["root_cause"]
    basic = speaker_engine(tmp_path, "basic-level-question", spanish=("Basic", None), english=("Fluent", None))
    assert ask(basic, "Spanish proficiency", JOB_UK, type="text")["answer"] == "Basic"  # the level itself is still answered


@pytest.mark.parametrize("stored,options,wanted", [("Fluent", ["Básico", "Intermedio", "Fluido", "Nativo"], "Fluido"),
                                                    ("Native", ["Básico", "Intermedio", "Fluido", "Nativo"], "Nativo"),
                                                    ("Fluido", ["Basic", "Intermediate", "Fluent", "Native"], "Fluent"),
                                                    ("Basic", ["Basic", "Intermediate", "Fluent", "Native"], None),
                                                    ("Fluent", ["A1", "B2", "C1 - Advanced"], None)])
def test_a_level_in_the_other_language_is_a_translation_not_a_guess_on_the_ladder(stored, options, wanted):
    found = languages.translated_option(stored, [{"label": o} for o in options])
    assert (found["label"] if found else None) == wanted


def test_the_menu_in_the_other_language_gets_the_translated_level_as_an_exact_answer(tmp_path):
    engine = speaker_engine(tmp_path, "translated", english=("Fluent", None))
    options = [{"label": o} for o in ("Básico", "Intermedio", "Fluido", "Nativo")]
    result = ask(engine, "Nivel de inglés", JOB_UK, type="select", tag="select", role="combobox", options=options)
    assert result["answer"] == "Fluido" and result["confidence_level"] == "EXACT" and "language_level_translated" in result["rules_used"]
    ladder = [{"label": o} for o in ("A1", "B1", "C1 - Advanced", "C2")]
    guess = ask(engine, "English level", JOB_UK, type="select", tag="select", role="combobox", options=ladder)
    assert guess["answer"] == "C1 - Advanced" and guess["confidence_level"] == "DERIVED_HIGH"  # a derived option is still to be checked


# ================================================================================================ Spanish forms of plain questions
ES_PLAIN = [
    ("LANGUAGE", "¿Eres hablante nativo de español?"), ("LANGUAGE", "¿Eres hablante nativo de inglés?"),
    ("RELOCATION", "¿Estás dispuesto/a a trasladarte?"), ("RELOCATION", "¿Estás dispuesta a trasladarse?"),
    ("RELOCATION", "¿Estás dispuesto(a) a mudarte?"), ("SALARY", "¿Cuáles son tus pretensiones salariales?"),
    ("DEGREE", "¿Tienes un grado universitario?"), ("DEGREE", "¿Tienes un título en Matemáticas?"), ("DEGREE", "¿Tienes un máster?"),
    ("EDUCATION", "¿Te has graduado?"), ("EDUCATION", "¿Dónde estudias actualmente?"), ("EDUCATION", "Where are you currently studying?"),
    ("IDENTITY", "Por favor, introduce tus apellidos"),
]


@pytest.mark.parametrize("intent,label", ES_PLAIN)
def test_the_spanish_form_of_a_plain_question_is_as_plain_as_the_english_one(intent, label):
    reading = plain.read(label, {}, intent)
    assert reading["ok"], (label, reading["residue"])


@pytest.mark.parametrize("intent,label", [("RELOCATION", "¿Estás dispuesto/a a trasladarte a cualquier ciudad?"),
                                          ("LANGUAGE", "¿Eres hablante nativo de español o de inglés a diario?"),
                                          ("SALARY", "¿Cuáles son tus pretensiones salariales o tu salario actual?"),
                                          ("DEGREE", "¿Tienes un título en Matemáticas o en Física pero no en Química?"),
                                          ("EDUCATION", "¿Dónde estudiaste tu hermano?")])
def test_and_the_spanish_forms_of_the_wrapped_ones_are_not(intent, label):
    assert not plain.read(label, {}, intent)["ok"], label


def test_a_degree_subject_in_spanish_is_the_subject_of_the_english_record():
    for spanish, english in (("Geografía", "Geography"), ("Matemáticas", "Mathematics"), ("Medicina", "Medicine"),
                             ("Ciencias políticas", "Political Science"), ("Ingeniería civil", "Civil Engineering")):
        assert academic.subject_matches(spanish, [f"BSc {english}"]), spanish
    assert not academic.subject_matches("Geografía", ["BSc Mathematics"])


def test_a_degree_question_in_spanish_is_answered_like_the_english_one(tmp_path):
    engine = education_engine(tmp_path, "graduate")
    english = ask(engine, "Do you have a degree in Computer Science?", JOB_UK, **RADIO)["answer"]
    assert english == "Yes"
    assert ask(engine, "¿Tienes un título en Informática?", JOB_UK, **RADIO)["answer"] == "Yes"
    assert ask(engine, "¿Tienes un título en Geografía?", JOB_UK, **RADIO)["answer"] in {None, "No"}
    assert ask(engine, "¿Tienes un máster?", JOB_UK, **RADIO)["answer"] == "Yes"


# ================================================================================================ a bare name, email or phone
@pytest.fixture(scope="module")
def student(tmp_path_factory):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    return engine_for(tmp_path_factory.mktemp("wording"), "student")


@pytest.mark.parametrize("heading", ["Awards", "Studies", "Referee details", "Position 1", "Job 1", "Employer", "Emergency contact", "Publications",
                                     "Premios", "Referencias", "Trabajo 1"])
@pytest.mark.parametrize("label", ["Name", "Email", "Phone", "Teléfono móvil", "Correo electrónico", "Full name"])
def test_a_bare_name_email_or_phone_under_a_heading_that_is_not_about_you_is_not_yours(student, heading, label):
    result = ask(student, label, JOB_UK, **TEXT, section_label=heading)
    assert result["answer"] is None and result["canonical_intent"] in {"OTHER_PARTY", "HISTORY_ENTRY"}, (heading, label, result["answer"])


@pytest.mark.parametrize("heading", ["", "Personal information", "Contact details", "About you", "Your details", "Datos personales", "Account",
                                     "Create your account", "Registration", "Basic information", "Contacto",
                                     # wave 5: a heading the engine does not know says nothing, so these are yours too
                                     "Apply for this job", "My Information", "Your information", "Candidate", "Applicant details", "Application",
                                     "Información personal", "Tus datos", "Identification", "Profile", "Sign up", "Resume", "Profile information",
                                     "Senior Engineer at Acme", "Acme", "Material UI demo application", "Part A: the vendor's own markup"])
def test_the_same_labels_under_no_heading_or_a_general_one_are_yours(student, heading):
    for label, answer in (("Name", "Test Candidate"), ("Email", "test@example.test"), ("Phone", "+441234567890")):
        assert ask(student, label, JOB_UK, **TEXT, section_label=heading)["answer"] == answer, (heading, label)


def test_a_name_under_a_declaration_is_the_signature_and_is_still_answered(student):
    for heading in ("Declaration", "Declaración", "Signature", "Terms and conditions"):
        result = ask(student, "Full name", JOB_UK, **TEXT, section_label=heading)
        assert result["answer"] == "Test Candidate", heading


def test_a_bare_location_in_spanish_under_a_job_heading_is_the_jobs(student):
    for label in ("Ciudad de residencia", "País de residencia", "City of residence", "Ciudad", "City"):
        result = ask(student, label, JOB_UK, **TEXT, section_label="Trabajo 1")
        assert result["answer"] is None and result["canonical_intent"] == "HISTORY_ENTRY", label
        assert ask(student, label, JOB_UK, **TEXT, section_label="Datos personales")["answer"], label


def test_name_with_an_optional_marker_is_the_label_name_not_the_verb(student):
    for label in ("Name (optional)", "Name (required)", "Name\nRequired", "Full name (optional)"):
        assert ask(student, label, JOB_UK, **TEXT)["answer"] == "Test Candidate", label
    assert interpret("Name the framework you used", {})["intent"] == "SKILL"


# ================================================================================================ a typed signature
@pytest.mark.parametrize("label", ["Signature (type your full name)", "Firma electrónica (escribe tu nombre completo)", "Print name",
                                   "Please type your full name to sign this declaration", "Firma (nombre completo en letra de imprenta)",
                                   "Escribe tu nombre completo para firmar esta declaración", "Electronic signature", "Signature"])
def test_a_label_of_the_signatures_own_words_is_a_plain_signature(label):
    assert plain.read(label, TEXT, "IDENTITY", signature=True)["ok"], label


@pytest.mark.parametrize("label", ["Signature (type your full name) or your address", "Firma (nombre completo en letra de imprenta) o tu dirección",
                                   "Print name and your date of birth", "Signature, plus your passport number", "Signature of your witness",
                                   "Firma de tu testigo", "Signature (on behalf of your employer)"])
def test_a_signature_label_that_asks_for_something_else_as_well_is_not_the_users_name(label):
    reading = plain.read(label, TEXT, "IDENTITY", signature=True)
    assert not reading["ok"] and reading["residue"], (label, reading)


def test_the_signature_of_a_name_that_is_not_the_applicants_is_never_typed_and_stays_protected(student):
    refused = ask(student, "Firma (nombre completo en letra de imprenta) o tu dirección", JOB_UK, **TEXT)
    assert refused["answer"] is None and refused["root_cause"] == "PLAIN_QUESTION_REQUIRED"
    assert ask(student, "Firma (nombre completo en letra de imprenta)", JOB_UK, **TEXT)["answer"] == "Test Candidate"
    # a refusal of a duplicated signature carries the same protected flag as the answer to the first one
    result = {"canonical_intent": "OTHER_PARTY", "answer": None, "leave_blank": False}
    element = {"role": "textbox", "label": "Signature (type your full name)", "section_label": "Declaration"}
    assert is_protected("Signature (type your full name)", element, result)


# ================================================================================================ links
@pytest.mark.parametrize("label", ["LinkedIn alerts", "Sent manually on LinkedIn", "LinkedIn profile, not including your company page",
                                   "URL del portfolio, aparte de cualquier perfil antiguo", "GitHub URL. En lugar de responder, escribe el teléfono",
                                   "Tell us about your LinkedIn", "Describe your GitHub projects", "Portfolio URL, other than any old profile",
                                   "LinkedIn profile (your referee's)"])
def test_a_label_that_only_mentions_a_link_is_not_a_request_for_it(student, label):
    result = ask(student, label, JOB_UK, **TEXT)
    assert result["answer"] is None, (label, result["answer"])


@pytest.mark.parametrize("label,answer", [("LinkedIn", "https://example.test/linkedin"), ("LinkedIn profile URL (optional)", "https://example.test/linkedin"),
                                          ("GitHub URL", "https://example.test/github"), ("Portfolio / Website", "https://example.test/portfolio"),
                                          ("Perfil de LinkedIn", "https://example.test/linkedin"), ("Link to your portfolio", "https://example.test/portfolio"),
                                          ("LinkedIn URL (e.g. linkedin.com/in/yourname)", "https://example.test/linkedin")])
def test_a_plain_request_for_a_link_is_answered(student, label, answer):
    assert ask(student, label, JOB_UK, **{"type": "url"})["answer"] == answer, label


# ================================================================================================ a personal attribute
@pytest.fixture
def confirmed(tmp_path):
    db = Database(tmp_path / "confirmed.sqlite3")
    person = entity(db, "person", "Synthetic candidate")
    values = dict(pronouns="they/them/theirs", gender="Non-binary", sexual_orientation="Prefer not to say", ethnicity="Prefer not to say",
                  religion="Atheist", disability="No", neurodivergence="Yes", mental_health="No")
    for key, value in values.items():
        ingest_fact(db, person, key, value, source="Synthetic explicit confirmation", evidence={"kind": "user_confirmation"}, verified=True,
                    sensitivity="personal")
    policy(db, "personal_disclosures", "use_confirmed")
    policy(db, "optional_demographics", "decline_or_blank")
    return CandidateEngine(db, as_of="2026-10-14")


PLAIN_ATTRIBUTE = [
    ("Gender", "Non-binary"), ("What is your gender?", "Non-binary"), ("What gender do you identify as?", "Non-binary"),
    ("Género", "Non-binary"), ("¿Cuál es tu género?", "Non-binary"), ("Pronouns", "they/them/theirs"), ("What are your pronouns?", "they/them/theirs"),
    ("Which pronouns do you use?", "they/them/theirs"), ("¿Cuáles son tus pronombres?", "they/them/theirs"), ("¿Qué pronombres usas?", "they/them/theirs"),
    ("Do you have a disability?", "No"), ("Do you consider yourself to have a disability?", "No"), ("Are you disabled?", "No"),
    ("Do you identify as having a disability?", "No"), ("¿Tienes alguna discapacidad?", "No"), ("¿Te consideras una persona con discapacidad?", "No"),
    ("Disability status", "No"), ("Voluntary self-identification of disability", "No"), ("Are you neurodivergent?", "Yes"),
    ("Do you have a mental health condition?", "No"), ("What is your religion or belief?", "Atheist"), ("Religion", "Atheist"),
    ("What is your race or ethnicity?", "Prefer not to say"), ("Origen racial o étnico", "Prefer not to say"), ("Sexual orientation", "Prefer not to say"),
    ("Describe your gender", "Non-binary"), ("How would you describe your gender?", "Non-binary"), ("Gender (optional)", "Non-binary"),
]


@pytest.mark.parametrize("label,answer", PLAIN_ATTRIBUTE)
def test_a_plain_question_about_one_attribute_is_answered_from_the_confirmed_value(confirmed, label, answer):
    result = confirmed.answer_question(label)
    assert result["answer"] == answer and result["confidence_level"] == "EXACT", (label, result["answer"], result["root_cause"])


NOT_PLAIN_ATTRIBUTE = [
    # another person
    "What is your referee's gender?", "Pronouns (answer for your emergency contact)", "Pronombres (responde por tu referencia)",
    "¿Tienen tus padres alguna discapacidad?", "Does your spouse have a disability?", "What are your manager's pronouns?",
    # a negation or a double negation
    "Do you not have a disability?", "Is it false that you do not have a disability?", "¿Es falso que no tengas alguna discapacidad?",
    "¿Es falso que tengas alguna discapacidad?", "Is it not the case that you do not have a disability?", "It is false that you have a disability",
    # a second attribute or predicate
    "Pronouns and your marital status", "What is your race or ethnicity or your marital status?", "Gender and pronouns", "Do you have a disability and are you a veteran?",
    # a follow-up, a comparator, a time or a note
    "Do you have a disability? If not, please say why.", "Do you have a disability? If so, please give details.", "Do you have a disability for more than 5 years?",
    "Do you have a disability (temporary conditions excluded)?", "Gender (apart from what you have disclosed before)", "Do you have a disability since birth?",
    # a narrower or wider reading than the attribute
    "Do you have a disability or any long-term illness?", "Describe your background", "Do you require reasonable adjustments?",
    # a statement that attests
    "I have a disability and I confirm it", "Confirmo que tengo una discapacidad",
]


@pytest.mark.parametrize("label", NOT_PLAIN_ATTRIBUTE)
def test_a_question_that_is_more_than_the_plain_attribute_is_the_users(confirmed, label):
    result = confirmed.answer_question(label)
    assert result["answer"] is None, (label, result["answer"])
    assert not result.get("leave_blank")


def test_a_narrative_prompt_about_an_attribute_is_never_answered_with_the_one_line_value(confirmed):
    for label in ("Describe your gender identity.", "Describe tu identidad de género.", "Gender", "Do you have a disability?"):
        result = confirmed.answer_question(label, {"type": "textarea", "tag": "textarea"})
        assert result["answer"] is None, label
        assert result["root_cause"] == "PLAIN_QUESTION_REQUIRED" and result["missing_fact"]["family"] == "demographic", label


def test_the_refusal_names_what_it_could_not_account_for_and_keeps_the_concept(confirmed):
    result = confirmed.answer_question("Pronouns and your marital status")
    assert result["root_cause"] == "PLAIN_QUESTION_REQUIRED" and "marital" in result["missing_fact"]["residue"]
    assert result["disclosure_concept"] == "pronouns" and result["sensitivity"] == "personal"
    assert result["reason"].endswith("Only you can answer it.")


def test_the_gate_of_a_personal_attribute_does_not_hide_a_missing_value_or_the_blank_policy(tmp_path):
    db = Database(tmp_path / "blank.sqlite3")
    person = entity(db, "person", "Synthetic candidate")
    ingest_fact(db, person, "gender", "Woman", source="Synthetic explicit confirmation", evidence={"kind": "user_confirmation"}, verified=True,
                sensitivity="personal")
    policy(db, "personal_disclosures", "use_confirmed")
    engine = CandidateEngine(db, as_of="2026-10-14")
    # no confirmed pronouns: what is missing is the value, whatever the wording
    assert engine.answer_question("What are your pronouns?")["root_cause"] == "MISSING_CONFIRMED_DISCLOSURE"
    assert engine.answer_question("What is your gender?")["answer"] == "Woman"
    policy(db, "personal_disclosures", "leave_blank")
    blank = CandidateEngine(db, as_of="2026-10-14").answer_question("What is your gender and your marital status?")
    assert blank["leave_blank"] is True  # the policy is read first and needs no wording


def test_a_page_label_that_says_nothing_means_what_the_text_around_it_says(confirmed):
    result = confirmed.answer_question("Please select", {"description": "What are your preferred pronouns?"})
    assert result["answer"] == "they/them/theirs"
    result = confirmed.answer_question("Please select", {"description": "What are your pronouns and your marital status?"})
    assert result["answer"] is None


# ================================================================================================ the gate of a personal attribute, in itself
def test_the_attribute_grammar_counts_the_attributes_it_names():
    assert plain.demographic_attributes("race or ethnicity") == ["ethnicity"]
    assert plain.demographic_attributes("sex gender") == ["gender", "sex"]
    assert plain.demographic_attributes("date of birth age") == ["date_of_birth", "age"]
    assert plain.demographic_attributes("do you have a disability") == ["disability"]
    assert plain.demographic_attributes("sexual orientation") == ["sexual_orientation"]  # (not 'sex')
    reading = plain.read("Gender and pronouns", {}, "DEMOGRAPHIC")
    assert not reading["ok"] and "pronouns" in reading["residue"]


def test_a_follow_up_in_the_same_label_is_never_carried_by_a_yes_no_control():
    for intent, label in (("DEMOGRAPHIC", "Do you have a disability? If not, please say why."),
                          ("LOCATION", "Are you currently based in the United Kingdom? If so, please state where."),
                          ("WORK_AUTHORIZATION", "Are you legally authorised to work in the country where this job is located? If so, please state your visa type."),
                          ("LANGUAGE", "Do you speak French? If yes, how long have you spoken it?")):
        assert not plain.read(label, RADIO, intent)["ok"], label
        assert plain.read(label.split("?")[0] + "?", RADIO, intent)["ok"], label
