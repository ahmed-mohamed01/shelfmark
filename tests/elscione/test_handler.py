"""Tests for ElscioneHandler: index-only trust, staging names, error mapping."""

import contextlib
import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.bypass.page_fetch import PageFetchError, PageFetchResult
from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.elscione import handler
from shelfmark.release_sources.elscione.handler import ElscioneHandler
from tests.elscione import sample_index as sample


@pytest.fixture(autouse=True)
def _fresh_index(tmp_path, monkeypatch):
    """Give every test empty in-memory state and a private CONFIG_DIR."""
    sample.reset_index_state()
    monkeypatch.setattr(handler.index, "CONFIG_DIR", tmp_path)
    yield
    sample.reset_index_state()


def _task(task_id=sample.AMAZING_GRACE_PATH, fmt="epub"):
    return DownloadTask(
        task_id=task_id,
        source="elscione",
        title="Amazing Grace",
        author="Novel Author",
        year="2024",
        format=fmt,
        size="152.8 KB",
    )


def _config(values=None):
    values = dict(values or {})
    return patch.object(
        handler.config, "get", side_effect=lambda key, default=None: values.get(key, default)
    )


def _fetch_ok(url, dest, *, origin_url, max_bytes, timeout, cancel_flag, method="fetch"):
    """Fake fetch_to_file: write bytes to dest and report them back."""
    dest.write_bytes(b"book-bytes")
    return PageFetchResult(
        path=dest,
        size=dest.stat().st_size,
        content_type="application/epub+zip",
        final_url=url,
        method="fetch",
    )


def _download(tmp_path, task=None, *, fetch=None, values=None, validate_result=None):
    """Run the handler with everything external patched; returns (result, status).

    ``validate_result`` is what the (mocked) validator reports, None meaning the
    file passes; the real validator has its own test module.
    """
    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    fetch_mock = fetch if fetch is not None else _fetch_ok
    patches = [
        _config(values),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch_mock),
        patch.object(handler.validate, "check", return_value=validate_result),
        # The 404 mapping case must not spawn a real refresh thread (offline tests).
        patch.object(handler.index, "start_background_refresh", MagicMock(return_value=True)),
    ]
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        result = ElscioneHandler().download(task or _task(), threading.Event(), MagicMock(), status)
    return result, status


def test_download_returns_staging_path(tmp_path):
    captured = {}

    def fetch(url, dest, **kwargs):
        captured.update(kwargs, url=url)
        return _fetch_ok(url, dest, **kwargs)

    result, status = _download(tmp_path, fetch=fetch, validate_result=None)
    expected = tmp_path / "Novel Author - Amazing Grace (2024).epub"
    assert result == str(expected)
    assert expected.exists()
    status.assert_any_call("resolving", "Opening Elscione in browser")
    status.assert_any_call("downloading", "Downloading in browser")
    assert captured["url"] == sample.EXPECTED_AMAZING_GRACE_URL
    assert captured["origin_url"] == handler.index.origin_url(sample.MIRROR_1)
    assert captured["max_bytes"] == 64 * 1024 * 1024
    assert captured["method"] == "fetch"


def test_download_uses_index_format_not_task_format(tmp_path):
    result, _ = _download(tmp_path, task=_task(fmt="pdf"), validate_result=None)
    assert result.endswith(".epub")


def test_unknown_path_rejected_without_fetching(tmp_path):
    fetch = MagicMock()
    result, status = _download(tmp_path, task=_task(task_id="../evil.bat"), fetch=fetch)
    assert result is None
    fetch.assert_not_called()
    status.assert_any_call("error", "No longer in the Elscione index; search again")


def test_staging_name_file_organization_none_uses_sanitised_index_name(tmp_path):
    entries = sample.sample_entries()
    # A colon cannot appear in a path segment we would download, but the file
    # name field is not segment-checked, so sanitising still matters.
    entries["we:ird.epub"] = handler.index.IndexEntry(
        path="we:ird.epub",
        root=sample.ROOT_NAME,
        name="we:ird.epub",
        folder="we:ird",
        format="epub",
        size_bytes=10,
        haystack="we ird",
        volumes=None,
    )
    sample.install_entries(entries)
    status = MagicMock()
    with (
        _config({"FILE_ORGANIZATION": "none"}),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=_fetch_ok),
        patch.object(handler.validate, "check", return_value=None),
    ):
        result = ElscioneHandler().download(
            _task(task_id="we:ird.epub"), threading.Event(), MagicMock(), status
        )
    assert result == str(tmp_path / "we_ird.epub")


