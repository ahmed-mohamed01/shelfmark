"""Elscione catalogue index: fetch, flatten, persist and search.

server.elscione.com (and its mirror server.elsci.one) publishes a single
``index.json`` describing every file it hosts: a tree (or a list of trees) of
``{name, path, folders, files}`` nodes whose file entries carry
``{name, path, size}``. Plain HTTP reads the index fine once the clearance
cookies are replayed, while file downloads need the bypasser page, so the index
is fetched once, flattened into a path-keyed map, cached under
``CONFIG_DIR/elscione/`` and searched locally. Search therefore never waits on
the network and stays well inside the shared search deadline.

The raw JSON is persisted next to its metadata so a flatten-rule change applies
on the next load without a re-download, and the whole in-memory index is swapped
in a single assignment so a concurrent search never sees a half-built one.
"""

import ipaddress
import json
import re
import threading
import time
import unicodedata
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import requests

from shelfmark.bypass import cookie_store, page_fetch
from shelfmark.bypass.challenge import challenge_marker
from shelfmark.config.env import CONFIG_DIR, TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources import SourceUnavailableError

logger = setup_logger(__name__)

# Both hosts carry the same catalogue; the configured mirror list is the single
# source of truth for every URL this module builds, so no queue payload can
# ever direct a fetch at another host.
DEFAULT_MIRRORS = ["https://server.elscione.com", "https://server.elsci.one"]
ALLOWED_FORMATS = {"epub", "pdf"}
# A canonical mirror host: IDNA output is only lowercase ASCII letters, digits,
# hyphens and the label dots.
_MIRROR_HOST = re.compile(r"[a-z0-9.-]+")

_DIR_NAME = "elscione"
_RAW_INDEX_NAME = "index.json"
_META_NAME = "index_meta.json"

_MAX_INDEX_BYTES = 32 * 1024 * 1024
_MAX_DEPTH = 64
_STREAM_CHUNK_BYTES = 1024 * 1024
_FETCH_TIMEOUT = (10, 60)
# Budget for the in-page fallback: one challenge solve plus ~4 MB at the site's pace.
_IN_PAGE_TIMEOUT_SECONDS = 180

# Failure backoff: 30 min doubling to 6 h, so a down site cannot cause a retry
# storm, and the settings button stays the manual way out.
_BACKOFF_BASE_SECONDS = 30 * 60
_BACKOFF_MAX_SECONDS = 6 * 60 * 60
# Staleness is worth one WARNING per hour, not one per search.
_STALE_WARN_INTERVAL_SECONDS = 60 * 60
# Warn once the index is three refresh intervals old (spec 5.2).
_STALE_WARN_MULTIPLIER = 3


@dataclass(frozen=True)
class IndexEntry:
    """One offered file, keyed by its root-relative path (the release source_id)."""

    path: str
    root: str
    name: str
    folder: str
    format: str
    size_bytes: int
    haystack: str
    # Inclusive volume range declared by the file name, e.g. (1, 17) for
    # "Volume 01-17" or (2, 2) for "Vol 2". None when the name declares no
    # volume, so a volume-filtered search skips it instead of guessing.
    volumes: tuple[float, float] | None


@dataclass(frozen=True)
class FlattenResult:
    """Outcome of flattening a raw index: the entries plus per-reason skip counts."""

    entries: dict[str, IndexEntry]
    skipped: dict[str, int]
    # First few skipped values per reason, for the DEBUG log line only.
    samples: dict[str, list[str]]


def normalise(text: str) -> str:
    """Fold text for matching: NFKD, marks dropped, casefolded, alnum runs kept.

    Accented and full-width spellings then match their ASCII haystack ("Amazíng"
    vs "Amazing"), and punctuation noise collapses to single spaces.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    spaced = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(spaced.split())


_DIGIT_SPLIT = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key treating digit runs as numbers, so Volume 2 sorts before Volume 10."""
    key: list[tuple[int, int | str]] = []
    for part in _DIGIT_SPLIT.split(text):
        # Split with a capture group yields the digit runs and the text between
        # them; the tag keeps digits and text comparable to each other.
        if part.isdecimal():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


@dataclass(frozen=True)
class ParsedQuery:
    """A query split into series-name candidates plus the volumes it asked for."""

    series_variants: list[str]
    # The inclusive requested range: a scalar "Vol. 3" is (3, 3) and a range
    # marker "Vol. 1-3" is (1, 3). None when the query names no volume.
    volume_range: tuple[float, float] | None


