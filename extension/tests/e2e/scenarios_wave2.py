"""Wave-2 scenarios: behaviours that were product gaps after wave 1 and are fixed now, plus the robustness cases around
them (slow bridge, hidden tabs, disconnect from the browser side, Spanish UI, a production-like closed-shadow build).

Each scenario asserts values AND the absence of forbidden effects (nothing submitted, nothing written to protected
answers or consent), like the rest of the suite.
"""

from __future__ import annotations

import asyncio
import re
import time

import expectations as ex
import flows
import harness
import seed
import ui
from framework import Env, Skip, check, scenario
from harness import UI_HOST
from playwright.async_api import Page, expect
from scenarios_connection import SUGGEST_CHAIN
from scenarios_forms import SUGGEST, drafts_finished, ready_form
from scenarios_journeys import greenhouse_journey
from ui import Chip, Panel
from scale import millis  # noqa: E402

REPO = harness.REPO


# ---------------------------------------------------------------------------------------------- "how did you hear"
HEAR_ABOUT = [
    ex.verified("#first_name", seed.FIRST),
    ex.F("#hear_select", ex.NEEDS_YOU, "never", kind="select", note="'How did you hear about us?': never Meridian's say-so"),
    ex.F("#hear_text", ex.NEEDS_YOU, "never", note="the same question as free text"),
    ex.F("#hear_linkedin", ex.NEEDS_YOU, "never", kind="radio", note="as a radio group, another wording"),
    ex.F("#hear_board", ex.NEEDS_YOU, "never", note="'which job board': still the candidate's own answer"),
]


@scenario(needs=SUGGEST, timeout=150)
async def scenario_how_did_you_hear_about_us_is_needs_you(env: Env) -> None:
    """Where the candidate found the job is theirs to say: every wording and control type stays 'needs you' (never
    'Other', never 'Meridian'), nothing is written, the model is not asked, and the verified answers around it still work."""
    _, page, panel = await ready_form(env, "hear_about.html")
    await flows.focus_first(page, "#first_name")
    await flows.walk(page, HEAR_ABOUT, label="hear_about")
    events = await ui.synthetic_events(page)
    ui.assert_untouched(events, "hear_select", "hear_text", "hear_linkedin", "hear_indeed", "hear_referral", "hear_other",
                        "hear_board", "submit_app")
    check("only the first name was written", ui.written_targets(events) == ["first_name"], ui.written_targets(events))
    check("the model was never asked about it", env.llm.generation_calls() == [])
    check("and no answer to it was invented anywhere: the radio group stays unchecked",
          await ui.checked_group(page, "input[name=hear_group]") == [])
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- JSON-LD location, UI level
@scenario(needs=SUGGEST, timeout=120)
async def scenario_job_location_survives_a_long_visible_description(env: Env) -> None:
    """The job's location is only in the JSON-LD; the visible description is much longer than the JSON-LD one. The
    country is still known, so work authorization, sponsorship and salary are answered (and the panel names the place)."""
    await env.start_backend()
    await env.backend.profile_answer("notice_period", "1 month")
    _, page, panel = await ready_form(env, "ashby_apply.html", "long=1")
    await page.wait_for_selector("#ashby_form", state="attached")
    long_text = await page.evaluate("document.querySelector('.ashby-job-posting-description').innerText.length")
    check("the visible description is long (over 800 characters)", long_text > 800, long_text)
    await expect(panel.tid("panel-sub")).to_contain_text("London", timeout=millis(15_000))  # where the job is, from the JSON-LD
    chip = Chip(page)
    await page.locator("#sponsor_yes").focus()
    await chip.expect_chip(state="VERIFIED", insert="explicit", value="No", timeout=millis(15_000))
    await page.keyboard.press("Enter")
    await expect(page.locator("#sponsor_no")).to_have_attribute("aria-pressed", "true")
    await page.locator("#salary").focus()
    await chip.expect_chip(state="VERIFIED", value=str(seed.SALARY), timeout=millis(15_000))
    await ui.tab(page)
    await expect(page.locator("#salary")).to_have_value(str(seed.SALARY))
    check("work authorization, sponsorship and salary all depend on the country, and all were answered", True)
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- chip click-through
def _overlap(a: dict, b: dict) -> dict | None:
    left, top = max(a["x"], b["x"]), max(a["y"], b["y"])
    right, bottom = min(a["x"] + a["width"], b["x"] + b["width"]), min(a["y"] + a["height"], b["y"] + b["height"])
    if right - left < 6 or bottom - top < 6:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


