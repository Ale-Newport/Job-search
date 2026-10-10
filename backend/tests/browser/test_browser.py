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
    assert all(answer["verified"] for answer in result["answers"]), result['answers']
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


async def test_trainline_delayed_ashby_form_fills_and_stops_before_submit(browser, fixture_server, candidate, resume):
    candidate += [fact("notice_period", "Immediately available"), fact("expected_salary", "45000"),
                  fact("sponsorship_required", "No"), fact("location", "London, UK"),
                  fact("Are you able to commit to our hybrid working policy?", "Yes")]
    result = await browser.fill_application({"id": "trainline", "url": fixture_server + "/ashby_trainline.html",
                                             "ats": "ashby"}, candidate, [resume],
                                            settings={"assisted_autofill": True, "skip_optional_unknown": True})
    assert result["status"] == "needs_review", result
    assert await browser.page.evaluate("window.submissions") == 0
    assert await browser.page.evaluate("window.selections") == {"notice": "Immediately available", "location": "London, Greater London, England, United Kingdom"}
    assert await browser.page.locator('[data-field-path="sponsor"] [data-option="no"]').get_attribute('aria-pressed') == 'true'
    assert await browser.page.locator('[data-field-path="hybrid"] [data-option="yes"]').get_attribute('aria-pressed') == 'true'
    assert await browser.page.locator('#salary').input_value() == '45000'
    assert await browser.page.locator('#resume').evaluate('n=>n.files[0].name') == 'resume.txt'
    assert await browser.page.locator('.ashby-application-form-autofill-input-root input').evaluate('n=>n.files.length') == 0
    assert await browser.page.locator('[data-field-path="diversity"] input').input_value() == ''
    assert not await browser.page.get_by_label('I agree to future job communications').is_checked()


async def test_trainline_missing_required_facts_fill_known_fields_then_ask(browser, fixture_server, candidate, resume):
    result = await browser.fill_application({"id": "trainline-missing", "url": fixture_server + "/ashby_overview.html"},
                                            candidate, [resume], settings={"skip_optional_unknown": True,
                                                "assisted_autofill": True, "has_filled_sections": True})
    assert result["status"] == "human_required", result
    assert len(result['questions']) == 5, result['questions']
    assert all(q.get('required') for q in result['questions'])
    assert await browser.page.locator('[name="name"]').input_value() == 'Alex Example'
    assert await browser.page.locator('#resume').evaluate('n=>n.files.length') == 1
    assert await browser.page.evaluate('window.submissions') == 0


async def test_hidden_upload_proxy_still_rejects_covered_control(browser, fixture_server, resume):
    snapshot = await browser.open(fixture_server + "/ashby_trainline.html")
    target = next(e for e in snapshot['elements'] if e['type'] == 'file')
    await browser.page.evaluate("""() => {const overlay=document.createElement('div');
        overlay.style.cssText='position:fixed;inset:0;z-index:999;background:white';document.body.append(overlay)}""")
    browser.allowed_uploads = {Path(resume['path'])}
    with pytest.raises(StaleState):
        await browser.execute(Decision(Operation.UPLOAD, target['index']), snapshot, file_path=resume['path'])
    assert await browser.page.locator('#resume').evaluate('n=>n.files.length') == 0


async def test_assisted_autofill_reobserves_async_dom_change_before_writing(browser, fixture_server, candidate, resume, monkeypatch):
    original = browser.execute
    changed = False

    async def changing_page(decision, snapshot, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            await browser.page.evaluate("() => document.body.insertAdjacentHTML('beforeend', '<p>Loaded application instructions</p>')")
        return await original(decision, snapshot, **kwargs)

    monkeypatch.setattr(browser, 'execute', changing_page)
    result = await browser.fill_application({'id': 'async-ats', 'url': fixture_server + '/lever.html', 'ats': 'lever'},
                                            candidate, [resume], settings={'assisted_autofill': True})
    assert result['status'] == 'needs_review', result
    assert await browser.page.locator('[name="name"]').input_value() == 'Alex Example'
    assert await browser.page.evaluate('window.submissions') == 0


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
    # The wrapping label is now a supported upload proxy, but no CV was selected.
    assert any(q["kind"] == "document" and q["question"] == "Resume" for q in result["questions"])
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


async def prompt_point(prompt, selector):
    import json

    result = await prompt.cdp.send(
        "Runtime.evaluate",
        {
            "contextId": prompt.context_id,
            "returnByValue": True,
            "expression": "(() => {const r=globalThis.__meridianPrompt.root.querySelector("
            + json.dumps(selector)
            + ").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()",
        },
    )
    return result["result"]["value"]


async def test_inline_guide_isolated_from_page_and_accepts_only_trusted_events(browser, fixture_server):
    import asyncio
    import json
    from jobagent.automation.browser_prompt import BrowserPrompt

    prompt = BrowserPrompt()
    await browser.open(fixture_server + "/sections.html")
    received = []

    async def handle(payload):
        received.append(payload)
        return {"message": "Saved"}

    await prompt.show(
        browser.page,
        {
            "question": "Your notice period?",
            "company": "Fixture",
            "required": True,
            "remaining": 1,
            "answer": "",
            "can_reuse": True,
        },
        handle,
    )
    assert await browser.page.evaluate("typeof globalThis.meridianAnswer") == "undefined"
    assert await browser.page.evaluate("document.querySelector('#meridian-browser-guide').shadowRoot") is None
    await prompt.cdp.send(
        "Runtime.evaluate",
        {
            "contextId": prompt.context_id,
            "expression": "globalThis.__meridianPrompt.root.querySelector('input').value='Fake';globalThis.__meridianPrompt.root.querySelector('button').click()",
        },
    )
    assert received == []
    prompt._message(
        {
            "executionContextId": prompt.context_id + 1,
            "name": "meridianAnswer",
            "payload": json.dumps({"token": prompt.token, "action": "answer", "answer": "Fake", "reusable": True}),
        }
    )
    assert received == []
    pt = await prompt_point(prompt, "#answer")
    await browser.page.mouse.click(**pt)
    await browser.page.keyboard.press("Meta+A")
    await browser.page.keyboard.insert_text("2 weeks")
    pt = await prompt_point(prompt, "button.primary")
    await browser.page.mouse.click(**pt)
    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.02)
    assert len(received) == 1 and received[0]["answer"] == "2 weeks"
    assert received[0]["reusable"] is False
    await prompt.close()
    assert await browser.page.locator("#meridian-browser-guide").count() == 0
    assert await browser.page.evaluate("window.submissions") == 0


