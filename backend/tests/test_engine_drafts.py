"""What a long answer is asked for, in which language, and what it may not state (``intelligence/answer_evidence.py``).

* CPL-03: the requirements of a draft were always {'language': 'English'} and knew only the English words 'words', 'characters',
  'up to': a Spanish form was answered in English and its 'máximo 120 palabras' was never enforced.
* SAFE-21: the deterministic validator accepted an uncited employer ('I worked at Google'), a spelled-out number ('fifty
  engineers'), a present-tense job ('I currently work at...'), a private fact and a degree presented as finished whose record is
  in progress, as long as one claim cited a fact: the safety rested on the reviewing model alone.
"""
from __future__ import annotations

import pytest

from jobagent.intelligence.answer_evidence import (AnswerEvidenceValidator, credential_issues, detect_language,  # noqa: E402
                                                   entailment_issues, fabrication_issues, language_of, requirements,
                                                   wrong_language_issue)

FACTS = [
    {"id": "f1", "key": "story", "value": "I led six engineers to deliver a responsive website.", "entity": "Example Studio",
     "entity_id": "e1", "category": "experience"},
    {"id": "f2", "key": "status", "value": "studying", "entity": "South University", "entity_id": "e2", "category": "education"},
    {"id": "f3", "key": "subject", "value": "Artificial Intelligence", "entity": "South University", "entity_id": "e2",
     "category": "education"},
    {"id": "f4", "key": "story", "value": "I built a vision pipeline using Python.", "entity": "First Project", "entity_id": "e3",
     "category": "project"},
    {"id": "f5", "key": "technologies", "value": ["Python"], "entity": "First Project", "entity_id": "e3", "category": "project"}]
CONTEXT = "Why do you want to work at Acme Robotics? Acme Robotics Software Engineer London"


def issues(answer, cited=("f1",), context=CONTEXT):
    return fabrication_issues(answer, FACTS, [f for f in FACTS if f["id"] in cited], context)


def generated(answer, claim="I led six engineers", fact="f1", **extra):
    return {"question_id": "q", "intent": "MOTIVATION", "answer": answer, "facts_used": [fact], "answers_question": True,
            "follows_instructions": True, "claims": [{"text": claim, "fact_ids": [fact]}], **extra}


# ----------------------------------------------------------------------------------------------- CPL-03: the requirements
@pytest.mark.parametrize("question,expected", [
    ("¿Por qué quieres trabajar en Acme Robotics? Máximo 120 palabras.", {"language": "Spanish", "max_words": 120}),
    ("Motivación para presentarte a esta oferta (máximo 120 palabras)", {"language": "Spanish", "max_words": 120}),
    ("Cuéntanos tu experiencia. Como máximo 80 palabras", {"language": "Spanish", "max_words": 80}),
    ("Describe tu proyecto en no más de 150 palabras", {"language": "Spanish", "max_words": 150}),
    ("Describe tu proyecto en menos de 100 palabras", {"language": "Spanish", "max_words": 99}),
    ("Háblanos de ti. Entre 80 y 120 palabras", {"language": "Spanish", "min_words": 80, "max_words": 120}),
    ("Explica tu motivación (100-150 palabras)", {"language": "Spanish", "min_words": 100, "max_words": 150}),
    ("Aproximadamente 150 palabras sobre tu motivación", {"language": "Spanish", "min_words": 135, "max_words": 165, "target_words": 150}),
    ("Escribe exactamente 60 palabras sobre tu experiencia", {"language": "Spanish", "min_words": 60, "max_words": 60, "target_words": 60}),
    ("Describe tu proyecto en hasta 1000 caracteres", {"language": "Spanish", "max_characters": 1000}),
    ("Resume tu experiencia en una frase", {"language": "Spanish", "sentences": 1}),
    ("Describe dos ejemplos de liderazgo", {"language": "Spanish", "examples": 2}),
    ("Lista tus logros con viñetas", {"language": "Spanish", "bullets": True}),
    ("Why do you want to work at Acme Robotics? Maximum 120 words.", {"language": "English", "max_words": 120}),
    ("Tell us about yourself in one sentence", {"language": "English", "sentences": 1}),
    ("Describe a project. Up to 1000 characters", {"language": "English", "max_characters": 1000})])
def test_the_language_and_the_limits_of_a_question_in_either_language(question, expected):
    got = requirements(question, {"type": "textarea"})
    assert {k: v for k, v in got.items() if k in expected} == expected, got
    assert got["language"] == expected["language"]


