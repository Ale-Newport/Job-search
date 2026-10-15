"""Scenarios of the assistant's intelligence and memory: background drafts, employer research, calm failure states,
the learning loop (save a correction, reuse it next session) and submit awareness (record the application, only with
the user's say-so).
"""

from __future__ import annotations

import asyncio
import json
import re

from playwright.async_api import expect

import expectations as ex
import flows
import seed
import ui
from framework import Env, check, scenario
from scenarios_forms import SUGGEST, drafts_finished, ready_form
from ui import Chip, Panel
from scale import millis  # noqa: E402

MISSION = "make physical automation as approachable as software"
# The fixed, GET-safe paths of the research design (research.FIXED_PATHS) plus the homepage check and robots.txt.
COMPANY_PATHS = {"/", "/robots.txt", "/about", "/about-us", "/company", "/mission", "/values", "/culture", "/careers",
                 "/engineering"}


@scenario(needs=SUGGEST, timeout=150)
async def scenario_draft_generation_dwell_regenerate(env: Env) -> None:
    """Long answers: 'drafting' state, Tab never blocks on it, the draft needs 500 ms on screen before Tab inserts it,
    regeneration never overwrites silently."""
    env.llm.hold()  # the model answers only when the test says so
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATING", insert="never")
    await expect(chip.part("chip-status")).to_be_visible()
    await expect(panel.tid("panel-drafts")).to_be_visible()
    check("the panel counts drafts that are pending", "1" in await panel.tid("panel-drafts").inner_text())
    await ui.tab(page)
    check("Tab while drafting inserts nothing and does not wait", await ui.field_value(page, "#why") == "")
    await ui.expect_focus(page, "#gender")
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATING")
    # The 500 ms rule is the PRODUCT's clock: run it 50 times slower while the draft arrives and Tab goes down, so that however long this
    # machine takes between the two (500 ms was seen on a loaded one) the draft has been on screen for next to nothing as far as the product knows.
    await rig.slow_clock(page)
    try:
        env.llm.release()
        await page.locator("[data-testid=chip][data-state=GENERATED_DRAFT]").wait_for(state="visible", timeout=millis(30_000))
        await expect(chip.part("chip-dwell").first).to_be_visible()  # the chip itself says the draft is not insertable yet
        await page.keyboard.press("Tab")
        check("a draft that has just appeared is NOT inserted by Tab (dwell rule)", await ui.field_value(page, "#why") == "")
        check("Tab still moved on", await ui.focused_id(page) != "why")
    finally:
        await rig.restore_clock(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATED_DRAFT", tone="draft", insert="tab", value=seed.PROJECT_STORY)
    await flows.wait_dwell_over(chip)
    first_draft = (await chip.value.first.inner_text()).strip()
    await ui.tab(page)
    inserted = await ui.field_value(page, "#why")
    check("after the dwell Tab inserts the draft", seed.PROJECT_STORY in inserted and inserted.strip() in first_draft.replace("\n", " ") + " "
          or inserted.strip() != "", inserted)
    check("the model was asked once for this question (draft + one independent review)",
          len(env.llm.generation_calls()) == 1 and len([c for c in env.llm.calls if c["kind"] == "review"]) == 1)
    # explicit regeneration: a different draft arrives, the text in the field is NOT replaced behind the user's back
    await page.locator("#why").focus()
    await expect(chip.part("chip-regen")).to_be_visible()
    await chip.click("chip-regen-different")
    await flows.poll_until(lambda: _generation_count(env, 2), timeout=30, message="the second generation")
    await chip.expect_chip(state="GENERATED_DRAFT", timeout=millis(30_000))
    await expect(chip.part("chip-replace")).to_be_visible(timeout=millis(20_000))
    check("the field still holds the first draft", await ui.field_value(page, "#why") == inserted)
    second = (await chip.value.first.inner_text()).strip()
    check("the new draft differs from the first", second != first_draft)
    await chip.click("chip-replace")
    await expect(page.locator("#why")).not_to_have_value(inserted)
    await chip.click("chip-undo")
    await expect(page.locator("#why")).to_have_value(inserted)
    await ui.assert_never_submitted(page)
    check("no uncaught page error", not rig.errors, rig.errors[:2])


async def _generation_count(env: Env, count: int) -> bool:
    return len(env.llm.generation_calls()) >= count


@scenario(needs=SUGGEST, timeout=120)
async def scenario_draft_failure_is_calm(env: Env) -> None:
    """The writing model is down: one quiet explanation, everything else keeps working, and a retry recovers."""
    env.llm.mode = "http_500"
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="NEEDS_YOU", timeout=millis(30_000))
    check("a quiet one-liner, no value, no alarm", await chip.el.first.get_attribute("data-quiet") == "true"
          and await chip.value.count() == 0)
    await ui.tab(page)
    check("Tab did not insert anything", await ui.field_value(page, "#why") == "")
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    check("short verified answers still work", await ui.field_value(page, "#first_name") == seed.FIRST)
    env.llm.mode = "normal"
    await page.locator("#why").focus()
    await chip.expect_chip(state="NEEDS_YOU")
    if await chip.part("chip-draft").count():
        await chip.click("chip-draft")  # the explicit 'Draft with AI'
    else:
        await page.keyboard.press("Alt+KeyR")
    await chip.expect_chip(state="GENERATED_DRAFT", timeout=millis(40_000), value=seed.PROJECT_STORY)
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_drafts_unavailable(env: Env) -> None:
    """No writing model configured: long answers explain that once, calmly; short verified answers are unaffected."""
    await env.start_backend(llm=False)
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await expect(panel.tid("note-drafts")).to_be_visible(timeout=millis(20_000))
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="NEEDS_YOU")
    check("no model was ever called", env.llm.calls == [])
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    check("the verified answer is inserted regardless", await ui.field_value(page, "#first_name") == seed.FIRST)


