from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from jobagent.automation import BrowserManager


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def fixture_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(Path(__file__).parent / "fixtures"))
    )
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


def settings():
    return {
        "assisted_autofill": True,
        "skip_optional_unknown": True,
        "sensitive_policies": {"personal_disclosures": {"action": "leave_blank"}},
    }


def facts():
    return [
        {"id": "name", "key": "first_name", "value": "Alex", "verification_status": "verified"},
        {
            "id": "old-pronouns",
            "key": "Preferred pronouns",
            "value": "he/him",
            "verification_status": "verified",
            "profile_explicit": True,
        },
        {
            "id": "work",
            "key": "Describe software you built for mental health services",
            "value": "I built a scheduling tool.",
            "verification_status": "verified",
            "profile_explicit": True,
        },
    ]


async def test_saved_and_prefilled_disclosures_are_cleared_without_substitution(browser, fixture_server):
    result = await browser.fill_application(
        {"id": "disclosures", "url": fixture_server + "/disclosure_policy.html"}, facts(), [], settings=settings()
    )
    assert result["status"] == "human_required", result
    assert await browser.page.locator("[name=first_name]").input_value() == "Alex"
    assert await browser.page.locator("[name=pronouns]").input_value() == ""
    assert await browser.page.locator("[name=ethnicity]").input_value() == ""
    assert not await browser.page.locator("[name=disabled]").is_checked()
    assert not await browser.page.locator("[name=gender]:checked").count()
    assert not await browser.page.locator(".select__single-value").count()
    assert await browser.page.locator("[name=health]").input_value() == ""
    assert await browser.page.locator("[name=work]").input_value() == "I built a scheduling tool."
    assert all(q["kind"] == "personal_disclosure_policy" and q["skip_inline_question"] for q in result["questions"])
    assert all(a["question"] not in {"Preferred pronouns", "Ethnic origin"} for a in result["answers"])
    assert await browser.page.evaluate("window.submissions") == 0


async def test_section_review_describes_clearing_and_preserves_blank_policy(browser, fixture_server):
    options = {**settings(), "section_consent": True}
    app = {"id": "review", "url": fixture_server + "/disclosure_policy.html"}
    result = await browser.fill_application(app, facts(), [], settings=options)
    assert result["status"] == "section_review", result
    clears = [field for field in result["section"]["fields"] if field["operation"] == "CLEAR"]
    assert clears and all(field["answer"] == "" for field in clears)
    assert await browser.page.locator("[name=pronouns]").input_value() == "he/him"
    options["_section_grant"] = await browser.authorize_section(app["id"], result["snapshot_id"])
    result = await browser.fill_application(app, facts(), [], settings=options)
    # Separate fieldsets are separate reviewed sections; each requires its own grant.
    for _ in range(10):
        if result["status"] != "section_review":
            break
        options["_section_grant"] = await browser.authorize_section(app["id"], result["snapshot_id"])
        result = await browser.fill_application(app, facts(), [], settings=options)
    assert result["status"] == "human_required", result
    assert await browser.page.locator("[name=pronouns]").input_value() == ""
    assert not await browser.page.locator("[name=gender]:checked").count()
    assert await browser.page.evaluate("window.submissions") == 0


async def test_custom_value_without_clear_control_blocks_and_never_chooses_decline(browser, fixture_server):
    app = {"id": "unclear", "url": fixture_server + "/disclosure_policy.html"}
    await browser.open(app["url"])
    browser._active_application_id = app["id"]
    await browser.page.locator(".select__clear-indicator").evaluate("(node) => node.remove()")
    await browser.page.locator("#required_health").evaluate("(node) => node.required=false")
    result = await browser.fill_application(app, facts(), [], settings=settings())
    assert result["status"] == "human_required", result
    assert any(q["kind"] == "personal_disclosure_clear" and q["skip_inline_question"] for q in result["questions"])
    assert await browser.page.locator(".select__single-value").inner_text() == "Example faith"
    assert await browser.page.evaluate("window.submissions") == 0


async def test_typed_policy_controls_direct_runner_without_orchestrator_settings(browser, fixture_server, tmp_path):
    from jobagent.db import Database
    from jobagent.intelligence.engine import CandidateEngine
    from jobagent.intelligence.store import entity, ingest_fact, policy

    db = Database(tmp_path / "typed-policy.sqlite")
    person = entity(db, "person", "Alex")
    ingest_fact(db, person, "first_name", "Alex", source="Fixture confirmation", evidence={}, verified=True)
    policy(db, "personal_disclosures", "leave_blank")
    result = await browser.fill_application(
        {"id": "typed-policy", "url": fixture_server + "/disclosure_policy.html"},
        facts(),
        [],
        settings={"_candidate_engine": CandidateEngine(db), "assisted_autofill": True, "skip_optional_unknown": True},
    )
    assert result["status"] == "human_required", result
    assert await browser.page.locator("[name=pronouns]").input_value() == ""
    assert await browser.page.locator("[name=ethnicity]").input_value() == ""
    assert not await browser.page.locator("[name=gender]:checked").count()
    assert any(q["kind"] == "personal_disclosure_policy" for q in result["questions"])
    assert await browser.page.evaluate("window.submissions") == 0