# Bracketed annotations, e.g. "(Light Novel)" or "[Seven Seas]": noise for
# matching, because file names never repeat the metadata title's brackets.
_BRACKETED = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}")
# A volume number with an explicit end boundary: "v1beta" is not volume 1, and
# the atomic group cannot backtrack to a shorter prefix that would dodge it.
# The capture lives inside the atomic group, so the number is still readable.
_VOLUME_NUMBER = r"(?>(\d+(?:\.\d+)?))"
# Volume/Vol/v markers with an optional range, e.g. "Volume 01", "Vol. 3",
# "v03", "Volume 01-17".
_MAIN_VOLUME = re.compile(
    rf"\b(?:volumes?|vol\.?|v)\s*{_VOLUME_NUMBER}(?:\s*-\s*{_VOLUME_NUMBER})?\b",
    re.IGNORECASE,
)
# Book/Part markers, the weaker tier: they name a volume only when no
# Volume/Vol/v marker exists anywhere ("Part 2 Volume 4" is volume 4).
_PART_VOLUME = re.compile(
    rf"\b(?:parts?|books?)\s*{_VOLUME_NUMBER}(?:\s*-\s*{_VOLUME_NUMBER})?\b",
    re.IGNORECASE,
)
# Inputs are stripped, collapsed and capped before any regex runs (see
# parse_query), so a padded or runaway title cannot make them scan megabytes.
_QUERY_MAX_LENGTH = 512


def _trailing_number(text: str) -> tuple[float, tuple[int, int]] | None:
    """A bare trailing number after the title, e.g. "Some Series, 12".

    The number is scanned back from the end in one pass: a right-anchored
    regex would restart at every offset, which is quadratic on padded input.
    The text is already collapsed, so the number can only follow a space or a
    comma, and a string that is only the number is a title ("42"), not a
    volume.
    """
    end = len(text)
    start = end
    while start > 0 and text[start - 1].isdigit():
        start -= 1
    if start in (end, 0):
        return None
    if text[start - 1] == "." and start >= 2 and text[start - 2].isdigit():
        # One decimal point belongs to the number ("1.5").
        start -= 1
        while start > 0 and text[start - 1].isdigit():
            start -= 1
        if start == 0:
            return None
    if text[start - 1] not in ", ":
        return None
    return float(text[start:end]), (start, end)


def parse_query(text: str) -> ParsedQuery:
    """Parse a metadata-style title into series-name variants and a volume.

    BookOrbit and Hardcover send series titles like "7th Time Loop: The
    Villainess ... (Light Novel), Vol. 1" while the files are named
    "7th Time Loop - Volume 01 ...": the long form matches nothing under the
    all-terms rule. Bracketed segments are dropped and the volume marker is
    extracted and removed. A Volume/Vol/v marker anywhere is the volume, with
    any Book/Part markers left in the title text; Book/Part count only when no
    Volume/Vol/v marker exists, and a bare trailing number only when no
    explicit marker does.
    """
    cleaned = " ".join(text.split())[:_QUERY_MAX_LENGTH]
    cleaned = " ".join(_BRACKETED.sub(" ", cleaned).split())

    candidates = list(_MAIN_VOLUME.finditer(cleaned))
    if not candidates:
        candidates = list(_PART_VOLUME.finditer(cleaned))
    trailing = _trailing_number(cleaned) if not candidates else None
    # The last marker in the text wins; equal end positions go to the longer
    # span, so the explicit marker beats the bare number inside it.
    marker = max(candidates, key=lambda m: (m.end(), -m.start())) if candidates else None

    selected: tuple[tuple[float, float], tuple[int, int]] | None = None
    if marker is not None:
        low = float(marker.group(1))
        high = float(marker.group(2)) if marker.group(2) is not None else low
        selected = ((min(low, high), max(low, high)), marker.span())
    elif trailing is not None:
        selected = ((trailing[0], trailing[0]), trailing[1])
    volume_range: tuple[float, float] | None = None
    if selected is not None:
        requested, (start, end) = selected
        remainder = cleaned[:start] + cleaned[end:]
        if normalise(remainder):
            # A title that is only a marker ("42", "Vol. 1") stays a title;
            # stripping its only term would make the query unsearchable.
            volume_range = requested
            cleaned = remainder
    cleaned = " ".join(cleaned.split()).strip(", ")

    # Full title first (the metadata order), then the shorter forms; the colon
    # split needs the space so "Re:Zero" survives it. A shorter form must keep
    # at least two terms: a single word ("Magic") would match every series
    # sharing the name, which is not what a subtitle-bearing title asked for.
    parts = [cleaned]
    head, sep, _ = cleaned.partition(": ")
    if sep:
        parts.append(head)
    head, sep, _ = cleaned.partition(" - ")
    if sep:
        parts.append(head)
    variants: list[str] = []
    seen: set[str] = set()
    for position, part in enumerate(parts):
        key = normalise(part)
        if not key or key in seen or (position and len(key.split()) < 2):
            continue
        seen.add(key)
        variants.append(part)
    return ParsedQuery(series_variants=variants, volume_range=volume_range)


