import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobagent.core import prepare_application
from jobagent.db import Database, dumps, now, uid
from jobagent.discovery import ingest_job
from jobagent.job_validity import deadline_evidence, eligible
from jobagent.mail import classify_message, ingest_message
from jobagent.main import create_app
from jobagent.supervisor import WorkerSupervisor


@pytest.fixture
def workspace(tmp_path):
    db = Database(tmp_path / 'database/meridian.sqlite3')
    db.execute("INSERT INTO settings VALUES('automation_paused','false')")
    db.execute("INSERT INTO settings VALUES('browser_engine','\"deterministic\"')")
    return db, tmp_path


def application(db, path, suffix='one'):
    job, _ = ingest_job(db, {'title': 'Software Engineer', 'company': 'Example', 'url': f'https://example.test/{suffix}/job'})
    return prepare_application(db, job['id'], data_dir=path)['id']


def test_deadlines_never_come_from_posting_age():
    result = deadline_evidence({'url': 'https://example.test/job', 'description': 'Posted 30 days ago'})
    assert result['application_deadline'] is None
    assert result['deadline_state'] == 'NO_DEADLINE_PUBLISHED'
    assert deadline_evidence({'description': 'Closing date: 15 October 2027'})['deadline_state'] == 'EXPLICIT_DEADLINE'
    assert deadline_evidence({'metadata': {'valid_through': '2020-01-01'}})['deadline_state'] == 'EXPIRED'
    assert eligible({'availability': 'UNKNOWN'})
    assert not eligible({'availability': 'CLOSED'})
    assert not eligible({'metadata': {'application_deadline': '2020-01-01'}})


@pytest.mark.parametrize('uncertain', [False, True])
async def test_explicit_review_switch_parks_idle_account_without_losing_checkpoint(workspace, monkeypatch, uncertain):
    from types import SimpleNamespace
    db, path = workspace
    supervisor = WorkerSupervisor(db, path)
    old, target = application(db, path, 'old'), application(db, path, 'new')
    db.execute('UPDATE applications SET section_consent=1 WHERE id=?', (target,))
    first, second = supervisor.launch(old), supervisor.launch(target)
    db.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER',worker_id='old-worker',account_scope='same-account' WHERE id=?", (first['task_id'],))
    db.execute("UPDATE automation_tasks SET account_scope='same-account' WHERE id=?", (second['task_id'],))
    checkpoint = {'snapshot_id': 'old-observation', 'answers': [{'question': 'Saved question', 'answer': 'Saved answer'}]}
    db.execute('UPDATE automation_runs SET status=?,checkpoint=? WHERE id=?', ('unconfirmed' if uncertain else 'needs_review', json.dumps(checkpoint), first['run_id']))
    task = db.one('SELECT * FROM automation_tasks WHERE id=?', (first['task_id'],))
    worker = SimpleNamespace(id='old-worker', task=task, state='WAITING_FOR_USER', stopping=False)
    supervisor.workers[worker.id] = worker
    calls = []
    async def rpc(_worker, command, **_kwargs):
        calls.append(command)
    async def stop(_worker):
        calls.append('stop')
        supervisor.workers.pop(_worker.id)
    monkeypatch.setattr(supervisor, '_rpc', rpc)
    monkeypatch.setattr(supervisor, '_stop', stop)
    await supervisor._release_idle_scopes()
    if uncertain:
        assert not calls
        assert db.one('SELECT state FROM automation_tasks WHERE id=?', (second['task_id'],))['state'] == 'WAITING_FOR_USER'
        assert 'uncertain submission' in db.one('SELECT error FROM automation_runs WHERE id=?', (second['run_id'],))['error']
    else:
        assert calls == ['park', 'stop']
        saved = json.loads(db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (first['run_id'],))['checkpoint'])
        assert saved['answers'] == checkpoint['answers'] and saved['snapshot_id'] is None
        assert db.one('SELECT worker_id FROM automation_tasks WHERE id=?', (first['task_id'],))['worker_id'] is None
        assert supervisor._claim(1)['id'] == second['task_id']


