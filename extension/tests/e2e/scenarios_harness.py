"""Self-checks of the harness, the fixtures, the seed and the fake model. They need no finished extension, so they are
the first thing to run in a fresh checkout and the reason a later failure can be trusted to be a product failure.

* harness_launch            the extension loads under the chosen recipe: pinned id, worker, manifest permissions
* production_build_contract production builds carry no e2e hooks; e2e and production manifests differ only in loopback
* fixtures_scan             the repository's own scanner (observation.js) sees every fixture exactly as designed
* seed_oracle               the real backend + the real answer engine give each fixture field the outcome that the
                            acceptance tables (expectations.py) promise
* llm_contract              the fake loopback model passes the real draft + independent-review pipeline, and the
                            employer research only reaches the draft when the backend supplies it
* bridge_contract           protocol-level guard rules of the real bridge (Origin, Host, auth, hello proof, pairing)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import re
import selectors
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler

import expectations as ex
import fake_llm
import seed
import servers
from framework import Env, check, scenario
from harness import PINNED_EXTENSION_ID, REPO, build_extension, manifest_of
from scale import millis  # noqa: E402

OBSERVATION_JS = (REPO / "backend" / "jobagent" / "automation" / "observation.js").read_text()
GOLDEN = REPO / "extension" / "tests" / "fixtures" / "scan_golden.json"

# Every fixture page with the selector to wait for before scanning (pages that render late).
FIXTURES: dict[str, str | None] = {
    "greenhouse_apply.html": "#application_form", "lever_apply.html": "#lever_form", "ashby_apply.html": "#ashby_form",
    "workday_apply.html": "#ats_frame", "react_form.html": "#react_form", "masked_phone.html": "#contact_form",
    "contenteditable.html": "#rich_form", "choices.html": "#choices_form", "conditional.html": "#conditional_form",
    "consent_demographics.html": "#eeo_form", "file_upload.html": "#docs_form", "prefilled.html": "#prefilled_form",
    "spa_wizard.html": "#wizard", "spa_wizard.html?step=2": "#email", "submit_flow.html": "#quick_form",
    "password_signup.html": "#signup_form", "captcha_gate.html": "#gate_form", "decoy.html": "main",
    "shadow_form.html": "apply-widget", "hear_about.html": "#hear_form", "ashby_apply.html?long=1": "#ashby_form",
    "workday_apply.html?bare=1": "#ats_frame", "workday_apply.html?tail=field": "#ats_frame",
    "widget_patterns.html": "#widgets_form",
}


# ---------------------------------------------------------------------------------------------- scanning helpers
MODES = ("plain", "widgets")  # how observation.js is called: no option (the Playwright filler) and {widgets: true} (the extension)


async def scan_frames(page, *, widgets: bool = True) -> list[dict]:
    """Run the repository's observation.js in every frame of the page, the way a caller does. ``widgets=True`` is how the extension
    calls it (extension/src/content/scan.ts OBSERVE_OPTIONS: ``{widgets: true}``, which also recognises dropzone file inputs, listbox
    buttons and Yes/No button pairs and names a file input by what a person reads); ``widgets=False`` is the Playwright filler's call
    without options. The default is the extension's, because that is the scan whose results the suite's other checks stand on."""
    call = "({widgets: true})" if widgets else "()"
    snapshots = []
    for frame in page.frames:
        try:
            snapshot = await frame.evaluate("(" + OBSERVATION_JS.strip() + ")" + call)
        except Exception:  # noqa: BLE001 - e.g. about:blank
            continue
        snapshot["frame_url"] = frame.url
        snapshots.append(snapshot)
    return snapshots


def reduce_element(element: dict) -> list:
    return [element["id"] or element["name"], element["role"], element["type"], element["label"], element["group"],
            bool(element["required"]), len(element["options"]), sorted(element["operations"])]


def reduce_scan(snapshots: list[dict]) -> dict:
    frames = []
    for snapshot in snapshots:
        frames.append({
            "frame": snapshot["frame_url"].rsplit("/", 1)[-1].split("?")[0],
            "flags": [bool(snapshot["challenge"]), bool(snapshot["login"]), bool(snapshot["mfa"])],
            "controls": [reduce_element(e) for e in snapshot["elements"] if e["role"] not in {"button", "link"}],
        })
    return {"frames": frames}


async def scan_fixture(page, env: Env, name: str, ready: str | None) -> dict:
    """The reduced scan of one fixture in BOTH modes (one document, one load)."""
    file, _, query = name.partition("?")
    await page.goto(env.fixtures.url(file, query), wait_until="load")
    if ready:
        await page.wait_for_selector(ready, state="attached", timeout=millis(8_000))
        await page.wait_for_timeout(200)
    return {mode: reduce_scan(await scan_frames(page, widgets=mode == "widgets")) for mode in MODES}


# ---------------------------------------------------------------------------------------------- scenarios
@scenario(timeout=120)
async def scenario_harness_launch(env: Env) -> None:
    """The extension loads under the selected launch recipe and the worker is the real, pinned one."""
    rig = await env.start_rig()
    manifest = manifest_of("e2e")
    check("extension id equals the id pinned by manifest-key.txt", rig.ext_id == PINNED_EXTENSION_ID, rig.ext_id)
    check("service worker discovered by polling", rig.sw.url.endswith("/background.js"), rig.sw.url)
    check("manifest is MV3", manifest["manifest_version"] == 3)
    hosts = manifest["host_permissions"]
    check("e2e build can reach both loopback fixture origins", "http://127.0.0.1/*" in hosts and "http://localhost/*" in hosts)
    check("activeTab + scripting + storage requested", {"activeTab", "scripting", "storage"} <= set(manifest["permissions"]))
    runtime = await rig.sw.evaluate("() => ({id: chrome.runtime.id, version: chrome.runtime.getManifest().version})")
    check("worker reports the pinned id", runtime["id"] == PINNED_EXTENSION_ID, runtime)
    page = await env.open("decoy.html")
    tab = await rig.tab_id(page)
    check("tab id resolved through the worker", isinstance(tab, int) and tab > 0, tab)
    listeners = await rig.sw.evaluate("() => chrome.action.onClicked.hasListeners()")
    if listeners:
        print("    toolbar listener registered at worker top level (production code path is dispatchable)")
    else:
        print("    NOTE toolbar listener not registered yet (workstream C): activation scenarios stay BLOCKED")
    await page.close()


def connections_taken_in_a_burst(server_class, count: int = 80, wait: float = 2.0) -> int:
    """How many of ``count`` simultaneous connections the kernel completes for a server of this class while NOTHING accepts them
    (the server is built but never serves): exactly what the listen backlog allows. A page makes dozens of requests at once and a
    loaded machine accepts them late; with the default backlog of 5 the rest are dropped or reset and the page comes up empty."""
    server = server_class(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    sockets, selector, done = [], selectors.DefaultSelector(), 0
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.setblocking(False)
            sock.connect_ex(("127.0.0.1", port))
            sockets.append(sock)
            selector.register(sock, selectors.EVENT_WRITE)
        pending = set(sockets)
        end = time.monotonic() + wait
        while pending and time.monotonic() < end:
            for key, _ in selector.select(timeout=0.1):
                if key.fileobj in pending:
                    if key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                        done += 1
                    pending.discard(key.fileobj)
                    selector.unregister(key.fileobj)
    finally:
        for sock in sockets:
            sock.close()
        selector.close()
        server.server_close()
    return done


def load_smoke_module():
    """extension/tests/unit/smoke_chromium.py (a script with its own fixture servers) as a module: nothing runs on import."""
    path = REPO / "extension" / "tests" / "unit" / "smoke_chromium.py"
    spec = importlib.util.spec_from_file_location("smoke_chromium_for_e2e", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@scenario(timeout=60)
async def scenario_loopback_servers_take_a_burst_of_connections(env: Env) -> None:
    """Every loopback server of the browser suites keeps a deep listen backlog (TEST-01): a loaded machine accepts a page's dozens of
    simultaneous requests late, and the default backlog of 5 drops the rest, so a page came up empty for a reason that is not the product's."""
    smoke = load_smoke_module()
    for label, server_class in (("the fixture/company/squatter servers (e2e/servers.py)", servers._Server),
                                ("the fake writing model (e2e/fake_llm.py)", fake_llm._Server),
                                ("the fake bridge and pages of the Chromium smoke (unit/smoke_chromium.py)", smoke.RoomyServer)):
        taken = connections_taken_in_a_burst(server_class)
        check(f"{label}: 80 connections in a burst are all completed", taken == 80, f"{taken} of 80 (backlog {server_class.request_queue_size})")
    # the servers a scenario really starts are of those classes (a new server built from the bare standard class would not be)
    fixtures = servers.FixtureServers()
    company = servers.CompanySite()
    llm = fake_llm.FakeLLM().start()
    try:
        for label, server in (("fixture origin", fixtures._servers[0]), ("frame origin", fixtures._servers[1]),
                              ("employer site", company.server), ("fake model", llm.server)):
            check(f"the {label} server is built with the deep backlog", server.request_queue_size >= 256, server.request_queue_size)
    finally:
        fixtures.close()
        company.close()
        llm.stop()


