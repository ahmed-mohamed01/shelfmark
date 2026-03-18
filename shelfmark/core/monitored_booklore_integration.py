"""Booklore integration for monitored book availability.

Fetches ebooks from a Booklore library, matches them to monitored books using a
3-phase algorithm (ISBN → series+position+title → fuzzy title), and records
matches in monitored_book_files with source='booklore'.

Called automatically at the end of the existing filesystem scan route — no
separate frontend button or API route is needed.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any
from urllib.request import Request, urlopen

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_integration_matching import (
    AUTHOR_SPLIT_RE as _AUTHOR_SPLIT_RE,
    COLON_SUBTITLE_RE as _COLON_SUBTITLE_RE,
    PAREN_SUFFIX_RE as _PAREN_SUFFIX_RE,
    author_matches as _author_matches,
    get_integration_thresholds as _get_integration_thresholds,
    norm as _norm,
    normalize_shelfmark_title as _normalize_shelfmark_title,
    parse_series_position as _parse_series_position,
)

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_booklore_config() -> dict[str, str] | None:
    """Return Booklore connection config or None if not configured/enabled."""
    if not app_config.get("BOOKLORE_ENABLED", True):
        return None
    url = (app_config.get("BOOKLORE_URL") or "").strip().rstrip("/")
    username = (app_config.get("BOOKLORE_USERNAME") or "").strip()
    password = (app_config.get("BOOKLORE_PASSWORD") or "").strip()
    if not (url and username and password):
        return None
    if not url.lower().startswith(("http://", "https://")):
        logger.warning("Booklore: BOOKLORE_URL must start with http:// or https://; ignoring.")
        return None
    return {"url": url, "username": username, "password": password}


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no requests dependency)
# ---------------------------------------------------------------------------


def _parse_json(raw: bytes, endpoint: str) -> Any:
    """Parse JSON bytes; raise ValueError with a useful preview on decode failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        preview = raw[:200].decode("utf-8", errors="replace")
        raise ValueError(f"Booklore returned non-JSON from {endpoint!r}: {preview!r}")


def _build_ssl_ctx(url: str):
    """Return an ssl.SSLContext that respects the CERTIFICATE_VALIDATION setting."""
    import ssl
    from shelfmark.download.network import get_ssl_verify
    ctx = ssl.create_default_context()
    if not get_ssl_verify(url):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _booklore_post(base_url: str, path: str, body: dict[str, Any], timeout: int = 10) -> Any:
    data = json.dumps(body).encode()
    req = Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout, context=_build_ssl_ctx(base_url)) as resp:  # noqa: S310
        return _parse_json(resp.read(), path)


def _booklore_get(
    base_url: str,
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 10,
) -> Any:
    from urllib.parse import urlencode
    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, timeout=timeout, context=_build_ssl_ctx(base_url)) as resp:  # noqa: S310
        return _parse_json(resp.read(), path)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _booklore_login(url: str, username: str, password: str) -> str:
    """Authenticate with Booklore and return the JWT access token."""
    data = _booklore_post(url, "/api/v1/auth/login", {"username": username, "password": password})
    token = data.get("accessToken") or data.get("token") or ""
    if not token:
        raise ValueError(f"Booklore login returned no access token (keys: {list(data)})")
    return str(token)


# ---------------------------------------------------------------------------
# Author + book lookup
# ---------------------------------------------------------------------------


_MAX_BOOK_PAGES = 200  # safety cap: 200 × 50 = 10 000 books per author


