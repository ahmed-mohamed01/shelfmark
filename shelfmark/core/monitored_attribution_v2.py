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
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

from shelfmark.core.monitored_files import normalize_match_text

# ---------------------------------------------------------------------------
# Tunables — initial values; revisit during fixture validation
# ---------------------------------------------------------------------------

ACCEPT_NET_SCORE_FLOOR = 1.5

# Positive contributions
W_TITLE_CORE_HIGH = 1.0       # title core fuzz >= 0.85
W_TITLE_CORE_MED = 0.6        # 0.70 <= fuzz < 0.85
W_TITLE_CORE_LOW = 0.3        # 0.55 <= fuzz < 0.70
W_AUTHOR_FOLDER = 0.8
W_AUTHOR_TRAILER = 0.4
W_SERIES_FOLDER = 0.8
W_SERIES_IN_FILENAME = 0.4
W_POSITION_AGREE_HIGH = 1.0
W_POSITION_AGREE_MED = 0.5
W_IDENTIFIER_MATCH = 2.0      # ISBN/ASIN match — strongest single positive
W_EMBEDDED_TITLE_AGREE = 1.0       # title fuzz >= TITLE_CORE_HIGH (0.85)
W_EMBEDDED_TITLE_AGREE_MED = 0.6   # 0.70 <= fuzz < 0.85
W_EMBEDDED_TITLE_AGREE_LOW = 0.3   # 0.55 <= fuzz < 0.70
W_EMBEDDED_SERIES_AGREE = 1.0
W_EMBEDDED_AUTHOR_AGREE = 0.6      # metadata author matches entity author (any source)

# Soft penalties
P_POSITION_DISAGREE_HIGH = 0.50
P_POSITION_DISAGREE_MED = 0.25
P_EMBEDDED_POSITION_DISAGREE = 0.50
P_WRONG_AUTHOR_FOLDER = 0.40

# Thresholds
# Lowered from 0.85 → 0.75 to handle dot/space variants like "DennisETaylor"
# matching "Dennis E. Taylor". Author folders are also normalized (dots/
# whitespace collapsed) before comparison.
AUTHOR_FUZZ_THRESHOLD = 0.75
SERIES_NAME_FUZZ_THRESHOLD = 0.75
TITLE_CORE_HIGH = 0.85
TITLE_CORE_MED = 0.70
TITLE_CORE_LOW = 0.55


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
_BARE_NUM_RE = re.compile(r"(?<![\d\.])(\d{1,3})(?!\.?\d)")

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

# Generic parenthetical that doesn't contain a 4-digit year or a book-marker —
# used to strip edition markers like "(Illustrated Edition)" from title core.
_PAREN_ANY_RE = re.compile(r"\s*\([^)]*\)\s*")