@scenario(timeout=120)
async def scenario_production_build_contract(env: Env) -> None:
    """Production bundles contain no e2e hooks and differ from the e2e manifest only in loopback host permissions."""
    chrome, e2e = build_extension("chrome"), build_extension("e2e")
    prod, test = manifest_of("chrome"), manifest_of("e2e")
    for key in sorted(set(prod) | set(test)):
        if key == "host_permissions":
            continue
        check(f"manifest key {key!r} identical in production and e2e builds", prod.get(key) == test.get(key),
              f"{prod.get(key)!r} vs {test.get(key)!r}")
    extra = set(test["host_permissions"]) - set(prod["host_permissions"])
    check("e2e adds only the localhost loopback origin", extra <= {"http://localhost/*"}, extra)
    check("production host permissions never include http://*/* or https://*/*",
          not {"http://*/*", "https://*/*", "<all_urls>"} & set(prod["host_permissions"]))
    check("production keeps the pinned key", bool(prod.get("key")) and prod.get("key") == test.get("key"))
    for bundle in ("background.js", "content.js", "options.js", "hint.js"):
        path = chrome / bundle
        if not path.exists() or path.stat().st_size < 200:
            continue
        text = path.read_text()
        for marker in ("meridian.e2e.ports", "__e2e", "e2e:activate", "__meridianRoot"):
            check(f"{bundle}: no e2e hook {marker!r} in the production build", marker not in text)
    connection = EXT_SRC / "shared" / "connection.ts"
    if connection.exists() and "meridian.e2e.ports" in connection.read_text():
        check("the e2e build keeps its port override (so the stripping check above is meaningful)",
              "meridian.e2e.ports" in (e2e / "background.js").read_text())