def _find_booklore_author_books(
    url: str,
    token: str,
    author_name: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (ebook_items, pagination_complete) for the best-matching author.

    Steps:
    1. GET /api/v1/app/authors?search={name}  (paginated author list)
    2. Fuzzy-match against author_name
    3. GET /api/v1/app/books?authors={matched_name}&size=50 (paginated)
    4. Filter out AUDIOBOOK primaryFileType

    ``pagination_complete`` is False if pagination was cut short by an
    exception or the page cap, so callers can skip pruning safely.
    """
    # Step 1: search authors
    try:
        data = _booklore_get(
            url, token, "/api/v1/app/authors",
            params={"search": author_name, "size": 50, "page": 0},
            timeout=15,
        )
        authors: list[dict[str, Any]] = data.get("content") or []
    except Exception as exc:
        logger.warning("Booklore: failed to fetch authors for %r: %s", author_name, exc)
        return [], True  # fetch failure → nothing to match, treat as complete

    if not authors:
        logger.warning("Booklore: no authors found matching %r", author_name)
        return [], True  # no authors → nothing to match, treat as complete

    # Step 2: fuzzy-match author names (same split logic as author_matches)
    target_parts = [p.strip() for p in _AUTHOR_SPLIT_RE.split(author_name) if p.strip()] or [author_name]
    best_author: dict[str, Any] | None = None
    best_ratio = 0.0
    for author in authors:
        name = str(author.get("name") or "")
        name_parts = [p.strip() for p in _AUTHOR_SPLIT_RE.split(name) if p.strip()] or [name]
        ratio = max(
            SequenceMatcher(None, _norm(a), _norm(b)).ratio()
            for a in target_parts
            for b in name_parts
        )
        if ratio > best_ratio:
            best_ratio, best_author = ratio, author

    if best_author is None or best_ratio < 0.70:
        logger.warning(
            "Booklore: no author match for %r (best ratio=%.2f, %d authors checked)",
            author_name, best_ratio, len(authors),
        )
        return [], True  # no author → nothing to fetch, treat as complete

    matched_name = str(best_author.get("name") or "")
    logger.info(
        "Booklore: matched author %r → %r (ratio=%.2f)",
        author_name, matched_name, best_ratio,
    )

    # Step 3: fetch all books for this author (paginated)
    all_books: list[dict[str, Any]] = []
    pagination_complete = False
    page = 0
    while True:
        try:
            resp = _booklore_get(
                url, token, "/api/v1/app/books",
                params={"authors": matched_name, "size": 50, "page": page},
                timeout=30,
            )
        except Exception as exc:
            logger.warning(
                "Booklore: failed to fetch books for author %r page %d: %s — skipping prune",
                matched_name, page, exc,
            )
            break  # pagination_complete stays False
        content = resp.get("content") or []
        all_books.extend(content)
        if not resp.get("hasNext"):
            pagination_complete = True
            break
        if page >= _MAX_BOOK_PAGES:
            logger.warning(
                "Booklore: page cap (%d) reached for author %r — skipping prune",
                _MAX_BOOK_PAGES, matched_name,
            )
            break  # pagination_complete stays False
        page += 1

    logger.info(
        "Booklore: fetched %d books for author %r (complete=%s)",
        len(all_books), matched_name, pagination_complete,
    )

    # Step 4: filter — exclude audiobooks
    ebook_items = [
        b for b in all_books
        if (b.get("primaryFileType") or "").upper() != "AUDIOBOOK"
    ]
    filtered = len(all_books) - len(ebook_items)
    if filtered:
        logger.debug("Booklore: filtered out %d audiobook items for %r", filtered, matched_name)

    return ebook_items, pagination_complete


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------


def _normalize_bl_title(title: str, series_names: list[str]) -> str:
    """Strip 'SeriesName: ' prefix and ': subtitle' suffix from a Booklore title."""
    t = title.strip()
    for sn in series_names:
        prefix = sn.rstrip() + ": "
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
            break
    t = _COLON_SUBTITLE_RE.sub("", t).strip()
    return t


# ---------------------------------------------------------------------------
# Core matching
# ---------------------------------------------------------------------------


def _match_booklore_item_to_books(
    bl_item: dict[str, Any],
    books: list[dict[str, Any]],
    entity_name: str = "",
    thresholds: dict[str, float] | None = None,
) -> tuple[dict[str, Any] | None, float, str]:
    """Match a Booklore MobileBookSummary to a monitored book.

    ``entity_name`` is used as a fallback author when book.authors is NULL.
    ``thresholds`` is the result of ``get_integration_thresholds()``; pass once
    per batch to avoid repeated config lookups.

    Returns (book, confidence, reason) or (None, 0.0, '') if no match.
    """
    if thresholds is None:
        thresholds = _get_integration_thresholds()

    bl_title = (bl_item.get("title") or "").strip()
    bl_authors_list: list[str] = [a for a in (bl_item.get("authors") or []) if a]
    bl_author_str = ", ".join(bl_authors_list)
    bl_series = (bl_item.get("seriesName") or "").strip()
    bl_series_num = bl_item.get("seriesNumber")  # float | None

    # ISBN fields — present only in full book detail responses, not list summaries.
    # Included here so Phase 1 fires automatically if enriched items are passed.
    bl_isbn13 = (bl_item.get("isbn13") or "").strip()

    # ------------------------------------------------------------------
    # Phase 1: ISBN exact match
    # ------------------------------------------------------------------
    if bl_isbn13:
        for book in books:
            b_isbn13 = (book.get("isbn13") or "").strip()
            if bl_isbn13 == b_isbn13:
                logger.debug("Booklore match [isbn] %r → %r", bl_title, book.get("title"))
                return book, 1.0, "bl_isbn"

    # ------------------------------------------------------------------
    # Phase 2: Series + position + title confirmation
    # ------------------------------------------------------------------
    series_names = [bl_series] if bl_series else []
    if bl_series and bl_series_num is not None and bl_title:
        try:
            bl_pos = float(bl_series_num)
        except (TypeError, ValueError):
            bl_pos = None

        if bl_pos is not None:
            norm_bl_sn = _norm(bl_series)
            norm_bl_title_n = _norm(_normalize_bl_title(bl_title, series_names))
            norm_bl_raw_n = _norm(bl_title)

            p2_best_book: dict[str, Any] | None = None
            p2_best_t = 0.0

            for book in books:
                b_series = (book.get("series_name") or "").strip()
                b_pos_raw = book.get("series_position")
                if b_pos_raw is None:
                    continue
                b_pos = _parse_series_position(str(b_pos_raw))
                if b_pos is None or abs(b_pos - bl_pos) > 0.01:
                    continue
                norm_b_series = _norm(b_series)  # pre-computed once per book
                if SequenceMatcher(None, norm_bl_sn, norm_b_series).ratio() < thresholds["series_name"]:
                    continue
                raw_shelf = book.get("title") or ""
                norm_shelf_stripped = _norm(_normalize_shelfmark_title(raw_shelf))
                norm_shelf_full = _norm(raw_shelf)
                t_ratio = max(
                    SequenceMatcher(None, norm_bl_title_n, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_bl_title_n, norm_shelf_full).ratio(),
                    SequenceMatcher(None, norm_bl_raw_n, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_bl_raw_n, norm_shelf_full).ratio(),
                    SequenceMatcher(None, norm_bl_sn, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_bl_sn, norm_shelf_full).ratio(),
                )
                if t_ratio >= thresholds["series_title"] and t_ratio > p2_best_t:
                    p2_best_t, p2_best_book = t_ratio, book

            if p2_best_book is not None:
                conf = min(0.92, 0.80 + p2_best_t * 0.12)
                logger.debug(
                    "Booklore match [series_pos] %r → %r (series=%r pos=%.1f t=%.2f conf=%.2f)",
                    bl_title, p2_best_book.get("title"), bl_series, bl_pos, p2_best_t, conf,
                )
                return p2_best_book, conf, "bl_series_pos"

    # ------------------------------------------------------------------
    # Phase 3: Bidirectional-normalised title fuzzy match
    # ------------------------------------------------------------------
    if not bl_title:
        return None, 0.0, ""

    norm_bl_n = _norm(_normalize_bl_title(bl_title, series_names))
    norm_bl_raw_n = _norm(bl_title)
    norm_bl_paren_n = _norm(_PAREN_SUFFIX_RE.sub("", bl_title))
    best_book: dict[str, Any] | None = None
    best_t_ratio = 0.0

    for book in books:
        raw_shelf = book.get("title") or ""
        norm_shelf_stripped = _norm(_normalize_shelfmark_title(raw_shelf))
        norm_shelf_full = _norm(raw_shelf)
        norm_shelf_paren = _norm(_PAREN_SUFFIX_RE.sub("", raw_shelf))
        t_ratio = max(
            SequenceMatcher(None, norm_bl_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_bl_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_bl_raw_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_bl_raw_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_bl_paren_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_bl_paren_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_bl_n, norm_shelf_paren).ratio(),
            SequenceMatcher(None, norm_bl_paren_n, norm_shelf_paren).ratio(),
        )
        if t_ratio < thresholds["title"] or t_ratio <= best_t_ratio:
            continue
        # Author confirmation — fall back to entity_name when book.authors is NULL.
        # Allow title-only match if ratio is strong (≥ 0.88) and no BL author data.
        book_author_str = book.get("authors") or entity_name
        if not bl_author_str:
            if t_ratio >= 0.88:
                best_t_ratio, best_book = t_ratio, book
            else:
                logger.debug(
                    "Booklore title match %r → %r (t=%.2f) skipped: no BL author and t < 0.88",
                    bl_title, book.get("title"), t_ratio,
                )
        elif _author_matches(bl_author_str, book_author_str, threshold=thresholds["author"]):
            best_t_ratio, best_book = t_ratio, book
        else:
            logger.debug(
                "Booklore title match %r → %r (t=%.2f) rejected: author mismatch (bl=%r vs book=%r)",
                bl_title, book.get("title"), t_ratio, bl_author_str, book_author_str,
            )

    if best_book:
        conf = best_t_ratio * 0.85
        logger.debug(
            "Booklore match [fuzzy] %r → %r (t=%.2f conf=%.2f)",
            bl_title, best_book.get("title"), best_t_ratio, conf,
        )
        return best_book, conf, "bl_fuzzy"

    return None, 0.0, ""


# ---------------------------------------------------------------------------
# Public sync function
# ---------------------------------------------------------------------------


def sync_booklore_availability_for_entity(
    *,
    monitored_db: Any,
    entity_id: int,
    entity_name: str,
    user_id: int | None,
) -> dict[str, Any]:
    """Sync Booklore ebook availability for one monitored entity (author).

    Fetches all Booklore ebooks for the author, matches them to monitored books,
    upserts matches with source='booklore', and prunes stale Booklore records.

    Returns a result dict with bl_matched / bl_total / bl_skipped.
    """
    cfg = get_booklore_config()
    if not cfg:
        # Prune stale Booklore records when integration is disabled or not configured
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=[], source="booklore"
        )
        return {"bl_skipped": True, "reason": "not_configured"}

    url = cfg["url"]

    try:
        token = _booklore_login(url, cfg["username"], cfg["password"])
    except Exception as exc:
        logger.warning("Booklore: login failed for entity_id=%s: %s", entity_id, exc)
        return {"bl_skipped": True, "reason": "login_failed"}

    bl_items, bl_complete = _find_booklore_author_books(url, token, entity_name)
    if not bl_items:
        if bl_complete:
            # Author genuinely has no ebooks — prune any stale records.
            monitored_db.prune_monitored_book_files(
                entity_id=entity_id, keep_paths=[], source="booklore"
            )
        return {"bl_matched": 0, "bl_total": 0}

    books = monitored_db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []

    matched = 0
    kept_paths: list[str] = []
    unmatched_titles: list[str] = []
    thresholds = _get_integration_thresholds()

    for item in bl_items:
        bl_id = item.get("id")
        bl_title = (item.get("title") or str(bl_id) or "?").strip()
        if bl_id is None:
            unmatched_titles.append(bl_title)
            continue

        book, conf, reason = _match_booklore_item_to_books(item, books, entity_name=entity_name, thresholds=thresholds)
        if not book:
            unmatched_titles.append(bl_title)
            continue

        # Fetch the real filesystem path from the Booklore web API.
        # The /api/v1/books/ endpoint returns BookFile objects with filePath;
        # the /api/v1/app/books/ endpoint intentionally omits paths.
        # Falls back to a stable synthetic URI if the fetch fails.
        # Cast bl_id to int to prevent path injection (e.g. "../../admin") in the URL.
        try:
            safe_id = int(bl_id)
        except (TypeError, ValueError):
            logger.warning("Booklore: skipping book with non-integer id=%r", bl_id)
            unmatched_titles.append(bl_title)
            continue
        path = f"booklore://{safe_id}"
        file_size: int | None = None
        try:
            detail = _booklore_get(url, token, f"/api/v1/books/{safe_id}", timeout=10)
            primary = detail.get("primaryFile") or {}
            real_path = (primary.get("filePath") or "").strip()
            if not real_path:
                for fobj in (detail.get("alternativeFormats") or []) + (detail.get("supplementaryFiles") or []):
                    fp = (fobj.get("filePath") or "").strip()
                    if fp:
                        real_path = fp
                        break
            if real_path:
                path = real_path
            size_kb = primary.get("fileSizeKb")
            if size_kb is not None:
                file_size = int(float(size_kb)) * 1024
        except Exception as fp_exc:
            logger.debug("Booklore: could not fetch file path for book %s: %s", bl_id, fp_exc)

        file_type_raw = (item.get("primaryFileType") or "ebook").lower()
        try:
            monitored_db.upsert_monitored_book_file(
                user_ids=[user_id],
                entity_id=entity_id,
                provider=book.get("provider"),
                provider_book_id=book.get("provider_book_id"),
                path=path,
                ext=file_type_raw,
                file_type="ebook",
                size_bytes=file_size,
                mtime=None,
                confidence=conf,
                match_reason=reason,
                source="booklore",
            )
            kept_paths.append(path)
            matched += 1
        except Exception as exc:
            logger.warning(
                "Booklore: failed to upsert match for %r (entity=%s book=%s): %s",
                path, entity_id, book.get("provider_book_id"), exc,
            )
            unmatched_titles.append(bl_title)

    # Only prune when pagination was complete — avoids deleting valid records
    # if a timeout or error cut the book list short.
    if bl_complete:
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=kept_paths, source="booklore"
        )
    else:
        logger.warning(
            "Booklore sync entity_id=%s: skipping prune (pagination incomplete)",
            entity_id,
        )

    bl_total = len(bl_items)
    logger.info(
        "Booklore sync entity_id=%s: %d/%d items matched",
        entity_id, matched, bl_total,
    )
    if unmatched_titles:
        logger.warning(
            "Booklore sync entity_id=%s: %d items not matched: %s",
            entity_id,
            len(unmatched_titles),
            ", ".join(repr(t) for t in unmatched_titles[:10]),
        )
    return {"bl_matched": matched, "bl_total": bl_total}
