"""Settings tab for third-party integrations — registered from monitored branch."""

from __future__ import annotations

from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    TextField,
    register_settings,
)


def _cfg_val(key: str, cv: dict[str, Any], cfg: Any) -> str:
    """Return current_values[key] if non-empty, else the saved config value."""
    v = cv.get(key)
    if v not in (None, ""):
        return str(v).strip()
    return str(cfg.get(key) or "").strip()


# ---------------------------------------------------------------------------
# ABS connection test
# ---------------------------------------------------------------------------


def test_abs_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: PT028
    """Test AudioBookShelf connectivity using current form values (or saved config)."""
    from shelfmark.core.config import config as app_config
    from shelfmark.core.monitored_audiobookshelf_integration import _abs_get

    cv = current_values or {}
    url = _cfg_val("AUDIOBOOKSHELF_URL", cv, app_config).rstrip("/")
    token = _cfg_val("AUDIOBOOKSHELF_TOKEN", cv, app_config)

    if not url:
        return {"success": False, "message": "AudioBookShelf URL is required"}
    if not token:
        return {"success": False, "message": "API Token is required"}

    configured_lib_id = _cfg_val("AUDIOBOOKSHELF_LIBRARY_ID", cv, app_config)

    try:
        data = _abs_get(url, token, "/api/libraries")
        libraries = data.get("libraries") or []
        book_libs = [lib for lib in libraries if lib.get("mediaType") == "book"]
        lib_count = len(book_libs)
        if lib_count == 0:
            return {"success": True, "message": "Connected — no audiobook libraries found"}
        names = ", ".join(lib.get("name") or lib.get("id") or "?" for lib in book_libs[:3])
        suffix = f" (+{lib_count - 3} more)" if lib_count > 3 else ""
        msg = f"Connected — {lib_count} audiobook {'library' if lib_count == 1 else 'libraries'}: {names}{suffix}"
        if configured_lib_id:
            known_ids = {str(lib.get("id") or "") for lib in book_libs}
            if configured_lib_id not in known_ids:
                msg += f". ⚠ Library ID '{configured_lib_id}' not found in this server"
        return {"success": True, "message": msg}  # noqa: TRY300
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Connection failed: {exc}"}


# ---------------------------------------------------------------------------
# Grimmory connection test
# ---------------------------------------------------------------------------


def test_grimmory_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: PT028
    """Test Grimmory connectivity using current form values (or saved config)."""
    from shelfmark.core.config import config as app_config
    from shelfmark.core.monitored_grimmory_integration import _grimmory_get, _grimmory_login

    cv = current_values or {}
    url = _cfg_val("GRIMMORY_URL", cv, app_config).rstrip("/")
    username = _cfg_val("GRIMMORY_USERNAME", cv, app_config)
    password = _cfg_val("GRIMMORY_PASSWORD", cv, app_config)

    if not url:
        return {"success": False, "message": "Grimmory URL is required"}
    if not username:
        return {"success": False, "message": "Username is required"}
    if not password:
        return {"success": False, "message": "Password is required"}

    try:
        token = _grimmory_login(url, username, password)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Login failed: {exc}"}

    try:
        _grimmory_get(url, token, "/api/v1/healthcheck")
        return {"success": True, "message": "Connected — Grimmory is reachable"}  # noqa: TRY300
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Connected but health check failed: {exc}"}


# ---------------------------------------------------------------------------
# Settings tab registration
# ---------------------------------------------------------------------------


@register_settings("integrations", "Integrations", icon="plug", order=16, group="monitoring")
def integrations_settings() -> list[Any]:
    """Third-party service integrations."""
    return [
        HeadingField(
            key="abs_integration_heading",
            title="AudioBookShelf",
            description="Match audiobooks from an AudioBookShelf instance during file scans.",
        ),
        CheckboxField(
            key="AUDIOBOOKSHELF_ENABLED",
            label="Enable AudioBookShelf Integration",
            description="Include AudioBookShelf library matching when scanning monitored authors.",
            default=True,
        ),
        TextField(
            key="AUDIOBOOKSHELF_URL",
            label="AudioBookShelf URL",
            description="Base URL of your AudioBookShelf instance (e.g. http://audiobookshelf:13378).",
            default="",
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        PasswordField(
            key="AUDIOBOOKSHELF_TOKEN",
            label="API Token",
            description="API token from AudioBookShelf → Settings → Users → your user → API Token.",
            default="",
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        TextField(
            key="AUDIOBOOKSHELF_LIBRARY_ID",
            label="Library ID (optional)",
            description="Leave empty to scan all audiobook libraries. Set to restrict scanning to a single library.",
            default="",
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_abs_connection",
            label="Check Connection",
            description="Verify that shelfmark can reach your AudioBookShelf instance.",
            style="primary",
            callback=test_abs_connection,
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        HeadingField(
            key="grimmory_integration_heading",
            title="Grimmory",
            description="Match ebooks from a Grimmory instance during file scans.",
        ),
        CheckboxField(
            key="GRIMMORY_ENABLED",
            label="Enable Grimmory Integration",
            description="Include Grimmory library matching when scanning monitored authors.",
            default=True,
        ),
        TextField(
            key="GRIMMORY_URL",
            label="Grimmory URL",
            description="Base URL of your Grimmory instance (e.g. http://grimmory:6060).",
            default="",
            show_when={"field": "GRIMMORY_ENABLED", "value": True},
        ),
        TextField(
            key="GRIMMORY_USERNAME",
            label="Username",
            description="Grimmory account username.",
            default="",
            show_when={"field": "GRIMMORY_ENABLED", "value": True},
        ),
        PasswordField(
            key="GRIMMORY_PASSWORD",
            label="Password",
            description="Grimmory account password.",
            default="",
            show_when={"field": "GRIMMORY_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_grimmory_connection",
            label="Check Connection",
            description="Verify that shelfmark can reach your Grimmory instance.",
            style="primary",
            callback=test_grimmory_connection,
            show_when={"field": "GRIMMORY_ENABLED", "value": True},
        ),
    ]
