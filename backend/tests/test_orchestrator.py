import pytest

from jobagent.core import prepare_application
from jobagent.db import Database, now, uid
from jobagent.discovery import ingest_job
from jobagent.orchestrator import Orchestrator


class FailingSubmitBrowser:
    def __init__(self, *_):
        self.on_step = None

    async def fill_application(self, *_):
        return {
            "status": "needs_review",
            "answers": [],
            "steps": [],
            "questions": [],
            "snapshot_id": "observed-snapshot",
        }

    async def submit(self, *_, **__):
        raise RuntimeError("Connection was lost after Submit")

    def pause(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_auto_submit_exception_preserves_uncertainty_and_blocks_retry(tmp_path, monkeypatch):
    import jobagent.automation

    monkeypatch.setattr(jobagent.automation, "BrowserManager", FailingSubmitBrowser)
    db = Database(tmp_path / "database" / "meridian.sqlite3")
    job, _ = ingest_job(db, {"title": "Engineer", "company": "Example", "url": "https://example.test/position"})
    application = prepare_application(db, job["id"], "auto", data_dir=tmp_path)
    db.execute("INSERT INTO settings VALUES('automation_paused','false')")
    run_id = uid()
    db.execute(
        "INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'running',?,?)",
        (run_id, application["id"], now(), now()),
    )
    orchestrator = Orchestrator(db, tmp_path)
    # Isolate exception handling from independent confidence/domain/document gates.
    monkeypatch.setattr(orchestrator, "auto_gate", lambda *_: None)
    monkeypatch.setattr(orchestrator, "rate_gate", lambda *_: None)
    await orchestrator._fill(run_id, orchestrator.application(application["id"]), False)
    assert db.one("SELECT status FROM automation_runs WHERE id=?", (run_id,))["status"] == "unconfirmed"
    with pytest.raises(ValueError, match="uncertain"):
        orchestrator.launch(application["id"])
    assert db.one("SELECT COUNT(*) AS n FROM application_events WHERE status='APPLYING'")["n"] == 1
    assert db.one("SELECT id FROM human_tasks WHERE kind='SUBMISSION_UNCONFIRMED'")


@pytest.mark.asyncio
async def test_manual_sensitive_answer_is_authorized_only_for_current_application(tmp_path, monkeypatch):
    import jobagent.automation
    from jobagent.automation.answers import resolve_answer
    from jobagent.core import get_settings, save_answer

    observed = []

    class AnswerBrowser(FailingSubmitBrowser):
        async def fill_application(self, application, facts, documents, mode, config):
            answer = resolve_answer("I agree to the privacy policy", facts, policies=config["sensitive_policies"])
            observed.append(answer)
            return {"status": "human_required", "answers": [], "questions": [], "steps": []}

    monkeypatch.setattr(jobagent.automation, "BrowserManager", AnswerBrowser)
    db = Database(tmp_path / "database" / "meridian.sqlite3")
    db.execute("INSERT INTO settings VALUES('automation_paused','false')")
    orchestrator = Orchestrator(db, tmp_path)
    for index in range(2):
        job, _ = ingest_job(db, {"title": "Engineer", "company": "Example", "url": f"https://example.test/job/{index}"})
        application = prepare_application(db, job["id"], "review", data_dir=tmp_path)
        if index == 0:
            saved = save_answer(db, "I agree to the privacy policy", "Yes", [], True, application["id"])
        run_id = uid()
        db.execute(
            "INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'running',?,?)",
            (run_id, application["id"], now(), now()),
        )
        await orchestrator._fill(run_id, orchestrator.application(application["id"]), False)
    assert observed[0]["answer"] == "Yes"
    assert observed[0]["fact_ids"] == [saved["candidate_fact_id"]]
    assert observed[1]["answer"] is None
    assert get_settings(db)["sensitive_policies"] == {}


@pytest.mark.asyncio
async def test_retries_and_omissions_retain_history_without_reusing_it(tmp_path):
    from types import SimpleNamespace
    from jobagent.core import application_detail
    db = Database(tmp_path / 'database/meridian.sqlite3')
    job,_ = ingest_job(db, {'title':'Engineer','company':'Fixture','url':'https://example.test/history-job'})
    app = prepare_application(db,job['id'],'review',data_dir=tmp_path)
    run_id = uid()
    db.execute("INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'running',?,?)",(run_id,app['id'],now(),now()))
    coordinator = Orchestrator(db,tmp_path,recover=False)
    old_id = uid()
    db.execute('INSERT INTO application_answers VALUES(?,?,?,?,?,?,?)',(old_id,app['id'],'University','Original institution','[]',1,now()))
    original = db.one('SELECT * FROM application_answers WHERE id=?',(old_id,))
    coordinator.persist_result(run_id,app['id'],{'status':'needs_review','answers':[{'question':'University','answer':'Current institution','verified':True}]})
    assert db.one('SELECT * FROM application_answers WHERE id=?',(old_id,)) == original
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    detail = await application_detail(app['id'],request)
    assert [a['answer'] for a in detail['answers']] == ['Current institution']
    assert [a['id'] for a in detail['answer_history']] == [old_id]
    current_fingerprint = coordinator.fingerprint(app['id'])
    db.execute("UPDATE application_answers SET answer='Historical correction' WHERE id=?",(old_id,))
    assert coordinator.fingerprint(app['id']) == current_fingerprint
    coordinator.persist_result(run_id,app['id'],{'status':'needs_review','omitted':[{'question':'University','reason':'Not applicable'}]})
    detail = await application_detail(app['id'],request)
    assert detail['answers'] == [] and len(detail['answer_history']) == 2
    assert db.one('SELECT count(*) n FROM application_answers')['n'] == 2


@pytest.mark.asyncio
async def test_equal_labels_keep_each_field_and_edit_only_its_target(tmp_path):
    import json
    from types import SimpleNamespace
    from jobagent.core import application_detail
    from jobagent.automation.answers import question_identity
    db = Database(tmp_path/'database/meridian.sqlite3')
    job,_ = ingest_job(db,{'title':'Engineer','company':'Fixture','url':'https://example.test/education-job'})
    app = prepare_application(db,job['id'],'review',data_dir=tmp_path)
    run_id=uid()
    db.execute("INSERT INTO automation_runs(id,application_id,status,created_at,updated_at) VALUES(?,?,'running',?,?)",(run_id,app['id'],now(),now()))
    coordinator=Orchestrator(db,tmp_path,recover=False)
    fields=[{'label':'University','name':'university','section_label':'Undergraduate'}, {'label':'University','name':'university','section_label':'Postgraduate'}]
    ids=[question_identity(app['id'],f,'University') for f in fields]
    coordinator.persist_result(run_id,app['id'],{'status':'needs_review','answers':[{'question':'University','question_id':ids[0],'answer':'Bachelor institution'}, {'question':'University','question_id':ids[1],'answer':'Master institution'}]})
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    detail=await application_detail(app['id'],request)
    assert [a['answer'] for a in detail['answers']]==['Bachelor institution','Master institution']
    coordinator.browser._active_application_id=app['id']
    coordinator.on_user_edit({'frame_url':job['url'],'answer':'Corrected master institution','element':fields[1]})
    detail=await application_detail(app['id'],request)
    assert [a['answer'] for a in detail['answers']]==['Bachelor institution','Corrected master institution']
    assert [a['answer'] for a in detail['answer_history']]==['Master institution']
    checkpoint=json.loads(db.one('SELECT checkpoint FROM automation_runs WHERE id=?',(run_id,))['checkpoint'])
    assert {a['question_id']:a['answer'] for a in checkpoint['answers']}=={ids[0]:'Bachelor institution',ids[1]:'Corrected master institution'}
