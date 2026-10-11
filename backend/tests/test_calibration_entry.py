"""Signed-sidecar calibration dispatch and its external-action boundaries."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from jobagent.calibration import answers, form, main as calibration_main
from jobagent.calibration.browser import DryRunBrowser
from jobagent.db import Database, now


def sidecar_entry():
    spec = spec_from_file_location("calibration_sidecar_entry", Path(__file__).parents[2] / "scripts/sidecar_entry.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["answers", "form"])
def test_explicit_calibration_dispatch_preserves_workflow_arguments(monkeypatch, mode):
    module = answers if mode == "answers" else form
    received = []
    monkeypatch.setattr(module, "main", received.append)
    arguments = ["--database", "profile-copy.sqlite3", "--allow-remote", "--usage-database", "shared.sqlite3"]
    calibration_main([mode, *arguments])
    assert received == [arguments]


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
    args = SimpleNamespace(database=source.path, output=tmp_path / "report", allow_remote=False, usage_database=None)
    with pytest.raises(ValueError, match="Credential access unavailable"):
        await answers.run(args)
    report = json.loads((args.output / "real-llm-regression.json").read_text())
    assert report["genuine_provider_calls"] is False
    assert report["answers"] == [] and report["no_form_fill_or_submission"] is True
    assert report["connection_error"]["error"] == "Credential access unavailable"
