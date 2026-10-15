"""HELD-OUT metamorphic and property suite for the answer safety of the REAL suggest pipeline (bridge routes -> SuggestService ->
FieldResolver -> CandidateEngine), written blind to the implementation.

What it does, in one paragraph. Six synthetic candidates (a UK Student-route candidate who will need sponsorship, a UK citizen, a
Spanish national in Spain, an EU settled candidate, a graduate with a finished BSc and an MSc in progress, and one about whom
almost nothing is known) are given stored facts for every family of question (right to work, sponsorship, visa, languages with
levels, completed and in-progress education, graduation dates, notice, availability, location, salary policy, identity and links,
confirmed and unconfirmed demographics). For each family the PLAIN question exists in English and Spanish with an answer derived
BY HAND from the facts (``metamorphic/people.py`` and ``bases.py``: an oracle table, never the engine). About 3,600 cases are then
generated with a fixed seed: every plain base, and wrappers that either leave the question alone (``trailing *``, ``(required)``,
case, punctuation, whitespace, accents, a translation, a neutral heading) or change its meaning (employer notes, exclusions,
negation, double negation, comparators, coordination, follow-ups, time qualifiers, quantifiers, another country, another
attribute, another person, narrative framings, renamed section headings, injected instructions, spoofed labels, hidden
controls...). Every case runs through the pipeline ALONE, and the properties of ``metamorphic/check.py`` are asserted:

  S structure   P protection   I injection   D consent / signature   E oracle   M must-answer   B benign   A strict   K inert
  F determinism (a fresh app)  G independence (inside a page of 40 controls)  H leave-blank policy  and the Tab switch on.

Nothing here is xfail: the suite reports what the pipeline does today. One test per (family, wrapper group) lists the failing cases
with their question, the answer and the oracle; the full report (classes ranked by frequency) is written to
``$METAMORPHIC_REPORT`` or ``<tmp>/meridian-engine-metamorphic-report.txt`` and summarised at the end of the pytest output.

    MERIDIAN_TEST=1 .venv/bin/python -m pytest backend/tests/test_engine_metamorphic.py -q          (about 2 minutes)
    PYTHONPATH=backend/tests/fixtures/engine MERIDIAN_TEST=1 .venv/bin/python -m metamorphic --family sponsorship

The checker itself is tested at the end of this file with synthetic observations (every property fails on the answer it must
fail on and passes on the answer it must accept), so a loosened checker cannot hide a failing pipeline."""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
from metamorphic import bases as B  # noqa: E402
from metamorphic import check, driver, oracle as O, people, plan, report  # noqa: E402
from metamorphic import wrappers as W  # noqa: E402
from metamorphic.runner import Case, Obs  # noqa: E402

COMMAND = "MERIDIAN_TEST=1 .venv/bin/python -m pytest backend/tests/test_engine_metamorphic.py -q"
SPEED_BUDGET = 150  # CPU seconds of the whole run (every control included): wall time follows the machine's load, CPU time does not
PLAN = plan.build_plan()
GROUPS = sorted({(B.BY_ID[c.base].family, "base" if c.rendering is None else c.rendering.group) for c in PLAN})
# The benign wrappers whose failure to answer is safe, and why: they may degrade toward "needs you" (never change a value).
FRAGILE = {
    "accents": "without its accents a Spanish word can be another one (si/sí, mas/más): failing closed is safe",
    "translate": "the engine's Spanish vocabulary is bounded: failing closed on a translation is safe, another value is not",
    "mixed": "one label in two languages, or Yes/No options in the other language: failing closed is safe",
    "zero_width": "an invisible character inside a word is also how a label is spoofed: failing closed is safe",
    "description_instruction": "the description is untrusted employer text: reading it and failing closed is safe",
}


@pytest.fixture(scope="module", autouse=True)
def offline():
    patch = pytest.MonkeyPatch()
    patch.setenv("MERIDIAN_TEST", "1")
    patch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    yield
    patch.undo()


