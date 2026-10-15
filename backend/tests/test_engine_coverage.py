"""COVERAGE of ordinary forms (the counterpart of the safety nets: a pipeline that never answers passes every one of them).

The plain-question gate and the other-party / history rules keep the engine from giving a right-looking wrong answer. This module
measures the other side with REAL field descriptors: the SuggestRequests the content script (``extension/src/content/scan.ts`` over
the bundled ``observation.js``) sends for the fixture pages of the repository (``fixtures/engine/coverage_scans.json``: the pages of
``backend/tests/browser/fixtures``, ``extension/tests/fixtures``, the content-script rig and the widget library fixtures, about 800
controls, captured by the rig of ``extension/tests/content``: regenerate with ``fixtures/engine/capture_coverage_scans.py``) are run through the REAL pipeline (bridge routes -> SuggestService ->
FieldResolver -> CandidateEngine) for a fully populated synthetic candidate.

* ``FAMILIES`` is the canonical set of questions every application form asks about the applicant: names, email, phone, where they
  live, links, the current employer, studies, the right to work, notice, start date, salary, the CV. A control whose OWN label is
  one of them must be answered with the right value in the right tier, unless the page gives POSITIVE evidence that it asks about
  somebody else or about an entry of a history (a referee, an emergency contact, 'Job 1', 'Awards', a block the form repeats).
* An unknown heading, a page title, the job's own title or company, no heading at all are NOT such evidence (``HEADINGS``).
* ``test_ordinary_coverage_is_complete`` pins the coverage (a regression cannot hide behind a safe refusal),
  ``test_no_ordinary_control_is_answered_wrongly`` pins that no value is wrong, and ``test_the_evidence_of_another_party_still_refuses``
  pins the other side: somebody else's details are never the applicant's.

``MERIDIAN_TEST=1 python backend/tests/test_engine_coverage.py run [rows.json]`` prints the coverage table (the numbers of the report); ``report rows.json`` prints the table of rows saved before.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import add_cv, bridge, el, http, make_app, posting  # noqa: E402
from jobagent.application_profile import write_profile  # noqa: E402
from jobagent.intelligence.store import entity, ingest_fact, link, policy  # noqa: E402

SCANS_FILE = json.loads((Path(__file__).parent / "fixtures" / "engine" / "coverage_scans.json").read_text())
# the stress pages (a form of hundreds of controls, hostile employer text) are the safety nets' business: their labels are decoys on purpose
STRESS = {"big_page.html", "many_fields.html", "hostile_text.html"}
SCANS = {page: entry for page, entry in SCANS_FILE["pages"].items() if page not in STRESS}

# the candidate (invented): everything an application form commonly asks for
FIRST, LAST, PREFERRED = "Alex", "Morgan", "Alexandra"
FULL = f"{FIRST} {LAST}"
EMAIL, PHONE = "alex.morgan@example.test", "+447700900123"
CITY, COUNTRY, LOCATION = "London", "United Kingdom", "London, United Kingdom"
ADDRESS, POSTCODE = "12 Example Street", "E1 6AN"
LINKEDIN, GITHUB = "https://www.linkedin.com/in/alex-morgan-example", "https://github.com/alex-morgan-example"
PORTFOLIO = "https://alexmorgan.example.test"
COMPANY, TITLE = "Brightwave Labs", "Software Engineer"
SALARY, START = 55000, "2026-11-02"
JOB_TITLE, JOB_COMPANY = "Software Engineer", "Acme Robotics"


def populate(db):
    """A fully populated, verified synthetic candidate: contact, address, links, employer, two degrees, languages, a UK right to work
    and the confirmed policies (salary, personal disclosures)."""
    def record(kind, name, fields, sensitivity="professional"):
        entity_id = entity(db, kind, name)
        for key, value in fields.items():
            kind_of = ("boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float))
                       else "list" if isinstance(value, list) else "text")
            ingest_fact(db, entity_id, key, value, value_type=kind_of, source="Synthetic verified test record",
                        evidence={"excerpt": str(value)}, verified=True, sensitivity=sensitivity)
        return entity_id

    person = record("person", FULL, dict(
        full_name=FULL, first_name=FIRST, last_name=LAST, preferred_name=PREFERRED, email=EMAIL, phone=PHONE, city=CITY,
        location=LOCATION, country=COUNTRY, country_code="GB", address=ADDRESS, postcode=POSTCODE, nationality="British",
        linkedin=LINKEDIN, github=GITHUB, portfolio=PORTFOLIO, current_company=COMPANY, current_title=TITLE,
        employment_contract_active=True, current_employment_notice_days=28, earliest_full_time_start_date=START,
        professional_interests="Robotics software and backend engineering"))
    for key, value in dict(gender="Non-binary", pronouns="they/them/theirs").items():
        ingest_fact(db, person, key, value, source="Synthetic explicit confirmation", evidence={"kind": "user_confirmation"},
                    verified=True, sensitivity="personal")
    record("education", "Northfield University", dict(
        institution="Northfield University", degree="BSc Computer Science", level="Bachelor's degree", rank=1,
        subject="Computer Science", status="completed", start_date="2021-09-01", end_date="2024-06-30",
        classification="First Class Honours"))
    record("education", "Westbridge University", dict(
        institution="Westbridge University", degree="MSc Artificial Intelligence", level="Master's degree", rank=2,
        subject="Artificial Intelligence", status="completed", start_date="2024-09-01", end_date="2025-09-30",
        classification="Merit"))
    record("language", "English", dict(proficiency="Native"))
    record("language", "Spanish", dict(proficiency="Fluent"))
    record("immigration", "UK Right to Work", dict(country=COUNTRY, route="Citizen", current_right_to_work=True,
                                                    sponsorship_now_or_future=False))
    record("experience", COMPANY, dict(role=TITLE, start_date="2025-10-01", ongoing=True,
                                       story="I built backend services for a logistics analytics product."))
    project = record("project", "Warehouse Route Planner", dict(
        start_date="2024-01-01", end_date="2024-05-31", technologies=["Python"],
        story="I built a route planner for warehouse robots using Python."))
    link(db, entity(db, "skill", "Python"), "used_in", project, [])
    policy(db, "optional_demographics", "decline_or_blank")
    policy(db, "personal_disclosures", "use_confirmed")
    policy(db, "salary_expected", {"target": SALARY, "currency": "GBP"}, {"country": COUNTRY})
    # and the Profile page (Settings > Profile), which is where a person fills in the fields of an application: the flat facts the
    # legacy resolver and the question memory read, mirrored into the typed layer exactly as the app does
    for key, value in dict(full_name=FULL, first_name=FIRST, last_name=LAST, preferred_name=PREFERRED, email=EMAIL, phone=PHONE,
                           location=LOCATION, address=ADDRESS, postcode=POSTCODE, linkedin=LINKEDIN, github=GITHUB,
                           portfolio=PORTFOLIO, current_company=COMPANY, current_title=TITLE).items():
        write_profile(db, key, value, True)
    return db


def page_url(page: str) -> str:
    name = page.split("?")[0]
    for ats, url in (("greenhouse", "https://boards.greenhouse.io/acmerobotics/jobs/4001"),
                     ("lever", "https://jobs.lever.co/acmerobotics/0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"),
                     ("ashby", "https://jobs.ashbyhq.com/acmerobotics/1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"),
                     ("workday", "https://acmerobotics.wd5.myworkdayjobs.com/en-US/Careers/job/London/Software-Engineer_R-1001")):
        if ats in name:
            return url
    return "https://careers.acmerobotics.example/jobs/42/apply"


# ================================================================================================ the pipeline over the scans
async def run_scans(scans: dict) -> list[dict]:
    """-> one row per scanned control: {page, frame, index, element, result} (``result``: the Suggestion the pipeline gave it)."""
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="meridian-coverage-") as tmp:
        app = make_app(Path(tmp), seed=False)
        populate(app.state.db)
        add_cv(app, approved=True, filename="my-cv.pdf")
        try:
            async with http(bridge(app)) as client:
                for page, entry in scans.items():
                    requests = {}
                    for request in entry["requests"]:  # the fullest scan of each frame
                        if len(request["elements"]) > len(requests.get(request["frame_key"], {}).get("elements", [])):
                            requests[request["frame_key"]] = request
                    if not requests:
                        continue
                    created = (await client.post("/v1/sessions", json=posting(url=page_url(page)))).json()
                    sid = created["session_id"]
                    await app.state.extension_research.wait(created["research"]["run_id"], 120)
                    for frame_key, request in requests.items():
                        response = await client.post(f"/v1/sessions/{sid}/suggest", json={**request, "mode": "scan"})
                        assert response.status_code == 200, (page, response.text[:300])
                        by_node = {s["node_id"]: s for s in response.json()["results"]}
                        for index, element in enumerate(request["elements"]):
                            rows.append({"page": page, "frame": frame_key, "index": index, "element": element,
                                         "result": by_node.get(element["node_id"])})
        finally:
            await app.state.extension_sessions.close()
            await app.state.extension_generation.close()
            await app.state.extension_research.close()
    return rows


def run(coroutine):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


# ================================================================================================ the canonical ordinary questions
def fold(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[*✱​]", " ", text)
    text = re.sub(r"\((?:required|optional|mandatory|obligatorio|opcional)\)", " ", text)
    text = re.sub(r"\(s\)", "", text)
    return re.sub(r"\s+", " ", text).strip(" :?.")


def shown(result) -> str:
    """What the suggestion would put in the control."""
    value = (result or {}).get("suggestion") or {}
    return str(value.get("text") or (value.get("option") or {}).get("label") or ", ".join(value.get("labels") or [])
               or (value.get("document") or {}).get("filename") or value.get("display") or "")


def digits(value) -> str:
    return re.sub(r"\D", "", str(value))


def has(*needles):
    return lambda text: all(needle.lower() in text.lower() for needle in needles)


def phone(text):
    return digits(text).endswith("7700900123")


def same(expected):
    return lambda text: text.strip().lower() == expected.lower()


# (family, pattern of the folded label (a full match), what the control must show, the tier)
FAMILIES = [
    ("first name", r"(?:legal |given |first )(?:first )?names?|first|forenames?|given", same(FIRST), "tab"),
    ("last name", r"(?:legal |family |last )(?:last )?names?|surname|last|family", same(LAST), "tab"),
    ("full name", r"(?:full |your |legal |complete )?(?:name|full name)|name", same(FULL), "tab"),
    ("preferred name", r"preferred (?:first )?name", has(PREFERRED), "tab"),
    ("email", r"e ?-?mail(?: address)?|email address|correo(?: electronico)?|your email", same(EMAIL), "tab"),
    ("phone", r"(?:mobile |cell |telephone |contact )?(?:phone|telephone|mobile)(?: number| phone)?|phone number|mobile phone number|"
              r"phone \(international format\)|telefono(?: movil)?|movil", phone, "tab"),
    ("country", r"country(?: of residence)?|home country|country/territory|country \*?", has("United Kingdom"), "tab"),
    ("city", r"city|town|current city|city of residence", has("London"), "tab"),
    ("location", r"(?:current |your |present )?location|where are you (?:currently )?(?:located|based)|current location|"
                 r"location \(city\)|where are you based", has("London"), "tab"),
    ("postcode", r"post ?code|postal code|zip(?: code)?", same(POSTCODE), "tab"),
    ("linkedin", r"linkedin(?: profile| url)?|linkedin profile url", same(LINKEDIN), "tab"),
    ("github", r"github(?: url| profile)?", same(GITHUB), "tab"),
    ("portfolio", r"portfolio(?: url| link)?|portfolio / personal website|personal website|portfolio or personal website", same(PORTFOLIO), "tab"),
    ("current company", r"current (?:company|employer|organi[sz]ation)|present employer|current company name", has(COMPANY), "tab"),
    ("current title", r"current (?:job )?title|current position|current role|present (?:job )?(?:title|position)|job title", has(TITLE), "tab"),
    ("degree", r"highest (?:completed )?(?:degree|level of education)(?: level| completed)?", has("Master"), "tab"),
    ("work authorisation (UK)", r"(?:are you )?(?:legally )?(?:authori[sz]ed|allowed|eligible) to work in the (?:uk|united kingdom)|"
                                r"do you have the right to work in the (?:uk|united kingdom)|i have the right to work in the uk|"
                                r"right to work in the (?:uk|united kingdom)", same("Yes"), "explicit"),
    ("sponsorship", r"(?:do you |will you )?(?:now or in the future )?(?:need|require)(?: visa)? sponsorship(?: now or in the future)?|"
                    r"will you now or in the future require (?:visa )?sponsorship|do you require (?:visa )?sponsorship|"
                    r"do you need sponsorship|do you require visa sponsorship", same("No"), "explicit"),
    ("notice period", r"(?:what is your )?(?:current )?notice period", lambda t: bool(t.strip()), "tab"),
    ("start date", r"(?:when can you start|earliest start date|available from|start date|when could you start|availability)",
     lambda t: START in t or "2 November 2026" in t, "tab"),
    ("salary expectation", r"(?:what are your )?(?:expected salary|salary expectations?(?: \(gbp\))?)|desired salary|expected annual salary",
     lambda t: digits(t) == str(SALARY), "tab"),
    ("resume upload", r"resume|cv|resume/cv|resume / cv|attach resume|candidate\[resume\]|curriculum vitae", has("my-cv.pdf"), "tab"),
    ("languages spoken", r"(?:which )?languages (?:do )?you speak(?: fluently)?|languages you speak", has("English"), "tab"),
    ("gender (confirmed)", r"(?:what is your )?gender(?: identity)?|describe your gender", has("Non-binary"), "explicit"),
    ("pronouns (confirmed)", r"(?:preferred )?pronouns|what are your (?:preferred )?pronouns", has("they/them"), "explicit"),
]
FAMILY_PATTERNS = [(name, re.compile(pattern), check, tier) for name, pattern, check, tier in FAMILIES]
# what gives POSITIVE evidence that a control asks about somebody else or about an entry of a history (the oracle of this test, written
# separately from the engine's): a heading that IS such a thing, or a person named in the control's own hint, name or id
ELSEWHERE_HEADING = re.compile(
    r"(?:emergency contact|next of kin|referees?(?: details)?|references?|job|position|employer|employment|work experience|"
    r"education|studies|awards|certifications|publications|projects|trabajo|estudios|referencias|premios|contacto de emergencia|"
    r"manager|supervisor)(?: \d+)?")
ELSEWHERE_PERSON = re.compile(r"\b(?:emergency|referees?|references?|next of kin|spouse|manager|supervisor|recruiter)\b")
# a placeholder heading the scanner writes when the page has none
NO_HEADING = {"application details", ""}


def family_of(element: dict):
    """The ordinary family an element's OWN label belongs to, or None."""
    choice = element["role"] in {"radio", "checkbox"} and element.get("options")
    label = fold((element.get("context") or element.get("label")) if choice else (element.get("label") or element.get("context")))
    for name, pattern, check, tier in FAMILY_PATTERNS:
        if pattern.fullmatch(label):
            return name, check, tier
    return None


