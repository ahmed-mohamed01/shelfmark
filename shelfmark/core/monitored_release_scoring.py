from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Iterable

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import Release

logger = setup_logger(__name__)


_WORD_NUMBER_MAP: dict[str, float] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

_ROMAN_MAP: dict[str, float] = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
}

_FORBIDDEN_WORDS = {
    "abridged",
    "sample",
    "excerpt",
    "summary",
    "book summary",
}

_GENERIC_TITLE_TOKENS = {
    "a",
    "an",
    "the",
    "of",
    "and",
    "book",
    "series",
    "audiobook",
    "audio",
    "litrpg",
    "adventure",
    "novel",
}

_LOW_INFORMATION_TITLE_TOKENS = {
    *_GENERIC_TITLE_TOKENS,
    "bk",
    "vol",
    "volume",
    "part",
    "edition",
    *set(_WORD_NUMBER_MAP.keys()),
    *set(_ROMAN_MAP.keys()),
}

_LOW_INFORMATION_TITLE_MAX_SCORE = 20

_SERIES_NUM_TOKEN_RE = (
    r"([0-9]+(?:\.[0-9]+)?|[ivx]+\b|zero|one|two|three|four|five|six|seven|eight|nine|ten|"  # noqa: S105 -- regex pattern, not a password
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
)

# Stronger priority boosts so preferred sources/indexers win close calls.
# Rank 0 => +12, rank 1 => +9, rank 2 => +6, rank 3 => +3, rank 4 => +1.
_PRIORITY_BOOST_BY_RANK = [12, 9, 6, 3, 1]


@dataclass
class ReleaseMatchScore:
    score: int
    breakdown: dict[str, int]
    confidence: str
    hard_reject: bool = False
    reject_reason: str | None = None


@dataclass
class ReleaseScoringConfig:
    forbidden_words: set[str]
    min_title_score: int
    min_author_score: int
    prefer_freeleech_or_direct: bool
    ebook_release_priority: dict[str, int]
    audiobook_release_priority: dict[str, int]
    ebook_format_priority: dict[str, int]
    audiobook_format_priority: dict[str, int]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def _tokens(value: str) -> list[str]:
    normalized = _normalize_text(value)
    return [token for token in normalized.split(" ") if token]


def _distinctive_tokens(value: str) -> list[str]:
    return [
        token for token in _tokens(value) if len(token) > 2 and token not in _GENERIC_TITLE_TOKENS
    ]


def _sequence_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _token_overlap_ratio(a: str, b: str) -> float:
    ta = set(_tokens(a))
    tb = set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _best_ratio(candidates: Iterable[str], target: str) -> float:
    norm_target = _normalize_text(target)
    if not norm_target:
        return 0.0
    best = 0.0
    for candidate in candidates:
        nc = _normalize_text(candidate)
        if not nc:
            continue
        best = max(best, _sequence_ratio(norm_target, nc), _token_overlap_ratio(norm_target, nc))
    return best


def _extract_release_author(release: Release) -> str:
    value = release.extra.get("author") if isinstance(release.extra, dict) else None
    if isinstance(value, str) and value.strip():
        return value

    if " - " in release.title:
        maybe_author = release.title.split(" - ", 1)[0].strip()
        if maybe_author and len(maybe_author) < 60:
            return maybe_author

    return ""


def _author_variants(value: str) -> list[str]:
    """Extract plausible author fragments from noisy source strings."""
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return []

    variants: list[str] = [raw]
    for part in re.split(r"\s*(?:,|;|\||/|&|\band\b)\s*", raw, flags=re.IGNORECASE):
        token = part.strip()
        if token and token not in variants:
            variants.append(token)
    return variants


def _extract_release_year(release: Release) -> int | None:
    value = release.extra.get("year") if isinstance(release.extra, dict) else None
    if value is not None:
        match = re.search(r"(19\d{2}|20\d{2})", str(value))
        if match:
            return int(match.group(1))

    match = re.search(r"(19\d{2}|20\d{2})", release.title)
    if match:
        return int(match.group(1))

    return None


