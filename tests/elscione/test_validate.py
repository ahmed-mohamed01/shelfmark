"""Tests for Elscione download validation: EPUB/PDF structure and size checks."""

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.bypass.page_fetch import PageFetchResult
from shelfmark.release_sources.elscione import validate
from shelfmark.release_sources.elscione.index import IndexEntry
from tests.elscione import sample_index as sample


def _entry(size_bytes, fmt="epub"):
    return IndexEntry(
        path=f"x/y.{fmt}",
        root="R",
        name=f"y.{fmt}",
        folder="x",
        format=fmt,
        size_bytes=size_bytes,
        haystack="x y",
        volumes=None,
    )


def _result(size, content_type="application/epub+zip"):
    return PageFetchResult(
        path=Path("/tmp/ignored"),
        size=size,
        content_type=content_type,
        final_url="https://server.elscione.com/x",
        method="fetch",
    )


def _epub_bytes(mimetype="application/epub+zip", container=True, content=b"<html/>"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if mimetype is not None:
            archive.writestr("mimetype", mimetype)
        if container:
            archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.xhtml", content)
    return buffer.getvalue()


def _write(path, data):
    path.write_bytes(data)
    return len(data)


def test_valid_epub_passes(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes())
    assert validate.check(path, _entry(size), _result(size)) is None


def test_wrong_epub_mimetype_is_warning_only(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes(mimetype="application/x-epub+zip"))
    with sample.captured_logs(validate.logger) as messages:
        assert validate.check(path, _entry(size), _result(size)) is None
    assert any("mimetype" in message for message in messages)


def test_missing_container_xml_fails(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes(container=False))
    assert "META-INF/container.xml" in validate.check(path, _entry(size), _result(size))


def test_missing_zip_header_fails(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, b"definitely not a zip" * 10)
    assert "missing zip header" in validate.check(path, _entry(size), _result(size))


def test_valid_epub_emits_no_warning(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes())
    with sample.captured_logs(validate.logger) as messages:
        assert validate.check(path, _entry(size), _result(size)) is None
    assert not messages


def test_truncated_zip_fails(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes()[:30])
    assert "corrupt or unreadable" in validate.check(path, _entry(size), _result(size))


@pytest.mark.parametrize("error", [RuntimeError, NotImplementedError])
def test_unreadable_archive_fails_instead_of_raising(tmp_path, monkeypatch, error):
    # zipfile raises RuntimeError for encrypted entries and NotImplementedError
    # for unsupported compression; either must become a validation error, not
    # an exception that leaves the staged file behind.
    monkeypatch.setattr(
        zipfile.ZipFile,
        "testzip",
        lambda self: (_ for _ in ()).throw(error("unreadable archive")),
    )
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes())
    assert "corrupt or unreadable" in validate.check(path, _entry(size), _result(size))


def test_crc_failure_fails(tmp_path):
    data = bytearray(_epub_bytes(content=b"A" * 4096))
    # Flip a byte inside the stored (uncompressed) content run: the archive
    # structure stays valid but the CRC no longer matches.
    offset = data.index(b"A" * 64) + 10
    data[offset] = 0x42
    path = tmp_path / "book.epub"
    size = _write(path, bytes(data))
    assert "CRC" in validate.check(path, _entry(size), _result(size))


def test_valid_pdf_passes(tmp_path):
    path = tmp_path / "book.pdf"
    size = _write(path, b"%PDF-1.7\n" + b"page content " * 50 + b"\n%%EOF\n")
    assert validate.check(path, _entry(size, "pdf"), _result(size, "application/pdf")) is None


def test_pdf_without_header_fails(tmp_path):
    path = tmp_path / "book.pdf"
    size = _write(path, b"not a pdf at all " * 50)
    assert "missing header" in validate.check(
        path, _entry(size, "pdf"), _result(size, "application/pdf")
    )


def test_pdf_without_trailer_is_warning_only(tmp_path):
    path = tmp_path / "book.pdf"
    size = _write(path, b"%PDF-1.4\n" + b"page content " * 50)
    with sample.captured_logs(validate.logger) as messages:
        result = validate.check(path, _entry(size, "pdf"), _result(size, "application/pdf"))
    assert result is None
    assert any("trailer" in message for message in messages)


def test_index_size_mismatch_fails_and_refreshes(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes())
    refresh = MagicMock(return_value=True)
    with patch.object(validate, "start_background_refresh", refresh):
        error = validate.check(path, _entry(size + 1), _result(size))
    assert error == "size differs from index (index may be stale)"
    refresh.assert_called_once()


def test_bytes_written_mismatch_fails_without_refresh(tmp_path):
    path = tmp_path / "book.epub"
    size = _write(path, _epub_bytes())
    refresh = MagicMock(return_value=True)
    with patch.object(validate, "start_background_refresh", refresh):
        error = validate.check(path, _entry(size + 5), _result(size + 5))
    assert "the browser reported" in error
    refresh.assert_not_called()


def test_unsupported_format_fails(tmp_path):
    path = tmp_path / "book.mobi"
    size = _write(path, b"some mobi bytes")
    assert "unsupported format" in validate.check(path, _entry(size, "mobi"), _result(size))
