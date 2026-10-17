"""Elscione source settings registration."""

from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    NumberField,
    SettingsField,
    TagListField,
    register_settings,
)
from shelfmark.release_sources.elscione import index

logger = setup_logger(__name__)

_ENABLED_SHOW_WHEN = {"field": "ELSCIONE_ENABLED", "value": True}


def _refresh_index_now() -> dict[str, Any]:
    """Action button: start a background refresh despite any failure backoff."""
    if index.start_background_refresh(force=True):
        return {"success": True, "message": "Elscione index refresh started."}
    return {"success": False, "message": "An Elscione index refresh is already running."}


def _show_index_status() -> dict[str, Any]:
    """Action button: report what the index cache currently holds."""
    status = index.index_status()
    age = status["age_hours"]
    fetched_at = status["fetched_at"] or "never"
    age_text = fetched_at if age is None else f"{fetched_at} ({age:.0f} h old)"
    parts = [f"{status['entries']} entries, fetched {age_text}"]
    if status["mirror"]:
        # Which mirror served the index: downloads start there.
        parts.append(f"mirror {status['mirror']}")
    if status["refreshing"]:
        parts.append("refreshing now")
    if status["last_error"]:
        parts.append(f"last error: {status['last_error']}")
    return {"success": True, "message": ", ".join(parts)}


@register_settings("elscione_config", "Elscione", icon="download", order=47)
def elscione_config_settings() -> list[SettingsField]:
    """Elscione source configuration settings."""
    return [
        CheckboxField(
            key="ELSCIONE_ENABLED",
            label="Enable Elscione",
            description=(
                "Search and download epub/pdf files from server.elscione.com. Downloads "
                "need the internal bypasser (they are fetched from inside a cleared page), "
                "so this source stays unavailable with an external bypasser. Turn on "
                '"Reuse Cleared Browser" under Browser Session Reuse so each download '
                "skips the 26-60 s challenge solve."
            ),
            default=False,
        ),
        TagListField(
            key="ELSCIONE_MIRRORS",
            label="Mirrors",
            description=(
                "Elscione mirror hosts to try, in order; the first is preferred. Each "
                "entry must be an https:// host with no path, port, login or query. "
                "Invalid entries are ignored."
            ),
            placeholder="https://server.elscione.com",
            default=["https://server.elscione.com", "https://server.elsci.one"],
            show_when=_ENABLED_SHOW_WHEN,
        ),
        NumberField(
            key="ELSCIONE_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of releases to return per search (1-200).",
            default=50,
            min_value=1,
            max_value=200,
            show_when=_ENABLED_SHOW_WHEN,
        ),
        NumberField(
            key="ELSCIONE_INDEX_REFRESH_HOURS",
            label="Index Refresh Interval (hours)",
            description="Refresh the cached file index when it is older than this (6-168).",
            default=24,
            min_value=6,
            max_value=168,
            show_when=_ENABLED_SHOW_WHEN,
        ),
        NumberField(
            key="ELSCIONE_MAX_FILE_MB",
            label="Max File Size (MB)",
            description="Refuse files larger than this (1-512).",
            default=64,
            min_value=1,
            max_value=512,
            show_when=_ENABLED_SHOW_WHEN,
        ),
        NumberField(
            key="ELSCIONE_DOWNLOAD_TIMEOUT",
            label="Download Timeout (seconds)",
            description="Give up on a single download after this long (60-900).",
            default=300,
            min_value=60,
            max_value=900,
            show_when=_ENABLED_SHOW_WHEN,
        ),
        ActionButton(
            key="elscione_refresh_index",
            label="Refresh index now",
            description="Download a fresh copy of the Elscione file index in the background.",
            callback=_refresh_index_now,
            style="primary",
        ),
        ActionButton(
            key="elscione_index_status",
            label="Show index status",
            description="Report the cached index's size, age and last refresh error.",
            callback=_show_index_status,
        ),
    ]
