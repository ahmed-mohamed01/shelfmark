"""Save-location handling for standalone (non-monitored) downloads.

``queue_release()`` applies ``destination_override`` verbatim, so the value a
browser sends must be proven to sit inside one of the user's allowed roots
before it ever reaches the orchestrator. These tests pin that boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.monitored_routes import (
    author_folder_exists,
    enrich_release_for_monitored,
    resolve_requested_destination,
)


@pytest.fixture
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "books"
    (root / "scifi").mkdir(parents=True)
    return root


@pytest.fixture
def mock_user_db(allowed_root: Path) -> MagicMock:
    user_db = MagicMock()
    user_db.get_user_settings.return_value = {"MONITORED_EBOOK_ROOTS": [str(allowed_root)]}
    return user_db


@pytest.fixture
def mock_config(allowed_root: Path):
    """Config stub matching how resolve_allowed_roots() reads destinations."""
    values = {"DESTINATION": str(allowed_root), "DESTINATION_AUDIOBOOK": ""}
    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default=None, **_kwargs: values.get(key, default)
    )
    with patch("shelfmark.core.config.config", config):
        yield config


class TestResolveRequestedDestination:
    def test_accepts_path_inside_allowed_root(self, mock_user_db, mock_config, allowed_root):
        result = resolve_requested_destination(
            str(allowed_root / "scifi"), user_db=mock_user_db, db_user_id=1
        )
        assert result == str(allowed_root / "scifi")

    def test_accepts_the_root_itself(self, mock_user_db, mock_config, allowed_root):
        result = resolve_requested_destination(
            str(allowed_root), user_db=mock_user_db, db_user_id=1
        )
        assert result == str(allowed_root)

    def test_rejects_path_outside_allowed_roots(self, mock_user_db, mock_config):
        assert (
            resolve_requested_destination("/etc/cron.d", user_db=mock_user_db, db_user_id=1) is None
        )

    def test_rejects_traversal_escaping_allowed_root(self, mock_user_db, mock_config, allowed_root):
        escaped = f"{allowed_root}/scifi/../../../../etc"
        assert resolve_requested_destination(escaped, user_db=mock_user_db, db_user_id=1) is None

    def test_rejects_relative_path(self, mock_user_db, mock_config):
        assert (
            resolve_requested_destination("books/scifi", user_db=mock_user_db, db_user_id=1) is None
        )

    def test_rejects_empty_value(self, mock_user_db, mock_config):
        assert resolve_requested_destination("   ", user_db=mock_user_db, db_user_id=1) is None

    def test_rejects_non_string(self, mock_user_db, mock_config):
        assert resolve_requested_destination(42, user_db=mock_user_db, db_user_id=1) is None

    def test_denies_when_user_context_missing(self, mock_user_db, mock_config, allowed_root):
        assert (
            resolve_requested_destination(str(allowed_root), user_db=mock_user_db, db_user_id=None)
            is None
        )


class TestAuthorFolderExists:
    """Existence checks behind the picker's "✓ already filed here" marker."""

    def test_true_for_existing_folder(self, tmp_path: Path):
        (tmp_path / "Brandon Sanderson").mkdir()
        assert author_folder_exists(tmp_path, "Brandon Sanderson") is True

    def test_false_when_absent(self, tmp_path: Path):
        assert author_folder_exists(tmp_path, "Brandon Sanderson") is False

    def test_false_for_file_with_author_name(self, tmp_path: Path):
        (tmp_path / "Brandon Sanderson").write_text("not a folder")
        assert author_folder_exists(tmp_path, "Brandon Sanderson") is False

    def test_matches_sanitized_folder_name(self, tmp_path: Path):
        # Post-processing sanitizes "AC/DC" to "AC_DC" before creating the folder.
        (tmp_path / "AC_DC").mkdir()
        assert author_folder_exists(tmp_path, "AC/DC") is True

    def test_raw_name_with_separator_cannot_escape_parent(self, tmp_path: Path):
        (tmp_path / "outside").mkdir()
        assert author_folder_exists(tmp_path / "outside", "../outside") is False

    def test_false_for_empty_author(self, tmp_path: Path):
        assert author_folder_exists(tmp_path, "   ") is False


class TestEnrichReleaseSaveLocation:
    """enrich_release_for_monitored() is the single chokepoint every download passes."""

    def test_keeps_valid_destination_override(self, mock_user_db, mock_config, allowed_root):
        payload = {"content_type": "ebook", "destination_override": str(allowed_root / "scifi")}

        result = enrich_release_for_monitored(payload, None, 1, mock_user_db)

        assert result["destination_override"] == str(allowed_root / "scifi")

    def test_strips_out_of_bounds_destination_override(self, mock_user_db, mock_config):
        payload = {"content_type": "ebook", "destination_override": "/etc"}

        result = enrich_release_for_monitored(payload, None, 1, mock_user_db)

        assert "destination_override" not in result

    def test_always_strips_client_supplied_layout_overrides(
        self, mock_user_db, mock_config, allowed_root
    ):
        payload = {
            "content_type": "ebook",
            "destination_override": str(allowed_root),
            "file_organization_override": "flat",
            "template_override": "{Author}/pwned",
        }

        result = enrich_release_for_monitored(payload, None, 1, mock_user_db)

        assert "file_organization_override" not in result
        assert "template_override" not in result
        assert result["destination_override"] == str(allowed_root)

    def test_does_not_mutate_caller_payload(self, mock_user_db, mock_config):
        payload = {"content_type": "ebook", "destination_override": "/etc"}

        enrich_release_for_monitored(payload, None, 1, mock_user_db)

        assert payload["destination_override"] == "/etc"
