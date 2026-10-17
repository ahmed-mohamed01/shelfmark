"""Download a URL from inside a cleared browser page to a local file (fork-only).

Some protected hosts (server.elscione.com: Cloudflare plus Anubis) refuse plain HTTP
file downloads even with every clearance cookie replayed, yet serve the same file to a
``fetch()`` made from a page that cleared the challenge. This module is that operation.

INTERFACE CONTRACT - the public names below are fixed; see docs/dev/elscione-source.md
section 4 for the full behaviour.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import traceback
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

from shelfmark.bypass import BypassCancelledError, cookie_store
from shelfmark.bypass import browser_session as session
from shelfmark.config import env
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool
from shelfmark.download import network

if TYPE_CHECKING:
    from threading import Event

PageFetchReason = Literal[
    "unsupported",
    "blocked",
    "http_status",
    "too_large",
    "timeout",
    "cancelled",
    "browser",
    "short_read",
]
PageFetchMethod = Literal["fetch", "native_then_fetch"]


class PageFetchError(Exception):
    """A page fetch failed; ``reason`` says why, ``status`` carries any HTTP status."""

    def __init__(self, reason: PageFetchReason, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.reason: PageFetchReason = reason
        self.status = status


@dataclass(frozen=True)
class PageFetchResult:
    """What a successful page fetch wrote."""

    path: Path
    size: int
    content_type: str | None
    final_url: str
    method: Literal["fetch", "native"]


def fetch_to_file(
    url: str,
    dest: Path,
    *,
    origin_url: str,
    max_bytes: int,
    timeout: float,
    cancel_flag: Event | None = None,
    method: PageFetchMethod = "fetch",
) -> PageFetchResult:
    """Fetch ``url`` from inside a page cleared on ``origin_url`` and write it to ``dest``.

    Raises PageFetchError on every failure. ``dest`` is only ever created complete: the
    bytes go to a ``.part`` sibling that is renamed on success and deleted on failure.
    """
    from shelfmark.bypass import internal_bypasser as bypass

    started = time.monotonic()
    try:
        _preconditions(url, origin_url, dest)
        logger.info(
            "Page fetch %s via %s (reuse=%s)", urlsplit(url).path, method, session.reuse_enabled()
        )
        if method == "native_then_fetch":
            logger.info("Native browser download is not built; using in-page fetch")
        with bypass.LOCKED:
            if network.host_cooldown_remaining(url) > 0:
                # Dispatch-level guard; hoisting the raise out of the try would
                # need a second LOCKED round-trip, so it stays inline.
                raise PageFetchError("http_status", "Host is rate-limited", status=429)  # noqa: TRY301
            _check_cancel(cancel_flag)
            if env.DOCKERMODE and os.environ.get(bypass._BYPASS_CHILD_ENV) != "1":
                payload = {
                    "op": "page_fetch",
                    "url": url,
                    "origin_url": origin_url,
                    "dest": str(dest),
                    "max_bytes": max_bytes,
                    "timeout": timeout,
                    "method": method,
                    "dns_config": network.get_dns_config(),
                    "result_path": str(
                        Path(tempfile.gettempdir())
                        / f"shelfmark-page-fetch-{uuid.uuid4().hex}.json"
                    ),
                }
                part = dest.with_suffix(dest.suffix + ".part")
                try:
                    result = bypass._BYPASS_HELPER.run(payload, timeout + 30, cancel_flag)
                except BaseException:
                    # A killed helper (cancel, timeout, crash) never runs its own
                    # finally, so the partial file it had been writing is the
                    # parent's to remove.
                    part.unlink(missing_ok=True)
                    raise
                cookie_store.import_store(result.get("cookies"), result.get("user_agents"))
                if not result.get("ok"):
                    # The helper reported a failure; its own cleanup normally removed
                    # the partial file, but nothing is lost by making sure. ``dest``
                    # is left alone: the child only renames into it on success, and
                    # another task may own a file of the same name.
                    part.unlink(missing_ok=True)
                    # Failure exit of the dispatch; an inner function would only
                    # move the cleanup and the raise apart.
                    raise PageFetchError(  # noqa: TRY301
                        _reason(result.get("reason")),
                        "Browser helper fetch failed",
                        status=result.get("status"),
                    )
                size = result.get("size")
                final_url = result.get("final_url")
                if isinstance(size, bool) or not isinstance(size, int) or not final_url:
                    # Malformed envelope guard; belongs beside the value checks.
                    raise PageFetchError(  # noqa: TRY301
                        "browser", "Browser helper returned a malformed result"
                    )
                fetched = PageFetchResult(
                    dest,
                    size,
                    result.get("content_type"),
                    str(final_url),
                    "fetch",
                )
            else:
                fetched = bypass._CDP_WORKER.run(
                    _fetch(url, dest, origin_url, max_bytes, timeout, cancel_flag), timeout
                )
        logger.info(
            "Page fetch done: %d bytes in %.1fs (%s)",
            fetched.size,
            time.monotonic() - started,
            fetched.method,
        )
    except BypassCancelledError:
        error = PageFetchError("cancelled", "Page fetch cancelled")
    except TimeoutError:
        error = PageFetchError("timeout", "Page fetch timed out")
    except PageFetchError as exc:
        error = exc
    except bypass._CDP_OPERATION_ERRORS:
        error = PageFetchError("browser", "Browser operation failed")
    else:
        return fetched
    logger.warning("Page fetch failed: reason=%s status=%s", error.reason, error.status)
    raise error


logger = setup_logger(__name__)
_CHUNK_SIZE = 1024 * 1024
# A typical EPUB is fetched in well under a second, so a coarse poll would dominate it.
_POLL_SECONDS = 0.1
_REASONS = {
    "unsupported",
    "blocked",
    "http_status",
    "too_large",
    "timeout",
    "cancelled",
    "browser",
    "short_read",
}


def _reason(value: Any) -> PageFetchReason:
    return cast("PageFetchReason", value if value in _REASONS else "browser")


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        parsed.hostname,
        parsed.port or {"https": 443, "http": 80}.get(parsed.scheme),
    )


def _preconditions(url: str, origin_url: str, dest: Path) -> None:
    try:
        supported = (
            coerce_bool(app_config.get("USE_CF_BYPASS", False))
            and not coerce_bool(app_config.get("USING_EXTERNAL_BYPASSER", False))
            and _origin(url) == _origin(origin_url)
            and _origin(url)[0] in {"http", "https"}
            and bool(_origin(url)[1])
            and dest.resolve().is_relative_to(Path(env.TMP_DIR).resolve())
        )
    except OSError, ValueError:
        supported = False
    if not supported:
        raise PageFetchError(
            "unsupported",
            "Page fetch requires the internal bypasser, same origin and a temporary destination",
        )


def _check_cancel(cancel_flag: Event | None) -> None:
    if cancel_flag is not None and cancel_flag.is_set():
        raise PageFetchError("cancelled", "Page fetch cancelled")


async def _wait_for_origin_ready(
    page: Any, cancel_flag: Event | None, *, within: float = 60.0
) -> None:
    """Wait until the page is loaded and shows no challenge marker, or raise ``blocked``."""
    deadline = time.monotonic() + within
    while True:
        _check_cancel(cancel_flag)
        state = await page.evaluate(
            "({title: document.title, body: document.body?.innerText || '', ready: document.readyState})"
        )
        if (
            state
            and state.get("ready") == "complete"
            and not any(
                marker in (str(state.get("title", "")) + " " + str(state.get("body", ""))).lower()
                for marker in _challenge_markers()
            )
        ):
            return
        if time.monotonic() >= deadline:
            raise PageFetchError("blocked", "Origin challenge did not clear")
        await asyncio.sleep(0.25)


def _start_script(url: str, token: str, max_bytes: int) -> str:
    # The promise runs independently so cancellation and timeout remain observable.
    # Values enter the script through quoted placeholders (json.dumps output) so
    # no URL or marker can break out into JS.
    return (
        """(() => {
      window.__smFetch ||= {};
      const id = __TOKEN__, cap = __CAP__;
      const ctl = new AbortController();
      const s = window.__smFetch[id] = {received:0,total:null,done:false,error:null,status:null,contentType:null,finalUrl:null,blob:null,ctl};
      (async () => {
        try {
          const r = await fetch(__URL__, {credentials:'same-origin',cache:'no-store',redirect:'follow',signal:ctl.signal});
          s.status=r.status; s.contentType=r.headers.get('content-type'); s.finalUrl=r.url;
          const length=r.headers.get('content-length'); s.total=length === null ? null : Number(length);
          if ([401,403,503].includes(r.status)) throw 'blocked';
          if (r.status !== 200) throw 'http_status';
          if (s.total > cap) throw 'too_large';
          if ((s.contentType || '').toLowerCase().includes('text/html')) throw 'blocked';
          const chunks=[], reader=r.body.getReader();
          while (true) {
            const {done,value}=await reader.read(); if(done) break;
            s.received+=value.byteLength;
            if(s.received > cap) throw 'too_large';
            chunks.push(value);
          }
          s.blob=new Blob(chunks);
          if(s.received <= 65536) {
            const text=(await s.blob.text()).toLowerCase();
            if (__MARKERS__.some(marker => text.includes(marker))) throw 'blocked';
          }
          s.done=true;
        } catch(e) { s.error=typeof e === 'string' ? e : 'browser'; ctl.abort(); }
      })();
      return true;
    })()""".replace("__TOKEN__", json.dumps(token))
        .replace("__CAP__", str(max_bytes))
        .replace("__URL__", json.dumps(url))
        .replace("__MARKERS__", json.dumps(_challenge_markers()))
    )


def _challenge_markers() -> list[str]:
    from shelfmark.bypass.challenge import (
        CLOUDFLARE_INDICATORS,
        DDOS_GUARD_INDICATORS,
        OTHER_CHALLENGE_INDICATORS,
    )

    return [*CLOUDFLARE_INDICATORS, *DDOS_GUARD_INDICATORS, *OTHER_CHALLENGE_INDICATORS]


async def _transfer(
    page: Any, url: str, dest: Path, max_bytes: int, cancel_flag: Event | None
) -> PageFetchResult:
    token = uuid.uuid4().hex
    ref = f"window.__smFetch[{json.dumps(token)}]"
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        begun = time.monotonic()
        await page.evaluate(_start_script(url, token, max_bytes))
        while True:
            _check_cancel(cancel_flag)
            state = await page.evaluate(
                f"(() => {{ const s={ref}; return s && {{received:s.received,total:s.total,done:s.done,error:s.error,status:s.status,contentType:s.contentType,finalUrl:s.finalUrl}}; }})()"
            )
            if not isinstance(state, dict):
                raise PageFetchError("browser", "Fetch state disappeared")
            if state.get("error"):
                raise PageFetchError(
                    _reason(state["error"]), "In-page fetch failed", status=state.get("status")
                )
            if state.get("done"):
                break
            await asyncio.sleep(_POLL_SECONDS)
        received = int(state["received"])
        fetched_at = time.monotonic()
        logger.debug("Page fetch: browser received %d bytes in %.1fs", received, fetched_at - begun)
        if received > max_bytes:
            raise PageFetchError("too_large", "File exceeds size cap")
        written = 0
        with part.open("wb") as output:
            for start in range(0, received, _CHUNK_SIZE):
                _check_cancel(cancel_flag)
                data = await page.evaluate(
                    f"new Promise((resolve,reject) => {{ const reader=new FileReader(); reader.onload=()=>resolve(reader.result); reader.onerror=()=>reject(reader.error); reader.readAsDataURL({ref}.blob.slice({start},{min(start + _CHUNK_SIZE, received)})); }})",
                    await_promise=True,
                )
                _, separator, encoded = str(data).partition(",")
                if not separator:
                    raise PageFetchError("short_read", "Browser returned an invalid data URL")
                chunk = base64.b64decode(encoded, validate=True)
                written += output.write(chunk)
        _check_cancel(cancel_flag)
        if written != received or (
            state.get("total") is not None and written != int(state["total"])
        ):
            raise PageFetchError("short_read", "File byte count differs from response")
        logger.debug("Page fetch: copied to disk in %.1fs", time.monotonic() - fetched_at)
        part.replace(dest)
        return PageFetchResult(
            dest, written, state.get("contentType"), state.get("finalUrl") or url, "fetch"
        )
    finally:
        from shelfmark.bypass import internal_bypasser as bypass

        try:
            with suppress(*bypass._CDP_OPERATION_ERRORS):
                await page.evaluate(
                    f"(() => {{ const s=window.__smFetch?.[{json.dumps(token)}]; if(s) s.ctl.abort(); if(window.__smFetch) delete {ref}; }})()"
                )
        finally:
            part.unlink(missing_ok=True)


async def _fetch(
    url: str, dest: Path, origin_url: str, max_bytes: int, timeout: float, cancel_flag: Event | None
) -> PageFetchResult:
    from shelfmark.bypass import internal_bypasser as bypass

    driver = None
    keep = False
    try:
        async with asyncio.timeout(timeout):
            driver = await session.acquire(
                origin_url, bypass._create_cdp_browser, bypass._close_cdp_driver
            )
            for attempt in range(2):
                try:
                    _check_cancel(cancel_flag)
                    page = driver.main_tab
                    step = time.monotonic()
                    if attempt or _origin(str(page.url)) != _origin(origin_url):
                        page = await driver.get(origin_url)
                        logger.debug("Page fetch: opened origin in %.1fs", time.monotonic() - step)
                        step = time.monotonic()
                        try:
                            # A reused browser still holds clearance, so the page is
                            # usually clear at once; the solver's settle waits would
                            # cost seconds on every download for nothing.
                            await _wait_for_origin_ready(page, cancel_flag, within=3.0)
                        except PageFetchError:
                            logger.debug("Page fetch: origin not clear, running the solver")
                            await bypass._bypass(page, cancel_flag=cancel_flag)
                            await _wait_for_origin_ready(page, cancel_flag)
                        logger.debug("Page fetch: origin ready in %.1fs", time.monotonic() - step)
                        step = time.monotonic()
                    await bypass._extract_cookies_from_cdp(driver, page, origin_url)
                    logger.debug("Page fetch: cookies stored in %.1fs", time.monotonic() - step)
                    step = time.monotonic()
                    result = await _transfer(page, url, dest, max_bytes, cancel_flag)
                    logger.debug("Page fetch: transfer took %.1fs", time.monotonic() - step)
                except PageFetchError as exc:
                    retry = exc.reason == "blocked" and attempt == 0
                    logger.debug("Page fetch retry=%s reason=%s", retry, exc.reason)
                    if not retry:
                        keep = exc.reason in {"http_status", "too_large", "short_read"}
                        raise
                except bypass._CDP_OPERATION_ERRORS as exc:
                    retry = "Execution context was destroyed" in str(exc) and attempt == 0
                    logger.debug("Page fetch retry=%s reason=browser", retry)
                    if not retry:
                        raise PageFetchError("browser", "Browser operation failed") from exc
                else:
                    keep = True
                    return result
    except TimeoutError as exc:
        raise PageFetchError("timeout", "Page fetch timed out") from exc
    finally:
        if driver is not None:
            # Stay on the (light) origin page: the next download from this host then
            # skips both the park and the reopen navigation.
            await session.release(driver, keep=keep, close_fn=bypass._close_cdp_driver, park=False)
    raise PageFetchError("browser", "Fetch ended without a result")


def handle_child_request(request: dict) -> int:
    """Publish one result using the helper's atomic result-file protocol."""
    from shelfmark.bypass import internal_bypasser as bypass

    result_path = Path(str(request["result_path"]))
    payload: dict[str, Any]
    try:
        if isinstance(request.get("dns_config"), dict):
            bypass._apply_parent_dns_config(request["dns_config"])
        cookie_store.clear_cf_cookies()
        result = fetch_to_file(
            str(request["url"]),
            Path(str(request["dest"])),
            origin_url=str(request["origin_url"]),
            max_bytes=int(request["max_bytes"]),
            timeout=float(request["timeout"]),
            method=request.get("method", "fetch"),
        )
        payload = {
            "ok": True,
            "size": result.size,
            "content_type": result.content_type,
            "final_url": result.final_url,
            "method": result.method,
        }
    except PageFetchError as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "reason": exc.reason,
            "status": exc.status,
        }
    except Exception as exc:  # noqa: BLE001 - helper boundary must serialize failures.
        # A malformed request or a CDP-worker crash must not kill the helper
        # without a result file; the parent maps this to a browser error.
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "reason": "browser",
            "traceback": traceback.format_exc(),
        }
    cookies, user_agents = cookie_store.export_store()
    payload.update(cookies=cookies, user_agents=user_agents)
    bypass._publish_result(result_path, payload)
    return 0 if payload["ok"] else 1