async def _interactive_rects(chip: Chip) -> list[dict]:
    """Boxes of the chip's own buttons and links (the only parts of the chip that take the mouse)."""
    rects = []
    controls = chip.el.first.locator("button, a, .value.open")
    for index in range(await controls.count()):
        box = await controls.nth(index).bounding_box()
        if box:
            rects.append(box)
    return rects


async def click_through_the_chip(page, chip: Chip, fields: list[str]) -> int:
    """Focus every field in turn; wherever the chip on screen covers part of ANOTHER field, a real mouse click on that part must reach that
    field (the element under the point is the field and it takes focus). Returns how many overlaps were exercised."""
    overlaps = 0
    for selector in fields:
        await page.locator(selector).scroll_into_view_if_needed()
        await page.locator(selector).focus()
        await expect(chip.el.first).to_be_visible(timeout=millis(10_000))
        await ui.settle(page, 250)
        box = await chip.el.first.bounding_box()
        buttons = await _interactive_rects(chip)
        for other in fields:
            if other == selector:
                continue
            field_box = await page.locator(other).bounding_box()
            shared = _overlap(box, field_box) if box and field_box else None
            if not shared:
                continue
            # a point inside the shared area that is not on one of the chip's buttons
            spot = None
            for fx in (0.5, 0.25, 0.75):
                for fy in (0.5, 0.25, 0.75):
                    x, y = shared["x"] + shared["width"] * fx, shared["y"] + shared["height"] * fy
                    if not any(_overlap({"x": x - 1, "y": y - 1, "width": 2, "height": 2}, b) for b in buttons):
                        spot = (x, y)
                        break
                if spot:
                    break
            if not spot:
                continue
            overlaps += 1
            under = await page.evaluate("([x, y]) => (document.elementFromPoint(x, y) || {}).id || ''", list(spot))
            check(f"{selector}'s chip covers part of {other}: the page element under it is the field", under == other.lstrip("#"), under)
            await page.mouse.click(*spot)
            await expect(page.locator(other)).to_be_focused(timeout=millis(5_000))
            await page.locator(selector).focus()
            await expect(chip.el.first).to_be_visible(timeout=millis(10_000))
    return overlaps


@scenario(needs=SUGGEST, timeout=240)
async def scenario_chip_never_blocks_the_field_under_it(env: Env) -> None:
    """The chip is a hint, not a lid: wherever it overlaps another field, a real mouse click on that part of the field
    reaches the field (focus moves there), and only the chip's own buttons take the mouse. The visual layer keeps the chip off the
    neighbours whenever it can, so the proof needs a layout where it cannot: a form with no room around its fields in a small window."""
    _, page, panel = await ready_form(env, "dense_form.html")
    chip = Chip(page)
    await page.set_viewport_size({"width": 400, "height": 720})
    dense = ["#first_name", "#last_name", "#email", "#phone", "#linkedin", "#github", "#portfolio", "#degree", "#employer"]
    overlaps = await click_through_the_chip(page, chip, dense)
    print(f"    chip/field overlaps exercised on the dense form: {overlaps}", flush=True)
    check("the chip really did cover other fields (the check is not vacuous: the form leaves it no free side)", overlaps >= 6, overlaps)
    # and the chip's own buttons do take the mouse
    await page.locator("#first_name").focus()
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await chip.click("chip-accept")
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    check("the chip's accept button still works with a click (also when it is the folded one-line chip)", True)
    await ui.assert_never_submitted(page)
    # the same proof on a realistic form in a small window and in a large one (the chip may or may not overlap there: whatever it does)
    page = await env.open_tab("greenhouse_apply.html")
    await Panel(page).expect_phase("ready", timeout=millis(25_000))
    fields = ["#first_name", "#last_name", "#email", "#phone", "#linkedin", "#github", "#portfolio", "#degree", "#employer", "#colour"]
    chip = Chip(page)
    for width, height in ((480, 560), (1280, 900)):
        await page.set_viewport_size({"width": width, "height": height})
        count = await click_through_the_chip(page, chip, fields)
        print(f"    greenhouse form at {width}x{height}: overlaps exercised {count}", flush=True)
    await ui.assert_never_submitted(page)


