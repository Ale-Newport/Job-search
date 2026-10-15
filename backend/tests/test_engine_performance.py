"""The answer engine's cost per option, and proof that making it cheaper changed nothing it answers.

A form of 400 controls with 60 options each asked ``answers.normalize`` for 1.1 million normalisations (2.5 s of CPU on the
event loop of the app that also serves the window): every ``choice`` re-read the question and every label, once per option.
``normalize`` is now memoised (a pure function of a short string), what a question asks is read once per menu, and a menu
that repeats is judged once. NONE of this may change an answer: the second half of this file runs the original algorithms
(uncached, option by option) next to the new ones over a large corpus and requires identical results."""
from __future__ import annotations

import asyncio
import random
import re
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import seed_candidate  # noqa: E402
from jobagent.automation import answers, disclosures, option_selection  # noqa: E402
from jobagent.automation.answers import canonical_key, normalize  # noqa: E402
from jobagent.automation.option_selection import OptionSelector  # noqa: E402
from jobagent.db import Database  # noqa: E402
from jobagent.extension.resolver import FieldResolver, load_environment, prepare_elements  # noqa: E402
from jobagent.extension.schemas import ElementIn  # noqa: E402
from jobagent.intelligence import formatting  # noqa: E402
from jobagent.intelligence.formatting import choice  # noqa: E402

COUNTRIES = ["Select...", "Afghanistan", "Albania", "Algeria", "Argentina", "Australia", "Austria", "Belgium", "Brazil", "Canada",
             "Chile", "China", "Colombia", "Denmark", "Egypt", "Finland", "France", "Germany", "Greece", "India", "Ireland", "Italy",
             "Japan", "Mexico", "Netherlands", "Norway", "Poland", "Portugal", "Spain", "Sweden", "Switzerland", "Turkey",
             "United Kingdom", "United Kingdom (+44)", "Spain (+34)", "United States (+1)", "United States"]
MENUS = [
    COUNTRIES, ["Yes", "No"], ["Sí", "No"], ["Male", "Female", "Non-binary", "Decline To Self Identify"],
    ["Hombre", "Mujer", "No binario", "Prefiero no decirlo"], ["High School", "Associate's Degree", "Bachelor's Degree",
                                                              "Master's Degree", "Doctorate"],
    ["Bachillerato", "Grado universitario", "Máster"], ["Asian", "Black", "White", "Mixed", "Prefer not to say"],
    ["I am not a protected veteran", "I identify as a protected veteran", "I don't wish to answer"],
    ["English", "Spanish", "French", "German"], ["Immediately", "1 week", "2 weeks", "1 month", "Other"],
    ["Select", "LinkedIn", "Indeed", "Referral", "Other", "Otro"], ["First Class Honours", "2:1", "2:2", "Third"],
    ["Student visa (with work rights)", "Graduate visa", "Skilled Worker visa", "British citizen"],
    ["Disabled", "Not disabled"], ["Yes, I consent", "No, I do not consent"], ["Sí, acepto", "No acepto"],
    ["London", "London, United Kingdom", "Greater London, England, United Kingdom", "Madrid, Spain"],
    [{"label": "Student", "value": "s", "disabled": True}, {"label": "Student visa", "value": "sv"}, {"label": "Other", "value": ""}]]
