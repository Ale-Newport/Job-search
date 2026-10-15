"""FieldResolver: the answer pipeline of ``automation/runner.py`` for ONE control of a page the user is filling.

``runner.py`` (``resolve_raw`` + ``resolve``) is not refactored; this module reproduces its decisions so the browser
assistant and Auto Apply can never disagree about what the verified profile says. ``test_extension_resolver.py`` runs
both on the shared ATS fixtures and compares them, so drift is caught.

Order of authority (first match wins), exactly as in the runner:

1. a manual correction the user typed in a browser form (``manual_correction`` facts)
2. the personal-disclosure policy (leave blank)
3. ``CandidateEngine.answer`` (typed knowledge; narratives are deferred: ``generate=False``) then
   ``confirmed_fallback`` (saved / explicit flat facts, never against structured evidence)
4. the legacy profile resolver for an unknown ``OTHER`` question, then the location country completion
5. for choice controls, the second stage: the verified raw answer is mapped to a REAL option by ``OptionSelector``
   (deterministic: no local model call)

Nothing here performs I/O other than short local SQLite reads, and nothing sends data to a model unless
``generate=True`` is passed (the generation queue does, with a validated, budgeted request).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date

from ..application_profile import application_knowledge
from ..automation.adapters import get_adapter
from ..automation.answers import normalize, question_identity
from ..automation.consent import radio_group, resolution_element
from ..automation.dialcode import resolve_dial_code
from ..automation.disclosures import (disclosure_memory_key, disclosure_resolution, inherit_disclosure_context,
                                      personal_disclosure_kind)
from ..automation.option_selection import OptionSelector
from ..automation.reasoning import conditional_state, profile_resolution
from ..automation.runner import BrowserManager
from ..intelligence import instructions
from ..intelligence.engine import CandidateEngine
from ..intelligence.plain import label_core
from ..intelligence.semantics import entry_attribute, form_repeats, refuse_repeated_identity
from .schemas import ElementIn
from .text import strip_controls

# the controls whose value a form dictates (a picker, a number box, an address) or that are written at length: never rewritten
NOT_REWRITTEN_TYPES = {"password", "hidden", "file", "checkbox", "radio", "date", "month", "number", "email", "url", "color", "range"}


def is_long_text(field_el: dict) -> bool:
    return field_el.get("tag") == "textarea" or str(field_el.get("type") or "") == "textarea" \
        or bool(field_el.get("max_length") and field_el["max_length"] >= 150)


DETERMINISTIC = {"browser_engine": "deterministic"}  # no text_provider: semantic_interpret never leaves the process
OPERATIONS_BY_ROLE = {"textbox": ["TYPE_TEXT", "CLEAR"], "radio": ["CHECK"], "checkbox": ["CHECK", "UNCHECK"],
                      "switch": ["CHECK", "UNCHECK"], "upload": ["UPLOAD"], "combobox": ["TYPE_TEXT", "CLICK"]}


class SnapshotEngine(CandidateEngine):
    """A CandidateEngine whose knowledge is loaded once per snapshot, not on every question.

    ``CandidateEngine.answer`` reloads the whole knowledge base per call (right for the runner, which can run while
    the profile is being edited); a page of 40 fields would reload it 40 times. Staleness is handled one level up:
    the snapshot is rebuilt when the data token changes."""

    def reload(self):
        return None


# The text a question is made of. Never what is sent back to find a control or an option (``value``, the options'
# labels and values, ``selected_*``): those must match the page character for character.
QUESTION_TEXT = ("label", "context", "description", "placeholder", "section_label", "group", "field_path", "name", "id")
# What a person asks fits these; the rest is a page's padding or an attack. Whitespace is ONE space and the text is bounded before
# any pattern of the engine runs (a run of 20,000 spaces, or 900 KB of 'normal' labels, must not hold the event loop).
QUESTION_LIMITS = {"label": 1500, "context": 2000, "description": 4000, "placeholder": 500, "section_label": 200, "group": 500,
                   "field_path": 300, "name": 300, "id": 300}


LINE_KEYS = ("label", "context", "group")  # the texts of a question keep their line breaks: a 'Required' on a second line is a hint of its own


def tidy(value: str, limit: int, lines: bool = False) -> str:
    if lines and "\n" in value:
        # one space for any other whitespace; a line break stays ONE line break (at most eight lines, the rest is joined)
        parts = [" ".join(line.split()) for line in value.replace("\r", "\n").split("\n")]
        parts = [line for line in parts if line]
        text = "\n".join([*parts[:7], " ".join(parts[7:])] if len(parts) > 8 else parts)
    else:
        text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit * 4 // 5] + " " + text[-(limit // 5):]


# Words that decide WHICH fact a label asks for: one present in only one of two names of a control makes them two questions.
DECIDING_WORDS = {"email", "phone", "password", "salary", "visa", "sponsorship", "gender", "ethnicity", "disability", "religion",
                  "nationality", "linkedin", "github", "consent", "signature", "birth"}
# the words of each, in English and Spanish, as ONE word ('mobile' and 'telephone' are a phone)
DECIDING_SYNONYMS = {"mail": "email", "correo": "email", "mobile": "phone", "telephone": "phone", "telefono": "phone",
                     "movil": "phone", "celular": "phone", "contrasena": "password", "salario": "salary", "visado": "visa",
                     "genero": "gender", "nacimiento": "birth", "firma": "signature"}
STOP_WORDS = {"the", "your", "a", "an", "of", "and", "or", "you", "is", "are", "to", "please", "tu", "su", "el", "la", "de"}


def labels_disagree(visible: str, accessible: str) -> bool:
    """Do the name the person sees and the page's own name for the control ask different things? A hint, a required marker or
    'Email' / 'Email address' is the same question; two names with nothing in common, or one carrying a word that decides
    the fact asked (email, phone, salary...) that the other lacks ('Hobbies' / 'Hobbies Email'), are not."""
    a = [DECIDING_SYNONYMS.get(w, w) for w in normalize(visible).split()]
    b = [DECIDING_SYNONYMS.get(w, w) for w in normalize(accessible).split()]
    if not a or not b or a == b:
        return False
    if not (set(a) & set(b)) - STOP_WORDS:
        return True
    return bool((set(a) ^ set(b)) & DECIDING_WORDS)


def without_option_text(text: str, options: list) -> str:
    """The label of a choice group without the texts of its own options at the end of it: a page whose question and answer buttons share one
    container reads 'Are you authorised to work in the UK?✱ YesNo' (the scanner joins the texts). The options say what they are; the
    question is what stands before them. Only an ending that is EXACTLY the option labels in their order (spaces aside) is taken off."""
    labels = ["".join(str(o.get("label") or "").split()) for o in options if str(o.get("label") or "").strip()]
    tail = "".join(labels)
    if not text or not 1 < len(labels) <= 12 or not tail:
        return text
    if "".join(text.split()).endswith(tail) and len("".join(text.split())) > len(tail):
        remaining, cut = len(tail), len(text)
        while remaining and cut > 0:
            cut -= 1
            if not text[cut].isspace():
                remaining -= 1
        return text[:cut].rstrip(" \n\t:-") or text
    return text


def prepare_elements(models: list[ElementIn], document_id: str, frame_url: str) -> list[dict]:
    """Request elements -> the dicts the engine and the runner helpers expect (``index``, ``document_id``...).

    Underscore keys never survive (the schema drops them) and typed text never arrives: ``value`` is only
    meaningful for choice controls."""
    prepared = []
    for index, model in enumerate(models):
        element = model.model_dump()
        for key in QUESTION_TEXT:
            # no NUL, no bidirectional override in what a question is read from; one space for any whitespace; bounded
            element[key] = tidy(strip_controls(element[key]), QUESTION_LIMITS[key], key in LINE_KEYS)
        element["visible_label"] = tidy(strip_controls(element.get("visible_label") or ""), QUESTION_LIMITS["label"], True)
        if element["role"] in {"radio", "checkbox"} and element["options"]:
            for key in ("label", "context", "visible_label"):
                element[key] = without_option_text(element[key], element["options"])
        if element["visible_label"]:
            if labels_disagree(element["visible_label"], element["label"]):
                # the page's accessible name is not what the person reads: answer what is READ, and never as verified
                element["_label_mismatch"] = element["label"]
            element["label"] = element["visible_label"]  # the person answers what is read
            element["visible_label"] = ""
        element.update(index=index, document_id=document_id or "document", frame_url=frame_url or "")
        element["options"] = [dict(o) for o in element["options"]]
        if element["role"] == "textbox":
            # What the user typed is none of the server's business even if a client sends it by mistake.
            element["has_value"] = bool(element["has_value"] or element["value"])
            element["value"] = ""
        if not element["operations"] and element["enabled"] and not element["readonly"]:
            # A scan that omits `operations` must not turn every radio option into a disabled one.
            element["operations"] = list(OPERATIONS_BY_ROLE.get(element["role"], []))
            if element["tag"] == "select":
                element["operations"] = ["SELECT"]
        prepared.append(element)
    return prepared


def is_client_group(element: dict) -> bool:
    """A radio set or multi-checkbox group the page already grouped: ONE element (the first member) whose ``options``
    list every member and whose ``selected_values`` say which are on (the extension's own scan does this)."""
    return (element.get("role") == "radio" or (element.get("role") == "checkbox" and bool(element.get("multiple")))) \
        and bool(element.get("options"))


def member_view(elements: list[dict]) -> list[dict]:
    """The frame as the runner's reasoning expects it: one element per radio/checkbox member (label = the option,
    ``checked`` = it is selected, ``context`` = the question). A group the page sent as ONE element is expanded here, so a
    conditional follow-up reads the SELECTED OPTION of its parent ('No') and not the parent's question text."""
    view: list[dict] = []
    for element in elements:
        if not is_client_group(element):
            view.append(element)
            continue
        on = {str(v) for v in element.get("selected_values") or []} | ({str(element["value"])} if element.get("value") else set())
        question = element.get("context") or element.get("label") or ""
        for number, option in enumerate(element["options"]):
            value = str(option.get("value") or option.get("label") or "")
            view.append({**element, "label": option.get("label", ""), "value": value, "context": question, "options": [],
                         "checked": value in on, "enabled": element.get("enabled", True) and not option.get("disabled"),
                         "node_id": element["node_id"] if number == 0 else -(element["node_id"] * 1000 + number),
                         "selected_values": []})
    return view


def data_token(db) -> str:
    """A cheap fingerprint of everything an answer can depend on (one SQL statement, no row decoding)."""
    row = db.one(
        "SELECT"
        " (SELECT COUNT(*)||':'||COALESCE(MAX(updated_at),'')||':'||COALESCE(SUM(locked),0)||':'||"
        "  COALESCE(SUM(verification_status='verified'),0) FROM facts) AS facts,"
        " (SELECT COUNT(*)||':'||COALESCE(MAX(updated_at),'') FROM browser_answers) AS memory,"
        " (SELECT COUNT(*)||':'||COALESCE(MAX(updated_at),'')||':'||COALESCE(SUM(revision),0) FROM candidate_policies)"
        "  AS policies,"
        " (SELECT COUNT(*)||':'||COALESCE(SUM(LENGTH(fact_ids)),0) FROM knowledge_relationships) AS links,"
        " (SELECT COALESCE(SUM(revision),0)||':'||COUNT(*) FROM knowledge_facts) AS knowledge,"
        " (SELECT value FROM settings WHERE key='sensitive_policies') AS sensitive")
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:24] + date.today().isoformat()


