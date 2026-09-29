"""Real UI/API smoke test. Run only against an isolated QA data directory.

../.venv/bin/python tests/ui_smoke.py --base-url http://127.0.0.1:1420
No external job board is contacted and no application is submitted.
"""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1420')
    args = parser.parse_args()
    output = Path(tempfile.gettempdir()) / 'meridian-ui-qa'
    output.mkdir(exist_ok=True)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(args.base_url)
        await page.get_by_role('heading', name='A clearer path to your next role.').wait_for()
        await page.get_by_text('Jobs discovered', exact=True).wait_for()
        await page.screenshot(path=str(output / 'dashboard.png'), full_page=True)
        pages = [('Discover', 'Discover'), ('Jobs', 'Jobs'), ('Applications', 'Applications'), ('Review queue', 'Review queue'), ('Companies', 'Companies'), ('Profile', 'Your profile'), ('Documents', 'Documents'), ('Email', 'Email'), ('Analytics', 'Analytics'), ('Automation', 'Automation'), ('Activity', 'Activity'), ('Settings', 'Settings')]
        for nav, heading in pages:
            print(f'Checking view: {nav}', flush=True)
            await page.get_by_role('navigation').get_by_role('button', name=nav, exact=False).click()
            await page.get_by_role('heading', name=heading, exact=True).first.wait_for()
            await page.wait_for_timeout(180)
            if await page.get_by_text('Something needs attention', exact=True).count():
                raise AssertionError(f'{nav} returned an API error: {await page.locator(".error-box").all_text_contents()}')
        await page.get_by_role('button', name='AI & providers', exact=True).click()
        await page.get_by_role('heading', name='Local Laya runtime', exact=True).wait_for()
        await page.get_by_role('button', name='Automation', exact=True).last.click()
        await page.get_by_role('heading', name='Rule builder', exact=True).wait_for()
        await page.get_by_role('button', name='Add rule', exact=True).click()
        await page.get_by_role('textbox', name='Rule name').fill('UI smoke rule')
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_text('UI smoke rule', exact=True).wait_for()
        await page.get_by_role('button', name='Remove UI smoke rule', exact=True).click()
        await page.get_by_role('button', name='Remove rule', exact=True).click()
        await page.get_by_role('navigation').get_by_role('button', name='Discover', exact=True).click()
        print('Checking source configuration CRUD', flush=True)
        await page.get_by_role('button', name='Add source', exact=True).first.click()
        await page.get_by_role('textbox', name='Source name', exact=True).fill('UI smoke source')
        await page.get_by_role('textbox', name='Board, careers page, or repository URL', exact=True).fill('https://meridian-fixture.invalid/careers')
        await page.get_by_role('checkbox', name='Enable scheduled discovery', exact=True).uncheck()
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_text('UI smoke source', exact=True).wait_for()
        await page.get_by_role('button', name='Delete UI smoke source', exact=True).click()
        await page.get_by_role('button', name='Delete', exact=True).click()
        await page.get_by_role('button', name='New profile', exact=True).click()
        await page.get_by_role('textbox', name='Search profile name').fill('UI smoke graduate search')
        await page.get_by_role('textbox', name='Roles', exact=True).fill('Graduate Software Engineer, AI Engineer')
        await page.get_by_role('textbox', name='Locations', exact=True).fill('London, Remote')
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_role('heading', name='UI smoke graduate search', exact=True).wait_for()
        await page.get_by_role('button', name='Delete UI smoke graduate search', exact=True).click()
        await page.get_by_role('button', name='Delete', exact=True).click()
        await page.get_by_role('navigation').get_by_role('button', name='Profile', exact=True).click()
        await page.get_by_role('button', name='Add fact', exact=True).click()
        await page.get_by_role('textbox', name='Fact name').fill('UI smoke skill')
        await page.get_by_role('textbox', name='Value', exact=True).fill('Python')
        await page.get_by_role('combobox', name='Category', exact=True).select_option('skill')
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_text('UI Smoke Skill', exact=True).wait_for()
        await page.get_by_role('button', name='Verify UI smoke skill', exact=True).click()
        await page.get_by_role('button', name='Delete UI smoke skill', exact=True).click()
        await page.get_by_role('button', name='Delete fact', exact=True).click()
        await page.get_by_role('navigation').get_by_role('button', name='Companies', exact=True).click()
        await page.get_by_role('button', name='Add company', exact=True).first.click()
        await page.get_by_role('textbox', name='Company name').fill('UI smoke company')
        await page.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_role('heading', name='UI smoke company', exact=True).wait_for()
        await page.get_by_role('button', name='Delete UI smoke company', exact=True).click()
        await page.get_by_role('button', name='Remove company', exact=True).click()
        await page.get_by_role('button', name='Search anything').click()
        await page.get_by_role('dialog', name='Search your workspace').wait_for()
        await page.keyboard.press('Escape')
        await page.get_by_role('navigation').get_by_role('button', name='Overview', exact=True).click()
        await page.get_by_role('button', name='Toggle appearance', exact=True).click()
        await page.wait_for_timeout(200)
        await page.screenshot(path=str(output / 'dashboard-dark.png'), full_page=True)
        await page.get_by_role('button', name='Toggle appearance', exact=True).click()
        await page.wait_for_timeout(200)
        await page.set_viewport_size({'width': 720, 'height': 1000})
        await page.screenshot(path=str(output / 'dashboard-compact.png'), full_page=True)
        await browser.close()
    if errors:
        raise AssertionError('\n'.join(errors))
    print(json.dumps({'result': 'passed', 'checked': '13 views, local Laya status, rule CRUD, search profile CRUD, verified fact CRUD, company CRUD, Command-K, light/dark, compact viewport', 'screenshots': str(output)}))

if __name__ == '__main__':
    asyncio.run(main())
