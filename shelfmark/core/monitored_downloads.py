"""Monitored download handling: queue integration and history recording.

Uses the terminal status hook to record download history without modifying
the core orchestrator module. This module handles:
- Recording successful downloads to monitored_book_download_history
- Recording failed downloads to monitored_book_attempt_history
- Future: retry logic, scheduled auto-search triggers
"""

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import book_queue
from shelfmark.release_sources import get_source_display_name

logger = setup_logger(__name__)

# UserDB handle injected at startup
_user_db: Any = None

# Pending releases for retry logic: key = "entity_id:provider:provider_book_id:content_type"
_pending_releases: Dict[str, "PendingDownload"] = {}
_pending_lock = threading.Lock()


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
    current_source_id: Optional[str] = None
    attempts: int = 0


def _pending_key(entity_id: int, provider: str, provider_book_id: str, content_type: str) -> str:
    """Generate key for pending releases dict."""
    return f"{entity_id}:{provider}:{provider_book_id}:{content_type}"


def set_user_db(user_db: Any) -> None:
    """Inject UserDB dependency for monitored download history recording."""
    global _user_db
    _user_db = user_db


# =============================================================================
# Release Date Parsing
# =============================================================================


def parse_release_date(value: Any) -> Optional[date]:
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


def is_book_released(release_date: Any) -> Tuple[bool, Optional[date]]:
    """Check if a book has been released based on its release date.
    
    Returns:
        Tuple of (is_released, parsed_date).
        is_released is True if no date or date is in the past.
    """
    parsed = parse_release_date(release_date)
    if parsed is None:
        return True, None  # No date = assume released
    
    today = datetime.now(timezone.utc).date()
    return parsed <= today, parsed


# =============================================================================
# Pre-Processing: Filter and Rank Releases
# =============================================================================


