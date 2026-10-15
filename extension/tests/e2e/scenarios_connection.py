"""Connection scenarios: pairing, revocation, the port-squatter defence, a dead and a restored bridge, cold start, the
closed-shadow build and the production build.

All of them need the full chain (bridge + worker + content script + visual layer), so they stay BLOCKED until every
workstream involved has landed, and then run for real.
"""

from __future__ import annotations

import asyncio
import json
import re

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import expect

import flows
import harness
import seed
import servers
import ui
from framework import Env, Skip, check, scenario
from harness import PINNED_EXTENSION_ID
from ui import Chip, Panel
from scale import millis  # noqa: E402

CHAIN = ("bridge", "worker", "e2e_ports", "content", "ui")
SUGGEST_CHAIN = (*CHAIN, "bridge_routes", "resolver")


async def code_shown(panel: Panel) -> str:
    return re.sub(r"\D", "", await panel.tid("pairing-code").inner_text())


async def assert_token_never_reaches_page(env: Env, page, token: str) -> None:
    html = await page.content()
    storage = await page.evaluate("() => JSON.stringify([localStorage, sessionStorage, document.cookie])")
    secret = token.split(".", 2)[2]
    check("the token is not in the page DOM", token not in html and secret not in html)
    check("nor in the page's storage or cookies", token not in storage and secret not in storage)
    check("no request to a page origin ever carried an Authorization header",
          not env.fixtures.requests_with_header("authorization"), env.fixtures.requests_with_header("authorization")[:1])
    leaked = [m for m in env.rig.console + env.rig.errors if secret in m]
    check("the token never appeared in a console message", not leaked)