def elsewhere(element: dict) -> bool:
    heading = fold(element.get("section_label"))
    hints = fold(" ".join(str(element.get(key) or "") for key in ("description", "placeholder", "name", "id")))
    return bool(ELSEWHERE_HEADING.fullmatch(heading) or ELSEWHERE_PERSON.search(hints))


STATES_ANSWERED = {"VERIFIED", "INFERRED"}
# the page offers no option the (right) answer maps to: not the engine's coverage
NO_OPTION = {"BAD_ENUM_MAPPING"}


def outcome(row: dict):
    """-> (family, 'right' | 'wrong' | 'refused' | 'elsewhere' | 'tier', detail) for an ordinary control, else None."""
    found = family_of(row["element"])
    if found is None:
        return None
    family, check, tier = found
    result = row["result"] or {}
    if elsewhere(row["element"]):
        return family, "elsewhere", result.get("state")
    if result.get("state") in STATES_ANSWERED:
        text = shown(result)
        if not check(text):
            return family, "wrong", text
        if tier == "tab" and result.get("insert") != "tab":
            return family, "tier", result.get("insert")
        return family, "right", text
    if result.get("state") == "SKIP":
        return None
    cause = (result.get("needs") or {}).get("root_cause") or result.get("state")
    if cause in NO_OPTION:
        return family, "no option", cause
    return family, "refused", cause


