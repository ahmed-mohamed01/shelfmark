"""Orchestration layer for the monitored feature.

Composes data ops (monitored_db_ops), file ops (monitored_files), and download
ops (monitored_downloads) into complete, repeatable operations. Route handlers
and the scheduler import only from this module.

Import graph: monitored_operations → monitored_db_ops, monitored_files,
              monitored_downloads, monitored_utils, monitored_types
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.monitored_db_ops import (
    diff_sync_books,
    fetch_book_releases,
    fetch_entity_metadata,
)
from shelfmark.core.monitored_types import (
    AvailabilityData,
    BatchSyncResult,
    MonitoredEntityNotFound,
    MonitoredPathError,
    MonitoredProviderError,
    RefreshResult,
    ScanResult,
    SearchSummary,
    is_transient_provider_error,
)

logger = setup_logger(__name__)


# =============================================================================
# Author refresh
# =============================================================================


def _sync_author_core(
    db: MonitoredDB,
    *,
    entity: dict,
    user_id: int | None,
    preferred_languages: set[str] | None = None,
) -> RefreshResult:
    """Fetch books, diff-sync (soft-flag removals), apply monitor modes, clear last_error.

    Pure data operation — no WS broadcasts, no sync_status updates.
    Shared by refresh_author() (scheduler) and _run_author_sync() (background thread).

    Provider errors now raise typed exceptions (MonitoredProviderTimeoutError,
    MonitoredProviderNetworkError, etc.) which prevent diff_sync_books from
    running — no data loss on API failure.
    """
    entity_id = int(entity["id"])

    # Returns set of 'provider:provider_book_id' strings.
    # Raises MonitoredProvider*Error on any API failure — diff never runs.
    discovered_ids = fetch_entity_metadata(
        db, entity=entity, user_id=user_id, preferred_languages=preferred_languages
    )

    # Diff-based sync: soft-flag missing books, resurrect returning ones.
    diff = diff_sync_books(
        db,
        entity_id=entity_id,
        user_id=user_id,
        current_provider_ids=discovered_ids,
    )

    # Re-load books after diff to pass accurate list to monitor modes
    books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []

    # Enrich missing release dates via Google Books
    try:
        from shelfmark.core.monitored_release_enricher import enrich_release_dates

        enriched_count = enrich_release_dates(
            db, entity_id=entity_id, user_id=user_id, books=books,
        )
        if enriched_count:
            books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []
    except Exception as exc:
        logger.warning("Release date enrichment failed for entity %d: %s", entity_id, exc)

    existing_files = db.list_monitored_book_files(user_id=user_id, entity_id=entity_id) or []

    if books and existing_files:
        from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books
        existing_files = expand_monitored_file_rows_for_equivalent_books(
            books=books, file_rows=existing_files
        )

    from shelfmark.core.monitored_files import apply_monitor_modes_for_books
    apply_monitor_modes_for_books(
        db, db_user_id=user_id, entity=entity, books=books, file_rows=existing_files
    )

    db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
    return RefreshResult(
        books_upserted=len(books),
        books_removed=diff.removed,
        removed_titles=diff.removed_titles,
    )


def refresh_author(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    preferred_languages: set[str] | None = None,
) -> RefreshResult:
    """Refresh author metadata from provider and update DB state.

    Raises:
        MonitoredEntityNotFound: If the entity does not exist or is not kind='author'.
        MonitoredProviderError: If the provider is unavailable.
    """
    entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
    if entity is None or entity.get("kind") != "author":
        raise MonitoredEntityNotFound(f"Author entity {entity_id} not found")
    return _sync_author_core(db, entity=entity, user_id=user_id, preferred_languages=preferred_languages)


# =============================================================================
# Background author sync
# =============================================================================


def _resolve_preferred_languages(user_db: Any, user_id: int | None) -> "set[str] | None":
    """Resolve preferred book languages from user settings or global config."""
    from shelfmark.core.config import config as _app_config
    from shelfmark.core.monitored_utils import normalize_preferred_languages

    if user_db is not None and user_id is not None:
        try:
            settings = user_db.get_user_settings(int(user_id)) or {}
            langs = normalize_preferred_languages(settings.get("BOOK_LANGUAGE"))
            if langs:
                return langs
        except Exception:
            pass
    return normalize_preferred_languages(_app_config.get("BOOK_LANGUAGE", []))


def _broadcast(ws_manager: Any, user_id: int | None, event: str, data: dict) -> None:
    """Emit a Socket.IO event to the user's room (best-effort, never raises)."""
    if ws_manager is None:
        return
    try:
        if not ws_manager.is_enabled():
            return
        socketio = getattr(ws_manager, "socketio", None)
        if socketio is None:
            return
        if user_id is not None:
            socketio.emit(event, data, to=f"user_{user_id}")
        socketio.emit(event, data, to="admins")
    except Exception:
        pass