def test_queue_deduplicates_and_survives_restart(workspace):
    db, path = workspace
    app_id = application(db, path)
    supervisor = WorkerSupervisor(db, path)
    first = supervisor.launch(app_id)
    assert supervisor.launch(app_id)['task_id'] == first['task_id']
    claimed = supervisor._claim(2)
    assert claimed['attempts'] == 1
    db.execute('UPDATE automation_runs SET checkpoint=? WHERE id=?', (json.dumps({'answers':[{'question':'Name','answer':'Test'}], 'snapshot_id':'stale'}), first['run_id']))
    second = WorkerSupervisor(Database(db.path), path)
    second._recover_abandoned()
    task = db.one('SELECT * FROM automation_tasks WHERE id=?', (first['task_id'],))
    assert task['state'] == 'RETRY_SCHEDULED'
    checkpoint = json.loads(db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (first['run_id'],))['checkpoint'])
    assert checkpoint['answers'][0]['answer'] == 'Test' and checkpoint['snapshot_id'] is None


def test_account_lock_and_retry_budget(workspace):
    db, path = workspace
    supervisor = WorkerSupervisor(db, path)
    a = supervisor.enqueue('application', application_id=application(db,path,'a'), account_scope='shared-account')
    b = supervisor.enqueue('application', application_id=application(db,path,'b'), account_scope='shared-account')
    claimed = supervisor._claim(2)
    assert claimed['id'] == a['task_id']
    assert supervisor._claim(2) is None
    db.execute('UPDATE automation_tasks SET attempts=max_attempts WHERE id=?', (claimed['id'],))
    supervisor._recover(db.one('SELECT * FROM automation_tasks WHERE id=?', (claimed['id'],)), 'injected_deadlock')
    assert db.one('SELECT state FROM automation_tasks WHERE id=?', (claimed['id'],))['state'] == 'FAILED'
    assert supervisor._claim(2)['id'] == b['task_id']


def test_submission_receipt_blocks_recovery_replay(workspace):
    db, path = workspace
    app_id = application(db,path)
    supervisor = WorkerSupervisor(db,path)
    task = supervisor.launch(app_id)
    db.execute('INSERT INTO application_submissions(id,application_id,run_id,identity_key,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', (uid(),app_id,task['run_id'],'identity','SUBMISSION_PENDING',now(),now()))
    assert supervisor._claim(2) is None
    assert db.one('SELECT state FROM automation_tasks WHERE id=?',(task['task_id'],))['state']=='WAITING_FOR_USER'
    with pytest.raises(ValueError, match='uncertain'):
        supervisor.launch(app_id)


def test_parked_review_resume_restores_its_checkpoint_instead_of_starting_over(workspace):
    db, path = workspace
    app_id = application(db, path)
    supervisor = WorkerSupervisor(db, path)
    run = supervisor.launch(app_id)
    db.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER' WHERE id=?", (run['task_id'],))
    db.execute('UPDATE automation_runs SET checkpoint=? WHERE id=?', (dumps({'answers': [{'question': 'Email', 'answer': 'synthetic@example.test'}]}), run['run_id']))
    resumed = supervisor.launch(app_id, resume=True)
    assert resumed['run_id'] == run['run_id']
    task = supervisor._claim(2)
    assert json.loads(task['payload'])['resume'] is True
    assert json.loads(db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (run['run_id'],))['checkpoint'])['answers']