async def test_orchestrator_inline_answers_draft_skip_resume_and_no_submission(tmp_path, fixture_server):
    import asyncio
    import json
    from jobagent.db import Database
    from jobagent.core import prepare_application
    from jobagent.discovery import ingest_job
    from jobagent.orchestrator import Orchestrator
    from jobagent.application_profile import write_profile

    db = Database(tmp_path / "database" / "meridian.sqlite")
    write_profile(db, "full_name", "Alex Example", True)
    job = ingest_job(
        db,
        {
            "title": "Engineer",
            "company": "Trainline",
            "location": "London",
            "url": fixture_server + "/inline_questions.html",
        },
    )[0]
    application = prepare_application(db, job["id"], section_consent=True, assisted_autofill=True)
    service = Orchestrator(db, tmp_path)

    class Drafts:
        async def draft(self, *args):
            return {"answer": "I built a Python automation project.", "facts_used": ["project"]}

    service.text_service = Drafts()
    await service.browser.start(headless=True)

    async def wait_status(status):
        for _ in range(500):
            run = db.one("SELECT * FROM automation_runs ORDER BY created_at DESC LIMIT 1")
            if run and run["status"] == status and not service.running:
                return run
            await asyncio.sleep(0.02)
        raise AssertionError(run)

    async def click(selector):
        await service.browser.page.mouse.click(**(await prompt_point(service.prompt, selector)))

    async def state():
        response = await service.prompt.cdp.send(
            "Runtime.evaluate",
            {
                "contextId": service.prompt.context_id,
                "returnByValue": True,
                "expression": "({question:globalThis.__meridianPrompt.root.querySelector('h2').textContent, answer:globalThis.__meridianPrompt.root.querySelector('#answer').value, status:globalThis.__meridianPrompt.root.querySelector('.status').textContent})",
            },
        )
        return response["result"]["value"]

    try:
        service.launch(application["id"])
        await wait_status("browser_question")
        assert (await state())["question"] == "What is your current notice period?"
        assert await service.browser.page.get_by_label("Name", exact=True).input_value() == "Alex Example"
        await click("#answer")
        await service.browser.page.keyboard.press("1")
        await service.browser.page.keyboard.press("Enter")
        await click("button.primary")
        # Wait for the new run, not the previous checkpoint.
        for _ in range(500):
            if (
                not service.running
                and service.prompt.cdp
                and (await state())["question"] == "How are you currently using AI?"
            ):
                break
            await asyncio.sleep(0.02)
        assert await service.browser.page.locator("select").input_value() == "1 month", await state()
        await click(".row button")  # Generate a draft via the browser UI.
        for _ in range(300):
            if (await state())["answer"]:
                break
            await asyncio.sleep(0.02)
        assert (await state())["answer"] == "I built a Python automation project."
        assert await service.browser.page.locator("textarea").input_value() == ""
        await click("button.primary")
        run = await wait_status("needs_review")
        assert await service.browser.page.locator("textarea").input_value() == "I built a Python automation project."
        # Unknown optional disclosures now stay blank without interrupting filling.
        assert await service.browser.page.get_by_label("Describe your gender").input_value() == ""
        assert not json.loads(run["checkpoint"])["questions"]
        assert await service.browser.page.evaluate("window.submissions") == 0
        assert db.one("SELECT count(*) AS n FROM browser_answers")["n"] == 2
        assert all(row["reusable"] == 0 for row in db.query("SELECT * FROM browser_answers"))
    finally:
        await service.close()


