"""Trusted browser edits in isolated CDP worlds; no credentials in page JS."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from pathlib import Path
import secrets

SCRIPT = Path(__file__).with_suffix('.js').read_text()


class ManualEdits:
    def __init__(self, observer, callback):
        self.observer, self.callback = observer, callback
        self.pages = {}
        self.records = {}
        self.revision = 0
        self.saved = {}
        self.tasks = set()

    async def watch(self, page):
        if page in self.pages:
            return
        cdp = await page.context.new_cdp_session(page)
        token = secrets.token_urlsafe(24)
        world = 'Meridian-edits-' + token
        contexts = set()
        state = {'cdp': cdp, 'token': token, 'contexts': contexts, 'world': world}
        self.pages[page] = state
        cdp.on('Runtime.executionContextCreated', lambda event: contexts.add(event['context']['id'])
               if event['context'].get('name') == world else None)
        cdp.on('Runtime.executionContextDestroyed', lambda event: contexts.discard(event['executionContextId']))
        cdp.on('Runtime.bindingCalled', lambda event: self._message(state, event))
        await cdp.send('Page.enable')
        await cdp.send('Runtime.enable')
        await cdp.send('Runtime.addBinding', {'name': 'meridianUserEdit', 'executionContextName': world})
        source = SCRIPT.replace('__DATA__', json.dumps({'token': token, 'binding': 'meridianUserEdit'})).replace('__OBSERVE__', self.observer)
        state['script'] = (await cdp.send('Page.addScriptToEvaluateOnNewDocument', {'source': source, 'worldName': world}))['identifier']
        tree = (await cdp.send('Page.getFrameTree'))['frameTree']

        async def attach(branch):
            try:
                result = await cdp.send('Page.createIsolatedWorld', {'frameId': branch['frame']['id'], 'worldName': world})
                contexts.add(result['executionContextId'])
                await cdp.send('Runtime.evaluate', {'contextId': result['executionContextId'], 'expression': source})
            except Exception as exc:
                # Third-party ATS frames can disappear between getFrameTree and
                # attachment. The registered new-document script watches their
                # replacements; an obsolete frame must not abort the whole form.
                if any(message in str(exc) for message in ('No frame for given id',
                    'Cannot find context with specified id', 'Execution context was destroyed')):
                    return
                raise
            for child in branch.get('childFrames', []):
                await attach(child)
        await attach(tree)

    def _message(self, state, event):
        if event.get('name') != 'meridianUserEdit' or event.get('executionContextId') not in state['contexts']:
            return
        try:
            record = json.loads(event['payload'])
            element = record['element']
            if (record.get('token') != state['token'] or not isinstance(element.get('manual_key'), str)
                    or element.get('type') in {'password', 'hidden'} or not isinstance(record.get('answer'), str)
                    or len(record['answer']) > 20000):
                return
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        record.pop('token', None)
        key = (record.get('frame_url'), element['manual_key'])
        previous = self.records.get(key, {})
        if not previous or any(record.get(k) != previous.get(k) for k in ('answer','pending','changed')):
            self.revision += 1
        record['revision'] = self.revision
        record['fact_ids'] = previous.get('fact_ids', []) if record['answer'] == previous.get('answer') else []
        record['save_error'] = previous.get('save_error', False)
        self.records[key] = record
        if record.get('commit') and self.saved.get(key) != record['answer']:
            self.saved[key] = record['answer']
            task = asyncio.create_task(self._save(key, record))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _save(self, key, record):
        try:
            result = self.callback(record) if self.callback else None
            if inspect.isawaitable(result):
                result = await result
            if self.records.get(key, {}).get('answer') == record['answer']:
                self.records[key]['fact_ids'] = [result] if result else []
                self.records[key]['save_error'] = False
        except Exception:
            if key in self.records:
                self.records[key]['save_error'] = True
            self.saved.pop(key, None)  # Keep the control protected and retry on flush.

    async def automate(self, page, keys):
        state = self.pages.get(page)
        if state:
            for context in tuple(state['contexts']):
                with contextlib.suppress(Exception):
                    await state['cdp'].send('Runtime.evaluate', {'contextId': context,
                        'expression': 'globalThis.__meridianEdits?.automate(' + json.dumps(keys) + ')'})

    def annotate(self, element):
        record = self.records.get((element.get('frame_url'), element.get('manual_key')))
        if record:
            element['user_edit'] = {k: record.get(k) for k in ('answer', 'revision', 'changed', 'pending', 'fact_ids', 'save_error')}

    async def flush(self):
        for state in self.pages.values():
            for context in tuple(state['contexts']):
                with contextlib.suppress(Exception):
                    await state['cdp'].send('Runtime.evaluate', {'contextId': context,
                        'expression': 'globalThis.__meridianEdits?.flush()'})
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks))

    async def close(self):
        await self.flush()
        for state in self.pages.values():
            for context in tuple(state['contexts']):
                with contextlib.suppress(Exception):
                    await state['cdp'].send('Runtime.evaluate', {'contextId': context,
                        'expression': 'globalThis.__meridianEdits?.remove()'})
            with contextlib.suppress(Exception):
                await state['cdp'].send('Page.removeScriptToEvaluateOnNewDocument', {'identifier': state['script']})
            with contextlib.suppress(Exception):
                await state['cdp'].detach()
        self.pages.clear()
