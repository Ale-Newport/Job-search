#!/usr/bin/env python3
"""Measure what Laya and the local writing model (Ollama) actually do for the browser assistant (docs/EXTENSION_DESIGN.md 5.4a).

    .venv/bin/python scripts/bench_local_assist.py [choice|write|all]

Needs both engines RUNNING on this Mac (Settings > AI starts Laya; Ollama is the app Meridian manages, or any local one with the model
``qwen2.5:7b-instruct-q4_K_M``): nothing is started here and nothing leaves loopback. The unit and end-to-end tests script the models;
this is the only place that asks the real ones, so it is how a change of model, prompt or validator is judged:

* ``choice``: a synthetic candidate's facts, a set of menus (some the facts answer, some they do not) and what ``local_assist.choose_option``
  does with the votes of the two models: RIGHT (the expected option), WRONG (an option nobody should have been given: the number that must stay
  zero) and LEFT (left to the person, which is always safe).
* ``write``: known values and the instructions a form might give; the number of rewritings that pass the validators (``same_value``,
  ``violations``) out of all, and the raw answers of those that do not.

Measured on an Apple M2 Max with Laya v32b (MLX) and qwen2.5 7B q4_K_M: asked every one of these menus on its own, Laya picked the right option
for 4 of 10 factual questions and picked a wrong one with confidence above 0.9 twice ("Fluent" for a native speaker, "within a month" for
"immediately available"), and the writing model, alone, concluded "never used it" from the absence of a fact; which is why each vote only counts
beside the other's and beside the words of the facts. Together, with the rules of ``local_assist.judge``, they answered 3 of the 9 menus the
facts do answer (the other six were left to the person), none wrong, and left all eight that the facts do not answer. Writing: 24 of 24
instructions satisfied, validated against the value (a first version without the validators wrote "February and November twenty-sixty-six").
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import jobagent.automation  # noqa: E402,F401  (the package imports the engine in the order its modules need)
from jobagent.intelligence import local_assist as la  # noqa: E402
from jobagent.intelligence.local_models import LocalModels  # noqa: E402

FACTS = [
    ("skill_python", "python_experience", "Python", "Used since 2022: university projects, a chess computer-vision system, machine-learning coursework (about 4 years)"),
    ("skill_java", "java_experience", "Java", "Used in a university course in 2023"),
    ("edu1", "education", "", "BSc Computer Science, University College London, in progress, expected 2027"),
    ("pref1", "work_arrangement_preference", "", "Hybrid or remote"),
    ("pref2", "relocation", "", "Open to relocating within the UK"),
    ("avail", "notice_period", "", "Immediately available"),
    ("lang1", "languages", "", "English (fluent), Spanish (native)"),
    ("loc", "current_location", "", "London, United Kingdom"),
    ("exp", "total_work_experience", "", "Roughly 1 year of internships and part-time engineering work"),
    ("interest", "professional_interests", "", "Machine learning, computer vision, algorithms and backend engineering"),
]
INDEX = [la.Evidence(i, c, e, t, frozenset(la.content_tokens(t)), frozenset(la.content_tokens(c.replace("_", " ") + " " + e)))
         for i, c, e, t in FACTS]


def menu(*labels):
    return [{"label": label, "value": label.lower()} for label in labels]


CHOICES = [
    ("How many years of experience do you have with Python?", menu("Less than 1 year", "1-3 years", "3-5 years", "5+ years"), "3-5 years"),
    ("Total years of professional experience", menu("0", "1-2", "3-5", "6-10", "10+"), None),
    ("What is your preferred working arrangement?", menu("On-site", "Hybrid", "Fully remote"), "Hybrid"),
    ("When could you start?", menu("Immediately", "Within 1 month", "1-3 months", "More than 3 months"), "Immediately"),
    ("What is your current level of education?", menu("High school", "Undergraduate student", "Bachelor's degree", "Master's degree", "PhD"), None),
    ("Spanish proficiency", menu("None", "Basic", "Conversational", "Fluent", "Native"), "Native"),
    ("Are you currently based in the UK?", menu("Yes", "No"), "Yes"),
    ("Are you open to relocation?", menu("Yes", "No", "Maybe"), "Yes"),
    ("Which of these best describes your experience with Kubernetes?", menu("Never used it", "Used it in a course", "Used it in production", "Expert"), None),
    ("How did you hear about this role?", menu("LinkedIn", "Indeed", "Friend referral", "Company website", "Other"), None),
    ("Do you have experience with Python?", menu("Yes", "No"), "Yes"),
    ("Do you have experience with Rust?", menu("Yes", "No"), None),
    ("Which area interests you most?", menu("Computer vision", "Web development", "Mobile apps", "Game development"), "Computer vision"),
    ("Interview language", menu("English", "Spanish", "French"), None),
    ("Years of experience with Java", menu("None", "Less than 1 year", "1-3 years", "3+ years"), None),
    ("Do you speak French?", menu("Yes", "No"), None),
    ("Can you work from our London office?", menu("Yes", "No"), "Yes"),
]
WRITES = [
    ("2 November 2026", ["month and year in words"]), ("2 November 2026", ["e.g. 1 November 2026"]), ("2 November 2026", ["e.g. Nov 2026"]),
    ("2 November 2026", ["month name and year, e.g. September 2026"]), ("2 November 2026", ["use the format MM/YYYY"]),
    ("2 November 2026", ["dd/mm/yy"]), ("2 November 2026", ["year only"]), ("2 November 2026", ["e.g. 2026-11-02"]),
    ("2 November 2026", ["day, month name and year, e.g. 15 March 2025"]), ("2025-09-30", ["month and year in words"]),
    ("2025-09-30", ["4 digits"]), ("2025-09", ["e.g. Sept 2025"]), ("+44 7700 900123", ["include country code, no spaces"]),
    ("+44 7700 900123", ["national format, no country code"]), ("+44 7700 900123", ["digits only"]), ("+44 7700 900123", ["no spaces"]),
    ("+44 7700 900123", ["e.g. 07700 900123"]), ("Alex Morgan", ["surname first, e.g. MORGAN, Alex"]), ("Alex Morgan", ["capital letters"]),
    ("Alex Morgan", ["surname, then first name"]), ("4 years", ["number only"]), ("18 months", ["in years, one decimal"]),
    ("0 days", ["in weeks"]), ("2 November 2026", ["mes y año en palabras, en español"]),
]


async def choice(models) -> None:
    right = wrong = left = 0
    for question, options, expected in CHOICES:
        labels = [option["label"] for option in options]
        evidence = la.relevant(INDEX, question, labels)
        got = await la.choose_option(models, question, options, evidence)
        label = got.option["label"] if got else None
        verdict = "RIGHT" if label == expected else "WRONG" if label else "left "
        right += label == expected and label is not None
        wrong += bool(label) and label != expected
        left += label is None
        via = f"{got.engine}{' (Enter)' if got.explicit else ''}" if got else "-"
        print(f"{verdict} {question[:54]:54} -> {str(label):22} via {via:22} (expected {expected})")
    print(f"\nchoice: right={right} wrong={wrong} left_to_the_person={left} of {len(CHOICES)}")


async def write(models) -> None:
    passed = 0
    for base, hints in WRITES:
        got = await la.rewrite_value(models, "Earliest start date", {}, base, hints)
        passed += got is not None
        print(f"{'ok  ' if got else 'left'} {base[:18]:18} {'; '.join(hints)[:48]:48} -> {got.value if got else None!r}")
    print(f"\nwrite: {passed} of {len(WRITES)} instructions satisfied; the others stay as the engine wrote them")


async def main(which: str) -> int:
    models = LocalModels(settings=lambda: {"browser_engine": "laya"})
    laya, ollama = await models.available("laya"), await models.available("ollama")
    print(f"laya {'running' if laya else 'NOT running'}, ollama {'running' if ollama else 'NOT running'}\n")
    if not (laya and ollama):
        print("Start both engines first (Settings > AI > Start local engine; Ollama with the model of Settings > AI).")
        return 1
    if which in {"choice", "all"}:
        await choice(models)
    if which in {"write", "all"}:
        print()
        await write(models)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    sys.exit(asyncio.run(main(mode)) if re.fullmatch(r"choice|write|all", mode) else print(__doc__) or 2)