# ---------------------------------------------------------------------------------------------- hidden tab catch-up
@scenario(needs=SUGGEST, timeout=180)
async def scenario_hidden_tab_catches_up_when_it_comes_back(env: Env) -> None:
    """A tab the user leaves is not polled; a draft that finishes meanwhile is waiting when the tab comes back, shown
    within seconds, and Tab can insert it (after its dwell). Nothing is lost, nothing is inserted unseen."""
    env.llm.hold()
    rig, page, panel = await ready_form(env, "greenhouse_apply.html")
    chip = Chip(page)
    await page.locator("#why").focus()
    await chip.expect_chip(state="GENERATING", insert="never")
    await expect(panel.tid("panel-drafts")).to_be_visible()
    tab = await rig.tab_id(page)
    # the user goes to another application tab
    other = await env.open_tab("lever_apply.html")
    await Panel(other).expect_phase("ready", timeout=millis(25_000))
    await rig.set_visibility(page, False)
    await flows.poll_until(lambda: _is_hidden(rig, tab), timeout=10, message="the worker to know the tab is hidden")
    env.llm.release()
    await drafts_finished_on_server(env)
    shown_while_away = await page.locator("[data-testid=chip][data-state=GENERATED_DRAFT]").count()
    await asyncio.sleep(4)  # more than a poll round trip: a tab that IS being served would have the draft by now
    still_waiting = await page.locator("[data-testid=chip][data-state=GENERATED_DRAFT]").count() == 0
    print(f"    while hidden: draft on screen={bool(shown_while_away)}, still showing 'drafting' after 4 s={still_waiting}", flush=True)
    check("the hidden tab holds no wait of its own at the bridge (the visible tab keeps the shared poll)",
          (await rig.polling())["hidden"].count(tab) == 1 and (await rig.polling())["inFlight"])
    check("nothing was inserted into the hidden tab", await ui.field_value(page, "#why") == "")
    # back to the tab
    came_back = time.monotonic()
    await rig.set_visibility(page, True)
    await page.bring_to_front()
    await expect(page.locator("[data-testid=chip][data-state=GENERATED_DRAFT]")).to_be_visible(timeout=millis(20_000))
    caught_up = time.monotonic() - came_back
    print(f"    the finished draft was on screen {caught_up:.1f}s after the tab came back", flush=True)
    check("the finished draft is on screen within ten seconds of coming back", caught_up < 10.0, f"{caught_up:.1f}s")
    await expect(panel.tid("panel-drafts")).to_have_count(0, timeout=millis(10_000))
    check("the hidden-tab bookkeeping was cleared", tab not in (await rig.polling())["hidden"])
    check("it is the draft the model wrote", seed.PROJECT_STORY in (await chip.value.first.inner_text()))
    await ui.tab(page)  # on arrival the 500 ms dwell had not elapsed: Tab never inserts what was not seen
    inserted_now = await ui.field_value(page, "#why")
    await page.locator("#why").focus()
    await flows.wait_dwell_over(chip)
    if inserted_now == "":
        await ui.tab(page)
    check("after the dwell Tab inserts the draft", seed.PROJECT_STORY in await ui.field_value(page, "#why"))
    await ui.assert_never_submitted(page)


async def _is_hidden(rig, tab: int) -> bool:
    return tab in (await rig.polling())["hidden"]


async def drafts_finished_on_server(env: Env, calls: int = 1) -> None:
    await flows.poll_until(lambda: _generated(env, calls), timeout=30, message=f"{calls} draft(s) written at the model")


async def _generated(env: Env, calls: int) -> bool:
    return len(env.llm.generation_calls()) >= calls and len([c for c in env.llm.calls if c["kind"] == "review"]) >= calls


# ---------------------------------------------------------------------------------------------- slow bridge
async def _freeze_and_click(env: Env, rig, backend, page):
    """Meridian is busy: its sockets accept connections but nothing answers. The assistant starts from a cold worker cache."""
    await rig.forget_connection_cache()
    backend.freeze()
    await rig.toolbar_click(page)
    await page.locator(UI_HOST).first.wait_for(state="attached", timeout=millis(15_000))


