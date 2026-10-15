"""An isolated stack for the UI end-to-end checks that must see the REAL backend: nothing here touches real user data.

  * the real FastAPI backend of Meridian, started by the extension suite's own launcher (extension/tests/e2e) on a temporary
    data directory with MERIDIAN_TEST=1, an in-memory secret store, the loopback bridge on an ephemeral port, and shim
    ``open`` / ``osascript`` / ``pbcopy`` executables first on PATH (the real Meridian.app and the Keychain stay out of reach);
  * a vite dev server on a free port whose ``/api`` proxy points at THAT backend. The config is generated in a temporary
    folder, so nothing depends on the fixed ports 1420 / 8765 and several runs (or other sessions) cannot collide. (The
    pre-bundled dependency cache is vite's default one, shared with any other dev server of this repository: scripts such as
    recovery_e2e.py import ``/node_modules/.vite/deps/...`` by path.)

    async with LiveStack() as stack:
        stack.base_url          # http://127.0.0.1:<vite port>
        stack.backend           # extension.tests.e2e RealBackend (app API client, bridge port, temp database)

Everything is stopped and removed on exit, also when the run is interrupted.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# MERIDIAN_UI_ROOT (mutation testing only): a copy of the frontend folder served instead of the repo's own.
FRONTEND = Path(os.environ.get("MERIDIAN_UI_ROOT") or REPO / "frontend").resolve()
E2E = REPO / "extension" / "tests" / "e2e"
# The token the page itself sends when it is not running inside the desktop shell (frontend/src/api.ts).
DEV_TOKEN = "meridian-development-token"

VITE_CONFIG = """import {{ defineConfig }} from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({{
  root: {root},
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {{
    host: "127.0.0.1",
    port: {port},
    strictPort: true,
    proxy: {{
      "/api": {{
        target: "http://127.0.0.1:{api}",
        changeOrigin: true,
        // The backend only accepts the dev server's own origin (backend/jobagent/main.py ALLOWED_ORIGINS): this server runs on a
        // free port, so its requests are presented as what the repo's dev server (port 1420) would send. Nothing else changes.
        configure: (proxy) => {{
          proxy.on("proxyReq", (request) => {{
            if (request.getHeader("origin")) request.setHeader("origin", "http://127.0.0.1:1420");
          }});
        }},
      }},
    }},
  }},
  envPrefix: ["VITE_", "TAURI_ENV_"],
}});
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def import_real_backend():
    """The extension suite's RealBackend (its modules live next to each other, not in a package)."""
    if str(E2E) not in sys.path:
        sys.path.insert(0, str(E2E))
    import backend  # noqa: PLC0415 - extension/tests/e2e/backend.py

    return backend


class ViteServer:
    """``vite`` with a generated config: the repo's own plugins, an isolated cache, a proxy to the given API port."""

    def __init__(self, api_port: int, work: Path) -> None:
        self.api_port, self.work = api_port, work
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.log_path = work / "vite.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 90.0) -> None:
        import json

        self.work.mkdir(parents=True, exist_ok=True)
        # The generated config imports vite and its plugins by name: resolve them through the repo's node_modules.
        link = self.work / "node_modules"
        if not link.exists():
            link.symlink_to(FRONTEND / "node_modules", target_is_directory=True)
        config = self.work / "vite.config.mjs"
        config.write_text(VITE_CONFIG.format(
            root=json.dumps(str(FRONTEND)), port=self.port, api=self.api_port))
        log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [str(FRONTEND / "node_modules" / ".bin" / "vite"), "--config", str(config)], cwd=str(FRONTEND),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "BROWSER": "none", "NO_COLOR": "1"})
        log.close()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"vite exited with {self.proc.returncode}\n{self.log_tail()}")
            try:
                with urllib.request.urlopen(self.url + "/", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"vite did not answer within {timeout:.0f} s\n{self.log_tail()}")

    def log_tail(self, chars: int = 2000) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-chars:]
        except OSError:
            return ""

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if proc.poll() is not None:
                break
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                continue
        try:  # vite's own children share the group
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class LiveStack:
    """Real backend + vite. ``profile=True`` also loads the synthetic candidate of the extension suite (typed knowledge
    layer, written before the app serves) and ``seed=True`` adds the facts and the approved CV through the real API.
    ``bridge_port`` is MERIDIAN_BRIDGE_PORT: ``"0"`` (ephemeral), a port, or ``LOW-HIGH``."""

    def __init__(self, *, profile: bool = False, seed: bool = False, bridge: bool = True, bridge_port: str = "0") -> None:
        self.profile, self.seed, self.bridge, self.bridge_port = profile, seed, bridge, bridge_port
        self.backend = None
        self.vite: ViteServer | None = None
        self.work: Path | None = None
        self._module = None

    @property
    def base_url(self) -> str:
        assert self.vite is not None, "the stack is not running"
        return self.vite.url

    async def __aenter__(self) -> "LiveStack":
        self._module = import_real_backend()
        self.work = Path(tempfile.mkdtemp(prefix="meridian-ui-live-"))
        (self.work / ".local-data" / "qa").mkdir(parents=True)
        try:
            # The older UI scripts refuse to run unless the workspace is named like a QA one (onboarding_e2e.py): it is, and it lives
            # in this run's own temporary folder, never in the repository's .local-data.
            self.backend = self._module.RealBackend(
                bridge=self.bridge, seed_profile=self.profile, token=DEV_TOKEN, bridge_port=self.bridge_port,
                data_dir=self.work / ".local-data" / "qa")
            await self.backend.start()
            if self.seed:
                await self.backend.seed_via_api()
            self.vite = ViteServer(self.backend.api_port, self.work / "vite")
            await asyncio.to_thread(self.vite.start)
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self.vite is not None:
            await asyncio.to_thread(self.vite.stop)
        if self.backend is not None:
            await self.backend.stop()
        if self._module is not None:
            self._module.cleanup_temp()
        if self.work is not None:
            shutil.rmtree(self.work, ignore_errors=True)
            self.work = None
        self.vite = self.backend = None

    def logs(self) -> str:
        parts = []
        if self.backend is not None:
            parts.append("--- backend ---\n" + self._module.redact(self.backend.log_tail(3000)))
        if self.vite is not None:
            parts.append("--- vite ---\n" + self.vite.log_tail())
        return "\n".join(parts)