def test_database_contention_is_bounded_and_data_preserved(workspace):
    db, _ = workspace
    writer = sqlite3.connect(db.path, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    start = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        db.execute("INSERT INTO settings VALUES('test_lock','true')")
    # Production writers wait up to 15 seconds for concurrent discovery/inbox
    # imports. A persistent lock must still fail within that bounded window.
    assert time.monotonic()-start < 17
    writer.rollback()
    writer.close()
    assert db.one("SELECT value FROM settings WHERE key='automation_paused'")['value']=='false'
    assert db.one('PRAGMA foreign_key_check') is None


@pytest.mark.parametrize('subject,body,event,sentiment', [
    ('Application','Thank you for applying','APPLICATION_CONFIRMATION','NEUTRAL'),
    ('Application','We will not be progressing','REJECTION','NEGATIVE'),
    ('Interview','We invite you to interview','INTERVIEW_INVITATION','POSITIVE'),
    ('Assessment','Complete a coding assessment within 5 days','TECHNICAL_TEST_INVITATION','POSITIVE'),
    ('Offer','We are pleased to offer you employment','OFFER','POSITIVE'),
    ('Newsletter','Unsubscribe. Try our HackerRank course','OTHER','NEUTRAL'),
    ('Update','Your application is under review','APPLICATION_UNDER_REVIEW','NEUTRAL'),
    ('Interview','Your interview has been rescheduled','INTERVIEW_RESCHEDULED','POSITIVE'),
])
def test_recruitment_dimensions(subject,body,event,sentiment):
    result=classify_message(subject,body,'2026-10-09T10:00:00+00:00')
    assert result['event_type']==event
    assert result['sentiment']==sentiment


def test_ambiguous_company_does_not_update_multiple_roles(workspace):
    db,path=workspace
    a=application(db,path,'a')
    other,_=ingest_job(db,{'title':'AI Engineer','company':'Example','url':'https://example.test/b'})
    b=prepare_application(db,other['id'],data_dir=path)['id']
    message={'external_id':'ambiguous','subject':'Example application','sender':'ats@vendor.test','body':'Thank you for applying','received_at':now()}
    before={identifier:db.one('SELECT status FROM applications WHERE id=?',(identifier,))['status'] for identifier in (a,b)}
    result=ingest_message(db,message)
    assert result['application_id'] is None
    for identifier in (a,b):
        assert db.one('SELECT status FROM applications WHERE id=?',(identifier,))['status']==before[identifier]
    assert ingest_message(db,message)['duplicate']


def test_networking_handoff_never_records_an_unsent_message(tmp_path, monkeypatch):
    monkeypatch.setenv('MERIDIAN_TEST','1')
    app=create_app(tmp_path,token='test',start_scheduler=False,worker_isolation=False)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        job=client.post('/api/jobs',json={'title':'Software Engineer','company':'Example','url':'https://example.test/role'}).json()
        client.post('/api/facts',json={'category':'project','key':'pipeline','value':'Built a Python pipeline.','verification_status':'verified'})
        payload={'full_name':'Test Recruiter','company':'Example','role':'Graduate recruiter','profile_url':'https://www.linkedin.com/in/test-recruiter','source':'User verified recruiting post','verified':True,'job_id':job['id'],'evidence':{'recruiting_responsibility':'Graduate hiring post'}}
        contact=client.post('/api/networking/contacts',json=payload).json()
        assert client.post('/api/networking/contacts',json=payload).json()['id']==contact['id']
        draft=client.post(f"/api/networking/contacts/{contact['id']}/draft",json={'job_id':job['id']}).json()
        assert 'Python pipeline' in draft['body']
        url=f"/api/networking/messages/{draft['id']}"
        assert client.post(url+'/handoff',json={'approved':False}).status_code==400
        assert client.post(url+'/handoff',json={'approved':True}).json()['direct_send'] is False
        assert app.state.db.one('SELECT state FROM outreach_messages WHERE id=?',(draft['id'],))['state']=='MESSAGE_PREPARED'
        sent={'sent_confirmed':True,'evidence':'Sent manually on LinkedIn'}
        assert client.post(url+'/confirm',json=sent).status_code==200
        assert client.post(url+'/confirm',json=sent).json()['duplicate']
        assert app.state.db.one('SELECT count(*) AS n FROM outreach_events')['n']==1


def test_additive_migration_preserves_existing_watchlist_and_evidence(workspace):
    from jobagent.companies import ensure_watchlist, REQUIRED_COMPANIES
    db,_=workspace
    db.execute('INSERT INTO companies(id,name,notes,created_at,updated_at) VALUES(?,?,?,?,?)', ('citi','Citi','Keep my priority notes',now(),now()))
    db.execute('INSERT INTO companies(id,name,notes,created_at,updated_at) VALUES(?,?,?,?,?)', ('extra','My extra company','Custom employer',now(),now()))
    ensure_watchlist(db)
    ensure_watchlist(db)
    assert db.one('SELECT notes FROM companies WHERE id=?',('citi',))['notes']=='Keep my priority notes'
    assert db.one('SELECT name FROM companies WHERE id=?',('extra',))['name']=='My extra company'
    assert db.one('SELECT count(*) AS n FROM companies')['n']==len(REQUIRED_COMPANIES)+1
    assert db.one('PRAGMA foreign_key_check') is None


@pytest.fixture
def reliability_server():
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self,*args):
            pass
    handler=partial(QuietHandler,directory=str(Path(__file__).parent/'browser/fixtures'))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['browser_close','worker_deadlock'])