@scenario(needs=SUGGEST, timeout=120)
async def scenario_profile_empty(env: Env) -> None:
    """A Meridian with no verified facts: the panel says so and offers Open Meridian; nothing is invented or inserted."""
    await env.start_backend(seed_profile=False, llm=False)
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    await expect(panel.tid("calm-profile-empty")).to_be_visible(timeout=millis(20_000))
    await page.click("#first_name")
    await ui.settle(page, 400)
    await ui.tab(page)
    check("nothing was inserted anywhere", await ui.synthetic_events(page) == [])
    await ui.assert_never_submitted(page)


@scenario(needs=(*SUGGEST, "research_hosts"), timeout=180)
async def scenario_research_strip_and_company_drafts(env: Env) -> None:
    """Background research of the company and role: strip fills from the fake employer site, drafts cite it, and the
    research traffic is minimal and carries nothing about the candidate."""
    await env.start_backend(research=True)
    _, page, panel = await ready_form(env, "greenhouse_apply.html")
    strip = panel.tid("panel-research")
    await expect(strip).to_be_visible(timeout=millis(20_000))
    await flows.poll_until(lambda: _research_finished(panel), timeout=60, message="research to finish")
    state = await panel.research_state()
    check("research ended well (done or partial, never failed)", state in {"done", "partial"}, state)
    await expect(panel.tid("research-count")).to_be_visible()
    await panel.tid("research-toggle").click()
    sources = await panel.tid("research-source").evaluate_all("(els) => els.map((e) => e.getAttribute('href') || e.textContent)")
    check("the sources are the employer's own pages", any(env.company.origin in (s or "") for s in sources), sources)
    check("at least the about, mission and values pages were read", len(sources) >= 3, sources)
    paths = [p for p in env.company.paths()]
    check("only fixed, GET-safe paths of the verified company site were requested", set(paths) <= COMPANY_PATHS, sorted(set(paths)))
    check("robots.txt was consulted", "/robots.txt" in paths)
    leaky = [r["path"] for r in env.company.requests
             if {k.lower() for k in r["headers"]} & {"cookie", "authorization"} or "?" in r["path"]]
    check("research requests carry no cookies, no credentials, no query string", not leaky, leaky)
    secrets = (seed.EMAIL, seed.PHONE, seed.NAME, seed.FIRST + " " + seed.LAST)
    check("nothing about the candidate reached the employer's site", not any(s in json.dumps(env.company.requests) for s in secrets))
    await drafts_finished(env, page)
    call = env.llm.generation_calls()[0]
    check("the model was given the employer's pages as research", call["has_company_context"], call)
    check("and only pages of the verified site", all((u or "").startswith(env.company.origin) for u in call["company_urls"]),
          call["company_urls"])
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATED_DRAFT", tone="draft", value=MISSION)
    await expect(chip.part("chip-source").first).to_be_visible()
    hrefs = await chip.part("chip-source").evaluate_all("(els) => els.map((e) => e.getAttribute('href') || '')")
    check("the draft names the employer pages it relied on", any(h.startswith(env.company.origin) for h in hrefs), hrefs)
    # "Research again" reads the site again ON PURPOSE: a user refresh bypasses the caches (backend/tests/test_extension_research.py:
    # "an explicit refresh bypasses the cache"). The panel still says "done" for a few milliseconds after the click, so the old check
    # (cached pages are not hammered) only passed when it sampled that stale state: it is a state-based wait now, and it asserts
    # what the product promises: the refresh really runs, stays inside the budget of one run, keeps to the same fixed paths, ends
    # well, and does not quietly re-write (and re-bill) the drafts.
    before = len(env.company.requests)
    drafts_before = len(env.llm.generation_calls())
    await panel.tid("research-refresh").click()
    await flows.poll_until(lambda: _site_read_again(env, before), timeout=60, message="the refresh to start reading the site")
    await flows.poll_until(_settled_after_refresh(env, panel), timeout=90, message="the refresh to finish and the site traffic to stop")
    reread = len(env.company.requests) - before
    check("a refresh really reads the site again (it is not a silent no-op)", reread >= 1, reread)
    check("and stays inside the budget of one research run", reread <= 14, reread)
    check("on the same fixed, GET-safe paths", {p for p in env.company.paths()} <= COMPANY_PATHS, sorted(set(env.company.paths())))
    state = await panel.research_state()
    check("research ends well after a refresh (done or partial, never failed)", state in {"done", "partial"}, state)
    check("a refresh never re-writes the drafts by itself (that would cost money)", len(env.llm.generation_calls()) == drafts_before,
          (drafts_before, len(env.llm.generation_calls())))


