"""Local help for what the engine could not finish: choosing among real options and writing a known value as the field asks.

Both models are replaced by doubles here (the real ones are exercised by ``scripts/bench_local_assist.py``); what these tests pin
down is the fence around them: an option nobody offered, a denial, a catch-all, a disagreement or a value that changed when it
was only meant to be written differently never reaches a form.
"""
import json

import httpx
import pytest

import jobagent.automation  # noqa: F401  (the package imports the engine in the order its modules need)
from jobagent.intelligence import local_assist as la
from jobagent.intelligence import instructions
from jobagent.intelligence.local_models import LayaVote, LocalModels, LocalUnavailable


def menu(*labels):
    return [{"label": label, "value": label.lower()} for label in labels]


def fact(identifier, concept, text, entity=""):
    return la.Evidence(identifier, concept, entity, text, frozenset(la.content_tokens(text)),
                       frozenset(la.content_tokens(concept.replace("_", " ") + " " + entity)))


class Models:
    """Laya picks ``laya`` (an option label, or None for a request for a person); the writing model answers ``ollama``."""

    def __init__(self, laya=None, ollama=None, *, laya_down=False, ollama_down=False):
        self.laya, self.ollama, self.laya_down, self.ollama_down = laya, ollama, laya_down, ollama_down
        self.laya_calls, self.ollama_calls = [], []

    async def laya_choose(self, question, options, facts, *, goal, rules, context=None):
        self.laya_calls.append((question, list(options), list(facts), context))
        if self.laya_down:
            raise LocalUnavailable("down")
        # a pick that was never offered (a catch-all the pool left out) is, to the real Laya, no pick at all
        return LayaVote(options.index(self.laya) if self.laya in options else None, 0.5, 1)

    async def ollama_json(self, system, payload, schema, *, max_tokens=160):
        self.ollama_calls.append((system, payload, schema))
        if self.ollama_down:
            raise LocalUnavailable("down")
        return self.ollama(payload) if callable(self.ollama) else self.ollama


HYBRID = [fact("f1", "work_arrangement_preference", "Hybrid")]
LANG = [fact("f2", "proficiency", "Native", "Spanish")]


# ---- choosing --------------------------------------------------------------------------------------------------------------
async def test_a_fact_that_is_an_option_settles_it_without_any_model():
    models = Models()
    got = await la.choose_option(models, "Preferred working arrangement", menu("On-site", "Hybrid", "Remote"), HYBRID)
    assert got.option["label"] == "Hybrid" and got.engine == "evidence" and not got.explicit
    assert models.laya_calls == [] and models.ollama_calls == []


async def test_both_models_picking_the_option_a_fact_names_is_a_tab_answer():
    models = Models("Native", {"reason": "r", "choice": "Native", "facts": [1]})
    got = await la.choose_option(models, "Spanish proficiency", menu("Basic", "Conversational", "Fluent", "Native"), LANG)
    # "Native" is the value of the fact itself: settled without a model
    assert got.option["label"] == "Native" and got.engine == "evidence"
    models = Models("Fluent", {"reason": "r", "choice": "Fluent", "facts": [1]})
    got = await la.choose_option(models, "Spanish proficiency", menu("Basic", "Conversational", "Fluent", "C2 mastery"),
                                 [fact("f3", "proficiency", "Fluent in speech and writing", "Spanish")])
    assert got.option["label"] == "Fluent" and got.engine == "laya+ollama" and got.explicit is False
    assert got.evidence[0].id == "f3"


async def test_agreement_on_an_option_no_fact_says_is_offered_for_Enter_never_for_Tab():
    facts = [fact("f4", "interests", "Machine learning and backend engineering")]
    models = Models("Data science", {"reason": "r", "choice": "Data science", "facts": [1]})
    got = await la.choose_option(models, "Which area interests you most?", menu("Web development", "Data science", "Mobile"), facts)
    assert got.explicit is True and got.confidence < 0.7 and got.option["label"] == "Data science"


