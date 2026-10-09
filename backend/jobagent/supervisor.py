"""Local process supervision. Browser handles never cross the API boundary.

Queue claims and account leases are transactional. A worker may hang in native
code: the parent watches its pipe and terminates only its owned process group.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .db import decode_row, dumps, now, uid
from .orchestrator import Orchestrator, settings_for

ACTIVE = ('STARTING', 'RUNNING', 'WAITING_FOR_PAGE', 'CHECKPOINTING', 'RECOVERING')
WAITING = ('WAITING_FOR_USER',)
TERMINAL = ('COMPLETED', 'FAILED', 'CANCELLED')


@dataclass
class Worker:
    id: str
    task: dict
    process: object
    heartbeat: float = field(default_factory=time.monotonic)
    progress: float = field(default_factory=time.monotonic)
    state: str = 'STARTING'
    status: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    reader: object = None
    stderr: object = None
    stopping: bool = False
    memory_bytes: int = 0


class WorkerSupervisor(Orchestrator):
    @property
    def running(self):
        # A parked review still owns browser/database resources. Restore and
        # maintenance must see every live child, including non-browser workers.
        return bool(getattr(self, 'workers', {}))

    @running.setter
    def running(self, value):
        self._legacy_running = value

    def __init__(self, db, data_dir):
        super().__init__(db, data_dir, recover=False)
        self.workers: dict[str, Worker] = {}
        self._closing = False
        self.watchdog_seconds = 30
        self.progress_seconds = 240
        self.max_memory_bytes = 2 * 1024**3
        self._monitor = None
        self._loop = None
        self._memory_checked = 0.0

    async def start(self):
        # Only leases from vanished parents are recovered. Never trust a saved PID:
        # it may have been reused; orphan children exit when their input pipe closes.
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._recover_abandoned)
        self._monitor = asyncio.create_task(self._watch(), name='worker-supervisor')

    def _recover_abandoned(self):
        for task in self.db.query("SELECT * FROM automation_tasks WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED','QUEUED','RETRY_SCHEDULED')"):
            self._cleanup_orphan(task)
            self._recover(task, 'backend_restart')

    def _cleanup_orphan(self, task):
        # macOS has no Linux parent-death signal. After a hard backend crash a
        # stopped child cannot read EOF, so verify its exact private task arguments
        # and orphan parent before touching its persisted PID.
        import subprocess
        row = self.db.one('SELECT process_id FROM worker_heartbeats WHERE worker_id=?', (task.get('worker_id'),))
        if not row:
            return
        pid = row['process_id']
        try:
            result = subprocess.run(['ps','-p',str(pid),'-o','ppid=,args='],capture_output=True,text=True,timeout=2)
            pieces = result.stdout.strip().split(None,1)
            if len(pieces)!=2 or int(pieces[0])!=1:
                return
            # ps prints argv without shell quoting; production's Application
            # Support path contains spaces. Compare its exact suffix instead of
            # incorrectly splitting that path into several arguments.
            command = pieces[1]
            if not command.endswith(' '+str(self.data_dir)+' '+task['id']) or not any(marker in command for marker in (' -m jobagent.worker ',' --meridian-worker ')):
                return
            if os.getpgid(pid)==pid:
                os.killpg(pid,signal.SIGKILL)
        except (ProcessLookupError,ValueError,subprocess.TimeoutExpired,PermissionError):
            pass

    def _uncertain(self, task):
        if not task.get('application_id'):
            return False
        return bool(self.db.one("SELECT id FROM application_submissions WHERE application_id=? AND state IN ('SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED','CONFIRMED')", (task['application_id'],)) or
                    self.db.one("SELECT id FROM applications WHERE id=? AND status IN ('SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED')", (task['application_id'],)) or
                    self.db.one("SELECT id FROM automation_runs WHERE application_id=? AND status IN ('submitting','unconfirmed','confirmed','interrupted')", (task['application_id'],)))

    def _recover(self, task, reason):
        if task.get('application_id'):
            from .documents import SUBMITTED_STATUSES
            application = self.application(task['application_id'])
            if application['status'] in SUBMITTED_STATUSES - {'APPLYING','SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED'}:
                self.db.execute("UPDATE automation_tasks SET state='COMPLETED',worker_id=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?", (now(),task['id']))
                if task.get('run_id'):
                    self.db.execute("UPDATE automation_runs SET status='reconciled',updated_at=? WHERE id=?", (now(),task['run_id']))
                return
        uncertain = self._uncertain(task)
        state = 'WAITING_FOR_USER' if uncertain else 'RETRY_SCHEDULED' if task['attempts'] < task['max_attempts'] else 'FAILED'
        if task.get('run_id'):
            run = self.db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (task['run_id'],))
            checkpoint = json.loads(run['checkpoint']) if run else {}
            checkpoint['snapshot_id'] = None  # approvals never survive process loss
            self.db.execute('UPDATE automation_runs SET status=?,checkpoint=?,error=?,updated_at=? WHERE id=?',
                            ('unconfirmed' if uncertain else 'recovering' if state != 'FAILED' else 'error', dumps(checkpoint), reason, now(), task['run_id']))
        self.db.execute('UPDATE automation_tasks SET state=?,error=?,worker_id=NULL,lease_until=NULL,next_run=?,updated_at=? WHERE id=?',
                        (state, reason, time.time() + min(60, 2 ** task['attempts']), now(), task['id']))
        self.db.execute('INSERT INTO worker_failures VALUES(?,?,?,?,?,?)', (uid(), task['id'], task.get('worker_id'), reason, state, now()))
        if task.get('worker_id'):
            self.db.execute('UPDATE worker_heartbeats SET state=?,timeout_status=? WHERE worker_id=?', (state,reason if 'timeout' in reason else None,task['worker_id']))
        if state in ('FAILED', 'WAITING_FOR_USER') and task.get('application_id'):
            self.human_task(task['application_id'], 'SUBMISSION_UNCONFIRMED' if uncertain else 'WORKER_FAILED',
                            'Check the employer confirmation before retrying.' if uncertain else 'Worker retry budget exhausted. Inspect logs and retry this task.')

    def enqueue(self, kind, payload=None, *, application_id=None, run_id=None, dedupe_key=None, account_scope=''):
        task_id = uid()
        key = dedupe_key or f'{kind}:{application_id or "daily"}'
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM automation_tasks WHERE dedupe_key=? AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (key,)).fetchone()
            if existing:
                return {'task_id': existing['id'], 'run_id': existing['run_id'], 'status': existing['state'].lower(), 'duplicate': True}
            conn.execute('INSERT INTO automation_tasks(id,kind,application_id,run_id,dedupe_key,account_scope,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (task_id, kind, application_id, run_id, key, account_scope, dumps(payload or {}), now(), now()))
        return {'task_id': task_id, 'run_id': run_id, 'status': 'queued'}

    def launch(self, application_id, resume=False, section_grant=None):
        application = self.application(application_id)
        if application['mode'] == 'manual':
            raise ValueError('Manual mode prepares documents only')
        if self.paused() and not application.get('section_consent'):
            raise ValueError('Automation is paused. Resume before filling a form.')
        from .documents import SUBMITTED_STATUSES
        if application['status'] in SUBMITTED_STATUSES - {'APPLYING'} or self._uncertain({'application_id': application_id}):
            raise ValueError('Already submitted or uncertain submission: reconcile before retrying')
        from .job_validity import eligible
        job = decode_row(self.db.one('SELECT * FROM jobs WHERE id=?', (application['job_id'],)))
        if not eligible(job):
            raise ValueError('This job is closed or its published deadline has expired')
        existing = self.db.one("SELECT * FROM automation_tasks WHERE application_id=? AND kind='application' AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (application_id,))
        if existing:
            worker = self.workers.get(existing.get('worker_id'))
            if worker and existing['state'] == 'WAITING_FOR_USER':
                self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._resume_worker(worker, resume=True)))
                return {'task_id': existing['id'], 'run_id': existing['run_id'], 'status': 'running'}
            if resume and not worker and existing['state'] == 'WAITING_FOR_USER':
                self.db.execute("UPDATE automation_tasks SET state='QUEUED',worker_id=NULL,lease_until=NULL,next_run=0,error=NULL,updated_at=? WHERE id=?", (now(), existing['id']))
                return {'task_id': existing['id'], 'run_id': existing['run_id'], 'status': 'queued'}
            return {'task_id': existing['id'], 'run_id': existing['run_id'], 'status': existing['state'].lower()}
        run_id = uid()
        self.db.execute("INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) VALUES(?,?,'queued',?,'{}',?,?)", (run_id, application_id, settings_for(self.db).get('browser_engine', 'deterministic'), now(), now()))
        parsed = urlparse(application['url'])
        # ATS board accounts are separate tenants. Custom shared account scope may
        # be configured explicitly; one lease guards every action including submit.
        scope = (application.get('metadata') or {}).get('account_scope') or f'{parsed.hostname}:{parsed.path.split("/")[1]}'
        return self.enqueue('application', {'resume': resume}, application_id=application_id, run_id=run_id, account_scope=scope)

    async def _resume_worker(self, worker, resume):
        worker.state = 'RUNNING'
        worker.progress = time.monotonic()
        self.db.execute("UPDATE automation_tasks SET state='RUNNING',updated_at=? WHERE id=?", (now(), worker.task['id']))
        await self._rpc(worker, 'fill', {'resume': resume}, timeout=self.progress_seconds)

    def _claim(self, capacity):
        with self.db.transaction() as conn:
            tasks = conn.execute("SELECT * FROM automation_tasks WHERE state IN ('QUEUED','RETRY_SCHEDULED') AND next_run<=? ORDER BY created_at LIMIT 50", (time.time(),)).fetchall()
            for row in tasks:
                task = dict(row)
                if task['kind'] == 'application':
                    from .documents import SUBMITTED_STATUSES
                    application = self.application(task['application_id'])
                    if application['status'] in SUBMITTED_STATUSES - {'APPLYING','SUBMISSION_PENDING','SUBMISSION_UNCONFIRMED'}:
                        conn.execute("UPDATE automation_tasks SET state='COMPLETED',error='Application already advanced; preparation not replayed',updated_at=? WHERE id=?", (now(), task['id']))
                        continue
                    if self._uncertain(task):
                        conn.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER',error='Reconcile submission evidence before retrying',updated_at=? WHERE id=?", (now(), task['id']))
                        continue
                if task['kind'] == 'application' and capacity <= 0:
                    continue
                if task['kind'] != 'application' and conn.execute("SELECT id FROM automation_tasks WHERE kind=? AND worker_id IS NOT NULL AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (task['kind'],)).fetchone():
                    continue
                if task['account_scope'] and conn.execute("SELECT id FROM automation_tasks WHERE account_scope=? AND worker_id IS NOT NULL AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (task['account_scope'],)).fetchone():
                    continue
                if task['kind'] == 'application' and self.paused() and not self.application(task['application_id']).get('section_consent'):
                    continue
                worker_id = uid()
                conn.execute("UPDATE automation_tasks SET state='STARTING',worker_id=?,attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?", (worker_id, time.time()+self.watchdog_seconds, now(), task['id']))
                task.update(worker_id=worker_id, attempts=task['attempts']+1)
                return task
        return None

    async def _spawn(self, task):
        command = [sys.executable, '--meridian-worker', str(self.data_dir), task['id']] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'jobagent.worker', str(self.data_dir), task['id']]
        env = dict(os.environ)
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, env=env, limit=4*1024**2)
        worker = Worker(task['worker_id'], task, process)
        self.workers[worker.id] = worker
        worker.reader = asyncio.create_task(self._read(worker))
        # Always drain diagnostics to avoid a full stderr pipe deadlocking a child.
        worker.stderr = asyncio.create_task(self._drain(process.stderr))

    @staticmethod
    async def _drain(pipe):
        while await pipe.read(8192):
            pass  # Detailed bounded errors live in task records, not credential logs.

    async def _read(self, worker):
        while line := await worker.process.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get('type') == 'heartbeat':
                worker.heartbeat = time.monotonic()
                worker.status = message
                if message.get('run_id'):
                    worker.task['run_id'] = message['run_id']
                worker.state = message['state']
                if message.get('progress') != worker.task.get('_progress'):
                    worker.progress = time.monotonic()
                    worker.task['_progress'] = message.get('progress')
                await asyncio.to_thread(self._persist_heartbeat, worker)
            elif message.get('id') in worker.pending:
                future = worker.pending.pop(message['id'])
                if not future.done():
                    if message.get('error'):
                        future.set_exception(ValueError(message['error']))
                    else:
                        future.set_result(message.get('result'))

    def _persist_heartbeat(self, worker):
        m = worker.status
        stamp = time.time()
        self.db.execute('INSERT OR REPLACE INTO worker_heartbeats VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (worker.id, worker.task['id'], worker.process.pid, worker.state, stamp, stamp-(time.monotonic()-worker.progress), m.get('operation', 'waiting'), m.get('url'), max(worker.memory_bytes, m.get('memory_bytes', 0)), worker.task['attempts']-1, None))
        if worker.state not in TERMINAL:
            self.db.execute('UPDATE automation_tasks SET state=?,lease_until=?,updated_at=? WHERE id=? AND worker_id=?', (worker.state, stamp+self.watchdog_seconds, now(), worker.task['id'], worker.id))
        if m.get('checkpoint_changed') and worker.task.get('run_id'):
            run = self.db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (worker.task['run_id'],))
            if run:
                self.db.execute('INSERT INTO task_checkpoints VALUES(?,?,?,?)', (uid(), worker.task['id'], run['checkpoint'], now()))
                self.db.execute('UPDATE automation_tasks SET checkpoint=? WHERE id=?', (run['checkpoint'], worker.task['id']))

    async def _rpc(self, worker, command, payload=None, timeout=30):
        if worker.process.returncode is not None:
            raise ValueError('Worker disconnected. Wait for recovery.')
        request_id = uid()
        future = asyncio.get_running_loop().create_future()
        worker.pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                worker.process.stdin.write((dumps({'id': request_id, 'command': command, 'payload': payload or {}})+'\n').encode())
                await worker.process.stdin.drain()
                return await future
        finally:
            worker.pending.pop(request_id, None)

    async def _watch(self):
        while not self._closing:
            try:
                config = await asyncio.to_thread(settings_for, self.db)
                self.watchdog_seconds = float(config.get('worker_watchdog_seconds', 30))
                self.progress_seconds = float(config.get('worker_progress_seconds', 240))
                if time.monotonic()-self._memory_checked >= 5 and self.workers:
                    await self._measure_memory()
                for worker in list(self.workers.values()):
                    expired = time.monotonic()-worker.heartbeat > self.watchdog_seconds
                    stalled = worker.state in ACTIVE and time.monotonic()-worker.progress > self.progress_seconds
                    over_memory = max(worker.memory_bytes, worker.status.get('memory_bytes', 0)) > self.max_memory_bytes
                    if worker.process.returncode is not None or expired or stalled or over_memory:
                        await self._stop(worker)
                        task = await asyncio.to_thread(self.db.one, 'SELECT * FROM automation_tasks WHERE id=?', (worker.task['id'],))
                        if task['state'] not in TERMINAL:
                            await asyncio.to_thread(self._recover, task, 'heartbeat_timeout' if expired else 'progress_timeout' if stalled else 'memory_limit' if over_memory else 'process_exit')
                    elif worker.state in TERMINAL:
                        await self._stop(worker)
                    elif worker.state == 'WAITING_FOR_USER' and time.monotonic()-worker.progress > 900:
                        await self._stop(worker)
                        await asyncio.to_thread(self.db.execute, "UPDATE automation_tasks SET worker_id=NULL,lease_until=NULL,error='Review session expired; resume to restore the form',updated_at=? WHERE id=?", (now(), worker.task['id']))
                count = sum(w.task['kind']=='application' for w in self.workers.values())
                task = await asyncio.to_thread(self._claim, max(0, int(config.get('application_concurrency', 2))-count))
                if task:
                    try:
                        await self._spawn(task)
                    except Exception:
                        await asyncio.to_thread(self._recover, task, 'spawn_failed')
            except Exception:
                # A bounded DB lock cannot take down the monitor or API.
                import logging
                logging.getLogger(__name__).warning('supervisor_tick_failed')
            await asyncio.sleep(.5)

    async def _measure_memory(self):
        # Include Chrome helpers in the owned process group, rather than measuring
        # only Python. ps is bounded and never blocks the API event loop.
        self._memory_checked = time.monotonic()
        process = await asyncio.create_subprocess_exec('ps','-axo','pgid=,rss=', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 2)
        except TimeoutError:
            process.kill()
            await asyncio.wait_for(process.wait(), 2)
            return
        totals = {}
        for line in output.decode().splitlines():
            columns = line.split()
            if len(columns) == 2:
                group, rss = map(int, columns)
                totals[group] = totals.get(group, 0)+rss*1024
        for worker in self.workers.values():
            worker.memory_bytes = totals.get(worker.process.pid, 0)

    async def _stop(self, worker):
        worker.stopping = True
        # Owned child process group only; never use broad Chrome kill commands.
        # A reaped leader can leave Chrome helpers in its still-owned process
        # group. Group IDs remain reserved while members exist.
        try:
            os.killpg(worker.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if worker.process.returncode is None:
            try:
                await asyncio.wait_for(worker.process.wait(), 2)
            except (ProcessLookupError, TimeoutError):
                try:
                    os.killpg(worker.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(worker.process.wait(), 2)
        for future in worker.pending.values():
            if not future.done():
                future.set_exception(ValueError('Worker stopped; check task recovery status'))
        for task in (worker.reader, worker.stderr):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (worker.reader, worker.stderr) if t), return_exceptions=True)
        self.workers.pop(worker.id, None)

    def _worker_for(self, application_id):
        for worker in self.workers.values():
            if worker.task['application_id'] == application_id:
                return worker
        raise ValueError('This application has no live worker. Resume to restore its checkpoint.')

    async def approve(self, application_id):
        return await self._rpc(self._worker_for(application_id), 'approve', timeout=70)

    async def approve_section(self, application_id, snapshot_id):
        return await self._rpc(self._worker_for(application_id), 'approve_section', {'snapshot_id': snapshot_id})

    async def takeover_application(self, application_id):
        return await self._rpc(self._worker_for(application_id), 'takeover')

    async def cancel(self, application_id):
        task = self.db.one("SELECT * FROM automation_tasks WHERE application_id=? AND state NOT IN ('COMPLETED','FAILED','CANCELLED') ORDER BY created_at DESC LIMIT 1", (application_id,))
        if not task:
            raise ValueError('No active task to cancel')
        worker = self.workers.get(task.get('worker_id'))
        if worker:
            await self._stop(worker)
        uncertain = self._uncertain(task)
        self.db.execute("UPDATE automation_tasks SET state='CANCELLED',worker_id=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now(), task['id']))
        if task['run_id']:
            self.db.execute('UPDATE automation_runs SET status=?,updated_at=? WHERE id=?', ('unconfirmed' if uncertain else 'cancelled', now(), task['run_id']))
        return {'task_id': task['id'], 'status': 'unconfirmed' if uncertain else 'cancelled'}

    async def control(self, task_id, action):
        task = self.db.one('SELECT * FROM automation_tasks WHERE id=?', (task_id,))
        if not task:
            raise ValueError('Task not found')
        if action == 'cancel':
            if task['application_id']:
                return await self.cancel(task['application_id'])
            worker = self.workers.get(task.get('worker_id'))
            if worker:
                await self._stop(worker)
            self.db.execute("UPDATE automation_tasks SET state='CANCELLED',worker_id=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now(), task_id))
        elif action in ('retry', 'restart', 'resume'):
            if self._uncertain(task):
                raise ValueError('Reconcile submission evidence before retrying')
            worker = self.workers.get(task.get('worker_id'))
            if worker:
                await self._stop(worker)
            self.db.execute("UPDATE automation_tasks SET state='QUEUED',attempts=0,worker_id=NULL,lease_until=NULL,next_run=0,error=NULL,updated_at=? WHERE id=?", (now(), task_id))
        else:
            raise ValueError('Unknown task action')
        return {'task_id': task_id, 'action': action}

    def reconcile_browser_state(self):
        pass  # Child state is reported independently; no stale browser IPC here.

    def pause(self):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('automation_paused','true')")
        for worker in self.workers.values():
            if worker.task['kind']=='application':
                asyncio.create_task(self._pause_worker(worker))
        return {'paused': True}

    async def _pause_worker(self, worker):
        try:
            await self._rpc(worker, 'pause')
        except (ValueError, TimeoutError, BrokenPipeError):
            # The watchdog owns recovery; a pause RPC failure cannot escape as an
            # unobserved task exception or affect sibling applications.
            pass

    async def resume(self):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('automation_paused','false')")
        return {'paused': False}

    def status(self):
        result = super().status()
        result['running'] = any(w.state in ACTIVE for w in self.workers.values())
        result['tasks'] = [decode_row(t) for t in self.db.query('SELECT * FROM automation_tasks ORDER BY created_at DESC LIMIT 100')]
        result['workers'] = self.db.query("SELECT * FROM worker_heartbeats WHERE worker_id IN (SELECT worker_id FROM automation_tasks WHERE worker_id IS NOT NULL AND state NOT IN ('COMPLETED','FAILED','CANCELLED'))")
        result['process_isolation'] = True
        result['browser_sessions'] = [{'application_id': w.task.get('application_id'), 'task_id': w.task['id'], 'running': w.process.returncode is None, 'current_url': w.status.get('url')} for w in self.workers.values()]
        return result

    async def close(self):
        self._closing = True
        if self._monitor:
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
        for worker in list(self.workers.values()):
            await self._stop(worker)
        # Persist active leases for startup recovery; closing UI is not cancellation.
        await self.browser.close()
