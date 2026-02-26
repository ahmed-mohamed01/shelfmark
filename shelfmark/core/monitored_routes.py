from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Callable

import re
import threading
from pathlib import Path

from flask import Flask, jsonify, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_downloads import process_monitored_book, write_monitored_book_attempt
from shelfmark.core.monitored_author_sync import sync_author_entity, transform_cached_cover_urls
from shelfmark.core.monitored_files import apply_monitor_modes_for_books, path_within_allowed_roots, resolve_allowed_roots
from shelfmark.core.monitored_release_scoring import parse_release_date
from shelfmark.core.monitored_utils import extract_book_popularity
from shelfmark.core.request_policy import PolicyMode, normalize_content_type, resolve_policy_mode
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.activity_service import ActivityService, build_download_item_key
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _resolve_global_monitor_user_id(user_db: UserDB) -> int:
    user = user_db.get_user(username="global")
    if user:
        return int(user["id"])
    created = user_db.create_user(username="global", password_hash=None, email=None, display_name="Global", auth_source="builtin", role="admin")
    return int(created["id"])


def _resolve_monitor_scope_user_id(
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
) -> tuple[int | None, tuple[Any, int] | None]:
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        raw = session.get("db_user_id")
        if raw is not None:
            try:
                return int(raw), None
            except (TypeError, ValueError):
                pass
        return _resolve_global_monitor_user_id(user_db), None

    raw = session.get("db_user_id")
    if raw is None:
        return None, (jsonify({"error": "Authentication required", "code": "user_identity_unavailable"}), 403)
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Authentication required", "code": "user_identity_unavailable"}), 403)


def _policy_allows_monitoring(*, user_db: UserDB, db_user_id: int | None) -> tuple[bool, str | None]:
    try:
        global_settings = load_config_file("users")
    except Exception:
        global_settings = {}

    user_settings: dict[str, Any] = {}
    if db_user_id is not None:
        try:
            user_settings = user_db.get_user_settings(db_user_id) or {}
        except Exception:
            user_settings = {}

    blocked_count = 0
    for ct in ("ebook", "audiobook"):
        mode = resolve_policy_mode(
            source="*",
            content_type=normalize_content_type(ct),
            global_settings=global_settings,
            user_settings=user_settings,
        )
        if mode == PolicyMode.BLOCKED:
            blocked_count += 1

    if blocked_count == 2:
        return False, "Monitoring is unavailable by policy"
    return True, None


