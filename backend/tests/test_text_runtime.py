import signal
from types import SimpleNamespace

import pytest

from jobagent.automation.text_runtime import OLLAMA_MODEL, OllamaRuntime


@pytest.mark.parametrize(
    "health", [{"ok": True, "version": "external", "model_available": True}, {"ok": False, "occupied": True}]
)
async def test_text_runtime_refuses_existing_service_and_never_signals_it(tmp_path, monkeypatch, health):
    runtime = OllamaRuntime(tmp_path)

    async def probe():
        return health

    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("Must not spawn over another service")

    def forbidden_signal(*args):
        pytest.fail("Must not signal an unowned service")

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr("jobagent.automation.text_runtime.asyncio.create_subprocess_exec", forbidden_spawn)
    monkeypatch.setattr("jobagent.automation.text_runtime.os.killpg", forbidden_signal)
    status = await runtime.start()
    assert status["state"] == "blocked"
    assert status["foreign_service"] is True
    assert status["managed"] is False
    assert status["ready"] is False
    await runtime.close()


async def test_text_runtime_missing_install_does_not_download_or_spawn(tmp_path, monkeypatch):
    runtime = OllamaRuntime(tmp_path)

    async def probe():
        return None

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr(runtime, "_executable", lambda: None)
    result = await runtime.start()
    assert result["state"] == "error"
    assert "Install" in result["error"]
    assert not runtime.models_dir.exists()


async def test_text_runtime_reports_only_current_model_downloaded_when_stopped(tmp_path, monkeypatch):
    runtime = OllamaRuntime(tmp_path)

    async def probe():
        return None

    monkeypatch.setattr(runtime, "_probe", probe)
    old_manifest = runtime.models_dir / "manifests/registry.ollama.ai/library/llama3.2/3b-instruct-q4_K_M"
    old_manifest.parent.mkdir(parents=True)
    old_manifest.write_text("{}")
    assert (await runtime.status())["model_downloaded"] is False
    model_name, model_tag = OLLAMA_MODEL.split(":", 1)
    current_manifest = runtime.models_dir / "manifests/registry.ollama.ai/library" / model_name / model_tag
    current_manifest.parent.mkdir(parents=True)
    current_manifest.write_text("{}")
    status = await runtime.status()
    assert status["model_downloaded"] is True
    assert status["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert status["ready"] is False


async def test_text_runtime_owns_one_local_only_process_and_closes_its_session(tmp_path, monkeypatch):
    runtime = OllamaRuntime(tmp_path)
    process = SimpleNamespace(pid=987654, returncode=None)

    async def wait():
        process.returncode = -signal.SIGTERM
        return process.returncode

    process.wait = wait
    calls, signals = [], []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    async def probe():
        return (
            {"ok": True, "version": "fixture", "model_available": True}
            if calls and process.returncode is None
            else None
        )

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr(runtime, "_executable", lambda: tmp_path / "Ollama.app/Contents/Resources/ollama")
    monkeypatch.setattr("jobagent.automation.text_runtime.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("jobagent.automation.text_runtime.os.killpg", lambda *args: signals.append(args))
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:9999")
    monkeypatch.setenv("OLLAMA_NO_CLOUD", "0")
    first, second = await runtime.start(), await runtime.start()
    assert first["ready"] and second["ready"]
    assert first["managed"] and first["model"] == OLLAMA_MODEL
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[-1] == "serve"
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert kwargs["env"]["OLLAMA_NO_CLOUD"] == "1"
    assert kwargs["env"]["OLLAMA_CONTEXT_LENGTH"] == "8192"
    assert kwargs["env"]["OLLAMA_MODELS"] == str(tmp_path / "models/ollama")
    await runtime.close()
    await runtime.close()
    assert signals == [(987654, signal.SIGTERM)]
    assert runtime._process is None
