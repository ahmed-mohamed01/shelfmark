from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from flask import Flask, jsonify, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_policy import PolicyMode, normalize_content_type, parse_policy_mode, resolve_policy_mode
from shelfmark.core.settings_registry import load_config_file
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
) -> None:
    @app.route("/api/monitored", methods=["GET"])
    def api_list_monitored():
        db_user_id, gate = _resolve_monitor_scope_user_id(user_db, resolve_auth_mode=resolve_auth_mode)
        if gate is not None:
            return gate

        rows = user_db.list_monitored_entities(user_id=db_user_id)
        return jsonify(rows)

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
        return jsonify(rows)

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

        author_name = entity.get("name") or ""
        provider_name = entity.get("provider") or "hardcover"

        try:
            from shelfmark.metadata_providers import MetadataSearchOptions, SortOrder, get_provider_kwargs, get_provider

            provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
            if not provider.is_available():
                raise RuntimeError(f"Metadata provider '{provider_name}' is not available")

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
                    user_db.upsert_monitored_book(
                        user_id=db_user_id,
                        entity_id=entity_id,
                        provider=str(payload.get("provider") or provider_name),
                        provider_book_id=str(payload.get("provider_id") or ""),
                        title=str(payload.get("title") or ""),
                        authors=authors_str,
                        publish_year=payload.get("publish_year"),
                        isbn_13=payload.get("isbn_13"),
                        cover_url=payload.get("cover_url"),
                        series_name=payload.get("series_name"),
                        series_position=payload.get("series_position"),
                        series_count=payload.get("series_count"),
                        state="discovered",
                    )
                    discovered += 1

                has_more = bool(getattr(result, "has_more", False))
                page += 1

            user_db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
            return jsonify({"ok": True, "discovered": discovered})

        except Exception as exc:
            logger.warning("Monitored sync failed entity_id=%s: %s", entity_id, exc)
            user_db.update_monitored_entity_check(entity_id=entity_id, last_error=str(exc))
            return jsonify({"error": "Sync failed"}), 500