async def test_a_disagreement_a_refusal_or_an_outage_leave_the_question_to_the_person():
    options = menu("Basic", "Fluent", "Native")
    facts = [fact("f5", "proficiency", "Fluent", "French")]
    cases = [Models("Fluent", {"reason": "r", "choice": "Native", "facts": [1]}),            # they disagree
             Models(None, {"reason": "r", "choice": "Fluent", "facts": [1]}),                 # Laya asks for a person
             Models("Fluent", {"reason": "r", "choice": "NONE", "facts": []}),                # the writing model finds no support
             Models("Fluent", {"reason": "r", "choice": "Fluent", "facts": []}, laya_down=True),
             Models("Basic", {"reason": "r", "choice": "Basic", "facts": [1]}, ollama_down=True)]
    for models in cases:
        question = "French level"
        # "Fluent" is also the fact itself: use a question and facts that no option equals
        got = await la.choose_option(models, question, options, [fact("f6", "languages", "speaks it well", "French")])
        assert got is None
    assert facts


@pytest.mark.parametrize("label", ["None", "Never used it", "No", "0", "No experience", "Ninguno", "Other", "Prefer not to say", "N/A"])
async def test_a_denial_or_a_catch_all_is_never_concluded_from_facts(label):
    options = menu("Used it in a course", "Used it at work", label)
    models = Models(label, {"reason": "r", "choice": label, "facts": [1]})
    got = await la.choose_option(models, "Experience with Kubernetes", options, [fact("f7", "skills", "Docker and Linux", "Kubernetes")])
    assert got is None


async def test_a_yes_no_menu_is_answered_yes_only_and_through_the_questions_own_words():
    facts = [fact("f8", "location", "London, United Kingdom")]
    models = Models("Yes", {"reason": "r", "choice": "Yes", "facts": [1]})
    got = await la.choose_option(models, "Are you based in London?", menu("Yes", "No"), facts)
    assert got.option["label"] == "Yes"
    for pick in ("No",):
        models = Models(pick, {"reason": "r", "choice": pick, "facts": [1]})
        assert await la.choose_option(models, "Are you based in Paris?", menu("Yes", "No"), facts) is None


async def test_the_models_only_ever_see_options_a_person_could_pick():
    models = Models("Hybrid", {"reason": "r", "choice": "Hybrid", "facts": [1]})
    options = menu("Select an option", "On-site", "Hybrid", "Other") + [{"label": "Disabled one", "value": "x", "disabled": True}]
    await la.choose_option(models, "How do you prefer to work?", options, [fact("f9", "preference", "likes mixed days")])
    _, shown, _, _ = models.laya_calls[0]
    assert shown == ["On-site", "Hybrid"]
    assert models.ollama_calls[0][1]["options"] == ["On-site", "Hybrid"]


async def test_menus_that_are_too_long_empty_or_without_facts_are_not_asked():
    models = Models("A", {"reason": "r", "choice": "A", "facts": [1]})
    assert await la.choose_option(models, "Pick", menu(*[f"Option {i}" for i in range(30)]), HYBRID) is None
    assert await la.choose_option(models, "Pick", menu("Only one"), HYBRID) is None
    assert await la.choose_option(models, "Pick", menu("Hybrid", "Remote"), []) is None
    assert models.laya_calls == [] and models.ollama_calls == []


async def test_today_reaches_both_models_so_dates_can_be_compared():
    models = Models("Within 1 month", {"reason": "r", "choice": "Within 1 month", "facts": [1]})
    await la.choose_option(models, "When could you start?", menu("Immediately", "Within 1 month", "Later"),
                           [fact("fa", "earliest_start", "2026-11-01")], today="2026-10-15")
    assert models.laya_calls[0][3] == {"today": "2026-10-15"} and models.ollama_calls[0][1]["today"] == "2026-10-15"


# ---- evidence --------------------------------------------------------------------------------------------------------------
def test_relevance_uses_the_question_the_options_and_aliases():
    index = [fact("a", "notice_period", "Immediately available"), fact("b", "current_location", "London, United Kingdom"),
             fact("c", "hobby", "Chess"), fact("d", "languages", "English; Spanish")]
    assert [e.id for e in la.relevant(index, "When could you start?", ["Immediately", "Later"])] == ["a"]
    assert [e.id for e in la.relevant(index, "Are you based in the UK?", ["Yes", "No"])] == ["b"]
    assert la.relevant(index, "How did you hear about us?", ["LinkedIn", "Friend"]) == []


