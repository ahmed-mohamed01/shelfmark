"""File scanning, matching, and management for monitored entities."""

from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_EBOOK_EXT = {".epub", ".pdf", ".azw", ".azw3", ".mobi"}
ALLOWED_AUDIO_EXT = {".m4b", ".m4a", ".mp3", ".flac"}
MAX_SCAN_FILES = 4000

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

_VOLUME_MARKER_RE = re.compile(
    r"\b(?:(arc|book|vol(?:ume)?)\s*[-:#]?\s*)(\d{1,3})\b",
    re.IGNORECASE,
)


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


def normalize_candidate_title(raw: str, author_name: str) -> str:
    """Normalize a filename stem into a candidate title for matching."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace("_", " ").replace(".", " ")
    for pat in _TAG_PATTERNS:
        s = pat.sub(" ", s)
    s = re.sub(
        r"\b(ebook|epub|mobi|azw3?|pdf|retail|repack|illustrated|unabridged|scan|ocr)\b",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    a = (author_name or "").strip()
    if a:
        s = re.sub(rf"\s*[-–—:]\s*{re.escape(a)}\s*$", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"^\s*{re.escape(a)}\s*[-–—:]\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_match_variants(raw_title: str) -> list[str]:
    """Generate variants of a title for matching (e.g., with/without subtitle)."""
    title = (raw_title or "").strip()
    if not title:
        return []

    variants: list[str] = [title]
    colon_base = title.split(":", 1)[0].strip()
    if colon_base and colon_base.lower() != title.lower():
        variants.append(colon_base)

    deduped: list[str] = []
    seen: set[str] = set()
    for value in variants:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def extract_volume_markers(s: str) -> dict[str, int]:
    """Extract structured volume markers (Arc X, Book Y, Vol Z) from text."""
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


def score_title_match(candidate: str, title: str) -> float:
    """Score how well a candidate filename matches a book title (0.0-1.0)."""
    c = normalize_match_text(candidate)
    t = normalize_match_text(title)
    if not c or not t:
        return 0.0
    if c == t:
        return 1.0

    base = difflib.SequenceMatcher(None, c, t).ratio()

    c_markers = extract_volume_markers(c)
    t_markers = extract_volume_markers(t)

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

    for kind in ("arc", "book", "vol"):
        if kind in c_markers:
            continue
        tn = t_markers.get(kind)
        if tn is None:
            continue
        penalty += min(0.16, 0.04 * max(0, int(tn) - 1))

    score = base + bonus - penalty
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return float(score)


def prefer_row_on_tie(
    *,
    candidate: str,
    row: dict[str, Any],
    display_title: str,
    best_row: dict[str, Any],
    best_display_title: str,
) -> bool:
    """Determine if `row` should be preferred over `best_row` on a tie score."""
    candidate_norm = normalize_match_text(candidate)
    title_norm = normalize_match_text(display_title)
    best_norm = normalize_match_text(best_display_title)

    title_extends_candidate = bool(candidate_norm and title_norm.startswith(f"{candidate_norm} "))
    best_extends_candidate = bool(candidate_norm and best_norm.startswith(f"{candidate_norm} "))
    if title_extends_candidate != best_extends_candidate:
        return title_extends_candidate

    row_has_series = row.get("series_position") is not None or bool(str(row.get("series_name") or "").strip())
    best_has_series = best_row.get("series_position") is not None or bool(str(best_row.get("series_name") or "").strip())
    if row_has_series != best_has_series:
        return row_has_series

    if len(title_norm) != len(best_norm):
        return len(title_norm) > len(best_norm)

    return False


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


def pick_best_audio_file(files: list[Path]) -> Path | None:
    """Pick the best audio file from a list (prefer m4b, then by size)."""
    if not files:
        return None
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


# ---------------------------------------------------------------------------
# Core scanning / pruning
# ---------------------------------------------------------------------------


def clear_entity_matched_files(
    *,
    user_db: UserDB,
    user_id: int | None,
    entity_id: int,
) -> int:
    """Clear all matched file rows for an entity. Returns count deleted."""
    return user_db.prune_monitored_book_files(user_id=user_id, entity_id=entity_id, keep_paths=[])


def prune_stale_matched_files(
    *,
    user_db: UserDB,
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
        existing_files = user_db.list_monitored_book_files(user_id=user_id, entity_id=entity_id) or []
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
        user_db.prune_monitored_book_files(user_id=user_id, entity_id=entity_id, keep_paths=keep)
    except Exception as exc:
        logger.warning("Failed pruning monitored book files entity_id=%s: %s", entity_id, exc)