# Word-number map (one..ten → 1..10)
_WORD_NUMBER_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
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

    leaf: str                  # filename or leaf-folder name (no separators)
    leaf_is_file: bool         # True if leaf has a file extension
    ext: str                   # file extension lowercased (".epub" etc.) or ""
    book_folder: str | None    # immediate parent dir name
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
    """

    title: str | None = None
    author: str | None = None
    series_name: str | None = None
    series_position: float | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    asin: str | None = None
    source_label: str = ""


@dataclass
class AttributionEvidence:
    """Per-(file, book) evidence vector. Persisted to evidence_json column."""

    # Score components
    net_score: float = 0.0
    confidence: float = 0.0           # 0..1, derived from net_score
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

    # Positive / penalty itemization for the UI
    positives: list[dict[str, Any]] = field(default_factory=list)
    penalties: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AttributionResult:
    """Final attribution decision."""

    book: dict[str, Any] | None        # the chosen book row, or None on reject
    confidence: float
    evidence: AttributionEvidence
    match_reason: str                  # e.g. "v2_structured", "v2_identifier", "v2_rejected"


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


def _norm_author(s: str) -> str:
    """Normalise an author-name for fuzz comparison.

    Strips dots/whitespace entirely so e.g. ``Dennis E. Taylor`` (canonical) and
    ``DennisETaylor`` (folder name with stripped separators) become identical.
    Used only for author-folder/author-trailer comparisons where the same
    author name appears in stripped form in the filesystem.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _fuzz_author(a: str, b: str) -> float:
    """Author-specific fuzz that's tolerant of dot/space variants."""
    na, nb = _norm_author(a), _norm_author(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _fuzz(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalised strings."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


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
    """Return (isbns, asins) collected from book row's isbn_13/isbn_10/isbns/asins."""
    isbns: set[str] = set()
    asins: set[str] = set()

    for key in ("isbn_13", "isbn_10"):
        cleaned = _clean_isbn(book.get(key))
        if cleaned:
            isbns.add(cleaned)

    raw_isbns = book.get("isbns") or ""
    if isinstance(raw_isbns, str):
        for tok in re.split(r"[,;\s]+", raw_isbns):
            cleaned = _clean_isbn(tok)
            if cleaned:
                isbns.add(cleaned)

    raw_asins = book.get("asins") or ""
    if isinstance(raw_asins, str):
        for tok in re.split(r"[,;\s]+", raw_asins):
            tok = tok.strip().upper()
            if _ASIN_RE.match(tok):
                asins.add(tok)

    return isbns, asins


def _strip_year_paren(s: str) -> str:
    return _YEAR_PAREN_RE.sub("", s).strip()


def _strip_non_year_parens(s: str) -> str:
    """Strip parentheticals that don't look like a year. Leaves "(2024)" alone."""

    def keep(match: re.Match[str]) -> str:
        body = match.group(0)
        if re.search(r"\b(?:19|20)\d{2}\b", body):
            return body
        if re.search(r"\b(?:book|vol(?:ume)?|part|arc|tome)\b", body, re.IGNORECASE):
            # keep "(Book 4)" so position extraction still sees it
            return body
        return " "

    return _PAREN_ANY_RE.sub(keep, s).strip()


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
                below = dirs[i + 1:]
                if leaf_is_file:
                    # The file is the book. Whatever's between author and file
                    # is series (commonly 0 or 1 dir; deeper structures: take last).
                    series_folder = below[-1] if below else None
                    book_folder = series_folder  # the file's parent
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
    if (is_filename or is_book_folder):
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
        votes.extend(extract_position_signals(
            decomposition.leaf, series_name=series_name, is_filename=True,
        ))
        # Also extract from the parent (book_folder) when it differs from the leaf —
        # audiobooks may live in a positionally-named folder.
        if decomposition.book_folder and decomposition.book_folder != decomposition.leaf:
            votes.extend(extract_position_signals(
                decomposition.book_folder, series_name=series_name, is_book_folder=True,
            ))
    else:
        # Leaf IS the book folder (audiobook layout). Extract from it directly.
        votes.extend(extract_position_signals(
            decomposition.leaf, series_name=series_name, is_book_folder=True,
        ))
    if decomposition.series_folder and decomposition.series_folder != decomposition.book_folder:
        # Series folder rarely contains position info; only extract explicit markers.
        for v in extract_position_signals(decomposition.series_folder, series_name=series_name):
            if v.source in ("explicit_marker", "after_series_name", "word_number_marker"):
                votes.append(v)
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

    # Strip trailing "- Author" (or "- Author1, Author2")
    if author_name:
        a_norm = re.sub(r"\s+", " ", (author_name or "").replace(".", " ")).strip()
        if a_norm:
            a_pat = _flexible_word_pattern(a_norm)
        if a_norm and a_pat:
            s = re.sub(rf"\s*[-–—:]\s*{a_pat}\s*$", " ", s, flags=re.IGNORECASE)
            # Also a fallback for "Title - Author1, Author2"
            s = re.sub(
                rf"\s*[-–—]\s*[^-–—]*{a_pat}[^-–—]*$",
                " ",
                s,
                flags=re.IGNORECASE,
            )
            # And leading "Author - Title"
            s = re.sub(rf"^\s*{a_pat}\s*[-–—:]\s*", " ", s, flags=re.IGNORECASE)

    # Strip leading "NN. " / "NN - " / "NN_ "
    s = _LEADING_NUM_RE.sub(" ", s)

    # Strip explicit volume markers like "(Book 4)", "Book 4", "Vol 1.5", "#15"
    s = _EXPLICIT_VOL_RE.sub(" ", s)

    # Strip standalone decimal position tokens (e.g. "1.5" in "Cradle 1.5 Title")
    s = _DECIMAL_RE.sub(" ", s)

    # Strip series-name prefix (e.g. "Stormlight Archive 1 - The Way of Kings"
    # → "1 - The Way of Kings"). When the strip would leave the title core
    # empty (or just position/punctuation), let it empty — the evaluator
    # treats empty title_core as neutral, not as a strong positive.
    if series_name:
        sn_pat = _flexible_word_pattern(series_name)
        s = re.sub(rf"^\s*{sn_pat}[\s\-:]+", " ", s, flags=re.IGNORECASE)
        s = re.sub(rf"\b{sn_pat}\b", " ", s, flags=re.IGNORECASE, count=1)

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
    s = re.sub(r"\s+", " ", s).strip()

    return s


# ---------------------------------------------------------------------------
# Title comparison helpers
# ---------------------------------------------------------------------------


def _title_variants(title: str) -> list[str]:
    """Generate variants of a title for fuzzy comparison."""
    if not title:
        return []
    out = [title]
    colon_base = title.split(":", 1)[0].strip()
    if colon_base and colon_base.lower() != title.lower():
        out.append(colon_base)
    return out


def _title_core_fuzz(title_core: str, book_title: str) -> float:
    """Best fuzz between candidate title core and any variant of the book title."""
    if not title_core or not book_title:
        return 0.0
    best = 0.0
    for variant in _title_variants(book_title):
        best = max(best, _fuzz(title_core, variant))
    return best


# ---------------------------------------------------------------------------
# Author detection
# ---------------------------------------------------------------------------


def _author_in_filename_trailer(leaf_stem: str, author_name: str | None) -> bool:
    if not author_name:
        return False
    a_norm = re.sub(r"\s+", " ", author_name.replace(".", " ")).strip()
    a_pat = _flexible_word_pattern(a_norm)
    if re.search(rf"\s*[-–—:]\s*{a_pat}\s*(?:\(.*\))?\s*$", leaf_stem, re.IGNORECASE):
        return True
    if re.search(rf"^\s*{a_pat}\s*[-–—:]", leaf_stem, re.IGNORECASE):
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
    except (TypeError, ValueError):
        return None


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
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the verbatim metadata dict surfaced to the UI.

    Drops None / empty-string / empty-list fields but keeps numeric zero
    (series_position=0 is a legitimate prequel position).
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
    }
    if extra:
        raw.update(extra)
    return {k: v for k, v in raw.items() if v is not None and v != "" and v != []}


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
    identifier_agrees = (
        (md_isbn and md_isbn in book_isbns)
        or (md_asin and md_asin in book_asins)
    )
    if identifier_present:
        if identifier_agrees:
            evidence.positives.append({
                "name": f"{label}_identifier",
                "weight": W_IDENTIFIER_MATCH,
                "detail": f"isbn={md_isbn or '-'} asin={md_asin or '-'}",
            })
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
        fuzz = _title_core_fuzz(title, book_title)
        if fuzz >= TITLE_CORE_HIGH:
            evidence.positives.append({
                "name": f"{label}_title_agree",
                "weight": W_EMBEDDED_TITLE_AGREE,
                "detail": f"'{title}' fuzz={fuzz:.2f}",
            })
            evidence.net_score += W_EMBEDDED_TITLE_AGREE
        elif fuzz >= TITLE_CORE_MED:
            evidence.positives.append({
                "name": f"{label}_title_agree_med",
                "weight": W_EMBEDDED_TITLE_AGREE_MED,
                "detail": f"'{title}' fuzz={fuzz:.2f}",
            })
            evidence.net_score += W_EMBEDDED_TITLE_AGREE_MED
        elif fuzz >= TITLE_CORE_LOW:
            evidence.positives.append({
                "name": f"{label}_title_agree_low",
                "weight": W_EMBEDDED_TITLE_AGREE_LOW,
                "detail": f"'{title}' fuzz={fuzz:.2f}",
            })
            evidence.net_score += W_EMBEDDED_TITLE_AGREE_LOW

    # ---- Author agreement ----
    # The metadata source carries an author name (e.g. ABS "authorName" or
    # an EPUB <dc:creator>) — compare against the monitored entity's name
    # using the dot-tolerant author fuzz.  Single positive signal, no graded
    # weights: either the names match or they don't.
    if metadata_author and author_name:
        if _fuzz_author(metadata_author, author_name) >= AUTHOR_FUZZ_THRESHOLD:
            evidence.positives.append({
                "name": f"{label}_author_agree",
                "weight": W_EMBEDDED_AUTHOR_AGREE,
                "detail": f"'{metadata_author}'",
            })
            evidence.net_score += W_EMBEDDED_AUTHOR_AGREE

    # ---- Series + position agreement / disagreement ----
    if series_position is not None and book_spos is not None:
        if abs(series_position - book_spos) < 1e-6:
            if (
                series_name
                and book_series_name
                and _fuzz(series_name, book_series_name) >= SERIES_NAME_FUZZ_THRESHOLD
            ):
                # Render position as integer when it has no fractional part.
                if series_position == int(series_position):
                    pos_str = f"#{int(series_position)}"
                else:
                    pos_str = f"#{series_position:g}"
                evidence.positives.append({
                    "name": f"{label}_series_agree",
                    "weight": W_EMBEDDED_SERIES_AGREE,
                    "detail": f"'{series_name}' {pos_str}",
                })
                evidence.net_score += W_EMBEDDED_SERIES_AGREE
        else:
            evidence.penalties.append({
                "name": f"{label}_position_disagree",
                "weight": -P_EMBEDDED_POSITION_DISAGREE,
                "detail": f"{label}={series_position} vs book #{book_spos}",
            })
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
        title_for_name, series_name=book_series_name, author_name=author_name,
    )
    title_core_unstripped = extract_title_core(
        title_for_name, series_name=None, author_name=author_name,
    )
    evidence.title_core = title_core_stripped or title_core_unstripped

    # Pre-compute position votes once; reused below for the position-evidence
    # section. We need them here to decide which title comparison to trust:
    # when a high-weight position vote disagrees with the book's series_position,
    # series-prefix title overlap shouldn't override that signal.
    votes = collect_position_votes(decomp, series_name=book_series_name)
    _will_disagree_high = False
    if book_spos is not None and votes:
        _will_disagree_high = (
            any(abs(v.value - book_spos) > 1e-6 and v.weight == "high" for v in votes)
            and not any(abs(v.value - book_spos) < 1e-6 and v.weight == "high" for v in votes)
        )

    if _will_disagree_high:
        # Strictly apples-to-apples — strip series_name from both sides.
        if title_core_stripped and book_title:
            book_residue = _strip_non_year_parens(book_title)
            if book_series_name:
                book_sn_pat = _flexible_word_pattern(book_series_name)
                book_residue = re.sub(rf"^\s*{book_sn_pat}[\s\-:]+", " ", book_residue, flags=re.IGNORECASE)
                book_residue = re.sub(rf"\b{book_sn_pat}\b", " ", book_residue, flags=re.IGNORECASE, count=1)
            book_residue = re.sub(r"\s+", " ", book_residue).strip()
            if book_residue:
                evidence.title_core_fuzz = _title_core_fuzz(title_core_stripped, book_residue)
            else:
                evidence.title_core_fuzz = 0.0
        else:
            evidence.title_core_fuzz = 0.0
    else:
        # Normal flow: prefer stripped comparison; fall back to unstripped when
        # the stripped form is empty (lets standalone-series-named books and
        # bare-title file matches still score positively).
        if title_core_stripped and book_title:
            evidence.title_core_fuzz = _title_core_fuzz(title_core_stripped, book_title)
        elif title_core_unstripped and book_title:
            evidence.title_core_fuzz = _title_core_fuzz(title_core_unstripped, book_title)
        else:
            evidence.title_core_fuzz = 0.0

    if evidence.title_core_fuzz >= TITLE_CORE_HIGH:
        evidence.positives.append({"name": "title_core_high", "weight": W_TITLE_CORE_HIGH,
                                   "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}"})
        evidence.net_score += W_TITLE_CORE_HIGH
    elif evidence.title_core_fuzz >= TITLE_CORE_MED:
        evidence.positives.append({"name": "title_core_med", "weight": W_TITLE_CORE_MED,
                                   "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}"})
        evidence.net_score += W_TITLE_CORE_MED
    elif evidence.title_core_fuzz >= TITLE_CORE_LOW:
        evidence.positives.append({"name": "title_core_low", "weight": W_TITLE_CORE_LOW,
                                   "detail": f"'{evidence.title_core}' fuzz={evidence.title_core_fuzz:.2f}"})
        evidence.net_score += W_TITLE_CORE_LOW

    # ---- Author folder match ----
    if decomp.author_folder and author_name:
        af_ratio = _fuzz_author(decomp.author_folder, author_name)
        evidence.author_folder_ratio = af_ratio
        if af_ratio >= AUTHOR_FUZZ_THRESHOLD:
            evidence.author_folder_match = True
            evidence.positives.append({"name": "author_folder", "weight": W_AUTHOR_FOLDER,
                                       "detail": f"'{decomp.author_folder}' ratio={af_ratio:.2f}"})
            evidence.net_score += W_AUTHOR_FOLDER

    # ---- Author trailer ----
    if _author_in_filename_trailer(leaf_stem, author_name):
        evidence.author_trailer_match = True
        evidence.positives.append({"name": "author_trailer", "weight": W_AUTHOR_TRAILER,
                                   "detail": f"author in filename"})
        evidence.net_score += W_AUTHOR_TRAILER

    # ---- Series folder ----
    if decomp.series_folder and book_series_name:
        sf_ratio = _fuzz(decomp.series_folder, book_series_name)
        evidence.series_folder_ratio = sf_ratio
        if sf_ratio >= SERIES_NAME_FUZZ_THRESHOLD:
            evidence.series_folder_match = True
            evidence.positives.append({"name": "series_folder", "weight": W_SERIES_FOLDER,
                                       "detail": f"'{decomp.series_folder}' ratio={sf_ratio:.2f}"})
            evidence.net_score += W_SERIES_FOLDER

    # ---- Series-name in filename ----
    if book_series_name and decomp.leaf:
        if _norm(book_series_name) and _norm(book_series_name) in _norm(decomp.leaf):
            evidence.series_in_filename = True
            evidence.positives.append({"name": "series_in_filename", "weight": W_SERIES_IN_FILENAME,
                                       "detail": f"'{book_series_name}'"})
            evidence.net_score += W_SERIES_IN_FILENAME

    # ---- Position votes ---- (votes already computed at the top of this
    # function for the title-fuzz disagreement check; reuse here.)
    evidence.position_votes = [
        {"value": v.value, "weight": v.weight, "source": v.source} for v in votes
    ]
    if book_spos is not None and votes:
        # Group votes by agree/disagree at high/medium weights
        agree_high = any(abs(v.value - book_spos) < 1e-6 and v.weight == "high" for v in votes)
        agree_med = any(abs(v.value - book_spos) < 1e-6 and v.weight == "medium" for v in votes)
        disagree_high = any(abs(v.value - book_spos) > 1e-6 and v.weight == "high" for v in votes)
        disagree_med = any(
            abs(v.value - book_spos) > 1e-6 and v.weight == "medium"
            and not any(abs(o.value - book_spos) < 1e-6 for o in votes)
            for v in votes
        )

        evidence.position_agree_high = agree_high
        evidence.position_agree_med = agree_med
        evidence.position_disagree_high = disagree_high
        evidence.position_disagree_med = disagree_med

        if agree_high:
            evidence.positives.append({"name": "position_agree_high", "weight": W_POSITION_AGREE_HIGH,
                                       "detail": f"value={book_spos}"})
            evidence.net_score += W_POSITION_AGREE_HIGH
        elif agree_med:
            evidence.positives.append({"name": "position_agree_med", "weight": W_POSITION_AGREE_MED,
                                       "detail": f"value={book_spos}"})
            evidence.net_score += W_POSITION_AGREE_MED

        if disagree_high and not agree_high:
            high_disagreeing = [v.value for v in votes if v.weight == "high" and abs(v.value - book_spos) > 1e-6]
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
            evidence.penalties.append({"name": "position_disagree_high",
                                       "weight": -scaled_penalty,
                                       "detail": f"votes={high_disagreeing} (×{agreeing_wrong}) vs book #{book_spos}; title_fuzz={evidence.title_core_fuzz:.2f}"})
            evidence.net_score -= scaled_penalty
        elif disagree_med and not agree_high and not agree_med:
            med_disagreeing = [v.value for v in votes if v.weight == "medium" and abs(v.value - book_spos) > 1e-6]
            evidence.penalties.append({"name": "position_disagree_med",
                                       "weight": -P_POSITION_DISAGREE_MED,
                                       "detail": f"votes={med_disagreeing} vs book #{book_spos}"})
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
            isbn_13=source_metadata.isbn_13,
            isbn_10=source_metadata.isbn_10,
            asin=source_metadata.asin,
            label=f"source_{source_metadata.source_label}" if source_metadata.source_label else "source",
        )

    # ---- Final decision ----
    if evidence.hard_reject:
        evidence.accept = False
        evidence.confidence = 0.0
    else:
        # Strong-evidence auto-accept: two independent confirming signals
        # (strong title fuzz + author confirmation) carry the attribution even
        # when the net score is below the standard floor.  This rescues flat
        # libraries (publisher-as-top-folder like Blinkist/Graphicaudio) where
        # author_folder doesn't fire and series_folder doesn't exist, but the
        # filename clearly names the right book and its author.
        #
        # When position evidence contradicts at any source, this override is
        # suppressed — we still want the wrong-book-number penalty to bite.
        any_position_disagree = (
            evidence.position_disagree_high
            or any(p["name"].endswith("_position_disagree") for p in evidence.penalties)
        )
        strong_title = evidence.title_core_fuzz >= TITLE_CORE_HIGH
        author_confirmed = evidence.author_folder_match or evidence.author_trailer_match
        strong_override = (
            strong_title and author_confirmed and not any_position_disagree
        )

        evidence.accept = (
            evidence.net_score >= ACCEPT_NET_SCORE_FLOOR
            or strong_override
        )

        # Bounded confidence: divide by a plausible max-positive sum.
        # Only include weight categories that actually contributed, so
        # confidence stays well-distributed across attribution styles.
        denom = (
            W_TITLE_CORE_HIGH + W_AUTHOR_FOLDER + W_AUTHOR_TRAILER
            + W_SERIES_FOLDER + W_SERIES_IN_FILENAME + W_POSITION_AGREE_HIGH
        )
        if evidence.embedded_metadata_used:
            denom += W_EMBEDDED_TITLE_AGREE + W_EMBEDDED_SERIES_AGREE
        if evidence.source_metadata_used:
            denom += W_EMBEDDED_TITLE_AGREE + W_EMBEDDED_SERIES_AGREE
        if any(p["name"].endswith("_identifier") for p in evidence.positives):
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

    best: tuple[float, AttributionEvidence, dict[str, Any]] | None = None
    all_evidence: list[tuple[float, AttributionEvidence, dict[str, Any]]] = []

    for book in books:
        ev = evaluate_match(
            path=path or "", book=book, author_name=author_name,
            embedded=embedded, source_metadata=source_metadata,
            decomposition=decomp,
        )
        all_evidence.append((ev.net_score, ev, book))
        if ev.hard_reject:
            continue
        if best is None or ev.net_score > best[0]:
            best = (ev.net_score, ev, book)

    if best is None:
        # No non-rejected candidate — return a "rejected" result with the
        # highest-scoring evidence for the "Why?" panel context.
        if all_evidence:
            all_evidence.sort(key=lambda x: x[0], reverse=True)
            _, top_ev, _ = all_evidence[0]
        else:
            top_ev = AttributionEvidence()
        return AttributionResult(
            book=None, confidence=0.0, evidence=top_ev,
            match_reason="v2_no_candidate",
        )

    _, ev, book = best
    if not ev.accept:
        return AttributionResult(
            book=None, confidence=ev.confidence, evidence=ev,
            match_reason="v2_below_floor",
        )

    # Reason follows the source label when external metadata drove the match;
    # otherwise the structural-vs-identifier distinction from filesystem v2.
    has_identifier_match = any(p["name"].endswith("_identifier") for p in ev.positives)
    if source_metadata is not None and source_metadata.source_label:
        reason = f"{source_metadata.source_label}_match"
    elif has_identifier_match:
        reason = "v2_identifier"
    else:
        reason = "v2_structured"
    return AttributionResult(book=book, confidence=ev.confidence, evidence=ev, match_reason=reason)


def _empty_decomposition() -> PathDecomposition:
    """A PathDecomposition that contributes no path-based signals.

    Used when the caller has no meaningful path (ABS/Booklore integrations).
    """
    return PathDecomposition(
        leaf="", leaf_is_file=False, ext="",
        book_folder=None, series_folder=None, author_folder=None,
        full_path="",
    )
