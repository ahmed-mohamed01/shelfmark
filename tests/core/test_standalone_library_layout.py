"""Standalone (one-off) SAVE TO layout and the per-author series-folder switch.

A one-off download from the book modal's SAVE TO bar always files under
``<root>/<Author>`` (the author folder is created here, so ``{Author}/`` is
stripped from the template). The "Organize into folders" checkbox then decides
the layout, reusing the existing post-processing for ebook-vs-audiobook:

    ebook     ON  → organize (series/naming from the global organize template)
    ebook     OFF → none (original filename, loose in the author folder)
    audiobook ON  → organize (series + per-book folder)
    audiobook OFF → organize, series folder stripped (own folder, no series)

Monitored authors keep their own per-entity template + ``series_folder`` switch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.monitored_operations import (
    entity_series_folder_enabled,
    standalone_author_folder,
    strip_author_prefix,
    strip_series_folder_segment,
)
from shelfmark.core.monitored_routes import enrich_release_for_monitored

EBOOK_ORGANIZE = "{Author}/{Series}/{Title} ({Year})"
AUDIOBOOK_ORGANIZE = "{Author}/{Series}/{Title}/{OriginalName}"


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "books"
    root.mkdir()
    return root


@pytest.fixture
def mock_user_db(allowed_root: Path) -> MagicMock:
    user_db = MagicMock()
    user_db.get_user_settings.return_value = {"MONITORED_EBOOK_ROOTS": [str(allowed_root)]}
    return user_db


@pytest.fixture
def config_values(allowed_root: Path) -> dict[str, str]:
    return {
        "DESTINATION": str(allowed_root),
        "DESTINATION_AUDIOBOOK": "",
        "FILE_ORGANIZATION": "organize",
        "FILE_ORGANIZATION_AUDIOBOOK": "organize",
        "TEMPLATE_ORGANIZE": EBOOK_ORGANIZE,
        "TEMPLATE_AUDIOBOOK_ORGANIZE": AUDIOBOOK_ORGANIZE,
        # monitored (separate flow)
        "MONITORED_EBOOK_TEMPLATE": "{Series}/{SeriesPosition} - {Title} - {Author}",
    }


@pytest.fixture
def mock_config(config_values: dict[str, str]):
    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default=None, **_kwargs: config_values.get(key, default)
    )
    with patch("shelfmark.core.config.config", config):
        yield config


def _payload(ct: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "direct_download",
        "source_id": "abc",
        "title": "Velocity Weapon",
        "author": "Megan E. O'Keefe",
        "content_type": ct,
        "extra": {},
    }
    payload.update(overrides)
    return payload


class TestStripAuthorPrefix:
    def test_removes_leading_author_segment(self):
        assert strip_author_prefix("{Author}/{Series}/{Title}") == "{Series}/{Title}"
        assert strip_author_prefix("{author}/{Title}") == "{Title}"

    def test_leaves_templates_without_the_prefix(self):
        assert strip_author_prefix("{Series}/{Title}") == "{Series}/{Title}"
        assert strip_author_prefix("{Title} - {Author}") == "{Title} - {Author}"


class TestStandaloneAuthorFolder:
    def test_appends_sanitized_author(self):
        assert standalone_author_folder("/books/", "Megan E. O'Keefe") == "/books/Megan E. O'Keefe"
        assert standalone_author_folder("/books", "AC/DC") == "/books/AC_DC"

    def test_skips_when_root_already_ends_in_author(self):
        assert standalone_author_folder("/books/Megan E. O'Keefe", "Megan E. O'Keefe") == (
            "/books/Megan E. O'Keefe"
        )

    def test_none_when_nothing_safe_to_join(self):
        assert standalone_author_folder("", "Author") is None
        assert standalone_author_folder("/books", "") is None
        assert standalone_author_folder("/books", "..") is None


class TestEntitySeriesFolderEnabled:
    def test_default_is_enabled(self):
        assert entity_series_folder_enabled(None) is True
        assert entity_series_folder_enabled({}) is True

    def test_only_explicit_false_disables(self):
        assert entity_series_folder_enabled({"series_folder": False}) is False
        assert entity_series_folder_enabled({"series_folder": True}) is True


class TestStripSeriesFolderSegment:
    def test_drops_the_series_folder_only(self):
        assert strip_series_folder_segment(AUDIOBOOK_ORGANIZE) == "{Author}/{Title}/{OriginalName}"


class TestEnrichStandaloneMatrix:
    def test_ebook_organize_on(self, mock_user_db, mock_config, allowed_root):
        result = enrich_release_for_monitored(
            _payload("ebook", organize=True, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        assert result["destination_override"] == f"{allowed_root.resolve()}/Megan E. O'Keefe"
        assert result["file_organization_override"] == "organize"
        assert result["template_override"] == "{Series}/{Title} ({Year})"

    def test_ebook_organize_off_is_none_original_filename(
        self, mock_user_db, mock_config, allowed_root
    ):
        result = enrich_release_for_monitored(
            _payload("ebook", organize=False, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        assert result["destination_override"] == f"{allowed_root.resolve()}/Megan E. O'Keefe"
        assert result["file_organization_override"] == "none"
        assert "template_override" not in result

    def test_audiobook_organize_on(self, mock_user_db, mock_config, allowed_root):
        result = enrich_release_for_monitored(
            _payload("audiobook", organize=True, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        assert result["destination_override"] == f"{allowed_root.resolve()}/Megan E. O'Keefe"
        assert result["file_organization_override"] == "organize"
        assert result["template_override"] == "{Series}/{Title}/{OriginalName}"

    def test_audiobook_organize_off_keeps_folder_drops_series(
        self, mock_user_db, mock_config, allowed_root
    ):
        result = enrich_release_for_monitored(
            _payload("audiobook", organize=False, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        # Own per-book folder (organize), but the {Series} folder is stripped.
        assert result["file_organization_override"] == "organize"
        assert result["template_override"] == "{Title}/{OriginalName}"

    def test_root_already_ending_in_author_is_not_doubled(
        self, mock_user_db, mock_config, allowed_root
    ):
        author_root = allowed_root / "Megan E. O'Keefe"
        author_root.mkdir()
        result = enrich_release_for_monitored(
            _payload("ebook", organize=True, destination_override=str(author_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        assert result["destination_override"] == str(author_root.resolve())

    def test_organize_flag_never_reaches_the_queue(self, mock_user_db, mock_config, allowed_root):
        result = enrich_release_for_monitored(
            _payload("ebook", organize=True, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        assert "organize" not in result

    def test_author_symlink_escaping_the_root_is_rejected(
        self, mock_user_db, mock_config, allowed_root, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        (allowed_root / "Megan E. O'Keefe").symlink_to(outside)
        result = enrich_release_for_monitored(
            _payload("ebook", organize=True, destination_override=str(allowed_root)),
            None,
            1,
            user_db=mock_user_db,
        )
        # The composed author dir resolves outside the root → not applied; the
        # template keeps {Author}/ so the author folder is still created.
        assert result["destination_override"] == str(allowed_root.resolve())
        assert result["template_override"] == EBOOK_ORGANIZE

    def test_no_organize_flag_leaves_layout_untouched(self, mock_user_db, mock_config):
        result = enrich_release_for_monitored(_payload("ebook"), None, 1, user_db=mock_user_db)
        assert "file_organization_override" not in result
        assert "template_override" not in result

    def test_organize_ignored_for_monitored_downloads(self, mock_user_db, mock_config):
        result = enrich_release_for_monitored(
            _payload("ebook", organize=True, monitored_entity_id=5),
            None,
            1,
            user_db=mock_user_db,
        )
        assert "file_organization_override" not in result
        assert "organize" not in result

    def test_does_not_mutate_caller_payload(self, mock_user_db, mock_config, allowed_root):
        payload = _payload("ebook", organize=True, destination_override=str(allowed_root))
        enrich_release_for_monitored(payload, None, 1, user_db=mock_user_db)
        assert payload["organize"] is True
        assert payload["destination_override"] == str(allowed_root)


class TestMonitoredSeriesFolder:
    @staticmethod
    def _monitored_db(settings: dict[str, object]) -> MagicMock:
        db = MagicMock()
        db.get_monitored_entity.return_value = {
            "id": 5,
            "kind": "author",
            "name": "Megan E. O'Keefe",
            "settings": settings,
        }
        db.list_monitored_books.return_value = []
        return db

    def test_entity_series_folder_off_drops_folder_keeps_metadata(self, mock_config):
        db = self._monitored_db(
            {"ebook_author_dir": "/books/Megan E. O'Keefe", "series_folder": False}
        )
        result = enrich_release_for_monitored(
            _payload("ebook", monitored_entity_id=5, series_name="Protectorate", series_position=1),
            db,
            1,
            user_db=None,
        )
        assert result["destination_override"] == "/books/Megan E. O'Keefe"
        assert result["series_name"] == "Protectorate"
        assert result["template_override"] == "{SeriesPosition} - {Title} - {Author}"