def test_the_index_holds_no_personal_data(tmp_path):
    import os
    import sys
    from pathlib import Path
    os.environ["MERIDIAN_TEST"] = "1"
    sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "engine"))
    from candidates import engine_for
    from jobagent.extension.resolver import load_environment
    engine = engine_for(tmp_path, "student")
    env = load_environment(engine.db, {}, job={"country": "United Kingdom"}, application={"id": "", "company": "Acme", "location": "London"})
    index = la.evidence_index(env)
    assert index and la.evidence_index(env) is index  # built once per snapshot
    text = json.dumps([[e.concept, e.entity, e.text] for e in index]).lower()
    for private in ("email", "phone", "nationality", "right to work", "sponsorship", "visa", "salary", "date of birth", "test@example"):
        assert private not in text, private
    assert {"proficiency", "degree"} <= {e.concept for e in index}


# ---- writing a known value as the field asks -------------------------------------------------------------------------------
async def rewrite(base, written, hints, **field):
    return await la.rewrite_value(Models(ollama={"value": written}), "Graduation date", field, base, hints)


async def test_a_value_written_as_asked_is_accepted_when_it_is_the_same_value():
    got = await rewrite("2027-09-27", "September 2027", ["e.g. June 2027"])
    assert got.value == "September 2027" and got.changed
    got = await rewrite("2027-09-27", "27/09/2027", ["dd/mm/yyyy"])
    assert got.value == "27/09/2027"
    got = await rewrite("+441234567890", "01234567890", ["no country code"])
    assert got.value == "01234567890"
    got = await rewrite("John Smith", "SMITH JOHN", ["surname first, capitals"])
    assert got.value == "SMITH JOHN"


@pytest.mark.parametrize("base,written,hints", [
    ("2027-09-27", "June 2027", ["e.g. June 2027"]),             # the example copied instead of the value
    ("2027-09", "27/09/2027", ["dd/mm/yyyy"]),                    # a day nobody knows
    ("2027-09-27", "27/09/2028", ["dd/mm/yyyy"]),                 # another year
    ("01234567890", "+441234567890", ["with country code"]),      # a code the value never had
    ("John Smith", "John", ["first name only"]),                  # a part of the value is another value
    ("Computer Science", "CS", ["abbreviated"]),                  # an abbreviation cannot be checked
    ("4 years", "5", ["number only"]),
    ("2027-09-27", "", ["dd/mm/yyyy"]),
    ("2027-09-27", "27/09/2027\nextra", ["dd/mm/yyyy"]),
])
async def test_a_rewriting_that_changes_the_value_is_discarded(base, written, hints):
    assert await rewrite(base, written, hints) is None


async def test_the_fields_own_limits_still_apply_to_a_rewriting():
    assert await rewrite("2027-09-27", "27/09/2027", ["dd/mm/yyyy"], pattern=r"\d{4}-\d{2}-\d{2}") is None
    assert (await rewrite("2027-09-27", "2027-09-27", ["iso"], pattern=r"\d{4}-\d{2}-\d{2}")).changed is False
    assert await rewrite("2027-09-27", "27/09/2027", ["dd/mm/yyyy"], max_length=5) is None
    assert await la.rewrite_value(Models(ollama_down=True), "Date", {}, "2027-09-27", ["dd/mm/yyyy"]) is None


# ---- instructions and equivalence ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("label,core,instruction", [
    ("Graduation date (MM/YYYY)", "Graduation date", "MM/YYYY"),
    ("Phone number (include country code, no spaces)", "Phone number", "include country code, no spaces"),
    ("Degree subject [e.g. Computer Science]", "Degree subject", "e.g. Computer Science"),
    ("Years of experience (including internships)", "Years of experience (including internships)", ""),
    ("Current employer (most recent job)", "Current employer (most recent job)", ""),
    ("Fecha de graduación (p. ej. junio de 2027)", "Fecha de graduación", "p. ej. junio de 2027"),
    ("Nombre completo (tal como aparece en tu pasaporte)", "Nombre completo", "tal como aparece en tu pasaporte"),
    ("Name", "Name", ""),
])
def test_the_instruction_is_taken_out_of_the_question_only_when_it_is_one(label, core, instruction):
    assert instructions.split_instruction(label) == (core, instruction)


