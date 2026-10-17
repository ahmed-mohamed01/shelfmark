"""Offline lifecycle checks for the shared browser."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from shelfmark.bypass import browser_session as session
from shelfmark.bypass import internal_bypasser as bypass


@pytest.fixture
def harness(monkeypatch):
    session._reset_state()
    settings = {"BYPASS_REUSE_BROWSER_SESSION": True}
    monkeypatch.setattr(
        session.app_config, "get", lambda key, default=None: settings.get(key, default)
    )
    monkeypatch.setattr(session, "_arm_timer", Mock())
    monkeypatch.setattr(session, "_session_key", lambda url: ("dns", "proxy"))
    monkeypatch.setattr(bypass, "_stop_ffmpeg_recording", Mock())
    monkeypatch.setattr(bypass, "_start_ffmpeg_recording", Mock())
    page = SimpleNamespace(evaluate=AsyncMock(return_value=1), close=AsyncMock())
    extra = SimpleNamespace(close=AsyncMock())
    driver = SimpleNamespace(main_tab=page, tabs=[page, extra], get=AsyncMock())
    create, close = AsyncMock(return_value=driver), AsyncMock()
    yield settings, driver, create, close
    session._reset_state()


def test_reuse_and_park(harness):
    _, driver, create, close = harness

    async def run():
        assert await session.acquire("https://a.test", create, close) is driver
        await session.release(driver, keep=True, close_fn=close)
        assert await session.acquire("https://b.test", create, close) is driver

    asyncio.run(run())
    driver.tabs[1].close.assert_awaited_once()
    driver.get.assert_awaited_once_with("about:blank")
    assert create.await_count == 1
    assert session.snapshot()["uses"] == 2


@pytest.mark.parametrize("failure", ["uses", "age", "key", "dead", "park", "off", "discard"])
def test_drop(harness, monkeypatch, failure):
    settings, driver, create, close = harness

    async def run():
        await session.acquire("https://a.test", create, close)
        if failure == "park":
            driver.get.side_effect = RuntimeError("closed")
            await session.release(driver, keep=True, close_fn=close)
            return
        if failure == "discard":
            await session.discard(driver, close)
            return
        if failure == "uses":
            settings["BYPASS_SESSION_MAX_JOBS"] = 1
        elif failure == "age":
            monkeypatch.setattr(session, "_created_at", -1000000)
        elif failure == "key":
            monkeypatch.setattr(session, "_session_key", lambda url: ("changed",))
        elif failure == "dead":
            driver.main_tab.evaluate.side_effect = RuntimeError("dead")
        elif failure == "off":
            settings["BYPASS_REUSE_BROWSER_SESSION"] = False
        await session.acquire("https://b.test", create, close)

    asyncio.run(run())
    close.assert_awaited_once_with(driver)


def test_release_without_park_keeps_the_page(harness):
    # page_fetch stays on its light origin page so the next download skips two
    # navigations; extra tabs are still closed and the browser is still kept.
    _, driver, create, close = harness

    async def run():
        await session.acquire("https://a.test", create, close)
        await session.release(driver, keep=True, close_fn=close, park=False)
        assert await session.acquire("https://a.test", create, close) is driver

    asyncio.run(run())
    driver.get.assert_not_awaited()
    driver.tabs[1].close.assert_awaited_once()
    close.assert_not_awaited()


def test_recording_and_idle(harness, monkeypatch):
    settings, driver, create, close = harness
    assert callable(bypass._start_ffmpeg_recording)
    assert callable(bypass._stop_ffmpeg_recording)
    settings["DEBUG"] = True
    monkeypatch.setitem(bypass.DISPLAY, "ffmpeg", None)
    asyncio.run(session.acquire("https://a.test", create, close))
    asyncio.run(session.release(driver, keep=True, close_fn=close))
    asyncio.run(session.acquire("https://b.test", create, close))
    bypass._start_ffmpeg_recording.assert_called_once()
    bypass._stop_ffmpeg_recording.assert_called_once()
    assert session.helper_idle_timeout(180) == 2700
    assert session.helper_idle_timeout(4000) == 4000
    settings["BYPASS_REUSE_BROWSER_SESSION"] = False
    assert session.helper_idle_timeout(180) == 180


def test_egress_fingerprint(monkeypatch):
    monkeypatch.setattr(
        session.network, "get_dns_config", lambda: {"provider": "manual", "servers": ["1.1.1.1"]}
    )
    monkeypatch.setattr(session.network, "get_proxies", lambda url: {})
    assert session._session_key("https://a.test") == session._session_key("https://b.test")


def test_idle_busy_rearms(harness, monkeypatch):
    monkeypatch.setattr(bypass, "LOCKED", SimpleNamespace(acquire=lambda **kwargs: False))
    session._close_if_idle()
    session._arm_timer.assert_called_once()


def test_idle_free_closes(harness, monkeypatch):
    _, driver, create, close = harness
    asyncio.run(session.acquire("https://a.test", create, close))
    asyncio.run(session.release(driver, keep=True, close_fn=close))
    # Pretend the parked browser sat unused well past the idle timeout.
    monkeypatch.setattr(session, "_released_at", session._released_at - 40000)
    monkeypatch.setattr(bypass._CDP_WORKER, "run", lambda coro, timeout: asyncio.run(coro))
    monkeypatch.setattr(bypass, "_close_cdp_driver", close)
    session._close_if_idle()
    close.assert_awaited_once()
    assert session.snapshot()["uses"] == 0


def test_stale_timer_spares_freshly_parked_browser(harness):
    # A timer callback that started before a new job cancelled it can take the
    # lock right after that job parks; it must not close the fresh browser.
    _, driver, create, close = harness
    worker = Mock(side_effect=lambda coro, timeout: asyncio.run(coro))
    with patch.object(bypass._CDP_WORKER, "run", worker):
        asyncio.run(session.acquire("https://a.test", create, close))
        asyncio.run(session.release(driver, keep=True, close_fn=close))
        session._close_if_idle()
    worker.assert_not_called()
    close.assert_not_called()
    assert session.snapshot()["uses"] == 1


def test_stale_timer_spares_in_use_browser(harness):
    # While a job holds the browser, _released_at is 0 and the idle timer
    # must not close it either.
    _, _, create, close = harness
    worker = Mock(side_effect=lambda coro, timeout: asyncio.run(coro))
    with patch.object(bypass._CDP_WORKER, "run", worker):
        asyncio.run(session.acquire("https://a.test", create, close))
        session._close_if_idle()
    worker.assert_not_called()
    close.assert_not_called()


def test_child_reloads_config_before_acquire(monkeypatch):
    session._reset_state()
    settings = {"BYPASS_REUSE_BROWSER_SESSION": False}
    monkeypatch.setattr(
        session.app_config, "get", lambda key, default=None: settings.get(key, default)
    )
    refresh = Mock()
    monkeypatch.setattr(session.app_config, "refresh", refresh)
    create, close = AsyncMock(return_value=object()), AsyncMock()

    monkeypatch.setenv(bypass._BYPASS_CHILD_ENV, "1")
    asyncio.run(session.acquire("https://a.test", create, close))
    refresh.assert_called_once()  # the helper child sees UI settings changes

    monkeypatch.delenv(bypass._BYPASS_CHILD_ENV)
    asyncio.run(session.acquire("https://a.test", create, close))
    refresh.assert_called_once()  # the parent reads settings on demand already
    session._reset_state()


@pytest.mark.parametrize("stage", ["probe", "park"])
def test_cancelled_session_is_dropped(harness, stage):
    _, driver, create, close = harness

    async def run():
        await session.acquire("https://a.test", create, close)
        if stage == "probe":
            driver.main_tab.evaluate.side_effect = asyncio.CancelledError()
            operation = session.acquire("https://a.test", create, close)
        else:
            driver.get.side_effect = asyncio.CancelledError()
            operation = session.release(driver, keep=True, close_fn=close)
        with pytest.raises(asyncio.CancelledError):
            await operation

    asyncio.run(run())
    close.assert_awaited_once_with(driver)
    assert session.snapshot()["uses"] == 0
