"""Live half of extension_e2e.py (``--live``): the browser-assistant UI against the REAL backend.

Nothing is mocked. ``live_stack.LiveStack`` starts the real FastAPI backend on a temporary data directory (MERIDIAN_TEST=1,
in-memory secrets, the loopback bridge on an ephemeral port, shim ``open`` / ``osascript`` / ``pbcopy``) and a vite dev
server proxying to it; ``live_extension.FakeExtension`` plays the extension's service worker over the real bridge (hello,
pairing, sessions, suggestions, feedback). The page under test is the real desktop UI in real Chrome (or WebKit).

What it proves that the mocked mode cannot:
  * every field of GET /extension the panel reads is really there (detection, the pairing code the extension was given,
    the token handed over once and never returned by the app, revocation, enable/disable, a bridge port conflict and recovery);
  * the data the UI reads after a real assisted session: the tracker row's ``assistant`` summary, the application detail's
    ``assistant`` and ``origin`` on answers, the Profile memory list's ``origin``, the activity feed, 'Open with assistant';
  * the copy that describes pairing and the Safari steps is shown next to the behaviour it describes.

No employer site, model provider or Keychain is contacted; the real Meridian.app is never launched.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import tempfile
import time
from pathlib import Path

from playwright.async_api import async_playwright, expect

from live_extension import SAFARI_ORIGIN, UNRECOGNISED_ID, FakeExtension, control
from live_stack import LiveStack

SECRET_MARK = "mrd1."


async def eventually(condition, timeout=20.0, message="condition"):
    """Await a (sync or async) predicate; the loaded machine decides how long, not the test."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(0.2)
    raise AssertionError(f"{message} not met within {timeout:.0f} s")


class Live:
    """The page, the backend and the helpers every scenario shares."""

    def __init__(self, page, stack, out: Path, browser_name: str):
        self.page, self.stack, self.out, self.browser_name = page, stack, out, browser_name
        self.backend = stack.backend
        self.token = self.client_id = None  # the Chrome stand-in's credential, once it is paired
        self.nav = page.get_by_role("navigation")
        self.hero = page.locator(".ext-hero")

    async def open_panel(self):
        await self.nav.get_by_role("button", name="Settings", exact=True).click()
        await self.page.get_by_role("button", name="Browser assistant", exact=True).click()
        await expect(self.hero.get_by_role("heading")).to_be_visible()

    async def refresh_now(self):
        """The app refreshes the status when its window regains focus: do that instead of waiting out the idle cadence."""
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")

    async def headline(self, text: str, timeout=30_000):
        await expect(self.hero.get_by_role("heading")).to_have_text(text, timeout=timeout)

    async def shot(self, name: str):
        await self.page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)

    async def raw_status(self) -> str:
        """GET /api/extension exactly as the app sees it (text, to search it for secrets)."""
        return (await self.backend.request("GET", "/extension")).text


