"""Flask routes for WireGuard VPN status."""
from __future__ import annotations

from typing import Callable, Optional

from flask import Flask, jsonify, session

from shelfmark.core.vpn_manager import VPNManager


def register_vpn_routes(
    app: Flask,
    vpn_manager: VPNManager,
    *,
    resolve_auth_mode: Optional[Callable[[], str]] = None,
) -> None:

    def _is_authenticated() -> bool:
        if resolve_auth_mode is not None and resolve_auth_mode() == "none":
            return True
        return session.get("db_user_id") is not None

    @app.route("/api/vpn/status", methods=["GET"])
    def vpn_status():
        if not _is_authenticated():
            return jsonify({"error": "Authentication required"}), 403
        return jsonify(vpn_manager.get_status())