async def test_three_process_applications_recover_independently(tmp_path,reliability_server,monkeypatch,failure):
    import httpx
    import os
    import signal
    import subprocess
    from jobagent.documents import save_version

    monkeypatch.setenv('MERIDIAN_TEST','1')
    app=create_app(tmp_path,token='reliability-test',start_scheduler=False)
    db=app.state.db
    for key,value in [('automation_paused',False),('browser_engine','deterministic'),('application_concurrency',3),('worker_watchdog_seconds',5)]:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))
    for category,key,value in [('contact','email','test@example.test'),('identity','full_name','Test Candidate')]:
        db.execute('INSERT INTO facts(id,category,key,value,source,verification_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uid(),category,key,value,'synthetic regression fixture','verified',now(),now()))
    tasks=[]
    async with app.router.lifespan_context(app):
        supervisor=app.state.automation
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'Authorization':'Bearer reliability-test'}) as client:
            for i,ats in enumerate(('greenhouse','ashby','workday')):
                flags='&reload=yes&conditional=yes' if i==1 else '&slow=yes' if i==2 else ''
                job,_=ingest_job(db,{'title':'Software Engineer','company':f'Fixture {i}','url':reliability_server+f'/reliability_wizard.html?identity={i}{flags}', 'ats':ats,'metadata':{'account_scope':f'fixture-{i}'}})
                application_id=prepare_application(db,job['id'],data_dir=tmp_path)['id']
                for kind in ('cv','cover_letter'):
                    doc={'id':uid()}
                    db.execute('INSERT INTO documents(id,name,kind,job_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',(doc['id'],kind,kind,job['id'],now(),now()))
                    version=save_version(db,tmp_path,doc,f'Fixture {i}: {kind}'.encode(),kind+'.txt','text/plain',kind,approved=True)
                    db.execute('INSERT INTO application_documents VALUES(?,?,?)',(application_id,version['id'],kind))
                started=supervisor.launch(application_id)
                db.execute('UPDATE automation_tasks SET payload=? WHERE id=?',(json.dumps({'headless':True}),started['task_id']))
                tasks.append(started)
            deadline=time.monotonic()+150
            injected=False
            max_health=0
            while time.monotonic()<deadline:
                before=time.monotonic()
                assert (await asyncio.wait_for(client.get('/api/health'),1)).status_code==200
                max_health=max(max_health,time.monotonic()-before)
                first=db.one('SELECT * FROM automation_tasks WHERE id=?',(tasks[0]['task_id'],))
                worker=supervisor.workers.get(first['worker_id'])
                if not injected and worker and worker.status.get('state')=='RUNNING' and len(supervisor.workers)>=3:
                    if failure=='worker_deadlock':
                        os.kill(worker.process.pid,signal.SIGSTOP)
                        injected=True
                    else:
                        marker=str(tmp_path/'browser-profile/applications'/first['application_id'])
                        lines=subprocess.check_output(['ps','-axo','pid=,args='],text=True).splitlines()
                        candidates=[int(line.strip().split()[0]) for line in lines if '--user-data-dir='+marker in line and '--type=' not in line]
                        if candidates:
                            os.kill(candidates[0],signal.SIGKILL)
                            injected=True
                states=[db.one('SELECT * FROM automation_tasks WHERE id=?',(t['task_id'],)) for t in tasks]
                if injected and all(t['state']=='WAITING_FOR_USER' for t in states):
                    runs=[db.one('SELECT * FROM automation_runs WHERE id=?',(t['run_id'],)) for t in states]
                    if all(r['status']=='needs_review' for r in runs):
                        break
                if any(t['state']=='FAILED' for t in states):
                    pytest.fail(str([(t['state'],t['error'],db.one('SELECT status,error,checkpoint FROM automation_runs WHERE id=?',(t['run_id'],))) for t in states]))
                await asyncio.sleep(.2)
            else:
                pytest.fail('Concurrent recovery exceeded 150 seconds: '+str([(t['state'],t['error']) for t in states]))
            assert injected
            assert max_health<1
            assert states[0]['attempts']>=2
            assert states[1]['attempts']==states[2]['attempts']==1
            assert db.one('SELECT count(*) AS n FROM applications')['n']==3
            assert db.one('SELECT count(*) AS n FROM application_submissions')['n']==0
            for run in runs:
                checkpoint=json.loads(run['checkpoint'])
                assert checkpoint['form_state']=='FINAL_REVIEW'
                assert len(checkpoint['completed_steps'])>=5
                assert checkpoint['documents']
            # Cancelling one idle review cannot invalidate either sibling review.
            await supervisor.cancel(states[0]['application_id'])
            assert db.one('SELECT state FROM automation_tasks WHERE id=?',(tasks[1]['task_id'],))['state']=='WAITING_FOR_USER'