def fmt(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


# ------------------------------------------------------------------------------------------------ scenarios


async def onboarding_step(backend) -> dict:
    """The ninth setup step, as the real backend computes it."""
    steps = (await backend.get("/onboarding"))["steps"]
    return next(step for step in steps if step["id"] == "extension")


async def first_run(live: Live):
    print("First run: the real status, the bridge port, the folder the backend installed", flush=True)
    be, page = live.backend, live.page
    status = await be.get("/extension")
    assert status["state"] == "not_installed" and status["bridge"]["state"] == "listening", status
    assert status["browsers"]["chrome"]["bundled"] is True, "build the extension first (make extension): the sidecar bundles none"
    # Today offers the setup only until a browser is connected, and the setup guide has a ninth, still untouched step.
    callout = page.get_by_role("button", name="Set up the browser assistant")
    await expect(callout).to_be_visible()
    await expect(page.get_by_role("button", name="Open with assistant")).to_have_count(0)
    assert (await onboarding_step(be))["status"] == "pending"
    await callout.click()
    await expect(live.hero.get_by_role("heading")).to_be_visible()
    await live.headline("Fill applications in your own browser")
    await expect(live.hero.locator(".tag")).to_have_text("Not set up")
    # The test bridge listens on an ephemeral port: the extension (ports 47615 to 47624) could not find it, and the panel says so.
    help_box = page.locator("details.ext-help")
    await expect(help_box.get_by_role("alert")).to_contain_text(
        f"listening on port {be.bridge_port}, which the extension does not look at")
    chrome = page.locator(".ext-browser").filter(has_text="Google Chrome")
    safari = page.locator(".ext-browser").filter(has_text="Safari").last
    await expect(chrome.locator(".ext-steps > li")).to_have_count(3)  # Developer mode, Load unpacked, Connect: everything else is one button
    await expect(safari).to_contain_text("This build of Meridian does not include the Safari extension.")  # a dev run has no appex
    # The backend installs the bundled folder when it starts, so the first step is already done and shows where it is.
    await expect(page.get_by_text("Waiting for the extension to appear…")).to_have_count(0)
    folder = Path(await chrome.get_by_label("Extension folder path").inner_text())
    assert folder.resolve() == (be.data_dir / "extensions" / "chrome").resolve(), folder
    manifest = json.loads((folder / "manifest.json").read_text())
    messages = json.loads((folder / "_locales" / "en" / "messages.json").read_text())
    assert manifest["manifest_version"] == 3 and manifest["version"] == status["browsers"]["chrome"]["bundled_version"], manifest["version"]
    assert messages["extName"]["message"] == "Meridian Assistant", messages["extName"]
    # One click prepares the folder, copies its path (the shim pbcopy or the browser clipboard), reveals it and opens Chrome's page.
    # Whether Google Chrome itself is on THIS machine is the backend's own finding (browser_installed), so the expected sentence follows
    # it: a host without Chrome (a CI runner) gets the "open your browser's page yourself" answer, not a failure of this test.
    await chrome.get_by_role("button", name="Install in Chrome").click()
    if status["browsers"]["chrome"]["browser_installed"]:
        await expect(page.get_by_text("The folder path is copied and Chrome's extensions page is open. Three steps are left in Chrome.")).to_be_visible()
    else:
        await expect(page.get_by_text("The folder path is copied and the folder is shown in Finder. Open your browser's extensions page")).to_be_visible()
    await expect(page.get_by_text("Waiting for the extension to appear…")).to_be_visible()
    await expect(chrome.get_by_role("button", name="Copy path again")).to_be_visible()
    await chrome.get_by_role("button", name="Show in Finder").click()
    await expect(page.get_by_text("Showing the extension folder in Finder.")).to_be_visible()
    await live.shot("live-first-run")


async def detection(live: Live, ext: FakeExtension):
    print("Detection: an extension saying hello turns the panel to 'connect it'", flush=True)
    hello = await ext.hello()
    assert hello["app"] == "Meridian" and hello["proof"] is None and hello["port"] == live.backend.bridge_port, hello
    await live.headline("Almost there: connect the extension")
    await expect(live.hero.locator(".tag")).to_have_text("Not connected")
    await expect(live.page.locator("#assistant-hint")).to_have_text("Browser assistant: extension found, connect it in Settings.")
    chrome = live.page.locator(".ext-browser").filter(has_text="Google Chrome")
    await expect(chrome.locator(".ext-found")).to_contain_text("Found the extension in Chrome. One step left.")
    await expect(chrome.locator(".tag")).to_have_text("Not connected")
    await expect(live.hero).to_contain_text("type it into the Meridian window that opens, or approve the request here")
    step = await onboarding_step(live.backend)
    assert step["status"] == "in_progress" and "Click its toolbar icon and choose Connect" in step["detail"], step


async def unrecognised_and_denied(live: Live):
    print("Pairing: an unrecognised extension id is flagged in red and Deny reaches the extension", flush=True)
    stranger = FakeExtension(live.backend.bridge_base, UNRECOGNISED_ID)
    try:
        started = await stranger.start_pairing("Some other extension")
        await live.headline("Chrome wants to connect")
        await expect(live.page.locator(".ext-code")).to_have_text(fmt(started["code"]))
        await expect(live.page.locator(".ext-warning")).to_contain_text("Unrecognised extension")
        pending = await live.backend.pending_pairing()
        assert pending["recognized"] is False and pending["code"] == started["code"], pending
        await live.shot("live-pairing-unrecognised")
        await live.page.get_by_role("button", name="Deny", exact=True).click()
        await expect(live.page.get_by_text("Request denied.")).to_be_visible()
        await live.headline("Almost there: connect the extension")
        answer = await stranger.claim(started["pairing_id"])
        assert answer["state"] == "denied" and not answer.get("token"), answer
    finally:
        await stranger.close()


async def chrome_pairing(live: Live, ext: FakeExtension):
    print("Pairing: the code the extension shows is the code the app shows; Approve hands the extension its token once", flush=True)
    started = await ext.start_pairing()
    await live.headline("Chrome wants to connect")
    await expect(live.page.locator(".ext-code")).to_have_text(fmt(started["code"]))
    await expect(live.page.get_by_role("alert").filter(has_text="wants to connect")).to_contain_text(
        "Pairing code " + " ".join(started["code"]))
    card = live.page.locator(".ext-pairing")
    await expect(card).to_contain_text("Meridian also opens a small window that asks for the code")
    await expect(card.locator(".ext-warning")).to_have_count(0)  # the extension id Meridian ships is recognised
    pending = await live.backend.pending_pairing()
    assert pending["code"] == started["code"] and pending["recognized"] is True and pending["browser"] == "chrome", pending
    await live.shot("live-pairing")
    # The same request is announced on every other page of the app.
    await live.nav.get_by_role("button", name="Today", exact=True).click()
    await expect(live.page.locator(".ext-banner")).to_contain_text(fmt(started["code"]))
    await live.page.locator(".ext-banner").get_by_role("button", name="Review request").click()
    await expect(live.page.locator(".ext-pairing h3")).to_be_focused()
    await live.page.get_by_role("button", name="Approve connection").click()
    await expect(live.page.get_by_text("Chrome connected.")).to_be_visible()
    await live.headline("Assistant ready in Chrome")
    answer = await ext.claim(started["pairing_id"])
    assert answer["state"] == "approved" and answer["token"].startswith(SECRET_MARK) and answer["client_id"], answer
    again = await ext.claim(started["pairing_id"])
    assert not again.get("token"), "the token must be handed over only once"
    token = answer["token"]
    status_text = await live.raw_status()
    assert SECRET_MARK not in status_text and token.split(".")[-1] not in status_text, "the app API must never carry the token"
    await expect(live.page.get_by_text("Chrome on this Mac")).to_be_visible()
    await expect(live.page.get_by_text("not used yet")).to_be_visible()
    await live.shot("live-ready")
    step = await onboarding_step(live.backend)
    assert step["status"] == "complete" and "1 browser connected" in step["detail"], step
    await live.nav.get_by_role("button", name="Today", exact=True).click()
    await expect(live.page.get_by_role("button", name="Set up the browser assistant")).to_have_count(0)  # nothing left to set up


async def safari_pairing_decided_elsewhere(live: Live):
    print("Pairing: a request decided in Meridian's own window (here: through the API) clears the card by itself", flush=True)
    safari = FakeExtension(live.backend.bridge_base, origin=SAFARI_ORIGIN)
    try:
        await live.open_panel()
        await safari.hello()
        started = await safari.start_pairing("Safari")
        await live.headline("Safari wants to connect")
        await expect(live.page.locator(".ext-code")).to_have_text(fmt(started["code"]))
        # what typing the right code into the native window does: the request is approved without a click in this window
        await live.backend.approve_pairing(await live.backend.pending_pairing())
        await expect(live.page.locator(".ext-pairing")).to_have_count(0, timeout=30_000)
        await live.headline("Assistant ready in Chrome and Safari")
        answer = await safari.claim(started["pairing_id"])
        assert answer["state"] == "approved" and answer["token"].startswith(SECRET_MARK), answer
        await expect(live.page.get_by_text("Safari on this Mac")).to_be_visible()
        return safari
    except BaseException:
        await safari.close()
        raise


async def assisted_session(live: Live, ext: FakeExtension) -> dict:
    print("An assisted session over the real bridge: two fields inserted, one answer typed and saved", flush=True)
    be = live.backend
    created = await ext.open_page()
    sid = created["session_id"]
    suggestions = await ext.suggest(sid, [
        control(1, "First name", autocomplete="given-name"),
        control(3, "Favourite colour"),
        control(5, "Email", type="email"),
    ])
    assert suggestions[1]["state"] == "VERIFIED" and suggestions[3]["state"] == "NEEDS_YOU", suggestions
    assert (await ext.feedback(sid, suggestions[1], "accepted"))["saved"] is False
    saved = await ext.feedback(sid, suggestions[3], "edited", final_value="Green", remember="application")
    assert saved["saved"] is True and saved["application_id"], saved
    assert (await ext.feedback(sid, suggestions[5], "accepted"))["saved"] is False
    rows = await be.tracker_rows()
    row = next(r for r in rows if r["application_id"] == saved["application_id"])
    assert row["assistant"]["browser"] == "chrome" and row["assistant"]["fields_inserted"] == 2, row["assistant"]
    return {"job_id": row["id"], "application_id": saved["application_id"], "session_id": sid}


async def data_in_the_ui(live: Live, ids: dict, ext: FakeExtension):
    be, page = live.backend, live.page
    print("Tracker: the 'Assisted in Chrome' line and Open with assistant, from the real rows", flush=True)
    await live.refresh_now()
    await live.nav.get_by_role("button", name="Tracker", exact=True).click()
    row = page.locator("tr").filter(has_text="Acme Robotics")
    await expect(row.get_by_text("Assisted in Chrome · 2 fields")).to_be_visible()
    await live.shot("live-tracker")
    await row.get_by_role("button", name="Open with assistant").click()
    await expect(page.get_by_text(re.compile(r"Opened in (Chrome|Safari)\. Click the Meridian icon when the form is open\."))).to_be_visible()
    await expect(page.get_by_text("Researching the company in the background.")).to_be_visible()

    print("Application detail: the Assisted tag, the answer's origin, and nothing else labelled", flush=True)
    application = await be.get(f"/applications/{ids['application_id']}")
    assert application["assistant"]["fields_inserted"] == 2 and [a["origin"] for a in application["answers"]] == ["extension"], application
    await row.get_by_role("button", name="Timeline & notes").click()
    await expect(page.locator(".detail-hero .tag.green").filter(has_text="Assisted in Chrome")).to_have_count(1)
    await page.get_by_role("button", name=re.compile(r"^Answers \(1\)$")).click()
    card = page.locator(".answer-card").filter(has_text="Favourite colour")
    await expect(card).to_contain_text("Green")
    await expect(card.get_by_text("Inserted by assistant")).to_have_count(1)
    # an answer typed into Meridian itself carries no label
    await page.get_by_role("button", name="Add verified answer").click()
    dialog = page.get_by_role("dialog", name="Add a verified answer", exact=True)
    await dialog.get_by_label("Question", exact=True).fill("Notice period")
    await dialog.get_by_label("Your answer", exact=True).fill("One month")
    await dialog.get_by_role("button", name="Verify & save answer", exact=True).click()
    own = page.locator(".answer-card").filter(has_text="Notice period")
    await expect(own).to_contain_text("One month")
    await expect(own.get_by_text("Inserted by assistant")).to_have_count(0)
    await expect(page.get_by_text("Inserted by assistant")).to_have_count(1)
    await live.shot("live-application-detail")
    # a correction typed again in the browser supersedes the first one: the history keeps the label it had
    fields = await ext.suggest(ids["session_id"], [control(3, "Favourite colour")])
    await ext.feedback(ids["session_id"], fields[3], "edited", final_value="Teal", remember="application")
    refreshed = await be.get(f"/applications/{ids['application_id']}")
    assert [(a["answer"], a["origin"]) for a in refreshed["answer_history"]] == [("Green", "extension")], refreshed["answer_history"]
    assert {(a["answer"], a["origin"]) for a in refreshed["answers"]} == {("Teal", "extension"), ("One month", None)}, refreshed["answers"]
    await page.keyboard.press("Escape")
    await row.get_by_role("button", name="Timeline & notes").click()
    await page.get_by_role("button", name=re.compile(r"^Answers \(2\)$")).click()
    await expect(page.locator(".answer-card").filter(has_text="Teal").get_by_text("Inserted by assistant")).to_have_count(1)
    await page.get_by_text("Previous answers (1)").click()
    previous = page.locator("details").filter(has_text="Previous answers")
    await expect(previous.locator(".answer-card").filter(has_text="Green").get_by_text("Inserted by assistant")).to_have_count(1)
    await page.keyboard.press("Escape")

    print("Profile memory list: the answer the assistant saved says so, a memory edited in Meridian does not", flush=True)
    await live.nav.get_by_role("button", name="Profile", exact=True).click()
    await page.get_by_text(re.compile(r"Answers saved from applications \(\d+\)")).click()
    memory = page.locator("details").filter(has_text="Answers saved from applications")
    tagged = memory.locator(".simple-list > div").filter(has_text="favourite colour")
    await expect(tagged).to_contain_text("Teal")
    await expect(tagged.get_by_text("Inserted by assistant")).to_have_count(1)
    await memory.screenshot(path=str(live.out / "live-profile-memory.png"))
    saved = (await be.get("/application-profile"))["answers"]
    assert [m["origin"] for m in saved if m["question"] == "favourite colour"] == ["extension"], saved
    await tagged.get_by_role("button", name="Edit answer").click()
    editor = page.get_by_role("dialog")
    await editor.get_by_label("Answer", exact=True).fill("Mint")
    await editor.get_by_role("button", name="Save changes", exact=True).click()
    await expect(page.get_by_text("Application profile saved.")).to_be_visible()
    await expect(memory.locator(".simple-list > div").filter(has_text="favourite colour").get_by_text("Inserted by assistant")).to_have_count(0)
    edited = (await be.get("/application-profile"))["answers"]
    assert [m["origin"] for m in edited if m["question"] == "favourite colour"] == [None], edited

    print("Activity: the assistant's own items and the Browser assistant origin in the timeline", flush=True)
    await live.nav.get_by_role("button", name="Activity", exact=True).click()
    panel = page.locator(".panel").filter(has=page.get_by_role("heading", name="Browser assistant", exact=True))
    await expect(panel.get_by_text("Filled 2 fields.")).to_be_visible()
    await expect(panel.get_by_text("Helped with Software Engineer at Acme Robotics.")).to_be_visible()
    await expect(panel.get_by_text("Chrome connected to Meridian.")).to_be_visible()
    entry = page.locator(".timeline-item").filter(has_text="Assisted by the browser extension")
    await expect(entry).to_contain_text("Acme Robotics · Software Engineer · Browser assistant")
    await live.shot("live-activity")


async def revoke_and_disable(live: Live, ext: FakeExtension):
    print("Disconnect: the extension's next call is refused; the other browser stays connected", flush=True)
    be, page = live.backend, live.page
    await live.open_panel()
    await live.headline("Assistant ready in Chrome and Safari")
    await page.get_by_role("button", name="Disconnect Chrome on this Mac").click()
    await page.get_by_role("dialog", name="Disconnect Chrome?").get_by_role("button", name="Disconnect", exact=True).click()
    await live.headline("Assistant ready in Safari")
    await expect(page.locator(".ext-hero h3")).to_be_focused()
    response = await ext.http.get("/v1/status", headers=ext._headers(auth=True))
    assert response.status_code == 401 and response.json()["code"] == "unauthorized", response.text
    hello = (await ext.http.get(f"/v1/hello?client={ext.client_id}&nonce=AAAAAAAAAAAAAAAAAAAAAA", headers=ext._headers())).json()
    assert hello["proof"] is None, "Meridian must not prove itself to a revoked client"  # that is how the extension learns it

    print("Turn off / on: the extension is refused while it is off", flush=True)
    await page.get_by_role("checkbox", name="Turn on the browser assistant").uncheck()
    await live.headline("The browser assistant is turned off")
    assert (await be.get("/extension"))["settings"]["enabled"] is False
    refused = await ext.http.post("/v1/pair/start", headers=ext._headers(), json={"client_name": "x"})
    assert refused.status_code == 403 and refused.json()["code"] == "disabled", refused.text
    await live.shot("live-disabled")
    await live.hero.get_by_role("button", name="Turn it on").click()
    await live.headline("Assistant ready in Safari")
    assert (await be.get("/extension"))["settings"]["enabled"] is True


async def bridge_conflict(args, out: Path):
    print("Port conflict: every bridge port taken, then freed and restarted from the panel", flush=True)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        async with LiveStack(bridge_port=str(port)) as stack:
            async with async_playwright() as p:
                browser = await (p.chromium.launch(channel="chrome", headless=True) if args.browser == "chrome" else p.webkit.launch())
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 1000}, locale="en-GB")
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    live = Live(page, stack, out, args.browser)
                    await page.goto(stack.base_url)
                    status = await stack.backend.get("/extension")
                    assert status["state"] == "bridge_error" and status["bridge"]["state"] == "port_in_use", status["bridge"]
                    await live.open_panel()
                    await live.headline("Meridian cannot open its browser connection")
                    await expect(live.hero.locator(".tag")).to_have_text("Needs attention")
                    await expect(live.hero).to_contain_text(f"every port from {port} is taken")
                    await expect(live.nav.get_by_role("button", name="Settings", exact=True)).to_have_accessible_description(
                        "Browser assistant: Meridian could not open its browser connection.")
                    await live.shot("live-bridge-conflict")
                    await live.hero.get_by_role("button", name="Try again").click()
                    await expect(page.get_by_text("The browser connection is still blocked.")).to_be_visible()
                    blocker.close()
                    await live.hero.get_by_role("button", name="Try again").click()
                    await expect(page.get_by_text("The browser connection is back.")).to_be_visible()
                    await live.headline("Fill applications in your own browser")
                    after = await stack.backend.get("/extension")
                    assert after["bridge"]["state"] == "listening" and after["bridge"]["port"] == port, after["bridge"]
                    # the recovered bridge really answers
                    ext = FakeExtension(f"http://127.0.0.1:{port}")
                    try:
                        assert (await ext.hello())["port"] == port
                    finally:
                        await ext.close()
                    assert not errors, errors
                finally:
                    await browser.close()
    finally:
        blocker.close()


