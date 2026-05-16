"""Read embedded metadata from EPUB / M4B / MP3 files for attribution.

EPUB uses stdlib only (zipfile + defusedxml). Audio formats require `mutagen`
when available — when not installed, this module silently returns None for
audio files (graceful degradation).

All functions are read-only and bounded: corrupt files, locked files, missing
tags etc. all return None or empty fields without raising.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

logger = setup_logger(__name__)

# Optional dep — only used for audio formats. Graceful degradation when missing.
try:
    from mutagen import File as MutagenFile  # type: ignore[import-not-found]
    _MUTAGEN_AVAILABLE = True
except ImportError:
    MutagenFile = None  # type: ignore[assignment, misc]
    _MUTAGEN_AVAILABLE = False


# Defused XML for safe parsing of untrusted EPUB content.
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-not-found]
except ImportError:
    import xml.etree.ElementTree as ET  # type: ignore[no-redef]


EBOOK_EXTS = {".epub"}
AUDIO_EXTS = {".m4b", ".m4a", ".mp4", ".mp3", ".aax", ".aaxc"}

# Max bytes to read from EPUB OPF — sanity limit.
_MAX_OPF_BYTES = 2 * 1024 * 1024  # 2 MB


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(value))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _parse_series_position(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError):
        m = re.search(r"\d+(?:\.\d+)?", str(value))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------------------
# EPUB reader
# ---------------------------------------------------------------------------


def _read_epub_metadata(path: str) -> EmbeddedMetadata | None:
    try:
        with zipfile.ZipFile(path) as zf:
            # Locate the OPF file via META-INF/container.xml.
            try:
                with zf.open("META-INF/container.xml") as cf:
                    container_xml = cf.read(_MAX_OPF_BYTES)
            except KeyError:
                return None

            container_root = ET.fromstring(container_xml)
            opf_path = None
            for rf in container_root.iter():
                if rf.tag.endswith("rootfile") and rf.get("full-path"):
                    opf_path = rf.get("full-path")
                    break
            if not opf_path:
                return None

            try:
                with zf.open(opf_path) as of:
                    opf_xml = of.read(_MAX_OPF_BYTES)
            except KeyError:
                return None

            return _parse_opf(opf_xml)
    except (zipfile.BadZipFile, OSError, ET.ParseError):
        return None
    except Exception as exc:  # noqa: BLE001 — read-only diagnostic catch
        logger.debug("EPUB metadata read failed for %s: %s", path, exc)
        return None


def _parse_opf(opf_xml: bytes) -> EmbeddedMetadata:
    """Parse an OPF document into an EmbeddedMetadata."""
    meta = EmbeddedMetadata()
    try:
        root = ET.fromstring(opf_xml)
    except ET.ParseError:
        return meta

    # Iterate every element; check local-name to be namespace-agnostic.
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""
        text = (el.text or "").strip() if el.text else ""

        if tag == "title" and text and not meta.title:
            meta.title = text
        elif tag == "creator" and text:
            meta.authors.append(text)
        elif tag == "date" and text and meta.year is None:
            meta.year = _parse_year(text)
        elif tag == "identifier" and text:
            scheme = (el.get("scheme") or el.get("{http://www.idpf.org/2007/opf}scheme") or "").lower()
            cleaned = re.sub(r"[^0-9Xx]", "", text).upper()
            if scheme == "isbn" or ("isbn" in text.lower()):
                if len(cleaned) == 13 and meta.isbn_13 is None:
                    meta.isbn_13 = cleaned
                elif len(cleaned) == 10 and meta.isbn_10 is None:
                    meta.isbn_10 = cleaned
            elif "asin" in text.lower() or scheme == "asin":
                m = re.search(r"B[0-9A-Z]{9}", text.upper())
                if m and meta.asin is None:
                    meta.asin = m.group(0)
            else:
                # Heuristic: bare 13- or 10-char isbn-shaped value with no scheme
                if not meta.isbn_13 and len(cleaned) == 13:
                    meta.isbn_13 = cleaned
                elif not meta.isbn_10 and len(cleaned) == 10:
                    meta.isbn_10 = cleaned
        elif tag == "meta":
            name = (el.get("name") or "").lower()
            content = (el.get("content") or "").strip()
            if name == "calibre:series" and content and not meta.series_name:
                meta.series_name = content
            elif name == "calibre:series_index" and content and meta.series_position is None:
                meta.series_position = _parse_series_position(content)
            elif el.get("property") == "belongs-to-collection" and text and not meta.series_name:
                meta.series_name = text
            elif el.get("property") == "group-position" and text and meta.series_position is None:
                meta.series_position = _parse_series_position(text)

    # Amazon/Kindle EPUBs frequently encode series info inside the title rather
    # than in dedicated OPF fields, e.g. "Earthside (Quantum Earth Book 2)".
    # Parse the trailing parenthetical to recover series_name + series_position
    # AND clean up the title to its core form. Only acts when those fields are
    # still unset from earlier OPF parsing.
    if meta.title:
        m = re.search(
            r"\s*\(([^)]+?)\s+(?:book|vol(?:ume)?|part)\s+(\d+(?:\.\d+)?)\)\s*$",
            meta.title,
            re.IGNORECASE,
        )
        if m:
            parsed_series = m.group(1).strip()
            try:
                parsed_position = float(m.group(2))
            except ValueError:
                parsed_position = None
            if meta.series_name is None and parsed_series:
                meta.series_name = parsed_series
            if meta.series_position is None and parsed_position is not None:
                meta.series_position = parsed_position
            # Strip the parenthetical from the title so subsequent fuzz-compare
            # against the book title works.
            meta.title = re.sub(
                r"\s*\([^)]+?\s+(?:book|vol(?:ume)?|part)\s+\d+(?:\.\d+)?\)\s*$",
                "",
                meta.title,
                flags=re.IGNORECASE,
            ).strip()

    return meta


# ---------------------------------------------------------------------------
# Audio reader (M4B / MP3 / etc.)
# ---------------------------------------------------------------------------


def _read_audio_metadata(path: str) -> EmbeddedMetadata | None:
    if not _MUTAGEN_AVAILABLE:
        return None
    try:
        mf = MutagenFile(path)  # type: ignore[misc]
        if mf is None:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Audio metadata read failed for %s: %s", path, exc)
        return None

    meta = EmbeddedMetadata()
    tags = mf.tags or {}

    def _get(key: str) -> str | None:
        try:
            v = tags.get(key)
        except Exception:
            return None
        if v is None:
            return None
        # mutagen returns lists for MP4Tags, single values for ID3
        if isinstance(v, list):
            return str(v[0]) if v else None
        return str(v)

    # MP4 atom keys (M4B / M4A)
    title = _get("\xa9nam")
    artist = _get("\xa9ART")
    album = _get("\xa9alb")
    cprt = _get("cprt")
    year_raw = _get("\xa9day")

    # ID3 (MP3)
    if not title:
        title = _get("TIT2")
    if not artist:
        artist = _get("TPE1")
    if not album:
        album = _get("TALB")
    if not year_raw:
        year_raw = _get("TDRC") or _get("TYER")

    if title:
        meta.title = title
    if artist:
        meta.authors = [a.strip() for a in re.split(r"[,;/]", artist) if a.strip()]
    if album:
        meta.series_name = album
    if year_raw:
        meta.year = _parse_year(year_raw)

    # Custom mutagen freeform/MP4 tag handling for series + ASIN.
    # MP4 freeform atoms come in as "----:com.apple.iTunes:KEY"
    try:
        all_keys = list(tags.keys()) if tags else []
    except Exception:
        all_keys = []

    for k in all_keys:
        kl = str(k).lower()
        if "series" in kl and "part" not in kl:
            val = _get(k)
            if val and not meta.series_name:
                meta.series_name = val
        elif "series-part" in kl or "series_part" in kl or kl.endswith(":part") or kl.endswith("position"):
            val = _get(k)
            if val and meta.series_position is None:
                meta.series_position = _parse_series_position(val)
        elif "asin" in kl:
            val = _get(k)
            if val:
                m = re.search(r"B[0-9A-Z]{9}", val.upper())
                if m and not meta.asin:
                    meta.asin = m.group(0)
        elif "isbn" in kl:
            val = _get(k)
            if val:
                cleaned = re.sub(r"[^0-9Xx]", "", val).upper()
                if len(cleaned) == 13 and not meta.isbn_13:
                    meta.isbn_13 = cleaned
                elif len(cleaned) == 10 and not meta.isbn_10:
                    meta.isbn_10 = cleaned

    # ID3 TXXX:KEY frames carry custom metadata too
    for key in ("TXXX:SERIES", "TXXX:SERIES-PART", "TXXX:ASIN", "TXXX:ISBN"):
        val = _get(key)
        if not val:
            continue
        if "SERIES-PART" in key and meta.series_position is None:
            meta.series_position = _parse_series_position(val)
        elif "SERIES" in key and "PART" not in key and not meta.series_name:
            meta.series_name = val
        elif "ASIN" in key and not meta.asin:
            m = re.search(r"B[0-9A-Z]{9}", val.upper())
            if m:
                meta.asin = m.group(0)
        elif "ISBN" in key:
            cleaned = re.sub(r"[^0-9Xx]", "", val).upper()
            if len(cleaned) == 13 and not meta.isbn_13:
                meta.isbn_13 = cleaned
            elif len(cleaned) == 10 and not meta.isbn_10:
                meta.isbn_10 = cleaned

    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_embedded_metadata(path: str) -> EmbeddedMetadata | None:
    """Read embedded metadata from a file. Returns None on any error / unknown format.

    Caller should treat None as "no embedded evidence available" and proceed
    with path-based attribution only.
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext in EBOOK_EXTS:
        return _read_epub_metadata(str(p))
    if ext in AUDIO_EXTS:
        return _read_audio_metadata(str(p))
    return None


def build_metadata_cache(paths: list[str]) -> dict[str, EmbeddedMetadata]:
    """Eagerly read metadata for every path. Returns path → EmbeddedMetadata.

    Skipped paths (unreadable, unknown format, no metadata) are not in the dict.
    Used by the scanner to populate a per-scan cache that the integrations also
    consume.
    """
    cache: dict[str, EmbeddedMetadata] = {}
    for path in paths:
        meta = read_embedded_metadata(path)
        if meta is not None:
            cache[path] = meta
    return cache
