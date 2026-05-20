"""Post-download processing policy.

This module holds configuration-driven *policy* decisions that are shared across
post-download processing components, but are not specific to archive extraction.

Examples:
- Which file formats are enabled
- How files should be organized (none/rename/organize)
- Which naming templates to use

Implementation note:
Keep this module free of dependencies on archive extraction mechanics to avoid
circular imports (`archive` is used by the pipeline).

"""

from __future__ import annotations

import shelfmark.core.config as core_config


def _normalize_format_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, str):
        return [fmt.strip().lower() for fmt in value.split(",") if fmt.strip()]
    if isinstance(value, (list, tuple, set)):
        normalized = [str(fmt).strip().lower() for fmt in value if str(fmt).strip()]
        return normalized or default
    return default


def _config_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def get_supported_formats() -> list[str]:
    """Get current supported formats from config singleton."""
    default_formats = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"]
    formats = core_config.config.get("SUPPORTED_FORMATS", default_formats)
    return _normalize_format_list(formats, default_formats)


def get_supported_audiobook_formats() -> list[str]:
    """Get current supported audiobook formats from config singleton."""
    default_formats = ["m4b", "mp3"]
    formats = core_config.config.get("SUPPORTED_AUDIOBOOK_FORMATS", default_formats)
    return _normalize_format_list(formats, default_formats)


def get_file_organization(*, is_audiobook: bool) -> str:
    """Get the file organization mode for the content type."""
    key = "FILE_ORGANIZATION_AUDIOBOOK" if is_audiobook else "FILE_ORGANIZATION"
    mode = _config_text(core_config.config.get(key, "rename")).strip().lower()
    return mode if mode in ("none", "rename", "organize") else "rename"


def get_template(*, is_audiobook: bool, organization_mode: str) -> str:
    """Get the template for the content type and organization mode."""
    if is_audiobook:
        if organization_mode == "organize":
            key = "TEMPLATE_AUDIOBOOK_ORGANIZE"
        else:
            key = "TEMPLATE_AUDIOBOOK_RENAME"
    else:
        key = "TEMPLATE_ORGANIZE" if organization_mode == "organize" else "TEMPLATE_RENAME"

    template = _config_text(core_config.config.get(key, ""))

    if not template:
        if organization_mode == "organize":
            return "{Author}/{Title} ({Year})"
        return "{Author} - {Title} ({Year})"

    return template


def get_file_organization_for_task(task) -> str:
    """Resolve organization mode for a task, respecting task overrides."""

    try:
        override = getattr(task, "file_organization_override", None)
    except Exception:
        override = None

    if isinstance(override, str) and override.strip():
        normalized = override.strip().lower()
        if normalized in {"none", "rename", "organize"}:
            return normalized

    from shelfmark.core.utils import is_audiobook as check_audiobook

    return get_file_organization(check_audiobook(getattr(task, "content_type", None)))


def get_template_for_task(task, organization_mode: str) -> str:
    """Resolve template for a task + org mode, respecting task overrides."""

    try:
        override = getattr(task, "template_override", None)
    except Exception:
        override = None

    if isinstance(override, str) and override.strip():
        return override

    from shelfmark.core.utils import is_audiobook as check_audiobook

    return get_template(check_audiobook(getattr(task, "content_type", None)), organization_mode)
