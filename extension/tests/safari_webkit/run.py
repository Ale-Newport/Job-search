#!/usr/bin/env python3
"""Run the Safari-like harness (``harness.swift``) against a real Meridian backend on a temporary data directory.

    .venv/bin/python extension/tests/safari_webkit/run.py [fixture.html] [seconds] [--options]
    WK_STEPS=steps.json .venv/bin/python extension/tests/safari_webkit/run.py local_assist.html 120   # focus fields, press keys

What it does, end to end and on this Mac only: builds the ``safari`` variant of the extension (with the bridge port, the panel's shadow root
and a console tracer changed for the test), starts the real backend (temp data dir, in-memory secrets, no Keychain) on loopback port 47690
with a copy of the backend that also accepts the harness's ``webkit-extension://`` origin, serves a fixture page, opens it in WebKit with the
extension loaded, presses "Connect", approves the pairing through the app API, and prints every state of the panel and the chip. With
``MERIDIAN_LOCAL_ENGINES=1`` in the environment the backend may meet the REAL Laya and Ollama of this Mac (start them first).
The installed Meridian.app listens on 47615: this never touches it (its own port is 47690).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "extension/tests/e2e"))
from backend import RealBackend  # noqa: E402
from servers import FixtureServers  # noqa: E402

HERE = Path(__file__).resolve().parent
PORT = "47690"
LOG_PORT = 47699


def build(work: Path) -> Path:
    out = work / "ext"
    subprocess.run(["node", str(REPO / "extension/build.mjs"), "--variant=safari", f"--out={out}"], check=True, capture_output=True,
                   cwd=REPO / "extension")
    content = (out / "content.js").read_text()
    assert 'attachShadow({ mode: "closed" })' in content
    (out / "content.js").write_text(content.replace('attachShadow({ mode: "closed" })', 'attachShadow({ mode: "open" })'))
    background = (out / "background.js").read_text()
    old = "console.info(`[meridian] ${label} ${Object.entries(clean).map(([k, v]) => `${k}=${v}`).join(\" \")}`);"
    assert old in background, "the tracer of shared/trace.ts changed"
    background = background.replace(
        old, "{ const m = `[meridian] ${label} ${Object.entries(clean).map(([k, v]) => `${k}=${v}`).join(\" \")}`; console.info(m); "
             f"fetch(\"http://127.0.0.1:{LOG_PORT}/log\", {{ method: \"POST\", body: m }}).catch(() => {{}}); }}")
    (out / "background.js").write_text(background)
    for name in ("background.js", "options.js", "content.js"):
        path = out / name
        path.write_text(re.sub(r"47615, 47616, 47617, 47618, 47619, 47620, 47621, 47622, 47623, 47624", PORT, path.read_text()))
    return out


def patched_backend(work: Path) -> Path:
    """A copy of the backend whose origin check also accepts WebKit's own scheme (the harness cannot be Safari's)."""
    target = work / "bk"
    shutil.copytree(REPO / "backend" / "jobagent", target / "jobagent", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "migrations").symlink_to(REPO / "migrations")  # the copy finds its migrations beside its own folder
    pairing = target / "jobagent/extension/pairing.py"
    text = pairing.read_text()
    assert "(chrome|safari-web)-extension" in text
    text = text.replace("(chrome|safari-web)-extension", "(chrome|safari-web|webkit)-extension")
    text = text.replace('"safari" if origin.startswith("safari-web-extension://")',
                        '"safari" if origin.startswith(("safari-web-extension://", "webkit-extension://"))')
    pairing.write_text(text)
    return target


class Log(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        print("LOG", self.rfile.read(length).decode(errors="replace")[:400], flush=True)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, *args):
        return


async def main() -> None:
    page = next((a for a in sys.argv[1:] if a.endswith(".html")), "greenhouse_apply.html")
    seconds = next((a for a in sys.argv[1:] if a.isdigit()), "60")
    options = "--options" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="meridian-safari-webkit-"))
    try:
        harness = work / "harness"
        subprocess.run(["swiftc", "-O", str(HERE / "harness.swift"), "-o", str(harness)], check=True)
        ext = build(work)
        server = HTTPServer(("127.0.0.1", LOG_PORT), Log)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        fixtures = FixtureServers()
        env = {"MERIDIAN_E2E_BACKEND_DIR": str(patched_backend(work))}
        if os.environ.get("MERIDIAN_LOCAL_ENGINES"):
            env["MERIDIAN_LOCAL_ENGINES"] = "1"
        async with RealBackend(bridge_port=PORT, extra_env=env) as backend:
            print("BACKEND bridge", backend.bridge_port, flush=True)
            process = await asyncio.create_subprocess_exec(
                str(harness), str(ext), f"{fixtures.top}/{page}", seconds, *(["options"] if options else []),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            approved = False

            async def approve():
                nonlocal approved
                while True:
                    try:
                        pending = (await backend.extension_status()).get("pending_pairing")
                        if pending and not approved:
                            await asyncio.sleep(2)
                            await backend.approve_pairing(pending)
                            approved = True
                            print("APPROVED the pairing through the app API", flush=True)
                    except Exception as error:  # noqa: BLE001
                        print("approver:", error, flush=True)
                    await asyncio.sleep(0.7)

            task = asyncio.create_task(approve())
            async for line in process.stdout:
                print(line.decode().rstrip(), flush=True)
            await process.wait()
            task.cancel()
            print("CLIENTS", [(c["browser"], c["label"], c["stats"]) for c in await backend.clients()], flush=True)
        server.shutdown()
        fixtures.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