def _run_author_sync(
    entity_id: int,
    user_id: int | None,
    db: MonitoredDB,
    ws_manager: Any,
    user_db: Any,
) -> None:
    """Core sync routine — runs in background thread or called directly."""
    try:
        db.update_entity_sync_status(entity_id, "syncing")
        entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
        if entity is None:
            db.update_entity_sync_status(entity_id, "error")
            return

        entity_name = str(entity.get("name") or "Author")
        _broadcast(ws_manager, user_id, "monitored_sync_started",
                   {"entity_id": entity_id, "name": entity_name})

        preferred_languages = _resolve_preferred_languages(user_db, user_id)

        # Fetch, diff-sync, apply monitor modes — shared with the scheduler path.
        _broadcast(ws_manager, user_id, "monitored_sync_progress",
                   {"entity_id": entity_id, "phase": "fetching_books"})
        sync_result = _sync_author_core(db, entity=entity, user_id=user_id, preferred_languages=preferred_languages)

        # Auto file scan (best-effort — skipped if library paths not configured)
        _broadcast(ws_manager, user_id, "monitored_sync_progress",
                   {"entity_id": entity_id, "phase": "scanning_files"})
        try:
            from shelfmark.core.monitored_files import resolve_allowed_roots
            roots = resolve_allowed_roots(user_db, db_user_id=int(user_id or 0)) if user_db else []
            if roots:
                update_file_availability(db, entity_id=entity_id, user_id=user_id, allowed_roots=roots)
        except Exception:
            pass

        # ABS sync (best-effort — skipped if ABS not configured)
        try:
            from shelfmark.core.monitored_audiobookshelf_integration import sync_abs_availability_for_entity
            sync_abs_availability_for_entity(
                monitored_db=db,
                entity_id=entity_id,
                entity_name=str(entity.get("name") or ""),
                user_id=user_id,
            )
        except Exception:
            pass

        # Booklore sync (best-effort — skipped if Booklore not configured)
        try:
            from shelfmark.core.monitored_booklore_integration import sync_booklore_availability_for_entity
            sync_booklore_availability_for_entity(
                monitored_db=db,
                entity_id=entity_id,
                entity_name=str(entity.get("name") or ""),
                user_id=user_id,
            )
        except Exception:
            pass

        # Cover prefetch — broadcast phase, then fetch covers into cache
        _broadcast(ws_manager, user_id, "monitored_sync_progress",
                   {"entity_id": entity_id, "phase": "fetching_covers"})
        try:
            from shelfmark.config.env import is_covers_cache_enabled
            if is_covers_cache_enabled():
                import base64
                from urllib.parse import parse_qs, urlparse
                from shelfmark.core.image_cache import get_image_cache
                img_cache = get_image_cache()

                # Prefetch book covers
                all_books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []
                for book in all_books:
                    cover_url = book.get("cover_url")
                    book_id = book.get("provider_book_id")
                    book_provider = book.get("provider")
                    if cover_url and book_id and book_provider:
                        cache_id = f"{book_provider}_{book_id}"
                        if img_cache.get(cache_id) is None:
                            img_cache.fetch_and_cache(cache_id, cover_url)

                # Prefetch the author's own photo if stored as a proxy URL
                entity_row = db.get_monitored_entity(user_id=user_id, entity_id=entity_id) or {}
                photo_proxy = (entity_row.get("settings") or {}).get("photo_url") or ""
                if photo_proxy and "/api/covers/" in photo_proxy:
                    try:
                        parsed = urlparse(photo_proxy)
                        # path looks like /api/covers/{cache_id} or /{base}/api/covers/{cache_id}
                        photo_cache_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
                        encoded = parse_qs(parsed.query).get("url", [None])[0]
                        if photo_cache_id and encoded:
                            original_url = base64.urlsafe_b64decode(encoded.encode()).decode()
                            if img_cache.get(photo_cache_id) is None:
                                img_cache.fetch_and_cache(photo_cache_id, original_url)
                    except Exception:
                        pass
        except Exception:
            pass

        books_count = len(db.list_monitored_books(user_id=user_id, entity_id=entity_id) or [])
        db.update_entity_sync_status(entity_id, "idle")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
        complete_data: dict[str, Any] = {
            "entity_id": entity_id, "books_count": books_count, "name": entity_name,
        }
        if sync_result.books_removed > 0:
            complete_data["books_removed"] = sync_result.books_removed
            complete_data["removed_titles"] = sync_result.removed_titles
        _broadcast(ws_manager, user_id, "monitored_sync_complete", complete_data)

    except MonitoredProviderError as exc:
        error_msg = f"[{exc.error_type}] {exc}"
        db.update_entity_sync_status(entity_id, "error")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=error_msg)
        _broadcast(ws_manager, user_id, "monitored_sync_error",
                   {"entity_id": entity_id, "error": error_msg, "error_type": exc.error_type})
    except Exception as exc:
        error_msg = f"[unknown] {exc}"
        db.update_entity_sync_status(entity_id, "error")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=error_msg)
        _broadcast(ws_manager, user_id, "monitored_sync_error",
                   {"entity_id": entity_id, "error": error_msg, "error_type": "unknown"})


