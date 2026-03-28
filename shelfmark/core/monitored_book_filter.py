"""Filter non-canonical and noise books from a monitored author's book list.

Split books are partial editions (e.g., "The Way of Kings, Part 1") that should
be excluded when the canonical full edition ("The Way of Kings") is present.

Noise books are boxed sets, graphic novel adaptations, magazine issues, samplers,
and other non-primary editions detected via title patterns.

Low-quality entries (translations without metadata, bare stubs) are auto-hidden
via a data quality score that combines edition availability, language confirmation,
metadata completeness, and popularity.
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
#   "(Part 2 of 2)"  "(Part 1 of 3)"
_SPLIT_SUFFIX = re.compile(
    r"(?:"
    r"[,\s]+(?:part|vol\.?|volume)\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"|"
    r"\s*\((?:part\s+)?\d+\s+of\s+\d+\)"
    r")\s*$",
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
            (s.get("name"), s.get("position"))
            for s in all_series
            if isinstance(s, dict) and s.get("name")
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
# Deduplication — merge duplicate Hardcover entries for the same book
# ---------------------------------------------------------------------------


def deduplicate_books(
    books: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Merge duplicate Hardcover entries for the same underlying book.

    Two entries are considered duplicates when:
    - Their full normalised titles match exactly (catches case/article
      differences like "The Hunger Games" vs "Hunger games"), OR
    - One title has no colon and the other's pre-colon portion normalises
      to the same key (catches subtitle variants like "The Age of Diagnosis"
      vs "The Age of Diagnosis: Sickness, Health and How Modern Medicine
      Has Gone Too Far").

    Series books using "Series Name: Book Title" format are NOT merged
    because both entries contain colons, so neither qualifies as the
    standalone "anchor" title.

    The entry with the higher ``users_count`` is kept.

    Returns ``(deduped_books, merged_count)`` where *merged_count* is
    the number of entries removed.
    """
    if not books:
        return [], 0

    norms = [_normalize_title(b.get("title") or "") for b in books]

    parent: list[int] = list(range(len(books)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Step 1: exact-match on full normalised title
    exact: dict[str, list[int]] = {}
    for i, norm in enumerate(norms):
        if norm:
            exact.setdefault(norm, []).append(i)

    for indices in exact.values():
        for j in range(1, len(indices)):
            union(indices[0], indices[j])

    # Step 2: subtitle-variant matching.
    # A standalone title (no colon) can merge with a colon-bearing title
    # whose pre-colon portion matches — BUT only when exactly 1 colon-bearing
    # book matches.  Multiple matches means it's a series name
    # (e.g. "Azarinth Healer" matching "Azarinth Healer: Book One/Two/..."),
    # not a subtitle variant.
    colon_stripped: dict[str, list[int]] = {}
    for i, book in enumerate(books):
        raw_title = (book.get("title") or "").strip()
        colon_idx = raw_title.find(":")
        if colon_idx > 0:
            pre_colon_norm = _normalize_title(raw_title[:colon_idx])
            if pre_colon_norm:
                colon_stripped.setdefault(pre_colon_norm, []).append(i)

    for i, book in enumerate(books):
        raw_title = (book.get("title") or "").strip()
        if ":" in raw_title:
            continue  # only standalone (no-colon) titles can be anchors
        norm = norms[i]
        if not norm:
            continue
        matches = colon_stripped.get(norm, [])
        if len(matches) != 1:
            continue  # 0 = no match, 2+ = series name, not subtitle variant
        j = matches[0]
        if find(i) != find(j):
            union(i, j)

    # Step 3: collect groups, pick winner
    groups: dict[int, list[int]] = {}
    for i in range(len(books)):
        root = find(i)
        groups.setdefault(root, []).append(i)

    removed: set[int] = set()
    for indices in groups.values():
        if len(indices) < 2:
            continue
        best_idx = max(indices, key=lambda i: books[i].get("users_count") or 0)
        for i in indices:
            if i != best_idx:
                removed.add(i)
                logger.debug(
                    "Dedup: dropping %r (id=%s, users=%s) in favour of %r (id=%s, users=%s)",
                    books[i].get("title"),
                    books[i].get("id"),
                    books[i].get("users_count"),
                    books[best_idx].get("title"),
                    books[best_idx].get("id"),
                    books[best_idx].get("users_count"),
                )

    deduped = [b for i, b in enumerate(books) if i not in removed]
    return deduped, len(removed)


# ---------------------------------------------------------------------------
# Core filter — split editions
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
            if pos is None:
                continue
            try:
                fpos = float(pos)
            except (TypeError, ValueError):
                continue
            if fpos == int(fpos):
                key = (series_name.strip().lower(), int(fpos))
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
                    # One or both sides missing readers data — title match is sufficient
                    filtered_indices.add(i)
                    break
            if i in filtered_indices:
                continue

        # --- Check 2: fractional series position with title match ---
        for series_name, pos in _get_series_entries(book):
            if pos is None:
                continue
            try:
                fpos = float(pos)
            except (TypeError, ValueError):
                continue
            frac = fpos - int(fpos)
            # Only fractional positions like .1, .2 — skip integers and .5 (novellas)
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
# Noise filter — title patterns
# ---------------------------------------------------------------------------

# Title patterns that indicate non-primary editions / noise entries.
_NOISE_TITLE_PATTERNS = [
    re.compile(r"\((?:part\s+)?\d+\s+of\s+\d+\)", re.IGNORECASE),      # "(1 of 5)", "(Part 2 of 2)" dramatized/split entries
    re.compile(r"Boxed Set|Box Set|\d-Book Bundle", re.IGNORECASE),  # boxed sets
    re.compile(r"Sneak Peek|Free Preview", re.IGNORECASE),           # previews
    re.compile(r"Chapters?[\s-]+\d", re.IGNORECASE),                  # "Chapters-1-7", "Chapter 1" excerpts
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
    re.compile(r"Adventure Game\b", re.IGNORECASE),                  # tabletop RPG/adventure game books
    re.compile(r"Magazine Issue|Magazine,", re.IGNORECASE),           # magazine issues
    re.compile(r"\bSampler\b", re.IGNORECASE),                       # samplers/excerpts
    re.compile(r"^[^/]{4,}\s/\s[^/]{4,}$"),                           # "Title / Title" combined volumes (4+ chars each side)
]

# Non-Latin script characters (Cyrillic, CJK, Arabic, Thai) — strong translation signal.
_NON_LATIN_RE = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u0600-\u06FF\u0E00-\u0E7F]"
)

