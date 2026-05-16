"""Settings tabs for the Monitoring feature — registered from monitored branch."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

from shelfmark.core.settings_registry import (
    register_settings,
    register_group,
    register_on_save,
    NumberField,
    CheckboxField,
    SelectField,
    TextField,
    TagListField,
    OrderableListField,
    HeadingField,
    CustomComponentField,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monitoring group (collapsible sidebar section)
# ---------------------------------------------------------------------------

register_group("monitoring", "Monitoring", icon="book", order=13)


# ---------------------------------------------------------------------------
# Option helpers
# ---------------------------------------------------------------------------

def _get_release_priority_source_options(content_type: str) -> list[dict[str, str]]:
    """Return release source options for the given content type."""
    from shelfmark.core.monitored_utils import source_supports_content_type
    from shelfmark.release_sources import list_available_sources

    options: list[dict[str, str]] = []
    for source in list_available_sources():
        if not source_supports_content_type(source, content_type):
            continue

        source_name = str(source.get("name") or "").strip()
        display_name = str(source.get("display_name") or source_name).strip()
        if not source_name or not display_name:
            continue

        state_text = "enabled" if source.get("enabled") else "disabled"
        options.append(
            {
                "id": f"source:{source_name}",
                "label": f"Source · {display_name}",
                "description": f"Release source ({state_text}).",
            }
        )
    return options


def _get_release_priority_prowlarr_indexer_options() -> list[dict[str, str]]:
    """Return Prowlarr indexer options for release priority controls."""
    from shelfmark.core.config import config
    from shelfmark.core.utils import normalize_http_url

    raw_url = config.get("PROWLARR_URL", "")
    api_key = config.get("PROWLARR_API_KEY", "")
    if not raw_url or not api_key:
        return []

    url = normalize_http_url(raw_url)
    if not url:
        return []

    try:
        from shelfmark.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient(url, api_key, timeout=5)
        indexers = client.get_enabled_indexers_detailed()
    except Exception:
        return []

    options: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for idx in indexers:
        name = str(idx.get("name") or "").strip()
        if not name:
            continue

        option_id = f"indexer:{name}"
        option_key = option_id.lower()
        if option_key in seen_ids:
            continue
        seen_ids.add(option_key)

        protocol = str(idx.get("protocol") or "").strip().lower()
        has_books = bool(idx.get("has_books", False))
        detail_bits = [bit for bit in [protocol if protocol else None, "books" if has_books else None] if bit]
        detail = f" ({', '.join(detail_bits)})" if detail_bits else ""

        options.append(
            {
                "id": option_id,
                "label": f"Indexer · {name}",
                "description": f"Prowlarr indexer{detail}.",
            }
        )

    options.sort(key=lambda item: item["label"].lower())
    return options


def _get_release_priority_options(content_type: str) -> list[dict[str, str]]:
    """Build combined source + indexer options for release priority settings."""
    source_options = _get_release_priority_source_options(content_type)
    indexer_options = _get_release_priority_prowlarr_indexer_options()
    return [*source_options, *indexer_options]


def _get_ebook_release_priority_options() -> list[dict[str, str]]:
    return _get_release_priority_options("ebook")


def _get_audiobook_release_priority_options() -> list[dict[str, str]]:
    return _get_release_priority_options("audiobook")


def _get_ebook_format_priority_options() -> list[dict[str, str]]:
    """Return configurable ebook format priority options for release scoring."""
    from shelfmark.config.settings import _FORMAT_OPTIONS

    excluded = {"zip", "rar"}
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for fmt in _FORMAT_OPTIONS:
        value = str(fmt.get("value") or "").strip().lower()
        if not value or value in excluded or value in seen:
            continue
        seen.add(value)
        label = str(fmt.get("label") or value.upper())
        options.append(
            {
                "id": value,
                "label": label,
                "description": "Preferred ebook format when ranking close matches.",
            }
        )
    return options


def _get_audiobook_format_priority_options() -> list[dict[str, str]]:
    """Return configurable audiobook format priority options for release scoring."""
    ordered_formats = ["m4b", "mp3", "m4a", "flac", "opus"]
    options: list[dict[str, str]] = []
    for fmt in ordered_formats:
        options.append(
            {
                "id": fmt,
                "label": fmt.upper(),
                "description": "Preferred audiobook format when ranking close matches.",
            }
        )
    return options


# ---------------------------------------------------------------------------
# Settings tab registration
# ---------------------------------------------------------------------------

@register_settings("release_scoring", "Release Scoring", icon="wrench", order=14, group="monitoring")
def release_scoring_settings():
    """Release matching and scoring behavior."""
    return [
        HeadingField(
            key="release_scoring_heading",
            title="Release Scoring",
            description="Control how release matches are scored and rejected for universal-mode release searches.",
        ),
        NumberField(
            key="AUTO_DOWNLOAD_MIN_MATCH_SCORE",
            label="Auto-Download Minimum Match Score",
            description="Minimum match score required before auto-download should accept a release.",
            default=75,
            min_value=0,
            max_value=100,
        ),
        CheckboxField(
            key="RELEASE_PREFER_FREELEECH_OR_DIRECT",
            label="Prioritize FreeLeech or Direct Download",
            description="Add +10 ranking boost to releases marked freeleech or from Direct Download, after title/author cutoffs are met.",
            default=False,
        ),
        OrderableListField(
            key="EBOOK_RELEASE_PRIORITY",
            label="eBook Source & Indexer Priority",
            description="Boost preferred eBook sources/indexers when ranking close matches. Drag to reorder.",
            options=_get_ebook_release_priority_options,
            default=[],
        ),
        OrderableListField(
            key="AUDIOBOOK_RELEASE_PRIORITY",
            label="Audiobook Source & Indexer Priority",
            description="Boost preferred audiobook sources/indexers when ranking close matches. Drag to reorder.",
            options=_get_audiobook_release_priority_options,
            default=[],
        ),
        OrderableListField(
            key="EBOOK_FORMAT_PRIORITY",
            label="eBook Format Priority",
            description="Boost preferred eBook formats by priority order (+5 per rank step). Applied only after strong metadata matching.",
            options=_get_ebook_format_priority_options,
            default=[],
        ),
        OrderableListField(
            key="AUDIOBOOK_FORMAT_PRIORITY",
            label="Audiobook Format Priority",
            description="Boost preferred audiobook formats by priority order (+5 per rank step). Applied only after strong metadata matching.",
            options=_get_audiobook_format_priority_options,
            default=[],
        ),
        TagListField(
            key="RELEASE_MATCH_FORBIDDEN_TERMS",
            label="Rejected Terms",
            description="Release titles containing these terms are hard-rejected.",
            default=["abridged", "sample", "excerpt", "summary", "book summary"],
            normalize_urls=False,
        ),
        NumberField(
            key="RELEASE_MATCH_MIN_TITLE_SCORE",
            label="Minimum Title Score",
            description="Hard-reject releases when title match score is below this value.",
            default=24,
            min_value=0,
            max_value=60,
        ),
        NumberField(
            key="RELEASE_MATCH_MIN_AUTHOR_SCORE",
            label="Minimum Author Score",
            description="Hard-reject releases when author match score is below this value (if author exists on release).",
            default=8,
            min_value=0,
            max_value=30,
        ),
    ]


def validate_monitored_refresh_times(values: Dict[str, Any]) -> Dict[str, Any] | None:
    """Validate and normalise MONITORED_REFRESH_TIMES in *values*.

    Returns an error dict if the value is invalid, or ``None`` when valid
    (values is updated in place with the normalised schedule string).
    """
    raw_schedule = str(values.get("MONITORED_REFRESH_TIMES") or "").strip()
    if not raw_schedule:
        raw_schedule = "02:00,14:00"

    parts = [segment.strip() for segment in raw_schedule.split(",") if segment.strip()]
    if not parts:
        return {
            "error": True,
            "message": "Monitored refresh times must include at least one time in HH:MM format",
            "values": values,
        }

    normalized_parts: list[str] = []
    seen_parts: set[str] = set()
    for part in parts:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", part):
            return {
                "error": True,
                "message": f"Invalid monitored refresh time '{part}'. Use 24-hour HH:MM (e.g. 02:00,14:00)",
                "values": values,
            }
        if part not in seen_parts:
            normalized_parts.append(part)
            seen_parts.add(part)

    values["MONITORED_REFRESH_TIMES"] = ",".join(normalized_parts)
    return None


# ---------------------------------------------------------------------------
# Monitoring → General tab
# ---------------------------------------------------------------------------

@register_settings("monitoring_general", "General", icon="settings", order=13, group="monitoring")
def monitoring_general_settings():
    """General monitoring preferences."""
    return [
        HeadingField(
            key="monitoring_general_heading",
            title="General",
            description="General preferences for the monitored authors and books feature.",
        ),
        CheckboxField(
            key="DEFAULT_TO_MONITORED_VIEW",
            label="Open app on Monitored view",
            description="Navigate to the Monitored page automatically when the app loads or the Shelfmark logo is clicked.",
            default=False,
        ),
        CheckboxField(
            key="SHOW_BOOKS_IN_MULTIPLE_SERIES",
            label="Show Books in Multiple Series",
            description="Display each book under all series it belongs to, not just the primary one.",
            default=True,
        ),
        CheckboxField(
            key="SHOW_RELEASE_MATCH_SCORE",
            label="Show Match Score in Release List",
            description="Display the Match score badge in release rows.",
            default=True,
        ),
        SelectField(
            key="RELEASE_PRIMARY_DEFAULT_ACTION",
            label="Default Download Button Action",
            description="Set the default action for the main download button. Uses the same options as the action dropdown.",
            options=[
                {
                    "value": "ebook_interactive_search",
                    "label": "eBook — Interactive Search",
                    "description": "Main button opens eBook interactive release picker.",
                },
                {
                    "value": "ebook_auto_search_download",
                    "label": "eBook — Auto Search + Download",
                    "description": "Main button runs eBook auto search and downloads when match score passes cutoff.",
                },
                {
                    "value": "audiobook_interactive_search",
                    "label": "Audiobook — Interactive Search",
                    "description": "Main button opens audiobook interactive release picker.",
                },
                {
                    "value": "audiobook_auto_search_download",
                    "label": "Audiobook — Auto Search + Download",
                    "description": "Main button runs audiobook auto search and downloads when match score passes cutoff.",
                },
                {
                    "value": "combined_interactive_search",
                    "label": "Combined — Interactive Search",
                    "description": "Main button opens a two-phase release picker: select an eBook release, then an audiobook release.",
                },
            ],
            default="ebook_interactive_search",
        ),
    ]


# ---------------------------------------------------------------------------
# Monitoring → Schedules tab
# ---------------------------------------------------------------------------

@register_settings("monitoring_schedules", "Schedules", icon="cog", order=15, group="monitoring")
def monitoring_schedules_settings():
    """Monitored author refresh scheduling."""
    return [
        HeadingField(
            key="monitored_refresh_heading",
            title="Monitored Author Refresh",
            description="Refresh monitored authors on a schedule to keep books, series, popularity, and covers current without refreshing on every author open.",
        ),
        CheckboxField(
            key="MONITORED_SCHEDULED_REFRESH_ENABLED",
            label="Enable Scheduled Monitored Refresh",
            description="Run monitored-author refresh jobs automatically at configured times.",
            default=True,
        ),
        CustomComponentField(
            key="monitored_refresh_times_picker",
            component="monitored_refresh_times",
            label="Refresh Times",
            description="Pick a time and click Add. Times run on the local clock.",
            bind_keys=["MONITORED_REFRESH_TIMES"],
            value_fields=[
                TextField(
                    key="MONITORED_REFRESH_TIMES",
                    label="Refresh Times",
                    default="02:00,14:00",
                ),
            ],
            wrap_in_field_wrapper=True,
            show_when={"field": "MONITORED_SCHEDULED_REFRESH_ENABLED", "value": True},
        ),
        CheckboxField(
            key="MONITORED_SCHEDULED_AUTO_DOWNLOAD_ENABLED",
            label="Enable Scheduled Auto-Download",
            description="Automatically search and download monitored books at the configured refresh times. Runs independently of book-info refresh — every monitored book flagged for ebook or audiobook is searched on each refresh slot.",
            default=True,
        ),
        NumberField(
            key="RELEASE_ENRICHMENT_RECHECK_DAYS",
            label="Release Date Recheck Interval (days)",
            description="Days before re-querying Google Books for release date enrichment on books with no date.",
            default=7,
            min_value=1,
            max_value=90,
        ),
    ]


def _on_save_schedules(values: Dict[str, Any]) -> Dict[str, Any]:
    """Validate monitored refresh times on save."""
    error = validate_monitored_refresh_times(values)
    if error is not None:
        return error
    return {"error": False, "message": "", "values": values}


register_on_save("monitoring_schedules", _on_save_schedules)


# ---------------------------------------------------------------------------
# One-time config migration: move monitoring fields from Advanced → new tabs
# ---------------------------------------------------------------------------

def _migrate_monitoring_settings() -> None:
    """Move monitoring settings from old config files to new tab files."""
    try:
        from shelfmark.core.config import CONFIG_DIR
    except Exception:
        return

    plugins_dir = Path(CONFIG_DIR) / "plugins"
    if not plugins_dir.is_dir():
        return

    # Migrations: (source_file, keys_to_extract, destination_file)
    migrations: list[tuple[str, set[str], str]] = [
        ("advanced.json", {"DEFAULT_TO_MONITORED_VIEW"}, "monitoring_general.json"),
        ("advanced.json", {"MONITORED_SCHEDULED_REFRESH_ENABLED", "MONITORED_REFRESH_TIMES"}, "monitoring_schedules.json"),
        ("release_scoring.json", {"SHOW_RELEASE_MATCH_SCORE", "RELEASE_PRIMARY_DEFAULT_ACTION"}, "monitoring_general.json"),
    ]

    source_cache: dict[str, dict[str, Any]] = {}
    sources_dirty: set[str] = set()
    dest_additions: dict[str, dict[str, Any]] = {}

    for src_name, keys, dest_name in migrations:
        src_path = plugins_dir / src_name
        if not src_path.is_file():
            continue

        if src_name not in source_cache:
            try:
                source_cache[src_name] = json.loads(src_path.read_text(encoding="utf-8"))
            except Exception:
                continue

        src_data = source_cache[src_name]
        for key in keys:
            if key in src_data:
                dest_additions.setdefault(dest_name, {})[key] = src_data.pop(key)
                sources_dirty.add(src_name)

    if not dest_additions:
        return

    for dest_name, vals in dest_additions.items():
        target = plugins_dir / dest_name
        existing: dict[str, Any] = {}
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(vals)
        target.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        log.info("Migrated monitoring settings to %s: %s", dest_name, list(vals.keys()))

    for src_name in sources_dirty:
        src_path = plugins_dir / src_name
        src_path.write_text(json.dumps(source_cache[src_name], indent=2) + "\n", encoding="utf-8")


_migrate_monitoring_settings()
