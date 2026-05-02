"""Monitored download handling: queue integration and history recording.

Uses the terminal status hook to record download history without modifying
the core orchestrator module. This module handles:
- Recording successful downloads to monitored_book_download_history
- Recording failed downloads to monitored_book_attempt_history
- Scheduled auto-search triggers (called after batch sync in monitored_routes.py)
"""

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.monitored_release_scoring import pre_process_releases
from shelfmark.core.monitored_utils import normalize_content_type as _normalize_content_type, parse_float_safe as _parse_float_safe
from shelfmark.core.queue import book_queue
from shelfmark.release_sources import get_source_display_name

logger = setup_logger(__name__)

# MonitoredDB handle injected at startup
_user_db: Any = None

# Guard to prevent double-registration when main.py is reloaded in tests
_hooks_registered: bool = False

# Pending releases for retry logic: key = "entity_id:provider:provider_book_id:content_type"
_pending_releases: Dict[str, "PendingDownload"] = {}
_pending_lock = threading.Lock()

# Deferred history inserts: task_id → insert kwargs (waiting for final_path)
_deferred_history: Dict[str, Dict[str, Any]] = {}
_deferred_lock = threading.Lock()


@dataclass
class PendingDownload:
    """Tracks pending releases for a monitored book download with retry support."""
    releases: List[Dict[str, Any]]
    user_id: int
    entity_id: int
    provider: str
    provider_book_id: str
    content_type: str
    destination_override: Optional[str] = None
    file_organization_override: Optional[str] = None
    template_override: Optional[str] = None
    series_name: Optional[str] = None
    series_position: Optional[float] = None
    current_source_id: Optional[str] = None
    attempts: int = 0
    post_process_retries: int = 0  # retries of the *same* release for post-proc failures

# Post-processing error types that should trigger a retry of the same release
# rather than skipping to the next one. These are transient filesystem/network errors.
_POST_PROCESS_ERROR_TYPES = frozenset({
    "PermissionError", "OSError", "IOError", "FileNotFoundError",
    "IsADirectoryError", "NotADirectoryError", "TimeoutError",
    "UnknownFailure",
})
_MAX_POST_PROCESS_RETRIES = 2


def _pending_key(entity_id: int, provider: str, provider_book_id: str, content_type: str) -> str:
    """Generate key for pending releases dict."""
    return f"{entity_id}:{provider}:{provider_book_id}:{content_type}"


def _persist_pending(key: str, pending: PendingDownload) -> None:
    """Write pending releases to DB for restart recovery."""
    if _user_db is None:
        return
    try:
        _user_db.upsert_pending_releases(
            pending_key=key,
            release_data_json=json.dumps(pending.releases),
            user_id=pending.user_id,
            entity_id=pending.entity_id,
            provider=pending.provider,
            provider_book_id=pending.provider_book_id,
            content_type=pending.content_type,
            destination_override=pending.destination_override,
            file_organization_override=pending.file_organization_override,
            template_override=pending.template_override,
            series_name=pending.series_name,
            series_position=pending.series_position,
            current_source_id=pending.current_source_id,
            attempts=pending.attempts,
            post_process_retries=pending.post_process_retries,
        )
    except Exception as exc:
        logger.warning("Failed to persist pending releases for %s: %s", key, exc)


def _delete_pending_from_db(key: str) -> None:
    """Remove pending releases from DB."""
    if _user_db is None:
        return
    try:
        _user_db.delete_pending_releases(key)
    except Exception as exc:
        logger.warning("Failed to delete pending releases for %s: %s", key, exc)


def load_pending_releases_from_db() -> None:
    """Restore pending releases from DB on startup. Call after set_monitored_db()."""
    if _user_db is None:
        return
    try:
        rows = _user_db.load_all_pending_releases()
        restored = 0
        with _pending_lock:
            for row in rows:
                key = row.get("pending_key", "")
                if not key or key in _pending_releases:
                    continue
                try:
                    releases = json.loads(row.get("release_data", "[]"))
                except (json.JSONDecodeError, TypeError):
                    releases = []
                if not releases:
                    # Empty releases list — clean up stale DB row
                    _delete_pending_from_db(key)
                    continue
                _pending_releases[key] = PendingDownload(
                    releases=releases,
                    user_id=row.get("user_id") or 0,
                    entity_id=row.get("entity_id") or 0,
                    provider=row.get("provider") or "",
                    provider_book_id=row.get("provider_book_id") or "",
                    content_type=row.get("content_type") or "ebook",
                    destination_override=row.get("destination_override"),
                    file_organization_override=row.get("file_organization_override"),
                    template_override=row.get("template_override"),
                    series_name=row.get("series_name"),
                    series_position=row.get("series_position"),
                    current_source_id=row.get("current_source_id"),
                    attempts=row.get("attempts") or 0,
                    post_process_retries=row.get("post_process_retries") or 0,
                )
                restored += 1
        if restored:
            logger.info("Restored %d pending release group(s) from database", restored)
    except Exception as exc:
        logger.warning("Failed to load pending releases from DB: %s", exc)


