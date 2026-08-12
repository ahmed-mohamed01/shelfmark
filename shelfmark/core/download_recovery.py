"""Sonarr-style download recovery: reconnect to downloads after app restart.

On startup, scans configured download clients for items in Shelfmark's category,
matches them against stale active DB rows, and either resumes polling or
auto-imports completed downloads.

Also provides retry support for interrupted downloads shown in the activity panel.

This is a branch-only module — all recovery logic lives here to minimize
upstream merge conflicts.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, Callable

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, SearchMode
from shelfmark.core.queue import book_queue
from shelfmark.core.utils import is_audiobook as check_audiobook

logger = setup_logger(__name__)

# Injected at register() time
_download_history_service: Any = None
_ws_manager: Any = None

# Prevent double-registration on module reload
_registered: bool = False

# Polling constants for recovery of in-progress client downloads
_RECOVERY_POLL_INTERVAL = 5  # seconds
_RECOVERY_STALL_TIMEOUT = 3600  # 1 hour with no progress change → give up

# Hooks invoked after a successful silent-import recovery, with (task_id, final_path).
# The general recovery path bypasses book_queue's terminal-status hook, so layered
# concerns (e.g. monitored events, monitored_book_download_history) need this hook
# to learn about completion. Branch-only — no Rule #1 conflict.
_recovery_complete_hooks: list[Callable[[str, str], None]] = []


def register_recovery_complete_hook(hook: Callable[[str, str], None]) -> None:
    """Append a callback fired after _recover_completed imports a file.

    Args:
        hook: ``hook(task_id, final_path)`` — task_id from the recovered DB row,
            final_path is the absolute path the file was imported to.
    """
    _recovery_complete_hooks.append(hook)


def _fire_recovery_complete_hooks(task_id: str, final_path: str) -> None:
    for hook in _recovery_complete_hooks:
        try:
            hook(task_id, final_path)
        except Exception as exc:
            logger.warning("Recovery-complete hook failed for %s: %s", task_id, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register(
    download_history_service: Any,
    ws_manager: Any = None,
) -> None:
    """Wire up recovery hooks. Call once during app startup."""
    global _download_history_service, _ws_manager, _registered
    if _registered:
        return
    _registered = True

    _download_history_service = download_history_service
    _ws_manager = ws_manager

    # Persist download_id when the handler receives it from the client
    from shelfmark.download.clients.base_handler import ExternalClientHandler

    ExternalClientHandler._download_id_hook = _on_download_id_available


def startup_recover() -> None:
    """Scan download clients for stale active downloads and reconcile.

    Called once during app startup, after download clients are configured.
    Runs in a background thread so it doesn't block the main startup path.
    """
    if _download_history_service is None:
        return

    thread = threading.Thread(
        target=_startup_recover_sync,
        name="DownloadRecovery",
        daemon=True,
    )
    thread.start()


def retry_interrupted(
    book_id: str,
    *,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    is_admin: bool = False,
) -> tuple[bool, str | None, int]:
    """Retry an interrupted download that is no longer in the in-memory queue.

    Returns (success, error_message, http_status_code).
    """
    if _download_history_service is None:
        return False, "Download history unavailable", 500

    row = _download_history_service.get_by_task_id(book_id)
    if row is None:
        return False, "Download not found", 404

    final_status = str(row.get("final_status") or "").strip().lower()
    if final_status != "active":
        return False, "Download is not in an interrupted state", 409

    # Ownership check
    if not is_admin and not _check_ownership(row, actor_user_id, actor_username):
        return False, "Forbidden", 403

    # Check if the download client still has this item
    download_id = row.get("download_id")
    if download_id and isinstance(download_id, str) and download_id.strip():
        recovery_result = _try_client_recovery_for_retry(row, download_id.strip())
        if recovery_result is not None:
            return recovery_result

    # Fallback: reconstruct task and re-queue as a fresh download
    task = _reconstruct_task_from_history(row)
    if not book_queue.add(task):
        return False, "Download is already in the queue", 409

    logger.info("Re-queued interrupted download from history: %s", book_id)
    _broadcast_status()
    return True, None, 200


def _try_client_recovery_for_retry(
    row: dict,
    download_id: str,
) -> tuple[bool, str | None, int] | None:
    """Check if a download client still has this item and handle accordingly.

    Returns a (success, error, status_code) tuple if the client had the item,
    or None to fall through to the re-queue-from-scratch path.
    """
    from shelfmark.download.clients import get_client, list_configured_clients

    for protocol in list_configured_clients():
        client = get_client(protocol)
        if client is None:
            continue
        try:
            status = client.get_status(download_id)
        except Exception:
            continue

        # Check for "not found" errors — client doesn't have this download
        if hasattr(status, "state"):
            state_str = str(getattr(status, "state", "")).lower()
            msg = str(getattr(status, "message", "") or "").lower()
            if state_str == "error" and "not found" in msg:
                continue

        task_id = row.get("task_id", "")
        if status.complete:
            # Complete — import in background thread (can't block HTTP response)
            thread = threading.Thread(
                target=_recover_completed,
                args=(client, download_id, protocol, row),
                name=f"RecoveryImport-{task_id[:16]}",
                daemon=True,
            )
            thread.start()
        else:
            # In-progress — poll in background thread until complete
            thread = threading.Thread(
                target=_poll_client_until_complete,
                args=(client, download_id, protocol, row),
                name=f"RecoveryPoll-{task_id[:16]}",
                daemon=True,
            )
            thread.start()
        return True, None, 200

    # No client had this download — return None to fall through
    return None


# ---------------------------------------------------------------------------
# Hook callbacks
# ---------------------------------------------------------------------------


def _on_download_id_available(task_id: str, download_id: str, _protocol: str) -> None:
    """Persist the client-side download ID to the DB for restart recovery."""
    if _download_history_service is None:
        return
    try:
        _download_history_service.update_download_id(
            task_id=task_id,
            download_id=download_id,
        )
    except Exception as exc:
        logger.warning("Failed to persist download_id for %s: %s", task_id, exc)


# ---------------------------------------------------------------------------
# Startup recovery (runs in background thread)
# ---------------------------------------------------------------------------


def _startup_recover_sync() -> None:
    """Synchronous recovery scan — runs in background thread.

    Two-phase recovery:
    1. Scan download clients for items matching stale active DB rows (by download_id).
       Completed items are auto-imported; in-progress items are re-queued for polling.
    2. Re-queue any remaining stale rows that weren't found in clients.
       The download handler will attempt a fresh download; if the source cache is gone
       the download will fail normally and the user/auto-search can retry later.
    """
    try:
        stale_rows = _download_history_service.get_stale_active_rows()
        if not stale_rows:
            logger.debug("No stale active downloads to recover")
            return

        logger.info("Found %d stale active download(s), recovering...", len(stale_rows))

        # Build lookup: download_id → row (only rows that have a persisted download_id)
        by_download_id: dict[str, dict] = {}
        rows_without_id: list[dict] = []
        for row in stale_rows:
            dl_id = row.get("download_id")
            if dl_id and isinstance(dl_id, str) and dl_id.strip():
                by_download_id[dl_id.strip().lower()] = row
            else:
                rows_without_id.append(row)

        # Phase 1: scan download clients for items we can reconnect to
        recovered_task_ids: set[str] = set()
        if by_download_id:
            from shelfmark.download.clients import list_configured_clients

            for protocol in list_configured_clients():
                try:
                    recovered_ids = _scan_client_for_recovery(protocol, by_download_id)
                    recovered_task_ids.update(recovered_ids)
                except Exception as exc:
                    logger.warning("Recovery scan failed for %s client: %s", protocol, exc)

        # Phase 2: re-queue anything not recovered from clients
        requeued = 0
        for row in stale_rows:
            task_id = row.get("task_id", "")
            if not task_id or task_id in recovered_task_ids:
                continue
            if book_queue.get_task(task_id) is not None:
                continue
            try:
                task = _reconstruct_task_from_history(row)
                if book_queue.add(task):
                    requeued += 1
                    logger.info("Recovery: re-queued stale download %s (%s)", task_id, task.title)
            except Exception as exc:
                logger.warning("Recovery: failed to re-queue %s: %s", task_id, exc)

        total = len(recovered_task_ids) + requeued
        if total > 0:
            logger.info(
                "Recovery complete: %d from client(s), %d re-queued fresh (of %d stale)",
                len(recovered_task_ids),
                requeued,
                len(stale_rows),
            )
            _broadcast_status()
        else:
            logger.info("No stale downloads needed recovery")

        # Phase 3: verify recently completed downloads still have files on disk
        _verify_completed_downloads()

    except Exception as exc:
        logger.exception("Startup download recovery failed: %s", exc)


def _verify_completed_downloads() -> None:
    """Check recently completed downloads for missing files and re-import or re-queue.

    Queries completed downloads from the last 7 days that have a download_id.
    If the file is missing from the expected path, tries to recover from the
    download client. If the client doesn't have it either, resets to active
    so the next cycle re-downloads.
    """
    if _download_history_service is None:
        return

    try:
        rows = _download_history_service.get_recent_completed_with_download_id(days=7)
    except Exception as exc:
        logger.warning("File verification: failed to query completed downloads: %s", exc)
        return

    if not rows:
        return

    from shelfmark.download.clients import get_client, list_configured_clients

    repaired = 0
    for row in rows:
        download_path = row.get("download_path")
        if not download_path or os.path.exists(download_path):
            continue  # File is present — all good

        task_id = row.get("task_id", "")
        download_id = row.get("download_id", "")
        title = row.get("title") or "Unknown"
        logger.warning(
            "File verification: missing file for completed download %s (%s): %s",
            task_id,
            title,
            download_path,
        )

        # Try to recover from download client
        recovered = False
        for protocol in list_configured_clients():
            client = get_client(protocol)
            if client is None:
                continue
            try:
                status = client.get_status(download_id)
            except Exception:
                continue

            # Skip "not found" errors
            state_str = (
                str(getattr(status, "state", "")).lower() if hasattr(status, "state") else ""
            )
            msg_str = str(getattr(status, "message", "") or "").lower()
            if state_str == "error" and "not found" in msg_str:
                continue

            if status.complete:
                # Client still has the files — re-import
                logger.info("File verification: client has files for %s, re-importing", task_id)
                # Reset to active first so finalize_download works
                _download_history_service.reset_to_active(task_id=task_id)
                _recover_completed(client, download_id, protocol, row)
                recovered = True
                repaired += 1
                break

        if not recovered:
            # Neither library nor client has the file — reset for re-download
            logger.info("File verification: resetting %s (%s) for re-download", task_id, title)
            _download_history_service.reset_to_active(task_id=task_id)
            try:
                task = _reconstruct_task_from_history(row)
                if book_queue.add(task):
                    repaired += 1
            except Exception as exc:
                logger.warning("File verification: failed to re-queue %s: %s", task_id, exc)

    if repaired > 0:
        logger.info("File verification: repaired %d download(s) with missing files", repaired)
        _broadcast_status()


def _scan_client_for_recovery(
    protocol: str,
    by_download_id: dict[str, dict],
) -> set[str]:
    """Scan a single download client and recover matching stale downloads.

    Returns the set of task_ids that were handled (recovered, resumed, or finalized).
    """
    from shelfmark.download.clients import get_client

    client = get_client(protocol)
    if client is None:
        return set()

    # Gather all categories Shelfmark might use
    categories = _get_shelfmark_categories(client)

    recovered_ids: set[str] = set()
    for category in categories:
        client_items = _list_client_downloads(client, category)
        for dl_id, status in client_items:
            normalized_id = dl_id.strip().lower() if isinstance(dl_id, str) else ""
            row = by_download_id.get(normalized_id)
            if row is None:
                continue

            task_id = row.get("task_id", "")
            # Already recovered or back in queue
            if book_queue.get_task(task_id) is not None:
                recovered_ids.add(task_id)
                continue

            try:
                if status.complete:
                    _recover_completed(client, dl_id, protocol, row)
                    recovered_ids.add(task_id)
                elif (
                    hasattr(status, "state")
                    and str(getattr(status, "state", "")).lower() == "error"
                ):
                    _finalize_as_error(row, "Download failed in client")
                    recovered_ids.add(task_id)
                else:
                    # In-progress: poll in a background thread until complete
                    thread = threading.Thread(
                        target=_poll_client_until_complete,
                        args=(client, dl_id, protocol, row),
                        name=f"RecoveryPoll-{task_id[:16]}",
                        daemon=True,
                    )
                    thread.start()
                    recovered_ids.add(task_id)
            except Exception as exc:
                logger.warning(
                    "Failed to recover download %s (client id %s): %s",
                    task_id,
                    dl_id,
                    exc,
                )

    return recovered_ids


# ---------------------------------------------------------------------------
# Client scanning helpers
# ---------------------------------------------------------------------------


def _get_shelfmark_categories(client: Any) -> list[str | None]:
    """Return the list of categories Shelfmark uses for the given client."""
    categories: list[str | None] = [None]  # None = default category

    audiobook_keys = {
        "qbittorrent": "QBITTORRENT_CATEGORY_AUDIOBOOK",
        "transmission": "TRANSMISSION_CATEGORY_AUDIOBOOK",
        "deluge": "DELUGE_CATEGORY_AUDIOBOOK",
        "nzbget": "NZBGET_CATEGORY_AUDIOBOOK",
        "sabnzbd": "SABNZBD_CATEGORY_AUDIOBOOK",
    }
    key = audiobook_keys.get(getattr(client, "name", ""))
    if key:
        ab_cat = config.get(key, "")
        if ab_cat:
            categories.append(ab_cat)

    return categories


def _list_client_downloads(
    client: Any,
    category: str | None,
) -> list[tuple[str, Any]]:
    """List all downloads in a client, optionally filtered by category.

    Returns [(download_id, DownloadStatus), ...].
    Uses client-specific APIs directly since we can't modify the ABC.
    """
    results: list[tuple[str, Any]] = []
    client_name = getattr(client, "name", "")

    try:
        if client_name == "qbittorrent":
            results = _list_qbittorrent(client, category)
        elif client_name == "nzbget":
            results = _list_nzbget(client, category)
        elif client_name == "sabnzbd":
            results = _list_sabnzbd(client, category)
        elif client_name == "transmission":
            results = _list_transmission(client, category)
        elif client_name == "deluge":
            results = _list_deluge(client, category)
        elif client_name == "rtorrent":
            results = _list_rtorrent(client, category)
    except Exception as exc:
        logger.warning("Failed to list downloads from %s: %s", client_name, exc)

    return results


def _list_qbittorrent(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List qBittorrent torrents, optionally filtered by category."""
    # Use the client's internal method to query torrents by category
    params: dict[str, str] = {}
    if category is not None:
        params["category"] = category
    else:
        # Use the default category from config
        default_cat = config.get("QBITTORRENT_CATEGORY", "books")
        if default_cat:
            params["category"] = default_cat

    try:
        client._client.auth_log_in()
        url = f"{client._base_url}/api/v2/torrents/info"
        response = client._client._session.get(url, params=params, timeout=10)
        if response.status_code != 200:
            return []

        torrents = [SimpleNamespace(**t) for t in json.loads(response.text)]
    except Exception as exc:
        logger.debug("qBittorrent category scan failed: %s", exc)
        return []

    results: list[tuple[str, Any]] = []
    for t in torrents:
        torrent_hash = getattr(t, "hash", "")
        if not torrent_hash:
            continue
        try:
            status = client.get_status(torrent_hash)
            results.append((torrent_hash, status))
        except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
            logger.debug("qBittorrent status lookup failed for %s: %s", torrent_hash, exc)

    return results


