"""The answer engine's newest behaviours, proven through the real page, the real extension and the real bridge (not through the engine's
own unit suites, which they are the counterpart of):

* the plain-question gate: a fact answers a question only when the question is a PLAIN one; a wording that adds anything the form's
  family does not account for stays 'needs you' in the real page, next to plain questions that ARE answered;
* a stratified sample of the held-out METAMORPHIC suite (backend/tests/fixtures/engine/metamorphic): 20 wrapped labels (notes, negators,
  translations, follow-ups, other people's details, injected instructions ...) and their plain bases, rendered as a real form for one of
  the suite's synthetic candidates, judged by the suite's own properties and hand-written oracle (``check.evaluate``), reading what the
  chip on screen says instead of the engine's result object.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass

import pages
import seed
import ui
from framework import Env, check, scenario
from scenarios_forms import SUGGEST
from ui import Chip, Panel
from scale import millis  # noqa: E402

def metamorphic():
    """The held-out suite's modules (imported on first use; the package lives beside the engine fixtures and is test data and machinery
    only: it imports nothing of the answer engine)."""
    from harness import REPO

    root = str(REPO / "backend" / "tests" / "fixtures" / "engine")
    if root not in sys.path:
        sys.path.insert(0, root)
    from metamorphic import bases, check as properties, people, plan, runner

    return bases, properties, people, plan, runner


# ---------------------------------------------------------------------------------------------- the plain-question gate
PLAIN = [  # (id, kind, label, options, the value a plain answer shows)
    ("p_first", "text", "First name", (), seed.FIRST),
    ("p_last", "text", "Last name", (), seed.LAST),
    ("p_email", "email", "Email", (), seed.EMAIL),
    ("p_phone", "tel", "Phone", (), seed.PHONE),
    ("p_linkedin", "url", "LinkedIn profile", (), seed.LINKEDIN),
    ("p_work", "select", "Are you legally authorized to work in the United Kingdom?", ("Yes", "No"), "Yes"),
]
# Labels the engine's gate suite (backend/tests/fixtures/engine/english_gate.json) holds as NOT plain questions: before the gate each was
# answered from a fact of the candidate that has nothing to do with what the label asks (a dialling code got the country, a heading got a
# graduation date, 'Original Name' got the candidate's name ...). On a real form they are labels of ordinary controls.
COMPLEX = [
    ("c_dial", "text", "Country dialing code", ()),
    ("c_prefix", "text", "Phone prefix", ()),
    ("c_study", "text", "Country of study", ()),
    ("c_berlin", "text", "Job location - Berlin", ()),
    ("c_search", "text", "Search current location", ()),
    ("c_november", "text", "Available from November", ()),
    ("c_british", "radio", "Do you have British citizenship?", ("Yes", "No")),
    ("c_spain", "checkbox", "I am a citizen of Spain.", ()),
    ("c_visa", "text", "Student/Graduate Visa (with work rights until 2028)", ()),
    ("c_grad", "text", "Graduate Software Engineer", ()),
    ("c_bsc", "text", "BSc Computer Science at North University", ()),
    ("c_original", "text", "Original Name", ()),
    ("c_langs", "text", "Spanish and British", ()),
    ("c_email_note", "text", "Email access was not authorized", ()),
]


@scenario(needs=SUGGEST, timeout=240)
async def scenario_plain_question_gate_leaves_complex_wordings_to_the_person_in_the_real_page(env: Env) -> None:
    """On one real form, the plain questions are answered from the candidate's verified facts and fourteen wordings that are NOT plain
    questions (a dialling code, a place of study, a heading, somebody else's name ...) are 'needs you': a quiet chip, nothing to insert,
    nothing written by Tab, nothing recorded as an answer."""
    controls = [{"id": cid, "kind": kind, "label": label, "options": options} for cid, kind, label, options, _ in PLAIN] + \
               [{"id": cid, "kind": kind, "label": label, "options": options} for cid, kind, label, options in COMPLEX]
    env.fixtures.generated["gate.html"] = pages.form_page(controls)
    await env.connected()
    page = await env.open("gate.html", activate=True)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    chip = Chip(page)
    for cid, kind, label, options, shown in PLAIN:
        await page.locator(f"#{cid}").focus()
        await chip.expect_chip(state="VERIFIED", value=shown, timeout=millis(30_000))
        check(f"plain: {label!r} is answered ({shown!r})", True)
    for cid, kind, label, options in COMPLEX:
        await page.locator(f"#{cid}").focus()
        await chip.expect_chip(state="NEEDS_YOU", insert="never", timeout=millis(30_000))
        check(f"not plain: {label!r} is left to the person (a quiet chip, no value)", await chip.el.first.get_attribute("data-quiet") == "true"
              and await chip.value.count() == 0, await chip.describe())
        await ui.tab(page)  # Tab only moves on: nothing to insert
    events = await ui.synthetic_events(page)
    wrote = {event.get("id") or event.get("name") for event in events}
    check("Tab wrote into none of the complex wordings", not wrote & {cid for cid, *_ in COMPLEX}, sorted(wrote))
    texts = [cid for cid, kind, *_ in COMPLEX if kind == "text"]
    check("and no value of the candidate sits in any of them", [await ui.field_value(page, f"#{cid}") for cid in texts] == [""] * len(texts))
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- the metamorphic sample
SAMPLE_PERSON = "uk_citizen"  # a London role: the page's JSON-LD (generated pages) is London, like the candidate's
SAMPLE_SIZE = 20
RENDERABLE_KINDS = {"text", "email", "tel", "url", "number", "date", "textarea", "select", "radio", "checkbox"}


@dataclass
class Pick:
    case: object
    control_id: str
    base_id: str
    plain: bool


def renderable(case) -> bool:
    """Can this case be a real control with the label, kind and options it has (nothing the scanner does not send, no twin controls)?"""
    rendering = case.rendering
    if rendering is None:
        return True
    return not rendering.companions and set(rendering.extra) <= pages.SUPPORTED_EXTRA and (rendering.kind in {None, *RENDERABLE_KINDS}) \
        and (rendering.extra.get("type") in {None, "hidden", "password"})


def stratified_sample(cases: list, size: int) -> list:
    """One renderable case of each wrapper group (the groups sorted by name; within a group the first by id), then the groups once more
    until ``size``: every kind of wrapper is represented before any is repeated, and the choice never depends on a random number."""
    groups: dict[str, list] = defaultdict(list)
    for case in sorted((c for c in cases if c.rendering is not None and renderable(c)), key=lambda c: c.cid):
        groups[case.rendering.group].append(case)
    chosen, turn = [], 0
    while len(chosen) < size and any(groups.values()):
        for name in sorted(groups):
            if groups[name] and len(chosen) < size:
                chosen.append(groups[name].pop(0))
        turn += 1
    return chosen


async def obs_of(chip: Chip, runner):
    """What the chip on screen says, in the vocabulary of the suite's ``Obs`` (the engine's result object is never read)."""
    if await chip.el.count() == 0:
        return runner.Obs("SKIP", "never", None, False, False, None, None, "")
    attributes = await chip.attributes()
    state, insert = attributes["data-state"], attributes["data-insert"]
    insertable = state in {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}
    value = await chip.shown_value() if insertable else None
    return runner.Obs(state, insert or "never", value or None, insertable and attributes["data-tone"] == "locked",
                      state in {"INFERRED", "GENERATED_DRAFT"}, None, None, "", leave_blank=state == "SENSITIVE_BLANK")


@scenario(needs=SUGGEST, timeout=420)
async def scenario_metamorphic_sample_of_twenty_labels_through_the_real_page(env: Env) -> None:
    """Twenty wrapped labels of the held-out metamorphic suite (one per kind of wrapper, in a fixed order) and their plain bases, as one
    real form for the suite's UK citizen: every control is focused, the chip is read, and the suite's own properties are applied to what a
    person sees (structure, protection, consent, injection, oracle, benign wrappers change nothing, strict ones are never silent)."""
    bases, properties, _people, plan, runner = metamorphic()
    cases = plan.by_person(plan.build_plan())[SAMPLE_PERSON]
    picked = stratified_sample(cases, SAMPLE_SIZE)
    check(f"{SAMPLE_SIZE} renderable wrapped cases were found in {len({c.rendering.group for c in picked})} wrapper groups",
          len(picked) == SAMPLE_SIZE, len(picked))
    print("    sample: " + "; ".join(f"{c.rendering.wid}[{c.lang}]" for c in picked), flush=True)
    controls, plain_of, index = [], {}, 0

    def add(case, plain_case):
        nonlocal index
        index += 1
        base = bases.BY_ID[case.base]
        rendering = case.rendering
        text = base.text(case.lang) if rendering is None else rendering.text
        kind = base.kind if (rendering is None or rendering.kind is None) else rendering.kind
        options = base.opts(case.lang) if (rendering is None or rendering.options is None) else rendering.options
        extra = {} if rendering is None else dict(rendering.extra)
        control = {"id": f"m{index}", "kind": kind, "label": text, "options": tuple(options) or bases.YES_NO[case.lang], "extra": extra}
        controls.append(control)
        return control["id"]

    for case in picked:  # the plain base of every sampled case (once), then the wrapped case
        key = (case.base, case.lang)
        if key not in plain_of:
            plain = runner.Case(f"{SAMPLE_PERSON}|{case.base}|{case.lang}|base", SAMPLE_PERSON, case.base, case.lang)
            plain_of[key] = (plain, add(plain, True))
    placed = [(case, add(case, False)) for case in picked]
    env.fixtures.generated["sample.html"] = pages.form_page(controls)
    await env.start_backend(seed_profile=True, api_seed=False, llm=False,
                                      extra_env={"MERIDIAN_E2E_PERSON": SAMPLE_PERSON, "MERIDIAN_E2E_DISCLOSURES": "use_confirmed"})
    await env.connected()
    page = await env.open("sample.html", activate=True)
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    chip = Chip(page)

    skipped = runner.Obs("SKIP", "never", None, False, False, None, None, "")

    async def look(control_id: str, kind: str, inert: bool):
        target = page.locator(f"#{control_id}")
        if not await target.is_visible():  # a hidden input is nobody's question: no chip, nothing to scan
            return skipped
        await target.scroll_into_view_if_needed(timeout=10_000)
        try:
            await target.focus(timeout=3_000)
        except Exception:  # noqa: BLE001 - a disabled control cannot take focus: it has no chip either
            return skipped
        if inert:
            await ui.settle(page, 700)
            return await obs_of(chip, runner)
        try:
            await chip.expect_chip(timeout=millis(20_000))
        except AssertionError:
            return skipped
        await ui.settle(page, 250)
        return await obs_of(chip, runner)

    base_obs = {}
    for (base_id, lang), (case, control_id) in plain_of.items():
        base = bases.BY_ID[base_id]
        base_obs[(base_id, lang)] = await look(control_id, base.kind, False)
    violations = []
    for case, control_id in placed:
        rendering = case.rendering
        kind = rendering.kind or bases.BY_ID[case.base].kind
        obs = await look(control_id, kind, rendering.verdict == "inert")
        violations += properties.evaluate(case, obs, [], base_obs.get((case.base, case.lang)))
        print(f"    {rendering.wid:34} [{case.lang}] {obs.show():60} {properties.question_text(case)[:70]!r}", flush=True)
    for (base_id, lang), (case, control_id) in plain_of.items():
        violations += properties.evaluate(case, base_obs[(base_id, lang)], [], None)
    answered = sum(1 for obs in base_obs.values() if obs.insertable)
    check("the plain bases are not all refused (the sample is not passing by never answering)", answered >= 3, f"{answered} of {len(base_obs)} answered")
    for violation in violations:
        print(f"    VIOLATION {violation.prop} {properties.LABELS[violation.prop]} :: {violation.group} {violation.text!r} -> {violation.got} "
              f"(wanted {violation.expected}) {violation.detail}", flush=True)
    check("no property of the suite is violated by what the real page shows", not violations,
          [(v.prop, v.group, v.text[:50], v.got) for v in violations[:4]])
    written = {event.get("id") for event in await ui.synthetic_events(page)}
    check("looking at the page wrote nothing into it", not written, sorted(written))
    await ui.assert_never_submitted(page)