def start_author_background_sync(
    entity_id: int,
    user_id: int | None,
    db: MonitoredDB,
    ws_manager: Any = None,
    user_db: Any = None,
) -> None:
    """Spawn daemon thread running single-phase sync + file scan."""
    import threading
    t = threading.Thread(
        target=_run_author_sync,
        args=(entity_id, user_id, db, ws_manager, user_db),
        daemon=True,
        name=f"MonitoredSync-{entity_id}",
    )
    t.start()


# =============================================================================
# Batch sync (shared by scheduler and sync-all route)
# =============================================================================


def _entity_cover(entity: dict) -> str | None:
    """Extract a cover/photo URL from an entity dict (best-effort)."""
    settings = entity.get("settings")
    if isinstance(settings, dict):
        photo = settings.get("photo_url")
        if isinstance(photo, str) and photo.strip():
            return photo.strip()
    cover = entity.get("best_book_cover_url")
    return cover if isinstance(cover, str) and cover.strip() else None


def _record_sync_failure(
    db: MonitoredDB,
    result: BatchSyncResult,
    *,
    eid: int,
    ename: str,
    exc: BaseException,
) -> None:
    """Record a failed entity sync into *result* and DB."""
    error_msg = f"[{getattr(exc, 'error_type', 'unknown')}] {exc}"
    db.update_monitored_entity_check(entity_id=eid, last_error=error_msg)
    result.failed += 1
    result.info.append({
        "entity_id": eid, "entity_name": ename,
        "message": error_msg, "is_error": True,
    })
    logger.warning("Batch sync failed entity_id=%s: %s", eid, error_msg)


def _record_sync_success(
    result: BatchSyncResult,
    sync_res: RefreshResult,
    *,
    eid: int,
    ename: str,
) -> None:
    """Record a successful entity sync into *result*."""
    result.successful += 1
    if sync_res.books_removed > 0:
        result.info.append({
            "entity_id": eid, "entity_name": ename,
            "message": f"{sync_res.books_removed} book(s) removed from provider",
            "removed_titles": sync_res.removed_titles,
        })


