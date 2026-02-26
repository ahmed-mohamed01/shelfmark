"""Shared utilities for the monitored authors/books feature.

Pure helper functions for text parsing and metadata extraction with no
external dependencies — importable from any monitored_* module.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple


def _parse_float_from_text(value: str) -> Optional[float]:
    """Extract the first float-like number from an arbitrary text string."""
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    try:
        parsed = float(match.group(0))
    except Exception:
        return None
    return parsed if parsed == parsed else None  # guard against NaN


def _parse_int_from_text(value: str) -> Optional[int]:
    """Extract a non-negative integer from an arbitrary text string (digits only)."""
    digits_only = re.sub(r"[^\d]", "", value or "")
    if not digits_only:
        return None
    try:
        return int(digits_only)
    except Exception:
        return None


def extract_book_popularity(
    display_fields: Any,
) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Parse rating, ratings_count, and readers_count from provider display_fields.

    Args:
        display_fields: List of display field dicts from provider metadata
            (each has 'icon', 'label', 'value' keys).

    Returns:
        Tuple of (rating, ratings_count, readers_count). Any entry may be None.
    """
    if not isinstance(display_fields, list):
        return None, None, None

    rating: Optional[float] = None
    ratings_count: Optional[int] = None
    readers_count: Optional[int] = None

    for raw in display_fields:
        if not isinstance(raw, dict):
            continue
        icon = str(raw.get("icon") or "").strip().lower()
        label = str(raw.get("label") or "").strip().lower()
        value = str(raw.get("value") or "")

        if rating is None and (icon == "star" or "rating" in label):
            maybe_rating = _parse_float_from_text(value)
            if maybe_rating is not None and maybe_rating <= 10:
                rating = maybe_rating

            paren_match = re.search(r"\(([^)]+)\)", value)
            if paren_match and ratings_count is None:
                parsed_count = _parse_int_from_text(paren_match.group(1))
                if parsed_count is not None:
                    ratings_count = parsed_count
            continue

        if ratings_count is None and re.search(r"ratings?", label):
            parsed_count = _parse_int_from_text(value)
            if parsed_count is not None:
                ratings_count = parsed_count
            continue

        if readers_count is None and (
            icon == "users" or re.search(r"readers?|users?|followers?|people", label)
        ):
            parsed_readers = _parse_int_from_text(value)
            if parsed_readers is not None:
                readers_count = parsed_readers

    return rating, ratings_count, readers_count
