"""Filter non-canonical and noise books from a monitored author's book list.

Split books are partial editions (e.g., "The Way of Kings, Part 1") that should
be excluded when the canonical full edition ("The Way of Kings") is present.

Noise books are translations, anthologies, boxed sets, graphic novel adaptations,
magazine issues, and other non-primary editions detected via title patterns,
language heuristics, and contributor count.
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


# ---------------------------------------------------------------------------
# Noise filter — title patterns, language heuristic
# ---------------------------------------------------------------------------

# Title patterns that indicate non-primary editions / noise entries.
_NOISE_TITLE_PATTERNS = [
    re.compile(r"\(\d+ of \d+\)", re.IGNORECASE),                    # "(1 of 5)" dramatized splits
    re.compile(r"Boxed Set|Box Set|\d-Book Bundle", re.IGNORECASE),  # boxed sets
    re.compile(r"Sneak Peek|Free Preview", re.IGNORECASE),           # previews
    re.compile(r"Chapters[\s-]\d", re.IGNORECASE),                   # "Chapters-1-7" samples
    re.compile(r"Annotations$", re.IGNORECASE),                      # annotations
    re.compile(r":\s*The Play$", re.IGNORECASE),                     # stage plays
    re.compile(r"Dramatized Adaptation", re.IGNORECASE),             # dramatized adaptations
    re.compile(r"Graphic Novel", re.IGNORECASE),                     # graphic novel adaptations
    re.compile(r"Diary \d{4}", re.IGNORECASE),                       # diaries "Diary 1999"
    re.compile(r"Yearbook", re.IGNORECASE),                          # yearbooks
    re.compile(r"Handbook \d{4}", re.IGNORECASE),                    # handbooks
    re.compile(r"Colou?ring Book", re.IGNORECASE),                   # colouring books
    re.compile(r"Quizbook", re.IGNORECASE),                          # quiz books
    re.compile(r"\bGURPS\b", re.IGNORECASE),                         # RPG sourcebooks
    re.compile(r"Magazine Issue|Magazine,", re.IGNORECASE),           # magazine issues
]

# Non-Latin script characters (Cyrillic, CJK, Arabic, Thai) — strong translation signal.
_NON_LATIN_RE = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u0600-\u06FF\u0E00-\u0E7F]"
)

# Contributor count threshold for auto-hide (anthologies with many contributors).
ANTHOLOGY_CONTRIBUTOR_THRESHOLD = 10


def _get_contrib_count(book: dict[str, Any]) -> int:
    """Extract contributor count from GraphQL or parsed shape."""
    # GraphQL shape: contributions_aggregate.aggregate.count
    agg = book.get("contributions_aggregate")
    if isinstance(agg, dict):
        return (agg.get("aggregate") or {}).get("count", 0)
    # Already parsed (flat key)
    val = book.get("contrib_count")
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    return 0


def _get_lang_codes(book: dict[str, Any]) -> list[str]:
    """Extract language codes from GraphQL shape."""
    # GraphQL shape: lang_editions -> [{language: {code2: "en"}}]
    raw = book.get("lang_editions")
    if isinstance(raw, list):
        codes = []
        for e in raw:
            lang = e.get("language") if isinstance(e, dict) else None
            if isinstance(lang, dict) and lang.get("code2"):
                codes.append(lang["code2"])
        return codes
    return []


def classify_noise(
    book: dict[str, Any],
    *,
    lang_codes: list[str] | None = None,
) -> str | None:
    """Classify a book as noise and return the reason, or None if it should be kept.

    Returns a short string describing the noise category, or None.
    """
    title = (book.get("title") or "").strip()
    users_read = _get_readers_count(book) or 0
    preferred = lang_codes or ["en"]

    # Title pattern match
    for pattern in _NOISE_TITLE_PATTERNS:
        if pattern.search(title):
            return f"title:{pattern.pattern}"

    # Language: no preferred-language edition + has readers (released non-English)
    book_langs = _get_lang_codes(book)
    if book_langs and not any(lc in preferred for lc in book_langs) and users_read > 0:
        return f"lang:{','.join(book_langs[:3])}"

    # Non-Latin title characters (Cyrillic, CJK, etc.) + has readers
    if _NON_LATIN_RE.search(title) and users_read > 0:
        return "non_latin_title"

    return None


def filter_noise_books(
    books: list[dict[str, Any]],
    *,
    lang_codes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate books into kept, noise (discard), and auto-hide (anthologies).

    Returns ``(kept, noise_filtered, auto_hide)``.

    - *noise_filtered*: books matched by title patterns or language heuristic — discard.
    - *auto_hide*: books with contributor count > threshold — upsert with ``hidden=1``.
    """
    kept: list[dict[str, Any]] = []
    noise: list[dict[str, Any]] = []
    auto_hide: list[dict[str, Any]] = []

    noise_reasons: list[tuple[str, str]] = []
    for book in books:
        reason = classify_noise(book, lang_codes=lang_codes)
        if reason:
            noise.append(book)
            noise_reasons.append((book.get("title", ""), reason))
            continue

        cc = _get_contrib_count(book)
        if cc > ANTHOLOGY_CONTRIBUTOR_THRESHOLD:
            auto_hide.append(book)
            continue

        kept.append(book)

    if noise:
        logger.debug(
            "Noise-filtered %d books: %s",
            len(noise),
            noise_reasons[:10],
        )
    if auto_hide:
        logger.debug(
            "Auto-hiding %d anthology books (contributor count > %d)",
            len(auto_hide),
            ANTHOLOGY_CONTRIBUTOR_THRESHOLD,
        )

    return kept, noise, auto_hide