def register_monitored_routes(
    app: Flask,
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
    activity_service: ActivityService | None = None,
) -> None:

    def _parse_schedule_times(raw_value: Any) -> list[str]:
        raw = str(raw_value or "").strip()
        if not raw:
            raw = "02:00,14:00"

        unique: list[str] = []
        seen: set[str] = set()
        for part in (segment.strip() for segment in raw.split(",")):
            if not part:
                continue
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", part):
                continue
            if part not in seen:
                seen.add(part)
                unique.append(part)

        return unique or ["02:00", "14:00"]



    def _start_monitored_refresh_scheduler() -> None:
        if app.config.get("TESTING"):
            return
        if app.extensions.get("monitored_refresh_scheduler_started"):
            return

        stop_event = threading.Event()

        def _run() -> None:
            from shelfmark.core.config import config as app_config

            last_run_marker = ""
            while not stop_event.is_set():
                try:
                    if not app_config.get("MONITORED_SCHEDULED_REFRESH_ENABLED", True):
                        stop_event.wait(30)
                        continue

                    now = datetime.now()
                    slot = now.strftime("%H:%M")
                    schedule_times = _parse_schedule_times(app_config.get("MONITORED_REFRESH_TIMES", "02:00,14:00"))

                    if slot in schedule_times:
                        marker = f"{now.strftime('%Y-%m-%d')}@{slot}"
                        if marker != last_run_marker:
                            last_run_marker = marker
                            user_ids = {int(u.get("id")) for u in user_db.list_users() if u.get("id") is not None}
                            global_user = user_db.get_user(username="global")
                            if global_user and global_user.get("id") is not None:
                                user_ids.add(int(global_user.get("id")))

                            total_entities = 0
                            total_books = 0
                            for uid in sorted(user_ids):
                                entities = user_db.list_monitored_entities(user_id=uid)
                                for entity in entities:
                                    if not bool(int(entity.get("enabled") or 0)):
                                        continue
                                    if str(entity.get("kind") or "") != "author":
                                        continue
                                    total_entities += 1
                                    try:
                                        total_books += sync_author_entity(user_db, db_user_id=uid, entity=entity, prefetch_covers=True)
                                    except Exception as exc:
                                        logger.warning("Scheduled monitored sync failed entity_id=%s user_id=%s: %s", entity.get("id"), uid, exc)
                                        try:
                                            user_db.update_monitored_entity_check(
                                                entity_id=int(entity.get("id") or 0),
                                                last_error=str(exc),
                                            )
                                        except Exception:
                                            pass

                            logger.info(
                                "Scheduled monitored refresh complete slot=%s entities=%s discovered_books=%s",
                                slot,
                                total_entities,
                                total_books,
                            )
                except Exception as exc:
                    logger.warning("Scheduled monitored refresh loop error: %s", exc)

                stop_event.wait(30)

        worker = threading.Thread(target=_run, daemon=True, name="MonitoredRefreshScheduler")
        worker.start()
        app.extensions["monitored_refresh_scheduler_started"] = True
        app.extensions["monitored_refresh_scheduler_stop_event"] = stop_event

    _start_monitored_refresh_scheduler()

    @app.route("/api/monitored/<int:entity_id>", methods=["GET"])
    def api_get_monitored(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        return jsonify(entity)

    @app.route("/api/monitored/<int:entity_id>", methods=["PATCH", "PUT"])
    def api_patch_monitored(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        settings_patch = data.get("settings")
        if settings_patch is None:
            settings_patch = {}
        if not isinstance(settings_patch, dict):
            return jsonify({"error": "settings must be an object"}), 400

        settings = entity.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        merged_settings = dict(settings)
        merged_settings.update(settings_patch)

        try:
            updated = user_db.create_monitored_entity(
                user_id=db_user_id,
                kind=str(entity.get("kind") or "author"),
                provider=entity.get("provider"),
                provider_id=entity.get("provider_id"),
                name=str(entity.get("name") or "").strip() or "Unknown",
                enabled=bool(int(entity.get("enabled") or 0)),
                settings=merged_settings,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        should_reapply_monitor_modes = (
            str(updated.get("kind") or "").strip().lower() == "author"
            and (
                "monitor_ebook_mode" in settings_patch
                or "monitor_audiobook_mode" in settings_patch
            )
        )
        if should_reapply_monitor_modes:
            books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id) or []
            existing_files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
            if books and existing_files:
                from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books

                existing_files = expand_monitored_file_rows_for_equivalent_books(
                    books=books,
                    file_rows=existing_files,
                )
            _apply_monitor_modes_for_books(
                db_user_id=db_user_id,
                entity=updated,
                books=books,
                file_rows=existing_files,
            )

        return jsonify(updated)

    @app.route("/api/monitored", methods=["GET"])
    def api_list_monitored():
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        rows = user_db.list_monitored_entities(user_id=db_user_id)

        # Enrich with cached author details (bio, source_url) if available
        try:
            from shelfmark.core.metadata_cache import get_metadata_file_cache
            mcache = get_metadata_file_cache()
            for row in rows:
                provider = row.get("provider")
                provider_id = row.get("provider_id")
                if not provider or not provider_id:
                    continue
                cached = mcache.get("authors", provider, provider_id)
                if cached and isinstance(cached, dict):
                    author_data = cached.get("author")
                    if isinstance(author_data, dict):
                        row["cached_bio"] = author_data.get("bio")
                        row["cached_source_url"] = author_data.get("source_url")
        except Exception:
            pass  # Best-effort enrichment

        return jsonify(rows)

    @app.route("/api/monitored/search/books", methods=["GET"])
    def api_search_monitored_author_books():
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        query = str(request.args.get("q") or "").strip()
        if not query:
            return jsonify({"results": []})

        raw_limit = request.args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 20
        except (TypeError, ValueError):
            limit = 20

        rows = user_db.search_monitored_author_books(user_id=db_user_id, query=query, limit=limit)
        if rows:
            from shelfmark.core.monitored_files import (
                expand_monitored_file_rows_for_equivalent_books,
                summarize_monitored_book_availability,
            )

            rows_by_entity: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                try:
                    entity_id = int(row.get("entity_id"))
                except Exception:
                    continue
                rows_by_entity.setdefault(entity_id, []).append(row)

            availability_by_entity: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}
            for entity_id, entity_rows in rows_by_entity.items():
                files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
                if not files:
                    availability_by_entity[entity_id] = {}
                    continue

                books_for_alias: list[dict[str, Any]] = []
                for entity_row in entity_rows:
                    books_for_alias.append(
                        {
                            "provider": entity_row.get("book_provider"),
                            "provider_book_id": entity_row.get("book_provider_id"),
                            "title": entity_row.get("book_title"),
                            "series_name": entity_row.get("series_name"),
                            "series_position": entity_row.get("series_position"),
                        }
                    )
                expanded_files = expand_monitored_file_rows_for_equivalent_books(
                    books=books_for_alias,
                    file_rows=files,
                )
                availability_by_entity[entity_id] = summarize_monitored_book_availability(
                    file_rows=expanded_files,
                    user_id=db_user_id,
                )

            for row in rows:
                try:
                    entity_id = int(row.get("entity_id"))
                except Exception:
                    entity_id = -1
                provider = str(row.get("book_provider") or "").strip()
                provider_book_id = str(row.get("book_provider_id") or "").strip()
                payload = availability_by_entity.get(entity_id, {}).get((provider, provider_book_id), {})
                row["has_ebook_available"] = bool(payload.get("has_ebook_available", False))
                row["has_audiobook_available"] = bool(payload.get("has_audiobook_available", False))
                row["ebook_path"] = payload.get("ebook_path")
                row["audiobook_path"] = payload.get("audiobook_path")
                row["ebook_available_format"] = payload.get("ebook_available_format")
                row["audiobook_available_format"] = payload.get("audiobook_available_format")
        transform_cached_cover_urls(rows, provider_key="book_provider", provider_id_key="book_provider_id")
        return jsonify({"results": rows})

    @app.route("/api/monitored", methods=["POST"])
    def api_create_monitored():
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        kind = str(data.get("kind") or "").strip().lower()
        if kind not in {"author", "book"}:
            return jsonify({"error": "kind must be 'author' or 'book'"}), 400

        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        provider = data.get("provider")
        provider_id = data.get("provider_id")
        provider = str(provider).strip() if isinstance(provider, str) and provider.strip() else None
        provider_id = str(provider_id).strip() if isinstance(provider_id, str) and provider_id.strip() else None

        if kind == "book" and (not provider or not provider_id):
            return jsonify({"error": "provider and provider_id are required for kind='book'"}), 400

        settings = data.get("settings")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            return jsonify({"error": "settings must be an object"}), 400

        try:
            row = user_db.create_monitored_entity(
                user_id=db_user_id,
                kind=kind,
                provider=provider,
                provider_id=provider_id,
                name=name,
                enabled=True,
                settings=settings,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if kind == "book" and provider and provider_id:
            monitor_ebook = bool(settings.get("monitor_ebook", True))
            monitor_audiobook = bool(settings.get("monitor_audiobook", True))

            seeded_title = name
            seeded_authors = str(settings.get("book_author") or "").strip() or None
            seeded_cover = str(settings.get("photo_url") or "").strip() or None
            seeded_year: Any = None
            seeded_release_date: str | None = None
            seeded_isbn13: str | None = None
            seeded_series_name: str | None = None
            seeded_series_position: float | None = None
            seeded_series_count: int | None = None
            seeded_rating: float | None = None
            seeded_ratings_count: int | None = None
            seeded_readers_count: int | None = None

            try:
                from shelfmark.metadata_providers import get_provider, get_provider_kwargs

                prov = get_provider(provider, **get_provider_kwargs(provider))
                if prov.is_available():
                    book = prov.get_book(provider_id)
                    if book is not None:
                        payload = asdict(book)
                        seeded_title = str(payload.get("title") or seeded_title or "").strip() or seeded_title
                        authors = payload.get("authors")
                        if isinstance(authors, list):
                            seeded_authors = ", ".join(str(author).strip() for author in authors if str(author).strip()) or seeded_authors
                        seeded_year = payload.get("publish_year")
                        seeded_release_date = payload.get("release_date")
                        seeded_isbn13 = payload.get("isbn_13")
                        seeded_cover = payload.get("cover_url") or seeded_cover
                        seeded_series_name = payload.get("series_name")
                        seeded_series_position = payload.get("series_position")
                        seeded_series_count = payload.get("series_count")
                        seeded_rating, seeded_ratings_count, seeded_readers_count = extract_book_popularity(payload.get("display_fields"))
            except Exception as exc:
                logger.warning("Book monitor metadata seed failed provider=%s provider_id=%s: %s", provider, provider_id, exc)

            try:
                user_db.upsert_monitored_book(
                    user_id=db_user_id,
                    entity_id=int(row.get("id")),
                    provider=provider,
                    provider_book_id=provider_id,
                    title=seeded_title or name,
                    authors=seeded_authors,
                    publish_year=seeded_year,
                    release_date=seeded_release_date,
                    isbn_13=seeded_isbn13,
                    cover_url=seeded_cover,
                    series_name=seeded_series_name,
                    series_position=seeded_series_position,
                    series_count=seeded_series_count,
                    rating=seeded_rating,
                    ratings_count=seeded_ratings_count,
                    readers_count=seeded_readers_count,
                    state="discovered",
                )
                user_db.set_monitored_book_monitor_flags(
                    user_id=db_user_id,
                    entity_id=int(row.get("id")),
                    provider=provider,
                    provider_book_id=provider_id,
                    monitor_ebook=monitor_ebook,
                    monitor_audiobook=monitor_audiobook,
                )
            except Exception as exc:
                logger.warning("Book monitor seed upsert failed entity_id=%s provider=%s provider_id=%s: %s", row.get("id"), provider, provider_id, exc)

        return jsonify(row), 201

    @app.route("/api/monitored/<int:entity_id>", methods=["DELETE"])
    def api_delete_monitored(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        deleted = user_db.delete_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if not deleted:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/books", methods=["GET"])
    def api_list_monitored_books(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        rows = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id)
        if rows is None:
            return jsonify({"error": "Not found"}), 404

        for row in rows:
            row["no_release_date"] = parse_release_date(row.get("release_date")) is None

        files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
        if rows and files:
            from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books

            files = expand_monitored_file_rows_for_equivalent_books(
                books=rows,
                file_rows=files,
            )

        from shelfmark.core.monitored_files import with_monitored_book_availability

        rows = with_monitored_book_availability(
            books=rows,
            file_rows=files,
            user_id=db_user_id,
        )
        transform_cached_cover_urls(rows)

        # Include last_checked_at so the frontend can decide whether to refresh
        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        last_checked_at = entity.get("last_checked_at") if entity else None

        return jsonify({"books": rows, "last_checked_at": last_checked_at})

    @app.route("/api/monitored/<int:entity_id>/files", methods=["GET"])
    def api_list_monitored_book_files(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        rows = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id)
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id) or []
        if books and rows:
            from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books

            rows = expand_monitored_file_rows_for_equivalent_books(
                books=books,
                file_rows=rows,
            )
        return jsonify({"files": rows})

    @app.route("/api/monitored/<int:entity_id>/books/history", methods=["GET"])
    def api_list_monitored_book_history(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        provider = str(request.args.get("provider") or "").strip()
        provider_book_id = str(request.args.get("provider_book_id") or "").strip()
        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id are required"}), 400

        raw_limit = request.args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 50
        except (TypeError, ValueError):
            limit = 50

        rows = user_db.list_monitored_book_download_history(
            user_id=db_user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            limit=limit,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        attempt_rows = user_db.list_monitored_book_attempt_history(
            user_id=db_user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            limit=limit,
        )
        if attempt_rows is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"history": rows, "attempt_history": attempt_rows})

    @app.route("/api/monitored/<int:entity_id>/books/attempt", methods=["POST"])
    def api_record_monitored_book_attempt(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        provider = str(payload.get("provider") or "").strip()
        provider_book_id = str(payload.get("provider_book_id") or "").strip()
        content_type = str(payload.get("content_type") or "").strip().lower()
        status = str(payload.get("status") or "").strip().lower()

        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id are required"}), 400
        if content_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400
        if status not in {"queued", "no_match", "below_cutoff", "not_released", "download_failed", "error"}:
            return jsonify({"error": "invalid status"}), 400

        source = str(payload.get("source") or "").strip() or None
        source_id = str(payload.get("source_id") or "").strip() or None
        release_title = str(payload.get("release_title") or "").strip() or None
        error_message = str(payload.get("error_message") or "").strip() or None

        raw_match_score = payload.get("match_score")
        match_score: float | None = None
        if raw_match_score is not None:
            try:
                match_score = float(raw_match_score)
            except (TypeError, ValueError):
                match_score = None

        write_monitored_book_attempt(user_db,
            user_id=db_user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            content_type=content_type,
            status=status,
            source=source,
            source_id=source_id,
            release_title=release_title,
            match_score=match_score,
            error_message=error_message,
        )
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/scan-files", methods=["POST"])
    def api_scan_monitored_files(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404
        if entity.get("kind") != "author":
            return jsonify({"error": "Scan is only supported for author entities"}), 400

        settings = entity.get("settings")
        if not isinstance(settings, dict):
            settings = {}

        author_name = str(entity.get("name") or "").strip()
        ebook_dir_raw = settings.get("ebook_author_dir")
        ebook_dir = str(ebook_dir_raw).strip() if isinstance(ebook_dir_raw, str) else ""
        ebook_dir = ebook_dir.rstrip('/')
        audiobook_dir_raw = settings.get("audiobook_author_dir")
        audiobook_dir = str(audiobook_dir_raw).strip() if isinstance(audiobook_dir_raw, str) else ""
        audiobook_dir = audiobook_dir.rstrip('/')
        if (not ebook_dir or not ebook_dir.startswith('/')) and (not audiobook_dir or not audiobook_dir.startswith('/')):
            return jsonify({"error": "ebook_author_dir or audiobook_author_dir must be set"}), 400

        roots = resolve_allowed_roots(user_db, db_user_id=int(db_user_id or 0))
        if not roots:
            return jsonify({"error": "No allowed roots configured"}), 400

        ebook_path: Path | None = None
        audiobook_path: Path | None = None
        dir_warnings: dict[str, str] = {}

        if ebook_dir:
            try:
                ebook_path = Path(ebook_dir).resolve()
            except Exception:
                return jsonify({"error": "Invalid ebook_author_dir"}), 400
            if not path_within_allowed_roots(path=ebook_path, roots=roots):
                return jsonify({"error": "Path not allowed"}), 403
            if not ebook_path.exists() or not ebook_path.is_dir():
                dir_warnings["ebook_author_dir"] = "Directory not found"
                ebook_path = None

        if audiobook_dir:
            try:
                audiobook_path = Path(audiobook_dir).resolve()
            except Exception:
                return jsonify({"error": "Invalid audiobook_author_dir"}), 400
            if not path_within_allowed_roots(path=audiobook_path, roots=roots):
                return jsonify({"error": "Path not allowed"}), 403
            if not audiobook_path.exists() or not audiobook_path.is_dir():
                dir_warnings["audiobook_author_dir"] = "Directory not found"
                audiobook_path = None

        if ebook_path is None and audiobook_path is None:
            # Harden: clear all matched files when both directories are gone
            from shelfmark.core.monitored_files import clear_entity_matched_files
            try:
                clear_entity_matched_files(user_db=user_db, user_id=db_user_id, entity_id=entity_id)
            except Exception as exc:
                logger.warning("Failed clearing matched files for missing dirs entity_id=%s: %s", entity_id, exc)
            if dir_warnings:
                return jsonify({"error": "Directory not found", "details": dir_warnings, "files_cleared": True}), 404
            return jsonify({"error": "Directory not found", "files_cleared": True}), 404

        books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id)
        if books is None:
            return jsonify({"error": "Not found"}), 404

        try:
            from shelfmark.core.monitored_files import scan_monitored_author_files

            scan_results = scan_monitored_author_files(
                user_db=user_db,
                user_id=db_user_id,
                entity_id=entity_id,
                books=books,
                author_name=author_name,
                ebook_path=ebook_path,
                audiobook_path=audiobook_path,
            )
            scanned_ebook_files = int(scan_results.get("scanned_ebook_files") or 0)
            scanned_audio_folders = int(scan_results.get("scanned_audio_folders") or 0)
            matched = scan_results.get("matched") or []
            unmatched = scan_results.get("unmatched") or []
            existing_files = scan_results.get("existing_files") or []
            missing_books = scan_results.get("missing_books") or []

            _apply_monitor_modes_for_books(
                db_user_id=db_user_id,
                entity=entity,
                books=books,
                file_rows=existing_files,
            )

            # Update settings with scan timestamp
            scan_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            merged_settings = dict(settings)
            if ebook_path is not None:
                merged_settings["last_ebook_scan_at"] = scan_at
            if audiobook_path is not None:
                merged_settings["last_audiobook_scan_at"] = scan_at
            merged_settings.pop("last_ebook_scan_error", None)
            merged_settings.pop("last_audiobook_scan_error", None)
            user_db.create_monitored_entity(
                user_id=db_user_id,
                kind=str(entity.get("kind") or "author"),
                provider=entity.get("provider"),
                provider_id=entity.get("provider_id"),
                name=str(entity.get("name") or "").strip() or "Unknown",
                enabled=bool(int(entity.get("enabled") or 0)),
                settings=merged_settings,
            )

            return jsonify({
                "ok": True,
                "entity_id": entity_id,
                "scanned": {
                    "ebook_author_dir": str(ebook_path) if ebook_path is not None else None,
                    "audiobook_author_dir": str(audiobook_path) if audiobook_path is not None else None,
                },
                "warnings": dir_warnings,
                "stats": {
                    "ebook_files_scanned": scanned_ebook_files,
                    "audiobook_folders_scanned": scanned_audio_folders,
                    "matched": len(matched),
                    "unmatched": len(unmatched),
                },
                "matched": matched,
                "unmatched": unmatched,
                "missing_books": missing_books,
            })

        except Exception as exc:
            logger.warning("Monitored scan failed entity_id=%s: %s", entity_id, exc)
            try:
                merged_settings = dict(settings)
                if ebook_dir:
                    merged_settings["last_ebook_scan_error"] = str(exc)
                if audiobook_dir:
                    merged_settings["last_audiobook_scan_error"] = str(exc)
                user_db.create_monitored_entity(
                    user_id=db_user_id,
                    kind=str(entity.get("kind") or "author"),
                    provider=entity.get("provider"),
                    provider_id=entity.get("provider_id"),
                    name=str(entity.get("name") or "").strip() or "Unknown",
                    enabled=bool(int(entity.get("enabled") or 0)),
                    settings=merged_settings,
                )
            except Exception:
                pass
            return jsonify({"error": "Scan failed"}), 500

    @app.route("/api/monitored/<int:entity_id>/books/series", methods=["PATCH"])
    def api_update_monitored_books_series(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        data = request.get_json(silent=True)
        if not isinstance(data, list):
            return jsonify({"error": "Expected a JSON array"}), 400

        updates = []
        for item in data:
            if not isinstance(item, dict):
                continue
            provider = item.get("provider")
            provider_book_id = item.get("provider_book_id")
            series_name = item.get("series_name")
            if not provider or not provider_book_id or not series_name:
                continue
            updates.append({
                "provider": str(provider),
                "provider_book_id": str(provider_book_id),
                "series_name": str(series_name),
                "series_position": item.get("series_position"),
                "series_count": item.get("series_count"),
            })

        count = user_db.batch_update_monitored_books_series(
            user_id=db_user_id,
            entity_id=entity_id,
            updates=updates,
        )
        return jsonify({"ok": True, "updated": count})

    @app.route("/api/monitored/<int:entity_id>/books/monitor-flags", methods=["PATCH"])
    def api_update_monitored_books_monitor_flags(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            return jsonify({"error": "Expected a JSON object or array"}), 400

        updated = 0
        for item in rows:
            if not isinstance(item, dict):
                continue

            provider = str(item.get("provider") or "").strip()
            provider_book_id = str(item.get("provider_book_id") or "").strip()
            if not provider or not provider_book_id:
                continue

            monitor_ebook = item.get("monitor_ebook") if "monitor_ebook" in item else None
            monitor_audiobook = item.get("monitor_audiobook") if "monitor_audiobook" in item else None

            if monitor_ebook is not None:
                monitor_ebook = bool(monitor_ebook)
            if monitor_audiobook is not None:
                monitor_audiobook = bool(monitor_audiobook)

            if monitor_ebook is None and monitor_audiobook is None:
                continue

            ok = user_db.set_monitored_book_monitor_flags(
                user_id=db_user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                monitor_ebook=monitor_ebook,
                monitor_audiobook=monitor_audiobook,
            )
            if ok:
                updated += 1

        return jsonify({"ok": True, "updated": updated})

    @app.route("/api/monitored/<int:entity_id>/search", methods=["POST"])
    def api_search_monitored_entity(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404
        if entity.get("kind") != "author":
            return jsonify({"error": "Search is only supported for author entities"}), 400

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        content_type = str(payload.get("content_type") or "ebook").strip().lower()
        if content_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400

        def _emit_monitored_search_error(*, provider: str, provider_book_id: str, title: str | None, reason: str, detail: str | None = None) -> None:
            if activity_service is None or db_user_id is None:
                return
            try:
                task_id = f"monitored-search:{entity_id}:{provider}:{provider_book_id}:{content_type}"
                activity_service.record_terminal_snapshot(
                    user_id=int(db_user_id),
                    item_type="download",
                    item_key=build_download_item_key(task_id),
                    origin="direct",
                    final_status="error",
                    source_id=provider_book_id,
                    snapshot={
                        "kind": "monitored_search",
                        "entity_id": entity_id,
                        "content_type": content_type,
                        "provider": provider,
                        "provider_book_id": provider_book_id,
                        "title": title,
                        "reason": reason,
                        "detail": detail,
                    },
                )
            except Exception:
                pass

        books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id) or []
        files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
        if books and files:
            from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books

            files = expand_monitored_file_rows_for_equivalent_books(
                books=books,
                file_rows=files,
            )

        monitor_col = "monitor_ebook" if content_type == "ebook" else "monitor_audiobook"

        from shelfmark.core.monitored_files import summarize_monitored_book_availability

        availability_by_book = summarize_monitored_book_availability(
            file_rows=files,
            user_id=db_user_id,
        )

        candidates: list[dict[str, Any]] = []
        for row in books:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            if not provider or not provider_book_id:
                continue
            if not bool(int(row.get(monitor_col) or 0)):
                continue
            availability = availability_by_book.get((provider, provider_book_id), {})
            if content_type == "ebook":
                has_wanted_available = bool(availability.get("has_ebook_available"))
            else:
                has_wanted_available = bool(availability.get("has_audiobook_available"))
            if has_wanted_available:
                continue
            candidates.append(row)

        summary = {
            "ok": True,
            "entity_id": entity_id,
            "content_type": content_type,
            "total_candidates": len(candidates),
            "queued": 0,
            "unreleased": 0,
            "no_match": 0,
            "below_cutoff": 0,
            "failed": 0,
        }

        if not candidates:
            return jsonify(summary)

        from shelfmark.core.config import config as app_config
        from shelfmark.core.monitored_release_scoring import rank_releases_for_book
        from shelfmark.core.search_plan import build_release_search_plan
        from shelfmark.metadata_providers import get_provider, get_provider_kwargs
        from shelfmark.release_sources import get_source, list_available_sources

        threshold = float(app_config.get("AUTO_DOWNLOAD_MIN_MATCH_SCORE", 75, user_id=int(db_user_id or 0)) or 75)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        for row in candidates:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            book_title = str(row.get("title") or "").strip() or None
            if not provider or not provider_book_id:
                continue

            # Note: Release date check is handled by process_monitored_book() internally

            try:
                provider_instance = get_provider(provider, **get_provider_kwargs(provider))
                book = provider_instance.get_book(provider_book_id)
                if not book:
                    summary["no_match"] += 1
                    write_monitored_book_attempt(user_db,
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        attempted_at=now_iso,
                        status="no_match",
                        error_message="book_not_found",
                    )
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="no_match",
                        detail="book_not_found",
                    )
                    continue

                # Search all sources for releases
                search_plan = build_release_search_plan(book, languages=None, manual_query=None, indexers=None)
                all_releases: list[Any] = []
                for source_row in list_available_sources():
                    source_name = str(source_row.get("name") or "").strip()
                    if not source_name or not bool(source_row.get("enabled")):
                        continue
                    try:
                        source = get_source(source_name)
                        releases = source.search(book, search_plan, expand_search=False, content_type=content_type)
                        all_releases.extend(releases)
                    except Exception:
                        continue

                if not all_releases:
                    summary["no_match"] += 1
                    write_monitored_book_attempt(user_db,
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        attempted_at=now_iso,
                        status="no_match",
                    )
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="no_match",
                    )
                    continue

                # Rank releases and convert to dicts for process_monitored_book
                scored = rank_releases_for_book(book, all_releases)
                release_dicts = []
                for release, _ in scored:
                    release_dict = asdict(release)
                    release_dict["content_type"] = content_type
                    release_dict["release_date"] = row.get("release_date")
                    release_dicts.append(release_dict)

                # Process through monitored_downloads - handles filtering, queueing, retry, history
                success, message = process_monitored_book(
                    release_dicts,
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    min_match_score=threshold / 100.0,  # Convert from percentage to 0-1
                )

                if success:
                    summary["queued"] += 1
                elif message == "Already in queue":
                    pass  # Don't count as failure, just skip
                elif "unreleased" in message.lower():
                    summary["unreleased"] += 1
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="not_released",
                        detail=message,
                    )
                elif "match score" in message.lower() or "no valid" in message.lower():
                    summary["below_cutoff"] += 1
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="below_cutoff",
                        detail=message,
                    )
                else:
                    summary["failed"] += 1
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="error",
                        detail=message,
                    )

            except Exception as exc:
                summary["failed"] += 1
                write_monitored_book_attempt(user_db,
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status="error",
                    error_message=str(exc),
                )
                _emit_monitored_search_error(
                    provider=provider,
                    provider_book_id=provider_book_id,
                    title=book_title,
                    reason="error",
                    detail=str(exc),
                )

        return jsonify(summary)

    @app.route("/api/monitored/<int:entity_id>/sync", methods=["POST"])
    def api_sync_monitored(entity_id: int):
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify({"error": message or "Monitoring is unavailable by policy", "code": "policy_blocked"}), 403

        entity = user_db.get_monitored_entity(user_id=db_user_id, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        if entity.get("kind") != "author":
            return jsonify({"error": "Sync is only supported for author entities"}), 400

        try:
            discovered = sync_author_entity(user_db, db_user_id=db_user_id, entity=entity, prefetch_covers=True)
            return jsonify({"ok": True, "discovered": discovered})

        except Exception as exc:
            logger.warning("Monitored sync failed entity_id=%s: %s", entity_id, exc)
            user_db.update_monitored_entity_check(entity_id=entity_id, last_error=str(exc))
            return jsonify({"error": "Sync failed"}), 500
