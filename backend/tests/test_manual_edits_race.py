import pytest

from jobagent.automation.manual_edits import ManualEdits


class Session:
    def __init__(self, failure):
        self.failure = failure
        self.attached = []

    def on(self, *_):
        pass

    async def send(self, method, args=None):
        if method == 'Page.addScriptToEvaluateOnNewDocument':
            return {'identifier': 'watch-replacements'}
        if method == 'Page.getFrameTree':
            return {'frameTree': {'frame': {'id': 'main'}, 'childFrames': [{'frame': {'id': 'retired'}}]}}
        if method == 'Page.createIsolatedWorld':
            if args['frameId'] == 'retired':
                raise RuntimeError(self.failure)
            self.attached.append(args['frameId'])
            return {'executionContextId': 1}
        return {}


class Page:
    def __init__(self, session):
        self.context = self
        self.session = session

    async def new_cdp_session(self, _):
        return self.session


async def test_retired_child_frame_does_not_disable_manual_edit_watcher():
    session = Session('Protocol error (Page.createIsolatedWorld): No frame for given id found')
    page = Page(session)
    edits = ManualEdits('()=>({})', None)
    await edits.watch(page)
    assert session.attached == ['main']
    assert edits.pages[page]['script'] == 'watch-replacements'
    assert edits.pages[page]['contexts'] == {1}


async def test_unrelated_cdp_failure_is_not_concealed():
    edits = ManualEdits('()=>({})', None)
    with pytest.raises(RuntimeError, match='Connection lost'):
        await edits.watch(Page(Session('Connection lost')))


async def test_close_releases_watches_when_a_retired_session_never_replies():
    import asyncio
    class RetiredSession:
        async def send(self, *_):
            await asyncio.Event().wait()
    edits = ManualEdits('()=>({})', None)
    edits.pages[object()] = {'cdp': RetiredSession(), 'contexts': {1}, 'script': 'retired'}
    await asyncio.wait_for(edits.close(), timeout=4)
    assert not edits.pages
