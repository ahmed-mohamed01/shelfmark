from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

import difflib
import re
from pathlib import Path

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

    _TAG_PATTERNS = [
        re.compile(r"\[[^\]]+\]"),
        re.compile(r"\([^\)]+\)"),
        re.compile(r"\{[^\}]+\}"),
    ]

    def _normalize_candidate_title(raw: str, author_name: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        s = s.replace('_', ' ').replace('.', ' ')
        for pat in _TAG_PATTERNS:
            s = pat.sub(' ', s)
        s = re.sub(r"\b(ebook|epub|mobi|azw3?|pdf|retail|repack|illustrated|unabridged|scan|ocr)\b", " ", s, flags=re.IGNORECASE)
        # Strip author suffix/prefix
        a = (author_name or "").strip()
        if a:
            s = re.sub(rf"\s*[-–—:]\s*{re.escape(a)}\s*$", " ", s, flags=re.IGNORECASE)
            s = re.sub(rf"^\s*{re.escape(a)}\s*[-–—:]\s*", " ", s, flags=re.IGNORECASE)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        return s

    _VOLUME_MARKER_RE = re.compile(
        r"\b(?:(arc|book|vol(?:ume)?)\s*[-:#]?\s*)(\d{1,3})\b",
        re.IGNORECASE,
    )

    def _extract_volume_markers(s: str) -> dict[str, int]:
        out: dict[str, int] = {}
        text = (s or "").strip().lower()
        if not text:
            return out
        for m in _VOLUME_MARKER_RE.finditer(text):
            kind = (m.group(1) or "").lower()
            num_raw = m.group(2) or ""
            try:
                num = int(num_raw)
            except Exception:
                continue
            if kind.startswith("vol"):
                kind = "vol"
            out[kind] = num
        return out

    def _score_title_match(candidate: str, title: str) -> float:
        c = (candidate or "").strip().lower()
        t = (title or "").strip().lower()
        if not c or not t:
            return 0.0
        if c == t:
            return 1.0

        base = difflib.SequenceMatcher(None, c, t).ratio()

        c_markers = _extract_volume_markers(c)
        t_markers = _extract_volume_markers(t)

        # Sonarr-style idea: structured tokens (e.g. ARC 1) should dominate over fuzzy similarity.
        bonus = 0.0
        penalty = 0.0
        for kind in ("arc", "book", "vol"):
            cn = c_markers.get(kind)
            tn = t_markers.get(kind)
            if cn is None or tn is None:
                continue
            if cn == tn:
                bonus = max(bonus, 0.22)
            else:
                penalty = max(penalty, 0.35)

        score = base + bonus - penalty
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return float(score)

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
        if not ebook_dir or not ebook_dir.startswith('/'):
            return jsonify({"error": "ebook_author_dir is not set"}), 400

        roots = _resolve_allowed_roots(db_user_id=int(db_user_id or 0))
        if not roots:
            return jsonify({"error": "No allowed roots configured"}), 400

        try:
            ebook_path = Path(ebook_dir).resolve()
        except Exception:
            return jsonify({"error": "Invalid ebook_author_dir"}), 400

        if not _path_within_allowed_roots(path=ebook_path, roots=roots):
            return jsonify({"error": "Path not allowed"}), 403
        if not ebook_path.exists() or not ebook_path.is_dir():
            return jsonify({"error": "Directory not found"}), 404

        books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id)
        if books is None:
            return jsonify({"error": "Not found"}), 404

        known_titles: list[tuple[dict[str, Any], str]] = []
        for row in books:
            title = str(row.get("title") or "").strip()
            if title:
                known_titles.append((row, title))

        allowed_ext = {".epub", ".pdf", ".azw", ".azw3", ".mobi"}
        max_files = 4000
        scanned_files = 0
        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        best_by_book_and_type: dict[tuple[str, str, str], float] = {}

        def _iso_mtime(p: Path) -> str | None:
            try:
                ts = p.stat().st_mtime
                return datetime.utcfromtimestamp(ts).isoformat() + "Z"
            except Exception:
                return None

        try:
            for p in ebook_path.rglob('*'):
                if scanned_files >= max_files:
                    break
                try:
                    if not p.is_file():
                        continue
                    if p.is_symlink():
                        continue
                except Exception:
                    continue

                ext = p.suffix.lower()
                if ext not in allowed_ext:
                    continue

                scanned_files += 1
                try:
                    st = p.stat()
                    size_bytes = int(st.st_size)
                except Exception:
                    size_bytes = None

                candidate = _normalize_candidate_title(p.stem, author_name)
                best_score = 0.0
                best_row: dict[str, Any] | None = None
                scored: list[tuple[float, dict[str, Any], str]] = []
                for row, title in known_titles:
                    score = _score_title_match(candidate, title)
                    scored.append((score, row, title))
                    if score > best_score:
                        best_score = score
                        best_row = row

                scored.sort(key=lambda x: x[0], reverse=True)
                top_matches = [
                    {
                        "title": t,
                        "provider": r.get("provider"),
                        "provider_book_id": r.get("provider_book_id"),
                        "score": float(s),
                    }
                    for (s, r, t) in scored[:5]
                    if t
                ]

                file_type = ext.lstrip('.')
                mtime = _iso_mtime(p)

                # Aggressive threshold
                if best_row is not None and best_score >= 0.55:
                    provider = best_row.get("provider")
                    provider_book_id = best_row.get("provider_book_id")
                    provider = str(provider) if provider is not None else None
                    provider_book_id = str(provider_book_id) if provider_book_id is not None else None

                    match_key = (str(provider or ""), str(provider_book_id or ""), file_type)
                    prev = best_by_book_and_type.get(match_key)
                    if prev is None or best_score >= prev:
                        best_by_book_and_type[match_key] = float(best_score)
                        user_db.upsert_monitored_book_file(
                            user_id=db_user_id,
                            entity_id=entity_id,
                            provider=provider,
                            provider_book_id=provider_book_id,
                            path=str(p),
                            ext=ext.lstrip('.'),
                            file_type=file_type,
                            size_bytes=size_bytes,
                            mtime=mtime,
                            confidence=float(best_score),
                            match_reason="filename_title_fuzzy",
                        )

                    matched.append({
                        "path": str(p),
                        "ext": ext.lstrip('.'),
                        "file_type": file_type,
                        "size_bytes": size_bytes,
                        "mtime": mtime,
                        "candidate": candidate,
                        "match": {
                            "provider": provider,
                            "provider_book_id": provider_book_id,
                            "title": best_row.get("title"),
                            "confidence": float(best_score),
                            "reason": "filename_title_fuzzy",
                            "top_matches": top_matches,
                        },
                    })
                else:
                    unmatched.append({
                        "path": str(p),
                        "ext": ext.lstrip('.'),
                        "file_type": file_type,
                        "size_bytes": size_bytes,
                        "mtime": mtime,
                        "candidate": candidate,
                        "best_score": float(best_score),
                        "top_matches": top_matches,
                    })

            # Determine missing books (best-effort): books with no matching file rows for epub
            existing_files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
            have_book_ids: set[tuple[str, str]] = set()
            for row in existing_files:
                prov = row.get("provider")
                bid = row.get("provider_book_id")
                if isinstance(prov, str) and isinstance(bid, str) and prov and bid:
                    have_book_ids.add((prov, bid))

            missing_books: list[dict[str, Any]] = []
            for row in books:
                prov = row.get("provider")
                bid = row.get("provider_book_id")
                if not isinstance(prov, str) or not isinstance(bid, str) or not prov or not bid:
                    continue
                if (prov, bid) not in have_book_ids:
                    missing_books.append({
                        "provider": prov,
                        "provider_book_id": bid,
                        "title": row.get("title"),
                    })

            # Update settings with scan timestamp
            scan_at = datetime.utcnow().isoformat() + "Z"
            merged_settings = dict(settings)
            merged_settings["last_ebook_scan_at"] = scan_at
            merged_settings.pop("last_ebook_scan_error", None)
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
                    "ebook_author_dir": str(ebook_path),
                },
                "stats": {
                    "files_scanned": scanned_files,
                    "matched": len(matched),
                    "unmatched": len(unmatched),
                },
                "matched": matched,
                "unmatched": unmatched,
                "missing_books": missing_books,
                "last_ebook_scan_at": scan_at,
            })

        except Exception as exc:
            logger.warning("Scan files failed entity_id=%s: %s", entity_id, exc)
            try:
                merged_settings = dict(settings)
                merged_settings["last_ebook_scan_error"] = str(exc)
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