def test_the_html_limit_of_the_field_still_holds_in_any_language():
    assert requirements("¿Por qué quieres trabajar aquí?", {"max_length": 900})["max_characters"] == 900
    assert requirements("Describe tu proyecto en hasta 500 caracteres", {"max_length": 900})["max_characters"] == 500


@pytest.mark.parametrize("question,page,language", [
    ("Carta de presentación", "es-ES", "Spanish"), ("Cover letter", "es", "Spanish"), ("Cover letter", "en-GB", "English"),
    ("Tell us about yourself", "es", "English"), ("¿Por qué Acme?", "en", "Spanish"), ("Why Acme?", "", "English"),
    ("Pourquoi voulez-vous travailler ici", "fr", "French"), ("Additional information", "", "English"),
    ("Información adicional", "", "Spanish")])
def test_the_language_a_draft_is_written_in(question, page, language):
    assert detect_language(question, page) == language


def test_the_page_language_reaches_the_requirements_through_the_field():
    assert requirements("Cover letter", {"_page_lang": "es-ES"})["language"] == "Spanish"
    assert requirements("Cover letter", {})["language"] == "English"
    assert requirements("Carta de presentación", {})["language"] == "Spanish"  # the question's own words


# ----------------------------------------------------------------------------------------------- SAFE-21: what it may not state
@pytest.mark.parametrize("answer,needle", [
    ("I worked at Google for ten years.", "Google"), ("At Globex Corporation I built a platform.", "Globex Corporation"),
    ("I joined Initech Ltd after my degree.", "Initech Ltd"), ("I worked for Umbrella Labs on data pipelines.", "Umbrella Labs"),
    ("En Globex Corporation construí una plataforma.", "Globex Corporation"), ("Trabajé en Banco Santander durante un año.", "Banco Santander"),
    ("Worked with Stark Industries in the summer.", "Stark Industries")])
def test_an_employer_nobody_gave_is_refused(answer, needle):
    found = [i for i in issues(answer) if "employer or organisation" in i]
    assert found and needle in found[0], (answer, found)


@pytest.mark.parametrize("answer", [
    "I led six engineers at Example Studio.", "I am studying Artificial Intelligence at South University.",
    "I built a vision pipeline using Python.", "I love Acme Robotics and its mission.", "I built it using Python at Example Studio.",
    "Dirigí a seis ingenieros en Example Studio.", "Estudio Inteligencia Artificial en South University.",
    "I want to work at Acme Robotics as a Software Engineer in London.", "I speak English and Spanish.",
    "In March I started; on Monday I finished."])
def test_what_the_evidence_and_the_question_name_is_never_refused(answer):
    assert not [i for i in issues(answer, cited=("f1", "f2", "f4")) if "employer or organisation" in i], answer


@pytest.mark.parametrize("answer,word", [
    ("I led a team of fifty engineers.", "fifty"), ("I managed ten developers.", "ten"), ("We shipped to a hundred customers.", "hundred"),
    ("I worked there for twelve months.", "twelve"), ("Dirigí a cincuenta ingenieros.", "cincuenta"), ("Trabajé diez años.", "diez"),
    ("Llevé a cabo cien pruebas.", "cien")])
def test_a_spelled_out_number_no_fact_states_is_refused_in_either_language(answer, word):
    assert any("number " + word in i for i in issues(answer)), (answer, issues(answer))


def test_a_spelled_out_number_the_evidence_states_is_not_refused():
    for answer in ("I led six engineers.", "Dirigí a seis ingenieros.", "I led 6 engineers."):
        assert not [i for i in issues(answer) if "number" in i and "absent" in i and "six" not in answer], answer
    assert not issues("I led six engineers.")
    assert not [i for i in issues("Dirigí a seis ingenieros en Example Studio.")]


@pytest.mark.parametrize("answer", ["I hold a Student visa in the UK.", "I am a Spanish citizen.", "My nationality is Spanish.",
                                    "I was born in Madrid.", "My salary expectations are 60000.", "Tengo un visado de estudiante.",
                                    "Soy de nacionalidad española.", "I need sponsorship."])
def test_private_information_the_cited_evidence_does_not_hold_is_refused(answer):
    assert any("private information" in i for i in issues(answer)), answer


@pytest.mark.parametrize("answer", [
    "I hold a Master's degree in AI from South University.", "I completed my master's at South University.",
    "I graduated from South University last year.", "He completado mi máster en South University."])
def test_a_degree_presented_as_finished_whose_record_is_in_progress_is_refused(answer):
    assert any("presented as finished" in i for i in issues(answer, cited=("f2", "f3"))), answer