# ------------------------------------------------------------------------------------------------ entry point


async def run(args):
    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "meridian-ui-qa"
    out = out / f"extension-live-{args.browser}"
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("FAILED.png", "FAILED.stack.log"):  # a failure of an earlier run must not look like this run's
        (out / stale).unlink(missing_ok=True)
    errors: list[str] = []

    def ignorable(text: str) -> bool:
        # WebKit reports requests cancelled by a reload as errors; the browser logs the 4xx the scenarios provoke on purpose.
        return "access control checks" in text or "Failed to load resource" in text

    async with LiveStack(profile=True, seed=True) as stack:
        async with async_playwright() as p:
            browser = await (p.chromium.launch(channel="chrome", headless=True) if args.browser == "chrome" else p.webkit.launch())
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-GB")
                if args.browser == "chrome":
                    await context.grant_permissions(["clipboard-read", "clipboard-write"])
                page = await context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)) if not ignorable(str(e)) else None)
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not ignorable(m.text) else None)
                live = Live(page, stack, out, args.browser)
                await page.goto(stack.base_url)
                ext = FakeExtension(stack.backend.bridge_base)
                safari = None
                try:
                    await first_run(live)
                    await detection(live, ext)
                    await unrecognised_and_denied(live)
                    await chrome_pairing(live, ext)
                    live.token, live.client_id = ext.token, ext.client_id
                    safari = await safari_pairing_decided_elsewhere(live)
                    ids = await assisted_session(live, ext)
                    await data_in_the_ui(live, ids, ext)
                    await revoke_and_disable(live, ext)
                    assert not errors, errors
                except BaseException:
                    try:
                        await page.screenshot(path=str(out / "FAILED.png"), full_page=True)
                        (out / "FAILED.stack.log").write_text(stack.logs())
                    except Exception:  # noqa: BLE001 - diagnostics must never hide the real failure
                        pass
                    raise
                finally:
                    await ext.close()
                    if safari is not None:
                        await safari.close()
            finally:
                await browser.close()
    await bridge_conflict(args, out)
    print(json.dumps({"result": "passed", "mode": "live", "browser": args.browser, "screenshots": str(out)}))
