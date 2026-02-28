"""Data operations layer for the monitored feature.

Combines provider API calls with DB persistence. Each function is a complete,
repeatable unit of work that can be called from orchestration or route layers.

Import graph: monitored_db_ops → monitored_utils (no other monitored_* imports)
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.monitored_utils import (
    extract_book_popularity,
    normalize_preferred_languages as _normalize_preferred_languages,
    should_hide_book_for_language,
    transform_cached_cover_urls,
)
from shelfmark.metadata_providers import normalize_language_code

logger = setup_logger(__name__)


# =============================================================================
# Author metadata
# =============================================================================


def fetch_author_metadata(
    db: MonitoredDB,
    *,
    entity: dict[str, Any],
    user_id: int | None,
    preferred_languages: set[str] | None = None,
    prefetch_covers: bool = False,
) -> list[dict[str, Any]]:
    """Fetch all books for a monitored author and upsert them into the local DB.

    Paginates through the metadata provider (up to 15 pages / 600 books),
    optionally prefetches cover images, and returns the list of all books
    currently in the DB for this entity after upsert.

    Raises:
        ValueError: If entity.kind is not "author".
        MonitoredProviderError: If the metadata provider is unavailable.
    """
    from shelfmark.core.monitored_types import MonitoredProviderError

    if entity.get("kind") != "author":
        raise ValueError("fetch_author_metadata is only supported for author entities")

    entity_id = int(entity["id"])
    author_name = str(entity.get("name") or "")
    provider_name = str(entity.get("provider") or "hardcover")

    from shelfmark.metadata_providers import MetadataSearchOptions, SortOrder, get_provider, get_provider_kwargs

    provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
    if not provider.is_available():
        raise MonitoredProviderError(f"Metadata provider '{provider_name}' is not available")

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
                    if normalized_key:
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
            is_compilation = bool(payload.get("is_compilation"))
            if not language and provider_book_id:
                language = language_by_provider_id.get(provider_book_id)

            db.upsert_monitored_book(
                user_id=user_id,
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
                is_compilation=is_compilation,
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

    return db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []


def fetch_single_book_metadata(
    db: MonitoredDB,
    *,
    entity_id: int,
    provider: str,
    provider_id: str,
    user_id: int | None,
    seed_name: str,
    seed_settings: dict[str, Any],
    preferred_languages: set[str] | None = None,
) -> None:
    """Fetch metadata for a single book entity and upsert to DB.

    Used when creating a kind='book' monitored entity — seeds the book record
    with fresh provider data and applies monitor flags from settings.
    """
    seeded_title = seed_name
    seeded_authors = str(seed_settings.get("book_author") or "").strip() or None
    seeded_cover = str(seed_settings.get("photo_url") or "").strip() or None
    seeded_year: Any = None
    seeded_release_date: str | None = None
    seeded_isbn13: str | None = None
    seeded_series_name: str | None = None
    seeded_series_position: float | None = None
    seeded_series_count: int | None = None
    seeded_language: str | None = None
    seeded_is_compilation: bool | None = None
    seeded_rating: float | None = None
    seeded_ratings_count: int | None = None
    seeded_readers_count: int | None = None

    try:
        from shelfmark.metadata_providers import get_provider, get_provider_kwargs

        prov = get_provider(provider, **get_provider_kwargs(provider))
        if prov.is_available():
            book = prov.get_book(provider_id)
            if book is not None:
                payload = asdict(book)
                seeded_title = str(payload.get("title") or seeded_title or "").strip() or seeded_title
                authors = payload.get("authors")
                if isinstance(authors, list):
                    seeded_authors = (
                        ", ".join(str(a).strip() for a in authors if str(a).strip()) or seeded_authors
                    )
                seeded_year = payload.get("publish_year")
                seeded_release_date = payload.get("release_date")
                seeded_isbn13 = payload.get("isbn_13")
                seeded_cover = payload.get("cover_url") or seeded_cover
                seeded_series_name = payload.get("series_name")
                seeded_series_position = payload.get("series_position")
                seeded_series_count = payload.get("series_count")
                seeded_language = normalize_language_code(payload.get("language"))
                seeded_is_compilation = bool(payload.get("is_compilation"))
                seeded_rating, seeded_ratings_count, seeded_readers_count = extract_book_popularity(
                    payload.get("display_fields")
                )
    except Exception as exc:
        logger.warning(
            "Book monitor metadata seed failed provider=%s provider_id=%s: %s",
            provider, provider_id, exc,
        )

    try:
        db.upsert_monitored_book(
            user_id=user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_id,
            title=seeded_title or seed_name,
            authors=seeded_authors,
            publish_year=seeded_year,
            release_date=seeded_release_date,
            isbn_13=seeded_isbn13,
            cover_url=seeded_cover,
            series_name=seeded_series_name,
            series_position=seeded_series_position,
            series_count=seeded_series_count,
            language=seeded_language,
            hidden=should_hide_book_for_language(
                book_language=seeded_language,
                preferred_languages=preferred_languages,
            ),
            is_compilation=seeded_is_compilation,
            rating=seeded_rating,
            ratings_count=seeded_ratings_count,
            readers_count=seeded_readers_count,
            state="discovered",
        )
        monitor_ebook = bool(seed_settings.get("monitor_ebook", True))
        monitor_audiobook = bool(seed_settings.get("monitor_audiobook", True))
        db.set_monitored_book_monitor_flags(
            user_id=user_id,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_id,
            monitor_ebook=monitor_ebook,
            monitor_audiobook=monitor_audiobook,
        )
    except Exception as exc:
        logger.warning(
            "Book monitor seed upsert failed entity_id=%s provider=%s provider_id=%s: %s",
            entity_id, provider, provider_id, exc,
        )


# =============================================================================
# Book pruning
# =============================================================================


def prune_deleted_books(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    current_provider_ids: set[str],
) -> int:
    """Delete books from DB that are no longer present at the provider.

    Args:
        current_provider_ids: Set of 'provider:provider_book_id' strings from
            the latest provider fetch. Books not in this set are removed.

    Returns:
        Number of books pruned.
    """
    existing_books = db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []
    pruned = 0
    for book in existing_books:
        provider = str(book.get("provider") or "").strip()
        provider_book_id = str(book.get("provider_book_id") or "").strip()
        if not provider or not provider_book_id:
            continue
        key = f"{provider}:{provider_book_id}"
        if key not in current_provider_ids:
            try:
                db.delete_monitored_book(
                    user_id=user_id,
                    entity_id=entity_id,
                    provider=provider,
                    provider_book_id=provider_book_id,
                )
                pruned += 1
            except Exception as exc:
                logger.warning(
                    "Failed to prune book entity_id=%s provider=%s book_id=%s: %s",
                    entity_id, provider, provider_book_id, exc,
                )
    return pruned


# =============================================================================
# Language filters
# =============================================================================


def apply_language_filters(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    preferred_languages: set[str] | None,
) -> None:
    """Update hidden flags for all books of an entity based on language preferences."""
    try:
        db.update_monitored_books_hidden_flags(
            user_id=user_id,
            entity_id=entity_id,
            preferred_languages=preferred_languages,
        )
    except Exception as exc:
        logger.debug("Failed updating monitored hidden flags entity_id=%s: %s", entity_id, exc)


# =============================================================================
# Release fetching
# =============================================================================


def fetch_book_releases(
    book: Any,
    *,
    content_type: str,
) -> list[dict[str, Any]]:
    """Search all configured sources for releases of a single book.

    Returns scored, ranked release dicts ready for process_monitored_book().
    Returns an empty list if no sources are available or no releases found.
    """
    from shelfmark.core.monitored_release_scoring import rank_releases_for_book
    from shelfmark.core.search_plan import build_release_search_plan
    from shelfmark.release_sources import get_source, list_available_sources

    search_plan = build_release_search_plan(book, languages=None, manual_query=None, indexers=None)
    all_releases: list[Any] = []
    for source_row in list_available_sources():
        source_name = str(source_row.get("name") or "").strip()
        if not source_name or not bool(source_row.get("enabled")):
            continue
        try:
            source = get_source(source_name)
            releases = source.search(book, search_plan, expand_search=False, content_type=content_type)
            all_releases.extend(releases)
        except Exception:
            continue

    if not all_releases:
        return []

    scored = rank_releases_for_book(book, all_releases)
    release_dicts = []
    for release, _ in scored:
        release_dict = asdict(release)
        release_dict["content_type"] = content_type
        release_dicts.append(release_dict)
    return release_dicts