def test_a_degree_that_is_finished_may_be_said_to_be():
    done = [{**f, "value": "completed"} if f["key"] == "status" else f for f in FACTS]
    assert not fabrication_issues("I completed my master's at South University.", done, done[:2], CONTEXT)


@pytest.mark.parametrize("answer", ["I currently work at Example Studio as a Developer.", "I work for Acme.", "I am now employed by Acme.",
                                    "Currently, I work at Example Studio.", "At present I am working at Example Studio.",
                                    "Actualmente trabajo en Example Studio.", "Trabajo para Acme.", "My current role is Developer."])
def test_a_present_tense_job_nobody_confirmed_is_refused(answer):
    found = AnswerEvidenceValidator().validate(generated(answer), FACTS, "q", "MOTIVATION", {"language": "English"})
    assert any("current employment" in i for i in found), (answer, found)


def test_a_confirmed_present_job_may_be_said():
    facts = [*FACTS, {"id": "f6", "key": "current_company", "value": "Example Studio", "category": "person", "entity": "Candidate"}]
    assert not [i for i in AnswerEvidenceValidator().validate(generated("I currently work at Example Studio."), facts, "q", "MOTIVATION",
                                                              {"language": "English"}) if "current employment" in i]


# ------------------------------------------------------------------------ CPL-03: a Spanish answer to English evidence
def test_a_spanish_answer_whose_claims_quote_the_english_evidence_is_validated():
    spanish = generated("Dirigí a seis ingenieros en Example Studio para entregar un sitio web adaptable.",
                        claim="I led six engineers to deliver a responsive website")
    assert not AnswerEvidenceValidator().validate(spanish, FACTS, "q", "MOTIVATION", {"language": "Spanish", "max_words": 120},
                                                  context_text=CONTEXT)


def test_the_same_spanish_answer_is_not_validated_against_the_english_word_overlap_it_cannot_have():
    claim = "I led six engineers to deliver a responsive website"
    found = entailment_issues("Dirigí a seis ingenieros para entregar un sitio web adaptable.", [{"text": claim, "fact_ids": ["f1"]}],
                              {f["id"]: f for f in FACTS}, ["f1"], cross_lingual=True)
    assert not [i for i in found if "not reflected" in i]
    assert [i for i in entailment_issues("Dirigí a seis ingenieros para entregar un sitio web adaptable.",
                                         [{"text": claim, "fact_ids": ["f1"]}], {f["id"]: f for f in FACTS}, ["f1"])
            if "not reflected" in i], "an English answer is still held to it"


def test_a_spanish_claim_that_is_no_quote_of_the_evidence_is_still_refused():
    """The claim is what the answer relies on: a claim about work the evidence never mentions fails in any language."""
    invented = generated("Construí un compilador en Rust.", claim="I built a compiler in Rust with Haskell and Erlang")
    found = AnswerEvidenceValidator().validate(invented, FACTS, "q", "MOTIVATION", {"language": "Spanish"}, context_text=CONTEXT)
    assert any("absent from cited evidence" in i for i in found), found


def test_a_spanish_answer_over_its_limit_in_spanish_words_is_refused():
    long = "Dirigí a seis ingenieros en Example Studio. " + "Disfruto resolviendo problemas. " * 40
    found = AnswerEvidenceValidator().validate(generated(long, claim="I led six engineers"), FACTS, "q", "MOTIVATION",
                                               requirements("Describe tu experiencia (máximo 50 palabras)", {}), context_text=CONTEXT)
    assert any("INSTRUCTION_COMPLIANCE: max_words" in i for i in found), found


def test_an_honest_english_draft_is_still_accepted():
    ok = generated("I led six engineers to deliver a responsive website at Example Studio.", claim="I led six engineers")
    assert not AnswerEvidenceValidator().validate(ok, FACTS, "q", "MOTIVATION", {"language": "English"}, context_text=CONTEXT)


# ------------------------------------------------------------------------------ RS-14: a Spanish draft of English facts
CITED = [f for f in FACTS if f["id"] in ("f4", "f5")]
SPANISH_DRAFT = ("Construí un sistema de visión por computador con Python y OpenCV para detectar las piezas de ajedrez en un tablero real, "
                 "y presenté los resultados en mi proyecto.")
ENGLISH_DRAFT = ("I built a computer vision system with Python and OpenCV to detect the chess pieces on a real board, and I presented the "
                 "results in my project.")
OPENCV = [{"id": "f4", "key": "story", "value": "I built a vision pipeline using Python and OpenCV.", "entity": "First Project",
           "entity_id": "e3", "category": "project"}]


