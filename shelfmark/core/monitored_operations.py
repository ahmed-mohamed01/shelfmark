"""Orchestration layer for the monitored feature.

Composes data ops (monitored_db_ops), file ops (monitored_files), and download
ops (monitored_downloads) into complete, repeatable operations. Route handlers
and the scheduler import only from this module.

Import graph: monitored_operations → monitored_db_ops, monitored_files,
              monitored_downloads, monitored_utils, monitored_types
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db_ops import (
    diff_sync_books,
    fetch_book_releases,
    fetch_entity_metadata,
)
from shelfmark.core.monitored_types import (
    AvailabilityData,
    AvailabilitySyncResult,
    BatchSyncResult,
    MonitoredEntityNotFoundError,
    MonitoredPathError,
    MonitoredProviderError,
    RefreshResult,
    ScanResult,
    SearchSummary,
    is_transient_provider_error,
)

if TYPE_CHECKING:
    from shelfmark.core.monitored_db import MonitoredDB

logger = setup_logger(__name__)


# =============================================================================
# Author refresh
# =============================================================================


def sync_availability_sources(
    db: MonitoredDB,
    *,
    entity_id: int,
    entity_name: str,
    user_id: int | None,
    user_db: Any,
) -> AvailabilitySyncResult:
    """Refresh file-availability state for one entity across all sources.

    Runs the three availability-source checks, each best-effort:

      1. Filesystem scan (skipped when no library roots are configured).
      2. AudioBookShelf sync (skipped when integration is disabled / no config).
      3. Grimmory sync (same).

    All three populate ``monitored_book_files`` via the unified attribution
    evaluator. Shared by the per-author sync (``_run_author_sync``), the
    batch sync (``run_batch_sync``), and the manual per-author scan route
    so all three paths behave identically.

    Errors are captured (not raised) so any one source failing doesn't
    prevent the others from running. Callers that need HTTP error responses
    inspect ``result.fs_error``; background sync paths ignore it.
    """
    result = AvailabilitySyncResult()

    # Filesystem scan — best-effort, skipped if library paths not configured.
    try:
        from shelfmark.core.monitored_files import resolve_allowed_roots

        roots = resolve_allowed_roots(user_db, db_user_id=int(user_id or 0)) if user_db else []
        if roots:
            result.fs_scan = update_file_availability(
                db,
                entity_id=entity_id,
                user_id=user_id,
                allowed_roots=roots,
            )
        else:
            logger.warning("File scan skipped for entity %s: no allowed roots resolved", entity_id)
    except (MonitoredEntityNotFoundError, MonitoredPathError) as exc:
        # Route handlers translate these into specific HTTP responses.
        result.fs_error = exc
    except Exception as exc:  # noqa: BLE001
        result.fs_error = exc
        logger.warning("File scan failed for entity %s: %s", entity_id, exc)

    # ABS sync — best-effort, skipped if ABS not configured.
    try:
        from shelfmark.core.monitored_audiobookshelf_integration import (
            sync_abs_availability_for_entity,
        )

        result.abs = (
            sync_abs_availability_for_entity(
                monitored_db=db,
                entity_id=entity_id,
                entity_name=entity_name,
                user_id=user_id,
            )
            or result.abs
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ABS availability sync failed for entity %s: %s", entity_id, exc)
        result.abs = {"abs_skipped": True, "reason": "error"}

    # Grimmory sync — best-effort, skipped if Grimmory not configured.
    try:
        from shelfmark.core.monitored_grimmory_integration import (
            sync_grimmory_availability_for_entity,
        )

        result.gm = (
            sync_grimmory_availability_for_entity(
                monitored_db=db,
                entity_id=entity_id,
                entity_name=entity_name,
                user_id=user_id,
            )
            or result.gm
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Grimmory availability sync failed for entity %s: %s", entity_id, exc)
        result.gm = {"gm_skipped": True, "reason": "error"}

    return result


def _sync_author_core(
    db: MonitoredDB,
    *,
    entity: dict,
    user_id: int | None,
    preferred_languages: set[str] | None = None,
) -> RefreshResult:
    """Fetch books, diff-sync (soft-flag removals), apply monitor modes, clear last_error.

    Pure data operation — no WS broadcasts, no sync_status updates.
    Shared by _run_author_sync() (background thread) and the batch scheduler.

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
    books = db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []

    # Enrich missing release dates via Google Books
    try:
        from shelfmark.core.monitored_release_enricher import enrich_release_dates

        enriched_count = enrich_release_dates(
            db,
            entity_id=entity_id,
            user_id=user_id,
            books=books,
        )
        if enriched_count:
            books = db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Release date enrichment failed for entity %d: %s", entity_id, exc)

    existing_files = db.list_monitored_book_files(user_ids=[user_id], entity_id=entity_id) or []

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
        books_added=diff.added,
        books_removed=diff.removed,
        removed_titles=diff.removed_titles,
    )