def test_expired_review_resumes_same_task_and_preserves_checkpoint(workspace):
    db,path=workspace
    supervisor=WorkerSupervisor(db,path)
    app_id=application(db,path)
    queued=supervisor.launch(app_id)
    db.execute("UPDATE automation_tasks SET state='WAITING_FOR_USER',worker_id=NULL WHERE id=?",(queued['task_id'],))
    db.execute('UPDATE automation_runs SET checkpoint=? WHERE id=?',(json.dumps({'completed_steps':['Education'],'answers':[{'question':'Email','answer':'synthetic@example.test'}]}),queued['run_id']))
    resumed=supervisor.launch(app_id,resume=True)
    assert resumed['task_id']==queued['task_id'] and resumed['status']=='queued'
    assert db.one('SELECT count(*) AS n FROM automation_tasks')['n']==1
    assert json.loads(db.one('SELECT checkpoint FROM automation_runs WHERE id=?',(queued['run_id'],))['checkpoint'])['completed_steps']==['Education']


def test_queued_preparation_does_not_replay_after_email_confirmation(workspace):
    db,path=workspace
    supervisor=WorkerSupervisor(db,path)
    app_id=application(db,path)
    queued=supervisor.launch(app_id)
    db.event(app_id,'CONFIRMED','Synthetic confirmation','email')
    assert supervisor._claim(2) is None
    assert db.one('SELECT state FROM automation_tasks WHERE id=?',(queued['task_id'],))['state']=='COMPLETED'


def test_official_source_seeding_preserves_custom_portals_and_settings(workspace):
    from jobagent.companies import ensure_watchlist, REQUIRED_COMPANIES
    from jobagent.career_sources import seed_official_sources, OFFICIAL_CAREERS
    from jobagent.core import sync_company_source
    db,_=workspace
    ensure_watchlist(db)
    assert set(OFFICIAL_CAREERS)==set(REQUIRED_COMPANIES)
    company=db.one("SELECT * FROM companies WHERE name='Monzo'")
    db.execute('INSERT INTO sources(id,name,kind,url,config,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',('custom','My portal','greenhouse','https://job-boards.greenhouse.io/monzo',json.dumps({'company_id':company['id'],'board':'monzo','region':'eu'}),now(),now()))
    seed_official_sources(db)
    seed_official_sources(db)
    assert db.one('SELECT count(*) AS n FROM sources')['n']==len(REQUIRED_COMPANIES)
    company.update(careers_url='https://job-boards.greenhouse.io/monzo')
    sync_company_source(db,company)
    original=db.one("SELECT * FROM sources WHERE id='custom'")
    assert original['kind']=='greenhouse' and json.loads(original['config'])['region']=='eu'
    morgan = db.one("SELECT * FROM sources WHERE name='Morgan Stanley careers'")
    config = json.loads(morgan['config'])
    assert config['published_board']['kind'] == 'eightfold'
    assert config['published_board']['url'] == 'https://morganstanley.eightfold.ai/api/pcsx/search?domain=morganstanley.com'
    # A configured provider/feed belongs to the user; seeding does not replace it.
    config['published_board'] = {'kind': 'json', 'url': 'https://example.test/confirmed-feed'}
    db.execute('UPDATE sources SET config=? WHERE id=?', (json.dumps(config), morgan['id']))
    seed_official_sources(db)
    assert json.loads(db.one('SELECT config FROM sources WHERE id=?', (morgan['id'],))['config'])['published_board'] == config['published_board']
    meta = db.one("SELECT * FROM sources WHERE name='Meta careers'")
    config = json.loads(meta['config'])
    assert config['access_policy']['automation_requires_written_permission'] is True
    config['access_policy']['written_permission_evidence'] = 'Recorded synthetic publisher permission'
    db.execute('UPDATE sources SET config=? WHERE id=?', (json.dumps(config), meta['id']))
    seed_official_sources(db)
    assert json.loads(db.one('SELECT config FROM sources WHERE id=?', (meta['id'],))['config']) == config