def test_RS14_two_technologies_joined_by_a_spanish_connective_are_the_same_two_names_as_with_the_english_one():
    """'Python y OpenCV' was read as ONE organisation absent from the evidence ('Python and OpenCV'): a Spanish draft of English facts
    could never be validated."""
    assert not [i for i in fabrication_issues(SPANISH_DRAFT, OPENCV, OPENCV, CONTEXT) if "organisation" in i]
    assert not [i for i in fabrication_issues(ENGLISH_DRAFT, OPENCV, OPENCV, CONTEXT) if "organisation" in i]
    still = fabrication_issues("Trabajé con Python y Stockfish en un proyecto.", OPENCV, OPENCV, CONTEXT)
    assert any("Stockfish" in i for i in still), "a name that no evidence holds is still refused"


def test_RS14_the_language_of_a_text_is_read_from_its_function_words():
    assert language_of(ENGLISH_DRAFT)[0] == "English" and language_of(SPANISH_DRAFT)[0] == "Spanish"
    assert language_of("Python y OpenCV")[0] is None  # too short to say


@pytest.mark.parametrize("answer,wanted,wrong", [(ENGLISH_DRAFT, "Spanish", True), (SPANISH_DRAFT, "English", True), (SPANISH_DRAFT, "Spanish", False),
                                                 (ENGLISH_DRAFT, "English", False),
                                                 ("J'ai construit un système de vision par ordinateur avec Python et OpenCV pour détecter les pièces "
                                                  "sur un échiquier et je les ai présentées dans mon projet.", "Spanish", True)])
def test_RS14_a_draft_in_another_language_than_the_form_asks_for_is_refused_without_a_model(answer, wanted, wrong):
    assert bool(wrong_language_issue(answer, wanted)) is wrong
    claim = "I built a vision pipeline using Python and OpenCV"
    found = AnswerEvidenceValidator().validate(generated(answer, claim=claim, fact="f4"), OPENCV, "q", "MOTIVATION", {"language": wanted},
                                               context_text=CONTEXT)
    assert any("is written in" in i for i in found) is wrong, found


def test_RS14_a_spanish_draft_of_english_facts_is_now_accepted():
    found = AnswerEvidenceValidator().validate(generated(SPANISH_DRAFT, claim="I built a vision pipeline using Python and OpenCV", fact="f4"),
                                               OPENCV, "q", "MOTIVATION", {"language": "Spanish"}, context_text=CONTEXT)
    assert not found, found


# --------------------------------------------------------------------------- RS-19: what a draft may claim about the person
HOSTILE = [
    "I have a PhD in physics.", "I am a Chartered Engineer.", "I hold a security clearance.", "I am fluent in German and French.",
    "I speak Spanish natively.", "I am eligible to work in the UK.", "I am available to start immediately.", "I was promoted to senior engineer.",
    "I won a hackathon.", "I founded a startup.", "I have over a decade of experience.", "I led an engineering team.", "I worked at google.",
    "I built a vision pipeline using Python and TensorFlow.", "I built a vision pipeline using Rust.", "I completed this as part of my master's thesis.",
    "I am a Spanish national with a Certified Kubernetes Administrator certification.", "Tengo un doctorado en física.", "Hablo alemán con fluidez."]


@pytest.mark.parametrize("answer", HOSTILE)
def test_RS19_a_credential_title_award_language_technology_or_legal_status_no_cited_fact_states_is_refused(answer):
    found = [*credential_issues(answer, CITED, CONTEXT), *fabrication_issues(answer, FACTS, CITED, CONTEXT)]
    assert found, answer


@pytest.mark.parametrize("answer", ["I built a vision pipeline using Python.", "I used Python in my first project.", "I built it with Python at Example Studio.",
                                    "I led six engineers to deliver a responsive website.", "Construí una canalización de visión con Python."])
def test_RS19_an_honest_draft_is_not_refused_by_these_checks(answer):
    assert not credential_issues(answer, [f for f in FACTS if f["id"] in ("f1", "f4", "f5")], CONTEXT), answer


def test_RS19_a_credential_the_evidence_or_the_question_names_may_be_said():
    facts = [{"id": "f9", "key": "degree", "value": "MSc Artificial Intelligence, thesis on chess engines", "entity": "South University",
              "entity_id": "e2", "category": "education"}]
    assert not credential_issues("My master's thesis was on chess engines.", facts, CONTEXT)
    assert not credential_issues("I would love to use Rust at Acme.", facts, "Do you know Rust? Acme Robotics")  # the question names it
