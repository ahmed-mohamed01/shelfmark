"""Validation of a downloaded Elscione file against its index entry.

page_fetch already refuses non-200s, HTML bodies and short reads, so what is left
here is: the bytes on disk must match what the fetch reported and what the index
promised, and the file must really be the EPUB or PDF its extension claims. A
size mismatch is a hard failure (the download could be a truncated or wrong
file) and also starts a background index refresh, since a stale index is the
usual cause.
"""

import zipfile
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.release_sources.elscione.index import IndexEntry, start_background_refresh

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.bypass.page_fetch import PageFetchResult

logger = setup_logger(__name__)

_EPUB_MAGIC = b"PK\x03\x04"
# Bytes, because zipfile.read() returns bytes; comparing against the str form
# warned on every valid EPUB.
_EPUB_MIMETYPE = b"application/epub+zip"
_PDF_HEADER = b"%PDF-"
_PDF_TRAILER = b"%%EOF"
# PDFs may carry a binary comment prologue before the header, and the trailer
# can sit inside the last kilobyte of cross-reference data.
_PDF_HEADER_WINDOW = 1024
_PDF_TRAILER_WINDOW = 2048


def check(path: Path, entry: IndexEntry, result: PageFetchResult) -> str | None:
    """Validate the downloaded file; return a user-facing error, or None when it passes.

    Warnings (wrong EPUB mimetype entry, missing PDF trailer) are logged and do
    not fail the download: converted files get the first wrong and the size
    equality already covers what the second would catch.
    """
    actual_bytes = path.stat().st_size
    if actual_bytes != result.size:
        return f"downloaded {actual_bytes} bytes but the browser reported {result.size}"

    if actual_bytes != entry.size_bytes:
        start_background_refresh()
        return "size differs from index (index may be stale)"

    if entry.format == "epub":
        return _check_epub(path)
    if entry.format == "pdf":
        return _check_pdf(path)
    return f"unsupported format: {entry.format}"


def _check_epub(path: Path) -> str | None:
    """Validate an EPUB: zip magic, CRC-clean archive, container.xml present."""
    with path.open("rb") as file:
        magic = file.read(len(_EPUB_MAGIC))
    if magic != _EPUB_MAGIC:
        return "not a valid EPUB (missing zip header)"

    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                return "EPUB failed its CRC check (file is corrupt)"
            names = set(archive.namelist())
            if "META-INF/container.xml" not in names:
                return "EPUB is missing META-INF/container.xml"
            if "mimetype" in names:
                mimetype = archive.read("mimetype").strip()
                if mimetype != _EPUB_MIMETYPE:
                    # Some converter output gets this wrong while the book is fine.
                    logger.warning(
                        "EPUB mimetype entry is %r, expected %r", mimetype, _EPUB_MIMETYPE
                    )
    except zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError:
        # BadZipFile/EOFError: corrupt or truncated; RuntimeError: encrypted
        # entries; NotImplementedError: unsupported compression; OSError: I/O.
        # None of these may escape check() and leave the staged file behind.
        return "not a valid EPUB (archive is corrupt or unreadable)"
    return None


def _check_pdf(path: Path) -> str | None:
    """Validate a PDF: header near the start; a missing trailer is only a warning."""
    data = path.read_bytes()
    if _PDF_HEADER not in data[:_PDF_HEADER_WINDOW]:
        return "not a valid PDF (missing header)"
    if _PDF_TRAILER not in data[-_PDF_TRAILER_WINDOW:]:
        logger.warning(
            "PDF is missing its %s trailer; size matched the index, so keeping it", "%%EOF"
        )
    return None
