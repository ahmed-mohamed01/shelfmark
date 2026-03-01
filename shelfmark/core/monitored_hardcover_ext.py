"""Monitored-feature-specific extensions to HardcoverProvider.

Subclasses HardcoverProvider to add queries not in the upstream provider.
Uses _execute_query() which is accessible to subclasses by design.
"""
from __future__ import annotations

from shelfmark.metadata_providers.hardcover import HardcoverProvider

_DEFAULT_LANG_CODES = ["en"]


class MonitoredHardcoverProvider(HardcoverProvider):
    """HardcoverProvider extended with monitored author book queries."""

    def get_author_books_paginated(
        self,
        author_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        lang_codes: list[str] | None = None,
    ) -> list[dict]:
        """Fetch books for an author via the direct books GraphQL query.

        Filters compilations at API level. Returns full data per book:
        - All series memberships with positions
        - Preferred-language ISBNs (preferred_isbns) and ASINs (preferred_asins)
        - Language detection via lang_editions (distinct per language_id)
        - Pages, tags, rating, readers count, cover, release date
        """
        codes = lang_codes or _DEFAULT_LANG_CODES
        query = """
        query GetAuthorBooks($authorId: Int!, $limit: Int!, $offset: Int!, $langCodes: [String!]!) {
            books(
                where: {
                    contributions: { author: { id: { _eq: $authorId } } }
                    compilation: { _eq: false }
                    users_count: { _gt: 10 }
                    _or: [
                        { editions: { asin: { _is_null: false }, language_id: { _eq: 1 } } }
                        { default_physical_edition: { _or: [
                            { isbn_13: { _is_null: false } }
                            { isbn_10: { _is_null: false } }
                        ] } }
                        { users_read_count: { _eq: 0 } }
                    ]
                }
                limit: $limit
                offset: $offset
                order_by: { release_date: asc }
            ) {
                id
                title
                description
                rating
                reviews_count
                users_read_count
                release_date
                cached_tags
                image { url }
                book_series {
                    position
                    series { name  primary_books_count }
                }
                featured_book_series {
                    position
                    series { name  primary_books_count }
                }
                default_physical_edition {
                    pages
                    isbn_13
                    isbn_10
                    release_date
                }
                preferred_isbns: editions(
                    where: { language: { code2: { _in: $langCodes } }, isbn_13: { _is_null: false } }
                ) {
                    isbn_13
                }
                preferred_asins: editions(
                    where: { language: { code2: { _in: $langCodes } }, asin: { _is_null: false } }
                ) {
                    asin
                }
                lang_editions: editions(
                    distinct_on: language_id
                    order_by: [{ language_id: asc }, { users_count: desc }]
                    limit: 5
                ) {
                    language { code2 }
                }
            }
        }
        """
        result = self._execute_query(
            query,
            {"authorId": int(author_id), "limit": limit, "offset": offset, "langCodes": codes},
        )
        return (result or {}).get("books") or []

    def get_book_rich(
        self, book_id: str, *, lang_codes: list[str] | None = None
    ) -> dict | None:
        """Fetch a single book with the same rich fields as get_author_books_paginated.

        Used for directly-monitored books (kind='book' entities).
        Caches results using the metadata CacheService.
        """
        from shelfmark.core.cache import get_metadata_cache
        from shelfmark.core.config import config as _app_config

        codes = lang_codes or _DEFAULT_LANG_CODES
        cache_enabled = bool(_app_config.get("METADATA_CACHE_ENABLED", True))
        cache = get_metadata_cache()
        cache_key = f"hardcover:book_rich:{book_id}:{'_'.join(sorted(codes))}"

        if cache_enabled:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        query = """
        query GetBookRich($bookId: Int!, $langCodes: [String!]!) {
            books(where: { id: { _eq: $bookId } }, limit: 1) {
                id
                title
                description
                rating
                reviews_count
                users_read_count
                release_date
                cached_tags
                image { url }
                book_series { position  series { name  primary_books_count } }
                featured_book_series { position  series { name  primary_books_count } }
                default_physical_edition { pages  isbn_13  isbn_10  release_date }
                preferred_isbns: editions(
                    where: { language: { code2: { _in: $langCodes } }, isbn_13: { _is_null: false } }
                ) {
                    isbn_13
                }
                preferred_asins: editions(
                    where: { language: { code2: { _in: $langCodes } }, asin: { _is_null: false } }
                ) {
                    asin
                }
                lang_editions: editions(
                    distinct_on: language_id
                    order_by: [{ language_id: asc }, { users_count: desc }]
                    limit: 5
                ) {
                    language { code2 }
                }
            }
        }
        """
        result = self._execute_query(query, {"bookId": int(book_id), "langCodes": codes})
        books = (result or {}).get("books") or []
        book = books[0] if books else None

        if book is not None and cache_enabled:
            ttl = int(_app_config.get("METADATA_CACHE_BOOK_TTL", 600) or 600)
            cache.set(cache_key, book, ttl=ttl)

        return book
