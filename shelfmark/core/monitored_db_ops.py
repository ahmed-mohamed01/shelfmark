"""Data operations layer for the monitored feature.

Combines provider API calls with DB persistence. Each function is a complete,
repeatable unit of work that can be called from orchestration or route layers.

Import graph: monitored_db_ops → monitored_utils (no other monitored_* imports)

Key public functions:
  fetch_entity_metadata  — fetch books from provider and upsert; dispatches on entity.kind
  prune_deleted_books    — remove books no longer present at the provider
  fetch_book_releases    — search all configured sources for releases of a single book
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.monitored_utils import extract_book_popularity
from shelfmark.metadata_providers import normalize_language_code

logger = setup_logger(__name__)


# =============================================================================
# Provider factory
# =============================================================================


def get_monitored_provider(provider_name: str):
    """Return a provider instance, using MonitoredHardcoverProvider for 'hardcover'."""
    from shelfmark.metadata_providers import get_provider_kwargs

    if str(provider_name or "").strip().lower() == "hardcover":
        from shelfmark.core.monitored_hardcover_ext import MonitoredHardcoverProvider
        return MonitoredHardcoverProvider(**get_provider_kwargs("hardcover"))

    from shelfmark.metadata_providers import get_provider
    return get_provider(provider_name, **get_provider_kwargs(provider_name))


# =============================================================================
# Book field parsing
# =============================================================================


def _parse_book_fields(book: dict, *, lang_codes: list[str]) -> dict:
    """Parse a raw GraphQL book dict into flat DB-ready fields.

    Returns a dict whose keys match upsert_monitored_book keyword params
    exactly, so callers can **-unpack it directly.
    Returns an empty dict if book is falsy.
    """
    if not book:
        return {}

    # Featured (primary) series
    featured = book.get("featured_book_series") or {}
    featured_series = featured.get("series") or {}
    series_name = featured_series.get("name")
    series_position = featured.get("position")
    series_count = featured_series.get("primary_books_count")

    # All series memberships as JSON
    all_series: list | None = [
        {
            "name": (s.get("series") or {}).get("name"),
            "position": s.get("position"),
            "count": (s.get("series") or {}).get("primary_books_count"),
        }
        for s in (book.get("book_series") or [])
        if (s.get("series") or {}).get("name")
    ] or None

    # Physical edition fields (canonical print identifier)
    phys = book.get("default_physical_edition") or {}
    isbn_13 = phys.get("isbn_13") or None
    isbn_10 = phys.get("isbn_10") or None
    pages = phys.get("pages") or None
    # Physical edition release_date takes priority over book-level date
    release_date = phys.get("release_date") or book.get("release_date") or None
    publish_year = int(release_date[:4]) if release_date else None

    # Preferred-language ISBNs and ASINs (aliased edition subfields)
    isbns: list | None = [
        {"isbn_13": e["isbn_13"]} for e in (book.get("preferred_isbns") or []) if e.get("isbn_13")
    ] or None
    asins: list | None = [
        {"asin": e["asin"]} for e in (book.get("preferred_asins") or []) if e.get("asin")
    ] or None

    # Language: first lang_editions entry ordered by language_id asc (English first if present)
    language = None
    for ed in (book.get("lang_editions") or []):
        lang_code = (ed.get("language") or {}).get("code2")
        if lang_code:
            language = normalize_language_code(lang_code)
            break

    return {
        "title": book.get("title") or "",
        "release_date": release_date,
        "publish_year": publish_year,
        "description": book.get("description") or None,
        "cover_url": (book.get("image") or {}).get("url"),
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "isbns": isbns,
        "asins": asins,
        "pages": pages,
        "cached_tags": book.get("cached_tags"),
        "language": language,
        "series_name": series_name,
        "series_position": series_position,
        "series_count": series_count,
        "all_series": all_series,
        "rating": book.get("rating"),
        "readers_count": book.get("users_read_count"),
    }


# =============================================================================
# Entity metadata
# =============================================================================


def fetch_entity_metadata(
    db: MonitoredDB,
    *,
    entity: dict[str, Any],
    user_id: int | None,
    provider: Any = None,
    preferred_languages: "set[str] | None" = None,
) -> set[str]:
    """Fetch metadata for a monitored entity and upsert to DB.

    For 'author' entities: queries the provider for all books via
    get_author_books_paginated(), paginates up to 2,000 results (100/page),
    and upserts each page immediately. Raises MonitoredProviderError if the
    provider is unavailable. Returns a set of 'provider:book_id' strings for
    use with prune_deleted_books().

    For 'book' entities: fetches a single book via get_book_rich() (Hardcover)
    or get_book() (generic providers), seeding from entity.settings defaults
    if the provider is unavailable. Sets monitor flags post-upsert.
    Returns a single-element set.

    Raises:
        ValueError: If entity.kind is not 'author' or 'book'.
        MonitoredProviderError: For author entities, if provider is unavailable.
    """
    from shelfmark.core.monitored_types import MonitoredProviderError

    kind = str(entity.get("kind") or "")
    if kind not in ("author", "book"):
        raise ValueError(f"fetch_entity_metadata: unsupported entity kind '{kind}'")

    entity_id = int(entity["id"])
    provider_name = str(entity.get("provider") or "hardcover")
    entity_provider_id = str(entity.get("provider_id") or "")
    lang_codes: list[str] = sorted(preferred_languages) if preferred_languages else ["en"]

    if provider is None:
        provider = get_monitored_provider(provider_name)

    # -------------------------------------------------------------------------
    # Author path: paginated books query (WHERE contributions.author_id = ?)
    # -------------------------------------------------------------------------
    if kind == "author":
        if not provider.is_available():
            raise MonitoredProviderError(f"Metadata provider '{provider_name}' is not available")

        # Collect all pages first so we can filter split editions
        all_books: list[dict] = []
        offset = 0
        limit = 100
        max_books = 2000

        while offset < max_books:
            page_books = provider.get_author_books_paginated(
                entity_provider_id, offset=offset, limit=limit,
                lang_codes=lang_codes if hasattr(provider, "get_book_rich") else None,
            )
            if not page_books:
                break
            all_books.extend(page_books)
            offset += limit
            if len(page_books) < limit:
                break  # last page

        # Filter non-canonical split editions (e.g. "Part 1", "Part 2")
        from shelfmark.core.monitored_book_filter import filter_split_books

        canonical_books, filtered_books = filter_split_books(all_books)
        if filtered_books:
            logger.debug(
                "entity_id=%s: filtered %d split books out of %d total",
                entity_id, len(filtered_books), len(all_books),
            )

        discovered_ids: set[str] = set()
        for book in canonical_books:
            book_id = str(book["id"])
            discovered_ids.add(f"{provider_name}:{book_id}")

            fields = _parse_book_fields(book, lang_codes=lang_codes)

            # Skip books in non-preferred languages (filter at upsert time, not post-hoc)
            if preferred_languages and fields.get("language") and fields["language"] not in preferred_languages:
                continue

            db.upsert_monitored_book(
                user_id=user_id,
                entity_id=entity_id,
                provider=provider_name,
                provider_book_id=book_id,
                authors=str(entity.get("name") or "").strip() or None,
                ratings_count=book.get("reviews_count"),
                state="discovered",
                **fields,
            )

        return discovered_ids

    # -------------------------------------------------------------------------
    # Book path: single fetch with seed defaults (WHERE books.id = ?)
    # -------------------------------------------------------------------------
    settings = entity.get("settings") or {}
    seed_name = str(entity.get("name") or "")
    seeded_title = seed_name
    seeded_authors: str | None = str(settings.get("book_author") or "").strip() or None
    seeded_cover: str | None = str(settings.get("photo_url") or "").strip() or None
    seeded_year: Any = None
    seeded_release_date: str | None = None
    seeded_description: str | None = None
    seeded_isbn13: str | None = None
    seeded_isbn10: str | None = None
    seeded_isbns: list | None = None
    seeded_asins: list | None = None
    seeded_pages: int | None = None
    seeded_cached_tags: Any = None
    seeded_series_name: str | None = None
    seeded_series_position: float | None = None
    seeded_series_count: int | None = None
    seeded_all_series: list | None = None
    seeded_language: str | None = None
    seeded_rating: float | None = None
    seeded_ratings_count: int | None = None
    seeded_readers_count: int | None = None

    try:
        if provider.is_available():
            if hasattr(provider, "get_book_rich"):
                # Hardcover path: rich data with all series, identifiers, etc.
                book = provider.get_book_rich(entity_provider_id, lang_codes=lang_codes)
                if book is not None:
                    fields = _parse_book_fields(book, lang_codes=lang_codes)
                    seeded_title = str(fields.get("title") or seeded_title or "").strip() or seeded_title
                    seeded_description = fields.get("description")
                    seeded_isbn13 = fields.get("isbn_13")
                    seeded_isbn10 = fields.get("isbn_10")
                    seeded_pages = fields.get("pages")
                    seeded_release_date = fields.get("release_date")
                    seeded_year = fields.get("publish_year")
                    seeded_cover = fields.get("cover_url") or seeded_cover
                    seeded_rating = fields.get("rating")
                    seeded_readers_count = fields.get("readers_count")
                    seeded_cached_tags = fields.get("cached_tags")
                    seeded_isbns = fields.get("isbns")
                    seeded_asins = fields.get("asins")
                    seeded_language = fields.get("language")
                    seeded_series_name = fields.get("series_name")
                    seeded_series_position = fields.get("series_position")
                    seeded_series_count = fields.get("series_count")
                    seeded_all_series = fields.get("all_series")
            else:
                # Generic provider path: use get_book() returning BookMetadata dataclass
                bm = provider.get_book(entity_provider_id)
                if bm is not None:
                    payload = asdict(bm)
                    seeded_title = str(payload.get("title") or seeded_title or "").strip() or seeded_title
                    authors_list = payload.get("authors")
                    if isinstance(authors_list, list):
                        seeded_authors = (
                            ", ".join(str(a).strip() for a in authors_list if str(a).strip())
                            or seeded_authors
                        )
                    seeded_year = payload.get("publish_year")
                    seeded_release_date = payload.get("release_date")
                    seeded_isbn13 = payload.get("isbn_13")
                    seeded_isbn10 = payload.get("isbn_10")
                    seeded_cover = payload.get("cover_url") or seeded_cover
                    seeded_series_name = payload.get("series_name")
                    seeded_series_position = payload.get("series_position")
                    seeded_series_count = payload.get("series_count")
                    seeded_language = normalize_language_code(payload.get("language"))
                    seeded_rating, seeded_ratings_count, seeded_readers_count = extract_book_popularity(
                        payload.get("display_fields")
                    )
    except Exception as exc:
        logger.warning(
            "Book monitor metadata seed failed provider=%s provider_id=%s: %s",
            provider_name, entity_provider_id, exc,
        )

    try:
        db.upsert_monitored_book(
            user_id=user_id,
            entity_id=entity_id,
            provider=provider_name,
            provider_book_id=entity_provider_id,
            title=seeded_title or seed_name,
            authors=seeded_authors,
            publish_year=seeded_year,
            release_date=seeded_release_date,
            description=seeded_description,
            isbn_13=seeded_isbn13,
            isbn_10=seeded_isbn10,
            isbns=seeded_isbns,
            asins=seeded_asins,
            pages=seeded_pages,
            cached_tags=seeded_cached_tags,
            cover_url=seeded_cover,
            series_name=seeded_series_name,
            series_position=seeded_series_position,
            series_count=seeded_series_count,
            all_series=seeded_all_series,
            language=seeded_language,
            rating=seeded_rating,
            ratings_count=seeded_ratings_count,
            readers_count=seeded_readers_count,
            state="discovered",
        )
        monitor_ebook = bool(settings.get("monitor_ebook", True))
        monitor_audiobook = bool(settings.get("monitor_audiobook", True))
        db.set_monitored_book_monitor_flags(
            user_id=user_id,
            entity_id=entity_id,
            provider=provider_name,
            provider_book_id=entity_provider_id,
            monitor_ebook=monitor_ebook,
            monitor_audiobook=monitor_audiobook,
        )
    except Exception as exc:
        logger.warning(
            "Book monitor seed upsert failed entity_id=%s provider=%s provider_id=%s: %s",
            entity_id, provider_name, entity_provider_id, exc,
        )

    return {f"{provider_name}:{entity_provider_id}"}


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
