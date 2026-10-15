from __future__ import annotations

from datetime import date
import hashlib
import re

from ..db import dumps, now, uid
from ..automation.answers import classify_question, fold, normalize
from ..automation.disclosures import (
    disclosure_action,
    disclosure_memory_key,
    disclosure_profile_key,
    disclosure_resolution,
    personal_disclosure_kind,
)
from . import academic, jurisdiction, languages as language_tools, legal, plain
from .narratives import compose_narrative
from .store import Knowledge
from .semantics import (DIGITS_ONLY, HISTORY_BLOCKED, MAX_QUESTION, NARRATIVE_INTENTS, bounded, entry_heading, interpret,
                        party_in, semantic_interpret, education_level, without_page_heading)
from .temporal import interval, exposure, requested_date
from .formatting import adapt, choice, format_date, infer_format
from .salary import explanation as salary_explanation, mismatch as salary_mismatch



# Credential-like controls are never described to a model, whatever their label says.
SECRET_INPUT_TYPES = {"password", "hidden"}


def confirmed_visa_route_options(route, options):
    """Match route enums without adding conditions to confirmed work rights.

    Slash/or alternatives are disjunctions: a confirmed Student route supports
    Student/Graduate, but not Student and Graduate. Extra eligibility assertions
    must be resolved separately and therefore do not match this grammar.
    """
    route_name = normalize(re.sub(r"\s+visa$", "", str(route or ""), flags=re.I))
    if not route_name:
        return []
    supported = []
    for option in options:
        label = str(option.get("label") or "").strip()
        match = re.fullmatch(
            r"(.+?)\s+visa(?:\s*(?:\(\s*(?:with\s+)?(?:work rights|right to work|work authori[sz]ation)\s*\)"
            r"|with\s+(?:work rights|right to work|work authori[sz]ation)))?",
            label, re.I,
        )
        if not match:
            continue
        alternatives = [normalize(part) for part in re.split(r"\s*/\s*|\s+or\s+", match[1], flags=re.I)]
        if route_name in alternatives:
            supported.append((len(alternatives), label))
    # Prefer the exact route over a broader union, but never arbitrarily choose
    # between equally specific options.
    if not supported:
        return []
    specificity = min(count for count, _ in supported)
    return [label for count, label in supported if count == specificity]


def country_name(value):
    """The one spelling of a country (or region) that a stored record and a question can be compared by ('GB', 'UK', 'Reino
    Unido' and 'United Kingdom' are one country). Anything unknown is kept, title-cased."""
    text = str(value or "").strip()
    if not text:
        return None
    from ..extension import places
    return places.country_of(text) or jurisdiction.canonical(text) or None


LEGAL_INTENTS = {"WORK_AUTHORIZATION", "SPONSORSHIP", "VISA"}
# A route that is a status, not a visa: 'I currently hold a Citizen visa' is a false sentence
NOT_A_VISA = {"citizen", "british citizen", "irish citizen", "uk citizen", "settled", "pre settled", "settled status", "pre settled status",
              "indefinite leave to remain", "ilr", "permanent resident", "none", "no visa", "no visa required", "other", "not applicable"}
# the same, by the words of the status ('EU Settlement Scheme (settled status)', 'Settled status (EUSS)', 'Ciudadano', 'Residencia permanente')
NOT_A_VISA_WORDS = re.compile(r"\b(?:citizen\w*|ciudadan\w+|settled|pre settled|settlement scheme|euss|indefinite leave|ilr|permanent resident|"
                              r"permanent residen\w+|residencia permanente|asentad\w+|naturalis\w+|nacionalidad)\b")


# what asks for a SENTENCE about the status ('What is your visa status?', 'Please describe / explain / confirm your status', 'details of
# your visa'), in either language: the answer is the status written as a sentence, and only for a status that is a visa
VISA_SENTENCE = re.compile(r"status|detail|explain|confirm|describe|situacion|estado|estatus|detalle|explica|confirma|describe|cuentanos")
# ... of which these ask the status to be DESCRIBED (a sentence about a status that is no visa is false); the others ask what it IS
VISA_DESCRIBE = re.compile(r"detail|explain|confirm|describe|detalle|explica|confirma|cuentanos|elaborate|tell us")


def not_a_visa(route) -> bool:
    """A route that is a status, not a visa: nobody 'holds a Citizen visa' or 'a EU Settlement Scheme visa'."""
    text = normalize(str(route or ""))
    return text in NOT_A_VISA or bool(NOT_A_VISA_WORDS.search(text))


