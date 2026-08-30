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
        multi_book=bool(payload.get("multi_book")),
        book_plan=payload.get("book_plan"),  # type: ignore[arg-type]
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


class TestAudiobookAlwaysGetsBookFolder:
    """A single-file audiobook from a BRANCH-managed download (one carrying a
    ``template_override`` — the SAVE-TO modal or a monitored auto-download) must land
    in its own per-book folder so Audiobookshelf indexes it, whatever the template
    renders. Plain downloads (no override) keep upstream's verbatim-template behavior;
    ebooks are never foldered by this rule."""

    def _transfer_single(
        self,
        tmp_path: Path,
        template: str,
        content_type: str,
        *,
        dest_sub: str = "",
        as_override: bool = True,
        title: str = "The Book",
    ) -> Path:
        from shelfmark.core.models import DownloadTask
        from shelfmark.download.postprocess.transfer import transfer_book_files

        destination = tmp_path / "dest"
        (destination / dest_sub).mkdir(parents=True)
        dest = destination / dest_sub if dest_sub else destination
        ext = "m4b" if content_type == "audiobook" else "epub"
        source = tmp_path / "src" / f"{title}.{ext}"
        source.parent.mkdir()
        source.write_text("x")
        # A branch download carries the resolved template as task.template_override;
        # a plain download has none and falls back to the config template.
        override = template if as_override else None
        patch_tmpl = None if as_override else (lambda _task, _mode: template)
        task = DownloadTask(
            task_id="t",
            source="direct_download",
            title=title,
            author="An Author",
            format=ext,
            content_type=content_type,
            template_override=override,
        )

        def _run() -> Path:
            paths, error, _ = transfer_book_files(
                [source],
                destination=dest,
                task=task,
                use_hardlink=False,
                is_torrent=False,
                organization_mode="organize",
            )
            assert error is None
            return paths[0].resolve().relative_to(destination.resolve())

        if patch_tmpl is not None:
            with patch("shelfmark.download.postprocess.transfer.get_template_for_task", patch_tmpl):
                return _run()
        return _run()

    def test_flat_template_audiobook_gets_own_folder(self, tmp_path: Path):
        # Flat "{Title}" would drop a lone file in the destination; it must be foldered.
        rel = self._transfer_single(tmp_path, "{Title}", "audiobook")
        assert rel == Path("The Book/The Book.m4b")

    def test_series_leaf_template_gets_per_book_folder(self, tmp_path: Path):
        # 1a: "{Series}/{Title}" would drop the file loose in the SHARED series folder;
        # it must be nested in its own book folder under the series.
        rel = self._transfer_single(tmp_path, "Foundation/{Title}", "audiobook")
        assert rel == Path("Foundation/The Book/The Book.m4b")

    def test_template_with_book_folder_is_left_alone(self, tmp_path: Path):
        rel = self._transfer_single(tmp_path, "{Title}/{Title}", "audiobook")
        assert rel == Path("The Book/The Book.m4b")

    def test_original_name_leaf_is_left_alone(self, tmp_path: Path):
        # Monitored-style: title-named folder + original-name file — already correct.
        rel = self._transfer_single(tmp_path, "{Title}/{OriginalName}", "audiobook")
        assert rel == Path("The Book/The Book.m4b")

    def test_plain_download_without_override_keeps_upstream_behavior(self, tmp_path: Path):
        # No template_override => not a branch flow => upstream verbatim behavior (loose).
        rel = self._transfer_single(tmp_path, "{Title}", "audiobook", as_override=False)
        assert rel == Path("The Book.m4b")

    def test_ebook_flat_template_may_stay_loose(self, tmp_path: Path):
        # The guarantee is audiobook-only; an ebook single file is not foldered.
        rel = self._transfer_single(tmp_path, "{Title}", "ebook")
        assert rel == Path("The Book.epub")