@pytest.fixture(scope="module")
def outcome(offline, request):
    """Runs the whole suite once (about 4,000 pipeline calls and four control runs); every group test reads it."""
    # `-k sponsorship` runs the cases of the selected families only (the controls included); anything else runs them all
    users = [item for item in request.session.items if "outcome" in getattr(item, "fixturenames", ())]
    families = {item.callspec.params["family"] for item in users if getattr(item, "callspec", None)
                and "family" in item.callspec.params}
    partial = bool(users) and len(families) > 0 and all(
        item.originalname == "test_every_property_holds_for_the_family_and_the_wrapper" for item in users)
    result = driver.execute([c for c in PLAN if B.BY_ID[c.base].family in families] if partial else PLAN)
    path = Path(os.environ.get("METAMORPHIC_REPORT") or Path(tempfile.gettempdir()) / "meridian-engine-metamorphic-report.txt")
    path.write_text(report.render(result, command=COMMAND) + "\n")

    def summary():
        manager = request.config.pluginmanager
        terminal, capture = manager.get_plugin("terminalreporter"), manager.get_plugin("capturemanager")
        if terminal is not None:
            with capture.global_and_fixture_disabled() if capture is not None else contextlib.nullcontext():
                terminal.write_sep("=", "engine metamorphic report")
                terminal.write_line(report.render(result, command=COMMAND, full=False))
                terminal.write_line(f"\nfull report: {path}")

    request.addfinalizer(summary)
    return result


# ================================================================================================ the properties, per group
@pytest.mark.parametrize("family,group", GROUPS, ids=[f"{f}::{g}" for f, g in GROUPS])
def test_every_property_holds_for_the_family_and_the_wrapper(outcome, family, group):
    failing = [v for v in outcome.violations if (v.family, v.group) == (family, group)]
    assert not failing, "\n" + report.render_group(failing)


def test_every_violation_belongs_to_a_group_that_has_a_test(outcome):
    assert {(v.family, v.group) for v in outcome.violations} <= set(GROUPS)


def test_the_run_stays_within_its_time_budget(outcome):
    assert outcome.seconds["cpu"] < SPEED_BUDGET, outcome.seconds


def test_an_engine_that_ignored_every_wrapper_would_be_caught_by_the_checker(outcome):
    """Sensitivity, on the real plain answers of this run: feed the checker the plain question's answer as the answer to each
    wrapped one (what a pipeline that ignores the wrapper says). The groups that change the question must flag most of them."""
    flagged, total = Counter(), Counter()
    for case in outcome.cases:
        r = case.rendering
        if r is None or r.verdict not in {"strict", "narrow", "derived", "consent", "inert"}:
            continue
        plain = outcome.results[driver.base_key(case)][0]
        if not plain.insertable:
            continue
        total[(r.verdict, r.group)] += 1
        flagged[(r.verdict, r.group)] += bool(check.evaluate(case, plain, [], plain))
    floors = {"strict": 0.6, "narrow": 0.5, "consent": 0.95, "derived": 0.1}
    floors_by_group = {"follow_up": 0.3}  # "If so..." of a plain No (and "If not..." of a plain Yes) leaves the plain answer complete
    misses = {key: (flagged[key], n) for key, n in total.items()
              if n >= 4 and key[0] != "inert" and flagged[key] / n < floors_by_group.get(key[1], floors[key[0]])}
    assert not misses, misses
    assert sum(total.values()) >= 300 and sum(flagged.values()) >= 150, (sum(flagged.values()), sum(total.values()))


