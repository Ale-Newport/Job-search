import asyncio
from types import SimpleNamespace

import pytest

from jobagent.automation import browser as browser_module
from jobagent.automation.browser import BrowserSession
from jobagent.automation.types import HumanRequired, Paused, StaleState


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.closed = False
        self.events = {}

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        self.events[event] = callback

    async def close(self):
        self.closed = True
        self.events["close"]()


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]
        self.events = {}
        self.browser = SimpleNamespace(is_connected=lambda: self.connected, on=lambda *args: None)
        self.connected = True

    def on(self, event, callback):
        self.events[event] = callback

    def set_default_timeout(self, timeout):
        pass

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        self.connected = False
        self.events["close"]()


@pytest.fixture
def launcher(monkeypatch):
    calls = []
    async def launch(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        return FakeContext()
    async def stop():
        driver.stops += 1
    driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=stop, stops=0)
    async def start():
        return driver
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: SimpleNamespace(start=start))
    monkeypatch.setattr(browser_module.platform, "system", lambda: "Darwin")
    return calls, driver


async def test_packaged_default_uses_installed_chrome_and_user_browser_cache(tmp_path, monkeypatch, launcher):
    calls, _ = launcher
    monkeypatch.setattr(browser_module, "system_chrome_available", lambda: True)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    session = BrowserSession(tmp_path)
    result = await session.start()
    assert calls[0]["channel"] == "chrome"
    assert result["channel"] == "chrome"
    assert browser_module.os.environ["PLAYWRIGHT_BROWSERS_PATH"].endswith("Library/Caches/ms-playwright")


async def test_absent_chrome_falls_back_to_shared_chromium_cache(tmp_path, monkeypatch, launcher):
    calls, _ = launcher
    monkeypatch.setattr(browser_module, "system_chrome_available", lambda: False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    await BrowserSession(tmp_path).start()
    assert calls[0]["channel"] is None
    assert "_MEI" not in browser_module.os.environ["PLAYWRIGHT_BROWSERS_PATH"]


async def test_explicit_channel_and_cache_configuration_are_respected(tmp_path, monkeypatch, launcher):
    calls, _ = launcher
    monkeypatch.setattr(browser_module, "system_chrome_available", lambda: True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/explicit/browser-cache")
    await BrowserSession(tmp_path).start(channel=None)
    assert calls[0]["channel"] is None
    assert browser_module.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/explicit/browser-cache"


async def test_browser_startup_error_is_actionable_without_full_launch_arguments(tmp_path, monkeypatch, launcher):
    _, driver = launcher
    monkeypatch.setattr(browser_module, "system_chrome_available", lambda: True)
    async def failed(**kwargs):
        raise RuntimeError("Executable missing\nLaunch arguments contain private-profile-details")
    driver.chromium.launch_persistent_context = failed
    session = BrowserSession(tmp_path)
    with pytest.raises(HumanRequired) as error:
        await session.start()
    assert "Executable missing" in str(error.value)
    assert "Google Chrome" in str(error.value)
    assert "private-profile-details" not in str(error.value)
    assert session.status()["running"] is False


async def test_concurrent_start_launches_only_one_browser(tmp_path, launcher):
    calls, _ = launcher
    session = BrowserSession(tmp_path)
    states = await asyncio.gather(session.start(headless=True), session.start())
    assert len(calls) == 1
    assert all(state["running"] for state in states)
    await session.close()


async def test_closed_browser_relaunches_and_invalidates_approvals(tmp_path, launcher):
    calls, driver = launcher
    session = BrowserSession(tmp_path)
    notices = []
    session.on_disconnect = notices.append
    await session.start(headless=True, channel=None)
    old_context = session.context
    session._snapshots["old-approval"] = ({}, {})
    await old_context.close()
    assert session.status()["running"] is False
    assert session.status()["current_url"] is None
    assert len(notices) == 1
    with pytest.raises(StaleState):
        await session._validate_snapshot("old-approval")
    await session.start()
    assert len(calls) == 2
    assert calls[1]["headless"] is True
    assert calls[1]["channel"] is None
    assert driver.stops == 1
    assert session.context is not old_context
    assert session.status()["last_error"] is None
    # Delayed signals from the previous browser cannot break the replacement.
    old_context.events["close"]()
    assert session.status()["running"] is True
    await session.close()
    assert len(notices) == 1  # Intentional shutdown is not an external failure.


async def test_closed_active_tab_reopens_on_next_start_only(tmp_path, launcher):
    calls, _ = launcher
    session = BrowserSession(tmp_path)
    notices = []
    session.on_disconnect = notices.append
    await session.start()
    old_page = session.page
    context = session.context
    session._snapshots["old"] = ({}, {})
    await old_page.close()
    assert session.page is None
    assert session.context is context
    assert not session._snapshots
    assert len(notices) == 1
    with pytest.raises(HumanRequired, match="Retry Auto Apply"):
        await session.observe()
    assert len(context.pages) == 1
    await session.start()
    assert session.page is not old_page
    assert session.context is context
    assert len(calls) == 1
    await session.close()


async def test_dead_browser_detected_even_without_close_event(tmp_path, launcher):
    calls, _ = launcher
    session = BrowserSession(tmp_path)
    await session.start()
    session.context.connected = False
    assert session.status()["running"] is False
    await session.start()
    assert len(calls) == 2
    assert session.status()["running"] is True
    await session.close()


async def test_browser_recovery_does_not_override_pause(tmp_path, launcher):
    session = BrowserSession(tmp_path)
    await session.start()
    session.pause()
    await session.context.close()
    with pytest.raises(Paused):
        await session.open("https://example.test")
    assert session.status()["running"] is False
    await session.resume()
    await session.start()
    assert session.status()["running"] is True
    await session.close()


async def test_cancelled_start_cleans_driver_and_can_retry(tmp_path, launcher):
    _, driver = launcher
    launch = driver.chromium.launch_persistent_context
    launching = asyncio.Event()
    async def blocked(**kwargs):
        launching.set()
        await asyncio.Event().wait()
    driver.chromium.launch_persistent_context = blocked
    session = BrowserSession(tmp_path)
    task = asyncio.create_task(session.start())
    await launching.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session._playwright is None
    assert session.context is None
    assert driver.stops == 1
    driver.chromium.launch_persistent_context = launch
    assert (await session.start())["running"] is True
    await session.close()


async def test_cleanup_still_stops_driver_if_context_close_fails(tmp_path, launcher):
    _, driver = launcher
    session = BrowserSession(tmp_path)
    await session.start()
    async def failed_close():
        raise RuntimeError("Already disconnected")
    session.context.close = failed_close
    await session.close()
    assert driver.stops == 1
    assert session.context is session.page is session._playwright is None
    await session.start()
    assert session.status()["running"] is True
    await session.close()


async def test_crashed_page_invalidates_state_and_notifies_once(tmp_path, launcher):
    session = BrowserSession(tmp_path)
    notices = []
    session.on_disconnect = notices.append
    await session.start()
    old_page = session.page
    session._snapshots["old"] = ({}, {})
    old_page.events["crash"]()
    assert session.page is None
    assert not session._snapshots
    await old_page.close()
    await session.context.close()
    assert len(notices) == 1
    assert "crashed" in notices[0]
    await session.close()