def table(rows: list[dict]) -> dict:
    """{family: Counter(right / wrong / refused / tier / elsewhere)} and the refused rows by cause."""
    counts: dict[str, Counter] = defaultdict(Counter)
    refused: list = []
    for row in rows:
        found = outcome(row)
        if found is None:
            continue
        family, kind, detail = found
        counts[family][kind] += 1
        if kind in {"refused", "wrong", "tier"}:
            refused.append((family, kind, row["page"], row["element"]["label"][:50], row["element"].get("section_label", "")[:30], detail))
    return {"counts": counts, "problems": refused}


def render(rows: list[dict]) -> str:
    report = table(rows)
    lines = [f"{'family':26} {'controls':>8} {'right':>6} {'wrong':>6} {'refused':>8} {'tier':>5}   coverage"]
    total = Counter()
    for family, *_ in FAMILIES:
        counts = report["counts"].get(family)
        if not counts:
            continue
        n = counts["right"] + counts["wrong"] + counts["refused"] + counts["tier"]
        total.update(counts)
        lines.append(f"{family:26} {n:8d} {counts['right']:6d} {counts['wrong']:6d} {counts['refused']:8d} {counts['tier']:5d}   "
                     f"{100 * counts['right'] // max(n, 1):3d}%")
    n = total["right"] + total["wrong"] + total["refused"] + total["tier"]
    lines.append(f"{'ALL ordinary':26} {n:8d} {total['right']:6d} {total['wrong']:6d} {total['refused']:8d} {total['tier']:5d}   "
                 f"{100 * total['right'] // max(n, 1):3d}%   (+{total['elsewhere']} controls that stand under evidence of another party/entry)")
    return "\n".join(lines)


