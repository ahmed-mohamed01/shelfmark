"""Shared utilities for the monitored authors/books feature.

Pure helper functions with no external dependencies — importable from any
monitored_* module without circular imports.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Optional, Tuple


# =============================================================================
# Numeric parsing
# =============================================================================


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


def parse_float_safe(value: Any) -> Optional[float]:
    """Return float(value) or None on failure."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# =============================================================================
# Content-type helpers
# =============================================================================


def normalize_content_type(value: Any) -> str:
    """Return 'ebook' or 'audiobook'; defaults to 'ebook' for unknown values."""
    ct = str(value or "ebook").strip().lower()
    return ct if ct in {"ebook", "audiobook"} else "ebook"


# =============================================================================
# Language helpers
# =============================================================================


def normalize_preferred_languages(raw: Any) -> set[str] | None:
    from shelfmark.metadata_providers import normalize_language_code

    if raw is None:
        return None

    values: list[Any]
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [part for part in str(raw).split(",")]

    normalized = {
        lang
        for lang in (normalize_language_code(value) for value in values)
        if lang
    }
    return normalized or None


def should_hide_book_for_language(
    *, book_language: str | None, preferred_languages: set[str] | None
) -> bool:
    if not preferred_languages:
        return False
    from shelfmark.metadata_providers import normalize_language_code

    normalized_book_language = normalize_language_code(book_language)
    if not normalized_book_language:
        return False
    return normalized_book_language not in preferred_languages


# =============================================================================
# Cover URL transformation
# =============================================================================


def transform_cached_cover_urls(
    rows: list[dict[str, Any]],
    *,
    provider_key: str = "provider",
    provider_id_key: str = "provider_book_id",
) -> None:
    """Rewrite cover_url fields in *rows* to proxy through the local image cache.

    Operates in-place. No-ops if the covers cache feature is disabled or the
    row list is empty.
    """
    if not rows:
        return

    from shelfmark.config.env import is_covers_cache_enabled
    from shelfmark.core.config import config as app_config
    from shelfmark.core.utils import normalize_base_path

    if not is_covers_cache_enabled():
        return

    base_path = normalize_base_path(app_config.get("URL_BASE", ""))

    for row in rows:
        if not isinstance(row, dict):
            continue

        cover_url = row.get("cover_url")
        if not isinstance(cover_url, str) or not cover_url:
            continue

        provider = str(row.get(provider_key) or "").strip()
        provider_book_id = str(row.get(provider_id_key) or "").strip()

        if provider and provider_book_id:
            cache_id = f"{provider}_{provider_book_id}"
        else:
            fallback_id = str(row.get("id") or "").strip()
            cache_id = f"monitored_{fallback_id}" if fallback_id else ""

        if cache_id:
            encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
            if base_path:
                row["cover_url"] = f"{base_path}/api/covers/{cache_id}?url={encoded_url}"
            else:
                row["cover_url"] = f"/api/covers/{cache_id}?url={encoded_url}"


# =============================================================================
# Book popularity extraction
# =============================================================================


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
