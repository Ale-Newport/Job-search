import asyncio
import json
from types import SimpleNamespace

import pytest

from jobagent.core import prepare_application
from jobagent.db import Database, now, uid
from jobagent.discovery import ingest_job
from jobagent.orchestrator import Orchestrator


class ControlledBrowser:
    def __init__(self, *_):
        self._active_application_id = None
        self.page = None
        self._paused = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.result = {'status': 'needs_review', 'answers': [], 'questions': [], 'steps': [], 'snapshot_id': 'form'}
        self.approvals_cleared = False

    async def fill_application(self, application, *_):
        self._active_application_id = application['id']
        self.page = SimpleNamespace(is_closed=lambda: False)
        self.entered.set()
        await self.release.wait()
        return dict(self.result)

    async def submit(self, *_args, **_kwargs):
        self.entered.set()
        await self.release.wait()
        return dict(self.result)

    def _invalidate_approvals(self):
        self.approvals_cleared = True

    def pause(self):
        self._paused = True

    async def resume(self):
        self._paused = False

    async def close(self):
        self.page = None

    def status(self):
        return {'running': self.page is not None}


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    import jobagent.automation
    import jobagent.automation.job_context

    async def context(application):
        return application

    monkeypatch.setattr(jobagent.automation, 'BrowserManager', ControlledBrowser)
    monkeypatch.setattr(jobagent.automation.job_context, 'prepare_job_context', context)
    db = Database(tmp_path / 'database' / 'meridian.sqlite3')
    db.execute("INSERT INTO settings VALUES('automation_paused','false')")
    job, _ = ingest_job(db, {'title': 'Engineer', 'company': 'Example', 'url': 'https://example.test/job'})
    app = prepare_application(db, job['id'], 'review', data_dir=tmp_path)
    return db, app['id'], tmp_path


def insert_run(db, application_id, status, checkpoint=None):
    run_id = uid()
    db.execute('INSERT INTO automation_runs(id,application_id,status,checkpoint,created_at,updated_at) VALUES(?,?,?,?,?,?)',
               (run_id, application_id, status, json.dumps(checkpoint or {}), now(), now()))
    return run_id


def get_run(db, run_id):
    return db.one('SELECT * FROM automation_runs WHERE id=?', (run_id,))


@pytest.mark.asyncio
@pytest.mark.parametrize('state,expected', [
    ('running', 'error'), ('submitting', 'unconfirmed'), ('needs_review', 'error'),
    ('browser_question', 'error'), ('section_review', 'error'), ('interrupted', 'error'),
])
async def test_restart_recovers_preparation_but_keeps_submission_uncertain(recovery, state, expected):
    db, application_id, path = recovery
    run_id = insert_run(db, application_id, state, {'snapshot_id': 'expired'})
    orchestrator = Orchestrator(db, path)
    run = get_run(db, run_id)
    assert run['status'] == expected
    assert json.loads(run['checkpoint'])['snapshot_id'] is None
    if expected == 'unconfirmed':
        with pytest.raises(ValueError, match='uncertain'):
            orchestrator.launch(application_id)
    else:
        orchestrator.browser.release.set()
        retry = orchestrator.launch(application_id)
        await asyncio.gather(*orchestrator.tasks)
        assert get_run(db, retry['run_id'])['status'] == 'needs_review'


def test_restart_keeps_real_manual_task_without_browser_snapshot(recovery):
    db, application_id, path = recovery
    run_id = insert_run(db, application_id, 'human_required', {'questions': [{'question': 'Upload your transcript'}]})
    Orchestrator(db, path)
    assert get_run(db, run_id)['status'] == 'human_required'


@pytest.mark.parametrize('application_status,run_status,expected', [
    ('NEEDS_REVIEW', 'error', 'ERROR'), ('PREPARING', 'blocked', 'ERROR'),
    ('CONFIRMED', 'error', 'CONFIRMED'), ('REJECTED', 'error', 'REJECTED'),
])
def test_restart_repairs_legacy_failed_application_tracking_once(recovery, application_status, run_status, expected):
    db, application_id, path = recovery
    run_id = insert_run(db, application_id, run_status, {'status': run_status, 'error': 'Target page was closed', 'snapshot_id': None})
    db.event(application_id, application_status, 'Original tracking event', 'automation')
    Orchestrator(db, path)
    Orchestrator(db, path)
    assert db.one('SELECT status FROM applications WHERE id=?', (application_id,))['status'] == expected
    assert get_run(db, run_id)['status'] == run_status
    assert db.one("SELECT id FROM application_events WHERE message='Original tracking event'")
    repairs = db.one("SELECT COUNT(*) AS n FROM application_events WHERE message LIKE 'Recovered previous preparation failure:%'")['n']
    assert repairs == int(expected == 'ERROR')