def _extract_series_number_after_series_name(series_name: str, release_title: str) -> float | None:
    """Extract a number that appears immediately after the matched series name."""
    if not series_name or not release_title:
        return None

    series_norm = _normalize_text(series_name)
    release_norm = _normalize_text(release_title)
    if not series_norm or not release_norm:
        return None

    marker = f" {series_norm} "
    haystack = f" {release_norm} "
    if marker not in haystack:
        return None

    tail = haystack.split(marker, 1)[1].strip()
    if not tail:
        return None

    # Handle titles like:
    # - "Dungeon Life 2: ..."  # noqa: ERA001
    # - "Dungeon Life Book 2"
    # - "Dungeon Life #2"
    match = re.match(
        rf"^(?:book|bk|volume|vol|part|#|no|number)?\s*{_SERIES_NUM_TOKEN_RE}\b",
        tail,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    number = _word_to_number(match.group(1))
    if number is None:
        return None

    # Guard against years/large unrelated numbers.
    if number > 200:
        return None
    return number


def _extract_release_series_number(release: Release, series_name: str | None) -> float | None:
    """Best-effort series number extraction from source metadata first, then title."""
    extra = release.extra if isinstance(release.extra, dict) else {}

    def _parse(raw: str) -> float | None:
        n = _word_to_number(raw.strip().lower())
        if n is not None and n <= 200:
            return n
        n = _extract_series_number(raw)
        return n if n is not None and n <= 200 else None

    # Prefer explicit metadata fields when available.
    for key in (
        "series_position",
        "series_number",
        "book_number",
        "book_num",
        "volume",
        "vol",
        "part",
        "book",
        "number",
    ):
        value = extra.get(key)
        if value is None:
            continue
        result = _parse(str(value))
        if result is not None:
            return result

    torznab_attrs = (
        extra.get("torznab_attrs") if isinstance(extra.get("torznab_attrs"), dict) else {}
    )
    for key in ("series", "seriesnumber", "book", "booknumber", "volume", "vol", "part"):
        value = torznab_attrs.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        result = _parse(value)
        if result is not None:
            return result

    release_num = _extract_series_number(release.title)
    if release_num is not None:
        return release_num

    return _extract_series_number_after_series_name(series_name or "", release.title)


def _word_to_number(token: str) -> float | None:
    if not token:
        return None
    if token in _WORD_NUMBER_MAP:
        return _WORD_NUMBER_MAP[token]
    if token in _ROMAN_MAP:
        return _ROMAN_MAP[token]
    try:
        return float(token)
    except Exception:  # noqa: BLE001
        return None


def _extract_series_number(text: str) -> float | None:
    if not text:
        return None

    raw = (text or "").lower()
    normalized = _normalize_text(text)

    patterns = [
        rf"(?:book|bk|volume|vol|part)\s*#?\s*{_SERIES_NUM_TOKEN_RE}",
        rf"#\s*{_SERIES_NUM_TOKEN_RE}",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            result = _word_to_number(match.group(1))
            if result is not None:
                return result

    # Fallback on normalized text in case symbols were stripped
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            result = _word_to_number(match.group(1))
            if result is not None:
                return result

    return None


# ---------------------------------------------------------------------------
# Segment-based release title parsing
# ---------------------------------------------------------------------------

_SEGMENT_SEPARATOR_RE = re.compile(
    r"\s*[-–—]\s+"  # dash variants with surrounding spaces
    r"|\s*:\s*"  # colon
    r"|\s*,\s+"  # comma followed by space (not inside numbers)
    r"|\s*[\[\]()]\s*"  # brackets and parens
)


def _split_release_segments(title: str) -> list[str]:
    """Split a release title into structural segments and normalize each."""
    parts = _SEGMENT_SEPARATOR_RE.split(title or "")
    return [norm for p in parts if (norm := _normalize_text(p))]


def _matches_any_segment(candidate_norm: str, segments: list[str]) -> bool:
    """Check if candidate matches any segment as an isolated title.

    Matches when the candidate:
    - Exactly equals a segment, OR
    - Appears as a leading phrase covering ≥70% of the segment's tokens
      (handles colon-less variants like "An Unwelcome Quest Magic 2 0").
    """
    if not candidate_norm or not segments:
        return False

    candidate_tokens = set(_tokens(candidate_norm))
    if not candidate_tokens:
        return False

    for seg in segments:
        if not seg:
            continue
        if candidate_norm == seg:
            return True
        seg_tokens = _tokens(seg)
        if not seg_tokens:
            continue
        # Candidate must appear at start of segment as whole words and cover most of it.
        if seg.startswith(candidate_norm + " ") and len(candidate_tokens) / len(seg_tokens) >= 0.70:
            return True
    return False


def _check_author_in_segments(book: BookMetadata, release_title: str) -> int:
    """Fallback: check if any known author name matches a release segment."""
    candidates: list[str] = []
    if book.search_author:
        candidates.append(book.search_author)
    candidates.extend(book.authors or [])
    if not candidates:
        return 0

    segments = _split_release_segments(release_title)
    if not segments:
        return 0

    for author in candidates:
        author_norm = _normalize_text(author)
        if not author_norm or len(author_norm) < 4:
            continue
        if _matches_any_segment(author_norm, segments):
            return 24

    return 0


def _score_single_title_candidate(candidate: str, release_title: str) -> int:
    candidate_norm = _normalize_text(candidate)
    release_norm = _normalize_text(release_title)
    if not candidate_norm or not release_norm:
        return 0

    candidate_tokens = _tokens(candidate_norm)
    is_low_information_candidate = (
        bool(candidate_tokens)
        and len(candidate_tokens) <= 3
        and all(
            token.isdigit() or token in _LOW_INFORMATION_TITLE_TOKENS for token in candidate_tokens
        )
    )

    ratio = _sequence_ratio(candidate_norm, release_norm)
    overlap = _token_overlap_ratio(candidate_norm, release_norm)

    distinct_candidate = set(_distinctive_tokens(candidate_norm))
    distinct_release = set(_distinctive_tokens(release_norm))

    if distinct_candidate and len(distinct_candidate & distinct_release) == 0:
        return 0

    # Exact full-string match is always the strongest signal.
    if candidate_norm == release_norm:
        return _LOW_INFORMATION_TITLE_MAX_SCORE if is_low_information_candidate else 60

    # Check if the candidate appears as a phrase inside the release title.
    if candidate_norm and f" {candidate_norm} " in f" {release_norm} ":
        if is_low_information_candidate:
            return _LOW_INFORMATION_TITLE_MAX_SCORE
        # Segment check: is this an isolated title segment or just a word
        # embedded in a larger phrase (e.g. "Anarchist" in "An Anarchist
        # History of...")?  Isolated segments get full score; embedded
        # substrings are capped near the rejection threshold.
        segments = _split_release_segments(release_title)
        if _matches_any_segment(candidate_norm, segments):
            return 60
        return 24

    score = 0
    if ratio >= 0.98:
        score = 58
    elif ratio >= 0.92 and overlap >= 0.55:
        score = 52
    elif ratio >= 0.85 and overlap >= 0.45:
        score = 44
    elif ratio >= 0.78 and overlap >= 0.35:
        score = 34
    elif ratio >= 0.70 and overlap >= 0.25:
        score = 24

    # Generic candidates like "book one" should not dominate scoring.
    if is_low_information_candidate:
        score = min(score, _LOW_INFORMATION_TITLE_MAX_SCORE)
    return score


def _get_title_candidates(book: BookMetadata) -> list[str]:
    candidates = [book.title, book.search_title or ""]
    if book.subtitle:
        candidates.append(f"{book.title} {book.subtitle}")
    candidates.extend((book.titles_by_language or {}).values())
    return [c for c in candidates if c]


def _score_author(book: BookMetadata, release: Release) -> int:
    release_author = _extract_release_author(release)
    if not release_author:
        return 0

    candidates: list[str] = []
    if book.search_author:
        candidates.append(book.search_author)
    candidates.extend(book.authors or [])

    release_author_candidates = _author_variants(release_author)
    if not release_author_candidates:
        return 0

    ratio = max(_best_ratio(candidates, variant) for variant in release_author_candidates)
    if ratio >= 0.98:
        return 30
    if ratio >= 0.9:
        return 24
    if ratio >= 0.8:
        return 18
    if ratio >= 0.7:
        return 12
    if ratio >= 0.6:
        return 8
    return 0


def _get_target_series_number(book: BookMetadata) -> float | None:
    if book.series_position is not None:
        return float(book.series_position)

    for value in [book.title, book.search_title or "", book.subtitle or ""]:
        number = _extract_series_number(value)
        if number is not None:
            return number

    # Fallback: extract a bare trailing number after the series name
    # (e.g. "The Primal Hunter 15" with series_name "The Primal Hunter").
    if book.series_name:
        for value in [book.title, book.search_title]:
            if value:
                number = _extract_series_number_after_series_name(book.series_name, value)
                if number is not None:
                    return number

    return None


def _score_series_name(book: BookMetadata, release: Release) -> int:
    if not book.series_name:
        return 0
    series_norm = _normalize_text(book.series_name)
    release_norm = _normalize_text(release.title)
    if series_norm and f" {series_norm} " in f" {release_norm} ":
        return 10

    ratio = _best_ratio([book.series_name], release.title)
    if ratio >= 0.9:
        return 6
    if ratio >= 0.8:
        return 3
    return 0


def _score_series_number(book: BookMetadata, release: Release) -> int:
    target = _get_target_series_number(book)
    if target is None:
        return 0

    release_num = _extract_release_series_number(release, book.series_name)
    if release_num is None:
        return 0

    if abs(target - release_num) < 0.001:
        return 22
    if abs(target - release_num) <= 1:
        return -60
    return -75


def _score_year(book: BookMetadata, release: Release) -> int:
    if not book.publish_year:
        return 0
    release_year = _extract_release_year(release)
    if release_year is None:
        return 0

    delta = abs(int(book.publish_year) - int(release_year))
    if delta == 0:
        return 10
    if delta == 1:
        return 6
    if delta <= 2:
        return 3
    return -15


def _score_format_priority_tiebreak(release: Release, priority: dict[str, int]) -> int:
    if not priority:
        return 0

    fmt = (release.format or "").strip().lower()
    if not fmt:
        return 0

    rank = priority.get(_normalize_priority_token(fmt))
    if rank is None:
        return 0

    # Every priority step is worth +5. Higher-ranked (earlier) formats get larger boosts.
    # Capped at 15 to prevent format preferences from dominating metadata signals.
    enabled_count = len(priority)
    return min(15, max(0, (enabled_count - rank) * 5))


def _score_freeleech_direct_tiebreak(release: Release, enabled: bool) -> int:
    if not enabled:
        return 0

    is_direct_download = (release.source or "").strip().lower() == "direct_download"
    is_freeleech = (
        bool(release.extra.get("freeleech")) if isinstance(release.extra, dict) else False
    )
    if is_direct_download or is_freeleech:
        return 10
    return 0


def _normalize_priority_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _build_release_priority_map(raw_priority: object) -> dict[str, int]:
    priority: dict[str, int] = {}
    if not isinstance(raw_priority, list):
        return priority

    rank = 0
    for item in raw_priority:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue

        raw_id = str(item.get("id") or "").strip()
        if not raw_id:
            continue
        normalized = _normalize_priority_token(raw_id)
        if not normalized or normalized in priority:
            continue

        priority[normalized] = rank
        rank += 1

    return priority


def _score_indexer_priority_tiebreak(release: Release, priority: dict[str, int]) -> int:
    if not priority:
        return 0

    candidates = [
        _normalize_priority_token(f"indexer:{release.indexer or ''}"),
        _normalize_priority_token(f"source:{release.source or ''}"),
        _normalize_priority_token(release.indexer or ""),  # backward compatibility
        _normalize_priority_token(release.source or ""),  # backward compatibility
    ]

    best_rank: int | None = None
    for candidate in candidates:
        if not candidate:
            continue
        rank = priority.get(candidate)
        if rank is None:
            continue
        if best_rank is None or rank < best_rank:
            best_rank = rank

    if best_rank is None:
        return 0

    if best_rank < len(_PRIORITY_BOOST_BY_RANK):
        return _PRIORITY_BOOST_BY_RANK[best_rank]
    return 0


_scoring_config_cache: tuple[ReleaseScoringConfig, float] | None = None
_scoring_config_lock = threading.Lock()
_SCORING_CONFIG_TTL = 60.0  # seconds


def _get_release_scoring_config() -> ReleaseScoringConfig:
    global _scoring_config_cache
    now = time.monotonic()
    with _scoring_config_lock:
        if (
            _scoring_config_cache is not None
            and now - _scoring_config_cache[1] < _SCORING_CONFIG_TTL
        ):
            return _scoring_config_cache[0]

    raw_forbidden = app_config.get("RELEASE_MATCH_FORBIDDEN_TERMS", list(_FORBIDDEN_WORDS))
    forbidden_words: set[str] = set()

    if isinstance(raw_forbidden, str):
        terms = [term.strip() for term in raw_forbidden.split(",") if term.strip()]
    elif isinstance(raw_forbidden, list):
        terms = [str(term).strip() for term in raw_forbidden if str(term).strip()]
    else:
        terms = list(_FORBIDDEN_WORDS)

    for term in terms:
        normalized = _normalize_text(term)
        if normalized:
            forbidden_words.add(normalized)

    if not forbidden_words:
        forbidden_words = set(_FORBIDDEN_WORDS)

    try:
        min_title_score = int(app_config.get("RELEASE_MATCH_MIN_TITLE_SCORE", 24))
    except ValueError, TypeError:
        min_title_score = 24
    try:
        min_author_score = int(app_config.get("RELEASE_MATCH_MIN_AUTHOR_SCORE", 8))
    except ValueError, TypeError:
        min_author_score = 8
    prefer_freeleech_or_direct = bool(app_config.get("RELEASE_PREFER_FREELEECH_OR_DIRECT", False))

    ebook_release_priority = _build_release_priority_map(
        app_config.get("EBOOK_RELEASE_PRIORITY", [])
    )
    audiobook_release_priority = _build_release_priority_map(
        app_config.get("AUDIOBOOK_RELEASE_PRIORITY", [])
    )
    ebook_format_priority = _build_release_priority_map(app_config.get("EBOOK_FORMAT_PRIORITY", []))
    audiobook_format_priority = _build_release_priority_map(
        app_config.get("AUDIOBOOK_FORMAT_PRIORITY", [])
    )

    # Backward compatibility with initial setting key from early rollout.
    if not audiobook_release_priority:
        audiobook_release_priority = _build_release_priority_map(
            app_config.get("AUDIOBOOK_INDEXER_PRIORITY", [])
        )

    config = ReleaseScoringConfig(
        forbidden_words=forbidden_words,
        min_title_score=max(0, min(60, min_title_score)),
        min_author_score=max(0, min(30, min_author_score)),
        prefer_freeleech_or_direct=prefer_freeleech_or_direct,
        ebook_release_priority=ebook_release_priority,
        audiobook_release_priority=audiobook_release_priority,
        ebook_format_priority=ebook_format_priority,
        audiobook_format_priority=audiobook_format_priority,
    )
    with _scoring_config_lock:
        _scoring_config_cache = (config, time.monotonic())
    return config


def score_release_match(
    book: BookMetadata,
    release: Release,
) -> ReleaseMatchScore:
    # 1. Read preferences from settings
    cfg = _get_release_scoring_config()
    forbidden_words = cfg.forbidden_words
    min_title_score = cfg.min_title_score
    min_author_score = cfg.min_author_score
    prefer_freeleech_direct = cfg.prefer_freeleech_or_direct
    content_type = (release.content_type or "ebook").strip().lower()
    release_priority_map = (
        cfg.audiobook_release_priority
        if content_type == "audiobook"
        else cfg.ebook_release_priority
    )
    format_priority_map = (
        cfg.audiobook_format_priority if content_type == "audiobook" else cfg.ebook_format_priority
    )

    # 2. Hard rejects
    title_norm = _normalize_text(release.title)
    for forbidden in forbidden_words:
        if forbidden in title_norm:
            return ReleaseMatchScore(
                score=0,
                breakdown={"forbidden": -100},
                confidence="none",
                hard_reject=True,
                reject_reason=f"forbidden:{forbidden}",
            )

    title_candidates = _get_title_candidates(book)
    if not title_candidates:
        return ReleaseMatchScore(
            score=0,
            breakdown={"title": 0, "author": 0},
            confidence="none",
            hard_reject=True,
            reject_reason="no_title_candidates",
        )
    title_score = max(_score_single_title_candidate(c, release.title) for c in title_candidates)
    author_score = _score_author(book, release)
    has_release_author = bool(_extract_release_author(release))
    release_distinct = set(_distinctive_tokens(release.title))
    has_distinctive_title_overlap = bool(release_distinct) and any(
        set(_distinctive_tokens(c)) & release_distinct
        for c in title_candidates
        if _distinctive_tokens(c)
    )

    # Guardrail: if title match is only coming from low-information tokens
    # (e.g. "book six") with no distinctive overlap, reject as unrelated.
    if title_score <= _LOW_INFORMATION_TITLE_MAX_SCORE and not has_distinctive_title_overlap:
        return ReleaseMatchScore(
            score=title_score + author_score,
            breakdown={"title": title_score, "author": author_score},
            confidence="none",
            hard_reject=True,
            reject_reason="low_information_title_match",
        )

    if title_score < min_title_score:
        return ReleaseMatchScore(
            score=title_score + author_score,
            breakdown={"title": title_score, "author": author_score},
            confidence="none",
            hard_reject=True,
            reject_reason="low_title_match",
        )

    # Fallback: if author scoring is low (possibly because _extract_release_author
    # grabbed the wrong part of the title), check if a known author name appears
    # as an isolated segment in the release title.
    if (book.authors or book.search_author) and author_score < min_author_score:
        segment_author_score = _check_author_in_segments(book, release.title)
        if segment_author_score > author_score:
            author_score = segment_author_score

    # Only hard-reject author mismatch when release actually provides author info.
    # If author is missing from a source payload, treat it as unknown/neutral.
    if (
        (book.authors or book.search_author)
        and has_release_author
        and author_score < min_author_score
    ):
        return ReleaseMatchScore(
            score=max(0, title_score + author_score - 20),
            breakdown={
                "title": title_score,
                "author": author_score,
                "author_mismatch_penalty": -20,
            },
            confidence="none",
            hard_reject=True,
            reject_reason="low_author_match",
        )

    # 3. Additive scoring
    series_score = _score_series_name(book, release)
    series_num_score = _score_series_number(book, release)

    # Series number only has meaning when series name also matches.
    # e.g. "Book 1" should not help if the release is from a different series.
    if series_score <= 0:
        series_num_score = 0

    # Hard reject: if both series numbers are confidently extracted and the
    # series name matches but the numbers differ, this is the wrong book
    # (e.g. "Primal Hunter 15" vs "Primal Hunter 10").  No amount of
    # title/author similarity should rescue a wrong series position.
    if series_score > 0 and series_num_score < 0:
        return ReleaseMatchScore(
            score=max(0, title_score + author_score + series_score + series_num_score),
            breakdown={
                "title": title_score,
                "author": author_score,
                "series": series_score,
                "series_number": series_num_score,
            },
            confidence="none",
            hard_reject=True,
            reject_reason="series_number_mismatch",
        )

    # Hard reject: series name matches and the book has a specific position
    # (> 1), but NO series number is extractable from the release.  A release
    # titled just "The Primal Hunter" when we want book 15 is almost certainly
    # book 1 or an omnibus — not the target.
    #
    # Exception: when the book title is distinct from the series name (e.g.
    # "Deceptions" in series "Ascendant"), the title alone identifies the book
    # and the missing series number is not dangerous.
    if series_score > 0 and series_num_score == 0:
        target = _get_target_series_number(book)
        if target is not None and target > 1:
            series_norm = _normalize_text(book.series_name or "")
            title_is_distinct = series_norm and all(
                series_norm not in _normalize_text(c) for c in title_candidates
            )
            release_num = _extract_release_series_number(release, book.series_name)
            if release_num is None and not title_is_distinct:
                return ReleaseMatchScore(
                    score=max(0, title_score + author_score + series_score),
                    breakdown={
                        "title": title_score,
                        "author": author_score,
                        "series": series_score,
                        "series_number": 0,
                    },
                    confidence="none",
                    hard_reject=True,
                    reject_reason="series_number_missing",
                )

    should_use_year = title_score >= 34 or (series_score > 0 and series_num_score > 0)
    year_score = _score_year(book, release) if should_use_year else 0

    # Tie-break bonuses should not rescue weak metadata matches.
    has_strong_metadata = title_score >= 34 or (series_score >= 10 and series_num_score > 0)
    freeleech_direct_score = (
        _score_freeleech_direct_tiebreak(release, prefer_freeleech_direct)
        if has_strong_metadata
        else 0
    )
    indexer_priority_score = (
        _score_indexer_priority_tiebreak(release, release_priority_map)
        if has_strong_metadata
        else 0
    )
    format_priority_score = (
        _score_format_priority_tiebreak(release, format_priority_map) if has_strong_metadata else 0
    )

    total = max(
        0,
        title_score
        + author_score
        + series_score
        + series_num_score
        + year_score
        + format_priority_score
        + freeleech_direct_score
        + indexer_priority_score,
    )

    if total >= 75:
        confidence = "high"
    elif total >= 60:
        confidence = "medium"
    elif total >= 45:
        confidence = "low"
    else:
        confidence = "none"

    return ReleaseMatchScore(
        score=total,
        confidence=confidence,
        breakdown={
            "title": title_score,
            "author": author_score,
            "series": series_score,
            "series_number": series_num_score,
            "year": year_score,
            "format_priority": format_priority_score,
            "freeleech_or_direct": freeleech_direct_score,
            "indexer_priority": indexer_priority_score,
        },
    )


def rank_releases_for_book(
    book: BookMetadata, releases: list[Release]
) -> list[tuple[Release, ReleaseMatchScore]]:
    scored: list[tuple[Release, ReleaseMatchScore]] = []
    for release in releases:
        match = score_release_match(book, release)
        if not isinstance(release.extra, dict):
            release.extra = {}
        release.extra["match_score"] = match.score
        release.extra["match_confidence"] = match.confidence
        release.extra["match_breakdown"] = match.breakdown
        if match.hard_reject:
            release.extra["match_reject_reason"] = match.reject_reason
        scored.append((release, match))

    scored.sort(key=lambda item: item[1].score, reverse=True)
    return scored


# =============================================================================
# Release Date Utilities
# =============================================================================


def parse_release_date(value: Any) -> date | None:
    """Parse release date values from API/search payloads."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    raw = str(value or "").strip()
    if not raw:
        return None

    token = raw
    if "T" in token:
        token = token.split("T", 1)[0]
    elif " " in token:
        token = token.split(" ", 1)[0]

    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def is_book_released(release_date: Any) -> tuple[bool, date | None]:
    """Check if a book has been released based on its release date.

    Returns:
        Tuple of (is_released, parsed_date).
        is_released is True if no date or date is in the past.
    """
    parsed = parse_release_date(release_date)
    if parsed is None:
        return True, None  # No date = assume released

    today = datetime.now(UTC).date()
    return parsed <= today, parsed


# =============================================================================
# Pre-Processing: Filter and Rank Releases
# =============================================================================


def pre_process_releases(
    releases: list[dict[str, Any]],
    *,
    user_db: Any = None,  # MonitoredDB instance; kept as user_db for call-site compatibility
    user_id: int,
    entity_id: int,
    provider: str,
    provider_book_id: str,
    content_type: str = "ebook",
    min_match_score: float | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Pre-process releases for a monitored book before queuing.

    Filters releases by:
    1. Release date (must be released)
    2. Match score cutoff
    3. Previous failed attempts (deprioritize but don't exclude)

    Args:
        releases: List of release dicts from search
        user_db: UserDB instance for failed-source lookup (optional)
        user_id: Current user ID
        entity_id: Monitored entity ID
        provider: Book provider (e.g., 'hardcover')
        provider_book_id: Provider's book ID
        content_type: 'ebook' or 'audiobook'
        min_match_score: Minimum match score cutoff (uses config default if None)

    Returns:
        Tuple of (valid_releases, rejection_reason).
        valid_releases is sorted by score (highest first), with failed attempts last.
        rejection_reason is set if no valid releases found.
    """
    if not releases:
        return [], "No releases found"

    if min_match_score is None:
        try:
            min_match_score = float(
                app_config.get("AUTO_DOWNLOAD_MIN_MATCH_SCORE", 75, user_id=user_id)
            )
        except ValueError, TypeError:
            min_match_score = 75.0

    valid_releases: list[dict[str, Any]] = []
    unreleased_count = 0
    below_cutoff_count = 0
    cross_type_count = 0

    failed_source_pairs: set[tuple[str, str]] = set()
    if user_db is not None:
        try:
            failed_source_pairs = user_db.list_monitored_failed_candidate_source_ids(
                user_ids=[user_id],
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get failed source IDs: %s", e)

    from shelfmark.core.monitored_utils import release_matches_content_type

    for release in releases:
        # Defence-in-depth: drop releases whose format/content_type belongs to
        # the wrong family. fetch_book_releases applies the same filter, so this
        # is a no-op for the scheduled path; it protects any future caller that
        # bypasses fetch_book_releases.
        if not release_matches_content_type(release, content_type):
            cross_type_count += 1
            logger.debug(
                "Skipping cross-type release: %s (format=%s, content_type=%s, requested=%s)",
                release.get("title"),
                release.get("format"),
                release.get("content_type"),
                content_type,
            )
            continue

        extra = release.get("extra") or {}
        release_date = (
            release.get("release_date")
            or extra.get("release_date")
            or extra.get("publication_date")
        )
        is_released, parsed_date = is_book_released(release_date)
        if not is_released:
            unreleased_count += 1
            logger.debug("Skipping unreleased: %s (releases %s)", release.get("title"), parsed_date)
            continue

        match_score = release.get("match_score") or extra.get("match_score")
        try:
            score = float(match_score) if match_score is not None else 0.0
        except TypeError, ValueError:
            score = 0.0

        if score < min_match_score:
            below_cutoff_count += 1
            logger.debug(
                "Skipping below cutoff: %s (score %.2f < %.2f)",
                release.get("title"),
                score,
                min_match_score,
            )
            continue

        src = str(release.get("source", "")).strip()
        src_id = str(release.get("source_id", "")).strip()
        release["_previously_failed"] = bool(
            src and src_id and (src, src_id) in failed_source_pairs
        )
        release["_match_score"] = score
        valid_releases.append(release)

    if not valid_releases:
        if unreleased_count > 0 and below_cutoff_count == 0 and cross_type_count == 0:
            return [], "Book is unreleased"
        if below_cutoff_count > 0:
            return [], f"No releases meet minimum match score ({min_match_score:.0f})"
        return [], "No valid releases found"

    valid_releases.sort(
        key=lambda r: (not r.get("_previously_failed", False), r.get("_match_score", 0)),
        reverse=True,
    )

    logger.info(
        "Pre-processed %d releases: %d valid, %d unreleased, %d below cutoff, %d cross-type, %d previously failed",
        len(releases),
        len(valid_releases),
        unreleased_count,
        below_cutoff_count,
        cross_type_count,
        sum(1 for r in valid_releases if r.get("_previously_failed")),
    )

    return valid_releases, None
