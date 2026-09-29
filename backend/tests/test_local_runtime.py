
from jobagent.automation.local_runtime import LAYA_REVISION, LayaRuntime


async def test_runtime_does_not_kill_external_service(tmp_path, monkeypatch):
    runtime = LayaRuntime(tmp_path)
    async def external_health():
        return {"ok": True, "backend": "laya-mlx"}
    monkeypatch.setattr(runtime, "_health", external_health)
    result = await runtime.start()
    assert result["running"] and not result["managed"]
    stopped = await runtime.stop()
    assert stopped["running"] and not stopped["managed"]
    assert runtime._process is None


async def test_missing_python_install_is_explicit_and_actionable(tmp_path, monkeypatch):
    runtime = LayaRuntime(tmp_path)
    monkeypatch.setattr(runtime, "_executable", lambda: None)
    monkeypatch.setattr(runtime, "_python", lambda: None)
    async def no_health():
        return None
    monkeypatch.setattr(runtime, "_health", no_health)
    status = await runtime.install()
    assert status["state"] == "error"
    assert "Python" in status["error"]
    assert runtime._install_task is None


async def test_occupied_non_laya_port_never_spawns_process(tmp_path, monkeypatch):
    runtime = LayaRuntime(tmp_path)
    async def occupied():
        return {"ok": False, "occupied": True, "error": "Port occupied"}
    monkeypatch.setattr(runtime, "_health", occupied)
    status = await runtime.start()
    assert status["error"] == "Port occupied"
    assert runtime._process is None


def test_runtime_revision_is_pinned():
    assert len(LAYA_REVISION) == 40
    assert all(c in "0123456789abcdef" for c in LAYA_REVISION)
