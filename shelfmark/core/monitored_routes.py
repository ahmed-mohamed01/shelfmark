from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Callable

import re
import threading
from pathlib import Path

from flask import Flask, jsonify, request, session

from shelfmark.core.logger import setup_logger
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

    def _transform_cached_cover_urls(
        rows: list[dict[str, Any]],
        *,
        provider_key: str = "provider",
        provider_id_key: str = "provider_book_id",
    ) -> None:
        if not rows:
            return

        from shelfmark.core.utils import transform_cover_url

        for row in rows:
            if not isinstance(row, dict):
                continue

            cover_url = row.get("cover_url")
            if not isinstance(cover_url, str) or not cover_url:
                continue

            provider = str(row.get(provider_key) or "").strip()
            provider_book_id = str(row.get(provider_id_key) or "").strip()

            cache_id = ""
            if provider and provider_book_id:
                cache_id = f"{provider}_{provider_book_id}"
            else:
                fallback_id = str(row.get("id") or "").strip()
                if fallback_id:
                    cache_id = f"monitored_{fallback_id}"

            if cache_id:
                row["cover_url"] = transform_cover_url(cover_url, cache_id)

    def _parse_float_from_text(value: str) -> float | None:
        match = re.search(r"-?\d+(?:\.\d+)?", value or "")
        if not match:
            return None
        try:
            parsed = float(match.group(0))
        except Exception:
            return None
        return parsed if parsed == parsed else None

    def _parse_int_from_text(value: str) -> int | None:
        digits_only = re.sub(r"[^\d]", "", value or "")
        if not digits_only:
            return None
        try:
            return int(digits_only)
        except Exception:
            return None

    def _extract_book_popularity(display_fields: Any) -> tuple[float | None, int | None, int | None]:
        if not isinstance(display_fields, list):
            return None, None, None

        rating: float | None = None
        ratings_count: int | None = None
        readers_count: int | None = None

        for raw in display_fields:
            if not isinstance(raw, dict):
                continue
            icon = str(raw.get("icon") or "").strip().lower()
            label = str(raw.get("label") or "").strip().lower()
            value = str(raw.get("value") or "")

            if rating is None and (icon == "star" or "rating" in label):
                maybe_rating = _parse_float_from_text(value)
                if maybe_rating is not None and maybe_rating <= 10:
                    rating = maybe_rating

                paren_match = re.search(r"\(([^)]+)\)", value)
                if paren_match and ratings_count is None:
                    parsed_count = _parse_int_from_text(paren_match.group(1))
                    if parsed_count is not None:
                        ratings_count = parsed_count
                continue

            if ratings_count is None and re.search(r"ratings?", label):
                parsed_count = _parse_int_from_text(value)
                if parsed_count is not None:
                    ratings_count = parsed_count
                continue

            if readers_count is None and (icon == "users" or re.search(r"readers?|users?|followers?|people", label)):
                parsed_readers = _parse_int_from_text(value)
                if parsed_readers is not None:
                    readers_count = parsed_readers

        return rating, ratings_count, readers_count

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

    def _normalize_monitor_mode(value: Any) -> str:
        mode = str(value or "").strip().lower()
        if mode not in {"all", "missing", "upcoming"}:
            return "all"
        return mode

    def _parse_explicit_release_date(value: Any) -> date | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except Exception:
            return None

    def _book_has_file_type(
        *,
        by_book: dict[tuple[str, str], set[str]],
        provider: str,
        provider_book_id: str,
        allowed_file_types: set[str],
    ) -> bool:
        key = (provider, provider_book_id)
        types = by_book.get(key)
        if not types:
            return False
        return any(ft in allowed_file_types for ft in types)

    def _apply_monitor_modes_for_books(
        *,
        db_user_id: int | None,
        entity: dict[str, Any],
        books: list[dict[str, Any]],
        file_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        if not books:
            return

        entity_id = int(entity.get("id") or 0)
        if entity_id <= 0:
            return

        settings = entity.get("settings") if isinstance(entity.get("settings"), dict) else {}
        ebook_mode = _normalize_monitor_mode(settings.get("monitor_ebook_mode"))
        audio_mode = _normalize_monitor_mode(settings.get("monitor_audiobook_mode"))

        by_book: dict[tuple[str, str], set[str]] = {}
        for row in file_rows or []:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            file_type = str(row.get("file_type") or "").strip().lower()
            if not provider or not provider_book_id or not file_type:
                continue
            key = (provider, provider_book_id)
            if key not in by_book:
                by_book[key] = set()
            by_book[key].add(file_type)

        ebook_types = {"epub", "pdf", "mobi", "azw", "azw3"}
        audio_types = {"m4b", "m4a", "mp3", "flac"}
        today = date.today()

        for row in books:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            if not provider or not provider_book_id:
                continue

            has_ebook = _book_has_file_type(
                by_book=by_book,
                provider=provider,
                provider_book_id=provider_book_id,
                allowed_file_types=ebook_types,
            )
            has_audio = _book_has_file_type(
                by_book=by_book,
                provider=provider,
                provider_book_id=provider_book_id,
                allowed_file_types=audio_types,
            )
            explicit_release_date = _parse_explicit_release_date(row.get("release_date"))

            if ebook_mode == "all":
                monitor_ebook = True
            elif ebook_mode == "missing":
                monitor_ebook = not has_ebook
            else:
                monitor_ebook = bool(explicit_release_date is not None and explicit_release_date > today and not has_ebook)

            if audio_mode == "all":
                monitor_audio = True
            elif audio_mode == "missing":
                monitor_audio = not has_audio
            else:
                monitor_audio = bool(explicit_release_date is not None and explicit_release_date > today and not has_audio)

            user_db.set_monitored_book_monitor_flags(
                user_id=db_user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                monitor_ebook=monitor_ebook,
                monitor_audiobook=monitor_audio,
            )

    def _sync_author_entity(*, db_user_id: int | None, entity: dict[str, Any], prefetch_covers: bool = False) -> int:
        if entity.get("kind") != "author":
            raise ValueError("Sync is only supported for author entities")

        entity_id = int(entity.get("id"))
        author_name = str(entity.get("name") or "")
        provider_name = str(entity.get("provider") or "hardcover")

        from shelfmark.metadata_providers import MetadataSearchOptions, SortOrder, get_provider_kwargs, get_provider

        provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
        if not provider.is_available():
            raise RuntimeError(f"Metadata provider '{provider_name}' is not available")

        cache = None
        covers_enabled = False
        if prefetch_covers:
            try:
                from shelfmark.config.env import is_covers_cache_enabled
                covers_enabled = bool(is_covers_cache_enabled())
                if covers_enabled:
                    from shelfmark.core.image_cache import get_image_cache

                    cache = get_image_cache()
            except Exception:
                cache = None
                covers_enabled = False

        limit = 40
        page = 1
        has_more = True
        discovered = 0

        while has_more and page <= 15 and discovered < 600:
            options = MetadataSearchOptions(
                query="",
                limit=limit,
                page=page,
                sort=SortOrder.RELEVANCE,
                fields={"author": author_name},
            )
            result = provider.search_paginated(options)
            for book in result.books:
                payload = asdict(book)
                authors = payload.get("authors")
                authors_str = ", ".join(authors) if isinstance(authors, list) else None
                rating, ratings_count, readers_count = _extract_book_popularity(payload.get("display_fields"))
                provider_book_id = str(payload.get("provider_id") or "")
                provider_value = str(payload.get("provider") or provider_name)
                cover_url = payload.get("cover_url")

                user_db.upsert_monitored_book(
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider_value,
                    provider_book_id=provider_book_id,
                    title=str(payload.get("title") or ""),
                    authors=authors_str,
                    publish_year=payload.get("publish_year"),
                    release_date=payload.get("release_date"),
                    isbn_13=payload.get("isbn_13"),
                    cover_url=cover_url,
                    series_name=payload.get("series_name"),
                    series_position=payload.get("series_position"),
                    series_count=payload.get("series_count"),
                    rating=rating,
                    ratings_count=ratings_count,
                    readers_count=readers_count,
                    state="discovered",
                )

                if covers_enabled and cache is not None and isinstance(cover_url, str) and cover_url.strip():
                    cache_id = f"{provider_value}_{provider_book_id}"
                    try:
                        # Avoid network fetch if we already have a cached cover.
                        if cache.get(cache_id) is None:
                            cache.fetch_and_cache(cache_id, cover_url)
                    except Exception:
                        pass

                discovered += 1

            has_more = bool(getattr(result, "has_more", False))
            page += 1

        refreshed_books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id) or []
        existing_files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
        _apply_monitor_modes_for_books(
            db_user_id=db_user_id,
            entity=entity,
            books=refreshed_books,
            file_rows=existing_files,
        )

        user_db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
        return discovered

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
                                        total_books += _sync_author_entity(db_user_id=uid, entity=entity, prefetch_covers=True)
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

    def _resolve_allowed_roots(*, db_user_id: int) -> list[Path]:
        # Mirror the safety model in /api/fs/list: only allow browsing/scanning inside
        # configured destinations + remembered monitored roots.
        try:
            from shelfmark.core.config import config as app_config
        except Exception:
            app_config = None

        def _normalize_root(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            v = value.strip().rstrip('/')
            if not v or not v.startswith('/'):
                return None
            return v

        allowed: list[Path] = []
        if app_config is not None:
            try:
                dest = _normalize_root(app_config.get('DESTINATION', '/books', user_id=db_user_id))
                if dest:
                    allowed.append(Path(dest).resolve())
                dest_audio = _normalize_root(app_config.get('DESTINATION_AUDIOBOOK', '', user_id=db_user_id))
                if dest_audio:
                    allowed.append(Path(dest_audio).resolve())
            except Exception:
                pass

        try:
            user_settings = user_db.get_user_settings(db_user_id) or {}
        except Exception:
            user_settings = {}

        for key in ('MONITORED_EBOOK_ROOTS', 'MONITORED_AUDIOBOOK_ROOTS'):
            roots_value = user_settings.get(key)
            if isinstance(roots_value, list):
                for item in roots_value:
                    root = _normalize_root(item)
                    if root:
                        try:
                            allowed.append(Path(root).resolve())
                        except Exception:
                            continue

        unique: list[Path] = []
        seen: set[str] = set()
        for root in allowed:
            s = str(root)
            if s not in seen:
                seen.add(s)
                unique.append(root)
        return unique

    def _path_within_allowed_roots(*, path: Path, roots: list[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except Exception:
                continue
        return False

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
        _transform_cached_cover_urls(rows, provider_key="book_provider", provider_id_key="book_provider_id")
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
            row["no_release_date"] = _parse_explicit_release_date(row.get("release_date")) is None

        _transform_cached_cover_urls(rows)

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

        roots = _resolve_allowed_roots(db_user_id=int(db_user_id or 0))
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
            if not _path_within_allowed_roots(path=ebook_path, roots=roots):
                return jsonify({"error": "Path not allowed"}), 403
            if not ebook_path.exists() or not ebook_path.is_dir():
                dir_warnings["ebook_author_dir"] = "Directory not found"
                ebook_path = None

        if audiobook_dir:
            try:
                audiobook_path = Path(audiobook_dir).resolve()
            except Exception:
                return jsonify({"error": "Invalid audiobook_author_dir"}), 400
            if not _path_within_allowed_roots(path=audiobook_path, roots=roots):
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
            from shelfmark.core.config import config as app_config
            from shelfmark.core.monitored_files import scan_monitored_author_files

            def _normalize_supported_exts(raw_value: Any, fallback: list[str]) -> set[str]:
                if isinstance(raw_value, str):
                    values = [part.strip().lower() for part in raw_value.split(",") if part.strip()]
                elif isinstance(raw_value, list):
                    values = [str(part).strip().lower() for part in raw_value if str(part).strip()]
                else:
                    values = []

                if not values:
                    values = [item.strip().lower() for item in fallback if item.strip()]

                return {
                    ext if ext.startswith(".") else f".{ext}"
                    for ext in values
                    if ext and ext.strip(".")
                }

            configured_book_ext = _normalize_supported_exts(
                app_config.get(
                    "SUPPORTED_FORMATS",
                    ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"],
                    user_id=int(db_user_id or 0),
                ),
                ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"],
            )
            configured_audio_ext = _normalize_supported_exts(
                app_config.get(
                    "SUPPORTED_AUDIOBOOK_FORMATS",
                    ["m4b", "mp3"],
                    user_id=int(db_user_id or 0),
                ),
                ["m4b", "mp3"],
            )

            scan_results = scan_monitored_author_files(
                user_db=user_db,
                user_id=db_user_id,
                entity_id=entity_id,
                books=books,
                author_name=author_name,
                ebook_path=ebook_path,
                audiobook_path=audiobook_path,
                allowed_ebook_ext=configured_book_ext,
                allowed_audio_ext=configured_audio_ext,
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

        ebook_types = {"epub", "pdf", "mobi", "azw", "azw3"}
        audio_types = {"m4b", "m4a", "mp3", "flac"}
        wanted_types = ebook_types if content_type == "ebook" else audio_types
        monitor_col = "monitor_ebook" if content_type == "ebook" else "monitor_audiobook"

        available_by_book: dict[tuple[str, str], set[str]] = {}
        for row in files:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            file_type = str(row.get("file_type") or "").strip().lower()
            if not provider or not provider_book_id or not file_type:
                continue
            key = (provider, provider_book_id)
            if key not in available_by_book:
                available_by_book[key] = set()
            available_by_book[key].add(file_type)

        candidates: list[dict[str, Any]] = []
        for row in books:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            if not provider or not provider_book_id:
                continue
            if not bool(int(row.get(monitor_col) or 0)):
                continue
            file_types = available_by_book.get((provider, provider_book_id), set())
            if any(ft in wanted_types for ft in file_types):
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

        from dataclasses import asdict
        from shelfmark.core.config import config as app_config
        from shelfmark.core.release_matcher import rank_releases_for_book
        from shelfmark.core.search_plan import build_release_search_plan
        from shelfmark.download import orchestrator as download_orchestrator
        from shelfmark.metadata_providers import get_provider, get_provider_kwargs
        from shelfmark.release_sources import get_source, list_available_sources

        threshold = float(app_config.get("AUTO_DOWNLOAD_MIN_MATCH_SCORE", 75, user_id=int(db_user_id or 0)) or 75)
        now_iso = datetime.utcnow().isoformat() + "Z"

        for row in candidates:
            provider = str(row.get("provider") or "").strip()
            provider_book_id = str(row.get("provider_book_id") or "").strip()
            book_title = str(row.get("title") or "").strip() or None
            if not provider or not provider_book_id:
                continue

            try:
                failed_candidates = user_db.list_monitored_failed_candidate_source_ids(
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                )

                provider_instance = get_provider(provider, **get_provider_kwargs(provider))
                book = provider_instance.get_book(provider_book_id)
                if not book:
                    summary["no_match"] += 1
                    user_db.set_monitored_book_search_status(
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        status="no_match",
                        searched_at=now_iso,
                    )
                    user_db.insert_monitored_book_attempt_history(
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

                scored = rank_releases_for_book(book, all_releases)
                ranked = [release for release, _ in scored]
                ranked = [
                    release
                    for release in ranked
                    if (
                        str(getattr(release, "source", "") or "").strip(),
                        str(getattr(release, "source_id", "") or "").strip(),
                    ) not in failed_candidates
                ]

                if not ranked:
                    summary["no_match"] += 1
                    user_db.set_monitored_book_search_status(
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        status="no_match",
                        searched_at=now_iso,
                    )
                    user_db.insert_monitored_book_attempt_history(
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

                best = ranked[0]
                best_source = str(getattr(best, "source", "") or "").strip() or None
                best_source_id = str(getattr(best, "source_id", "") or "").strip() or None
                best_title = str(getattr(best, "title", "") or "").strip() or None
                extra = getattr(best, "extra", None)
                score_raw = extra.get("match_score") if isinstance(extra, dict) else None
                try:
                    match_score = float(score_raw) if score_raw is not None else None
                except (TypeError, ValueError):
                    match_score = None

                if match_score is None or match_score < threshold:
                    summary["below_cutoff"] += 1
                    user_db.set_monitored_book_search_status(
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        status="below_cutoff",
                        searched_at=now_iso,
                    )
                    user_db.insert_monitored_book_attempt_history(
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=provider,
                        provider_book_id=provider_book_id,
                        content_type=content_type,
                        attempted_at=now_iso,
                        status="below_cutoff",
                        source=best_source,
                        source_id=best_source_id,
                        release_title=best_title,
                        match_score=match_score,
                    )
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="below_cutoff",
                        detail=f"match_score={match_score}",
                    )
                    continue

                release_payload = asdict(best)
                release_payload["content_type"] = content_type
                release_payload["monitored_entity_id"] = entity_id
                release_payload["monitored_book_provider"] = provider
                release_payload["monitored_book_provider_id"] = provider_book_id
                release_payload["release_title"] = best_title
                release_payload["match_score"] = match_score
                release_payload["release_date"] = row.get("release_date")

                success, error_message = download_orchestrator.queue_release(
                    release_payload,
                    user_id=db_user_id,
                    username=session.get("user_id"),
                )
                if success:
                    summary["queued"] += 1
                    status = "queued"
                else:
                    if isinstance(error_message, str) and error_message.startswith("Book is unreleased until "):
                        summary["unreleased"] += 1
                        status = "not_released"
                    else:
                        summary["failed"] += 1
                        status = "download_failed"

                user_db.set_monitored_book_search_status(
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    status=status,
                    searched_at=now_iso,
                )
                user_db.insert_monitored_book_attempt_history(
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    attempted_at=now_iso,
                    status=status,
                    source=best_source,
                    source_id=best_source_id,
                    release_title=best_title,
                    match_score=match_score,
                    error_message=error_message,
                )
                if not success:
                    _emit_monitored_search_error(
                        provider=provider,
                        provider_book_id=provider_book_id,
                        title=book_title,
                        reason="download_failed",
                        detail=error_message,
                    )
            except Exception as exc:
                summary["failed"] += 1
                user_db.set_monitored_book_search_status(
                    user_id=db_user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                    content_type=content_type,
                    status="error",
                    searched_at=now_iso,
                )
                user_db.insert_monitored_book_attempt_history(
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
            discovered = _sync_author_entity(db_user_id=db_user_id, entity=entity, prefetch_covers=True)
            return jsonify({"ok": True, "discovered": discovered})

        except Exception as exc:
            logger.warning("Monitored sync failed entity_id=%s: %s", entity_id, exc)
            user_db.update_monitored_entity_check(entity_id=entity_id, last_error=str(exc))
            return jsonify({"error": "Sync failed"}), 500
