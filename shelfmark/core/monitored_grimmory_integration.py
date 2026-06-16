"""Grimmory integration for monitored book availability.

Fetches ebooks from a Grimmory library, matches them to monitored books using a
3-phase algorithm (ISBN → series+position+title → fuzzy title), and records
matches in monitored_book_files with source='grimmory'.

Called automatically at the end of the existing filesystem scan route — no
separate frontend button or API route is needed.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    import ssl

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_attribution_v2 import (
    SourceMetadata,
    pick_best_attribution,
)
from shelfmark.core.monitored_integration_matching import (
    AUTHOR_SPLIT_RE as _AUTHOR_SPLIT_RE,
)
from shelfmark.core.monitored_integration_matching import (
    norm as _norm,
)

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_grimmory_config() -> dict[str, str] | None:
    """Return Grimmory connection config or None if not configured/enabled."""
    if not app_config.get("GRIMMORY_ENABLED", True):
        return None
    url = (app_config.get("GRIMMORY_URL") or "").strip().rstrip("/")
    username = (app_config.get("GRIMMORY_USERNAME") or "").strip()
    password = (app_config.get("GRIMMORY_PASSWORD") or "").strip()
    if not (url and username and password):
        return None
    if not url.lower().startswith(("http://", "https://")):
        logger.warning("Grimmory: GRIMMORY_URL must start with http:// or https://; ignoring.")
        return None
    return {"url": url, "username": username, "password": password}


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no requests dependency)
# ---------------------------------------------------------------------------


def _parse_json(raw: bytes, endpoint: str) -> Any:
    """Parse JSON bytes; raise ValueError with a useful preview on decode failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        preview = raw[:200].decode("utf-8", errors="replace")
        raise ValueError(f"Grimmory returned non-JSON from {endpoint!r}: {preview!r}") from err


def _build_ssl_ctx(url: str) -> ssl.SSLContext:
    """Return an ssl.SSLContext that respects the CERTIFICATE_VALIDATION setting."""
    import ssl

    from shelfmark.download.network import get_ssl_verify

    ctx = ssl.create_default_context()
    if not get_ssl_verify(url):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _grimmory_post(base_url: str, path: str, body: dict[str, Any], timeout: int = 10) -> Any:
    data = json.dumps(body).encode()
    req = Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout, context=_build_ssl_ctx(base_url)) as resp:
        return _parse_json(resp.read(), path)


