"""Cooperative scheduling helper for CPU-heavy monitored sync loops."""

from __future__ import annotations


def cooperative_yield() -> None:
    """Yield to gevent when its monkey patching is active; otherwise do nothing."""
    try:
        import gevent
        from gevent import monkey
    except ImportError:
        return

    if monkey.is_module_patched("threading"):
        gevent.sleep(0)
