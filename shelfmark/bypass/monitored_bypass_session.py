"""Branch-only: reuse a solved DDoS-Guard/Cloudflare browser session across bypasses.

Upstream's internal bypasser deliberately closes Chrome after every solve ("nothing
accumulates between requests"), so each Anna's Archive search re-solves the JS challenge
from scratch (~40s). But DDoS-Guard honours a *reused* cleared browser: navigating the
same live Chrome to a new ``/search`` URL serves real results with no re-challenge
(measured 16s -> 3s -> 2s). This module keeps the just-solved driver alive and hands it
back for the next bypass whose routing key matches, so 2nd+ searches skip the solve.

Gated by ``BYPASS_REUSE_BROWSER_SESSION`` (default off): when off, ``acquire``/``release``
are a pure passthrough to create/close (and drop any driver a prior on-window left cached),
so behaviour matches upstream.

Kept out of ``internal_bypasser.py`` (an upstream file) so the upstream diff is just three
swapped call-sites; all reuse policy lives here.

Concurrency: ``get()`` serialises every bypass behind the module-global ``LOCKED``, and the
cached driver is only mutated from the single CDP worker loop, so no lock is needed here.
The one path that can make the *previous* request's cleanup overlap the next request is the
worker's timeout backstop (``_CdpWorker.run`` cancels a wedged coroutine without awaiting its
``finally``). That stays safe because ``_drop_cached`` clears ``_driver`` **synchronously,
before its first await** — so a following ``acquire`` always sees ``None`` and creates fresh
while the cancelled cleanup closes its now-detached driver via the ``driver is not _driver``
branch. (Non-Docker note: outside Docker the browser runs in the gunicorn worker and only the
Docker child wires ``shutdown``; a reused Chrome there is not swept until process exit. Docker
kills the whole helper process group, browser included. This is an experimental, default-off
setting; Docker is the supported path.)

Routing key = ``(domain, DNS fingerprint, proxy)``. Domain covers mirror rotation (.pk->.gl);
the DNS/proxy parts invalidate the cache when egress config changes, because a reused browser
keeps the ``--host-resolver-rules``/proxy baked in at create time. A same-domain, same-config
IP change *within* the reuse window is the residual gap: a reused browser could keep hitting a
now-stale IP and, if that IP answers with a non-challenge page, serve it until the use/age cap
recycles the driver — which is why the window is bounded (``_MAX_USES``/``_MAX_AGE_SECONDS``).

Safety rules (each learned from an adversarial review):
- **Never cache a driver that produced no page.** ``_get`` returns ``""`` *without raising*
  when the bypass fails; caching that browser would poison every later search until recycle.
  ``release`` keeps the driver only on a non-empty, non-cancelled result.
- **Recycle by use-count and age**, not just the child's idle reap — a user searching
  continuously would otherwise keep one Chrome alive for the whole window (memory growth).
- **Drop the cache on any create failure** and **close-before-replace** on a key change.
"""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool

logger = setup_logger("shelfmark.bypass.session_reuse")

# Recycle a reused browser after this many uses or this age, whichever first. Bounds
# memory growth and how long a silently-degraded or TTL-expired session can be reused.
_MAX_USES = 25
_MAX_AGE_SECONDS = 300.0

CreateFn = Callable[[str], "Awaitable[Any]"]
CloseFn = Callable[[Any], "Awaitable[Any]"]

SessionKey = tuple[Any, ...]

_driver: Any | None = None
_key: SessionKey | None = None
_uses: int = 0
_created_at: float = 0.0


def reuse_enabled() -> bool:
    """Whether browser-session reuse is turned on (default off = upstream behaviour)."""
    return coerce_bool(app_config.get("BYPASS_REUSE_BROWSER_SESSION", False))


