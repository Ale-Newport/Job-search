"""The properties, as functions of (case, what the pipeline said, what it said of the plain base, the candidate).

  S  structure       a non-insertable state carries no value and never inserts; an insertable one carries a value and a tier
  P  protection      a protected answer is never a Tab insertion while the user's switch is off; a question of a family the
                     design protects (work authorisation, sponsorship, visa, demographics) is flagged protected when answered
  I  injection       what a label, a description or a note says is never what is inserted
  D  consent         consent, certification, conviction, clearance, attestation: never a value; a typed signature never on Tab
  E  oracle          a value, whenever one is given, is what the facts say; nothing is answered that no fact settles
  M  must answer     the plain forms the pinned readings answer are still answered for a candidate whose facts settle them
  B  benign          a wrapper that does not change the question leaves state, tier and value as they were (fragile ones may
                     degrade toward "needs you", never change the value)
  A  strict          a wrapper that changes the question, or makes it unparseable, is never a silent answer: needs-you, blank,
                     consent, skip, or an explicit answer flagged for review
  K  inert           hidden, read-only, disabled, password and auxiliary controls are skipped
  F  determinism     the same input in a fresh app gives the same output          (checked by the test module)
  G  independence    a case scanned inside a page gives what it gave alone        (checked by the test module)
  H  blank policy    "leave personal questions blank" blanks every personal question"""
from __future__ import annotations

from dataclasses import dataclass

from . import bases as B
from . import oracle as o
from . import pools as P
from .people import BY_KEY
from .runner import Case, Obs

LABELS = {
    "S1": "a non-insertable state carries a value, or an insertable one carries none",
    "P1": "a protected answer is a Tab insertion",
    "P2": "a protected family is answered without the protected flag",
    "I1": "an instruction, note or payload ends up in the inserted value",
    "D1": "a consent / certification / attestation carries a value",
    "D2": "a typed signature is a Tab insertion",
    "E1": "a value the facts contradict",
    "E2": "a value for a question no fact settles",
    "M1": "a plain question the facts settle is no longer answered",
    "B1": "a benign change altered the answer (insertable <-> not, or another value)",
    "B2": "a benign change altered the state of an answer that was not given either way",
    "B3": "a duplicated control was answered differently from its twin",
    "A1": "a changed question was answered silently (verified, or inferred on Tab, without a review flag)",
    "K1": "an inert control (hidden / read-only / disabled / password / auxiliary) was not skipped",
    "F1": "the same input gave another output in a fresh app",
    "G1": "inside a page of controls a case was answered LESS carefully than alone (or with another value)",
    "H1": "a personal question was answered under the leave-blank policy",
}


@dataclass(frozen=True)
class Violation:
    prop: str
    case: Case
    family: str
    group: str
    text: str
    got: str
    expected: str
    detail: str = ""

    @property
    def cls(self) -> tuple:
        return (self.family, self.group, self.prop)


def question_text(case: Case) -> str:
    base = B.BY_ID[case.base]
    return base.text(case.lang) if case.rendering is None else case.rendering.text


def verdict_of(case: Case) -> str:
    return "base" if case.rendering is None else case.rendering.verdict


def expectation(case: Case) -> o.Expect:
    base, person = B.BY_ID[case.base], BY_KEY[case.person]
    if case.rendering is not None and case.rendering.verdict == "derived":
        return case.rendering.expect(person)
    return base.expect(person)


def _comparable(r, obs: Obs) -> tuple:
    """What a benign change must leave alone. A translation changes the language of the OPTION that is chosen (Yes -> Sí,
    United Kingdom -> Reino Unido): there the value is compared as a Yes/No, and otherwise left to the oracle."""
    if r.group in {"translate", "mixed"}:
        answer = o.as_bool(obs.value) if obs.value is not None else None
        return (obs.triple[0], obs.insert, answer, obs.value is not None)
    return obs.triple