EXT_SRC = REPO / "extension" / "src"


@scenario(timeout=240)
async def scenario_fixtures_scan(env: Env) -> None:
    """observation.js (the scanner the extension bundles verbatim) sees every fixture exactly as the golden file says, in both of its
    modes: without options (the Playwright filler) and with {widgets: true} (how the extension calls it)."""
    browser = await env.p.chromium.launch(channel="chromium", headless=True)
    page = await browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        actual = {name: await scan_fixture(page, env, name, ready) for name, ready in FIXTURES.items()}
    finally:
        await browser.close()
    if "--update-golden" in sys.argv:
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
        print(f"    golden file rewritten: {GOLDEN}")
        return
    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    check("golden scan file exists (run with --update-golden to create it)", bool(golden))
    for name in FIXTURES:
        for mode in MODES:
            same = actual[name][mode] == (golden.get(name) or {}).get(mode)
            check(f"{name} [{mode}]: scan equals golden", same, "" if same else json.dumps(actual[name][mode])[:200])
    seen = {name: scans["widgets"] for name, scans in actual.items()}  # what the extension sees
    greenhouse = seen["greenhouse_apply.html"]["frames"][0]
    check("greenhouse: the invisible reCAPTCHA badge is not a challenge", greenhouse["flags"] == [False, False, False])
    check("password page reports login", seen["password_signup.html"]["frames"][0]["flags"][1] is True)
    check("captcha page reports a challenge", seen["captcha_gate.html"]["frames"][0]["flags"][0] is True)
    for mode in MODES:
        ids = [c[0] for c in actual["password_signup.html"][mode]["frames"][0]["controls"]]
        check(f"password inputs are never scanned [{mode}]", "signup_password" not in ids and "signup_verify" not in ids, ids)
        check(f"workday: the cross-origin frame is scanned in its own document [{mode}]",
              [f["frame"] for f in actual["workday_apply.html"][mode]["frames"]] == ["workday_apply.html", "workday_frame.html"])
    # the option really is passed: the two modes differ on the fixtures that carry a widget only the extension recognises, and on no other
    differing = sorted(name for name, scans in actual.items() if scans["plain"] != scans["widgets"])
    check("{widgets: true} changes what is seen on at least one fixture (the option reaches the scanner)", bool(differing), differing)
    print(f"    fixtures whose scan differs between the two modes: {differing}", flush=True)