def _infer_monitored_match_content_type(*, row: dict[str, Any], user_id: int | None) -> str | None:
    """Infer monitored file row content type (ebook/audiobook) from row fields."""
    file_type = str(row.get("file_type") or "").strip().lower()
    ext = str(row.get("ext") or "").strip().lower()
    if file_type in {"ebook", "audiobook"}:
        return file_type

    token_candidates = [file_type.lstrip("."), ext.lstrip(".")]
    token_candidates = [token for token in token_candidates if token]
    if not token_candidates:
        return None

    try:
        from shelfmark.core.monitored_files import resolve_monitored_format_preferences

        ebook_formats, audiobook_formats = resolve_monitored_format_preferences(user_id=user_id)
    except Exception:
        return None

    for token in token_candidates:
        if token in audiobook_formats:
            return "audiobook"
        if token in ebook_formats:
            return "ebook"
    return None


def set_monitored_db(monitored_db: Any) -> None:
    """Inject MonitoredDB dependency for monitored download history recording."""
    global _user_db
    _user_db = monitored_db


# =============================================================================
# Hook Registration
# =============================================================================


def register_hooks() -> None:
    """Register monitored download hooks. Call during app startup."""
    global _hooks_registered
    if _hooks_registered:
        logger.debug("Monitored download hooks already registered, skipping")
        return
    _hooks_registered = True

    # Use late-binding: capture the upstream hook in a mutable cell so that
    # if main.py is reloaded and re-registers its hook, we always call the
    # latest version exactly once — no chain duplication.
    upstream_hook_cell: list = [None]

    def _meta_hook(book_id, status, task):
        _on_download_terminal(book_id, status, task)
        upstream = upstream_hook_cell[0]
        if upstream is not None:
            upstream(book_id, status, task)

    def _capturing_set_hook(hook):
        # Replace (not chain) the upstream hook — prevents double-calling on reload
        upstream_hook_cell[0] = hook

    book_queue.set_terminal_status_hook = _capturing_set_hook
    book_queue._terminal_status_hook = _meta_hook

    # Patch update_download_path to flush deferred history inserts.
    # The terminal hook fires before the final path is available; the path is
    # set via update_download_path moments later in the orchestrator.
    _orig_update_path = book_queue.update_download_path

    def _patched_update_path(task_id: str, download_path: str) -> None:
        _orig_update_path(task_id, download_path)
        _flush_deferred_history(task_id, download_path)

    book_queue.update_download_path = _patched_update_path

    logger.info("Monitored download hooks registered")


def _on_download_terminal(book_id: str, status: QueueStatus, task: DownloadTask) -> None:
    """Hook called when a download reaches terminal status (COMPLETE, ERROR, etc.)."""
    try:
        if status == QueueStatus.COMPLETE:
            _record_download_history(task)
            _clear_pending(task)
        elif status == QueueStatus.ERROR:
            try:
                _record_attempt_failure(task)
            except Exception as e:
                logger.warning("Failed to record monitored attempt failure for %s: %s", book_id, e)
            _try_next_release(task)
        # CANCELLED status: clear pending, no retry
        elif status == QueueStatus.CANCELLED:
            _clear_pending(task)
    except Exception as e:
        logger.warning("Failed to record monitored download history for %s: %s", book_id, e)

    try:
        _notify_download_terminal(status, task)
    except Exception as e:
        logger.warning("Failed to send monitored download notification for %s: %s", book_id, e)


def _is_monitored_download(task: DownloadTask) -> bool:
    """Check if a download task originated from a monitored entity."""
    history_context = task.output_args.get("history_context") if isinstance(task.output_args, dict) else None
    return isinstance(history_context, dict) and history_context.get("entity_id") is not None