@dataclass
class Environment:
    """Everything a resolution reads, loaded once: scoped facts, policies and one knowledge snapshot."""

    db: object
    settings: dict
    job: dict
    application: dict
    facts: list
    skipped: list
    policies: dict
    engine: SnapshotEngine
    token: str
    version: str
    created: float = field(default_factory=time.monotonic)

    def engine_for(self, answer_llm, settings: dict) -> SnapshotEngine:
        """The same knowledge snapshot wired to the shared answer model (generation only)."""
        engine = SnapshotEngine(self.db, settings=settings, answer_llm=answer_llm)
        engine.knowledge = self.engine.knowledge
        return engine

    def fact_label(self, fact_id: str) -> dict | None:
        """{id, concept, entity, verified} for a fact id, without its value (values never leave in `why`)."""
        for row in self.engine.knowledge.rows:
            if row["id"] == fact_id:
                entity = self.engine.knowledge.entities.get(row["entity_id"], {})
                # the candidate's own entity is named after them: show "Your profile", never the name itself
                name = "Your profile" if entity.get("kind") == "person" else entity.get("name", "")
                return {"id": fact_id, "concept": row["concept"], "entity": name,
                        "verified": row["verification_status"] == "verified"}
        for row in self.facts:
            if row.get("id") == fact_id:
                label = row.get("key") if row.get("category") != "application_answer" else "saved answer"
                return {"id": fact_id, "concept": str(label)[:80], "entity": str(row.get("category", ""))[:40],
                        "verified": row.get("verification_status") == "verified" or bool(row.get("locked"))}
        return None


