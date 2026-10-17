"""Fake CDP state exercises file transfer without Chrome or network."""

import asyncio
import base64
import json
import re
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shelfmark.bypass import internal_bypasser as bypass
from shelfmark.bypass import page_fetch as fetch


class Tab:
    url = "about:blank"

    def __init__(self, data, error=None, total=None, cancel=None):
        self.data = data
        self.error = error
        self.total = len(data) if total is None else total
        self.cancel = cancel
        self.cleaned = False
        self.slices = 0

    async def evaluate(self, script, **kwargs):
        if "delete window" in script:
            self.cleaned = True
        elif "new FileReader" in script:
            start, end = map(int, re.search(r"blob.slice\((\d+),(\d+)\)", script).groups())
            self.slices += 1
            if self.cancel:
                self.cancel.set()
            return "data:;base64," + base64.b64encode(self.data[start:end]).decode()
        elif "return s &&" in script:
            return {
                "received": len(self.data),
                "total": self.total,
                "done": True,
                "error": self.error,
                "status": 403 if self.error == "blocked" else 404,
            }
        elif "ready: document" in script:
            return {"ready": "complete", "body": "listing", "title": "files"}
        return True


def test_chunks(tmp_path):
    data = b"abc" * 900000
    tab = Tab(data)
    dest = tmp_path / "book.epub"
    result = asyncio.run(fetch._transfer(tab, "https://a.test/book", dest, len(data), None))
    assert result.size == len(data)
    assert dest.read_bytes() == data
    assert tab.slices == 3
    assert tab.cleaned
    assert not dest.with_suffix(".epub.part").exists()


@pytest.mark.parametrize(
    "reason", ["blocked", "http_status", "too_large", "short_read", "cancelled"]
)
def test_failure_cleanup(tmp_path, reason):
    cancel = Event() if reason == "cancelled" else None
    tab = Tab(
        b"content",
        error=reason if reason in {"blocked", "http_status", "too_large"} else None,
        total=100 if reason == "short_read" else None,
        cancel=cancel,
    )
    dest = tmp_path / "book.pdf"
    with pytest.raises(fetch.PageFetchError) as error:
        asyncio.run(fetch._transfer(tab, "https://a.test/book", dest, 1000, cancel))
    assert error.value.reason == reason
    assert not dest.exists()
    assert not dest.with_suffix(".pdf.part").exists()
    assert tab.cleaned


@pytest.mark.parametrize(
    "reason,attempts,keep",
    [("blocked", 2, False), ("http_status", 1, True), ("too_large", 1, True)],
)
def test_retry(tmp_path, monkeypatch, reason, attempts, keep):
    tab = Tab(b"x", error=reason)
    driver = SimpleNamespace(main_tab=tab, get=AsyncMock(return_value=tab))
    monkeypatch.setattr(fetch.session, "acquire", AsyncMock(return_value=driver))
    release = AsyncMock()
    monkeypatch.setattr(fetch.session, "release", release)
    monkeypatch.setattr(bypass, "_bypass", AsyncMock())
    monkeypatch.setattr(bypass, "_extract_cookies_from_cdp", AsyncMock())
    with pytest.raises(fetch.PageFetchError):
        asyncio.run(
            fetch._fetch("https://a.test/book", tmp_path / "book", "https://a.test/", 100, 5, None)
        )
    assert driver.get.await_count == attempts
    assert release.call_args.kwargs["keep"] is keep


@pytest.mark.parametrize(
    "external,cross,outside", [(True, False, False), (False, True, False), (False, False, True)]
)
def test_preconditions(tmp_path, monkeypatch, external, cross, outside):
    monkeypatch.setattr(
        fetch.app_config,
        "get",
        lambda key, default=None: external if key == "USING_EXTERNAL_BYPASSER" else True,
    )
    monkeypatch.setattr(fetch.env, "TMP_DIR", tmp_path)
    with pytest.raises(fetch.PageFetchError, match="internal bypasser"):
        fetch.fetch_to_file(
            "https://b.test/file" if cross else "https://a.test/file",
            tmp_path.parent / "outside" if outside else tmp_path / "book",
            origin_url="https://a.test/",
            max_bytes=100,
            timeout=5,
        )


def test_dispatch(monkeypatch):
    monkeypatch.setattr(fetch, "handle_child_request", lambda request: 7)
    assert bypass._handle_child_request('{"op":"page_fetch"}') == 7


