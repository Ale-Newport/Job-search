"""Validate frozen sidecar and owned workers with synthetic local data only."""

import asyncio
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import uuid

import httpx


def bridge_ports():
    """Ports of the extension bridge to smoke-test, from MERIDIAN_BRIDGE_PORT: a port, LOW-HIGH, or empty/0 for none.

    The bridge is off under MERIDIAN_TEST, so a packaged bridge smoke test opts in by setting MERIDIAN_BRIDGE_PORT when
    starting this script, for example MERIDIAN_BRIDGE_PORT=47700-47709. Port 0 starts the bridge on an unpredictable
    port: it still runs, but there is nothing fixed to probe, so it is not smoke-tested.
    """
    value = os.environ.get("MERIDIAN_BRIDGE_PORT", "").strip()
    if not value:
        return []
    low, _, high = value.partition("-")
    first, last = int(low), int(high or low)
    assert 0 <= first <= last <= 65535, "MERIDIAN_BRIDGE_PORT must be a port or a LOW-HIGH range"
    return [] if first == 0 else list(range(first, last + 1))


def sidecar_environment(token, data, port):
    environment = dict(
        os.environ,
        MERIDIAN_TEST="1",
        MERIDIAN_API_TOKEN=token,
        MERIDIAN_DATA_DIR=str(data),
        MERIDIAN_PORT=str(port),
        MERIDIAN_NO_NOTIFICATIONS="1",
    )
    # Only MERIDIAN_BRIDGE_PORT enables the bridge here; a forcing flag left in the shell must not.
    environment.pop("MERIDIAN_BRIDGE", None)
    return environment


async def bridge_hello(ports):
    """The packaged bridge answers GET /v1/hello as Meridian on one of the candidate ports (no credentials involved)."""
    async with httpx.AsyncClient(timeout=3) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for candidate in ports:
                try:
                    response = await client.get(f"http://127.0.0.1:{candidate}/v1/hello")
                    hello = response.json()
                except (httpx.HTTPError, ValueError):
                    continue
                if response.status_code == 200 and hello.get("app") == "Meridian" and hello.get("port") == candidate:
                    return hello
            await asyncio.sleep(0.2)
    raise AssertionError(f"The packaged extension bridge did not answer on ports {ports[0]}-{ports[-1]}")


