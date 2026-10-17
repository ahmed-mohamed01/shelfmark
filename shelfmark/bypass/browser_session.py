"""Keep a cleared browser while its baked-in egress settings remain valid.

All async state changes run on the CDP worker. Detach cached state before awaiting
teardown so a timed-out job cannot close a following job's cached browser.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from shelfmark.config import env
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool
from shelfmark.download import network

logger = setup_logger(__name__)
CreateFn = Callable[[str], Awaitable[Any]]
CloseFn = Callable[[Any], Awaitable[Any]]
_driver: Any = None
_key: tuple[Any, ...] | None = None
_uses = 0
_created_at = 0.0
_released_at = 0.0
_timer: threading.Timer | None = None
_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, TimeoutError)


def _setting(key: str, default: int, minimum: int, maximum: int) -> int:
    value = app_config.get(key, default)
    try:
        number = default if isinstance(value, bool) else int(str(value))
    except TypeError, ValueError, OverflowError:
        number = default
    return max(minimum, min(maximum, number))


def reuse_enabled() -> bool:
    """Whether the cleared browser is parked and reused between jobs."""
    return coerce_bool(app_config.get("BYPASS_REUSE_BROWSER_SESSION", False))


def helper_idle_timeout(base_seconds: float) -> float:
    """How long a parked browser may sit unused; the base when reuse is off."""
    return (
        max(base_seconds, _setting("BYPASS_SESSION_IDLE_TIMEOUT", 2700, 300, 14400))
        if reuse_enabled()
        else base_seconds
    )


def _session_key(url: str) -> tuple[Any, ...]:
    dns = network.get_dns_config()
    provider = str(dns.get("provider") or "").strip().lower()
    servers = dns.get("servers") if provider == "manual" else None
    return (
        provider,
        tuple(str(server) for server in servers) if isinstance(servers, list) else (),
        coerce_bool(dns.get("doh_enabled")),
        tuple(sorted(network.get_proxies(url).items())),
    )


def _cancel_timer() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


def _reset_state() -> None:
    global _driver, _key, _uses, _created_at, _released_at
    _cancel_timer()
    _driver, _key, _uses, _created_at, _released_at = None, None, 0, 0.0, 0.0


async def _drop_cached(close_fn: CloseFn) -> None:
    """Close the cached browser and forget it.

    ``_reset_state()`` runs before the first await because the timeout
    backstop can cancel this cleanup at any await: the captured ``driver``
    is what gets closed, so a cancelled job's cleanup can never close the
    next job's freshly cached browser, and the cache is already empty even
    if the close itself never completes.
    """
    driver = _driver
    _reset_state()
    if driver is not None:
        from shelfmark.bypass import internal_bypasser as bypass

        with suppress(*bypass._CDP_OPERATION_ERRORS):
            await close_fn(driver)


def _arm_timer() -> None:
    global _timer
    _cancel_timer()
    if not env.DOCKERMODE:
        _timer = threading.Timer(helper_idle_timeout(0), _close_if_idle)
        _timer.daemon = True
        _timer.start()


def _close_if_idle() -> None:
    from shelfmark.bypass import internal_bypasser as bypass

    if not bypass.LOCKED.acquire(blocking=False):
        _arm_timer()
        return
    try:
        # A timer callback that started before a new job cancelled it can take
        # the lock right after that job parks; only a browser that has really
        # been parked for the full idle timeout may be closed. A browser in
        # active use has _released_at == 0 (acquire resets it).
        now = time.monotonic()
        if _driver is None or not _released_at or now - _released_at < helper_idle_timeout(0):
            return
        logger.info(
            "Closing idle browser session after %.0f min",
            (now - _released_at) / 60,
        )
        bypass._CDP_WORKER.run(shutdown(bypass._close_cdp_driver), 30)
    except _ERRORS:
        logger.warning("Could not close idle browser session", exc_info=True)
    finally:
        bypass.LOCKED.release()


def snapshot() -> dict[str, Any]:
    """Cache statistics for the settings page: uses, age, idle time, key."""
    now = time.monotonic()
    return {
        "uses": _uses,
        "age": now - _created_at if _driver is not None else 0,
        "idle": now - _released_at if _released_at else 0,
        "key": _key,
    }


async def acquire(url: str, create_fn: CreateFn, close_fn: CloseFn) -> Any:
    """Return a cleared browser for ``url``, reusing the parked one when valid.

    The parked browser is recycled (not reused) once its egress fingerprint
    changes or its job-count/age/liveness limits are exceeded.
    """
    global _driver, _key, _uses, _created_at, _released_at
    from shelfmark.bypass import internal_bypasser as bypass

    if os.environ.get(bypass._BYPASS_CHILD_ENV) == "1":
        # The helper child caches app_config from its first read, and it is a
        # long-lived process: without this, a reuse toggle or limit change made
        # in the parent's UI would never reach the child. refresh() coalesces,
        # so the call is cheap for every acquire.
        app_config.refresh()
    _cancel_timer()
    _released_at = 0.0
    if not reuse_enabled():
        await _drop_cached(close_fn)
        return await create_fn(url)
    key = _session_key(url)
    max_jobs = _setting("BYPASS_SESSION_MAX_JOBS", 50, 1, 1000)
    reason = "none"
    if _driver is not None:
        if key != _key:
            reason = "key-changed"
        elif _uses >= max_jobs:
            reason = "recycle-uses"
        elif (
            time.monotonic() - _created_at
            >= _setting("BYPASS_SESSION_MAX_AGE_HOURS", 4, 1, 48) * 3600
        ):
            reason = "recycle-age"
        else:
            try:
                await asyncio.wait_for(_driver.main_tab.evaluate("1"), 5)
            except asyncio.CancelledError:
                await _drop_cached(close_fn)
                raise
            except bypass._CDP_OPERATION_ERRORS:
                logger.warning("Browser session liveness probe failed")
                reason = "dead"
            else:
                _uses += 1
                logger.info(
                    "Reusing cleared browser (use %d/%d, age %.0f min)",
                    _uses,
                    max_jobs,
                    (time.monotonic() - _created_at) / 60,
                )
                if coerce_bool(app_config.get("DEBUG", False)) and not bypass.DISPLAY.get("ffmpeg"):
                    bypass._start_ffmpeg_recording(os.environ.get("DISPLAY", ":0"))
                return _driver
        await _drop_cached(close_fn)
    logger.info("Starting fresh browser (reason: %s)", reason)
    driver = await create_fn(url)
    _driver, _key, _uses, _created_at = driver, key, 1, time.monotonic()
    return driver


async def release(driver: Any, *, keep: bool, close_fn: CloseFn, park: bool = True) -> None:
    """Park the browser on about:blank for the next job, or close it.

    Parking fails (or ``keep`` is false) means the browser is discarded: a
    parked browser must always be in a clean, predictable state. ``park=False``
    leaves the main tab where it is, for callers whose page is light and that
    reopen the same origin next time (page_fetch): the blank-page round trip
    would cost two navigations per download for nothing.
    """
    global _released_at
    from shelfmark.bypass import internal_bypasser as bypass

    if keep and reuse_enabled() and driver is _driver:
        try:
            main = driver.main_tab
            for tab in driver.tabs:
                if tab is not main:
                    await tab.close()
            if park:
                await driver.get("about:blank")
            bypass._stop_ffmpeg_recording()
            logger.debug("Parked browser%s", " on about:blank" if park else " on its page")
            _released_at = time.monotonic()
            _arm_timer()
        except asyncio.CancelledError:
            await discard(driver, close_fn)
            raise
        except bypass._CDP_OPERATION_ERRORS:
            logger.warning("Could not park browser session; dropping it")
        else:
            return
    await discard(driver, close_fn)


async def discard(driver: Any, close_fn: CloseFn) -> None:
    """Close ``driver`` and drop it from the cache when it is the cached one."""
    from shelfmark.bypass import internal_bypasser as bypass

    if driver is _driver:
        await _drop_cached(close_fn)
    else:
        with suppress(*bypass._CDP_OPERATION_ERRORS):
            await close_fn(driver)


async def shutdown(close_fn: CloseFn) -> None:
    """Close whatever browser is parked; the helper calls this before exiting."""
    await _drop_cached(close_fn)