def _grimmory_get(
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
    with urlopen(req, timeout=timeout, context=_build_ssl_ctx(base_url)) as resp:
        return _parse_json(resp.read(), path)


def _prefetch_grimmory_book_details(
    base_url: str,
    token: str,
    book_ids: list[int],
) -> dict[int, Any]:
    """Fetch ``/api/v1/books/{id}`` for each book, sequentially.

    The detail payload carries the real filesystem path (which feeds path-side
    match scoring, so it cannot be deferred to only matched books). Prefetching
    them up front still lets the match loop read from a dict, but the requests
    are issued ONE AT A TIME on purpose: the Grimmory/Booklore server 500s under
    concurrent load, so a thread pool here broke the integration. Failed fetches
    are omitted (callers fall back to the bare ``grimmory://`` path).
    """
    results: dict[int, Any] = {}
    for safe_id in book_ids:
        try:
            results[safe_id] = _grimmory_get(
                base_url, token, f"/api/v1/books/{safe_id}", timeout=10
            )
        except Exception as exc:  # noqa: BLE001 — optional enrichment; omit on failure.
            logger.debug("Grimmory: could not fetch file detail for book %s: %s", safe_id, exc)
    return results


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _grimmory_login(url: str, username: str, password: str) -> str:
    """Authenticate with Grimmory and return the JWT access token."""
    data = _grimmory_post(url, "/api/v1/auth/login", {"username": username, "password": password})
    token = data.get("accessToken") or data.get("token") or ""
    if not token:
        raise ValueError(f"Grimmory login returned no access token (keys: {list(data)})")
    return str(token)


# ---------------------------------------------------------------------------
# Author + book lookup
# ---------------------------------------------------------------------------


_MAX_BOOK_PAGES = 200  # safety cap: 200 × 50 = 10 000 books per author


def _find_grimmory_author_books(
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
        data = _grimmory_get(
            url,
            token,
            "/api/v1/app/authors",
            params={"search": author_name, "size": 50, "page": 0},
            timeout=15,
        )
        authors: list[dict[str, Any]] = data.get("content") or []
    except Exception as exc:  # noqa: BLE001 — Grimmory is external; any fetch failure (HTTP, SSL, JSON, auth) should degrade gracefully rather than crash the scan.
        logger.warning("Grimmory: failed to fetch authors for %r: %s", author_name, exc)
        return [], True  # fetch failure → nothing to match, treat as complete

    if not authors:
        logger.warning("Grimmory: no authors found matching %r", author_name)
        return [], True  # no authors → nothing to match, treat as complete

    # Step 2: fuzzy-match author names. Split on commas/semicolons so co-author
    # lists don't drop the per-pair ratio below threshold.
    target_parts = [p.strip() for p in _AUTHOR_SPLIT_RE.split(author_name) if p.strip()] or [
        author_name
    ]
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
            "Grimmory: no author match for %r (best ratio=%.2f, %d authors checked)",
            author_name,
            best_ratio,
            len(authors),
        )
        return [], True  # no author → nothing to fetch, treat as complete

    matched_name = str(best_author.get("name") or "")
    logger.info(
        "Grimmory: matched author %r → %r (ratio=%.2f)",
        author_name,
        matched_name,
        best_ratio,
    )

    # Step 3: fetch all books for this author (paginated)
    all_books: list[dict[str, Any]] = []
    pagination_complete = False
    page = 0
    while True:
        try:
            resp = _grimmory_get(
                url,
                token,
                "/api/v1/app/books",
                params={"authors": matched_name, "size": 50, "page": page},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 — external API; bail out of pagination on any failure rather than crash the scan.
            logger.warning(
                "Grimmory: failed to fetch books for author %r page %d: %s — skipping prune",
                matched_name,
                page,
                exc,
            )
            break  # pagination_complete stays False
        content = resp.get("content") or []
        all_books.extend(content)
        if not resp.get("hasNext"):
            pagination_complete = True
            break
        if page >= _MAX_BOOK_PAGES:
            logger.warning(
                "Grimmory: page cap (%d) reached for author %r — skipping prune",
                _MAX_BOOK_PAGES,
                matched_name,
            )
            break  # pagination_complete stays False
        page += 1

    logger.info(
        "Grimmory: fetched %d books for author %r (complete=%s)",
        len(all_books),
        matched_name,
        pagination_complete,
    )

    # Step 4: filter — exclude audiobooks
    ebook_items = [b for b in all_books if (b.get("primaryFileType") or "").upper() != "AUDIOBOOK"]
    filtered = len(all_books) - len(ebook_items)
    if filtered:
        logger.debug("Grimmory: filtered out %d audiobook items for %r", filtered, matched_name)

    return ebook_items, pagination_complete


# ---------------------------------------------------------------------------
# Adapter: Grimmory item → unified SourceMetadata
# ---------------------------------------------------------------------------


def _grimmory_item_to_source_metadata(item: dict[str, Any]) -> SourceMetadata:
    """Translate a Grimmory book item into the unified ``SourceMetadata`` shape."""
    authors_list = [a for a in (item.get("authors") or []) if a]
    return SourceMetadata(
        title=(item.get("title") or "").strip() or None,
        author=", ".join(authors_list) if authors_list else None,
        series_name=(item.get("seriesName") or "").strip() or None,
        series_position=item.get("seriesNumber"),
        isbn_13=(item.get("isbn13") or "").strip() or None,
        source_label="grimmory",
    )


# ---------------------------------------------------------------------------
# Public sync function
# ---------------------------------------------------------------------------


def sync_grimmory_availability_for_entity(
    *,
    monitored_db: Any,
    entity_id: int,
    entity_name: str,
    user_id: int | None,
) -> dict[str, Any]:
    """Sync Grimmory ebook availability for one monitored entity (author).

    Fetches all Grimmory ebooks for the author, matches them to monitored books,
    upserts matches with source='grimmory', and prunes stale Grimmory records.

    Returns a result dict with gm_matched / gm_total / gm_skipped.
    """
    cfg = get_grimmory_config()
    if not cfg:
        # Prune stale Grimmory records when integration is disabled or not configured
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=[], source="grimmory"
        )
        return {"gm_skipped": True, "reason": "not_configured"}

    url = cfg["url"]

    try:
        token = _grimmory_login(url, cfg["username"], cfg["password"])
    except Exception as exc:  # noqa: BLE001 — any auth or network failure degrades to skipping Grimmory for this entity; no need to crash the scan.
        logger.warning("Grimmory: login failed for entity_id=%s: %s", entity_id, exc)
        return {"gm_skipped": True, "reason": "login_failed"}

    gm_items, gm_complete = _find_grimmory_author_books(url, token, entity_name)
    if not gm_items:
        if gm_complete:
            # Author genuinely has no ebooks — prune any stale records.
            monitored_db.prune_monitored_book_files(
                entity_id=entity_id, keep_paths=[], source="grimmory"
            )
        return {"gm_matched": 0, "gm_total": 0}

    books = monitored_db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []

    # User-rejected (path, book) pairs — sync must not re-attribute these.
    rejections = monitored_db.list_file_rejections_for_entity(entity_id=entity_id)

    matched = 0
    kept_paths: list[str] = []
    unmatched_titles: list[str] = []

    # Concurrently prefetch the per-book file detail (real path + size). This is
    # the dominant cost of a Grimmory sync — one HTTP round-trip per book — so
    # fanning it out across a small pool turns O(books) serial latency into
    # O(books / workers). The path feeds match scoring, so it must be available
    # before pick_best_attribution (can't be deferred to matched books only).
    detail_ids: list[int] = []
    for _item in gm_items:
        try:
            detail_ids.append(int(_item.get("id")))
        except TypeError, ValueError:
            continue
    details_by_id = _prefetch_grimmory_book_details(url, token, detail_ids)

    pending_files: list[dict[str, Any]] = []

    for item in gm_items:
        gm_id = item.get("id")
        gm_title = (item.get("title") or str(gm_id) or "?").strip()
        if gm_id is None:
            unmatched_titles.append(gm_title)
            continue

        src_meta = _grimmory_item_to_source_metadata(item)

        # Fetch the real filesystem path from the Grimmory web API BEFORE
        # picking the best attribution. The path string carries strong
        # author_folder / series_folder / position signals that
        # evaluate_match can use on top of the curated source_metadata —
        # without it, ABS/Grimmory matches lose the path-side scoring
        # contribution and over-classify as candidates instead of confirmed.
        # The /api/v1/books/ endpoint returns BookFile objects with filePath;
        # /api/v1/app/books/ intentionally omits paths.
        # Cast gm_id to int to prevent path injection in the URL.
        try:
            safe_id = int(gm_id)
        except TypeError, ValueError:
            logger.warning("Grimmory: skipping book with non-integer id=%r", gm_id)
            unmatched_titles.append(gm_title)
            continue
        path = f"grimmory://{safe_id}"
        file_size: int | None = None
        detail = details_by_id.get(safe_id)
        if detail:
            try:
                primary = detail.get("primaryFile") or {}
                real_path = (primary.get("filePath") or "").strip()
                if not real_path:
                    for fobj in (detail.get("alternativeFormats") or []) + (
                        detail.get("supplementaryFiles") or []
                    ):
                        fp = (fobj.get("filePath") or "").strip()
                        if fp:
                            real_path = fp
                            break
                if real_path:
                    path = real_path
                size_kb = primary.get("fileSizeKb")
                if size_kb is not None:
                    file_size = int(float(size_kb)) * 1024
            except Exception as fp_exc:  # noqa: BLE001 — optional enrichment; fall back to the bare book payload if the detail shape is unexpected.
                logger.debug("Grimmory: could not parse file detail for book %s: %s", gm_id, fp_exc)

        # Drop rejected (path, book) pairs from consideration up front so the
        # first pick_best call already sees the filtered set — no re-pick needed.
        rejected_for_path: set[tuple[str, str]] = {
            (prov, pbid) for (src, p, prov, pbid) in rejections if src == "grimmory" and p == path
        }
        item_books = books
        if rejected_for_path:
            item_books = [
                b
                for b in books
                if ((b.get("provider") or ""), (b.get("provider_book_id") or ""))
                not in rejected_for_path
            ]

        result = pick_best_attribution(
            path=path if not path.startswith("grimmory://") else None,
            books=item_books,
            author_name=entity_name,
            embedded=None,
            source_metadata=src_meta,
        )
        if result.book is None:
            unmatched_titles.append(gm_title)
            continue

        file_type_raw = (item.get("primaryFileType") or "ebook").lower()

        try:
            import json as _json
            from dataclasses import asdict as _asdict

            evidence_json = _json.dumps(_asdict(result.evidence), default=str)
        except Exception:  # noqa: BLE001 — evidence is a diagnostic blob; if it fails to serialize, persist NULL rather than crash the whole upsert.
            evidence_json = None

        # Tier → status: confirmed = 'matched' (counts toward owned),
        # candidate = 'candidate' (Possible Candidates UI). Rejected returns
        # book=None and was handled above.
        tier = getattr(result, "tier", None) or getattr(result.evidence, "tier", "rejected")
        db_status = "matched" if tier == "confirmed" else "candidate"
        pending_files.append(
            {
                "provider": result.book.get("provider"),
                "provider_book_id": result.book.get("provider_book_id"),
                "path": path,
                "ext": file_type_raw,
                "file_type": "ebook",
                "size_bytes": file_size,
                "mtime": None,
                "confidence": result.confidence,
                "match_reason": result.match_reason,
                "source": "grimmory",
                "evidence_json": evidence_json,
                "status": db_status,
            }
        )
        kept_paths.append(path)

    # Flush all matched/candidate rows in one transaction (per-row SAVEPOINT
    # isolates any bad row). Replaces one commit per matched book.
    if pending_files:
        try:
            matched = monitored_db.upsert_monitored_book_files_batch(
                user_ids=[user_id],
                entity_id=entity_id,
                files=pending_files,
            )
        except Exception as exc:  # noqa: BLE001 — batch failure is logged; sync continues to prune below.
            logger.warning(
                "Grimmory: failed to batch-upsert %d matches for entity %s: %s",
                len(pending_files),
                entity_id,
                exc,
            )

    # Only prune when pagination was complete — avoids deleting valid records
    # if a timeout or error cut the book list short.
    if gm_complete:
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=kept_paths, source="grimmory"
        )
    else:
        logger.warning(
            "Grimmory sync entity_id=%s: skipping prune (pagination incomplete)",
            entity_id,
        )

    gm_total = len(gm_items)
    logger.info(
        "Grimmory sync entity_id=%s: %d/%d items matched",
        entity_id,
        matched,
        gm_total,
    )
    if unmatched_titles:
        logger.warning(
            "Grimmory sync entity_id=%s: %d items not matched: %s",
            entity_id,
            len(unmatched_titles),
            ", ".join(repr(t) for t in unmatched_titles[:10]),
        )
    return {"gm_matched": matched, "gm_total": gm_total}
