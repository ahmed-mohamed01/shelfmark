"""Monitored-feature-specific extensions to HardcoverProvider.

Subclasses HardcoverProvider to add queries not in the upstream provider.
Uses _execute_query() which is accessible to subclasses by design.

The monitored sync path requires *strict* error handling — API failures must
raise typed exceptions instead of silently returning None/[], which upstream's
_execute_query() does.  _execute_query_strict() fills this role.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import requests as _requests

from shelfmark.core.monitored_types import (
    MonitoredProviderAPIError,
    MonitoredProviderAuthError,
    MonitoredProviderNetworkError,
    MonitoredProviderRateLimitError,
    MonitoredProviderTimeoutError,
)
from shelfmark.metadata_providers.hardcover import HARDCOVER_API_URL, HardcoverProvider

logger = logging.getLogger(__name__)

_DEFAULT_LANG_CODES = ["en"]


class MonitoredHardcoverProvider(HardcoverProvider):
    """HardcoverProvider extended with monitored author book queries."""

    # ------------------------------------------------------------------
    # Strict query executor — raises typed exceptions on failure
    # ------------------------------------------------------------------

    def _execute_query_strict(self, query: str, variables: Dict[str, Any]) -> Dict:
        """Execute a GraphQL query, raising typed exceptions on any failure.

        Unlike the base ``_execute_query()`` (which catches everything and
        returns ``None``), this method propagates failures as structured
        exceptions so callers can distinguish network errors from auth errors
        from rate limits, etc.
        """
        from shelfmark.download.network import get_ssl_verify

        try:
            response = self.session.post(
                HARDCOVER_API_URL,
                json={"query": query, "variables": variables},
                timeout=15,
                verify=get_ssl_verify(HARDCOVER_API_URL),
            )
        except _requests.Timeout as exc:
            raise MonitoredProviderTimeoutError(
                f"Hardcover API request timed out: {exc}"
            ) from exc
        except _requests.ConnectionError as exc:
            raise MonitoredProviderNetworkError(
                f"Cannot reach Hardcover API: {exc}"
            ) from exc
        except Exception as exc:
            raise MonitoredProviderNetworkError(
                f"Hardcover API request failed: {exc}"
            ) from exc

        # HTTP-level errors
        try:
            response.raise_for_status()
        except _requests.HTTPError as exc:
            status = response.status_code
            if status in (401, 403):
                raise MonitoredProviderAuthError(
                    f"Hardcover API auth error (HTTP {status})"
                ) from exc
            if status == 429:
                raise MonitoredProviderRateLimitError(
                    "Hardcover API rate limited (HTTP 429)"
                ) from exc
            raise MonitoredProviderAPIError(
                f"Hardcover API HTTP error {status}: {exc}"
            ) from exc

        # Parse JSON
        try:
            data = response.json()
        except Exception as exc:
            raise MonitoredProviderAPIError(
                f"Hardcover API returned non-JSON response: {exc}"
            ) from exc

        # GraphQL-level errors
        if "errors" in data:
            msgs = "; ".join(
                str(e.get("message", e)) for e in data["errors"]
            )
            raise MonitoredProviderAPIError(f"Hardcover GraphQL errors: {msgs}")

        return data.get("data") or {}

    def get_author_books_paginated(
        self,
        author_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        lang_codes: list[str] | None = None,
    ) -> list[dict]:
        """Fetch books for an author via the direct books GraphQL query.

        Filters compilations and books with <=4 users at API level.
        Post-processing filters in the sync pipeline further narrow results
        via hybrid threshold, title patterns, language heuristic, and
        contributor count.

        Returns full data per book:
        - All series memberships with positions
        - Preferred-language ISBNs (preferred_isbns) and ASINs (preferred_asins)
        - Language detection via lang_editions (distinct per language_id)
        - Pages, tags, rating, readers/users count, cover, release date
        - Contributor count via contributions_aggregate
        """
        codes = lang_codes or _DEFAULT_LANG_CODES
        query = """
        query GetAuthorBooks($authorId: Int!, $limit: Int!, $offset: Int!, $langCodes: [String!]!) {
            books(
                where: {
                    contributions: { author: { id: { _eq: $authorId } } }
                    compilation: { _eq: false }
                    users_count: { _gt: 4 }
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
                users_count
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
                contributions_aggregate { aggregate { count } }
            }
        }
        """
        result = self._execute_query_strict(
            query,
            {"authorId": int(author_id), "limit": limit, "offset": offset, "langCodes": codes},
        )
        return result.get("books") or []

    def browse_author_books(
        self,
        author_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch all books for an author without download-availability filters.

        Used for the unmonitored author browse view. Returns all books regardless
        of whether they have ISBNs or ASINs — just filters compilations and
        requires at least 1 user to reduce noise.
        """
        query = """
        query BrowseAuthorBooks($authorId: Int!, $limit: Int!, $offset: Int!) {
            books(
                where: {
                    contributions: { author: { id: { _eq: $authorId } } }
                    compilation: { _eq: false }
                    users_count: { _gt: 0 }
                }
                limit: $limit
                offset: $offset
                order_by: { release_date: asc }
            ) {
                id
                title
                description
                rating
                users_read_count
                release_date
                image { url }
                book_series {
                    position
                    series { name primary_books_count }
                }
                featured_book_series {
                    position
                    series { name primary_books_count }
                }
                default_physical_edition {
                    isbn_13
                    isbn_10
                    release_date
                }
            }
        }
        """
        result = self._execute_query(
            query,
            {"authorId": int(author_id), "limit": limit, "offset": offset},
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
        result = self._execute_query_strict(query, {"bookId": int(book_id), "langCodes": codes})
        books = result.get("books") or []
        book = books[0] if books else None

        if book is not None and cache_enabled:
            ttl = int(_app_config.get("METADATA_CACHE_BOOK_TTL", 600) or 600)
            cache.set(cache_key, book, ttl=ttl)

        return book
