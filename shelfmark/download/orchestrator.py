"""Download queue orchestration and worker management.

Two-stage architecture: handlers stage to TMP_DIR, orchestrator moves to INGEST_DIR
with archive extraction and custom script support.
"""

import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Tuple

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import BookInfo, DownloadTask, QueueStatus, SearchFilters, SearchMode
from shelfmark.core.queue import book_queue
from shelfmark.core.utils import transform_cover_url, is_audiobook as check_audiobook
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.postprocess.pipeline import is_torrent_source, safe_cleanup_path
from shelfmark.download.postprocess.router import post_process_download
from shelfmark.release_sources import direct_download, get_handler, get_source_display_name
from shelfmark.release_sources.direct_download import SearchUnavailable

logger = setup_logger(__name__)

# Optional UserDB handle injected from main for monitored download history writes.
_history_user_db: Any = None


def set_history_user_db(user_db: Any) -> None:
    """Inject UserDB dependency for monitored download history recording."""
    global _history_user_db
    _history_user_db = user_db


def _record_monitored_download_history(task: DownloadTask, *, final_path: str) -> None:
    if _history_user_db is None:
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

    previous = _history_user_db.get_monitored_book_file_match(
        user_id=int(user_id),
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
    )

    overwrite_path = None
    if isinstance(previous, dict):
        previous_path = previous.get("path")
        if isinstance(previous_path, str) and previous_path.strip():
            overwrite_path = previous_path.strip()

    raw_match_score = history_context.get("match_score")
    try:
        match_score = float(raw_match_score) if raw_match_score is not None else None
    except (TypeError, ValueError):
        match_score = None

    downloaded_filename = str(history_context.get("downloaded_filename") or "").strip() or None

    _history_user_db.insert_monitored_book_download_history(
        user_id=int(user_id),
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
        downloaded_at=datetime.utcnow().isoformat() + "Z",
        source=str(task.source or ""),
        source_display_name=get_source_display_name(task.source),
        title_after_rename=str(task.title or "").strip() or None,
        match_score=match_score,
        downloaded_filename=downloaded_filename,
        final_path=str(final_path or "").strip(),
        overwritten_path=overwrite_path,
    )


def _parse_release_date(value: Any) -> Optional[date]:
    """Parse release date values from API/search payloads."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    raw = str(value or "").strip()
    if not raw:
        return None

    token = raw
    if "T" in token:
        token = token.split("T", 1)[0]
    elif " " in token:
        token = token.split(" ", 1)[0]

    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


# =============================================================================
# Task Download and Processing
# =============================================================================
#
# Post-download processing (staging, extraction, transfers, cleanup) lives in
# `shelfmark.download.postprocess`.


# WebSocket manager (initialized by app.py)
# Track whether WebSocket is available for status reporting
WEBSOCKET_AVAILABLE = True
try:
    from shelfmark.api.websocket import ws_manager
except ImportError:
    logger.error("WebSocket unavailable - real-time updates disabled")
    ws_manager = None
    WEBSOCKET_AVAILABLE = False

# Progress update throttling - track last broadcast time per book
_progress_last_broadcast: Dict[str, float] = {}
_progress_lock = Lock()

# Stall detection - track last activity time per download
_last_activity: Dict[str, float] = {}
# De-duplicate status updates (keep-alive updates shouldn't spam clients)
_last_status_event: Dict[str, Tuple[str, Optional[str]]] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled

def search_books(query: str, filters: SearchFilters) -> List[Dict[str, Any]]:
    """Search for books matching the query."""
    try:
        books = direct_download.search_books(query, filters)
        return [_book_info_to_dict(book) for book in books]
    except SearchUnavailable:
        raise
    except Exception as e:
        logger.error_trace(f"Error searching books: {e}")
        raise

def get_book_info(book_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information for a specific book."""
    try:
        book = direct_download.get_book_info(book_id)
        return _book_info_to_dict(book)
    except Exception as e:
        logger.error_trace(f"Error getting book info: {e}")
        raise

def _is_plain_email_address(value: str) -> bool:
    parsed = parseaddr(value or "")[1]
    return bool(parsed) and "@" in parsed and parsed == value