def _list_nzbget(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List NZBGet downloads."""
    results: list[tuple[str, Any]] = []
    cat_filter = category or config.get("NZBGET_CATEGORY", "Books")

    # Active queue
    try:
        groups = client._call("listgroups")
        for g in groups:
            if cat_filter and g.get("Category") != cat_filter:
                continue
            nzb_id = str(g.get("NZBID", ""))
            if nzb_id:
                try:
                    status = client.get_status(nzb_id)
                    results.append((nzb_id, status))
                except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                    logger.debug("NZBGet queue status lookup failed for %s: %s", nzb_id, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("NZBGet queue scan failed: %s", exc)

    # History
    try:
        history = client._call("history")
        for h in history:
            if cat_filter and h.get("Category") != cat_filter:
                continue
            nzb_id = str(h.get("NZBID", ""))
            if nzb_id:
                try:
                    status = client.get_status(nzb_id)
                    results.append((nzb_id, status))
                except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                    logger.debug("NZBGet history status lookup failed for %s: %s", nzb_id, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("NZBGet history scan failed: %s", exc)

    return results


def _list_sabnzbd(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List SABnzbd downloads."""
    results: list[tuple[str, Any]] = []
    cat_filter = category or config.get("SABNZBD_CATEGORY", "books")

    # Queue
    try:
        queue_data = client._api_call("queue", params={"limit": 100})
        for slot in (queue_data or {}).get("queue", {}).get("slots", []):
            if cat_filter and slot.get("cat") != cat_filter:
                continue
            nzo_id = slot.get("nzo_id", "")
            if nzo_id:
                try:
                    status = client.get_status(nzo_id)
                    results.append((nzo_id, status))
                except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                    logger.debug("SABnzbd queue status lookup failed for %s: %s", nzo_id, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("SABnzbd queue scan failed: %s", exc)

    # History
    try:
        hist_data = client._api_call("history", params={"limit": 100})
        for slot in (hist_data or {}).get("history", {}).get("slots", []):
            if cat_filter and slot.get("category") != cat_filter:
                continue
            nzo_id = slot.get("nzo_id", "")
            if nzo_id:
                try:
                    status = client.get_status(nzo_id)
                    results.append((nzo_id, status))
                except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                    logger.debug("SABnzbd history status lookup failed for %s: %s", nzo_id, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("SABnzbd history scan failed: %s", exc)

    return results


def _list_transmission(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List Transmission torrents."""
    results: list[tuple[str, Any]] = []
    try:
        torrents = client._client.get_torrents()
        for t in torrents:
            torrent_hash = getattr(t, "hashString", "")
            if not torrent_hash:
                continue
            # Filter by label if category specified
            labels = getattr(t, "labels", []) or []
            if category and category not in labels:
                continue
            try:
                status = client.get_status(torrent_hash)
                results.append((torrent_hash, status))
            except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                logger.debug("Transmission status lookup failed for %s: %s", torrent_hash, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("Transmission scan failed: %s", exc)
    return results


def _list_deluge(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List Deluge torrents."""
    results: list[tuple[str, Any]] = []
    try:
        # Deluge uses web UI JSON-RPC
        response = client._call("web.update_ui", [["hash", "label", "state", "progress"], {}])
        torrents = (response or {}).get("result", {}).get("torrents", {})
        for torrent_hash, info in torrents.items():
            if category and info.get("label") != category:
                continue
            try:
                status = client.get_status(torrent_hash)
                results.append((torrent_hash, status))
            except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                logger.debug("Deluge status lookup failed for %s: %s", torrent_hash, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("Deluge scan failed: %s", exc)
    return results


def _list_rtorrent(client: Any, category: str | None) -> list[tuple[str, Any]]:
    """List rTorrent downloads."""
    results: list[tuple[str, Any]] = []
    try:
        torrents = client._client.d.multicall2(
            "",
            "main",
            "d.hash=",
            "d.custom1=",
        )
        for row in torrents:
            torrent_hash = row[0] if len(row) > 0 else ""
            label = row[1] if len(row) > 1 else ""
            if category and label != category:
                continue
            if torrent_hash:
                try:
                    status = client.get_status(torrent_hash)
                    results.append((torrent_hash, status))
                except Exception as exc:  # noqa: BLE001 - per-item, keep scanning
                    logger.debug("rTorrent status lookup failed for %s: %s", torrent_hash, exc)
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("rTorrent scan failed: %s", exc)
    return results


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------


def _recover_completed(
    client: Any,
    download_id: str,
    protocol: str,
    row: dict,
) -> None:
    """Import a download that completed while the app was down."""
    task_id = row.get("task_id", "")

    # Get the file path from the client
    raw_path = client.get_download_path(download_id)
    if not raw_path:
        logger.warning("Recovery: client reports complete but no path for %s", task_id)
        _finalize_as_error(row, "Completed in client but file path unavailable")
        return

    source_path = Path(raw_path)
    if not source_path.exists():
        logger.warning("Recovery: completed path does not exist: %s", raw_path)
        _finalize_as_error(row, f"Completed file not found: {raw_path}")
        return

    # Reconstruct task and run post-processing
    task = _reconstruct_task_from_history(row)
    if protocol == "torrent":
        task.original_download_path = str(source_path)

    logger.info("Recovery: auto-importing completed download %s from %s", task_id, source_path)

    # Run post-processing directly (we're in a background thread).
    # We do NOT add to the in-memory queue to avoid the download loop
    # racing to pick up the task.  Instead we finalize the DB row directly.
    cancel_flag = Event()
    try:
        from shelfmark.download.postprocess.router import post_process_download

        def noop_status(status: str, message: str | None = None) -> None:
            pass

        result = post_process_download(
            source_path,
            task,
            cancel_flag,
            noop_status,
            preserve_source_on_failure=True,
        )

        if result:
            _download_history_service.finalize_download(
                task_id=task_id,
                final_status="complete",
                status_message=None,
                download_path=result,
            )
            logger.info("Recovery: successfully imported %s → %s", task_id, result)
            _fire_recovery_complete_hooks(task_id, result)
        else:
            _download_history_service.finalize_download(
                task_id=task_id,
                final_status="error",
                status_message="Post-processing failed during recovery",
            )
            logger.warning("Recovery: post-processing failed for %s", task_id)

    except Exception as exc:
        logger.exception("Recovery: import failed for %s: %s", task_id, exc)
        _finalize_as_error(row, f"Recovery import failed: {exc}")


def _poll_client_until_complete(
    client: Any,
    download_id: str,
    protocol: str,
    row: dict,
) -> None:
    """Poll a download client until the download completes, then auto-import.

    Used for in-progress downloads found in the client on startup. We poll
    directly rather than going through the handler because the source cache
    (Prowlarr release data) is gone after restart — the handler's
    _resolve_download() would fail.
    """
    task_id = row.get("task_id", "")
    title = row.get("title") or "Unknown"
    last_progress = -1.0
    last_progress_change = time.monotonic()

    logger.info("Recovery: polling %s client for %s (%s) until complete", protocol, task_id, title)

    while True:
        try:
            status = client.get_status(download_id)
        except Exception as exc:
            logger.warning("Recovery: lost contact with client for %s: %s", task_id, exc)
            _finalize_as_error(row, f"Lost contact with download client: {exc}")
            return

        if status.complete:
            logger.info("Recovery: download %s completed in client, importing", task_id)
            _recover_completed(client, download_id, protocol, row)
            _broadcast_status()
            return

        # Check for client-side errors
        state_str = str(getattr(status, "state", "")).lower() if hasattr(status, "state") else ""
        if state_str == "error":
            msg = str(getattr(status, "message", "") or "Download failed in client")
            logger.warning("Recovery: download %s errored in client: %s", task_id, msg)
            _finalize_as_error(row, msg)
            _broadcast_status()
            return

        # Stall detection: if progress hasn't changed for _RECOVERY_STALL_TIMEOUT, give up
        current_progress = getattr(status, "progress", 0.0) or 0.0
        now = time.monotonic()
        if current_progress != last_progress:
            last_progress = current_progress
            last_progress_change = now
        elif now - last_progress_change > _RECOVERY_STALL_TIMEOUT:
            logger.warning(
                "Recovery: download %s stalled (%.1f%% for %ds), giving up",
                task_id,
                current_progress,
                _RECOVERY_STALL_TIMEOUT,
            )
            _finalize_as_error(row, f"Download stalled at {current_progress:.1f}%")
            _broadcast_status()
            return

        time.sleep(_RECOVERY_POLL_INTERVAL)


def _finalize_as_error(row: dict, message: str) -> None:
    """Mark a stale active DB row as error."""
    if _download_history_service is None:
        return
    task_id = row.get("task_id", "")
    try:
        _download_history_service.finalize_download(
            task_id=task_id,
            final_status="error",
            status_message=message,
        )
        logger.info("Recovery: finalized %s as error: %s", task_id, message)
    except Exception as exc:
        logger.warning("Recovery: failed to finalize %s: %s", task_id, exc)


# ---------------------------------------------------------------------------
# Task reconstruction
# ---------------------------------------------------------------------------


def _reconstruct_task_from_history(row: dict) -> DownloadTask:
    """Build a minimal DownloadTask from a persisted download_history row.

    Missing fields (source_url, series info, monitored overrides) are not
    available in the DB. The download handler resolves the actual download
    from source + task_id, so this is sufficient for re-queuing.
    """
    user_id = row.get("user_id")
    content_type = row.get("content_type")
    is_audiobook = check_audiobook(content_type)

    # Resolve output_mode from current config (same logic as queue_release)
    books_output_mode = (
        str(config.get("BOOKS_OUTPUT_MODE", "folder", user_id=user_id) or "folder").strip().lower()
    )
    output_mode = "folder" if is_audiobook else books_output_mode
    output_args: dict[str, Any] = {}

    if output_mode == "email" and not is_audiobook:
        # Private import — acceptable for branch-only code to avoid upstream changes
        from shelfmark.download.orchestrator import _resolve_email_destination

        email_to, _ = _resolve_email_destination(user_id=user_id)
        if email_to:
            output_args = {"to": email_to}

    source = row.get("source") or "unknown"

    task_id = row.get("task_id")
    if not task_id:
        raise ValueError("Cannot reconstruct task: missing task_id in history row")

    return DownloadTask(
        task_id=task_id,
        source=source,
        title=row.get("title") or "Unknown title",
        author=row.get("author"),
        format=row.get("format"),
        size=row.get("size"),
        preview=row.get("preview"),
        content_type=content_type,
        search_mode=SearchMode.UNIVERSAL,
        output_mode=output_mode,
        output_args=output_args,
        user_id=user_id,
        username=row.get("username"),
        request_id=row.get("request_id"),
        priority=-10,  # High priority for retries
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_ownership(
    row: dict,
    actor_user_id: int | None,
    actor_username: str | None,
) -> bool:
    """Check if the actor owns the download based on DB row fields."""
    row_user_id = row.get("user_id")
    if actor_user_id is not None and row_user_id is not None:
        try:
            return int(row_user_id) == actor_user_id
        except TypeError, ValueError:
            pass

    row_username = row.get("username")
    if isinstance(row_username, str) and row_username.strip() and isinstance(actor_username, str):
        return row_username.strip() == actor_username.strip()

    return False


def _broadcast_status() -> None:
    """Broadcast queue status update via WebSocket."""
    if _ws_manager is None:
        return
    try:
        from shelfmark.download.orchestrator import queue_status

        _ws_manager.broadcast_status_update(queue_status())
    except Exception as exc:  # noqa: BLE001 - client libraries raise their own types
        logger.debug("WebSocket status broadcast failed: %s", exc)
