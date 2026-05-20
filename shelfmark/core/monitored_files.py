"""File scanning, matching, and management for monitored entities."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.user_db import UserDB
from shelfmark.download.postprocess.policy import get_supported_audiobook_formats, get_supported_formats

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SCAN_FILES = 4000

# Keyed by content kind ("ebook" / "audiobook") → field names used when
# summarising and enriching availability payloads.
_AVAILABILITY_FIELDS: dict[str, dict[str, str]] = {
    "ebook": {
        "has_key": "has_ebook_available",
        "path_key": "ebook_path",
        "format_key": "ebook_available_format",
    },
    "audiobook": {
        "has_key": "has_audiobook_available",
        "path_key": "audiobook_path",
        "format_key": "audiobook_available_format",
    },
}

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

# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def normalize_match_text(raw: str) -> str:
    """Normalize text for fuzzy matching (lowercase, strip punctuation, etc.)."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    s = s.replace("_", " ").replace(":", " ")
    s = re.sub(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        lambda m: _WORD_NUMBER_MAP.get(m.group(1), m.group(1)),
        s,
    )
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(\d{1,3})\s+\1\b", r"\1", s)
    return s


def _row_provider_key(row: dict[str, Any]) -> tuple[str, str]:
    """Extract (provider, provider_book_id) as stripped strings from a row dict."""
    return (
        str(row.get("provider") or "").strip(),
        str(row.get("provider_book_id") or "").strip(),
    )


def _record_scan_match_v2(
    *,
    monitored_db: MonitoredDB,
    user_id: int,
    entity_id: int,
    file_path: Path,
    file_type: str,
    size_bytes: int | None,
    mtime: str,
    author_name: str,
    books: list[dict[str, Any]],
    metadata_cache: dict[str, Any],
    best_by_book_and_type: dict[tuple[str, str, str], float],
    matched: list[dict[str, Any]],
    unmatched: list[dict[str, Any]],
    rejections: set[tuple[str, str, str, str]] | None = None,
) -> None:
    """Record a single scan result using the v2 attribution matcher.

    Uses path decomposition + position extraction + embedded metadata. Persists
    structured evidence_json alongside the existing confidence/match_reason.
    """
    # Local imports keep cyclic-risk low (v2 module imports normalize_match_text
    # from this module).
    from shelfmark.core.monitored_attribution_v2 import pick_best_attribution

    path_str = str(file_path)
    embedded = metadata_cache.get(path_str)

    # Drop books the user explicitly rejected for this (source=filesystem, path).
    if rejections:
        eligible_books = [
            b for b in books
            if ("filesystem", path_str,
                (b.get("provider") or ""), (b.get("provider_book_id") or "")) not in rejections
        ]
    else:
        eligible_books = books

    result = pick_best_attribution(
        path=path_str,
        books=eligible_books,
        author_name=author_name,
        embedded=embedded,
    )
    evidence = result.evidence

    # Serialise evidence for the API / "Why?" UI. asdict drops dataclass typing
    # but keeps the same field shape we render.
    try:
        evidence_payload = asdict(evidence)
        evidence_json: str | None = json.dumps(evidence_payload, default=str)
    except (TypeError, ValueError):
        evidence_json = None

    top_matches: list[dict[str, Any]] = []
    if result.book is not None:
        top_matches.append({
            "title": result.book.get("title"),
            "provider": result.book.get("provider"),
            "provider_book_id": result.book.get("provider_book_id"),
            "score": float(result.confidence),
        })

    if result.book is None or not evidence.accept:
        unmatched.append({
            "path": path_str,
            "ext": file_type,
            "file_type": file_type,
            "size_bytes": size_bytes,
            "mtime": mtime,
            "candidate": evidence.title_core or "",
            "best_score": float(result.confidence),
            "top_matches": top_matches,
            "reason": result.match_reason,
        })
        return

    best_book = result.book
    p_val = best_book.get("provider")
    bid_val = best_book.get("provider_book_id")
    provider: str | None = str(p_val) if p_val is not None else None
    provider_book_id: str | None = str(bid_val) if bid_val is not None else None
    match_key = (str(provider or ""), str(provider_book_id or ""), file_type)
    prev = best_by_book_and_type.get(match_key)
    if prev is None or result.confidence > prev:
        best_by_book_and_type[match_key] = float(result.confidence)
        monitored_db.upsert_monitored_book_file(
            user_ids=[user_id],
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            path=path_str,
            ext=file_type,
            file_type=file_type,
            size_bytes=size_bytes,
            mtime=mtime,
            confidence=float(result.confidence),
            match_reason=result.match_reason,
            evidence_json=evidence_json,
        )

    matched.append({
        "path": path_str,
        "ext": file_type,
        "file_type": file_type,
        "size_bytes": size_bytes,
        "mtime": mtime,
        "candidate": evidence.title_core or "",
        "match": {
            "provider": provider,
            "provider_book_id": provider_book_id,
            "title": best_book.get("title"),
            "confidence": float(result.confidence),
            "reason": result.match_reason,
            "top_matches": top_matches,
            "evidence": evidence_payload if evidence_json else None,
        },
    })