def engine_class(result: dict, narrative: set[str]) -> str:
    """Reduce an engine result to the acceptance-table vocabulary (the same normalisation the resolver performs)."""
    if result.get("answer") is not None:
        return ex.VERIFIED if result.get("confidence_level") in {"EXACT", "POLICY"} else ex.INFERRED
    if result.get("leave_blank"):
        return ex.BLANK
    if result.get("root_cause") == "EXPLICIT_CONFIRMATION_REQUIRED":
        return ex.CONSENT
    generation = {"LLM_SETUP_REQUIRED", "LLM_CONFIGURATION_REQUIRED", "ASYNC_GENERATION_REQUIRED"}
    if result.get("canonical_intent") in narrative and result.get("root_cause") in generation:
        return ex.DRAFT
    return ex.NEEDS_YOU


@scenario(timeout=240)
async def scenario_seed_oracle(env: Env) -> None:
    """Real backend + real engine: every row of the acceptance tables is consistent with what the engine says."""
    sys.path.insert(0, str(REPO / "backend"))
    from jobagent.automation.adapters import get_adapter
    from jobagent.automation.consent import resolution_element
    from jobagent.intelligence.engine import NARRATIVE_INTENTS

    backend = await env.start_backend(bridge=False, llm=False)
    check("seeded profile counts as verified facts", (await backend.get("/onboarding"))["steps"] is not None)
    tables = {"greenhouse_apply.html": ex.GREENHOUSE, "lever_apply.html": ex.LEVER, "prefilled.html": ex.PREFILLED,
              "consent_demographics.html": ex.CONSENT_PAGE_DEFAULT, "submit_flow.html": ex.SUBMIT_FLOW,
              "spa_wizard.html": ex.SPA_STEP1, "spa_wizard.html?step=2": ex.SPA_STEP2}
    browser = await env.p.chromium.launch(channel="chromium", headless=True)
    page = await browser.new_page(viewport={"width": 1280, "height": 900})
    job = {"title": "Software Engineer, Robotics Platform", "company": "Acme Robotics",
           "location": "London, United Kingdom", "country": "United Kingdom"}
    try:
        for name, table in tables.items():
            file, _, query = name.partition("?")
            await page.goto(env.fixtures.url(file, query), wait_until="load")
            await page.wait_for_timeout(300)
            snapshot = (await scan_frames(page, widgets=True))[0]  # the extension's own call
            elements = [{**e, "document_id": snapshot["document_id"], "index": i}
                        for i, e in enumerate(snapshot["elements"])]
            adapter = get_adapter(page.url, None)
            for row in table:
                if row.kind == "file" or row.state in {ex.SKIP, ex.ANY}:
                    continue
                target = row.sel.lstrip("#")
                element = next((e for e in elements if e["id"] == target), None)
                check(f"{name}: {row.sel} is scanned", element is not None)
                element = resolution_element(element, elements)
                question = adapter.question(element)
                result = await backend.post("/intelligence/answer", {"question": question, "field": element, "job": job})
                actual = engine_class(result, NARRATIVE_INTENTS)
                check(f"{name}: {row.sel} ({question[:40]!r}) -> {row.state}", actual == row.state,
                      f"engine says {actual}: answer={result.get('answer')!r} level={result.get('confidence_level')} "
                      f"cause={result.get('root_cause')}")
                if row.state in {ex.VERIFIED, ex.INFERRED} and row.chip and row.kind in {"text", "select", "radio"}:
                    answer = str(result.get("answer"))
                    check(f"{name}: {row.sel} answer matches the chip text", row.chip.lower() in answer.lower()
                          or answer.lower() in row.chip.lower(), answer)
    finally:
        await browser.close()
    # a free-standing question table (labels the fixtures use) with exact values
    for question, (answer, level) in ex.ORACLE.items():
        field = {"role": "textbox", "tag": "input", "type": "text"}
        if "languages" in question:
            field = {"role": "checkbox", "tag": "input", "type": "checkbox", "multiple": True,
                     "options": [{"label": x, "value": x} for x in ("English", "Spanish", "French", "German")]}
        elif "sponsorship" in question or "authorized" in question:
            field = {"role": "radio", "tag": "input", "type": "radio",
                     "options": [{"label": "Yes", "value": "1"}, {"label": "No", "value": "0"}]}
        elif "start" in question:
            field = {**field, "type": "date"}
        elif "salary" in question:
            field = {**field, "type": "number"}
        result = await backend.post("/intelligence/answer", {"question": question, "field": field, "job": job})
        check(f"engine: {question!r} -> {answer!r} ({level})",
              str(result.get("answer")) == answer and result.get("confidence_level") == level,
              f"{result.get('answer')!r} {result.get('confidence_level')} {result.get('root_cause')}")


