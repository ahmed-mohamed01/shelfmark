"""Shared helpers for monitored integrations (ABS / Booklore).

Used by the integration adapters for author-discovery (matching the entity
to a source-side author) and series-position regex extraction. The actual
attribution scoring is unified in ``monitored_attribution_v2``.
"""

from __future__ import annotations

import re

# Extract series position from "Book N", "#N", "Part N", "Volume N".
# Used by integration adapters when parsing source-side series fields.
SERIES_POS_RE = re.compile(
    r"(?:book|part|vol(?:ume)?|#)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Splits multi-value author/narrator strings like "A, B; C".
# Used by integration author-discovery loops.
AUTHOR_SPLIT_RE = re.compile(r"[,;]")

# Normalisation helpers
_NORM_STRIP_RE = re.compile(r"[^a-z0-9]+")
_NORM_SPACE_RE = re.compile(r"\s+")


def norm(value: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy comparison."""
    return _NORM_SPACE_RE.sub(" ", _NORM_STRIP_RE.sub(" ", (value or "").lower())).strip()