def run_batch_sync(
    entities: list[tuple[int, dict]],
    db: MonitoredDB,
    ws_manager: Any,
    user_db: Any,
    batch_id: str,
) -> BatchSyncResult:
    """Iterate *entities*, sync each, broadcast batch-level progress.

    Each entry in *entities* is ``(user_id, entity_dict)``.
    Failed entities with transient errors are retried once after all others.
    """
    total = len(entities)
    result = BatchSyncResult(total=total)

    _broadcast(ws_manager, None, "monitored_batch_sync_started",
               {"batch_id": batch_id, "total": total})

    retry_queue: list[tuple[int, int, dict]] = []  # (index, user_id, entity)

    for idx, (uid, entity) in enumerate(entities, 1):
        eid = int(entity.get("id") or 0)
        ename = str(entity.get("name") or "Author")

        _broadcast(ws_manager, uid, "monitored_batch_sync_progress", {
            "batch_id": batch_id, "index": idx, "total": total,
            "entity_id": eid, "entity_name": ename,
            "entity_cover": _entity_cover(entity),
        })

        preferred_languages = _resolve_preferred_languages(user_db, uid)
        try:
            sync_res = _sync_author_core(
                db, entity=entity, user_id=uid,
                preferred_languages=preferred_languages,
            )
            _record_sync_success(result, sync_res, eid=eid, ename=ename)
        except MonitoredProviderError as exc:
            if is_transient_provider_error(exc):
                error_msg = f"[{exc.error_type}] {exc}"
                db.update_monitored_entity_check(entity_id=eid, last_error=error_msg)
                retry_queue.append((idx, uid, entity))
            else:
                _record_sync_failure(db, result, eid=eid, ename=ename, exc=exc)
        except Exception as exc:
            _record_sync_failure(db, result, eid=eid, ename=ename, exc=exc)

    # Retry transient failures once
    if retry_queue:
        result.retried = len(retry_queue)
        for idx, uid, entity in retry_queue:
            eid = int(entity.get("id") or 0)
            ename = str(entity.get("name") or "Author")
            _broadcast(ws_manager, uid, "monitored_batch_sync_progress", {
                "batch_id": batch_id, "index": idx, "total": total,
                "entity_id": eid, "entity_name": f"{ename} (retry)",
                "entity_cover": _entity_cover(entity),
            })
            preferred_languages = _resolve_preferred_languages(user_db, uid)
            try:
                sync_res = _sync_author_core(
                    db, entity=entity, user_id=uid,
                    preferred_languages=preferred_languages,
                )
                _record_sync_success(result, sync_res, eid=eid, ename=ename)
                result.retry_succeeded += 1
            except Exception as exc:
                _record_sync_failure(db, result, eid=eid, ename=ename, exc=exc)

    _broadcast(ws_manager, None, "monitored_batch_sync_complete", {
        "batch_id": batch_id,
        "total": total,
        "successful": result.successful,
        "failed": result.failed,
        "info": result.info,
        "retried": result.retried,
        "retry_succeeded": result.retry_succeeded,
    })

    return result


# =============================================================================
# Book availability
# =============================================================================


def compute_book_availability(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
) -> AvailabilityData:
    """Load books and files, expand alias-equivalent books, summarize availability.

    Returns an AvailabilityData with enriched books, expanded files, and a
    keyed availability dict for fast per-book lookups.
    """
    from shelfmark.core.monitored_files import (
        expand_monitored_file_rows_for_equivalent_books,
        summarize_monitored_book_availability,
    )

    books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []
    files = db.list_monitored_book_files(user_id=user_id, entity_id=entity_id) or []

    if books and files:
        files = expand_monitored_file_rows_for_equivalent_books(books=books, file_rows=files)

    availability_by_book = summarize_monitored_book_availability(file_rows=files, user_id=user_id)

    return AvailabilityData(
        books=books,
        files=files,
        availability_by_book=availability_by_book,
    )


