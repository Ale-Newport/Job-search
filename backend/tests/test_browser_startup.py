from types import SimpleNamespace

import pytest

from jobagent.automation import browser as browser_module
from jobagent.automation.browser import BrowserSession
from jobagent.automation.types import HumanRequired


@pytest.fixture
def launcher(monkeypatch):
    calls = []
    page = SimpleNamespace(url="about:blank", is_closed=lambda: False)
    context = SimpleNamespace(pages=[page], set_default_timeout=lambda timeout: None)
    async def launch(**kwargs):
        calls.append(kwargs)
        return context
    async def stop():
        pass
    driver = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=stop)
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