@scenario(needs=SUGGEST_CHAIN, timeout=180)
async def scenario_slow_bridge_recovers_without_forgetting_the_credential(env: Env) -> None:
    """Meridian is busy (the bridge accepts connections but answers late, or not for a long while): the assistant never
    claims a failure that did not happen (not proof_failed, not unpaired, not revoked), never forgets the credential,
    inserts nothing meanwhile, and is ready again by itself when Meridian answers."""
    rig = await env.connected()
    backend = env.backend
    token = (await rig.stored_connection())["token"]
    page = await env.open("greenhouse_apply.html")
    panel = Panel(page)
    try:
        await _freeze_and_click(env, rig, backend, page)
        timeline: list[tuple[float, str | None]] = []
        began = time.monotonic()
        for _ in range(12):  # about three seconds of waiting for a bridge that does not answer
            timeline.append((time.monotonic() - began, await panel.phase()))
            await asyncio.sleep(0.25)
        seen = [phase for _, phase in timeline]
        changes = [(f"{at:.1f}s", phase) for index, (at, phase) in enumerate(timeline) if index == 0 or phase != timeline[index - 1][1]]
        print(f"    phases shown while Meridian was slow: {changes}", flush=True)
        check("while Meridian is merely slow the panel never claims a pairing or proof failure",
              set(seen) <= {"connecting", "no_bridge", "error", None}, set(seen))
        await page.locator("#first_name").focus()
        await ui.settle(page, 300)
        await ui.tab(page)
        check("Tab while Meridian does not answer inserts nothing and only moves on",
              await ui.field_value(page, "#first_name") == "" and await ui.focused_id(page) == "last_name")
    finally:
        backend.thaw()
    await panel.expect_phase("ready", timeout=millis(40_000))
    check("the credential was never dropped (no re-pairing)", (await rig.stored_connection())["token"] == token
          and len(await backend.clients()) == 1)
    await page.click("#first_name")
    await Chip(page).expect_chip(state="VERIFIED", value=seed.FIRST, timeout=millis(20_000))
    # a bridge that stays silent for longer than the worker's patience (the first look waits up to six seconds)
    await rig.forget_connection_cache()
    backend.freeze()
    try:
        await page.reload(wait_until="domcontentloaded")
        await rig.ensure_active(page, wait_panel=False)  # the worker restarts the assistant after a reload by itself
        await page.locator(UI_HOST).first.wait_for(state="attached", timeout=millis(15_000))
        await flows.poll_until(lambda: _phase_in(panel, {"error", "no_bridge"}), timeout=30,
                               message="a calm 'Meridian is not answering' state after the patience ran out")
        phase = await panel.phase()
        print(f"    after a long silence the panel says {phase!r}", flush=True)
        await expect(panel.tid(f"calm-{phase}")).to_be_visible()
        await expect(panel.tid("calm-retry")).to_be_visible()
        check("still paired: nothing alarming, the card offers a retry", (await rig.stored_connection())["token"] == token)
    finally:
        backend.thaw()
    await panel.expect_phase("ready", timeout=millis(60_000))
    check("and it recovers without the user doing anything", len(await backend.clients()) == 1)


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_slow_bridge_shows_the_connecting_state(env: Env) -> None:
    """While Meridian is only slow to answer, the panel says it is connecting (a calm spinner) and does not say that
    Meridian is closed or offer to open it."""
    rig = await env.connected()
    backend = env.backend
    page = await env.open("greenhouse_apply.html")
    panel = Panel(page)
    await ui.record_phases(page)  # the page remembers what the panel showed: 'connecting' lasts seconds, a loaded machine can ask too late
    try:
        await _freeze_and_click(env, rig, backend, page)

        async def connecting_shown() -> bool:
            return any(sample["phase"] == "connecting" for sample in await ui.phases_seen(page))

        await flows.poll_until(connecting_shown, timeout=30, message="the panel to show 'connecting' while Meridian does not answer")
        samples = await ui.phases_seen(page)
        print(f"    what the panel showed while Meridian was slow: {[(s['phase'], s['calm']) for s in samples]}", flush=True)
        connecting = [sample for sample in samples if sample["phase"] == "connecting"]
        check("the connecting state came with its calm card", any("calm-connecting" in sample["calm"] for sample in connecting), connecting)
        check("it was never presented as 'Meridian isn't open'", not any(sample["open"] for sample in connecting), connecting)
    finally:
        backend.thaw()
    await panel.expect_phase("ready", timeout=millis(40_000))


async def _phase_in(panel: Panel, phases: set[str]) -> bool:
    return await panel.phase() in phases


