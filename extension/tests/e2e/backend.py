"""Harness side of the real backend: process management, the app API client and synthetic-profile seeding.

The backend always runs ``backend_launcher.py`` in a subprocess on a temporary data directory with MERIDIAN_TEST=1,
an in-memory secret store, an unusable keyring backend and shim ``open``/``osascript``/``pbcopy`` executables first
on PATH, so no code path of the backend can launch the real Meridian app, show a native dialog on the tester's
Mac or touch the Keychain.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

import seed
from fake_llm import MODEL, FakeLLM

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PYTHON = REPO / ".venv" / "bin" / "python"
# The backend imports the whole application before it says "ready". On a machine at a load average of 150+ (other projects' browsers
# running beside the suite) that took longer than the former 60 s and failed two scenarios that had nothing wrong. An event wait: a pass is never slower.
READY_TIMEOUT = 180.0

_TEMP_ROOT: Path | None = None
_TEMP_DIRS: list[Path] = []


def temp_root() -> Path:
    """One private directory per test run; everything the suite creates lives below it and is removed at exit."""
    global _TEMP_ROOT
    if _TEMP_ROOT is None:
        base = os.environ.get("MERIDIAN_E2E_TMP")
        _TEMP_ROOT = Path(tempfile.mkdtemp(prefix="meridian-e2e-", dir=base or None))
    return _TEMP_ROOT


def make_dir(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=temp_root()))
    _TEMP_DIRS.append(path)
    return path


def cleanup_temp() -> None:
    global _TEMP_ROOT
    if _TEMP_ROOT is not None:
        shutil.rmtree(_TEMP_ROOT, ignore_errors=True)
        _TEMP_ROOT = None
    _TEMP_DIRS.clear()


def prune_stale_temp(max_age_hours: float = 24.0) -> list[str]:
    """Remove ``meridian-e2e-*`` directories that an earlier, killed run left behind (and failure-artifact folders older than
    ``max_age_hours``). Only this suite's own prefix is touched, only below the system temp directory, and never a directory
    some running process still points into."""
    base = Path(os.environ.get("MERIDIAN_E2E_TMP") or tempfile.gettempdir())
    try:
        running = subprocess.run(["ps", "-Ao", "command"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    cutoff = time.time() - max_age_hours * 3600
    removed: list[str] = []
    for path in base.glob("meridian-e2e-*"):
        try:
            if not path.is_dir() or path.is_symlink() or path.stat().st_mtime > cutoff or str(path) in running:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
        except OSError:
            continue
    return removed


def write_shims(directory: Path) -> Path:
    """Executables that shadow the macOS helpers the backend may call. They only log; they never act."""
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "calls.log"
    scripts = {
        "open": '#!/bin/sh\necho "open $*" >> "{log}"\nexit 0\n',
        "pbcopy": '#!/bin/sh\necho "pbcopy" >> "{log}"\ncat > /dev/null\nexit 0\n',
        # An unanswered native dialog: blocks like a real one until the app gives up on it or the test ends.
        "osascript": '#!/bin/sh\necho "osascript" >> "{log}"\nexec sleep 300\n',
        "say": '#!/bin/sh\nexit 0\n',
    }
    for name, body in scripts.items():
        path = directory / name
        path.write_text(body.format(log=log))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return directory


class BackendError(RuntimeError):
    pass


class RealBackend:
    """The real FastAPI backend of Meridian on a temp data dir. Use ``async with RealBackend(...) as be``."""

    def __init__(self, *, bridge: bool = True, seed_profile: bool = True, llm: FakeLLM | None = None,
                 extra_env: dict[str, str] | None = None, data_dir: Path | None = None,
                 bridge_port: str = "0", token: str = "e2e-app-token") -> None:
        self.bridge = bridge
        self.seed_profile = seed_profile
        self.llm = llm
        self.extra_env = dict(extra_env or {})
        self.data_dir = data_dir or make_dir("meridian-e2e-data-")
        self.token = token
        self.requested_bridge_port = bridge_port
        self.api_port = 0
        self.bridge_port: int | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.client: httpx.AsyncClient | None = None
        self.shim_dir = write_shims(self.data_dir.parent / (self.data_dir.name + "-shims"))
        self._log_path = self.data_dir.parent / (self.data_dir.name + ".log")
        self._log = None
        self.seeded: dict = {}  # ids created by seed_via_api (document_version_id, ...)

    # ---------------------------------------------------------------------------- lifecycle
    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def bridge_base(self) -> str:
        assert self.bridge_port, "bridge not running"
        return f"http://127.0.0.1:{self.bridge_port}"

    async def __aenter__(self) -> "RealBackend":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    def environment(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("MERIDIAN_") or k in {"MERIDIAN_E2E_TMP", "MERIDIAN_E2E_BACKEND_DIR"}}
        env.update(
            MERIDIAN_TEST="1", MERIDIAN_NO_NOTIFICATIONS="1", MERIDIAN_DATA_DIR=str(self.data_dir),
            MERIDIAN_API_TOKEN=self.token, MERIDIAN_PORT="0", PYTHONDONTWRITEBYTECODE="1",
            PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
            PATH=f"{self.shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            HOME=str(self.data_dir.parent / (self.data_dir.name + "-home")),
        )
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        if self.bridge:
            env["MERIDIAN_BRIDGE_PORT"] = self.requested_bridge_port
        env.update(self.extra_env)
        return env

    async def start(self) -> None:
        if self.proc is not None:
            raise BackendError("backend already running")
        args = [str(PYTHON), str(HERE / "backend_launcher.py")]
        if not self.bridge:
            args.append("--no-bridge")
        if not self.seed_profile or (self.data_dir / "database" / "meridian.sqlite3").exists():
            args.append("--no-seed")
        self._log = open(self._log_path, "ab")
        self.proc = await asyncio.create_subprocess_exec(
            *args, env=self.environment(), cwd=str(self.data_dir), stdout=asyncio.subprocess.PIPE, stderr=self._log,
            start_new_session=True)
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), READY_TIMEOUT)
        except asyncio.TimeoutError as exc:
            await self.kill()
            raise BackendError(f"backend did not report ready within {READY_TIMEOUT:.0f} s\n" + self.log_tail()) from exc
        if not line:
            await self.proc.wait()
            raise BackendError(f"backend exited with {self.proc.returncode} before ready\n" + self.log_tail())
        try:
            info = json.loads(line)
        except ValueError as exc:
            await self.kill()
            raise BackendError("unexpected launcher output: " + line.decode(errors="replace")) from exc
        if info.get("event") == "error":
            await self.proc.wait()
            raise BackendError(f"{info.get('code')}: {info.get('message')}")
        self.api_port = info["api_port"]
        self.bridge_port = info.get("bridge_port")
        self.client = httpx.AsyncClient(base_url=self.api_base, headers={"Authorization": f"Bearer {self.token}"},
                                        timeout=30, trust_env=False)
        asyncio.create_task(self._drain_stdout(self.proc))
        if self.bridge and not self.bridge_port:
            self.bridge_port = await self._discover_bridge_port()

    async def _drain_stdout(self, proc) -> None:
        try:
            while await proc.stdout.readline():
                pass
        except Exception:  # noqa: BLE001 - best effort, the process is going away
            return

    async def _discover_bridge_port(self) -> int | None:
        """Ask the app itself (GET /api/extension, workstream A) when the launcher could not read the port."""
        for _ in range(50):
            try:
                status = (await self.client.get("/api/extension")).json()
                port = (status.get("bridge") or {}).get("port")
                if isinstance(port, int) and port > 0:
                    return port
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.1)
        return None

    def log_tail(self, chars: int = 2500) -> str:
        try:
            return self._log_path.read_text(errors="replace")[-chars:]
        except OSError:
            return ""

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGCONT)  # a frozen (SIGSTOP) process would never see SIGTERM
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        if proc is not None:
            try:  # shim children (the fake "dialog") live in the same process group
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if self._log is not None:
            self._log.close()
            self._log = None

    async def kill(self) -> None:
        """SIGKILL the whole backend (a crash / Quit Meridian): the bridge port goes silent immediately."""
        proc = self.proc
        if proc is not None and proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        await self.stop()

    async def restart(self, *, same_bridge_port: bool = True) -> None:
        """Start again on the SAME data dir (clients and tokens persist) and, by default, the SAME bridge port."""
        previous = self.bridge_port
        await self.stop()
        if same_bridge_port and previous:
            self.requested_bridge_port = str(previous)
        await self.start()

    def freeze(self) -> None:
        """SIGSTOP the whole backend: its sockets keep accepting connections (the kernel completes the handshakes) but nothing
        answers, exactly like an app that is busy or still starting. ``thaw`` continues it."""
        if self.proc is not None and self.proc.returncode is None:
            os.killpg(self.proc.pid, signal.SIGSTOP)

    def thaw(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            os.killpg(self.proc.pid, signal.SIGCONT)

    def shim_calls(self) -> list[str]:
        log = self.shim_dir / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    # ---------------------------------------------------------------------------- app API (per-launch token)
    async def request(self, method: str, path: str, *, expect: int | tuple[int, ...] | None = (200, 201), **kwargs):
        response = await self.client.request(method, "/api" + path, **kwargs)
        if expect is not None:
            allowed = (expect,) if isinstance(expect, int) else expect
            if response.status_code not in allowed:
                raise BackendError(f"{method} /api{path} -> {response.status_code}: {response.text[:400]}")
        return response

    async def get(self, path: str, **kwargs):
        return (await self.request("GET", path, **kwargs)).json()

    async def post(self, path: str, body=None, **kwargs):
        response = await self.request("POST", path, json=body, **kwargs)
        return response.json() if response.content else None

    async def put(self, path: str, body=None, **kwargs):
        return (await self.request("PUT", path, json=body, **kwargs)).json()

    async def patch(self, path: str, body=None, **kwargs):
        return (await self.request("PATCH", path, json=body, **kwargs)).json()

    async def delete(self, path: str, **kwargs):
        return (await self.request("DELETE", path, **kwargs)).json()

    # ---------------------------------------------------------------------------- seeding through the real API
    async def seed_via_api(self) -> dict:
        """What a user does in the app: facts, an approved CV, the writing model. Returns ids for later assertions."""
        for category, key, value in seed.FLAT_FACTS:
            await self.post("/facts", {"category": category, "key": key, "value": value, "source": seed.SOURCE,
                                       "verification_status": "verified"})
        imported = await self.request("POST", "/documents/import",
                                      files={"file": (seed.CV_FILENAME, cv_pdf_bytes(), "application/pdf")})
        documents = (await self.get("/documents"))["items"]
        version_id = next(d["latest_version_id"] for d in documents if d.get("latest_version_id"))
        approved = await self.post(f"/document-versions/{version_id}/approve")
        info = {"document_version_id": version_id, "approved": approved.get("approved"),
                "import_status": imported.status_code}
        await self.profile_answer("full_name", seed.NAME)  # what the Profile page does; also names the user in /v1/status
        if self.llm is not None:
            await self.configure_llm()
        self.seeded = info
        return info

    async def configure_llm(self) -> dict:
        """Point the answer model at the fake loopback provider and run the app's own connection test."""
        assert self.llm is not None
        await self.patch("/settings", {
            "text_provider": "openai-compatible", "text_base_url": self.llm.base_url, "text_model": MODEL,
            "text_retry_attempts": 0, "text_timeout_seconds": 60, "text_quality_review": True,
            "scheduler_enabled": False,
        })
        return await self.post("/ai/answers/test")

    async def settings(self, **values) -> dict:
        return await self.patch("/settings", values)

    async def profile_answer(self, key: str, value: str) -> None:
        """Confirm an Application profile answer, like the Profile page does (sensitive ones included)."""
        await self.put(f"/application-profile/{key}", {"value": value, "confirmed": True})

    async def cv_bytes(self) -> bytes:
        """The approved CV exactly as the app would hand it to a form (download endpoint of the document version)."""
        response = await self.request("GET", f"/document-versions/{self.seeded['document_version_id']}/download")
        return response.content

    async def policy_id(self, subject: str) -> str:
        view = await self.get("/intelligence")
        return next(p["id"] for p in view["policies"] if p["subject"] == subject)

    async def set_policy(self, subject: str, value: str) -> None:
        await self.put(f"/intelligence/policies/{await self.policy_id(subject)}", {"value": value, "confirmed": True})

    # ---------------------------------------------------------------------------- extension management (workstream A)
    async def extension_status(self) -> dict:
        return await self.get("/extension")

    async def pending_pairing(self, timeout: float = 15.0) -> dict:
        """The pairing the extension just started, as the app shows it: {id, browser, code, expires_at, recognized}."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            pending = (await self.extension_status()).get("pending_pairing")
            if pending:
                return pending
            await asyncio.sleep(0.2)
        raise BackendError("no pending pairing appeared in GET /api/extension")

    async def approve_pairing(self, pairing: dict | None = None) -> dict:
        """Approve through the in-app API, i.e. bypass the native code-entry dialog (the user would type the code)."""
        pairing = pairing or await self.pending_pairing()
        return await self.post(f"/extension/pairings/{pairing['id']}/approve")

    async def deny_pairing(self, pairing: dict | None = None) -> dict:
        pairing = pairing or await self.pending_pairing()
        return await self.post(f"/extension/pairings/{pairing['id']}/deny")

    async def clients(self) -> list[dict]:
        return (await self.extension_status()).get("clients") or []

    async def revoke_client(self, client_id: str) -> None:
        await self.delete(f"/extension/clients/{client_id}")

    # ---------------------------------------------------------------------------- tracker
    async def tracker_rows(self) -> list[dict]:
        data = await self.get("/tracker", params={"limit": 200})
        return data.get("items", data.get("rows", [])) if isinstance(data, dict) else data

    def db_write(self, sql: str, params: tuple = ()) -> int:
        """A write into the TEMP database (never the user's), for what the user does in Meridian's own screens and the app API has no call for
        (e.g. an application's status moving to REJECTED when an email arrives). Returns the number of rows changed."""
        import sqlite3

        path = self.data_dir / "database" / "meridian.sqlite3"
        assert path.resolve().is_relative_to(temp_root().resolve()), f"refusing to write outside this run's temp root: {path}"
        connection = sqlite3.connect(str(path), timeout=20)
        try:
            with connection:
                return connection.execute(sql, params).rowcount
        finally:
            connection.close()

    def db_rows(self, sql: str, params: tuple = ()) -> list[dict]:
        """Read-only look into the TEMP database (never the user's): for assertions the API does not expose."""
        import sqlite3

        path = self.data_dir / "database" / "meridian.sqlite3"
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()


def cv_pdf_bytes() -> bytes:
    """A small but real PDF resume (reportlab is a backend dependency)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    lines = [
        seed.NAME, f"{seed.EMAIL} | {seed.PHONE} | London, United Kingdom", "",
        "Software Engineer", "",
        "Education",
        "MSc Artificial Intelligence, Westbridge University (2024-2025), Merit",
        "BSc Computer Science, Northfield University (2021-2024), First Class Honours", "",
        "Experience", "Software Engineering Intern, Brightwave Labs (summer 2023)", seed.EXPERIENCE_STORY, "",
        "Projects", "Warehouse Route Planner", seed.PROJECT_STORY,
    ]
    y = 800
    for line in lines:
        pdf.drawString(56, y, line)
        y -= 18
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def redact(text: str) -> str:
    return re.sub(r"mrd1\.[A-Za-z0-9_.-]+", "mrd1.<redacted>", text)


if __name__ == "__main__":  # pragma: no cover - manual smoke: start, seed, print status, stop
    async def smoke() -> None:
        llm = FakeLLM().start()
        try:
            async with RealBackend(bridge="--bridge" in sys.argv, llm=llm) as backend:
                print(await backend.seed_via_api())
                print(json.dumps((await backend.get("/onboarding")).get("steps", [])[:2], indent=1)[:600])
        finally:
            llm.stop()
            cleanup_temp()

    asyncio.run(smoke())
