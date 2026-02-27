"""Author sync orchestration for the monitored authors feature.

Handles fetching book metadata from providers, upserting to the local
monitored_books DB, cover prefetching, and cover URL transformation for
the image proxy cache.

Functions are module-level so they can be called from both the Flask routes
and the background scheduled refresh without requiring Flask context.
"""
from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.metadata_providers import normalize_language_code

logger = setup_logger(__name__)


def normalize_preferred_languages(raw: Any) -> set[str] | None:
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


def should_hide_book_for_language(*, book_language: str | None, preferred_languages: set[str] | None) -> bool:
    if not preferred_languages:
        return False
    normalized_book_language = normalize_language_code(book_language)
    if not normalized_book_language:
        return False
    return normalized_book_language not in preferred_languages


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


def sync_author_entity(
    user_db: MonitoredDB,
    *,
    db_user_id: int | None,
    entity: dict[str, Any],
    prefetch_covers: bool = False,
    preferred_languages: set[str] | None = None,
) -> int:
    """Fetch all books for a monitored author and upsert them into the local DB.

    Paginates through the metadata provider, upserts each discovered book,
    optionally prefetches cover images into the local cache, then applies
    monitor-mode flags based on current availability.

    Args:
        user_db: MonitoredDB instance.
        db_user_id: The user context for this sync.
        entity: Monitored entity row (must have kind="author").
        prefetch_covers: If True, download cover images into the local cache.

    Returns:
        Number of books discovered/updated.

    Raises:
        ValueError: If entity.kind is not "author".
        RuntimeError: If the metadata provider is unavailable.
    """
    if entity.get("kind") != "author":
        raise ValueError("Sync is only supported for author entities")

    entity_id = int(entity.get("id"))
    author_name = str(entity.get("name") or "")
    provider_name = str(entity.get("provider") or "hardcover")

    from shelfmark.metadata_providers import MetadataSearchOptions, SortOrder, get_provider, get_provider_kwargs

    provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
    if not provider.is_available():
        raise RuntimeError(f"Metadata provider '{provider_name}' is not available")

    cache = None
    covers_enabled = False
    if prefetch_covers:
        try:
            from shelfmark.config.env import is_covers_cache_enabled
            covers_enabled = bool(is_covers_cache_enabled())
            if covers_enabled:
                from shelfmark.core.image_cache import get_image_cache
                cache = get_image_cache()
        except Exception:
            cache = None
            covers_enabled = False

    from shelfmark.core.monitored_utils import extract_book_popularity

    limit = 40
    page = 1
    has_more = True
    discovered = 0

    while has_more and page <= 15 and discovered < 600:
        options = MetadataSearchOptions(
            query="",
            limit=limit,
            page=page,
            sort=SortOrder.RELEVANCE,
            fields={"author": author_name},
        )
        result = provider.search_paginated(options)

        language_by_provider_id: dict[str, str | None] = {}
        if preferred_languages and hasattr(provider, "get_book_languages_batch"):
            provider_ids = [
                str(getattr(book, "provider_id", "") or "").strip()
                for book in result.books
                if str(getattr(book, "provider_id", "") or "").strip()
            ]
            try:
                raw_lang_map = provider.get_book_languages_batch(provider_ids)
            except Exception:
                raw_lang_map = {}
            if isinstance(raw_lang_map, dict):
                for key, value in raw_lang_map.items():
                    normalized_key = str(key or "").strip()
                    if not normalized_key:
                        continue
                    language_by_provider_id[normalized_key] = normalize_language_code(value)

        for book in result.books:
            payload = asdict(book)
            authors = payload.get("authors")
            authors_str = ", ".join(authors) if isinstance(authors, list) else None
            rating, ratings_count, readers_count = extract_book_popularity(payload.get("display_fields"))
            provider_book_id = str(payload.get("provider_id") or "")
            provider_value = str(payload.get("provider") or provider_name)
            cover_url = payload.get("cover_url")
            language = normalize_language_code(payload.get("language"))
            if not language and provider_book_id:
                language = language_by_provider_id.get(provider_book_id)

            user_db.upsert_monitored_book(
                user_id=db_user_id,
                entity_id=entity_id,
                provider=provider_value,
                provider_book_id=provider_book_id,
                title=str(payload.get("title") or ""),
                authors=authors_str,
                publish_year=payload.get("publish_year"),
                release_date=payload.get("release_date"),
                isbn_13=payload.get("isbn_13"),
                cover_url=cover_url,
                series_name=payload.get("series_name"),
                series_position=payload.get("series_position"),
                series_count=payload.get("series_count"),
                language=language,
                hidden=should_hide_book_for_language(
                    book_language=language,
                    preferred_languages=preferred_languages,
                ),
                rating=rating,
                ratings_count=ratings_count,
                readers_count=readers_count,
                state="discovered",
            )

            if covers_enabled and cache is not None and isinstance(cover_url, str) and cover_url.strip():
                cache_id = f"{provider_value}_{provider_book_id}"
                try:
                    if cache.get(cache_id) is None:
                        cache.fetch_and_cache(cache_id, cover_url)
                except Exception:
                    pass

            discovered += 1

        has_more = bool(getattr(result, "has_more", False))
        page += 1

    refreshed_books = user_db.list_monitored_books(user_id=db_user_id, entity_id=entity_id) or []
    existing_files = user_db.list_monitored_book_files(user_id=db_user_id, entity_id=entity_id) or []
    if refreshed_books and existing_files:
        from shelfmark.core.monitored_files import expand_monitored_file_rows_for_equivalent_books
        existing_files = expand_monitored_file_rows_for_equivalent_books(
            books=refreshed_books,
            file_rows=existing_files,
        )

    from shelfmark.core.monitored_files import apply_monitor_modes_for_books
    apply_monitor_modes_for_books(
        user_db,
        db_user_id=db_user_id,
        entity=entity,
        books=refreshed_books,
        file_rows=existing_files,
    )

    user_db.update_monitored_entity_check(entity_id=entity_id, last_error=None)
    return discovered