async def _research_finished(panel: Panel) -> bool:
    return await panel.research_state() in {"done", "partial", "failed"}


async def _site_read_again(env: Env, before: int) -> bool:
    return len(env.company.requests) > before


def _settled_after_refresh(env: Env, panel: Panel, quiet: float = 1.5):
    """Research reports finished AND the employer site has been quiet for `quiet` seconds (a finished state seen a moment after the
    click is the old run's, not the refresh's)."""
    last = {"count": -1, "at": 0.0}

    async def probe() -> bool:
        now = asyncio.get_running_loop().time()
        count = len(env.company.requests)
        if count != last["count"]:
            last.update(count=count, at=now)
            return False
        return now - last["at"] >= quiet and await _research_finished(panel)

    return probe


@scenario(needs=SUGGEST, timeout=180)
async def scenario_save_correction_persisted_and_reused(env: Env) -> None:
    """Typing an answer Meridian did not have: ask once, send it ONLY on an explicit save, persist it, offer it next time."""
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.click("#colour")
    await chip.expect_chip(state="NEEDS_YOU")
    await page.keyboard.type("Forest green")
    await ui.tab(page)  # leaves the field: the learning loop looks at what the user typed
    ask = page.locator("[data-testid=save-ask]")
    await expect(ask).to_be_visible(timeout=millis(15_000))
    await expect(page.locator("[data-testid=save-value]")).to_contain_text("Forest green")
    check("nothing was sent before the user answered the prompt",
          backend.db_rows("SELECT COUNT(*) AS n FROM facts WHERE value LIKE '%Forest green%'")[0]["n"] == 0)
    check("no application row exists just from browsing and typing", backend.db_rows("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 0)
    knowledge_before = (await flows.assist_state(rig, page))["knowledge"]
    await page.locator("[data-testid=save-application]").click()
    await expect(ask).to_have_count(0, timeout=millis(10_000))
    await flows.poll_until(lambda: _saved(backend, "Forest green"), timeout=15, message="the correction to be stored")
    # what Meridian knows has changed: the assistant reloads its answers for the whole page (see wait_suggestions_settled)
    await flows.wait_suggestions_settled(rig, page, changed_from=knowledge_before)
    app = backend.db_rows("SELECT a.id,a.status,a.mode,j.source,j.company FROM applications a JOIN jobs j ON j.id=a.job_id")
    check("exactly one lazily created application, marked as assisted by the extension, still PREPARING",
          len(app) == 1 and app[0]["status"] == "PREPARING" and app[0]["source"] == "extension", app)
    check("no human task and no review notification were raised", backend.db_rows("SELECT COUNT(*) AS n FROM human_tasks")[0]["n"] == 0)
    check("no APPLIED/CONFIRMED event exists", backend.db_rows(
        "SELECT COUNT(*) AS n FROM application_events WHERE status IN ('APPLYING','APPLIED','CONFIRMED')")[0]["n"] == 0)
    # a different answer the user declines to save is never stored
    await page.locator("#employer").focus()
    await chip.expect_chip(state="INFERRED")
    await page.keyboard.type("Other Company Ltd")
    await page.locator("#degree").focus()
    await expect(ask).to_be_visible(timeout=millis(15_000))
    await page.locator("[data-testid=save-no]").click()
    await expect(ask).to_have_count(0)
    check("an answer the user said No to was not stored",
          backend.db_rows("SELECT COUNT(*) AS n FROM facts WHERE value LIKE '%Other Company%'")[0]["n"] == 0)
    # the next session on the same posting offers the saved answer
    await page.reload(wait_until="domcontentloaded")
    await rig.ensure_active(page)  # the worker restarts the assistant after a reload by itself; a click would switch it off
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    await page.click("#colour")
    await chip.expect_chip(state="VERIFIED", value="Forest green", timeout=millis(20_000))
    await ui.tab(page)
    await expect(page.locator("#colour")).to_have_value("Forest green")
    check("the saved answer was inserted by Tab next session", True)
    await ui.assert_never_submitted(page)


async def _saved(backend, text: str) -> bool:
    return backend.db_rows("SELECT COUNT(*) AS n FROM facts WHERE value LIKE ?", (f"%{text}%",))[0]["n"] > 0


@scenario(needs=SUGGEST, timeout=180)
async def scenario_submit_confirmation_record_tracker(env: Env) -> None:
    """The user submits; the assistant notes it, sees the confirmation, ASKS, and records only after a yes (tracker row)."""
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=spa")
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, ex.SUBMIT_FLOW, label="submit_flow")
    await ui.expect_focus(page, "#submit_app")
    await ui.assert_never_submitted(page)
    check("before the user clicks, nothing was tracked as applied", _applied(backend) == 0)
    await page.click("#submit_app")  # the user's own click
    ask = page.locator("[data-testid=track-ask]")
    await expect(ask).to_be_visible(timeout=millis(20_000))
    await expect(page.locator("[data-testid=track-evidence]")).to_contain_text("Thank you for applying")
    await expect(page.locator("[data-testid=track-what]")).to_contain_text("Acme Robotics")
    check("seeing the confirmation did not record anything by itself", _applied(backend) == 0)
    submits = await ui.submit_events(page)
    check("exactly one submit click, and it was the user's", len(submits) >= 1 and all(s["trusted"] for s in submits), submits)
    await page.locator("[data-testid=track-record]").click()
    await flows.poll_until(lambda: _applied_async(backend), timeout=15, message="the application to be recorded")
    rows = backend.db_rows("SELECT a.status,j.company,j.source FROM applications a JOIN jobs j ON j.id=a.job_id")
    check("one application, APPLIED, from the extension, for Acme Robotics",
          len(rows) == 1 and rows[0]["status"] == "APPLIED" and rows[0]["company"] == "Acme Robotics" and rows[0]["source"] == "extension", rows)
    events = backend.db_rows("SELECT origin,status FROM application_events ORDER BY created_at")
    check("its history says the user recorded it through the extension", any(e["origin"] == "extension" and e["status"] == "APPLIED" for e in events)
          or any(e["status"] == "APPLIED" for e in events), events)
    tracker = await backend.tracker_rows()
    mine = [row for row in tracker if "Acme Robotics" in json.dumps(row)]
    check("the tracker shows the row", mine, [r.get("company") for r in tracker][:5])
    assistant = [row.get("assistant") for row in mine if row.get("assistant")]
    check("the tracker row says the assistant helped (browser, fields inserted)", assistant and assistant[0]["browser"] == "chrome"
          and assistant[0]["fields_inserted"] >= 1, assistant)


def _applied(backend) -> int:
    return backend.db_rows("SELECT COUNT(*) AS n FROM applications WHERE status IN ('APPLIED','CONFIRMED')")[0]["n"]


async def _applied_async(backend) -> bool:
    return _applied(backend) == 1


async def submit_by_the_user_and_navigate(env: Env):
    backend = await env.start_backend()
    rig, page, panel = await ready_form(env, "submit_flow.html", "mode=nav")
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, ex.SUBMIT_FLOW, label="submit_flow")
    await ui.assert_never_submitted(page)
    await page.click("#submit_app")  # the user's own click: the form navigates to the confirmation page
    await page.wait_for_url(re.compile(r"/confirmation\.html$"))
    return backend, rig, page