# =============================================================================
# Background author sync
# =============================================================================


def _resolve_preferred_languages(user_db: Any, user_id: int | None) -> set[str] | None:
    """Resolve preferred book languages from user settings or global config."""
    from shelfmark.core.config import config as _app_config
    from shelfmark.core.monitored_utils import normalize_preferred_languages

    if user_db is not None and user_id is not None:
        try:
            settings = user_db.get_user_settings(int(user_id)) or {}
            langs = normalize_preferred_languages(settings.get("BOOK_LANGUAGE"))
            if langs:
                return langs
        except Exception:  # noqa: BLE001, S110
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
    except Exception:  # noqa: BLE001, S110
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
        entity = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id)
        if entity is None:
            db.update_entity_sync_status(entity_id, "error")
            return

        entity_name = str(entity.get("name") or "Author")
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_started",
            {"entity_id": entity_id, "name": entity_name},
        )

        preferred_languages = _resolve_preferred_languages(user_db, user_id)

        # Fetch, diff-sync, apply monitor modes — shared with the scheduler path.
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_progress",
            {"entity_id": entity_id, "phase": "fetching_books"},
        )
        sync_result = _sync_author_core(
            db, entity=entity, user_id=user_id, preferred_languages=preferred_languages
        )

        # File availability across all sources (filesystem + ABS + Grimmory).
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_progress",
            {"entity_id": entity_id, "phase": "scanning_files"},
        )
        sync_availability_sources(
            db,
            entity_id=entity_id,
            entity_name=entity_name,
            user_id=user_id,
            user_db=user_db,
        )

        # Cover prefetch — broadcast phase, then fetch covers into cache
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_progress",
            {"entity_id": entity_id, "phase": "fetching_covers"},
        )
        try:
            from shelfmark.config.env import is_covers_cache_enabled

            if is_covers_cache_enabled():
                import base64
                from urllib.parse import parse_qs, urlparse

                from shelfmark.core.image_cache import get_image_cache

                img_cache = get_image_cache()

                # Prefetch book covers. Each uncached cover is an independent,
                # blocking HTTP fetch — serially this was N × round-trip added to
                # the refresh wall-clock. Fan out across a small pool; the image
                # cache writes to a distinct path per id, so concurrent puts are
                # safe. Workers kept modest to stay polite to the cover host.
                all_books = db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []
                covers_to_fetch: list[tuple[str, str]] = []
                for book in all_books:
                    cover_url = book.get("cover_url")
                    book_id = book.get("provider_book_id")
                    book_provider = book.get("provider")
                    if cover_url and book_id and book_provider:
                        cache_id = f"{book_provider}_{book_id}"
                        if img_cache.get(cache_id) is None:
                            covers_to_fetch.append((cache_id, cover_url))

                if covers_to_fetch:
                    from shelfmark.core.monitored_concurrency import bounded_map

                    def _fetch_cover(job: tuple[str, str]) -> None:
                        cid, curl = job
                        try:
                            img_cache.fetch_and_cache(cid, curl)
                        except Exception as cover_exc:  # noqa: BLE001 — cover prefetch is best-effort.
                            logger.debug("Cover prefetch failed for %s: %s", cid, cover_exc)

                    bounded_map(_fetch_cover, covers_to_fetch)

                # Prefetch the author's own photo if stored as a proxy URL
                entity_row = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id) or {}
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
                    except Exception:  # noqa: BLE001, S110
                        pass
        except Exception:  # noqa: BLE001, S110
            pass

        books_count = len(db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or [])
        db.update_entity_sync_status(entity_id, "idle")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
        complete_data: dict[str, Any] = {
            "entity_id": entity_id,
            "books_count": books_count,
            "name": entity_name,
        }
        if sync_result.books_removed > 0:
            complete_data["books_removed"] = sync_result.books_removed
            complete_data["removed_titles"] = sync_result.removed_titles
        _broadcast(ws_manager, user_id, "monitored_sync_complete", complete_data)
        try:
            from shelfmark.core.monitored_history import record_author_synced

            record_author_synced(
                entity_id=entity_id,
                author_name=entity_name,
                books_added=sync_result.books_added,
                books_removed=sync_result.books_removed,
                total_books=books_count,
                user_id=user_id,
                triggered_by="manual",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to record author_synced event for %s: %s", entity_id, exc)

    except MonitoredProviderError as exc:
        error_msg = f"[{exc.error_type}] {exc}"
        db.update_entity_sync_status(entity_id, "error")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=error_msg)
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_error",
            {"entity_id": entity_id, "error": error_msg, "error_type": exc.error_type},
        )
        try:
            from shelfmark.core.monitored_history import record_author_sync_failed

            record_author_sync_failed(
                entity_id=entity_id,
                author_name=entity_name,
                error_message=error_msg,
                user_id=user_id,
                triggered_by="manual",
            )
        except Exception as log_exc:  # noqa: BLE001
            logger.debug("Failed to record author_sync_failed event for %s: %s", entity_id, log_exc)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"[unknown] {exc}"
        db.update_entity_sync_status(entity_id, "error")
        db.update_monitored_entity_check(entity_id=entity_id, last_error=error_msg)
        _broadcast(
            ws_manager,
            user_id,
            "monitored_sync_error",
            {"entity_id": entity_id, "error": error_msg, "error_type": "unknown"},
        )
        try:
            from shelfmark.core.monitored_history import record_author_sync_failed

            record_author_sync_failed(
                entity_id=entity_id,
                author_name=entity_name,
                error_message=error_msg,
                user_id=user_id,
                triggered_by="manual",
            )
        except Exception as log_exc:  # noqa: BLE001
            logger.debug("Failed to record author_sync_failed event for %s: %s", entity_id, log_exc)