def _normalize_alias_series_position(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _alias_identity_for_book(row: dict[str, Any]) -> tuple[str, float | None, str]:
    raw_title = str(row.get("title") or "").strip()
    base_title = raw_title.split(":", 1)[0].strip() if raw_title else ""
    normalized_base = normalize_match_text(base_title or raw_title)
    series_position = _normalize_alias_series_position(row.get("series_position"))
    normalized_series_name = normalize_match_text(str(row.get("series_name") or ""))
    return (normalized_base, series_position, normalized_series_name)


def _books_are_alias_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_base, left_series_pos, left_series_name = _alias_identity_for_book(left)
    right_base, right_series_pos, right_series_name = _alias_identity_for_book(right)

    if not left_base or left_base != right_base:
        return False

    if left_series_pos is not None and right_series_pos is not None and abs(left_series_pos - right_series_pos) > 1e-6:
        return False

    if left_series_name and right_series_name and left_series_name != right_series_name:
        return False

    return True


def expand_monitored_file_rows_for_equivalent_books(
    *,
    books: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expand matched file rows so equivalent monitored books share detection state.

    This is read-time expansion only; it does not mutate the DB schema.
    """

    if not books or not file_rows:
        return list(file_rows or [])

    books_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in books:
        provider, provider_book_id = _row_provider_key(row)
        if not provider or not provider_book_id:
            continue
        books_by_key[(provider, provider_book_id)] = row

    alias_map: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in books_by_key}
    keys_by_normalized_base: dict[str, list[tuple[str, str]]] = {}
    for key, row in books_by_key.items():
        normalized_base, _, _ = _alias_identity_for_book(row)
        if not normalized_base:
            continue
        keys_by_normalized_base.setdefault(normalized_base, []).append(key)

    for candidate_keys in keys_by_normalized_base.values():
        if len(candidate_keys) < 2:
            continue
        for idx in range(len(candidate_keys)):
            left_key = candidate_keys[idx]
            left_row = books_by_key[left_key]
            for jdx in range(idx + 1, len(candidate_keys)):
                right_key = candidate_keys[jdx]
                right_row = books_by_key[right_key]
                if _books_are_alias_equivalent(left_row, right_row):
                    alias_map[left_key].add(right_key)
                    alias_map[right_key].add(left_key)

    expanded: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _append_row(provider: str, provider_book_id: str, base_row: dict[str, Any]) -> None:
        row_path = str(base_row.get("path") or "")
        row_type = str(base_row.get("file_type") or "")
        dedupe_key = (provider, provider_book_id, row_type, row_path)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        cloned = dict(base_row)
        cloned["provider"] = provider
        cloned["provider_book_id"] = provider_book_id
        expanded.append(cloned)

    for row in file_rows:
        provider, provider_book_id = _row_provider_key(row)
        if not provider or not provider_book_id:
            continue

        _append_row(provider, provider_book_id, row)
        for alias_provider, alias_provider_book_id in alias_map.get((provider, provider_book_id), set()):
            _append_row(alias_provider, alias_provider_book_id, row)

    return expanded


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def iso_mtime(p: Path) -> str | None:
    """Return ISO 8601 UTC mtime for a path, or None on error."""
    try:
        ts = p.stat().st_mtime
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _iter_files_recursive_safe(root: Path) -> list[Path]:
    """Return all files under *root*, skipping unreadable paths gracefully."""
    files: list[Path] = []

    def _onerror(err: OSError) -> None:
        logger.warning("Skipping unreadable monitored scan path root=%s: %s", root, err)

    try:
        for dirpath, _, filenames in os.walk(root, topdown=True, followlinks=False, onerror=_onerror):
            for filename in filenames:
                files.append(Path(dirpath) / filename)
    except Exception as exc:
        logger.warning("Failed walking monitored scan root=%s: %s", root, exc)
    return files


def pick_best_audio_file(
    files: list[Path],
    *,
    format_rank: dict[str, int] | None = None,
) -> Path | None:
    """Pick the best audio file using user-configured format preference, then size."""
    if not files:
        return None

    rank_map = format_rank or {}

    def _key(p: Path) -> tuple[int, int]:
        ext = p.suffix.lower().lstrip(".")
        pr = rank_map.get(ext, 999)
        try:
            sz = int(p.stat().st_size)
        except Exception:
            sz = 0
        return (pr, -sz)

    return sorted(files, key=_key)[0]


def _parse_extension_tokens(values: Any, *, parse_orderable_items: bool = False) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        return [segment.strip() for segment in values.split(",") if segment.strip()]

    if not isinstance(values, (list, tuple, set)):
        return []

    raw_values: list[str] = []
    for segment in values:
        if parse_orderable_items and isinstance(segment, dict):
            if segment.get("enabled") is False:
                continue
            token = str(segment.get("id") or "").strip()
        else:
            token = str(segment).strip()
        if token:
            raw_values.append(token)
    return raw_values


def _normalize_extensions(values: Any) -> set[str]:
    raw_values = _parse_extension_tokens(values)
    return {ext.lower().lstrip(".") for ext in raw_values if ext}


def _normalize_ordered_extensions(values: Any) -> list[str]:
    raw_values = _parse_extension_tokens(values, parse_orderable_items=True)

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        ext = raw.lower().lstrip(".")
        if not ext or ext in seen:
            continue
        seen.add(ext)
        ordered.append(ext)
    return ordered


def _build_format_rank_map(*, preferred_order: list[str], allowed_formats: set[str]) -> dict[str, int]:
    rank: dict[str, int] = {}
    for ext in preferred_order:
        if ext in allowed_formats and ext not in rank:
            rank[ext] = len(rank)

    for ext in sorted(allowed_formats):
        if ext not in rank:
            rank[ext] = len(rank)
    return rank


def resolve_monitored_format_rankings(
    *,
    user_id: int | None,
    ebook_formats: set[str],
    audiobook_formats: set[str],
) -> tuple[dict[str, int], dict[str, int]]:
    ebook_priority = _normalize_ordered_extensions(
        core_config.config.get("EBOOK_FORMAT_PRIORITY", [], user_id=user_id)
    )
    if not ebook_priority:
        ebook_priority = _normalize_ordered_extensions(
            core_config.config.get(
                "SUPPORTED_FORMATS",
                get_supported_formats(),
                user_id=user_id,
            )
        )

    audiobook_priority = _normalize_ordered_extensions(
        core_config.config.get("AUDIOBOOK_FORMAT_PRIORITY", [], user_id=user_id)
    )
    if not audiobook_priority:
        audiobook_priority = _normalize_ordered_extensions(
            core_config.config.get(
                "SUPPORTED_AUDIOBOOK_FORMATS",
                get_supported_audiobook_formats(),
                user_id=user_id,
            )
        )

    return (
        _build_format_rank_map(preferred_order=ebook_priority, allowed_formats=ebook_formats),
        _build_format_rank_map(preferred_order=audiobook_priority, allowed_formats=audiobook_formats),
    )


def resolve_monitored_format_preferences(
    *,
    user_id: int | None,
    allowed_ebook_ext: set[str] | None = None,
    allowed_audio_ext: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Resolve effective monitored file formats for ebooks and audiobooks.

    Priority:
    1) Explicit function args
    2) Config singleton (user override aware)
    3) Postprocess policy defaults
    """

    ebook_ext = _normalize_extensions(allowed_ebook_ext)
    if not ebook_ext:
        configured_ebook = core_config.config.get(
            "SUPPORTED_FORMATS",
            get_supported_formats(),
            user_id=user_id,
        )
        ebook_ext = _normalize_extensions(configured_ebook)
    if not ebook_ext:
        ebook_ext = _normalize_extensions(get_supported_formats())

    audio_ext = _normalize_extensions(allowed_audio_ext)
    if not audio_ext:
        configured_audio = core_config.config.get(
            "SUPPORTED_AUDIOBOOK_FORMATS",
            get_supported_audiobook_formats(),
            user_id=user_id,
        )
        audio_ext = _normalize_extensions(configured_audio)
    if not audio_ext:
        audio_ext = _normalize_extensions(get_supported_audiobook_formats())

    return ebook_ext, audio_ext


def _infer_file_kind_and_format(
    row: dict[str, Any],
    *,
    ebook_formats: set[str],
    audiobook_formats: set[str],
) -> tuple[str | None, str | None]:
    file_type = str(row.get("file_type") or "").strip().lower()
    ext = str(row.get("ext") or "").strip().lower().lstrip(".")
    if not ext and file_type and file_type not in {"ebook", "audiobook"}:
        ext = file_type.lstrip(".")

    if file_type == "ebook":
        return "ebook", ext or None
    if file_type == "audiobook":
        return "audiobook", ext or None
    if ext and ext in ebook_formats:
        return "ebook", ext
    if ext and ext in audiobook_formats:
        return "audiobook", ext
    if file_type or ext:
        logger.debug(
            "Skipping monitored availability row with unrecognized kind: file_type=%r ext=%r path=%r",
            file_type or None,
            ext or None,
            row.get("path"),
        )
    return None, ext or None


def _format_rank(*, fmt: str | None, rank_by_format: dict[str, int]) -> int:
    if not fmt:
        return 999
    return rank_by_format.get(fmt, 500)


def summarize_monitored_book_availability(
    *,
    file_rows: list[dict[str, Any]],
    user_id: int | None,
    allowed_ebook_ext: set[str] | None = None,
    allowed_audio_ext: set[str] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build canonical availability by monitored book.

    Returns keyed by (provider, provider_book_id) and includes:
    - has_ebook_available / has_audiobook_available
    - ebook_path / audiobook_path
    - ebook_available_format / audiobook_available_format
    """

    ebook_formats, audio_formats = resolve_monitored_format_preferences(
        user_id=user_id,
        allowed_ebook_ext=allowed_ebook_ext,
        allowed_audio_ext=allowed_audio_ext,
    )
    ebook_rank_by_format, audio_rank_by_format = resolve_monitored_format_rankings(
        user_id=user_id,
        ebook_formats=ebook_formats,
        audiobook_formats=audio_formats,
    )

    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for row in file_rows or []:
        provider, provider_book_id = _row_provider_key(row)
        if not provider or not provider_book_id:
            continue
        key = (provider, provider_book_id)
        payload = summary.get(key)
        if payload is None:
            payload = {
                "has_ebook_available": False,
                "has_audiobook_available": False,
                "ebook_path": None,
                "audiobook_path": None,
                "ebook_available_format": None,
                "audiobook_available_format": None,
            }
            summary[key] = payload

        kind, fmt = _infer_file_kind_and_format(
            row,
            ebook_formats=ebook_formats,
            audiobook_formats=audio_formats,
        )
        if kind not in {"ebook", "audiobook"}:
            continue

        _fields = _AVAILABILITY_FIELDS[kind]
        current_has_key = _fields["has_key"]
        current_path_key = _fields["path_key"]
        current_format_key = _fields["format_key"]

        current_format = payload.get(current_format_key)
        if not payload.get(current_has_key):
            payload[current_has_key] = True
            payload[current_format_key] = fmt
            payload[current_path_key] = row.get("path")
        else:
            rank_by_format = ebook_rank_by_format if kind == "ebook" else audio_rank_by_format
            if _format_rank(fmt=fmt, rank_by_format=rank_by_format) < _format_rank(
                fmt=str(current_format or "") or None,
                rank_by_format=rank_by_format,
            ):
                payload[current_format_key] = fmt
                payload[current_path_key] = row.get("path")

    return summary


def with_monitored_book_availability(
    *,
    books: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
    user_id: int | None,
    allowed_ebook_ext: set[str] | None = None,
    allowed_audio_ext: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return monitored books enriched with canonical availability fields."""

    availability = summarize_monitored_book_availability(
        file_rows=file_rows,
        user_id=user_id,
        allowed_ebook_ext=allowed_ebook_ext,
        allowed_audio_ext=allowed_audio_ext,
    )

    out: list[dict[str, Any]] = []
    for row in books or []:
        provider, provider_book_id = _row_provider_key(row)
        payload = availability.get((provider, provider_book_id), {})
        enriched = dict(row)
        enriched["has_ebook_available"] = bool(payload.get("has_ebook_available", False))
        enriched["has_audiobook_available"] = bool(payload.get("has_audiobook_available", False))
        enriched["ebook_path"] = payload.get("ebook_path")
        enriched["audiobook_path"] = payload.get("audiobook_path")
        enriched["ebook_available_format"] = payload.get("ebook_available_format")
        enriched["audiobook_available_format"] = payload.get("audiobook_available_format")
        out.append(enriched)
    return out


# ---------------------------------------------------------------------------
# Core scanning / pruning
# ---------------------------------------------------------------------------


def clear_entity_matched_files(
    *,
    monitored_db: MonitoredDB,
    user_id: int | None,
    entity_id: int,
) -> int:
    """Clear all filesystem-sourced matched file rows for an entity. Returns count deleted."""
    return monitored_db.prune_monitored_book_files(entity_id=entity_id, keep_paths=[], source="filesystem")


def prune_stale_matched_files(
    *,
    monitored_db: MonitoredDB,
    user_id: int | None,
    entity_id: int,
    scanned_roots: list[Path],
    seen_paths: set[str],
) -> None:
    """
    Prune DB rows for files that no longer exist on disk.
    Only prunes within the given scanned_roots.
    """
    if not scanned_roots:
        return

    try:
        existing_files = monitored_db.list_monitored_book_files(user_ids=[user_id], entity_id=entity_id) or []
        keep: list[str] = []
        for row in existing_files:
            path = row.get("path")
            if not isinstance(path, str) or not path:
                continue
            should_consider = False
            for root in scanned_roots:
                try:
                    Path(path).resolve().relative_to(root)
                    should_consider = True
                    break
                except Exception:
                    pass
            if not should_consider:
                keep.append(path)
                continue
            if path in seen_paths and Path(path).exists():
                keep.append(path)
        monitored_db.prune_monitored_book_files(entity_id=entity_id, keep_paths=keep, source="filesystem")
    except Exception as exc:
        logger.warning("Failed pruning monitored book files entity_id=%s: %s", entity_id, exc)


def scan_monitored_author_files(
    *,
    monitored_db: MonitoredDB,
    user_id: int | None,
    entity_id: int,
    books: list[dict[str, Any]],
    author_name: str,
    ebook_path: Path | None,
    audiobook_path: Path | None,
    allowed_ebook_ext: set[str] | None = None,
    allowed_audio_ext: set[str] | None = None,
) -> dict[str, Any]:
    """Scan filesystem paths and upsert matched monitored_book_files rows.

    Returns scan stats and matched/unmatched payloads for API responses.
    """

    effective_ebook_names, effective_audio_names = resolve_monitored_format_preferences(
        user_id=user_id,
        allowed_ebook_ext=allowed_ebook_ext,
        allowed_audio_ext=allowed_audio_ext,
    )
    _, audio_rank_by_format = resolve_monitored_format_rankings(
        user_id=user_id,
        ebook_formats=effective_ebook_names,
        audiobook_formats=effective_audio_names,
    )
    effective_ebook_ext = {f".{ext}" for ext in effective_ebook_names}
    effective_audio_ext = {f".{ext}" for ext in effective_audio_names}

    scanned_ebook_files = 0
    scanned_audio_folders = 0
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    best_by_book_and_type: dict[tuple[str, str, str], float] = {}
    seen_paths: set[str] = set()

    # User-rejected (path, book) pairs — scanner must not re-attribute these.
    rejections = monitored_db.list_file_rejections_for_entity(entity_id=entity_id)

    # Per-scan embedded-metadata cache (eager, per design). Filled lazily as we
    # encounter each file — keeps the call inline rather than re-walking.
    from shelfmark.core.monitored_attribution_metadata import read_embedded_metadata
    metadata_cache: dict[str, Any] = {}

    if ebook_path is not None:
        for p in _iter_files_recursive_safe(ebook_path):
            if scanned_ebook_files >= MAX_SCAN_FILES:
                break
            try:
                if not p.is_file() or p.is_symlink():
                    continue
            except Exception:
                continue

            ext = p.suffix.lower()
            if ext not in effective_ebook_ext:
                continue

            seen_paths.add(str(p))
            scanned_ebook_files += 1
            try:
                size_bytes = int(p.stat().st_size)
            except Exception:
                size_bytes = None

            file_type = ext.lstrip(".")
            mtime = iso_mtime(p)
            path_str = str(p)
            if path_str not in metadata_cache:
                meta = read_embedded_metadata(path_str)
                if meta is not None:
                    metadata_cache[path_str] = meta

            _record_scan_match_v2(
                monitored_db=monitored_db,
                user_id=user_id,
                entity_id=entity_id,
                file_path=p,
                file_type=file_type,
                size_bytes=size_bytes,
                mtime=mtime,
                author_name=author_name,
                books=books,
                metadata_cache=metadata_cache,
                best_by_book_and_type=best_by_book_and_type,
                matched=matched,
                unmatched=unmatched,
                rejections=rejections,
            )

    if audiobook_path is not None:
        seen_dirs: set[str] = set()
        for p in _iter_files_recursive_safe(audiobook_path):
            try:
                if not p.is_file() or p.is_symlink():
                    continue
            except Exception:
                continue

            path_ext = p.suffix.lower()
            if path_ext not in effective_audio_ext:
                continue

            parent = p.parent
            parent_key = str(parent)
            if parent_key in seen_dirs:
                continue

            try:
                audio_files = [
                    fp
                    for fp in parent.iterdir()
                    if fp.is_file() and (not fp.is_symlink()) and fp.suffix.lower() in effective_audio_ext
                ]
            except Exception:
                continue

            best_file = pick_best_audio_file(audio_files, format_rank=audio_rank_by_format)
            if best_file is None:
                continue

            seen_dirs.add(parent_key)
            scanned_audio_folders += 1

            best_ext = best_file.suffix.lower()
            file_type = best_ext.lstrip(".")
            mtime = iso_mtime(best_file)
            try:
                size_bytes = int(best_file.stat().st_size)
            except Exception:
                size_bytes = None

            seen_paths.add(str(best_file))

            # Eager-read metadata for the chosen audio file. The v2 matcher
            # uses the full file path (which is the canonical "leaf" for the
            # audio folder) for path decomposition.
            path_str = str(best_file)
            if path_str not in metadata_cache:
                meta = read_embedded_metadata(path_str)
                if meta is not None:
                    metadata_cache[path_str] = meta

            _record_scan_match_v2(
                monitored_db=monitored_db,
                user_id=user_id,
                entity_id=entity_id,
                file_path=best_file,
                file_type=file_type,
                size_bytes=size_bytes,
                mtime=mtime,
                author_name=author_name,
                books=books,
                metadata_cache=metadata_cache,
                best_by_book_and_type=best_by_book_and_type,
                matched=matched,
                unmatched=unmatched,
                rejections=rejections,
            )

    scanned_roots = [p for p in [ebook_path, audiobook_path] if p is not None]
    prune_stale_matched_files(
        monitored_db=monitored_db,
        user_id=user_id,
        entity_id=entity_id,
        scanned_roots=scanned_roots,
        seen_paths=seen_paths,
    )

    existing_files = monitored_db.list_monitored_book_files(user_ids=[user_id], entity_id=entity_id) or []
    expanded_existing_files = expand_monitored_file_rows_for_equivalent_books(
        books=books,
        file_rows=existing_files,
    )
    have_book_ids: set[tuple[str, str]] = set()
    for row in expanded_existing_files:
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
            missing_books.append(
                {
                    "provider": prov,
                    "provider_book_id": bid,
                    "title": row.get("title"),
                }
            )

    return {
        "scanned_ebook_files": scanned_ebook_files,
        "scanned_audio_folders": scanned_audio_folders,
        "matched": matched,
        "unmatched": unmatched,
        "existing_files": expanded_existing_files,
        "missing_books": missing_books,
    }


# ---------------------------------------------------------------------------
# Monitor-mode flag logic
# ---------------------------------------------------------------------------


def normalize_monitor_mode(value: Any) -> str:
    """Validate and normalize a monitor mode string.

    Valid values: "all", "missing", "upcoming". Defaults to "all" for
    unrecognized input.
    """
    mode = str(value or "").strip().lower()
    if mode not in {"all", "missing", "upcoming"}:
        return "all"
    return mode


def compute_monitor_flag(
    mode: str,
    has_available: bool,
    explicit_release_date: Any,
    today: date,
) -> bool:
    """Determine whether a book should be auto-monitored given mode and availability.

    Args:
        mode: Monitor mode — "all", "missing", or "upcoming".
        has_available: True if the book already has a matched local file.
        explicit_release_date: Parsed release date or None.
        today: Reference date for "upcoming" check.
    """
    if mode == "all":
        return True
    if mode == "missing":
        return not has_available
    # "upcoming": only monitor future books we don't have yet
    return bool(
        explicit_release_date is not None
        and explicit_release_date > today
        and not has_available
    )


def apply_monitor_modes_for_books(
    monitored_db: MonitoredDB,
    *,
    db_user_id: int | None,
    entity: dict[str, Any],
    books: list[dict[str, Any]],
    file_rows: list[dict[str, Any]] | None = None,
) -> None:
    """Compute and persist monitor flags for all books belonging to an entity.

    Reads monitor_ebook_mode / monitor_audiobook_mode from entity settings,
    computes the flag for each book based on current availability, then writes
    the result to the database.
    """
    if not books:
        return

    entity_id = int(entity.get("id") or 0)
    if entity_id <= 0:
        return

    settings = entity.get("settings") if isinstance(entity.get("settings"), dict) else {}
    ebook_mode = normalize_monitor_mode(settings.get("monitor_ebook_mode"))
    audio_mode = normalize_monitor_mode(settings.get("monitor_audiobook_mode"))

    availability_by_book = summarize_monitored_book_availability(
        file_rows=file_rows or [],
        user_id=db_user_id,
    )
    today = date.today()

    from shelfmark.core.monitored_release_scoring import parse_release_date

    for row in books:
        # Hidden books should keep monitor flags at 0 — skip recomputation
        if row.get("hidden"):
            continue
        # User manually set monitor flags — don't overwrite their choice
        if int(row.get("monitor_locked") or 0):
            continue

        provider = str(row.get("provider") or "").strip()
        provider_book_id = str(row.get("provider_book_id") or "").strip()
        if not provider or not provider_book_id:
            continue

        availability = availability_by_book.get((provider, provider_book_id), {})
        has_ebook = bool(availability.get("has_ebook_available"))
        has_audio = bool(availability.get("has_audiobook_available"))
        explicit_release_date = parse_release_date(row.get("release_date"))

        monitor_ebook = compute_monitor_flag(ebook_mode, has_ebook, explicit_release_date, today)
        monitor_audio = compute_monitor_flag(audio_mode, has_audio, explicit_release_date, today)

        monitored_db.set_monitored_book_monitor_flags(
            user_ids=[db_user_id],
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            monitor_ebook=monitor_ebook,
            monitor_audiobook=monitor_audio,
        )


# ---------------------------------------------------------------------------
# Scan path safety helpers
# ---------------------------------------------------------------------------


def resolve_allowed_roots(user_db: UserDB, *, db_user_id: int) -> list[Path]:
    """Build the list of filesystem paths that are safe to scan for this user.

    Combines configured ebook/audiobook destinations with any user-specific
    monitored root directories.
    """
    try:
        from shelfmark.core.config import config as app_config
    except Exception:
        app_config = None

    def _normalize_root(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        v = value.strip().rstrip("/")
        if not v or not v.startswith("/"):
            return None
        return v

    allowed: list[Path] = []
    if app_config is not None:
        try:
            dest = _normalize_root(app_config.get("DESTINATION", "/books", user_id=db_user_id))
            if dest:
                allowed.append(Path(dest).resolve())
            dest_audio = _normalize_root(app_config.get("DESTINATION_AUDIOBOOK", "", user_id=db_user_id))
            if dest_audio:
                allowed.append(Path(dest_audio).resolve())
        except Exception:
            pass

    try:
        user_settings = user_db.get_user_settings(db_user_id) or {}
    except Exception:
        user_settings = {}

    for key in ("MONITORED_EBOOK_ROOTS", "MONITORED_AUDIOBOOK_ROOTS"):
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


def path_within_allowed_roots(*, path: Path, roots: list[Path]) -> bool:
    """Return True if *path* is contained within any of the allowed roots."""
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except Exception:
            continue
    return False
