"""Private child entry point; JSON pipe commands are never sourced from websites."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import resource
import signal
import sys
from pathlib import Path

from .db import Database, dumps, now


async def serve(root: Path, task_id: str):
    db = Database(root / 'database' / 'meridian.sqlite3', migrate=False)
    task = db.one('SELECT * FROM automation_tasks WHERE id=?', (task_id,))
    if not task:
        return
    payload = json.loads(task['payload'])
    orchestrator = None
    operation = 'starting'
    last_version = None
    background = None

    def emit(message):
        print(dumps(message), flush=True)

    async def execute(command, args):
        nonlocal operation
        operation = command
        if command == 'fill':
            app = orchestrator.application(task['application_id'])
            from .documents import SUBMITTED_STATUSES
            if app['status'] in SUBMITTED_STATUSES - {'APPLYING'} or db.one("SELECT id FROM application_submissions WHERE application_id=? AND state IN ('SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED','CONFIRMED')", (app['id'],)):
                raise ValueError('Application already submitted or uncertain; reconcile before preparing')
            if not os.environ.get('MERIDIAN_TEST'):
                from .job_validity import verify_job
                if await verify_job(db, app) == 'CLOSED':
                    raise ValueError('The employer page reports this position closed')
            orchestrator.active_run = task['run_id']
            orchestrator.running = True
            await orchestrator._fill(task['run_id'], app, args.get('resume', False))
            return {'run_id': task['run_id']}
        if command == 'approve':
            return await orchestrator.approve(task['application_id'])
        if command == 'approve_section':
            result = await orchestrator.approve_section(task['application_id'], args['snapshot_id'])
            task['run_id'] = result['run_id']
            db.execute('UPDATE automation_tasks SET run_id=? WHERE id=?', (task['run_id'], task_id))
            return result
        if command == 'takeover':
            return await orchestrator.browser.takeover()
        if command == 'pause':
            orchestrator.browser.pause()
            if background and not background.done():
                background.cancel()
            db.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER',updated_at=? WHERE id=?", (now(), task_id))
            return {'paused': True}
        raise ValueError('Unknown internal worker command')

    async def heartbeat():
        nonlocal last_version
        while True:
            run = db.one('SELECT * FROM automation_runs WHERE id=?', (task['run_id'],)) if task['run_id'] else None
            state = 'WAITING_FOR_USER' if task['kind'] == 'browser' else 'RUNNING'
            changed = False
            url = None
            progress = None
            if task['kind'] == 'discovery':
                latest = db.one('SELECT max(updated_at) AS stamp FROM sources')
                progress = latest['stamp'] if latest else None
            if task['kind'] == 'mail':
                latest = db.one('SELECT count(*) AS n FROM email_messages')
                progress = latest['n'] if latest else 0
            if run:
                checkpoint = json.loads(run['checkpoint'])
                progress = run['updated_at']
                url = checkpoint.get('current_url')
                changed = progress != last_version
                last_version = progress
                if run['status'] in ('needs_review', 'ready_for_review', 'human_required', 'section_review', 'section_approved', 'browser_question', 'unconfirmed'):
                    state = 'WAITING_FOR_USER'
                elif run['status'] == 'confirmed':
                    state = 'COMPLETED'
                elif run['status'] in ('error', 'blocked'):
                    # Disconnection while preparing is recoverable, but unknown
                    # answers and explicit site restrictions require attention.
                    if orchestrator and (not orchestrator.browser.page or orchestrator.browser.page.is_closed()):
                        return  # parent recovers the exited worker
                    state = 'FAILED'
            current = db.one('SELECT state FROM automation_tasks WHERE id=?', (task_id,))
            if current and current['state'] in ('COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_FOR_USER'):
                state = current['state']
            if state in ('COMPLETED', 'FAILED'):
                db.execute('UPDATE automation_tasks SET state=?,updated_at=? WHERE id=?', (state, now(), task_id))
            memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
            emit({'type': 'heartbeat', 'state': state, 'operation': checkpoint.get('form_state', operation) if run else operation,
                  'progress': progress, 'url': url, 'memory_bytes': memory, 'checkpoint_changed': changed, 'run_id': task.get('run_id')})
            if state in ('COMPLETED', 'FAILED', 'CANCELLED'):
                return
            await asyncio.sleep(1)

    async def initial():
        nonlocal operation
        if task['kind'] == 'browser':
            from .automation.runner import BrowserManager
            browser = BrowserManager(root)
            await browser.open(payload['url'])
            db.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER',updated_at=? WHERE id=?", (now(), task_id))
            await asyncio.Future()  # parked child; parent has an independent idle watchdog
        if task['kind'] == 'application':
            return await execute('fill', payload)
        async with asyncio.timeout(1800 if task['kind']=='discovery' else 180):
            if task['kind'] == 'discovery':
                from .tracking import refresh_daily
                from .discovery import run_sources
                operation = 'scan_sources'
                result = await run_sources(db, root, payload['source_id']) if payload.get('source_id') else await refresh_daily(db, root)
            elif task['kind'] == 'mail':
                from .mail import MailService
                from .security import SecretStore
                operation = 'sync_mail'
                result = await MailService(db, SecretStore()).sync()
            elif task['kind'] == 'document':
                from .documents import generate_document
                operation = 'generate_document'
                result = await asyncio.to_thread(generate_document, db, root, **payload)
            else:
                raise ValueError('Unsupported worker type')
        db.execute("UPDATE automation_tasks SET state='COMPLETED',checkpoint=?,updated_at=? WHERE id=?", (dumps(result), now(), task_id))
        return result

    if task['kind'] == 'application':
        from .orchestrator import Orchestrator
        from .security import SecretStore
        from .text_ai import TextService
        orchestrator = Orchestrator(db, root, application_scope=task['application_id'], recover=False)
        orchestrator.browser.profile_path = root / 'browser-profile' / 'applications' / task['application_id']
        orchestrator.browser._headless = bool(payload.get('headless', False))
        orchestrator.text_service = TextService(db, SecretStore())
        # Reopen semantic checkpoint URL after process loss, never replay a submit.
        run = db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (task['run_id'],))
        checkpoint = json.loads(run['checkpoint']) if run else {}
        saved_url = checkpoint.get('current_url')
        if saved_url:
            original_application = orchestrator.application
            def application(identifier):
                app = original_application(identifier)
                from urllib.parse import urlparse
                if urlparse(saved_url).hostname == urlparse(app['url']).hostname:
                    app['url'] = app['application_url'] = saved_url
                return app
            orchestrator.application = application
        db.execute("UPDATE automation_runs SET status='running',updated_at=? WHERE id=?", (now(), task['run_id']))

    background = asyncio.create_task(initial())
    beat = asyncio.create_task(heartbeat())
    commands = set()

    async def answer(message):
        try:
            result = await execute(message['command'], message.get('payload', {}))
            emit({'id': message['id'], 'result': result})
        except Exception as exc:
            emit({'id': message['id'], 'error': str(exc)[:500]})

    async def read_commands():
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                message = json.loads(line)
                command = asyncio.create_task(answer(message))
                commands.add(command)
                command.add_done_callback(commands.discard)
            except ValueError:
                continue
        # The parent disappeared: stop our own group, including owned browser.
        return

    reader = asyncio.create_task(read_commands())
    try:
        while True:
            done, _ = await asyncio.wait((background, beat, reader), return_when=asyncio.FIRST_COMPLETED)
            if reader in done or beat in done:
                break
            if background in done:
                try:
                    background.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    db.execute("UPDATE automation_tasks SET state='FAILED',error=?,updated_at=? WHERE id=?", (type(exc).__name__, now(), task_id))
                # The form worker stays alive for user review and explicit approval.
                await asyncio.wait((beat, reader), return_when=asyncio.FIRST_COMPLETED)
                break
    finally:
        for pending in (background, beat, reader, *commands):
            pending.cancel()
        if orchestrator:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await orchestrator.close()
        # stdin uses a blocking thread; do not wait indefinitely for executor shutdown.


def main():
    if len(sys.argv) < 3:
        raise SystemExit('Private worker requires a data directory and task ID')
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(serve(Path(sys.argv[-2]), sys.argv[-1]))
    finally:
        loop.close()
    # The parent deliberately created a private session. Even if graceful browser
    # close timed out, no owned Chrome helpers should survive a worker exit.
    if os.getpgrp() == os.getpid():
        os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(0)


if __name__ == '__main__':
    main()