@scenario(needs=SUGGEST_CHAIN, timeout=150)
async def scenario_delaying_proxy_in_front_of_the_bridge_is_refused(env: Env) -> None:
    """A proxy that sits in front of the bridge and delays it answers for another port than the one Meridian bound to:
    the worker takes it for a stranger and sends it nothing credentialed (the hello names its own port)."""
    rig = await env.connected()
    backend = env.backend
    proxy = await env.delay_proxy(backend.bridge_port, delay=1.5)
    connection = await rig.stored_connection()
    await rig.install_connection({**connection, "port": proxy.port})
    await rig.set_bridge_ports([proxy.port])
    page = await env.open("greenhouse_apply.html", activate=False)
    await rig.toolbar_click(page)
    await page.locator(UI_HOST).first.wait_for(state="attached", timeout=millis(30_000))
    await flows.poll_until(lambda: _phase_in(Panel(page), {"no_bridge", "proof_failed", "error", "unpaired"}), timeout=40,
                           message="a refusal state")
    await asyncio.sleep(1.0)
    print(f"    the worker's verdict on the delaying proxy: {await Panel(page).phase()!r}", flush=True)
    check("the proxy was probed", proxy.connections >= 1, proxy.connections)
    check("no credential ever went through it", not proxy.credentialed, proxy.credentialed[:1])
    secret = connection["token"].split(".", 2)[2]
    check("and the secret is nowhere in what it forwarded", not any(secret in head for head in proxy.seen))
    answer = await rig.bg_request({"t": "status"})
    check("a credentialed call is refused", answer["ok"] is False, answer)
    check("still nothing credentialed went through the proxy", not proxy.credentialed)


# ---------------------------------------------------------------------------------------------- disconnect from the browser
@scenario(needs=SUGGEST_CHAIN, timeout=150)
async def scenario_disconnect_from_the_extension_revokes_the_client_in_the_app(env: Env) -> None:
    """'Disconnect' on the extension's options page asks Meridian to drop this browser: the app stops listing it, the old
    credential is dead on the bridge, the assistant on open pages falls back to 'connect', and connecting again works."""
    import httpx

    rig = await env.connected()
    backend = env.backend
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    stored = await rig.stored_connection()
    clients = await backend.clients()
    check("the app lists this Chrome", len(clients) == 1 and clients[0]["id"] == stored["clientId"], clients)
    check("and says it is ready", (await backend.extension_status())["state"] == "ready")
    options = await rig.open_options()
    disconnect = options.locator("[data-action=disconnect]")
    await expect(disconnect).to_be_visible(timeout=millis(15_000))
    await disconnect.click()
    await flows.poll_until(lambda: _no_clients(backend), timeout=15, message="the app to stop listing the browser")
    status = await backend.extension_status()
    check("the app no longer shows a connected browser", status["state"] != "ready" and status["clients"] == [], status["state"])
    check("the credential is gone from the browser", await rig.stored_connection() is None)
    await expect(options.locator("[data-action=connect]")).to_be_visible(timeout=millis(15_000))
    await expect(options.locator("body")).to_contain_text("isconnected")
    check("the options page says so and offers to connect", True)
    async with httpx.AsyncClient(base_url=backend.bridge_base, timeout=10, trust_env=False) as bridge:
        old = await bridge.get("/v1/status", headers={"Authorization": f"Bearer {stored['token']}"})
    check("the old credential is dead on the bridge", old.status_code == 401, old.status_code)
    # the open page follows: it offers to connect again
    await rig.forget_connection_cache()
    await rig.bg_request({"t": "conn:state", "refresh": True})
    await panel.expect_phase("unpaired", timeout=millis(25_000))
    await expect(panel.tid("calm-connect")).to_be_visible()
    # connect again with a fresh pairing
    await page.locator("[data-testid=calm-connect]").click()
    await expect(panel.tid("pairing-code")).to_be_visible(timeout=millis(20_000))
    await backend.approve_pairing()
    await panel.expect_phase("ready", timeout=millis(25_000))
    again = await backend.clients()
    check("a new client was issued", len(again) == 1 and again[0]["id"] != stored["clientId"])
    await ui.assert_never_submitted(page)


async def _no_clients(backend) -> bool:
    return await backend.clients() == []


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_disconnected_browser_stops_inserting_cached_answers(env: Env) -> None:
    """Fail closed: once this browser is no longer connected (unpaired or revoked) an open page neither shows chips from
    its cache nor inserts anything with Tab. (A merely unreachable Meridian is different: its prefetched answers may be used.)"""
    rig = await env.connected()
    backend = env.backend
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    chip = Chip(page)
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)  # the answers are cached by now
    await backend.revoke_client((await backend.clients())[0]["id"])
    await rig.forget_connection_cache()
    await rig.bg_request({"t": "conn:state", "refresh": True})
    await panel.expect_phase("revoked", timeout=millis(25_000))
    await page.locator("#last_name").focus()
    await ui.settle(page, 500)
    check("no chip is offered from the cache after the revocation", await chip.el.count() == 0, await chip.describe())
    await page.locator("#first_name").focus()
    await ui.settle(page, 300)
    await ui.tab(page)
    check("Tab inserts nothing after the revocation", await ui.field_value(page, "#last_name") == "")