def load_environment(db, settings: dict, *, job: dict, application: dict, token: str | None = None) -> Environment:
    """Verified/locked facts scoped to this application (corrections, saved answers), the disclosure policy and a
    knowledge snapshot. ``application`` needs only ``id`` (may be ''), ``company`` and ``location``."""
    token = token or data_token(db)
    rows = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
    facts, skipped = application_knowledge(db, application, rows)
    engine = SnapshotEngine(db, settings=dict(DETERMINISTIC))
    policies = dict(settings.get("sensitive_policies") or {})
    disclosure = engine.knowledge.get_policy("personal_disclosures", job)
    if disclosure:
        policies["personal_disclosures"] = disclosure["value"]
    # The version describes KNOWLEDGE (facts, policies, the protected-Tab switch) and the one thing about the page that
    # decides answers: the COUNTRY of the job (work authorisation, sponsorship and salary are scoped by it). It does not
    # move while research learns the company, title or city, only when the country becomes known or changes.
    digest = hashlib.sha256(json.dumps([engine.knowledge.version,
                                        [(f["id"], str(f.get("value")), f.get("updated_at")) for f in facts],
                                        policies, bool(settings.get("_tab_inserts_protected")),
                                        str(job.get("country") or ""), sorted(map(str, job.get("countries") or []))],
                                       sort_keys=True, default=str).encode()).hexdigest()[:32]
    return Environment(db, settings, job, application, facts, skipped, policies, engine, token, digest)