# ================================================================================================ the fixtures through the pipeline
@pytest.fixture(scope="module")
def rows():
    patch = pytest.MonkeyPatch()
    patch.setenv("MERIDIAN_TEST", "1")
    try:
        return run(run_scans(SCANS))
    finally:
        patch.undo()


def test_the_scans_are_real_descriptors_of_many_pages():
    pages = [entry for entry in SCANS.values() if entry["requests"]]
    controls = sum(len(request["elements"]) for entry in pages for request in entry["requests"])
    assert len(pages) >= 100 and controls >= 600
    first = pages[0]["requests"][0]["elements"][0]
    assert {"node_id", "role", "label", "section_label", "section_id", "form_id", "operations"} <= set(first)
    assert {"workday_frame.html", "greenhouse_apply.html", "engine_subjects.html"} <= set(SCANS)


def test_ordinary_coverage_is_complete(rows):
    """Every control whose own label is a canonical ordinary question and whose page gives no evidence of another party is answered."""
    report = table(rows)
    refused = [p for p in report["problems"] if p[1] == "refused"]
    assert not refused, "\n" + "\n".join(map(str, refused))
    assert sum(c["right"] for c in report["counts"].values()) >= 150


def test_no_ordinary_control_is_answered_wrongly(rows):
    assert not [p for p in table(rows)["problems"] if p[1] in {"wrong", "tier"}]