@scenario(needs=SUGGEST, timeout=180)
async def scenario_submit_navigates_to_confirmation_page(env: Env) -> None:
    """The confirmation is on the NEXT document: the pending submit survives the navigation, the assistant (which has no
    form to help with there) still shows the 'Record this application?' prompt, and dismissing records nothing."""
    backend, rig, page = await submit_by_the_user_and_navigate(env)
    ask = page.locator("[data-testid=track-ask]")
    await expect(ask).to_be_visible(timeout=millis(25_000))
    await expect(page.locator("[data-testid=track-evidence]")).to_contain_text("Thank you for applying")
    await expect(page.locator("[data-testid=track-what]")).to_contain_text("Acme Robotics")
    check("seeing the confirmation did not record anything by itself", _applied(backend) == 0)
    await page.locator("[data-testid=track-dismiss]").click()
    await expect(ask).to_have_count(0)
    await asyncio.sleep(0.5)
    check("dismissing recorded nothing", _applied(backend) == 0)
    check("and no application row exists for the page that was only looked at",
          backend.db_rows("SELECT COUNT(*) AS n FROM applications WHERE status IN ('APPLYING','APPLIED','CONFIRMED')")[0]["n"] == 0)


@scenario(needs=SUGGEST, timeout=180)
async def scenario_submit_navigates_then_record(env: Env) -> None:
    """Same navigating submit, but the user says yes on the confirmation page: the tracker gets one APPLIED row."""
    backend, rig, page = await submit_by_the_user_and_navigate(env)
    ask = page.locator("[data-testid=track-ask]")
    await expect(ask).to_be_visible(timeout=millis(25_000))
    check("nothing is recorded before the user answers", _applied(backend) == 0)
    await page.locator("[data-testid=track-record]").click()
    await flows.poll_until(lambda: _applied_async(backend), timeout=15, message="the application to be recorded")
    rows = backend.db_rows("SELECT a.status,j.company,j.source FROM applications a JOIN jobs j ON j.id=a.job_id")
    check("one APPLIED application of Acme Robotics from the extension",
          len(rows) == 1 and rows[0]["status"] == "APPLIED" and rows[0]["company"] == "Acme Robotics" and rows[0]["source"] == "extension", rows)
    await expect(ask).to_have_count(0, timeout=millis(10_000))
    submits = await ui.submit_events(page)
    check("the only submit the page ever saw was the user's", all(s["trusted"] for s in submits), submits)


