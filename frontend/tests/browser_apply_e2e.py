"""Chrome entry UI regression. Browser metadata and every API response are synthetic."""
import argparse
import asyncio
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1432')
    args = parser.parse_args()
    sent, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.add_init_script('''window.__TAURI_INTERNALS__ = {invoke: async cmd => {
          if (cmd === 'connection_info') return {base_url:'/api',token:'synthetic-local-token'};
          if (cmd === 'current_chrome_tab') return {url:'https://unregistered.example.test/apply/42',title:'Graduate Engineer'};
          return null;
        }};''')
        async def api(route):
            path = urlparse(route.request.url).path
            result = {}
            if path == '/api/onboarding':
                result = {'hidden': True, 'steps': [], 'completed': 0}
            elif path == '/api/documents':
                result = {'items': [
                    {'id': 'approved', 'name': 'Approved CV', 'latest_version_id': 'approved-version', 'latest_version': {'id': 'approved-version', 'approved': True}},
                    {'id': 'draft', 'name': 'Unapproved draft', 'latest_version': {'id': 'draft-version', 'approved': False}},
                ], 'total': 2}
            elif path == '/api/applications/from-browser':
                sent.append(route.request.post_data_json)
                result = {'application_id': 'application-fixture', 'run_id': 'run-fixture', 'status': 'running'}
            elif path == '/api/applications/application-fixture':
                result = {'id': 'application-fixture', 'job': {'title': 'Graduate Engineer', 'company': 'New Company'}, 'mode': 'review', 'status': 'PREPARING', 'runs': [], 'answers': [], 'documents': [], 'events': [], 'human_tasks': []}
            elif path in ('/api/human-tasks', '/api/applications'):
                result = {'items': [], 'total': 0}
            await route.fulfill(status=200, json=result)
        await page.route('**/api/**', api)
        await page.route('https://**', lambda route: route.abort())
        await page.goto(args.base_url)
        await page.get_by_role('button', name='Auto Apply from Chrome', exact=True).click()
        dialog = page.get_by_role('dialog', name='Auto Apply from Chrome', exact=True)
        await dialog.get_by_role('button', name='Use current Chrome tab').click()
        await expect(dialog.get_by_label('Application URL')).to_have_value('https://unregistered.example.test/apply/42')
        await expect(dialog.get_by_label('Job title')).to_have_value('Graduate Engineer')
        await dialog.get_by_label('Company', exact=True).fill('New Company')
        await expect(dialog.get_by_label('Approved CV').get_by_role('option', name='Unapproved draft')).to_have_count(0)
        await dialog.get_by_label('Approved CV').select_option('approved-version')
        await dialog.get_by_role('button', name='Fill and review').click()
        await expect(dialog).to_have_count(0)
        assert sent == [{'url': 'https://unregistered.example.test/apply/42', 'title': 'Graduate Engineer', 'company': 'New Company', 'document_version_id': 'approved-version'}]
        assert not errors, errors
        await browser.close()
    print('Passed: current Chrome metadata → arbitrary application URL → approved CV → fill-only review request. Synthetic services; no employer or AI calls.')


if __name__ == '__main__':
    asyncio.run(main())