def test_the_evidence_of_another_party_still_refuses(rows):
    """A control under a referee / emergency contact / job / awards heading is never answered with the applicant's own details."""
    checked = 0
    for row in rows:
        found = outcome(row)
        if found and found[1] == "elsewhere":
            checked += 1
            assert (row["result"] or {}).get("state") not in STATES_ANSWERED, (row["page"], row["element"]["label"], row["element"].get("section_label"))
    assert checked >= 5


# ================================================================================================ the headings
# Positive evidence or none: the SAME labels under every kind of heading a real form has. A heading the engine does not know is
# no evidence of anything (the page's title, the job's title or company, 'My Information', 'Datos personales', no heading at all);
# one that NAMES somebody else or an entry of a history is.
ORDINARY_HEADINGS = [
    "", "Application details", "Apply for this job", "Job application", "Application", "Application form", "My Information",
    "Your information", "Basic information", "Personal information", "Personal details", "Candidate", "Candidate details",
    "Applicant details", "About you", "Your details", "Contact details", "Contact information", "Identification", "Profile",
    "Profile information", "Account", "Create account", "Create your account", "Sign up", "Registration", "Resume", "Let's get started",
    "Tell us about yourself", "Información personal", "Datos personales", "Tus datos", "Identificación", "Perfil", "Crear cuenta",
    "Solicitud", "Contacto", f"{JOB_TITLE} at {JOB_COMPANY}", JOB_TITLE, JOB_COMPANY, f"Apply for {JOB_TITLE}",
    f"{JOB_TITLE} - {JOB_COMPANY}", f"{JOB_COMPANY} careers", "react-select demo application", "Material UI demo", "Part A: the vendor's markup",
    "Eligibility", "Background", "Career", "Additional questions", "Voluntary self-identification", "Step 1 of 3", "Your application",
]
ELSEWHERE_HEADINGS = [
    "Emergency contact", "Emergency contact details", "Referee", "Referee details", "Referee 1", "References", "Reference", "Next of kin",
    "Spouse", "Parent or guardian", "Line manager", "Supervisor", "Recruiter", "Job 1", "Job 2", "Position", "Position 1", "Employer",
    "Employment", "Employment history", "Work experience", "Previous employment 2", "Education", "Studies", "School", "University", "Awards",
    "Certifications", "Publications", "Projects", "Trabajo 1", "Estudios", "Experiencia laboral", "Referencias", "Premios",
    "Contacto de emergencia", "Work background", "Professional background", "Academic background", "Historial laboral", "Formación académica",
]
IDENTITY = [("Name", "text", FULL), ("Full name", "text", FULL), ("First name", "text", FIRST), ("Last name", "text", LAST),
            ("Email", "email", EMAIL), ("Phone", "tel", "7700900123"), ("Nombre", "text", FIRST), ("Correo electrónico", "email", EMAIL)]