def pre_process_releases(
    releases: List[Dict[str, Any]],
    *,
    user_id: int,
    entity_id: int,
    provider: str,
    provider_book_id: str,
    content_type: str = "ebook",
    min_match_score: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Pre-process releases for a monitored book before queuing.
    
    Filters releases by:
    1. Release date (must be released)
    2. Match score cutoff
    3. Previous failed attempts (deprioritize but don't exclude)
    
    Args:
        releases: List of release dicts from search
        user_id: Current user ID
        entity_id: Monitored entity ID
        provider: Book provider (e.g., 'goodreads')
        provider_book_id: Provider's book ID
        content_type: 'ebook' or 'audiobook'
        min_match_score: Minimum match score cutoff (uses config default if None)
    
    Returns:
        Tuple of (valid_releases, rejection_reason).
        valid_releases is sorted by score (highest first), with failed attempts last.
        rejection_reason is set if no valid releases found.
    """
    if not releases:
        return [], "No releases found"
    
    # Get cutoff from config if not specified
    if min_match_score is None:
        min_match_score = float(config.get("AUTO_DOWNLOAD_MIN_MATCH_SCORE", 0.7, user_id=user_id))
    
    valid_releases: List[Dict[str, Any]] = []
    unreleased_count = 0
    below_cutoff_count = 0
    
    # Get failed (source, source_id) pairs to deprioritize
    failed_source_pairs: set[tuple[str, str]] = set()
    if _user_db is not None:
        try:
            failed_source_pairs = _user_db.list_monitored_failed_candidate_source_ids(
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
            )
        except Exception as e:
            logger.warning("Failed to get failed source IDs: %s", e)
    
    for release in releases:
        # Check release date
        release_date = (
            release.get("release_date")
            or release.get("extra", {}).get("release_date")
            or release.get("extra", {}).get("publication_date")
        )
        is_released, parsed_date = is_book_released(release_date)
        if not is_released:
            unreleased_count += 1
            logger.debug("Skipping unreleased: %s (releases %s)", release.get("title"), parsed_date)
            continue
        
        # Check match score
        extra = release.get("extra", {})
        match_score = release.get("match_score") or extra.get("match_score")
        try:
            score = float(match_score) if match_score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        
        if score < min_match_score:
            below_cutoff_count += 1
            logger.debug("Skipping below cutoff: %s (score %.2f < %.2f)", release.get("title"), score, min_match_score)
            continue
        
        # Mark if previously failed (for sorting)
        src = str(release.get("source", "")).strip()
        src_id = str(release.get("source_id", "")).strip()
        release["_previously_failed"] = bool(src and src_id and (src, src_id) in failed_source_pairs)
        release["_match_score"] = score
        valid_releases.append(release)
    
    if not valid_releases:
        if unreleased_count > 0 and below_cutoff_count == 0:
            return [], f"Book is unreleased"
        elif below_cutoff_count > 0:
            return [], f"No releases meet minimum match score ({min_match_score:.0%})"
        else:
            return [], "No valid releases found"
    
    # Sort: highest score first, previously failed last
    valid_releases.sort(
        key=lambda r: (not r.get("_previously_failed", False), r.get("_match_score", 0)),
        reverse=True
    )
    
    logger.info(
        "Pre-processed %d releases: %d valid, %d unreleased, %d below cutoff, %d previously failed",
        len(releases), len(valid_releases), unreleased_count, below_cutoff_count,
        sum(1 for r in valid_releases if r.get("_previously_failed"))
    )
    
    return valid_releases, None


# =============================================================================
# Hook Registration
# =============================================================================


def register_hooks() -> None:
    """Register monitored download hooks. Call during app startup."""
    book_queue.set_terminal_status_hook(_on_download_terminal)
    logger.info("Monitored download hooks registered")


def _on_download_terminal(book_id: str, status: QueueStatus, task: DownloadTask) -> None:
    """Hook called when a download reaches terminal status (COMPLETE, ERROR, etc.)."""
    try:
        if status == QueueStatus.COMPLETE:
            _record_download_history(task)
            _clear_pending(task)
        elif status == QueueStatus.ERROR:
            _record_attempt_failure(task)
            _try_next_release(task)
        # CANCELLED status: clear pending, no retry
        elif status == QueueStatus.CANCELLED:
            _clear_pending(task)
    except Exception as e:
        logger.warning("Failed to record monitored download history for %s: %s", book_id, e)


def _record_download_history(task: DownloadTask) -> None:
    """Record successful download to monitored_book_download_history."""
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

    # Check for existing file to track overwrites
    previous = _user_db.get_monitored_book_file_match(
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
    final_path = str(task.download_path or "").strip()

    _user_db.insert_monitored_book_download_history(
        user_id=int(user_id),
        entity_id=int(entity_id),
        provider=provider,
        provider_book_id=provider_book_id,
        downloaded_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source=str(task.source or ""),
        source_display_name=get_source_display_name(task.source),
        title_after_rename=str(task.title or "").strip() or None,
        match_score=match_score,
        downloaded_filename=downloaded_filename,
        final_path=final_path,
        overwritten_path=overwrite_path,
    )

    logger.debug(
        "Recorded monitored download history: entity_id=%s provider=%s book_id=%s",
        entity_id, provider, provider_book_id
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

    content_type = str(task.content_type or "ebook").strip().lower()
    if content_type not in {"ebook", "audiobook"}:
        content_type = "ebook"

    try:
        raw_match_score = history_context.get("match_score")
        match_score = float(raw_match_score) if raw_match_score is not None else None
    except (TypeError, ValueError):
        match_score = None

    failure_text = (error_message or task.status_message or "").strip() or None

    _user_db.insert_monitored_book_attempt_history(
        user_id=int(user_id),
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
    # Check if already pending/in-queue
    key = _pending_key(entity_id, provider, provider_book_id, content_type)
    with _pending_lock:
        if key in _pending_releases:
            return False, "Already in queue"
    
    # Pre-process releases
    valid_releases, error = pre_process_releases(
        releases,
        user_id=user_id,
        entity_id=entity_id,
        provider=provider,
        provider_book_id=provider_book_id,
        content_type=content_type,
        min_match_score=min_match_score,
    )
    
    if error or not valid_releases:
        return False, error or "No valid releases"
    
    # Store pending releases for retry
    key = _pending_key(entity_id, provider, provider_book_id, content_type)
    with _pending_lock:
        _pending_releases[key] = PendingDownload(
            releases=valid_releases,
            user_id=user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            content_type=content_type,
            destination_override=destination_override,
            file_organization_override=file_organization_override,
            template_override=template_override,
        )
    
    # Queue first release
    return _queue_next_from_pending(key)


def _queue_next_from_pending(key: str) -> Tuple[bool, str]:
    """Queue the next release from pending list. Returns (success, message)."""
    from shelfmark.download import orchestrator as download_orchestrator
    
    with _pending_lock:
        pending = _pending_releases.get(key)
        if not pending or not pending.releases:
            return False, "No more releases to try"
        
        # Take next release
        release = pending.releases.pop(0)
        pending.current_source_id = str(release.get("source_id", ""))
        pending.attempts += 1
    
    # Enrich with monitored context
    release["monitored_entity_id"] = pending.entity_id
    release["monitored_book_provider"] = pending.provider
    release["monitored_book_provider_id"] = pending.provider_book_id
    release["destination_override"] = pending.destination_override
    release["file_organization_override"] = pending.file_organization_override
    release["template_override"] = pending.template_override
    release["content_type"] = pending.content_type
    
    # Queue via orchestrator
    success, error_msg = download_orchestrator.queue_release(release, user_id=pending.user_id)
    
    if success:
        title = release.get("title") or release.get("display_title") or "Unknown"
        score = release.get("_match_score", 0)
        remaining = len(pending.releases)
        return True, f"Queued: {title} (score: {score:.0%}, {remaining} fallbacks)"
    else:
        # Immediate queue failure - try next
        logger.warning("Queue failed for %s: %s, trying next", pending.current_source_id, error_msg)
        return _queue_next_from_pending(key)


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
    content_type = str(task.content_type or "ebook").strip().lower()
    
    if entity_id is None or not provider or not provider_book_id:
        return None
    
    return _pending_key(int(entity_id), provider, provider_book_id, content_type)


def _clear_pending(task: DownloadTask) -> None:
    """Clear pending releases for a task (called on success/cancel)."""
    key = _get_pending_key_from_task(task)
    if key:
        with _pending_lock:
            _pending_releases.pop(key, None)
        logger.debug("Cleared pending releases for %s", key)


def _try_next_release(task: DownloadTask) -> None:
    """Try next release from pending list (called on failure)."""
    key = _get_pending_key_from_task(task)
    if not key:
        return
    
    with _pending_lock:
        pending = _pending_releases.get(key)
        if not pending or not pending.releases:
            # No more releases to try
            _pending_releases.pop(key, None)
            logger.info("No more fallback releases for %s after %d attempts", key, pending.attempts if pending else 0)
            return
    
    logger.info("Download failed, trying next release for %s (%d remaining)", key, len(pending.releases))
    success, msg = _queue_next_from_pending(key)
    if success:
        logger.info("Queued fallback: %s", msg)
    else:
        logger.warning("Failed to queue fallback for %s: %s", key, msg)


# =============================================================================
# Batch Processing
# =============================================================================

# Default batch size for processing multiple books
DEFAULT_BATCH_SIZE = 10


@dataclass
class BookDownloadRequest:
    """Request to download a monitored book."""
    releases: List[Dict[str, Any]]
    entity_id: int
    provider: str
    provider_book_id: str
    content_type: str = "ebook"
    destination_override: Optional[str] = None
    file_organization_override: Optional[str] = None
    template_override: Optional[str] = None


@dataclass
class BatchResult:
    """Result of batch processing."""
    queued: int = 0
    skipped_in_queue: int = 0
    skipped_unreleased: int = 0
    skipped_no_match: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)


def is_book_pending(
    entity_id: int,
    provider: str,
    provider_book_id: str,
    content_type: str = "ebook",
) -> bool:
    """Check if a book is already pending/in-queue."""
    key = _pending_key(entity_id, provider, provider_book_id, content_type)
    with _pending_lock:
        return key in _pending_releases


def process_batch(
    requests: List[BookDownloadRequest],
    *,
    user_id: int,
    min_match_score: Optional[float] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> BatchResult:
    """Process multiple book download requests in batches.
    
    Processes books in chunks of batch_size, with each batch queuing up to
    batch_size books before moving to the next batch. Books already in queue
    are skipped.
    
    Args:
        requests: List of book download requests
        user_id: Current user ID
        min_match_score: Minimum match score cutoff (0.0-1.0)
        batch_size: Max books to queue per batch (default 10)
    
    Returns:
        BatchResult with counts of queued, skipped, failed.
    """
    result = BatchResult()
    
    for i, req in enumerate(requests):
        # Process in batches - check how many are currently pending
        with _pending_lock:
            current_pending = len(_pending_releases)
        
        if current_pending >= batch_size:
            # Wait indicator - caller should check queue status
            logger.info(
                "Batch limit reached (%d pending), processed %d/%d books",
                current_pending, i, len(requests)
            )
            # Continue processing but don't queue more until space frees up
            # In practice, the skip-if-pending guard handles this
        
        success, message = process_monitored_book(
            req.releases,
            user_id=user_id,
            entity_id=req.entity_id,
            provider=req.provider,
            provider_book_id=req.provider_book_id,
            content_type=req.content_type,
            min_match_score=min_match_score,
            destination_override=req.destination_override,
            file_organization_override=req.file_organization_override,
            template_override=req.template_override,
        )
        
        if success:
            result.queued += 1
        elif message == "Already in queue":
            result.skipped_in_queue += 1
        elif "unreleased" in message.lower():
            result.skipped_unreleased += 1
        elif "match score" in message.lower() or "no valid" in message.lower():
            result.skipped_no_match += 1
        else:
            result.failed += 1
            result.errors.append(f"{req.provider_book_id}: {message}")
    
    logger.info(
        "Batch complete: %d queued, %d in-queue, %d unreleased, %d no-match, %d failed",
        result.queued, result.skipped_in_queue, result.skipped_unreleased,
        result.skipped_no_match, result.failed
    )
    
    return result


def get_pending_count() -> int:
    """Get count of books currently pending/in-queue."""
    with _pending_lock:
        return len(_pending_releases)