@pytest.mark.parametrize('output_format',['pdf','docx','txt'])
def test_grounded_document_formats_remain_registered_and_immutable(workspace,output_format):
    from jobagent.documents import generate_document,document_path
    db,path=workspace
    app_id=application(db,path)
    db.execute('INSERT INTO facts(id,category,key,value,source,verification_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uid(),'project','pipeline','Built a Python pipeline.','verified test evidence','verified',now(),now()))
    job_id=db.one('SELECT job_id FROM applications WHERE id=?',(app_id,))['job_id']
    result=generate_document(db,path,job_id,'cv',approved=True,output_format=output_format)
    document,version=document_path(db,path,result['version']['id'])
    assert document.suffix=='.'+output_format
    assert version['approved'] and 'Python pipeline' in version['text']
    document.write_bytes(b'altered')
    with pytest.raises(ValueError,match='immutable'):
        document_path(db,path,version['id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('provider',['gmail','outlook'])
async def test_incremental_mail_cursor_commits_after_ingestion(tmp_path,monkeypatch,provider):
    import base64
    import httpx
    from jobagent.mail import MailService
    db=Database(tmp_path/'mail.db')
    cursor_key='gmail_history_id' if provider=='gmail' else 'outlook_delta_url'
    previous='100' if provider=='gmail' else 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=synthetic-old'
    next_cursor='200' if provider=='gmail' else 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=synthetic-next'
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',(provider,provider,json.dumps({cursor_key:previous}),'connected'))
    class Vault:
        def get(self,name):
            return json.dumps({'access_token':'synthetic-private-token','expires_at':9999999999})
    service=MailService(db,Vault())
    calls=[]
    raw=base64.urlsafe_b64encode(b'From: recruiter@example.test\r\nSubject: Application\r\nDate: Fri, 9 Oct 2026 10:00:00 +0000\r\n\r\nThank you for applying').decode()
    def respond(request):
        calls.append(request)
        if provider=='gmail':
            if request.url.path.endswith('/history'):
                assert request.url.params['startHistoryId'] in (previous,next_cursor)
                return httpx.Response(200,json={'historyId':next_cursor,'history':[{'messagesAdded':[{'message':{'id':'message-one'}}]}]})
            assert request.url.path.endswith('/message-one')
            return httpx.Response(200,json={'raw':raw,'threadId':'thread-one','historyId':'200'})
        assert 'IdType="ImmutableId"' in request.headers['Prefer']
        return httpx.Response(200,json={'value':[{'id':'message-one','subject':'Application','body':{'content':'Thank you for applying'},'receivedDateTime':'2026-10-09T10:00:00Z'}],'@odata.deltaLink':next_cursor})
    original=httpx.AsyncClient
    monkeypatch.setattr('jobagent.mail.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    assert (await service.sync())['imported']==1
    assert json.loads(db.one('SELECT config FROM integrations')['config'])[cursor_key]==next_cursor
    assert (await service.sync())['imported']==0
    assert db.one('SELECT count(*) AS n FROM email_messages')['n']==1
    assert all(request.url.host in ('gmail.googleapis.com','graph.microsoft.com') for request in calls)


@pytest.mark.asyncio
async def test_public_workday_pagination_is_bounded_and_preserves_unknown_data(monkeypatch):
    import httpx
    from jobagent.discovery import discover
    async def permitted(url):
        assert url=='https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/Public/jobs'
    monkeypatch.setattr('jobagent.discovery.validate_public_url',permitted)
    offsets=[]
    def respond(request):
        assert request.method=='POST' and request.headers.get('authorization') is None
        payload=json.loads(request.content)
        offsets.append(payload['offset'])
        return httpx.Response(200,json={'total':1000,'jobPostings':[{'title':'Software Engineer','externalPath':f"/job/{payload['offset']+i}",'locationsText':'London'} for i in range(payload['limit'])]})
    original=httpx.AsyncClient
    monkeypatch.setattr('jobagent.discovery.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    jobs=await discover({'kind':'workday','url':'https://fixture.wd1.myworkdayjobs.com/en-US/Public','config':{'company_name':'Fixture','max_jobs':45}})
    assert len(jobs)==45 and offsets==[0,20,40]
    assert len({j['url'] for j in jobs})==45
    assert jobs[0]['company']=='Fixture' and 'description' not in jobs[0]
    assert jobs[0]['metadata']['description_state']=='NOT_PUBLISHED_IN_LISTING'


@pytest.mark.asyncio
async def test_document_api_uses_owned_process_and_returns_durable_result(tmp_path,monkeypatch):
    import httpx
    monkeypatch.setenv('MERIDIAN_TEST','1')
    app=create_app(tmp_path,token='document-test',start_scheduler=False)
    db=app.state.db
    job,_=ingest_job(db,{'title':'Software Engineer','company':'Fixture','url':'https://example.test/document-job'})
    db.execute('INSERT INTO facts(id,category,key,value,source,verification_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uid(),'project','pipeline','Built a verified Python pipeline.','synthetic regression fixture','verified',now(),now()))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'Authorization':'Bearer document-test'}) as client:
            started=time.monotonic()
            response=await client.post('/api/documents/generate',json={'job_id':job['id'],'kind':'cv','approved':True})
            assert response.status_code==200 and time.monotonic()-started<1
            task_id=response.json()['task_id']
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                task=(await client.get('/api/automation/tasks/'+task_id)).json()
                if task['state']=='COMPLETED':
                    break
                assert task['state']!='FAILED',task
                await asyncio.sleep(.1)
            assert task['state']=='COMPLETED'
            assert task['checkpoint']['version']['approved']
            assert Path(task['checkpoint']['version']['path']).is_file()
            assert db.one('SELECT process_id FROM worker_heartbeats WHERE task_id=?',(task_id,))['process_id']!=__import__('os').getpid()


@pytest.mark.parametrize('matching',[True,False])
def test_orphan_cleanup_requires_exact_private_task_in_path_with_spaces(tmp_path,monkeypatch,matching):
    import subprocess
    from types import SimpleNamespace
    path=tmp_path/'Application Support'/'Meridian'
    db=Database(path/'database/meridian.sqlite3')
    supervisor=WorkerSupervisor(db,path)
    task=supervisor.enqueue('mail')
    db.execute('INSERT INTO worker_heartbeats(worker_id,task_id,process_id,state,heartbeat,last_progress,operation) VALUES(?,?,?,?,?,?,?)',('synthetic-worker',task['task_id'],200,'RUNNING',0,0,'test'))
    row=db.one('SELECT * FROM automation_tasks WHERE id=?',(task['task_id'],))
    row['worker_id']='synthetic-worker'
    suffix=task['task_id'] if matching else 'an-unrelated-task'
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=f'1 /private/tmp/meridian-backend --meridian-worker {path} {suffix}\n'))
    monkeypatch.setattr('jobagent.supervisor.os.getpgid',lambda pid:pid)
    killed=[]
    monkeypatch.setattr('jobagent.supervisor.os.killpg',lambda pid,sig:killed.append(pid))
    supervisor._cleanup_orphan(row)
    assert killed==([200] if matching else [])


def test_checkpoints_keep_job_identity_but_remove_authentication_secrets():
    from jobagent.security import private_checkpoint
    source={'current_url':'https://ats.example.test/form?gh_jid=123&code=private-code&state=private-state&access_token=private-token#access_token=fragment-secret','answers':[{'question':'Email','answer':'synthetic@example.test','fact_ids':['verified-fact']}],'last_step':{'url':'https://ats.example.test/next?job=123&sessionid=private-session','headers':{'Authorization':'private-bearer'}}}
    cleaned=private_checkpoint(source)
    assert cleaned['current_url']=='https://ats.example.test/form?gh_jid=123'
    assert cleaned['last_step']['url']=='https://ats.example.test/next?job=123'
    assert cleaned['last_step']['headers']=='[redacted]'
    assert cleaned['answers']==source['answers']
    assert 'private-' not in json.dumps(cleaned)
    assert 'private-code' in source['current_url']
