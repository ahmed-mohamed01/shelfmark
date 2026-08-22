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

from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.postprocess.policy import get_file_organization, get_template

if TYPE_CHECKING:
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