async def test_general_profile_fills_infers_skips_conditions_and_marks_review(browser, fixture_server):
    candidate = [fact('full_name','Alex Example'), fact('earliest_start_month','2027-09'),
                 fact('masters_completion_month','2027-09'),fact('sponsorship_required','No'),
                 fact('If you answered yes to the above, confirm your visa status:', 'must never be filled'),
                 fact('project','Developed retrieval system',category='project')]
    calls=[]
    async def reason(question, element):
        from jobagent.automation.reasoning import derived
        calls.append(question)
        if question=='Tell us about a technically challenging project.':
            return derived(question,'I developed a retrieval system.',[candidate[-1]],'AI draft from professional profile.',.6)
    result = await browser.fill_application({'id':'reasoning','url':fixture_server+'/profile_reasoning.html'}, candidate, [], settings={'assisted_autofill':True,'_reason_answer':reason})
    assert result['status']=='human_required', result
    assert await browser.page.locator('[name=notice]').input_value()=='Other'
    assert 'September 2027' in await browser.page.locator('#notice_other').input_value()
    assert await browser.page.locator('#visa').input_value()==''
    assert await browser.page.locator('#gender').input_value()==''
    assert await browser.page.locator('#start').input_value()==''
    assert any(q.get('reason') == 'MISSING_EXACT_START_DAY' for q in result['questions'])
    assert await browser.page.locator('#project').input_value()=='I developed a retrieval system.'
    assert len(calls)==1, calls
    assert len([a for a in result['answers'] if a.get('inferred')])==3
    assert all(not a['verified'] for a in result['answers'] if a.get('inferred'))
    assert await browser.page.evaluate('window.submissions')==0
    await browser.mark_inferred_answers(result)
    assert await browser.page.locator('[data-meridian-inference-badge]').count()==3
    # Visible badges must not invalidate the approval snapshot.
    await browser._validate_snapshot(result['snapshot_id'])
    assert await browser.page.evaluate('window.submissions')==0


async def test_typed_intelligence_live_dom_formats_and_submission_gate(browser,fixture_server,tmp_path):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.ingestion import import_bundle
    db=Database(tmp_path/'intelligence.sqlite')
    import_bundle(db,{'source':'test evidence','entities':[
        {'key':'person','kind':'person','name':'Test Candidate','facts':{'earliest_full_time_start_date':'2027-09-28'}},
        {'key':'bsc','kind':'education','name':'North University','facts':{'institution':'North University','level':"Bachelor's degree",'rank':1,'status':'completed','classification':'First Class Honours','average':79.94}},
        {'key':'msc','kind':'education','name':'South University','facts':{'institution':'South University','level':"Master's degree",'rank':2,'start_date':'2026-09-28','end_date':'2027-09-27','status':'studying'}},
        {'key':'project','kind':'project','name':'Project','facts':{'story':'I built the frontend and backend of an educational product.','tags':['ownership']}}
    ]},trusted_user_input=True)
    engine=CandidateEngine(db,as_of='2026-10-03')
    result=await browser.fill_application({'id':'intelligence','url':fixture_server+'/candidate_intelligence.html'},[],[],settings={'_candidate_engine':engine,'assisted_autofill':True,'skip_optional_unknown':True})
    assert result['status']=='human_required'
    assert await browser.page.locator('[name="card[0]"]').input_value()=='South University'
    assert await browser.page.locator('[name="card[1]"]').input_value()=='ba'
    assert await browser.page.locator('[name="card[2]"]').input_value()=='first'
    assert await browser.page.locator('[name="start"]').input_value()=='2027-09-28'
    assert await browser.page.locator('[name="grade"]').input_value()=='80'
    # An unconfigured generative provider cannot silently paste a project summary.
    assert await browser.page.locator('[name="story"]').input_value() == ''
    assert any(q.get('reason') in {'LLM_SETUP_REQUIRED', 'LLM_CONFIGURATION_REQUIRED'} for q in result['questions'])
    assert not await browser.page.locator('[name="consent"]').is_checked()
    assert await browser.page.evaluate('window.submissions')==0
    assert len([q for q in result['questions'] if 'GDPR' in q['question']])==1


async def test_hidden_lever_upload_and_ambiguous_attach_keep_document_types(browser,tmp_path):
    cv=tmp_path/'cv.pdf'
    cv.write_text('Synthetic CV')
    letter=tmp_path/'letter.pdf'
    letter.write_text('Synthetic letter')
    await browser.start()
    await browser.page.set_content('''<form><a href="#">ATTACH RESUME/CV<input type="file" name="resume" style="display:none"></a><label>Attach<input id="cover_letter" type="file"></label></form>''')
    snapshot=await browser.observe()
    uploads=[e for e in snapshot['elements'] if e['type']=='file']
    assert len(uploads)==2
    documents=[{'kind':'cv','path':str(cv)},{'kind':'cover_letter','path':str(letter)}]
    assert browser._documents_for(uploads[0]['label'],documents)[0]['kind']=='cv'
    assert browser._documents_for(uploads[1]['label'],documents)[0]['kind']=='cover_letter'
    assert browser._documents_for('Attach',[documents[0]])==[]
    browser.allowed_uploads={cv,letter}
    await browser.execute(Decision(Operation.UPLOAD,uploads[0]['index']),snapshot,file_path=cv)
    assert await browser.page.locator('[name=resume]').evaluate('n=>n.files.length')==1
    assert await browser.page.locator('#cover_letter').evaluate('n=>n.files.length')==0