@dataclass
class Resolution:
    question: str
    question_id: str
    element: dict  # the representative control with its context keys (_condition, _disclosure_parent_question, ...)
    field: dict  # what the engine was shown (options of a radio group / selected option of a closed combobox)
    result: dict  # normalised resolution result
    condition: dict | None = None
    options_from_client: bool = False


class FieldResolver:
    """Resolve the controls of ONE frame document against an :class:`Environment`."""

    def __init__(self, env: Environment, *, url: str, ats: str | None, scope: str, frame_key: str,
                 document_id: str, elements: list[dict]):
        self.env, self.scope, self.frame_key = env, scope, frame_key
        self.adapter = get_adapter(url, ats)
        self.elements = elements
        self.document_id = document_id
        self.selector = OptionSelector(dict(DETERMINISTIC))
        self._peers: tuple | None = None
        self._labels: dict | None = None

    # ------------------------------------------------------------------------------- identity
    def question(self, element: dict) -> str:
        """The exact question text the engine sees (radio/checkbox groups use their group question)."""
        try:
            return self.adapter.question(element)
        except KeyError:
            return str(element.get("label") or element.get("context") or "")

    def question_id(self, element: dict, question: str | None = None) -> str:
        """Stable across reloads of the same page: scoped by the session, not by the observation document."""
        identity = {**element, "document_id": self.frame_key}
        return question_identity(self.scope, identity, question if question is not None else self.question(element))

    # ------------------------------------------------------------------------------- context
    def context(self, element: dict) -> dict | None:
        """Conditional-question state (also records the disclosure parent). None = unconditional."""
        peers = self.peers()
        inherit_disclosure_context(element, peers, self.adapter)
        condition = conditional_state(element, peers, self.adapter)
        element["_condition"] = condition or {}
        return condition

    def peers(self) -> list[dict]:
        """``member_view`` of the frame, built once per resolver instead of once per control (a form of 400 controls
        expanded all 400 for every one of them). The elements of a resolver do not change: a new request, with new or
        changed controls, builds a new resolver."""
        cached = self._peers
        if cached is None or cached[0] is not self.elements or cached[1] != len(self.elements):
            cached = self._peers = (self.elements, len(self.elements), member_view(self.elements))
        return cached[2]

    def block_index(self, element: dict) -> int | None:
        """1-based position of this control's block among the blocks the page repeats under ONE history heading (two
        sections both titled 'Employment', each with its own container): they are 'entry 1' and 'entry 2' although the
        heading carries no number. None when the heading says which entry it is, or there is only one block."""
        from ..intelligence.semantics import history_block
        heading = normalize(element.get("section_label") or "")
        block = history_block(element)
        if not heading or block is None or block["ordinal"] is not None or not element.get("section_id"):
            return None
        ids: list = []
        for peer in self.elements:
            if peer.get("form_id") == element.get("form_id") and normalize(peer.get("section_label") or "") == heading \
                    and peer.get("section_id") and peer["section_id"] not in ids:
                ids.append(peer["section_id"])
        return ids.index(element["section_id"]) + 1 if len(ids) > 1 and element["section_id"] in ids else None

    def repeats(self, element: dict, question: str) -> dict:
        """``semantics.form_repeats`` for this control, from an index of the page's controls by question (built once)."""
        if self._labels is None:
            self._labels = {}
            for peer in self.elements:
                self._labels.setdefault(normalize(self.question(peer)), []).append(peer)
        return form_repeats(element, self._labels.get(normalize(question), []), self.question)

    def group_members(self, element: dict) -> list[dict]:
        return radio_group(element, self.elements)

    def choice_field(self, element: dict) -> tuple[dict, bool]:
        """The control as the engine must see it. A client that already grouped a radio/checkbox set sent its
        options; otherwise the observed peers define them (``resolution_element``)."""
        grouped = element["role"] in {"radio", "checkbox"} and (element["role"] == "radio" or element["multiple"])
        if grouped and element.get("options"):
            return dict(element), True
        return resolution_element(element, self.elements), False

    # ------------------------------------------------------------------------------- the pipeline
    async def resolve(self, element: dict, *, generate: bool = False, engine=None, job: dict | None = None,
                      related_answers=(), condition: dict | None = None) -> Resolution:
        """``resolve_raw`` + ``resolve`` of the runner for one control. ``generate=False`` (default) never calls a
        model; ``generate=True`` requires an ``engine`` wired to the answer LLM (the generation queue)."""
        env = self.env
        engine = engine or env.engine
        job = job if job is not None else env.job
        question = self.question(element)
        qid = self.question_id(element, question)
        if condition is None:
            condition = self.context(element)
        field_el, from_client = self.choice_field(element)
        # `_condition` and `_disclosure_parent_question` were recorded on the control by context() and travel with it
        field_el = {**field_el, "question_id": qid, "_related_answers": [dict(a) for a in related_answers]}
        block = self.block_index(element)
        if block:
            field_el["_block_index"] = block  # the 2nd of the blocks the page repeats under one heading ('Employment')
        repeats = self.repeats(element, question)
        asked = label_core(question)  # what the label asks: without an instruction ('Please answer:'), a 'Required' line, a hidden character
        if repeats["repeated"] and entry_attribute(asked):
            field_el["_repeated_block"] = True  # 'Start date' of the 2nd block of a form that repeats it: an entry, not the applicant
        result = await self._raw(asked, element, field_el, engine, job, generate)
        result = await self._map_options(asked, field_el, result, engine, job)
        result = self._choose_plan(field_el, result, generate)
        result = self.normalise(result, question, qid)
        result = refuse_repeated_identity(result, question, repeats)  # the applicant's own details are offered in the first block only
        if element.get("_label_mismatch") and result["answer"] is not None and result["confidence_level"] in {"EXACT", "POLICY"}:
            # the page names this control otherwise than the person reads it: an answer to what is read, to be checked
            result = {**result, "verified": False, "inferred": True, "confidence_level": "DERIVED_MEDIUM", "confidence": 0.85,
                      "requires_review": True, "reason": "The page's name for this field differs from the label you see; "
                                                         "check that this is what it asks"}
        return Resolution(question, qid, element, field_el, result, condition, from_client)

    async def _raw(self, question: str, element: dict, field_el: dict, engine, job: dict, generate: bool) -> dict:
        result = await self._raw_core(question, element, field_el, engine, job, generate)
        return await self._with_instructions(question, element, field_el, result, engine, job, generate)

    async def _with_instructions(self, question: str, element: dict, field_el: dict, result: dict, engine, job: dict,
                                 generate: bool) -> dict:
        """A control that says HOW its answer is to be written ('(e.g. Summer 2027)', 'no spaces', a placeholder example) is answered
        in two parts: the question without its instruction is answered by the engine as it always is, and the instruction is left
        to the local writing model, which may only write that value differently (``local_assist.rewrite_value``). Here only the
        first part happens, and what the second needs travels with the result as ``_local``; nothing is changed in what the
        engine said, so a page that never reaches a model behaves exactly as before."""
        if (generate or element.get("role") not in {"textbox", "combobox"} or field_el.get("options")
                or str(field_el.get("type") or "").lower() in NOT_REWRITTEN_TYPES or is_long_text(field_el)):
            return result
        if not [hint for hint in instructions.hints(question, field_el) if not instructions.explained(hint)]:
            return result
        core, _ = instructions.split_instruction(question)
        cleaned = {**field_el, "label": instructions.split_instruction(field_el.get("label") or "")[0],
                   "description": "", "help_text": "", "instructions": "", "hint": "",
                   "placeholder": "" if instructions.example_of(field_el) else field_el.get("placeholder", "")}
        base = await engine.answer(core, cleaned, job, audit=False, generate=False)
        base = engine.confirmed_fallback(core, base, self.env.facts, self.env.policies)
        if base.get("answer") is None or base.get("root_cause") or base.get("leave_blank") \
                or base.get("confidence_level") not in {"EXACT", "POLICY", "DERIVED_HIGH", "DERIVED_MEDIUM"}:
            return result
        hints = instructions.needs_rewriting(str(base["answer"]), question, field_el)
        if not hints or not instructions.worth_rewriting(str(base["answer"]), hints):
            # Nothing a model could check would improve the answer to the question itself ("Degree subject (e.g. Computer Science)": the
            # bracket is an example, not a fact), so no model works on this field. When the engine had refused the label only because of
            # that bracket, the answer to the plain question is the answer.
            if result.get("answer") is not None or result.get("root_cause") != "PLAIN_QUESTION_REQUIRED":
                return result
            return {**base, "question": question, "rules_used": [*(base.get("rules_used") or []), "instruction_split"]}
        return {**result, "_local": {"kind": "rewrite", "base": base, "hints": hints, "core": core}}

    async def _raw_core(self, question: str, element: dict, field_el: dict, engine, job: dict, generate: bool) -> dict:
        env = self.env
        facts, policies = env.facts, env.policies
        # 'Phone country code', 'Country Phone Code', 'Dialling code'...: a country's code, never the whole number (the
        # same reading as Auto Apply's; a closed menu gets no answer: the assistant cannot open it and look again)
        dial = resolve_dial_code(question, field_el.get("options") or [], engine.answer_question, job, closed="refuse")
        if dial is not None:
            return dial
        corrections = [f for f in facts if f.get("manual_correction")]
        if corrections:
            corrected = profile_resolution(question, corrections, element=field_el, policies={})
            if corrected["answer"] is not None or corrected["leave_blank"]:
                return BrowserManager._location_resolution(corrected, facts, field_el)
        disclosure = disclosure_resolution(question, field_el, policies)
        if disclosure:
            return disclosure
        self._education_reference(question, field_el)
        result = await engine.answer(question, field_el, job, audit=False, generate=generate)
        result = engine.confirmed_fallback(question, result, facts, policies)
        if result["canonical_intent"] != "OTHER" or result["answer"] is not None:
            return BrowserManager._location_resolution(result, facts, field_el)
        legacy = profile_resolution(question, facts, element=field_el, policies=policies,
                                    hints=[element.get("name", ""), element.get("autocomplete", "")])
        if legacy["answer"] is None and not legacy["leave_blank"] and result.get("root_cause") == "ASYNC_GENERATION_REQUIRED":
            return result  # still a drafting candidate: keep the deferral, not the legacy "unknown"
        if legacy["answer"] is None and not legacy["leave_blank"] and result.get("root_cause"):
            return result  # an engine failure (budget, model, validation) outranks a legacy "unknown"
        return BrowserManager._location_resolution(legacy, facts, field_el)

    def _education_reference(self, question: str, field_el: dict) -> None:
        """'GPA' / 'discipline' / 'above university' follow the institution chosen earlier in the SAME form.
        Only controls whose choice the page reports (selects, comboboxes) can serve as that reference: typed text is
        never sent to Meridian."""
        if not (re.search(r"\babove (?:university|institution|school)\b", question, re.I)
                or (re.search(r"\b(?:grade|gpa|average|classification|discipline)\b", question, re.I)
                    and not re.search(r"\b(?:highest|bachelor|undergraduate|master|postgraduate|school|secondary)\b",
                                      question, re.I))):
            return
        elements = self.elements
        preceding = [e for e in elements if e["index"] < field_el["index"] and e.get("form_id") == field_el.get("form_id")
                     and e.get("document_id") == field_el.get("document_id")
                     and re.search(r"university|institution|school", self.question(e), re.I)]
        preceding = [e for e in preceding if e.get("section_label") == field_el.get("section_label")
                     and any(op in e.get("operations", []) for op in ("TYPE_TEXT", "SELECT"))
                     and (e.get("selected_text") or e.get("value"))]
        from ..intelligence.semantics import interpret
        preceding = [e for e in preceding if interpret(self.question(e), e).get("intent") == "EDUCATION"]
        if not preceding:
            return
        parent = preceding[-1]
        explicit = re.search(r"\babove (?:university|institution|school)\b", question, re.I)
        completed = any(parent["index"] < peer["index"] < field_el["index"]
                        and peer.get("form_id") == field_el.get("form_id")
                        and peer.get("document_id") == field_el.get("document_id")
                        and re.search(r"\bhighest completed (?:degree|qualification)\b", self.question(peer), re.I)
                        and (peer.get("value") or peer.get("selected_text")) for peer in elements)
        if explicit or not completed:
            field_el["_education_reference"] = parent.get("selected_text") or parent.get("value")

    async def _map_options(self, question: str, field_el: dict, result: dict, engine, job: dict) -> dict:
        """Second stage of ``resolve``: map the verified raw answer to a REAL option of the control."""
        options = field_el.get("options") or []
        if not options or result.get("leave_blank"):
            return result
        facts, policies = self.env.facts, self.env.policies
        bare = {**field_el, "options": []}
        if field_el.get("role") in {"radio", "checkbox"} or field_el.get("multiple"):
            bare.update(type="text", role="textbox", tag="input", multiple=False)
        raw = engine.answer_question(question, bare, job)
        raw = engine.confirmed_fallback(question, raw, facts, policies)
        raw = BrowserManager._location_resolution(raw, facts, field_el)
        if raw.get("answer") is None or raw.get("leave_blank") or not raw.get("verified"):
            return result
        if field_el.get("role") == "combobox" and field_el.get("tag") != "select" and not field_el.get("expanded"):
            return result if result.get("answer") is not None else raw
        values = (raw.get("form_value") if isinstance(raw.get("form_value"), list) else
                  [raw["answer"]] if raw.get("disclosure_concept") else
                  result.get("form_value") if field_el.get("multiple") and isinstance(result.get("form_value"), list)
                  else [raw["answer"]])
        decisions = [await self.selector.select(raw, options, item) for item in values]
        if not decisions or any(decision is None for decision in decisions):
            return result
        labels = list(dict.fromkeys(decision["option"]["label"] for decision in decisions))
        return {**raw, "answer": ", ".join(labels) if field_el.get("multiple") else labels[0],
                "form_value": labels if field_el.get("multiple") else labels[0],
                "option_selection": [{k: v for k, v in decision.items() if k != "option"} for decision in decisions],
                "confirmed_answer": raw["answer"]}

    @staticmethod
    def _choose_plan(field_el: dict, result: dict, generate: bool) -> dict:
        """A menu the engine found no option for: say so, so that the local models may be asked (``local_assist.choose_option``).
        Only a menu of one choice (not a set of boxes) and only the plain "no confirmed value"; the policy of the question (consent,
        demographics, legal) is judged where the control is known (``suggest._local``)."""
        options = field_el.get("options") or []
        if (generate or not options or field_el.get("multiple") or result.get("answer") is not None or result.get("leave_blank")
                or str(field_el.get("type") or "").lower() in {"password", "hidden", "file"}):
            return result
        return {**result, "_local": {"kind": "choose"}}

    @staticmethod
    def normalise(result: dict, question: str, question_id: str) -> dict:
        """The engine's result dicts are inconsistent across paths (docs/EXTENSION_DESIGN.md 5.3): make every key the
        classifier reads present, and never trust a single flag."""
        out = dict(result)
        out.setdefault("question", question)
        out["question_id"] = question_id
        out["canonical_intent"] = out.get("canonical_intent") or "OTHER"
        out["answer"] = out.get("answer")
        out["leave_blank"] = bool(out.get("leave_blank"))
        out["verified"] = bool(out.get("verified"))
        out["inferred"] = bool(out.get("inferred"))
        out["fact_ids"] = list(out.get("fact_ids") or out.get("facts_used") or [])
        level = out.get("confidence_level")
        out["confidence_level"] = level or ("EXACT" if out["verified"] else "DERIVED_MEDIUM" if out["inferred"] else "UNKNOWN")
        if out.get("source") == "user_edit" and not level:
            out["confidence_level"] = "EXACT"
        return out


def memory_key(question: str, element: dict) -> str:
    """The key under which a saved answer for this exact question is stored and found again."""
    return disclosure_memory_key(question, element)


def is_personal(question: str, element: dict) -> bool:
    return bool(personal_disclosure_kind(question, element))
