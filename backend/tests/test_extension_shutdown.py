"""Quitting Meridian while assisted pages are long-polling (SHUT-01): the waiting polls are released BEFORE the listener
stops, so the quit is quick and logs no error."""
import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, make_app, posting  # noqa: E402
from jobagent.db import now  # noqa: E402
from jobagent.extension.app import create_bridge_app  # noqa: E402
from jobagent.extension.auth import new_token  # noqa: E402
from jobagent.extension.server import BridgeServer  # noqa: E402
from jobagent.extension.state import close_extension_state  # noqa: E402

ORIGIN = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.fixture
async def listening(tmp_path):
    app = make_app(tmp_path)
    ScriptedModel().install(app)
    client_id = str(uuid.uuid4())
    token, digest = new_token(client_id)
    app.state.db.execute("INSERT INTO extension_clients(id,name,browser,origin,token_hash,version,created_at) "
                         "VALUES(?,?,?,?,?,?,?)", (client_id, "Test browser", "chrome", ORIGIN, digest, "1.0.0", now()))
    server = BridgeServer(create_bridge_app(app), ports=[0])
    assert await server.start() is True
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}
    yield type("Ctx", (), {"app": app, "server": server, "headers": headers,
                            "url": f"http://127.0.0.1:{server.port}"})
    await server.stop()
    await close_extension_state(app)


async def open_session(ctx, client):
    created = await client.post(ctx.url + "/v1/sessions", json=posting(), headers=ctx.headers)
    assert created.status_code == 200, created.text
    sid, cursor = created.json()["session_id"], created.json()["cursor"]
    for _ in range(40):  # the page's own research settles first: from then on a poll really has to wait
        quick = await client.post(ctx.url + "/v1/poll", json={"sessions": [{"session_id": sid, "cursor": cursor}],
                                                              "wait_ms": 250}, headers=ctx.headers)
        entry = quick.json()["sessions"][0]
        cursor = entry["cursor"]
        if not entry["events"]:
            await asyncio.sleep(0.4)
            again = await client.post(ctx.url + "/v1/poll", json={"sessions": [{"session_id": sid, "cursor": cursor}],
                                                                  "wait_ms": 250}, headers=ctx.headers)
            if not again.json()["sessions"][0]["events"]:
                break
    return sid, cursor


async def test_a_long_poll_in_flight_does_not_hold_the_quit_and_logs_no_error(listening, caplog):
    ctx = listening
    async with httpx.AsyncClient(timeout=30) as client:
        sid, cursor = await open_session(ctx, client)
        waiting = asyncio.create_task(client.post(
            ctx.url + "/v1/poll", json={"sessions": [{"session_id": sid, "cursor": cursor}], "wait_ms": 25000},
            headers=ctx.headers))
        await asyncio.sleep(0.6)  # the poll is now waiting on the server
        assert not waiting.done()
        with caplog.at_level(logging.INFO):
            started = time.monotonic()
            await ctx.server.stop()
            took = time.monotonic() - started
        assert took < 2.5, took  # was 3.5 s: uvicorn waited its whole graceful timeout for the poll (the log check is exact)
        answered = await asyncio.wait_for(waiting, 5)  # the page gets an answer, not a reset connection
        assert answered.status_code == 200
        entry = answered.json()["sessions"][0]
        assert entry["status"] == "ok" and entry["events"] == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    texts = " ".join(r.getMessage() for r in caplog.records)
    assert errors == [] and "Cancel" not in texts and "timeout graceful shutdown" not in texts


async def test_every_waiting_page_of_the_browser_is_released_at_once(listening):
    ctx = listening
    async with httpx.AsyncClient(timeout=30) as client:
        first, cursor = await open_session(ctx, client)
        other = posting(url="https://boards.greenhouse.io/acmerobotics/jobs/4002")
        created = (await client.post(ctx.url + "/v1/sessions", json=other, headers=ctx.headers)).json()
        second = created["session_id"]
        await asyncio.sleep(1.5)
        quiet = await client.post(ctx.url + "/v1/poll", json={"sessions": [{"session_id": second, "cursor": 0}],
                                                              "wait_ms": 0}, headers=ctx.headers)
        single = asyncio.create_task(client.post(
            ctx.url + f"/v1/sessions/{second}/poll", json={"cursor": quiet.json()["sessions"][0]["cursor"],
                                                           "wait_ms": 25000}, headers=ctx.headers))
        many = asyncio.create_task(client.post(
            ctx.url + "/v1/poll", json={"sessions": [{"session_id": first, "cursor": cursor}], "wait_ms": 25000},
            headers=ctx.headers))
        await asyncio.sleep(0.6)
        started = time.monotonic()
        await ctx.server.stop()
        assert time.monotonic() - started < 2.5
        assert (await asyncio.wait_for(single, 5)).status_code == 200
        assert (await asyncio.wait_for(many, 5)).status_code == 200


async def test_a_restart_of_the_listener_leaves_polling_working(listening):
    """``BridgeServer.restart()`` (the app's "Try again") stops the listener but not the services: polls work after it."""
    ctx = listening
    async with httpx.AsyncClient(timeout=30) as client:
        sid, cursor = await open_session(ctx, client)
        assert await ctx.server.restart() is True
        url = f"http://127.0.0.1:{ctx.server.port}"
        started = time.monotonic()
        waited = await client.post(url + "/v1/poll", json={"sessions": [{"session_id": sid, "cursor": cursor}],
                                                           "wait_ms": 600}, headers=ctx.headers)
        assert waited.status_code == 200 and time.monotonic() - started >= 0.5  # a real wait again, not a drained one
        assert waited.json()["sessions"][0]["status"] == "ok"