async def test_universal_groups_exact_questions_and_post_write_verification(browser, tmp_path):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.ingestion import import_bundle

    db = Database(tmp_path / 'group-regression.sqlite3')
    import_bundle(db, {'source': 'Synthetic user evidence', 'entities': [
        {'key': 'person', 'kind': 'person', 'name': 'Example', 'facts': {
            'preferred_name': 'Alex', 'earliest_full_time_start_date': '2027-09'}},
        {'key': 'en', 'kind': 'language', 'name': 'English', 'facts': {'proficiency': 'Fluent'}},
        {'key': 'es', 'kind': 'language', 'name': 'Spanish', 'facts': {'proficiency': 'Native'}},
    ]}, trusted_user_input=True)
    await browser.page.set_content('''<form novalidate>
      <li class="application-question"><div class="application-label"><div class="text">Language Skill(s) (Check all that apply)<span class="required">*</span></div></div>
        <label><input type="checkbox" name="languages" value="English (ENG)" required>English (ENG)</label>
        <label><input type="checkbox" name="languages" value="Spanish (SPA)" required>Spanish (SPA)</label>
        <label><input type="checkbox" name="languages" value="German (DEU)" required>German (DEU)</label></li>
      <label>Preferred name<input name="preferred"></label>
      <label>When would you be available to start? (Month/Year)<input name="start" pattern="[0-9]{2}/[0-9]{4}"></label>
      <label>High school institution<input name="secondary" required></label>
      <button type="submit">Submit application</button></form>
      <script>window.submissions=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submissions++}</script>''')
    browser._active_application_id = 'universal-groups'
    result = await browser.fill_application({'id': 'universal-groups', 'url': 'about:blank'}, [], [], settings={
        '_candidate_engine': CandidateEngine(db, as_of='2026-10-10'), 'skip_optional_unknown': True})
    assert result['status'] == 'human_required', result
    assert await browser.page.locator('[name=languages]:checked').evaluate_all('nodes=>nodes.map(n=>n.value)') == ['English (ENG)', 'Spanish (SPA)']
    assert await browser.page.locator('[name=start]').input_value() == '09/2027'
    assert await browser.page.locator('[name=preferred]').input_value() == 'Alex'
    assert await browser.page.locator('[name=secondary]').input_value() == ''
    assert await browser.page.evaluate('window.submissions') == 0
    language = next(d for d in result['answer_diagnostics'] if d['canonical_intent'] == 'LANGUAGE')
    assert language['question'] == 'Language Skill(s) (Check all that apply)'
    assert language['status'] == 'ANSWER_ACCEPTED_BY_FORM'
    assert language['observed_choices'] == ['English (ENG)', 'Spanish (SPA)']
    assert len({d['question_id'] for d in result['answer_diagnostics']}) == len(result['answer_diagnostics'])


async def test_native_multiselect_uses_observed_options_and_verifies_all_selections(browser, tmp_path):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.ingestion import import_bundle

    db = Database(tmp_path / 'multiselect.sqlite3')
    import_bundle(db, {'source': 'Synthetic user evidence', 'entities': [
        {'key': 'en', 'kind': 'language', 'name': 'English', 'facts': {'proficiency': 'Fluent'}},
        {'key': 'es', 'kind': 'language', 'name': 'Spanish', 'facts': {'proficiency': 'Native'}},
    ]}, trusted_user_input=True)
    await browser.page.set_content('''<form><label>Languages you speak (select all that apply)
      <select name="langs" multiple required><option value="en">English (ENG)</option>
      <option value="de">German (DEU)</option><option value="es">Spanish (SPA)</option></select></label>
      <button type="submit">Submit application</button></form>''')
    browser._active_application_id = 'native-multi'
    result = await browser.fill_application({'id': 'native-multi', 'url': 'about:blank'}, [], [], settings={
        '_candidate_engine': CandidateEngine(db), 'skip_optional_unknown': True})
    assert result['status'] == 'needs_review', result
    assert await browser.page.locator('select').evaluate('n=>[...n.selectedOptions].map(o=>o.value)') == ['en', 'es']
    assert result['answer_diagnostics'][0]['status'] == 'ANSWER_ACCEPTED_BY_FORM'


async def test_intelligence_missing_fact_accepts_scoped_browser_confirmation(browser,fixture_server,tmp_path,candidate):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.store import entity,ingest_fact
    db=Database(tmp_path/'knowledge.sqlite')
    person=entity(db,'person','Example')
    ingest_fact(db,person,'email','alex@example.test',source='verified',evidence={},verified=True)
    await browser.open(fixture_server+'/generic.html')
    await browser.page.evaluate("""document.querySelector('form').insertAdjacentHTML('afterbegin','<label>Expected salary<input name="salary" required type="number"></label>')""")
    browser._active_application_id='confirmed-salary'
    memory={**fact('Expected salary','42000'),'profile_explicit':True}
    result=await browser.fill_application({'id':'confirmed-salary','url':fixture_server+'/generic.html'},candidate+[memory],[],settings={'_candidate_engine':CandidateEngine(db),'assisted_autofill':True,'skip_optional_unknown':True})
    assert await browser.page.locator('[name=salary]').input_value()=='42000'
    assert result['status']=='needs_review',result
    assert await browser.page.evaluate('window.submissions')==0


async def test_react_select_and_select2_observed_options(browser):
    await browser.page.set_content('''<form onsubmit="window.submissions++;return false">
    <label for="country">Country</label><div class="select__control" onclick="country.style.opacity=1;menu.hidden=false;country.setAttribute('aria-expanded','true')"><span class="select__single-value"></span>
    <input role="combobox" id="country" aria-expanded="false" onclick="menu.hidden=false;this.setAttribute('aria-expanded','true')"></div>
    <div id="menu" hidden><div role="option" onclick="document.querySelector('.select__single-value').textContent=this.textContent;country.value='';country.setAttribute('aria-expanded','false');country.style.opacity=0;menu.hidden=true">United Kingdom +44</div></div>
    <div class="application-question">University<span class="select2-container"><span role="combobox" tabindex="0" aria-expanded="false" onclick="universities.hidden=false;this.setAttribute('aria-expanded','true')" id="university">Select</span></span></div>
    <div class="select2-results" id="universities" hidden><div role="treeitem" onclick="university.textContent=this.textContent;university.setAttribute('aria-expanded','false');universities.hidden=true">Example University (EU)</div></div>
    <button type="submit">Submit application</button></form><script>window.submissions=0</script>''')
    browser._active_application_id = 'widgets'
    result = await browser.fill_application({'id':'widgets','url':'https://example.test'}, [fact('Country','United Kingdom'),fact('University','Example University')], [], settings={'assisted_autofill':True})
    assert result['status'] == 'needs_review', result
    assert await browser.page.locator('.select__single-value').inner_text() == 'United Kingdom +44'
    assert await browser.page.locator('#university').inner_text() == 'Example University (EU)'
    assert await browser.page.evaluate('window.submissions') == 0


