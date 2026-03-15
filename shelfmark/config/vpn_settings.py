"""Settings tab for WireGuard VPN — registered from monitored branch."""
from __future__ import annotations

import os
from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    SelectField,
    TextField,
    register_settings,
)


def _test_vpn_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delegate to VPNManager.test_connection() — single source of truth."""
    from shelfmark.core.vpn_manager import get_vpn_manager, VPNManager

    mgr = get_vpn_manager()
    if mgr is None:
        cv = current_values or {}
        iface = (cv.get("WG_IFACE") or os.environ.get("WG_IFACE", "wg0")).strip().removesuffix(".conf")
        mgr = VPNManager(iface=iface)

    return mgr.test_connection()


@register_settings("vpn", "VPN", icon="shield", order=8)
def vpn_settings():
    """WireGuard VPN settings tab."""
    vpn_active = os.environ.get("USING_VPN", "").lower() in ("true", "1", "yes")
    active_description = (
        "To disable VPN, set USING_VPN=false and restart the container."
        if vpn_active else
        "To enable VPN, set USING_VPN=true and restart the container. "
        "Also requires the NET_ADMIN and NET_RAW Docker capabilities."
    )
    return [
        HeadingField(
            key="vpn_heading",
            title="WireGuard VPN",
            description=(
                "Routes all outbound traffic through a WireGuard VPN tunnel for privacy. "
                "Requires the NET_ADMIN and NET_RAW Docker capabilities."
            ),
        ),
        CheckboxField(
            key="USING_VPN",
            label="WireGuard VPN Active",
            description=active_description,
            disabled=True,
            disabled_reason="VPN state is controlled by the USING_VPN environment variable and requires a container restart to change.",
        ),
        ActionButton(
            key="test_vpn_connection",
            label="Test Connection",
            description="Check if the WireGuard tunnel is active and show your current outbound IP.",
            style="default",
            callback=_test_vpn_connection,
        ),
        TextField(
            key="WG_CONFIG_PATH",
            label="WireGuard Config Path",
            description=(
                "Path to your wg0.conf file inside the container. "
                "Map this via a Docker volume, e.g. /path/to/wg0.conf:/config/wg0.conf:ro"
            ),
            placeholder="/config/wg0.conf",
            default="/config/wg0.conf",
        ),
        TextField(
            key="WG_IFACE",
            label="WireGuard Interface Name",
            description="Linux network interface name created by wg-quick (not the filename). Default: wg0",
            placeholder="wg0",
            default="wg0",
        ),
        SelectField(
            key="VPN_KILL_SWITCH",
            label="Kill Switch Mode",
            description=(
                "Hard: add PostUp/PreDown iptables rules to your wg0.conf to block all "
                "non-VPN traffic at the kernel level (recommended). "
                "Soft: pauses the download queue when the VPN tunnel is detected as down. "
                "None: no kill switch — traffic flows unprotected if the VPN drops."
            ),
            options=[
                {"value": "hard", "label": "Hard (iptables via wg0.conf PostUp — recommended)"},
                {"value": "soft", "label": "Soft (download queue pause only)"},
                {"value": "none", "label": "None (no kill switch)"},
            ],
            default="hard",
        ),
    ]