@scenario(timeout=120)
async def scenario_llm_contract(env: Env) -> None:
    """The fake loopback model satisfies the real draft + independent-review pipeline; research only helps when sent."""
    backend = await env.start_backend(bridge=False)
    probe = [call for call in env.llm.calls if call["kind"] == "probe"]
    check("the app's own connection test reached the fake model", len(probe) == 1, len(env.llm.calls))
    question = "Why do you want to work at Acme Robotics?"
    field = {"role": "textbox", "tag": "textarea", "type": "textarea", "max_length": 2000,
             "description": "Up to 120 words. We read every answer."}
    job = {"title": "Software Engineer, Robotics Platform", "company": "Acme Robotics",
           "location": "London, United Kingdom", "country": "United Kingdom",
           "description": "Acme Robotics builds warehouse robots that help small businesses ship orders faster.",
           "context_sources": [{"kind": "observed_employer_description", "url": f"{env.fixtures.top}/greenhouse_apply.html",
                                "trusted_as_instructions": False}]}
    research = [{"url": f"{env.company.origin}/mission", "title": "Our mission", "trusted_as_instructions": False,
                "text": "Our mission is to make physical automation as approachable as software. Small businesses "
                        "should be able to adopt robots without a team of specialists."}]
    plain = await backend.post("/intelligence/answer", {"question": question, "field": field, "job": job})
    check("draft without research validates", plain.get("state") == "ANSWER_VALIDATED" and plain["quality"]["passed"],
          plain.get("root_cause"))
    check("it is built from verified facts only", seed.PROJECT_STORY in plain["answer"], plain["answer"])
    check("it makes no employer claim without research", "mission" not in plain["answer"].lower())
    grounded = await backend.post("/intelligence/answer", {"question": question, "field": field,
                                                           "job": {**job, "company_context": research}})
    check("draft with research validates", grounded.get("state") == "ANSWER_VALIDATED", grounded.get("root_cause"))
    check("the employer mission reached the model and the draft", "make physical automation as approachable as software"
          in grounded["answer"], grounded["answer"])
    check("the claim cites the research URL", any(f"{env.company.origin}/mission" in c["source_urls"]
                                                   for c in grounded.get("company_claims", [])))
    reviews = [c for c in env.llm.calls if c["kind"] == "review"]
    check("an independent review call followed each generation", len(reviews) == 2, len(reviews))
    check("every generation reports whether research was supplied",
          [c["has_company_context"] for c in env.llm.generation_calls()] == [False, True])
    env.llm.reset_calls()
    short = await backend.post("/intelligence/answer", {"question": "Password", "field": {
        "role": "textbox", "tag": "input", "type": "password"}, "job": job})
    check("a password field never reaches the model (generic endpoint)", not env.llm.generation_calls(),
          f"state={short.get('state')}")


