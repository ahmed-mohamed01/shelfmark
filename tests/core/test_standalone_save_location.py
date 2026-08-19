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
    enrich_release_for_monitored,
    resolve_requested_destination,
    template_creates_author_folder,
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


class TestTemplateCreatesAuthorFolder:
    """The picker shows parent + author, so it must only promise a real folder."""

    @staticmethod
    def _policy_config(mode: str, template: str):
        values = {
            "FILE_ORGANIZATION": mode,
            "FILE_ORGANIZATION_AUDIOBOOK": mode,
            "TEMPLATE_ORGANIZE": template,
            "TEMPLATE_RENAME": template,
            "TEMPLATE_AUDIOBOOK_ORGANIZE": template,
            "TEMPLATE_AUDIOBOOK_RENAME": template,
        }
        config = MagicMock()
        config.get = MagicMock(
            side_effect=lambda key, default=None, **_kwargs: values.get(key, default)
        )
        return patch("shelfmark.core.config.config", config)

    def test_true_when_author_is_a_directory_segment(self):
        with self._policy_config("organize", "{Author}/{Title} ({Year})"):
            assert template_creates_author_folder(is_audiobook=False) is True

    def test_true_for_nested_author_and_series(self):
        with self._policy_config("organize", "{Author}/{Series}/{Title}"):
            assert template_creates_author_folder(is_audiobook=True) is True

    def test_false_when_author_is_only_in_the_filename(self):
        with self._policy_config("rename", "{Author} - {Title} ({Year})"):
            assert template_creates_author_folder(is_audiobook=False) is False

    def test_false_when_directory_segment_has_no_author(self):
        with self._policy_config("organize", "{Series}/{Title}"):
            assert template_creates_author_folder(is_audiobook=False) is False

    def test_false_when_organization_disabled(self):
        with self._policy_config("none", "{Author}/{Title}"):
            assert template_creates_author_folder(is_audiobook=False) is False


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
