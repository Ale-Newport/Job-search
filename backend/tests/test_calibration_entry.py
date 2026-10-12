"""Signed-sidecar calibration dispatch and its external-action boundaries."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from jobagent.calibration import answers, form, mail, main as calibration_main
from jobagent.calibration.browser import DryRunBrowser
from jobagent.db import Database, now


def sidecar_entry():
    spec = spec_from_file_location("calibration_sidecar_entry", Path(__file__).parents[2] / "scripts/sidecar_entry.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["answers", "form", "mail"])
def test_explicit_calibration_dispatch_preserves_workflow_arguments(monkeypatch, mode):
    module = {'answers': answers, 'form': form, 'mail': mail}[mode]
    received = []
    monkeypatch.setattr(module, "main", received.append)
    arguments = ["--database", "profile-copy.sqlite3", "--allow-remote", "--usage-database", "shared.sqlite3"]
    calibration_main([mode, *arguments])
    assert received == [arguments]


async def test_mail_calibration_requires_explicit_read_and_private_copy_before_credentials(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Inbox validation reached credentials without its explicit read guard')
    monkeypatch.setattr(mail, 'SecretStore', forbidden)
    args = SimpleNamespace(database=tmp_path/'copy.sqlite3', output=tmp_path/'report.json', read_inbox=False)
    with pytest.raises(ValueError, match='explicit --read-inbox'):
        await mail.run(args)
    args.database = Path.home()/'Library/Application Support/Meridian/database/meridian.sqlite3'
    args.read_inbox = True
    with pytest.raises(ValueError, match='separate database copy'):
        await mail.run(args)


@pytest.mark.parametrize("mode", ["unknown", "--meridian-worker"])
def test_calibration_rejects_unknown_modes(mode):
    with pytest.raises(SystemExit) as error:
        calibration_main([mode])
    assert error.value.code == 2


def test_sidecar_explicit_calibration_does_not_launch_application_or_worker(monkeypatch):
    module = sidecar_entry()
    calls = []
    monkeypatch.setattr(module, "run", lambda: calls.append("app"))
    monkeypatch.setattr(module, "worker_main", lambda: calls.append("worker"))
    monkeypatch.setattr(module, "calibration_main", lambda argv: calls.append(argv))
    module.main(["--meridian-calibration", "form", "--help"])
    assert calls == [["form", "--help"]]
    calls.clear()
    module.main([])
    module.main(["--meridian-worker", "--root", "/tmp/fixture"])
    assert calls == ["app", "worker"]


@pytest.mark.parametrize("workflow", [answers, form], ids=["answers", "form"])
@pytest.mark.parametrize("allow_remote", [False, True], ids=["unauthorized", "missing-shared-ledger"])
async def test_remote_calibration_guards_precede_keys_provider_and_browser(tmp_path, monkeypatch, workflow, allow_remote):
    source = Database(tmp_path / "profile-copy.sqlite3")
    source.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("text_provider", '"openai"'))

    def forbidden(*args, **kwargs):
        pytest.fail("Calibration reached credentials, provider construction, or browser before its remote-use guard")

    monkeypatch.setattr(workflow, "SecretStore", forbidden)
    monkeypatch.setattr(workflow, "ApplicationAnswerLLM", forbidden)
    if workflow is form:
        monkeypatch.setattr(workflow, "DryRunBrowser", forbidden)
    args = SimpleNamespace(
        database=source.path, output=tmp_path / "report", allow_remote=allow_remote, usage_database=None,
        job=None, url="https://example.invalid/application", company="Example", title="Engineer",
        location="London", fill=True, headless=True,
    )
    expected = "shared live database" if allow_remote else "explicit --allow-remote"
    with pytest.raises(ValueError, match=expected):
        await workflow.run(args)


async def test_calibration_browser_refuses_submission_even_without_a_browser(tmp_path):
    browser = DryRunBrowser(tmp_path)
    with pytest.raises(RuntimeError, match="never submits"):
        await browser.submit("snapshot")
    with pytest.raises(RuntimeError, match="Submission permission is unavailable"):
        await browser.execute(None, allow_submit=True)


async def test_form_calibration_rejects_another_vacancy_before_browser_or_keys(tmp_path, monkeypatch):
    from jobagent.discovery import ingest_job
    db = Database(tmp_path/'profile-copy.sqlite3')
    job, _ = ingest_job(db, {'title': 'Engineer', 'company': 'Fixture', 'url': 'https://jobs.lever.co/fixture/one'})
    def forbidden(*_):
        pytest.fail('A mismatched vacancy reached credentials or browser construction')
    monkeypatch.setattr(form, 'SecretStore', forbidden)
    monkeypatch.setattr(form, 'DryRunBrowser', forbidden)
    args = SimpleNamespace(database=db.path, output=tmp_path/'report', usage_database=None, job=job['id'], url='https://jobs.lever.co/fixture/two')
    with pytest.raises(ValueError, match='different vacancy'):
        await form.run(args)


async def test_custom_question_public_context_retains_the_exact_stored_job_identity(tmp_path,monkeypatch):
    from jobagent.discovery import ingest_job
    db = Database(tmp_path/'profile-copy.sqlite3')
    db.execute("INSERT INTO settings VALUES('text_provider','\"ollama\"')")
    job,_ = ingest_job(db,{'title':'Engineer','company':'Fixture','url':'https://example.test/jobs/123'})
    async def context(record):return {'job_id':record['id'],'company':'Fixture','title':'Engineer'}
    monkeypatch.setattr('jobagent.automation.job_context.prepare_job_context',context)
    class Offline:
        def __init__(self,*args,**kwargs):pass
        async def test(self,settings):raise ValueError('Synthetic provider offline')
    monkeypatch.setattr(answers,'ApplicationAnswerLLM',Offline)
    monkeypatch.setattr(answers,'SecretStore',lambda:object())
    output = tmp_path/'custom'
    args = SimpleNamespace(database=db.path,output=output,usage_database=None,allow_remote=False,job=job['id'],
                           question=None,question_text=['What coding experience do you have?'])
    with pytest.raises(ValueError,match='Synthetic provider offline'):
        await answers.run(args)
    report = json.loads((output/'real-llm-regression.json').read_text())
    assert report['job']['id']==job['id'] and report['custom_questions']
    assert not report['genuine_provider_calls']


def test_form_audit_matches_repeated_labels_to_their_actual_field_identity():
    from jobagent.automation.answers import question_identity
    from jobagent.calibration.browser import audit_fields
    from jobagent.automation.adapters import get_adapter
    fields = [{'index':i,'node_id':i,'document_id':'doc','form_id':'form','name':'university','label':'University','section_label':name,
               'role':'combobox','tag':'select','operations':['SELECT'],'value':value,'options':[]}
              for i,name,value in [(1,'undergraduate','North University'),(2,'postgraduate','South University')]]
    answers = [{'question':'University','answer':field['value'],'question_id':question_identity('job',field,'University')} for field in fields]
    engine = SimpleNamespace(answer_question=lambda *args: {'answer':None,'canonical_intent':'EDUCATION'})
    audit = audit_fields({'elements':fields},get_adapter('https://example.test'),engine,{'answers':answers},{'id':'job'})
    assert [field['verified_in_final_dom'] for field in audit] == [True,True]


@pytest.mark.parametrize("mode", ["answers", "form"])
def test_signed_workflow_help_exposes_required_authorization_and_shared_ledger(mode, capsys):
    with pytest.raises(SystemExit) as error:
        calibration_main([mode, "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--allow-remote" in help_text
    assert "--usage-database" in help_text
    assert "--database" in help_text and "--output" in help_text
    assert "--submit" not in help_text


async def test_failed_connection_keeps_a_report_without_claiming_genuine_calls(tmp_path, monkeypatch):
    source = Database(tmp_path / "profile-copy.sqlite3")
    source.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("text_provider", '"ollama"'))
    source.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("sample", "Software Engineer, New Grad", "Palantir", "London", "https://example.invalid/job",
                    "https://example.invalid/job", "sample", now(), now()))
    source.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("newer-vacancy", "Software Engineer, New Grad", "Palantir", "London", "https://example.invalid/other",
                    "https://example.invalid/other", "newer-vacancy", "2030-01-01", "2030-01-01"))

    async def unavailable(url):
        return None

    class FailedProbe:
        def __init__(self, *args, **kwargs):
            pass

        async def test(self, settings):
            raise ValueError("Credential access unavailable")

    monkeypatch.setattr(answers, "CompanyResearch", lambda path: SimpleNamespace(source=unavailable))
    monkeypatch.setattr(answers, "SecretStore", lambda: None)
    monkeypatch.setattr(answers, "ApplicationAnswerLLM", FailedProbe)
    args = SimpleNamespace(database=source.path, output=tmp_path / "report", job="sample", allow_remote=False, usage_database=None)
    with pytest.raises(ValueError, match="Credential access unavailable"):
        await answers.run(args)
    report = json.loads((args.output / "real-llm-regression.json").read_text())
    assert report["genuine_provider_calls"] is False
    assert report["job"]["id"] == "sample"
    assert report["answers"] == [] and report["no_form_fill_or_submission"] is True
    assert report["connection_error"]["error"] == "Credential access unavailable"