def entry_volumes(name: str) -> tuple[float, float] | None:
    """The inclusive volume range a file name declares, None when it does not.

    "Volume 01-17" covers every volume in 1..17, "Vol. 3" and "v03" are the
    single-volume (3, 3), and "Volume 1.5" keeps its decimal. The marker tiers
    follow parse_query: Volume/Vol/v beats Book/Part, which count only when no
    Volume/Vol/v marker exists. A name with no marker at all ("Complete")
    declares nothing, so a volume-filtered search skips it rather than guess.
    """
    match = _MAIN_VOLUME.search(name)
    if match is None:
        match = _PART_VOLUME.search(name)
    if match is None:
        return None
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) is not None else low
    return (min(low, high), max(low, high))


def _format_volume_range(volume_range: tuple[float, float] | None) -> str:
    """The volume part of the search log line: "3.0" or "1.0-3.0"."""
    if volume_range is None:
        return "None"
    low, high = volume_range
    if low == high:
        return str(low)
    return f"{low}-{high}"


def coerce_int(value: object, default: int, *, lo: int, hi: int) -> int:
    """Return an int config value clamped to ``[lo, hi]``, or ``default`` when unusable.

    ``bool`` is rejected explicitly because it subclasses ``int`` and a stray
    ``True`` would otherwise read as 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(lo, min(hi, value))


def max_file_bytes() -> int:
    """The per-download size cap, from ELSCIONE_MAX_FILE_MB."""
    megabytes = coerce_int(config.get("ELSCIONE_MAX_FILE_MB", 64), 64, lo=1, hi=512)
    return megabytes * 1024 * 1024


def refresh_interval_hours() -> int:
    """How often the index should refresh, from ELSCIONE_INDEX_REFRESH_HOURS."""
    return coerce_int(config.get("ELSCIONE_INDEX_REFRESH_HOURS", 24), 24, lo=6, hi=168)


def _normalise_mirror(entry: str) -> str | None:
    """Validate one configured mirror and reduce it to its ``https://<host>`` form.

    Only a bare https origin is acceptable: this base is prefixed onto every
    index and download URL this module builds, so a path, query, port or
    userinfo here would leak into those URLs. The whole parse-and-canonicalise
    step is exception-guarded because ``urlsplit`` itself, ``.port`` and IDNA
    encoding all raise on malformed input; one bad entry is dropped, never the
    whole list.
    """
    text = entry.strip()
    if not text:
        return None
    # No interior whitespace or control characters: a host can never contain
    # them, and several spellings urlsplit would accept are not hostnames.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        port = parsed.port
        if not host:
            return None
        # One trailing dot is legal (the DNS root); IDNA-encoding folds the
        # Unicode and punycode spellings of the same host onto one base.
        host = host.removesuffix(".")
        canonical = host.encode("idna").decode("ascii")
    except ValueError, UnicodeError:
        # e.g. "https://[" or a fullwidth character smuggled into the host.
        return None
    if parsed.scheme != "https":
        return None
    if port not in (None, 443):
        return None
    if "@" in parsed.netloc:
        # Even empty userinfo ("https://@host") is a login this source cannot
        # honour, and urlsplit reports it as a falsy username.
        return None
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None
    if not _MIRROR_HOST.fullmatch(canonical):
        # IDNA can map characters back onto URL separators (a fullwidth slash
        # becomes "/"), so the canonical form is checked to be a plain host.
        return None
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        pass
    else:
        # Mirrors are hostnames: an IP literal has no cookies, TLS name or
        # challenge story worth configuring.
        return None
    return f"https://{canonical}"


def mirrors() -> list[str]:
    """The configured mirrors, validated and de-duplicated, in preference order.

    ``ELSCIONE_MIRRORS`` arrives as a list from the tag-list settings field, or
    as a comma-separated string from a hand-edited config file; both forms are
    handled. Invalid entries are dropped with one WARNING; when nothing valid
    remains the two defaults stand in, so the source never loses its hosts.
    """
    raw = config.get("ELSCIONE_MIRRORS", DEFAULT_MIRRORS)
    if isinstance(raw, list):
        entries = [str(item) for item in raw]
    elif isinstance(raw, str):
        entries = raw.split(",")
    else:
        entries = []
    valid: list[str] = []
    invalid: list[str] = []
    for entry in entries:
        base = _normalise_mirror(entry)
        if base is None:
            if entry.strip():
                invalid.append(entry.strip())
        elif base not in valid:
            valid.append(base)
    if invalid:
        logger.warning("Ignoring invalid Elscione mirrors: %s", ", ".join(invalid))
    return valid or list(DEFAULT_MIRRORS)


def mirror_host(base: str) -> str:
    """The host a mirror's clearance cookies and user agent are stored under."""
    return urlsplit(base).hostname or ""


def origin_url(base: str) -> str:
    """The page a browser must clear before an in-page fetch or download works."""
    return base + "/"


def index_url(base: str) -> str:
    """Where a mirror publishes its catalogue."""
    return base + "/index.json"


