"""Six synthetic candidates (never real data), the pages they apply on, and what their stored facts say about every family of
question. The facts are seeded through the PUBLIC helpers only (entity / ingest_fact / policy / write_profile); the
expectations are written by hand from those facts below, never by calling the engine.

Dates are relative to the real today (Europe/London, the engine's own calendar) so that "in progress", "available in a month"
and "graduates next year" stay true whenever the suite runs; the questions that mention a year derive it from the same dates.

 student_uk   Indian national on a UK Student route who will need sponsorship later; BA finished, MSc in progress
 uk_citizen   British citizen, permanent unrestricted right to work; BSc and MSc finished, employed (3 months' notice)
 es_in_es     Spanish national in Spain, applying on a Madrid page; right to work in Spain and the EU; no UK status at all
 eu_settled   Italian national with UK settled status; BA finished; 2 weeks' notice
 irish_grad   Irish citizen (Common Travel Area, EU); BSc finished, part-time MSc in progress; some confirmed demographics
 sparse       Name, email and phone only: nothing else is known, so every other question must be left to the user"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import oracle as o

TODAY = datetime.now(ZoneInfo("Europe/London")).date()
EU_MEMBERS = {"Spain", "Ireland", "Germany", "France"}
UK, SPAIN, EU = "United Kingdom", "Spain", "European Union"


def ahead(days: int) -> date:
    return TODAY + timedelta(days=days)


def level_rank(level: str) -> int:
    return {"Bachelor's": 1, "Master's": 2, "Doctorate": 3}[level]


@dataclass
class Person:
    key: str
    ats: str
    job: str
    full: str
    first: str
    last: str
    email: str
    phone: str
    city: str
    country: str
    code: str
    nationality: str
    links: dict = field(default_factory=dict)
    employed: bool | None = None
    start: date | None = None
    notice: str | None = None
    notice_patterns: tuple = ()
    notice_weeks: tuple | None = None
    relocation: str | None = None
    preferred: str | None = None  # preferred work locations, as saved in the application profile
    salary: int | None = None
    currency: str = "GBP"
    immigration: dict | None = None
    visa_patterns: tuple = ()
    visa_forbid: tuple = ()  # a citizen or a settled person holds no visa: an answer that says so is wrong
    visa_profile: str | None = None
    jurisdiction: dict = field(default_factory=dict)  # profile keys: work_authorization_es, sponsorship_eu...
    education: list = field(default_factory=list)
    languages: dict = field(default_factory=dict)  # name -> (level, native flag or None)
    demographics: dict = field(default_factory=dict)  # key -> (value, confirmed)

    # ------------------------------------------------------------------------------------------ the page
    @property
    def job_country(self) -> str:
        return SPAIN if self.job == "es" else UK

    # ------------------------------------------------------------------------------------------ work rights
    @property
    def eu_right(self) -> bool | None:
        value = self.jurisdiction.get("work_authorization_eu")
        return None if value is None else value == "Yes"

    @property
    def eu_sponsor(self) -> bool | None:
        value = self.jurisdiction.get("sponsorship_eu")
        return None if value is None else value == "Yes"

    def rtw(self, place: str) -> o.Expect:
        """Right to work in ``place``: what a stored fact says, or what EU law says of a member state once the EU/EEA right
        is stored. Nothing else is ever inferred (a Spanish national has no UK status; a UK one has no Irish one)."""
        if place == UK:
            return o.boolean(self.immigration["current_right_to_work"] if self.immigration else None)
        stored = self.jurisdiction.get({SPAIN: "work_authorization_es", EU: "work_authorization_eu"}.get(place, ""))
        if stored is not None:
            return o.boolean(stored == "Yes")
        if place in EU_MEMBERS and self.eu_right is not None:
            return o.boolean(self.eu_right)
        return o.UNKNOWN

    def sponsorship(self, place: str) -> o.Expect:
        if place == UK:
            return o.boolean(self.immigration["sponsorship_now_or_future"] if self.immigration else None)
        stored = self.jurisdiction.get({SPAIN: "sponsorship_es", EU: "sponsorship_eu"}.get(place, ""))
        if stored is not None:
            return o.boolean(stored == "Yes")
        if place in EU_MEMBERS and self.eu_sponsor is not None:
            return o.boolean(self.eu_sponsor)
        return o.UNKNOWN

    # ------------------------------------------------------------------------------------------ languages
    def level(self, lang: str) -> str | None:
        return self.languages.get(lang, (None, None))[0]

    def speaks(self, lang: str) -> o.Expect:
        # 'Basic' is not a certain "yes" to "do you speak it", and an unlisted language is not a certain "no"
        return o.YES if self.level(lang) in {"Intermediate", "Fluent", "Native"} else o.UNKNOWN

    def fluent(self, lang: str) -> o.Expect:
        level = self.level(lang)
        return o.YES if level in {"Fluent", "Native"} else o.NO if level in {"Basic", "Intermediate"} else o.UNKNOWN

    def native(self, lang: str) -> o.Expect:
        level = self.level(lang)
        return o.YES if level == "Native" else o.NO if level else o.UNKNOWN

    def level_expect(self, lang: str) -> o.Expect:
        level = self.level(lang)
        words = {"Basic": r"basic|basico", "Intermediate": r"intermediate|intermedio", "Fluent": r"fluent|fluido",
                 "Native": r"native|nativo|bilingual|bilingue"}
        return o.text(words[level]) if level else o.UNKNOWN

    # ------------------------------------------------------------------------------------------ education
    def completed(self) -> list:
        return [e for e in self.education if e["status"] == "completed"]

    def studying(self) -> dict | None:
        return next((e for e in self.education if e["status"] == "studying"), None)

    def has_degree(self, level: str) -> o.Expect:
        if not self.education:
            return o.UNKNOWN
        held = any(level_rank(level) == self.rank(e) for e in self.completed())
        return o.YES if held else o.NO  # the records are complete: a degree that is not among them is not held

    @staticmethod
    def rank(record: dict) -> int:
        return level_rank(record["level"].split(" ")[0])

    def has_subject(self, subject: str) -> o.Expect:
        return o.YES if any(e["subject"] == subject for e in self.completed()) else o.UNKNOWN

    @property
    def student(self) -> o.Expect:
        return o.YES if self.studying() else o.NO if self.education else o.UNKNOWN

    @property
    def graduated(self) -> o.Expect:
        return o.YES if self.completed() else o.UNKNOWN

    def graduates_by(self, year: int) -> o.Expect:
        return o.YES if self.education and all(date.fromisoformat(e["end_date"]).year < year for e in self.education) \
            else o.UNKNOWN

    def highest_level(self) -> o.Expect:
        done = self.completed()
        if not done:
            return o.UNKNOWN
        top = max(self.rank(e) for e in done)
        return o.text({1: r"bachelor|grado|licenciatura|undergraduate", 2: r"master", 3: r"doctor|phd"}[top])

    def expected_year(self) -> o.Expect:
        record = self.studying()
        return o.text(rf"\b{date.fromisoformat(record['end_date']).year}\b") if record else o.UNKNOWN

    def expected_graduation(self) -> o.Expect:
        record = self.studying()
        return o.on(date.fromisoformat(record["end_date"])) if record else o.UNKNOWN

    def current_institution(self) -> o.Expect:
        record = self.studying()
        return o.text(o.fold(record["institution"]).split()[-1]) if record else o.UNKNOWN

    # ------------------------------------------------------------------------------------------ availability and place
    def notice_expect(self) -> o.Expect:
        return o.text(*self.notice_patterns) if self.notice_patterns else o.UNKNOWN

    def notice_weeks_expect(self) -> o.Expect:
        return o.number(*self.notice_weeks) if self.notice_weeks else o.UNKNOWN

    def start_expect(self) -> o.Expect:
        return o.on(self.start) if self.start else o.UNKNOWN

    def salary_expect(self) -> o.Expect:
        return o.number(self.salary) if self.salary else o.UNKNOWN

    def based_in(self, place: str) -> o.Expect:
        return o.UNKNOWN if not self.country else (o.YES if self.country == place else o.NO)

    def country_expect(self) -> o.Expect:
        words = {UK: r"united kingdom|reino unido|\buk\b|great britain", SPAIN: r"spain|espana"}
        return o.text(words[self.country]) if self.country else o.UNKNOWN

    def preferred_expect(self) -> o.Expect:
        return o.text(*[o.fold(place) for place in self.preferred.split(",")]) if self.preferred else o.UNKNOWN

    def relocate_expect(self) -> o.Expect:
        return o.boolean(None if self.relocation is None else self.relocation == "Yes")

    # ------------------------------------------------------------------------------------------ demographics
    def confirmed(self, key: str) -> str | None:
        value, confirmed = self.demographics.get(key, (None, False))
        return value if confirmed else None

    def gender_expect(self) -> o.Expect:
        words = {"Woman": r"woman|mujer|female", "Man": r"^(man|hombre|male)$", "Non-binary": r"non-binary|no binario"}
        value = self.confirmed("gender")
        return o.text(words[value]) if value else o.UNKNOWN

    def pronouns_expect(self) -> o.Expect:
        value = self.confirmed("pronouns")
        return o.text(o.fold(value).replace("/", "[/ ]")) if value else o.UNKNOWN

    def disability_expect(self) -> o.Expect:
        value = self.confirmed("disability")
        return o.boolean(None if value is None else value == "Yes")

    def ethnicity_expect(self) -> o.Expect:
        words = {"White": r"white|blanco", "Asian": r"asian|asiatico"}
        value = self.confirmed("ethnicity")
        return o.text(words[value]) if value else o.UNKNOWN

    def visa_expect(self) -> o.Expect:
        return o.text(*self.visa_patterns, forbid=self.visa_forbid) if self.visa_patterns else o.UNKNOWN


def build() -> list[Person]:
    student = Person(
        "student_uk", "greenhouse", "uk", "Priya Raman", "Priya", "Raman", "priya.raman@example.test", "+447700900123",
        "Manchester", UK, "GB", "Indian",
        links=dict(linkedin="https://example.test/in/priya-raman", github="https://example.test/priya-raman",
                   portfolio="https://example.test/priya"),
        employed=False, start=ahead(344), notice_patterns=(r"none|no notice|\b0\b|zero|immediate|ninguno|sin preaviso",),
        notice_weeks=(0, 0), relocation="Yes", preferred="London, Manchester", salary=32000,
        immigration=dict(country=UK, route="Student", current_right_to_work=True, permanent_unrestricted=False,
                         sponsorship_now_or_future=True, sponsorship_current=False, sponsorship_future=True),
        visa_patterns=(r"student",), visa_profile="Student visa",
        education=[
            dict(institution="Delhi University", degree="BA Economics", level="Bachelor's degree", subject="Economics",
                 status="completed", start_date="2018-08-01", end_date="2021-06-30"),
            dict(institution="University of Manchester", degree="MSc Data Science", level="Master's degree",
                 subject="Data Science", status="studying", start_date=ahead(-400).isoformat(),
                 end_date=ahead(330).isoformat())],
        languages={"English": ("Fluent", None), "Hindi": ("Native", True), "French": ("Basic", None)},
        demographics=dict(gender=("Woman", True), pronouns=("she/her/hers", True), disability=("No", True),
                          ethnicity=("Asian", False), religion=("Hindu", False)))
    citizen = Person(
        "uk_citizen", "lever", "uk", "Oliver Bennett", "Oliver", "Bennett", "oliver.bennett@example.test", "+442079460321",
        "London", UK, "GB", "British", links=dict(linkedin="https://example.test/in/oliver-bennett"),
        employed=True, start=ahead(92), notice="3 months",
        notice_patterns=(r"\b3 months?\b|three months?|\b1[23] weeks?\b|\b90 days\b|\b3 meses\b",),
        notice_weeks=(12, 13.1), relocation="No", preferred="London", salary=55000,
        immigration=dict(country=UK, route="Citizen", current_right_to_work=True, permanent_unrestricted=True,
                         unrestricted_right_to_work=True, full_time_right_to_work=True, future_right_to_work=True,
                         sponsorship_now_or_future=False, sponsorship_current=False, sponsorship_future=False),
        visa_patterns=(r"citizen|british|not applicable|n/a|no visa|ciudadan",), visa_forbid=(r"\bvisa\b(?! (?:required|needed))",),
        education=[
            dict(institution="Cardiff University", degree="BSc Geography", level="Bachelor's degree", subject="Geography",
                 status="completed", start_date="2017-09-01", end_date="2020-06-30"),
            dict(institution="University of Leeds", degree="MSc Geographic Information Science", level="Master's degree",
                 subject="Geographic Information Science", status="completed", start_date="2020-09-01",
                 end_date="2021-09-30")],
        languages={"English": ("Native", True), "Welsh": ("Intermediate", None), "Spanish": ("Basic", None)},
        demographics=dict(gender=("Man", True), pronouns=("he/him/his", True), disability=("No", False),
                          ethnicity=("White", True), neurodivergence=("Yes", False)))
    spanish = Person(
        "es_in_es", "ashby", "es", "María García López", "María", "García López", "maria.garcia@example.test",
        "+34600123456", "Madrid", SPAIN, "ES", "Spanish", links=dict(linkedin="https://example.test/in/maria-garcia"),
        employed=True, start=ahead(31), notice="1 month",
        notice_patterns=(r"\b1 months?\b|one months?|\b4 weeks?\b|\b30 days\b|\b4\.\d+ weeks?\b|\b1 mes\b|\b4 semanas\b",),
        notice_weeks=(4, 4.5), relocation="No", preferred="Madrid, Barcelona", salary=38000, currency="EUR",
        jurisdiction=dict(work_authorization_es="Yes", sponsorship_es="No", work_authorization_eu="Yes",
                          sponsorship_eu="No", expected_salary_es="38000"),
        education=[
            dict(institution="Universidad Complutense de Madrid", degree="Grado en Ingeniería Informática",
                 level="Bachelor's degree", subject="Ingeniería Informática", status="completed",
                 start_date="2016-09-01", end_date="2020-06-30"),
            dict(institution="Universidad Politécnica de Madrid", degree="Máster en Ciberseguridad",
                 level="Master's degree", subject="Ciberseguridad", status="completed", start_date="2020-09-01",
                 end_date="2022-06-30")],
        languages={"Spanish": ("Native", True), "English": ("Fluent", None), "French": ("Basic", None)},
        demographics=dict(gender=("Woman", True), disability=("No", True), religion=("Catholic", False)))
    settled = Person(
        "eu_settled", "workday", "uk", "Giulia Rossi", "Giulia", "Rossi", "giulia.rossi@example.test", "+447700900456",
        "Bristol", UK, "GB", "Italian",
        links=dict(linkedin="https://example.test/in/giulia-rossi", github="https://example.test/giulia-rossi"),
        employed=True, start=ahead(14), notice="2 weeks",
        notice_patterns=(r"\b2 weeks?\b|two weeks?|\b14 days\b|fortnight|\b2 semanas\b",), notice_weeks=(2, 2),
        relocation="Yes", preferred="Bristol, London", salary=45000,
        immigration=dict(country=UK, route="EU Settlement Scheme (settled status)", current_right_to_work=True,
                         permanent_unrestricted=True, unrestricted_right_to_work=True, full_time_right_to_work=True,
                         future_right_to_work=True, sponsorship_now_or_future=False, sponsorship_current=False,
                         sponsorship_future=False),
        visa_patterns=(r"settle|euss|eu settlement|asentad",), visa_forbid=(r"\bvisa\b(?! (?:required|needed))",),
        jurisdiction=dict(work_authorization_eu="Yes", sponsorship_eu="No"),
        education=[dict(institution="University of Bologna", degree="BA Modern Languages", level="Bachelor's degree",
                        subject="Modern Languages", status="completed", start_date="2014-09-01", end_date="2017-06-30")],
        languages={"Italian": ("Native", True), "English": ("Fluent", None), "German": ("Intermediate", None)},
        demographics=dict(gender=("Woman", False), disability=("No", False)))
    irish = Person(
        "irish_grad", "greenhouse", "uk", "Aoife Murphy", "Aoife", "Murphy", "aoife.murphy@example.test", "+447700900789",
        "Leeds", UK, "GB", "Irish", links=dict(portfolio="https://example.test/aoife"),
        employed=False, start=ahead(3), notice="Immediately available",
        notice_patterns=(r"none|no notice|\b0\b|zero|immediate|ninguno|sin preaviso",), notice_weeks=(0, 0),
        relocation="Yes", preferred="Leeds, Dublin", salary=38000,
        immigration=dict(country=UK, route="Citizen", current_right_to_work=True, permanent_unrestricted=True,
                         unrestricted_right_to_work=True, full_time_right_to_work=True, future_right_to_work=True,
                         sponsorship_now_or_future=False, sponsorship_current=False, sponsorship_future=False),
        visa_patterns=(r"citizen|irish|common travel|ciudadan|n/a|not applicable|no visa",),
        visa_forbid=(r"\bvisa\b(?! (?:required|needed))",),
        jurisdiction=dict(work_authorization_eu="Yes", sponsorship_eu="No"),
        education=[
            dict(institution="Trinity College Dublin", degree="BSc Mathematics", level="Bachelor's degree",
                 subject="Mathematics", status="completed", start_date="2021-09-01", end_date="2025-06-30"),
            dict(institution="University College Dublin", degree="MSc Statistics", level="Master's degree",
                 subject="Statistics", status="studying", start_date=ahead(-130).isoformat(),
                 end_date=ahead(600).isoformat())],
        languages={"English": ("Native", None), "Irish": ("Fluent", None), "French": ("Intermediate", None)},
        demographics=dict(gender=("Non-binary", True), pronouns=("they/them/theirs", True), disability=("Yes", True),
                          religion=("Atheist", False)))
    sparse = Person("sparse", "lever", "uk", "Sam Taylor", "Sam", "Taylor", "sam.taylor@example.test", "+447700900999",
                    "", "", "", "")
    return [student, citizen, spanish, settled, irish, sparse]


PEOPLE = build()
BY_KEY = {p.key: p for p in PEOPLE}
PAGES = {  # the same URLs as the policy matrix: one ATS per candidate
    "greenhouse": "https://boards.greenhouse.io/acmerobotics/jobs/4001",
    "lever": "https://jobs.lever.co/acmerobotics/0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0",
    "ashby": "https://jobs.ashbyhq.com/acmerobotics/1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d",
    "workday": "https://acmerobotics.wd5.myworkdayjobs.com/en-US/Careers/job/London/Software-Engineer_R-1001",
}


def page_for(person: Person, posting) -> dict:
    """The page report of the candidate's session (``posting`` is the harness builder): a London role, or the Madrid one."""
    url = PAGES[person.ats]
    if person.job == "uk":
        return posting(url=url, title="Software Engineer - Acme Robotics")
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Software Engineer",
           "description": "<p>" + "Desarrollamos servicios de pago fiables para millones de clientes cada día. " * 8 + "</p>",
           "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics", "url": "https://acmerobotics.example/"},
           "jobLocation": {"address": {"addressLocality": "Madrid", "addressCountry": "ES"}}}
    return posting(url=url, lang="es", jsonld=[json.dumps(job)], title="Ingeniero de software - Acme Robotics")