def test_restart_does_not_replay_legacy_failure_before_newer_manual_review(recovery):
    db, application_id, path = recovery
    insert_run(db, application_id, 'error', {'error': 'Old failure'})
    insert_run(db, application_id, 'human_required', {'questions': [{'question': 'Upload transcript'}]})
    db.event(application_id, 'NEEDS_REVIEW', 'Current review', 'automation')
    Orchestrator(db, path)
    assert db.one('SELECT status FROM applications WHERE id=?', (application_id,))['status'] == 'NEEDS_REVIEW'


def test_legacy_interrupted_submission_is_not_automatically_retried(recovery):
    db, application_id, path = recovery
    run_id = insert_run(db, application_id, 'interrupted')
    db.event(application_id, 'APPLYING', 'Submit attempted', 'automation')
    orchestrator = Orchestrator(db, path)
    assert get_run(db, run_id)['status'] == 'interrupted'
    with pytest.raises(ValueError, match='uncertain'):
        orchestrator.launch(application_id)


@pytest.mark.asyncio
async def test_preparation_timeout_releases_busy_state_and_allows_retry(recovery):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    orchestrator.preparation_timeout = .02
    run = orchestrator.launch(application_id)
    await asyncio.wait_for(asyncio.gather(*orchestrator.tasks), 2)
    assert get_run(db, run['run_id'])['status'] == 'error'
    assert 'time limit' in get_run(db, run['run_id'])['error']
    assert not orchestrator.running and not orchestrator.lock.locked()
    orchestrator.preparation_timeout = 2
    orchestrator.browser.release.set()
    retry = orchestrator.launch(application_id)
    await asyncio.gather(*orchestrator.tasks)
    assert get_run(db, retry['run_id'])['status'] == 'needs_review'


@pytest.mark.asyncio
@pytest.mark.parametrize('started', [False, True])
async def test_cancel_even_before_task_starts_never_strands_running_state(recovery, started):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    run = orchestrator.launch(application_id)
    if started:
        await orchestrator.browser.entered.wait()
    result = await orchestrator.cancel(application_id)
    assert result['status'] == 'error'
    assert get_run(db, run['run_id'])['status'] == 'error'
    assert not orchestrator.running and not orchestrator.lock.locked()
    orchestrator.browser.release.set()
    retry = orchestrator.launch(application_id)
    await asyncio.gather(*orchestrator.tasks)
    assert get_run(db, retry['run_id'])['status'] == 'needs_review'
    assert not orchestrator.browser._paused


@pytest.mark.asyncio
async def test_disconnect_aborts_active_fill_and_invalidates_idle_review(recovery):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    run = orchestrator.launch(application_id)
    await orchestrator.browser.entered.wait()
    orchestrator.browser.page = None
    orchestrator.browser.on_disconnect('Chrome closed.')
    await asyncio.gather(*orchestrator.tasks, return_exceptions=True)
    assert get_run(db, run['run_id'])['status'] == 'error'
    assert orchestrator.browser.approvals_cleared
    orchestrator.browser.release.set()
    retry = orchestrator.launch(application_id)
    await asyncio.gather(*orchestrator.tasks)
    assert get_run(db, retry['run_id'])['status'] == 'needs_review'
    orchestrator.reconcile_browser_state()
    assert get_run(db, retry['run_id'])['status'] == 'needs_review'  # Live review remains valid.
    orchestrator.browser.page = None
    orchestrator.reconcile_browser_state()
    assert get_run(db, retry['run_id'])['status'] == 'error'
    with pytest.raises(ValueError, match='Prepare and review'):
        await orchestrator.approve(application_id)


