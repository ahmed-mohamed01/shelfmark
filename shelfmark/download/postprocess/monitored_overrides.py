"""Task-level overrides for post-processing decisions.

Branch-only wrappers around `policy.get_file_organization` and `policy.get_template`
that honour the per-task override fields populated by monitored downloads
(`file_organization_override`, `template_override`). Non-monitored tasks have these
fields as `None` and fall through to the upstream config-driven defaults.

Kept in a separate file so `policy.py` stays bit-identical to upstream — see
`monitored_routes.enrich_release_for_monitored` for the producer side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shelfmark.core.naming import sanitize_filename
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.postprocess.policy import get_file_organization, get_template

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.core.models import DownloadTask


def get_file_organization_for_task(task: DownloadTask) -> str:
    """Resolve organization mode for a task, respecting task overrides."""
    override = task.file_organization_override
    if isinstance(override, str) and override.strip():
        normalized = override.strip().lower()
        if normalized in {"none", "rename", "rename_and_group", "organize"}:
            return normalized

    return get_file_organization(is_audiobook=check_audiobook(task.content_type))


def get_template_for_task(task: DownloadTask, organization_mode: str) -> str:
    """Resolve template for a task + org mode, respecting task overrides."""
    override = task.template_override
    if isinstance(override, str) and override.strip():
        return override

    return get_template(
        is_audiobook=check_audiobook(task.content_type),
        organization_mode=organization_mode,
    )


def ensure_audiobook_book_folder(
    dest_path: Path, task: DownloadTask, *, is_audiobook: bool
) -> Path:
    """Guarantee a single-file audiobook from a branch-managed download lands in its
    own per-book folder.

    Audiobookshelf only indexes an audiobook that sits inside a folder of its own. A
    multi-file audiobook always gets one; a single-file audiobook only does when the
    template's leaf is a folder. Rather than trust the template, inspect the rendered
    path: if the file's immediate parent folder is not book-specific — its name does
    not contain the title, so it is the destination root, an author folder, or a
    shared series folder — nest the file in a book folder named after it. This is why
    a flat ``{Author}/{Title}`` or a ``{Series}/{Title}`` leaf still gets foldered.

    Scoped to downloads this branch composed — those carrying a ``template_override``
    (the SAVE-TO modal and monitored auto-downloads). A plain config-template download
    keeps upstream's behavior (which files a single-file audiobook per the template
    verbatim), so shared post-processing stays bit-compatible with upstream and its
    tests. Non-audiobooks, plain downloads, and files already inside a title-named
    folder pass through unchanged.

    The title-in-folder-name test is a deliberate heuristic: a pathologically short
    title that is a substring of a shared folder name is left loose (no worse than
    before), and a book folder named without the title (e.g. by series position)
    gains one extra level. Both are rare and non-destructive.
    """
    override = task.template_override
    if not is_audiobook or not (isinstance(override, str) and override.strip()):
        return dest_path
    title_slug = sanitize_filename(task.title or "")
    if title_slug and title_slug.casefold() in dest_path.parent.name.casefold():
        return dest_path
    return dest_path.parent / dest_path.stem / dest_path.name