def start_author_background_sync(
    entity_id: int,
    user_id: int | None,
    db: MonitoredDB,
    ws_manager: Any = None,
    user_db: Any = None,
) -> None:
    """Spawn daemon thread running single-phase sync + file scan.

    Callers are responsible for checking/setting sync_status before calling.
    """
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
    user_id: int | None = None,
    batch_id: str | None = None,
    triggered_by: str | None = None,
) -> None:
    """Record a failed entity sync into *result* and DB."""
    error_msg = f"[{getattr(exc, 'error_type', 'unknown')}] {exc}"
    db.update_monitored_entity_check(entity_id=eid, last_error=error_msg)
    result.failed += 1
    result.info.append(
        {
            "entity_id": eid,
            "entity_name": ename,
            "message": error_msg,
            "is_error": True,
        }
    )
    logger.warning("Batch sync failed entity_id=%s: %s", eid, error_msg)
    try:
        from shelfmark.core.monitored_history import record_author_sync_failed

        record_author_sync_failed(
            entity_id=eid,
            author_name=ename,
            error_message=error_msg,
            batch_id=batch_id,
            user_id=user_id,
            triggered_by=triggered_by,
        )
    except Exception as log_exc:  # noqa: BLE001
        logger.debug("Failed to record author_sync_failed event for %s: %s", eid, log_exc)


