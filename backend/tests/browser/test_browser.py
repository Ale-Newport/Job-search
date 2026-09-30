from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from jobagent.automation import BrowserManager, Decision, Operation
from jobagent.automation.types import HumanRequired, InvalidDecision, Paused, StaleState


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def fixture_server():
    handler = partial(QuietHandler, directory=str(Path(__file__).parent / "fixtures"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
async def browser(tmp_path):
    manager = BrowserManager(tmp_path)
    await manager.start(headless=True)
    yield manager
    await manager.close()


def fact(key, value, *, verified=True, category="identity"):
    return {"id": key, "key": key, "value": value, "category": category,
            "verification_status": "verified" if verified else "proposed", "locked": False}


@pytest.fixture
def candidate():
    return [fact("first_name", "Alex"), fact("last_name", "Example"), fact("email", "alex@example.test"),
            fact("country", "United Kingdom"), fact("relocation", "Yes"), fact("privacy", True, category="legal_certification")]


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("Alex Example — verified fixture resume")
    return {"id": "resume-v1", "kind": "cv", "path": str(path)}


async def test_greenhouse_full_form_review_and_verified_submit(browser, fixture_server, candidate, resume):
    application = {"id": "gh", "url": fixture_server + "/greenhouse.html", "ats": "greenhouse"}
    result = await browser.fill_application(application, candidate, [resume], settings={
        "sensitive_policies": {"legal_certification": {"action": "saved_answer", "fact_id": "privacy"}}})
    assert result["status"] == "needs_review", result
    assert result["sensitive"] is True
    assert await browser.page.evaluate("window.submissions") == 0
    assert await browser.page.locator('input[name="relocation"][value="yes"]').is_checked()
    assert await browser.page.locator('select').input_value() == "gb"
    assert await browser.page.locator('input[type="file"]').evaluate("n=>n.files[0].name") == "resume.txt"
    assert all(answer["verified"] for answer in result["answers"])
    submitted = await browser.submit(result["snapshot_id"])
    assert submitted["status"] == "confirmed", submitted
    assert submitted["evidence"][0]["kind"] == "visible_confirmation"
    assert await browser.page.evaluate("window.submissions") == 1
    repeated = await browser.submit(result["snapshot_id"])
    assert repeated["status"] == "human_required"
    assert await browser.page.evaluate("window.submissions") == 1


async def test_lever_derives_only_verified_full_name(browser, fixture_server, candidate, resume):
    result = await browser.fill_application({"id": "lever", "url": fixture_server + "/lever.html", "ats": "lever"}, candidate, [resume])
    assert result["status"] == "needs_review", result
    assert await browser.page.locator('[name="name"]').input_value() == "Alex Example"
    assert (await browser.submit(result["snapshot_id"]))["status"] == "confirmed"


async def test_workday_multistep_iframe(browser, fixture_server, candidate):
    result = await browser.fill_application({"id": "wd", "url": fixture_server + "/workday.html", "ats": "workday"}, candidate, [])
    assert result["status"] == "needs_review", result
    assert await browser.page.evaluate("window.submissions") == 0
    assert any(step["label"] == "Next" for step in result["steps"])
    assert (await browser.submit(result["snapshot_id"]))["status"] == "confirmed"


async def test_no_false_success_on_validation(browser, fixture_server, candidate):
    result = await browser.fill_application({"id": "generic", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "needs_review", result
    submitted = await browser.submit(result["snapshot_id"])
    assert submitted["status"] == "human_required"
    assert submitted["evidence"] == []
    assert "different email" in submitted["validation_errors"][0]


async def test_unverified_and_sensitive_fields_require_human(browser, fixture_server, resume):
    result = await browser.fill_application({"id": "unknown", "url": fixture_server + "/greenhouse.html"},
        [fact("first_name", "Unverified", verified=False), fact("email", "alex@example.test")], [resume])
    assert result["status"] == "human_required"
    assert await browser.page.locator('input[name="job_application[first_name]"]').input_value() == ""
    assert any(q["kind"] == "legal_certification" for q in result["questions"])
    assert await browser.page.evaluate("window.submissions") == 0


async def test_review_stale_state_blocks_submit(browser, fixture_server, candidate):
    result = await browser.fill_application({"id": "stale", "url": fixture_server + "/generic.html"}, candidate, [])
    await browser.page.locator('input').fill("changed@example.test")
    submitted = await browser.submit(result["snapshot_id"])
    assert submitted["status"] == "human_required"
    assert "changed" in submitted["error"]
    assert await browser.page.evaluate("window.submissions") == 0


async def test_stale_node_replacement_is_rejected(browser, fixture_server):
    snapshot = await browser.open(fixture_server + "/generic.html")
    target = next(e for e in snapshot["elements"] if e["role"] == "textbox")
    await browser.page.locator('input').evaluate("n=>n.replaceWith(n.cloneNode(true))")
    with pytest.raises(StaleState):
        await browser.execute(Decision(Operation.TYPE_TEXT, target["index"]), snapshot, value="alex@example.test")


async def test_occlusion_and_pause_are_enforced(browser, fixture_server):
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;background:red;z-index:999\"></div>')")
    snapshot = await browser.observe()
    target = next(e for e in snapshot["elements"] if e["role"] == "textbox")
    with pytest.raises(StaleState, match="covered"):
        await browser.execute(Decision(Operation.TYPE_TEXT, target["index"]), snapshot, value="alex@example.test")
    browser.pause()
    with pytest.raises(Paused):
        await browser.execute(Decision(Operation.TYPE_TEXT, target["index"]), snapshot, value="alex@example.test")


async def test_model_cannot_submit_or_upload_arbitrary_file(browser, fixture_server, tmp_path):
    snapshot = await browser.open(fixture_server + "/greenhouse.html")
    submit = next(e for e in snapshot["elements"] if e["label"] == "Submit application")
    with pytest.raises(HumanRequired, match="approval"):
        await browser.execute(Decision(Operation.CLICK, submit["index"]), snapshot)
    upload = next(e for e in snapshot["elements"] if e["type"] == "file")
    unrelated = tmp_path / "private.txt"
    unrelated.write_text("unselected")
    with pytest.raises(InvalidDecision, match="selected"):
        await browser.execute(Decision(Operation.UPLOAD, upload["index"]), snapshot, file_path=unrelated)


async def test_captcha_and_manual_mode_never_fill(browser, fixture_server, candidate):
    result = await browser.fill_application({"id": "manual", "url": fixture_server + "/generic.html"}, candidate, [], mode="MANUAL")
    assert result["steps"] == []
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("document.body.insertAdjacentHTML('afterbegin','<h2>Verify you are human</h2>')")
    browser._active_application_id = "captcha"
    result = await browser.fill_application({"id": "captcha", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "human_required"
    assert result["questions"][0]["kind"] == "captcha"
    assert await browser.page.locator('input').input_value() == ""


async def test_done_never_confirms_without_evidence(browser, fixture_server):
    snapshot = await browser.open(fixture_server + "/generic.html")
    result = await browser.execute(Decision(Operation.DONE), snapshot)
    assert result["confirmed"] is False


async def test_open_shadow_root_control(browser, fixture_server):
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("""() => {const host=document.createElement('section');document.body.append(host);
        host.attachShadow({mode:'open'}).innerHTML='<label>First name <input id="shadow-name"></label>';}""")
    snapshot = await browser.observe()
    target = next(e for e in snapshot["elements"] if e["id"] == "shadow-name")
    await browser.execute(Decision(Operation.TYPE_TEXT, target["index"]), snapshot, value="Alex")
    assert await browser.page.locator('#shadow-name').input_value() == "Alex"


async def test_custom_dropdown_matches_verified_option_and_progress_callback(browser, fixture_server, candidate):
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("""() => {
        const widget=document.createElement('div');widget.setAttribute('role','combobox');
        widget.setAttribute('aria-label','Country');widget.style.cssText='padding:20px;border:1px solid';
        widget.innerText='Select country';document.querySelector('form').prepend(widget);
        widget.onclick=()=>{if(document.querySelector('[role=option]'))return;
          const option=document.createElement('div');option.setAttribute('role','option');
          option.innerText='United Kingdom';option.style.cssText='padding:20px;border:1px solid';
          widget.after(option);option.onclick=()=>{widget.innerText='United Kingdom';option.remove()}}
    }""")
    browser._active_application_id = "custom"
    events = []
    browser.on_step = events.append
    result = await browser.fill_application({"id": "custom", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "needs_review", result
    assert await browser.page.locator('[role="combobox"]').inner_text() == "United Kingdom"
    assert any(e["operation"] == "CLICK" and e["observation"] for e in events)


async def test_laya_failure_in_ambiguous_navigation_requires_human(browser, fixture_server, candidate):
    class OfflineEngine:
        async def decide(self, snapshot, goal):
            raise HumanRequired("Laya is offline; no action performed.")
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("""() => {
        document.querySelector('button').remove();
        document.querySelector('form').insertAdjacentHTML('beforeend','<button type="button">Continue to profile</button><button type="button">Continue to review</button>');
    }""")
    browser._active_application_id = "laya-offline"
    browser.decision_engine = OfflineEngine()
    result = await browser.fill_application({"id": "laya-offline", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "human_required", result
    assert "Laya is offline" in result["error"]
    assert all(s["operation"] != "CLICK" for s in result["steps"])


async def test_laya_cannot_choose_outside_safe_navigation(browser, fixture_server, candidate):
    class UnsafeEngine:
        async def decide(self, snapshot, goal):
            assert all(e["label"].startswith("Continue") for e in snapshot["elements"])
            return Decision(Operation.TYPE_TEXT, 1)
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("""() => {
        document.querySelector('button').remove();
        document.querySelector('form').insertAdjacentHTML('beforeend','<button type="button">Continue to profile</button><button type="button">Continue to review</button>');
    }""")
    browser._active_application_id = "unsafe-decision"
    browser.decision_engine = UnsafeEngine()
    result = await browser.fill_application({"id": "unsafe-decision", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "human_required"
    assert "safe next step" in result["error"]


async def test_missing_resume_remains_question_after_other_fields_filled(browser, fixture_server, candidate):
    result = await browser.fill_application({"id": "missing-doc", "url": fixture_server + "/lever.html", "ats": "lever"}, candidate, [])
    assert result["status"] == "human_required"
    assert any(q["kind"] == "document" for q in result["questions"])
    assert await browser.page.locator('[name="email"]').input_value() == "alex@example.test"


async def test_disabled_controls_are_not_actionable(browser, fixture_server):
    await browser.open(fixture_server + "/generic.html")
    await browser.page.locator('input').evaluate("n=>n.disabled=true")
    snapshot = await browser.observe()
    target = next(e for e in snapshot["elements"] if e["role"] == "textbox")
    assert target["operations"] == []
    with pytest.raises(InvalidDecision):
        await browser.execute(Decision(Operation.TYPE_TEXT, target["index"]), snapshot, value="alex@example.test")


async def test_hidden_required_upload_cannot_be_declared_ready(browser, fixture_server, candidate):
    await browser.open(fixture_server + "/generic.html")
    await browser.page.evaluate("document.querySelector('form').insertAdjacentHTML('afterbegin','<label>Resume<input type=\"file\" required style=\"display:none\"></label>')")
    browser._active_application_id = "hidden-upload"
    result = await browser.fill_application({"id": "hidden-upload", "url": fixture_server + "/generic.html"}, candidate, [])
    assert result["status"] == "human_required"
    assert any(q["kind"] == "unsupported_widget" for q in result["questions"])
    assert await browser.page.evaluate("window.submissions") == 0


async def test_low_confidence_decision_cannot_execute(browser, fixture_server):
    snapshot = await browser.open(fixture_server + "/generic.html")
    target = next(e for e in snapshot["elements"] if e["role"] == "textbox")
    with pytest.raises(HumanRequired, match="confidence"):
        await browser.execute(Decision(Operation.TYPE_TEXT, target["index"], .5), snapshot, value="alex@example.test")
    assert await browser.page.locator('input').input_value() == ""


async def test_guided_consent_covers_selected_document_select_radio_and_checkbox(browser, fixture_server, candidate, resume):
    application = {"id": "guided-gh", "url": fixture_server + "/greenhouse.html", "ats": "greenhouse"}
    settings = {"section_consent": True,
                "sensitive_policies": {"legal_certification": {"action": "saved_answer", "fact_id": "privacy"}}}
    result = await browser.fill_application(application, candidate, [resume], settings=settings)
    assert result["status"] == "section_review", result
    assert await browser.page.locator('input[type="file"]').evaluate('n => n.files.length') == 0
    assert await browser.page.locator('input[name="job_application[first_name]"]').input_value() == ''
    saw_upload = False
    for _ in range(10):
        if result['status'] != 'section_review':
            break
        saw_upload |= any(field['operation'] == 'UPLOAD' and field['answer'] == 'resume.txt'
                          for field in result['section']['fields'])
        grant = await browser.authorize_section(application['id'], result['snapshot_id'])
        result = await browser.fill_application(application, candidate, [resume], settings={**settings, '_section_grant': grant})
    assert saw_upload and result['status'] == 'needs_review', result
    assert await browser.page.locator('select').input_value() == 'gb'
    assert await browser.page.locator('input[name="relocation"][value="yes"]').is_checked()
    assert await browser.page.locator('input[type="file"]').evaluate('n=>n.files[0].name') == 'resume.txt'
    assert await browser.page.evaluate('window.submissions') == 0