def _pack_plan(
    ext: str, *, second: tuple[str, ...] = ("two-a", "two-b")
) -> list[dict[str, object]]:
    """An approved two-book split, as ``orchestrator._normalize_book_plan`` keeps it."""
    return [
        {
            "title": "Book One",
            "series_position": 1,
            "year": 2011,
            "files": [f"Book 1 - Book One/one.{ext}"],
        },
        {
            "title": "Book Two",
            "series_position": 2,
            "year": 2012,
            "files": [f"Book 2 - Book Two/{stem}.{ext}" for stem in second],
        },
    ]


def _stage_pack(tmp_path: Path, plan: list[dict[str, object]]) -> tuple[Path, list[Path]]:
    """Lay the plan's files out on disk the way a downloaded series pack arrives."""
    pack = tmp_path / "staging" / "Series Pack"
    files: list[Path] = []
    for entry in plan:
        for rel in entry["files"]:  # type: ignore[union-attr]
            path = pack / str(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("data")
            files.append(path)
    return pack, files


class TestStandalonePackTransfers:
    """Upstream v1.3.12 multi-book packs (``f441b85``) × the SAVE-TO shelf.

    ``transfer._transfer_book_groups`` re-enters ``transfer_book_files`` once per book
    with a ``dataclasses.replace`` of the task, so our ``destination_override`` /
    ``template_override`` must survive that copy: every book of a series pack lands
    under ``<root>/<Author>/…`` with its *own* title — never the searched one — and
    a multi-file book inside the pack still keeps its original filenames (935f7b9).
    """

    @staticmethod
    def _pack_task(
        ct: str, *, organize: bool, root: Path, user_db: MagicMock, plan: list[dict[str, object]]
    ) -> DownloadTask:
        payload = _payload(ct, organize=organize, root=root)
        payload["multi_book"] = True
        payload["book_plan"] = plan
        return _task_from(enrich_release_for_monitored(payload, None, 1, user_db=user_db), ct)

    def test_audiobook_pack_files_each_book_under_the_shelf(
        self, mock_user_db, mock_config, allowed_root, tmp_path
    ):
        plan = _pack_plan("m4b")
        pack, files = _stage_pack(tmp_path, plan)
        task = self._pack_task(
            "audiobook", organize=True, root=allowed_root, user_db=mock_user_db, plan=plan
        )

        paths = _transfer(task, files, source_root=pack)

        series_dir = allowed_root.resolve() / AUTHOR / SERIES
        by_book: dict[Path, list[str]] = {}
        for path in paths:
            by_book.setdefault(path.parent, []).append(path.name)
        assert {parent: sorted(names) for parent, names in by_book.items()} == {
            series_dir / "Book One": ["one.m4b"],
            series_dir / "Book Two": ["two-a.m4b", "two-b.m4b"],
        }
        assert all(TITLE not in path.parts for path in paths)

    def test_ebook_pack_files_each_book_under_the_shelf(
        self, mock_user_db, mock_config, allowed_root, tmp_path
    ):
        plan = _pack_plan("epub", second=("two",))
        pack, files = _stage_pack(tmp_path, plan)
        task = self._pack_task(
            "ebook", organize=True, root=allowed_root, user_db=mock_user_db, plan=plan
        )

        paths = _transfer(task, files, source_root=pack)

        series_dir = allowed_root.resolve() / AUTHOR / SERIES
        # Title and year come from the plan, the series from the searched book.
        assert sorted(paths) == [
            series_dir / "Book One (2011).epub",
            series_dir / "Book Two (2012).epub",
        ]

    def test_ebook_pack_with_organize_off_lands_loose_in_the_author_folder(
        self, mock_user_db, mock_config, allowed_root, tmp_path
    ):
        # Upstream only splits packs in organizing modes; the ebook OFF cell is
        # ``none``, so the pack's files keep their names loose under <root>/<Author>.
        plan = _pack_plan("epub", second=("two",))
        pack, files = _stage_pack(tmp_path, plan)
        task = self._pack_task(
            "ebook", organize=False, root=allowed_root, user_db=mock_user_db, plan=plan
        )

        paths = _transfer(task, files, source_root=pack)

        author_dir = allowed_root.resolve() / AUTHOR
        assert {path.parent for path in paths} == {author_dir}
        assert sorted(path.name for path in paths) == ["one.epub", "two.epub"]