def _record_sync_success(
    result: BatchSyncResult,
    sync_res: RefreshResult,
    *,
    eid: int,
    ename: str,
    user_id: int | None = None,
    batch_id: str | None = None,
    triggered_by: str | None = None,
) -> None:
    """Record a successful entity sync into *result*."""
    result.successful += 1
    if sync_res.books_removed > 0:
        result.info.append(
            {
                "entity_id": eid,
                "entity_name": ename,
                "message": f"{sync_res.books_removed} book(s) removed from provider",
                "removed_titles": sync_res.removed_titles,
            }
        )
    try:
        from shelfmark.core.monitored_history import record_author_synced

        record_author_synced(
            entity_id=eid,
            author_name=ename,
            books_added=sync_res.books_added,
            books_removed=sync_res.books_removed,
            total_books=sync_res.books_upserted,
            batch_id=batch_id,
            user_id=user_id,
            triggered_by=triggered_by,
        )
    except Exception as log_exc:  # noqa: BLE001
        logger.debug("Failed to record author_synced event for %s: %s", eid, log_exc)


def run_batch_sync(
    entities: list[tuple[int, dict]],
    db: MonitoredDB,
    ws_manager: Any,
    user_db: Any,
    batch_id: str,
    triggered_by: str = "manual",
) -> BatchSyncResult:
    """Iterate *entities*, sync each, broadcast batch-level progress.

    Each entry in *entities* is ``(user_id, entity_dict)``.
    Failed entities with transient errors are retried once after all others.
    """
    total = len(entities)
    result = BatchSyncResult(total=total)

    _broadcast(
        ws_manager, None, "monitored_batch_sync_started", {"batch_id": batch_id, "total": total}
    )

    retry_queue: list[tuple[int, int, dict]] = []  # (index, user_id, entity)

    # Sync authors concurrently with a small pool. Each author is independent:
    # its DB writes are batched/short and serialize safely on the single SQLite
    # writer (busy_timeout covers contention), and its network phases (Hardcover
    # pages, ABS, Grimmory, covers) are already internally concurrent — so
    # running a few authors at once overlaps one author's network waits with
    # another's work. The matcher is GIL-bound, so CPU itself doesn't speed up;
    # the win is I/O overlap. IMPORTANT: only the worker body runs off-thread;
    # every _broadcast and shared-result mutation happens here on the calling
    # thread, so we never emit SocketIO from pool threads (unsafe under gevent
    # async_mode) and need no locks on `result` / `retry_queue`.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _sync_one(uid: int, entity: dict) -> Any:
        eid = int(entity.get("id") or 0)
        ename = str(entity.get("name") or "Author")
        preferred_languages = _resolve_preferred_languages(user_db, uid)
        sync_res = _sync_author_core(
            db, entity=entity, user_id=uid, preferred_languages=preferred_languages
        )
        sync_availability_sources(
            db, entity_id=eid, entity_name=ename, user_id=uid, user_db=user_db
        )
        return sync_res

    workers = max(1, min(3, total))
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_sync_one, uid, entity): (idx, uid, entity)
            for idx, (uid, entity) in enumerate(entities, 1)
        }
        for fut in as_completed(future_map):
            idx, uid, entity = future_map[fut]
            eid = int(entity.get("id") or 0)
            ename = str(entity.get("name") or "Author")
            completed += 1

            _broadcast(
                ws_manager,
                uid,
                "monitored_batch_sync_progress",
                {
                    "batch_id": batch_id,
                    "index": completed,
                    "total": total,
                    "entity_id": eid,
                    "entity_name": ename,
                    "entity_cover": _entity_cover(entity),
                },
            )

            try:
                sync_res = fut.result()
                _record_sync_success(
                    result,
                    sync_res,
                    eid=eid,
                    ename=ename,
                    user_id=uid,
                    batch_id=batch_id,
                    triggered_by=triggered_by,
                )
            except MonitoredProviderError as exc:
                if is_transient_provider_error(exc):
                    error_msg = f"[{exc.error_type}] {exc}"
                    db.update_monitored_entity_check(entity_id=eid, last_error=error_msg)
                    retry_queue.append((idx, uid, entity))
                else:
                    _record_sync_failure(
                        db,
                        result,
                        eid=eid,
                        ename=ename,
                        exc=exc,
                        user_id=uid,
                        batch_id=batch_id,
                        triggered_by=triggered_by,
                    )
            except Exception as exc:  # noqa: BLE001
                _record_sync_failure(
                    db,
                    result,
                    eid=eid,
                    ename=ename,
                    exc=exc,
                    user_id=uid,
                    batch_id=batch_id,
                    triggered_by=triggered_by,
                )

    # Retry transient failures once
    if retry_queue:
        result.retried = len(retry_queue)
        for idx, uid, entity in retry_queue:
            eid = int(entity.get("id") or 0)
            ename = str(entity.get("name") or "Author")
            _broadcast(
                ws_manager,
                uid,
                "monitored_batch_sync_progress",
                {
                    "batch_id": batch_id,
                    "index": idx,
                    "total": total,
                    "entity_id": eid,
                    "entity_name": f"{ename} (retry)",
                    "entity_cover": _entity_cover(entity),
                },
            )
            preferred_languages = _resolve_preferred_languages(user_db, uid)
            try:
                sync_res = _sync_author_core(
                    db,
                    entity=entity,
                    user_id=uid,
                    preferred_languages=preferred_languages,
                )
                sync_availability_sources(
                    db,
                    entity_id=eid,
                    entity_name=ename,
                    user_id=uid,
                    user_db=user_db,
                )
                _record_sync_success(
                    result,
                    sync_res,
                    eid=eid,
                    ename=ename,
                    user_id=uid,
                    batch_id=batch_id,
                    triggered_by=triggered_by,
                )
                result.retry_succeeded += 1
            except Exception as exc:  # noqa: BLE001
                _record_sync_failure(
                    db,
                    result,
                    eid=eid,
                    ename=ename,
                    exc=exc,
                    user_id=uid,
                    batch_id=batch_id,
                    triggered_by=triggered_by,
                )

    _broadcast(
        ws_manager,
        None,
        "monitored_batch_sync_complete",
        {
            "batch_id": batch_id,
            "total": total,
            "successful": result.successful,
            "failed": result.failed,
            "info": result.info,
            "retried": result.retried,
            "retry_succeeded": result.retry_succeeded,
        },
    )

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

    books = db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []
    files = db.list_monitored_book_files(user_ids=[user_id], entity_id=entity_id) or []

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

    history_rows = (
        db.list_monitored_book_download_history(
            user_ids=[user_id],
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            limit=20,
        )
        or []
    )
    for history_row in history_rows:
        final_path = str(history_row.get("final_path") or "").strip()
        if not final_path:
            continue
        try:
            if Path(final_path).exists():
                return "history_final_path_exists", final_path
        except Exception:  # noqa: BLE001
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
        MonitoredEntityNotFoundError: If the entity does not exist.
        MonitoredPathError: If neither ebook nor audiobook dir is configured.
    """
    from shelfmark.core.monitored_files import (
        apply_monitor_modes_for_books,
        clear_entity_matched_files,
        path_within_allowed_roots,
        scan_monitored_author_files,
    )

    entity = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id)
    if entity is None:
        raise MonitoredEntityNotFoundError(f"Entity {entity_id} not found")

    settings = entity.get("settings") or {}
    author_name = str(entity.get("name") or "").strip()

    ebook_dir_raw = settings.get("ebook_author_dir")
    ebook_dir = str(ebook_dir_raw).strip().rstrip("/") if isinstance(ebook_dir_raw, str) else ""
    audiobook_dir_raw = settings.get("audiobook_author_dir")
    audiobook_dir = (
        str(audiobook_dir_raw).strip().rstrip("/") if isinstance(audiobook_dir_raw, str) else ""
    )

    # Auto-derive scan paths from default library destinations when not explicitly set.
    # Downloads without explicit author_dir use the template "{Author}/{Series}/{Title}"
    # relative to the default destination, so we should scan there too.
    if (not ebook_dir or not ebook_dir.startswith("/")) and author_name:
        try:
            from shelfmark.core.utils import get_destination

            default_dest = str(get_destination(is_audiobook=False, user_id=user_id)).rstrip("/")
            if default_dest and default_dest.startswith("/"):
                candidate = f"{default_dest}/{author_name}"
                logger.debug(
                    "Auto-derive ebook scan path: dest=%s candidate=%s exists=%s",
                    default_dest,
                    candidate,
                    Path(candidate).is_dir(),
                )
                if Path(candidate).is_dir():
                    ebook_dir = candidate
        except Exception as exc:  # noqa: BLE001
            logger.debug("Auto-derive ebook scan path failed: %s", exc)

    if (not audiobook_dir or not audiobook_dir.startswith("/")) and author_name:
        try:
            from shelfmark.core.utils import get_destination

            default_dest = str(get_destination(is_audiobook=True, user_id=user_id)).rstrip("/")
            if default_dest and default_dest.startswith("/"):
                candidate = f"{default_dest}/{author_name}"
                logger.debug(
                    "Auto-derive audiobook scan path: dest=%s candidate=%s exists=%s",
                    default_dest,
                    candidate,
                    Path(candidate).is_dir(),
                )
                if Path(candidate).is_dir():
                    audiobook_dir = candidate
        except Exception as exc:  # noqa: BLE001
            logger.debug("Auto-derive audiobook scan path failed: %s", exc)

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
        except (OSError, ValueError) as exc:
            raise MonitoredPathError("Invalid ebook_author_dir") from exc
        if not path_within_allowed_roots(path=p, roots=allowed_roots):
            raise MonitoredPathError("ebook_author_dir is not within allowed roots")
        if not p.exists() or not p.is_dir():
            warnings["ebook_author_dir"] = "Directory not found"
        else:
            ebook_path = p

    if audiobook_dir:
        try:
            p = Path(audiobook_dir).resolve()
        except (OSError, ValueError) as exc:
            raise MonitoredPathError("Invalid audiobook_author_dir") from exc
        if not path_within_allowed_roots(path=p, roots=allowed_roots):
            raise MonitoredPathError("audiobook_author_dir is not within allowed roots")
        if not p.exists() or not p.is_dir():
            warnings["audiobook_author_dir"] = "Directory not found"
        else:
            audiobook_path = p

    if ebook_path is None and audiobook_path is None:
        try:
            clear_entity_matched_files(monitored_db=db, user_id=user_id, entity_id=entity_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed clearing matched files entity_id=%s: %s", entity_id, exc)
        raise MonitoredPathError("directories_not_found")

    books = db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []

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
    scan_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
        truncated=bool(scan_data.get("truncated")),
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
    entity = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id)
    if entity is None:
        return
    settings = dict(entity.get("settings") or {})
    if ebook_dir:
        settings["last_ebook_scan_error"] = str(error)
    if audiobook_dir:
        settings["last_audiobook_scan_error"] = str(error)
    with contextlib.suppress(Exception):
        db.create_monitored_entity(
            user_id=user_id,
            kind=str(entity.get("kind") or "author"),
            provider=entity.get("provider"),
            provider_id=entity.get("provider_id"),
            name=str(entity.get("name") or "").strip() or "Unknown",
            enabled=bool(int(entity.get("enabled") or 0)),
            settings=settings,
        )


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

    entity = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id)
    if entity is None:
        raise MonitoredEntityNotFoundError(f"Entity {entity_id} not found")

    availability = compute_book_availability(db, entity_id=entity_id, user_id=user_id)
    availability_payload = availability.availability_by_book.get(
        (normalized_provider, normalized_provider_book_id), {}
    )
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


def resolve_monitored_output_overrides(
    entity_settings: dict[str, Any] | None,
    *,
    content_type: str,
    user_id: int | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve (destination_override, file_organization_override, template_override)
    for a monitored download.

    Combines the entity's configured author folder (ebook_author_dir /
    audiobook_author_dir) with the user's MONITORED_<TYPE>_TEMPLATE setting.
    When no template is configured but an author dir is set, the file is still
    routed to the author folder and the global File Organization setting decides
    layout. When neither is configured, all three overrides are None and the
    download falls back to the global Downloads behaviour.
    """
    if not isinstance(entity_settings, dict):
        entity_settings = {}
    dir_key = "audiobook_author_dir" if content_type == "audiobook" else "ebook_author_dir"
    author_dir = entity_settings.get(dir_key)
    has_author_dir = isinstance(author_dir, str) and author_dir.strip().startswith("/")
    dest_override = author_dir.strip().rstrip("/") if has_author_dir else None

    from shelfmark.core.config import config as app_config

    template_key = (
        "MONITORED_AUDIOBOOK_TEMPLATE"
        if content_type == "audiobook"
        else "MONITORED_EBOOK_TEMPLATE"
    )
    template = str(app_config.get(template_key, "", user_id=user_id) or "").strip()

    if not template:
        return dest_override, None, None
    if has_author_dir:
        return dest_override, "organize", template
    # No per-entity author folder configured — prepend {Author}/ so files still
    # land under a per-author directory inside the default Downloads destination.
    return None, "organize", f"{{Author}}/{template}"