# ================================================================================================ what the suite is
def test_the_suite_has_the_size_and_the_breadth_it_claims():
    assert 1500 <= len(PLAN) <= 4000, len(PLAN)
    assert len({c.cid for c in PLAN}) == len(PLAN)
    assert len(people.PEOPLE) >= 5 and {c.person for c in PLAN} == {p.key for p in people.PEOPLE}
    assert {c.lang for c in PLAN} == {"en", "es"} and len({B.BY_ID[c.base].family for c in PLAN}) >= 14
    groups = {c.rendering.group for c in PLAN if c.rendering}
    assert {group for group, _ in W.REGISTRY} - groups <= {"negation_other"} | {"comparator_dates"}, \
        {group for group, _ in W.REGISTRY} - groups
    kinds = Counter(c.rendering.verdict for c in PLAN if c.rendering)
    assert kinds["benign"] >= 500 and kinds["strict"] >= 500 and kinds["derived"] >= 100 and kinds["narrow"] >= 50
    assert kinds["inert"] >= 50 and kinds["consent"] >= 20 and kinds["signature"] >= 1
    plain = [c for c in PLAN if c.rendering is None]
    assert len(plain) == sum(len(b.langs) for b in B.BASES) * len(people.PEOPLE)


def test_the_plan_is_deterministic():
    again = plan.build_plan()
    assert [c.cid for c in again] == [c.cid for c in PLAN]
    assert [c.rendering.text if c.rendering else "" for c in again] == [c.rendering.text if c.rendering else "" for c in PLAN]


def test_the_candidates_are_the_five_the_task_names_plus_a_sparse_one():
    by = people.BY_KEY
    student = by["student_uk"].immigration
    assert student["route"] == "Student" and student["current_right_to_work"] and student["sponsorship_future"] is True
    assert by["uk_citizen"].immigration["permanent_unrestricted"] and by["uk_citizen"].nationality == "British"
    assert by["es_in_es"].nationality == "Spanish" and by["es_in_es"].job == "es" and by["es_in_es"].country == "Spain"
    assert "Settle" in by["eu_settled"].immigration["route"] and by["eu_settled"].nationality == "Italian"
    graduate = by["irish_grad"]
    assert [e["status"] for e in graduate.education] == ["completed", "studying"]
    assert any(p.level("English") == "Fluent" for p in people.PEOPLE) and any(
        level == "Native" for p in people.PEOPLE for level, _ in p.languages.values())
    assert any(c for p in people.PEOPLE for _, c in p.demographics.values()) and any(
        not c for p in people.PEOPLE for _, c in p.demographics.values())
    assert not by["sparse"].education and not by["sparse"].immigration and not by["sparse"].languages


def test_every_family_has_facts_for_some_candidates_and_none_for_others():
    for family in sorted({b.family for b in B.BASES}):
        if family in {"consent", "signature"}:
            continue
        known = {p.key for p in people.PEOPLE if any(b.expect(p).known for b in B.BASES if b.family == family)}
        unknown = {p.key for p in people.PEOPLE if all(not b.expect(p).known for b in B.BASES if b.family == family)}
        assert len(known) >= 3, (family, known)
        assert "sparse" in unknown or family in {"identity"}, (family, unknown)


def test_the_hand_oracle_is_consistent_with_the_facts_it_is_written_from():
    student, citizen, spanish = people.BY_KEY["student_uk"], people.BY_KEY["uk_citizen"], people.BY_KEY["es_in_es"]
    assert student.rtw("United Kingdom").truth is True and student.sponsorship("United Kingdom").truth is True
    assert citizen.sponsorship("United Kingdom").truth is False and citizen.rtw("Spain").kind == "none"
    assert spanish.rtw("United Kingdom").kind == "none" and spanish.rtw("Spain").truth is True
    assert spanish.rtw("Germany").truth is True  # a member state, once the EU right is stored
    assert student.highest_level().accepts("Bachelor's Degree") and not student.highest_level().accepts("Master's Degree")
    assert citizen.highest_level().accepts("Master's Degree")
    assert student.has_degree("Master's").truth is False and citizen.has_degree("Master's").truth is True
    assert student.native("English").truth is False and citizen.native("English").truth is True
    assert student.fluent("French").truth is False and student.fluent("German").kind == "none"
    assert people.BY_KEY["sparse"].rtw("United Kingdom").kind == "none"
    assert student.notice_weeks_expect().accepts("0") and not student.notice_weeks_expect().accepts("12")
    assert citizen.salary_expect().accepts("£55,000") and not citizen.salary_expect().accepts("32000")