async def test_apply_entry_opens_observed_href_without_clicking_submission_code(browser, fixture_server):
    await browser.page.set_content(f'<a role="button" href="{fixture_server}/generic.html" onclick="window.clicked=true;return false">Apply</a>')
    snapshot = await browser.observe()
    target = snapshot['elements'][0]
    with pytest.raises(HumanRequired):
        await browser.execute(Decision(Operation.CLICK,target['index']),snapshot)
    old_page = browser.page
    await browser.execute(Decision(Operation.OPEN_TAB,target['index']),snapshot)
    assert browser.page.url == fixture_server + '/generic.html'
    assert not await old_page.evaluate('Boolean(window.clicked)')
    await browser.page.set_content(f'<form><input aria-label="Name"><a role="button" href="{fixture_server}/generic.html">Apply</a></form>')
    snapshot = await browser.observe()
    target = next(e for e in snapshot['elements'] if e['tag']=='a')
    with pytest.raises(HumanRequired):
        await browser.execute(Decision(Operation.OPEN_TAB,target['index']),snapshot)


@pytest.mark.parametrize('view', ['links', 'buttons'])
async def test_application_entry_and_workday_method_choice_reach_final_review(browser, fixture_server, candidate, view):
    result = await browser.fill_application(
        {'id': 'entry-' + view, 'url': fixture_server + '/application_entry.html?view=' + view, 'ats': 'workday'},
        candidate, [], settings={'assisted_autofill': True, 'skip_optional_unknown': True})
    assert result['status'] == 'needs_review', result
    assert result['form_state'] == 'FINAL_REVIEW'
    assert await browser.page.evaluate('window.submissions') == 0
    assert await browser.page.evaluate('window.imports') == 0
    assert any(a['answer'] == 'alex@example.test' for a in result['answers'])
    # The final Apply button has the same text as the initial entry control and
    # sits outside a form on a review page with no inputs. It still cannot send.
    snapshot = browser._snapshots[result['snapshot_id']][0]
    final = next(e for e in snapshot['elements'] if e['label'] == 'Apply')
    with pytest.raises(HumanRequired, match='Final submission'):
        await browser.execute(Decision(Operation.CLICK, final['index']), snapshot)
    assert await browser.page.evaluate('window.submissions') == 0
    assert (await browser.submit(result['snapshot_id']))['status'] == 'confirmed'
    assert await browser.page.evaluate('window.submissions') == 1
    if view == 'links':
        assert not await browser.context.pages[0].evaluate('window.entryClicks')
        assert any('/application_entry.html?view=methods' in p.url for p in browser.context.pages)


async def test_application_entry_respects_section_consent(browser, fixture_server):
    application = {'id': 'entry-consent', 'url': fixture_server + '/application_entry.html?view=buttons'}
    settings = {'section_consent': True, 'assisted_autofill': True}
    result = await browser.fill_application(application, [], [], settings=settings)
    assert result['status'] == 'section_review', result
    assert result['section']['kind'] == 'start_application'
    assert result['section']['fields'][0]['answer'] == 'Apply'
    assert await browser.page.evaluate('window.entryClicks') == 0
    grant = await browser.authorize_section(application['id'], result['snapshot_id'])
    result = await browser.fill_application(application, [], [], settings={**settings, '_section_grant': grant})
    assert result['status'] == 'section_review', result
    assert result['section']['fields'][0]['answer'] == 'Apply Manually'
    assert await browser.page.evaluate('window.entryClicks') == 1
    assert await browser.page.evaluate('window.imports') == 0


async def test_nested_application_route_waits_for_delayed_workday_signin(browser, fixture_server):
    url = fixture_server + '/apply/applyManually'
    await browser.page.route(url, lambda route: route.fulfill(content_type='text/html', body='''
        <main><h1>Careers</h1><button>Sign In</button></main>
        <script>setTimeout(() => document.querySelector('main').innerHTML =
          '<h1>Create Account/Sign In</h1><label>Email <input type="email"></label>' +
          '<label>Password <input type="password"></label>', 1700)</script>'''))
    snapshot = await browser.open(url)
    assert snapshot['blocked'] and snapshot['login']
    assert any(e['label'] == 'Email' for e in snapshot['elements'])
    assert await browser.page.locator('input[type="email"]').input_value() == ''


@pytest.mark.parametrize('markup', [
    '<button type="submit" onclick="window.submissions++">Apply</button>',
    '<form id="application"></form><button form="application" type="button" onclick="window.submissions++">Apply</button>',
    '<form><button type="button" onclick="window.submissions++">Apply</button></form>',
])
async def test_explicit_or_form_associated_apply_cannot_bypass_submission_gate(browser, markup):
    await browser.page.set_content('<script>window.submissions=0</script>' + markup)
    snapshot = await browser.observe()
    target = next(e for e in snapshot['elements'] if e['label'] == 'Apply')
    with pytest.raises(HumanRequired, match='Final submission'):
        await browser.execute(Decision(Operation.CLICK, target['index']), snapshot)
    assert await browser.page.evaluate('window.submissions') == 0