def _notify_download_terminal(status: QueueStatus, task: DownloadTask) -> None:
    """Send Apprise notification for monitored download completion/failure.

    Only fires for monitored downloads — regular downloads are already
    notified by the upstream hook in main.py.
    """
    if not _is_monitored_download(task):
        return

    from shelfmark.core.notifications import (
        NotificationContext, NotificationEvent, notify_admin, notify_user,
    )

    if status == QueueStatus.COMPLETE:
        event = NotificationEvent.DOWNLOAD_COMPLETE
    elif status == QueueStatus.ERROR:
        event = NotificationEvent.DOWNLOAD_FAILED
    else:
        return

    # Admin notifications are already sent by the upstream hook in main.py
    # (_record_download_terminal_snapshot → _notify_admin_for_terminal_download_status).
    # This hook only adds the per-user notification for monitored downloads.
    user_id = getattr(task, "user_id", None)
    if user_id is None:
        return

    context = NotificationContext(
        event=event,
        title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
        author=str(getattr(task, "author", "Unknown author") or "Unknown author"),
        content_type=getattr(task, "content_type", None),
        format=getattr(task, "format", None),
        source=getattr(task, "source", None),
        error_message=(
            str(getattr(task, "status_message", "") or "")
            if event == NotificationEvent.DOWNLOAD_FAILED else None
        ),
    )
    try:
        notify_user(int(user_id), event, context)
    except Exception:
        logger.warning("Failed to send user notification for monitored download: %s (user_id=%s)", task.task_id, user_id)


def _upsert_file_match_from_history(
    kwargs: Dict[str, Any],
    final_path: str,
    content_type: str | None = None,
) -> None:
    """Register a downloaded file in monitored_book_files for immediate availability."""
    if _user_db is None or not final_path:
        return
    try:
        from pathlib import Path as _Path
        dl_path = _Path(final_path)
        entity_id = kwargs.get("entity_id")
        provider = kwargs.get("provider") or ""
        provider_book_id = kwargs.get("provider_book_id") or ""
        user_ids = kwargs.get("user_ids")
        if not entity_id or not provider or not provider_book_id or not user_ids:
            return
        ct = _normalize_content_type(content_type) if content_type else None
        file_type = "audiobook" if ct == "audiobook" else "ebook"
        _user_db.upsert_monitored_book_file(
            user_ids=user_ids,
            entity_id=int(entity_id),
            provider=provider,
            provider_book_id=provider_book_id,
            path=final_path,
            ext=dl_path.suffix.lower().lstrip(".") if dl_path.suffix else None,
            file_type=file_type,
            size_bytes=None,
            mtime=None,
            confidence=1.0,
            match_reason="download",
            source="download",
        )
    except Exception as exc:
        logger.warning("Failed to upsert file match after download: %s", exc)


def _record_download_history(task: DownloadTask) -> None:
    """Record successful download to monitored_book_download_history.

    If task.download_path is not yet set (hook fired before orchestrator sets
    it), stages the insert in _deferred_history keyed by task_id. The patched
    update_download_path will flush it with the real path shortly after.
    """
    if _user_db is None:
        return

    history_context = task.output_args.get("history_context") if isinstance(task.output_args, dict) else None
    if not isinstance(history_context, dict):
        return

    entity_id = history_context.get("entity_id")
    provider = str(history_context.get("provider") or "").strip()
    provider_book_id = str(history_context.get("provider_book_id") or "").strip()
    user_id = task.user_id

    if entity_id is None or not provider or not provider_book_id or user_id is None:
        return

    previous = _user_db.get_monitored_book_file_match(
        user_ids=[int(user_id)],
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
    )

    overwrite_path = None
    if isinstance(previous, dict):
        previous_content_type = _infer_monitored_match_content_type(
            row=previous,
            user_id=int(user_id),
        )
        current_content_type = _normalize_content_type(task.content_type)
        if previous_content_type != current_content_type:
            previous = None

    if isinstance(previous, dict):
        previous_path = previous.get("path")
        if isinstance(previous_path, str) and previous_path.strip():
            overwrite_path = previous_path.strip()

    match_score = _parse_float_safe(history_context.get("match_score"))
    downloaded_filename = str(history_context.get("downloaded_filename") or "").strip() or None

    kwargs: Dict[str, Any] = dict(
        user_ids=[int(user_id)],
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
        downloaded_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source=str(task.source or ""),
        source_display_name=get_source_display_name(task.source),
        title_after_rename=str(task.title or "").strip() or None,
        match_score=match_score,
        downloaded_filename=downloaded_filename,
        overwritten_path=overwrite_path,
    )

    if task.download_path:
        final_path = str(task.download_path).strip()
        kwargs["final_path"] = final_path
        _user_db.insert_monitored_book_download_history(**kwargs)
        _upsert_file_match_from_history(kwargs, final_path, content_type=task.content_type)
        logger.debug(
            "Recorded monitored download history: entity_id=%s provider=%s book_id=%s",
            entity_id, provider, provider_book_id,
        )
    else:
        # Store content_type so the deferred flush path can use it
        kwargs["_content_type"] = task.content_type
        # Path not yet available; defer until update_download_path is called
        with _deferred_lock:
            _deferred_history[task.task_id] = kwargs
        logger.debug(
            "Deferred monitored download history: task_id=%s entity_id=%s",
            task.task_id, entity_id,
        )