def build_download_url(entry: IndexEntry, base: str) -> str:
    """Build one mirror's download URL for an entry.

    ``quote(seg, safe="")`` encodes ``!'()*`` as well, which matches the proven
    live URL for the ``!Yaoi`` tree. ``base`` always comes from the mirror
    config, so no payload can ever redirect a download elsewhere.
    """
    segments = [entry.root, *entry.path.split("/")]
    return base + "/" + "/".join(quote(segment, safe="") for segment in segments)


def _bad_segment(segment: str) -> bool:
    """Reject empty, dot, backslash or control-character path segments."""
    return (
        not segment
        or segment in (".", "..")
        or "\\" in segment
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in segment)
    )


def _file_stem(name: str) -> str:
    head, dot, _ = name.rpartition(".")
    return head if dot else name


def flatten_index(raw: object, *, max_bytes: int) -> FlattenResult:
    """Flatten a raw index tree into path-keyed entries, skipping (never failing on) bad data.

    A single root dict or a top-level list of roots is accepted. Every unusable
    node, entry or path is counted per reason and dropped: one hostile ``size``
    field must not cost the other 13k files. The walk is iterative with an
    explicit stack, so a deeply nested tree cannot hit the recursion limit; the
    depth cap bounds the stack itself.
    """
    entries: dict[str, IndexEntry] = {}
    skipped: dict[str, int] = {}
    samples: dict[str, list[str]] = {}

    def skip(reason: str, sample: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1
        bucket = samples.setdefault(reason, [])
        if len(bucket) < 5:
            bucket.append(sample[:80])

    roots: list[object] = raw if isinstance(raw, list) else [raw]
    # root_name of None marks "this node is itself a root"; children inherit the
    # resolved name instead.
    stack: list[tuple[object, int, str | None]] = [(root, 0, None) for root in roots]

    while stack:
        node, depth, inherited_root = stack.pop()
        if not isinstance(node, dict):
            skip("bad_node", repr(node))
            continue
        if depth > _MAX_DEPTH:
            skip("bad_node", f"depth > {_MAX_DEPTH}: {node.get('name', node)!r}")
            continue
        folders = node.get("folders")
        files = node.get("files")
        if not isinstance(folders, list) or not isinstance(files, list):
            skip("bad_node", str(node.get("name", node)))
            continue

        root_name = node.get("name") if inherited_root is None else inherited_root
        folder_name = node.get("name")
        # Reversed so the stack (LIFO) visits children in index order, which is
        # what "keep the first duplicate" depends on.
        inherited_root = root_name if isinstance(root_name, str) else None
        stack.extend((child, depth + 1, inherited_root) for child in reversed(folders))

        for file_entry in files:
            if not isinstance(file_entry, dict):
                skip("bad_entry", repr(file_entry))
                continue
            name = file_entry.get("name")
            path = file_entry.get("path")
            size = file_entry.get("size")
            if (
                not isinstance(name, str)
                or not isinstance(path, str)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
            ):
                skip("bad_entry", str(name or path))
                continue
            last_segment = path.split("/")[-1]
            extension = last_segment.rsplit(".", 1)[-1].lower() if "." in last_segment else ""
            if extension not in ALLOWED_FORMATS:
                skip("other_format", path)
                continue
            if size == 0:
                skip("empty", path)
                continue
            if size > max_bytes:
                skip("too_large", path)
                continue
            if not isinstance(root_name, str) or any(
                _bad_segment(seg) for seg in (root_name, *path.split("/"))
            ):
                skip("bad_path", path)
                continue
            if path in entries:
                skip("duplicate", path)
                continue
            # A file sitting directly in a root has no series folder to name it,
            # so its own stem stands in; deeper files take the holding folder's name.
            if depth == 0 or not isinstance(folder_name, str) or not folder_name:
                folder = _file_stem(name)
            else:
                folder = folder_name
            entries[path] = IndexEntry(
                path=path,
                root=root_name,
                name=name,
                folder=folder,
                format=extension,
                size_bytes=size,
                haystack=normalise(f"{folder} {name}"),
                volumes=entry_volumes(name),
            )

    return FlattenResult(entries=entries, skipped=skipped, samples=samples)


# --- Process-global index state, guarded by _STATE_LOCK -------------------------

_STATE_LOCK = threading.Lock()
_entries: dict[str, IndexEntry] = {}
_meta: dict[str, Any] = {}
_loaded = False  # whether the on-disk cache has been read (or found missing)
_refreshing = False
_failure_count = 0
_backoff_until = 0.0
_stale_warned_at = 0.0
# The mirror that last served an index or a download: its clearance cookies are
# the ones most likely still valid, so it is tried first next time.
_preferred_mirror: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _age_hours(fetched_at: object) -> float | None:
    """Hours since ``fetched_at`` (an ISO string), or None when unknown/unparseable."""
    if not isinstance(fetched_at, str):
        return None
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return (datetime.now(UTC) - fetched).total_seconds() / 3600


def _raw_index_path() -> Path:
    return Path(CONFIG_DIR) / _DIR_NAME / _RAW_INDEX_NAME


def _meta_path() -> Path:
    return Path(CONFIG_DIR) / _DIR_NAME / _META_NAME


def _write_meta_unlocked() -> None:
    """Persist metadata atomically; callers hold _STATE_LOCK."""
    _meta_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _meta_path().with_name(_META_NAME + ".tmp")
    try:
        tmp.write_text(json.dumps(_meta, indent=2), encoding="utf-8")
        tmp.replace(_meta_path())
    except OSError:
        # A failed persist must not leave its half-written tmp behind.
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _load_from_disk_unlocked() -> None:
    """Install the cached index from CONFIG_DIR, if one is there. Callers hold _STATE_LOCK.

    Re-flattening from the raw JSON (rather than persisting entries) is what
    makes a flatten-rule or size-cap change apply without a re-download.
    """
    global _entries, _meta
    try:
        meta = json.loads(_meta_path().read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            meta = {}
    except OSError, ValueError:
        meta = {}
    try:
        raw = json.loads(_raw_index_path().read_bytes())
    except OSError, ValueError:
        # Nothing (or nothing usable) on disk: the next search starts a refresh.
        return
    result = flatten_index(raw, max_bytes=max_file_bytes())
    _entries = result.entries
    _meta = meta
    _meta["entries"] = len(result.entries)
    _meta["skipped"] = result.skipped


def _ensure_loaded_unlocked() -> None:
    """Read the on-disk cache once per process, on first use rather than at startup."""
    global _loaded
    if not _loaded:
        _load_from_disk_unlocked()
        _loaded = True


def get_entry(path: str) -> IndexEntry | None:
    """Look up an entry by its root-relative path, or None when the index lacks it."""
    with _STATE_LOCK:
        _ensure_loaded_unlocked()
        return _entries.get(path)


def fetched_at_iso() -> str | None:
    """When the served index was fetched, ISO 8601, for release metadata."""
    with _STATE_LOCK:
        _ensure_loaded_unlocked()
        return _meta.get("fetched_at")


def index_status() -> dict[str, Any]:
    """Snapshot for the settings button and tests."""
    with _STATE_LOCK:
        _ensure_loaded_unlocked()
        return {
            "entries": len(_entries),
            "fetched_at": _meta.get("fetched_at"),
            "mirror": _meta.get("mirror"),
            "age_hours": _age_hours(_meta.get("fetched_at")),
            "refreshing": _refreshing,
            "last_error": _meta.get("last_error"),
            "last_attempt_at": _meta.get("last_attempt_at"),
            "skipped": dict(_meta.get("skipped") or {}),
        }


def set_preferred_mirror(base: str) -> None:
    """Sticky preference: remember the mirror that just served an index or file.

    Downloads and the next refresh then try it first; a mirror that is no
    longer configured is dropped by ``download_order``.
    """
    global _preferred_mirror
    with _STATE_LOCK:
        _preferred_mirror = base


def download_order() -> list[str]:
    """The mirrors to try: the sticky favourite first, then the configured order.

    Shared by the index refresh and the download handler so whichever mirror
    last worked is the first to get a request.
    """
    configured = mirrors()
    with _STATE_LOCK:
        preferred = _preferred_mirror
    if preferred is not None and preferred in configured:
        return [preferred, *(base for base in configured if base != preferred)]
    return configured


def search(query: str, max_results: int, *, allow_fallback: bool = True) -> list[IndexEntry]:
    """Search the index for the best series variant of ``query`` (spec 5.3).

    Each variant in turn applies the all-terms-substring match; the first with
    hits wins, and a requested volume range keeps only entries whose declared
    range overlaps it. ``allow_fallback`` False (a manual query: what the user
    typed) tries only the cleaned full title, because shortening it to a
    series name would silently widen the search to unrelated series. Raises
    SourceUnavailableError when no index is loaded or cached, after starting a
    background build; search never waits on the network itself.
    """
    with _STATE_LOCK:
        _ensure_loaded_unlocked()
        entries = _entries
        fetched_at = _meta.get("fetched_at")
    if not entries:
        start_background_refresh()
        msg = "Elscione index is being built, retry in about a minute"
        raise SourceUnavailableError(msg)

    interval = refresh_interval_hours()
    age = _age_hours(fetched_at)
    if age is None or age > interval:
        # Answer from what is served; the refresh lands in the background.
        start_background_refresh()
    _maybe_warn_stale(age, interval, fetched_at)

    # The variants go longest first; the first one with hits wins, so a query
    # never widens past what the metadata title itself matched, and a shorter
    # form needs at least two terms (parse_query) so it cannot fall back to a
    # single shared word.
    parsed = parse_query(query)
    variants = parsed.series_variants if allow_fallback else parsed.series_variants[:1]
    hits: list[IndexEntry] = []
    winner = variants[0] if variants else query
    for variant in variants:
        terms: list[str] = []
        for term in normalise(variant).split():
            if term not in terms:
                terms.append(term)
        if not terms:
            continue
        # Term-as-substring over the precomputed haystacks: ~13k normalised
        # strings, scanned in milliseconds.
        found = [
            entry for entry in entries.values() if all(term in entry.haystack for term in terms)
        ]
        if parsed.volume_range is not None:
            # Volumes were asked for: an entry must declare a range overlapping
            # them, so unnumbered files ("Complete", extras) cannot sneak in.
            low, high = parsed.volume_range
            found = [
                entry
                for entry in found
                if entry.volumes is not None
                and entry.volumes[0] <= high
                and low <= entry.volumes[1]
            ]
        if found:
            winner = variant
            hits = found
            break
    logger.info(
        "Searching Elscione for: %s (volume %s, %d hits)",
        winner,
        _format_volume_range(parsed.volume_range),
        len(hits),
    )
    hits.sort(key=lambda entry: (natural_key(entry.folder), natural_key(entry.name)))
    return hits[:max_results]


def _maybe_warn_stale(age: float | None, interval: int, fetched_at: object) -> None:
    """Log the stale-index WARNING at most once per hour (spec 5.2)."""
    global _stale_warned_at
    if age is None or age <= interval * _STALE_WARN_MULTIPLIER:
        return
    now = time.time()
    with _STATE_LOCK:
        if now - _stale_warned_at < _STALE_WARN_INTERVAL_SECONDS:
            return
        _stale_warned_at = now
        last_error = _meta.get("last_error")
    logger.warning(
        "Serving Elscione index from %s (%.0f h old); last refresh error: %s",
        fetched_at,
        age,
        last_error,
    )


# --- Refresh --------------------------------------------------------------------


@dataclass(frozen=True)
class _FetchResult:
    """One HTTP attempt for index.json."""

    kind: Literal["ok", "not_modified", "blocked", "error", "rate_limited"]
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    detail: str = ""


def _fetch_once(base: str) -> _FetchResult:
    """One plain-HTTP attempt for index.json from one mirror, with its cookies.

    Conditional headers go only to the mirror whose response produced them and
    only while an index is actually served: another mirror's validator names a
    different document, and a validator for a cache that flattens to nothing
    can only produce a wrong answer.
    """
    url = index_url(base)
    host = mirror_host(base)
    headers = {"Accept": "application/json"}
    user_agent = cookie_store.get_cf_user_agent_for_domain(host)
    if user_agent:
        headers["User-Agent"] = user_agent
    with _STATE_LOCK:
        etag = _meta.get("etag")
        last_modified = _meta.get("last_modified")
        etag_mirror = _meta.get("etag_mirror")
        usable = bool(_entries)
    # Conditional headers go only to the mirror whose response produced them
    # and only while an index is actually served: a validator proves the
    # *old* document is unchanged, which is worthless (and a 304 answered on
    # it a false success) when the cache in memory has nothing usable.
    if usable and etag_mirror == base:
        if isinstance(etag, str):
            headers["If-None-Match"] = etag
        if isinstance(last_modified, str):
            headers["If-Modified-Since"] = last_modified

    try:
        response = requests.get(
            url,
            stream=True,
            headers=headers,
            cookies=cookie_store.get_cf_cookies_for_domain(host),
            proxies=network.get_proxies(url),
            verify=network.get_ssl_verify(url),
            timeout=_FETCH_TIMEOUT,
        )
    except requests.RequestException as exc:
        return _FetchResult("error", detail=f"request failed: {exc}")

    with response:
        status = response.status_code
        if status == 304:
            if not usable:
                # Nothing in memory can be served, so "unchanged" is not a
                # success: this mirror failed and the next one gets a turn.
                return _FetchResult("error", detail="HTTP 304 with no usable index")
            return _FetchResult("not_modified")
        if status == 429:
            # Rate limiting is not a challenge to solve; back off and stop.
            network.note_rate_limited(url)
            return _FetchResult("rate_limited", detail="rate limited (HTTP 429)")
        if status in (403, 503):
            return _FetchResult("blocked", detail=f"HTTP {status}")
        if status != 200:
            return _FetchResult("error", detail=f"HTTP {status}")
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
                body += chunk
                if len(body) > _MAX_INDEX_BYTES:
                    return _FetchResult("error", detail="index exceeds the 32 MiB cap")
        except requests.RequestException as exc:
            return _FetchResult("error", detail=f"transfer failed: {exc}")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        if "html" in content_type:
            return _FetchResult(
                "blocked", detail="HTML body", etag=etag, last_modified=last_modified
            )
        marker = challenge_marker(bytes(body).decode("utf-8", errors="replace"))
        if marker:
            return _FetchResult(
                "blocked",
                detail=f"challenge marker {marker!r}",
                etag=etag,
                last_modified=last_modified,
            )
        return _FetchResult("ok", body=bytes(body), etag=etag, last_modified=last_modified)


def _fetch_in_page(base: str) -> _FetchResult:
    """Fetch index.json from inside one mirror's cleared browser page.

    The fallback for when plain HTTP stays blocked even after a fresh solve: from some
    egress IPs Cloudflare also scores the client itself (its TLS fingerprint), which
    replayed cookies cannot fix but a request made by Chrome passes. page_fetch writes
    the bytes straight to disk, so the truncation of Chrome's *rendered* JSON view
    does not apply.
    """
    dest = Path(TMP_DIR) / f"elscione-index-{uuid.uuid4().hex}.json"
    try:
        page_fetch.fetch_to_file(
            index_url(base),
            dest,
            origin_url=origin_url(base),
            max_bytes=_MAX_INDEX_BYTES,
            timeout=_IN_PAGE_TIMEOUT_SECONDS,
        )
        body = dest.read_bytes()
    except page_fetch.PageFetchError as exc:
        kind: Literal["blocked", "error"] = "blocked" if exc.reason == "blocked" else "error"
        return _FetchResult(kind, detail=f"in-page fetch failed ({exc.reason})")
    except OSError as exc:
        return _FetchResult("error", detail=f"in-page fetch could not be read: {exc}")
    finally:
        dest.unlink(missing_ok=True)
    return _FetchResult("ok", body=body)


def _attempt_mirror(base: str) -> _FetchResult:
    """One mirror's full chain: plain HTTP, one solve plus retry, in-page fetch.

    The result is an error (with the reason in ``detail``) when the mirror was
    exhausted, so the caller can fall over to the next one.
    """
    result = _fetch_once(base)
    if result.kind != "blocked":
        return result
    logger.info("Elscione index blocked on %s (%s); solving challenge", base, result.detail)
    try:
        cleared = downloader.get_bypassed_page(origin_url(base))
    except Exception as exc:  # noqa: BLE001 - any bypass failure means "still blocked"
        return _FetchResult(
            "error",
            detail=f"blocked ({result.detail}) and the challenge could not be cleared: {exc}",
        )
    if cleared is None:
        return _FetchResult(
            "error", detail=f"blocked ({result.detail}) and the challenge could not be cleared"
        )
    result = _fetch_once(base)
    if result.kind != "blocked":
        return result
    logger.info(
        "Elscione index still blocked over plain HTTP on %s (%s); fetching it in the browser",
        base,
        result.detail,
    )
    result = _fetch_in_page(base)
    if result.kind == "blocked":
        return _FetchResult("error", detail=f"blocked ({result.detail}) after one retry")
    return result


def _record_failure(error: str) -> None:
    """Keep the old index, record the error and set the doubling backoff."""
    global _failure_count, _backoff_until
    with _STATE_LOCK:
        _failure_count += 1
        wait = min(_BACKOFF_BASE_SECONDS * (1 << (_failure_count - 1)), _BACKOFF_MAX_SECONDS)
        _backoff_until = time.time() + wait
        _meta["last_error"] = error
        _meta["last_attempt_at"] = _now_iso()
        fetched_at = _meta.get("fetched_at") or "never"
        try:
            _write_meta_unlocked()
        except OSError:
            # Metadata persistence is best-effort; the in-memory state stands.
            logger.debug("Could not persist Elscione index metadata", exc_info=True)
    logger.warning("Elscione index refresh failed (%s); keeping index from %s", error, fetched_at)


def _install(result: _FetchResult, started: float, base: str) -> str | None:
    """Parse, sanity-check, persist and swap in a fetched index body.

    Returns the failure detail (without recording it) so the caller can fall
    over to the next mirror; None means the new index is installed and served.
    """
    global _entries, _meta, _failure_count, _backoff_until, _preferred_mirror
    try:
        raw = json.loads(result.body)
    except ValueError as exc:
        return f"index body is not valid JSON: {exc}"

    flattened = flatten_index(raw, max_bytes=max_file_bytes())
    for reason, samples in flattened.samples.items():
        logger.debug("Elscione index skipped entries (%s): %s", reason, ", ".join(samples))

    with _STATE_LOCK:
        current_count = len(_entries)
    # The sanity guard: a partial or garbage index must not wipe a good catalogue
    # (0 entries always fails; a first-ever build has nothing to compare against).
    if not flattened.entries:
        return "new index has no usable entries"
    if current_count and len(flattened.entries) * 2 < current_count:
        return (
            f"new index has {len(flattened.entries)} entries, "
            f"under half of the current {current_count}"
        )

    fetched_at = _now_iso()
    meta: dict[str, Any] = {
        "fetched_at": fetched_at,
        "mirror": base,
        # Conditional headers are only ever sent back to the mirror that produced
        # the validator (see _fetch_once).
        "etag_mirror": base if (result.etag or result.last_modified) else None,
        "etag": result.etag,
        "last_modified": result.last_modified,
        "entries": len(flattened.entries),
        "skipped": flattened.skipped,
        "last_attempt_at": fetched_at,
        "last_error": None,
    }
    directory = _raw_index_path().parent
    raw_path = _raw_index_path()
    raw_tmp = raw_path.with_name(_RAW_INDEX_NAME + ".tmp")
    meta_tmp = _meta_path().with_name(_META_NAME + ".tmp")
    backup = raw_path.with_name(_RAW_INDEX_NAME + ".bak")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        raw_tmp.write_bytes(result.body)
        meta_tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        # Replace index.json only after both tmp writes succeeded, and swap in
        # the metadata after it: a failure earlier leaves both disk files and
        # the in-memory state untouched instead of mixing new and old. The old
        # raw file is moved aside first so a metadata replace failure can put
        # the previous catalogue back; without the backup the disk would hold
        # new bytes while the refresh reports failure.
        if raw_path.exists():
            raw_path.replace(backup)
        raw_tmp.replace(raw_path)
        meta_tmp.replace(_meta_path())
    except OSError as exc:
        with suppress(OSError):
            raw_tmp.unlink(missing_ok=True)
            meta_tmp.unlink(missing_ok=True)
            if backup.exists():
                backup.replace(raw_path)
        return f"could not persist the index: {exc}"
    backup.unlink(missing_ok=True)

    with _STATE_LOCK:
        _entries = flattened.entries
        _meta.update(meta)
        _failure_count = 0
        _backoff_until = 0.0
        _preferred_mirror = base
    skipped_summary = ", ".join(f"{reason}={count}" for reason, count in flattened.skipped.items())
    logger.info(
        "Elscione index refreshed from %s: %d entries (%s skipped) in %.1fs",
        base,
        len(flattened.entries),
        skipped_summary or "none",
        time.monotonic() - started,
    )
    return None


def refresh_index() -> bool:
    """Run one refresh across the mirrors; the first success wins.

    Each mirror gets the same chain (plain HTTP, one solve plus retry, the
    in-page fallback), in ``download_order``. A 304 counts as success. A 429
    stops the whole refresh: rate limiting is not a mirror-specific challenge.
    Every failure keeps the old index (memory and disk) and backs off once for
    the whole refresh, not per mirror.
    """
    global _failure_count, _backoff_until, _preferred_mirror
    with _STATE_LOCK:
        # Load the disk cache first: a manual refresh before any search would
        # otherwise compare the new body against empty memory and skip the
        # sanity guard, letting a tiny response replace a large cache.
        _ensure_loaded_unlocked()
        _meta["last_attempt_at"] = _now_iso()

    started = time.monotonic()
    reasons: list[str] = []
    order = download_order()
    for position, base in enumerate(order):
        result = _attempt_mirror(base)
        if result.kind == "rate_limited":
            _record_failure(result.detail or result.kind)
            return False
        if result.kind == "not_modified":
            with _STATE_LOCK:
                _meta["fetched_at"] = _now_iso()
                _meta["mirror"] = base
                _meta["last_attempt_at"] = _meta["fetched_at"]
                _meta["last_error"] = None
                _failure_count = 0
                _backoff_until = 0.0
                _preferred_mirror = base
                try:
                    _write_meta_unlocked()
                except OSError:
                    logger.debug("Could not persist Elscione index metadata", exc_info=True)
            logger.info("Elscione index unchanged (HTTP 304) from %s", base)
            return True
        if result.kind == "ok":
            failure = _install(result, started, base)
            if failure is None:
                return True
            detail: str = failure
        else:
            detail = result.detail or result.kind
        reasons.append(f"{base}: {detail}")
        if position + 1 < len(order):
            logger.info(
                "Elscione mirror %s failed (%s); trying %s", base, detail, order[position + 1]
            )
    _record_failure(f"all mirrors failed ({'; '.join(reasons)})")
    return False


def _refresh_thread_main() -> None:
    global _refreshing
    try:
        refresh_index()
    except Exception:
        # refresh_index handles its own failures; this guards the thread itself
        # so a bug can never leave _refreshing stuck at True.
        logger.exception("Unexpected error refreshing the Elscione index")
        _record_failure("unexpected error during refresh (see log)")
    finally:
        with _STATE_LOCK:
            _refreshing = False


def start_background_refresh(*, force: bool = False) -> bool:
    """Start a single-flight refresh in a daemon thread.

    True when this call started one. Automatic triggers honour the failure
    backoff; the settings button passes force=True to run despite it.
    """
    global _refreshing
    with _STATE_LOCK:
        if _refreshing:
            return False
        if not force and time.time() < _backoff_until:
            return False
        _refreshing = True
    thread = threading.Thread(
        target=_refresh_thread_main, name="elscione-index-refresh", daemon=True
    )
    thread.start()
    return True