async def test_clipped_upload_uses_only_associated_attach_button(browser, tmp_path):
    await browser.page.set_content('''<style>.visually-hidden{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)}</style>
    <div><button type="button">Attach</button><label class="visually-hidden" for="resume">Attach</label><input id="resume" class="visually-hidden" type="file"></div>
    <div><button type="button">Attach</button><input id="cover_letter" class="visually-hidden" type="file"></div>''')
    path = tmp_path/'cv.txt'
    path.write_text('Synthetic CV')
    browser.allowed_uploads = {path}
    snapshot = await browser.observe()
    target = next(e for e in snapshot['elements'] if e['id']=='resume')
    assert target['upload_proxy_id']
    await browser.execute(Decision(Operation.UPLOAD,target['index']),snapshot,file_path=path)
    assert await browser.page.locator('#resume').evaluate('n=>n.files.length') == 1
    assert await browser.page.locator('#cover_letter').evaluate('n=>n.files.length') == 0


@pytest.mark.parametrize("next_application_id", ["restart", "another-job"])
async def test_auto_apply_reopens_closed_browser_with_fresh_review(browser, fixture_server, candidate, next_application_id):
    application = {"id": "restart", "url": fixture_server + "/generic.html"}
    first = await browser.fill_application(application, candidate, [])
    assert first["status"] == "needs_review", first
    assert await browser.page.evaluate("window.submissions") == 0
    notices = []
    browser.on_disconnect = notices.append
    old_context = browser.context
    await old_context.close()
    assert browser.status()["running"] is False
    assert browser.status()["current_url"] is None
    assert len(notices) == 1
    assert not browser._snapshots
    assert (await browser.submit(first["snapshot_id"]))["status"] == "human_required"
    assert browser.context is None  # Submitting an obsolete review cannot reopen Chrome.
    second = await browser.fill_application({**application, "id": next_application_id}, candidate, [])
    assert second["status"] == "needs_review", second
    assert second["snapshot_id"] != first["snapshot_id"]
    assert browser.context is not old_context
    assert browser.status()["running"] is True
    assert browser.status()["headless"] is True
    assert await browser.page.get_by_label("Email", exact=True).input_value() == "alex@example.test"
    assert await browser.page.evaluate("window.submissions") == 0
    assert (await browser.submit(first["snapshot_id"]))["status"] == "human_required"
    assert await browser.page.evaluate("window.submissions") == 0


async def test_auto_apply_reopens_active_tab_without_reusing_unrelated_tab(browser, fixture_server, candidate):
    application = {"id": "tab-restart", "url": fixture_server + "/generic.html"}
    first = await browser.fill_application(application, candidate, [])
    assert first["status"] == "needs_review", first
    context = browser.context
    unrelated_page = await context.new_page()
    await unrelated_page.set_content("<h1>Unrelated tab</h1>")
    await browser.page.close()
    assert browser.page is None
    assert not browser._snapshots
    result = await browser.fill_application(application, candidate, [])
    assert result["status"] == "needs_review", result
    assert browser.context is context
    assert browser.page is not unrelated_page
    assert await unrelated_page.locator("h1").inner_text() == "Unrelated tab"
    assert await browser.page.get_by_label("Email", exact=True).input_value() == "alex@example.test"
    assert await browser.page.evaluate("window.submissions") == 0


async def test_browser_closed_while_filling_never_returns_ready_review(browser, fixture_server, candidate):
    notices = []
    browser.on_disconnect = notices.append
    async def close_after_first_step(step):
        await browser.context.close()
    browser.on_step = close_after_first_step
    application = {"id": "interrupted", "url": fixture_server + "/generic.html"}
    result = await browser.fill_application(application, candidate, [])
    assert result["status"] in {"human_required", "error"}, result
    assert result.get("error")
    assert not browser._snapshots
    assert not browser._review
    assert len(notices) == 1
    browser.on_step = None
    retry = await browser.fill_application(application, candidate, [])
    assert retry["status"] == "needs_review", retry
    assert await browser.page.evaluate("window.submissions") == 0


async def test_browser_relaunch_preserves_manual_pause_gate(browser, fixture_server, candidate):
    application = {"id": "paused-restart", "url": fixture_server + "/generic.html"}
    result = await browser.fill_application(application, candidate, [])
    assert result["status"] == "needs_review", result
    await browser.takeover()
    await browser.context.close()
    blocked = await browser.fill_application(application, candidate, [])
    assert blocked["status"] == "human_required", blocked
    assert browser.status()["paused"] is True
    assert browser.status()["takeover"] is True
    assert browser.context is None
    await browser.resume()
    retry = await browser.fill_application(application, candidate, [])
    assert retry["status"] == "needs_review", retry
    assert await browser.page.evaluate("window.submissions") == 0


@pytest.mark.asyncio
async def test_tall_iframe_checks_control_hit_point(browser, fixture_server):
    result=await browser.fill_application({'id':'tall-frame','url':fixture_server+'/tall_iframe.html'},[fact('email','frame@example.test',category='contact')],[])
    assert result['status']=='needs_review',result.get('questions')
    frame=next(f for f in browser.page.frames if f.url.endswith('tall_iframe_child.html'))
    assert await frame.locator('input').input_value()=='frame@example.test'