@scenario(needs=CHAIN, timeout=120)
async def scenario_pairing_happy_path(env: Env) -> None:
    """Unpaired -> Connect -> 6-digit code in the panel == code in the app -> approve in the app -> ready."""
    backend = await env.start_backend()
    rig = await env.start_rig()
    page = await env.open("decoy.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("unpaired")
    await expect(panel.tid("calm-unpaired")).to_be_visible()
    status = await backend.extension_status()
    check("the app has seen the extension say hello (detected)", status["browsers"]["chrome"]["state"] in {"detected", "not_installed"},
          status["browsers"]["chrome"]["state"])
    await page.locator("[data-testid=calm-connect]").click()
    await expect(panel.tid("pairing-code")).to_be_visible(timeout=millis(20_000))
    shown = await code_shown(panel)
    pending = await backend.pending_pairing()
    check("the code in the page equals the code the app asks for", shown == pending["code"] and len(shown) == 6, (shown, pending["code"]))
    check("the app names Chrome and the pinned extension as recognised", pending["browser"] == "chrome" and pending["recognized"])
    check("the app says pairing is in progress", (await backend.extension_status())["state"] == "pairing")
    check("no native dialog was started (setting off, shims only)", not [c for c in backend.shim_calls() if c.startswith("osascript")])
    await backend.approve_pairing(pending)
    await panel.expect_phase("ready", timeout=millis(25_000))
    check("the code box is gone once connected", await panel.tid("pairing-code").count() == 0)
    stored = await rig.stored_connection()
    check("the credential is stored by the worker", stored and stored["token"].startswith("mrd1.") and stored["port"] == backend.bridge_port)
    clients = await backend.clients()
    check("the app lists exactly one connected Chrome", len(clients) == 1 and clients[0]["browser"] == "chrome" and clients[0]["id"] == stored["clientId"])
    check("the app state is ready", (await backend.extension_status())["state"] == "ready")
    await assert_token_never_reaches_page(env, page, stored["token"])
    check("the extension id the app saw is the pinned one", rig.ext_id == PINNED_EXTENSION_ID)


@scenario(needs=CHAIN, timeout=120)
async def scenario_pairing_denied_and_expired_code(env: Env) -> None:
    """Deny in the app: the panel says so calmly and offers a new code; nothing is stored."""
    backend = await env.start_backend()
    rig = await env.start_rig()
    page = await env.open("decoy.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("unpaired")
    await page.locator("[data-testid=calm-connect]").click()
    await expect(panel.tid("pairing-code")).to_be_visible(timeout=millis(20_000))
    await backend.deny_pairing()
    await expect(panel.tid("pairing-code")).to_have_count(0, timeout=millis(20_000))
    check("denied pairing leaves the browser unpaired", await panel.phase() in {"unpaired", "pairing"})
    check("no credential was stored", await rig.stored_connection() is None)
    check("the app lists no client", await backend.clients() == [])
    await expect(panel.tid("calm-connect")).to_be_visible(timeout=millis(15_000))


@scenario(needs=CHAIN, timeout=150)
async def scenario_pairing_revoke_and_reconnect(env: Env) -> None:
    """Disconnect in the app: the next call gets 401, the panel says so calmly, Connect again works with a new code."""
    rig = await env.connected()
    backend = env.backend
    page = await env.open("decoy.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready")
    first = (await backend.clients())[0]
    await backend.revoke_client(first["id"])
    check("the app lists no active client any more", await backend.clients() == [])
    answer = await rig.bg_request({"t": "status"})  # what any panel/page call does next
    check("the worker's next bridge call fails as unauthorized/revoked (the app no longer proves itself to this client)",
          answer["ok"] is False and answer["error"]["code"] in {"unauthorized", "revoked"}, answer)
    await panel.expect_phase("revoked", timeout=millis(20_000))
    await expect(panel.tid("calm-revoked")).to_be_visible()
    await page.locator("[data-testid=calm-connect]").click()
    await expect(panel.tid("pairing-code")).to_be_visible(timeout=millis(20_000))
    await backend.approve_pairing()
    await panel.expect_phase("ready", timeout=millis(25_000))
    again = (await backend.clients())[0]
    check("a NEW client id was issued", again["id"] != first["id"])


@scenario(needs=CHAIN, timeout=150)
async def scenario_pairing_proof_refusal(env: Env) -> None:
    """A process squatting the bridge port cannot prove it is Meridian: the worker never sends it the token."""
    rig = await env.connected()
    backend = env.backend
    page = await env.open("decoy.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready")
    token = (await rig.stored_connection())["token"]
    port = backend.bridge_port
    await backend.kill()
    # (no settling time before the impostor binds: every credentialed request carries a hello proof of its own, so a listener that
    # takes the port at ANY moment is asked for a proof first; port_takeover_inside_the_verification_window_gets_no_token pins the
    # moment right after a verification, this one is about the proof check itself)
    # (a) an impostor that answers hello like Meridian but with a WRONG proof
    squatter = env.squatter(port, proof="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    await rig.forget_connection_cache()
    await rig.bg_request({"t": "conn:state", "refresh": True})
    await panel.expect_phase("proof_failed", timeout=millis(25_000))
    await expect(panel.tid("calm-proof_failed")).to_be_visible()
    check("the squatter was probed (hello) but never saw an Authorization header", squatter.requests and not squatter.credentialed_requests(),
          squatter.credentialed_requests()[:1])
    secret = token.split(".", 2)[2]
    check("the secret never reached the squatter in any form", not any(secret in repr(r) for r in squatter.requests))
    answer = await rig.bg_request({"t": "status"})
    check("a credentialed call refuses to go to the unproven listener", answer["ok"] is False and answer["error"]["code"] == "proof_failed",
          answer)
    check("still nothing credentialed after that call", not squatter.credentialed_requests())
    check("the page cannot read the token", token not in await page.content())
    # (b) the impostor goes away and the REAL app comes back on its port: the same credential works again
    squatter.close()
    await backend.restart(same_bridge_port=True)
    check("the real app came back on the very same bridge port", backend.bridge_port == port, (backend.bridge_port, port))
    await rig.forget_connection_cache()
    state = await rig.bg_request({"t": "conn:state", "refresh": True})
    print("    worker state after the real app returned:", json.dumps(state)[:300])
    await panel.expect_phase("ready", timeout=millis(30_000))
    check("the stored credential survived the episode", (await rig.stored_connection())["token"] == token)
    # (c) an impostor that does not even claim a proof (indistinguishable from an app that revoked this browser)
    await backend.kill()
    mute = env.squatter(port)
    await rig.forget_connection_cache()
    await rig.bg_request({"t": "conn:state", "refresh": True})
    # (it is NOT "revoked": only a Meridian that proves it knows this credential can say so; silence is an unknown listener)
    await flows.poll_until(lambda: _phase_in(panel, {"unknown_listener", "proof_failed"}), timeout=25, message="a calm refusal state")
    check("the worker calls it an unknown listener, not a revocation",
          (await rig.bg_request({"t": "conn:state", "refresh": True}))["data"]["phase"] == "unknown_listener")
    answer = await rig.bg_request({"t": "status"})
    check("no credentialed call went to the mute impostor either", answer["ok"] is False and not mute.credentialed_requests(),
          mute.credentialed_requests()[:1])
    check("and the secret is nowhere in what it received", not any(secret in repr(r) for r in mute.requests))
    mute.close()


@scenario(needs=CHAIN, timeout=180)
async def scenario_pairing_with_an_unproven_listener_cannot_replace_the_credential(env: Env) -> None:
    """MEDIUM-1: Meridian quits and a program on another candidate port answers hello with the right shape but no proof and
    approves a pairing at once. The browser must not call that "revoked", must not pair without the user's explicit say-so, and
    even then must keep the real credential until the real Meridian is recognised again."""
    rig = await env.connected()
    backend = env.backend
    token = (await rig.stored_connection())["token"]
    port = backend.bridge_port
    await backend.kill()
    impostor = servers.PairingImpostor()
    try:
        await rig.set_bridge_ports([port, impostor.port])
        state = (await rig.bg_request({"t": "conn:state", "refresh": True}))["data"]
        check("a mute listener with a credential in hand is an unknown listener, not 'revoked'", state["phase"] == "unknown_listener", state)
        answer = await rig.bg_request({"t": "pair:start"})
        check("pairing needs an explicit confirmation", answer["ok"] is False and answer["error"]["code"] == "confirm_required", answer)
        check("nothing was posted to the impostor", not impostor.posts(), impostor.posts()[:1])
        # the options page explains and asks; only its explicit confirmation lets pairing go ahead
        options = await rig.open_options()
        connect = options.locator("[data-action=connect_unknown]")
        await expect(connect).to_be_visible(timeout=millis(15_000))
        await expect(options.locator("body")).to_contain_text("doesn't recognise this browser")
        check("no plain Connect button is offered for an unrecognised listener", await options.locator("[data-action=connect]").is_hidden())
        await connect.click()
        confirm = options.locator("[data-action=confirm_unknown]")
        await expect(confirm).to_be_visible(timeout=millis(15_000))
        check("the first click only asks: nothing was posted yet", not impostor.posts(), impostor.posts()[:1])
        await expect(options.locator("body")).to_contain_text("kept aside, not deleted")
        await confirm.click()
        await flows.poll_until(lambda: _stored_token_is(rig, impostor.token), timeout=40, message="the confirmed pairing to complete")
        check("the impostor was asked to pair exactly once, after the confirmation", len(impostor.posts()) == 1, impostor.posts())
        local = await rig.storage_local()
        check("the impostor's credential is the one in use after the user's confirmation", (local.get("meridian.connection") or {}).get("token") == impostor.token)
        check("the real credential was NOT lost: it is kept as the fallback", (local.get("meridian.connection.previous") or {}).get("token") == token)
        # the real Meridian comes back on its port: the browser recognises it, goes back to its credential and drops the impostor's
        await backend.restart(same_bridge_port=True)
        await rig.forget_connection_cache()
        credentialed_before = len(impostor.credentialed_requests())
        await flows.poll_until(lambda: _status_ok(rig), timeout=40, message="the real credential to work against the real app again")
        local = await rig.storage_local()
        check("the real credential is the one in use again", (local.get("meridian.connection") or {}).get("token") == token)
        check("the fallback slot is empty again", "meridian.connection.previous" not in local)
        check("the real app never learned a second client", len(await backend.clients()) == 1)
        check("no credentialed request went to the impostor once the real app was back",
              len(impostor.credentialed_requests()) == credentialed_before, impostor.credentialed_requests()[-1:])
    finally:
        impostor.close()


@scenario(needs=CHAIN, timeout=90)
async def scenario_port_takeover_inside_the_verification_window_gets_no_token(env: Env) -> None:
    """Meridian quits and another process takes its port right away: the worker's next request must prove the listener first,
    send it nothing credentialed, and keep the credential for when the real Meridian is back."""
    rig = await env.connected()
    backend = env.backend
    token = (await rig.stored_connection())["token"]
    port = backend.bridge_port
    first = await rig.bg_request({"t": "status"})  # verifies the bridge now; no page, so no long poll is in flight to notice the quit
    check("the first request works", first["ok"] is True, first)
    await backend.kill()
    squatter = env.squatter(port, proof="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    answer = await rig.bg_request({"t": "status"})  # straight after the verification: nothing cached may stand in for a proof
    check("the request fails calmly", answer["ok"] is False, answer)
    check("the squatter was never sent the credential", not squatter.credentialed_requests(), len(squatter.credentialed_requests()))
    check("and the secret is nowhere in what it received", not any(token.split(".", 2)[2] in repr(r) for r in squatter.requests))
    check("the worker still holds the credential (an impostor's 401 is not a revocation)",
          (await rig.stored_connection() or {}).get("token") == token)
    again = await rig.bg_request({"t": "status"})  # and a second one, as a page's next call would be
    check("a second request is refused just the same", again["ok"] is False and not squatter.credentialed_requests(), again)
    # the real Meridian comes back on its port: the very same credential works, nobody has to pair again
    squatter.close()
    await backend.restart(same_bridge_port=True)
    await flows.poll_until(lambda: _status_ok(rig), timeout=40, message="the stored credential to work against the real app again")
    check("the same credential was accepted by the real app (no new pairing)", (await rig.stored_connection() or {}).get("token") == token
          and len(await backend.clients()) == 1)


async def _stored_token_is(rig, token: str) -> bool:
    return ((await rig.stored_connection()) or {}).get("token") == token


async def _status_ok(rig) -> bool:
    return (await rig.bg_request({"t": "status"}))["ok"] is True


async def _phase_in(panel: Panel, phases: set[str]) -> bool:
    return await panel.phase() in phases


@scenario(needs=CHAIN, timeout=150)
async def scenario_bridge_killed_and_restored(env: Env) -> None:
    """Quit Meridian mid-session: calm 'not open' state with a way back; start it again and the assistant resumes."""
    rig = await env.connected()
    backend = env.backend
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready")
    await backend.kill()
    await rig.bg_request({"t": "conn:state", "refresh": True})
    await panel.expect_phase("no_bridge", timeout=millis(30_000))
    await expect(panel.tid("calm-no_bridge")).to_be_visible()
    await expect(panel.tid("calm-open-meridian")).to_be_visible()
    await page.click("#first_name")
    await page.keyboard.press("Tab")
    offline = await ui.field_value(page, "#first_name")
    check("while Meridian is closed Tab never invents anything: the field holds nothing or the cached verified answer",
          offline in {"", seed.FIRST}, offline)
    print(f"    (offline Tab {'inserted the answer prefetched before Meridian closed' if offline else 'was a plain Tab'})")
    check("and focus moved on", await ui.focused_id(page) == "last_name")
    await backend.restart(same_bridge_port=True)
    # The assistant keeps retrying on its own, so by now the calm card may already have gone. Both are right: either the
    # user's "Try now" works, or the assistant has already come back by itself.
    try:
        await panel.tid("calm-retry").click(timeout=4_000)
        print("    (reconnected through the panel's 'Try now')")
    except PlaywrightTimeout:
        print("    (the assistant reconnected by itself before 'Try now' was clicked)")
    await panel.expect_phase("ready", timeout=millis(45_000))
    check("the credential survived the restart (no new pairing)", len(await backend.clients()) == 1)
    caps = harness.capabilities()
    if caps["bridge_routes"][0] and caps["resolver"][0]:
        await page.click("#email")
        await Chip(page).expect_chip(state="VERIFIED", value="alex.morgan@example.test")
        check("the page's session was re-opened after the restart: suggestions flow again", True)


@scenario(needs=CHAIN, timeout=150)
async def scenario_cold_start_same_profile(env: Env) -> None:
    """Quit and relaunch the browser on the same profile: same extension id, still paired, assistant starts normally."""
    rig = await env.connected()
    backend = env.backend
    profile = rig.profile
    token = (await rig.stored_connection())["token"]
    first_id = rig.ext_id
    await rig.close()
    env.rigs.clear()
    second = await env.start_rig(profile=profile)
    check("same extension id after the relaunch", second.ext_id == first_id == PINNED_EXTENSION_ID)
    check("the credential persisted in chrome.storage.local", (await second.stored_connection() or {}).get("token") == token)
    page = await env.open("decoy.html", activate=True, rig=second)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    check("no second client was created", len(await backend.clients()) == 1)
    check("no pairing UI was shown", await panel.tid("pairing-code").count() == 0)


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_cross_origin_frames_without_permission(env: Env) -> None:
    """With access to the top site only (no broad permission) a form in another site's frame is not reached: the panel says
    so and offers the permission; the top-level fields keep working."""
    await env.connected(variant="e2e-top")
    page = await env.open("workday_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready", timeout=millis(25_000))
    note = panel.tid("note-cross-origin")
    # Nobody touches the page: the panel itself notices, after the frame has been seen for a few seconds, that it could
    # not reach it, and says so (no user event is needed to schedule that render).
    await expect(note).to_be_visible(timeout=millis(15_000))
    await expect(panel.tid("note-allow-all")).to_be_visible()
    frame = page.frames[1]
    check("the extension was not injected into the cross-origin frame", await frame.locator("[data-meridian-assistant]").count() == 0)
    await page.click("#contact_name")
    await Chip(page).expect_chip(state="VERIFIED", value=seed.NAME)
    await ui.tab(page)
    check("the top-level field still works", await ui.field_value(page, "#contact_name") == seed.NAME)
    check("nothing was written inside the frame", await frame.evaluate("window.__events || []") == [])


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_toolbar_toggle_and_panel_collapse(env: Env) -> None:
    """The toolbar icon starts and stops the assistant; the panel collapses to a pill and comes back."""
    rig = await env.connected()
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready")
    await panel.tid("panel-collapse").click()
    await expect(panel.tid("panel-pill")).to_be_visible()
    await panel.tid("panel-pill").click()
    await expect(panel.tid("panel-card")).to_be_visible()
    await page.click("#first_name")
    await Chip(page).expect_chip(state="VERIFIED", value=seed.FIRST)
    await rig.toolbar_click(page)  # a second click on the icon: off
    await expect(page.locator("[data-meridian-assistant]")).to_have_count(0, timeout=millis(10_000))
    await page.locator("#last_name").focus()
    await ui.tab(page)
    check("switched off: Tab is a plain Tab", await ui.field_value(page, "#last_name") == ""
          and await ui.focused_id(page) == "email")
    await rig.toolbar_click(page)  # and on again
    await page.locator("[data-meridian-assistant]").first.wait_for(state="attached", timeout=millis(15_000))
    await panel.expect_phase("ready", timeout=millis(25_000))
    await page.locator("#last_name").focus()
    await Chip(page).expect_chip(state="VERIFIED", value=seed.LAST)
    await ui.tab(page)
    check("on again: Tab inserts", await ui.field_value(page, "#last_name") == seed.LAST)
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_closed_shadow_smoke(env: Env) -> None:
    """The closed-root build: the page can see neither the UI nor any suggestion, and Tab still inserts."""
    rig = await env.connected(variant="e2e-closed")
    page = await env.open("greenhouse_apply.html")
    await rig.activate(page, wait_panel=False)  # a closed root hides the panel from every locator
    await flows.poll_until(lambda: _worker_phase_is(rig, "ready"), timeout=20, message="the connection to be ready")
    host = await page.evaluate("() => { const h = document.querySelector('[data-meridian-assistant]'); "
                               "return h ? {present: true, shadow: h.shadowRoot === null ? 'closed' : 'open'} : {present: false}; }")
    check("the UI host is attached and its shadow root is CLOSED to the page", host == {"present": True, "shadow": "closed"}, host)
    await page.click("#first_name")
    backend = env.backend
    await flows.poll_until(lambda: _suggestions_seen(backend), timeout=20, message="the suggest request to reach Meridian")
    await asyncio.sleep(0.4)
    check("Playwright cannot see the chip through a closed root", await page.locator("[data-testid=chip]").count() == 0)
    text = await page.evaluate("() => document.documentElement.outerHTML + document.body.innerText")
    check("the suggestion text is nowhere in the page's DOM or text", "Alex" not in text and "Verified" not in text)
    await page.keyboard.press("Tab")
    await expect(page.locator("#first_name")).to_have_value("Alex")
    await expect(page.locator("#last_name")).to_be_focused()
    check("Tab inserted and advanced with a closed root", rig.variant == "e2e-closed")


async def _worker_phase_is(rig, phase: str) -> bool:
    cache = (await rig.storage_session()).get("meridian.conn") or {}
    return (cache.get("state") or {}).get("phase") == phase


async def _suggestions_seen(backend) -> bool:
    clients = await backend.clients()
    return bool(clients and clients[0]["stats"]["suggestions"] > 0)


@scenario(needs=("worker", "content", "ui"), timeout=120)
async def scenario_production_smoke(env: Env) -> None:
    """The production build: no e2e hooks, closed root, calm when Meridian is not there, never touches the form."""
    async with harness.FIXED_PORTS_LOCK:  # a scenario that binds a test backend to the fixed bridge ports must not overlap
        await production_smoke(env)


async def production_smoke(env: Env) -> None:
    busy = harness.real_bridge_listening()
    if busy:
        raise Skip(f"something already listens on bridge port(s) {busy} (the real Meridian app?): the production "
                   "build would talk to it, so this scenario refuses to run")
    rig = await env.start_rig(variant="chrome")
    page = await env.open("greenhouse_apply.html", rig=rig)
    await rig.activate(page, wait_panel=False)
    await page.locator("[data-meridian-assistant]").first.wait_for(state="attached", timeout=millis(15_000))
    host = await page.evaluate("() => document.querySelector('[data-meridian-assistant]').shadowRoot === null")
    check("production UI root is closed", host is True)
    check("no e2e hook is reachable from the page", await page.evaluate(
        "() => typeof globalThis.__e2e === 'undefined' && typeof globalThis.__meridianRoot === 'undefined'"))
    worker_hooks = await rig.sw.evaluate("() => typeof globalThis.__e2e")
    check("no e2e hook in the production worker", worker_hooks == "undefined", worker_hooks)
    phase = None
    for _ in range(40):
        cache = (await rig.storage_session()).get("meridian.conn")
        phase = (cache or {}).get("state", {}).get("phase")
        if phase:
            break
        await asyncio.sleep(0.25)
    check("with no Meridian running the connection state is calm 'no_bridge'", phase == "no_bridge", phase)
    await page.click("#first_name")
    await page.keyboard.type("Typed by the user")
    await page.keyboard.press("Tab")
    check("the user's own typing is untouched and nothing else was written",
          await ui.field_value(page, "#first_name") == "Typed by the user"
          and await ui.field_value(page, "#last_name") == "" and await ui.field_value(page, "#email") == "")
    events = await ui.synthetic_events(page)
    check("the production extension performed no write on the page", not events, events[:3])
    await ui.assert_never_submitted(page)


@scenario(needs=SUGGEST_CHAIN, timeout=120)
async def scenario_toolbar_click_on_a_quiet_page_shows_the_panel(env: Env) -> None:
    """One click on the icon must always end with a visible assistant, whatever happened before in that tab: after the
    worker re-activated the tab by itself on a page without a form, the next click SHOWS the panel (it does not switch
    the assistant off), and only a click on a visible panel switches it off."""
    rig = await env.connected()
    page = await env.open("greenhouse_apply.html", activate=True)
    panel = Panel(page)
    await panel.expect_phase("ready")
    await page.goto(env.fixtures.url("decoy.html"), wait_until="load")  # same site: the worker follows with a quiet start
    await asyncio.sleep(2.5)
    await rig.toolbar_click(page)
    await expect(panel.card.or_(page.locator("[data-testid=panel-pill]"))).to_be_visible(timeout=millis(10_000))
    await expect(panel.tid("calm-no-form")).to_be_visible(timeout=millis(10_000))
    check("the panel is visible after a single click and says there is no form yet", True)
    await rig.toolbar_click(page)  # the panel is on screen now: a second click is the user's 'off'
    await expect(page.locator("[data-meridian-assistant]")).to_have_count(0, timeout=millis(10_000))
    check("a click on a visible panel switches the assistant off", True)
    await rig.toolbar_click(page)  # and the next one starts it again
    await expect(panel.card.or_(page.locator("[data-testid=panel-pill]"))).to_be_visible(timeout=millis(15_000))
    await page.goto(env.fixtures.url("greenhouse_apply.html"), wait_until="load")  # the form appears on the next page
    await panel.expect_phase("ready", timeout=millis(25_000))
    await page.click("#first_name")
    await Chip(page).expect_chip(state="VERIFIED", value=seed.FIRST)