def test_helper_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.app_config, "get", lambda key, default=None: key == "USE_CF_BYPASS")
    monkeypatch.setattr(fetch.env, "TMP_DIR", tmp_path)
    monkeypatch.setattr(fetch.env, "DOCKERMODE", True)
    monkeypatch.setattr(fetch.network, "host_cooldown_remaining", lambda url: 0)
    captured = {}

    def run(payload, timeout, cancel):
        captured.update(payload)
        return {
            "ok": True,
            "size": 3,
            "final_url": "https://a.test/book",
            "cookies": {"a.test": {}},
            "user_agents": {"a.test": "fake"},
        }

    monkeypatch.setattr(bypass._BYPASS_HELPER, "run", run)
    imported = []
    monkeypatch.setattr(fetch.cookie_store, "import_store", lambda *args: imported.append(args))
    result = fetch.fetch_to_file(
        "https://a.test/book",
        tmp_path / "book",
        origin_url="https://a.test/",
        max_bytes=100,
        timeout=5,
    )
    assert result.size == 3
    assert captured["op"] == "page_fetch"
    assert imported == [({"a.test": {}}, {"a.test": "fake"})]


def test_timeout_releases(tmp_path, monkeypatch):
    tab = Tab(b"x")

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    # Navigation is on every path (fast readiness check or full solve), so hang there.
    driver = SimpleNamespace(main_tab=tab, get=hang)
    monkeypatch.setattr(fetch.session, "acquire", AsyncMock(return_value=driver))
    release = AsyncMock()
    monkeypatch.setattr(fetch.session, "release", release)
    with pytest.raises(fetch.PageFetchError) as error:
        asyncio.run(
            fetch._fetch(
                "https://a.test/book", tmp_path / "book", "https://a.test/", 100, 0.01, None
            )
        )
    assert error.value.reason == "timeout"
    assert release.call_args.kwargs["keep"] is False


def _docker(monkeypatch, tmp_path):
    """Point the parent at the Docker helper branch without any real helper."""
    monkeypatch.setattr(fetch.app_config, "get", lambda key, default=None: key == "USE_CF_BYPASS")
    monkeypatch.setattr(fetch.env, "TMP_DIR", tmp_path)
    monkeypatch.setattr(fetch.env, "DOCKERMODE", True)
    monkeypatch.setattr(fetch.network, "host_cooldown_remaining", lambda url: 0)


def test_killed_helper_leaves_no_part_file(tmp_path, monkeypatch):
    from shelfmark.bypass import BypassCancelledError

    _docker(monkeypatch, tmp_path)
    dest = tmp_path / "book"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"partial bytes")

    def killed(payload, timeout, cancel):
        raise BypassCancelledError("cancelled while transferring")

    monkeypatch.setattr(bypass._BYPASS_HELPER, "run", killed)
    with pytest.raises(fetch.PageFetchError) as error:
        fetch.fetch_to_file(
            "https://a.test/book",
            dest,
            origin_url="https://a.test/",
            max_bytes=100,
            timeout=5,
        )
    assert error.value.reason == "cancelled"
    assert not part.exists()


def test_failed_helper_report_removes_partial_but_keeps_same_named_file(tmp_path, monkeypatch):
    _docker(monkeypatch, tmp_path)
    dest = tmp_path / "book"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"partial bytes")
    # Another task may own a file of the same name; a failed fetch must leave it be.
    dest.write_bytes(b"another task's staged book")

    monkeypatch.setattr(
        bypass._BYPASS_HELPER,
        "run",
        lambda payload, timeout, cancel: {
            "ok": False,
            "reason": "blocked",
            "status": 403,
            "cookies": {},
            "user_agents": {},
        },
    )
    with pytest.raises(fetch.PageFetchError) as error:
        fetch.fetch_to_file(
            "https://a.test/book",
            dest,
            origin_url="https://a.test/",
            max_bytes=100,
            timeout=5,
        )
    assert (error.value.reason, error.value.status) == ("blocked", 403)
    assert not part.exists()
    assert dest.read_bytes() == b"another task's staged book"


def test_malformed_success_envelope_is_a_browser_error(tmp_path, monkeypatch):
    _docker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bypass._BYPASS_HELPER,
        "run",
        lambda payload, timeout, cancel: {"ok": True, "cookies": {}, "user_agents": {}},
    )
    with pytest.raises(fetch.PageFetchError) as error:
        fetch.fetch_to_file(
            "https://a.test/book",
            tmp_path / "book",
            origin_url="https://a.test/",
            max_bytes=100,
            timeout=5,
        )
    assert error.value.reason == "browser"


def test_child_request_serializes_unexpected_errors(tmp_path):
    # A malformed request (no url) must become a published failure, not an
    # exception that kills the helper without a result file.
    result_path = tmp_path / "result.json"
    exit_code = fetch.handle_child_request({"result_path": str(result_path)})
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["reason"] == "browser"
    assert payload["error_type"] == "KeyError"