PAIRS = [  # (question, answer)
    ("Country", "United Kingdom"), ("Country of residence", "United Kingdom"), ("Nationality", "Spanish"), ("País de residencia", "Spain"),
    ("Phone country code", "United Kingdom (+44)"), ("Are you legally authorized to work in the UK?", "Yes"),
    ("Will you now or in the future require sponsorship?", "No"), ("¿Tienes autorización para trabajar en el Reino Unido?", "Yes"),
    ("¿Necesitarás patrocinio?", "No"), ("Gender", "Non-binary"), ("Género", "Non-binary"), ("Pronouns", "they/them/theirs"),
    ("Gender", "Prefer not to say"), ("Race", "Asian"), ("Veteran Status", "I don't wish to answer"),
    ("Do you have a disability?", "No"), ("¿Tienes alguna discapacidad?", "No"), ("Highest level of education", "Bachelor's degree"),
    ("Nivel de estudios", "Master's degree"), ("Degree classification", "First Class Honours"), ("Languages", "English"),
    ("Notice period", "None"), ("How did you hear about us?", "Other"), ("I consent to the terms", "Yes"),
    ("Acepto los términos", "Yes"), ("Location", "London, United Kingdom"), ("City", "London"), ("Visa", "Student"),
    ("Religion", "Atheist"), ("Sexual orientation", "Heterosexual"), ("Ethnicity", "White other"), ("Salary", "60000"),
    ("Years of experience", "3"), ("Grade", "79.94"), ("Anything", "Zürich"), ("Question", ""), ("Question", "0")]


def reference_normalize(value) -> str:
    """``answers.normalize`` as it was written before it was memoised."""
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


# --------------------------------------------------------------------------------------------- normalize
def corpus():
    rng = random.Random(7)
    words = ["Zürich", "Ñandú", "İstanbul", "Seção", "ﬁnancial", "é", "日本語", "Tokyo ", "  spaced  ", "UPPER", "lower", "12", "3.5",
             "A&B", "x/y", "(+44)", "O'Neil", "​", "‮", "a\x00b", "émigré", "ǅ", "ß", "Å", "½", "①"]
    strings = ["", " ", "a", "Yes", "Sí", "No", "Phone country code", "United Kingdom (+44)", "x" * 511, "x" * 512, "x" * 513,
               "é" * 600, "word " * 400]
    strings += [" ".join(rng.choice(words) for _ in range(rng.randint(1, 8))) for _ in range(3000)]
    strings += list(COUNTRIES) + [q for q, _ in PAIRS] + [a for _, a in PAIRS]
    return strings


def test_normalize_is_what_it_always_was_for_every_kind_of_input():
    inputs = corpus() + [None, 0, 1.5, True, [], ["a", "B"], {"k": "v"}, b"bytes", ("t",), "Sí", Exception("boom")]

    class Text(str):
        pass
    inputs += [Text("Hello World"), Text(""), Text("É")]
    for value in inputs:
        assert normalize(value) == reference_normalize(value), repr(value)[:60]
    assert normalize(None) == "none" and normalize(5) == "5"


def test_the_cache_serves_short_strings_and_never_keeps_long_ones():
    answers._normalized.cache_clear()
    normalize("A unique label")
    normalize("A unique label")
    info = answers._normalized.cache_info()
    assert (info.hits, info.misses) == (1, 1)
    normalize("x" * 5000)
    normalize("x" * 5000)
    assert answers._normalized.cache_info().currsize == 1  # a pasted paragraph is normalised each time and not kept
    assert answers._normalized.cache_info().maxsize >= 1 << 14


def test_canonical_key_is_what_it_was_and_cached():
    for label in ["First name", "first name (required)", "Name", "Nombre", "Email", "E-mail", "Phone number", "Salary expectations",
                  "Notice period", "Unknown thing", "", "Teléfono móvil", "Pretensiones salariales"]:
        assert canonical_key(label) == answers._canonical_key.__wrapped__(label)
    answers._canonical_key.cache_clear()
    canonical_key("Phone number")
    canonical_key("Phone number")
    assert answers._canonical_key.cache_info().hits == 1
    with pytest.raises(TypeError):
        canonical_key(None)


# --------------------------------------------------------------------------------------------- the original select
async def original_select(selector: OptionSelector, resolution: dict, options: list[dict], answer=None):
    """``OptionSelector.select`` as it was before: ``choice`` once per option, everything read again each time."""
    answer = resolution.get("answer") if answer is None else answer
    if answer is None or resolution.get("leave_blank"):
        return None
    kwargs = {"question": resolution["question"], "disclosure_concept": resolution.get("disclosure_concept")}
    supported = [option for option in options if not option.get("disabled") and choice(str(answer), [option], **kwargs)]
    if not supported:
        return None
    preferred = choice(str(answer), supported, **kwargs)
    exact = [o for o in supported if normalize(o["label"]) == normalize(str(answer))]
    if len(exact) == 1:
        return {"option": exact[0], "engine": "exact", "confidence": 1.0}
    return {"option": preferred, "engine": "verified_equivalence", "confidence": 1.0} if preferred else None


