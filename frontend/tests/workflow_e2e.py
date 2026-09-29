"""Import → verify document → prepare → answer → email evidence UI integration.

Run ONLY against an isolated backend data directory and its Vite proxy.
The synthetic records remain in that QA directory. No external website is
opened and no recruitment application is submitted.

.venv/bin/python frontend/tests/workflow_e2e.py --base-url http://127.0.0.1:1420
"""
import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:1420')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    unique = str(int(time.time()))
    name = f'ui-workflow-cv-{unique}.txt'
    company = f'UI Workflow Company {unique}'
    title = f'Graduate Software Engineer {unique}'
    output = Path(tempfile.gettempdir()) / 'meridian-ui-qa'
    output.mkdir(exist_ok=True)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            await page.goto(base)
            print('Importing a synthetic CV through the UI', flush=True)
            await page.get_by_role('navigation').get_by_role('button', name='Documents', exact=True).click()
            await page.get_by_label('Import document', exact=True).set_input_files({'name': name, 'mimeType': 'text/plain', 'buffer': b'Name: Avery Example\nEmail: avery@example.test\nSkills: Python, TypeScript, React\nEducation: BSc Computer Science\n'})
            await page.get_by_role('heading', name='Import complete', exact=True).wait_for()
            await page.get_by_role('button', name=name, exact=True).click()
            doc_dialog = page.get_by_role('dialog', name='Document versions', exact=True)
            await doc_dialog.get_by_role('button', name='Approve for use', exact=True).click()
            await doc_dialog.get_by_text('Approved', exact=True).wait_for()
            async with page.expect_download() as received:
                await doc_dialog.get_by_role('button', name='Download', exact=True).click()
            assert (await received.value).suggested_filename == name
            await doc_dialog.get_by_role('button', name='Close', exact=True).click()
            headers = {'Authorization': 'Bearer meridian-development-token'}
            docs_response = await page.request.get(base + '/api/documents', headers=headers)
            docs = await docs_response.json()
            doc = next(d for d in docs['items'] if d['name'] == name)
            job_response = await page.request.post(base + '/api/jobs', headers=headers, data={'title': title, 'company': company, 'url': f'https://meridian-fixture.invalid/jobs/{unique}', 'description': 'Synthetic local UI test fixture. Graduate software engineer using Python and TypeScript.', 'skills': ['Python', 'TypeScript'], 'location': 'London', 'source': 'ui_test_fixture', 'ats': 'generic'})
            assert job_response.ok, await job_response.text()
            print('Opening matched job and preparing a tracked application', flush=True)
            await page.get_by_role('navigation').get_by_role('button', name='Jobs', exact=True).click()
            await page.get_by_role('button', name=f'{title} {company}', exact=False).first.click()
            await page.get_by_role('button', name='Prepare application', exact=True).click()
            prepare = page.get_by_role('dialog', name='Prepare this application', exact=True)
            await prepare.get_by_label('CV version').select_option(doc['latest_version_id'])
            await prepare.get_by_role('button', name='Prepare application', exact=True).click()
            application = page.get_by_role('dialog', name='Application workspace', exact=True)
            await application.get_by_role('button', name='Apply', exact=True).wait_for()
            await application.get_by_role('button', name='Answers', exact=True).click()
            await application.get_by_role('button', name='Add verified answer', exact=True).click()
            answer = page.get_by_role('dialog', name='Add a verified answer', exact=True)
            await answer.get_by_label('Question', exact=True).fill('What programming language do you use?')
            await answer.get_by_label('Your answer', exact=True).fill('Python')
            await answer.get_by_role('button', name='Verify & save answer', exact=True).click()
            await application.get_by_role('heading', name='What programming language do you use?', exact=True).wait_for()
            await application.get_by_role('button', name='Documents', exact=True).click()
            await application.get_by_text(name, exact=True).wait_for()
            await page.screenshot(path=str(output / 'application-documents.png'), full_page=True)
            await application.get_by_role('button', name='Close', exact=True).click()
            print('Importing application email evidence', flush=True)
            await page.get_by_role('navigation').get_by_role('button', name='Email', exact=True).click()
            eml = f'From: careers@workflow.example.test\r\nTo: avery@example.test\r\nSubject: Application confirmation — {company}\r\nMessage-ID: <ui-workflow-{unique}@example.test>\r\nDate: Tue, 29 Sep 2026 12:00:00 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nThank you for applying for {title} at {company}. We have received your application.\r\n'
            await page.get_by_label('Import .eml', exact=True).set_input_files({'name': f'confirmation-{unique}.eml', 'mimeType': 'message/rfc822', 'buffer': eml.encode()})
            await page.get_by_role('button', name=f'Application confirmation — {company}', exact=False).first.click()
            message = page.get_by_role('dialog', name='Email evidence', exact=True)
            await message.get_by_role('heading', name='Original email text', exact=True).wait_for()
            await page.screenshot(path=str(output / 'email-evidence.png'), full_page=True)
            await message.get_by_role('button', name='Close', exact=True).click()
            await page.get_by_role('navigation').get_by_role('button', name='Applications', exact=True).click()
            await page.screenshot(path=str(output / 'application-board.png'), full_page=True)
            print('Recording synthetic analytics milestones and inspecting cohort comparisons', flush=True)
            applications_response = await page.request.get(base + '/api/applications?q=' + unique, headers=headers)
            application_record = (await applications_response.json())['items'][0]
            # These are manual tracking events on a synthetic .invalid fixture only.
            for status in ['APPLIED', 'INTERVIEW']:
                event_response = await page.request.patch(base + '/api/applications/' + application_record['id'], headers=headers, data={'status': status, 'notes': 'Synthetic UI analytics fixture; no external submission occurred.'})
                assert event_response.ok, await event_response.text()
            await page.get_by_role('navigation').get_by_role('button', name='Analytics', exact=True).click()
            await page.get_by_role('heading', name='Time to first response', exact=True).wait_for()
            await page.get_by_role('heading', name='Observed application funnel', exact=True).wait_for()
            for dimension in ['source', 'company', 'role', 'cv', 'match']:
                await page.get_by_role('combobox', name='Group by', exact=True).select_option(dimension)
                await page.get_by_role('combobox', name='Outcome', exact=True).select_option('interview')
                await page.get_by_role('columnheader', name='Interview rate', exact=True).wait_for()
            await page.get_by_role('combobox', name='Group by', exact=True).select_option('cv')
            await page.screenshot(path=str(output / 'analytics-cohorts.png'), full_page=True)
            if errors:
                raise AssertionError('\n'.join(errors))
            print(json.dumps({'result': 'passed', 'checked': 'CV import, document approval, authenticated download, job matching detail, application preparation with exact CV, verified answer, email import and evidence, response timing and five analytics cohort views', 'screenshots': str(output)}), flush=True)
        except Exception:
            await page.screenshot(path=str(output / 'workflow-failure.png'), full_page=True)
            print((await page.locator('body').inner_text())[-6000:], flush=True)
            raise
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
