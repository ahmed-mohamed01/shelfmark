"""Settings for reusing a cleared bypass browser across jobs (fork-only).

Registered by importing this module from ``shelfmark.release_sources.elscione``: the
config loader imports ``shelfmark.release_sources`` in every process that reads settings,
the bypass helper subprocess included, so no upstream settings file has to change.
"""

from shelfmark.core.settings_registry import (
    CheckboxField,
    NumberField,
    SettingsField,
    register_settings,
)


@register_settings(
    "browser_session",
    "Browser Session Reuse",
    icon="shield",
    order=23,
    group="direct_download",
)
def browser_session_settings() -> list[SettingsField]:
    """Return settings for keeping one cleared browser alive between bypass jobs."""
    return [
        CheckboxField(
            key="BYPASS_REUSE_BROWSER_SESSION",
            label="Reuse Cleared Browser",
            description=(
                "Keep the bypass browser open between jobs so later searches and "
                "browser downloads skip the protection challenge. Internal bypasser only. "
                "A parked browser holds roughly 300-500 MB of memory until it is closed."
            ),
            default=False,
        ),
        NumberField(
            key="BYPASS_SESSION_IDLE_TIMEOUT",
            label="Idle Timeout (seconds)",
            description="Close the reused browser after this long without a job.",
            default=2700,
            min_value=300,
            max_value=14400,
            show_when={"field": "BYPASS_REUSE_BROWSER_SESSION", "value": True},
        ),
        NumberField(
            key="BYPASS_SESSION_MAX_JOBS",
            label="Recycle After Jobs",
            description="Start a fresh browser after this many jobs.",
            default=50,
            min_value=1,
            max_value=1000,
            show_when={"field": "BYPASS_REUSE_BROWSER_SESSION", "value": True},
        ),
        NumberField(
            key="BYPASS_SESSION_MAX_AGE_HOURS",
            label="Recycle After Hours",
            description="Start a fresh browser once the current one is this old.",
            default=4,
            min_value=1,
            max_value=48,
            show_when={"field": "BYPASS_REUSE_BROWSER_SESSION", "value": True},
        ),
    ]