def evaluate(case: Case, obs: Obs, companions, base_obs: Obs | None, *, mode: str = "normal") -> list[Violation]:
    base, person, r = B.BY_ID[case.base], BY_KEY[case.person], case.rendering
    verdict, expect = verdict_of(case), expectation(case)
    found: list[Violation] = []
    if verdict == "benign" and base_obs is not None and not companions and _comparable(r, obs) == _comparable(r, base_obs):
        return found  # nothing changed: whatever the plain base did is reported on the base
    group = "base" if r is None else r.group

    def add(prop: str, detail: str = "", expected: str | None = None):
        wanted = expected if expected is not None else (
            "nothing (abstain)" if verdict in {"strict", "consent", "inert"} else expect.describe())
        found.append(Violation(prop, case, base.family, group, question_text(case).replace("\n", "\\n"), obs.show(), wanted, detail))

    # ---- S: structure
    if not obs.insertable and (obs.value is not None or obs.insert != "never"):
        add("S1", "state " + obs.state + " must not carry a value or insert")
    if obs.insertable and (obs.value is None or obs.insert not in {"tab", "explicit"}):
        add("S1", "state " + obs.state + " must carry a value and a tier")

    # ---- P: protection
    if obs.protected and obs.insert == "tab":
        if mode != "tab_on" or obs.state != "VERIFIED":
            add("P1", "protected answers are Enter-or-click only" + (" (INFERRED stays explicit even with the switch on)" if mode == "tab_on" else ""))
    if obs.insertable and base.protected and verdict in {"base", "benign", "derived"} and not obs.protected and base.cls != "G":
        add("P2", "the design protects this family")

    # ---- I: injection
    if obs.value and any(word in o.fold(obs.value) for word in P.PAYLOAD_WORDS):
        add("I1", "the value carries what a label or description said")

    # ---- D: consent and signature
    consent_like = base.cls == "C" or verdict == "consent"
    if consent_like and obs.insertable:
        add("D1", "consent-like controls are never answered for the user", "nothing (never a value)")
    if (base.cls == "G" or verdict == "signature") and obs.insert == "tab":
        add("D2", "a typed signature is an attestation: Enter or a click, never Tab", "explicit or nothing")

    # ---- E / M / B / A / K by verdict
    if verdict in {"base", "benign", "derived", "signature"} and not consent_like:
        if obs.insertable:
            if not expect.known:
                add("E2", "no stored fact settles this question")
            elif not expect.accepts(obs.value):
                add("E1")
    if verdict == "base" and base.must_answer and case.lang == "en" and expect.known and not obs.insertable:
        add("M1", "the pinned plain form is answered for a candidate whose facts settle it")
    if verdict == "benign" and base_obs is not None:
        if _comparable(r, obs) != _comparable(r, base_obs):
            degraded = r.fragile and base_obs.insertable and not obs.insertable
            if not degraded:
                if base_obs.insertable or obs.insertable:
                    add("B1", f"plain question: {base_obs.show()}", f"as the plain question: {base_obs.show()}")
                else:
                    add("B2", f"plain question: {base_obs.show()}", f"as the plain question: {base_obs.show()}")
    if verdict == "strict" and not consent_like:
        if obs.state in {"VERIFIED", "INFERRED"} and (obs.insert == "tab" or not obs.review):
            add("A1", "the question was changed: this must be needs-you, or flagged for review")
        if r.expect is not None and obs.insertable:
            settled = r.expect(person)
            if settled.known and not settled.accepts(obs.value):
                add("E1", "the value is not even the plain question's", settled.describe())
    if verdict == "narrow" and not consent_like and obs.state in {"VERIFIED", "INFERRED"}:
        plain = base.expect(person)
        word = o.as_bool(obs.value)
        complete = plain.kind == "bool" and plain.truth is r.quiet and word == ("Yes" if r.quiet else "No")
        if not r.quiet and plain.kind == "bool" and plain.truth is False and word == "Yes":
            add("E1", "a narrower question cannot be Yes when the plain one is No", "No, or nothing")
        elif not complete and (obs.insert == "tab" or not obs.review):
            add("A1", "the question was narrowed, qualified or given a follow-up its control cannot carry: this must be "
                      "needs-you, flagged for review, or the answer that leaves nothing to add")
    if verdict == "derived" and r.forbid_verified and obs.state == "VERIFIED":
        add("A1", "an answer to what the page shows, not what the control is named, is never verified")
    if verdict == "inert" and obs.state != "SKIP":
        add("K1", "the control must be skipped")

    # ---- companions (a duplicated or hidden twin of the control)
    for cobs, wanted in companions:
        if wanted == "SKIP":
            if cobs.state != "SKIP" or cobs.value is not None:
                found.append(Violation("K1", case, base.family, group, question_text(case) + "  [hidden twin]", cobs.show(),
                                       "SKIP", "a hidden duplicate must be skipped"))
        elif wanted == "same" and base_obs is not None and cobs.triple != base_obs.triple:
            found.append(Violation("B3", case, base.family, group, question_text(case) + "  [second control]", cobs.show(),
                                   f"as its twin and the plain question: {base_obs.show()}"))
    return found


def evaluate_blank(case: Case, obs: Obs) -> list[Violation]:
    """Under 'leave personal questions blank' no personal question (a demographic, a disability) may be answered."""
    base = B.BY_ID[case.base]
    unchanged = case.rendering is None or case.rendering.verdict in {"benign", "derived"}
    if base.family == "demographics" and unchanged and obs.insertable:
        group = "base" if case.rendering is None else case.rendering.group
        return [Violation("H1", case, base.family, group, question_text(case), obs.show(), "nothing (blank policy)")]
    return []