# Extended Latin diacritics common in non-English European languages but rare
# in English titles.  Two or more hits strongly suggests a translation.
# Covers: àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþ and uppercase variants.
_DIACRITIC_RE = re.compile(
    r"[àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþ"
    r"ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ"
    r"ąćęłńśźżĄĆĘŁŃŚŹŻ"        # Polish
    r"ğışĞİŞ"                     # Turkish
    r"ěřůĚŘŮ"                     # Czech
    r"]"
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


# ---------------------------------------------------------------------------
# Data quality scoring
# ---------------------------------------------------------------------------

# Minimum quality score for a book to be shown by default.  Books below
# this are auto-hidden (user can unhide from the Hidden section).
# Calibrated against 7543 books from 69 authors: at threshold=20,
# zero confirmed-English false positives and zero confirmed
# non-English false negatives.
DATA_QUALITY_THRESHOLD = 20


def compute_data_quality(
    book: dict[str, Any],
    *,
    lang_codes: list[str] | None = None,
) -> int:
    """Score a book's data quality on a 0-100 scale.

    Combines edition availability, language confirmation, metadata
    completeness, popularity, and title-based translation signals.

    Books scoring below ``DATA_QUALITY_THRESHOLD`` are auto-hidden.
    """
    preferred = lang_codes or ["en"]
    title = (book.get("title") or "").strip()
    phys = book.get("default_physical_edition") or {}
    book_langs = _get_lang_codes(book)
    uc = book.get("users_count") or 0
    readers = _get_readers_count(book) or 0

    score = 0

    # ── Edition availability (strongest signals) ──
    if book.get("preferred_isbns"):                              score += 35
    if book.get("preferred_asins"):                              score += 25

    # ── Language confirmation ──
    if book_langs and any(lc in preferred for lc in book_langs): score += 20

    # ── Metadata completeness ──
    if (book.get("description") or "").strip():                  score += 5
    if (book.get("image") or {}).get("url"):                     score += 3
    if phys.get("isbn_13") or phys.get("isbn_10"):               score += 8
    if phys.get("pages"):                                        score += 5
    if book.get("rating"):                                       score += 2
    if readers > 0:                                              score += 3
    if book.get("cached_tags"):                                  score += 2

    # ── Popularity bonus (compensates for popular books missing
    #    edition data on Hardcover) ──
    if   uc >= 50: score += 25
    elif uc >= 20: score += 20
    elif uc >= 10: score += 12
    elif uc >=  5: score += 5

    # ── Penalties ──
    diac = len(_DIACRITIC_RE.findall(title))

    # Confirmed non-preferred language editions only
    if book_langs and not any(lc in preferred for lc in book_langs):
        score -= 40

    # Non-Latin script (Cyrillic, CJK, Arabic, Thai)
    if _NON_LATIN_RE.search(title):
        score -= 40

    # Diacritics in title — translation signal
    if diac >= 2:
        score -= 25
    elif diac == 1 and not book_langs:
        score -= 15

    return max(0, min(100, score))


def classify_noise(book: dict[str, Any]) -> str | None:
    """Classify a book as hard noise (should be discarded entirely).

    Only matches title patterns that indicate non-primary editions
    (boxed sets, previews, samplers, graphic novels, etc.).
    Language and data-quality filtering is handled separately by
    ``compute_data_quality()``.

    Returns a short string describing the noise category, or None.
    """
    title = (book.get("title") or "").strip()

    for pattern in _NOISE_TITLE_PATTERNS:
        if pattern.search(title):
            return f"title:{pattern.pattern}"

    return None


def filter_noise_books(
    books: list[dict[str, Any]],
    *,
    lang_codes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate books into kept, noise (discard), and auto-hide.

    Returns ``(kept, noise_filtered, auto_hide)``.

    - *noise_filtered*: books matched by title patterns — discard entirely.
    - *auto_hide*: compilations, anthologies (>N contributors), or books
      with data quality below ``DATA_QUALITY_THRESHOLD`` — upsert with
      ``hidden=1`` (user can unhide).
    """
    kept: list[dict[str, Any]] = []
    noise: list[dict[str, Any]] = []
    auto_hide: list[dict[str, Any]] = []

    noise_reasons: list[tuple[str, str]] = []
    hide_reasons: list[tuple[str, str]] = []

    for book in books:
        title = book.get("title", "")

        # Hard noise — title patterns → discard
        reason = classify_noise(book)
        if reason:
            noise.append(book)
            noise_reasons.append((title, reason))
            continue

        # Auto-hide: compilations
        if book.get("compilation"):
            auto_hide.append(book)
            hide_reasons.append((title, "compilation"))
            continue

        # Auto-hide: anthologies (many contributors)
        cc = _get_contrib_count(book)
        if cc > ANTHOLOGY_CONTRIBUTOR_THRESHOLD:
            auto_hide.append(book)
            hide_reasons.append((title, f"contributors:{cc}"))
            continue

        # Auto-hide: low data quality (translations, bare metadata)
        quality = compute_data_quality(book, lang_codes=lang_codes)
        if quality < DATA_QUALITY_THRESHOLD:
            auto_hide.append(book)
            hide_reasons.append((title, f"quality:{quality}"))
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
            "Auto-hiding %d books: %s",
            len(auto_hide),
            hide_reasons[:20],
        )

    return kept, noise, auto_hide