HISTORY_BLOCKED_INTENTS = HISTORY_BLOCKED
# Kinds of question a yes/no question about is answered whole, never as the join of the answers of its halves.
YES_NO_INTENTS = {"LANGUAGE", "WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "EDUCATION", "DEGREE", "GRADUATION"}
# The kinds of question whose answer is true FOR A PLACE: the place the question names, if it names one, else the vacancy's.
JURISDICTIONAL = {"WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "SALARY"}
VACANCY_PLACES = (
    (r"\blondon|\bunited kingdom\b|\buk\b|\bengland\b|\bscotland\b|\bwales\b|\breino unido\b|\bgran bretana\b|\binglaterra\b|\bescocia\b|\bgales\b|\blondres\b", "United Kingdom"),
    (r"\bunited states\b|\busa\b|\bnew york\b|\bsan francisco\b|\bestados unidos\b|\bnueva york\b", "United States"),
    (r"\bspain\b|\bmadrid\b|\bbarcelona\b|\bespana\b", "Spain"),
    (r"\bfrance\b|\bparis\b|\bfrancia\b", "France"),
    (r"\bgermany\b|\bberlin\b|\bmunich\b|\balemania\b", "Germany"),
    (r"\bcanada\b|\btoronto\b|\bvancouver\b", "Canada"),
)


def vacancy_country(job):
    """The country the vacancy is in: what the caller already read (``country``), else the first place of its location text
    the engine knows. None when nothing says (several countries, no stated country, a remote role limited to a region)."""
    if job.get("country"):
        return country_name(job["country"])
    # A caller that already READ the place (the browser assistant lists the countries it found in ``countries``) has
    # decided: no country there means several, none stated or a remote role limited to a region, and the location text
    # must not make the engine pick one (the first city it knows) for the candidate.
    location = "" if "countries" in job else normalize(str(job.get("location") or ""))
    for pattern, country in VACANCY_PLACES:
        if re.search(pattern, location):
            return country
    return None


def jurisdiction_of(job, question=""):
    """-> (place, note): the place a question is about. A place the question NAMES wins over the vacancy's country: it is
    the one named country, or the one named region ('the EU': the caller looks for a record of exactly that region). Several
    places, an ambiguous or unknown name ('Georgia', 'Kyiv') and a region combined with a country have NO single place: the
    answer is then not the vacancy's, nobody's: None, and ``note`` says why ('several', 'unknown', 'ambiguous'). 'UK or the
    EU' on a UK vacancy is the vacancy's: a right in either place answers yes (note 'or': only a yes is ever given)."""
    named = jurisdiction.named_places(question)
    unknown = [place for place in named["places"] if not jurisdiction.place_of_job(job, place)]
    if named["ambiguous"]:
        return None, "ambiguous:" + ", ".join(named["ambiguous"])
    if unknown:
        return None, "unknown:" + ", ".join(unknown)
    names = [*named["countries"], *named["regions"]]
    vacancy = vacancy_country(job)
    if len(names) == 1:
        return names[0], ""
    if len(names) > 1:
        if named["connective"] == "or" and vacancy in names:
            return vacancy, "or"
        return None, "several:" + ", ".join(names)
    return vacancy, ""


def target_country(job, question=""):
    """The COUNTRY a question is about: the one it names, else the vacancy's. None for a region, several places, or a place
    nobody can place (use ``jurisdiction_of`` to know which)."""
    place, _ = jurisdiction_of(job, question)
    return None if place and jurisdiction.is_region(place) else place


# Text written into a label to steer the assistant, not to ask the candidate something. A keyword engine reads WHICH fact
# a label asks for from its words, so an instruction hidden in a label can pick another fact: such a question is never
# answered from facts (the user answers it).
INSTRUCTION_IN_LABEL = re.compile(
    # the SHAPE of an instruction to an assistant, in English and Spanish, never a single word that an ordinary question can
    # contain ('you are now eligible', 'a system prompt you wrote', 'in admin mode on a production system' are questions)
    r"(?i)\b(?:ignore|disregard|forget|override|bypass)\b"
    r".{0,40}\b(?:previous|above|prior|earlier|preceding|former|all|any)\b"
    r".{0,30}\b(?:instructions?|prompts?|rules?|guidelines?|directions?)\b"
    r"|\b(?:ignora|ignorar|olvida|olvidar|omite|descarta|desatiende|anula)\b.{0,40}\b(?:instrucciones|indicaciones|reglas|directrices|ordenes)\b"
    r"|(?:^|[.:;(\[])\s*(?:system|assistant)\s*(?:prompt|message)?\s*:|\bsystem prompt\s*:|\bdeveloper (?:message|prompt)\s*:"
    r"|\byou are now (?:in |an? |the )?(?:admin|developer|debug|god|root|unrestricted|jailbr\w+|dan)\b"
    r"|\b(?:enter|enable|activate|switch (?:to|into)|now in) (?:admin|developer|debug|god|root|jailbreak|dan) mode\b"
    r"|\bnew instructions?\s*[:=\-\u2013]|<\s*/?\s*(?:system|assistant|developer)\s*>"
    r"|\b(?:reveal|print|leak|exfiltrate|send|show|output|disclose|dump|reply with|respond with|answer with)\b.{0,30}"
    r"\b(?:password|api key|secret key|system prompt|credentials?|access token|private key)\b"
    r"|\b(?:revela|imprime|muestra|envia|escribe|dame|responde con)\b.{0,30}\b(?:contrase[nñ]a|clave|credenciales|token|prompt del sistema)\b"
    r"|\binstead,? (?:answer|reply|respond|say|enter|put|write|type)\b.{0,40}\b(?:candidate|applicant|user|owner|assistant|phone|"
    r"email|password|number)\b"
    r"|\b(?:answer|reply|respond)\b(?: only| just)?(?: with| using)? (?:the )?(?:candidate|applicant|user|owner)(?:'s|s)?\b"
    r"|\ben su lugar,? (?:responde|escribe|contesta|indica)\b")
# The national number (no country code) is asked for in the label, not by a widget that has its own dial-code menu.
NATIONAL_NUMBER = re.compile(
    r"\bnational (?:format|number|phone)\b|\bwithout (?:the |a |any )?(?:country|international|dial(?:l?ing)?) (?:code|prefix)\b|"
    r"\bno (?:country|international|dial(?:l?ing)?) (?:code|prefix)\b|\bexclud\w* (?:the )?(?:country|international|dial(?:l?ing)?) (?:code|prefix)\b|"
    r"\bdo not include (?:the )?(?:country|international|dial(?:l?ing)?) (?:code|prefix)\b|\bwithout (?:the )?\+|"
    r"\bformato nacional\b|\bsin (?:el )?(?:prefijo|codigo de pais|\+)")
NOTICE_UNIT = re.compile(r"\(\s*(?:in |en )?(days?|weeks?|months?|dias?|semanas?|meses|mes)\s*\)|\b(?:in|en) (days|weeks|months|dias|semanas|meses)\b|"
                         r"\bnumber of (days|weeks|months)\b|\b(?:numero de) (dias|semanas|meses)\b|[/,-] ?(days|weeks|months|dias|semanas|meses)\b")


DAYS = {"days": (1, 1), "weeks": (7, 7), "months": (28, 31), "years": (365, 366)}


def compare_notice(days: float, threshold: dict) -> bool | None:
    """Is a notice of ``days`` days ``<comparator> <value> <unit>``? True or False only where it holds (or fails) for every length
    the unit may have ('a month' is 28 to 31 days): else None (the user answers)."""
    sign, value, unit = threshold.get("comparator"), threshold.get("value"), threshold.get("unit")
    if sign not in {">", ">=", "<", "<=", "="} or value is None or unit not in DAYS:
        return None
    low, high = value * DAYS[unit][0], value * DAYS[unit][1]
    if sign == ">":
        return True if days > high else False if days <= low else None
    if sign == ">=":
        return True if days >= high else False if days < low else None
    if sign == "<":
        return True if days < low else False if days >= high else None
    if sign == "<=":
        return True if days <= low else False if days > high else None
    return True if low == high == days else False if days < low or days > high else None


# a label that is only a name, an email or a phone, however it is written ('Full name', 'E-mail address', 'Teléfono móvil')
BARE_PERSONAL = re.compile(r"(?:(?:full|first|last|given|family|legal|preferred|middle) )?(?:name|surname|nombre|apellidos?|nombre completo)|"
                           r"e ?mail(?: address)?|correo(?: electronico)?|(?:mobile |telephone |cell )?(?:phone|telephone|mobile)(?: number)?|"
                           r"telefono(?: movil)?|movil|celular|nombre y apellidos?")


def bare_text(question) -> str:
    """The label alone: normalised, without a required / optional marker."""
    return re.sub(r"\s+(?:required|optional|obligatorio|opcional)$", "", normalize(re.sub(r"\([^)]*\)", " ", str(question or "")[:200]))).strip()


def entry_of(field) -> str:
    """The heading of the block a control stands in when that heading NAMES an entry of a history or another party ('Job 1', 'Position',
    'Employer', 'Education', 'Awards', 'Certifications', 'Trabajo 1', 'Referee', 'Emergency contact'...), else ''. A heading the engine does not know is no evidence of
    anything: 'Apply for this job', 'My Information', 'Datos personales', the title of the job or the name of the company, no heading
    at all. (The page's own title has been taken out of ``field`` already: ``semantics.without_page_heading``.)"""
    heading = " ".join(str(field.get(key) or "")[:200] for key in ("section_label", "section")).strip()
    return heading if heading and (entry_heading(normalize(heading)) or party_in(normalize(heading))) else ""


def val(record, key, default=None):
    return record.get("fields", {}).get(key, {}).get("value", default)


class CandidateEngine:
    def __init__(self, db, *, as_of=None, settings=None, answer_llm=None, allow_extractive_narratives=False):
        self.db, self.as_of, self.settings = db, as_of, settings or {}
        self.knowledge = Knowledge(db, as_of)
        self.semantic_cache = {}
        self.answer_llm = answer_llm
        self.allow_extractive_narratives = allow_extractive_narratives

    @property
    def enabled(self):
        return bool(self.knowledge.entities)

    def reload(self):
        self.knowledge = Knowledge(self.db, self.as_of)

    def confirmed_fallback(self, question, result, facts, policies):
        """Use deliberate scoped answers only when structured evidence is absent.

        An edited/cleared/conflicting typed value must never revive an older flat
        value. Nor may question memory bypass a known form-context contradiction.
        """
        if result["answer"] is not None or result["leave_blank"]:
            return result
        reason = result.get("root_cause") or ""
        if not (reason.startswith("MISSING_") or reason == "EXPLICIT_CONFIRMATION_REQUIRED"):
            return result
        semantic = result.get("semantic", {})
        intent = result["canonical_intent"]
        # Legacy question-memory does not encode school level or legal
        # jurisdiction. It cannot override these precise missing concepts.
        if (semantic.get("education_level") in {"primary", "secondary"} and intent in {"EDUCATION", "DEGREE", "GRADUATION"}) or intent in {"WORK_AUTHORIZATION", "SPONSORSHIP", "VISA", "ROLE_CONFIRMATION", "STUDY_YEAR"} or (intent == "LOCATION" and semantic.get("qualifier") == "future"):
            return result
        if intent == "HISTORY_ENTRY":
            # The page says this control belongs to an ENTRY of a history ('Start date' under 'Job 1', 'Location' under 'Referee', a block the
            # form repeats): no Profile fact is its answer, whatever its name says ('location', 'availability'), and an answer saved
            # for the bare label would be the same for every block of the page.
            return result
        if intent == "OTHER_PARTY":
            # Only an answer the user saved for THIS question, whose own words name the other party ('Emergency contact
            # phone number'). Never the Profile's aliases ('Name' is the applicant's full name there) and never an answer
            # saved for a question whose heading alone said whose details these were.
            wanted = normalize(question)
            own = [f for f in facts if f.get("profile_explicit") and str(f.get("source", "")).startswith("browser_answer:")
                   and normalize(f.get("key", "")) == wanted] if semantic.get("party_in_question") else []
            from ..automation.answers import resolve_answer as saved_answer

            confirmed = saved_answer(question, own, policies={}) if own else None
            return ({**confirmed, "question": question, "canonical_intent": intent}
                    if confirmed and confirmed["answer"] is not None and confirmed["verified"] else result)
        concept = result.get("disclosure_concept") or (
            semantic.get("attribute") if intent in {"IDENTITY", "CONTACT", "ADDRESS", "LOCATION"} else intent.lower()
        )
        if any(
            f["concept"] == concept and self.knowledge.entities[f["entity_id"]]["kind"] == "person"
            for f in self.knowledge.rows
        ):
            return result
        from ..automation.answers import resolve_answer

        explicit_disclosure = result.get("explicit_disclosure")
        memory_question = result.get("disclosure_memory_key") or question
        confirmed = resolve_answer(
            memory_question, [f for f in facts if f.get("profile_explicit")], policies={} if explicit_disclosure else policies
        )
        return (
            {**confirmed, "question": question, "canonical_intent": intent}
            if confirmed["answer"] is not None
            and confirmed["verified"]
            and (not explicit_disclosure or confirmed.get("fact_ids"))
            else result
        )

    def unknown(self, question, semantic, reason="MISSING_FACT"):
        return {
            "question": question,
            "kind": classify_question(question),
            "canonical_intent": semantic["intent"],
            "semantic": semantic,
            "answer": None,
            "form_value": None,
            "answer_type": semantic["output"],
            "requested_format": semantic.get("requested_format") or infer_format(question, {}),
            "confidence": 0,
            "confidence_level": "UNKNOWN",
            "fact_ids": [],
            "facts_used": [],
            "rules_used": [],
            "verified": False,
            "inferred": False,
            "leave_blank": False,
            "requires_review": True,
            "requires_confirmation": True,
            "reason": reason,
            "reasoning_summary": reason,
            "root_cause": reason,
            "sensitivity": "personal"
            if semantic["intent"] in {"VISA", "SPONSORSHIP", "WORK_AUTHORIZATION", "DEMOGRAPHIC"}
            else "professional",
        }

    def _history_entry(self, question, s, field):
        """A field inside ONE entry of a work or education history ('Work Experience 2' / 'Start date'): answered from that
        entry's record, or not at all. Never from the candidate's availability or current city (the entry is about a job that
        started and ended elsewhere and earlier), and never one record's answer for every block of the page."""
        history, attribute, intent = s["history"], s.get("entry_attribute"), s["intent"]
        if attribute is None and intent not in HISTORY_BLOCKED_INTENTS:
            return None  # not about the entry itself (a narrative, a skill, a consent...): read as any other question
        kind, ordinal, k = history["kind"], history["ordinal"], self.knowledge
        if kind == "education" and attribute in {"school", "start", "end"}:
            return None  # the education branch answers these from the record of the entry (``education_entry``)
        out = self.unknown(question, s, "MISSING_HISTORY_ENTRY")
        # a fact of ONE entry of a history is neither a narrative to draft nor a fact of the applicant's Profile
        out["canonical_intent"] = "HISTORY_ENTRY"

        def refuse(detail):
            return {**out, "reason": detail, "explanation": detail}

        whose = ("a work" if kind == "experience" else "an education" if kind == "education" else "a") + " history entry"
        if kind != "experience" or ordinal is None or attribute is None:
            return refuse(f"This field belongs to {whose}, not to your own current details; Meridian cannot tell which entry")
        jobs = sorted(k.records("experience"), key=lambda r: (val(r, "ongoing") is True, val(r, "end_date") or "",
                                                               val(r, "start_date") or ""), reverse=True)
        if ordinal > len(jobs):
            return refuse(f"This is work entry {ordinal}, and your profile holds {len(jobs)}")
        job = jobs[ordinal - 1]
        used = []

        def read_job(key):
            fact = job["fields"].get(key)
            if fact:
                used.append(fact)
                return fact["value"]
            return None

        if attribute not in {"title", "company", "location", "current", "start", "end"}:
            return refuse(f"Your profile does not hold this detail ({attribute}) of work entry {ordinal}")
        if attribute == "title":
            answer = read_job("role")
        elif attribute == "company":
            answer, _ = job["name"], read_job("role")
        elif attribute == "location":
            answer = read_job("location")
        elif attribute == "current":
            ongoing = read_job("ongoing")
            answer = "Yes" if ongoing is True else "No" if read_job("end_date") else None
        else:
            raw = read_job("start_date" if attribute == "start" else "end_date")
            if attribute == "end" and read_job("ongoing") is True:
                raw = None
            answer, error = format_date(raw, question, field) if raw else (None, None)
            if error:
                return refuse("This date cannot be written the way the field asks (" + error.replace("_", " ").lower() + ")")
        if answer in (None, ""):
            return refuse(f"Your profile does not hold this detail of work entry {ordinal}")
        return self._finish_evidence_answer(out, answer, used, [f"work_entry_{ordinal}:{attribute}"], s, field)

    def _not_plain(self, question, s, reading):
        """The question is not a PLAIN one of its family (``plain.py``): nothing is answered from a fact, and the user is told
        which words of it nothing accounted for (root cause ``PLAIN_QUESTION_REQUIRED``)."""
        out = self.unknown(question, s, "PLAIN_QUESTION_REQUIRED")
        detail = reading["reason"]
        out.update(reason=detail, explanation=detail, reasoning_summary=detail,
                   missing_fact={"concept": "plain_question", "family": reading["family"], "residue": reading["residue"]})
        if s["intent"] in LEGAL_INTENTS:
            out["sensitivity"] = "immigration"
        return out

    @staticmethod
    def _plain_blocked(s, reading, question, field, model_read=False) -> bool:
        """Does the plain-question gate refuse this question? Not where a more precise reading of the same kind refuses it with
        its own reason (a restriction, a contact variant, a wish, a blocker of a degree): those keep their reason."""
        if not reading["gated"] or reading["ok"]:
            return False
        intent = s["intent"]
        if intent == "DEMOGRAPHIC":
            return False  # a personal attribute is read where the confirmed value would answer it (the disclosure policy), not here
        if model_read and intent in plain.LINKS:
            return False  # a link an unusual wording was read as by the local model ('where would a recruiter look you up?'): flagged for review
        if intent in LEGAL_INTENTS:
            return (s.get("legal") or {}).get("kind", "plain") == "plain"
        if intent == "CONTACT" and s.get("contact_variant"):
            return False
        if intent == "LOCATION" and s.get("qualifier") in {"preferred", "future"}:
            return False
        if intent in {"EDUCATION", "DEGREE"} and s.get("boolean") and academic.facets(question)["blockers"]:
            return False
        return True

    @staticmethod
    def _legal_problem(intent, s, reading, place, note, held, records):
        """(root cause, explanation) when a work-authorisation, sponsorship or visa question cannot be answered from a
        boolean fact: its predicate is not the plain one (a restriction, an expiry, a history, an explanation...), or it
        names no single place the confirmed facts are about. None when it can."""
        kind = reading.get("kind", "plain")
        if kind != "plain":
            return ("MISSING_" + intent + "_PREDICATE:" + kind,
                    reading.get("detail") or "This is not a plain yes or no about your right to work")
        base, _, what = note.partition(":")
        holds = " (your confirmed answers are for " + ", ".join(sorted(set(held))) + ")" if held else ""
        if base == "several":
            return "MISSING_TARGET_WORK_COUNTRY", f"This asks about several places ({what}); your profile answers one place at a time{holds}"
        if base in {"unknown", "ambiguous"}:
            return ("MISSING_TARGET_WORK_COUNTRY",
                    f"This asks about {what}, which Meridian cannot match to a country of your profile{holds}")
        if note == "or" and (s.get("negated_legal_predicate") or intent == "VISA"):
            return ("MISSING_TARGET_WORK_COUNTRY",
                    "This names several places and asks the opposite of having a right; only you can answer it")
        return None

    def answer_question(self, question, form_context=None, job_context=None, *, semantic=None):
        question = plain.label_core(question)  # the label as a person reads it: no instruction line ('Please answer:'), no hidden character
        if isinstance(question, str) and (len(question) > MAX_QUESTION or "  " in question or "\n" in question or "\t" in question):
            question = bounded(question, 6000)  # one space for any whitespace, and bounded: no pattern runs on a hostile label
        result = self._answer_question(question, form_context, job_context, semantic=semantic)
        if INSTRUCTION_IN_LABEL.search(str(question or "")) and (
                result.get("answer") is not None or str(result.get("root_cause") or "").startswith(("MISSING_", "MULTI_INTENT", "PLAIN_QUESTION"))):
            detail = "The question contains instructions written to the assistant; they are never followed"
            return {**self.unknown(question, result.get("semantic") or interpret(question, form_context or {}),
                                   "UNTRUSTED_INSTRUCTION_IN_QUESTION"),
                    "reason": detail, "explanation": detail, "reasoning_summary": detail}
        return result

    def _answer_question(self, question, form_context=None, job_context=None, *, semantic=None):
        field, job = form_context or {}, dict(job_context or {})
        field = without_page_heading(field, job)  # the title of the page ('Apply for this job', the job's own title) is no section heading
        s = semantic or interpret(question, field)
        s = {**s, "requested_format": infer_format(question, field)}
        # The place a work-authorisation, sponsorship, visa or salary answer is true for: the one the question names, else the
        # vacancy's. Every other kind of question reads only the vacancy's country.
        # (the field's own hint counts as the question: 'Are you authorised to work?' + 'in the United States' is about the US)
        asked_place = legal.focus_sentence(question) + " " + str(field.get("description") or "")[:600]
        place, place_note = jurisdiction_of(job, asked_place) if s["intent"] in JURISDICTIONAL else (vacancy_country(job), "")
        country = None if place and jurisdiction.is_region(place) else place
        if country:
            job["country"] = country
        policy = self.knowledge.get_policy("personal_disclosures", job)
        disclosure = disclosure_resolution(question, field, {"personal_disclosures": policy["value"]} if policy else {})
        if disclosure:
            return {
                **self.unknown(question, s),
                **disclosure,
                "confidence_level": "POLICY",
                "requires_confirmation": False,
                "rules_used": ["policy:" + policy["id"] + ":" + str(policy["revision"])],
                "reasoning_summary": disclosure["reason"],
            }
        if (
            policy
            and disclosure_action(policy["value"]) == "use_confirmed"
            and personal_disclosure_kind(question, field)
        ):
            concept = disclosure_profile_key(question, field)
            semantic = {**s, "intent": "DEMOGRAPHIC", "attribute": concept or "disclosure_detail"}
            out = self.unknown(question, semantic, "MISSING_CONFIRMED_DISCLOSURE")
            out.update(disclosure_concept=concept, explicit_disclosure=True, sensitivity="personal",
                       disclosure_memory_key=disclosure_memory_key(question, field))
            people = self.knowledge.records("person")
            fact = people[0]["fields"].get(concept) if len(people) == 1 and concept else None
            if not fact:
                return out
            # The confirmed value answers a PLAIN question about that one attribute, nothing more: not a question about another
            # person, a negation, a second attribute, a follow-up, a sentence that merely mentions it (plain.py)
            reading = plain.read(question, field, "DEMOGRAPHIC")
            if not reading["ok"] and s.get("carried_from"):
                # a label that says nothing ('Please select') means what the text around it says: THAT must be the plain question
                reading = next((r for r in (plain.read(text, field, "DEMOGRAPHIC") for text in s["carried_from"]) if r["ok"]), reading)
            if not reading["ok"]:
                return {**self._not_plain(question, semantic, reading), "disclosure_concept": concept, "sensitivity": "personal"}
            value, error = adapt(fact["value"], semantic, field)
            rules = [
                "explicit_personal_disclosure:" + str(concept),
                "policy:" + policy["id"] + ":" + str(policy["revision"]),
            ]
            return {
                **out,
                "answer": value,
                "form_value": value,
                "fact_ids": [fact["id"]],
                "facts_used": [fact["id"]],
                "rules_used": rules,
                "verified": not error,
                "confidence": 1 if not error else 0,
                "confidence_level": "EXACT" if not error else "UNKNOWN",
                "requires_review": bool(error),
                "requires_confirmation": bool(error),
                "reason": error or "Your explicitly confirmed personal-disclosure answer",
                "reasoning_summary": error
                or "Uses only your confirmed "
                + str(concept).replace("_", " ")
                + "; no related personal attributes are inferred.",
                "root_cause": error,
            }
        if semantic is None and s.get("history"):
            entry = self._history_entry(question, s, field)
            if entry is not None:
                return entry
        if (semantic is None and not s.get("history") and s["intent"] != "HISTORY_ENTRY"
                and (s.get("entry_attribute") or s["intent"] == "ADDRESS")):
            heading = entry_of(field)
            # 'Address' / 'Postcode' / 'Zip code' under 'Job 1' (an entry heading that is no 'Work experience': ``history_block`` does not
            # read it) are the employer's, not the applicant's home: a heading that names an entry refuses them as it refuses 'Location'.
            # A block the form merely repeats is evidence only for the labels of an entry (a home and a mailing address repeat too).
            if heading or (field.get("_repeated_block") and s.get("entry_attribute")):
                # 'Start date' / 'Location' / 'City' / 'Title' / 'Company' say nothing of whose they are: they are the applicant's own
                # availability or city unless the page gives POSITIVE evidence that they belong to an entry: a heading that names one
                # ('Job 1', 'Studies', 'Awards') or a block the form repeats. An unknown heading is no such evidence.
                why = ("This label stands in a block the form repeats" if field.get("_repeated_block") and not heading
                       else f"This label stands under the heading '{heading}', which names an entry of a history, not your own details")
                detail = why + "; Meridian cannot tell which entry it belongs to, and your availability or current city is not its answer"
                return {**self.unknown(question, {**s, "intent": "HISTORY_ENTRY"}, "MISSING_HISTORY_ENTRY"), "reason": detail,
                        "explanation": detail, "canonical_intent": "HISTORY_ENTRY"}
        if semantic is None and s["intent"] in {"IDENTITY", "CONTACT"} and not s.get("signature") and BARE_PERSONAL.fullmatch(bare_text(question)):
            heading = entry_of(field)
            if heading:
                # 'Name' / 'Email' / 'Phone' say nothing of whose they are: they are the applicant's own unless the page gives POSITIVE
                # evidence that they are somebody else's (a referee, an emergency contact: ``semantics.interpret`` reads those) or
                # that they belong to an entry ('Awards', 'Studies', 'Job 1'): the award's name, the employer's phone. An unknown
                # heading is no such evidence.
                detail = (f"This label stands under the heading '{heading}', which names an entry of a history; Meridian cannot tell "
                          "whose name, email or phone it asks for")
                return {**self.unknown(question, {**s, "intent": "OTHER_PARTY"}, "MISSING_OTHER_PARTY_DETAILS"), "reason": detail,
                        "explanation": detail, "canonical_intent": "OTHER_PARTY"}
        # Compose independently requested concepts; conjunctions inside degree
        # names or project descriptions are not treated as separate questions.
        if (
            semantic is None
            and not (s['intent'] == 'DEGREE' and s['attribute'] in {'start_date','end_date','study_interval'})
            and not field.get("options")
            and field.get("type") not in {"number", "date", "month", "checkbox", "radio"}
        ):
            parts = re.split(r"\s+(?:and also|and|;)\s+", question, flags=re.I)
            parsed = [interpret(part, field) for part in parts]
            signatures = {(p["intent"], p["attribute"], p["selector"]) for p in parsed}
            cohesive_narrative = s["intent"] in NARRATIVE_INTENTS and all(
                p["intent"] in NARRATIVE_INTENTS for p in parsed
            )
            if (
                len(parts) > 1
                and len(signatures) > 1
                and all(p["intent"] != "OTHER" for p in parsed)
                and not cohesive_narrative
                # one yes/no question has ONE answer: 'fluent in English and Spanish?' is not 'Yes; Native', and 'based in London
                # and available in September?' is not 'Yes; 28 September 2027' (two predicates are never one answer)
                and not s["boolean"]
            ):
                components = [self._answer_question(part, field, job, semantic=p) for part, p in zip(parts, parsed)]
                base = self.unknown(question, parsed[0], "MULTI_INTENT_REQUIRES_MORE_EVIDENCE")
                base["canonical_intents"] = [p["intent"] for p in parsed]
                base["components"] = components
                if all(c["answer"] is not None for c in components):
                    text = "; ".join(dict.fromkeys(c["answer"] for c in components))
                    if not field.get("max_length") or len(text) <= field["max_length"]:
                        base.update(
                            answer=text,
                            form_value=text,
                            confidence=0.9,
                            confidence_level="GENERATED"
                            if any(c["confidence_level"] == "GENERATED" for c in components)
                            else "DERIVED_HIGH",
                            inferred=True,
                            root_cause=None,
                            fact_ids=list(dict.fromkeys(f for c in components for f in c["fact_ids"])),
                            facts_used=list(dict.fromkeys(f for c in components for f in c["facts_used"])),
                            rules_used=["compose_independent_intents"],
                            reason="Each component resolved independently from evidence",
                        )
                return base
        reading = plain.read(question, field, s["intent"], signature=bool(s.get("signature")))
        if not reading["ok"] and s.get("carried_from"):
            # a label that says nothing ('Answer', 'Further details') means what the text around it says: THAT is the question the
            # gate reads, and it must be a plain one
            reading = next((r for r in (plain.read(text, field, s["intent"]) for text in s["carried_from"]) if r["ok"]), reading)
        if self._plain_blocked(s, reading, question, field, model_read=semantic is not None):
            return self._not_plain(question, s, reading)
        if s["intent"] in {"EDUCATION", "DEGREE"} and s["boolean"] and reading["ok"] and reading["template"].startswith("grad_"):
            # 'Will you have completed your degree by 2027?' compares the date the degree ends with the date asked, whatever
            # word it uses for the degree: it is the graduation question (a degree HELD or in progress says nothing of when)
            s = {**s, "intent": "GRADUATION"}
        out = self.unknown(question, s)
        out["requested_format"] = infer_format(question, field)
        k = self.knowledge
        used = []
        rules = []
        explanations = []
        confidence = "EXACT"

        def read(record, key, default=None):
            f = record.get("fields", {}).get(key)
            if f:
                used.append(f)
                return f["value"]
            return default

        def person(key):
            records = k.records("person")
            return read(records[0], key) if len(records) == 1 else None

        def use_policy(subject):
            p = k.get_policy(subject, job)
            if p:
                rules.append("policy:" + p["id"] + ":" + str(p["revision"]))
                explanations.append("Configured policy: " + subject)
                return p["value"]
            return None

        def education(selector):
            def record_level(record):
                return education_level(val(record, "education_level", "")) or education_level(val(record, "level", ""))

            rows = k.records("education")
            if selector in {"primary", "secondary", "bachelor", "master", "doctorate"}:
                rows = [e for e in rows if record_level(e) == selector]
            elif selector == "latest":
                rows = [e for e in rows if val(e, "start_date") and val(e, "start_date")[:7] <= k.today.isoformat()[:7]]
            elif selector == "completed":
                rows = [e for e in rows if val(e, "status") == "completed"]
            else:
                rows = [
                    e
                    for e in rows
                    if val(e, "start_date")
                    and val(e, "end_date")
                    and val(e, "start_date")[:7] <= k.today.isoformat()[:7] <= val(e, "end_date")[:7]
                ]
            # Completion and level are independent constraints. A completed
            # master's question cannot use an ongoing master's record.
            if s.get("education_level") and selector == "completed":
                rows = [e for e in rows if record_level(e) == s["education_level"]]
            rows.sort(key=lambda e: (val(e, "rank", 0), val(e, "end_date", "")), reverse=True)
            if not rows:
                return None
            e = rows[0]
            for key in ("status", "start_date", "end_date", "rank", "level", "education_level"):
                read(e, key)
            return e

        def education_entry(ordinal):
            """The ordinal-th qualification of the entries of an education history, the most advanced first (the order
            ``education`` itself prefers): 'Education 1' and 'Education 2' are two records, never one answer twice."""
            rows = sorted(k.records("education"), key=lambda e: (val(e, "rank", 0), val(e, "end_date", "")), reverse=True)
            if not 1 <= ordinal <= len(rows):
                return None
            e = rows[ordinal - 1]
            for key in ("status", "start_date", "end_date", "rank", "level", "education_level"):
                read(e, key)
            rules.append(f"education_entry_{ordinal}")
            return e

        block = s.get("history") or {}
        entry_ordinal = block.get("ordinal") if block.get("kind") == "education" else None
        intent, text = s["intent"], s["text"]
        employer_request = (
            intent == "WORK_EXPERIENCE"
            and re.search(r"employer|company|empleador|empresa|compania", text)
            and not re.search(r"describe|experience|background|responsibilities|achiev|learn|experiencia|trayectoria|responsabilidades|logros|aprend", text)
        )
        answer = None
        if intent == "CONTACT" and s.get("contact_variant"):
            detail = ("This asks for a different kind of phone or email (" + s["contact_variant"] + "); your profile holds "
                      "one phone number and one email address")
            out.update(reason=detail, explanation=detail, root_cause="MISSING_FACT",
                       missing_fact={"concept": "contact_variant", "variant": s["contact_variant"]})
        elif intent in {"IDENTITY", "CONTACT", "ADDRESS", "NATIONALITY", "LINKEDIN", "GITHUB", "PORTFOLIO"}:
            key = s["attribute"] if intent in {"IDENTITY", "CONTACT", "ADDRESS"} else intent.lower()
            if key == "age":
                birth = person("date_of_birth")
                if birth:
                    b = date.fromisoformat(birth)
                    answer = k.today.year - b.year - ((k.today.month, k.today.day) < (b.month, b.day))
                    confidence = "DERIVED_HIGH"
                    rules.append("age_on_date")
            else:
                if intent == "NATIONALITY" and "country" in s["text"]:
                    key = "nationality_country"
                answer = person(key)
                if key == "phone" and answer is not None and not (field.get("phone_country_code") or field.get("phone_national_only")) \
                        and NATIONAL_NUMBER.search(fold(" ".join(str(x or "") for x in (
                            question, field.get("placeholder"), field.get("description"), field.get("label"))))):
                    # The number is held with its country code; where the code ends is not known here, so the national
                    # number is never cut out of it by guessing. (A widget with its own dial-code menu reports it.)
                    detail = "This asks for the number without its country code; Meridian holds the international number"
                    out.update(reason=detail, explanation=detail, root_cause="ANSWER_FORMAT_ERROR", candidate_value=answer)
                    answer = None
                elif key == "phone" and isinstance(answer, str) and DIGITS_ONLY.search(fold(" ".join(str(x or "") for x in (
                        question, field.get("placeholder"), field.get("description"), field.get("label"))))):
                    answer = re.sub(r"\D", "", answer)  # 'digits only' means no '+' either: the same number, written as digits
                    rules.append("phone_digits_only")
        elif intent == "LOCATION":
            if s["qualifier"] == "preferred":
                # Where the candidate LIVES is not where they would LIKE to work: only a stated preference answers it.
                wish = use_policy("preferred_work_location")
                if wish is None:
                    detail = "This asks where you would like to work; your profile holds where you live, not a preference"
                    out.update(reason=detail, explanation=detail, root_cause="MISSING_WORK_LOCATION_PREFERENCE",
                               missing_fact={"concept": "preferred_work_location"})
                else:
                    answer, confidence = wish, "POLICY"
            elif s["qualifier"] == "future":
                plan = k.get_policy("future_residence", job)
                if not plan or not {"id", "job_id", "application_id"}.intersection(plan["scope"]):
                    out.update(reason="MISSING_VERIFIED_RESIDENCE_PLAN", root_cause="MISSING_VERIFIED_RESIDENCE_PLAN",
                               missing_fact={"concept": "future_residence", "job_id": job.get("job_id", job.get("id"))})
                else:
                    answer = use_policy("future_residence")
                    confidence = "POLICY"
            else:
                answer = person(s["attribute"])
            if answer and s["attribute"] == "city" and field.get("role") == "combobox":
                country = person("country")
                if country:
                    answer = f"{answer}, {country}"
                    rules.append("geocoder_city_country_disambiguation")
            if s["boolean"] and s["qualifier"] != "future":
                city = person("city")
                country = person("country")
                mentioned = [x for x in (city, country) if x and normalize(x) in text]
                if country and not mentioned:
                    # 'the UK' / 'Great Britain' are the same country as 'United Kingdom' (a CITY of it names only that city)
                    mentioned = [country] if any(re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", text)
                                                 for name in jurisdiction.country_names(country)) else []
                answer = True if mentioned else None
        elif intent == "OTHER_PARTY":
            # Somebody else's name, number, address or place (an emergency contact, a referee, the employer...): this
            # profile holds the applicant's facts and none of theirs. Never answered from them, never guessed.
            detail = "This asks for the details of someone or something other than you (" + str(s.get("party") or "") + \
                "), which your profile does not hold"
            out.update(reason=detail, explanation=detail, root_cause="MISSING_OTHER_PARTY_DETAILS",
                       missing_fact={"concept": "other_party_details", "party": str(s.get("party") or "")})
        elif intent == "NAME_PRONUNCIATION":
            answer = person("name_pronunciation")
            if answer is None:
                out.update(reason="MISSING_NAME_PRONUNCIATION", root_cause="MISSING_NAME_PRONUNCIATION")
        elif intent == "STUDY_YEAR":
            e = education(s["selector"])
            if e:
                study_year = read(e, "study_year") or read(e, "year_of_study")
                if study_year is None:
                    start = read(e, "start_date")
                    if start and len(str(start)) >= 7:
                        start_date = date.fromisoformat(start + "-01" if len(start) == 7 else start)
                        # Month precision cannot establish which side of a
                        # later anniversary date a student currently occupies.
                        ambiguous_anniversary = len(start) == 7 and k.today.year > start_date.year and k.today.month == start_date.month
                        if start_date <= k.today and not ambiguous_anniversary:
                            study_year = k.today.year - start_date.year + 1 - ((k.today.month, k.today.day) < (start_date.month, start_date.day))
                            confidence = "DERIVED_HIGH"
                            rules.append("study_year_from_verified_active_study_start")
                if study_year is not None:
                    option = choice(str(study_year), field.get("options", [])) or choice(f"Year {study_year}", field.get("options", []))
                    answer = option["label"] if option else study_year if field.get("type") == "number" else f"Year {float(study_year):g}"
            if answer is None:
                out.update(reason="MISSING_CURRENT_STUDY_YEAR", root_cause="MISSING_CURRENT_STUDY_YEAR")
        elif intent == "ROLE_CONFIRMATION":
            related = field.get("_related_answers") or job.get("related_answers") or []
            preferences = [row for row in related if isinstance(row, dict) and (row.get("canonical_intent") or row.get("intent")) == "ROLE_PREFERENCE" and row.get("answer")
                           and not row.get("root_cause") and (row.get("quality", {}).get("passed") is True or row.get("verified") is True)]
            selections = list(dict.fromkeys(str(value.get("label") or value.get("value")) if isinstance(value, dict) else str(value)
                                           for row in preferences for value in (row.get("selected_roles") or row.get("selected_options") or [])))
            applied_role = job.get("application_role") or job.get("role") or job.get("title")

            def role_identity(role):
                # Seniority and graduate qualifiers are different from the
                # occupational role. Never strip e.g. forward deployed/backend.
                value = normalize(str(role or "").split(",", 1)[0])
                return re.sub(r"\b(?:new grad|graduate|junior|senior|entry level|internship|intern)\b", "", value).strip()

            if len(selections) == 1 and applied_role and role_identity(selections[0]) == role_identity(applied_role):
                answer = True
                rules.append("structured_role_preference_matches_actual_vacancy")
                out["related_question_ids"] = [row.get("question_id") for row in preferences]
                confidence = "DERIVED_HIGH"
            else:
                missing = "ROLE_PREFERENCE_MISMATCH" if selections and applied_role else "MISSING_STRUCTURED_ROLE_PREFERENCE"
                out.update(reason=missing, root_cause=missing)
        elif intent in {"GRADUATION", "DEGREE"} and s["boolean"] and re.search(r"\brecent(?:ly)?\b", text):
            e = education("completed")
            end = read(e, "end_date") if e else None
            subject = read(e, "subject") if e else None
            if end and subject:
                # A reviewable conservative interpretation, never a stored fact
                # or a claim about the employer's actual graduate eligibility.
                earliest_end = date.fromisoformat(end + "-01" if len(end) == 7 else end)
                within_year = 0 <= (k.today - earliest_end).days <= 365
                broad_stem = bool(re.search(r"\bstem\b", text))
                stem_subject = bool(
                    re.search(
                        r"computer science|computing|engineering|mathematics|statistics|physics|chemistry|biology",
                        normalize(subject),
                    )
                )
                unspecified_subject = not re.search(r"\b(?:with|in)\b.*\b(?:degree|qualification)\b", text)
                if within_year and (unspecified_subject or (broad_stem and stem_subject) or normalize(subject) in text):
                    result = self._finish_evidence_answer(
                        out,
                        "Yes",
                        used,
                        [
                            "recent_graduate:completed_within_12_months",
                            "review_assumption:employer_recency_definition_not_supplied",
                        ],
                        s,
                        field,
                    )
                    result.update(confidence_level="DERIVED_MEDIUM", confidence=0.85)
                    return result
            return self.unknown(question, s, "MISSING_RECENCY_OR_SUBJECT_EVIDENCE")
        elif intent in {"EDUCATION", "DEGREE", "MODULE", "GRADE", "GPA", "ACADEMIC_RESULT"}:
            if intent == 'DEGREE' and re.search(r'\b(?:other|additional) (?:degrees|qualifications)\b', text):
                related = field.get('_related_answers') or []
                covered = {identifier for answer in related for identifier in answer.get('fact_ids', [])}
                extras = [record for record in k.records('education') if val(record,'degree') and
                          not any(fact['id'] in covered for fact in record['fields'].values())]
                if not covered or len(extras) != 1:
                    return self.unknown(question, s, 'MISSING_ADDITIONAL_QUALIFICATION')
                record = extras[0]
                degree = read(record, 'degree')
                status = read(record, 'status')
                answer = degree + ' (in progress)' if status == 'studying' else degree
                return self._finish_evidence_answer(out, answer, used, ['additional_verified_qualification'], s, field)
            if s["boolean"] and intent in {"EDUCATION", "DEGREE"} and not field.get("_education_reference"):
                # A yes/no question about studies is 'yes' only when ONE record confirms EVERY facet it asks (level, held or
                # in progress, subject, institution); a country, an accreditation or a grade it names no record establishes.
                asked = academic.facets(question)
                level = s.get("education_level")
                found_subjects = reading["slots"].get("subjects") if reading["gated"] and reading["ok"] else None
                found_institutions = reading["slots"].get("institutions") if reading["gated"] and reading["ok"] else None
                if not asked["blockers"] and ((found_subjects and not asked["subjects"]) or (found_institutions and not asked["institutions"])):
                    # a subject or a school the grammar read and the facet reader did not: it cannot be compared with a record
                    detail = "This names a subject or an institution that Meridian cannot compare with your education records"
                    return {**self.unknown(question, s, "MISSING_EDUCATION_EVIDENCE"), "reason": detail, "explanation": detail}

                def level_of(record):
                    return education_level(val(record, "education_level", "")) or education_level(val(record, "level", ""))

                def current(record):
                    start, end = val(record, "start_date"), val(record, "end_date")
                    return val(record, "status") != "completed" and bool(start and end and start[:7] <= k.today.isoformat()[:7] <= end[:7])

                qualifying = []
                for record in k.records("education"):
                    kind = level_of(record)
                    if level and kind != level or (not level and kind in {"primary", "secondary"} and asked["status"] == "completed"):
                        continue
                    if (val(record, "status") == "completed") != (asked["status"] == "completed") and not (
                            asked["status"] == "current" and current(record)):
                        continue
                    if asked["status"] == "current" and not current(record):
                        continue
                    texts = [val(record, "subject", ""), val(record, "degree", ""), record["name"]]
                    if asked["subjects"] and not any(academic.subject_matches(subject, texts) for subject in asked["subjects"]):
                        continue
                    names = [val(record, "institution", ""), record["name"], *record["aliases"]]
                    if asked["institutions"] and not any(academic.institution_matches(name, names) for name in asked["institutions"]):
                        continue
                    qualifying.append(record)
                if asked["blockers"] or not qualifying:
                    detail = ("This asks about " + ", ".join(b.replace("_", " ") for b in asked["blockers"])
                              + ", which your education records do not establish") if asked["blockers"] else \
                        "None of your education records confirms everything this asks (its level, whether it is finished, " \
                        "its subject and institution)"
                    cause = "MISSING_COMPLETED_QUALIFICATION" if (
                        asked["status"] == "completed" and not asked["blockers"] and not asked["subjects"]
                        and not asked["institutions"] and level) else "MISSING_EDUCATION_EVIDENCE"
                    return {**self.unknown(question, s, cause), "reason": detail, "explanation": detail}
                record = max(qualifying, key=lambda r: (val(r, "rank", 0), val(r, "end_date", "")))
                for key in ("status", "level", "education_level", "subject", "degree", "institution", "start_date", "end_date"):
                    read(record, key)
                return self._finish_evidence_answer(out, "Yes", used, ["education_facets_confirmed"], s, field)
            selector = s["selector"]
            if intent in {"GRADE", "GPA", "ACADEMIC_RESULT"} and s.get("predicted"):
                # 'Expected / predicted grade' is a prediction about the degree still in progress: never the finished result of
                # another degree. Only a stored prediction answers it.
                record = education("pursued")
                kind = "classification" if re.search(r"class|honour|clasificacion", text) else "gpa" if intent == "GPA" else "average"
                answer = read(record, "predicted_" + kind) if record else None
                if answer is None:
                    detail = "This asks for a predicted or expected result; your profile holds no prediction for the degree in progress"
                    return {**self.unknown(question, s, "MISSING_PREDICTED_GRADE"), "reason": detail, "explanation": detail}
                return self._finish_evidence_answer(out, answer, used, ["stored_predicted_" + kind], s, field)
            if intent in {"GRADE", "GPA", "ACADEMIC_RESULT"} and selector == "current":
                selector = "completed"
            e = education_entry(entry_ordinal) if entry_ordinal else education(selector)
            if entry_ordinal and e is None:
                return self.unknown(question, s, "MISSING_EDUCATION_ENTRY")
            if e is None and selector in {"primary", "secondary"}:
                out.update(reason="MISSING_" + selector.upper() + "_INSTITUTION", root_cause="MISSING_" + selector.upper() + "_INSTITUTION")
            if (
                e
                and val(e, "status") != "completed"
                and re.search(r"completed|earned|awarded|obtained|\bdid\b.*\bcomplete\b", text)
                and not re.search(r"pursu|current|in progress", text)
            ):
                return self.unknown(question, s, "MISSING_COMPLETED_QUALIFICATION")
            if e and field.get("_education_reference"):
                reference = normalize(field["_education_reference"])
                if not any(normalize(name) == reference for name in [e["name"], *e["aliases"]]):
                    referenced = next(
                        (
                            r
                            for r in k.records("education")
                            if choice(
                                val(r, "institution", r["name"]),
                                [{"label": field["_education_reference"], "value": reference}],
                            )
                        ),
                        None,
                    )
                    if (
                        referenced
                        and selector == "completed"
                        and intent == "DEGREE"
                        and s["attribute"] == "degree"
                        and val(referenced, "status") == "studying"
                        and not s["boolean"]
                        and not field.get("options")
                        and (field.get("type") == "text" or field.get("tag") == "textarea")
                    ):
                        current_degree, current_institution = (
                            read(referenced, "degree"),
                            read(referenced, "institution"),
                        )
                        completed_degree, completed_institution = read(e, "degree"), read(e, "institution")
                        end = read(referenced, "end_date")
                        read(referenced, "status")
                        if all([current_degree, current_institution, completed_degree, completed_institution, end]):
                            answer = f"My {current_degree} at {current_institution} is in progress (expected completion {end}). My completed qualification is {completed_degree} at {completed_institution}."
                            return self._finish_evidence_answer(
                                out,
                                answer,
                                used,
                                ["clarify_in_progress_qualification_at_selected_institution"],
                                s,
                                field,
                            )
                    return self.unknown(question, s, "CONFLICT_WITH_SELECTED_INSTITUTION")
            if e:
                confidence = "DERIVED_HIGH"
                if not entry_ordinal:
                    rules.append("education_selection:" + selector)
                if intent == "EDUCATION":
                    answer = (
                        True if s["boolean"] and re.search(r"student|studying|enroll", text) else read(e, "institution")
                    )
                    if field.get('options') and answer and not choice(str(answer), field['options'], question=question):
                        aliases = [choice(alias, field['options'], question=question) for alias in e['aliases']]
                        matches = {option['label'] for option in aliases if option}
                        if len(matches) == 1:
                            answer = next(iter(matches))
                            rules.append('verified_institution_alias')
                elif intent == "MODULE":
                    answer = read(e, "modules")
                elif intent in {"GRADE", "ACADEMIC_RESULT"}:
                    if s['attribute'].startswith('year_'):
                        answer = read(e, s['attribute'])
                        if answer is None:
                            return self.unknown(question, s, 'MISSING_YEAR_SPECIFIC_RESULT')
                    else:
                        answer = read(e, "classification") if re.search(r"class|honour", text) else read(e, "average")
                    if (
                        answer is not None
                        and "%" in question
                        and field.get("type") != "number"
                        and not field.get("options")
                    ):
                        answer = str(answer) + "%"
                elif intent == "GPA":
                    official = read(e, "gpa")
                    if official is not None:
                        answer = official
                    elif field.get("type") == "number":
                        conversion = use_policy("gpa_conversion")
                        average = read(e, "average")
                        if conversion and average is not None:
                            matches = [r for r in conversion.get("ranges", []) if r["min"] <= average <= r["max"]]
                            if len(matches) == 1:
                                answer = matches[0]["gpa"]
                                confidence = "DERIVED_MEDIUM"
                                explanations.append("Policy conversion; not an official university GPA")
                    else:
                        grade, average = read(e, "classification"), read(e, "average")
                        if grade and average is not None:
                            answer = f"UK {grade}; {average}%. No official US GPA."
                else:
                    if s['attribute'] == 'study_interval':
                        start,end = read(e,'start_date'),read(e,'end_date')
                        if not start or not end:
                            return self.unknown(question,s,'MISSING_EDUCATION_DATE_RANGE')
                        start,error = format_date(start,question,field)
                        end,end_error = format_date(end,question,field)
                        if error or end_error:
                            return self.unknown(question,s,error or end_error)
                        return self._finish_evidence_answer(out,f'{start} – {end}',used,
                            ['verified_education_date_range'],s,field)
                    answer = read(e, s["attribute"])
                    if answer is not None and s['attribute'] in {'start_date','end_date'}:
                        answer, error = format_date(answer, question, field)
                        if error:
                            return self.unknown(question, s, error)
                    if s["boolean"]:
                        answer = True if answer else None
        elif intent in {"GRADUATION", "START_DATE", "AVAILABILITY"}:
            if intent == "GRADUATION" and s["boolean"] and academic.relation_of(question) is not None:
                # 'by 2027', 'before 2028', 'since 2020', 'in 2025 or 2026': a RELATION between the graduation date and the
                # date asked, read against the degrees it is about, never an equality with the first year in the text
                records = []
                for record in k.records("education"):
                    records.append({"level": education_level(val(record, "education_level", "")) or education_level(val(record, "level", "")),
                                    "status": val(record, "status"), "end_date": val(record, "end_date"), "_record": record})
                verdict, why_rules, relevant, why_not = academic.graduation_verdict(question, records, k.today)
                if verdict is None:
                    detail = "This compares your graduation date with a date, but " + (why_not or "it cannot be answered")
                    return {**self.unknown(question, s, "MISSING_GRADUATION_RELATION_EVIDENCE"), "reason": detail,
                            "explanation": detail}
                for degree in relevant:
                    for key in ("status", "level", "education_level", "end_date"):
                        read(degree["_record"], key)
                return self._finish_evidence_answer(out, "Yes" if verdict else "No", used, why_rules, s, field)
            if intent == "GRADUATION" and s["boolean"] and (re.search(r"\b20\d\d\b", text) or requested_date(question)):
                # a date the relation of which was not read with certainty above (see ``academic.relation_of``)
                detail = "This compares your graduation date with a date, but the date or its relation cannot be read with certainty"
                return {**self.unknown(question, s, "MISSING_GRADUATION_RELATION_EVIDENCE"), "reason": detail, "explanation": detail}
            selector = s["selector"]
            e = education(selector)
            if intent == "GRADUATION" and e is None and selector in {"secondary", "primary"}:
                missing = "MISSING_" + selector.upper() + "_GRADUATION_DATE"
                out.update(reason=missing, root_cause=missing)
            end = read(e, "end_date") if e else None
            availability = person("earliest_full_time_start_date") if intent in {"START_DATE", "AVAILABILITY"} else None
            if intent in {"START_DATE", "AVAILABILITY"} and availability in (None, "") and any(
                fact["concept"] == "earliest_full_time_start_date" and fact["source_priority"] >= 100
                and k.entities[fact["entity_id"]]["kind"] == "person" for fact in k.rows
            ):
                return self.unknown(question, s, "MISSING_CONFIRMED_AVAILABILITY")
            availability = availability or (use_policy("anticipated_start_date") if intent in {"START_DATE", "AVAILABILITY"} else None)
            if intent in {"START_DATE", "AVAILABILITY"} and not availability:
                return self.unknown(question, s, "MISSING_ANTICIPATED_START_DATE")
            raw_date = availability or end
            if raw_date:
                try:
                    raw = str(raw_date)
                    target = date.fromisoformat(raw + "-01-01" if len(raw) == 4 else raw + "-01" if len(raw) == 7 else raw)
                except ValueError:
                    return self.unknown(question, s, "INVALID_STORED_DATE")
                confidence = "EXACT" if availability else "DERIVED_HIGH"
                rules.append("explicit_candidate_availability" if availability else "education_end_date")
                requested = requested_date(question)
                if s["boolean"]:
                    if requested:
                        proposed, precision = requested
                        if precision == "month" and target.year == proposed.year and target.month == proposed.month:
                            if field.get("type") != "checkbox" and field.get("role") not in {"radio", "combobox"}:
                                displayed = target.strftime("%d %B %Y" if len(str(raw_date)) == 10 else "%B %Y")
                                answer = f"Available from {displayed}; the exact programme start must be checked."
                            else:
                                out["reason"] = "AMBIGUOUS_QUESTION"
                        else:
                            answer = target <= proposed
                elif re.search(r"how long|time until|months until|days until", text):
                    if len(str(raw_date)) != 10:
                        return self.unknown(question, s, "MISSING_EXACT_DATE")
                    days = max(0, (target - k.today).days)
                    answer = (
                        days
                        if "days" in text or field.get("type") == "number"
                        else f"{days} days, until {target.strftime('%d %B %Y')}."
                    )
                    rules.append("date_difference:" + k.today.isoformat())
                else:
                    answer, error = format_date(raw_date, question, field)
                    if error:
                        return self.unknown(question, s, error)
        elif intent == "NOTICE_PERIOD":
            notice = person("current_employment_notice_days")
            if notice is None and person("employment_contract_active") is False:
                notice = 0
                confidence = "DERIVED_HIGH"
                rules.append("no_active_employment_contract")
            if notice is not None:
                unit = None if s["boolean"] else NOTICE_UNIT.search(
                    fold(" ".join(str(x or "") for x in (question, field.get("placeholder"), field.get("description")))))
                if unit:
                    # The field names its unit: the answer is a number OF THAT UNIT. Days convert to weeks only when they
                    # make whole weeks, and never to months (a month is not a number of days): else the user answers.
                    name = next(g for g in unit.groups() if g)
                    name = {"dias": "days", "dia": "days", "semanas": "weeks", "semana": "weeks", "meses": "months",
                            "mes": "months"}.get(name, name).rstrip("s") + "s"
                    if notice == 0 or name == "days":
                        answer = int(notice) if float(notice).is_integer() else notice
                    elif name == "weeks" and float(notice) % 7 == 0:
                        answer = int(float(notice) // 7)
                    else:
                        detail = f"Your notice is {float(notice):g} days, which is not a whole number of {name}"
                        out.update(reason=detail, explanation=detail, root_cause="ANSWER_FORMAT_ERROR", candidate_value=notice)
                elif s["boolean"]:
                    # 'Do you have a notice period?' is whether there is one; 'Is it longer than 4 weeks?' is a COMPARISON of the
                    # stored days with the threshold asked (``compare_notice``), true or false only where the units leave no doubt
                    threshold = reading["slots"].get("threshold") if reading["gated"] and reading["ok"] else None
                    if threshold is None:
                        answer = bool(notice)
                    else:
                        verdict = compare_notice(float(notice), threshold)
                        if verdict is None:
                            asked = (f"{threshold['comparator']} {threshold['value']:g} {threshold['unit']}"
                                     if threshold.get("comparator") and threshold.get("value") is not None else "what it is compared with")
                            detail = f"Your notice is {float(notice):g} days; that does not settle whether it is {asked}"
                            out.update(reason=detail, explanation=detail, root_cause="MISSING_NOTICE_COMPARISON_EVIDENCE",
                                       candidate_value=notice)
                        else:
                            answer, confidence = verdict, "DERIVED_HIGH"
                            rules.append("notice_period_compared_with_the_threshold_asked")
                else:
                    answer = notice if field.get("type") == "number" else "None" if notice == 0 else f"{float(notice):g} days"
                explanations.append("Contractual notice only; academic availability is a separate constraint")
        elif intent == "LANGUAGE":
            languages = k.records("language")
            requested = s.get("requested_languages", [])
            named = [r for r in languages if normalize(r["name"]) in text or language_tools.canonical(r["name"]) in requested]
            unknown_languages = [name for name in requested if not any(language_tools.canonical(r["name"]) == name for r in languages)]
            if unknown_languages:
                return self.unknown(question, s, "MISSING_LANGUAGE_PROFICIENCY:" + ",".join(unknown_languages))
            selected = named or languages
            if s.get("attribute") == "native_language":
                selected = [r for r in languages if val(r, "native") is True or normalize(val(r, "proficiency", "")) == "native"]
            asked = language_tools.asked_class(text) if s["boolean"] and named else None
            if asked is not None:
                # 'native', 'first language', 'C2', 'fluent', 'bilingual': a LEVEL, compared with the stored one. A level is an
                # order, not a presence: only where the comparison is certain is it answered (``languages.compare``).
                verdicts = []
                for r in named:
                    read(r, "proficiency"), read(r, "native")
                    verdict, rule = language_tools.compare(val(r, "proficiency", ""), val(r, "native"), asked)
                    verdicts.append(verdict)
                    rules.append(rule + ":" + r["name"])
                answer = False if False in verdicts else True if all(v is True for v in verdicts) else None
                confidence = "DERIVED_HIGH"
                if answer is None:
                    names = ", ".join(r["name"] for r in named)
                    detail = f"This asks about your level in {names}; your profile does not say it in a way that answers it"
                    out.update(reason=detail, explanation=detail, root_cause="MISSING_LANGUAGE_LEVEL_EVIDENCE:" + names)
            else:
                parts = []
                for r in selected:
                    level = read(r, "proficiency")
                    if level:
                        parts.append(r["name"] if s["output"] == "MULTIPLE_CHOICE" or s.get("attribute") == "native_language" else level if len(named) == 1 else r["name"] + ": " + level)
                basic = [r["name"] for r in named if language_tools.level_class(val(r, "proficiency", "")) == 1] if s["boolean"] else []
                if basic:
                    # 'Do you speak Spanish?' of a person whose confirmed level is Basic: whether that is speaking it is theirs to say
                    names = ", ".join(basic)
                    detail = (f"Your confirmed level in {names} is basic; whether that counts as speaking it is for you to say, "
                              "so Meridian does not answer Yes for you")
                    out.update(reason=detail, explanation=detail, root_cause="MISSING_LANGUAGE_LEVEL_EVIDENCE:" + names)
                elif parts:
                    answer = parts if s["output"] == "MULTIPLE_CHOICE" else True if s["boolean"] and named else ", ".join(parts)
                    options = field.get("options") or []
                    if (options and len(named) == 1 and s.get("attribute") == "proficiency" and not s["boolean"]
                            and s["output"] != "MULTIPLE_CHOICE" and not choice(str(parts[0]), options, question=question)):
                        # the menu has other words for the level ('Advanced', 'C1'): one option on the ladder, or none
                        option = language_tools.translated_option(str(parts[0]), options)
                        if option:
                            answer = option["label"]  # the same word in the other language: not a guess
                            rules.append("language_level_translated")
                        else:
                            option = language_tools.option_for_level(str(parts[0]), val(named[0], "native"), options)
                            if option:
                                answer, confidence = option["label"], "DERIVED_HIGH"
                                rules.append("language_level_on_the_menus_own_ladder")
        elif intent in {"VISA", "WORK_AUTHORIZATION", "SPONSORSHIP"}:
            records = k.records("immigration")
            reading = s.get("legal") or {}
            held = [country_name(val(r, "country")) for r in records if val(r, "country")]
            problem = self._legal_problem(intent, s, reading, place, place_note, held, records)
            if problem:
                out.update(reason=problem[1], explanation=problem[1], root_cause=problem[0],
                           missing_fact={"concept": intent.lower(), "country": place, "qualifier": s["qualifier"]})
                out["sensitivity"] = "immigration"
                return out
            if place and jurisdiction.is_region(place) and any(country_name(val(r, "country")) == place for r in records):
                country = place  # a region is answered from a record of exactly that region, or not at all
                job["country"] = place
            monotone = place_note == "or"
            record = next((r for r in records if country_name(val(r, "country")) == country), None) if country else None
            if record is None and intent == "VISA" and len(records) == 1 and country is None:
                record = records[0]
            if record:
                read(record, "country")  # Preserve the jurisdiction evidence in the answer audit.
                if intent == "VISA":
                    answer = read(record, "route")
                    if re.search(r"expir|valid until", text):
                        answer = read(record, "valid_until")
                    elif answer and not_a_visa(answer) and not field.get("options") and not s["boolean"] and (
                            field.get("tag") == "textarea" or VISA_DESCRIBE.search(text)):
                        # 'I currently hold a Citizen visa' is false: a citizen, a settled or pre-settled person holds none
                        detail = f"Your profile says '{answer}', which is not a visa; Meridian writes no sentence about a visa for it"
                        out.update(reason=detail, explanation=detail, root_cause="MISSING_VISA_STATUS_SENTENCE")
                        answer = None
                    elif (
                        answer
                        and not not_a_visa(answer)  # ('What is your visa status?' of a citizen is 'Citizen': the status itself)
                        and not field.get("options")
                        and not s["boolean"]
                        and (
                            field.get("tag") == "textarea" or VISA_SENTENCE.search(text)
                        )
                    ):
                        route = str(answer)
                        visa_country = read(record, "country")
                        country_label = (
                            "the " + visa_country
                            if visa_country in {"United Kingdom", "United States"}
                            else visa_country
                        )
                        answer = (
                            f"I currently hold a {route} visa" + (f" in {country_label}" if country_label else "") + "."
                        )
                        if normalize(route) == "student":
                            current_study = education("current")
                            if current_study:
                                degree = read(current_study, "degree")
                                institution = read(current_study, "institution")
                                end = read(current_study, "end_date")
                                if degree and institution:
                                    answer += f" I am studying for an {degree} at {institution}"
                                    if end:
                                        end_date = date.fromisoformat(end + "-01" if len(end) == 7 else end)
                                        answer += f", with my course expected to finish in {end_date.strftime('%B %Y')}"
                                    answer += "."
                        rules.append("visa_status_with_verified_study_context")
                        confidence = "DERIVED_HIGH"
                elif intent == "SPONSORSHIP":
                    answer = read(record, "sponsorship_" + ("now_or_future" if s["qualifier"] == "unspecified" else s["qualifier"]))
                    if answer is None:
                        out["reason"] = out["root_cause"] = "MISSING_CURRENT_IMMIGRATION_EVIDENCE"
            if intent == "WORK_AUTHORIZATION" and country:
                facets = s.get("authorization_requirements") or [
                    s["qualifier"] if s["qualifier"] in {"permanent", "full_time", "future"} else "current"]
                facets_values = []
                for facet in facets:
                    field_key = {"permanent": "permanent_unrestricted", "unrestricted": "unrestricted_right_to_work",
                        "full_time": "full_time_right_to_work", "future": "future_right_to_work",
                        "current": "current_right_to_work", "visa_free": "visa_free_right_to_work"}.get(facet)
                    if facet == "sponsorship_free":
                        sponsor_key = "sponsorship_future" if "future" in facets else "sponsorship_current"
                        sponsor = read(record or {}, sponsor_key)
                        if sponsor is None and read(record or {}, "sponsorship_now_or_future") is False:
                            sponsor = False
                        facet_value = not sponsor if isinstance(sponsor, bool) else None
                    elif facet == "period":
                        # A duration-specific assurance cannot come from a
                        # global Boolean. Require the actual application's
                        # confirmed permission policy and jurisdiction.
                        scoped = k.get_policy("work_authorization_period", job)
                        facet_value = use_policy("work_authorization_period") if scoped and (
                            scoped["scope"].get("country") and {"id", "job_id", "application_id"}.intersection(scoped["scope"])
                        ) else None
                        if facet_value is not None:
                            confidence = "POLICY"
                        elif read(record or {}, "permanent_unrestricted") is True:
                            facet_value = True  # a permanent, unrestricted right covers any period (a time-limited one never does)
                    else:
                        facet_value = read(record or {}, field_key)
                        if facet == "unrestricted" and facet_value is None:
                            # A positive conjunction entails unrestricted; a
                            # negative conjunction cannot prove its negation.
                            if read(record or {}, "permanent_unrestricted") is True:
                                facet_value = True
                    if facet_value is None and facet not in {"sponsorship_free", "period"}:
                        scoped = k.get_policy("work_authorization_" + facet, job)
                        if scoped and scoped["scope"].get("country"):
                            facet_value = use_policy("work_authorization_" + facet)
                            confidence = "POLICY"
                    facets_values.append(facet_value if isinstance(facet_value, bool) else None)
                answer = False if False in facets_values else True if all(v is True for v in facets_values) else None
                if answer is True and facets == ['current'] and record:
                    # This classifies an already confirmed present permission.
                    # Student-route evidence alone cannot establish any work
                    # permission, expiry, hours, future or full-time eligibility.
                    route = read(record, 'route')
                    route_options = confirmed_visa_route_options(route, field.get('options', []))
                    temporary_options = []
                    if not route_options and country == 'United Kingdom' and normalize(route or '') == 'student':
                        for option in field.get('options', []):
                            label = option.get('label', '')
                            option_text = normalize(label)
                            option_semantic = interpret(label, {'type': 'radio'})
                            if (re.search(r'\bvisa\b', option_text) and re.search(r'\btemporary\b', option_text)
                                    and re.search(r'right to work|authori[sz].*work|permission.*work', option_text)
                                    and not re.search(r'\d|permanent|unrestricted|full time|future|without|until|through|during|need|require', option_text)
                                    and not set(option_semantic.get('authorization_requirements') or ['current']) - {'current'}):
                                temporary_options.append(label)
                    if len(route_options) == 1:
                        answer = route_options[0]
                        confidence = 'DERIVED_HIGH'
                        rules.append('confirmed_visa_route_and_current_work_permission')
                        explanations.append('The selected option contains the confirmed visa route and only '
                                            'the independently confirmed current work permission. It does not '
                                            'establish any additional route, expiry, hours or future eligibility.')
                    elif len(temporary_options) == 1:
                        answer = temporary_options[0]
                        confidence = 'DERIVED_HIGH'
                        rules.append('uk_student_time_limited_route_with_confirmed_current_work_permission')
                        explanations.append('Confirmed current UK work permission plus the verified Student visa route; '
                                            'route duration reference: https://www.gov.uk/student-visa. '
                                            'This does not establish expiry, working hours or full-time eligibility.')
                if answer is None:
                    out["missing_authorization_facets"] = [facet for facet, value in zip(facets, facets_values) if value is None]
                elif len(facets) > 1:
                    rules.append("conjunction_of_verified_work_authorization_facets")
            if answer is None and country and intent == "SPONSORSHIP":
                policy_subject = "sponsorship_" + ("now_or_future" if s["qualifier"] == "unspecified" else s["qualifier"])
                scoped = k.get_policy(policy_subject, job)
                # An unscoped immigration policy must never leak across countries.
                if scoped and scoped["scope"].get("country"):
                    answer = use_policy(policy_subject)
                    confidence = "POLICY"
            if monotone and answer is not True:
                # 'a right in the UK or the EU': a right in the vacancy's country says yes; its absence says nothing about
                # the other place, so it is never a 'no'
                answer = None
            if answer is None:
                missing = "MISSING_TARGET_WORK_COUNTRY" if not country else "MISSING_" + intent + "_EVIDENCE:" + country
                out.update(reason=missing, root_cause=missing, missing_fact={"concept": intent.lower(), "country": country, "qualifier": s["qualifier"]})
            elif isinstance(answer, bool) and s.get("negated_legal_predicate"):
                answer = not answer
                rules.append("negated_legal_predicate")
            elif monotone:
                rules.append("true_in_one_of_the_named_places")
            out["sensitivity"] = "immigration"
        elif intent == "DEMOGRAPHIC":
            p = use_policy("optional_demographics")
            if p == "decline_or_blank":
                opt = choice("Prefer not to say", field.get("options", []))
                if opt:
                    answer = opt["label"]
                elif not field.get("required"):
                    out["leave_blank"] = True
                    out["reason"] = "Configured policy: optional disclosure left blank"
                confidence = "POLICY"
        elif intent == "REASONABLE_ADJUSTMENTS":
            p = use_policy("adjustments_request")
            if p == "none_requested" and not field.get("required"):
                answer = (
                    False
                    if s["boolean"] and (field.get("options") or field.get("role") in {"radio", "checkbox"})
                    else "No adjustments requested"
                )
                confidence = "POLICY"
        elif intent == "CONSENT":
            answer = use_policy("consent_" + s["attribute"])
            if answer is None:
                p = use_policy("candidate_consent")
                answer = p.get(s["attribute"]) if isinstance(p, dict) else None
            if answer is None:
                out.update(reason="EXPLICIT_CONFIRMATION_REQUIRED", root_cause="EXPLICIT_CONFIRMATION_REQUIRED",
                           missing_fact={"concept": "consent_" + s["attribute"]})
            else:
                confidence = "POLICY"
        elif intent == "OFFER_DEADLINE":
            deadlines = person("upcoming_offer_deadlines")
            anticipated = person("anticipated_offer_deadline")
            p = use_policy("employment_offer_deadlines")
            if s["boolean"]:
                if re.search(r"anticipat|expect", text):
                    answer = True if deadlines or anticipated is True else False if deadlines is not None and anticipated is False else p if isinstance(p, bool) else None
                else:
                    answer = bool(deadlines) if deadlines is not None else p if isinstance(p, bool) else None
            elif deadlines:
                answer = deadlines
            elif p is not None and not isinstance(p, bool):
                answer = p
            if answer is None:
                out.update(reason="MISSING_EMPLOYMENT_OFFER_DEADLINES", root_cause="MISSING_EMPLOYMENT_OFFER_DEADLINES")
        elif intent in {
            "GDPR",
            "CRIMINAL_HISTORY",
            "SECURITY_CLEARANCE",
            "BACKGROUND_CHECK",
            "CONFLICT_OF_INTEREST",
            "ROLE_CONFIRMATION",
        }:
            out["root_cause"] = out["reason"] = "EXPLICIT_CONFIRMATION_REQUIRED"
        elif intent == "SALARY":
            p = use_policy("salary_" + s["qualifier"])
            if p is not None:
                figure = p.get("target") if isinstance(p, dict) else p
                # The saved figure is ONE annual amount in ONE currency: a field that asks for another currency, period,
                # unit, a range, a whole package or a net figure is not answered (never converted, never guessed).
                problem = salary_mismatch(question, field, p) if figure is not None else None
                if problem:
                    detail = salary_explanation(problem, p)
                    out.update(reason=detail, explanation=detail, root_cause="ANSWER_FORMAT_ERROR", candidate_value=figure)
                else:
                    answer = figure
                    confidence = "POLICY"
        elif intent in {"RELOCATION", "OFFICE_POLICY", "REMOTE_WORK", "TRAVEL"}:
            city = person("city")
            if intent == "RELOCATION" and city and normalize(city) in text:
                answer = True if s["boolean"] and field.get("tag") != "textarea" else "Already based in " + city
            else:
                p = use_policy(intent.lower())
                if p is not None:
                    answer = p
                    confidence = "POLICY"
        elif intent == "SOURCE_OF_APPLICATION":
            provenance = job.get("discovery_provenance") or {}
            if not isinstance(provenance, dict):
                provenance = {}
            answer = provenance.get("source") or provenance.get("source_name") or job.get("source")
            if answer and answer not in {"manual", "unknown"}:
                rules.append("job_discovery_provenance:" + str(job.get("id", "")))
                confidence = "DERIVED_HIGH"
                options = field.get("options", [])
                if options and not choice(answer, options):
                    # A factual broad category can represent a named board. The
                    # presence of a referral option never establishes a referral.
                    source = normalize(answer)
                    categories = (
                        (r"linkedin", "LinkedIn"), (r"indeed", "Indeed"), (r"github", "GitHub"),
                        (r"google|search engine", "Search engine"), (r"university|careers service", "University careers service"),
                        (r"company careers|company website", "Company website"), (r"recruiter", "Recruiter"),
                    )
                    broad = next((label for pattern, label in categories if re.search(pattern, source)), None)
                    match = choice(broad, options) if broad else None
                    match = match or choice("Other", options)
                    if match:
                        out["discovery_source"] = answer
                        answer = match["label"]
                        rules.append("accurate_discovery_source_category")
            else:
                answer = None
        elif intent == "EMPLOYEE_REFERRAL":
            referral = job.get("referrer")
            answer = referral or (False if job.get("referral_confirmed_absent") else None)
            if answer is not None:
                rules.append("application_referral_provenance")
        elif intent == "PREVIOUS_EMPLOYMENT":
            employer = normalize(job.get("company", ""))
            for r in k.records("experience"):
                aliases = [normalize(r["name"]), *[normalize(x) for x in r["aliases"]]]
                if employer and employer in aliases:
                    answer = True
                    read(r, "role")
                    rules.append("employment_entity_match")
                    break
            if answer is None and person("employment_history_complete") is True:
                answer = False
        elif employer_request:
            rows = k.records("experience")
            current = s["selector"] == "current"
            if current:
                explicit = person("current_company")
                if explicit is not None:
                    if explicit == "":
                        if not field.get("required"):
                            return {**out, "leave_blank": True, "root_cause": None,
                                    "reason": "Explicit candidate profile leaves optional current employer blank",
                                    "fact_ids": [fact["id"] for fact in used], "facts_used": [fact["id"] for fact in used],
                                    "confidence_level": "EXACT", "confidence": 1,
                                    "verified": True,
                                    "requires_review": False, "requires_confirmation": False}
                        return {**out, "root_cause": "MISSING_CURRENT_EMPLOYER", "reason": "MISSING_CURRENT_EMPLOYER"}
                    return self._finish_evidence_answer(out, explicit, used, ["explicit_current_employer"], s, field)
            rows = [r for r in rows if (val(r, "ongoing") is True if current else val(r, "end_date") is not None)]
            rows.sort(key=lambda r: val(r, "end_date", ""), reverse=True)
            if rows:
                for key in ("role", "start_date", "end_date", "ongoing"):
                    read(rows[0], key)
                answer = rows[0]["name"]
                rules.append("employment_selection:" + s["selector"])
                confidence = "DERIVED_HIGH"
        elif intent in {
            "SKILL",
            "SKILL_DURATION",
            "PROGRAMMING_LANGUAGE",
            "FRAMEWORK",
            "CLOUD",
            "DATA",
            "MACHINE_LEARNING",
            "SOFTWARE_ENGINEERING",
            "PROJECT",
        }:
            if (
                intent == "PROJECT"
                and not s["boolean"]
                and not re.search(r"(?:largest|biggest|maximum).*data\s?set", text)
            ):
                answer = self.narrative(s, job, used, rules, out)
                if answer is not None:
                    confidence = "GENERATED"
            if re.search(r"(?:largest|biggest|maximum).*data\s?set", text):
                datasets = [
                    r
                    for kind in ("experience", "project")
                    for r in k.records(kind)
                    if val(r, "dataset_size") is not None
                ]
                if datasets:
                    largest = max(datasets, key=lambda r: val(r, "dataset_size"))
                    answer = read(largest, "dataset_size")
                    rules.append("largest_verified_dataset")
                    confidence = "DERIVED_HIGH"
            skill = self.find_skill(text) if intent != "PROJECT" or s["boolean"] or re.search(r"(?:largest|biggest|maximum).*data\s?set", text) else None
            if skill and answer is None:
                if intent == "PROJECT" and s["boolean"]:
                    project_evidence = [edge for edge in k.links if edge["subject_id"] == skill["id"] and edge["predicate"] == "used_in"
                                        and k.entities.get(edge["object_id"], {}).get("kind") == "project"
                                        and any(fact["id"] in edge["fact_ids"] and k.active(fact) for fact in k.rows)]
                    if not project_evidence:
                        return self.unknown(question, s, "MISSING_VERIFIED_PROJECT_SKILL_EVIDENCE")
                evidence = self.skill_exposure(skill)
                used.extend(evidence.pop("facts"))
                if intent == "SKILL_DURATION":
                    bucket = (
                        "professional"
                        if "professional" in text or "commercial" in text
                        else "academic"
                        if "academic" in text
                        else "project"
                        if "project" in text
                        else "calendar"
                    )
                    metric = evidence[bucket]
                    if metric["intervals"]:
                        answer = (
                            metric["months"]
                            if "month" in text
                            else metric["integer_years"]
                            if field.get("type") == "number"
                            else f"{metric['years']:g}"
                        )
                        # what the number counts is the reason shown beside it, never text typed into the employer's form
                        explanations.append(f"{metric['years']:g} {'year' if metric['years'] == 1 else 'years'} of evidenced {bucket} exposure (overlapping periods counted once)")
                        out["calculation"] = evidence
                        confidence = "DERIVED_HIGH"
                        rules.append("union_of_dated_skill_evidence")
                elif evidence["evidence_count"]:
                    answer = True if s["boolean"] else skill["name"]
                    confidence = "DERIVED_HIGH"
                    rules.append("skill_evidence_exists")
            if answer is None and intent == "PROJECT":
                answer = self.narrative(s, job, used, rules, out)
                if answer is not None:
                    confidence = "GENERATED"
        if employer_request:
            return out if answer is None else self._finish_evidence_answer(out, answer, used, rules, s, field)
        if (
            intent
            in {
                "PROJECT",
                "OWNERSHIP",
                "LEADERSHIP",
                "TEAMWORK",
                "CHALLENGE",
                "FAILURE",
                "PROBLEM_SOLVING",
                "ACHIEVEMENT",
                "MOTIVATION",
                "COMPANY_INTEREST",
                "ROLE_INTEREST",
                "CAREER_GOALS",
                "ADDITIONAL_CONTEXT",
                "COVER_LETTER",
                "AI_USAGE",
                "LLM_USAGE",
                "WORK_EXPERIENCE",
            }
            and answer is None
        ):
            if intent == "LEADERSHIP" and re.search(r"largest|how many|number|size|more than|at least", text):
                rows = [r for r in k.records("experience") if val(r, "team_size") is not None]
                if rows:
                    r = max(rows, key=lambda r: val(r, "team_size"))
                    n = read(r, "team_size")
                    answer = n
                    confidence = "DERIVED_HIGH"
                    rules.append("largest_verified_team")
                    threshold = re.search(r"(?:more than|over|at least|greater than)\s*(\d+)", text)
                    if threshold:
                        answer = n >= int(threshold[1]) if "at least" in text else n > int(threshold[1])
            else:
                answer = self.narrative(s, job, used, rules, out)
            if answer is not None and not isinstance(answer, (int, bool)):
                confidence = "GENERATED"
        if answer is None:
            if intent in NARRATIVE_INTENTS and not s["boolean"] and not employer_request:
                out.update(root_cause="ASYNC_GENERATION_REQUIRED" if self.answer_llm else "LLM_SETUP_REQUIRED",
                           reason="A question-specific generative answer is required", state="generation_required" if self.answer_llm else "setup_required")
            return out
        if s.get("context_inherited") and confidence in {"EXACT", "POLICY"}:
            confidence = "DERIVED_HIGH"  # read from the text around a label that says nothing itself: to be checked
            rules.append("intent_read_from_the_text_around_the_label")
        formatted, error = adapt(answer, s, field)
        if error:
            return {
                **out,
                "root_cause": error,
                "reason": error,
                "candidate_value": answer,
                "fact_ids": list(dict.fromkeys(f["id"] for f in used)),
            }
        ids = list(dict.fromkeys(f["id"] for f in used))
        reason = "; ".join(explanations + rules) or "Direct verified candidate fact"
        result = {
            **out,
            "answer": ", ".join(formatted) if isinstance(formatted, list) else formatted,
            "form_value": formatted,
            "confidence": 1 if confidence in {"EXACT", "POLICY"} else 0.96 if confidence == "DERIVED_HIGH" else 0.85,
            "confidence_level": confidence,
            "verified": confidence in {"EXACT", "POLICY"},
            "inferred": confidence not in {"EXACT", "POLICY"},
            "fact_ids": ids,
            "facts_used": ids,
            "rules_used": rules,
            "reason": reason,
            "reasoning_summary": reason,
            "requires_confirmation": confidence not in {"EXACT", "POLICY"},
            "requires_review": confidence not in {"EXACT", "POLICY"},
            "root_cause": None,
        }
        if isinstance(formatted, list):
            result["selected_options"] = [choice(label, field.get("options", []), question=question) for label in formatted]
            result["answer_type"] = "MULTIPLE_CHOICE"
        result["claims"] = [
            {"text": formatted, "facts": ids, "rules": rules, "construction": "deterministic evidence composition"}
        ]
        return result

    def _finish_evidence_answer(self, out, answer, used, rules, s, field):
        value, error = adapt(answer, s, field)
        ids = list(dict.fromkeys(f["id"] for f in used))
        return {
            **out,
            "answer": value,
            "form_value": value,
            "fact_ids": ids,
            "facts_used": ids,
            "rules_used": rules,
            "confidence_level": "DERIVED_HIGH",
            "confidence": 0.96,
            "inferred": True,
            "root_cause": error,
            "reason": "; ".join(rules),
            "reasoning_summary": "; ".join(rules),
            "claims": [
                {"text": value, "facts": ids, "rules": rules, "construction": "deterministic evidence composition"}
            ]
            if value
            else [],
        }

    def find_skill(self, text):
        candidates = []
        for skill in self.knowledge.records("skill"):
            names = [skill["name"], *skill["aliases"]]
            if any(re.search(r"\b" + re.escape(normalize(name)) + r"\b", text) for name in names):
                candidates.append(skill)
        return max(candidates, key=lambda s: len(s["name"])) if candidates else None

    def skill_exposure(self, skill):
        k = self.knowledge
        intervals = {x: [] for x in ("professional", "academic", "project")}
        facts = []
        count = 0
        for link in k.links:
            if link["subject_id"] != skill["id"] or link["predicate"] != "used_in":
                continue
            e = k.entities.get(link["object_id"])
            if not e:
                continue
            supporting = [f for f in k.rows if f["id"] in link["fact_ids"] and k.active(f)]
            if not supporting:
                continue
            count += 1
            facts.extend(supporting)
            record = next(r for r in k.records(e["kind"]) if r["id"] == e["id"])
            for key in ("start_date", "end_date", "ongoing"):
                if key in record["fields"]:
                    facts.append(record["fields"][key])
            bucket = {"experience": "professional", "education": "academic", "project": "project"}.get(
                e["kind"], "project"
            )
            intervals[bucket].append(
                interval(val(record, "start_date"), val(record, "end_date"), k.today, val(record, "ongoing", False))
            )
        return {
            **{b: exposure(v) for b, v in intervals.items()},
            "calendar": exposure(sum(intervals.values(), [])),
            "facts": facts,
            "evidence_count": count,
        }

    def narrative(self, s, job, used, rules, metadata=None):
        if not self.allow_extractive_narratives:
            return None
        result = compose_narrative(self.knowledge, s, job)
        used.extend(result["facts"])
        rules.extend(result["rules"])
        if metadata is not None and result["answer"]:
            metadata["narrative_context"] = result["context"]
            metadata["job_context_sources"] = job.get("context_sources", [])
        return result["answer"]

    async def answer(self, question, field=None, job=None, *, audit=True, generate=True):
        """Resolve one question. ``generate=False`` never calls a model: narrative questions come back
        as ``ASYNC_GENERATION_REQUIRED`` so a caller can queue the paid draft separately (browser extension).
        A credential-like control (password/hidden) is never described to any model, local or remote."""
        field, job = field or {}, job or {}
        self.reload()  # no stale relative facts or policy snapshots across requests
        secret_input = str(field.get("type") or "").lower() in SECRET_INPUT_TYPES
        result = self.answer_question(question, field, job)
        if result["canonical_intent"] == "OTHER" and not secret_input:
            cache_key = dumps([question, field])
            if cache_key not in self.semantic_cache:
                self.semantic_cache[cache_key] = await semantic_interpret(question, field, self.settings)
            parsed = self.semantic_cache[cache_key]
            if parsed and (parsed.get("intent") not in plain.FAMILY_OF or parsed.get("intent") in plain.LINKS):
                # a local model may read an unusual wording, never move a question INTO a kind of fact that has a plain-question
                # gate and a legal reading (a work right, a sponsorship, a visa, a degree...): those are read by grammar or not at all
                result = self.answer_question(question, field, job, semantic=parsed)
        semantic = result.get("semantic", interpret(question, field))
        intent = result["canonical_intent"]
        # Explanations, preferences and unusual free-text questions always go
        # through a capable generative provider. Extractive project prose is a
        # diagnostics facility, never a production fallback for these fields.
        narrative = intent in NARRATIVE_INTENTS and not (
            semantic.get("boolean") and intent in {"PROJECT", "WORK_EXPERIENCE"}
        )
        if intent == "WORK_EXPERIENCE" and re.search(r"employer|company|empleador|empresa|compania", semantic["text"]) and not re.search(
            r"describe|experience|background|responsibilities|achiev|learn|experiencia|trayectoria|responsabilidades|logros|aprend", semantic["text"]
        ):
            narrative = False
        if intent == "LEADERSHIP" and result.get("answer") is not None and re.search(
            r"largest|how many|number|size|more than|at least", semantic["text"]
        ):
            narrative = False
        if intent == "OTHER" and field.get("type") not in {"number", "date", "month", "file", "checkbox"}:
            narrative = True
        if secret_input:
            narrative = False
        hostile = bool(INSTRUCTION_IN_LABEL.search(str(question or "")))
        if hostile and (narrative or intent == "OTHER"):
            # Instructions written into the label are data, never a request: nothing is drafted for it (the employer's
            # words are not sent to any model as a task) and no fact is read for it.
            detail = "The question contains instructions written to the assistant; they are never followed"
            result = {**self.unknown(question, semantic, "UNTRUSTED_INSTRUCTION_IN_QUESTION"), "reason": detail,
                      "explanation": detail, "reasoning_summary": detail}
            narrative = False
        if narrative:
            base = self.unknown(question, semantic, "LLM_SETUP_REQUIRED")
            base["requested_format"] = infer_format(question, field)
            if not generate:
                base.update(state="generation_required", root_cause="ASYNC_GENERATION_REQUIRED",
                            reason="This answer is drafted separately from the verified facts.",
                            reasoning_summary="ASYNC_GENERATION_REQUIRED")
                result = base
            elif self.answer_llm is None:
                base.update(state="setup_required", reason="An LLM is required to generate personalised answers to open-ended application questions.",
                            missing_fact={"concept": "application_answer_llm_configuration"})
                result = base
            else:
                try:
                    generated = await self.answer_llm.answer(
                        question, field, job, self.knowledge, semantic, settings=self.settings
                    )
                    value = generated.get("answer")
                    if generated.get("selected_options") and field.get("options"):
                        value = generated["selected_options"] if semantic["output"] == "MULTIPLE_CHOICE" else generated["selected_options"][0]
                    if value is None:
                        result = {**base, **generated}
                    else:
                        adapted, error = adapt(value, semantic, field)
                        ids = generated.get("facts_used", generated.get("fact_ids", []))
                        result = {
                            **base, **generated, "question": question, "semantic": semantic,
                            "canonical_intent": intent, "answer": None if error else ", ".join(adapted) if isinstance(adapted, list) else adapted,
                            "form_value": adapted, "fact_ids": ids, "facts_used": ids,
                            "answer_type": semantic["output"], "confidence_level": "GENERATED",
                            "verified": False, "inferred": True, "requires_review": True,
                            "requires_confirmation": False, "root_cause": error,
                            "reason": error or "Question-specific generation from cited verified candidate evidence",
                        }
                        if isinstance(adapted, list):
                            result["selected_options"] = [choice(label, field.get("options", []), question=question) for label in adapted]
                except (ValueError, RuntimeError) as error:
                    code = getattr(error, "code", None) or getattr(error, "root_cause", None) or type(error).__name__
                    issues = getattr(error, "issues", [])
                    factual_issue = next((str(issue) for issue in issues if str(issue).startswith("MISSING_CANDIDATE_FACT")), None)
                    if factual_issue:
                        code = "MISSING_CANDIDATE_FACT"
                    result = {**base, "root_cause": code, "reason": str(error), "state": "setup_required" if "CONFIGURATION" in str(code) else "failed",
                              "validation_issues": issues, "missing_facts": [factual_issue] if factual_issue else generated.get("missing_facts", []) if "generated" in locals() else []}
        # An uncertain classifier may interpret an unknown concept or refine
        # an unmappable enum. It cannot replace an already grounded resolution
        # or evade a known missing fact/contradiction by switching concepts.
        protected = {
            "CONSENT",
            "GDPR",
            "DEMOGRAPHIC",
            "WORK_AUTHORIZATION",
            "SPONSORSHIP",
            "VISA",
            "CRIMINAL_HISTORY",
            "SECURITY_CLEARANCE",
            "BACKGROUND_CHECK",
            "CONFLICT_OF_INTEREST",
            "ROLE_CONFIRMATION",
        }
        if not narrative and not secret_input and not hostile and result["canonical_intent"] not in protected and (
            result["canonical_intent"] == "OTHER" or result.get("root_cause") == "BAD_ENUM_MAPPING"
        ):
            key = dumps(
                [
                    question,
                    field.get("description"),
                    field.get("section_label") or field.get("section"),
                    field.get("type"),
                    field.get("options"),
                ]
            )
            if key not in self.semantic_cache:
                self.semantic_cache[key] = await semantic_interpret(question, field, self.settings)
            semantic = self.semantic_cache[key]
            if semantic and semantic.get("intent") in plain.FAMILY_OF and semantic.get("intent") not in plain.LINKS \
                    and result["canonical_intent"] == "OTHER":
                semantic = None
            if semantic and (result["canonical_intent"] == "OTHER" or semantic["intent"] == result["canonical_intent"]):
                result = self.answer_question(question, field, job, semantic=semantic)
        if result.get("semantic", {}).get("parser") == "local_semantic" and result.get("answer") is not None:
            result.update(
                verified=False,
                inferred=True,
                requires_confirmation=True,
                requires_review=True,
                confidence_level="DERIVED_MEDIUM",
                confidence=0.85,
            )
        if audit:
            digest = hashlib.sha256(dumps([self.knowledge.version, question, field, job]).encode()).hexdigest()
            self.db.execute(
                "INSERT INTO answer_provenance VALUES(?,?,?,?,?,?)",
                (uid(), job.get("application_id"), question, digest, dumps(result), now()),
            )
        return result
