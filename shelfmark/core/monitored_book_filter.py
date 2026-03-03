"""Filter non-canonical split/partial books from a monitored author's book list.

Split books are partial editions (e.g., "The Way of Kings, Part 1") that should
be excluded when the canonical full edition ("The Way of Kings") is present.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)

# Matches split suffixes at end of title:
#   ", Part 1"  " Part 1"  ", Part One"  " Part One"
#   ", Vol. 1"  " Vol 1"   ", Volume 1"  " Volume One"
_SPLIT_SUFFIX = re.compile(
    r"[,\s]+(?:part|vol\.?|volume)\s+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"\s*$",
    re.IGNORECASE,
)


def _normalize_title(title: str) -> str:
    """Lowercase, strip articles, collapse whitespace and punctuation."""
    s = (title or "").strip().lower()
    s = _ARTICLES.sub("", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_split_suffix(title: str) -> tuple[str, bool]:
    """Strip a split suffix from *title*, return (base, had_suffix)."""
    stripped = _SPLIT_SUFFIX.sub("", title.strip())
    had = stripped.strip().lower() != title.strip().lower()
    return stripped.strip(), had


# ---------------------------------------------------------------------------
# Series entry extraction (works with both GraphQL and DB shapes)
# ---------------------------------------------------------------------------


def _get_series_entries(book: dict[str, Any]) -> list[tuple[str, float | None]]:
    """Return [(series_name, position), ...] from either shape."""
    # GraphQL shape: book_series -> [{position, series: {name}}]
    raw = book.get("book_series")
    if isinstance(raw, list):
        entries = []
        for s in raw:
            name = (s.get("series") or {}).get("name")
            if name:
                entries.append((name, s.get("position")))
        return entries

    # DB shape: all_series is a JSON string -> [{name, position, count}]
    all_series = book.get("all_series")
    if isinstance(all_series, str):
        try:
            all_series = json.loads(all_series)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(all_series, list):
        return [
            (s["name"], s.get("position"))
            for s in all_series
            if s.get("name")
        ]
    return []


def _get_readers_count(book: dict[str, Any]) -> int | None:
    """Extract readers count from either GraphQL or DB shape."""
    # GraphQL: users_read_count; DB: readers_count
    for key in ("users_read_count", "readers_count"):
        val = book.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Core filter
# ---------------------------------------------------------------------------


def filter_split_books(
    books: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate canonical books from split editions.

    Works on raw GraphQL book dicts (title, book_series, users_read_count)
    and on DB rows (title, all_series JSON, readers_count).

    Returns (canonical, filtered_out).
    """
    if not books:
        return [], []

    # Build a normalized-title → book indices lookup for parent matching
    norm_to_indices: dict[str, list[int]] = {}
    for i, book in enumerate(books):
        norm = _normalize_title(book.get("title") or "")
        if norm:
            norm_to_indices.setdefault(norm, []).append(i)

    # Build a (series_name, int_position) → book indices lookup
    series_int_pos: dict[tuple[str, int], list[int]] = {}
    for i, book in enumerate(books):
        for series_name, pos in _get_series_entries(book):
            if pos is not None and float(pos) == int(pos):
                key = (series_name.strip().lower(), int(pos))
                series_int_pos.setdefault(key, []).append(i)

    filtered_indices: set[int] = set()

    for i, book in enumerate(books):
        title = (book.get("title") or "").strip()
        if not title:
            continue

        # --- Check 1: title suffix match ---
        base_title, had_suffix = _strip_split_suffix(title)
        if had_suffix:
            norm_base = _normalize_title(base_title)
            parent_indices = norm_to_indices.get(norm_base, [])
            for pi in parent_indices:
                if pi == i:
                    continue
                # Confirm: parent should have more readers (or at least exist)
                parent_readers = _get_readers_count(books[pi])
                split_readers = _get_readers_count(book)
                if parent_readers is not None and split_readers is not None:
                    if parent_readers >= split_readers * 2:
                        filtered_indices.add(i)
                        break
                else:
                    # No readers data — title match alone is sufficient
                    filtered_indices.add(i)
                    break
            if i in filtered_indices:
                continue

        # --- Check 2: fractional series position with title match ---
        for series_name, pos in _get_series_entries(book):
            if pos is None:
                continue
            fpos = float(pos)
            frac = fpos - int(fpos)
            # Only .1, .2 etc — NOT .5 (novellas)
            if frac == 0 or abs(frac - 0.5) < 0.01:
                continue

            int_pos = int(fpos)
            key = (series_name.strip().lower(), int_pos)
            parent_indices = series_int_pos.get(key, [])
            for pi in parent_indices:
                if pi == i:
                    continue
                # Check title overlap: parent's norm title should be
                # a prefix of or match the split's norm title
                parent_norm = _normalize_title(books[pi].get("title") or "")
                split_norm = _normalize_title(title)
                if not parent_norm or not split_norm:
                    continue
                # Strip the split suffix from the split title for comparison
                split_base_norm = _normalize_title(_strip_split_suffix(title)[0])
                if parent_norm == split_base_norm or split_base_norm.startswith(parent_norm):
                    filtered_indices.add(i)
                    break
            if i in filtered_indices:
                break

    canonical = [b for i, b in enumerate(books) if i not in filtered_indices]
    filtered_out = [b for i, b in enumerate(books) if i in filtered_indices]

    if filtered_out:
        logger.debug(
            "Filtered %d split books: %s",
            len(filtered_out),
            [b.get("title") for b in filtered_out],
        )

    return canonical, filtered_out
