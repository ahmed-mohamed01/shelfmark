"""Structured file→book attribution for monitored libraries (v2).

Replaces the fuzzy-only matcher with an evidence-counting model:

  - Decompose the file path into author/series/book/leaf components.
  - Extract weighted series_position votes from the filename + folders.
  - Extract a "title core" (filename stripped of position/year/author/series prefix).
  - Read embedded metadata when available (EPUB OPF, M4B/MP3 tags).
  - Score each (file, candidate_book) pair by tallying agreement vs. contradiction
    across these typed signals, not by a single fuzzy ratio.

Design principles:
  - Evidence beats fuzz. A path that says "(Book 4)" must not attach to book #1
    no matter how high the title fuzz, unless other identifier evidence (ISBN/ASIN)
    explicitly overrides.
  - Trust embedded file metadata over the filename when they disagree.
  - Confidence is fraction-of-evidence-agreed, not a fuzzy ratio.
  - Pure functions, no DB writes. Caller decides whether to persist.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Iterable

from shelfmark.core.monitored_files import normalize_match_text

# ---------------------------------------------------------------------------
# Tunables — initial values; revisit during fixture validation
# ---------------------------------------------------------------------------

ACCEPT_NET_SCORE_FLOOR = 1.5

# Positive contributions
W_TITLE_CORE_HIGH = 1.0  # title core fuzz >= 0.85
W_TITLE_CORE_MED = 0.6  # 0.70 <= fuzz < 0.85
W_TITLE_CORE_LOW = 0.3  # 0.55 <= fuzz < 0.70
W_AUTHOR_FOLDER = 0.8
W_AUTHOR_TRAILER = 0.4
W_SERIES_FOLDER = 0.8
W_SERIES_IN_FILENAME = 0.4
W_POSITION_AGREE_HIGH = 1.0
W_POSITION_AGREE_MED = 0.5
W_IDENTIFIER_MATCH = 2.0  # ISBN/ASIN match — strongest single positive
W_EMBEDDED_TITLE_AGREE = 1.0  # title fuzz >= TITLE_CORE_HIGH (0.85)
W_EMBEDDED_TITLE_AGREE_MED = 0.6  # 0.70 <= fuzz < 0.85
W_EMBEDDED_TITLE_AGREE_LOW = 0.3  # 0.55 <= fuzz < 0.70
W_EMBEDDED_SERIES_AGREE = 1.0
# Position-only match: metadata's series_position == book's series_position,
# but series_name doesn't fuzz-match (common across ABS/Booklore/Hardcover).
# Weaker than series_agree because we have one corroborating signal instead
# of two, but still useful for tier classification.
W_EMBEDDED_POSITION_MATCH = 0.6
W_EMBEDDED_AUTHOR_AGREE = 0.6  # metadata author matches entity author (any source)

# Soft penalties
P_POSITION_DISAGREE_HIGH = 0.50
P_POSITION_DISAGREE_MED = 0.25
P_EMBEDDED_POSITION_DISAGREE = 0.50
P_WRONG_AUTHOR_FOLDER = 0.40
# Title-mismatch penalty fires when both sides have a title and the fuzz is
# below TITLE_CORE_MISMATCH. Without this, two books in the same series with
# the same author folder collect ~1.6 net score (author_folder + series_folder)
# and pass the floor even though their titles share almost nothing.
P_TITLE_MISMATCH = 0.6

# Thresholds
# Lowered from 0.85 → 0.75 to handle dot/space variants like "DennisETaylor"
# matching "Dennis E. Taylor". Author folders are also normalized (dots/
# whitespace collapsed) before comparison.
AUTHOR_FUZZ_THRESHOLD = 0.75
SERIES_NAME_FUZZ_THRESHOLD = 0.75
TITLE_CORE_HIGH = 0.85
TITLE_CORE_MED = 0.70
TITLE_CORE_LOW = 0.55
# Below this, the title is considered actively wrong (not just incomplete).
# Aligned with TITLE_CORE_LOW so the regions are continuous: at or above LOW
# means "similar enough to count as a positive signal", anything below means
# "actively different — penalise." Without this, fuzz in [0.40, 0.55) fell in
# a dead zone where no title evidence fired in either direction, letting
# strong series+position signals push obviously-wrong matches above the floor
# (e.g. "Daughters War (Unabridged)" vs "Thrice-Bound Fool" fuzz=0.41 — both
# in the same Blacktongue series with mis-tagged position metadata).
TITLE_CORE_MISMATCH = 0.55


# ---------------------------------------------------------------------------
# Constants & regexes
# ---------------------------------------------------------------------------

# Volume-marker regex: catches "Book N", "Vol N", "Volume N", "Arc N", "Part N",
# "Tome N", and "#N". Allows decimal (e.g. "Book 1.5"). Case-insensitive at use.
_EXPLICIT_VOL_RE = re.compile(
    r"\b(arc|book|vol(?:ume)?|part|tome)\s*[-:#]?\s*(\d{1,3}(?:\.\d+)?)\b"
    r"|#\s*(\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)

# Leading "NN." or "NN -" or "NN_" at the start of a name component
_LEADING_NUM_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*[\.\-\_]\s+")

# Bare standalone number token mid/end of a name (1-3 digits, no decimal here —
# decimals are handled by _DECIMAL_RE).
# Excludes letters as boundary characters so digits embedded in alphanumeric
# IDs ("B0B75MS6F3", catalog codes, model numbers) don't emit phantom votes.
_BARE_NUM_RE = re.compile(r"(?<![\d\.A-Za-z])(\d{1,3})(?![\d\.A-Za-z])")

# Standalone decimal token (e.g. "1.5") — must NOT be inside a longer
# digit/period run like "101.201".
_DECIMAL_RE = re.compile(r"(?<![\d\.])(\d{1,3}\.\d{1,3})(?![\d\.])")

# Roman numerals in marker context — "Book IV", "Vol III"
_ROMAN_RE = re.compile(
    r"\b(?:book|vol(?:ume)?|part)\s+([ivxlcdm]+)\b",
    re.IGNORECASE,
)

# Year in parens — strip from title core
_YEAR_PAREN_RE = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")

# Bracketed marker — ASIN codes ([B0B75MS6F3]), edition markers ([Unabridged],
# [GA] for Graphicaudio, etc.). Strip non-year brackets the same way we strip
# non-year parens; leaves "[2024]" alone in case a year is bracketed.
_BRACKET_ANY_RE = re.compile(r"\s*\[[^\]]*\]\s*")

# Generic parenthetical that doesn't contain a 4-digit year or a book-marker —
# used to strip edition markers like "(Illustrated Edition)" from title core.
_PAREN_ANY_RE = re.compile(r"\s*\([^)]*\)\s*")

# Word-number map (one..ten → 1..10)
_WORD_NUMBER_MAP = {
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
}

# ISBN / ASIN cleanup
_ISBN_CLEAN_RE = re.compile(r"[^0-9Xx]")
_ASIN_RE = re.compile(r"^B[0-9A-Z]{9}$")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionVote:
    """One extracted series_position vote with its source and weight."""

    value: float
    weight: str  # "high" | "medium" | "low"
    source: str  # e.g. "leading_num_filename", "explicit_book_marker", "bare_trailing"


@dataclass(frozen=True)
class PathDecomposition:
    """Typed components of a file path."""

    leaf: str  # filename or leaf-folder name (no separators)
    leaf_is_file: bool  # True if leaf has a file extension
    ext: str  # file extension lowercased (".epub" etc.) or ""
    book_folder: str | None  # immediate parent dir name
    series_folder: str | None  # dir above book_folder
    author_folder: str | None  # dir above series_folder
    full_path: str


@dataclass
class EmbeddedMetadata:
    """Metadata read from inside an EPUB/M4B/MP3 file."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    series_name: str | None = None
    series_position: float | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    asin: str | None = None
    year: int | None = None


@dataclass
class SourceMetadata:
    """Curated book metadata from an external source (ABS / Booklore / …).

    Treated as a high-trust evidence input alongside embedded file metadata.
    Empty fields contribute nothing — same as if the field were absent.

    ``source_label`` is propagated into match_reason / log lines so the UI can
    show "abs_match" / "booklore_match" / etc.

    ``all_series_pairs`` carries every (series_name, position) the source
    knows about — ABS commonly returns "Stormlight Archive #5, Cosmere #19"
    when a book belongs to multiple series. The scorer iterates every pair
    against the book's own ``all_series`` so multi-series numbering
    differences across catalogs don't trigger spurious position-disagree
    penalties. ``series_name`` / ``series_position`` (singular) remain as
    the "primary" the source picked — used only when ``all_series_pairs``
    is empty (back-compat).
    """

    title: str | None = None
    author: str | None = None
    series_name: str | None = None
    series_position: float | None = None
    all_series_pairs: list[tuple[str, float]] = field(default_factory=list)
    isbn_13: str | None = None
    isbn_10: str | None = None
    asin: str | None = None
    source_label: str = ""


@dataclass
class AttributionEvidence:
    """Per-(file, book) evidence vector. Persisted to evidence_json column."""

    # Score components
    net_score: float = 0.0
    confidence: float = 0.0  # 0..1, derived from net_score
    accept: bool = False

    # Signal breakdown — used for the UI "Why?" panel and for tests
    title_core: str = ""
    title_core_fuzz: float = 0.0
    author_folder_match: bool = False
    author_folder_ratio: float = 0.0
    author_trailer_match: bool = False
    series_folder_match: bool = False
    series_folder_ratio: float = 0.0
    series_in_filename: bool = False

    position_votes: list[dict[str, Any]] = field(default_factory=list)
    position_agree_high: bool = False
    position_agree_med: bool = False
    position_disagree_high: bool = False
    position_disagree_med: bool = False

    embedded_metadata_used: bool = False
    # Verbatim values read from the file, surfaced to the UI so users can see
    # exactly what the EPUB/M4B claimed. None when the format wasn't readable
    # or the field was missing. (Per-signal agree/disagree booleans were
    # removed — the same information is in positives/penalties arrays.)
    embedded_data: dict[str, Any] = field(default_factory=dict)

    # Same shape for external metadata supplied by an integration (ABS/Booklore).
    source_metadata_used: bool = False
    source_data: dict[str, Any] = field(default_factory=dict)

    hard_reject: bool = False
    hard_reject_reason: str = ""

    # Three-tier classification: "confirmed" → auto-accept and counts toward
    # "book is owned"; "candidate" → surfaces in the Possible Candidates UI
    # for user Accept/Reject; "rejected" → not surfaced. Set by the final
    # decision block in evaluate_match. ``accept`` is kept as a convenience
    # bool == (tier == "confirmed").
    tier: str = "rejected"

    # Positive / penalty itemization for the UI
    positives: list[dict[str, Any]] = field(default_factory=list)
    penalties: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AttributionResult:
    """Final attribution decision."""

    book: dict[str, Any] | None  # the chosen book row, or None on reject
    confidence: float
    evidence: AttributionEvidence
    match_reason: str  # e.g. "v2_structured", "v2_identifier",
    # "abs_match", "booklore_match",
    # "abs_match_candidate", "v2_no_candidate"
    tier: str = "rejected"  # "confirmed" | "candidate" | "rejected"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Strict normalisation: lowercase, alnum-only, single-spaced."""
    return normalize_match_text(s or "")


def _flexible_word_pattern(s: str) -> str:
    """Build a regex that matches `s` with flexible whitespace between word tokens.

    Example: "Rise of the Living Forge" -> r"Rise\\s+of\\s+the\\s+Living\\s+Forge"
    (where `\\s+` is the regex whitespace class — the doubled backslash is just
    Python string syntax for a single backslash in the regex).
    """
    tokens = (s or "").split()
    if not tokens:
        return ""
    return r"\s+".join(re.escape(t) for t in tokens)


@lru_cache(maxsize=2048)
def _author_name_variants_cached(author: str) -> tuple[str, ...]:
    """Cached, immutable form of _author_name_variants for hot-path callers."""
    return tuple(_author_name_variants(author))


@lru_cache(maxsize=4096)
def _flexible_tolerant_pattern_cached(s: str) -> str:
    """Cached form of _flexible_tolerant_pattern.

    `s` is typically an author name, a series name, or one of a small
    number of author-name variants — the same handful of strings is
    re-tokenized for every (file × book) pair during a scan, so caching
    eliminates 90%+ of the tokenization work.
    """
    return _flexible_tolerant_pattern(s)