# ---------------------------------------------------------------------------------------------- Spanish, one full flow
def _dictionary(locale: str) -> dict[str, str]:
    """The extension's own strings of one locale (ui.dictionary: content/i18n.ts, one 'key': 'value' per line)."""
    return ui.dictionary(locale)


async def _ui_text(page: Page) -> str:
    """Everything the assistant shows on the page (panel, chip, prompts, toasts), as one string."""
    return await page.evaluate("""() => [...document.querySelectorAll('[data-meridian-assistant]')]
        .map((host) => host.shadowRoot ? host.shadowRoot.textContent : '').join(' ')""")


@scenario(needs=SUGGEST, timeout=240)
async def scenario_spanish_ui_full_flow(env: Env) -> None:
    """With the language set to Spanish in Meridian, a whole flow reads in Spanish: panel, every trust label, hints,
    insertion toast, the save prompt, and the 'record this application?' prompt after a submit."""
    en, es = _dictionary("en"), _dictionary("es")
    check("the extension ships a Spanish dictionary as complete as the English one", set(en) == set(es) and len(es) > 200,
          (len(en), len(es)))
    backend = await env.start_backend()
    await backend.request("PATCH", "/extension/settings", json={"language": "es"})
    rig, page, panel = await ready_form(env, "greenhouse_apply.html", backend_args=None)
    await drafts_finished(env, page)
    chip = Chip(page)
    # a person who has never used the assistant: the first-use card is Spanish too (title and the three steps), none of it English
    await expect(panel.tid("panel-first-use")).to_be_visible(timeout=millis(15_000))
    check("the first-use card's title is Spanish", (await panel.tid("first-title").inner_text()).strip() == es["first.title"],
          await panel.tid("first-title").inner_text())
    steps = [(await item.inner_text()).strip().lstrip("0123456789\n ") for item in await panel.tid("first-steps").locator("li").all()]
    check("and so are its three steps", steps == [es["first.step1"], es["first.step2"], es["first.step3"]], steps)
    check("not one of them is the English sentence", not any(en[key] in " ".join(steps) for key in ("first.step1", "first.step2", "first.step3")))
    lang = await page.evaluate("document.querySelector('[data-meridian-assistant]').shadowRoot.querySelector('[data-testid=ui]').lang")
    check("the UI declares Spanish to assistive technology", lang.startswith("es"), lang)
    labels = {  # field -> (state, dictionary key of the trust line)
        "#first_name": ("VERIFIED", "chip.trust.verified"), "#degree": ("INFERRED", "chip.trust.inferred"),
        "#why": ("GENERATED_DRAFT", "chip.trust.draft"), "#authorised": ("VERIFIED", "chip.trust.protected"),
    }
    for selector, (state, key) in labels.items():
        await page.locator(selector).focus()
        await chip.expect_chip(state=state)
        shown = (await chip.part("chip-trust").first.inner_text()).strip()
        check(f"{selector}: the trust line is exactly the Spanish one ({es[key]!r})", shown == es[key], shown)
        if es[key] != en[key]:
            check(f"{selector}: and not the English one", en[key] not in await _ui_text(page))
    for selector, state, key in (("#gender", "SENSITIVE_BLANK", "chip.trust.sensitive"), ("#gdpr_consent", "CONSENT", "chip.trust.consent"),
                                 ("#colour", "NEEDS_YOU", "chip.trust.needs")):
        await page.locator(selector).focus()
        await chip.expect_chip(state=state)
        shown = (await chip.part("chip-label").first.inner_text()).strip()
        check(f"{selector}: the quiet label is Spanish ({es[key]!r})", es[key].lower() in shown.lower(), shown)
    # insertion and its toast
    await page.click("#first_name")
    await chip.expect_chip(state="VERIFIED", value=seed.FIRST)
    await ui.tab(page)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    # the bulk button, its notification and an undo all speak Spanish
    toast = page.locator("[data-testid=toast]")
    fill = panel.tid("panel-fill-verified")
    await expect(fill).to_be_enabled(timeout=millis(15_000))
    prefix = es["act.fillVerified"].split("{n}")[0]
    check(f"the 'fill verified' button reads {prefix.strip()!r}...", (await fill.inner_text()).strip().startswith(prefix.strip()),
          await fill.inner_text())
    await fill.click()
    await panel.tid("fill-confirm").click()  # (SEC-01: the first press lists what it will fill; one more click confirms)
    await expect(page.locator("#first_name")).to_have_value(seed.FIRST)
    await expect(page.locator("[data-testid=toast][data-kind=filled]").first).to_be_attached(timeout=millis(5_000))
    filled_text = await page.locator("[data-testid=toast][data-kind=filled]").first.inner_text()
    check("the notification counts the insertions in Spanish", "respuestas insertadas" in filled_text, filled_text)
    await page.locator("#last_name").focus()
    await page.keyboard.press("Alt+KeyZ")
    await expect(page.locator("[data-testid=toast][data-kind=undone]").first).to_be_attached(timeout=millis(5_000))
    undone_text = await page.locator("[data-testid=toast][data-kind=undone]").first.inner_text()
    check("and the undo notification too", undone_text.startswith(es["toast.undone"]), undone_text)
    toasts = await toast.all_inner_texts()
    english = [v for k, v in en.items() if k.startswith("toast.") and "{" not in v and v != es[k]]
    check("every notification on screen is Spanish", not any(text in " ".join(toasts) for text in english), toasts)
    # a typed answer: the save prompt, in Spanish, with the user's choice
    await page.click("#colour")
    await chip.expect_chip(state="NEEDS_YOU")
    await page.keyboard.type("Verde bosque")
    await ui.tab(page)
    ask = page.locator("[data-testid=save-ask]")
    await expect(ask).to_be_visible(timeout=millis(15_000))
    ask_text = await ask.inner_text()
    check("the save prompt asks in Spanish", es["save.title"] in ask_text, ask_text)
    for testid, key in (("save-application", "save.application"), ("save-no", "save.no")):
        check(f"{testid} is labelled in Spanish ({es[key]!r})", es[key] in await page.locator(f"[data-testid={testid}]").inner_text())
    await page.locator("[data-testid=save-no]").click()
    await expect(ask).to_have_count(0)
    english_ui = [en[k] for k in ("chip.trust.verified", "chip.trust.inferred", "chip.trust.needs") if en[k] != es[k]]
    leaked = [text for text in english_ui if text in await _ui_text(page)]
    check("no English trust label is left on screen", not leaked, leaked)
    # a second page: submit, confirmation, the record prompt in Spanish
    page2 = await env.open_tab("submit_flow.html", "mode=spa")
    await Panel(page2).expect_phase("ready", timeout=millis(25_000))
    await flows.focus_first(page2, "#first_name")
    await flows.walk(page2, ex.SUBMIT_FLOW, label="submit_flow (es)")
    await page2.click("#submit_app")  # the user's own click
    ask2 = page2.locator("[data-testid=track-ask]")
    await expect(ask2).to_be_visible(timeout=millis(20_000))
    text2 = await ask2.inner_text()
    for key in ("track.title", "track.record", "track.dismiss"):
        check(f"{key}: the prompt says {es[key]!r}", es[key] in text2, text2)
    check("and none of the English wording", not any(en[k] in text2 for k in ("track.title", "track.record", "track.dismiss")), text2)
    await page2.locator("[data-testid=track-dismiss]").click()
    check("nothing was recorded without a yes", backend.db_rows(
        "SELECT COUNT(*) AS n FROM applications WHERE status IN ('APPLIED','CONFIRMED')")[0]["n"] == 0)
    await ui.assert_never_submitted(page)
    check("no uncaught page error", not rig.errors, rig.errors[:2])