ENTRY = [("City", "text", "London"), ("Country", "text", "United Kingdom"), ("Location", "text", "London"),
         ("Start date", "text", "2 November 2026")]
LABELS = IDENTITY + ENTRY


def heading_cases(headings, labels):
    return [(heading, label, kind, wanted) for heading in headings for label, kind, wanted in labels]


async def run_cases(cases, *, title=JOB_TITLE):
    """-> {(heading, label): Suggestion}: every case alone in its own form of one page (nothing repeats, nothing signs)."""
    answers = {}
    with tempfile.TemporaryDirectory(prefix="meridian-headings-") as tmp:
        app = make_app(Path(tmp), seed=False)
        populate(app.state.db)
        try:
            async with http(bridge(app)) as client:
                page = posting(title=f"{title} - {JOB_COMPANY}", h1=title)
                posted = json.loads(page["page"]["jsonld"][0])
                posted["title"] = title  # the job the structured data of the page names
                page["page"]["jsonld"] = [json.dumps(posted)]
                created = (await client.post("/v1/sessions", json=page)).json()
                sid = created["session_id"]
                await app.state.extension_research.wait(created["research"]["run_id"], 120)
                for start in range(0, len(cases), 150):
                    chunk = cases[start:start + 150]
                    elements = [el(2 * n + 1, "textbox", label, type=kind, section_id=f"s{start + n}", section_label=heading,
                                   form_id=start + n, name="", id="") for n, (heading, label, kind, _) in enumerate(chunk)]
                    body = {"document_id": f"doc-{start}", "frame_url": page["page"]["url"], "frame_key": "top", "elements": elements,
                            "mode": "scan", "challenge": False, "mfa": False, "login": False}
                    response = await client.post(f"/v1/sessions/{sid}/suggest", json=body)
                    assert response.status_code == 200, response.text
                    by_node = {item["node_id"]: item for item in response.json()["results"]}
                    for n, (heading, label, *_rest) in enumerate(chunk):
                        answers[(heading, label)] = by_node[2 * n + 1]
        finally:
            await app.state.extension_sessions.close()
            await app.state.extension_generation.close()
            await app.state.extension_research.close()
    return answers


@pytest.fixture(scope="module")
def headed():
    patch = pytest.MonkeyPatch()
    patch.setenv("MERIDIAN_TEST", "1")
    try:
        return run(run_cases(heading_cases(ORDINARY_HEADINGS, LABELS) + heading_cases(ELSEWHERE_HEADINGS, LABELS)))
    finally:
        patch.undo()


@pytest.mark.parametrize("heading", ORDINARY_HEADINGS, ids=lambda h: h or "(no heading)")
def test_an_unknown_or_general_heading_never_stops_an_ordinary_label(headed, heading):
    """No heading, the scanner's default, the page's title, the job's title or company, 'My Information', 'Datos personales'...:
    the label asks the applicant's own detail and gets it, in the Tab tier."""
    for label, _kind, wanted in LABELS:
        got = headed[(heading, label)]
        assert got["state"] == "VERIFIED" and got["insert"] == "tab", (heading, label, got["state"], (got.get("needs") or {}).get("root_cause"))
        assert wanted.lower() in shown(got).lower() or digits(wanted) and digits(wanted) in digits(shown(got)), (heading, label, shown(got))