@pytest.mark.parametrize('alternate,expected', [
    (False, 'London, Greater London, England, United Kingdom'), (True, 'London (UK)')])
@pytest.mark.parametrize('remembered', ['London, Greater London, England, United Kingdom', 'London (UK)', 'London UK'])
async def test_location_search_uses_current_employer_options(browser, fixture_server, candidate, alternate, expected, remembered):
    # A remembered geocoder label from another employer must still search by city.
    facts = candidate + [fact('location', remembered)]
    result = await browser.fill_application({'id': 'location', 'url': fixture_server +
        '/search_location.html?single_uk_label' + ('&alternate' if alternate else '')}, facts, [],
        settings={'assisted_autofill': True, 'skip_optional_unknown': True})
    assert result['status'] == 'needs_review', result
    assert await browser.page.locator('#location').input_value() == expected
    assert await browser.page.evaluate('window.queries') == ['London']
    assert await browser.page.evaluate('window.submissions') == 0
    assert browser.edits.records == {}  # No engine events become candidate memory.


async def test_user_edits_win_during_fill_and_survive_rerender(browser, fixture_server, candidate, monkeypatch):
    import asyncio
    entered, resume_fill = asyncio.Event(), asyncio.Event()
    original = browser.execute
    saved = []
    browser.on_user_edit = lambda record: saved.append(record.copy()) or 'user-fact-' + str(len(saved))

    async def wait_for_user(decision, snapshot, **kwargs):
        target = next((e for e in snapshot['elements'] if e['index'] == decision.target), {})
        if target.get('name') == 'email' and not entered.is_set():
            entered.set()
            await resume_fill.wait()
        return await original(decision, snapshot, **kwargs)

    monkeypatch.setattr(browser, 'execute', wait_for_user)
    filling = asyncio.create_task(browser.fill_application({'id': 'manual', 'url': fixture_server + '/manual_edit.html'},
        candidate + [fact('phone', '0123456789')], [], settings={'assisted_autofill': True, 'skip_optional_unknown': True}))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        for name, value in [('first_name', 'User chosen name'), ('email', 'changed@example.test'), ('phone', '')]:
            await browser.page.locator('[name="' + name + '"]').click()
            await browser.page.keyboard.press('Meta+A')
            await browser.page.keyboard.press('Backspace')
            if value:
                await browser.page.keyboard.insert_text(value)
        await browser.page.locator('[name="relocation"]').click()
        # Frameworks replace DOM nodes during a render; semantic identity persists.
        await browser.page.evaluate('''() => {const old=document.querySelector('[name=first_name]');
            const replacement=old.cloneNode(true);replacement.value=old.value;old.replaceWith(replacement)}''')
        await browser.edits.flush()
        resume_fill.set()
        result = await asyncio.wait_for(filling, 15)
        assert result['status'] == 'needs_review', result
        assert await browser.page.locator('[name=first_name]').input_value() == 'User chosen name'
        assert await browser.page.locator('[name=email]').input_value() == 'changed@example.test'
        assert await browser.page.locator('[name=phone]').input_value() == ''
        assert await browser.page.locator('[name=relocation]').is_checked()
        assert await browser.page.locator('[name=last_name]').input_value() == 'Example'
        assert await browser.page.evaluate('window.submissions') == 0
        assert {'User chosen name', 'changed@example.test', '', 'Yes'} <= {r['answer'] for r in saved}
        assert all(a['verified'] for a in result['answers']), result['answers']
        assert any(a.get('source') == 'user_edit' for a in result['answers'])
    finally:
        resume_fill.set()
        if not filling.done():
            filling.cancel()
            await asyncio.gather(filling, return_exceptions=True)


async def test_page_scripts_cannot_forge_user_correction(browser, fixture_server, candidate):
    result = await browser.fill_application({'id': 'script', 'url': fixture_server + '/manual_edit.html'},
        candidate + [fact('phone', '0123456789')], [], settings={'assisted_autofill': True, 'skip_optional_unknown': True})
    assert result['status'] == 'needs_review', result
    await browser.page.evaluate('''() => {const input=document.querySelector('[name=email]');
        input.value='forged@example.test';input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));}''')
    await browser.edits.flush()
    assert browser.edits.records == {}
    assert await browser.page.evaluate('typeof window.meridianUserEdit') == 'undefined'
    assert await browser.page.evaluate('typeof window.__meridianEdits') == 'undefined'


async def test_location_menu_opens_then_searches_linked_portal(browser, fixture_server, candidate):
    result = await browser.fill_application({'id': 'portal', 'url': fixture_server + '/search_location_portal.html'},
        candidate + [fact('location', 'London (UK)')], [],
        settings={'assisted_autofill': True, 'skip_optional_unknown': True})
    assert result['status'] == 'needs_review', {k:result[k] for k in ('status','error','questions','steps')}
    assert await browser.page.locator('#location').get_attribute('aria-valuetext') == 'London (UK)'
    assert await browser.page.evaluate('window.queries') == ['London']
    assert browser.edits.records == {}
    assert await browser.page.evaluate('window.submissions') == 0