@pytest.mark.asyncio
async def test_returned_browser_failure_is_not_labeled_as_review(recovery):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    orchestrator.browser.result = {'status': 'error', 'error': 'Chrome could not start.'}
    orchestrator.browser.release.set()
    run = orchestrator.launch(application_id)
    await asyncio.gather(*orchestrator.tasks)
    assert get_run(db, run['run_id'])['error'] == 'Chrome could not start.'
    assert db.one('SELECT status FROM applications WHERE id=?', (application_id,))['status'] == 'ERROR'
    assert db.one("SELECT COUNT(*) AS n FROM application_events WHERE message='Form preparation paused for review'")['n'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_submission_timeout_and_cancel_preserve_uncertainty(recovery, monkeypatch, cancel):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    monkeypatch.setattr(orchestrator, 'rate_gate', lambda *_: None)
    orchestrator.submission_timeout = .02
    run_id = insert_run(db, application_id, 'needs_review')
    if cancel:
        orchestrator.submission_timeout = 2
        task = asyncio.create_task(orchestrator._submit(run_id, orchestrator.application(application_id), {}))
        await orchestrator.browser.entered.wait()
        assert (await orchestrator.cancel(application_id))['status'] == 'unconfirmed'
        assert task.cancelled()
    else:
        result = await orchestrator._submit(run_id, orchestrator.application(application_id), {})
        assert result['status'] == 'unconfirmed'
        assert 'time limit' in result['error']
    assert get_run(db, run_id)['status'] == 'unconfirmed'
    with pytest.raises(ValueError, match='uncertain'):
        orchestrator.launch(application_id)
    assert orchestrator.active_run is None
    assert json.loads(get_run(db, run_id)['checkpoint'])['status'] == 'unconfirmed'


@pytest.mark.asyncio
async def test_close_during_preparation_does_not_leave_running_run(recovery):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    run = orchestrator.launch(application_id)
    await orchestrator.close()
    assert get_run(db, run['run_id'])['status'] == 'error'
    assert not orchestrator.running


@pytest.mark.asyncio
async def test_late_result_preserves_email_outcome_and_blocks_submission(recovery, monkeypatch):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    run = orchestrator.launch(application_id)
    await orchestrator.browser.entered.wait()
    db.event(application_id, 'REJECTED', 'Employer rejection received', 'email')
    orchestrator.browser.release.set()
    await asyncio.gather(*orchestrator.tasks)
    assert db.one('SELECT status FROM applications WHERE id=?', (application_id,))['status'] == 'REJECTED'
    monkeypatch.setattr(orchestrator, 'rate_gate', lambda *_: None)
    with pytest.raises(ValueError, match='advanced or closed'):
        await orchestrator._submit(run['run_id'], orchestrator.application(application_id), {})


@pytest.mark.asyncio
async def test_switching_applications_expires_old_review(recovery):
    db, application_id, path = recovery
    orchestrator = Orchestrator(db, path)
    orchestrator.browser.release.set()
    first = orchestrator.launch(application_id)
    await asyncio.gather(*orchestrator.tasks)
    job, _ = ingest_job(db, {'title': 'Engineer', 'company': 'Second', 'url': 'https://example.test/second'})
    second_application = prepare_application(db, job['id'], 'review', data_dir=path)
    second = orchestrator.launch(second_application['id'])
    await asyncio.gather(*orchestrator.tasks)
    assert get_run(db, first['run_id'])['status'] == 'error'
    assert get_run(db, second['run_id'])['status'] == 'needs_review'
    with pytest.raises(ValueError, match='Prepare and review'):
        await orchestrator.approve(application_id)


@pytest.mark.asyncio
async def test_runner_browser_start_failure_is_recoverable_error(tmp_path, monkeypatch):
    from jobagent.automation.runner import BrowserManager
    from jobagent.automation.types import HumanRequired

    browser = BrowserManager(tmp_path)

    async def unavailable(_url):
        raise HumanRequired('Chrome could not start.')

    monkeypatch.setattr(browser, 'open', unavailable)
    result = await browser.fill_application({'id': 'app', 'url': 'https://example.test/job'}, [], [])
    assert result['status'] == 'error'
    assert result['snapshot_id'] is None
    assert result['error'] == 'Chrome could not start.'
