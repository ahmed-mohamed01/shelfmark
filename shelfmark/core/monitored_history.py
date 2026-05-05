"""Unified event recording for monitored entity history.

This module records denormalized timeline events to ``monitored_events`` for
the History tab UI. It is an **audit log** layered on top of the structured
``monitored_book_download_history`` and ``monitored_book_attempt_history``
tables — those remain the source of truth for business logic
(``resolve_book_auto_search_precheck``, file-match upserts, search-status
tracking). Every recorder here is paired with a structured write elsewhere;
both paths run on the same hook by design.

Wiping ``monitored_events`` is safe — it loses the timeline view but does
not affect the precheck or other business logic.

This is a branch-only module.
"""

from __future__ import annotations

import json
from typing import Any

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Injected at startup via set_db()
_db: Any = None


def set_db(monitored_db: Any) -> None:
    """Inject the MonitoredDB instance. Call once at startup."""
    global _db
    _db = monitored_db


def _record(
    *,
    event_type: str,
    entity_id: int | None = None,
    book_provider: str | None = None,
    book_provider_id: str | None = None,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    source: str | None = None,
    source_display_name: str | None = None,
    status: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    """Write an event to the monitored_events table."""
    if _db is None:
        return
    try:
        _db.insert_event(
            event_type=event_type,
            entity_id=entity_id,
            book_provider=book_provider,
            book_provider_id=book_provider_id,
            book_title=book_title,
            author_name=author_name,
            content_type=content_type,
            source=source,
            source_display_name=source_display_name,
            status=status,
            message=message,
            metadata_json=json.dumps(metadata) if metadata else None,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.debug("Failed to record monitored event %s: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Download events
# ---------------------------------------------------------------------------


def record_download_queued(
    *,
    entity_id: int,
    book_provider: str,
    book_provider_id: str,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    task_id: str | None = None,
    source: str | None = None,
    source_display_name: str | None = None,
    release_title: str | None = None,
    match_score: float | None = None,
    format: str | None = None,
    size: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="download_queued",
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        source=source,
        source_display_name=source_display_name,
        status="info",
        message=f"Download queued: {release_title or book_title or 'Unknown'}",
        metadata={
            k: v for k, v in {
                "task_id": task_id,
                "release_title": release_title,
                "match_score": match_score,
                "format": format,
                "size": size,
            }.items() if v is not None
        } or None,
        session_id=session_id,
        user_id=user_id,
    )


def record_download_complete(
    *,
    entity_id: int,
    book_provider: str,
    book_provider_id: str,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    source: str | None = None,
    source_display_name: str | None = None,
    download_path: str | None = None,
    downloaded_filename: str | None = None,
    match_score: float | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="download_complete",
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        source=source,
        source_display_name=source_display_name,
        status="success",
        message=f"Downloaded: {downloaded_filename or book_title or 'Unknown'}",
        metadata={
            k: v for k, v in {
                "task_id": task_id,
                "download_path": download_path,
                "downloaded_filename": downloaded_filename,
                "match_score": match_score,
            }.items() if v is not None
        } or None,
        session_id=session_id,
        user_id=user_id,
    )


def record_download_failed(
    *,
    entity_id: int,
    book_provider: str,
    book_provider_id: str,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    source: str | None = None,
    source_display_name: str | None = None,
    error_message: str | None = None,
    release_title: str | None = None,
    match_score: float | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="download_failed",
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        source=source,
        source_display_name=source_display_name,
        status="error",
        message=error_message or f"Download failed: {release_title or book_title or 'Unknown'}",
        metadata={
            k: v for k, v in {
                "task_id": task_id,
                "release_title": release_title,
                "match_score": match_score,
                "error_message": error_message,
            }.items() if v is not None
        } or None,
        session_id=session_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Search events
# ---------------------------------------------------------------------------


def record_search_started(
    *,
    entity_id: int,
    book_provider: str,
    book_provider_id: str,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    session_id: str,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record the start of a search attempt for a book. Creates a session."""
    _record(
        event_type="search_started",
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        status="info",
        message=f"Searching releases for: {book_title or 'Unknown'}",
        metadata=metadata or None,
        session_id=session_id,
        user_id=user_id,
    )


def record_search_result(
    *,
    entity_id: int,
    book_provider: str,
    book_provider_id: str,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    search_status: str,
    releases_found: int | None = None,
    best_score: float | None = None,
    cutoff_score: float | None = None,
    release_title: str | None = None,
    error_message: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a search outcome (queued, no_match, below_cutoff, not_released, error)."""
    status_map = {
        "queued": "success",
        "no_match": "warning",
        "below_cutoff": "warning",
        "not_released": "info",
        "error": "error",
        "download_failed": "error",
    }
    message_map = {
        "queued": f"Search matched: {release_title or book_title or 'Unknown'}",
        "no_match": f"No match found for: {book_title or 'Unknown'}",
        "below_cutoff": f"Below score cutoff: {book_title or 'Unknown'}",
        "not_released": f"Not yet released: {book_title or 'Unknown'}",
        "error": error_message or f"Search error: {book_title or 'Unknown'}",
        "download_failed": error_message or f"Download failed: {book_title or 'Unknown'}",
    }
    event_type = f"search_{search_status}" if search_status in ("no_match", "below_cutoff", "not_released", "queued") else "search_result"

    merged_metadata = {
        k: v for k, v in {
            "search_status": search_status,
            "releases_found": releases_found,
            "best_score": best_score,
            "cutoff_score": cutoff_score,
            "release_title": release_title,
            "error_message": error_message,
        }.items() if v is not None
    }
    if metadata:
        merged_metadata.update({k: v for k, v in metadata.items() if v is not None})

    _record(
        event_type=event_type,
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        status=status_map.get(search_status, "info"),
        message=message_map.get(search_status, f"Search: {search_status}"),
        metadata=merged_metadata or None,
        session_id=session_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Author / entity events
# ---------------------------------------------------------------------------


def record_author_added(
    *,
    entity_id: int,
    author_name: str,
    provider: str | None = None,
    provider_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="author_added",
        entity_id=entity_id,
        author_name=author_name,
        status="info",
        message=f"Author added: {author_name}",
        metadata={"provider": provider, "provider_id": provider_id} if provider else None,
        user_id=user_id,
    )


def record_author_removed(
    *,
    entity_id: int,
    author_name: str,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="author_removed",
        entity_id=entity_id,
        author_name=author_name,
        status="info",
        message=f"Author removed: {author_name}",
        user_id=user_id,
    )


def record_author_synced(
    *,
    entity_id: int,
    author_name: str,
    books_added: int = 0,
    books_removed: int = 0,
    total_books: int | None = None,
    batch_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="author_synced",
        entity_id=entity_id,
        author_name=author_name,
        status="success",
        message=(
            f"Synced {author_name}: {books_added} new, {books_removed} removed" if books_added
            else f"Synced {author_name}: {books_removed} removed" if books_removed
            else f"Synced {author_name}"
        ),
        metadata={
            k: v for k, v in {
                "books_added": books_added,
                "books_removed": books_removed,
                "total_books": total_books,
                "batch_id": batch_id,
            }.items() if v is not None
        } or None,
        user_id=user_id,
    )


def record_author_sync_failed(
    *,
    entity_id: int,
    author_name: str,
    error_message: str | None = None,
    batch_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="author_sync_failed",
        entity_id=entity_id,
        author_name=author_name,
        status="error",
        message=error_message or f"Sync failed: {author_name}",
        metadata={
            k: v for k, v in {
                "error_message": error_message,
                "batch_id": batch_id,
            }.items() if v is not None
        } or None,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Batch run events (scheduled or manual auto-download runs)
# ---------------------------------------------------------------------------


def record_run_started(
    *,
    run_id: str,
    trigger: str,
    total_candidates: int,
    slot: str | None = None,
    user_id: int | None = None,
) -> None:
    """Record the start of a batch auto-download run.

    A run groups per-book sessions (each anchored by ``search_started``) under
    a single timeline entry. ``trigger`` is ``"scheduled"`` or ``"manual"`` —
    the History tab uses it to badge the parent row.
    """
    label = "Scheduled" if trigger == "scheduled" else "Manual"
    _record(
        event_type="monitored_run_started",
        status="info",
        message=(
            f"{label} search for monitored books — {total_candidates} books to download"
            if total_candidates
            else f"{label} search for monitored books"
        ),
        metadata={
            k: v for k, v in {
                "run_id": run_id,
                "trigger": trigger,
                "total_candidates": total_candidates,
                "slot": slot,
            }.items() if v is not None
        } or None,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# File events
# ---------------------------------------------------------------------------


def record_file_imported(
    *,
    entity_id: int,
    book_provider: str | None = None,
    book_provider_id: str | None = None,
    book_title: str | None = None,
    author_name: str | None = None,
    content_type: str | None = None,
    final_path: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    _record(
        event_type="file_imported",
        entity_id=entity_id,
        book_provider=book_provider,
        book_provider_id=book_provider_id,
        book_title=book_title,
        author_name=author_name,
        content_type=content_type,
        status="success",
        message=f"File imported: {book_title or 'Unknown'}",
        metadata={"final_path": final_path} if final_path else None,
        session_id=session_id,
        user_id=user_id,
    )