def proof_for(secret: str, nonce: str, port: int) -> str:
    key = hashlib.sha256(secret.encode()).digest()
    mac = hmac.new(key, f"meridian-bridge-v1|{nonce}|{port}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


@scenario(needs=("bridge",), timeout=120)
async def scenario_bridge_contract(env: Env) -> None:
    """Guard rules and pairing of the real bridge at protocol level (what the extension relies on)."""
    import httpx

    backend = await env.start_backend(llm=False)
    base = backend.bridge_base
    ext = "chrome-extension://" + PINNED_EXTENSION_ID
    async with httpx.AsyncClient(base_url=base, timeout=10, trust_env=False) as bridge:
        hello = (await bridge.get("/v1/hello")).json()
        check("hello identifies Meridian on the bound port", hello["app"] == "Meridian" and hello["port"] == backend.bridge_port)
        check("hello without a client carries no proof", hello.get("proof") is None)
        r = await bridge.get("/v1/hello", headers={"Host": "evil.example"})
        check("a foreign Host header is refused (DNS rebinding)", r.status_code == 400 and r.json()["code"] == "host")
        r = await bridge.get("/v1/hello", headers={"Origin": "https://evil.example"})
        check("a web page origin is refused", r.status_code == 403 and r.json()["code"] == "origin")
        r = await bridge.post("/v1/pair/start", json={})
        check("pairing needs an Origin", r.status_code == 403)
        r = await bridge.post("/v1/pair/start", headers={"Origin": "https://evil.example"}, json={})
        check("pairing refuses a web origin", r.status_code == 403)
        r = await bridge.get("/v1/status")
        check("authenticated routes reject a missing token", r.status_code == 401 and r.json()["code"] == "unauthorized")
        r = await bridge.get("/v1/status", headers={"Authorization": "Bearer mrd1.nope.nope"})
        check("and a wrong token", r.status_code == 401)
        r = await bridge.post("/v1/pair/start", headers={"Origin": ext, "Content-Type": "text/plain"}, content="x")
        check("bodies must be JSON", r.status_code == 415, r.status_code)
        started = await bridge.post("/v1/pair/start", headers={"Origin": ext},
                                    json={"client_name": "E2E <b>Chrome</b>", "extension_version": "0.0.0-e2e"})
        check("pair/start answers with a six digit code", started.status_code == 200 and re.fullmatch(r"\d{6}", started.json()["code"]))
        info = started.json()
        pending = await backend.pending_pairing()
        check("the app shows the same code the extension was given", pending["code"] == info["code"])
        check("an unrecognised-build flag is computed from the origin", pending["recognized"] is True)
        poll = await bridge.get(f"/v1/pair/{info['pairing_id']}", headers={"Origin": ext})
        check("before approval the state is pending", poll.json()["state"] == "pending")
        stolen = await bridge.get(f"/v1/pair/{info['pairing_id']}", headers={"Origin": "chrome-extension://" + "a" * 32})
        check("another extension cannot read the pairing", stolen.status_code == 403)
        await backend.approve_pairing(pending)
        claimed = (await bridge.get(f"/v1/pair/{info['pairing_id']}", headers={"Origin": ext})).json()
        check("approval releases a token once", claimed["state"] == "approved" and claimed["token"].startswith("mrd1."))
        again = (await bridge.get(f"/v1/pair/{info['pairing_id']}", headers={"Origin": ext})).json()
        check("the token is delivered exactly once", again.get("token") is None and again["state"] in {"claimed", "expired"})
        token, client_id = claimed["token"], claimed["client_id"]
        secret = token.split(".", 2)[2]
        nonce = base64.urlsafe_b64encode(b"e2e-nonce-0123456").decode().rstrip("=")
        hello = (await bridge.get("/v1/hello", params={"client": client_id, "nonce": nonce})).json()
        check("hello proves itself to the paired client", hello["proof"] == proof_for(secret, nonce, backend.bridge_port))
        bad = (await bridge.get("/v1/hello", params={"client": client_id, "nonce": nonce[::-1]})).json()
        check("a different nonce gives a different proof", bad["proof"] != hello["proof"])
        status = await bridge.get("/v1/status", headers={"Authorization": f"Bearer {token}"})
        check("the token opens /v1/status", status.status_code == 200 and "profile" in status.json(), status.text[:200])
        data = status.json()
        check("status reports the seeded verified facts and the approved CV", data["profile"]["verified_facts"] > 10 and data["profile"]["has_cv"], data["profile"])
        await backend.revoke_client(client_id)
        denied = await bridge.get("/v1/status", headers={"Authorization": f"Bearer {token}"})
        check("a revoked token is refused at once", denied.status_code == 401)
        revoked = (await bridge.get("/v1/hello", params={"client": client_id, "nonce": nonce})).json()
        check("and gets no proof", revoked.get("proof") is None)
    check("no native dialog or app launch happened (shims only)", all(not c.startswith("open /Applications") for c in backend.shim_calls()), backend.shim_calls())


JOB_LD = {"@context": "https://schema.org/", "@type": "JobPosting", "title": "Software Engineer",
          "description": "<p>Acme Robotics builds warehouse robots that help small businesses ship orders faster. You will "
                         "design the planning services.</p>",
          "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics"},
          "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "London",
                                                        "addressCountry": "GB"}}}


