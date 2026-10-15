"""The BASE questions: every family in its canonical plain form, English and Spanish, with the hand oracle of each (a function
of the candidate's stored facts, see people.py).

Classes of base (what a wrapper may legitimately do to it):

  S  a Yes/No proposition about the candidate (right to work, sponsorship, languages, degrees, graduation, relocation...)
  V  a value (visa status, notice period, start date, salary, level of education, city...)
  I  identity, contact and links
  D  demographics (gender, pronouns, ethnicity... a disability is an S with a protected answer)
  C  consent, certification, conviction, clearance: NEVER answered
  G  a typed signature: answered at most on Enter, never on Tab

A proposition of class S is written from a ``Clause`` (auxiliary + predicate + the Spanish verb forms), so that the wrappers
can negate it, swap its subject or turn it into a declaration without guessing at grammar."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from . import oracle as o
from .people import EU, SPAIN, TODAY, UK, Person

YES_NO = {"en": ("Yes", "No"), "es": ("Sí", "No")}
PLACES = {UK: ("the United Kingdom", "el Reino Unido"), SPAIN: ("Spain", "España"),
          "United States": ("the United States", "Estados Unidos"), EU: ("the European Union", "la Unión Europea"),
          "Ireland": ("Ireland", "Irlanda"), "Germany": ("Germany", "Alemania"), "France": ("France", "Francia"),
          "Australia": ("Australia", "Australia"), "India": ("India", "la India"), "Canada": ("Canada", "Canadá")}
LANGS = {"English": ("English", "inglés"), "Spanish": ("Spanish", "español"), "French": ("French", "francés"),
         "German": ("German", "alemán"), "Hindi": ("Hindi", "hindi"), "Welsh": ("Welsh", "galés"),
         "Italian": ("Italian", "italiano"), "Irish": ("Irish", "irlandés"), "Mandarin": ("Mandarin", "mandarín")}
SUBJECTS = {"Mathematics": ("Mathematics", "Matemáticas"), "Geography": ("Geography", "Geografía"),
            "Medicine": ("Medicine", "Medicina"), "Economics": ("Economics", "Economía"), "Law": ("Law", "Derecho"),
            "Modern Languages": ("Modern Languages", "Filología Moderna")}
DEGREES = {"Bachelor's": ("a Bachelor's degree", "un grado universitario"), "Master's": ("a Master's degree", "un máster"),
           "Doctorate": ("a PhD", "un doctorado")}
POOLS = {"place": PLACES, "lang": LANGS, "subject": SUBJECTS, "degree": DEGREES}
GRAD_YEAR = TODAY.year + 3  # every graduation of every candidate ends before this year

AUX_EN = {"be": ("Are", "Is"), "do": ("Do", "Does"), "will": ("Will", "Will"), "have": ("Have", "Has"), "can": ("Can", "Can")}
DECL_POS = {"be": "are", "do": "", "will": "will", "have": "have", "can": "can"}
DECL_NEG = {"be": "are not", "do": "do not", "will": "will not", "have": "have not", "can": "cannot"}
FIRST_PERSON = {"be": "am", "do": "", "will": "will", "have": "have", "can": "can"}
WHO_EN = {"you": ("you", 0), "spouse": ("your spouse", 1), "referee": ("your referee", 1), "parents": ("your parents", 0),
          "emergency": ("your emergency contact", 1)}
WHO_ES = {"spouse": ("tu cónyuge", 1), "referee": ("tu referencia", 1), "parents": ("tus padres", 2),
          "emergency": ("tu contacto de emergencia", 1)}


@dataclass(frozen=True)
class Clause:
    """A proposition about the candidate, as the pieces the wrappers recombine."""
    aux: str
    rest_en: str
    verbs_es: tuple  # 2nd person, 3rd singular, 3rd plural, 2nd person present subjunctive
    rest_es: str
    rest_es3: str | None = None
    one_es: str = ""  # the same, as a first-person statement ("tengo autorización para trabajar en...")


@dataclass(frozen=True)
class Base:
    id: str
    family: str
    cls: str
    kind: str
    en: str
    es: str
    truth: Callable
    options: dict = field(default_factory=dict)
    protected: bool = False
    must_answer: bool = False
    langs: tuple = ("en", "es")
    generic: bool = False
    clause: Clause | None = None
    slots: dict = field(default_factory=dict)  # {"place": "United Kingdom"}
    topic: dict = field(default_factory=dict)  # {"en": "notice period", "es": "periodo de preaviso"}
    subj: dict = field(default_factory=dict)  # {"en": "What is {s} notice period?", "es": "..."} {s}: the other person
    please: dict = field(default_factory=dict)
    note: str = ""

    def expect(self, person: Person, slots: dict | None = None) -> o.Expect:
        return self.truth(person, slots if slots is not None else self.slots)

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.es

    def opts(self, lang: str) -> tuple:
        return self.options.get(lang, ())


def fill(template: str, lang: str, slots: dict) -> str:
    index = 0 if lang == "en" else 1
    out = template
    for name, key in slots.items():
        if name == "year":
            out = out.replace("{year}", str(key))
        else:
            out = out.replace("{" + name + "}", POOLS[name][key][index])
    return out


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def question(base: Base, lang: str, who: str = "you", slots: dict | None = None, negated: bool = False) -> str:
    """The question of an S base (``negated``: 'Are you not ...?', 'No tienes ...?')."""
    slots = base.slots if slots is None else slots
    c = base.clause
    if lang == "en":
        who_en, index = WHO_EN[who]
        aux = AUX_EN[c.aux][1 if (who != "you" and index == 1) else 0]
        mid = " not" if negated else ""
        return fill(f"{aux} {who_en}{mid} {c.rest_en}?", "en", slots)
    verb2, verb3s, verb3p, _ = c.verbs_es
    if who == "you":
        body = f"{verb2} {c.rest_es}".strip()
        return fill("¿" + cap(("no " if negated else "") + body) + "?", "es", slots)
    who_es, number = WHO_ES[who]
    verb = verb3p if number == 2 else verb3s
    rest = c.rest_es3 or c.rest_es
    if number == 2:
        rest = rest.replace("dispuesto/a", "dispuestos/as")
    return fill("¿" + cap(("No " if negated else "") + f"{verb} {who_es} {rest}".strip()) + "?", "es", slots)


def declaration(base: Base, lang: str, negated: bool, slots: dict | None = None) -> str:
    """'you are legally authorised to work in X' / 'you are not ...' (no capital, no punctuation)."""
    slots = base.slots if slots is None else slots
    c = base.clause
    if lang == "en":
        aux = (DECL_NEG if negated else DECL_POS)[c.aux]
        return fill(f"you {aux} {c.rest_en}".replace("  ", " "), "en", slots)
    body = f"{c.verbs_es[0]} {c.rest_es}".strip()
    return fill(("no " if negated else "") + body, "es", slots)


def first_person(base: Base, lang: str, slots: dict | None = None) -> str:
    slots = base.slots if slots is None else slots
    c = base.clause
    if lang == "en":
        return fill(f"I {FIRST_PERSON[c.aux]} {c.rest_en}".replace("  ", " "), "en", slots)
    return fill(c.one_es, "es", slots)


# ------------------------------------------------------------------------------------------------ S bases
def s_base(family: str, name: str, clause: Clause, truth, slot_name: str | None = None, values: tuple = (None,),
           year: int | None = None, **kw):
    """One base per value of the slot (``rw[United Kingdom]``), the question rendered from its clause."""
    out = []
    for value in values:
        slots = {"year": year} if year else {} if slot_name is None else {slot_name: value}
        base = Base(id=name if slot_name is None else f"{name}[{value}]", family=family, cls="S", kind="radio", en="",
                    es="", truth=truth, options=dict(YES_NO), clause=clause, slots=slots, **kw)
        out.append(replace(base, en=question(base, "en"), es=question(base, "es")))
    return out


BASES: list[Base] = []
add = BASES.extend

add(s_base("right_to_work", "rw", Clause(
    "be", "legally authorised to work in {place}", ("tienes", "tiene", "tienen", "tengas"),
    "autorización para trabajar en {place}", one_es="tengo autorización para trabajar en {place}"),
    lambda p, s: p.rtw(s["place"]), "place", (UK, SPAIN, "United States", EU), protected=True))
add(s_base("right_to_work", "rw_job", Clause(
    "be", "legally authorised to work in the country where this job is located", ("tienes", "tiene", "tienen", "tengas"),
    "autorización para trabajar en el país donde se encuentra este puesto",
    one_es="tengo autorización para trabajar en el país donde se encuentra este puesto"),
    lambda p, s: p.rtw(p.job_country), protected=True))
add(s_base("sponsorship", "sp", Clause(
    "will", "now or in the future require sponsorship to work in {place}", ("necesitarás", "necesitará", "necesitarán", "necesites"),
    "patrocinio de visado, ahora o en el futuro, para trabajar en {place}",
    one_es="necesitaré patrocinio de visado, ahora o en el futuro, para trabajar en {place}"),
    lambda p, s: p.sponsorship(s["place"]), "place", (UK, SPAIN, "United States"), protected=True))
add(s_base("sponsorship", "sp_job", Clause(
    "will", "now or in the future require sponsorship to work in the country where this job is located",
    ("necesitarás", "necesitará", "necesitarán", "necesites"),
    "patrocinio de visado, ahora o en el futuro, para trabajar en el país donde se encuentra este puesto",
    one_es="necesitaré patrocinio de visado, ahora o en el futuro, para trabajar en el país donde se encuentra este puesto"),
    lambda p, s: p.sponsorship(p.job_country), protected=True))
add(s_base("language", "lg_speak", Clause("do", "speak {lang}", ("hablas", "habla", "hablan", "hables"), "{lang}",
                                          one_es="hablo {lang}"),
           lambda p, s: p.speaks(s["lang"]), "lang", ("English", "German")))
add(s_base("language", "lg_fluent", Clause("be", "fluent in {lang}", ("hablas", "habla", "hablan", "hables"),
                                           "{lang} con fluidez", one_es="hablo {lang} con fluidez"),
           lambda p, s: p.fluent(s["lang"]), "lang", ("English", "French")))
add(s_base("language", "lg_native", Clause("be", "a native {lang} speaker", ("eres", "es", "son", "seas"),
                                           "hablante nativo de {lang}", one_es="soy hablante nativo de {lang}"),
           lambda p, s: p.native(s["lang"]), "lang", ("English", "Spanish")))
add(s_base("education", "ed_degree", Clause("do", "have {degree}", ("tienes", "tiene", "tienen", "tengas"), "{degree}",
                                            one_es="tengo {degree}"),
           lambda p, s: p.has_degree(s["degree"]), "degree", ("Bachelor's", "Master's", "Doctorate")))
add(s_base("education", "ed_subject", Clause("do", "have a degree in {subject}", ("tienes", "tiene", "tienen", "tengas"),
                                             "un título en {subject}", one_es="tengo un título en {subject}"),
           lambda p, s: p.has_subject(s["subject"]), "subject", ("Mathematics", "Geography", "Medicine")))
add(s_base("education", "ed_student", Clause("be", "currently a student", ("eres", "es", "son", "seas"),
                                             "estudiante actualmente", one_es="soy estudiante actualmente"),
           lambda p, s: p.student))
add(s_base("graduation", "gr_done", Clause("have", "graduated", ("te has graduado", "se ha graduado", "se han graduado",
                                                                 "te hayas graduado"), "", one_es="me he graduado"),
           lambda p, s: p.graduated))
add(s_base("graduation", "gr_by", Clause(
    "will", "have graduated by {year}", ("te habrás graduado", "se habrá graduado", "se habrán graduado", "te hayas graduado"),
    "para {year}", one_es="me habré graduado para {year}"), lambda p, s: p.graduates_by(GRAD_YEAR), year=GRAD_YEAR))
add(s_base("location", "loc_relocate", Clause(
    "be", "willing to relocate", ("estás", "está", "están", "estés"), "dispuesto/a a trasladarte",
    rest_es3="dispuesto/a a trasladarse", one_es="estoy dispuesto/a a trasladarme"), lambda p, s: p.relocate_expect()))
add(s_base("location", "loc_based", Clause("be", "currently based in {place}", ("resides", "reside", "residen", "residas"),
                                           "actualmente en {place}", one_es="resido actualmente en {place}"),
           lambda p, s: p.based_in(s["place"]), "place", (UK,)))
add(s_base("demographics", "dm_disability", Clause("do", "have a disability", ("tienes", "tiene", "tienen", "tengas"),
                                                   "alguna discapacidad", one_es="tengo una discapacidad"),
           lambda p, s: p.disability_expect(), protected=True))

# Which of the plain forms the pipeline is expected to ANSWER (not abstain) for a candidate whose facts settle them: the
# forms the pinned English readings and the policy matrix already answer. Anything else may abstain.
MUST_ANSWER = {"rw[United Kingdom]", "sp[United Kingdom]", "lg_speak[English]", "id_email", "id_phone", "id_first",
               "id_last", "id_full"}


# ------------------------------------------------------------------------------------------------ V, I, D, C, G bases
def b(id, family, cls, kind, en, es, truth, *, options=None, topic=None, subj=None, please=None, **kw) -> Base:
    return Base(id=id, family=family, cls=cls, kind=kind, en=en, es=es, truth=truth, options=options or {},
                topic=topic or {}, subj=subj or {}, please=please or {}, **kw)


GENDERS = {"en": ("Man", "Woman", "Non-binary", "Prefer not to say"),
           "es": ("Hombre", "Mujer", "No binario", "Prefiero no decirlo")}
ETHNICITY = {"en": ("Asian", "Black", "White", "Mixed", "Prefer not to say"),
             "es": ("Asiático", "Negro", "Blanco", "Mixto", "Prefiero no decirlo")}
VETERAN = {"en": ("I am not a protected veteran", "I identify as a protected veteran", "I don't wish to answer"),
           "es": ("No soy veterano", "Soy veterano", "Prefiero no responder")}
LEVELS = {"en": ("Basic", "Intermediate", "Fluent", "Native"), "es": ("Básico", "Intermedio", "Fluido", "Nativo")}
EDUCATION = {"en": ("High School", "Bachelor's Degree", "Master's Degree", "Doctorate"),
             "es": ("Bachillerato", "Grado universitario", "Máster", "Doctorado")}
COUNTRIES = {"en": ("Select...", "Spain", "France", "Germany", "India", "Ireland", "United Kingdom", "United States"),
             "es": ("Seleccionar", "España", "Francia", "Alemania", "India", "Irlanda", "Reino Unido", "Estados Unidos")}
UNKNOWN = lambda p, s=None: o.UNKNOWN  # noqa: E731

add([
    # ---- visa and notice, availability, salary, location
    b("visa_status", "visa_route", "V", "text", "What is your current visa status?", "¿Cuál es tu situación de visado actual?",
      lambda p, s: p.visa_expect(), protected=True, topic=dict(en="visa status", es="situación de visado"),
      subj=dict(en="What is {s} visa status?", es="¿Cuál es la situación de visado de {s}?")),
    b("nt_period", "notice_period", "V", "text", "What is your notice period?", "¿Cuál es tu periodo de preaviso?",
      lambda p, s: p.notice_expect(), topic=dict(en="notice period", es="periodo de preaviso"),
      subj=dict(en="What is {s} notice period?", es="¿Cuál es el periodo de preaviso de {s}?")),
    b("nt_weeks", "notice_period", "V", "text", "Notice period (weeks)", "Periodo de preaviso (semanas)",
      lambda p, s: p.notice_weeks_expect(), topic=dict(en="notice period in weeks", es="periodo de preaviso en semanas"),
      subj=dict(en="How many weeks of notice does {s} have to give?", es="¿Cuántas semanas de preaviso debe dar {s}?"),
      please=dict(en="Please enter your notice period in weeks", es="Por favor, indica tu preaviso en semanas")),
    b("av_start", "availability", "V", "date", "Earliest start date", "Fecha de incorporación",
      lambda p, s: p.start_expect(), topic=dict(en="earliest start date", es="fecha de incorporación"),
      subj=dict(en="What is {s} earliest start date?", es="¿Cuál es la fecha de incorporación de {s}?"),
      please=dict(en="Please enter your earliest start date", es="Por favor, indica tu fecha de incorporación")),
    b("av_when", "availability", "V", "text", "When can you start?", "¿Cuándo puedes incorporarte?",
      lambda p, s: p.start_expect(), topic=dict(en="start date", es="fecha de incorporación"),
      subj=dict(en="When can {who} start?", es="¿Cuándo puede incorporarse {s}?")),
    b("av_start_generic", "availability", "V", "date", "Start date", "Fecha de inicio", lambda p, s: p.start_expect(),
      generic=True, topic=dict(en="start date", es="fecha de inicio"),
      subj=dict(en="What is {s} start date?", es="¿Cuál es la fecha de inicio de {s}?")),
    b("sal_expect", "salary", "V", "text", "What are your salary expectations?", "¿Cuáles son tus pretensiones salariales?",
      lambda p, s: p.salary_expect(), topic=dict(en="salary expectations", es="pretensiones salariales"),
      subj=dict(en="What are {s} salary expectations?", es="¿Cuáles son las pretensiones salariales de {s}?")),
    b("loc_city", "location", "V", "text", "City", "Ciudad de residencia",
      lambda p, s: o.text(o.fold(p.city)) if p.city else o.UNKNOWN, generic=True,
      topic=dict(en="city", es="ciudad de residencia"), subj=dict(en="In which city does {who} live?", es="¿En qué ciudad reside {s}?")),
    b("loc_preferred", "location", "V", "text", "Preferred work location", "Ubicación de trabajo preferida",
      lambda p, s: p.preferred_expect(), topic=dict(en="preferred work location", es="ubicación de trabajo preferida"),
      subj=dict(en="What is {s} preferred work location?", es="¿Cuál es la ubicación de trabajo preferida de {s}?")),
    b("loc_country", "location", "V", "select", "Country", "País de residencia", lambda p, s: p.country_expect(),
      options=COUNTRIES, generic=True, topic=dict(en="country of residence", es="país de residencia"),
      subj=dict(en="In which country does {who} live?", es="¿En qué país reside {s}?")),
    # ---- languages and education
    b("lg_level", "language", "V", "select", "English proficiency", "Nivel de inglés", lambda p, s: p.level_expect("English"),
      options=LEVELS, topic=dict(en="level of English", es="nivel de inglés"),
      subj=dict(en="What is {s} level of English?", es="¿Cuál es el nivel de inglés de {s}?")),
    b("ed_highest", "education", "V", "select", "Highest level of education completed",
      "Nivel de estudios más alto completado", lambda p, s: p.highest_level(), options=EDUCATION,
      topic=dict(en="highest level of education", es="nivel de estudios"),
      subj=dict(en="What is the highest level of education {who} has completed?",
                es="¿Cuál es el nivel de estudios más alto que ha completado {s}?")),
    b("ed_current", "education", "V", "text", "Where are you currently studying?", "¿Dónde estudias actualmente?",
      lambda p, s: p.current_institution(), topic=dict(en="current place of study", es="lugar de estudio actual"),
      subj=dict(en="Where is {who} currently studying?", es="¿Dónde estudia actualmente {s}?")),
    b("gr_year", "graduation", "V", "text", "Expected graduation year", "Año de graduación previsto",
      lambda p, s: p.expected_year(), topic=dict(en="expected graduation year", es="año de graduación previsto"),
      subj=dict(en="What is {s} expected graduation year?", es="¿Cuál es el año de graduación previsto de {s}?")),
    b("gr_date", "graduation", "V", "date", "Expected graduation date", "Fecha de graduación prevista",
      lambda p, s: p.expected_graduation(), topic=dict(en="expected graduation date", es="fecha de graduación prevista"),
      subj=dict(en="What is {s} expected graduation date?", es="¿Cuál es la fecha de graduación prevista de {s}?")),
    # ---- identity
    b("id_first", "identity", "I", "text", "First name", "Nombre", lambda p, s: o.text(o.fold(p.first)),
      topic=dict(en="first name", es="nombre"), please=dict(en="Please enter your first name", es="Por favor, introduce tu nombre de pila")),
    b("id_last", "identity", "I", "text", "Last name", "Apellidos", lambda p, s: o.text(o.fold(p.last)),
      topic=dict(en="last name", es="apellidos"), please=dict(en="Please enter your last name", es="Por favor, introduce tus apellidos")),
    b("id_full", "identity", "I", "text", "Full name", "Nombre completo", lambda p, s: o.text(o.fold(p.full)),
      topic=dict(en="full name", es="nombre completo"), please=dict(en="Please enter your full name", es="Por favor, introduce tu nombre completo")),
    b("id_name", "identity", "I", "text", "Name", "Nombre y apellidos", lambda p, s: o.text(o.fold(p.full)),
      generic=True, topic=dict(en="name", es="nombre y apellidos"), langs=("en",)),
    b("id_email", "identity", "I", "email", "Email", "Correo electrónico", lambda p, s: o.text(o.fold(p.email)),
      generic=True, topic=dict(en="email address", es="correo electrónico"),
      please=dict(en="Please enter your email address", es="Por favor, introduce tu correo electrónico")),
    b("id_phone", "identity", "I", "tel", "Phone", "Teléfono móvil", lambda p, s: o.digits(p.phone), generic=True,
      topic=dict(en="phone number", es="número de teléfono"),
      please=dict(en="Please enter your phone number", es="Por favor, introduce tu teléfono")),
    b("id_linkedin", "identity", "I", "url", "LinkedIn profile", "Perfil de LinkedIn",
      lambda p, s: o.text(o.fold(p.links["linkedin"])) if "linkedin" in p.links else o.UNKNOWN,
      topic=dict(en="LinkedIn profile", es="perfil de LinkedIn")),
    b("id_github", "identity", "I", "url", "GitHub URL", "URL de GitHub",
      lambda p, s: o.text(o.fold(p.links["github"])) if "github" in p.links else o.UNKNOWN,
      topic=dict(en="GitHub profile", es="perfil de GitHub")),
    b("id_portfolio", "identity", "I", "url", "Portfolio URL", "URL del portfolio",
      lambda p, s: o.text(o.fold(p.links["portfolio"])) if "portfolio" in p.links else o.UNKNOWN,
      topic=dict(en="portfolio", es="portfolio")),
    # ---- demographics (a disability is an S above)
    b("dm_gender", "demographics", "D", "select", "Gender", "Género", lambda p, s: p.gender_expect(), options=GENDERS,
      protected=True, topic=dict(en="gender", es="género"), subj=dict(en="What is {s} gender?", es="¿Cuál es el género de {s}?")),
    b("dm_pronouns", "demographics", "D", "text", "Pronouns", "Pronombres", lambda p, s: p.pronouns_expect(),
      protected=True, topic=dict(en="pronouns", es="pronombres"),
      subj=dict(en="What are {s} pronouns?", es="¿Cuáles son los pronombres de {s}?")),
    b("dm_ethnicity", "demographics", "D", "select", "What is your race or ethnicity?", "Origen racial o étnico",
      lambda p, s: p.ethnicity_expect(), options=ETHNICITY, protected=True,
      topic=dict(en="race or ethnicity", es="origen racial o étnico"),
      subj=dict(en="What is {s} race or ethnicity?", es="¿Cuál es el origen racial o étnico de {s}?")),
    b("dm_veteran", "demographics", "D", "select", "Veteran status", "Condición de veterano", UNKNOWN, options=VETERAN,
      protected=True, langs=("en",), topic=dict(en="veteran status", es="condición de veterano")),
    b("dm_religion", "demographics", "D", "text", "Religion", "Religión", UNKNOWN, protected=True,
      topic=dict(en="religion", es="religión"), subj=dict(en="What is {s} religion?", es="¿Cuál es la religión de {s}?")),
    b("dm_dob", "demographics", "D", "date", "Date of birth", "Fecha de nacimiento", UNKNOWN, protected=True,
      topic=dict(en="date of birth", es="fecha de nacimiento"),
      subj=dict(en="What is {s} date of birth?", es="¿Cuál es la fecha de nacimiento de {s}?")),
])

# ---- consent, certification, conviction, clearance: never answered. ``kind`` is the control that carries it.
CONSENT_TEXT = [
    ("cs_privacy", "checkbox", "I have read and agree to the Privacy Policy", "He leído y acepto la política de privacidad"),
    ("cs_gdpr", "checkbox", "I consent to Acme Robotics storing my personal data for 12 months",
     "Doy mi consentimiento al tratamiento de mis datos (RGPD)"),
    ("cs_terms", "checkbox", "I agree to the Terms and Conditions", "Acepto los términos y condiciones"),
    ("cs_certify", "checkbox", "I certify that the information I have provided is true and complete",
     "Declaro que la información proporcionada es veraz"),
    ("cs_rtw_confirm", "checkbox", "I confirm that I have the right to work in the United Kingdom",
     "Confirmo que tengo derecho a trabajar en el Reino Unido"),
    ("cs_marketing", "checkbox", "I agree to receive marketing communications", "Acepto recibir comunicaciones comerciales"),
    ("cs_ai", "checkbox", "I acknowledge that AI tools may be used to screen my application",
     "Reconozco que se pueden usar herramientas de IA para evaluar mi candidatura"),
    ("cs_criminal", "radio", "Have you ever been convicted of a criminal offence?", "¿Tienes antecedentes penales?"),
    ("cs_clearance", "radio", "Do you hold an active security clearance?", "¿Dispones de una habilitación de seguridad activa?"),
]
for _id, _kind, _en, _es in CONSENT_TEXT:
    BASES.append(b(_id, "consent", "C", _kind, _en, _es, UNKNOWN, options=dict(YES_NO) if _kind == "radio" else {},
                   topic=dict(en=_en, es=_es)))
for _id, _en, _es in [("sg_signature", "Signature (type your full name)", "Firma electrónica (escribe tu nombre completo)"),
                      ("sg_sign_declaration", "Please type your full name to sign this declaration",
                       "Escribe tu nombre completo para firmar esta declaración"),
                      ("sg_print", "Print name", "Firma (nombre completo en letra de imprenta)")]:
    BASES.append(b(_id, "signature", "G", "text", _en, _es, lambda p, s: o.text(o.fold(p.full)),
                   topic=dict(en="signature", es="firma"), protected=True))

BASES[:] = [replace(base, must_answer=base.id in MUST_ANSWER) for base in BASES]
CONSENT_NEGATED = {
    "cs_privacy": ("I have not read and I do not agree to the Privacy Policy", "No he leído ni acepto la política de privacidad"),
    "cs_gdpr": ("I do not consent to Acme Robotics storing my personal data for 12 months",
                "No doy mi consentimiento al tratamiento de mis datos (RGPD)"),
    "cs_terms": ("I do not agree to the Terms and Conditions", "No acepto los términos y condiciones"),
    "cs_certify": ("I do not certify that the information I have provided is true and complete",
                   "No declaro que la información proporcionada sea veraz"),
    "cs_rtw_confirm": ("I do not confirm that I have the right to work in the United Kingdom",
                       "No confirmo que tengo derecho a trabajar en el Reino Unido"),
    "cs_marketing": ("I do not agree to receive marketing communications", "No acepto recibir comunicaciones comerciales"),
    "cs_ai": ("I do not acknowledge that AI tools may be used to screen my application",
              "No reconozco que se puedan usar herramientas de IA para evaluar mi candidatura"),
    "cs_criminal": ("Have you never been convicted of a criminal offence?", "¿Nunca has sido condenado por un delito?"),
    "cs_clearance": ("Do you not hold an active security clearance?", "¿No dispones de una habilitación de seguridad activa?"),
}
BY_ID = {base.id: base for base in BASES}
FAMILIES = sorted({base.family for base in BASES})
assert len(BY_ID) == len(BASES), "duplicate base ids"