# ------------------------------------------------------------------------------------------------ seeding
def seed(db, person: Person, *, disclosures: str = "use_confirmed") -> None:
    """Store the candidate's facts exactly as the existing matrices do: verified, source-labelled records through the public
    ingestion helpers; confirmed demographics as explicit user confirmations, unconfirmed ones as old suggestions."""
    from jobagent.application_profile import write_profile
    from jobagent.intelligence.store import entity, ingest_fact, policy

    def record(kind, name, fields, **extra):
        entity_id = entity(db, kind, name)
        for key, value in fields.items():
            value_type = ("boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float))
                          else "list" if isinstance(value, list) else "text")
            ingest_fact(db, entity_id, key, value, value_type=value_type, source="Synthetic verified test record",
                        evidence={"excerpt": str(value)}, verified=True, **extra)
        return entity_id

    identity = dict(full_name=person.full, first_name=person.first, last_name=person.last, email=person.email,
                    phone=person.phone)
    if person.country:
        identity.update(city=person.city, location=f"{person.city}, {person.country}", country=person.country,
                        country_code=person.code, nationality=person.nationality, **person.links)
    if person.employed is not None:
        identity["employment_contract_active"] = person.employed
    if person.start:
        identity["earliest_full_time_start_date"] = person.start.isoformat()
    owner = record("person", person.full, identity)
    for item in person.education:
        record("education", item["institution"], {**item, "rank": level_rank(item["level"].split(" ")[0])})
    for name, (level, native) in person.languages.items():
        record("language", name, {"proficiency": level, **({} if native is None else {"native": native})})
    if person.immigration:
        record("immigration", "UK " + person.immigration["route"], person.immigration)
    for key, (value, confirmed) in person.demographics.items():
        ingest_fact(db, owner, key, value, source="Synthetic explicit confirmation" if confirmed
                    else "Suggested from an old form, never confirmed",
                    evidence={"kind": "user_confirmation" if confirmed else "suggestion"}, verified=confirmed,
                    sensitivity="personal")
    policy(db, "optional_demographics", "decline_or_blank")
    policy(db, "personal_disclosures", disclosures)
    if person.salary and person.job == "uk":
        policy(db, "salary_expected", {"target": person.salary, "currency": person.currency}, {"country": UK})
    for key, value in {**person.jurisdiction, "notice_period": person.notice, "relocation": person.relocation, "preferred_locations": person.preferred,
                       "visa_uk": person.visa_profile}.items():
        if value is not None:
            write_profile(db, key, value, True)
