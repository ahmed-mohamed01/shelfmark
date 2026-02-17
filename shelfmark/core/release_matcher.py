from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shelfmark.core.config import config as app_config
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release


_WORD_NUMBER_MAP: Dict[str, float] = {
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

_ROMAN_MAP: Dict[str, float] = {
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
    r"([0-9]+(?:\.[0-9]+)?|[ivx]+|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
)

# Stronger priority boosts so preferred sources/indexers win close calls.
# Rank 0 => +12, rank 1 => +9, rank 2 => +6, rank 3 => +3, rank 4 => +1.
_PRIORITY_BOOST_BY_RANK = [12, 9, 6, 3, 1]


@dataclass
class ReleaseMatchScore:
    score: int
    breakdown: Dict[str, int]
    confidence: str
    hard_reject: bool = False
    reject_reason: Optional[str] = None


@dataclass
class ReleaseScoringConfig:
    forbidden_words: set[str]
    min_title_score: int
    min_author_score: int
    prefer_freeleech_or_direct: bool
    ebook_release_priority: Dict[str, int]
    audiobook_release_priority: Dict[str, int]
    ebook_format_priority: Dict[str, int]
    audiobook_format_priority: Dict[str, int]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def _tokens(value: str) -> List[str]:
    normalized = _normalize_text(value)
    return [token for token in normalized.split(" ") if token]


def _distinctive_tokens(value: str) -> List[str]:
    return [
        token
        for token in _tokens(value)
        if len(token) > 2 and token not in _GENERIC_TITLE_TOKENS
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


def _author_variants(value: str) -> List[str]:
    """Extract plausible author fragments from noisy source strings."""
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return []

    variants: List[str] = [raw]
    for part in re.split(r"\s*(?:,|;|\||/|&|\band\b)\s*", raw, flags=re.IGNORECASE):
        token = part.strip()
        if token and token not in variants:
            variants.append(token)
    return variants


def _extract_release_year(release: Release) -> Optional[int]:
    value = release.extra.get("year") if isinstance(release.extra, dict) else None
    if value is not None:
        match = re.search(r"(19\d{2}|20\d{2})", str(value))
        if match:
            return int(match.group(1))

    match = re.search(r"(19\d{2}|20\d{2})", release.title)
    if match:
        return int(match.group(1))

    return None


def _word_to_number(token: str) -> Optional[float]:
    if not token:
        return None
    if token in _WORD_NUMBER_MAP:
        return _WORD_NUMBER_MAP[token]
    if token in _ROMAN_MAP:
        return _ROMAN_MAP[token]
    try:
        return float(token)
    except Exception:
        return None


def _extract_series_number(text: str) -> Optional[float]:
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
            return _word_to_number(match.group(1))

    # Fallback on normalized text in case symbols were stripped
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return _word_to_number(match.group(1))

    return None


def _score_single_title_candidate(candidate: str, release_title: str) -> int:
    candidate_norm = _normalize_text(candidate)
    release_norm = _normalize_text(release_title)
    if not candidate_norm or not release_norm:
        return 0

    candidate_tokens = _tokens(candidate_norm)
    is_low_information_candidate = (
        bool(candidate_tokens)
        and len(candidate_tokens) <= 3
        and all(token.isdigit() or token in _LOW_INFORMATION_TITLE_TOKENS for token in candidate_tokens)
    )

    ratio = _sequence_ratio(candidate_norm, release_norm)
    overlap = _token_overlap_ratio(candidate_norm, release_norm)

    distinct_candidate = set(_distinctive_tokens(candidate_norm))
    distinct_release = set(_distinctive_tokens(release_norm))

    if distinct_candidate and len(distinct_candidate & distinct_release) == 0:
        return 0

    # If the canonical metadata title appears as a full phrase inside the
    # release title, treat it as a top-quality title match.
    if candidate_norm == release_norm:
        return _LOW_INFORMATION_TITLE_MAX_SCORE if is_low_information_candidate else 60
    if candidate_norm and f" {candidate_norm} " in f" {release_norm} ":
        return _LOW_INFORMATION_TITLE_MAX_SCORE if is_low_information_candidate else 60

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


def _score_title(book: BookMetadata, release: Release) -> int:
    candidates = [book.title, book.search_title or ""]
    if book.subtitle:
        candidates.append(f"{book.title} {book.subtitle}")
    candidates.extend(list((book.titles_by_language or {}).values()))

    return max(_score_single_title_candidate(candidate, release.title) for candidate in candidates if candidate)


def _score_author(book: BookMetadata, release: Release) -> int:
    release_author = _extract_release_author(release)
    if not release_author:
        return 0

    candidates: List[str] = []
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


def _has_release_author_signal(release: Release) -> bool:
    author = _extract_release_author(release)
    return bool(author)


def _get_target_series_number(book: BookMetadata) -> Optional[float]:
    if book.series_position is not None:
        return float(book.series_position)

    for value in [book.title, book.search_title or "", book.subtitle or ""]:
        number = _extract_series_number(value)
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

    release_num = _extract_series_number(release.title)
    if release_num is None:
        return 0

    if abs(target - release_num) < 0.001:
        return 22
    if abs(target - release_num) <= 1:
        return -10
    return -35


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
    return -5


def _score_format_priority_tiebreak(release: Release, priority: Dict[str, int]) -> int:
    if not priority:
        return 0

    fmt = (release.format or "").strip().lower()
    if not fmt:
        return 0

    rank = priority.get(_normalize_priority_token(fmt))
    if rank is None:
        return 0

    # Every priority step is worth +5. Higher-ranked (earlier) formats get larger boosts.
    enabled_count = len(priority)
    return max(0, (enabled_count - rank) * 5)


def _score_freeleech_direct_tiebreak(release: Release, enabled: bool) -> int:
    if not enabled:
        return 0

    is_direct_download = (release.source or "").strip().lower() == "direct_download"
    is_freeleech = bool(release.extra.get("freeleech")) if isinstance(release.extra, dict) else False
    if is_direct_download or is_freeleech:
        return 10
    return 0


def _normalize_priority_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _build_release_priority_map(raw_priority: object) -> Dict[str, int]:
    priority: Dict[str, int] = {}
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


def _score_indexer_priority_tiebreak(release: Release, priority: Dict[str, int]) -> int:
    if not priority:
        return 0

    candidates = [
        _normalize_priority_token(f"indexer:{release.indexer or ''}"),
        _normalize_priority_token(f"source:{release.source or ''}"),
        _normalize_priority_token(release.indexer or ""),  # backward compatibility
        _normalize_priority_token(release.source or ""),   # backward compatibility
    ]

    best_rank: Optional[int] = None
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


def _get_release_scoring_config() -> ReleaseScoringConfig:
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

    min_title_score = int(app_config.get("RELEASE_MATCH_MIN_TITLE_SCORE", 24))
    min_author_score = int(app_config.get("RELEASE_MATCH_MIN_AUTHOR_SCORE", 8))
    prefer_freeleech_or_direct = bool(app_config.get("RELEASE_PREFER_FREELEECH_OR_DIRECT", False))

    ebook_release_priority = _build_release_priority_map(app_config.get("EBOOK_RELEASE_PRIORITY", []))
    audiobook_release_priority = _build_release_priority_map(app_config.get("AUDIOBOOK_RELEASE_PRIORITY", []))
    ebook_format_priority = _build_release_priority_map(app_config.get("EBOOK_FORMAT_PRIORITY", []))
    audiobook_format_priority = _build_release_priority_map(app_config.get("AUDIOBOOK_FORMAT_PRIORITY", []))

    # Backward compatibility with initial setting key from early rollout.
    if not audiobook_release_priority:
        audiobook_release_priority = _build_release_priority_map(
            app_config.get("AUDIOBOOK_INDEXER_PRIORITY", [])
        )

    return ReleaseScoringConfig(
        forbidden_words=forbidden_words,
        min_title_score=max(0, min(60, min_title_score)),
        min_author_score=max(0, min(30, min_author_score)),
        prefer_freeleech_or_direct=prefer_freeleech_or_direct,
        ebook_release_priority=ebook_release_priority,
        audiobook_release_priority=audiobook_release_priority,
        ebook_format_priority=ebook_format_priority,
        audiobook_format_priority=audiobook_format_priority,
    )


def score_release_match(book: BookMetadata, release: Release) -> ReleaseMatchScore:
    scoring_config = _get_release_scoring_config()
    title_norm = _normalize_text(release.title)
    for forbidden in scoring_config.forbidden_words:
        if forbidden in title_norm:
            return ReleaseMatchScore(
                score=0,
                breakdown={"forbidden": -100},
                confidence="none",
                hard_reject=True,
                reject_reason=f"forbidden:{forbidden}",
            )

    title_score = _score_title(book, release)
    author_score = _score_author(book, release)
    has_release_author = _has_release_author_signal(release)

    if title_score < scoring_config.min_title_score:
        return ReleaseMatchScore(
            score=title_score + author_score,
            breakdown={"title": title_score, "author": author_score},
            confidence="none",
            hard_reject=True,
            reject_reason="low_title_match",
        )

    # Only hard-reject author mismatch when release actually provides author info.
    # If author is missing from a source payload, treat it as unknown/neutral.
    if (
        (book.authors or book.search_author)
        and has_release_author
        and author_score < scoring_config.min_author_score
    ):
        return ReleaseMatchScore(
            score=max(0, title_score + author_score - 20),
            breakdown={"title": title_score, "author": author_score, "author_mismatch_penalty": -20},
            confidence="none",
            hard_reject=True,
            reject_reason="low_author_match",
        )

    series_score = _score_series_name(book, release)
    series_num_score = _score_series_number(book, release)

    # Series number only has meaning when series name also matches.
    # e.g. "Book 1" should not help if the release is from a different series.
    if series_score <= 0:
        series_num_score = 0

    should_use_year = title_score >= 34 or (series_score > 0 and series_num_score > 0)
    year_score = _score_year(book, release) if should_use_year else 0

    # Tie-break bonuses should not rescue weak metadata matches.
    has_strong_metadata = title_score >= 34 or (series_score >= 10 and series_num_score > 0)
    freeleech_direct_score = (
        _score_freeleech_direct_tiebreak(release, scoring_config.prefer_freeleech_or_direct)
        if has_strong_metadata
        else 0
    )
    content_type = (release.content_type or "ebook").strip().lower()
    release_priority_map = (
        scoring_config.audiobook_release_priority
        if content_type == "audiobook"
        else scoring_config.ebook_release_priority
    )
    format_priority_map = (
        scoring_config.audiobook_format_priority
        if content_type == "audiobook"
        else scoring_config.ebook_format_priority
    )
    indexer_priority_score = (
        _score_indexer_priority_tiebreak(release, release_priority_map)
        if has_strong_metadata
        else 0
    )
    format_priority_score = (
        _score_format_priority_tiebreak(release, format_priority_map)
        if has_strong_metadata
        else 0
    )

    total = max(
        0,
        min(
            100,
            title_score
            + author_score
            + series_score
            + series_num_score
            + year_score
            + format_priority_score
            + freeleech_direct_score
            + indexer_priority_score,
        ),
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


def rank_releases_for_book(book: BookMetadata, releases: List[Release]) -> List[Tuple[Release, ReleaseMatchScore]]:
    scored: List[Tuple[Release, ReleaseMatchScore]] = []
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
