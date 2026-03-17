"""Settings tab for third-party integrations — registered from monitored branch."""
from __future__ import annotations

from typing import Any

from shelfmark.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    CheckboxField,
    ActionButton,
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


def test_abs_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
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
        return {"success": True, "message": msg}
    except Exception as exc:
        return {"success": False, "message": f"Connection failed: {exc}"}


# ---------------------------------------------------------------------------
# Booklore connection test
# ---------------------------------------------------------------------------


def test_booklore_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test Booklore connectivity using current form values (or saved config)."""
    from shelfmark.core.config import config as app_config
    from shelfmark.core.monitored_booklore_integration import _booklore_get, _booklore_login

    cv = current_values or {}
    url = _cfg_val("BOOKLORE_URL", cv, app_config).rstrip("/")
    username = _cfg_val("BOOKLORE_USERNAME", cv, app_config)
    password = _cfg_val("BOOKLORE_PASSWORD", cv, app_config)

    if not url:
        return {"success": False, "message": "Booklore URL is required"}
    if not username:
        return {"success": False, "message": "Username is required"}
    if not password:
        return {"success": False, "message": "Password is required"}

    try:
        token = _booklore_login(url, username, password)
    except Exception as exc:
        return {"success": False, "message": f"Login failed: {exc}"}

    try:
        _booklore_get(url, token, "/api/v1/healthcheck")
        return {"success": True, "message": "Connected — Booklore is reachable"}
    except Exception as exc:
        return {"success": False, "message": f"Connected but health check failed: {exc}"}


# ---------------------------------------------------------------------------
# Settings tab registration
# ---------------------------------------------------------------------------


@register_settings("integrations", "Integrations", icon="plug", order=16, group="monitoring")
def integrations_settings():
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
            key="booklore_integration_heading",
            title="Booklore",
            description="Match ebooks from a Booklore instance during file scans.",
        ),
        CheckboxField(
            key="BOOKLORE_ENABLED",
            label="Enable Booklore Integration",
            description="Include Booklore library matching when scanning monitored authors.",
            default=True,
        ),
        TextField(
            key="BOOKLORE_URL",
            label="Booklore URL",
            description="Base URL of your Booklore instance (e.g. http://booklore:6060).",
            default="",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        TextField(
            key="BOOKLORE_USERNAME",
            label="Username",
            description="Booklore account username.",
            default="",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        PasswordField(
            key="BOOKLORE_PASSWORD",
            label="Password",
            description="Booklore account password.",
            default="",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_booklore_connection",
            label="Check Connection",
            description="Verify that shelfmark can reach your Booklore instance.",
            style="primary",
            callback=test_booklore_connection,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
    ]