def test_cancelled_before_start(tmp_path):
    status = MagicMock()
    cancel = threading.Event()
    cancel.set()
    with _config(), patch.object(handler, "TMP_DIR", tmp_path):
        result = ElscioneHandler().download(_task(), cancel, MagicMock(), status)
    assert result is None
    status.assert_any_call("cancelled", "Cancelled")


def test_fetch_cancelled_reports_cancelled(tmp_path):
    def fetch(url, dest, **kwargs):
        raise PageFetchError("cancelled", "stopped")

    result, status = _download(tmp_path, fetch=fetch)
    assert result is None
    status.assert_any_call("cancelled", "Cancelled")
    assert not any(call.args[0] == "error" for call in status.call_args_list)


@pytest.mark.parametrize(
    "reason,status_code,message",
    [
        ("unsupported", None, "Elscione downloads need the internal bypasser"),
        ("blocked", None, "Elscione challenge could not be cleared"),
        ("http_status", 404, "Elscione returned HTTP 404"),
        ("http_status", 500, "Elscione returned HTTP 500"),
        ("too_large", None, "File exceeds the 64 MiB limit"),
        ("timeout", None, "Timed out after 300 s"),
        ("short_read", None, "Download ended early (short read)"),
        ("browser", None, "Browser error while downloading from Elscione"),
    ],
)
def test_fetch_error_mapping(tmp_path, reason, status_code, message):
    def fetch(url, dest, **kwargs):
        raise PageFetchError(reason, "boom", status=status_code)

    result, status = _download(tmp_path, fetch=fetch)
    assert result is None
    status.assert_any_call("error", message)


def test_http_404_starts_index_refresh(tmp_path):
    def fetch(url, dest, **kwargs):
        raise PageFetchError("http_status", "gone", status=404)

    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    refresh = MagicMock(return_value=True)
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
        patch.object(handler.index, "start_background_refresh", refresh),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is None
    refresh.assert_called_once()


def test_validation_failure_deletes_file_and_reports_reason(tmp_path):
    result, status = _download(tmp_path, validate_result="not a valid EPUB (missing zip header)")
    assert result is None
    assert list(tmp_path.iterdir()) == []
    status.assert_any_call("error", "not a valid EPUB (missing zip header)")


def test_unexpected_validation_exception_deletes_staged_file(tmp_path):
    # Whatever escapes validate.check(), the staged file must not survive a
    # download() that returns None.
    sample.install_entries(sample.sample_entries())
    status = MagicMock()

    def explode(path, entry, result):
        raise RuntimeError("boom")

    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=_fetch_ok),
        patch.object(handler.validate, "check", side_effect=explode),
        patch.object(handler.index, "start_background_refresh", MagicMock(return_value=True)),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is None
    assert list(tmp_path.iterdir()) == []
    status.assert_any_call("error", "boom")


def test_fetch_failure_leaves_a_same_named_file_alone(tmp_path):
    # Two queued books can map to one staging name; a fetch that never produced a
    # file must not delete the other task's staged copy.
    other = tmp_path / handler.build_filename("Amazing Grace", "Novel Author", "2024", "epub")
    other.write_bytes(b"another task's staged book")

    def refuse(url, dest, **_kwargs):
        raise PageFetchError("blocked", "challenge")

    result, status = _download(tmp_path, fetch=refuse)
    assert result is None
    assert other.read_bytes() == b"another task's staged book"
    status.assert_any_call("error", "Elscione challenge could not be cleared")


def test_cancel_returns_false():
    assert ElscioneHandler().cancel("any-id") is False


# --- Mirror fallover ---------------------------------------------------------------


