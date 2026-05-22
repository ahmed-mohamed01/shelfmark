"""AudioBookShelf integration for monitored book availability.

Fetches items from an ABS library, matches them to monitored books using a
3-phase algorithm (ASIN → series+position+title → fuzzy title), and records
matches in monitored_book_files with source='audiobookshelf'.

Called automatically at the end of the existing filesystem scan route — no
separate frontend button or API route is needed.
"""

from __future__ import annotations

import json
import re
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
    SERIES_POS_RE as _SERIES_POS_RE,
)
from shelfmark.core.monitored_integration_matching import (
    norm as _norm,
)

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# ABS-specific regex
# ---------------------------------------------------------------------------

# Pre-compiled helpers for _parse_abs_series_pairs()
_SERIES_SEGMENT_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z])")
# Matches "Series Name #N", "Series Name #N.M", "Series Name #N/M" (fraction notation)
_SERIES_SEGMENT_POS_RE = re.compile(r"^(.*?)\s+#\s*(\d+(?:[./]\d+)?)$")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_abs_config() -> dict[str, str] | None:
    """Return ABS connection config or None if not configured/enabled."""
    if not app_config.get("AUDIOBOOKSHELF_ENABLED", True):
        return None
    url = (app_config.get("AUDIOBOOKSHELF_URL") or "").strip().rstrip("/")
    token = (app_config.get("AUDIOBOOKSHELF_TOKEN") or "").strip()
    if url and token:
        return {"url": url, "token": token}
    return None


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no requests dependency)
# ---------------------------------------------------------------------------


def _build_ssl_ctx(url: str) -> ssl.SSLContext:
    """Return an ssl.SSLContext that respects the CERTIFICATE_VALIDATION setting."""
    import ssl

    from shelfmark.download.network import get_ssl_verify

    ctx = ssl.create_default_context()
    if not get_ssl_verify(url):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _abs_get(base_url: str, token: str, path: str, timeout: int = 10) -> Any:
    req = Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=timeout, context=_build_ssl_ctx(base_url)) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Library resolution
# ---------------------------------------------------------------------------


def _get_abs_library_ids(url: str, token: str) -> list[str]:
    """Return audiobook library IDs to scan.

    If ``AUDIOBOOKSHELF_LIBRARY_ID`` is configured, only that library is
    returned.  Otherwise *all* libraries with ``mediaType == "book"`` are
    included so that items across multiple audiobook libraries are matched.
    """
    configured = (app_config.get("AUDIOBOOKSHELF_LIBRARY_ID") or "").strip()
    if configured:
        return [configured]
    try:
        data = _abs_get(url, token, "/api/libraries")
        return [
            str(lib["id"])
            for lib in (data.get("libraries") or [])
            if lib.get("mediaType") == "book"
        ]
    except Exception as exc:  # noqa: BLE001 — ABS is external; bail to an empty library list on any fetch failure.
        logger.warning("ABS: failed to fetch libraries: %s", exc)
    return []


# ---------------------------------------------------------------------------
# Author lookup
# ---------------------------------------------------------------------------


