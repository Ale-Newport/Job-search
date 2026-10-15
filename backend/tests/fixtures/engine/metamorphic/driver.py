"""Runs the whole suite once: the main plan (every case alone), then four control runs of the SAME cases or samples of them
in other circumstances (a fresh app: determinism; a page of 40 controls: independence; the leave-blank policy; the user's
"Tab inserts protected answers" switch), and evaluates the properties."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import bases as B
from . import check, plan
from .runner import Case, Obs, run_all


@dataclass
class Outcome:
    cases: list
    results: dict
    violations: list
    seconds: dict = field(default_factory=dict)
    controls: dict = field(default_factory=dict)  # name -> number of cases compared
    notes: list = field(default_factory=list)  # observations that are not violations

    @property
    def violated_ids(self) -> set:
        return {v.case.cid for v in self.violations}


def base_key(case: Case) -> str:
    return f"{case.person}|{case.base}|{case.lang}|base"


def every(cases: list, step: int, offset: int = 0) -> list:
    return [c for n, c in enumerate(sorted(cases, key=lambda c: c.cid)) if n % step == offset]


TIER = {"never": 0, "explicit": 1, "tab": 2}


def less_cautious(inside: Obs, alone: Obs) -> bool:
    """A page may make an answer MORE careful (a name next to a signature becomes Enter-only); it may never make it less careful
    or another value."""
    if inside.insertable and not alone.insertable:
        return True
    if inside.insertable and alone.insertable and inside.value != alone.value:
        return True
    return TIER[inside.insert] > TIER[alone.insert] or (alone.protected and not inside.protected) or \
        (alone.review and not inside.review)


def same_output(a: Obs, b: Obs) -> bool:
    return (a.triple, a.protected, a.review, a.why) == (b.triple, b.protected, b.review, b.why)


def execute(cases: list | None = None, *, determinism_step=10, independence_step=8, page=40, log=None) -> Outcome:
    log = log or (lambda message: None)
    cases = cases if cases is not None else plan.build_plan()
    seconds = {}
    cpu_began = time.process_time()
    began = time.perf_counter()
    results = run_all(plan.by_person(cases), batch=1)
    seconds["main"] = time.perf_counter() - began
    log(f"main plan: {len(cases)} cases in {seconds['main']:.1f}s")

    violations: list = []
    for case in cases:
        obs, companions = results[case.cid]
        reference = results.get(base_key(case))
        violations += check.evaluate(case, obs, companions, reference[0] if reference else None)

    controls = {}
    outcome_notes: list = []
    # ---- F: the same input in a fresh app
    began = time.perf_counter()
    sample = every(cases, determinism_step)
    again = run_all(plan.by_person(sample), batch=1)
    for case in sample:
        first, second = results[case.cid][0], again[case.cid][0]
        if not same_output(first, second):
            violations.append(check.Violation("F1", case, B.BY_ID[case.base].family, "base" if case.rendering is None else case.rendering.group,
                                              check.question_text(case), second.show(), f"as the first time: {first.show()}"))
    controls["determinism"] = len(sample)
    seconds["determinism"] = time.perf_counter() - began

    # ---- G: inside a page of controls
    began = time.perf_counter()
    sample = every([c for c in cases if not (c.rendering and c.rendering.companions)], independence_step, offset=3)
    paged = run_all(plan.by_person(sample), batch=page)
    cautious = 0
    for case in sample:
        alone, inside = results[case.cid][0], paged[case.cid][0]
        if alone.triple != inside.triple:
            if less_cautious(inside, alone):
                violations.append(check.Violation("G1", case, B.BY_ID[case.base].family, "base" if case.rendering is None else case.rendering.group,
                                                  check.question_text(case), inside.show(), f"as alone: {alone.show()}"))
            else:
                cautious += 1
    if cautious:
        outcome_notes.append(f"inside a page of controls {cautious} of {len(sample)} answers became MORE careful than alone "
                             "(for instance a name field next to a typed signature became Enter-only): reported, not a violation")
    controls["independence"] = len(sample)
    seconds["independence"] = time.perf_counter() - began

    # ---- H: the leave-blank policy blanks every personal question
    began = time.perf_counter()
    sample = [c for c in cases if B.BY_ID[c.base].family == "demographics"]
    blanked = run_all(plan.by_person(sample), batch=1, disclosures="leave_blank")
    for case in sample:
        violations += check.evaluate_blank(case, blanked[case.cid][0])
    controls["leave_blank"] = len(sample)
    seconds["leave_blank"] = time.perf_counter() - began

    # ---- P: the user's "Tab inserts protected answers" switch is on
    began = time.perf_counter()
    protected = [c for c in cases if B.BY_ID[c.base].protected or B.BY_ID[c.base].cls in {"C", "G"}]
    wanted = {c.cid: c for c in every([c for c in protected if c.rendering is not None], 3)}
    for case in list(wanted.values()):
        wanted[base_key(case)] = next((c for c in cases if c.cid == base_key(case)), None)
    sample = [c for c in wanted.values() if c is not None] + [c for c in protected if c.rendering is None]
    sample = list({c.cid: c for c in sample}.values())
    switched = run_all(plan.by_person(sample), batch=1, tab_protected=True)
    known = {(v.prop, v.case.cid) for v in violations}
    for case in sample:
        obs, companions = switched[case.cid]
        reference = switched.get(base_key(case))
        for v in check.evaluate(case, obs, companions, reference[0] if reference else None, mode="tab_on"):
            if (v.prop, v.case.cid) not in known:  # what the switch changed, not what it left as it was
                violations.append(check.Violation(v.prop, v.case, v.family, v.group, v.text, v.got, v.expected,
                                                  (v.detail + " " if v.detail else "") + "[with the Tab switch on]"))
    controls["tab_switch"] = len(sample)
    seconds["tab_switch"] = time.perf_counter() - began
    seconds["total"] = sum(seconds.values())
    seconds["cpu"] = time.process_time() - cpu_began  # the whole process: stable when the machine is busy
    return Outcome(cases, results, violations, seconds, controls, outcome_notes)