def test_what_a_control_shows_decides_whether_a_known_value_needs_rewriting():
    field = {"placeholder": "June 2027"}
    assert instructions.needs_rewriting("27 September 2027", "Graduation date", field, known_contract=False) == ["e.g. June 2027"]
    assert instructions.needs_rewriting("September 2027", "Graduation date", field, known_contract=False) == []
    assert instructions.needs_rewriting("27 September 2027", "Graduation date", field, known_contract=True) == []
    assert instructions.needs_rewriting("London", "City", {"placeholder": "Type here..."}, known_contract=False) == []
    assert instructions.needs_rewriting("London", "City", {"description": "e.g. the city you live in"}, known_contract=False)


@pytest.mark.parametrize("base,written,instruction,expected", [
    ("2027-09-27", "27 September 2027", "", True), ("2027-09-27", "2027", "", True), ("2027-09", "27/09/2027", "", False),
    ("2027-06-07", "06/07/2027", "", False), ("2027-06-07", "07/06/2027", "day/month/year", True),
    ("2027-06-07", "06/07/2027", "month/day/year", True), ("27 de septiembre de 2027", "27/09/2027", "dd/mm/aaaa", True),
    ("+441234567890", "01234567890", "", True), ("01234567890", "+441234567890", "", False),
    ("18 months", "1.5 years", "", True), ("4 years", "4", "", True), ("4 years", "40", "", False),
    ("John Smith", "SMITH, JOHN", "", True), ("John Smith", "John Smyth", "", False),
])
def test_same_value(base, written, instruction, expected):
    assert instructions.same_value(base, written, instruction=instruction) is expected


# ---- the engines ----------------------------------------------------------------------------------------------------------
def handler_for(calls, *, laya_ok=True, ollama_models=("qwen2.5:7b-instruct-q4_K_M",)):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.host, request.url.port, request.url.path))
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"ok": laya_ok, "backend": "laya-mlx"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in ollama_models]})
        body = json.loads(request.content)
        if request.url.path == "/v1/systemone":
            keys = [key for key in body["questions"]["option"]["criteria"]]
            return httpx.Response(200, json={"model": "m", "answers": {"option": {
                "choice": "o1", "probabilities": {key: (1.0 if key == "o1" else 0.0) for key in keys}, "confidence": 0.9}}})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": json.dumps({"value": "ok"})}})
        return httpx.Response(404)
    return handle


async def test_local_models_talk_only_to_loopback_and_validate_what_comes_back():
    calls = []
    models = LocalModels(settings=lambda: {"browser_engine": "laya"}, transport=httpx.MockTransport(handler_for(calls)))
    assert await models.available("laya") and await models.available("ollama")
    vote = await models.laya_choose("Q?", ["A", "B", "C"], ["fact"], goal="g", rules="r")
    assert vote.index == 1 and vote.confidence == pytest.approx(0.9)
    assert await models.ollama_json("s", {"x": 1}, {"type": "object"}) == {"value": "ok"}
    assert {host for _, host, _, _ in calls} == {"127.0.0.1"}
    with pytest.raises(LocalUnavailable):
        await models.laya_choose("Q?", ["only one"], [], goal="g", rules="r")
    hostile = LocalModels(settings=lambda: {"browser_engine": "laya", "laya_endpoint": "http://example.com:8791"},
                          transport=httpx.MockTransport(handler_for(calls)))
    assert await hostile.available("laya") is False
    with pytest.raises(LocalUnavailable):
        await hostile.laya_choose("Q?", ["A", "B"], [], goal="g", rules="r")


