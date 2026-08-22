"""SAVE-TO matrix × multi-file audiobooks: real route decision → real transfer layout.

Upstream v1.3.11 (``f4421ff``, ``7d56624``) taught ``transfer_book_files`` a new
``rename_and_group`` organization mode plus a ``source_root`` parameter that groups a
multi-file audiobook into ``<destination>/<source folder>/``. The one-off SAVE TO bar
only ever emits ``organize`` / ``none`` (see ``monitored_routes.enrich_release_for_monitored``),
so that grouping must never hijack any of the four cells:

    ebook     ON  → organize  → <root>/<Author>/<Series>/<Title> (<Year>).<ext>
    ebook     OFF → none      → <root>/<Author>/<original filename>
    audiobook ON  → organize  → <root>/<Author>/<Series>/<Title>/<original names>
    audiobook OFF → organize  → <root>/<Author>/<Title>/<original names>   (series folder stripped)

Each cell here is produced by the real ``enrich_release_for_monitored`` and then
transferred by the real ``transfer_book_files`` — with ``source_root`` pointing at a
multi-file release folder — mirroring what ``outputs/folder.py`` does at runtime.
The last two tests pin that tasks *without* an override still reach upstream's new
mode through our branch-only wrapper.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.core.monitored_routes import enrich_release_for_monitored
from shelfmark.download.postprocess.destination import get_final_destination, validate_destination
from shelfmark.download.postprocess.monitored_overrides import get_file_organization_for_task
from shelfmark.download.postprocess.transfer import transfer_book_files

EBOOK_ORGANIZE = "{Author}/{Series}/{Title} ({Year})"
AUDIOBOOK_ORGANIZE = "{Author}/{Series}/{Title}/{OriginalName}"
AUTHOR = "Megan E. O'Keefe"
TITLE = "Velocity Weapon"
SERIES = "The Protectorate"
RELEASE_FOLDER = "Velocity Weapon Unabridged"  # the multi-file release's source folder
CHAPTERS = ("01 - Chapter 1.m4b", "02 - Chapter 2.m4b", "03 - Chapter 3.m4b")


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
    }


@pytest.fixture
def mock_config(config_values: dict[str, str]):
    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default=None, **_kwargs: config_values.get(key, default)
    )
    with patch("shelfmark.core.config.config", config):
        yield config


@pytest.fixture
def release_folder(tmp_path: Path) -> Path:
    """A staged multi-file audiobook release, as ``collect_staged_files`` would hand over."""
    folder = tmp_path / "staging" / RELEASE_FOLDER
    folder.mkdir(parents=True)
    for name in CHAPTERS:
        (folder / name).write_text("audio")
    return folder


@pytest.fixture
def ebook_file(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    source = staging / "Velocity_Weapon_retail.epub"
    source.write_text("ebook")
    return source


def _payload(ct: str, *, organize: bool, root: Path) -> dict[str, object]:
    return {
        "source": "direct_download",
        "source_id": "abc",
        "title": TITLE,
        "author": AUTHOR,
        "content_type": ct,
        "organize": organize,
        "destination_override": str(root),
        "extra": {},
    }


def _task_from(payload: dict[str, object], ct: str) -> DownloadTask:
    """Mirror the fields ``orchestrator.queue_release`` copies onto the task."""
    return DownloadTask(
        task_id="matrix",
        source="direct_download",
        title=TITLE,
        author=AUTHOR,
        year="2019",
        content_type=ct,
        series_name=SERIES,
        series_position=1,
        destination_override=payload.get("destination_override"),  # type: ignore[arg-type]
        file_organization_override=payload.get("file_organization_override"),  # type: ignore[arg-type]
        template_override=payload.get("template_override"),  # type: ignore[arg-type]
    )


def _transfer(task: DownloadTask, files: list[Path], source_root: Path) -> list[Path]:
    """What ``outputs/folder.py`` does: resolve the mode via our wrapper, validate
    (and thereby create) the destination, then transfer."""
    destination = get_final_destination(task)
    assert validate_destination(destination, lambda *_args: None)
    final_paths, error, _ops = transfer_book_files(
        files,
        destination=destination,
        task=task,
        use_hardlink=False,
        is_torrent=False,
        organization_mode=get_file_organization_for_task(task),
        source_root=source_root,
    )
    assert error is None
    return final_paths


def _enrich(ct: str, *, organize: bool, root: Path, user_db: MagicMock) -> dict[str, object]:
    return enrich_release_for_monitored(
        _payload(ct, organize=organize, root=root), None, 1, user_db=user_db
    )


class TestStandaloneMatrixTransfers:
    def test_ebook_organize_on(
        self, mock_user_db, mock_config, allowed_root, ebook_file, release_folder
    ):
        payload = _enrich("ebook", organize=True, root=allowed_root, user_db=mock_user_db)
        task = _task_from(payload, "ebook")

        paths = _transfer(task, [ebook_file], source_root=release_folder)

        author_dir = allowed_root.resolve() / AUTHOR
        assert paths == [author_dir / SERIES / "Velocity Weapon (2019).epub"]

    def test_ebook_organize_off_keeps_original_filename(
        self, mock_user_db, mock_config, allowed_root, ebook_file, release_folder
    ):
        payload = _enrich("ebook", organize=False, root=allowed_root, user_db=mock_user_db)
        task = _task_from(payload, "ebook")

        paths = _transfer(task, [ebook_file], source_root=release_folder)

        author_dir = allowed_root.resolve() / AUTHOR
        assert paths == [author_dir / "Velocity_Weapon_retail.epub"]

    def test_audiobook_organize_on_groups_into_series_and_title_folder(
        self, mock_user_db, mock_config, allowed_root, release_folder
    ):
        payload = _enrich("audiobook", organize=True, root=allowed_root, user_db=mock_user_db)
        task = _task_from(payload, "audiobook")
        files = sorted(release_folder.iterdir())

        paths = _transfer(task, files, source_root=release_folder)

        book_dir = allowed_root.resolve() / AUTHOR / SERIES / TITLE
        assert {p.parent for p in paths} == {book_dir}
        assert sorted(p.name for p in paths) == sorted(CHAPTERS)
        # Upstream's source-folder grouping must not leak into the layout.
        assert all(RELEASE_FOLDER not in p.parts for p in paths)

    def test_audiobook_organize_off_own_folder_no_series(
        self, mock_user_db, mock_config, allowed_root, release_folder
    ):
        payload = _enrich("audiobook", organize=False, root=allowed_root, user_db=mock_user_db)
        task = _task_from(payload, "audiobook")
        files = sorted(release_folder.iterdir())

        paths = _transfer(task, files, source_root=release_folder)

        book_dir = allowed_root.resolve() / AUTHOR / TITLE
        assert {p.parent for p in paths} == {book_dir}
        assert sorted(p.name for p in paths) == sorted(CHAPTERS)
        assert all(SERIES not in p.parts for p in paths)
        assert all(RELEASE_FOLDER not in p.parts for p in paths)


class TestRenameAndGroupThroughOverrideWrapper:
    """Tasks without a SAVE-TO override must still get upstream's new mode."""

    def test_override_accepts_rename_and_group(self, mock_config):
        task = DownloadTask(
            task_id="rag",
            source="direct_download",
            title=TITLE,
            content_type="audiobook",
            file_organization_override="rename_and_group",
        )
        assert get_file_organization_for_task(task) == "rename_and_group"

    def test_global_rename_and_group_groups_multifile_audiobook(
        self, mock_config, config_values, allowed_root, release_folder
    ):
        config_values["FILE_ORGANIZATION_AUDIOBOOK"] = "rename_and_group"
        task = DownloadTask(
            task_id="rag-global",
            source="direct_download",
            title=TITLE,
            author=AUTHOR,
            content_type="audiobook",
        )
        assert get_file_organization_for_task(task) == "rename_and_group"
        files = sorted(release_folder.iterdir())

        final_paths, error, _ops = transfer_book_files(
            files,
            destination=allowed_root,
            task=task,
            use_hardlink=False,
            is_torrent=False,
            organization_mode=get_file_organization_for_task(task),
            source_root=release_folder,
        )

        assert error is None
        assert {p.parent for p in final_paths} == {allowed_root / RELEASE_FOLDER}
        assert sorted(p.name for p in final_paths) == sorted(CHAPTERS)
