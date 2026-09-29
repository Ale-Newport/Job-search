"""Opt-in local Laya installation and supervised service lifecycle; no bundled model."""

from __future__ import annotations

import asyncio
from collections import deque
import os
from pathlib import Path
import platform
import shutil
import sys
import time

import httpx


LAYA_REVISION = "9060f073e7836d6f10a6b0596f362e362f32107e"


class LayaRuntime:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.models_dir = self.data_dir / "models"
        self.runtime_dir = self.models_dir / "laya-runtime"
        self.endpoint = "http://127.0.0.1:8791"
        self._process: asyncio.subprocess.Process | None = None
        self._install_task: asyncio.Task | None = None
        self._install_process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._logs: deque[str] = deque(maxlen=60)
        self._state = "not_installed"
        self._error: str | None = None
        self._started_at: float | None = None

    def _executable(self) -> Path | None:
        preferred = self.runtime_dir / "bin" / "localdecide"
        if preferred.is_file():
            return preferred
        # Development checkout only: never search arbitrary user directories.
        if not getattr(sys, "frozen", False):
            project = Path(__file__).resolve().parents[3]
            dev = project / ".local-models/laya/models/laya-runtime/bin/localdecide"
            if dev.is_file():
                return dev
        return None

    def _cache(self, executable: Path | None = None) -> Path:
        if executable and ".local-models" in executable.parts:
            return executable.parents[3] / "huggingface"
        return self.models_dir / "huggingface"

    @staticmethod
    def _python() -> str | None:
        for name in ("python3.12", "python3.13"):
            found = shutil.which(name)
            if found:
                return found
            for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
                candidate = Path(prefix) / name
                if candidate.is_file():
                    return str(candidate)
        return None

    async def _health(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=1.5, follow_redirects=False) as client:
                response = await client.get(self.endpoint + "/healthz")
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and data.get("backend") in {"laya-mlx", "laya-torch"}:
                        return data
                # A listener exists; never overwrite it even when it is not Laya.
                return {"ok": False, "occupied": True, "error": "Port 8791 is occupied by another service."}
        except httpx.ConnectError:
            return None
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "occupied": True, "error": "The existing service did not return a valid Laya health response."}

    async def status(self) -> dict:
        executable = self._executable()
        health = await self._health()
        managed = self._process is not None and self._process.returncode is None
        installed = executable is not None
        cache = self._cache(executable)
        model_downloaded = bool(list(cache.glob("hub/models--ichenney--laya-browser-v32b/snapshots/*/v32b/*.safetensors")))
        memory_mb = None
        if managed:
            try:
                proc = await asyncio.create_subprocess_exec("ps", "-o", "rss=", "-p", str(self._process.pid),
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                output, _ = await asyncio.wait_for(proc.communicate(), 2)
                memory_mb = round(int(output.strip()) / 1024, 2)
            except (OSError, ValueError, asyncio.TimeoutError):
                pass
        state = "installing" if self._install_task and not self._install_task.done() else \
            "running" if health and health.get("ok") else \
            "starting" if managed else "error" if self._error else "installed" if installed else "not_installed"
        return {"state": state, "installed": installed, "running": bool(health and health.get("ok")),
                "managed": managed, "endpoint": self.endpoint, "pid": self._process.pid if managed else None,
                "model": "ichenney/laya-browser-v32b", "model_downloaded": model_downloaded,
                "revision": LAYA_REVISION, "runtime": health.get("backend") if health else None,
                "memory_mb": memory_mb, "memory_measurement": "service RSS" if memory_mb is not None else None,
                "executable": str(executable) if executable else None,
                "python_available": self._python() is not None, "logs": list(self._logs),
                "error": self._error or (health or {}).get("error"), "health": health,
                "guidance": "Install Python 3.12 to install the optional local AI runtime. The desktop app works without it." if not self._python() else None}

    async def start(self) -> dict:
        async with self._lock:
            existing = await self._health()
            if existing is not None:
                return await self.status()
            if self._process and self._process.returncode is None:
                return await self.status()
            executable = self._executable()
            if executable is None:
                self._error = "Laya is not installed. Use Install local engine first."
                return await self.status()
            self._error = None
            backend = "laya-mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "laya-torch"
            env = dict(os.environ, HF_HOME=str(self._cache(executable)), PYTHONUNBUFFERED="1", LOCALDECIDE_BACKEND=backend)
            self._process = await asyncio.create_subprocess_exec(str(executable), "serve", "--host", "127.0.0.1", "--port", "8791",
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            self._started_at = time.time()
            self._reader_task = asyncio.create_task(self._read_logs(self._process))
            for _ in range(20):
                if self._process.returncode is not None:
                    self._error = "Laya exited during startup. Check its installation log."
                    break
                if await self._health():
                    break
                await asyncio.sleep(.1)
            return await self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._process and self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), 5)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            self._process = None
            if self._reader_task:
                await self._reader_task
                self._reader_task = None
            # A service started by another program belongs to that program and is never killed.
            return await self.status()

    async def install(self) -> dict:
        async with self._lock:
            if self._install_task and not self._install_task.done():
                return await self.status()
            if self._executable() and not self._error:
                return await self.status()
            python = self._python()
            if python is None:
                self._error = "Python 3.12 or 3.13 is required for optional Laya installation. Install Python, then retry."
                return await self.status()
            self._error = None
            self._logs.clear()
            self._install_task = asyncio.create_task(self._install(python))
            return await self.status()

    async def _install(self, python: str) -> None:
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
            self.models_dir.chmod(0o700)
            await self._command([python, "-m", "venv", str(self.runtime_dir)])
            extra = "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "torch"
            await self._command([str(self.runtime_dir / "bin/pip"), "install",
                f"laya-browser-agent[{extra}] @ git+https://github.com/ChenneyZhuang/laya-browser-agent.git@{LAYA_REVISION}"])
            await self._command([str(self.runtime_dir / "bin/localdecide"), "doctor"])
            self._logs.append("Installation and real inference check completed.")
        except Exception as error:
            self._error = str(error)[:400]
        finally:
            self._install_process = None

    async def _command(self, command: list[str]) -> None:
        env = dict(os.environ, HF_HOME=str(self._cache()), PIP_CACHE_DIR=str(self.models_dir / "pip-cache"),
                   PYTHONUNBUFFERED="1", LOCALDECIDE_BACKEND="laya-mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "laya-torch")
        self._install_process = await asyncio.create_subprocess_exec(*command, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        await self._read_logs(self._install_process)
        code = await self._install_process.wait()
        if code:
            raise RuntimeError(f"Local runtime setup exited with code {code}. Review the installation log and retry.")

    async def _read_logs(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            self._logs.append(line.decode("utf-8", "replace").strip()[:600])

    async def close(self) -> None:
        if self._install_process and self._install_process.returncode is None:
            self._install_process.terminate()
            await self._install_process.wait()
        if self._install_task and not self._install_task.done():
            self._install_task.cancel()
            try:
                await self._install_task
            except asyncio.CancelledError:
                pass
        await self.stop()
