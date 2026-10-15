"""Start the REAL Meridian backend (FastAPI + SQLite) for the extension e2e suite, in its own process.

    .venv/bin/python extension/tests/e2e/backend_launcher.py [--no-bridge] [--no-seed]

Required environment (set by ``backend.RealBackend``; never point it at real data):
    MERIDIAN_DATA_DIR   a temporary data directory
    MERIDIAN_API_TOKEN  the per-launch bearer token of the desktop API (the app token)
    MERIDIAN_PORT       port of the main API (0 = pick a free one)
    MERIDIAN_BRIDGE_PORT  bridge port, ``0`` for an ephemeral one, or LOW-HIGH
Everything else is fixed here: MERIDIAN_TEST=1 (no network discovery, no notifications, no local model start),
an in-memory secret store (the macOS Keychain is never touched; PYTHON_KEYRING_BACKEND makes any accidental use fail),
no scheduler, no worker isolation. The synthetic candidate of ``seed.py`` (typed knowledge layer) is written before
the app serves its first request.

Once the app answers, ONE JSON line is printed on stdout:
    {"event": "ready", "api_port": 1234, "bridge_port": 5678 | null, "data_dir": "..."}
or, when the bridge was requested but this checkout cannot provide it yet:
    {"event": "error", "code": "bridge_unsupported", "message": "..."}  (exit code 3)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("MERIDIAN_TEST", "1")
os.environ.setdefault("MERIDIAN_NO_NOTIFICATIONS", "1")
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
# MERIDIAN_E2E_BACKEND_DIR (mutation testing only): a directory holding another copy of the ``jobagent`` package.
sys.path.insert(0, os.environ.get("MERIDIAN_E2E_BACKEND_DIR") or str(REPO / "backend"))
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402


class MemorySecrets:
    """Same shape as the in-memory vault of the backend tests: no Keychain access."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def emit(**payload) -> None:
    print(json.dumps(payload), flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def bridge_port_of(app) -> int | None:
    """Find the bound bridge port on whatever object ``create_app`` attached (A owns the exact attribute)."""
    bridge = getattr(app.state, "bridge", None)
    if bridge is None:
        return None
    for name in ("port", "bound_port", "actual_port"):
        value = getattr(bridge, name, None)
        if isinstance(value, int) and value > 0:
            return value
    ports = getattr(bridge, "ports", None)
    if isinstance(ports, (list, tuple)) and len(ports) == 1 and isinstance(ports[0], int) and ports[0] > 0:
        return ports[0]
    try:  # uvicorn.Server: servers[0].sockets[0].getsockname()
        server = getattr(bridge, "server", bridge)
        return int(server.servers[0].sockets[0].getsockname()[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


async def serve(app, port: int, want_bridge: bool) -> int:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False, log_config=None)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            await task
            return 1
        await asyncio.sleep(0.05)
    bridge_port = None
    if want_bridge:
        for _ in range(100):
            bridge_port = bridge_port_of(app)
            if bridge_port:
                break
            await asyncio.sleep(0.1)
    emit(event="ready", api_port=port, bridge_port=bridge_port, data_dir=str(app.state.data_dir))
    await task
    return 0


def main() -> int:
    want_bridge = "--no-bridge" not in sys.argv
    from jobagent import main as meridian_main

    parameters = inspect.signature(meridian_main.create_app).parameters
    if want_bridge and "bridge" not in parameters:
        emit(event="error", code="bridge_unsupported",
             message="jobagent.main.create_app has no 'bridge' parameter yet (workstream A has not landed): "
                     "the bridge cannot be started. Run with --no-bridge for backend-only checks.")
        return 3
    kwargs = {"token": os.environ["MERIDIAN_API_TOKEN"], "secret_store": MemorySecrets(), "start_scheduler": False,
              "worker_isolation": False}
    if "bridge" in parameters:
        kwargs["bridge"] = want_bridge
        if want_bridge:
            os.environ.setdefault("MERIDIAN_BRIDGE_PORT", "0")
    app = meridian_main.create_app(Path(os.environ["MERIDIAN_DATA_DIR"]), **kwargs)
    if "--no-seed" not in sys.argv:
        person = os.environ.get("MERIDIAN_E2E_PERSON")
        if person:
            # One of the held-out metamorphic suite's synthetic candidates (backend/tests/fixtures/engine/metamorphic/people.py): the answer
            # engine's own suites know exactly what each of them is entitled to answer, so a scenario can compare a real page with that.
            sys.path.insert(0, str(REPO / "backend" / "tests" / "fixtures" / "engine"))
            from metamorphic import people

            people.seed(app.state.db, people.BY_KEY[person], disclosures=os.environ.get("MERIDIAN_E2E_DISCLOSURES", "use_confirmed"))
        else:
            import seed

            seed.seed_typed(app.state.db)
    port = int(os.environ.get("MERIDIAN_PORT") or 0) or free_port()
    return asyncio.run(serve(app, port, want_bridge))


if __name__ == "__main__":
    sys.exit(main())
