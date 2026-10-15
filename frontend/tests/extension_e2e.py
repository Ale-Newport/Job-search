"""Browser-assistant management UI regression. Every API response is synthetic: no backend, extension, employer
site or AI provider is contacted, and nothing outside the page's own mocked routes is reachable.

  vite on a spare port:  npx vite --host 127.0.0.1 --port 1432 --strictPort   (from frontend/)
  .venv/bin/python frontend/tests/extension_e2e.py --base-url http://127.0.0.1:1432 [--browser chrome|webkit] [--out DIR]

  --live  skips the mocks: starts its OWN isolated stack (the real backend on a temporary data directory with the loopback
          bridge, and a vite dev server proxying to it; --base-url is ignored) and drives the panel with a stand-in for the
          extension's service worker (frontend/tests/live_extension.py) over the real bridge. See frontend/tests/extension_live.py.
"""
import argparse
import asyncio
import copy
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

NOW = '2026-10-13T12:00:00+00:00'
PATH = '/Users/test/Library/Application Support/Meridian/extensions/chrome'
SETTINGS = {
    'enabled': True, 'language': 'auto', 'auto_advance': True, 'skip_filled': True, 'tab_inserts_protected': False,
    'save_corrections': 'ask', 'background_research': True, 'background_drafts': True, 'web_lookup': True,
    'hint_on_ats': True, 'draft_budget_usd': None, 'always_on_sites': [],
}
BASE = {
    'state': 'not_installed', 'server_time': NOW, 'app_version': '1.1.0',
    'bridge': {'state': 'listening', 'port': 47615, 'candidate_ports': [47615, 47616, 47617], 'conflict_process': None, 'message': None},
    'browsers': {
        'chrome': {'browser_installed': True, 'extension_path': None, 'bundled_version': '1.1.0', 'state': 'not_installed',
                   'seen_unpaired_at': None, 'seen_version': None},
        'safari': {'browser_installed': True, 'extension_path': None, 'bundled_version': '1.1.0', 'state': 'not_installed',
                   'seen_unpaired_at': None, 'seen_version': None, 'needs_unsigned_toggle': False},
    },
    'clients': [], 'pending_pairing': None, 'settings': SETTINGS,
    'drafts': {'configured': True, 'provider': 'deepseek', 'model': 'deepseek-v4-pro', 'message': '', 'month': '2026-10',
               'spent_usd': 0.4231, 'calls': 31, 'monthly_limit_usd': 10, 'unlimited': False},
}
CLIENT = {'id': 'c1', 'browser': 'chrome', 'label': 'Chrome on this Mac', 'version': '1.1.0', 'paired_at': NOW,
          'last_seen_at': '2026-10-13T11:57:00+00:00', 'last_error': None, 'outdated': False,
          'stats': {'pages': 12, 'suggestions': 140, 'insertions': 96}}
ACTIVITY = [
    {'id': 'a1', 'created_at': NOW, 'message': 'Assisted 14 fields on boards.greenhouse.io', 'origin': 'assistant', 'kind': 'page_assisted',
     'browser': 'chrome', 'host': 'boards.greenhouse.io', 'company': 'Example Co', 'title': 'Engineer'},
    {'id': 'a2', 'created_at': '2026-10-13T11:00:00+00:00', 'message': 'Left a sensitive question blank', 'origin': 'assistant',
     'kind': 'skipped_sensitive', 'browser': 'chrome', 'host': 'jobs.lever.co', 'company': None, 'title': None},
]
JOB = {'id': 'j1', 'title': 'Software Engineer', 'company': 'Example Co', 'location': 'London', 'source': 'fixture',
       'match_score': 82, 'status': 'DISCOVERED', 'submitted': False, 'url': 'https://example.test/job', 'metadata': {}}
APPLICATION = {
    'id': 'a1', 'job_id': 'j1', 'status': 'APPLIED', 'mode': 'manual', 'created_at': NOW, 'title': 'Software Engineer',
    'company': 'Example Co', 'job': {'id': 'j1', 'title': 'Software Engineer', 'company': 'Example Co'},
    'assistant': {'browser': 'chrome', 'fields_inserted': 14, 'last_used_at': NOW},
    'events': [{'id': 'e1', 'status': 'APPLIED', 'message': 'Application recorded.', 'origin': 'extension', 'created_at': NOW}],
    'answers': [{'id': 'v1', 'question': 'Why Example Co?', 'answer': 'Because of the mission.', 'verified': True, 'origin': 'extension'},
                {'id': 'v2', 'question': 'Notice period', 'answer': 'One month', 'verified': True, 'origin': 'manual'}],
    'answer_history': [{'id': 'h1', 'question': 'Notice period', 'answer': 'Two months', 'origin': 'extension', 'superseded_at': NOW},
                       {'id': 'h2', 'question': 'Favourite colour', 'answer': 'Blue', 'origin': None, 'superseded_at': NOW}],
    'runs': [], 'documents': [], 'emails': [], 'tasks': [], 'human_tasks': [],
}
PROFILE = {
    'fields': [{'key': 'full_name', 'label': 'Full name', 'group': 'Contact', 'value': 'Alex Morgan', 'state': 'confirmed', 'source': 'You'}],
    'answers': [
        {'id': 'm1', 'question': 'favourite colour', 'answer': 'Green', 'reusable': 0, 'scope': None, 'origin': 'extension'},
        {'id': 'm2', 'question': 'Notice period', 'answer': 'One month', 'reusable': 1, 'scope': 'country', 'origin': None},
    ],
}


