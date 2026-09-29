"""Truthful setup UI checks against an isolated QA backend and Vite proxy.

Run: .venv/bin/python frontend/tests/onboarding_e2e.py
The data directory must be .local-data/qa or .local-data/onboarding-ui-qa. No credentials, real accounts,
remote model calls, websites, or applications are used. IMAP responses are
stubbed only inside this test to verify the save/connect request contract.
"""
import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    headers = {"Authorization": "Bearer meridian-development-token"}
    output = Path(tempfile.gettempdir()) / "meridian-ui-qa"
    output.mkdir(exist_ok=True)
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        health = await page.request.get(base + "/api/health", headers=headers)
        assert health.ok, await health.text()
        assert (await health.json())["data_dir"].rstrip("/").endswith(("/.local-data/qa", "/.local-data/onboarding-ui-qa", "/.local-data/tracking-qa")), "Refusing to modify a non-QA workspace"
        response = await page.request.get(base + "/api/onboarding", headers=headers)
        assert response.ok, await response.text()
        original = await response.json()
        settings_response = await page.request.get(base + "/api/settings", headers=headers)
        settings = await settings_response.json()
        await page.request.patch(base + "/api/onboarding", headers=headers, data={"hidden": False})
        try:
            print("Checking guide evidence and visibility", flush=True)
            await page.goto(base)
            await page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Show guide", exact=True).click()
            await expect(page.locator("[data-step]")).to_have_count(8)
            for step in original["steps"]:
                await expect(page.locator(f'[data-step="{step["id"]}"]')).to_have_attribute("data-status", step["status"])
            await page.get_by_role("button", name="Hide guide", exact=True).click()
            await expect(page.locator("[data-step]")).to_have_count(0)
            hidden = await (await page.request.get(base + "/api/onboarding", headers=headers)).json()
            assert hidden["completed"] == original["completed"], "Hiding must not complete setup"
            await page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Show guide", exact=True).click()
            await expect(page.locator("[data-step]")).to_have_count(8)
            print("Checking direct links and DeepSeek preset without saving credentials", flush=True)
            await page.locator('[data-step="ai"]').click()
            await page.get_by_role("heading", name="Text generation", exact=True).wait_for()
            await page.get_by_role("button", name="Set up DeepSeek", exact=True).click()
            preset = page.get_by_role("dialog", name="Set up DeepSeek", exact=True)
            assert await preset.get_by_label("Monthly remote AI budget (USD)", exact=True).input_value() == str(settings.get("monthly_budget", 0))
            assert await preset.get_by_label("Input price per million tokens (USD)", exact=True).input_value() == "0.3"
            assert await preset.get_by_label("Output price per million tokens (USD)", exact=True).input_value() == "1.2"
            assert await preset.get_by_label("DeepSeek API key", exact=True).input_value() == ""
            await preset.get_by_role("button", name="Cancel", exact=True).click()
            await page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Show guide", exact=True).click()
            await page.locator('[data-step="profile"]').click()
            assert await page.get_by_label("Fact verification", exact=True).input_value() == "unverified"
            await page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Show guide", exact=True).click()
            await page.locator('[data-step="boundaries"]').click()
            await page.get_by_role("heading", name="Application safety & scheduling", exact=True).wait_for()
            await page.get_by_role("button", name=re.compile(r"^(I have reviewed these boundaries|Confirm boundaries again)$")).click()
            await page.get_by_role("button", name="Record my review", exact=True).click()
            await page.get_by_role("button", name="Confirm boundaries again", exact=True).wait_for()
            print("Checking explicit browser confirmation and IMAP connect contract", flush=True)
            await page.get_by_role("navigation").get_by_role("button", name="Advanced automation", exact=True).click()
            await page.get_by_role("button", name=re.compile(r"^(Confirm I am signed in|Update sign-in confirmation)$")).click()
            account = page.get_by_role("dialog", name="Confirm browser sign-in", exact=True)
            checkbox = account.get_by_role("checkbox")
            assert not await checkbox.is_checked()
            await expect(checkbox).to_have_attribute("required", "")
            await account.get_by_role("button", name="Cancel", exact=True).click()
            sequence = []

            async def mock_imap(route):
                request = route.request
                sequence.append((request.method, request.url.rsplit("/", 1)[-1]))
                if request.method == "PUT":
                    assert "secret" not in request.post_data_json
                    await route.fulfill(status=200, json={"provider": "imap", "status": "configured"})
                else:
                    await route.fulfill(status=422, json={"detail": "Synthetic authentication failure; no mailbox contacted"})

            await page.route("**/api/integrations/imap", mock_imap)
            await page.route("**/api/integrations/imap/connect", mock_imap)
            await page.get_by_role("navigation").get_by_role("button", name="Email", exact=True).click()
            card = page.locator(".integration-card").filter(has=page.get_by_role("heading", name="IMAP over TLS", exact=True))
            await card.get_by_role("button").click()
            imap = page.get_by_role("dialog", name="Configure IMAP", exact=True)
            await imap.get_by_label("IMAP host", exact=True).fill("mail.synthetic.invalid")
            await imap.get_by_label("Email / username", exact=True).fill("synthetic@example.test")
            await imap.get_by_role("button", name="Save & connect", exact=True).click()
            await imap.get_by_text("Synthetic authentication failure; no mailbox contacted", exact=True).wait_for()
            assert sequence == [("PUT", "imap"), ("POST", "connect")], sequence
            await imap.get_by_role("button", name="Cancel", exact=True).click()
            await page.unroute("**/api/integrations/imap", mock_imap)
            await page.unroute("**/api/integrations/imap/connect", mock_imap)
            await page.get_by_role("navigation").get_by_role("button", name="Documents", exact=True).click()
            async with page.expect_response(lambda response: response.url.endswith("/api/documents/import") and response.request.method == "POST") as imported_response:
                await page.get_by_label("Import document", exact=True).set_input_files({"name": "setup-review-synthetic.txt", "mimeType": "text/plain", "buffer": b"Skills: Python, TypeScript\nEducation:\nBSc Computer Science, Example Test University, 2024\nExperience:\nTest engineering project\nBuilt a local synthetic testing tool.\n"})
            imported = await (await imported_response.value).json()
            await expect(page.locator(".import-fact").first).to_be_visible()
            first = page.locator(".import-fact").first
            await expect(first.locator(".tag.amber")).to_have_text("Unverified")
            await first.get_by_role("button", name="Verify fact", exact=True).click()
            await expect(first.locator(".tag.green")).to_have_text("Verified")
            await first.get_by_role("button", name="Reject fact", exact=True).click()
            await expect(first.locator(".tag.red")).to_have_text("Rejected")
            print("Checking document re-extraction and current-version idempotence", flush=True)
            await page.locator(f'tr[data-document-id="{imported["document"]["id"]}"]').get_by_role("button", name="Re-extract facts from setup-review-synthetic.txt", exact=True).click()
            reextract = page.get_by_role("dialog", name="Re-extract facts from this document?", exact=True)
            await reextract.get_by_text("Original versions and verified or locked facts are preserved.", exact=False).wait_for()
            async with page.expect_response(lambda response: response.url.endswith("/reimport") and response.request.method == "POST") as reimported_response:
                await reextract.get_by_role("button", name="Re-extract facts", exact=True).click()
            reimported = await (await reimported_response.value).json()
            assert reimported["already_current"] is True, reimported
            assert reimported["version"]["id"] == imported["version"]["id"]
            await page.get_by_role("heading", name="Extraction is up to date", exact=True).wait_for()
            await page.screenshot(path=str(output / "import-fact-review.png"), full_page=True)
            await page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Show guide", exact=True).click()
            await expect(page.locator("[data-step]")).to_have_count(8)
            await page.screenshot(path=str(output / "onboarding-progress.png"), full_page=True)
            if errors:
                raise AssertionError("\n".join(errors))
            print(json.dumps({"result": "passed", "checks": "8 evidence states, hide preserves progress, direct links, zero inherited budget and blank key, boundary confirmation, explicit browser checkbox, IMAP save/connect failure feedback, per-fact verification/rejection, idempotent document re-extraction", "screenshots": str(output)}), flush=True)
        finally:
            await page.request.patch(base + "/api/onboarding", headers=headers, data={"hidden": original["hidden"]})
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