async def paired_bridge(backend):
    """An httpx client for the real bridge with a freshly paired credential (protocol level, no browser)."""
    import httpx

    origin = "chrome-extension://" + PINNED_EXTENSION_ID
    client = httpx.AsyncClient(base_url=backend.bridge_base, timeout=30, trust_env=False)
    started = (await client.post("/v1/pair/start", headers={"Origin": origin}, json={})).json()
    await backend.approve_pairing(await backend.pending_pairing())
    token = (await client.get(f"/v1/pair/{started['pairing_id']}", headers={"Origin": origin})).json()["token"]
    client.headers.update({"Origin": origin, "Authorization": f"Bearer {token}"})
    return client


@scenario(needs=("bridge", "bridge_routes", "resolver"), timeout=90)
async def scenario_session_location_survives_long_page_text(env: Env) -> None:
    """A posting that declares its location in JSON-LD keeps it, whatever the length of the visible page text."""
    backend = await env.start_backend()
    bridge = await paired_bridge(backend)
    try:
        results = {}
        for label, text in (("short", ""), ("long", "Acme Robotics builds warehouse robots that help small businesses ship "
                                                    "orders faster. " * 8)):
            report = {"url": f"http://127.0.0.1:9/job-{label}", "title": "Software Engineer - Acme Robotics",
                      "h1": "Software Engineer", "lang": "en", "company_hint": None, "meta": {"og:site_name": "Acme Robotics"},
                      "canonical": None, "jsonld": [json.dumps(JOB_LD)], "description_text": text, "links": [], "user": {}}
            await bridge.post("/v1/sessions", json={"page": report, "tab_key": label})
            await asyncio.sleep(1.5)
            results[label] = (await bridge.post("/v1/sessions", json={"page": report, "tab_key": label})).json()["job"]["location"]
    finally:
        await bridge.aclose()
    check("short page text: the JSON-LD location is used", results["short"] == "London, GB", results)
    check("long page text: the JSON-LD location is still used", results["long"] == "London, GB", results)