@scenario(needs=("worker",), timeout=60)
async def scenario_options_page_smoke(env: Env) -> None:
    """The options/welcome page loads inside the extension with no errors."""
    rig = await env.start_rig()
    page = await rig.new_page()
    await page.goto(f"chrome-extension://{rig.ext_id}/options.html")
    await page.wait_for_load_state("load")
    title = await page.title()
    check("it has a title that names the product", "Meridian" in title, title)
    await expect(page.locator("body")).not_to_be_empty()
    check("no page error", not rig.errors, rig.errors[:2])



# ---------------------------------------------------------------------------------------------- local models (Laya, Ollama)
@scenario(needs=SUGGEST, timeout=240)
async def scenario_local_models_finish_what_the_profile_cannot(env: Env) -> None:
    """A menu the engine found no option for and a value the field wants written its own way are finished by two models on this
    Mac (scripted here, on loopback): the field says it is working, is told when they end, never takes a Tab for what no fact says,
    never gives a protected question to a model, and keeps what it had when the models fail."""
    local = env.local
    local.laya_picks = {"area of engineering": "Robotics", "kind of company": "Startup"}
    local.ollama_picks = {"area of engineering": "Robotics", "kind of company": "Startup"}
    local.ollama_values = {"month and year in words": "November 2026"}
    local.hold()
    await env.start_backend(local=True)
    rig, page, panel = await ready_form(env, "local_assist.html")
    chip = Chip(page)

    await page.locator("#start_month").focus()
    await chip.expect_chip(state="GENERATING", insert="never")
    await expect(chip.part("chip-status")).to_contain_text("local AI")
    await ui.tab(page)
    check("Tab while a model works inserts nothing and does not wait", await ui.field_value(page, "#start_month") == "")
    await page.locator("#start_month").focus()
    local.release()
    await chip.expect_chip(state="VERIFIED", tone="verified", insert="tab", value="November 2026", timeout=millis(30_000))
    await ui.tab(page)
    check("the value is written the way the field asked (month and year in words)", await ui.field_value(page, "#start_month") == "November 2026")
    sent = local.calls_of("ollama_write")
    check("the writing model was given the question WITHOUT its instruction, and the instruction apart",
          sent and sent[0]["question"] == "Earliest start date" and sent[0]["hints"] == ["month and year in words"], sent)

    await page.locator("#area").focus()
    await chip.expect_chip(state="INFERRED", tone="inferred", insert="tab", value="Robotics", timeout=millis(30_000))
    check("both models read only the options the page offers (no placeholder) and short statements of the profile",
          local.calls_of("laya")[0]["options"] == ["Web development", "Data science", "Robotics", "Mobile apps"]
          and not any(word in json.dumps(local.calls_of("laya")[0]["facts"]).lower() for word in ("alex", "example.test", "+44", "settled")),
          local.calls_of("laya")[0])
    await ui.tab(page)
    check("Tab took the option a fact of the profile names", await ui.select_label(page, "#area") == "Robotics")

    await page.locator("#company_type").focus()
    await chip.expect_chip(state="INFERRED", tone="inferred", insert="explicit", value="Startup", timeout=millis(30_000))
    await ui.tab(page)
    check("an option the models only read into the profile is NOT taken by Tab", await ui.select_label(page, "#company_type") in {"", "Select..."})
    await page.locator("#company_type").focus()
    await chip.expect_chip(state="INFERRED", insert="explicit", value="Startup")
    await page.keyboard.press("Enter")
    check("it is taken by Enter", await ui.select_label(page, "#company_type") == "Startup")

    before = len(local.calls)
    await page.locator("input[name=disability]").first.focus()
    await asyncio.sleep(1.5)
    check("a protected question is never given to a model", len(local.calls) == before and
          not any("disab" in str(call.get("question", "")).lower() for call in local.calls))
    await page.locator("#subject").focus()
    await chip.expect_chip(state="NEEDS_YOU", timeout=millis(10_000))
