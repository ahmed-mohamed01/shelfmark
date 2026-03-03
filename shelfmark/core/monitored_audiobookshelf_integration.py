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
from typing import Any
from urllib.request import Request, urlopen

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Matching thresholds
# ---------------------------------------------------------------------------

_SERIES_NAME_MIN_RATIO = 0.75   # series name fuzzy threshold (phase 2)
_SERIES_TITLE_MIN_RATIO = 0.60  # title confirmation in phase 2
_TITLE_FUZZY_MIN = 0.70         # title ratio threshold for phase 3
_AUTHOR_FUZZY_MIN = 0.70        # author ratio threshold for phase 3

# Regex for "(Unabridged)" suffix in ABS titles
_UNABRIDGED_RE = re.compile(r"\s*\(unabridged\)\s*$", re.IGNORECASE)

# Regex to strip any trailing parenthetical — used as an additional title
# candidate when one source has "(We Are Bob)" or "(Graphic Audio)" and the
# other doesn't.  Safer than lowering the threshold alone.
_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")

# Regex to strip ": subtitle" from shelfmark titles
# e.g. "Mitosis: A Reckoners Story" → "Mitosis"
_COLON_SUBTITLE_RE = re.compile(r"\s*:.*$")

# Regex to extract series position from "Book N", "#N", "Part N", "Volume N"
_SERIES_POS_RE = re.compile(
    r"(?:book|part|vol(?:ume)?|#)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Pre-compiled helpers for _norm() — avoids repeated pattern compilation
_NORM_STRIP_RE = re.compile(r"[^a-z0-9]+")
_NORM_SPACE_RE = re.compile(r"\s+")

# Pre-compiled helpers for _parse_abs_series_pairs()
_SERIES_SEGMENT_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z])")
# Matches "Series Name #N", "Series Name #N.M", "Series Name #N/M" (fraction notation)
_SERIES_SEGMENT_POS_RE = re.compile(r"^(.*?)\s+#\s*(\d+(?:[./]\d+)?)$")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_abs_config() -> dict[str, str] | None:
    """Return ABS connection config or None if not configured."""
    url = (app_config.get("AUDIOBOOKSHELF_URL") or "").strip().rstrip("/")
    token = (app_config.get("AUDIOBOOKSHELF_TOKEN") or "").strip()
    if url and token:
        return {"url": url, "token": token}
    return None


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no requests dependency)
# ---------------------------------------------------------------------------


def _abs_get(base_url: str, token: str, path: str, timeout: int = 10) -> Any:
    req = Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Library resolution
# ---------------------------------------------------------------------------


def _get_abs_library_id(url: str, token: str) -> str | None:
    """Return configured library ID, or the first audiobook library found."""
    configured = (app_config.get("AUDIOBOOKSHELF_LIBRARY_ID") or "").strip()
    if configured:
        return configured
    try:
        data = _abs_get(url, token, "/api/libraries")
        for lib in data.get("libraries") or []:
            if lib.get("mediaType") == "book":
                return str(lib["id"])
    except Exception as exc:
        logger.warning("ABS: failed to fetch libraries: %s", exc)
    return None


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
    except Exception as exc:
        logger.warning("ABS: failed to fetch authors for library %s: %s", library_id, exc)
        return []

    if not authors:
        return []

    # Use the same token-split logic as _author_matches so that name suffixes
    # like "(Author)" or middle names don't drop the ratio below threshold.
    # target_parts is constant across all authors — compute once outside the loop.
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
            "ABS: no author match for %r in library %s (best ratio=%.2f, %d authors checked)",
            author_name, library_id, best_ratio, len(authors),
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
        logger.info("ABS: fetched %d library items for author %r (id=%s)", len(items), best_author.get("name"), author_id)
        return items
    except Exception as exc:
        logger.warning("ABS: failed to fetch items for author %s: %s", author_id, exc)
        return []


# ---------------------------------------------------------------------------
# Title normalisation helpers
# ---------------------------------------------------------------------------


