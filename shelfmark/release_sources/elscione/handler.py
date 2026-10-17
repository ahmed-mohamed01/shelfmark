"""Elscione download handler - fetches a file from inside a cleared browser page.

The queue payload is trusted for display only: the path, format and size all
come from the index lookup on ``task.task_id``. That lookup is the only way a
path becomes a URL, so a crafted ``source_id`` (``../``, another host, a
``.bat``) can never reach the network. The mirrors are tried in turn; a
mirror-specific failure falls over, one that says nothing about the mirror
does not.
"""

import re
import time
import uuid
from typing import TYPE_CHECKING

from shelfmark.bypass.page_fetch import PageFetchError, fetch_to_file
from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename
from shelfmark.download.activity import release_activity_grace, request_activity_grace
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.elscione import index, validate

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# Reasons that say the mirror itself is the problem, so the next one gets a
# chance. cancelled, unsupported and too_large would repeat identically on every
# mirror (the user cancelled; no bypasser; the cap is ours), so those do not.
_MIRROR_FALLOVER_REASONS = frozenset({"blocked", "browser", "timeout", "http_status", "short_read"})


def _sanitised_name(name: str) -> str:
    """Make an index file name filesystem-safe for the FILE_ORGANIZATION == "none" rule."""
    return _UNSAFE_FILENAME_CHARS.sub("_", name.strip())


def _remove_attempt(path: Path) -> None:
    """Delete one mirror attempt's temp file and its ``.part`` sibling.

    The fetch layer streams to the ``.part`` sibling and renames it on success,
    but it can also rename a complete file and then raise (a timeout during
    browser cleanup), so both names are always swept.
    """
    path.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".part").unlink(missing_ok=True)