def _flush_deferred_history(task_id: str, final_path: str) -> None:
    """Complete a deferred history insert with the now-known final_path."""
    with _deferred_lock:
        kwargs = _deferred_history.pop(task_id, None)
    if kwargs is None or _user_db is None:
        return
    clean_path = str(final_path or "").strip()
    content_type = kwargs.pop("_content_type", None)
    kwargs["final_path"] = clean_path
    _user_db.insert_monitored_book_download_history(**kwargs)
    # Also register the file match (mirrors the immediate path in _record_download_history)
    _upsert_file_match_from_history(kwargs, clean_path, content_type=content_type)
    logger.debug(
        "Flushed deferred monitored download history: task_id=%s path=%s",
        task_id, final_path,
    )


def _record_attempt_failure(task: DownloadTask, *, error_message: Optional[str] = None) -> None:
    """Record failed download attempt to monitored_book_attempt_history."""
    if _user_db is None or not isinstance(task.output_args, dict):
        return

    history_context = task.output_args.get("history_context")
    if not isinstance(history_context, dict):
        return

    entity_id = history_context.get("entity_id")
    provider = str(history_context.get("provider") or "").strip()
    provider_book_id = str(history_context.get("provider_book_id") or "").strip()
    user_id = task.user_id

    if entity_id is None or not provider or not provider_book_id or user_id is None:
        return

    content_type = _normalize_content_type(task.content_type)
    match_score = _parse_float_safe(history_context.get("match_score"))
    failure_text = (error_message or task.status_message or "").strip() or None

    _user_db.insert_monitored_book_attempt_history(
        user_ids=[int(user_id)],
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
        content_type=content_type,
        attempted_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        status="download_failed",
        source=str(task.source or "") or None,
        source_id=str(task.task_id or "") or None,
        release_title=str(history_context.get("release_title") or "") or None,
        match_score=match_score,
        error_message=failure_text,
    )

    logger.debug(
        "Recorded monitored attempt failure: entity_id=%s provider=%s book_id=%s",
        entity_id, provider, provider_book_id
    )


# =============================================================================
# Download Processor: Single Entry Point with Auto-Retry
# =============================================================================


def process_monitored_book(
    releases: List[Dict[str, Any]],
    *,
    user_id: int,
    entity_id: int,
    provider: str,
    provider_book_id: str,
    content_type: str = "ebook",
    min_match_score: Optional[float] = None,
    destination_override: Optional[str] = None,
    file_organization_override: Optional[str] = None,
    template_override: Optional[str] = None,
    series_name: Optional[str] = None,
    series_position: Optional[float] = None,
) -> Tuple[bool, str]:
    """Process releases for a monitored book: pre-process, queue best, auto-retry on failure.
    
    This is the main entry point for monitored book downloads. It:
    1. Pre-processes releases (filters by date, score, failed history)
    2. Queues the best release
    3. Stores remaining releases for automatic retry on failure
    
    When the download completes:
    - Success: clears pending, records history
    - Failure: records attempt, auto-queues next release
    - Cancelled: clears pending
    
    Args:
        releases: Raw releases from search
        user_id: Current user ID
        entity_id: Monitored entity ID
        provider: Book provider (e.g., 'hardcover')
        provider_book_id: Provider's book ID
        content_type: 'ebook' or 'audiobook'
        min_match_score: Minimum match score cutoff (0.0-1.0)
        destination_override: Override destination path
        file_organization_override: Override file organization
        template_override: Override naming template
    
    Returns:
        Tuple of (queued, message). queued=True means first release was queued.
        Returns (False, "Already in queue") if book is already being processed.
    """
    # Normalize content_type to match the key format used by _get_pending_key_from_task
    content_type = _normalize_content_type(content_type)
    # Check if already pending/in-queue
    key = _pending_key(entity_id, provider, provider_book_id, content_type)
    with _pending_lock:
        if key in _pending_releases:
            return False, "Already in queue"

    # Pre-process releases
    valid_releases, error = pre_process_releases(
        releases,
        user_db=_user_db,
        user_id=user_id,
        entity_id=entity_id,
        provider=provider,
        provider_book_id=provider_book_id,
        content_type=content_type,
        min_match_score=min_match_score,
    )

    if error or not valid_releases:
        return False, error or "No valid releases"

    # Store pending releases for retry (re-check under lock to prevent TOCTOU race)
    with _pending_lock:
        if key in _pending_releases:
            return False, "Already in queue"
        pending = PendingDownload(
            releases=valid_releases,
            user_id=user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            content_type=content_type,
            destination_override=destination_override,
            file_organization_override=file_organization_override,
            template_override=template_override,
            series_name=series_name,
            series_position=series_position,
        )
        _pending_releases[key] = pending
        _persist_pending(key, pending)

    # Queue first release
    return _queue_next_from_pending(key)