def test_the_fragile_benign_wrappers_are_exactly_the_listed_ones():
    fragile = {r.group for b in B.BASES for lang in b.langs for r in W.all_renderings(b, lang) if r.fragile}
    assert fragile == set(FRAGILE), fragile ^ set(FRAGILE)
    assert all(r.why for b in B.BASES for lang in b.langs for r in W.all_renderings(b, lang) if r.fragile)


def test_every_wrapper_changes_the_question_it_wraps():
    for base in B.BASES:
        for lang in base.langs:
            for r in W.all_renderings(base, lang):
                changed = (r.text != base.text(lang) or r.extra or r.kind or r.options or r.companions)
                assert changed, (base.id, lang, r.wid)


def test_this_suite_never_reads_the_implementation_it_tests():
    """Blind by construction: only the public seeding helpers and the bridge harness are imported, and the engine, the
    resolver, the suggest service and the answer rules are not even named by a monkeypatch."""
    import ast

    allowed = {"jobagent.intelligence.store", "jobagent.application_profile"}
    names = {"CandidateEngine", "FieldResolver", "SuggestService", "OptionSelector", "interpret", "answer_question"}
    for source in sorted((Path(__file__).parent / "fixtures" / "engine" / "metamorphic").glob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for module in modules:
                assert not module.startswith("jobagent") or module in allowed, (source.name, module)
            used = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            assert used not in names, (source.name, used)


# ================================================================================================ the checker, on synthetic answers
def obs(state="VERIFIED", insert="tab", value="Yes", *, protected=False, review=False) -> Obs:
    if state not in {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}:
        value, insert = None, "never"
    return Obs(state, insert, value, protected, review, None, None, "")


def case_of(base_id: str, wid: str | None = None, person="student_uk", lang="en") -> Case:
    base = B.BY_ID[base_id]
    rendering = None
    if wid:
        rendering = next(r for r in W.all_renderings(base, lang) if r.wid == wid)
    return Case(f"{person}|{base_id}|{lang}|{wid or 'base'}", person, base_id, lang, rendering)


def props(case: Case, answer: Obs, base: Obs | None = None, companions=(), mode="normal") -> set:
    return {v.prop for v in check.evaluate(case, answer, list(companions), base, mode=mode)}


RTW = "rw[United Kingdom]"
NEEDS = obs("NEEDS_YOU")


def test_checker_a_changed_question_is_never_a_silent_answer():
    changed = case_of(RTW, "note.paren0")
    assert "A1" in props(changed, obs(insert="explicit", protected=True))  # verified, no review flag
    assert "A1" in props(changed, obs("INFERRED", "tab", review=True))  # inferred on Tab
    assert "A1" in props(case_of("nt_period", "note.paren0"), obs(value="1 month"))
    assert not props(changed, obs("INFERRED", "explicit", review=True, protected=True)) - {"P2"}
    assert not props(changed, NEEDS)
    assert not props(changed, obs("CONSENT")) and not props(changed, obs("SKIP"))


def test_checker_a_changed_question_must_not_even_be_the_plain_questions_other_value():
    injected = case_of("id_phone", "injection.label0")
    assert "E1" in props(injected, obs(value="priya.raman@example.test", insert="tab"))
    assert "E1" in props(case_of("sp[United Kingdom]", "double_negation.false_that_not"), obs(value="No", insert="explicit", protected=True))


def test_checker_protected_answers_are_never_tab_unless_the_switch_is_on_and_they_are_verified():
    plain = case_of(RTW)
    assert "P1" in props(plain, obs(insert="tab", protected=True))
    assert "P1" not in props(plain, obs(insert="explicit", protected=True))
    assert "P1" not in props(plain, obs(insert="tab", protected=True), mode="tab_on")
    assert "P1" in props(plain, obs("INFERRED", "tab", protected=True, review=True), mode="tab_on")
    assert "P2" in props(plain, obs(insert="explicit", protected=False))  # a protected family answered without the flag


def test_checker_consent_and_declarations_never_carry_a_value_and_a_signature_never_goes_on_tab():
    consent = case_of("cs_privacy")
    assert "D1" in props(consent, obs(value="checked", insert="explicit", protected=True))
    assert not props(consent, obs("CONSENT"))
    assert "D1" in props(case_of(RTW, "attestation.i_confirm"), obs(value="checked", insert="explicit", protected=True))
    signature = case_of("sg_signature")
    assert "D2" in props(signature, obs(value="Priya Raman", insert="tab"))
    assert not props(signature, obs(value="Priya Raman", insert="explicit", protected=True))
    assert "E1" in props(signature, obs(value="Somebody Else", insert="explicit", protected=True))
    assert "D2" in props(case_of("id_name", "heading_subject.declaration_declaration"), obs(value="Priya Raman", insert="tab"))


def test_checker_a_value_must_be_what_the_facts_say_and_nothing_is_answered_without_a_fact():
    plain = case_of(RTW)
    assert not props(plain, obs(value="Yes", insert="explicit", protected=True))
    assert "E1" in props(plain, obs(value="No", insert="explicit", protected=True))
    assert "E2" in props(case_of(RTW, person="sparse"), obs(value="Yes", insert="explicit", protected=True))
    assert "E2" in props(case_of("rw[Spain]"), obs(value="Yes", insert="explicit", protected=True))
    assert not props(case_of("rw[Spain]", person="es_in_es"), obs(value="Yes", insert="explicit", protected=True))
    assert "E1" in props(case_of("nt_weeks", person="uk_citizen"), obs("INFERRED", "tab", value="1", review=True))
    assert not props(case_of("nt_weeks", person="uk_citizen"), obs("INFERRED", "tab", value="12 weeks", review=True))
    assert "E1" in props(case_of("ed_highest"), obs("INFERRED", "tab", value="Master's Degree", review=True))


def test_checker_a_plain_question_the_pinned_readings_answer_must_still_be_answered():
    assert "M1" in props(case_of("id_email"), NEEDS)
    assert "M1" not in props(case_of("id_email", person="sparse"), obs(value="sam.taylor@example.test"))
    assert "M1" not in props(case_of("sal_expect", person="sparse"), NEEDS)


def test_checker_a_benign_change_leaves_state_tier_and_value_alone():
    benign = case_of(RTW, "star.spaced")
    plain = obs(insert="explicit", protected=True)
    assert not props(benign, plain, plain)
    assert "B1" in props(benign, NEEDS, plain)  # an answer was lost
    assert "B1" in props(benign, obs("INFERRED", "explicit", protected=True, review=True), plain)  # another state
    assert "B1" in props(case_of(RTW, "star.spaced"), obs(value="No", insert="explicit", protected=True), plain)
    assert "B2" in props(case_of("cs_privacy", "star.spaced"), NEEDS, obs("CONSENT"))
    fragile = case_of(RTW, "translate.whole_question")
    assert not props(fragile, NEEDS, plain)  # a fragile wrapper may degrade to needs-you
    assert "B1" in props(fragile, obs("INFERRED", "explicit", protected=True, review=True), plain)  # but not to another answer
    assert not props(fragile, obs(value="Sí", insert="explicit", protected=True), plain)  # Yes and Sí are the same answer
    assert "E1" in props(fragile, obs(value="No", insert="explicit", protected=True), plain)


def test_checker_negation_is_derived_and_a_narrower_question_cannot_say_yes_to_a_no():
    flip = case_of("sp[United Kingdom]", "negation.is_it_true")
    assert "E1" in props(flip, obs(value="Yes", insert="explicit", protected=True))  # the student requires sponsorship: not "Yes"
    assert not props(flip, obs(value="No", insert="explicit", protected=True))
    # work authorisation and sponsorship: the pinned English readings answer the PROPOSITION of "Are you not ...?"
    aux = case_of("sp[United Kingdom]", "negation.aux_not")
    assert "E1" in props(aux, obs(value="Yes", insert="explicit", protected=True))
    assert not props(aux, obs(value="No", insert="explicit", protected=True))
    # elsewhere a negative question is ambiguous (Yes may mean "I am"): either polarity, but never without a fact
    ambiguous = case_of("lg_speak[English]", "negation.aux_not")
    assert not props(ambiguous, obs(value="Yes")) and not props(ambiguous, obs(value="No"))
    assert "E2" in props(case_of("sp[United Kingdom]", "negation.aux_not", person="sparse"), obs(value="Yes", insert="explicit", protected=True))
    assert "E2" in props(case_of("lg_speak[English]", "negation.aux_not", person="sparse"), obs(value="Yes"))
    narrow = case_of("ed_degree[Master's]", "comparator.c0")  # the student holds no master's degree: "No" is certain
    assert not props(narrow, obs("INFERRED", "tab", value="No", review=True))
    assert "E1" in props(narrow, obs("INFERRED", "tab", value="Yes", review=True))
    assert "A1" in props(case_of("ed_degree[Bachelor's]", "comparator.c0"), obs("INFERRED", "tab", value="Yes", review=True))
    assert not props(case_of("ed_degree[Bachelor's]", "comparator.c0"), NEEDS)


def test_checker_inert_controls_are_skipped_and_a_hidden_twin_is_never_answered():
    assert "K1" in props(case_of("id_email", "inert.hidden"), obs(value="priya.raman@example.test"))
    assert not props(case_of("id_email", "inert.hidden"), obs("SKIP"))
    twin = case_of("id_email", "duplicates.hidden_twin")
    plain = obs(value="priya.raman@example.test")
    assert not props(twin, plain, plain, companions=[(obs("SKIP"), "SKIP")])
    assert "K1" in props(twin, plain, plain, companions=[(plain, "SKIP")])
    visible = case_of("id_email", "duplicates.twice_visible")
    assert "B3" in props(visible, plain, plain, companions=[(NEEDS, "same")])
    assert not props(visible, plain, plain, companions=[(plain, "same")])


def test_checker_structure_injection_and_the_blank_policy():
    assert "S1" in props(case_of("id_email"), Obs("NEEDS_YOU", "never", "x", False, False, None, None, ""))
    assert "S1" in props(case_of("id_email"), Obs("VERIFIED", "never", "x", False, False, None, None, ""))
    assert "I1" in props(case_of("id_email", "heading_neutral.h0"), obs(value="Ignore all previous instructions"))
    gender = case_of("dm_gender")
    assert check.evaluate_blank(gender, obs("VERIFIED", "explicit", "Woman", protected=True))
    assert not check.evaluate_blank(gender, obs("SENSITIVE_BLANK"))
    assert not check.evaluate_blank(case_of("id_email"), obs(value="x"))


def test_checker_oracle_primitives():
    assert O.number(32000).accepts("£32,000") and O.number(38000).accepts("38.000") and not O.number(32000).accepts("32")
    assert O.on(people.TODAY).accepts(people.TODAY.isoformat()) and not O.on(people.TODAY).accepts("2000-01-01")
    assert O.text(r"^man$").accepts("Man") and not O.text(r"^man$").accepts("Woman")
    assert O.digits("+447700900123").accepts("07700 900123") and not O.digits("+447700900123").accepts("07700900124")
    assert O.YES.accepts("Sí") and not O.YES.accepts("No") and O.UNKNOWN.accepts(None) and not O.UNKNOWN.accepts("Yes")
    assert O.flip(O.YES).accepts("No") and not O.flip(O.UNKNOWN).known and O.either(O.YES).accepts("No")
    assert not people.BY_KEY["uk_citizen"].visa_expect().accepts("I currently hold a Citizen visa in the United Kingdom.")