async def main(location_form=False):
    root = Path(__file__).resolve().parent.parent
    data = root / ".local-data" / ("packaged worker qa " + str(uuid.uuid4()))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Quiet, directory=str(root / "backend/tests/browser/fixtures"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    binary = root / "src-tauri/target/release/bundle/macos/Meridian.app/Contents/MacOS/meridian-backend"
    token = str(uuid.uuid4())
    log = open("/private/tmp/meridian-packaged-worker.log", "w")
    parent = subprocess.Popen(
        [str(binary)],
        env=sidecar_environment(token, data, port),
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    max_health = 0
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}/api", headers={"Authorization": "Bearer " + token}, timeout=3
        ) as client:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    if (await client.get("/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Frozen sidecar failed to start")

            async def post(path, payload):
                response = await client.post(path, json=payload)
                response.raise_for_status()
                return response.json()

            smoke_ports = bridge_ports()
            bridge = await bridge_hello(smoke_ports) if smoke_ports else None
            response = await client.patch(
                "/settings",
                json={
                    "automation_paused": False,
                    "scheduler_enabled": False,
                    "browser_engine": "deterministic",
                    "worker_watchdog_seconds": 5,
                },
            )
            response.raise_for_status()
            for category, key, value in [
                ("contact", "email", "packaged@example.test"),
                ("identity", "full_name", "Packaged Test Candidate"),
                ("project", "pipeline", "Built a Python pipeline."),
            ]:
                await post(
                    "/facts",
                    {
                        "category": category,
                        "key": key,
                        "value": value,
                        "source": "synthetic packaged regression fixture",
                        "verification_status": "verified",
                    },
                )
            if location_form:
                response = await client.put('/application-profile/location', json={'value': 'London, UK', 'confirmed': True})
                response.raise_for_status()
            fixture = 'search_location_portal.html' if location_form else 'reliability_wizard.html?identity=packaged&reload=yes'
            job = await post(
                "/jobs",
                {
                    "title": "Software Engineer",
                    "company": "Packaged Fixture",
                    "url": f"http://127.0.0.1:{server.server_port}/{fixture}",
                    "ats": "greenhouse",
                },
            )
            application = await post("/jobs/" + job["id"] + "/prepare", {"mode": "review"})

            async def result(task_id):
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    task = (await client.get("/automation/tasks/" + task_id)).json()
                    if task["state"] == "COMPLETED":
                        return task
                    assert task["state"] != "FAILED", task.get("error")
                    await asyncio.sleep(0.2)
                raise AssertionError("Frozen document worker did not finish")

            for kind in ("cv", "cover_letter"):
                queued = await post("/documents/generate", {"job_id": job["id"], "kind": kind, "approved": True})
                generated = await result(queued["task_id"])
                assert Path(generated["checkpoint"]["version"]["path"]).is_file()
            queued = await post("/applications/" + application["id"] + "/apply", {})
            injected = False
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                started = time.monotonic()
                assert (await client.get("/health")).status_code == 200
                max_health = max(max_health, time.monotonic() - started)
                task = (await client.get("/automation/tasks/" + queued["task_id"])).json()
                state = (await client.get("/automation")).json()
                worker = next((w for w in state.get("workers", []) if w["task_id"] == queued["task_id"]), None)
                if worker and not injected and worker["state"] == "RUNNING":
                    os.kill(worker["process_id"], signal.SIGSTOP)
                    injected = True
                if injected and task["attempts"] >= 2 and task["state"] == "WAITING_FOR_USER":
                    run = (await client.get("/automation/runs/" + task["run_id"])).json()
                    if run.get("status") == "needs_review":
                        break
                assert task["state"] != "FAILED", task.get("error")
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Frozen browser worker recovery exceeded deadline")
            assert injected and max_health < 1
            # Also kill the backend while its browser worker is stopped. A stopped
            # child cannot consume EOF; startup must verify and clean that orphan.
            before_steps = len(run["checkpoint"].get("completed_steps", []))
            state = (await client.get("/automation")).json()
            worker = next(w for w in state["workers"] if w["task_id"] == queued["task_id"])
            os.kill(worker["process_id"], signal.SIGSTOP)
            os.killpg(parent.pid, signal.SIGKILL)
            parent.wait(timeout=5)
            parent = subprocess.Popen(
                [str(binary)],
                env=sidecar_environment(token, data, port),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    task = (await client.get("/automation/tasks/" + queued["task_id"])).json()
                    if task["attempts"] >= 3 and task["state"] == "WAITING_FOR_USER":
                        run = (await client.get("/automation/runs/" + task["run_id"])).json()
                        if run["status"] == "needs_review":
                            break
                    assert task["state"] != "FAILED", task.get("error")
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Frozen backend restart failed to recover the same task")
            assert len(run["checkpoint"].get("completed_steps", [])) >= before_steps
            if location_form:
                assert any(a.get('question') == 'Current location' and a.get('verified') for a in run['checkpoint']['answers'])
                assert not any(step.get('label') == 'Submit application' for step in run['checkpoint'].get('steps', []))
            await post("/applications/" + application["id"] + "/cancel", {})
            report = {
                "result": "passed",
                "packaged_sidecar": True,
                "document_workers": 2,
                "browser_worker_deadlock_recovered": True,
                "backend_hard_restart_recovered": True,
                "completed_steps_preserved": True,
                "attempts": task["attempts"],
                "max_health_seconds": round(max_health, 4),
                "submitted": False,
                "synthetic_data": True,
            }
            if bridge:
                report["extension_bridge"] = {"answered_as": bridge["app"], "port": bridge["port"], "protocol": bridge["bridge"]}
            if location_form:
                report.pop('completed_steps_preserved')
                report['searchable_location_verified'] = True
            report_path = 'docs/location-edits-packaged.json' if location_form else 'docs/upgrade-packaged-workers.json'
            (root / report_path).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
    finally:
        parent.terminate()
        try:
            parent.wait(timeout=20)
        except subprocess.TimeoutExpired:
            parent.kill()
            parent.wait(timeout=5)
        log.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--location', action='store_true', help='Validate the searchable location fixture and its recovery')
    asyncio.run(main(parser.parse_args().location))