def menu(labels):
    return [dict(o) if isinstance(o, dict) else {"label": o, "value": o.lower()} for o in labels]


def run_corpus(select):
    results = []
    selector = OptionSelector({"browser_engine": "deterministic"})
    for question, answer in PAIRS:
        for labels in MENUS:
            options = menu(labels)
            resolution = {"question": question, "answer": answer, "verified": True, "fact_ids": ["f1"]}
            found = asyncio.run(select(selector, resolution, options)) if select is original_select else \
                asyncio.run(selector.select(resolution, options))
            results.append(None if found is None else (found["option"]["label"], found["engine"], found["confidence"]))
    return results


def test_select_gives_the_same_option_as_the_original_for_every_question_answer_and_menu(monkeypatch):
    cached = run_corpus(None)
    with monkeypatch.context() as patch:
        patch.setattr(answers, "normalize", reference_normalize)
        patch.setattr(formatting, "normalize", reference_normalize)
        patch.setattr(option_selection, "normalize", reference_normalize)
        patch.setattr(disclosures, "_normal", lambda value: reference_normalize(value or ""))
        patch.setattr(answers, "canonical_key", answers._canonical_key.__wrapped__)
        patch.setattr(formatting, "question_concept", lambda q: disclosures.disclosure_profile_key(q))
        original = run_corpus(original_select)
    assert len(cached) == len(PAIRS) * len(MENUS) >= 600
    assert cached == original
    chosen = [r for r in cached if r]
    assert len(chosen) >= 40 and len(cached) - len(chosen) >= 400  # a choice is made, and refused, often
    assert {engine for _, engine, _ in chosen} == {"exact", "verified_equivalence"}


def test_a_menu_that_repeats_is_judged_once_and_judged_the_same(monkeypatch):
    selector = OptionSelector({"browser_engine": "deterministic"})
    resolution = {"question": "Country of residence", "answer": "United Kingdom", "verified": True, "fact_ids": ["f"]}
    options = menu(COUNTRIES)
    option_selection._supports.cache_clear()
    first = asyncio.run(selector.select(resolution, options))
    misses = option_selection._supports.cache_info().misses
    second = asyncio.run(OptionSelector({"browser_engine": "deterministic"}).select(resolution, menu(COUNTRIES)))
    assert first == {**first, "option": second["option"]} or first["option"] == second["option"]
    assert option_selection._supports.cache_info().misses == misses  # nothing was judged twice
    assert option_selection._supports.cache_info().hits >= len(COUNTRIES) - 1
    assert first["option"]["label"] == "United Kingdom"


def test_an_option_with_something_unhashable_is_judged_directly_not_skipped():
    options = [{"label": "United Kingdom", "value": ["not", "hashable"]}, {"label": "Spain", "value": "es"}]
    assert option_selection.supports("United Kingdom", options[0], question="Country", concept=None) is True
    assert option_selection.supports("United Kingdom", options[1], question="Country", concept=None) is False
    assert option_selection.supports("United Kingdom", {"label": "United Kingdom"}, question="Country", concept=None) is True


def test_an_option_without_a_label_fails_exactly_as_it_always_did():
    """The memo keeps a missing label apart from an empty one: ``choice`` raises for the first and answers for the second."""
    for broken in ({"value": "x"}, {"value": "x", "role": "option"}):
        with pytest.raises(KeyError):
            choice("United Kingdom", [broken], question="Country")
        with pytest.raises(KeyError):
            option_selection.supports("United Kingdom", broken, question="Country", concept=None)
    for empty in ({"label": ""}, {"label": None}):
        assert choice("United Kingdom", [empty], question="Country") is None
        assert option_selection.supports("United Kingdom", empty, question="Country", concept=None) is False