def _resolve_email_destination(
    user_id: Optional[int] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the destination email address for email output mode.

    Returns:
      (email_to, error_message)
    """
    configured_recipient = str(config.get("EMAIL_RECIPIENT", "", user_id=user_id) or "").strip()
    if configured_recipient:
        if _is_plain_email_address(configured_recipient):
            return configured_recipient, None
        return None, "Configured email recipient is invalid"

    return None, None


def queue_book(
    book_id: str,
    priority: int = 0,
    source: str = "direct_download",
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Add a book to the download queue. Returns (success, error_message)."""
    try:
        book_info = direct_download.get_book_info(book_id, fetch_download_count=False)
        if not book_info:
            error_msg = f"Could not fetch book info for {book_id}"
            logger.warning(error_msg)
            return False, error_msg

        books_output_mode = str(
            config.get("BOOKS_OUTPUT_MODE", "folder", user_id=user_id) or "folder"
        ).strip().lower()
        is_audiobook = check_audiobook(book_info.content)

        # Capture output mode at queue time so tasks aren't affected if settings change later.
        output_mode = "folder" if is_audiobook else books_output_mode
        output_args: Dict[str, Any] = {}

        if output_mode == "email" and not is_audiobook:
            email_to, email_error = _resolve_email_destination(user_id=user_id)
            if email_error:
                return False, email_error
            if email_to:
                output_args = {"to": email_to}

        # Create a source-agnostic download task
        task = DownloadTask(
            task_id=book_id,
            source=source,
            title=book_info.title,
            author=book_info.author,
            format=book_info.format,
            size=book_info.size,
            preview=book_info.preview,
            content_type=book_info.content,
            search_mode=SearchMode.DIRECT,
            output_mode=output_mode,
            output_args=output_args,
            priority=priority,
            user_id=user_id,
            username=username,
        )

        if not book_queue.add(task):
            logger.info(f"Book already in queue: {book_info.title}")
            return False, "Book is already in the download queue"

        logger.info(f"Book queued with priority {priority}: {book_info.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None
    except SearchUnavailable as e:
        error_msg = f"Search service unavailable: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing book: {e}"
        logger.error_trace(error_msg)
        return False, error_msg


def queue_release(
    release_data: dict,
    priority: int = 0,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Add a release to the download queue. Returns (success, error_message)."""
    try:
        source = release_data.get('source', 'direct_download')
        extra = release_data.get('extra', {})
        raw_request_id = release_data.get('_request_id')
        request_id: Optional[int] = None
        if isinstance(raw_request_id, int) and raw_request_id > 0:
            request_id = raw_request_id

        # Get author, year, preview, and content_type from top-level (preferred) or extra (fallback)
        author = release_data.get('author') or extra.get('author')
        year = release_data.get('year') or extra.get('year')
        preview = release_data.get('preview') or extra.get('preview')
        content_type = release_data.get('content_type') or extra.get('content_type')
        source_url_raw = (
            release_data.get('download_url')
            or release_data.get('source_url')
            or release_data.get('info_url')
            or extra.get('detail_url')
            or extra.get('source_url')
        )
        source_url = source_url_raw.strip() if isinstance(source_url_raw, str) else None
        if source_url == "":
            source_url = None

        # Get series info for library naming templates
        series_name = release_data.get('series_name') or extra.get('series_name')
        series_position = release_data.get('series_position') or extra.get('series_position')
        subtitle = release_data.get('subtitle') or extra.get('subtitle')

        explicit_release_date = _parse_release_date(
            release_data.get('release_date')
            or extra.get('release_date')
            or extra.get('publication_date')
            or extra.get('publish_date')
        )
        if explicit_release_date is not None and datetime.now(timezone.utc).date() < explicit_release_date:
            return False, f"Book is unreleased until {explicit_release_date.isoformat()}"

        monitored_entity_id = release_data.get('monitored_entity_id')
        monitored_book_provider = release_data.get('monitored_book_provider')
        monitored_book_provider_id = release_data.get('monitored_book_provider_id')
        release_title = release_data.get('release_title') or release_data.get('raw_title') or release_data.get('display_title')
        raw_match_score = release_data.get('match_score')
        if raw_match_score is None and isinstance(extra, dict):
            raw_match_score = extra.get('match_score')
        try:
            release_match_score = float(raw_match_score) if raw_match_score is not None else None
        except (TypeError, ValueError):
            release_match_score = None
        destination_override = release_data.get('destination_override')
        file_organization_override = release_data.get('file_organization_override')
        template_override = release_data.get('template_override')

        books_output_mode = str(
            config.get("BOOKS_OUTPUT_MODE", "folder", user_id=user_id) or "folder"
        ).strip().lower()
        is_audiobook = check_audiobook(content_type)

        output_mode = "folder" if is_audiobook else books_output_mode
        output_args: Dict[str, Any] = {}

        if output_mode == "email" and not is_audiobook:
            email_to, email_error = _resolve_email_destination(user_id=user_id)
            if email_error:
                return False, email_error
            if email_to:
                output_args = {"to": email_to}

        history_context: Dict[str, Any] | None = None
        if monitored_entity_id is not None:
            try:
                history_context = {
                    "entity_id": int(monitored_entity_id),
                    "provider": str(monitored_book_provider or "").strip() or None,
                    "provider_book_id": str(monitored_book_provider_id or "").strip() or None,
                    "release_title": str(release_title or "").strip() or None,
                    "match_score": release_match_score,
                }
            except (TypeError, ValueError):
                history_context = None

        if history_context and history_context.get("provider") and history_context.get("provider_book_id"):
            output_args = dict(output_args)
            output_args["history_context"] = history_context

        # Create a source-agnostic download task from release data
        task = DownloadTask(
            task_id=release_data['source_id'],
            source=source,
            title=release_data.get('title', 'Unknown'),
            author=author,
            year=year,
            format=release_data.get('format'),
            size=release_data.get('size'),
            preview=preview,
            content_type=content_type,
            source_url=source_url,
            series_name=series_name,
            series_position=series_position,
            subtitle=subtitle,
            search_mode=SearchMode.UNIVERSAL,
            output_mode=output_mode,
            output_args=output_args,
            monitored_entity_id=int(monitored_entity_id) if monitored_entity_id is not None else None,
            destination_override=str(destination_override).strip() if isinstance(destination_override, str) and destination_override.strip() else None,
            file_organization_override=str(file_organization_override).strip() if isinstance(file_organization_override, str) and file_organization_override.strip() else None,
            template_override=str(template_override) if isinstance(template_override, str) and template_override.strip() else None,
            priority=priority,
            user_id=user_id,
            username=username,
            request_id=request_id,
        )

        if not book_queue.add(task):
            logger.info(f"Release already in queue: {task.title}")
            return False, "Release is already in the download queue"

        logger.info(f"Release queued with priority {priority}: {task.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None

    except ValueError as e:
        # Handler not found for this source
        error_msg = f"Unknown release source: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except KeyError as e:
        error_msg = f"Missing required field in release data: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing release: {e}"
        logger.error_trace(error_msg)
        return False, error_msg

def queue_status(user_id: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue."""
    status = book_queue.get_status(user_id=user_id)
    for _, tasks in status.items():
        for _, task in tasks.items():
            if task.download_path and not run_blocking_io(os.path.exists, task.download_path):
                task.download_path = None

    # Convert Enum keys to strings and DownloadTask objects to dicts for JSON serialization
    return {
        status_type.value: {
            task_id: _task_to_dict(task)
            for task_id, task in tasks.items()
        }
        for status_type, tasks in status.items()
    }

def get_book_data(task_id: str) -> Tuple[Optional[bytes], Optional[DownloadTask]]:
    """Get downloaded file data for a specific task."""
    task = None
    try:
        task = book_queue.get_task(task_id)
        if not task:
            return None, None

        path = task.download_path
        if not path:
            return None, task

        with open(path, "rb") as f:
            return f.read(), task
    except Exception as e:
        logger.error_trace(f"Error getting book data: {e}")
        if task:
            task.download_path = None
        return None, task

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo to dict, transforming cover URLs for caching."""
    result = {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

    # Transform external preview URLs to local proxy URLs
    if result.get('preview'):
        result['preview'] = transform_cover_url(result['preview'], book.id)

    return result


def _task_to_dict(task: DownloadTask) -> Dict[str, Any]:
    """Convert DownloadTask to dict for frontend, transforming cover URLs."""
    # Transform external preview URLs to local proxy URLs
    preview = transform_cover_url(task.preview, task.task_id)

    return {
        'id': task.task_id,
        'title': task.title,
        'author': task.author,
        'format': task.format,
        'size': task.size,
        'preview': preview,
        'content_type': task.content_type,
        'source': task.source,
        'source_display_name': get_source_display_name(task.source),
        'priority': task.priority,
        'added_time': task.added_time,
        'progress': task.progress,
        'status': task.status,
        'status_message': task.status_message,
        'download_path': task.download_path,
        'user_id': task.user_id,
        'username': task.username,
        'request_id': task.request_id,
    }


def _download_task(task_id: str, cancel_flag: Event) -> Optional[str]:
    """Download a task via appropriate handler, then post-process to ingest."""
    try:
        # Check for cancellation before starting
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before starting", task_id)
            return None

        task = book_queue.get_task(task_id)
        if not task:
            logger.error("Task not found in queue: %s", task_id)
            return None

        title_label = task.title or "Unknown title"
        logger.info(
            "Task %s: starting download (%s) - %s",
            task_id,
            get_source_display_name(task.source),
            title_label,
        )

        def progress_callback(progress: float) -> None:
            update_download_progress(task_id, progress)

        def status_callback(status: str, message: Optional[str] = None) -> None:
            update_download_status(task_id, status, message)

        # Get the download handler based on the task's source
        handler = get_handler(task.source)
        temp_path = handler.download(
            task,
            cancel_flag,
            progress_callback,
            status_callback
        )

        # Handler returns temp path - orchestrator handles post-processing
        if not temp_path:
            return None

        temp_file = Path(temp_path)
        if not run_blocking_io(temp_file.exists):
            logger.error(f"Handler returned non-existent path: {temp_path}")
            return None

        if isinstance(task.output_args, dict):
            history_context = task.output_args.get("history_context")
            if isinstance(history_context, dict):
                history_context["downloaded_filename"] = temp_file.name

        # Check cancellation before post-processing
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before post-processing", task_id)
            if not is_torrent_source(temp_file, task):
                safe_cleanup_path(temp_file, task)
            return None

        logger.info("Task %s: download finished; starting post-processing", task_id)
        logger.debug("Task %s: post-processing input path: %s", task_id, temp_file)

        # Post-processing: output routing + file processing pipeline
        result = post_process_download(temp_file, task, cancel_flag, status_callback)

        if cancel_flag.is_set():
            logger.info("Task %s: post-processing cancelled", task_id)
        elif result:
            logger.info("Task %s: post-processing complete", task_id)
            logger.debug("Task %s: post-processing result: %s", task_id, result)
            try:
                _record_monitored_download_history(task, final_path=result)
            except Exception as hist_exc:
                logger.warning("Task %s: failed to record monitored download history: %s", task_id, hist_exc)
        else:
            logger.warning("Task %s: post-processing failed", task_id)

        try:
            handler.post_process_cleanup(task, success=bool(result))
        except Exception as e:
            logger.warning("Post-processing cleanup hook failed for %s: %s", task_id, e)

        return result

    except Exception as e:
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled during error handling", task_id)
        else:
            logger.error_trace("Task %s: error downloading: %s", task_id, e)
            # Update task status so user sees the failure
            task = book_queue.get_task(task_id)
            if task:
                if _history_user_db is not None and isinstance(task.output_args, dict):
                    history_context = task.output_args.get("history_context")
                    if isinstance(history_context, dict):
                        entity_id = history_context.get("entity_id")
                        provider = str(history_context.get("provider") or "").strip()
                        provider_book_id = str(history_context.get("provider_book_id") or "").strip()
                        if entity_id is not None and provider and provider_book_id and task.user_id is not None:
                            content_type = str(task.content_type or "ebook").strip().lower()
                            if content_type not in {"ebook", "audiobook"}:
                                content_type = "ebook"
                            try:
                                raw_match_score = history_context.get("match_score")
                                match_score = float(raw_match_score) if raw_match_score is not None else None
                            except (TypeError, ValueError):
                                match_score = None
                            try:
                                _history_user_db.insert_monitored_book_attempt_history(
                                    user_id=int(task.user_id),
                                    entity_id=int(entity_id),
                                    provider=provider,
                                    provider_book_id=provider_book_id,
                                    content_type=content_type,
                                    attempted_at=datetime.utcnow().isoformat() + "Z",
                                    status="download_failed",
                                    source=str(task.source or "") or None,
                                    source_id=str(task.task_id or "") or None,
                                    release_title=str(history_context.get("release_title") or "") or None,
                                    match_score=match_score,
                                    error_message=str(e),
                                )
                            except Exception as hist_exc:
                                logger.warning("Task %s: failed to record monitored attempt failure: %s", task_id, hist_exc)
                book_queue.update_status(task_id, QueueStatus.ERROR)
                # Check for known misconfiguration from earlier versions
                if isinstance(e, PermissionError) and "/cwa-book-ingest" in str(e):
                    book_queue.update_status_message(
                        task_id,
                        "Destination misconfigured. Go to Settings → Downloads to update."
                    )
                else:
                    if isinstance(e, PermissionError):
                        book_queue.update_status_message(task_id, f"Permission denied: {e}")
                    else:
                        book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}")
        return None



def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts."""
    book_queue.update_progress(book_id, progress)

    # Track activity for stall detection
    with _progress_lock:
        _last_activity[book_id] = time.time()
    
    # Broadcast progress via WebSocket with throttling
    if ws_manager:
        current_time = time.time()
        should_broadcast = False
        
        with _progress_lock:
            last_broadcast = _progress_last_broadcast.get(book_id, 0)
            last_progress = _progress_last_broadcast.get(f"{book_id}_progress", 0)
            time_elapsed = current_time - last_broadcast
            
            # Always broadcast at start (0%) or completion (>=99%)
            if progress <= 1 or progress >= 99:
                should_broadcast = True
            # Broadcast if enough time has passed (convert interval from seconds)
            elif time_elapsed >= config.DOWNLOAD_PROGRESS_UPDATE_INTERVAL:
                should_broadcast = True
            # Broadcast on significant progress jumps (>10%)
            elif progress - last_progress >= 10:
                should_broadcast = True
            
            if should_broadcast:
                _progress_last_broadcast[book_id] = current_time
                _progress_last_broadcast[f"{book_id}_progress"] = progress
        
        if should_broadcast:
            task = book_queue.get_task(book_id)
            task_user_id = task.user_id if task else None
            ws_manager.broadcast_download_progress(book_id, progress, 'downloading', user_id=task_user_id)

def update_download_status(book_id: str, status: str, message: Optional[str] = None) -> None:
    """Update download status with optional message for UI display."""
    # Map string status to QueueStatus enum
    status_map = {
        'queued': QueueStatus.QUEUED,
        'resolving': QueueStatus.RESOLVING,
        'locating': QueueStatus.LOCATING,
        'downloading': QueueStatus.DOWNLOADING,
        'complete': QueueStatus.COMPLETE,
        'available': QueueStatus.AVAILABLE,
        'error': QueueStatus.ERROR,
        'done': QueueStatus.DONE,
        'cancelled': QueueStatus.CANCELLED,
    }
    
    status_key = status.lower()
    queue_status_enum = status_map.get(status_key)
    if not queue_status_enum:
        return

    # Always update activity timestamp (used by stall detection) even if the status
    # event is a duplicate keep-alive update.
    with _progress_lock:
        _last_activity[book_id] = time.time()
        status_event = (status_key, message)
        if _last_status_event.get(book_id) == status_event:
            return
        _last_status_event[book_id] = status_event

    # Update status message first so terminal snapshots capture the final message
    # (for example, "Complete" or "Sent to ...") instead of a stale in-progress one.
    if message is not None:
        book_queue.update_status_message(book_id, message)

    book_queue.update_status(book_id, queue_status_enum)

    # Broadcast status update via WebSocket
    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

def cancel_download(book_id: str) -> bool:
    """Cancel a download."""
    result = book_queue.cancel_download(book_id)
    
    # Broadcast status update via WebSocket
    if result and ws_manager and ws_manager.is_enabled():
        ws_manager.broadcast_status_update(queue_status())
    
    return result

def set_book_priority(book_id: str, priority: int) -> bool:
    """Set priority for a queued book (lower = higher priority)."""
    return book_queue.set_priority(book_id, priority)

def reorder_queue(book_priorities: Dict[str, int]) -> bool:
    """Bulk reorder queue by mapping book_id to new priority."""
    return book_queue.reorder_queue(book_priorities)

def get_queue_order() -> List[Dict[str, Any]]:
    """Get current queue order for display."""
    return book_queue.get_queue_order()

def get_active_downloads() -> List[str]:
    """Get list of currently active downloads."""
    return book_queue.get_active_downloads()

def clear_completed(user_id: Optional[int] = None) -> int:
    """Clear completed downloads from tracking (optionally user-scoped)."""
    return book_queue.clear_completed(user_id=user_id)

def _cleanup_progress_tracking(task_id: str) -> None:
    """Clean up progress tracking data for a completed/cancelled download."""
    with _progress_lock:
        _progress_last_broadcast.pop(task_id, None)
        _progress_last_broadcast.pop(f"{task_id}_progress", None)
        _last_activity.pop(task_id, None)
        _last_status_event.pop(task_id, None)


def _process_single_download(task_id: str, cancel_flag: Event) -> None:
    """Process a single download job."""
    try:
        # Status will be updated through callbacks during download process
        # (resolving -> downloading -> complete)
        download_path = _download_task(task_id, cancel_flag)

        # Clean up progress tracking
        _cleanup_progress_tracking(task_id)

        if cancel_flag.is_set():
            book_queue.update_status(task_id, QueueStatus.CANCELLED)
            # Broadcast cancellation
            if ws_manager:
                ws_manager.broadcast_status_update(queue_status())
            return

        if download_path:
            book_queue.update_download_path(task_id, download_path)
            # Only update status if not already set (e.g., by archive extraction callback)
            task = book_queue.get_task(task_id)
            if not task or task.status != QueueStatus.COMPLETE:
                book_queue.update_status(task_id, QueueStatus.COMPLETE)
        else:
            book_queue.update_status(task_id, QueueStatus.ERROR)

        # Broadcast final status (completed or error)
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

    except Exception as e:
        # Clean up progress tracking even on error
        _cleanup_progress_tracking(task_id)

        if not cancel_flag.is_set():
            logger.error_trace(f"Error in download processing: {e}")
            book_queue.update_status(task_id, QueueStatus.ERROR)
            # Set error message if not already set by handler
            task = book_queue.get_task(task_id)
            if task and not task.status_message:
                book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}: {str(e)}")
        else:
            logger.info(f"Download cancelled: {task_id}")
            book_queue.update_status(task_id, QueueStatus.CANCELLED)

        # Broadcast error/cancelled status
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

def concurrent_download_loop() -> None:
    """Main download coordinator using ThreadPoolExecutor for concurrent downloads."""
    max_workers = config.MAX_CONCURRENT_DOWNLOADS
    logger.info(f"Starting concurrent download loop with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Download") as executor:
        active_futures: Dict[Future, str] = {}  # Track active download futures
        stalled_tasks: set[str] = set()  # Track tasks already cancelled due to stall

        while True:
            # Clean up completed futures
            completed_futures = [f for f in active_futures if f.done()]
            for future in completed_futures:
                task_id = active_futures.pop(future)
                stalled_tasks.discard(task_id)
                try:
                    future.result()  # This will raise any exceptions from the worker
                except Exception as e:
                    logger.error_trace(f"Future exception for {task_id}: {e}")

            # Check for stalled downloads (no activity in STALL_TIMEOUT seconds)
            current_time = time.time()
            with _progress_lock:
                for future, task_id in list(active_futures.items()):
                    if task_id in stalled_tasks:
                        continue
                    last_active = _last_activity.get(task_id, current_time)
                    if current_time - last_active > STALL_TIMEOUT:
                        logger.warning(f"Download stalled for {task_id}, cancelling")
                        book_queue.cancel_download(task_id)
                        book_queue.update_status_message(task_id, f"Download stalled (no activity for {STALL_TIMEOUT}s)")
                        stalled_tasks.add(task_id)

            # Start new downloads if we have capacity
            while len(active_futures) < max_workers:
                next_download = book_queue.get_next()
                if not next_download:
                    break

                # Stagger concurrent downloads to avoid rate limiting on shared download servers
                # Only delay if other downloads are already active
                if active_futures:
                    stagger_delay = random.uniform(2, 5)
                    logger.debug(f"Staggering download start by {stagger_delay:.1f}s")
                    time.sleep(stagger_delay)

                task_id, cancel_flag = next_download

                # Submit download job to thread pool
                future = executor.submit(_process_single_download, task_id, cancel_flag)
                active_futures[future] = task_id

            # Brief sleep to prevent busy waiting
            time.sleep(config.MAIN_LOOP_SLEEP_TIME)

# Download coordinator thread (started explicitly via start())
_coordinator_thread: Optional[threading.Thread] = None
_started = False


def start() -> None:
    """Start the download coordinator thread. Safe to call multiple times."""
    global _coordinator_thread, _started

    if _started:
        logger.debug("Download coordinator already started")
        return

    _coordinator_thread = threading.Thread(
        target=concurrent_download_loop,
        daemon=True,
        name="DownloadCoordinator"
    )
    _coordinator_thread.start()
    _started = True

    logger.info(f"Download coordinator started with {config.MAX_CONCURRENT_DOWNLOADS} concurrent workers")