def _queue_next_from_pending(key: str) -> Tuple[bool, str]:
    """Queue the next release from pending list. Returns (success, message).

    Iterates through remaining releases until one is successfully queued
    or the list is exhausted.
    """
    from shelfmark.download import orchestrator as download_orchestrator

    while True:
        with _pending_lock:
            pending = _pending_releases.get(key)
            if not pending or not pending.releases:
                _pending_releases.pop(key, None)
                _delete_pending_from_db(key)
                return False, "No more releases to try"

            # Take next release
            release = pending.releases.pop(0)
            pending.current_source_id = str(release.get("source_id", ""))
            pending.attempts += 1
            pending.post_process_retries = 0  # reset for new release

            # Snapshot fields we need outside the lock
            entity_id = pending.entity_id
            provider = pending.provider
            provider_book_id = pending.provider_book_id
            destination_override = pending.destination_override
            file_organization_override = pending.file_organization_override
            template_override = pending.template_override
            content_type = pending.content_type
            series_name = pending.series_name
            series_position = pending.series_position
            user_id = pending.user_id
            remaining = len(pending.releases)

            # Persist updated state to DB while still holding the lock
            _persist_pending(key, pending)

        # Enrich with monitored context
        release["monitored_entity_id"] = entity_id
        release["monitored_book_provider"] = provider
        release["monitored_book_provider_id"] = provider_book_id
        release["destination_override"] = destination_override
        release["file_organization_override"] = file_organization_override
        release["template_override"] = template_override
        release["content_type"] = content_type
        if series_name:
            release.setdefault("series_name", series_name)
        if series_position is not None:
            release.setdefault("series_position", series_position)

        # Queue via orchestrator
        success, error_msg = download_orchestrator.queue_release(release, user_id=user_id)

        if success:
            title = release.get("title") or release.get("display_title") or "Unknown"
            score = release.get("_match_score", 0)
            return True, f"Queued: {title} (score: {score:.0f}%, {remaining} fallbacks)"

        # Immediate queue failure — loop to try next release
        logger.warning("Queue failed for %s: %s, trying next", release.get("source_id", ""), error_msg)


def _get_pending_key_from_task(task: DownloadTask) -> Optional[str]:
    """Extract pending key from task's history context."""
    if not isinstance(task.output_args, dict):
        return None
    history_context = task.output_args.get("history_context")
    if not isinstance(history_context, dict):
        return None
    
    entity_id = history_context.get("entity_id")
    provider = str(history_context.get("provider") or "").strip()
    provider_book_id = str(history_context.get("provider_book_id") or "").strip()
    content_type = _normalize_content_type(task.content_type)

    if entity_id is None or not provider or not provider_book_id:
        return None
    
    return _pending_key(int(entity_id), provider, provider_book_id, content_type)


def _clear_pending(task: DownloadTask) -> None:
    """Clear pending releases for a task (called on success/cancel)."""
    key = _get_pending_key_from_task(task)
    if key:
        with _pending_lock:
            _pending_releases.pop(key, None)
            _delete_pending_from_db(key)
        logger.debug("Cleared pending releases for %s", key)


