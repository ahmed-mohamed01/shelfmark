"""Shared title/author/series matching utilities for monitored integrations.

Imported by both monitored_audiobookshelf_integration and
monitored_booklore_integration to avoid code duplication.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Matching thresholds
# ---------------------------------------------------------------------------

SERIES_NAME_MIN_RATIO = 0.75   # series name fuzzy threshold (phase 2)
SERIES_TITLE_MIN_RATIO = 0.60  # title confirmation in phase 2
TITLE_FUZZY_MIN = 0.70         # title ratio threshold for phase 3
AUTHOR_FUZZY_MIN = 0.70        # author ratio threshold for phase 3

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Strip any trailing parenthetical — handles "(We Are Bob)" vs title without it
PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")

# Strip ": subtitle" from titles (e.g. "Mitosis: A Reckoners Story" → "Mitosis")
COLON_SUBTITLE_RE = re.compile(r"\s*:.*$")

# Extract series position from "Book N", "#N", "Part N", "Volume N"
SERIES_POS_RE = re.compile(
    r"(?:book|part|vol(?:ume)?|#)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Normalisation helpers
_NORM_STRIP_RE = re.compile(r"[^a-z0-9]+")
_NORM_SPACE_RE = re.compile(r"\s+")

# Splits multi-value author/narrator strings like "A, B; C"
AUTHOR_SPLIT_RE = re.compile(r"[,;]")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def norm(value: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy comparison."""
    return _NORM_SPACE_RE.sub(" ", _NORM_STRIP_RE.sub(" ", (value or "").lower())).strip()


def normalize_shelfmark_title(title: str) -> str:
    """Strip ': subtitle' suffix from a shelfmark title."""
    return COLON_SUBTITLE_RE.sub("", title).strip()


# ---------------------------------------------------------------------------
# Series position parsing
# ---------------------------------------------------------------------------


def parse_series_position(raw: str) -> float | None:
    """Extract a numeric position from strings like '#3', 'Book 3', '3.1', '1/2'."""
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    # "N/M" means "book N of M total" — position is the numerator
    if "/" in raw:
        parts = raw.split("/", 1)
        try:
            return float(parts[0])
        except ValueError:
            pass
    m = SERIES_POS_RE.search(raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Author matching
# ---------------------------------------------------------------------------


def author_matches(source_author: str, book_authors: str) -> bool:
    """Return True if any source author token fuzzy-matches any book author token.

    Splits on commas/semicolons before comparing so that narrators or
    co-authors listed in a single field don't drag down the overall ratio.
    """
    src_parts = [p.strip() for p in AUTHOR_SPLIT_RE.split(source_author) if p.strip()]
    bk_parts = [p.strip() for p in AUTHOR_SPLIT_RE.split(book_authors) if p.strip()]
    if not src_parts:
        src_parts = [source_author]
    if not bk_parts:
        bk_parts = [book_authors]
    for a in src_parts:
        for b in bk_parts:
            if SequenceMatcher(None, norm(a), norm(b)).ratio() >= AUTHOR_FUZZY_MIN:
                return True
    return False
