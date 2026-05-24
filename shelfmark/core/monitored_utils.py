"""Shared utilities for the monitored authors/books feature.

Pure helper functions with no external dependencies — importable from any
monitored_* module without circular imports.
"""

from __future__ import annotations

import base64
import re
from typing import Any

# =============================================================================
# Numeric parsing
# =============================================================================


def _parse_float_from_text(value: str) -> float | None:
    """Extract the first float-like number from an arbitrary text string."""
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    try:
        parsed = float(match.group(0))
    except Exception:  # noqa: BLE001
        return None
    return parsed if parsed == parsed else None  # guard against NaN


def _parse_int_from_text(value: str) -> int | None:
    """Extract a non-negative integer from an arbitrary text string (digits only)."""
    digits_only = re.sub(r"[^\d]", "", value or "")
    if not digits_only:
        return None
    try:
        return int(digits_only)
    except Exception:  # noqa: BLE001
        return None


def parse_float_safe(value: Any) -> float | None:
    """Return float(value) or None on failure."""
    try:
        return float(value) if value is not None else None
    except TypeError, ValueError:
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

    values: list[Any] = (
        list(raw) if isinstance(raw, (list, tuple, set)) else list(str(raw).split(","))
    )

    normalized = {lang for lang in (normalize_language_code(value) for value in values) if lang}
    return normalized or None


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


def transform_cached_event_thumbnail_urls(events: list[dict[str, Any]]) -> None:
    """Rewrite ``book_cover_url`` on event rows to proxy through the image cache.

    Event rows snapshot ``monitored_books.cover_url`` verbatim, which is the
    raw external CDN URL. Same-origin CSP blocks those in the browser, so
    rewrite to ``/api/covers/{book_provider}_{book_provider_id}?url=…`` when
    the covers cache is enabled (same proxy that already serves author photos).
    """
    if not events:
        return

    from shelfmark.config.env import is_covers_cache_enabled
    from shelfmark.core.config import config as app_config
    from shelfmark.core.utils import normalize_base_path

    if not is_covers_cache_enabled():
        return

    base_path = normalize_base_path(app_config.get("URL_BASE", ""))

    for row in events:
        if not isinstance(row, dict):
            continue

        cover_url = row.get("book_cover_url")
        if not isinstance(cover_url, str) or not cover_url:
            continue
        # Already a proxy URL — leave it alone.
        if cover_url.startswith("/"):
            continue

        provider = str(row.get("book_provider") or "").strip()
        provider_book_id = str(row.get("book_provider_id") or "").strip()
        if not (provider and provider_book_id):
            continue

        cache_id = f"{provider}_{provider_book_id}"
        encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
        if base_path:
            row["book_cover_url"] = f"{base_path}/api/covers/{cache_id}?url={encoded_url}"
        else:
            row["book_cover_url"] = f"/api/covers/{cache_id}?url={encoded_url}"


# =============================================================================
# Book popularity extraction
# =============================================================================


def extract_book_popularity(
    display_fields: Any,
) -> tuple[float | None, int | None, int | None]:
    """Parse rating, ratings_count, and readers_count from provider display_fields.

    Args:
        display_fields: List of display field dicts from provider metadata
            (each has 'icon', 'label', 'value' keys).

    Returns:
        Tuple of (rating, ratings_count, readers_count). Any entry may be None.
    """
    if not isinstance(display_fields, list):
        return None, None, None

    rating: float | None = None
    ratings_count: int | None = None
    readers_count: int | None = None

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


# =============================================================================
# Author photo resolution
# =============================================================================


def extract_author_photo_url(author: dict) -> str | None:
    """Extract photo URL from a Hardcover author GraphQL/Typesense object.

    Checks ``image.url``, ``image`` (string), then ``cached_image``.
    Returns the raw external URL or None.
    """
    image_obj = author.get("image")
    if isinstance(image_obj, dict) and image_obj.get("url"):
        return image_obj["url"]
    if isinstance(image_obj, str) and image_obj:
        return image_obj
    return author.get("cached_image") or None


# =============================================================================
# Content-type matching for releases
# =============================================================================


_EBOOK_FORMATS = frozenset(
    {"epub", "mobi", "azw", "azw3", "pdf", "fb2", "djvu", "cbz", "cbr", "lit", "lrf"}
)
_AUDIOBOOK_FORMATS = frozenset({"m4b", "mp3", "m4a", "flac", "ogg", "wma", "aac", "wav", "opus"})


def _release_field(release: Any, name: str) -> str:
    """Read a field from a release object or dict, normalised to lowercase string."""
    value = release.get(name) if isinstance(release, dict) else getattr(release, name, None)
    return str(value or "").strip().lower()


def release_matches_content_type(release: Any, requested: str) -> bool:
    """Return True if a release plausibly matches the requested content_type.

    Filters cross-type pollution from sources that support both kinds (Prowlarr)
    but expand or mis-categorize results — e.g. an audiobook search that
    auto-expands and returns ebook torrents. Also catches the Anna's Archive
    parser quirk where ebook records get tagged "audiobook" because the page
    text contains "Book (Audiobook)".

    Decision order:
    1. If `format` clearly belongs to the wrong family, drop.
    2. Else if `content_type` clearly indicates wrong family, drop.
    3. Else keep (ambiguous releases pass through; downstream postprocessing
       will catch genuinely-wrong files).

    Works on both Release objects and asdict-style dicts.
    """
    fmt = _release_field(release, "format")
    rct = _release_field(release, "content_type")
    is_audio_format = fmt in _AUDIOBOOK_FORMATS if fmt else False
    is_ebook_format = fmt in _EBOOK_FORMATS if fmt else False
    rct_says_audio = bool(rct) and "audiobook" in rct
    rct_says_ebook = bool(rct) and "audiobook" not in rct

    if requested == "audiobook":
        if is_ebook_format:
            return False
        return not (rct_says_ebook and not is_audio_format)
    # requested == "ebook"  # noqa: ERA001
    if is_audio_format:
        return False
    return not (rct_says_audio and not is_ebook_format)


def source_supports_content_type(source_row: dict | None, requested: str) -> bool:
    """Return True if a source_row from list_available_sources() declares support
    for the requested content_type.

    Sources without an explicit `supported_content_types` list are assumed to
    support both — matches the existing default in list_available_sources().
    The default tuple lives in `shelfmark.core.request_policy` so all callers
    agree on what "no declaration" implies.
    """
    if not source_row:
        return False
    from shelfmark.core.request_policy import DEFAULT_SUPPORTED_CONTENT_TYPES

    supported = source_row.get("supported_content_types") or DEFAULT_SUPPORTED_CONTENT_TYPES
    return requested in supported