def _find_abs_author_items(
    url: str,
    token: str,
    library_id: str,
    author_name: str,
) -> list[dict[str, Any]]:
    """Return all library items for the ABS author best-matching *author_name*.

    Steps:
    1. GET /api/libraries/{library_id}/authors  (all authors)
    2. Fuzzy-match against author_name
    3. GET /api/authors/{id}?include=items  for the winner
    """
    try:
        data = _abs_get(url, token, f"/api/libraries/{library_id}/authors")
        authors: list[dict[str, Any]] = data.get("authors") or []
    except Exception as exc:  # noqa: BLE001 — ABS is external; skip this library on fetch failure rather than abort the scan.
        logger.warning("ABS: failed to fetch authors for library %s: %s", library_id, exc)
        return []

    if not authors:
        return []

    # Split on commas/semicolons so name suffixes like "(Author)" or co-author
    # lists don't drop the per-pair ratio below threshold. target_parts is
    # constant across all authors — compute once outside the loop.
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
            "ABS: no author match for %r in library %s (best ratio=%.2f, %d authors checked)",
            author_name,
            library_id,
            best_ratio,
            len(authors),
        )
        return []

    author_id = best_author.get("id")
    logger.info(
        "ABS: matched author %r → %r (ratio=%.2f, id=%s)",
        author_name,
        best_author.get("name"),
        best_ratio,
        author_id,
    )

    try:
        author_data = _abs_get(url, token, f"/api/authors/{author_id}?include=items", timeout=60)
        items: list[dict[str, Any]] = author_data.get("libraryItems") or []
    except Exception as exc:  # noqa: BLE001 — ABS is external; treat fetch failure as zero items for this author.
        logger.warning("ABS: failed to fetch items for author %s: %s", author_id, exc)
        return []
    logger.info(
        "ABS: fetched %d library items for author %r (id=%s)",
        len(items),
        best_author.get("name"),
        author_id,
    )
    return items


# ---------------------------------------------------------------------------
# Title normalisation helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Series parsing
# ---------------------------------------------------------------------------


def _parse_abs_series_pairs(
    series_name_str: str,
    subtitle: str,
) -> list[tuple[str, float]]:
    """Parse ABS series metadata into [(series_name, position)] pairs.

    Handles both:
    - seriesName field: "Stormlight Archive #3, Cosmere #9"
    - subtitle field:   "The Stormlight Archive, Book 3"
    """
    pairs: list[tuple[str, float]] = []

    # --- Parse seriesName: "SeriesA #N, SeriesB #M" ---
    if series_name_str:
        # Split on ", " but only when not inside a number
        for raw_segment in _SERIES_SEGMENT_SPLIT_RE.split(series_name_str):
            segment = raw_segment.strip()
            if not segment:
                continue
            # Try to extract "#N" or similar at the end
            m = _SERIES_SEGMENT_POS_RE.search(segment)
            if m:
                sname = m.group(1).strip()
                pos_str = m.group(2)
                try:
                    # "N/M" = book N of M — position is the numerator
                    pos = float(pos_str.split("/", 1)[0]) if "/" in pos_str else float(pos_str)
                    if sname:
                        pairs.append((sname, pos))
                except ValueError:
                    pass

    # --- Parse subtitle: "The Stormlight Archive, Book 3" ---
    if subtitle:
        m = _SERIES_POS_RE.search(subtitle)
        if m:
            try:
                pos = float(m.group(1))
                # Series name is the part before "Book N" / "#N"
                sname = subtitle[: m.start()].rstrip(", ").strip()
                if sname:
                    pairs.append((sname, pos))
            except ValueError:
                pass

    return pairs


# ---------------------------------------------------------------------------
# Adapter: ABS library item → unified SourceMetadata
# ---------------------------------------------------------------------------


def _abs_item_to_source_metadata(item: dict[str, Any]) -> SourceMetadata:
    """Translate an ABS library item into the unified ``SourceMetadata`` shape.

    ABS items can carry multiple series pairs (e.g.
    "Stormlight Archive #5, Cosmere #19"). All pairs are forwarded via
    ``all_series_pairs`` so the scorer can match any of them against the
    book's own ``all_series``. The primary ``series_name`` / ``series_position``
    fields keep the first pair for back-compat with non-multi-series callers.
    """
    meta = (item.get("media") or {}).get("metadata") or {}
    series_pairs = _parse_abs_series_pairs(
        (meta.get("seriesName") or "").strip(),
        (meta.get("subtitle") or "").strip(),
    )
    first_series, first_pos = series_pairs[0] if series_pairs else (None, None)
    return SourceMetadata(
        title=(meta.get("title") or "").strip() or None,
        author=(meta.get("authorName") or "").strip() or None,
        series_name=first_series,
        series_position=first_pos,
        all_series_pairs=list(series_pairs),
        asin=(meta.get("asin") or "").strip() or None,
        source_label="abs",
    )


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_KNOWN_AUDIO_EXTS = {"m4b", "mp3", "m4a", "flac", "opus", "aac", "ogg", "wav"}