def filter_search_candidates(availability_books: list[dict], content_type: str) -> list[dict]:
    """Filter monitored books to those eligible for auto-search.

    A book is a candidate when (1) it is flagged for the requested content_type,
    (2) it is not hidden, (3) it is not removed from the upstream provider, and
    (4) it has provider/id fields populated. Used by ``search_missing_books``
    and by the scheduler's upfront candidate-count step — keep one source of truth.
    """
    monitor_col = "monitor_ebook" if content_type == "ebook" else "monitor_audiobook"
    return [
        row
        for row in availability_books
        if bool(int(row.get(monitor_col) or 0))
        and not bool(int(row.get("hidden") or 0))
        and str(row.get("state") or "") != "removed_from_provider"
        and str(row.get("provider") or "").strip()
        and str(row.get("provider_book_id") or "").strip()
    ]


def search_missing_books(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    content_type: str = "ebook",
    min_match_score: float | None = None,
    run_id: str | None = None,
    triggered_by: str = "manual",
) -> SearchSummary:
    """Find monitored books with no existing file and queue downloads for them.

    1. Loads current availability for the entity.
    2. Filters to books that are monitored for content_type and have no file.
    3. For each candidate: fetches releases, calls process_monitored_book().
    4. Returns a SearchSummary with counts.

    Raises:
        MonitoredEntityNotFoundError: If the entity does not exist or is not kind='author'.
    """
    from shelfmark.core.monitored_downloads import (
        process_monitored_book,
        write_monitored_book_attempt,
    )
    from shelfmark.core.monitored_history import record_search_started
    from shelfmark.core.monitored_release_scoring import is_book_released
    from shelfmark.metadata_providers import BookMetadata

    entity = db.get_monitored_entity(user_ids=[user_id], entity_id=entity_id)
    if entity is None or entity.get("kind") != "author":
        raise MonitoredEntityNotFoundError(f"Author entity {entity_id} not found")

    dest_override, org_override, tmpl_override = resolve_monitored_output_overrides(
        entity.get("settings") if isinstance(entity, dict) else None,
        content_type=content_type,
        user_id=user_id,
    )

    availability = compute_book_availability(db, entity_id=entity_id, user_id=user_id)
    candidates = filter_search_candidates(availability.books, content_type)

    summary = SearchSummary(
        entity_id=entity_id,
        content_type=content_type,
        total_candidates=len(candidates),
    )

    if not candidates:
        return summary

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    run_metadata: dict[str, Any] | None = {"run_id": run_id} if run_id else None

    for row in candidates:
        provider = str(row.get("provider") or "").strip()
        provider_book_id = str(row.get("provider_book_id") or "").strip()
        book_title = str(row.get("title") or "").strip() or None
        availability_payload = availability.availability_by_book.get(
            (provider, provider_book_id), {}
        )

        # Resolve release status before emitting any History events. Otherwise an
        # unreleased book gets a phantom "SEARCHING" row in the History tab even
        # though we immediately skip the actual search below.
        release_date_raw = str(row.get("release_date") or "").strip()
        is_released, parsed_release_date = is_book_released(release_date_raw)
        if parsed_release_date is None:
            if len(release_date_raw) == 4 and release_date_raw.isdigit():
                try:
                    parsed_release_date = date(int(release_date_raw), 1, 1)
                    is_released = parsed_release_date <= datetime.now(UTC).date()
                except ValueError:
                    is_released = False
            else:
                # No parseable release_date → treat as unreleased. We deliberately
                # don't fall back to publish_year: it usually reflects the original
                # work, not the edition we'd be searching for, and a missing
                # release_date is a stronger signal that data is incomplete. These
                # books surface in the Upcoming section so the user can decide
                # whether to keep monitoring or unmonitor (matches Upcoming UI).
                is_released = False
        if not is_released:
            # Skip silently — recording an attempt would flood the History
            # tab with one row per unreleased book on every batch search.
            summary.unreleased += 1
            continue

        # Resolve skip BEFORE emitting search_started — otherwise an already-
        # available book gets a phantom "Searching releases" row in the History
        # tab followed by a misleading "no_match" attempt. The skip is silent
        # for the same reason the unreleased path is: it's a correct no-op,
        # not a failed search, and noisy entries drown out real events.
        skip_reason, _skip_detail = _resolve_search_skip_reason(
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
            continue
        if skip_reason == "existing_file":
            summary.skipped_existing_file += 1
            continue

        # Mint a session_id for this attempt and emit search_started so the History
        # tab can group all subsequent events (search, queue, complete/fail) under
        # one expandable row.
        session_id = str(uuid.uuid4())
        try:
            record_search_started(
                entity_id=entity_id,
                book_provider=provider,
                book_provider_id=provider_book_id,
                book_title=book_title,
                content_type=content_type,
                session_id=session_id,
                user_id=user_id,
                metadata=run_metadata,
                triggered_by=triggered_by,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Failed to record search_started for %s/%s: %s", provider, provider_book_id, exc
            )

        try:
            # Build BookMetadata from DB row — data is already stored from sync
            authors_raw = row.get("authors") or ""
            authors_list = (
                [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []
            )
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

            # Attach book metadata from DB row so downloads use correct naming
            book_author = authors_list[0] if authors_list else None
            for rd in release_dicts:
                rd["release_date"] = row.get("release_date")
                if book_author:
                    rd.setdefault("author", book_author)
                if row.get("series_name"):
                    rd.setdefault("series_name", row.get("series_name"))
                if row.get("series_position") is not None:
                    rd.setdefault("series_position", row.get("series_position"))

            if not release_dicts:
                summary.no_match += 1
                write_monitored_book_attempt(
                    db,
                    user_id=user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status="no_match",
                    book_title=book_title,
                    session_id=session_id,
                    triggered_by=triggered_by,
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
                series_name=row.get("series_name") or None,
                series_position=row.get("series_position"),
                session_id=session_id,
                triggered_by=triggered_by,
            )

            if success:
                summary.queued += 1
                write_monitored_book_attempt(
                    db,
                    user_id=user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status="queued",
                    error_message=message,
                    book_title=book_title,
                    session_id=session_id,
                    triggered_by=triggered_by,
                )
            elif message == "Already in queue":
                summary.queued += 1
            elif "unreleased" in message.lower():
                summary.unreleased += 1
            elif "match score" in message.lower() or "no valid" in message.lower():
                summary.below_cutoff += 1
                write_monitored_book_attempt(
                    db,
                    user_id=user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status="below_cutoff",
                    error_message=message,
                    book_title=book_title,
                    session_id=session_id,
                    triggered_by=triggered_by,
                )
            else:
                summary.failed += 1
                write_monitored_book_attempt(
                    db,
                    user_id=user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status="failed",
                    error_message=message,
                    book_title=book_title,
                    session_id=session_id,
                    triggered_by=triggered_by,
                )

        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            write_monitored_book_attempt(
                db,
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
                attempted_at=now_iso,
                status="error",
                error_message=str(exc),
                book_title=book_title,
                session_id=session_id,
                triggered_by=triggered_by,
            )

    return summary