def test_blocked_mirror_falls_over_and_second_serves(tmp_path, monkeypatch):
    seen = []

    def fetch(url, dest, **kwargs):
        seen.append((url, kwargs["origin_url"]))
        if url.startswith(sample.MIRROR_1):
            raise PageFetchError("blocked", "challenge")
        return _fetch_ok(url, dest, **kwargs)

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    result, status = _download(tmp_path, fetch=fetch)
    assert result is not None
    assert seen == [
        (sample.EXPECTED_AMAZING_GRACE_URL, sample.MIRROR_1 + "/"),
        (sample.MIRROR_2_DOWNLOAD_URL, sample.MIRROR_2 + "/"),
    ]
    status.assert_any_call("downloading", "Downloading in browser")
    status.assert_any_call(
        "downloading", f"Retrying on {handler.index.mirror_host(sample.MIRROR_2)}"
    )
    # The mirror that served becomes the sticky favourite.
    assert handler.index.download_order()[0] == sample.MIRROR_2


def test_non_mirror_errors_do_not_fall_over(tmp_path, monkeypatch):
    # cancelled, unsupported and too_large would hit the same wall on every
    # mirror, so the first failure is also the final one.
    for reason in ("cancelled", "unsupported", "too_large"):
        fetch = MagicMock(side_effect=PageFetchError(reason, "boom"))
        sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
        result, _ = _download(tmp_path, fetch=fetch)
        assert result is None
        fetch.assert_called_once()


def test_validation_failure_does_not_fall_over(tmp_path, monkeypatch):
    # A validation failure says the file is bad, not the mirror: the file on
    # the next mirror is byte-identical, so there is nothing to retry.
    fetch = MagicMock(side_effect=_fetch_ok)
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    result, _ = _download(tmp_path, fetch=fetch, validate_result="not a valid EPUB")
    assert result is None
    fetch.assert_called_once()


def test_preferred_mirror_is_tried_first(tmp_path, monkeypatch):
    sample.install_entries(sample.sample_entries())
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    # install_entries resets the favourite, so it is set after it here.
    handler.index.set_preferred_mirror(sample.MIRROR_2)
    captured = {}

    def fetch(url, dest, **kwargs):
        captured.update(kwargs, url=url)
        return _fetch_ok(url, dest, **kwargs)

    status = MagicMock()
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is not None
    assert captured["url"] == sample.MIRROR_2_DOWNLOAD_URL
    assert captured["origin_url"] == sample.MIRROR_2 + "/"


def test_404_on_first_mirror_falls_through_without_index_refresh(tmp_path, monkeypatch):
    def fetch(url, dest, **kwargs):
        if url.startswith(sample.MIRROR_1):
            raise PageFetchError("http_status", "gone", status=404)
        return _fetch_ok(url, dest, **kwargs)

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    refresh = MagicMock(return_value=True)
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
        patch.object(handler.index, "start_background_refresh", refresh),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is not None
    # One mirror saying 404 does not make the index stale: another served it.
    refresh.assert_not_called()


def test_404_on_one_mirror_and_blocked_other_does_not_refresh(tmp_path, monkeypatch):
    def fetch(url, dest, **kwargs):
        if url.startswith(sample.MIRROR_1):
            raise PageFetchError("http_status", "gone", status=404)
        raise PageFetchError("blocked", "challenge")

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    refresh = MagicMock(return_value=True)
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
        patch.object(handler.index, "start_background_refresh", refresh),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is None
    # Only when every mirror says gone is the index itself stale.
    refresh.assert_not_called()
    status.assert_any_call("error", "Elscione challenge could not be cleared")


def test_404_on_all_mirrors_refreshes_the_index(tmp_path, monkeypatch):
    def fetch(url, dest, **kwargs):
        raise PageFetchError("http_status", "gone", status=404)

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    refresh = MagicMock(return_value=True)
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
        patch.object(handler.index, "start_background_refresh", refresh),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is None
    refresh.assert_called_once()