def _try_next_release(task: DownloadTask) -> None:
    """Try next release from pending list (called on failure).

    If the failure was a post-processing error (file downloaded successfully but
    copy/move failed), retry the *same* release up to _MAX_POST_PROCESS_RETRIES
    times before falling through to the next release. This avoids wasting bandwidth
    re-downloading when the issue is a transient disk/network error.
    """
    key = _get_pending_key_from_task(task)
    if not key:
        return

    # Check if this was a post-processing failure that should be retried
    error_type = getattr(task, "last_error_type", None) or ""
    staged_path = getattr(task, "staged_path", None)
    if staged_path and error_type in _POST_PROCESS_ERROR_TYPES:
        retry_num = 0
        should_retry = False
        with _pending_lock:
            pending = _pending_releases.get(key)
            if pending is not None and pending.post_process_retries < _MAX_POST_PROCESS_RETRIES:
                pending.post_process_retries += 1
                retry_num = pending.post_process_retries
                should_retry = True
                _persist_pending(key, pending)

        if should_retry:
            logger.info(
                "Post-processing failed for %s (retry %d/%d), re-queuing same release",
                key, retry_num, _MAX_POST_PROCESS_RETRIES,
            )
            from shelfmark.download.orchestrator import retry_download
            success, error = retry_download(task.task_id)
            if success:
                return
            logger.warning("Failed to retry post-processing for %s: %s", key, error)
            # Fall through to next release

    remaining = 0
    exhausted: Optional[PendingDownload] = None
    with _pending_lock:
        pending = _pending_releases.get(key)
        if not pending or not pending.releases:
            exhausted = _pending_releases.pop(key, None)
            if exhausted:
                _delete_pending_from_db(key)
        else:
            remaining = len(pending.releases)
            # Reset post_process_retries for the new release
            pending.post_process_retries = 0

    if exhausted is not None:
        logger.info("No more fallback releases for %s after %d attempts", key, exhausted.attempts)
        if _user_db is not None:
            try:
                _user_db.set_monitored_book_search_status(
                    user_ids=[exhausted.user_id] if exhausted.user_id else [],
                    entity_id=exhausted.entity_id,
                    provider=exhausted.provider,
                    provider_book_id=exhausted.provider_book_id,
                    content_type=exhausted.content_type,
                    status="download_failed",
                    searched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
            except Exception:
                pass
        return

    if remaining == 0:
        # Key was removed by another thread between checks
        return

    logger.info("Download failed, trying next release for %s (%d remaining)", key, remaining)
    success, msg = _queue_next_from_pending(key)
    if success:
        logger.info("Queued fallback: %s", msg)
    else:
        logger.warning("Failed to queue fallback for %s: %s", key, msg)


# =============================================================================
# Attempt Recording
# =============================================================================


def write_monitored_book_attempt(
    user_db: Any,
    *,
    user_id: int | None,
    entity_id: int,
    provider: str,
    provider_book_id: str,
    content_type: str,
    status: str,
    attempted_at: str | None = None,
    source: str | None = None,
    source_id: str | None = None,
    release_title: str | None = None,
    match_score: float | None = None,
    error_message: str | None = None,
) -> str:
    """Record a monitored book search attempt and update its search status.

    Writes to both set_monitored_book_search_status and
    insert_monitored_book_attempt_history in a single call.

    Args:
        user_db: Database access object.
        user_id: The user context for this attempt.
        entity_id: The monitored entity ID.
        provider: Metadata provider (e.g. "hardcover").
        provider_book_id: Provider's book identifier.
        content_type: "ebook" or "audiobook".
        status: Attempt status string (e.g. "no_match", "error", "queued").
        attempted_at: ISO timestamp; defaults to current UTC time if omitted.
        source: Release source name, if any.
        source_id: Release source identifier, if any.
        release_title: Title of the attempted release, if any.
        match_score: Scoring value for the release, if any.
        error_message: Error description, if any.

    Returns:
        The ISO timestamp used for this attempt.
    """
    attempted_at_iso = attempted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    safe_user_ids = [user_id] if user_id is not None else []
    user_db.set_monitored_book_search_status(
        user_ids=safe_user_ids,
        entity_id=entity_id,
        provider=provider,
        provider_book_id=provider_book_id,
        content_type=content_type,
        status=status,
        searched_at=attempted_at_iso,
    )
    user_db.insert_monitored_book_attempt_history(
        user_ids=safe_user_ids,
        entity_id=entity_id,
        provider=provider,
        provider_book_id=provider_book_id,
        content_type=content_type,
        attempted_at=attempted_at_iso,
        status=status,
        source=source,
        source_id=source_id,
        release_title=release_title,
        match_score=match_score,
        error_message=error_message,
    )
    return attempted_at_iso