def _session_key(url: str) -> SessionKey:
    """Route reuse by domain + egress config, so a mirror/DNS/proxy change forces a fresh
    browser rather than reusing one whose baked-in host-resolver-rules/proxy are now stale."""
    host = (urlparse(url).hostname or "").lower()
    dns_fp: Any = ()
    proxy_fp: Any = ()
    try:
        from shelfmark.download import network

        dns = network.get_dns_config()
        provider = str(dns.get("provider") or "").strip().lower()
        servers = dns.get("servers") if provider == "manual" else None
        server_list = tuple(str(s) for s in servers) if isinstance(servers, list) else ()
        dns_fp = (provider, server_list, bool(dns.get("doh_enabled")))
        proxy = network.get_proxies(url)
        proxy_fp = tuple(sorted((str(k), str(v)) for k, v in proxy.items()))
    except Exception:  # noqa: BLE001 - a fingerprint failure must not block a bypass; key on host only
        logger.debug("Could not fingerprint egress for %s; keying reuse on host only", host)
    return (host, dns_fp, proxy_fp)


def _reset_state() -> None:
    global _driver, _key, _uses, _created_at
    _driver, _key, _uses, _created_at = None, None, 0, 0.0


async def _drop_cached(close_fn: CloseFn) -> None:
    """Close and forget the cached driver, swallowing teardown errors.

    Clears ``_driver`` synchronously *before* the first await; the timeout-backstop race
    documented in the module docstring relies on that ordering.
    """
    driver = _driver
    _reset_state()
    if driver is not None:
        with suppress(Exception):
            await close_fn(driver)


def _too_old_or_used() -> bool:
    return _uses >= _MAX_USES or (time.monotonic() - _created_at) >= _MAX_AGE_SECONDS


async def acquire(url: str, create_fn: CreateFn, close_fn: CloseFn) -> Any:
    """Return a driver for ``url`` — the cached one if reusable, else a fresh one.

    With reuse off, always creates a fresh driver (dropping any driver a prior on-window
    left cached, e.g. after a live toggle-off). With reuse on, reuses the cached driver
    while its routing key matches and it is under the use/age cap; otherwise closes any
    stale cached driver and creates + caches a fresh one. A create failure leaves no cached
    driver behind.
    """
    global _driver, _key, _uses, _created_at

    if not reuse_enabled():
        if _driver is not None:
            await _drop_cached(close_fn)
        return await create_fn(url)

    key = _session_key(url)
    if _driver is not None and _key == key and not _too_old_or_used():
        _uses += 1
        logger.info("Reusing cleared browser session for %s (use %d)", key[0], _uses)
        return _driver

    # Key change, cap reached, or nothing cached: close any stale driver first.
    if _driver is not None:
        await _drop_cached(close_fn)

    driver = await create_fn(url)  # may raise; cache stays empty on failure
    _driver, _key, _uses, _created_at = driver, key, 1, time.monotonic()
    return driver


async def release(driver: Any, *, keep: bool, close_fn: CloseFn) -> None:
    """Hand a driver back after a bypass.

    With reuse off, closes it (upstream behaviour). With reuse on: keep the cached driver
    alive only when ``keep`` is true (a real page came back); otherwise close and forget it
    so a challenge-stuck or cancelled browser is never handed to the next search. A driver
    that is no longer the cached one (e.g. detached by the timeout-backstop race) is simply
    closed.
    """
    if not reuse_enabled():
        with suppress(Exception):
            await close_fn(driver)
        return

    if keep and driver is _driver:
        return  # leave it cached for the next matching bypass

    if driver is _driver:
        await _drop_cached(close_fn)
    else:
        with suppress(Exception):
            await close_fn(driver)


async def discard(driver: Any, close_fn: CloseFn) -> None:
    """Force-close a driver mid-bypass (e.g. a CDP error) and drop it from the cache."""
    if driver is _driver:
        await _drop_cached(close_fn)
    else:
        with suppress(Exception):
            await close_fn(driver)


async def shutdown(close_fn: CloseFn) -> None:
    """Close the cached browser on child-process exit so no Chrome is orphaned."""
    if _driver is not None:
        await _drop_cached(close_fn)