def _get_abs_item_format(item: dict[str, Any]) -> str | None:
    """Try to extract the primary audio format (e.g. 'm4b') from an ABS library item.

    Tries multiple fields because the author-items endpoint may return a lighter
    representation where ``metadata.ext`` is absent but filenames are available.
    """
    media = item.get("media") or {}
    audio_files = media.get("audioFiles") or []
    for af in audio_files:
        af_meta = af.get("metadata") or {}
        # metadata.ext — present in full item responses
        ext = (af_meta.get("ext") or "").lstrip(".").lower()
        if ext in _KNOWN_AUDIO_EXTS:
            return ext
        # Fallback: extract extension from filename / relPath
        for fname in (af_meta.get("filename"), af.get("relPath"), af_meta.get("path")):
            if fname and "." in str(fname):
                candidate = str(fname).rsplit(".", 1)[-1].lower()
                if candidate in _KNOWN_AUDIO_EXTS:
                    return candidate
    # Last resort: item path has an extension for single-file audiobooks
    item_path = (item.get("path") or "").lower()
    basename = item_path.rsplit("/", 1)[-1]
    if "." in basename:
        candidate = basename.rsplit(".", 1)[-1]
        if candidate in _KNOWN_AUDIO_EXTS:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Public sync functions
# ---------------------------------------------------------------------------