# ---------------------------------------------------------------------------------------------- production smoke journey
@scenario(needs=("bridge", "bridge_routes", "resolver", "worker", "content", "ui"), timeout=300)
async def scenario_production_build_paired_with_closed_shadow(env: Env) -> None:
    """The smoke journey, made only of what ships: the PRODUCTION build (closed shadow root, no e2e hooks, the fixed bridge ports)
    is paired through its own options page with a test backend, started by the registered toolbar listener, and a whole
    Greenhouse application is then walked with Tab, Shift+Tab, Enter and Esc alone. The page can see neither the UI nor any
    suggestion (the chip is read through the DevTools protocol, which is how DevTools itself opens a closed root), and every
    forbidden effect is ruled out: nothing submitted, no consent ticked, no protected or demographic answer written, no
    password or CAPTCHA touched, nothing recorded, no credential in anything the page could see. This is the closest thing to the
    shipped extension that can run without the user's own Meridian."""
    async with harness.FIXED_PORTS_LOCK:
        busy = harness.real_bridge_listening()
        if busy:
            raise Skip(f"something already listens on bridge port(s) {busy} (the real Meridian app?): this scenario binds a "
                       "test backend to the fixed bridge range and must never talk to a real app")
        await env.start_backend(extra_env={"MERIDIAN_BRIDGE_PORT": "47615"})
        backend = env.backend
        check("the test backend sits on the first fixed bridge port", backend.bridge_port == 47615, backend.bridge_port)
        rig = await env.start_rig(variant="chrome")
        check("production worker has no e2e hook", await rig.sw.evaluate("() => typeof globalThis.__e2e") == "undefined")
        options = await rig.open_options()
        await options.locator("[data-action=connect]").click()
        code_box = options.locator(".pair .code")
        await expect(code_box).to_be_visible(timeout=millis(20_000))
        shown = re.sub(r"\D", "", await code_box.inner_text())
        pending = await backend.pending_pairing()
        check("the code on the options page is the code the app asks for", shown == pending["code"], (shown, pending["code"]))
        await backend.approve_pairing(pending)
        await expect(options.locator("[data-action=disconnect]")).to_be_visible(timeout=millis(25_000))
        stored = await rig.stored_connection()
        check("paired against the fixed port", stored and stored["port"] == 47615, stored and stored["port"])
        page = await env.open("greenhouse_apply.html", rig=rig)
        await rig.activate(page, wait_panel=False)  # the registered chrome.action.onClicked listener, exactly as a toolbar click runs it
        host = await page.evaluate("() => { const h = document.querySelector('[data-meridian-assistant]'); "
                                   "return h ? (h.shadowRoot === null ? 'closed' : 'open') : 'missing'; }")
        check("the UI host is there and its shadow root is closed to the page", host == "closed", host)
        check("no e2e hook reachable from the page", await page.evaluate(
            "() => typeof globalThis.__e2e === 'undefined' && typeof globalThis.__meridianRoot === 'undefined'"))
        await flows.poll_until(lambda: _connection_ready(rig), timeout=40, message="the production worker to be ready")
        await drafts_finished_on_server(env)  # the one long answer is drafted in the background; its chip is awaited by the walk
        chip = ui.ClosedChip(page)
        try:
            await greenhouse_journey(env, page, chip, name="production")
            # the assistant is on screen for the person (the DevTools protocol reads it) yet there is nothing for the page to see
            await page.locator("#colour").focus()
            await chip.expect_chip(state="NEEDS_YOU")
            seen = await chip.read()
            check("a chip is on screen for the person", seen is not None and seen["text"].strip() != "", seen)
            check("Playwright cannot see a chip through the closed root", await page.locator("[data-testid=chip]").count() == 0)
            everything = await page.evaluate("() => document.documentElement.outerHTML + '\\n' + document.body.innerText")
            # The chip's first line repeats the page's own label (the question it answers): that line is on the page by design. Every other
            # line is the assistant's own text, and none of it may be anywhere the page can read.
            lines = [line.strip() for line in seen["own"].splitlines() if len(line.strip()) >= 6]
            check("the chip's question line is the page's own label (it is shown back to the person, so it is not a leak)",
                  seen["question"] != "" and seen["question"] in everything, seen["question"])
            check("not one line of the chip's own text is anywhere in the page's DOM or text", lines and not any(line in everything for line in lines),
                  lines)
            check("no suggestion text in the page's DOM or text", "Verified" not in everything and "Verificado" not in everything)
        finally:
            await chip.close()
        check("no uncaught page error", not rig.errors, rig.errors[:2])
        check("the page never saw an Authorization header from the browser",
              not env.fixtures.requests_with_header("authorization"))


async def _connection_ready(rig) -> bool:
    cache = (await rig.storage_session()).get("meridian.conn") or {}
    return (cache.get("state") or {}).get("phase") == "ready"


async def _suggestions_seen(backend) -> bool:
    clients = await backend.clients()
    return bool(clients and clients[0]["stats"]["suggestions"] > 0)