def test_what_the_question_asks_is_read_once_per_menu_not_once_per_option(monkeypatch):
    """60 options used to read the question 61 times (a dozen regular expressions each)."""
    calls = []
    real = disclosures.disclosure_profile_key
    monkeypatch.setattr(disclosures, "disclosure_profile_key", lambda question, element=None: calls.append(question) or real(question, element))
    formatting._question_concept.cache_clear()
    options = menu([f"Option {i}" for i in range(60)] + ["United Kingdom"])
    asyncio.run(OptionSelector({"browser_engine": "deterministic"}).select(
        {"question": "A question nobody asked before", "answer": "United Kingdom", "verified": True, "fact_ids": ["f"]}, options))
    assert calls == ["A question nobody asked before"]


# --------------------------------------------------------------------------------------------- a whole form
def form(count=400):
    rows = []
    for i in range(count):
        label = ["Country", "Country of residence", "Nationality", "Gender", "City"][i % 5]
        rows.append({"node_id": 1 + 2 * i, "role": "combobox", "tag": "select", "type": "select-one", "label": label,
                     "context": "", "description": "", "placeholder": "", "section_id": "page", "section_label": "Details",
                     "form_id": 1, "group": "", "field_path": f"q{i}", "name": f"q{i}", "id": "", "autocomplete": "",
                     "required": False, "enabled": True, "readonly": False, "multiple": False, "checked": False,
                     "expanded": False, "has_value": False, "value": "", "selected_text": "", "selected_values": [],
                     "operations": ["SELECT"], "options": [{"label": o, "value": o} for o in COUNTRIES + [f"Extra {i}"]]})
    return rows


def test_a_form_of_400_selects_of_60_options_normalises_each_distinct_string_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    db = seed_candidate(Database(tmp_path / "meridian.sqlite3"))
    env = load_environment(db, {"sensitive_policies": {}}, job={"country": "United Kingdom", "company": "Acme"},
                           application={"id": "", "company": "Acme", "location": "London"})
    prepared = prepare_elements([ElementIn.model_validate(e) for e in form()], "doc", "https://x.example/")
    resolver = FieldResolver(env, url="https://x.example/apply", ats=None, scope="s", frame_key="top", document_id="doc",
                             elements=prepared)
    answers._normalized.cache_clear()

    async def resolve_all():
        return [(await resolver.resolve(element)).result["answer"] for element in prepared]

    found = asyncio.run(resolve_all())
    info = answers._normalized.cache_info()
    calls = info.hits + info.misses
    assert found.count("United Kingdom") >= 100 and calls > 20_000, info  # the work is still asked for ...
    assert info.misses < 2_000 and info.misses < calls / 20, info  # ... and almost all of it is answered from memory


def test_the_peers_of_a_form_are_built_once_per_resolver_not_once_per_control(tmp_path, monkeypatch):
    from jobagent.extension import resolver as module
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    db = seed_candidate(Database(tmp_path / "meridian.sqlite3"))
    env = load_environment(db, {"sensitive_policies": {}}, job={"country": "United Kingdom"}, application={"id": "", "company": "A", "location": "L"})
    prepared = prepare_elements([ElementIn.model_validate(e) for e in form(60)], "doc", "https://x.example/")
    resolver = FieldResolver(env, url="https://x.example/apply", ats=None, scope="s", frame_key="top", document_id="doc", elements=prepared)
    built = []
    real = module.member_view
    monkeypatch.setattr(module, "member_view", lambda elements: built.append(len(elements)) or real(elements))
    for element in prepared:
        resolver.context(element)
    assert built == [60]
    prepared.append(dict(prepared[0], node_id=9999, index=60))  # the frame changed: the view is rebuilt, never stale
    resolver.context(prepared[-1])
    assert built == [60, 61]