async def test_the_settings_decide_which_engine_exists_and_a_missing_model_means_unavailable():
    calls = []
    deterministic = LocalModels(settings=lambda: {"browser_engine": "deterministic"}, transport=httpx.MockTransport(handler_for(calls)))
    assert await deterministic.available("laya") is False
    off = LocalModels(settings=lambda: {"local_text_assist": False}, transport=httpx.MockTransport(handler_for(calls)))
    assert await off.available("ollama") is False
    assert calls == []  # nothing was even probed
    other = LocalModels(settings=lambda: {}, transport=httpx.MockTransport(handler_for(calls, ollama_models=("llama3:8b",))))
    assert await other.available("ollama") is False


async def test_an_installed_engine_is_started_once_a_minute_and_never_a_foreign_one():
    started = []

    class Runtime:
        def __init__(self, status):
            self._status = status

        async def status(self):
            return self._status

        async def start(self):
            started.append(1)

    import asyncio

    clock = [0.0]
    down = httpx.MockTransport(lambda request: httpx.Response(503))
    models = LocalModels(laya_runtime=Runtime({"installed": True, "running": False}), settings=lambda: {"browser_engine": "laya"},
                         transport=down, clock=lambda: clock[0])
    assert await models.available("laya") is False
    await asyncio.sleep(0.01)
    assert len(started) == 1
    clock[0] += 5
    assert await models.available("laya") is False
    await asyncio.sleep(0.01)
    assert len(started) == 1       # cooling down
    clock[0] += 61
    assert await models.available("laya") is False
    await asyncio.sleep(0.01)
    assert len(started) == 2
    await models.close()
    foreign = LocalModels(laya_runtime=Runtime({"installed": True, "running": False, "foreign_service": True}),
                          settings=lambda: {"browser_engine": "laya"}, transport=down, clock=lambda: clock[0])
    started.clear()
    await foreign.available("laya")
    await asyncio.sleep(0.01)
    await foreign.close()
    assert started == []


# ---- layouts, constraints and the second attempt ---------------------------------------------------------------------------
@pytest.mark.parametrize("example,written,ok", [
    ("1 November 2026", "2 November 2026", True), ("1 November 2026", "02 November 2026", True), ("1 November 2026", "2 Nov 2026", False),
    ("Nov 2026", "Nov 2026", True), ("Nov 2026", "Nov 26", False), ("June 2027", "September 30, 2025", False),
    ("06/2027", "11/2026", True), ("06/2027", "11/26", False), ("06/2027", "11-2026", False), ("+44 7700 900123", "+44 7700 900123", True),
])
def test_layout_matches(example, written, ok):
    assert instructions.layout_matches(example, written) is ok


@pytest.mark.parametrize("written,hints,expected", [
    ("447700900123", ["digits only"], []), ("+447700900123", ["digits only"], ["digits only"]),
    ("+44 7700", ["include country code, no spaces"], ["no spaces"]), ("+447700900123", ["include country code, no spaces"], []),
    ("Morgan Alex", ["surname first, in capital letters"], ["capital"]), ("MORGAN ALEX", ["capital letters"], []),
    ("11/26", ["use the format MM/YYYY"], ["laid out as MM/YYYY"]), ("11/2026", ["use the format MM/YYYY"], []),
    ("02/11/26", ["two-digit year, numbers only: dd/mm/yy"], []),            # "numbers only" does not forbid the slashes of a layout
    ("Nov 26", ["e.g. Nov 2026"], ["example"]), ("Nov 2026", ["e.g. Nov 2026"], []),
    ("Artificial Intelligence", ["e.g. Computer Science"], []),               # an example without a layout shows nothing to match
])
def test_violations_are_what_the_hints_say_in_words(written, hints, expected):
    found = instructions.violations(written, hints)
    assert len(found) == len(expected) and all(word in " ".join(found) for word in expected), found