@pytest.mark.parametrize("heading", ELSEWHERE_HEADINGS)
def test_a_heading_that_names_another_party_or_an_entry_still_refuses(headed, heading):
    """A referee, an emergency contact, a job, a school, an award: the bare label is theirs, not the applicant's."""
    for label, _kind, _wanted in LABELS:
        got = headed[(heading, label)]
        assert got["state"] == "NEEDS_YOU" and got["insert"] == "never", (heading, label, got["state"], shown(got))


@pytest.mark.parametrize("title", ["Account Manager", "Recruiter", "Business Partner", "Employee Benefits Specialist", "Education Consultant",
                                   "Referee Coordinator", "Solicitor"])
def test_a_job_title_that_contains_a_word_for_somebody_else_is_still_only_the_page(title):
    """'Account Manager', 'Recruiter', 'Business Partner': the title of the job is the title of the page, not the heading of a section about
    a manager: the applicant's own details under it are still the applicant's."""
    cases = heading_cases([title, f"{title} - {JOB_COMPANY}", f"Apply for {title}", f"Senior {title} at Acme", f"{title} (London)"], IDENTITY + ENTRY)
    got = run(run_cases(cases, title=title))
    refused = [(h, label, shown(g)) for (h, label), g in got.items() if g["state"] != "VERIFIED"]
    assert not refused, refused


def test_the_other_names_of_the_current_job_and_the_postcode_are_the_profiles():
    """'Current position', 'Current role', 'Present employer', 'Current organisation', 'Post code': the Profile's current job title,
    employer and postcode under the names forms give them."""
    wanted = {"Current position": TITLE, "Current role": TITLE, "Present employer": COMPANY, "Current organisation": COMPANY,
              "Post code": POSTCODE, "Current job title": TITLE}
    got = run(run_cases([("Application details", label, "text", "") for label in wanted]))
    for label, value in wanted.items():
        answer = got[("Application details", label)]
        assert answer["state"] in STATES_ANSWERED and shown(answer) == value, (label, answer["state"], shown(answer))


async def run_textareas(labels):
    answers = {}
    with tempfile.TemporaryDirectory(prefix="meridian-textarea-") as tmp:
        app = make_app(Path(tmp), seed=False)
        populate(app.state.db)
        try:
            async with http(bridge(app)) as client:
                page = posting()
                created = (await client.post("/v1/sessions", json=page)).json()
                sid = created["session_id"]
                await app.state.extension_research.wait(created["research"]["run_id"], 120)
                elements = [el(2 * n + 1, "textbox", label, tag="textarea", type="textarea", form_id=n, section_id=f"s{n}")
                            for n, label in enumerate(labels)]
                body = {"document_id": "doc-t", "frame_url": page["page"]["url"], "frame_key": "top", "elements": elements, "mode": "scan",
                        "challenge": False, "mfa": False, "login": False}
                response = await client.post(f"/v1/sessions/{sid}/suggest", json=body)
                by_node = {item["node_id"]: item for item in response.json()["results"]}
                answers = {label: by_node[2 * n + 1] for n, label in enumerate(labels)}
        finally:
            await app.state.extension_sessions.close()
            await app.state.extension_generation.close()
            await app.state.extension_research.close()
    return answers


def test_an_address_in_a_textarea_is_answered_and_a_location_prompt_in_one_is_not():
    got = run(run_textareas(["Address", "Home address", "Where are you based?", "Describe your address history"]))
    for label in ("Address", "Home address"):
        assert got[label]["state"] == "VERIFIED" and shown(got[label]) == ADDRESS, (label, got[label]["state"])
    for label in ("Where are you based?", "Describe your address history"):
        assert got[label]["state"] != "VERIFIED", label


def test_the_coverage_table(rows, capsys):
    print("\n" + render(rows))


if __name__ == "__main__" and sys.argv[1:2] in (["run"], ["report"]):
    import os
    os.environ["MERIDIAN_TEST"] = "1"
    if sys.argv[1] == "run":  # run [rows.json]: the pipeline over the scans, the rows saved, the table printed
        data = run(run_scans(SCANS))
        if len(sys.argv) > 2:
            json.dump(data, open(sys.argv[2], "w"))
    else:  # report rows.json: the table of rows saved before
        data = json.load(open(sys.argv[2]))
    print(render(data))
    for problem in table(data)["problems"]:
        print(problem)