def _norm(value: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    return _NORM_SPACE_RE.sub(" ", _NORM_STRIP_RE.sub(" ", (value or "").lower())).strip()


def _normalize_abs_title(title: str, series_names: list[str]) -> str:
    """Strip '(Unabridged)', strip 'SeriesName: ' prefix, and strip ': subtitle' suffix.

    ABS sometimes stores titles as "Title : SeriesName" (e.g. "The Dark Talent :
    Alcatraz vs the Evil Librarians"). Stripping the suffix gives the bare title
    that matches the shelfmark entry.
    """
    t = _UNABRIDGED_RE.sub("", title).strip()
    # Strip 'SeriesName: ' prefix (e.g. 'Infinity Blade: Awakening' → 'Awakening')
    for sn in series_names:
        prefix = sn.rstrip() + ": "
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
            break
    # Strip ': subtitle' suffix (e.g. 'The Dark Talent : Alcatraz vs the Evil Librarians'
    # → 'The Dark Talent')
    t = _COLON_SUBTITLE_RE.sub("", t).strip()
    return t


def _normalize_shelfmark_title(title: str) -> str:
    """Strip ': subtitle' suffix (e.g. 'Mitosis: A Reckoners Story' → 'Mitosis')."""
    return _COLON_SUBTITLE_RE.sub("", title).strip()


# ---------------------------------------------------------------------------
# Series parsing
# ---------------------------------------------------------------------------


def _parse_series_position(raw: str) -> float | None:
    """Extract a numeric position from strings like '#3', 'Book 3', '3.1', '1/2'."""
    raw = raw.strip()
    # Try direct float first (handles "3", "3.1", "0.5")
    try:
        return float(raw)
    except ValueError:
        pass
    # "N/M" means "book N of M total" — the position is the numerator only.
    # e.g. "1/2" → 1.0 (first book in a two-book series), "2/3" → 2.0
    if "/" in raw:
        parts = raw.split("/", 1)
        try:
            return float(parts[0])
        except ValueError:
            pass
    m = _SERIES_POS_RE.search(raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


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
        for segment in _SERIES_SEGMENT_SPLIT_RE.split(series_name_str):
            segment = segment.strip()
            if not segment:
                continue
            # Try to extract "#N" or similar at the end
            m = _SERIES_SEGMENT_POS_RE.search(segment)
            if m:
                sname = m.group(1).strip()
                pos_str = m.group(2)
                try:
                    if "/" in pos_str:
                        # "N/M" = book N of M — position is the numerator
                        pos = float(pos_str.split("/", 1)[0])
                    else:
                        pos = float(pos_str)
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
# ASIN helpers
# ---------------------------------------------------------------------------


def _parse_asins(raw: Any) -> set[str]:
    """Return the set of ASINs from a book's asins field (JSON string or list)."""
    if not raw:
        return set()
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except Exception:
            return set()
        if isinstance(val, list):
            return {str(a).strip() for a in val if a}
        return set()
    if isinstance(raw, list):
        return {str(a).strip() for a in raw if a}
    return set()


# ---------------------------------------------------------------------------
# Author matching helper
# ---------------------------------------------------------------------------

# Regex to split multi-value author/narrator strings like "A, B; C"
_AUTHOR_SPLIT_RE = re.compile(r"[,;]")


def _author_matches(abs_author: str, book_authors: str) -> bool:
    """Return True if any ABS author token fuzzy-matches any book author token.

    ABS often stores narrators alongside authors in the ``authorName`` field
    (e.g. ``"Brandon Sanderson, Michael Kramer, Kate Reading"``).  A whole-
    string comparison would produce a low ratio and reject valid matches.  By
    splitting on ``,`` / ``;`` first we compare individual names.
    """
    abs_parts = [p.strip() for p in _AUTHOR_SPLIT_RE.split(abs_author) if p.strip()]
    book_parts = [p.strip() for p in _AUTHOR_SPLIT_RE.split(book_authors) if p.strip()]
    # Fall back to whole strings if either side is empty after splitting
    if not abs_parts:
        abs_parts = [abs_author]
    if not book_parts:
        book_parts = [book_authors]
    for a in abs_parts:
        for b in book_parts:
            if SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= _AUTHOR_FUZZY_MIN:
                return True
    return False


# ---------------------------------------------------------------------------
# Core matching
# ---------------------------------------------------------------------------


def _match_abs_item_to_books(
    abs_item: dict[str, Any],
    books: list[dict[str, Any]],
    entity_name: str = "",
) -> tuple[dict[str, Any] | None, float, str]:
    """Match an ABS library item to a monitored book.

    ``entity_name`` is used as a fallback author for the confirmation check
    when a book row has ``authors = NULL`` (which is always the case for books
    belonging to an author entity, since the author is implied by the entity).

    Returns (book, confidence, reason) or (None, 0.0, '') if no match.
    """
    meta = (abs_item.get("media") or {}).get("metadata") or {}
    abs_asin = (meta.get("asin") or "").strip()
    abs_title = (meta.get("title") or "").strip()
    abs_author = (meta.get("authorName") or "").strip()
    abs_series_str = (meta.get("seriesName") or "").strip()
    abs_subtitle = (meta.get("subtitle") or "").strip()

    # ------------------------------------------------------------------
    # Phase 1: ASIN exact match
    # ------------------------------------------------------------------
    if abs_asin:
        for book in books:
            if abs_asin in _parse_asins(book.get("asins")):
                logger.debug("ABS match [asin] %r → %r", abs_title, book.get("title"))
                return book, 1.0, "abs_asin"

    # ------------------------------------------------------------------
    # Phase 2: Series + position + title confirmation
    # ------------------------------------------------------------------
    series_pairs = _parse_abs_series_pairs(abs_series_str, abs_subtitle)
    series_names = [sn for sn, _ in series_pairs]

    if series_pairs and abs_title:
        norm_abs = _normalize_abs_title(abs_title, series_names)
        norm_abs_n = _norm(norm_abs)
        # Raw normalised title — no series-prefix stripping, fixes cases where
        # the prefix strip removes too much (e.g. "Azarinth Healer: Book Four"
        # → "Book Four" after strip, but "azarinth healer book four" raw).
        norm_abs_raw_n = _norm(abs_title)
        # Pre-normalise series-pair names once
        norm_series_pairs = [(sn, pos, _norm(sn)) for sn, pos in series_pairs]

        p2_best_book: dict[str, Any] | None = None
        p2_best_t = 0.0
        p2_best_sn = ""
        p2_best_pos = 0.0

        for book in books:
            b_series = (book.get("series_name") or "").strip()
            b_pos_raw = book.get("series_position")
            if b_pos_raw is None:
                continue
            b_pos = _parse_series_position(str(b_pos_raw))
            if b_pos is None:
                continue
            norm_b_series = _norm(b_series)
            raw_shelf = book.get("title") or ""
            norm_shelf_stripped = _norm(_normalize_shelfmark_title(raw_shelf))
            norm_shelf_full = _norm(raw_shelf)
            for abs_sn, abs_pos, norm_abs_sn in norm_series_pairs:
                if abs(b_pos - abs_pos) > 0.01:
                    continue
                sn_ratio = SequenceMatcher(None, norm_abs_sn, norm_b_series).ratio()
                if sn_ratio < _SERIES_NAME_MIN_RATIO:
                    continue
                # Also compare raw title and series name against shelfmark title.
                # Fixes "Azarinth Healer: Book Four" (raw includes series prefix
                # that was stripped away) and "Beware of Chicken: A Xianxia …"
                # book 1 (series name itself is the book title).
                t_ratio = max(
                    SequenceMatcher(None, norm_abs_n, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_abs_n, norm_shelf_full).ratio(),
                    SequenceMatcher(None, norm_abs_raw_n, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_abs_raw_n, norm_shelf_full).ratio(),
                    SequenceMatcher(None, norm_abs_sn, norm_shelf_stripped).ratio(),
                    SequenceMatcher(None, norm_abs_sn, norm_shelf_full).ratio(),
                )
                if t_ratio >= _SERIES_TITLE_MIN_RATIO and t_ratio > p2_best_t:
                    p2_best_t, p2_best_book = t_ratio, book
                    p2_best_sn, p2_best_pos = abs_sn, abs_pos

        if p2_best_book is not None:
            # Confidence is proportional to title quality, capped at 0.92
            conf = min(0.92, 0.80 + p2_best_t * 0.12)
            logger.debug(
                "ABS match [series_pos] %r → %r (series=%r pos=%.1f t=%.2f conf=%.2f)",
                abs_title,
                p2_best_book.get("title"),
                p2_best_sn,
                p2_best_pos,
                p2_best_t,
                conf,
            )
            return p2_best_book, conf, "abs_series_pos"

    # ------------------------------------------------------------------
    # Phase 3: Bidirectional-normalised title fuzzy match
    # ------------------------------------------------------------------
    if not abs_title:
        return None, 0.0, ""

    norm_abs = _normalize_abs_title(abs_title, series_names)
    norm_abs_n = _norm(norm_abs)
    # Raw normalised title without any stripping — fallback for cases where the
    # series-prefix strip removes meaningful words (e.g. "Azarinth Healer: Book
    # Four" stripped to "Book Four" would fail, but raw title matches fine).
    norm_abs_raw_n = _norm(abs_title)
    # Parenthetical-stripped form — handles "We Are Legion (We Are Bob)" vs
    # "We Are Legion": stripping "(We Are Bob)" makes both sides comparable.
    norm_abs_paren_n = _norm(_PAREN_SUFFIX_RE.sub("", abs_title))
    best_book: dict[str, Any] | None = None
    best_t_ratio = 0.0

    for book in books:
        raw_shelf = book.get("title") or ""
        norm_shelf_stripped = _norm(_normalize_shelfmark_title(raw_shelf))
        norm_shelf_full = _norm(raw_shelf)
        norm_shelf_paren = _norm(_PAREN_SUFFIX_RE.sub("", raw_shelf))
        t_ratio = max(
            SequenceMatcher(None, norm_abs_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_abs_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_abs_raw_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_abs_raw_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_abs_paren_n, norm_shelf_stripped).ratio(),
            SequenceMatcher(None, norm_abs_paren_n, norm_shelf_full).ratio(),
            SequenceMatcher(None, norm_abs_n, norm_shelf_paren).ratio(),
            SequenceMatcher(None, norm_abs_paren_n, norm_shelf_paren).ratio(),
        )
        if t_ratio < _TITLE_FUZZY_MIN or t_ratio <= best_t_ratio:
            continue
        # Author confirmation — split on commas to handle narrators in ABS authorName.
        # Fall back to entity_name when book.authors is NULL (author entities always
        # store NULL there since the author is implied by the entity itself).
        # When ABS has no author metadata at all, allow a title-only match if the
        # title ratio is strong (≥ 0.88) to avoid rejecting valid matches.
        book_author_str = book.get("authors") or entity_name
        if not abs_author:
            if t_ratio >= 0.88:
                best_t_ratio, best_book = t_ratio, book
            else:
                logger.debug(
                    "ABS title match %r → %r (t=%.2f) skipped: no ABS author and t < 0.88",
                    abs_title,
                    book.get("title"),
                    t_ratio,
                )
        elif _author_matches(abs_author, book_author_str):
            best_t_ratio, best_book = t_ratio, book
        else:
            logger.debug(
                "ABS title match %r → %r (t=%.2f) rejected: author mismatch (abs=%r vs book=%r)",
                abs_title,
                book.get("title"),
                t_ratio,
                abs_author,
                book_author_str,
            )

    if best_book:
        conf = best_t_ratio * 0.85
        logger.debug(
            "ABS match [fuzzy] %r → %r (t=%.2f conf=%.2f)",
            abs_title,
            best_book.get("title"),
            best_t_ratio,
            conf,
        )
        return best_book, conf, "abs_fuzzy"

    return None, 0.0, ""


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
        return {"abs_skipped": True, "reason": "not_configured"}

    library_id = _get_abs_library_id(cfg["url"], cfg["token"])
    if not library_id:
        return {"abs_skipped": True, "reason": "no_library"}

    abs_items = _find_abs_author_items(cfg["url"], cfg["token"], library_id, entity_name)
    if not abs_items:
        monitored_db.prune_monitored_book_files(
            entity_id=entity_id, keep_paths=[], source="audiobookshelf"
        )
        return {"abs_matched": 0, "abs_total": 0}

    books = monitored_db.list_monitored_books(user_id=user_id, entity_id=entity_id) or []

    # Filter out items ABS itself marks as unavailable — these are never matchable.
    # isInvalid: files present but ABS considers the metadata/files broken.
    # isMissing: files are gone from disk.
    candidate_items = [
        item for item in abs_items
        if not item.get("isMissing") and not item.get("isInvalid")
    ]
    skipped = len(abs_items) - len(candidate_items)
    if skipped:
        logger.warning(
            "ABS entity_id=%s: %d/%d items skipped (isMissing or isInvalid)",
            entity_id, skipped, len(abs_items),
        )

    matched = 0
    kept_paths: list[str] = []
    unmatched_titles: list[str] = []

    for item in candidate_items:
        meta = (item.get("media") or {}).get("metadata") or {}
        abs_title = (meta.get("title") or item.get("path") or "?").strip()

        book, conf, reason = _match_abs_item_to_books(item, books, entity_name=entity_name)
        if not book:
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
                    full_item = _abs_get(cfg["url"], cfg["token"], f"/api/items/{item_id}", timeout=10)
                    item_ext = _get_abs_item_format(full_item)
                except Exception as _fmt_exc:
                    logger.debug("ABS: could not fetch full item %s for format: %s", item_id, _fmt_exc)

        try:
            monitored_db.upsert_monitored_book_file(
                user_id=user_id,
                entity_id=entity_id,
                provider=book.get("provider"),
                provider_book_id=book.get("provider_book_id"),
                path=path,
                ext=item_ext,
                file_type="audiobook",
                size_bytes=item.get("size"),
                mtime=None,
                confidence=conf,
                match_reason=reason,
                source="audiobookshelf",
            )
            kept_paths.append(path)
            matched += 1
        except Exception as exc:
            logger.warning(
                "ABS: failed to upsert match for %r (entity=%s book=%s): %s",
                path,
                entity_id,
                book.get("provider_book_id"),
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
