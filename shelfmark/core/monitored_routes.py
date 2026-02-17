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

    _WORD_NUMBER_MAP = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }

    def _normalize_match_text(raw: str) -> str:
        s = (raw or "").strip().lower()
        if not s:
            return ""
        s = s.replace("_", " ").replace(":", " ")
        s = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", lambda m: _WORD_NUMBER_MAP.get(m.group(1), m.group(1)), s)
        s = re.sub(r"[^a-z0-9]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\b(\d{1,3})\s+\1\b", r"\1", s)
        return s

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
        c = _normalize_match_text(candidate)
        t = _normalize_match_text(title)
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

        # This prevents drifting to late entries when names are otherwise very similar.
        for kind in ("arc", "book", "vol"):
            if kind in c_markers:
                continue
            tn = t_markers.get(kind)
            if tn is None:
                continue
            # ARC/Book/Vol 1 should be close to neutral; higher numbers get a small penalty.
            penalty += min(0.16, 0.04 * max(0, int(tn) - 1))

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
            if dir_warnings:
                return jsonify({"error": "Directory not found", "details": dir_warnings}), 404
            return jsonify({"error": "Directory not found"}), 404

        books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id)
        if books is None:
            return jsonify({"error": "Not found"}), 404

        known_titles: list[tuple[dict[str, Any], str]] = []
        for row in books:
            title = str(row.get("title") or "").strip()
            if title:
                known_titles.append((row, title))

        allowed_ebook_ext = {".epub", ".pdf", ".azw", ".azw3", ".mobi"}
        allowed_audio_ext = {".m4b", ".m4a", ".mp3", ".flac"}
        max_files = 4000
        scanned_ebook_files = 0
        scanned_audio_folders = 0
        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        best_by_book_and_type: dict[tuple[str, str, str], float] = {}
        seen_paths: set[str] = set()

        def _iso_mtime(p: Path) -> str | None:
            try:
                ts = p.stat().st_mtime
                return datetime.utcfromtimestamp(ts).isoformat() + "Z"
            except Exception:
                return None

        def _pick_best_audio_file(files: list[Path]) -> Path | None:
            if not files:
                return None
            # Prefer single-file audiobooks (m4b/m4a) over mp3, then by size.
            priority = {".m4b": 0, ".m4a": 1, ".mp3": 2, ".flac": 3}

            def _key(p: Path) -> tuple[int, int]:
                ext = p.suffix.lower()
                pr = priority.get(ext, 99)
                try:
                    sz = int(p.stat().st_size)
                except Exception:
                    sz = 0
                return (pr, -sz)

            return sorted(files, key=_key)[0]

        try:
            if ebook_path is not None:
                for p in ebook_path.rglob('*'):
                    if scanned_ebook_files >= max_files:
                        break
                    try:
                        if not p.is_file():
                            continue
                        if p.is_symlink():
                            continue
                    except Exception:
                        continue

                    ext = p.suffix.lower()
                    if ext not in allowed_ebook_ext:
                        continue

                    seen_paths.add(str(p))

                    scanned_ebook_files += 1
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

            if audiobook_path is not None:
                # Folder-oriented scan: each folder with audio files is a candidate "book".
                # This mirrors how many libraries (and Audiobookshelf) organize one audiobook per directory.
                seen_dirs: set[str] = set()
                for p in audiobook_path.rglob('*'):
                    try:
                        if not p.is_file():
                            continue
                        if p.is_symlink():
                            continue
                    except Exception:
                        continue

                    ext = p.suffix.lower()
                    if ext not in allowed_audio_ext:
                        continue

                    parent = p.parent
                    parent_key = str(parent)
                    if parent_key in seen_dirs:
                        continue

                    try:
                        audio_files = [
                            fp
                            for fp in parent.iterdir()
                            if fp.is_file() and (not fp.is_symlink()) and fp.suffix.lower() in allowed_audio_ext
                        ]
                    except Exception:
                        continue

                    best_file = _pick_best_audio_file(audio_files)
                    if best_file is None:
                        continue

                    seen_dirs.add(parent_key)
                    scanned_audio_folders += 1

                    # Candidate construction:
                    # - Common case: one book per folder -> folder name is the title (often has "1. Title" / "Book 2").
                    # - Edge case: series container folder contains audio file directly (e.g. Mistborn/Final Empire.m4b)
                    #   -> use the filename stem as the title, and keep folder name as series hint.
                    folder_name = parent.name
                    series_name = parent.parent.name if parent.parent and parent.parent != audiobook_path else ""

                    # Detect "series container" folder: it contains subdirectories with audio files.
                    is_series_container = False
                    try:
                        for child in parent.iterdir():
                            if not child.is_dir():
                                continue
                            try:
                                has_audio = any(
                                    fp.is_file() and (not fp.is_symlink()) and fp.suffix.lower() in allowed_audio_ext
                                    for fp in child.iterdir()
                                )
                            except Exception:
                                has_audio = False
                            if has_audio:
                                is_series_container = True
                                break
                    except Exception:
                        is_series_container = False

                    if is_series_container:
                        combined = best_file.stem
                        if folder_name:
                            combined = f"{combined} {folder_name}"
                    else:
                        combined = folder_name
                        if series_name and not re.match(r"^\d+\.?\s*", folder_name):
                            combined = f"{folder_name} {series_name}"

                    candidate = _normalize_candidate_title(combined, author_name)

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

                    ext = best_file.suffix.lower()
                    file_type = ext.lstrip('.')
                    mtime = _iso_mtime(best_file)
                    try:
                        size_bytes = int(best_file.stat().st_size)
                    except Exception:
                        size_bytes = None

                    seen_paths.add(str(best_file))

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
                                path=str(best_file),
                                ext=ext.lstrip('.'),
                                file_type=file_type,
                                size_bytes=size_bytes,
                                mtime=mtime,
                                confidence=float(best_score),
                                match_reason="folder_title_fuzzy",
                            )

                        matched.append({
                            "path": str(best_file),
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
                                "reason": "folder_title_fuzzy",
                                "top_matches": top_matches,
                            },
                        })
                    else:
                        unmatched.append({
                            "path": str(best_file),
                            "ext": ext.lstrip('.'),
                            "file_type": file_type,
                            "size_bytes": size_bytes,
                            "mtime": mtime,
                            "candidate": candidate,
                            "best_score": float(best_score),
                            "top_matches": top_matches,
                        })

            # Determine missing books (best-effort): books with no matching file rows for epub
            # Prune DB rows for files that no longer exist on disk.
            # Only prune within the roots used for this scan (ebook_path/audiobook_path).
            try:
                if ebook_path is not None or audiobook_path is not None:
                    existing_files_before = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
                    keep: list[str] = []
                    for row in existing_files_before:
                        path = row.get("path")
                        if not isinstance(path, str) or not path:
                            continue
                        should_consider = False
                        if ebook_path is not None:
                            try:
                                Path(path).resolve().relative_to(ebook_path)
                                should_consider = True
                            except Exception:
                                pass
                        if audiobook_path is not None and not should_consider:
                            try:
                                Path(path).resolve().relative_to(audiobook_path)
                                should_consider = True
                            except Exception:
                                pass
                        if not should_consider:
                            keep.append(path)
                            continue
                        if path in seen_paths and Path(path).exists():
                            keep.append(path)
                    user_db.prune_monitored_book_files(user_id=db_user_id, entity_id=entity_id, keep_paths=keep)
            except Exception as exc:
                logger.warning("Failed pruning monitored book files entity_id=%s: %s", entity_id, exc)

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