@pytest.mark.parametrize('fixture', ['search_location.html', 'search_location_portal.html'])
async def test_user_location_selection_is_remembered_after_search(browser, fixture_server, candidate, monkeypatch, fixture):
    import asyncio
    entered, continue_fill = asyncio.Event(), asyncio.Event()
    original = browser.execute
    saved = []
    browser.on_user_edit = lambda record: saved.append(record.copy()) or 'location-correction'

    async def user_before_location(decision, snapshot, **kwargs):
        target = next((e for e in snapshot['elements'] if e['index'] == decision.target), {})
        if target.get('id') == 'location' and not entered.is_set():
            entered.set()
            await continue_fill.wait()
        return await original(decision, snapshot, **kwargs)

    monkeypatch.setattr(browser, 'execute', user_before_location)
    filling = asyncio.create_task(browser.fill_application({'id': 'manual-location', 'url': fixture_server + '/' + fixture},
        candidate + [fact('location', 'London, UK')], [], settings={'assisted_autofill': True, 'skip_optional_unknown': True}))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await browser.page.locator('#location').click()
        if fixture == 'search_location_portal.html':
            await browser.page.get_by_role('searchbox').click()
        await browser.page.keyboard.insert_text('London')
        await browser.page.get_by_role('option', name='London (UK)', exact=True).click()
        await browser.edits.flush()
        continue_fill.set()
        result = await asyncio.wait_for(filling, 15)
        assert result['status'] == 'needs_review', result
        selected = await browser.page.locator('#location').get_attribute('aria-valuetext') if fixture == 'search_location_portal.html' else await browser.page.locator('#location').input_value()
        assert selected == 'London (UK)'
        assert saved[-1]['answer'] == 'London (UK)'
        assert not any(r['answer'] == 'London' for r in saved)  # Search text is never a saved location.
        assert await browser.page.evaluate('window.submissions') == 0
    finally:
        continue_fill.set()
        if not filling.done():
            filling.cancel()
            await asyncio.gather(filling, return_exceptions=True)


async def test_user_typing_during_actual_write_wins_without_stopping_other_fields(browser, fixture_server, candidate, monkeypatch):
    import asyncio
    entered, release = asyncio.Event(), asyncio.Event()
    original = browser._write_text
    saved = []
    browser.on_user_edit = lambda record: saved.append(record.copy()) or 'race-correction'

    async def delayed_write(node, value):
        if value == 'Alex' and not entered.is_set():
            entered.set()
            await release.wait()
        return await original(node, value)

    monkeypatch.setattr(browser, '_write_text', delayed_write)
    filling = asyncio.create_task(browser.fill_application({'id': 'race', 'url': fixture_server + '/manual_edit.html'},
        candidate + [fact('phone', '0123456789')], [], settings={'assisted_autofill': True, 'skip_optional_unknown': True}))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await browser.page.locator('[name=first_name]').click()
        # A real key records user intent while this exact engine action is active.
        await browser.page.keyboard.type('Chosen during write')
        release.set()
        result = await asyncio.wait_for(filling, 15)
        assert result['status'] == 'needs_review', result
        assert await browser.page.locator('[name=first_name]').input_value() == 'Chosen during write'
        assert await browser.page.locator('[name=email]').input_value() == 'alex@example.test'
        assert await browser.page.locator('[name=last_name]').input_value() == 'Example'
        assert saved[-1]['answer'] == 'Chosen during write'
        assert await browser.page.evaluate('window.submissions') == 0
    finally:
        release.set()
        if not filling.done():
            filling.cancel()
            await asyncio.gather(filling, return_exceptions=True)


async def test_next_application_prioritizes_manual_memory_over_structured_inference(browser, fixture_server, candidate):
    from jobagent.db import Database
    from jobagent.core import prepare_application
    from jobagent.discovery import ingest_job
    from jobagent.application_profile import remember_answer, application_knowledge
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.store import entity, ingest_fact
    db = Database(browser.data_dir / 'corrections.sqlite')
    job = ingest_job(db, {'title': 'Engineer', 'company': 'Previous employer',
                         'url': 'https://jobs.example.test/previous'})[0]
    previous = {**prepare_application(db, job['id']), 'company': 'Previous employer', 'location': 'London'}
    person = entity(db, 'person', 'Original candidate')
    ingest_fact(db, person, 'first_name', 'Old structured name', source='Fixture confirmation', evidence={}, verified=True)
    for key, value in [('email','alex@example.test'), ('last_name','Example'), ('phone','0123456789')]:
        ingest_fact(db, person, key, value, source='Fixture confirmation', evidence={}, verified=True)
    remember_answer(db, previous, 'First name', 'Remembered correction', reusable=True, direct_edit=True)
    remember_answer(db, previous, 'Phone', '', reusable=True, skip=True, direct_edit=True)
    future = {'id': 'next-employer', 'company': 'Next employer', 'location': 'London',
              'url': fixture_server + '/manual_edit.html?identity_only'}
    memories, _ = application_knowledge(db, future, db.query("SELECT * FROM facts WHERE verification_status='verified'"))
    result = await browser.fill_application(future, candidate + memories, [],
        settings={'assisted_autofill': True, 'skip_optional_unknown': True, '_candidate_engine': CandidateEngine(db)})
    assert await browser.page.locator('[name=first_name]').input_value() == 'Remembered correction'
    assert result['status'] == 'needs_review', str(result['questions'])
    assert await browser.page.locator('[name=phone]').input_value() == ''
    assert not any(a['question'] == 'Phone' and a['answer'] == '0123456789' for a in result['answers'])
    assert await browser.page.locator('[name=last_name]').input_value() == 'Example'
    assert await browser.page.evaluate('window.submissions') == 0