def _author_name_variants(author: str) -> list[str]:
    """Generate common surface variants of an author name.

    Catalog sources represent author names in several conventions; each
    variant is a string we expect might appear inside a title field:

      * "First Middle Last"  → as given
      * "Last, First Middle" → bibliographic "surname-first" form
      * "Last, First"        → "Last, First" without middle
      * "First M. Last"      → middle as single initial
      * "F. Last"            → first as single initial
      * "Last, F."           → bibliographic with first as initial

    Returns the original PLUS each derived variant; callers iterate them as
    candidate patterns for stripping. Single-name inputs return just the
    original. Output preserves whatever dots/spaces the caller supplied —
    pattern building tolerates separator variation downstream.
    """
    if not author or not author.strip():
        return []
    raw = author.strip()
    tokens = [t for t in re.split(r"\s+", raw) if t]
    if not tokens:
        return []
    if len(tokens) == 1:
        return [raw]

    # Drop trailing dots when extracting bare token names (so "E." → "E").
    bare = [t.rstrip(".") for t in tokens]
    first = bare[0]
    last = bare[-1]
    middle = bare[1:-1]

    out: list[str] = [raw]
    seen: set[str] = {_norm(raw)}

    def _add(v: str) -> None:
        v = v.strip()
        if not v:
            return
        norm = _norm(v)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(v)

    middle_str = " ".join(middle)
    middle_initials = " ".join(f"{m[0]}." for m in middle if m)

    # "Last, First Middle"
    if middle_str:
        _add(f"{last}, {first} {middle_str}")
    _add(f"{last}, {first}")

    # Initial forms
    if middle:
        _add(f"{first} {middle_initials} {last}")
    if first:
        _add(f"{first[0]}. {last}")
        _add(f"{last}, {first[0]}.")

    return out


def _flexible_tolerant_pattern(s: str) -> str:
    """Build a regex that tolerates any non-alphanumeric separator between tokens.

    Splits ``s`` on non-alphanumeric characters and joins the surviving
    tokens with ``[^A-Za-z0-9]*`` so the pattern matches every common
    author/series naming convention regardless of separator choice:

      * "Dennis E. Taylor"  (whitespace + dots)
      * "Dennis E Taylor"   (whitespace only)
      * "DennisETaylor"     (zero separators — folder-name form)
      * "Dennis.E.Taylor"   (dots only)
      * "Taylor, Dennis"    (comma — bibliographic form)
      * "Taylor - Dennis"   (hyphen)

    Used for stripping author / series names from titles + filenames where
    naming conventions vary across catalog sources (ABS, Booklore, local).
    """
    tokens = re.split(r"[^A-Za-z0-9]+", s or "")
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    return r"[^A-Za-z0-9]*".join(re.escape(t) for t in tokens)


