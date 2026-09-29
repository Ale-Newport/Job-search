"""Email status regressions, with every API response intercepted. No accounts or mailbox are accessed."""
import argparse
import asyncio
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1421')
    args = parser.parse_args()
    state = {'connected': False, 'phase': 'idle', 'fail': True, 'error': None, 'synced': False}
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def api(route):
            path = urlparse(route.request.url).path
            result = {}
            if path == '/api/health':
                result = {'status': 'ok', 'name': 'Meridian'}
            elif path == '/api/onboarding':
                result = {'hidden': True, 'steps': [], 'completed': 0}
            elif path == '/api/integrations':
                result = {'items': [{'provider': 'gmail', 'status': 'connected' if state['connected'] else 'configured',
                                     'config': {'account_email': 'candidate@example.test'} if state['connected'] else {},
                                     'last_sync': '2026-09-29T15:00:00+00:00' if state['synced'] else None,
                                     'last_error': state['error']}], 'total': 1}
            elif path == '/api/email/status':
                result = {'syncing': state['phase'] != 'idle', 'phase': state['phase'],
                          'provider': 'gmail', 'messages_read': 0}
            elif path == '/api/email/sync':
                state['error'] = 'Allow Meridian in the Keychain prompt, then try again.' if state['fail'] else None
                state['synced'] = not state['fail']
                result = {'imported': 0, 'errors': [{'provider': 'gmail', 'error': state['error']}] if state['fail'] else []}
            elif path in ('/api/emails', '/api/human-tasks'):
                result = {'items': [], 'total': 0}
            await route.fulfill(status=200, json=result)

        await page.route('**/api/**', api)
        await page.route('https://**', lambda route: route.abort())
        await page.goto(args.base_url)
        await page.get_by_role('navigation').get_by_role('button', name='Email', exact=True).click()
        card = page.locator('.integration-card').filter(has=page.get_by_role('heading', name='Gmail', exact=True))
        await expect(card.get_by_text('Configured', exact=True)).to_be_visible()
        # External OAuth finishes while the Email page stays open, without a focus event.
        state['connected'], state['phase'] = True, 'waiting_for_keychain'
        await expect(card.get_by_text('Connected', exact=True)).to_be_visible(timeout=7000)
        await expect(card.get_by_text('Account connected. First sync pending.', exact=True)).to_be_visible()
        await expect(page.get_by_text('Waiting for macOS Keychain', exact=True)).to_be_visible()
        await expect(page.get_by_role('button', name='Sync inbox', exact=True)).to_be_disabled()
        state['phase'] = 'idle'
        await expect(page.get_by_role('button', name='Sync inbox', exact=True)).to_be_enabled(timeout=7000)
        await page.get_by_role('button', name='Sync inbox', exact=True).click()
        await expect(page.get_by_text('Email sync needs attention. Check the account message below.', exact=True)).to_be_visible()
        await expect(card.get_by_text(state['error'], exact=True)).to_be_visible()
        await expect(page.get_by_text('Email sync completed.', exact=False)).to_have_count(0)
        state['fail'] = False
        await page.get_by_role('button', name='Sync inbox', exact=True).click()
        await expect(page.get_by_text('Email sync completed. 0 new messages imported.', exact=True)).to_be_visible()
        await expect(card.get_by_text('Connected', exact=True)).to_be_visible()
        await expect(card.get_by_text('Account connected. First sync pending.', exact=True)).to_have_count(0)
        assert not errors, errors
        await browser.close()
    print('Passed: OAuth refresh, connected account, Keychain progress, failed-sync feedback and successful sync.')


asyncio.run(main())
