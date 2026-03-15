"""WireGuard VPN state monitor — on-demand status checks only."""
from __future__ import annotations

import os
import re
import subprocess
import time
from enum import Enum
from typing import Optional

import requests as _requests


class VPNStatus(str, Enum):
    DISABLED = "disabled"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STALE = "stale"  # interface exists but handshake > 3 min old


HANDSHAKE_STALE_SECS = 180


class VPNManager:
    """On-demand WireGuard status checker. No background threads."""

    def __init__(self, iface: str = "wg0") -> None:
        self._iface = iface

    def is_enabled(self) -> bool:
        return os.environ.get("USING_VPN", "").lower() in ("true", "1", "yes")

    def get_status(self) -> dict:
        """Fast status check — no outbound network call. Use test_connection() for IP."""
        if not self.is_enabled():
            return {"enabled": False, "connected": False,
                    "status": VPNStatus.DISABLED, "ip": None, "interface": self._iface}

        if not self._interface_exists():
            return {"enabled": True, "connected": False,
                    "status": VPNStatus.DISCONNECTED, "ip": None, "interface": self._iface}

        fresh = self._handshake_fresh()
        return {
            "enabled": True,
            "connected": True,  # Interface is up — STALE means handshake is old, not that tunnel is down
            "status": VPNStatus.CONNECTED if fresh else VPNStatus.STALE,
            "ip": None,  # Omitted from fast path — call test_connection() for IP
            "interface": self._iface,
        }

    def test_connection(self) -> dict:
        """Full connectivity test including external IP fetch. Used by Test Connection button."""
        status = self.get_status()
        if not status["enabled"]:
            return {"success": False, "message": "VPN is not enabled (USING_VPN is not set)"}
        if not status["connected"]:
            return {"success": False, "message": f"Interface {self._iface} not found — VPN is not active"}

        ip = self._external_ip()
        if ip is not None:
            # IP fetch succeeded — tunnel is routing traffic regardless of handshake age.
            return {"success": True, "message": f"Connected (IP: {ip})"}

        # IP fetch failed — interface is up but no external connectivity confirmed.
        return {"success": False,
                "message": f"Interface {self._iface} is up but cannot reach internet — VPN tunnel may be unhealthy"}

    def is_blocking_downloads(self) -> bool:
        """True when VPN is enabled but tunnel is not healthy (soft kill-switch)."""
        if not self.is_enabled():
            return False
        if os.environ.get("VPN_KILL_SWITCH", "hard") == "none":
            return False
        return not self._interface_exists() or not self._handshake_fresh()

    def _interface_exists(self) -> bool:
        try:
            with open("/proc/net/dev") as fh:
                return (self._iface + ":") in fh.read()
        except Exception:
            return False

    def _handshake_fresh(self) -> bool:
        try:
            result = subprocess.run(
                ["wg", "show", self._iface, "latest-handshakes"],
                capture_output=True, text=True, timeout=5,
            )
            now = time.time()
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        ts = int(parts[1])
                        if ts > 0 and (now - ts) < HANDSHAKE_STALE_SECS:
                            return True
                    except ValueError:
                        pass
            return False
        except Exception:
            return False

    def _external_ip(self) -> Optional[str]:
        try:
            resp = _requests.get("https://api.ipify.org", timeout=8, proxies={})
            ip = resp.text.strip()
            # Validate it looks like an IPv4 address before displaying it.
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                return ip
            return None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_vpn_manager: Optional[VPNManager] = None


def init_vpn_manager(iface: Optional[str] = None) -> VPNManager:
    global _vpn_manager
    _vpn_manager = VPNManager(iface=iface or os.environ.get("WG_IFACE", "wg0"))
    return _vpn_manager


def get_vpn_manager() -> Optional[VPNManager]:
    return _vpn_manager


def is_vpn_blocking_downloads() -> bool:
    mgr = _vpn_manager
    return mgr.is_blocking_downloads() if mgr is not None else False
