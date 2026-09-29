"""Own an optional local-only Ollama service without changing system services."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal

import httpx


OLLAMA_MODEL = "qwen2.5:7b-instruct-q4_K_M"


class OllamaRuntime:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.models_dir = self.data_dir / "models" / "ollama"
        self.endpoint = "http://127.0.0.1:11434"
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._error: str | None = None

    def _executable(self) -> Path | None:
        for application in (Path.home() / "Applications/Ollama.app", Path("/Applications/Ollama.app")):
            executable = application / "Contents/Resources/ollama"
            if executable.is_file() and os.access(executable, os.X_OK):
                return executable
        return None

    async def _probe(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=1, follow_redirects=False, trust_env=False) as client:
                response = await client.get(self.endpoint + "/api/version")
                response.raise_for_status()
                version = response.json()
                if not isinstance(version, dict) or not isinstance(version.get("version"), str):
                    return {"ok": False, "occupied": True}
                response = await client.get(self.endpoint + "/api/tags")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                    return {"ok": False, "occupied": True}
                available = any(
                    isinstance(model, dict) and model.get("name", model.get("model")) == OLLAMA_MODEL
                    for model in payload["models"]
                )
                return {"ok": True, "version": version["version"], "model_available": available}
        except httpx.ConnectError:
            return None
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "occupied": True}

    async def status(self) -> dict:
        executable = self._executable()
        probe = await self._probe()
        managed = self._process is not None and self._process.returncode is None
        foreign = probe is not None and not managed
        running = managed and bool(probe and probe.get("ok"))
        model_name, model_tag = OLLAMA_MODEL.split(":", 1)
        manifest = self.models_dir / "manifests/registry.ollama.ai/library" / model_name / model_tag
        downloaded = bool(probe.get("model_available")) if running else manifest.is_file()
        error = self._error
        if foreign:
            error = "Port 11434 is occupied by a service Meridian does not manage. Close that service before starting local text AI."
        state = (
            "blocked"
            if foreign
            else "running"
            if running
            else "starting"
            if managed
            else "error"
            if error
            else "installed"
            if executable
            else "not_installed"
        )
        return {
            "state": state,
            "installed": executable is not None,
            "running": running,
            "managed": managed,
            "foreign_service": foreign,
            "pid": self._process.pid if managed else None,
            "endpoint": self.endpoint,
            "model": OLLAMA_MODEL,
            "model_downloaded": downloaded,
            "ready": running and downloaded,
            "executable": str(executable) if executable else None,
            "models_dir": str(self.models_dir),
            "version": (probe or {}).get("version"),
            "cloud_enabled": False if managed else None,
            "context_length": 8192,
            "error": error,
        }

    async def start(self) -> dict:
        async with self._lock:
            if self._process and self._process.returncode is None:
                return await self.status()
            if await self._probe() is not None:
                return await self.status()
            executable = self._executable()
            if executable is None:
                self._error = "Install the official Ollama macOS app to enable optional local text generation."
                return await self.status()
            self.models_dir.mkdir(parents=True, exist_ok=True)
            self.models_dir.chmod(0o700)
            self._error = None
            environment = dict(
                os.environ,
                OLLAMA_HOST="127.0.0.1:11434",
                OLLAMA_NO_CLOUD="1",
                OLLAMA_CONTEXT_LENGTH="8192",
                OLLAMA_MODELS=str(self.models_dir),
                OLLAMA_NUM_PARALLEL="1",
                OLLAMA_MAX_LOADED_MODELS="1",
                OLLAMA_DEBUG="0",
            )
            try:
                self._process = await asyncio.create_subprocess_exec(
                    str(executable),
                    "serve",
                    env=environment,
                    start_new_session=True,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                self._error = "Ollama could not start. Check the official app installation and macOS permissions."
                return await self.status()
            for _ in range(50):
                if self._process.returncode is not None:
                    self._error = "Ollama exited during startup. Check port 11434 and the app installation."
                    break
                probe = await self._probe()
                if probe and probe.get("ok"):
                    break
                await asyncio.sleep(0.1)
            return await self.status()

    async def close(self) -> None:
        async with self._lock:
            process = self._process
            if process is None:
                return
            if process.returncode is None:
                # The new session is exclusively ours, including the model runner child.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            self._process = None


TextRuntime = OllamaRuntime
