"""Enrich monitored books with release dates from Google Books API.

Queries Google Books for books that have no release_date, picking the best
title+author match and storing the full YYYY-MM-DD date.  Works without an
API key (~100 req/day) but supports an optional key for higher quota.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db import MonitoredDB

logger = setup_logger(__name__)

_RECHECK_DAYS = 7
_TITLE_MATCH_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Google Books search (shared with monitored_routes)
# ---------------------------------------------------------------------------

def search_google_books(title: str, author: str, *, api_key: str = "") -> list[dict[str, Any]]:
    """Search Google Books API.  Returns normalised result dicts."""
    query_parts: list[str] = []
    if title:
        query_parts.append(f"intitle:{title}")
    if author:
        query_parts.append(f"inauthor:{author}")
    if not query_parts:
        return []

    try:
        import requests as http_requests
        from shelfmark.download.network import get_ssl_verify

        params: dict[str, str] = {
            "q": "+".join(query_parts),
            "maxResults": "10",
            "printType": "books",
        }
        if api_key:
            params["key"] = api_key

        resp = http_requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=15,
            verify=get_ssl_verify("https://www.googleapis.com"),
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or []
    except Exception as exc:
        logger.warning("Google Books search failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for item in items:
        info = item.get("volumeInfo") or {}
        raw_date = info.get("publishedDate") or ""
        # publishedDate can be "YYYY-MM-DD", "YYYY-MM", or "YYYY"
        release_date = raw_date if len(raw_date) >= 10 else None
        pub_year = None
        if raw_date and len(raw_date) >= 4:
            try:
                pub_year = int(raw_date[:4])
            except (ValueError, TypeError):
                pass
        cover = (info.get("imageLinks") or {}).get("thumbnail")
        results.append({
            "asin": "",
            "title": info.get("title") or "",
            "authors": info.get("authors") or [],
            "release_date": release_date,
            "publish_year": pub_year,
            "cover_url": cover,
            "series_name": None,
            "source": "google",
        })
    return results


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, strip subtitles and non-alphanum."""
    text = text.lower().split(":")[0].split("(")[0]
    return re.sub(r"[^a-z0-9 ]", "", text).strip()


def _best_match(
    book_title: str,
    book_author: str,
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the best Google Books result matching our book."""
    norm_title = _normalise(book_title)
    if not norm_title:
        return None

    best: dict[str, Any] | None = None
    best_score = 0.0

    for r in results:
        if not r.get("release_date"):
            continue
        r_title = _normalise(r.get("title") or "")
        if not r_title:
            continue

        title_score = SequenceMatcher(None, norm_title, r_title).ratio()
        if title_score < _TITLE_MATCH_THRESHOLD:
            continue

        # Boost if author overlaps
        r_authors = " ".join(r.get("authors") or []).lower()
        author_parts = book_author.lower().split()
        author_bonus = 0.1 if any(p in r_authors for p in author_parts if len(p) > 2) else 0.0

        score = title_score + author_bonus
        if score > best_score:
            best_score = score
            best = r

    return best


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------

def enrich_release_dates(
    db: MonitoredDB,
    *,
    entity_id: int,
    user_id: int | None,
    books: list[dict[str, Any]],
    max_lookups: int = 20,
) -> int:
    """Enrich books that have no release date using Google Books.

    Called during scheduled sync.  Returns the number of books enriched.
    Caps lookups at *max_lookups* per call to stay within API quotas.
    """
    api_key = str(app_config.get("GOOGLEBOOKS_API_KEY", "") or "")
    now = datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []

    for book in books:
        rd = (book.get("release_date") or "").strip()
        if rd:
            continue  # Already has a date

        checked_at = book.get("release_date_checked_at")
        if checked_at:
            try:
                last = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
                if (now - last).days < _RECHECK_DAYS:
                    continue
            except (ValueError, TypeError):
                pass

        title = (book.get("title") or "").strip()
        if not title:
            continue

        candidates.append(book)
        if len(candidates) >= max_lookups:
            break

    if not candidates:
        return 0

    enriched = 0
    for book in candidates:
        title = (book.get("title") or "").strip()
        author = (book.get("authors") or "").strip()
        provider = (book.get("provider") or "").strip()
        provider_book_id = (book.get("provider_book_id") or "").strip()

        if not provider or not provider_book_id:
            continue

        results = search_google_books(title, author, api_key=api_key)
        match = _best_match(title, author, results)

        if match and match.get("release_date"):
            ok = db.update_book_release_date(
                user_id=user_id,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                release_date=match["release_date"],
            )
            if ok:
                enriched += 1
                logger.info(
                    "Enriched release date for '%s': %s (via Google Books)",
                    title, match["release_date"],
                )
        else:
            # No match — mark as checked so we don't retry for _RECHECK_DAYS
            db.mark_release_date_checked(
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
            )

    if enriched:
        logger.info("Enriched %d release date(s) for entity %d", enriched, entity_id)

    return enriched