def sync_abs_availability_for_entity(
    *,
    monitored_db: Any,
    entity_id: int,
    entity_name: str,
    user_id: int | None,
) -> dict[str, Any]:
    """Sync ABS audiobook availability for one monitored entity (author).

    Fetches all ABS items for the author, matches them to monitored books,
    upserts matches with source='audiobookshelf', and prunes stale ABS records.

    Returns a result dict with abs_matched / abs_total / abs_skipped.
    """
    cfg = get_abs_config()
    if not cfg:
        # Prune stale ABS records when integration is disabled or not configured
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=[], source="audiobookshelf"
        )
        return {"abs_skipped": True, "reason": "not_configured"}

    library_ids = _get_abs_library_ids(cfg["url"], cfg["token"])
    if not library_ids:
        return {"abs_skipped": True, "reason": "no_library"}

    # Gather items across all audiobook libraries, deduplicating by item ID
    abs_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for lib_id in library_ids:
        for item in _find_abs_author_items(cfg["url"], cfg["token"], lib_id, entity_name):
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            abs_items.append(item)

    if not abs_items:
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=[], source="audiobookshelf"
        )
        return {"abs_matched": 0, "abs_total": 0}

    books = monitored_db.list_monitored_books(user_ids=[user_id], entity_id=entity_id) or []

    # User-rejected (path, book) pairs — sync must not re-attribute these.
    rejections = monitored_db.list_file_rejections_for_entity(entity_id=entity_id)

    # Filter out items ABS itself marks as unavailable — these are never matchable.
    # isInvalid: files present but ABS considers the metadata/files broken.
    # isMissing: files are gone from disk.
    candidate_items = [
        item for item in abs_items if not item.get("isMissing") and not item.get("isInvalid")
    ]
    skipped = len(abs_items) - len(candidate_items)
    if skipped:
        logger.warning(
            "ABS entity_id=%s: %d/%d items skipped (isMissing or isInvalid)",
            entity_id,
            skipped,
            len(abs_items),
        )

    matched = 0
    kept_paths: list[str] = []
    unmatched_titles: list[str] = []

    for item in candidate_items:
        meta = (item.get("media") or {}).get("metadata") or {}
        abs_title = (meta.get("title") or item.get("path") or "?").strip()
        item_path = (item.get("path") or "").strip()

        # Drop books the user explicitly rejected for this (source, path).
        item_books = (
            [
                b
                for b in books
                if (
                    "audiobookshelf",
                    item_path,
                    (b.get("provider") or ""),
                    (b.get("provider_book_id") or ""),
                )
                not in rejections
            ]
            if item_path
            else books
        )

        src_meta = _abs_item_to_source_metadata(item)
        # Pass the ABS path through to pick_best_attribution. The path string
        # follows the same author/series/book conventions as local files, so
        # path-side signals (author_folder, series_folder, position votes
        # from "Book N" markers, etc.) are valuable corroboration on top of
        # the source_metadata. evaluate_match never touches the filesystem;
        # it only decomposes the string.
        result = pick_best_attribution(
            path=item_path or None,
            books=item_books,
            author_name=entity_name,
            embedded=None,
            source_metadata=src_meta,
        )
        if result.book is None:
            unmatched_titles.append(abs_title)
            continue

        path = (item.get("path") or "").strip()
        if not path:
            unmatched_titles.append(abs_title)
            continue

        # The author-items endpoint returns minified items without audioFiles, so
        # _get_abs_item_format often returns None.  Fetch the full item to get the
        # actual audio file extension (e.g. "m4b") when the quick check fails.
        item_ext = _get_abs_item_format(item)
        if item_ext is None:
            item_id = item.get("id")
            if item_id:
                try:
                    full_item = _abs_get(
                        cfg["url"], cfg["token"], f"/api/items/{item_id}", timeout=10
                    )
                    item_ext = _get_abs_item_format(full_item)
                except Exception as _fmt_exc:  # noqa: BLE001 — format enrichment is best-effort; fall back to the bare item payload.
                    logger.debug(
                        "ABS: could not fetch full item %s for format: %s", item_id, _fmt_exc
                    )

        # Serialise the v2 evidence vector for the API + UI "Why?" panel.
        try:
            import json as _json
            from dataclasses import asdict as _asdict

            evidence_json = _json.dumps(_asdict(result.evidence), default=str)
        except Exception:  # noqa: BLE001 — evidence is a diagnostic blob; if it fails to serialize, persist NULL rather than crash the whole upsert.
            evidence_json = None

        # Tier maps to status: confirmed → 'matched' (counts toward owned);
        # candidate → 'candidate' (Possible Candidates UI). Rejected returns
        # book=None and was handled above.
        tier = getattr(result, "tier", None) or getattr(result.evidence, "tier", "rejected")
        db_status = "matched" if tier == "confirmed" else "candidate"
        try:
            monitored_db.upsert_monitored_book_file(
                user_ids=[user_id],
                entity_id=entity_id,
                provider=result.book.get("provider"),
                provider_book_id=result.book.get("provider_book_id"),
                path=path,
                ext=item_ext,
                file_type="audiobook",
                size_bytes=item.get("size"),
                mtime=None,
                confidence=result.confidence,
                match_reason=result.match_reason,
                source="audiobookshelf",
                evidence_json=evidence_json,
                status=db_status,
            )
            kept_paths.append(path)
            matched += 1
        except Exception as exc:  # noqa: BLE001 — per-row failure (DB lock, JSON, constraint) is logged and counted as unmatched; one bad row must not abort the whole scan.
            logger.warning(
                "ABS: failed to upsert match for %r (entity=%s book=%s): %s",
                path,
                entity_id,
                result.book.get("provider_book_id"),
                exc,
            )
            unmatched_titles.append(abs_title)

    monitored_db.prune_monitored_book_files(
        entity_id=entity_id, keep_paths=kept_paths, source="audiobookshelf"
    )

    abs_total = len(candidate_items)
    logger.info(
        "ABS sync entity_id=%s: %d/%d items matched",
        entity_id,
        matched,
        abs_total,
    )
    if unmatched_titles:
        logger.warning(
            "ABS sync entity_id=%s: %d items not matched: %s",
            entity_id,
            len(unmatched_titles),
            ", ".join(repr(t) for t in unmatched_titles[:10]),
        )
    return {"abs_matched": matched, "abs_total": abs_total}