def _norm_author(s: str) -> str:
    """Normalise an author-name for fuzz comparison.

    Strips dots/whitespace entirely so e.g. ``Dennis E. Taylor`` (canonical) and
    ``DennisETaylor`` (folder name with stripped separators) become identical.
    Used only for author-folder/author-trailer comparisons where the same
    author name appears in stripped form in the filesystem.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _fuzz_author(a: str, b: str) -> float:
    """Author-specific fuzz tolerant of dot/space, name-order, AND
    multi-author-folder variants.

    The second argument ``b`` is treated as the canonical author name and
    expanded into common surface variants ("First Last", "Last, First",
    "F. Last", "Last, F."). Returns the best fuzz across every variant —
    so a candidate like ``"Taylor, Dennis"`` (bibliographic form from a
    catalog) matches canonical ``"Dennis E. Taylor"`` at ratio 1.0 via
    the ``"Taylor, Dennis"`` variant.

    The first argument ``a`` may be a folder/filename containing multiple
    comma- or ampersand-separated authors (e.g.
    ``"Robert Jordan, Brandon Sanderson"`` for collaboratively-finished
    works). Each token is tried independently and the best ratio wins —
    so a folder containing the canonical author among co-authors counts
    as a full match instead of falling below the 0.75 threshold.
    """
    if not a or not b:
        return 0.0
    # Split candidate on common multi-author separators so each name is
    # compared independently.  "Robert Jordan, Brandon Sanderson" → ["Robert
    # Jordan", "Brandon Sanderson"]; the canonical-side variants then match
    # one of those at ratio 1.0.
    candidates = [a]
    if re.search(r"[,&;]| and | with ", a, flags=re.IGNORECASE):
        for raw_token in re.split(r"\s*[,&;]\s*| and | with ", a, flags=re.IGNORECASE):
            token = raw_token.strip()
            if token and token != a:
                candidates.append(token)

    canonical_variants = _author_name_variants_cached(b)
    best = 0.0
    for candidate in candidates:
        na = _norm_author(candidate)
        if not na:
            continue
        for variant in canonical_variants:
            nb = _norm_author(variant)
            if not nb:
                continue
            if na == nb:
                return 1.0
            ratio = difflib.SequenceMatcher(None, na, nb).ratio()
            if ratio > best:
                best = ratio
    return best


def _fuzz(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalised strings."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# Small English stopword set for the content-token equality guard. Kept
# minimal — articles, common conjunctions/prepositions, copula. Anything
# semantically meaningful (nouns, names, verbs) stays.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "at",
        "by",
        "with",
        "from",
        "is",
        "&",
    }
)

# Position-marker words that surround a number and should be stripped along
# with their digit (the digit itself is then caught by the bare/decimal/roman
# regexes used in extract_title_core).
_POSITION_WORDS: frozenset[str] = frozenset(
    {
        "book",
        "vol",
        "volume",
        "part",
        "arc",
        "tome",
    }
)


def _content_tokens(text: str, *, series_name: str | None) -> list[str]:
    """Tokenise ``text`` into the meaningful content words, in order.

    Strips stopwords, series-name tokens, year tokens, position-marker words
    (book / vol / part etc.), bare numbers, and alphanumeric-ID tokens
    (length ≥6 containing both letters and digits — catches ASIN-style codes
    even when bare in the path).

    Used by the LOW-band content-equality guard. Pure helper — no side
    effects.
    """
    if not text:
        return []

    raw = re.findall(r"[A-Za-z0-9]+", text.lower())
    series_tokens: set[str] = set()
    if series_name:
        series_tokens = set(re.findall(r"[a-z0-9]+", series_name.lower()))

    out: list[str] = []
    for tok in raw:
        if tok in _STOPWORDS:
            continue
        if tok in series_tokens:
            continue
        if tok in _POSITION_WORDS:
            continue
        # 4-digit standalone year (1900-2099)
        if tok.isdigit() and len(tok) == 4:
            y = int(tok)
            if 1900 <= y <= 2099:
                continue
        # Bare integers (1-3 digits)
        if tok.isdigit() and len(tok) <= 3:
            continue
        # Standalone decimal-looking? findall splits on non-alphanum so
        # decimals come in as two separate tokens. Nothing to do here.

        # Alphanumeric ID: length ≥6 with BOTH letters AND digits mixed.
        if len(tok) >= 6:
            has_alpha = any(c.isalpha() for c in tok)
            has_digit = any(c.isdigit() for c in tok)
            if has_alpha and has_digit:
                continue

        out.append(tok)
    return out


def _content_tokens_equal(a: str, b: str, *, series_name: str | None) -> bool:
    """Are the content-token sequences of ``a`` and ``b`` strictly equal?

    Strict equality (same tokens, same order) — not subsequence/containment.
    "Review of Lord of the Rings" and "Lord of the Rings" share the same
    content tokens in order but are different books, so the LOW-band guard
    must reject subsequence matches.

    Returns False for empty-vs-empty: two title strings that strip down to
    no content tokens carry no positive evidence either way.
    """
    ta = _content_tokens(a, series_name=series_name)
    tb = _content_tokens(b, series_name=series_name)
    if not ta or not tb:
        return False
    return ta == tb


def _roman_to_int(roman: str) -> int | None:
    table = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = roman.lower()
    if not s or any(c not in table for c in s):
        return None
    total = 0
    prev = 0
    for c in reversed(s):
        v = table[c]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    # Validate it's a "real" roman numeral by re-encoding (cheap sanity)
    return total if total > 0 else None


def _clean_isbn(raw: str | None) -> str | None:
    """Strip dashes/spaces; return uppercase. Returns None when not 10/13 chars."""
    if not raw:
        return None
    cleaned = _ISBN_CLEAN_RE.sub("", str(raw)).upper()
    if len(cleaned) in (10, 13):
        return cleaned
    return None


def _parse_book_identifiers(book: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return (isbns, asins) collected from book row's isbn_13/isbn_10/isbns/asins.

    Handles three storage shapes the columns may take:
      * scalar isbn_13 / isbn_10 — direct string fields on the row.
      * JSON list-of-dicts — Hardcover stores it as
        ``[{"isbn_13": "9..."}, ...]`` / ``[{"asin": "B0..."}, ...]``.
        The DB column type is TEXT so the row dict gets the raw JSON
        string; we json.loads it and walk the structure.
      * Already-parsed list (in-memory book dicts in tests or code
        paths that pre-parse the JSON).
      * Legacy comma/semicolon/whitespace-separated string (older
        rows; kept for back-compat).

    The original implementation only handled the legacy CSV form, so any
    Hardcover-synced book whose identifiers are in the JSON list-of-dicts
    form silently yielded empty sets. That made the embedded-identifier
    hard-reject in ``_score_metadata_signals`` (line ~1604) misfire: the
    file's ASIN/ISBN never "matched" the book's, so any embedded-tagged
    file got rejected outright even when it was the correct book.
    """
    isbns: set[str] = set()
    asins: set[str] = set()

    for key in ("isbn_13", "isbn_10"):
        cleaned = _clean_isbn(book.get(key))
        if cleaned:
            isbns.add(cleaned)

    def _walk_for_field(value: Any, field_keys: tuple[str, ...]) -> list[str]:
        """Pull string values out of (list[dict] | list[str] | dict | str)."""
        out: list[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for k in field_keys:
                        v = item.get(k)
                        if isinstance(v, str):
                            out.append(v)
                elif isinstance(item, str):
                    out.append(item)
        elif isinstance(value, dict):
            for k in field_keys:
                v = value.get(k)
                if isinstance(v, str):
                    out.append(v)
        elif isinstance(value, str):
            out.append(value)
        return out

    raw_isbns = book.get("isbns")
    if raw_isbns:
        parsed: Any = raw_isbns
        if isinstance(raw_isbns, str) and raw_isbns.strip().startswith(("[", "{")):
            try:
                parsed = json.loads(raw_isbns)
            except ValueError, TypeError:
                parsed = raw_isbns
        for val in _walk_for_field(parsed, ("isbn_13", "isbn_10", "isbn")):
            cleaned = _clean_isbn(val)
            if cleaned:
                isbns.add(cleaned)
        # Legacy CSV fallback: if nothing parsed and we still have a raw
        # string, split on separators (handles older row formats).
        if isinstance(raw_isbns, str) and not (isinstance(parsed, (list, dict))):
            for tok in re.split(r"[,;\s]+", raw_isbns):
                cleaned = _clean_isbn(tok)
                if cleaned:
                    isbns.add(cleaned)

    raw_asins = book.get("asins")
    if raw_asins:
        parsed = raw_asins
        if isinstance(raw_asins, str) and raw_asins.strip().startswith(("[", "{")):
            try:
                parsed = json.loads(raw_asins)
            except ValueError, TypeError:
                parsed = raw_asins
        for val in _walk_for_field(parsed, ("asin",)):
            tok = val.strip().upper()
            if _ASIN_RE.match(tok):
                asins.add(tok)
        if isinstance(raw_asins, str) and not (isinstance(parsed, (list, dict))):
            for raw_tok in re.split(r"[,;\s]+", raw_asins):
                tok = raw_tok.strip().upper()
                if _ASIN_RE.match(tok):
                    asins.add(tok)

    # Also pick up the embedded asin field directly (some book rows carry
    # an "asin" scalar in addition to the asins list).
    direct_asin = book.get("asin")
    if isinstance(direct_asin, str):
        tok = direct_asin.strip().upper()
        if _ASIN_RE.match(tok):
            asins.add(tok)

    return isbns, asins


def _strip_year_paren(s: str) -> str:
    return _YEAR_PAREN_RE.sub("", s).strip()


def _strip_non_year_parens(s: str) -> str:
    """Strip parentheticals and brackets that don't look like a year.

    Catches:
      * `(...)` non-year parens (existing behaviour) — "(Unabridged)",
        "(2nd edition)", "(B0F6F4GKJY)".
      * `[...]` brackets — ASIN codes (`[B0B75MS6F3]`), Graphicaudio
        markers (`[GA]`), condition tags (`[Unabridged]`). These leak into
        title cores when filenames embed catalog identifiers.

    Preserves the body when it contains a 4-digit year or an explicit
    book/vol marker so position-extraction still sees those signals.
    """

    def keep(match: re.Match[str]) -> str:
        body = match.group(0)
        if re.search(r"\b(?:19|20)\d{2}\b", body):
            return body
        if re.search(r"\b(?:book|vol(?:ume)?|part|arc|tome)\b", body, re.IGNORECASE):
            # keep "(Book 4)" / "[Book 4]" so position extraction still sees it
            return body
        return " "

    s = _PAREN_ANY_RE.sub(keep, s)
    s = _BRACKET_ANY_RE.sub(keep, s)
    return s.strip()


# ---------------------------------------------------------------------------
# Path decomposition
# ---------------------------------------------------------------------------


def decompose_path(path: str, author_name: str | None = None) -> PathDecomposition:
    """Split a file path into typed components.

    The author_name (when given) is used to anchor the decomposition — we look
    for a directory whose name fuzzy-matches and use the position of that
    directory in the path to assign the series and book folders relative to it.

    When no author_name match is found, fall back to "last three components"
    semantics: book_folder = parent, series_folder = grandparent.
    """
    p = PurePosixPath(path.replace("\\", "/"))
    parts = list(p.parts)
    leaf = parts[-1] if parts else ""
    leaf_is_file = "." in leaf and not leaf.startswith(".")
    # Extension: only the last suffix, lowercased, including the leading dot.
    # Use a manual extension extraction to avoid PurePath.suffix quirks on names
    # like "1. Unsouled" (where suffix would be " Unsouled").
    ext = ""
    if leaf_is_file:
        m = re.search(r"\.([A-Za-z0-9]{1,8})$", leaf)
        if m:
            ext = "." + m.group(1).lower()

    # Default heuristic depends on whether leaf is a file or a directory.
    #
    #   File layout (ebook):       author / series / book.ext     OR  author / book.ext
    #   Folder layout (audiobook): author / series / book_folder  OR  author / book_folder
    #
    # When leaf is a file, the book is the file itself and its parent is the
    # series folder. When leaf is a directory, the leaf IS the book folder.
    dirs = parts[:-1]
    if leaf_is_file:
        book_folder: str | None = dirs[-1] if dirs else None
        series_folder: str | None = dirs[-2] if len(dirs) >= 2 else None
        author_folder: str | None = dirs[-3] if len(dirs) >= 3 else None
    else:
        # leaf is the book folder
        book_folder = leaf or None
        series_folder = dirs[-1] if dirs else None
        author_folder = dirs[-2] if len(dirs) >= 2 else None

    # If we have an author name, try to anchor: find the *deepest* dir whose
    # name fuzzy-matches the author, and re-assign series/book relative to it.
    #
    # Two layouts:
    #   ebook (leaf_is_file):  author / [series /] <leaf-file>
    #     - dirs below author  = [series?] (0 or 1 component)
    #     - the file IS the book; series_folder = below[-1] if any, else None
    #     - book_folder is conceptually the immediate parent of the file —
    #       same as series_folder when present, else just None.
    #   audiobook (leaf is folder): author / [series /] <leaf-folder>
    #     - dirs below author  = [series?] (0 or 1 component)
    #     - the leaf folder IS the book folder; series_folder = below[-1] if any.
    if author_name and dirs:
        for i in range(len(dirs) - 1, -1, -1):
            if _fuzz_author(dirs[i], author_name) >= AUTHOR_FUZZ_THRESHOLD:
                author_folder = dirs[i]
                below = dirs[i + 1 :]
                if leaf_is_file:
                    # Layouts (between author and the file):
                    #   author / file.epub                  → below = []
                    #   author / series / file.epub         → below = [series]
                    #   author / series / book / file.epub  → below = [series, book]
                    # When there's a distinct book sub-folder, the immediate
                    # parent is the book folder and its parent is the series.
                    if len(below) >= 2:
                        book_folder = below[-1]
                        series_folder = below[-2]
                    elif len(below) == 1:
                        # Flat series layout: file sits directly under series.
                        series_folder = below[0]
                        book_folder = below[0]
                    else:
                        series_folder = None
                        book_folder = None
                else:
                    # Leaf is the book folder. Series is the last dir under
                    # author *before* the leaf.
                    series_folder = below[-1] if below else None
                    book_folder = leaf or None
                break

    return PathDecomposition(
        leaf=leaf,
        leaf_is_file=leaf_is_file,
        ext=ext,
        book_folder=book_folder,
        series_folder=series_folder,
        author_folder=author_folder,
        full_path=path,
    )


# ---------------------------------------------------------------------------
# Series-position signal extraction
# ---------------------------------------------------------------------------


def _name_without_ext(name: str) -> str:
    """Strip a file extension if present. Treats only the LAST suffix as ext."""
    m = re.match(r"^(.*)\.([A-Za-z0-9]{1,8})$", name)
    return m.group(1) if m else name


def _is_year(value: float) -> bool:
    """Is `value` a 4-digit standalone year (1900-2099)?"""
    if value != int(value):
        return False
    iv = int(value)
    return 1900 <= iv <= 2099 and iv >= 1000


def extract_position_signals(
    text: str,
    *,
    series_name: str | None = None,
    is_filename: bool = False,
    is_book_folder: bool = False,
) -> list[PositionVote]:
    """Extract series_position votes from a name component.

    Each vote carries (value, weight, source). Weights: "high" | "medium" | "low".

    Skips 4-digit standalone years (1900-2099). Skips numbers adjacent to other
    digits (so "01099" doesn't match within "1099212").
    """
    if not text:
        return []

    votes: list[PositionVote] = []
    stem = _name_without_ext(text)
    # We won't strip year parens here — we let _is_year filter them numerically
    # so we don't risk losing context like "(Book 4)" by accident.

    # 1) Leading "NN." / "NN -" / "NN_" — high weight in filenames and book folders
    if is_filename or is_book_folder:
        m = _LEADING_NUM_RE.match(stem)
        if m:
            try:
                v = float(m.group(1))
                if not _is_year(v):
                    votes.append(PositionVote(v, "high", "leading_num"))
            except ValueError:
                pass

    # 2) Explicit "Book N", "Vol N", "Volume N", "Arc N", "Part N", "Tome N", "#N"
    for m in _EXPLICIT_VOL_RE.finditer(stem):
        raw = m.group(2) or m.group(3)
        if raw is None:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        if _is_year(v):
            continue
        votes.append(PositionVote(v, "high", "explicit_marker"))

    # 3) Roman numerals in marker context — "Book IV"
    for m in _ROMAN_RE.finditer(stem):
        ival = _roman_to_int(m.group(1))
        if ival is not None and not _is_year(float(ival)):
            votes.append(PositionVote(float(ival), "medium", "roman_marker"))

    # 4) "<series_name> N" — number directly after series name
    if series_name:
        sn_pat = _flexible_word_pattern(series_name)
        seriestrail_re = re.compile(
            rf"\b{sn_pat}\s*[#\-:]?\s*(\d{{1,4}}(?:\.\d+)?)\b",
            re.IGNORECASE,
        )
        for m in seriestrail_re.finditer(stem):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if _is_year(v):
                continue
            votes.append(PositionVote(v, "high", "after_series_name"))

    # 5) Standalone decimal token (e.g. "Cradle 1.5 - Title")
    #    Only when not inside a longer digit/period run.
    for m in _DECIMAL_RE.finditer(stem):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if _is_year(v):
            continue
        votes.append(PositionVote(v, "medium", "decimal"))

    # 6) Bare standalone number token — medium weight.
    #    We want to skip numbers we already captured above (leading, after-series,
    #    inside explicit markers, decimals). Easiest: blank out the contexts
    #    we already used before this pass.
    redacted = stem
    redacted = _LEADING_NUM_RE.sub(" ", redacted) if (is_filename or is_book_folder) else redacted
    redacted = _EXPLICIT_VOL_RE.sub(" ", redacted)
    redacted = _ROMAN_RE.sub(" ", redacted)
    redacted = _DECIMAL_RE.sub(" ", redacted)
    if series_name:
        sn_pat = _flexible_word_pattern(series_name)
        redacted = re.sub(
            rf"\b{sn_pat}\s*[#\-:]?\s*\d{{1,4}}(?:\.\d+)?\b",
            " ",
            redacted,
            flags=re.IGNORECASE,
        )
    # Also redact 4-digit years in parens
    redacted = re.sub(r"\((?:19|20)\d{2}\)", " ", redacted)

    for m in _BARE_NUM_RE.finditer(redacted):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if _is_year(v):
            continue
        votes.append(PositionVote(v, "medium", "bare_number"))

    # 7) Word-number tokens ("Book Three" etc.) — handled by tokenising redacted
    for m in re.finditer(
        r"\b(?:book|vol(?:ume)?|part|arc|tome)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        stem,
        re.IGNORECASE,
    ):
        word = m.group(1).lower()
        v = float(_WORD_NUMBER_MAP[word])
        votes.append(PositionVote(v, "high", "word_number_marker"))

    # Deduplicate identical (value, weight, source) but preserve order
    seen: set[tuple[float, str, str]] = set()
    deduped: list[PositionVote] = []
    for v in votes:
        key = (v.value, v.weight, v.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(v)
    return deduped


def collect_position_votes(
    decomposition: PathDecomposition,
    *,
    series_name: str | None = None,
) -> list[PositionVote]:
    """Run extract_position_signals over relevant path components, aggregated."""
    votes: list[PositionVote] = []
    if decomposition.leaf_is_file:
        votes.extend(
            extract_position_signals(
                decomposition.leaf,
                series_name=series_name,
                is_filename=True,
            )
        )
        # Also extract from the parent (book_folder) when it differs from the leaf —
        # audiobooks may live in a positionally-named folder.
        if decomposition.book_folder and decomposition.book_folder != decomposition.leaf:
            votes.extend(
                extract_position_signals(
                    decomposition.book_folder,
                    series_name=series_name,
                    is_book_folder=True,
                )
            )
    else:
        # Leaf IS the book folder (audiobook layout). Extract from it directly.
        votes.extend(
            extract_position_signals(
                decomposition.leaf,
                series_name=series_name,
                is_book_folder=True,
            )
        )
    if decomposition.series_folder and decomposition.series_folder != decomposition.book_folder:
        # Series folder rarely contains position info; only extract explicit markers.
        votes.extend(
            v
            for v in extract_position_signals(decomposition.series_folder, series_name=series_name)
            if v.source in ("explicit_marker", "after_series_name", "word_number_marker")
        )
    return votes


# ---------------------------------------------------------------------------
# Title core extraction
# ---------------------------------------------------------------------------


def extract_title_core(
    name: str,
    *,
    series_name: str | None = None,
    author_name: str | None = None,
) -> str:
    """Strip a filename stem down to the book title core for fuzzy comparison.

    Removes (in order): file extension, trailing year `(YYYY)`, trailing
    `- Author`, leading `NN.`/`NN -`/`NN_`, explicit `(Book N)`/`Book N`/`Vol N`/
    `#N` markers, decimal position tokens, series-name prefix.
    """
    if not name:
        return ""

    s = _name_without_ext(name).strip()

    # Strip trailing year paren
    s = _strip_year_paren(s)

    # Strip trailing "- Author" (or "- Author1, Author2") and leading
    # "Author - Title". Uses the dot-tolerant pattern so author names with
    # initials ("Dennis E. Taylor") match in the filename even when the
    # filename preserves the dots — the prior pattern stripped dots from
    # the author name but then couldn't match the dot-containing form.
    if author_name:
        a_pat = _flexible_tolerant_pattern_cached(author_name)
        if a_pat:
            s = re.sub(rf"\s*[-–—:]\s*{a_pat}\s*$", " ", s, flags=re.IGNORECASE)
            # Also a fallback for "Title - Author1, Author2"
            s = re.sub(
                rf"\s*[-–—]\s*[^-–—]*{a_pat}[^-–—]*$",
                " ",
                s,
                flags=re.IGNORECASE,
            )
            # Leading "Author - Title" or "Author, Title"
            s = re.sub(rf"^\s*{a_pat}\s*[-–—:,]\s*", " ", s, flags=re.IGNORECASE)

    # Strip leading "NN. " / "NN - " / "NN_ "
    s = _LEADING_NUM_RE.sub(" ", s)

    # When the filename uses a "<series prefix> #N - <real title>" or
    # "<series prefix> Book N - <real title>" pattern (common for audiobook
    # libraries — e.g. "Epeditionary Force #08 - Armageddon" or "Mistborn
    # 04 - The Alloy of Law"), keep ONLY the part after the position marker
    # as the title core. The prefix is always the series name (possibly
    # misspelled — series-name strip can't handle e.g. the missing 'x' in
    # 'Epeditionary'), so trying to fuzz the whole thing against the book
    # title 'Armageddon' returns 0.51 and emits a spurious title_mismatch.
    # The suffix is the real title and matches cleanly.
    m = re.search(
        r"\b(?:book|vol(?:ume)?|part|arc|tome)\s*[-:#]?\s*\d{1,3}(?:\.\d+)?\s*[-–—:]\s*(.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and m.group(1).strip():
        s = m.group(1).strip()
    else:
        m = re.search(r"#\s*\d{1,3}(?:\.\d+)?\s*[-–—:]\s*(.+)$", s)
        if m and m.group(1).strip():
            s = m.group(1).strip()

    # Strip explicit volume markers like "(Book 4)", "Book 4", "Vol 1.5", "#15"
    s = _EXPLICIT_VOL_RE.sub(" ", s)

    # Strip standalone decimal position tokens (e.g. "1.5" in "Cradle 1.5 Title")
    s = _DECIMAL_RE.sub(" ", s)

    # Strip series-name occurrences anywhere. Uses the dot-tolerant pattern so
    # series names with punctuation ("Mistborn: The Original Trilogy") match
    # the filename's form too. When the strip leaves the title core empty,
    # the evaluator treats empty as neutral (no positive evidence).
    if series_name:
        sn_pat = _flexible_tolerant_pattern_cached(series_name)
        if sn_pat:
            s = re.sub(rf"^\s*{sn_pat}[\s\-:,]+", " ", s, flags=re.IGNORECASE)
            s = re.sub(rf"\b{sn_pat}\b", " ", s, flags=re.IGNORECASE, count=1)
            # Re-run the leading-number strip — series-strip may have just
            # exposed a leading "NN - " or "NN. " pattern that wasn't at the
            # start of the original string ("Mistborn 04 - The Alloy of Law"
            # -> " 04 - The Alloy of Law" -> "The Alloy of Law"). Also handle
            # the bare leading number followed by a `:` colon separator
            # which the existing regex doesn't cover.
            s = s.lstrip()
            s = _LEADING_NUM_RE.sub(" ", s)
            s = re.sub(r"^\s*\d{1,3}(?:\.\d+)?\s*:\s*", " ", s)

    # Strip remaining edition-style parentheticals — only those that don't
    # contain a year or a book-marker (those were already handled).
    s = _strip_non_year_parens(s)

    # Strip bare trailing volume number ("Primal Hunter 15" → "Primal Hunter").
    # Allow start-of-string (handles the case where series strip leaves just the
    # number, e.g. "Beware of Chicken 2" → "2" → "").
    s = re.sub(r"(?:^|\s+)\d{1,3}(?:\.\d+)?\s*$", " ", s)

    # Strip stray separator chars now exposed at the edges
    s = re.sub(r"^\s*[-–—:_.\s]+", "", s)
    s = re.sub(r"[-–—:_.\s]+\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Title comparison helpers
# ---------------------------------------------------------------------------


def _content_token_count(
    text: str,
    *,
    series_name: str | None,
    author_name: str | None,
) -> int:
    """Count distinguishing content tokens in `text` after stripping series
    name, author name, position markers, years, and stopwords. Used by the
    Channel 2 suppression to decide whether a title is essentially "just
    the series" (low count) or has its own identity (high count).
    """
    s = text.lower()
    if series_name:
        s = re.sub(rf"\b{re.escape(series_name.lower())}\b", " ", s, flags=re.IGNORECASE)
    if author_name:
        for variant in _author_name_variants_cached(author_name):
            s = re.sub(rf"\b{re.escape(variant.lower())}\b", " ", s, flags=re.IGNORECASE)
    # Position markers: "Book N", "Vol N", "#N", "Part N", "Book One", etc.
    s = re.sub(
        r"\b(?:book|vol(?:ume)?|part|arc|tome|chapter|episode)\s*"
        r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|i{1,3}|iv|vi{0,3}|ix|x{1,3})\b",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"#\s*\d+", " ", s)
    # Year (4-digit 1800-2099).
    s = re.sub(r"\b(?:1[89]|20)\d{2}\b", " ", s)
    # Bare numbers (positions, edition numbers).
    s = re.sub(r"\b\d{1,3}\b", " ", s)
    # Common series/edition descriptor words that aren't real content.
    s = re.sub(
        r"\b(?:untitled|series|trilogy|collection|complete|saga|anthology)\b",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    tokens = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    return sum(1 for t in tokens if t not in _STOPWORDS and len(t) > 1)


def _canonical_title_forms(
    title: str,
    *,
    series_name: str | None = None,
    author_name: str | None = None,
    companion_title: str | None = None,
) -> list[str]:
    """Return successive canonicalisations of a title for fuzzy comparison.

    External catalogs (ABS / Booklore / publishers) routinely embed extra
    metadata in the title string that doesn't appear in Hardcover's
    canonical title. The strategy: strip every known piece of cruft
    (author / series / position / year / parens / subtitle) one channel at
    a time and emit each progressively-stripped form. Callers take the
    MAX fuzz across all (form_a, form_b) pairs — so any stripping that
    happens to align the two sides wins.

    Known cruft channels:
      * Parenthetical content: "Critical Mass (Expeditionary Force Book 10)"
      * Subtitle after ":":    "Beware of Chicken 2: A Xianxia ..."
      * Series name:           "Mistborn 6 - The Bands of Mourning"
      * Author name (any common form): "Title - Last, First" / "F. Last - Title"
      * Position markers:      "Book 4", "Vol 1.5", "#15"
      * Year tokens:           "(2024)", "2024"

    Title fragments that don't get stripped (because they're not in any
    known channel) ARE the title — which is exactly what we want.
    """
    if not title:
        return []

    out: list[str] = [title]
    seen: set[str] = {_norm(title)}
    norm_series = _norm(series_name) if series_name else None

    def _add(form: str) -> None:
        form = re.sub(r"\s+", " ", form).strip()
        if not form:
            return
        # Clean up stray separators left at the edges by stripping.
        form = re.sub(r"^[-–—:_,.\s]+", "", form)
        form = re.sub(r"[-–—:_,.\s]+$", "", form).strip()
        if not form:
            return
        norm = _norm(form)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(form)

    def _subtitle_split_distinguishes(before: str, after: str) -> bool:
        """True when subtitle-splitting `"<series><sep><after>"` would emit
        a before-form (the bare series name) that doesn't distinguish books
        within the series, because `after` contains real content beyond
        position markers and series/author metadata.

        Used by Channel 2 for every subtitle separator (':', ' - ', ' — ',
        ' – '). The name is generic; the logic is the same regardless of
        which separator triggered the call.

        Pocket Companion case → True (after = "A Pocket Companion to The
        Way of Kings and Words of Radiance" — distinguishing content).
        Wandering Inn Book One Part One case → False (after = "Book One,
        Part One of the Wandering Inn Series" — only position + series +
        stopwords; bare-series form is the legitimate short title).

        Dominion of Blades case → False (after = "A LitRPG Adventure" has
        2 content tokens, but the COMPANION title is just the series name
        itself — Dominion of Blades is a one-book "series", so the bare
        form is the only sensible match). Companion-aware suppression
        avoids over-restricting standalone-titled books.
        """
        if not (norm_series and _norm(before) == norm_series):
            return False  # The before-side isn't the series name; nothing to suppress.
        # Companion-aware: if the OTHER side being compared is essentially
        # just the series name itself (no distinguishing content beyond
        # series + position + author + stopwords), this is the standalone-
        # book case (book.title == series_name). Keep the bare form so it
        # can match at fuzz=1.0.
        if companion_title is not None:
            companion_content = _content_token_count(
                companion_title,
                series_name=series_name,
                author_name=author_name,
            )
            if companion_content < 2:
                return False
        residue = after.lower()
        if series_name:
            residue = re.sub(
                rf"\b{re.escape(series_name.lower())}\b", " ", residue, flags=re.IGNORECASE
            )
        if author_name:
            for variant in _author_name_variants_cached(author_name):
                residue = re.sub(
                    rf"\b{re.escape(variant.lower())}\b", " ", residue, flags=re.IGNORECASE
                )
        # Position-marker words with adjacent digits or number-words.
        residue = re.sub(
            r"\b(?:book|vol(?:ume)?|part|arc|tome|chapter|episode)\s*"
            r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|i{1,3}|iv|vi{0,3}|ix|x{1,3})\b",
            " ",
            residue,
            flags=re.IGNORECASE,
        )
        residue = re.sub(r"#\s*\d+", " ", residue)
        tokens = [t for t in re.split(r"[^a-z0-9]+", residue) if t]
        content = [t for t in tokens if t not in _STOPWORDS and not t.isdigit() and len(t) > 1]
        return len(content) >= 2

    # Channel 1: strip parens AND brackets (year, edition, ASIN, catalog
    # markers like [GA]). The `_add` cleanup re-collapses whitespace.
    no_parens = re.sub(r"\s*\([^)]*\)\s*", " ", title)
    no_parens = re.sub(r"\s*\[[^\]]*\]\s*", " ", no_parens)
    _add(no_parens)

    # Channel 2: strip subtitle after any common subtitle separator —
    # colon, space-dash-space, em-dash, or en-dash. External catalogs
    # are inconsistent: Booklore typically writes Title-colon-Subtitle,
    # ABS writes Title-dash-Subtitle, publishers sometimes use em-dash.
    # Every separator produces a bare before-form so cross-catalog
    # title fuzz can find a match. The _subtitle_split_distinguishes
    # guard suppresses the bare form only when it would normalize to
    # the series name AND the subtitle carries real distinguishing
    # content — the Pocket-Companion failure mode where dropping the
    # subtitle would let a real different book mimic the series's #N
    # entry. When the subtitle is just position/series metadata, or
    # when the before-form includes a position number distinct from
    # the series name, the bare form is the legitimate short title
    # and is kept.
    for base in (title, no_parens):
        for sep in (":", " - ", " — ", " – "):
            if sep not in base:
                continue
            before, _, after = base.partition(sep)
            if not _subtitle_split_distinguishes(before, after):
                _add(before)

    # Channel 3: strip series_name (any variant — tolerant pattern handles
    # naming differences across catalogs).
    series_stripped_bases: list[str] = []
    if series_name:
        sn_pat = _flexible_tolerant_pattern_cached(series_name)
        if sn_pat:
            for base in (title, no_parens):
                stripped = re.sub(rf"\b{sn_pat}\b", " ", base, flags=re.IGNORECASE)
                series_stripped_bases.append(stripped)
                _add(stripped)
                if ":" in stripped:
                    _add(stripped.split(":", 1)[0])

    # Channel 4: strip author_name in all common surface variants
    # ("First Last", "Last, First", "F. Last", "Last, F.", etc.).
    author_stripped_bases: list[str] = []
    if author_name:
        for variant in _author_name_variants_cached(author_name):
            v_pat = _flexible_tolerant_pattern_cached(variant)
            if not v_pat:
                continue
            # Try each existing form (original + already-stripped) and strip
            # the author both leading and trailing. "by Author" form too.
            # Separator class includes `.` to handle trailing initial-dots
            # like "Taylor, Dennis E. - Outland" where the dot sits between
            # the variant and the separator.
            bases = [title, no_parens, *series_stripped_bases]
            for base in bases:
                stripped = base
                # Trailing: " - Author", " by Author", ", Author"
                stripped = re.sub(
                    rf"\s*[\.\s]*(?:[-–—:,]|by)\s+{v_pat}[\.\s]*$",
                    " ",
                    stripped,
                    flags=re.IGNORECASE,
                )
                # Leading: "Author - Title", "Author, Title"
                stripped = re.sub(
                    rf"^\s*{v_pat}[\.\s]*[-–—:,]\s*",
                    " ",
                    stripped,
                    flags=re.IGNORECASE,
                )
                if stripped != base:
                    author_stripped_bases.append(stripped)
                    _add(stripped)

    # Channel 5: strip explicit "Book N" / "Vol N" / "#N" markers from
    # every variant we've produced so far.
    bases_for_markers = [title, no_parens, *series_stripped_bases, *author_stripped_bases]
    for base in bases_for_markers:
        stripped = _EXPLICIT_VOL_RE.sub(" ", base)
        _add(stripped)

    return out


def _title_core_fuzz(
    title_core: str,
    book_title: str,
    *,
    series_name: str | None = None,
    author_name: str | None = None,
) -> float:
    """Best fuzz between any canonical form of the candidate title and any
    canonical form of the book title.

    Both sides get canonicalised (author / series / subtitle / parens /
    position-markers / year stripped in each combination) so cross-catalog
    title-format differences don't bury a real match. The max across all
    (form_a, form_b) pairs wins.
    """
    if not title_core or not book_title:
        return 0.0
    forms_core = _canonical_title_forms(
        title_core,
        series_name=series_name,
        author_name=author_name,
        companion_title=book_title,
    )
    forms_book = _canonical_title_forms(
        book_title,
        series_name=series_name,
        author_name=author_name,
        companion_title=title_core,
    )
    best = 0.0
    for a in forms_core:
        for b in forms_book:
            best = max(best, _fuzz(a, b))
    return best


# ---------------------------------------------------------------------------
# Author detection
# ---------------------------------------------------------------------------


def _author_in_filename_trailer(leaf_stem: str, author_name: str | None) -> bool:
    """Does the author name appear at the start or end of ``leaf_stem``?

    Tries every common surface variant of the canonical author name
    ("First Last", "Last, First", "F. Last", "Last, F.") with the dot-
    tolerant pattern so all separator conventions match.
    """
    if not author_name:
        return False
    for variant in _author_name_variants_cached(author_name):
        a_pat = _flexible_tolerant_pattern_cached(variant)
        if not a_pat:
            continue
        # Trailing: " - Author" / " - Last, First" / " by Author" (optional year-paren suffix).
        if re.search(
            rf"\s*[\.\s]*(?:[-–—:,]|by)\s+{a_pat}[\.\s]*(?:\(.*\))?\s*$",
            leaf_stem,
            re.IGNORECASE,
        ):
            return True
        # Leading: "Author - Title" / "Last, First - Title".
        if re.search(
            rf"^\s*{a_pat}[\.\s]*[-–—:,]\s*",
            leaf_stem,
            re.IGNORECASE,
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Evaluate match — the core scorer
# ---------------------------------------------------------------------------


def _book_series_position_float(book: dict[str, Any]) -> float | None:
    raw = book.get("series_position")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


def _all_book_positions(book: dict[str, Any]) -> list[float]:
    """All series positions the book occupies (primary + alternates).

    Many books belong to several series at different positions:
    "The Alloy of Law" is #1 in *Mistborn: Wax & Wayne*, #4 in *The Mistborn
    Saga*, and #8 in *The Cosmere*. External catalogs (ABS, Booklore) often
    use a different one than Hardcover's primary, so position-disagreement
    must only fire when the candidate position matches NONE of the book's
    known positions.

    Reads from ``book['series_position']`` (primary) plus any positions in
    ``book['all_series']`` (JSON-encoded or pre-parsed list of
    ``{"name": ..., "position": ..., "count": ...}`` dicts).
    """
    positions: set[float] = set()
    primary = _book_series_position_float(book)
    if primary is not None:
        positions.add(round(primary, 4))

    raw_all = book.get("all_series")
    if isinstance(raw_all, str):
        try:
            raw_all = json.loads(raw_all)
        except json.JSONDecodeError, TypeError, ValueError:
            raw_all = None

    if isinstance(raw_all, list):
        for entry in raw_all:
            if not isinstance(entry, dict):
                continue
            pos_raw = entry.get("position")
            if pos_raw is None or pos_raw == "":
                continue
            try:
                positions.add(round(float(pos_raw), 4))
            except TypeError, ValueError:
                continue

    # Fallback: parse the position out of book.title when neither
    # `series_position` nor `all_series` carries it. Hardcover entries
    # sometimes bake the position into the title ("He Who Fights with
    # Monsters, Book 2") without populating the structured position field.
    # Without this, all_book_positions stays empty -> position scoring is
    # skipped entirely -> Layer 2 title-borne reject can't fire -> a Book 9
    # file can attach to a Book 2 entry uncontested. Restrict to
    # high-confidence sources only (explicit Book/Vol markers, after-series-name
    # tokens, word-number-markers, roman numerals) so noisy signals like
    # bare digits in subtitles can never inject a false position.
    if not positions:
        title = book.get("title")
        series_name = book.get("series_name")
        if isinstance(title, str) and title.strip():
            high_conf_sources = {
                "explicit_marker",
                "after_series_name",
                "word_number_marker",
                "roman_marker",
            }
            for v in extract_position_signals(
                title,
                series_name=series_name if isinstance(series_name, str) else None,
                is_filename=False,
            ):
                if v.source in high_conf_sources:
                    positions.add(round(v.value, 4))

    return sorted(positions)


def _position_matches_book(value: float, all_positions: list[float]) -> bool:
    """Does ``value`` match any of the book's series positions (any series)?

    Exact match (within float tolerance) is the primary rule.

    Prequel / novella tolerance: a position in the half-open interval
    ``[0, 1)`` is treated as compatible with any other position in the same
    interval. Different catalogs use different conventions for "this is a
    short work that precedes Book 1" — Hardcover commonly stores 0.0,
    AudioBookShelf often stores 0.5 — and forcing exact equality there
    surfaces these as false-positive conflicts in the Possible Candidates
    UI (real case: ``The Daughters' War`` Hardcover=0.0 vs ABS=0.5).
    The tolerance is intentionally narrow: 1.0 vs 1.5 are NOT considered
    equivalent because 1.5 is a novella *between* books 1 and 2, a
    distinct work from book 1.
    """
    if any(abs(value - p) < 1e-6 for p in all_positions):
        return True
    return 0.0 <= value < 1.0 and any(0.0 <= p < 1.0 for p in all_positions)


def _metadata_to_dict(
    *,
    title: str | None,
    authors: list[str] | None,
    series_name: str | None,
    series_position: float | None,
    isbn_13: str | None,
    isbn_10: str | None,
    asin: str | None,
    year: int | None,
    all_series_pairs: list[tuple[str, float]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the verbatim metadata dict surfaced to the UI AND used by the
    Fix-match dialog to reconstruct ``SourceMetadata`` for re-scoring.

    Drops None / empty-string / empty-list fields but keeps numeric zero
    (series_position=0 is a legitimate prequel position).

    ``all_series_pairs`` (when present) is serialised as a list of
    ``[name, position]`` arrays so JSON round-trips cleanly — the
    Fix-match reconstruction reads it back into the SourceMetadata so
    multi-series matching survives interactive re-evaluation.
    """
    raw = {
        "title": title,
        "authors": list(authors) if authors else None,
        "series_name": series_name,
        "series_position": series_position,
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "asin": asin,
        "year": year,
        "all_series_pairs": (
            [[name, pos] for name, pos in all_series_pairs] if all_series_pairs else None
        ),
    }
    if extra:
        raw.update(extra)
    return {k: v for k, v in raw.items() if v is not None and v not in ("", [])}


def _score_metadata_signals(
    *,
    evidence: AttributionEvidence,
    book: dict[str, Any],
    book_title: str,
    book_series_name: str | None,
    book_spos: float | None,
    author_name: str | None,
    title: str | None,
    metadata_author: str | None,
    series_name: str | None,
    series_position: float | None,
    isbn_13: str | None,
    isbn_10: str | None,
    asin: str | None,
    label: str,
    path_will_disagree_high: bool = False,
    all_series_pairs: list[tuple[str, float]] | None = None,
) -> None:
    """Score identifier / title / author / series-position signals from a
    curated metadata source (either embedded file tags or an external API).

    Mutates ``evidence`` in place: appends to positives/penalties, bumps
    net_score, and sets hard_reject on identifier contradiction.

    ``label`` is used as a name prefix for positives/penalties (e.g.
    ``"embedded"`` → ``embedded_identifier`` / ``embedded_title_agree`` /
    ``embedded_series_agree`` / ``embedded_position_disagree`` /
    ``embedded_author_agree``; ``"source_abs"`` → ``source_abs_*`` and so on).
    """
    book_isbns, book_asins = _parse_book_identifiers(book)

    md_isbn = _clean_isbn(isbn_13) or _clean_isbn(isbn_10)
    md_asin_raw = (asin or "").strip().upper() or None
    # ABS frequently stores an ISBN-10 in the "asin" field. Reclassify before
    # using it as an identifier — otherwise an ISBN-shaped value would
    # contradict the book's real ASIN and trigger hard_reject even when title
    # and author agree.
    if md_asin_raw and not _ASIN_RE.match(md_asin_raw):
        if not md_isbn:
            md_isbn = _clean_isbn(md_asin_raw)
        md_asin = None
    else:
        md_asin = md_asin_raw

    # ---- Identifier ----
    identifier_present = bool(md_isbn or md_asin)
    identifier_agrees = (md_isbn and md_isbn in book_isbns) or (md_asin and md_asin in book_asins)
    if identifier_present:
        if identifier_agrees:
            evidence.positives.append(
                {
                    "name": f"{label}_identifier",
                    "weight": W_IDENTIFIER_MATCH,
                    "detail": f"isbn={md_isbn or '-'} asin={md_asin or '-'}",
                }
            )
            evidence.net_score += W_IDENTIFIER_MATCH
        elif (book_isbns or book_asins) and label == "embedded":
            # File-level identifier disagrees with the book → hard reject.
            # External catalogs (ABS/Booklore) often carry edition-specific
            # identifiers that legitimately differ from Hardcover's, so we
            # only treat embedded-tag disagreement as decisive.
            evidence.hard_reject = True
            evidence.hard_reject_reason = f"{label}_identifier_mismatch"
            return  # No further scoring once hard-rejected.

    # ---- Title agreement (graded high/med/low) ----
    # Graded so that integrations whose only signal is a partial title fuzz
    # still produce SOMETHING (the old Phase-3 fallback used to accept these).
    if title and book_title:
        # Symmetric series-strip when position evidence disagrees. Without
        # this, "Azarinth Healer Book 2" vs "Azarinth Healer (Book 1)" fuzzes
        # at 0.95 (raw strings only differ by " Book 2" vs " (Book 1)") and
        # grants +1.0, carrying a wrong attribution. The path side already
        # does this via _will_disagree_high (see evaluate_match).
        # Position disagreement: NONE of the metadata's positions match any
        # of the book's series positions. ABS commonly returns multiple
        # series pairs ("Stormlight Archive #5, Cosmere #19") and Hardcover
        # stores its own all_series — we check the cross product, so any
        # numbering scheme that aligns counts as agreement.
        meta_position_disagrees = False
        positions_to_check: list[float] = []
        if all_series_pairs:
            positions_to_check = [pos for _name, pos in all_series_pairs if pos is not None]
        if not positions_to_check and series_position is not None:
            positions_to_check = [series_position]
        if positions_to_check:
            all_positions = _all_book_positions(book)
            if all_positions:
                meta_position_disagrees = not any(
                    _position_matches_book(p, all_positions) for p in positions_to_check
                )
        meta_will_disagree = bool(path_will_disagree_high) or meta_position_disagrees

        title_for_fuzz = title
        book_title_for_fuzz = book_title
        if meta_will_disagree and book_series_name:
            sn_pat = _flexible_tolerant_pattern_cached(book_series_name)
            if sn_pat:

                def _strip_for_disagree(text: str) -> str:
                    """Strip series name + position markers when meta positions
                    disagree, so the remaining fuzz reflects only DISTINGUISHING
                    content. Catches cases where the book number is baked into
                    the title ('He Who Fights with Monsters 12: A LitRPG
                    Adventure' vs '...2: A LitRPG Adventure' — char fuzz=0.95
                    despite being unambiguously different books).
                    """
                    s = re.sub(rf"\b{sn_pat}\b", " ", text, flags=re.IGNORECASE)
                    # "Book N" / "Vol N" / "#N" / "Part N".
                    s = _EXPLICIT_VOL_RE.sub(" ", s)
                    # Leading "NN." / "NN -" / "NN_".
                    s = _LEADING_NUM_RE.sub(" ", s)
                    # Leading bare "NN:" or "NN: " — Hardcover/ABS use a colon
                    # separator between position and subtitle in titles like
                    # 'He Who Fights with Monsters 12: A LitRPG Adventure'.
                    # Existing _LEADING_NUM_RE only handles . - _, not :.
                    s = re.sub(r"^\s*\d{1,3}(?:\.\d+)?\s*:\s*", " ", s)
                    # Any remaining bare 1-3 digit token (e.g. mid-string
                    # 'Series 12 Subtitle' after series strip leaves '12').
                    s = _BARE_NUM_RE.sub(" ", s)
                    return re.sub(r"\s+", " ", s).strip(" :-")

                title_for_fuzz = _strip_for_disagree(title)
                book_title_for_fuzz = _strip_non_year_parens(_strip_for_disagree(book_title))
                book_title_for_fuzz = re.sub(r"\s+", " ", book_title_for_fuzz).strip()

        if not title_for_fuzz or not book_title_for_fuzz:
            # Both sides stripped to empty — the title IS the series name.
            # Two sub-cases:
            #   (a) Originals are identical → legit match (e.g. "Warbreaker"
            #       is both the title AND the series name).
            #   (b) Originals differ (e.g. "Azarinth Healer Book 2" vs
            #       "Azarinth Healer (Book 1)" both strip to bare series) →
            #       real conflict where two different books would otherwise
            #       fuzz to 1.0 via the series-name overlap alone.
            original_fuzz = _fuzz(title, book_title) if title and book_title else 0.0
            if original_fuzz >= TITLE_CORE_HIGH:
                evidence.positives.append(
                    {
                        "name": f"{label}_title_agree",
                        "weight": W_EMBEDDED_TITLE_AGREE,
                        "detail": f"'{title}' fuzz={original_fuzz:.2f} (title equals series name)",
                    }
                )
                evidence.net_score += W_EMBEDDED_TITLE_AGREE
            elif title and book_title:
                evidence.penalties.append(
                    {
                        "name": f"{label}_title_mismatch",
                        "weight": -P_TITLE_MISMATCH,
                        "detail": f"'{title}' vs '{book_title}' (series-only after strip)",
                    }
                )
                evidence.net_score -= P_TITLE_MISMATCH
        else:
            fuzz = _title_core_fuzz(
                title_for_fuzz,
                book_title_for_fuzz,
                series_name=book_series_name,
                author_name=author_name,
            )
            # Guard: when meta positions disagree AND the originals are
            # NEARLY identical (raw fuzz >= 0.90) AND the post-strip residues
            # are essentially identical, the original "match" was driven
            # entirely by series + position overlap — there's no distinguishing
            # content. Demote to mismatch.
            #
            # Real positive case (HWFWM 12 vs HWFWM 2): raw fuzz≈0.99 — the
            # originals differ only by a digit, and stripping makes them
            # identical → genuinely different books distinguished only by the
            # conflicting position.
            #
            # Real negative case ("Arcanum Unbounded" vs "Arcanum Unbounded:
            # The Cosmere Collection"): raw fuzz≈0.59 — the originals differ
            # by a real descriptive subtitle, not by a position. ABS and
            # Hardcover just count this in different sub-series (Cosmere
            # #8 vs Cosmere #18) — same book, cross-catalog numbering. The
            # raw fuzz threshold of 0.90 excludes this case so title_agree
            # fires correctly.
            raw_fuzz = _fuzz(title, book_title) if title and book_title else 0.0
            if meta_will_disagree and fuzz >= TITLE_CORE_HIGH and 0.90 <= raw_fuzz < 1.0:
                evidence.penalties.append(
                    {
                        "name": f"{label}_title_mismatch",
                        "weight": -P_TITLE_MISMATCH,
                        "detail": (
                            f"'{title}' vs '{book_title}' — distinguishing content "
                            f"stripped to identical residue; only the conflicting "
                            f"position differentiated the originals"
                        ),
                    }
                )
                evidence.net_score -= P_TITLE_MISMATCH
            elif fuzz >= TITLE_CORE_HIGH:
                evidence.positives.append(
                    {
                        "name": f"{label}_title_agree",
                        "weight": W_EMBEDDED_TITLE_AGREE,
                        "detail": f"'{title}' fuzz={fuzz:.2f}",
                    }
                )
                evidence.net_score += W_EMBEDDED_TITLE_AGREE
            elif fuzz >= TITLE_CORE_MED:
                evidence.positives.append(
                    {
                        "name": f"{label}_title_agree_med",
                        "weight": W_EMBEDDED_TITLE_AGREE_MED,
                        "detail": f"'{title}' fuzz={fuzz:.2f}",
                    }
                )
                evidence.net_score += W_EMBEDDED_TITLE_AGREE_MED
            elif fuzz >= TITLE_CORE_LOW:
                # LOW band: require content-token sequence equality to keep
                # the positive; otherwise demote to mismatch. Catches cases
                # where char-fuzz is inflated by stopwords or shared series
                # tokens, but no real content overlap exists.
                if _content_tokens_equal(
                    title_for_fuzz, book_title_for_fuzz, series_name=book_series_name
                ):
                    evidence.positives.append(
                        {
                            "name": f"{label}_title_agree_low",
                            "weight": W_EMBEDDED_TITLE_AGREE_LOW,
                            "detail": f"'{title}' fuzz={fuzz:.2f}",
                        }
                    )
                    evidence.net_score += W_EMBEDDED_TITLE_AGREE_LOW
                else:
                    evidence.penalties.append(
                        {
                            "name": f"{label}_title_mismatch",
                            "weight": -P_TITLE_MISMATCH,
                            "detail": f"'{title}' vs '{book_title}' fuzz={fuzz:.2f} (low-band content mismatch)",
                        }
                    )
                    evidence.net_score -= P_TITLE_MISMATCH
            elif fuzz < TITLE_CORE_MISMATCH:
                # Source/embedded title is actively different from the book title —
                # symmetric with the path-side title_mismatch penalty.
                evidence.penalties.append(
                    {
                        "name": f"{label}_title_mismatch",
                        "weight": -P_TITLE_MISMATCH,
                        "detail": f"'{title}' vs '{book_title}' fuzz={fuzz:.2f}",
                    }
                )
                evidence.net_score -= P_TITLE_MISMATCH

    # ---- Author agreement ----
    # The metadata source carries an author name (e.g. ABS "authorName" or
    # an EPUB <dc:creator>) — compare against the monitored entity's name
    # using the dot-tolerant author fuzz.  Single positive signal, no graded
    # weights: either the names match or they don't.
    if (
        metadata_author
        and author_name
        and _fuzz_author(metadata_author, author_name) >= AUTHOR_FUZZ_THRESHOLD
    ):
        evidence.positives.append(
            {
                "name": f"{label}_author_agree",
                "weight": W_EMBEDDED_AUTHOR_AGREE,
                "detail": f"'{metadata_author}'",
            }
        )
        evidence.net_score += W_EMBEDDED_AUTHOR_AGREE

    # ---- Series + position agreement / disagreement ----
    # The metadata may carry multiple (series_name, position) pairs (ABS:
    # "Stormlight Archive #5, Cosmere #19"). Cross-check every pair against
    # the book's all_series. ANY pair whose (name, pos) both match emits the
    # stronger `series_agree`; otherwise any pair with a matching position
    # alone emits `position_match`. Only when NO pair matches any of the
    # book's positions do we emit `position_disagree`. The singular
    # series_name/series_position arguments stay supported for callers that
    # don't supply multiple pairs (e.g. EmbeddedMetadata).
    pairs_to_check: list[tuple[str | None, float]] = []
    if all_series_pairs:
        pairs_to_check = [(name, pos) for name, pos in all_series_pairs if pos is not None]
    if not pairs_to_check and series_position is not None:
        pairs_to_check = [(series_name, series_position)]

    if pairs_to_check:
        all_positions = _all_book_positions(book)
        if not all_positions:
            all_positions = []

        # Find the best-matching pair: prefer (name match + pos match), then
        # (pos match only), then (no match).
        best_kind = None  # "series_agree" | "position_match" | None
        best_pair: tuple[str | None, float] | None = None
        for s_name, s_pos in pairs_to_check:
            if not _position_matches_book(s_pos, all_positions):
                continue
            # Position matches — check if series_name also matches.
            name_matches = bool(
                s_name
                and book_series_name
                and _fuzz(s_name, book_series_name) >= SERIES_NAME_FUZZ_THRESHOLD
            )
            if name_matches:
                best_kind, best_pair = "series_agree", (s_name, s_pos)
                break  # Stop on first full match — strongest signal.
            if best_kind is None:
                best_kind, best_pair = "position_match", (s_name, s_pos)

        if best_kind == "series_agree" and best_pair is not None:
            s_name, s_pos = best_pair
            pos_str = f"#{int(s_pos)}" if s_pos == int(s_pos) else f"#{s_pos:g}"
            evidence.positives.append(
                {
                    "name": f"{label}_series_agree",
                    "weight": W_EMBEDDED_SERIES_AGREE,
                    "detail": f"'{s_name}' {pos_str}",
                }
            )
            evidence.net_score += W_EMBEDDED_SERIES_AGREE
        elif best_kind == "position_match" and best_pair is not None:
            s_name, s_pos = best_pair
            pos_str = f"#{int(s_pos)}" if s_pos == int(s_pos) else f"#{s_pos:g}"
            evidence.positives.append(
                {
                    "name": f"{label}_position_match",
                    "weight": W_EMBEDDED_POSITION_MATCH,
                    "detail": f"{pos_str} (matches one of the book's series positions)",
                }
            )
            evidence.net_score += W_EMBEDDED_POSITION_MATCH
        elif book_spos is not None or all_positions:
            # No pair matched any book position — genuine disagreement.
            offered = sorted({round(p, 4) for _n, p in pairs_to_check})
            target = sorted(all_positions) if all_positions else [book_spos]
            evidence.penalties.append(
                {
                    "name": f"{label}_position_disagree",
                    "weight": -P_EMBEDDED_POSITION_DISAGREE,
                    "detail": f"{label}={offered} vs book positions {target}",
                }
            )
            evidence.net_score -= P_EMBEDDED_POSITION_DISAGREE


def evaluate_match(
    *,
    path: str | None,
    book: dict[str, Any],
    author_name: str | None = None,
    embedded: EmbeddedMetadata | None = None,
    source_metadata: SourceMetadata | None = None,
    decomposition: PathDecomposition | None = None,
) -> AttributionEvidence:
    """Score a single (file, book) pair, returning the full evidence vector.

    Pure function. No DB writes, no side effects.

    Inputs are independent — any combination of (``path``, ``embedded``,
    ``source_metadata``) may be supplied. Filesystem scans typically pass
    ``path`` + ``embedded``; ABS/Booklore integrations pass only
    ``source_metadata`` (no path → path-derived signals skipped). Empty or
    None path is treated as "no path" and contributes no path-based signals.
    """
    evidence = AttributionEvidence()

    if decomposition is None:
        decomp = decompose_path(path, author_name) if path else _empty_decomposition()
    else:
        decomp = decomposition
    leaf_stem = _name_without_ext(decomp.leaf) if decomp.leaf_is_file else decomp.leaf

    book_title = str(book.get("title") or "")
    book_series_name = str(book.get("series_name") or "").strip() or None
    book_spos = _book_series_position_float(book)

    # ---- Title core fuzz ----
    # Compute two versions of the candidate's title core:
    #   * stripped    — series_name removed; apples-to-apples vs other books in
    #                   the same series.
    #   * unstripped  — series_name preserved; useful as a tie-breaker when the
    #                   stripped form is empty on both sides (standalone book
    #                   named after its own series).
    #
    # When position evidence disagrees at high weight, we deliberately use the
    # *stripped* comparison only. Otherwise the series-prefix overlap (e.g.
    # "Rise of the Living Forge (Book 4)" vs "Rise of the Living Forge (Book 1)"
    # both reduce to the series name) would let title-fuzz override the
    # position penalty — exactly the failure mode we're fixing.
    title_for_name = decomp.leaf if decomp.leaf_is_file else decomp.book_folder or decomp.leaf
    title_core_stripped = extract_title_core(
        title_for_name,
        series_name=book_series_name,
        author_name=author_name,
    )
    title_core_unstripped = extract_title_core(
        title_for_name,
        series_name=None,
        author_name=author_name,
    )
    evidence.title_core = title_core_stripped or title_core_unstripped

    # Pre-compute position votes once; reused below for the position-evidence
    # section. We need them here to decide which title comparison to trust:
    # when a high-weight position vote disagrees with the book's series_position,
    # series-prefix title overlap shouldn't override that signal.
    votes = collect_position_votes(decomp, series_name=book_series_name)
    # Check votes against ALL series positions the book occupies (primary +
    # alternates from all_series), not just the primary series_position.
    # Books in multiple series have multiple "correct" position values.
    all_book_positions = _all_book_positions(book)
    _will_disagree_high = False
    if all_book_positions and votes:
        _will_disagree_high = any(
            v.weight == "high" and not _position_matches_book(v.value, all_book_positions)
            for v in votes
        ) and not any(
            v.weight == "high" and _position_matches_book(v.value, all_book_positions)
            for v in votes
        )

    if _will_disagree_high:
        # Strictly apples-to-apples — strip series_name from both sides.
        if title_core_stripped and book_title:
            book_residue = _strip_non_year_parens(book_title)
            if book_series_name:
                book_sn_pat = _flexible_word_pattern(book_series_name)
                book_residue = re.sub(
                    rf"^\s*{book_sn_pat}[\s\-:]+", " ", book_residue, flags=re.IGNORECASE
                )
                book_residue = re.sub(
                    rf"\b{book_sn_pat}\b", " ", book_residue, flags=re.IGNORECASE, count=1
                )
            book_residue = re.sub(r"\s+", " ", book_residue).strip()
            if book_residue:
                evidence.title_core_fuzz = _title_core_fuzz(
                    title_core_stripped,
                    book_residue,
                    series_name=book_series_name,
                    author_name=author_name,
                )
            else:
                evidence.title_core_fuzz = 0.0
        else:
            evidence.title_core_fuzz = 0.0
    else:
        # Normal flow: prefer stripped comparison; fall back to unstripped when
        # the stripped form is empty (lets standalone-series-named books and
        # bare-title file matches still score positively).
        if title_core_stripped and book_title:
            evidence.title_core_fuzz = _title_core_fuzz(
                title_core_stripped,
                book_title,
                series_name=book_series_name,
                author_name=author_name,
            )
        elif title_core_unstripped and book_title:
            evidence.title_core_fuzz = _title_core_fuzz(
                title_core_unstripped,
                book_title,
                series_name=book_series_name,
                author_name=author_name,
            )
        else:
            evidence.title_core_fuzz = 0.0

    if evidence.title_core_fuzz >= TITLE_CORE_HIGH:
        evidence.positives.append(
            {
                "name": "title_core_high",
                "weight": W_TITLE_CORE_HIGH,
                "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}",
            }
        )
        evidence.net_score += W_TITLE_CORE_HIGH
    elif evidence.title_core_fuzz >= TITLE_CORE_MED:
        evidence.positives.append(
            {
                "name": "title_core_med",
                "weight": W_TITLE_CORE_MED,
                "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}",
            }
        )
        evidence.net_score += W_TITLE_CORE_MED
    elif evidence.title_core_fuzz >= TITLE_CORE_LOW:
        # LOW band (0.55-0.70): char-fuzz can be inflated by shared stopwords
        # ("The X of Y") or series-name overlap. Require content-token
        # sequence equality to confirm a real partial match; otherwise demote
        # to title_mismatch.
        if _content_tokens_equal(evidence.title_core, book_title, series_name=book_series_name):
            evidence.positives.append(
                {
                    "name": "title_core_low",
                    "weight": W_TITLE_CORE_LOW,
                    "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}",
                }
            )
            evidence.net_score += W_TITLE_CORE_LOW
        elif evidence.title_core and book_title:
            evidence.penalties.append(
                {
                    "name": "title_mismatch",
                    "weight": -P_TITLE_MISMATCH,
                    "detail": f"'{evidence.title_core}' vs '{book_title}' fuzz={evidence.title_core_fuzz:.2f} (low-band content mismatch)",
                }
            )
            evidence.net_score -= P_TITLE_MISMATCH
    elif evidence.title_core_fuzz < TITLE_CORE_MISMATCH and evidence.title_core and book_title:
        # Both sides have a title to compare and they're actively different —
        # prevents same-series same-author books from passing the floor on path
        # signals alone.
        evidence.penalties.append(
            {
                "name": "title_mismatch",
                "weight": -P_TITLE_MISMATCH,
                "detail": f"'{evidence.title_core}' vs '{book_title}' fuzz={evidence.title_core_fuzz:.2f}",
            }
        )
        evidence.net_score -= P_TITLE_MISMATCH

    # ---- Author folder match ----
    if decomp.author_folder and author_name:
        af_ratio = _fuzz_author(decomp.author_folder, author_name)
        evidence.author_folder_ratio = af_ratio
        if af_ratio >= AUTHOR_FUZZ_THRESHOLD:
            evidence.author_folder_match = True
            evidence.positives.append(
                {
                    "name": "author_folder",
                    "weight": W_AUTHOR_FOLDER,
                    "detail": f"'{decomp.author_folder}' ratio={af_ratio:.2f}",
                }
            )
            evidence.net_score += W_AUTHOR_FOLDER

    # ---- Author trailer ----
    if _author_in_filename_trailer(leaf_stem, author_name):
        evidence.author_trailer_match = True
        evidence.positives.append(
            {"name": "author_trailer", "weight": W_AUTHOR_TRAILER, "detail": "author in filename"}
        )
        evidence.net_score += W_AUTHOR_TRAILER

    # ---- Series folder ----
    if decomp.series_folder and book_series_name:
        sf_ratio = _fuzz(decomp.series_folder, book_series_name)
        # Substring fallback: when the canonical series name is normalized
        # and fully contained inside the normalized folder name, treat as
        # strong match. Handles cases where the folder uses a fuller form
        # of the series name (e.g. "The Mistborn Saga_ The Original Trilogy"
        # vs canonical "The Mistborn Saga", or "Mistborn Era 2 Wax & Wayne"
        # vs canonical "Mistborn: Wax & Wayne"). Plain _fuzz drops below the
        # threshold when one side is much longer; substring containment is
        # the right signal here. Same fallback the series-in-filename check
        # uses below.
        folder_norm = _norm(decomp.series_folder)
        canon_norm = _norm(book_series_name)
        substring_hit = bool(canon_norm) and canon_norm in folder_norm
        # Reverse direction: canonical is the longer one (e.g. canonical
        # "Mistborn: The Original Trilogy" vs folder just "Mistborn"). Use
        # this only when the folder's normalized form is long enough to
        # carry meaning (3+ chars after norm) to avoid matching the empty
        # / 1-char case.
        reverse_substring_hit = (
            bool(folder_norm) and len(folder_norm) >= 3 and folder_norm in canon_norm
        )
        if sf_ratio >= SERIES_NAME_FUZZ_THRESHOLD or substring_hit or reverse_substring_hit:
            # When the fuzz ratio is the qualifier use it; when substring
            # containment rescues the match, report a synthetic 1.0.
            effective_ratio = max(
                sf_ratio, 1.0 if (substring_hit or reverse_substring_hit) else 0.0
            )
            evidence.series_folder_ratio = effective_ratio
            evidence.series_folder_match = True
            evidence.positives.append(
                {
                    "name": "series_folder",
                    "weight": W_SERIES_FOLDER,
                    "detail": f"'{decomp.series_folder}' ratio={effective_ratio:.2f}",
                }
            )
            evidence.net_score += W_SERIES_FOLDER
        else:
            evidence.series_folder_ratio = sf_ratio

    # ---- Series-name in filename ----
    if (
        book_series_name
        and decomp.leaf
        and _norm(book_series_name)
        and _norm(book_series_name) in _norm(decomp.leaf)
    ):
        evidence.series_in_filename = True
        evidence.positives.append(
            {
                "name": "series_in_filename",
                "weight": W_SERIES_IN_FILENAME,
                "detail": f"'{book_series_name}'",
            }
        )
        evidence.net_score += W_SERIES_IN_FILENAME

    # ---- Position votes ---- (votes already computed at the top of this
    # function for the title-fuzz disagreement check; reuse here.)
    evidence.position_votes = [
        {"value": v.value, "weight": v.weight, "source": v.source} for v in votes
    ]
    if all_book_positions and votes:
        # Group votes by whether they match ANY of the book's series positions.
        # Books in multiple series have multiple "correct" position values
        # (e.g. The Alloy of Law is Wax & Wayne #1, Mistborn Saga #4, and
        # Cosmere #8); a vote matching any one of them is an agreement.
        agree_high = any(
            v.weight == "high" and _position_matches_book(v.value, all_book_positions)
            for v in votes
        )
        agree_med = any(
            v.weight == "medium" and _position_matches_book(v.value, all_book_positions)
            for v in votes
        )
        disagree_high = any(
            v.weight == "high" and not _position_matches_book(v.value, all_book_positions)
            for v in votes
        )
        disagree_med = any(
            v.weight == "medium"
            and not _position_matches_book(v.value, all_book_positions)
            and not any(_position_matches_book(o.value, all_book_positions) for o in votes)
            for v in votes
        )

        evidence.position_agree_high = agree_high
        evidence.position_agree_med = agree_med
        evidence.position_disagree_high = disagree_high
        evidence.position_disagree_med = disagree_med

        # For the "value=..." display in the evidence panel: show the
        # primary book_spos when defined, otherwise the first matching pos.
        agree_display = (
            book_spos
            if book_spos is not None
            else (all_book_positions[0] if all_book_positions else None)
        )
        if agree_high:
            evidence.positives.append(
                {
                    "name": "position_agree_high",
                    "weight": W_POSITION_AGREE_HIGH,
                    "detail": f"value={agree_display}",
                }
            )
            evidence.net_score += W_POSITION_AGREE_HIGH
        elif agree_med:
            evidence.positives.append(
                {
                    "name": "position_agree_med",
                    "weight": W_POSITION_AGREE_MED,
                    "detail": f"value={agree_display}",
                }
            )
            evidence.net_score += W_POSITION_AGREE_MED

        if disagree_high and not agree_high:
            high_disagreeing = [
                v.value
                for v in votes
                if v.weight == "high" and not _position_matches_book(v.value, all_book_positions)
            ]
            # Penalty scales by two factors:
            #
            #  1. How many independent high-weight votes agree on the same wrong
            #     value. One vote → soft (legit cross-source numbering disagreement).
            #     Multiple agreeing votes → strong (the file is unambiguously
            #     labelled as a different book).
            #
            #  2. The title core agreement. When title fuzz is high, this is the
            #     "cross-source numbering disagreement" case (legit Hardcover vs
            #     ABS spos mismatch) → keep penalty soft. When title fuzz is low,
            #     nothing else points at this book and the wrong-position signal
            #     should dominate → heavier penalty so the attachment falls below
            #     the accept floor.
            #
            # Cap so ISBN/ASIN identifier (+2.0) can still rescue when present.
            counts = Counter(round(v, 4) for v in high_disagreeing)
            agreeing_wrong = max(counts.values()) if counts else 1
            base = P_POSITION_DISAGREE_HIGH + (agreeing_wrong - 1) * 0.5
            # Inverse-title scaling: weak title → +0.75, mid → +0.25, strong → 0.
            if evidence.title_core_fuzz < TITLE_CORE_LOW:
                base += 0.75
            elif evidence.title_core_fuzz < TITLE_CORE_HIGH:
                base += 0.25
            scaled_penalty = min(base, 1.75)
            evidence.penalties.append(
                {
                    "name": "position_disagree_high",
                    "weight": -scaled_penalty,
                    "detail": f"votes={high_disagreeing} (×{agreeing_wrong}) vs book #{book_spos}; title_fuzz={evidence.title_core_fuzz:.2f}",
                }
            )
            evidence.net_score -= scaled_penalty
        elif disagree_med and not agree_high and not agree_med:
            med_disagreeing = [
                v.value
                for v in votes
                if v.weight == "medium" and not _position_matches_book(v.value, all_book_positions)
            ]
            evidence.penalties.append(
                {
                    "name": "position_disagree_med",
                    "weight": -P_POSITION_DISAGREE_MED,
                    "detail": f"votes={med_disagreeing} vs book positions {all_book_positions}",
                }
            )
            evidence.net_score -= P_POSITION_DISAGREE_MED

    # ---- Embedded metadata ----
    if embedded is not None:
        evidence.embedded_metadata_used = True
        evidence.embedded_data = _metadata_to_dict(
            title=embedded.title,
            authors=embedded.authors,
            series_name=embedded.series_name,
            series_position=embedded.series_position,
            isbn_13=embedded.isbn_13,
            isbn_10=embedded.isbn_10,
            asin=embedded.asin,
            year=embedded.year,
        )
        _score_metadata_signals(
            evidence=evidence,
            book=book,
            book_title=book_title,
            book_series_name=book_series_name,
            book_spos=book_spos,
            author_name=author_name,
            title=embedded.title,
            metadata_author=(embedded.authors[0] if embedded.authors else None),
            series_name=embedded.series_name,
            series_position=embedded.series_position,
            isbn_13=embedded.isbn_13,
            isbn_10=embedded.isbn_10,
            asin=embedded.asin,
            label="embedded",
            path_will_disagree_high=_will_disagree_high,
        )

    # ---- External source metadata (ABS / Booklore adapters) ----
    if source_metadata is not None and not evidence.hard_reject:
        evidence.source_metadata_used = True
        evidence.source_data = _metadata_to_dict(
            title=source_metadata.title,
            authors=[source_metadata.author] if source_metadata.author else None,
            series_name=source_metadata.series_name,
            series_position=source_metadata.series_position,
            isbn_13=source_metadata.isbn_13,
            isbn_10=source_metadata.isbn_10,
            asin=source_metadata.asin,
            year=None,
            all_series_pairs=source_metadata.all_series_pairs or None,
            extra={"source_label": source_metadata.source_label or None},
        )
        _score_metadata_signals(
            evidence=evidence,
            book=book,
            book_title=book_title,
            book_series_name=book_series_name,
            book_spos=book_spos,
            author_name=author_name,
            title=source_metadata.title,
            metadata_author=source_metadata.author,
            series_name=source_metadata.series_name,
            series_position=source_metadata.series_position,
            all_series_pairs=source_metadata.all_series_pairs,
            isbn_13=source_metadata.isbn_13,
            isbn_10=source_metadata.isbn_10,
            asin=source_metadata.asin,
            label=f"source_{source_metadata.source_label}"
            if source_metadata.source_label
            else "source",
            path_will_disagree_high=_will_disagree_high,
        )

    # ---- Identifier match overrides title-mismatch penalties ----
    # An ISBN/ASIN match is a hard identity claim — if the file says it's
    # this book, a differing title field is metadata weirdness, not a
    # different book. Strip any title_mismatch penalties accumulated above.
    has_identifier_positive = any(p["name"].endswith("_identifier") for p in evidence.positives)
    if has_identifier_positive:
        retained_penalties = []
        for p in evidence.penalties:
            if p["name"].endswith("title_mismatch"):
                evidence.net_score -= p["weight"]  # weight is negative; subtracting un-applies it
            else:
                retained_penalties.append(p)
        evidence.penalties = retained_penalties

    # ---- Strong title disagreement on both sides = hard reject ----
    # The filename title AND the file's embedded/source metadata title both
    # actively disagree with the candidate book — the file itself is naming
    # a different book on two independent channels. An ISBN/ASIN match would
    # have stripped these penalties just above, so reaching here means we
    # have contradicting title evidence on both sides with no identifier
    # rescue. Symmetric with the identifier-mismatch hard-reject earlier.
    if not evidence.hard_reject:
        has_path_title_mismatch = any(p["name"] == "title_mismatch" for p in evidence.penalties)
        has_meta_title_mismatch = any(
            p["name"].endswith("_title_mismatch") and p["name"] != "title_mismatch"
            for p in evidence.penalties
        )
        if has_path_title_mismatch and has_meta_title_mismatch:
            evidence.hard_reject = True
            evidence.hard_reject_reason = "title_mismatch_both_sides"

    # ---- Title-borne position contradiction = hard reject ----
    # The filename contains a deliberate position marker (Book N / Vol N /
    # SeriesName N / Book Three / Vol IV) that disagrees with the book's
    # series_position(s), and no marker of the same kind agrees. These
    # sources are title-borne authoring choices, not local file-numbering.
    # `leading_num` ("01.") is excluded — that's a user's local convention
    # which legitimately differs from Hardcover's (Children-of-Ruin case).
    #
    # Position matching is checked against ALL of the book's series
    # positions (primary + alternates), so a book in multiple series with
    # different numbering in each won't be hard-rejected when the filename
    # uses one of the alternate numberings.
    if not evidence.hard_reject and not has_identifier_positive and all_book_positions and votes:
        title_borne_sources = {
            "explicit_marker",
            "after_series_name",
            "word_number_marker",
            "roman_marker",
        }
        title_borne_votes = [v for v in votes if v.source in title_borne_sources]
        if title_borne_votes:
            disagrees = [
                v
                for v in title_borne_votes
                if not _position_matches_book(v.value, all_book_positions)
            ]
            agrees = [
                v for v in title_borne_votes if _position_matches_book(v.value, all_book_positions)
            ]
            if disagrees and not agrees:
                evidence.hard_reject = True
                evidence.hard_reject_reason = "title_borne_position_mismatch"
                wrong_values = sorted({round(v.value, 4) for v in disagrees})
                evidence.penalties.append(
                    {
                        "name": "title_borne_position_mismatch",
                        "weight": 0.0,
                        "detail": (
                            f"title-borne votes={wrong_values} vs book positions "
                            f"{all_book_positions} (sources: {sorted({v.source for v in disagrees})})"
                        ),
                    }
                )

    # ---- Final decision: tier assignment ----
    # Three outcomes:
    #   confirmed  → auto-accept; counts toward "book is owned".
    #   candidate  → surface in the Possible Candidates UI for user review;
    #                does NOT count toward "owned" (missing-book searches
    #                still fire).
    #   rejected   → any hard_reject, OR no meaningful identity evidence.
    if evidence.hard_reject:
        evidence.tier = "rejected"
        evidence.accept = False
        evidence.confidence = 0.0
    else:
        # ---- Signal booleans for tier classification ----
        title_match_path_strong = any(
            p["name"] in ("title_core_high", "title_core_med") for p in evidence.positives
        )
        title_match_meta_strong = any(
            p["name"].endswith("_title_agree") or p["name"].endswith("_title_agree_med")
            for p in evidence.positives
        )
        title_mismatch_meta = any(
            p["name"].endswith("_title_mismatch") and p["name"] != "title_mismatch"
            for p in evidence.penalties
        )

        identifier_match = any(p["name"].endswith("_identifier") for p in evidence.positives)
        author_match = (
            evidence.author_folder_match
            or evidence.author_trailer_match
            or any(p["name"].endswith("_author_agree") for p in evidence.positives)
        )
        series_match = (
            evidence.series_folder_match
            or evidence.series_in_filename
            or any(p["name"].endswith("_series_agree") for p in evidence.positives)
        )
        # `position_disagree_high` is a RAW flag set whenever any high-weight
        # vote disagrees, regardless of whether agreement votes cancelled it
        # out. The actual scored signal is the penalty: it only emits when
        # there's a NET disagreement (disagree_high AND NOT agree_high). Use
        # penalty presence — not the raw flag — for compatibility checks, so
        # a spurious title-borne vote (e.g. "Part Two" inside "Sins of Our
        # Fathers 03 Part Two" extracting #2) doesn't demote an otherwise
        # clean match to candidate when the bulk of votes agree on the
        # actual book number.
        has_position_disagree_penalty = any(
            p["name"].endswith("_position_disagree") for p in evidence.penalties
        )
        position_match = (
            evidence.position_agree_high or evidence.position_agree_med
        ) and not has_position_disagree_penalty
        # Position is "compatible" when it doesn't disagree — either it
        # actively agrees, or there's no position evidence either way
        # (typical for files in a book sub-folder with no "01." prefix or
        # "(Book N)" marker). Used for the confirmed-tier check so a clean
        # title+author+series match doesn't get demoted to candidate just
        # because the file isn't position-numbered.
        position_does_not_disagree = not has_position_disagree_penalty

        # ---- Tier 1: Confirmed ----
        # Identifier (ISBN/ASIN) match is priority-1 — file is unambiguously
        # this book regardless of how the title/path is named.
        #
        # Without an identifier we need strong title agreement + author +
        # series, with position either actively agreeing or silent. The
        # third path covers ABS/Booklore integrations where there is no
        # filesystem path (path=None) and all evidence comes from the
        # external API's curated metadata.
        # Metadata-side position evidence: either the strong `*_series_agree`
        # (name AND position both match) OR the weaker `*_position_match`
        # (position matches but series name diverges — ABS / Booklore vs
        # Hardcover naming differences). Either is enough corroboration to
        # confirm a metadata-only attribution.
        meta_position_corroborates = any(
            p["name"].endswith("_series_agree") or p["name"].endswith("_position_match")
            for p in evidence.positives
        )
        confirmed = (
            identifier_match
            or (
                title_match_path_strong
                and author_match
                and series_match
                and position_does_not_disagree
                and not title_mismatch_meta
            )
            or (
                title_match_path_strong
                and title_match_meta_strong
                and author_match
                and position_does_not_disagree
                and not title_mismatch_meta
            )
            or (
                # Metadata-only confirmed: ABS / Booklore integrations send
                # curated title + series_position + author. Strong title +
                # author + position corroboration (series_agree or
                # position_match) is enough — series_name divergence between
                # external catalogs and Hardcover is too common to require.
                title_match_meta_strong and author_match and meta_position_corroborates
            )
        )

        # ---- Tier 2: Candidate ----
        # Real evidence but a notable weakness, contradiction, or missing
        # context — surface for user review. Hard-rejects (Layer 2) have
        # already short-circuited above.
        candidate = (not confirmed) and (
            # Strong title agreement on the path side + author match. Catches:
            #   - flat libraries (no series/position context)
            #   - cross-source numbering disagreement (Children-of-Ruin)
            #   - file mistagging on the metadata side
            (title_match_path_strong and author_match)
            # Same but title agreement comes from embedded/source metadata
            # instead of the path (ABS / Booklore integration paths).
            or (title_match_meta_strong and author_match)
            # Strong context (author + series + position) but title is weak
            # or absent on the path side.
            or (author_match and series_match and position_match)
        )

        if confirmed:
            evidence.tier = "confirmed"
            evidence.accept = True
        elif candidate:
            evidence.tier = "candidate"
            evidence.accept = False  # Candidates don't count toward "owned".
        else:
            evidence.tier = "rejected"
            evidence.accept = False

        # Adaptive confidence denominator: only count weights for signals
        # that COULD have fired for this (book, file) pair. The previous
        # static denominator over-penalised legitimate matches -- a
        # standalone non-fiction book (no series, no position) matched
        # perfectly on title + author would show as 41% confidence because
        # the denominator still included series, position, and identifier
        # weights that were never in scope.
        #
        # In-scope rules:
        #   * title fuzz       -- always in scope (every book has a title).
        #   * path-side author -- only when a filesystem path was supplied.
        #   * series signals   -- only when the book has a series_name.
        #   * position signals -- only when the book has at least one
        #                         known series position (primary or any
        #                         entry in all_series).
        #   * embedded fields  -- only when EmbeddedMetadata was supplied
        #                         AND the book carries the matching field
        #                         (e.g. don't count W_EMBEDDED_SERIES_AGREE
        #                         when the book has no series).
        #   * source fields    -- same shape as embedded, for ABS/Booklore.
        #   * identifier       -- only when the (book, source) pair has an
        #                         identifier match positive; otherwise the
        #                         book's identifier is opaque to scoring.
        has_path = bool(decomp.leaf)
        has_series = bool(book_series_name)
        has_positions = bool(all_book_positions)

        denom = W_TITLE_CORE_HIGH
        if has_path:
            denom += W_AUTHOR_FOLDER + W_AUTHOR_TRAILER
        if has_series:
            denom += W_SERIES_FOLDER + W_SERIES_IN_FILENAME
        if has_positions:
            denom += W_POSITION_AGREE_HIGH
        if evidence.embedded_metadata_used:
            denom += W_EMBEDDED_TITLE_AGREE
            if has_series:
                denom += W_EMBEDDED_SERIES_AGREE
        if evidence.source_metadata_used:
            denom += W_EMBEDDED_TITLE_AGREE
            if has_series:
                denom += W_EMBEDDED_SERIES_AGREE
        if identifier_match:
            denom += W_IDENTIFIER_MATCH

        evidence.confidence = max(0.0, min(1.0, evidence.net_score / max(denom, 1.0)))

    return evidence


def pick_best_attribution(
    *,
    path: str | None,
    books: Iterable[dict[str, Any]],
    author_name: str | None = None,
    embedded: EmbeddedMetadata | None = None,
    source_metadata: SourceMetadata | None = None,
) -> AttributionResult:
    """Evaluate every candidate book against the supplied evidence sources
    (path / embedded / source_metadata) and return the best result.

    Returns a result with ``book=None`` when no book passes the accept floor.

    ABS / Booklore integrations call with ``path=None`` and only
    ``source_metadata`` — they trust their API's curated metadata and don't
    re-derive signals from a remote path. Filesystem scans pass ``path`` and
    typically ``embedded`` as well.
    """
    # Decompose path once if provided; otherwise build an empty decomposition
    # so path-based signal blocks short-circuit cleanly.
    decomp = decompose_path(path, author_name) if path else _empty_decomposition()

    # Track the best confirmed and best candidate independently — confirmed
    # always wins; if no confirmed, the best candidate is surfaced.
    #
    # Tiebreaker on net_score: prefer the book with the highest title fuzz
    # (most precise title match). Necessary when a file's title is just the
    # series name (e.g. "The Primal Hunter - Zogarth (2022).m4b") — every
    # book in the series passes the title_core_high threshold at slightly
    # different fuzz values (Book 1 fuzz=1.00, Book 2 fuzz=0.94, Book 16
    # fuzz=0.92) but all yield the same weighted score. Without a
    # tiebreaker, iteration order determined the winner: Book 1 on one
    # database, Book 16 on another. The most-precise title match (Book 1's
    # fuzz=1.00 against the bare-series filename) is the principled winner.
    def _sort_key(ev: AttributionEvidence) -> tuple[float, float]:
        title_fuzz_best = ev.title_core_fuzz
        for p in ev.positives:
            if p["name"].endswith("_title_agree") or p["name"].endswith("_title_agree_med"):
                # Detail format: "'<title>' fuzz=N.NN[...]"
                m = re.search(r"fuzz=(\d+\.\d+)", p.get("detail", ""))
                if m:
                    title_fuzz_best = max(title_fuzz_best, float(m.group(1)))
        return (ev.net_score, title_fuzz_best)

    best_confirmed: tuple[tuple[float, float], AttributionEvidence, dict[str, Any]] | None = None
    best_candidate: tuple[tuple[float, float], AttributionEvidence, dict[str, Any]] | None = None
    all_evidence: list[tuple[float, AttributionEvidence, dict[str, Any]]] = []

    for book in books:
        ev = evaluate_match(
            path=path or "",
            book=book,
            author_name=author_name,
            embedded=embedded,
            source_metadata=source_metadata,
            decomposition=decomp,
        )
        all_evidence.append((ev.net_score, ev, book))
        if ev.hard_reject:
            continue
        key = _sort_key(ev)
        if ev.tier == "confirmed" and (best_confirmed is None or key > best_confirmed[0]):
            best_confirmed = (key, ev, book)
        elif ev.tier == "candidate" and (best_candidate is None or key > best_candidate[0]):
            best_candidate = (key, ev, book)

    chosen = best_confirmed or best_candidate

    if chosen is None:
        # No non-rejected candidate — return a "rejected" result with the
        # highest-scoring evidence for the "Why?" panel context.
        if all_evidence:
            all_evidence.sort(key=lambda x: x[0], reverse=True)
            _, top_ev, _ = all_evidence[0]
        else:
            top_ev = AttributionEvidence()
        return AttributionResult(
            book=None,
            confidence=0.0,
            evidence=top_ev,
            match_reason="v2_no_candidate",
            tier="rejected",
        )

    _, ev, book = chosen

    # Reason follows the source label when external metadata drove the match;
    # otherwise the structural-vs-identifier distinction from filesystem v2.
    has_identifier_match = any(p["name"].endswith("_identifier") for p in ev.positives)
    if source_metadata is not None and source_metadata.source_label:
        reason = f"{source_metadata.source_label}_match"
    elif has_identifier_match:
        reason = "v2_identifier"
    else:
        reason = "v2_structured"
    if ev.tier == "candidate":
        reason = f"{reason}_candidate"
    return AttributionResult(
        book=book,
        confidence=ev.confidence,
        evidence=ev,
        match_reason=reason,
        tier=ev.tier,
    )


def _empty_decomposition() -> PathDecomposition:
    """A PathDecomposition that contributes no path-based signals.

    Used when the caller has no meaningful path (ABS/Booklore integrations).
    """
    return PathDecomposition(
        leaf="",
        leaf_is_file=False,
        ext="",
        book_folder=None,
        series_folder=None,
        author_folder=None,
        full_path="",
    )
