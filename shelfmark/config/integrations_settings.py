"""Settings tab for third-party integrations — registered from monitored branch."""
from __future__ import annotations

from typing import Any

from shelfmark.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    ActionButton,
)


# ---------------------------------------------------------------------------
# ABS connection test
# ---------------------------------------------------------------------------


def test_abs_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test AudioBookShelf connectivity using current form values (or saved config)."""
    from shelfmark.core.config import config as app_config
    from shelfmark.core.monitored_audiobookshelf_integration import _abs_get, _get_abs_library_id

    cv = current_values or {}

    def _val(key: str) -> str:
        v = cv.get(key)
        if v not in (None, ""):
            return str(v).strip()
        return str(app_config.get(key) or "").strip()

    url = _val("AUDIOBOOKSHELF_URL").rstrip("/")
    token = _val("AUDIOBOOKSHELF_TOKEN")

    if not url:
        return {"success": False, "message": "AudioBookShelf URL is required"}
    if not token:
        return {"success": False, "message": "API Token is required"}

    configured_lib_id = _val("AUDIOBOOKSHELF_LIBRARY_ID")

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
# Settings tab registration
# ---------------------------------------------------------------------------


@register_settings("integrations", "Integrations", icon="plug", order=16)
def integrations_settings():
    """Third-party service integrations."""
    return [
        HeadingField(
            key="abs_integration_heading",
            title="AudioBookShelf",
            description="Match audiobooks from an AudioBookShelf instance during file scans.",
        ),
        TextField(
            key="AUDIOBOOKSHELF_URL",
            label="AudioBookShelf URL",
            description="Base URL of your AudioBookShelf instance (e.g. http://audiobookshelf:13378).",
            default="",
        ),
        PasswordField(
            key="AUDIOBOOKSHELF_TOKEN",
            label="API Token",
            description="API token from AudioBookShelf → Settings → Users → your user → API Token.",
            default="",
        ),
        TextField(
            key="AUDIOBOOKSHELF_LIBRARY_ID",
            label="Library ID (optional)",
            description="Leave empty to use the first audiobook library automatically.",
            default="",
        ),
        ActionButton(
            key="test_abs_connection",
            label="Check Connection",
            description="Verify that shelfmark can reach your AudioBookShelf instance.",
            style="primary",
            callback=test_abs_connection,
        ),
    ]