async def eventually(condition, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('condition not met in time')


def variant(name):
    s = copy.deepcopy(BASE)
    chrome = s['browsers']['chrome']
    if name == 'prepared':
        chrome['extension_path'] = PATH
    elif name == 'detected':
        s.update(state='detected')
        chrome.update(state='detected', extension_path=PATH, seen_unpaired_at=NOW, seen_version='1.1.0')
    elif name == 'pairing':
        s.update(state='pairing')
        chrome.update(state='detected', extension_path=PATH, seen_unpaired_at=NOW, seen_version='1.1.0')
        expires = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time() + 300))
        s['pending_pairing'] = {'id': 'p1', 'browser': 'chrome', 'code': '482913', 'extension_version': '1.1.0',
                                'requested_at': NOW, 'expires_at': expires}
    elif name == 'pairing_unrecognised':
        s = variant('pairing')
        s['pending_pairing']['recognized'] = False
    elif name == 'ready':
        s.update(state='ready', clients=[CLIENT])
        chrome.update(state='connected', extension_path=PATH)
    elif name == 'outdated':
        s.update(state='outdated', clients=[dict(CLIENT, version='1.0.0', outdated=True)])
        chrome.update(state='outdated', extension_path=PATH)
    elif name == 'bridge_error':
        s.update(state='bridge_error')
        s['bridge'] = {'state': 'port_in_use', 'port': 47615, 'candidate_ports': [47615, 47616, 47617],
                       'conflict_process': 'node', 'message': 'Every candidate port is busy.'}
    elif name == 'bridge_outside':
        s = variant('not_installed')
        s['bridge'] = dict(s['bridge'], port=50123, candidate_ports=[50123])
    elif name == 'disabled':
        s.update(state='disabled', settings=dict(SETTINGS, enabled=False))
    elif name == 'no_model':
        s = variant('ready')
        s['drafts'] = dict(s['drafts'], configured=False, message='No answer model is configured.')
    elif name == 'limit_reached':
        s = variant('ready')
        s['drafts'] = dict(s['drafts'], spent_usd=10.0)
    return s


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1432')
    parser.add_argument('--browser', default='chrome', choices=['chrome', 'webkit'])
    parser.add_argument('--out', default='')
    parser.add_argument('--live', action='store_true', help='run against its own real backend instead of the mocked API')
    args = parser.parse_args()
    if args.live:
        import extension_live
        return await extension_live.run(args)
    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / 'meridian-ui-qa'
    out = out / f'extension-{args.browser}'
    out.mkdir(parents=True, exist_ok=True)
    errors, requests = [], []
    holder = {'state': variant('not_installed'), 'restart': 'blocked'}
    writes = []

    def ignorable(text):
        # WebKit reports requests cancelled by page.reload() as errors; Chrome logs mocked 5xx responses.
        return 'access control checks' in text or 'Failed to load resource' in text

    async with async_playwright() as p:
        browser = await (p.chromium.launch(channel='chrome', headless=True) if args.browser == 'chrome' else p.webkit.launch())
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='en-GB')
        if args.browser == 'chrome':
            await context.grant_permissions(['clipboard-read', 'clipboard-write'])
        page = await context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)) if not ignorable(str(e)) else None)
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and not ignorable(m.text) else None)

        async def api(route):
            req = route.request
            path = urlparse(req.url).path
            result = {}
            if path == '/api/extension':
                requests.append(time.time())
                result = holder['state']
            elif path.startswith('/api/extension/pairings/') and req.method == 'POST':
                writes.append((req.method, path))
                if holder.get('expired'):  # the request expired (or was decided in the native dialog) before the click
                    holder['state'] = variant('detected')
                    return await route.fulfill(status=410, json={'detail': 'This connection request has expired', 'code': 'expired'})
                holder['state'] = variant('ready') if path.endswith('/approve') else variant('detected')
                result = holder['state']
            elif path.startswith('/api/extension/clients/') and req.method == 'DELETE':
                writes.append((req.method, path))
                holder['state'] = variant('detected')
            elif path == '/api/extension/settings' and req.method == 'PATCH':
                body = req.post_data_json
                writes.append((req.method, path, body))
                holder['state']['settings'].update(body)
                if body.get('enabled') is True and holder['state']['state'] == 'disabled':
                    holder['state'] = variant('not_installed')
                result = holder['state']
            elif path.startswith('/api/extension/install/'):
                body = req.post_data_json
                writes.append((req.method, path, body))
                if body['action'] == 'install':  # the real backend runs the steps in this order and reports each
                    holder['state']['browsers']['chrome']['extension_path'] = PATH
                    names = ['prepare', 'open_browser_page'] if body.get('update') else ['prepare', 'copy_path', 'reveal', 'open_browser_page']
                    steps = []
                    for name in names:
                        if name == 'open_browser_page' and not holder['state']['browsers']['chrome']['browser_installed']:
                            steps.append({'action': name, 'ok': True, 'skipped': True, 'message': 'Google Chrome was not found.'})
                        elif name == 'open_browser_page' and holder.get('open_fails'):
                            steps.append({'action': name, 'ok': False, 'message': 'Chrome is not installed.'})
                        else:
                            steps.append({'action': name, 'ok': True, 'message': 'Done.'})
                    failed = next((step for step in steps if not step['ok']), None)
                    result = {'ok': failed is None, 'message': failed['message'] if failed else 'Meridian did it all.', 'path': PATH, 'steps': steps}
                elif body['action'] == 'prepare':
                    holder['state']['browsers']['chrome']['extension_path'] = PATH
                    result = {'ok': True, 'message': 'The extension folder is ready.', 'path': PATH}
                elif body['action'] == 'reveal':
                    result = {'ok': True, 'message': 'Opened the extension folder in Finder.', 'path': PATH}
                elif body['action'] == 'open_browser_page':
                    result = ({'ok': False, 'message': 'Chrome is not installed.'} if holder.get('open_fails')
                              else {'ok': True, 'message': 'Opened the extensions page in Chrome.'})
                else:
                    result = {'ok': True, 'message': 'Folder path copied.', 'path': PATH}
            elif path == '/api/extension/bridge/restart':
                writes.append((req.method, path))
                if holder['restart'] == 'ok':
                    holder['state'] = variant('not_installed')
                result = holder['state']
            elif path == '/api/extension/activity':
                result = {'items': ACTIVITY, 'total': len(ACTIVITY)}
            elif path == '/api/onboarding':
                ids = ['documents', 'profile', 'search_profile', 'browser_account', 'email', 'ai', 'boundaries', 'search_run', 'extension']
                result = {'hidden': False, 'completed': 0, 'total': 9, 'steps': [{'id': i, 'status': 'pending', 'detail': 'x', 'evidence': {}} for i in ids]}
            elif path == '/api/daily':
                result = {'items': [JOB], 'total': 1, 'new_today': 1, 'applied': 0, 'saved': 0, 'date': '2026-10-13', 'sources': [], 'email': [], 'region': 'any'}
            elif path == '/api/tracker':
                result = {'items': [
                    dict(JOB, id='j1', application_id='a1', application_status='APPLIED', submitted=True, applied_at=NOW,
                         assistant=APPLICATION['assistant']),
                    dict(JOB, id='j2', title='Data Engineer', application_id=None),
                ], 'total': 2, 'sources': ['fixture']}
            elif path == '/api/applications/a1':
                result = APPLICATION
            elif path == '/api/application-profile':
                result = PROFILE
            elif path == '/api/activity':
                result = {'items': [{'id': 'x1', 'created_at': NOW, 'message': 'Application recorded.', 'company': 'Example Co',
                                     'title': 'Software Engineer', 'origin': 'extension'}], 'total': 1}
            elif path == '/api/extension/jobs/j1/open':
                writes.append((req.method, path))
                result = {'ok': True, 'browser': 'chrome'}
            elif path in ('/api/human-tasks', '/api/integrations'):
                result = {'items': [], 'total': 0}
            elif path == '/api/health':
                result = {'status': 'ok', 'version': '0.1.0', 'data_dir': '/tmp/qa'}
            await route.fulfill(status=200, json=result)

        await page.route('**/api/**', api)
        nav = page.get_by_role('navigation')
        settings_button = nav.get_by_role('button', name='Settings', exact=True)
        hero = page.locator('.ext-hero')

        async def open_panel():
            if page.viewport_size['width'] <= 960:  # the sidebar becomes a drawer
                await page.get_by_role('button', name='Open navigation').click()
            await settings_button.click()
            await page.get_by_role('button', name='Browser assistant', exact=True).click()

        async def toggle_theme():
            narrow = page.viewport_size['width'] <= 960
            if narrow:
                await page.get_by_role('button', name='Open navigation').click()
            await page.get_by_role('button', name='Toggle appearance').click()
            if narrow:
                await page.get_by_role('button', name='Close navigation').click()
            await page.wait_for_timeout(400)  # .button has a 0.15 s background transition

        async def load(name):
            holder['state'] = variant(name)
            await page.reload()
            await open_panel()
            await expect(hero.get_by_role('heading')).to_be_visible()

        async def show(name, headline, tag, shot=True):
            await load(name)
            await expect(hero.get_by_role('heading')).to_have_text(headline)
            await expect(hero.locator('.tag')).to_have_text(tag)
            await expect(page.locator('.ext-banner')).to_be_hidden()  # the panel already shows the request
            if shot:
                await page.screenshot(path=str(out / f'{name}.png'), full_page=True)

        await page.goto(args.base_url)

        print('State copy (7 headline states)', flush=True)
        await show('not_installed', 'Fill applications in your own browser', 'Not set up')
        await show('detected', 'Almost there: connect the extension', 'Not connected')
        await show('pairing', 'Chrome wants to connect', 'Waiting for you')
        await show('ready', 'Assistant ready in Chrome', 'Connected')
        await show('outdated', 'Update the assistant in Chrome', 'Update needed')
        await show('bridge_error', 'Meridian cannot open its browser connection', 'Needs attention')
        await show('disabled', 'The browser assistant is turned off', 'Turned off')

        print('Guided Chrome install: ONE click does prepare, copy path, reveal and open; three steps are left; live detection', flush=True)
        await load('not_installed')
        chrome_card = page.locator('.ext-browser').filter(has_text='Google Chrome')
        safari_card = page.locator('.ext-browser').filter(has_text='Safari').last
        await expect(chrome_card.locator('.ext-steps > li')).to_have_count(3)
        await expect(safari_card.locator('.ext-steps > li')).to_have_count(3)
        await expect(chrome_card.locator('.ext-steps > li').first).to_have_attribute('aria-current', 'step')
        await expect(chrome_card.get_by_role('button', name='Install in Chrome')).to_be_visible()
        for gone in ('Prepare folder', 'Reveal folder', 'Copy folder path', 'Open extensions page'):  # no separate chores before the click
            await expect(chrome_card.get_by_role('button', name=gone)).to_have_count(0)
        await expect(chrome_card).to_contain_text('Turn on Developer mode')  # honest about the one thing Meridian cannot do for you
        await expect(chrome_card).to_contain_text('does not come from the Chrome Web Store')
        await expect(chrome_card).to_contain_text('press ⌘⇧G, paste the folder path (⌘V)')
        await expect(page.get_by_text('Waiting for the extension to appear…')).to_have_count(0)
        writes.clear()
        await chrome_card.get_by_role('button', name='Install in Chrome').click()
        await expect(chrome_card.get_by_label('Extension folder path')).to_have_text(PATH)

        def installs():
            return [w[2] for w in writes if len(w) == 3 and w[1] == '/api/extension/install/chrome']
        await eventually(lambda: len(installs()) >= 1)
        # ONE request does the four steps (the backend runs them in order and copies the path itself): no chain of calls from the page
        assert installs() == [{'action': 'install'}], installs()
        await expect(page.get_by_text("The folder path is copied and Chrome's extensions page is open. Three steps are left in Chrome.")).to_be_visible()
        await expect(page.get_by_text('Waiting for the extension to appear…')).to_be_visible()
        # the fallbacks stay one click away once the folder is known
        for name in ('Copy path again', 'Show in Finder', 'Open extensions page'):
            await expect(chrome_card.get_by_role('button', name=name)).to_be_visible()
        before = len(writes)
        await chrome_card.get_by_role('button', name='Show in Finder').click()
        await eventually(lambda: len(writes) > before and writes[-1][2] == {'action': 'reveal'})
        await expect(page.get_by_text('Opened the extension folder in Finder.')).to_be_visible()
        await chrome_card.get_by_role('button', name='Open extensions page').click()
        await eventually(lambda: writes[-1][2] == {'action': 'open_browser_page'})
        await chrome_card.get_by_role('button', name='Copy path again').click()
        if args.browser == 'chrome':
            await page.wait_for_timeout(300)
            assert await page.evaluate('navigator.clipboard.readText()') == PATH
            await expect(page.get_by_text('Folder path copied. In Chrome')).to_be_visible()
        else:  # WebKit may accept the write itself or refuse it without a prompt, in which case the backend (pbcopy) does it
            await expect(page.get_by_text('Folder path copied.').first).to_be_visible()
        # A webview that refuses clipboard access falls back to the backend (pbcopy) instead of failing silently.
        await page.evaluate("Object.defineProperty(navigator.clipboard, 'writeText', {value: () => Promise.reject(new Error('denied')), configurable: true})")
        await chrome_card.get_by_role('button', name='Copy path again').click()
        await eventually(lambda: ('POST', '/api/extension/install/chrome', {'action': 'copy_path'}) in writes)
        # ...while the one-click install never depends on the webview's clipboard: it is still ONE request
        writes.clear()
        await chrome_card.get_by_role('button', name='Install in Chrome').click()
        await eventually(lambda: len(installs()) >= 1)
        assert installs() == [{'action': 'install'}], installs()
        # Chrome missing or failing to open: the click says so instead of announcing success
        holder['open_fails'] = True
        await chrome_card.get_by_role('button', name='Install in Chrome').click()
        await expect(page.get_by_text('Chrome is not installed.').first).to_be_visible()
        await expect(page.get_by_text("Three steps are left in Chrome.")).to_have_count(0)
        holder['open_fails'] = False
        await page.screenshot(path=str(out / 'install-guided.png'), full_page=True)
        # Chrome itself not found (Brave, Edge and other Chromium browsers load the same folder): the button still works, minus opening Chrome
        holder['state'] = variant('not_installed')
        holder['state']['browsers']['chrome']['browser_installed'] = False
        await page.reload()
        await open_panel()
        await expect(chrome_card.get_by_text('Google Chrome was not found in Applications.')).to_be_visible()
        await expect(chrome_card.get_by_role('button', name='Install in Chrome')).to_be_enabled()
        writes.clear()
        await chrome_card.get_by_role('button', name='Install in Chrome').click()
        await expect(page.get_by_text("The folder path is copied and the folder is shown in Finder. Open your browser's extensions page")).to_be_visible()
        await eventually(lambda: len(installs()) >= 1)
        assert installs() == [{'action': 'install'}], installs()  # the backend skips the browser page itself and says so
        holder['state'] = variant('not_installed')
        await page.reload()
        await open_panel()
        # a fresh page is not watching for the extension yet (idle polling is slow): the click starts the fast watch again
        await chrome_card.get_by_role('button', name='Install in Chrome').click()
        await expect(page.get_by_text('Waiting for the extension to appear…')).to_be_visible()
        holder['state'] = variant('detected')  # the user loaded the extension in Chrome
        await expect(hero.get_by_role('heading')).to_have_text('Almost there: connect the extension', timeout=8000)
        await expect(chrome_card.locator('.ext-found')).to_contain_text('Found the extension in Chrome. One step left.')
        await expect(chrome_card.locator('.ext-found')).to_contain_text('type the code it shows into the Meridian window that opens, or approve the request here')
        await expect(hero).to_contain_text('The extension shows a 6-digit code: type it into the Meridian window that opens, or approve the request here.')
        await expect(chrome_card.locator('.ext-steps')).to_be_hidden()  # collapsed behind "Show the install steps"
        await chrome_card.get_by_text('Show the install steps').click()
        await expect(chrome_card.locator('.ext-steps > li[data-done="true"]')).to_have_count(2)
        await expect(page.locator('#assistant-hint')).to_have_text('Browser assistant: extension found, connect it in Settings.')

        print('Safari steps: per-site access, what Meridian cannot see, and the unsigned-extensions toggle only as a fallback', flush=True)
        holder['state'] = variant('not_installed')
        await page.reload()
        await open_panel()
        safari_card = page.locator('.ext-browser').filter(has_text='Safari').last
        await expect(safari_card).to_contain_text('Safari > Settings > Extensions and tick Meridian Assistant')
        await expect(safari_card).to_contain_text('Edit Websites')
        await expect(safari_card).to_contain_text('Always Allow on Every Website')
        await expect(safari_card).to_contain_text('type the code it shows into the Meridian window, or approve the request here')
        await expect(safari_card).to_contain_text('Meridian cannot see Safari\'s settings')
        await expect(safari_card).to_contain_text('Applications folder')
        await expect(safari_card).to_contain_text('Quit Safari (⌘Q) and open it again.')
        await expect(safari_card.get_by_text('Allow Unsigned Extensions')).to_have_count(0)  # a notarised build never needs it
        await expect(safari_card.locator('.tag')).to_have_text('Not detected')  # Meridian cannot read Safari's switch
        holder['state']['browsers']['safari']['needs_unsigned_toggle'] = True
        await page.reload()
        await open_panel()
        safari_card = page.locator('.ext-browser').filter(has_text='Safari').last
        await expect(safari_card).to_contain_text('Settings > Extensions')
        await expect(safari_card.get_by_text('Still missing? This build is not notarised')).to_be_visible()
        await expect(safari_card.get_by_text('Develop > Allow Unsigned Extensions')).to_be_visible()
        await expect(safari_card.get_by_text('Show features for web developers')).to_be_visible()
        await expect(safari_card.locator('.ext-steps > li[data-done="true"]')).to_have_count(0)  # nothing is ticked off blindly
        await safari_card.get_by_role('button', name='Open Safari').click()
        await eventually(lambda: ('POST', '/api/extension/install/safari', {'action': 'open_browser_page'}) in writes)
        holder['state'] = variant('not_installed')  # the extension reached Meridian: only the first step is evidenced
        holder['state']['browsers']['safari'].update(state='detected', seen_unpaired_at=NOW, seen_version='1.1.0', needs_unsigned_toggle=True)
        await page.reload()
        await open_panel()
        safari_card = page.locator('.ext-browser').filter(has_text='Safari').last
        await expect(safari_card.locator('.ext-found')).to_contain_text('Found the extension in Safari. One step left.')
        await expect(safari_card.locator('.ext-found')).to_contain_text('View > Customize Toolbar')
        await safari_card.get_by_text('Show the install steps').click()
        await expect(safari_card.locator('.ext-steps > li[data-done="true"]')).to_have_count(1)
        await page.screenshot(path=str(out / 'safari-detected.png'), full_page=True)
        holder['state'] = variant('not_installed')
        holder['state']['browsers']['safari']['state'] = 'outdated'
        await page.reload()
        await open_panel()
        safari_card = page.locator('.ext-browser').filter(has_text='Safari').last
        await expect(safari_card).to_contain_text('Open the updated Meridian from your Applications folder')
        await expect(safari_card).to_contain_text('still allowed on your websites')
        holder['state'] = variant('not_installed')
        holder['state'] = variant('not_installed')
        holder['state']['browsers']['safari']['bundled'] = False
        await page.reload()
        await open_panel()
        await expect(page.get_by_text('This build of Meridian does not include the Safari extension.')).to_be_visible()

        print('Nav chip must not rename the Settings button, and must be described', flush=True)
        holder['state'] = variant('pairing')
        await page.reload()
        await expect(settings_button).to_have_count(1)
        await expect(settings_button).to_have_accessible_description('Browser assistant: a connection request is waiting for your approval.')
        banner = page.locator('.ext-banner')
        await expect(banner).to_contain_text('482 913')
        await page.screenshot(path=str(out / 'banner-on-dashboard.png'))

        print('Pairing card: alert text must stay constant while the countdown ticks', flush=True)
        await banner.get_by_role('button', name='Review request').click()
        await expect(page.locator('.ext-banner')).to_have_count(0)
        await expect(page.locator('.ext-pairing h3')).to_be_focused()  # a deep link lands on the request
        alert = page.get_by_role('alert').filter(has_text='wants to connect')
        first = await alert.inner_text()
        assert 'Pairing code 4 8 2 9 1 3' in first, first
        await page.wait_for_timeout(2600)
        assert await alert.inner_text() == first
        countdown = await page.locator('.ext-code-wrap small').first.inner_text()
        assert countdown.startswith('Expires in 4:'), countdown
        pairing_card = page.locator('.ext-pairing')
        await expect(pairing_card).to_contain_text('The extension is showing a 6-digit code')
        await expect(pairing_card).to_contain_text('Meridian also opens a small window that asks for the code')
        await expect(pairing_card).to_contain_text("Type the extension's code there to connect, or approve here once the two codes match")
        await expect(pairing_card).to_contain_text('three wrong codes deny the request')
        await expect(banner).to_have_count(0)

        print('Keyboard: Approve is not auto-focused; Enter approves; the headline takes focus afterwards', flush=True)
        assert await page.evaluate("(document.activeElement?.closest('button')?.textContent ?? '').includes('Approve connection')") is False
        approve = page.get_by_role('button', name='Approve connection')
        await approve.focus()
        await page.keyboard.press('Enter')
        await expect(hero.get_by_role('heading')).to_have_text('Assistant ready in Chrome')
        assert ('POST', '/api/extension/pairings/p1/approve') in writes, writes
        await expect(page.locator('.ext-hero h3')).to_be_focused()
        await expect(page.get_by_text('Chrome connected.')).to_be_visible()

        print('Unrecognised extension is flagged in red; Deny works and focuses the headline', flush=True)
        await load('pairing_unrecognised')
        await expect(page.locator('.ext-warning')).to_contain_text('Unrecognised extension')
        await page.screenshot(path=str(out / 'pairing-unrecognised.png'), full_page=True)
        await page.get_by_role('button', name='Deny', exact=True).click()
        await expect(hero.get_by_role('heading')).to_have_text('Almost there: connect the extension')
        assert ('POST', '/api/extension/pairings/p1/deny') in writes, writes
        await expect(page.locator('.ext-hero h3')).to_be_focused()

        print('An expired request: calm error, and the stale card disappears', flush=True)
        await load('pairing')
        holder['expired'] = True
        await page.get_by_role('button', name='Approve connection').click()
        await expect(page.get_by_role('alert').filter(has_text='This connection request has expired')).to_be_visible()
        await expect(page.locator('.ext-code')).to_have_count(0)
        await expect(hero.get_by_role('heading')).to_have_text('Almost there: connect the extension')
        holder['expired'] = False

        print('Keyboard order in the request: Approve, then Deny', flush=True)
        await load('pairing')
        await page.get_by_role('button', name='Approve connection').focus()
        # Safari/WebKit skips buttons with plain Tab unless Full Keyboard Access is on; Option+Tab is its documented way.
        await page.keyboard.press('Alt+Tab' if args.browser == 'webkit' else 'Tab')
        await expect(page.get_by_role('button', name='Deny', exact=True)).to_be_focused()

        print('Polling: one shared poller (2 s while pairing, idle otherwise)', flush=True)
        for name, low, high in (('pairing', 2, 5), ('ready', 0, 1)):
            await load(name)
            await page.wait_for_timeout(500)
            before = len(requests)
            await page.wait_for_timeout(7000)
            seen = len(requests) - before
            print(f'  {name}: {seen} GET /extension in 7 s', flush=True)
            assert low <= seen <= high, f'{name}: {seen} polls in 7 s, expected {low}-{high}'

        print('Ready: connected browsers, how-it-works, install guides tucked away', flush=True)
        await load('ready')
        await expect(page.get_by_text('Chrome on this Mac')).to_be_visible()
        await expect(page.get_by_text('96 fields filled on 12 pages')).to_be_visible()
        await expect(page.get_by_role('heading', name='How it works')).to_be_visible()
        keys = page.locator('.ext-keys')
        await expect(keys.locator('kbd', has_text='⌥ ⇧ M')).to_have_count(1)  # CPL-18: the panel shortcut and the start shortcut are listed
        await expect(keys.locator('kbd', has_text='⌘ ⇧ Y')).to_have_count(1)
        await expect(keys.locator('kbd', has_text='⌃ ⌥ 1 to 7')).to_have_count(1)  # CPL-18/UX-02: the chip's numbered actions (they type nothing)
        await expect(keys.locator('kbd', has_text='⌥ Z')).to_have_count(1)
        await expect(keys.locator('kbd', has_text='⌥ ⇧ Z')).to_have_count(1)  # Undo all
        await expect(keys.locator('kbd', has_text='⌥ R')).to_have_count(1)
        await expect(keys).to_contain_text('chrome://extensions/shortcuts')
        await expect(keys).to_contain_text('Alt + Shift + 1 to 7 on other systems')
        await expect(keys).to_contain_text('Safari has no such shortcut')
        await expect(page.get_by_text("Help lists the keys and colours")).to_be_visible()
        print('Privacy copy: says what leaves this Mac (SEC-13)', flush=True)
        privacy = page.locator('.ext-privacy')
        await expect(privacy).to_contain_text('Your profile and answers stay on this Mac.')
        await expect(privacy).to_contain_text('The extension itself talks only to Meridian on this Mac (127.0.0.1)')
        await expect(privacy).to_contain_text('what you type goes to Meridian only when you choose to save it')
        await expect(privacy).to_contain_text("Meridian itself, not the extension, reaches out to prepare answers: it reads public pages on the company's own website")
        await expect(privacy).to_contain_text('sending only the company name to a public search engine')
        await expect(privacy).to_contain_text('Settings > Browser assistant > Preparing answers')
        await expect(privacy).to_contain_text('The one part of your information that can leave this Mac is the text Meridian sends to the writing model you chose')
        await expect(privacy).to_contain_text('the question, the job description and the verified facts needed to answer')
        await expect(privacy).to_contain_text('A model that runs on this Mac sends nothing out.')
        await expect(privacy).to_contain_text('the one you chose for that application, else the one Meridian wrote for that job, else your default CV (Settings > Automation > Default CV)')
        for banned in ('Everything stays on this Mac', 'never to the internet', 'talks only to Meridian on this Mac and nothing else', 'if you allow it'):
            await expect(privacy).not_to_contain_text(banned)
        await page.screenshot(path=str(out / 'privacy-copy.png'), full_page=True)
        await expect(chrome_card.locator('summary')).to_have_text('Reinstall or update')
        await expect(page.locator('.ext-browser').filter(has_text='Safari').last.locator('summary')).to_have_text('Set up Safari too')
        await expect(page.get_by_text('Recent assistant activity')).to_be_visible()
        await expect(page.get_by_text('Assisted 14 fields on boards.greenhouse.io')).to_be_visible()
        await expect(page.get_by_text('Left a sensitive question blank')).to_be_visible()

        print('Outdated: one click refreshes the folder and opens the extensions page; the reload arrow is the one step left', flush=True)
        await load('outdated')
        await expect(chrome_card.get_by_role('button', name='Update in Chrome')).to_be_visible()
        await expect(chrome_card.get_by_text('Click the reload arrow on Meridian Assistant.')).to_be_visible()
        writes.clear()
        await chrome_card.get_by_role('button', name='Update in Chrome').click()
        await expect(page.get_by_text("The extension folder is refreshed and Chrome's extensions page is open.")).to_be_visible()
        assert [w[2] for w in writes if len(w) == 3 and w[1] == '/api/extension/install/chrome'] == [{'action': 'install', 'update': True}], writes
        await expect(page.get_by_text('Meridian 1.1.0 is newer than the extension 1.0.0.')).to_be_visible()
        await page.screenshot(path=str(out / 'outdated-steps.png'), full_page=True)
        # an update without Chrome (Brave, Edge): the folder is refreshed, the click says what is left instead of announcing an opened page
        holder['state'] = variant('outdated')
        holder['state']['browsers']['chrome']['browser_installed'] = False
        await page.reload()
        await open_panel()
        await chrome_card.get_by_role('button', name='Update in Chrome').click()
        await expect(page.get_by_text("The extension folder is refreshed. Open your browser's extensions page (chrome://extensions) yourself and reload Meridian Assistant.")).to_be_visible()
        await expect(page.get_by_text("Chrome's extensions page is open")).to_have_count(0)

        print('Bridge conflict: names the process, Try again restarts, still-blocked and recovered toasts', flush=True)
        await load('bridge_error')
        await expect(hero).to_contain_text('every port from 47615 to 47617 is taken (node is using 47615)')
        await expect(hero).to_contain_text('Quit the app that is using them, then try again.')
        await expect(hero.locator('.ext-detail')).to_have_count(0)  # the sentence above already says which ports were tried
        await hero.get_by_role('button', name='Try again').click()
        await expect(page.get_by_text('The browser connection is still blocked.')).to_be_visible()
        holder['restart'] = 'ok'
        await hero.get_by_role('button', name='Try again').click()
        await expect(page.get_by_text('The browser connection is back.')).to_be_visible()
        await expect(hero.get_by_role('heading')).to_have_text('Fill applications in your own browser')
        assert writes.count(('POST', '/api/extension/bridge/restart')) == 2, writes

        print('Connection help: what to do about a missing window, two answering programs and a custom port', flush=True)
        await load('detected')
        help_box = page.locator('details.ext-help')
        await expect(help_box).to_be_visible()
        assert await help_box.get_attribute('open') is None  # a quiet disclosure while the port is the one the extension tries
        await help_box.locator('summary').click()
        await expect(help_box).to_contain_text('No Meridian window asks for the code')
        await expect(help_box).to_contain_text('press Cancel in the extension, because something else on this Mac may be answering')
        await expect(help_box).to_contain_text('The extension says more than one program answers as Meridian')
        await expect(help_box).to_contain_text('the installed app and a development build')
        await expect(help_box).to_contain_text('Open with assistant always opens Google Chrome')
        await expect(help_box).to_contain_text('Meridian listens on this Mac only, on port 47615. The extension looks for it on ports 47615 to 47624.')
        await expect(help_box.get_by_role('alert')).to_have_count(0)
        await load('bridge_outside')
        help_box = page.locator('details.ext-help')
        assert await help_box.get_attribute('open') is not None  # the extension cannot find this bridge: shown open
        await expect(help_box.get_by_role('alert')).to_contain_text('listening on port 50123, which the extension does not look at')
        await page.screenshot(path=str(out / 'connection-help.png'), full_page=True)
        await load('ready')
        await expect(page.locator('details.ext-help summary')).to_have_text('If connecting does not work')

        print('Disabled: one click turns it back on', flush=True)
        await load('disabled')
        await hero.get_by_role('button', name='Turn it on').click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'enabled': True}) in writes)
        await expect(hero.get_by_role('heading')).to_have_text('Fill applications in your own browser')

        print('Settings: labelled controls, optimistic toggles, one narrow PATCH each', flush=True)
        await load('ready')
        for name, key in (
            ('Tab fills and moves to the next field', 'auto_advance'),
            ('Skip fields that already have a value', 'skip_filled'),
            ('Research the company and role in the background', 'background_research'),
            ('Prepare long answers in advance', 'background_drafts'),
            ('Show a discreet hint on known application sites', 'hint_on_ats'),
        ):
            box = page.get_by_role('checkbox', name=name)
            await expect(box).to_be_checked()
            await box.uncheck()
            await expect(box).not_to_be_checked()
            await eventually(lambda key=key: ('PATCH', '/api/extension/settings', {key: False}) in writes)
            await expect(box).to_be_enabled()  # disabled only while its own PATCH is in flight
        await expect(page.get_by_role('checkbox', name='Search the web for the company\'s official site')).to_be_disabled()  # research is off
        await page.get_by_role('checkbox', name='Research the company and role in the background').check()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'background_research': True}) in writes)
        await expect(page.get_by_role('checkbox', name='Search the web for the company\'s official site')).to_be_enabled()
        await page.get_by_role('checkbox', name='Search the web for the company\'s official site').uncheck()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'web_lookup': False}) in writes)
        await page.get_by_role('combobox', name='When you correct a suggestion').select_option('never')
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'save_corrections': 'never'}) in writes)
        await page.get_by_role('combobox', name='Language on websites').select_option('es')
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'language': 'es'}) in writes)
        await expect(page.get_by_role('combobox', name='Language on websites')).to_have_value('es')

        print('Settings: Tab for sensitive answers needs an explicit confirmation', flush=True)
        sensitive = page.get_by_role('checkbox', name='Let Tab insert sensitive answers')
        await expect(sensitive).not_to_be_checked()
        n = len(writes)
        await sensitive.click()  # opens the confirmation; the switch itself only flips after it
        dialog = page.get_by_role('dialog', name='Let Tab insert sensitive answers?')
        await expect(dialog).to_be_visible()
        await dialog.get_by_role('button', name='Cancel').click()
        await expect(sensitive).not_to_be_checked()
        assert len(writes) == n, 'cancel must not send a PATCH'
        await sensitive.click()  # opens the confirmation; the switch itself only flips after it
        await page.get_by_role('dialog', name='Let Tab insert sensitive answers?').get_by_role('button', name='Allow Tab').click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'tab_inserts_protected': True}) in writes)
        await expect(sensitive).to_be_checked()
        await sensitive.uncheck()  # turning it off needs no confirmation
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'tab_inserts_protected': False}) in writes)

        print('Settings: per-application draft cap validates and sends null to clear', flush=True)
        cap = page.get_by_role('textbox', name='Spending cap per application')
        await expect(cap).to_be_disabled()  # background drafts were switched off above
        await page.get_by_role('checkbox', name='Prepare long answers in advance').check()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'background_drafts': True}) in writes)
        await expect(cap).to_be_enabled()
        save = page.locator('.ext-cap').get_by_role('button', name='Save')
        await expect(save).to_be_disabled()
        await cap.fill('abc')
        await expect(page.get_by_text('Enter an amount above 0')).to_be_visible()
        await expect(save).to_be_disabled()
        await cap.fill('0.50')
        await expect(save).to_be_enabled()
        await save.click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'draft_budget_usd': 0.5}) in writes)
        await expect(cap).to_have_value('0.5')
        await cap.fill('')
        await save.click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'draft_budget_usd': None}) in writes)

        print('Settings: sites where the assistant starts automatically', flush=True)
        site = page.get_by_role('textbox', name='Start automatically on these sites')
        add = page.get_by_role('button', name='Add site')
        await site.fill('not a site')
        await add.click()
        await expect(page.get_by_text('Enter a website address such as careers.example.com.')).to_be_visible()
        await site.fill('https://www.Careers.Example.com/jobs?x=1')
        await add.click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'always_on_sites': ['careers.example.com']}) in writes)
        await expect(page.get_by_label('Sites where the assistant starts automatically').get_by_text('careers.example.com')).to_be_visible()
        await site.fill('careers.example.com')
        await add.click()
        await expect(page.get_by_text('careers.example.com is already in the list.')).to_be_visible()
        await page.get_by_role('button', name='Remove careers.example.com').click()
        await eventually(lambda: ('PATCH', '/api/extension/settings', {'always_on_sites': []}) in writes)
        await page.screenshot(path=str(out / 'settings.png'), full_page=True)

        print('Written answers: no model configured and monthly limit reached', flush=True)
        await load('no_model')
        await expect(page.get_by_text('Long answers need an answer model.')).to_be_visible()
        await page.get_by_role('button', name='Configure answer model').click()
        await expect(page.get_by_role('button', name='AI & providers', exact=True)).to_have_class('active')
        await load('limit_reached')
        await expect(page.get_by_text('Monthly AI limit reached.')).to_be_visible()
        await expect(page.get_by_role('progressbar', name='Monthly AI spending against your limit')).to_be_visible()

        print('Disconnect confirm, then the headline takes focus', flush=True)
        await load('ready')
        await page.get_by_role('button', name='Disconnect Chrome on this Mac').click()
        dialog = page.get_by_role('dialog', name='Disconnect Chrome?')
        await dialog.get_by_role('button', name='Disconnect', exact=True).click()
        await expect(hero.get_by_role('heading')).to_have_text('Almost there: connect the extension')
        assert ('DELETE', '/api/extension/clients/c1') in writes, writes
        await expect(page.locator('.ext-hero h3')).to_be_focused()

        print('Spanish guide is marked lang=es and follows the current state', flush=True)
        guide = page.locator('details.ext-guide')
        assert await guide.get_attribute('lang') == 'es'
        await guide.locator('summary').click()
        await expect(guide).to_contain_text('Casi listo: conecta la extensión')
        await expect(guide).to_contain_text('Pulsa Cargar descomprimida')
        await expect(guide).to_contain_text('Iniciar automáticamente en estos sitios')
        await page.screenshot(path=str(out / 'spanish-guide.png'), full_page=True)
        await expect(guide).to_contain_text('Si no se conecta')
        await expect(guide).to_contain_text('Editar sitios web')
        await expect(guide).to_contain_text('Meridian no puede ver los ajustes de Safari')
        # SEC-13 and CPL-18 in Spanish: what can leave this Mac, and the chord that types nothing
        await expect(guide).to_contain_text('Lo único de tu información que puede salir de este Mac es el texto que Meridian envía al modelo de redacción que elegiste')
        await expect(guide).to_contain_text('enviando solo el nombre de la empresa a un buscador público')
        await expect(guide).to_contain_text('Tu perfil y tus respuestas se quedan en este Mac.')
        await expect(guide).to_contain_text('⌃ ⌥ 1 a 7.')
        await expect(guide).to_contain_text('Settings > Automation > Default CV')
        for name, expected in (('pairing', 'Chrome quiere conectarse'), ('bridge_error', 'todos los puertos de 47615 a 47617 están ocupados'), ('outdated', 'Actualiza el asistente en Chrome'),
                               ('disabled', 'desactivado'), ('ready', 'Asistente listo en Chrome'), ('not_installed', 'Rellena candidaturas')):
            await load(name)
            await page.locator('details.ext-guide summary').click()
            await expect(page.locator('details.ext-guide')).to_contain_text(expected)

        print('A Spanish browser sees the Spanish guide already open; others see it closed', flush=True)
        spanish = await browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='es-ES')
        es_page = await spanish.new_page()
        await es_page.route('**/api/**', api)
        holder['state'] = variant('detected')
        await es_page.goto(args.base_url)
        await es_page.get_by_role('navigation').get_by_role('button', name='Settings', exact=True).click()
        await es_page.get_by_role('button', name='Browser assistant', exact=True).click()
        await expect(es_page.locator('details.ext-guide')).to_have_attribute('open', '')
        await expect(es_page.get_by_text('Casi listo: conecta la extensión')).to_be_visible()
        await spanish.close()
        await load('detected')
        assert await page.locator('details.ext-guide').get_attribute('open') is None

        print('Dark mode and narrow width', flush=True)
        await load('pairing')
        await toggle_theme()
        await page.screenshot(path=str(out / 'pairing-dark.png'), full_page=True)
        for name in ('ready', 'not_installed'):
            await load(name)
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / f'{name}-dark.png'), full_page=True)
        await page.set_viewport_size({'width': 760, 'height': 1000})
        for name in ('pairing', 'ready', 'bridge_error', 'not_installed'):
            await load(name)
            await page.wait_for_timeout(300)
            overflow = await page.evaluate('document.documentElement.scrollWidth - window.innerWidth')
            assert overflow <= 0, f'{name}: horizontal overflow {overflow}px at 760px'
            await page.screenshot(path=str(out / f'{name}-narrow-dark.png'), full_page=True)
        await toggle_theme()
        for name in ('pairing', 'ready', 'not_installed'):
            await load(name)
            await page.wait_for_timeout(300)
            overflow = await page.evaluate('document.documentElement.scrollWidth - window.innerWidth')
            assert overflow <= 0, f'{name}: horizontal overflow {overflow}px at 760px'
            await page.screenshot(path=str(out / f'{name}-narrow.png'), full_page=True)
        await page.set_viewport_size({'width': 1440, 'height': 1000})

        print('A failed poll keeps the pairing card on screen', flush=True)
        await load('pairing')

        async def fail(route):
            await route.fulfill(status=500, json={'detail': 'boom'})
        await page.route('**/api/extension', fail)
        await page.wait_for_timeout(2600)
        await expect(page.locator('.ext-code')).to_be_visible()
        await expect(page.get_by_text('Could not refresh the assistant status. Retrying…')).to_be_visible()
        await page.unroute('**/api/extension', fail)

        print('Today: setup callout only before connection; "Not now" hides it for 30 days; Open with assistant only when connected', flush=True)
        snooze_key = 'meridian-assistant-callout-snoozed-at'
        holder['state'] = variant('not_installed')
        await page.evaluate(f"localStorage.removeItem('{snooze_key}')")
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()
        await expect(page.get_by_role('button', name='Open with assistant')).to_have_count(0)
        await page.get_by_role('button', name='Set up the browser assistant').click()
        await expect(page.get_by_role('button', name='Browser assistant', exact=True)).to_have_class('active')
        await nav.get_by_role('button', name='Today', exact=True).click()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()  # opening the setup does not snooze it
        await page.get_by_role('button', name='Not now').click()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_have_count(0)
        await expect(page.get_by_role('button', name='Email status')).to_be_focused()  # the keyboard stays on the page
        stored = await page.evaluate(f"localStorage.getItem('{snooze_key}')")
        assert stored and abs(int(stored) - time.time() * 1000) < 60_000, stored
        await page.reload()
        await expect(page.get_by_role('button', name='Email status')).to_be_visible()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_have_count(0)  # still hidden after a reload
        await page.screenshot(path=str(out / 'today-snoozed.png'), full_page=True)
        day = 24 * 3600 * 1000
        await page.evaluate(f"localStorage.setItem('{snooze_key}', String(Date.now() - 29 * {day}))")
        await page.reload()
        await expect(page.get_by_role('button', name='Email status')).to_be_visible()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_have_count(0)  # 29 days: still put off
        await page.evaluate(f"localStorage.setItem('{snooze_key}', String(Date.now() - 31 * {day}))")
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()  # 31 days: it comes back
        await page.evaluate(f"localStorage.setItem('{snooze_key}', String(Date.now() + 40 * {day}))")
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()  # a date in the future is not trusted
        await page.evaluate(f"localStorage.setItem('{snooze_key}', 'not a number')")
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()
        await page.evaluate(f"localStorage.setItem('{snooze_key}', String(Date.now()))")
        holder['state'] = variant('pairing')  # a request waiting for the user is never hidden by an old "Not now"
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_be_visible()
        await expect(page.get_by_role('button', name='Not now')).to_have_count(0)
        holder['state'] = variant('ready')
        await page.reload()
        await expect(page.get_by_role('button', name='Set up the browser assistant')).to_have_count(0)
        await page.get_by_role('button', name='Open with assistant').click()
        await eventually(lambda: ('POST', '/api/extension/jobs/j1/open') in writes)
        await expect(page.get_by_text('Opened in Chrome. Click the Meridian icon to start.')).to_be_visible()
        await page.evaluate(f"localStorage.removeItem('{snooze_key}')")
        await page.screenshot(path=str(out / 'today-ready.png'), full_page=True)

        print('Tracker: assisted line, Open with assistant only on pending rows', flush=True)
        await nav.get_by_role('button', name='Tracker', exact=True).click()
        await expect(page.get_by_text('Assisted in Chrome · 14 fields')).to_be_visible()
        await expect(page.get_by_role('button', name='Open with assistant')).to_have_count(1)  # the applied row has none
        await page.screenshot(path=str(out / 'tracker.png'), full_page=True)

        print('Application detail: Assisted tag and Inserted by assistant origin', flush=True)
        await page.get_by_role('button', name='Timeline & notes').click()
        await expect(page.locator('.detail-hero .tag.green').filter(has_text='Assisted in Chrome')).to_have_count(1)
        await page.get_by_role('button', name='Answers (2)', exact=True).click()
        current = page.locator('.answer-list').first
        await expect(current.get_by_text('Inserted by assistant')).to_have_count(1)  # only the extension-origin answer
        await expect(current.locator('.answer-card').filter(has_text='Because of the mission').get_by_text('Inserted by assistant')).to_have_count(1)
        await expect(current.locator('.answer-card').filter(has_text='One month').get_by_text('Inserted by assistant')).to_have_count(0)
        await page.get_by_text('Previous answers (2)').click()  # a superseded answer keeps the label it had
        previous = page.locator('details').filter(has_text='Previous answers')
        await expect(previous.locator('.answer-card').filter(has_text='Two months').get_by_text('Inserted by assistant')).to_have_count(1)
        await expect(previous.locator('.answer-card').filter(has_text='Blue').get_by_text('Inserted by assistant')).to_have_count(0)
        await page.screenshot(path=str(out / 'application-detail.png'), full_page=True)
        await page.keyboard.press('Escape')

        print('Profile memory list: the answers the assistant saved carry the same tag', flush=True)
        await nav.get_by_role('button', name='Profile', exact=True).click()
        await page.get_by_text('Answers saved from applications (2)').click()
        memory = page.locator('details').filter(has_text='Answers saved from applications')
        await expect(memory.get_by_text('Inserted by assistant')).to_have_count(1)
        await expect(memory.locator('.simple-list > div').filter(has_text='favourite colour').get_by_text('Inserted by assistant')).to_have_count(1)
        await expect(memory.locator('.simple-list > div').filter(has_text='Notice period').get_by_text('Inserted by assistant')).to_have_count(0)
        await page.screenshot(path=str(out / 'profile-memory.png'), full_page=True)

        print('Activity page lists assistant activity once it is set up', flush=True)
        await nav.get_by_role('button', name='Activity', exact=True).click()
        await expect(page.get_by_role('heading', name='Browser assistant', exact=True)).to_be_visible()
        await expect(page.get_by_text('Assisted 14 fields on boards.greenhouse.io')).to_be_visible()
        await expect(page.get_by_text('Browser assistant', exact=False).first).to_be_visible()
        holder['state'] = variant('not_installed')
        await page.reload()
        await nav.get_by_role('button', name='Activity', exact=True).click()
        await expect(page.get_by_role('heading', name='Every step leaves a trace')).to_have_count(0)
        await expect(page.get_by_role('heading', name='Browser assistant', exact=True)).to_have_count(0)

        print('Onboarding grid has nine steps', flush=True)
        await nav.get_by_role('button', name='Today', exact=True).click()
        await settings_button.click()
        await page.get_by_role('button', name='General', exact=True).click()
        await page.get_by_role('button', name='Show guide', exact=True).click()
        await expect(page.locator('[data-step]')).to_have_count(9)
        await expect(page.locator('[data-step="extension"]')).to_contain_text('Install the browser assistant')
        await page.screenshot(path=str(out / 'onboarding-9.png'), full_page=True)
        await page.locator('[data-step="extension"]').click()
        await expect(hero.get_by_role('heading')).to_be_visible()

        assert not errors, errors

        print('Desktop shell: the window is raised once per pairing request, never on later polls', flush=True)
        shell = await browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='en-GB')
        desktop = await shell.new_page()
        await desktop.add_init_script('''window.__shown = [];
          window.__TAURI_INTERNALS__ = {invoke: async cmd => {
            if (cmd === 'connection_info') return {base_url: location.origin + '/api', token: 'synthetic-local-token'};
            if (cmd === 'show_review_window') { window.__shown.push(Date.now()); return null; }
            return null;
          }};''')
        await desktop.route('**/api/**', api)
        holder['state'] = variant('pairing')
        await desktop.goto(args.base_url)
        await desktop.wait_for_timeout(6500)  # several polls of the same request
        assert await desktop.evaluate('window.__shown.length') == 1, await desktop.evaluate('window.__shown.length')
        holder['state'] = variant('pairing')
        holder['state']['pending_pairing']['id'] = 'p2'
        await desktop.wait_for_timeout(2600)
        assert await desktop.evaluate('window.__shown.length') == 2
        await shell.close()

        assert not errors, errors
        await browser.close()
    print(json.dumps({'result': 'passed', 'browser': args.browser, 'screenshots': str(out)}))


if __name__ == '__main__':
    asyncio.run(main())