def _resolve_search_skip_reason(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    provider: str,
    provider_book_id: str,
    content_type: str,
    availability_payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return skip reason for monitored auto-search when files already exist.

    Priority:
    1) Shelfmark-managed history final_path exists on disk.
    2) Canonical monitored availability says requested content already exists.
    """

    history_rows = db.list_monitored_book_download_history(
        user_id=user_id,
        entity_id=entity_id,
        provider=provider,
        provider_book_id=provider_book_id,
        limit=20,
    ) or []
    for history_row in history_rows:
        final_path = str(history_row.get("final_path") or "").strip()
        if not final_path:
            continue
        try:
            if Path(final_path).exists():
                return "history_final_path_exists", final_path
        except Exception:
            continue

    has_file_key = "has_ebook_available" if content_type == "ebook" else "has_audiobook_available"
    if bool(availability_payload.get(has_file_key)):
        return "existing_file", None

    return None, None


# =============================================================================
# File scanning
# =============================================================================


def update_file_availability(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    allowed_roots: list[Path],
) -> ScanResult:
    """Validate configured paths, scan files, apply monitor modes, update timestamps.

    Raises:
        MonitoredEntityNotFound: If the entity does not exist.
        MonitoredPathError: If neither ebook nor audiobook dir is configured.
    """
    from shelfmark.core.monitored_files import (
        apply_monitor_modes_for_books,
        clear_entity_matched_files,
        path_within_allowed_roots,
        scan_monitored_author_files,
    )

    entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
    if entity is None:
        raise MonitoredEntityNotFound(f"Entity {entity_id} not found")

    settings = entity.get("settings") or {}
    author_name = str(entity.get("name") or "").strip()

    ebook_dir_raw = settings.get("ebook_author_dir")
    ebook_dir = str(ebook_dir_raw).strip().rstrip("/") if isinstance(ebook_dir_raw, str) else ""
    audiobook_dir_raw = settings.get("audiobook_author_dir")
    audiobook_dir = str(audiobook_dir_raw).strip().rstrip("/") if isinstance(audiobook_dir_raw, str) else ""

    if (not ebook_dir or not ebook_dir.startswith("/")) and (
        not audiobook_dir or not audiobook_dir.startswith("/")
    ):
        raise MonitoredPathError("ebook_author_dir or audiobook_author_dir must be set")

    ebook_path: Path | None = None
    audiobook_path: Path | None = None
    warnings: dict[str, str] = {}

    if ebook_dir:
        try:
            p = Path(ebook_dir).resolve()
        except Exception:
            raise MonitoredPathError("Invalid ebook_author_dir")
        if not path_within_allowed_roots(path=p, roots=allowed_roots):
            raise MonitoredPathError("ebook_author_dir is not within allowed roots")
        if not p.exists() or not p.is_dir():
            warnings["ebook_author_dir"] = "Directory not found"
        else:
            ebook_path = p

    if audiobook_dir:
        try:
            p = Path(audiobook_dir).resolve()
        except Exception:
            raise MonitoredPathError("Invalid audiobook_author_dir")
        if not path_within_allowed_roots(path=p, roots=allowed_roots):
            raise MonitoredPathError("audiobook_author_dir is not within allowed roots")
        if not p.exists() or not p.is_dir():
            warnings["audiobook_author_dir"] = "Directory not found"
        else:
            audiobook_path = p

    if ebook_path is None and audiobook_path is None:
        try:
            clear_entity_matched_files(monitored_db=db, user_id=user_id, entity_id=entity_id)
        except Exception as exc:
            logger.warning("Failed clearing matched files entity_id=%s: %s", entity_id, exc)
        raise MonitoredPathError("directories_not_found")

    books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []

    scan_data = scan_monitored_author_files(
        monitored_db=db,
        user_id=user_id,
        entity_id=entity_id,
        books=books,
        author_name=author_name,
        ebook_path=ebook_path,
        audiobook_path=audiobook_path,
    )
    existing_files = scan_data.get("existing_files") or []

    apply_monitor_modes_for_books(
        db, db_user_id=user_id, entity=entity, books=books, file_rows=existing_files
    )

    # Update scan timestamps
    scan_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    merged_settings = dict(settings)
    if ebook_path is not None:
        merged_settings["last_ebook_scan_at"] = scan_at
    if audiobook_path is not None:
        merged_settings["last_audiobook_scan_at"] = scan_at
    merged_settings.pop("last_ebook_scan_error", None)
    merged_settings.pop("last_audiobook_scan_error", None)
    db.create_monitored_entity(
        user_id=user_id,
        kind=str(entity.get("kind") or "author"),
        provider=entity.get("provider"),
        provider_id=entity.get("provider_id"),
        name=str(entity.get("name") or "").strip() or "Unknown",
        enabled=bool(int(entity.get("enabled") or 0)),
        settings=merged_settings,
    )

    return ScanResult(
        entity_id=entity_id,
        matched=scan_data.get("matched") or [],
        unmatched=scan_data.get("unmatched") or [],
        missing_books=scan_data.get("missing_books") or [],
        scanned_ebook_files=int(scan_data.get("scanned_ebook_files") or 0),
        scanned_audio_folders=int(scan_data.get("scanned_audio_folders") or 0),
        ebook_dir=str(ebook_path) if ebook_path else None,
        audiobook_dir=str(audiobook_path) if audiobook_path else None,
        warnings=warnings,
    )


def record_scan_error(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    error: Exception,
    ebook_dir: str,
    audiobook_dir: str,
) -> None:
    """Persist scan error to entity settings. Called from route on scan failure."""
    entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
    if entity is None:
        return
    settings = dict(entity.get("settings") or {})
    if ebook_dir:
        settings["last_ebook_scan_error"] = str(error)
    if audiobook_dir:
        settings["last_audiobook_scan_error"] = str(error)
    try:
        db.create_monitored_entity(
            user_id=user_id,
            kind=str(entity.get("kind") or "author"),
            provider=entity.get("provider"),
            provider_id=entity.get("provider_id"),
            name=str(entity.get("name") or "").strip() or "Unknown",
            enabled=bool(int(entity.get("enabled") or 0)),
            settings=settings,
        )
    except Exception:
        pass


# =============================================================================
# Missing book search
# =============================================================================


def resolve_book_auto_search_precheck(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    provider: str,
    provider_book_id: str,
    content_type: str,
) -> tuple[bool, str | None, str | None]:
    """Return whether monitored auto-search should skip this book.

    Returns tuple: (skip, reason, detail)
    - skip=True when a previously downloaded final_path exists or availability says
      requested content is already present.
    - reason in {"history_final_path_exists", "existing_file"}
    """
    normalized_provider = str(provider or "").strip()
    normalized_provider_book_id = str(provider_book_id or "").strip()
    normalized_content_type = str(content_type or "").strip().lower()
    if normalized_content_type not in {"ebook", "audiobook"}:
        normalized_content_type = "ebook"

    if not normalized_provider or not normalized_provider_book_id:
        return False, None, None

    entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
    if entity is None:
        raise MonitoredEntityNotFound(f"Entity {entity_id} not found")

    availability = compute_book_availability(db, entity_id=entity_id, user_id=user_id)
    availability_payload = availability.availability_by_book.get((normalized_provider, normalized_provider_book_id), {})
    reason, detail = _resolve_search_skip_reason(
        db,
        entity_id=entity_id,
        user_id=user_id,
        provider=normalized_provider,
        provider_book_id=normalized_provider_book_id,
        content_type=normalized_content_type,
        availability_payload=availability_payload,
    )
    return bool(reason), reason, detail


def search_missing_books(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    content_type: str = "ebook",
    min_match_score: float | None = None,
) -> SearchSummary:
    """Find monitored books with no existing file and queue downloads for them.

    1. Loads current availability for the entity.
    2. Filters to books that are monitored for content_type and have no file.
    3. For each candidate: fetches releases, calls process_monitored_book().
    4. Returns a SearchSummary with counts.

    Raises:
        MonitoredEntityNotFound: If the entity does not exist or is not kind='author'.
    """
    from shelfmark.core.monitored_downloads import process_monitored_book, write_monitored_book_attempt
    from shelfmark.core.monitored_release_scoring import is_book_released
    from shelfmark.metadata_providers import BookMetadata

    entity = db.get_monitored_entity(user_id=user_id, entity_id=entity_id)
    if entity is None or entity.get("kind") != "author":
        raise MonitoredEntityNotFound(f"Author entity {entity_id} not found")

    # Resolve output overrides from entity settings so auto-downloads land in
    # the correct author/series folder — mirrors enrich_release_for_monitored().
    settings = entity.get("settings") if isinstance(entity, dict) else None
    if not isinstance(settings, dict):
        settings = {}
    dest_override: str | None = None
    org_override: str | None = "organize"
    tmpl_override: str | None = None
    dir_key = "audiobook_author_dir" if content_type == "audiobook" else "ebook_author_dir"
    author_dir = settings.get(dir_key)
    if isinstance(author_dir, str) and author_dir.strip().startswith("/"):
        dest_override = author_dir.strip().rstrip("/")
        tmpl_override = "{Series}/{Title} - {Author} ({Year})"
    else:
        tmpl_override = "{Author}/{Series}/{Title} ({Year})"

    availability = compute_book_availability(db, entity_id=entity_id, user_id=user_id)
    monitor_col = "monitor_ebook" if content_type == "ebook" else "monitor_audiobook"
    has_file_key = "has_ebook_available" if content_type == "ebook" else "has_audiobook_available"

    candidates = [
        row for row in availability.books
        if bool(int(row.get(monitor_col) or 0))
        and str(row.get("state") or "") != "removed_from_provider"
        and str(row.get("provider") or "").strip()
        and str(row.get("provider_book_id") or "").strip()
    ]

    summary = SearchSummary(
        entity_id=entity_id,
        content_type=content_type,
        total_candidates=len(candidates),
    )

    if not candidates:
        return summary

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for row in candidates:
        provider = str(row.get("provider") or "").strip()
        provider_book_id = str(row.get("provider_book_id") or "").strip()
        book_title = str(row.get("title") or "").strip() or None
        availability_payload = availability.availability_by_book.get((provider, provider_book_id), {})

        skip_reason, skip_detail = _resolve_search_skip_reason(
            db,
            entity_id=entity_id,
            user_id=user_id,
            provider=provider,
            provider_book_id=provider_book_id,
            content_type=content_type,
            availability_payload=availability_payload,
        )
        if skip_reason == "history_final_path_exists":
            summary.skipped_history_final_path_exists += 1
            write_monitored_book_attempt(
                db,
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
                attempted_at=now_iso,
                status="no_match",
                error_message="skip_existing_file_history_final_path_exists",
            )
            continue
        if skip_reason == "existing_file":
            summary.skipped_existing_file += 1
            write_monitored_book_attempt(
                db,
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
                attempted_at=now_iso,
                status="no_match",
                error_message="skip_existing_file",
            )
            continue

        release_date_raw = str(row.get("release_date") or "").strip()
        is_released, parsed_release_date = is_book_released(release_date_raw)
        if parsed_release_date is None and len(release_date_raw) == 4 and release_date_raw.isdigit():
            try:
                parsed_release_date = date(int(release_date_raw), 1, 1)
                is_released = parsed_release_date <= datetime.now(timezone.utc).date()
            except ValueError:
                pass
        if not is_released:
            summary.unreleased += 1
            unreleased_message = "Book is unreleased"
            if parsed_release_date is not None:
                unreleased_message = f"Book is unreleased until {parsed_release_date.isoformat()}"
            write_monitored_book_attempt(
                db,
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
                attempted_at=now_iso,
                status="not_released",
                error_message=unreleased_message,
            )
            continue

        try:
            # Build BookMetadata from DB row — data is already stored from sync
            authors_raw = row.get("authors") or ""
            authors_list = [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []
            book = BookMetadata(
                provider=provider,
                provider_id=provider_book_id,
                title=str(row.get("title") or ""),
                authors=authors_list,
                isbn_13=row.get("isbn_13"),
                isbn_10=row.get("isbn_10"),
                series_name=row.get("series_name"),
                series_position=row.get("series_position"),
                series_count=row.get("series_count"),
                release_date=row.get("release_date"),
                language=row.get("language"),
            )

            release_dicts = fetch_book_releases(book, content_type=content_type)

            # Attach release_date from DB row so process_monitored_book can check unreleased
            for rd in release_dicts:
                rd["release_date"] = row.get("release_date")

            if not release_dicts:
                summary.no_match += 1
                write_monitored_book_attempt(
                    db, user_id=user_id, entity_id=entity_id,
                    provider=provider, provider_book_id=provider_book_id,
                    content_type=content_type, attempted_at=now_iso,
                    status="no_match",
                )
                continue

            success, message = process_monitored_book(
                release_dicts,
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
                min_match_score=min_match_score,
                destination_override=dest_override,
                file_organization_override=org_override,
                template_override=tmpl_override,
            )

            if success:
                summary.queued += 1
            elif message == "Already in queue":
                pass
            elif "unreleased" in message.lower():
                summary.unreleased += 1
            elif "match score" in message.lower() or "no valid" in message.lower():
                summary.below_cutoff += 1
            else:
                summary.failed += 1

        except Exception as exc:
            summary.failed += 1
            write_monitored_book_attempt(
                db, user_id=user_id, entity_id=entity_id,
                provider=provider, provider_book_id=provider_book_id,
                content_type=content_type, attempted_at=now_iso,
                status="error", error_message=str(exc),
            )

    return summary