def test_mirror_budget_stops_later_mirrors(tmp_path, monkeypatch):
    # Two per-mirror timeouts (2 x 300 s) since handler start: the third
    # mirror is never asked.
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2, "https://third.example"])
    fetch = MagicMock(side_effect=PageFetchError("blocked", "challenge"))
    ticks = iter([0.0, 1.0, 1000.0])
    monkeypatch.setattr(handler.time, "monotonic", lambda: next(ticks, 1000.0))
    result, status = _download(tmp_path, fetch=fetch)
    assert result is None
    assert fetch.call_count == 2
    tried = [call.args[0] for call in fetch.call_args_list]
    assert tried[0].startswith(sample.MIRROR_1)
    assert tried[1].startswith(sample.MIRROR_2)
    status.assert_any_call("error", "Elscione challenge could not be cleared")


def test_budget_stop_does_not_trigger_the_all_404_refresh(tmp_path, monkeypatch):
    # Two mirrors said 404 and the budget ran out before the third: the file
    # may live on the mirror never tried, so the index is not known stale.
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2, "https://third.example"])
    fetch = MagicMock(side_effect=PageFetchError("http_status", "gone", status=404))
    ticks = iter([0.0, 1.0, 1000.0])
    monkeypatch.setattr(handler.time, "monotonic", lambda: next(ticks, 1000.0))
    sample.install_entries(sample.sample_entries())
    status = MagicMock()
    refresh = MagicMock(return_value=True)
    with (
        _config(),
        patch.object(handler, "TMP_DIR", tmp_path),
        patch.object(handler, "fetch_to_file", side_effect=fetch),
        patch.object(handler.validate, "check", return_value=None),
        patch.object(handler.index, "start_background_refresh", refresh),
    ):
        result = ElscioneHandler().download(_task(), threading.Event(), MagicMock(), status)
    assert result is None
    assert fetch.call_count == 2
    refresh.assert_not_called()
    status.assert_any_call("error", "Elscione returned HTTP 404")


# --- Attempt temp files and stall grace ---------------------------------------------


def test_completed_file_from_a_failed_attempt_is_swept(tmp_path, monkeypatch):
    # The fetch layer can rename a complete file into place and still raise
    # (a timeout during browser cleanup); that file belongs to a failed
    # attempt and must not survive it.
    def fetch(url, dest, **kwargs):
        dest.write_bytes(b"complete-but-unvalidated")
        if url.startswith(sample.MIRROR_1):
            raise PageFetchError("timeout", "timed out during cleanup")
        raise PageFetchError("blocked", "challenge")

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    result, status = _download(tmp_path, fetch=fetch)
    assert result is None
    assert list(tmp_path.iterdir()) == []
    status.assert_any_call("error", "Elscione challenge could not be cleared")


def test_success_leaves_exactly_the_staging_file(tmp_path):
    result, _ = _download(tmp_path)
    expected = tmp_path / "Novel Author - Amazing Grace (2024).epub"
    assert result == str(expected)
    assert [path.name for path in tmp_path.iterdir()] == [expected.name]


def test_attempt_temp_names_are_unique_per_mirror(tmp_path, monkeypatch):
    dests = []

    def fetch(url, dest, **kwargs):
        dests.append(dest)
        raise PageFetchError("blocked", "challenge")

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    result, _ = _download(tmp_path, fetch=fetch)
    assert result is None
    assert len(dests) == 2
    assert dests[0] != dests[1]
    assert all(path.name.startswith(".elscione-") for path in dests)
    assert [path.suffix for path in dests] == [".epub", ".epub"]
    assert list(tmp_path.iterdir()) == []


def test_activity_grace_declares_each_mirror_attempt(tmp_path, monkeypatch):
    # A slow fetch (or a fallover across mirrors) can outlive the
    # orchestrator's stall timeout, so every attempt declares its worst case
    # up front and releases it when it ends.
    def fetch(url, dest, **kwargs):
        if url.startswith(sample.MIRROR_1):
            raise PageFetchError("timeout", "slow")
        return _fetch_ok(url, dest, **kwargs)

    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    result, status = _download(tmp_path, fetch=fetch)
    assert result is not None
    graces = [
        call.args[1] for call in status.call_args_list if call.args[0] == "__activity_grace__"
    ]
    # Request (timeout 300 s + 60 s) then release, once per attempt.
    assert graces == ["360.0", "0.0", "360.0", "0.0"]