@register_handler("elscione")
class ElscioneHandler(DownloadHandler):
    """Download handler for Elscione search releases."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Fetch the indexed file into TMP_DIR and return the staged path.

        Each mirror attempt streams into its own uniquely named temp file that
        this call owns, and only a file that passed validation is moved onto
        the staging name. A failed attempt therefore leaves nothing behind,
        while a file some other task staged under the same name is never
        touched. The mirrors are tried in turn under one wall-clock budget
        (two per-mirror timeouts).
        """
        staging_path: Path | None = None
        # The temp file of the attempt in flight; this call created it, so it
        # may always be swept, unlike whatever sits at the staging name.
        attempt_path: Path | None = None
        # True only when every mirror was attempted and each answered 404/410:
        # then the file really is gone and the index itself needs a refresh,
        # not just this mirror.
        all_gone = True
        attempted = 0
        # Bound before the try so the except handlers can read it even when the
        # failure happened before the order was built.
        mirror_list: list[str] = []
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            entry = index.get_entry(task.task_id)
            if entry is None:
                # Not a validation problem: the index moved on since the search.
                status_callback("error", "No longer in the Elscione index; search again")
                return None

            if config.get("FILE_ORGANIZATION", "rename") == "none":
                staging_name = _sanitised_name(entry.name)
            else:
                staging_name = build_filename(task.title, task.author, task.year, entry.format)
            staging_path = TMP_DIR / staging_name

            if config.get("BYPASS_REUSE_BROWSER_SESSION", False) is not True:
                # Each download then pays the full 26-60 s challenge solve (spec 3.2).
                logger.info(
                    "Elscione download without browser session reuse; expect a full challenge solve"
                )

            timeout = self._download_timeout()
            # One budget for the whole task: every mirror may use its full
            # timeout, but no more than two of them can run end to end.
            deadline = time.monotonic() + 2 * timeout
            mirror_list = index.download_order()
            status_callback("resolving", "Opening Elscione in browser")
            last_error: PageFetchError | None = None
            for position, base in enumerate(mirror_list):
                if position and time.monotonic() >= deadline:
                    logger.info(
                        "Elscione mirror budget spent after %s; not trying %s",
                        mirror_list[position - 1],
                        base,
                    )
                    break
                # Docker mode has no live progress across the helper boundary, so
                # this stays an indeterminate status (spec 4.7).
                if position:
                    status_callback("downloading", f"Retrying on {index.mirror_host(base)}")
                else:
                    status_callback("downloading", "Downloading in browser")
                attempted += 1
                # Each attempt gets its own file: the fetch layer may rename a
                # complete .part into the destination and still raise, and a
                # completed file from a failed attempt must never be mistaken
                # for the delivered one.
                attempt_path = TMP_DIR / f".elscione-{uuid.uuid4().hex}.{entry.format}"
                # A mirror fetch is one long blocking call with no incremental
                # progress; without a declared grace the orchestrator's stall
                # detector would cancel a slow (or fallover) attempt early.
                request_activity_grace(status_callback, timeout + 60)
                try:
                    result = fetch_to_file(
                        index.build_download_url(entry, base),
                        attempt_path,
                        origin_url=index.origin_url(base),
                        max_bytes=index.max_file_bytes(),
                        timeout=timeout,
                        cancel_flag=cancel_flag,
                        method="fetch",
                    )
                except PageFetchError as exc:
                    _remove_attempt(attempt_path)
                    attempt_path = None
                    if exc.reason not in _MIRROR_FALLOVER_REASONS:
                        # cancelled, unsupported and too_large would hit the same
                        # wall on every mirror; there is nothing to fall over to.
                        raise
                    last_error = exc
                    all_gone = all_gone and exc.reason == "http_status" and exc.status in (404, 410)
                    if position + 1 < len(mirror_list):
                        logger.info(
                            "Elscione mirror %s failed (%s); trying %s",
                            base,
                            exc.reason,
                            mirror_list[position + 1],
                        )
                    continue
                finally:
                    release_activity_grace(status_callback)
                index.set_preferred_mirror(base)
                error = validate.check(attempt_path, entry, result)
                if error is not None:
                    logger.warning("Elscione download failed validation: %s", error)
                    status_callback("error", error)
                    return None
                # The validated file moves onto the staging name only now, so a
                # failed attempt never touches it. Like the other sources, a file
                # already at that name (another task's, same title) is replaced.
                attempt_path.replace(staging_path)
                logger.info(
                    "Elscione download validated: %s (%d bytes)", staging_path.name, result.size
                )
                return str(staging_path)

            # The loop only ever falls out after every mirror failed (or the
            # budget stopped the rest), so the last fetch error is the report.
            if last_error is None:
                last_error = PageFetchError("timeout", "Elscione mirror budget spent")
            logger.info("Elscione download failed on every mirror (%s)", ", ".join(mirror_list))
            self._report_fetch_error(
                last_error,
                status_callback,
                refresh_gone=all_gone and attempted > 0 and attempted == len(mirror_list),
            )
        except PageFetchError as exc:
            # A non-fallover reason (cancelled, unsupported, too_large) lands
            # here without the "every mirror" log: it failed at the first one.
            self._report_fetch_error(
                exc,
                status_callback,
                refresh_gone=all_gone and attempted > 0 and attempted == len(mirror_list),
            )
        except Exception as exc:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                logger.exception("Error downloading from Elscione")
                status_callback("error", str(exc))
        finally:
            if attempt_path is not None:
                # Backstop: an attempt file no branch above already swept.
                _remove_attempt(attempt_path)
        return None

    def _download_timeout(self) -> int:
        return index.coerce_int(config.get("ELSCIONE_DOWNLOAD_TIMEOUT", 300), 300, lo=60, hi=900)

    def _report_fetch_error(
        self,
        exc: PageFetchError,
        status_callback: Callable[[str, str | None], None],
        *,
        refresh_gone: bool = True,
    ) -> None:
        """Map a PageFetchError reason to a user-facing message (spec 5.5).

        ``refresh_gone`` is False when a 404/410 came from only some of the
        mirrors: then another mirror may still serve the file, so the index
        (which is mirror-independent) is not stale and must not be refreshed.
        """
        if exc.reason == "cancelled":
            status_callback("cancelled", "Cancelled")
            return
        if exc.reason == "unsupported":
            message = "Elscione downloads need the internal bypasser"
        elif exc.reason == "blocked":
            message = "Elscione challenge could not be cleared"
        elif exc.reason == "http_status":
            message = f"Elscione returned HTTP {exc.status}"
            if exc.status in (404, 410) and refresh_gone:
                # The file dropped out of the live site but not our index yet.
                index.start_background_refresh()
        elif exc.reason == "too_large":
            message = f"File exceeds the {index.max_file_bytes() // (1024 * 1024)} MiB limit"
        elif exc.reason == "timeout":
            message = f"Timed out after {self._download_timeout()} s"
        elif exc.reason == "short_read":
            message = "Download ended early (short read)"
        else:  # "browser": the fetch machinery failed, not the site
            message = "Browser error while downloading from Elscione"
        logger.warning("Elscione page fetch failed (%s): %s", exc.reason, exc)
        status_callback("error", message)
        return

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled by the orchestrator via the cancel_flag."""
        return False