async def test_a_first_attempt_that_breaks_the_instruction_is_told_so_and_a_second_one_may_pass():
    replies = iter([{"value": "+447700900123"}, {"value": "447700900123"}])
    models = Models(ollama=lambda payload: next(replies))
    got = await la.rewrite_value(models, "Phone", {}, "+44 7700 900123", ["digits only"])
    assert got.value == "447700900123"
    first, second = (call[1] for call in models.ollama_calls)
    assert "your_previous_answer_was_wrong_because" not in first
    assert any("digits only" in line for line in second["your_previous_answer_was_wrong_because"])
    # two wrong attempts: the field keeps what it had
    models = Models(ollama={"value": "+447700900123"})
    assert await la.rewrite_value(models, "Phone", {}, "+44 7700 900123", ["digits only"]) is None
    assert len(models.ollama_calls) == 2


def test_a_model_is_given_the_parts_of_a_value_not_asked_to_read_it():
    date = instructions.parts_of("2 November 2026")["date"]
    assert date == {"year": 2026, "month_number": "11", "month_name": "November", "month_abbreviation": "Nov", "day_number": "02"}
    assert "day_number" not in instructions.parts_of("November 2026")["date"]
    assert instructions.parts_of("+44 7700 900123") == {"phone_digits": "447700900123", "starts_with_plus": True}
    assert instructions.parts_of("Alex Morgan") == {"words_in_order": ["Alex", "Morgan"]}
    assert instructions.parts_of("Computer") == {}


# ---- what the agreement of the two models is worth -----------------------------------------------------------------------
async def agree(label, question, options, facts):
    models = Models(label, {"reason": "r", "choice": label, "facts": [1]})
    return await la.choose_option(models, question, menu(*options), facts)


async def test_facts_that_name_several_options_do_not_choose_among_them():
    facts = [fact("f1", "languages", "English (fluent), Spanish (native)")]
    assert await agree("Spanish", "Interview language", ["English", "Spanish", "French"], facts) is None
    facts = [fact("f2", "work_arrangement_preference", "Hybrid or remote")]
    assert await agree("Hybrid", "Preferred working arrangement", ["On-site", "Hybrid", "Fully remote"], facts) is None


async def test_a_pick_that_goes_against_the_words_of_the_facts_is_dropped():
    facts = [fact("f3", "notice_period", "Immediately available")]
    assert await agree("Within 1 month", "When could you start?", ["Immediately", "Within 1 month", "Later"], facts) is None \
        or (await agree("Within 1 month", "When could you start?", ["Immediately", "Within 1 month", "Later"], facts)).engine == "evidence"


async def test_an_option_without_words_of_its_own_is_anchored_by_the_question():
    facts = [fact("f4", "relocation", "Open to relocating within the UK")]
    got = await agree("Yes", "Are you open to relocation?", ["Yes", "No", "Maybe"], facts)
    assert got.option["label"] == "Yes" and got.explicit is False
    unrelated = [fact("f5", "interests", "Robotics and path planning")]
    got = await agree("Yes", "Would you consider a hybrid arrangement?", ["Yes", "No", "Maybe"], unrelated)
    assert got is not None and got.explicit is True    # read into the facts: Enter only


async def test_a_scan_does_not_probe_once_per_field_and_jobs_do_not_each_wait_for_an_engine_that_did_not_come_up():
    probes, clock = [], [0.0]

    def refuse(request):
        probes.append(request.url.path)
        return httpx.Response(503)

    models = LocalModels(settings=lambda: {"browser_engine": "laya"}, transport=httpx.MockTransport(refuse), clock=lambda: clock[0])
    for _ in range(5):
        assert await models.available("laya") is False   # five fields of one scan: one probe
    assert len(probes) == 1
    clock[0] += 9
    assert await models.available("laya") is False and len(probes) == 2

    async def instant_sleep(_):
        clock[0] += 0.5
    import jobagent.intelligence.local_models as module
    original, module.asyncio.sleep = module.asyncio.sleep, instant_sleep
    try:
        before = clock[0]
        assert await models.available("laya", wait=5) is False        # waits its 5 seconds (simulated)...
        assert clock[0] - before >= 5
        before, calls = clock[0], len(probes)
        assert await models.available("laya", wait=5) is False        # ...and the next job behind it does not wait again
        assert clock[0] - before < 1 and len(probes) - calls <= 1
    finally:
        module.asyncio.sleep = original
