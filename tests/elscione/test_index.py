"""Tests for the Elscione index: flatten, URL building, refresh, cache and triggers."""

import json
import logging
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.elscione import index
from tests.elscione import sample_index as sample


@pytest.fixture(autouse=True)
def _fresh_index(tmp_path, monkeypatch):
    """Give every test empty in-memory state and a private CONFIG_DIR."""
    sample.reset_index_state()
    monkeypatch.setattr(index, "CONFIG_DIR", tmp_path)
    yield
    sample.reset_index_state()


class FakeResponse:
    """Minimal stand-in for a streamed requests.Response."""

    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        yield self._body


def _ok_response(tree, headers=None):
    merged = {"Content-Type": "application/json", "ETag": '"v1"'}
    merged.update(headers or {})
    return FakeResponse(200, json.dumps(tree).encode(), merged)


def _patch_get(monkeypatch, responses):
    """Route index.requests.get to the canned responses; returns the call log."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(index.requests, "get", fake_get)
    return calls


# --- Flatten ----------------------------------------------------------------------


def test_flatten_counts_per_reason():
    result = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES)
    assert len(result.entries) == sample.EXPECTED_ENTRIES
    assert result.skipped == sample.EXPECTED_SKIPPED


def test_flatten_entry_fields():
    entry = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES).entries[
        sample.AMAZING_GRACE_PATH
    ]
    assert entry.root == sample.ROOT_NAME
    assert entry.name == "Amazing Grace - Complete [enjugroup][Kindle].epub"
    assert entry.folder == "Amazing Grace"
    assert entry.format == "epub"
    assert entry.size_bytes == 156484
    assert "amazing" in entry.haystack and "kindle" in entry.haystack


def test_flatten_duplicate_keeps_first():
    entry = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES).entries[
        "!Yaoi/Amazing Grace/dup.epub"
    ]
    assert entry.size_bytes == 64


def test_flatten_root_level_file_uses_stem_as_folder():
    entry = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES).entries[
        "Root Book.epub"
    ]
    assert entry.folder == "Root Book"
    assert entry.root == sample.ROOT_NAME


def test_flatten_nested_folder_name_becomes_folder():
    entry = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES).entries[
        "!Yaoi/Amazing Grace/Deep/Deep Book.pdf"
    ]
    assert entry.folder == "Deep"
    assert entry.format == "pdf"


def test_flatten_accepts_list_of_roots():
    result = index.flatten_index(sample.LIST_OF_ROOTS_INDEX, max_bytes=sample.MAX_BYTES)
    assert len(result.entries) == sample.EXPECTED_ENTRIES + 1
    solo = result.entries["Solo Book.pdf"]
    assert solo.root == "Another Root"


def test_flatten_depth_cap_skips_deeper_nodes():
    over = index.flatten_index(sample.deep_tree(70), max_bytes=sample.MAX_BYTES)
    assert over.entries == {}
    assert over.skipped["bad_node"] >= 1

    under = index.flatten_index(sample.deep_tree(60), max_bytes=sample.MAX_BYTES)
    assert len(under.entries) == 1


def test_amazing_grace_url_matches_live_exactly():
    entry = index.flatten_index(sample.SAMPLE_INDEX, max_bytes=sample.MAX_BYTES).entries[
        sample.AMAZING_GRACE_PATH
    ]
    assert index.build_download_url(entry, sample.MIRROR_1) == sample.EXPECTED_AMAZING_GRACE_URL


def test_normalise_folds_accents_full_width_and_punctuation():
    assert index.normalise("Amazíng　Gràce!") == "amazing grace"
    assert index.normalise("Ａｍａｚｉｎｇ") == "amazing"
    assert index.normalise("Volume--10") == "volume 10"


def test_natural_key_orders_numbers_numerically():
    assert index.natural_key("Vol 2") < index.natural_key("Vol 10")
    assert index.natural_key("Vol 10") > index.natural_key("Vol 9")


# --- Refresh ----------------------------------------------------------------------


def test_refresh_persists_and_reloads_from_disk(monkeypatch, tmp_path):
    calls = _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True
    assert calls[0][0] == index.index_url(sample.MIRROR_1)

    raw = tmp_path / "elscione" / "index.json"
    meta_path = tmp_path / "elscione" / "index_meta.json"
    assert raw.exists()
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["entries"] == sample.EXPECTED_ENTRIES
    assert meta["etag"] == '"v1"'
    assert meta["last_error"] is None

    # A fresh process state must rebuild from disk without another download.
    sample.reset_index_state()
    entry = index.get_entry(sample.AMAZING_GRACE_PATH)
    assert entry is not None
    assert entry.size_bytes == 156484


def test_sanity_guard_keeps_old_index_on_shrink(monkeypatch):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True

    smaller = {
        "name": "R",
        "path": ".",
        "folders": [],
        "files": [{"name": "a.epub", "path": "a.epub", "size": 1}],
    }
    _patch_get(monkeypatch, [_ok_response(smaller)])
    assert index.refresh_index() is False
    assert index.index_status()["entries"] == sample.EXPECTED_ENTRIES
    assert "under half" in index.index_status()["last_error"]


def test_sanity_guard_rejects_empty_index(monkeypatch):
    _patch_get(monkeypatch, [_ok_response({"name": "R", "path": ".", "folders": [], "files": []})])
    assert index.refresh_index() is False
    assert index.index_status()["entries"] == 0
    assert "no usable entries" in index.index_status()["last_error"]


def test_garbage_body_is_a_failure_not_an_install(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(200, b"this is not json")])
    assert index.refresh_index() is False
    assert "not valid JSON" in index.index_status()["last_error"]


def test_meta_write_failure_leaves_memory_and_disk_untouched(monkeypatch, tmp_path):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True
    before_fetched_at = index.fetched_at_iso()

    # The next body is fine (the 8-entry list-of-roots form passes the sanity
    # guard), but writing the metadata tmp file fails: only json.dumps is used
    # on that path, so it is the one thing made to raise.
    _patch_get(monkeypatch, [_ok_response(sample.LIST_OF_ROOTS_INDEX)])
    monkeypatch.setattr(index.json, "dumps", MagicMock(side_effect=OSError("disk full")))
    assert index.refresh_index() is False

    status = index.index_status()
    assert "could not persist" in status["last_error"]
    assert status["entries"] == sample.EXPECTED_ENTRIES  # memory still the old index
    assert status["fetched_at"] == before_fetched_at
    directory = tmp_path / "elscione"
    assert json.loads((directory / "index.json").read_bytes()) == sample.SAMPLE_INDEX
    assert not list(directory.glob("*.tmp"))


def test_304_bumps_fetched_at_and_sends_etag(monkeypatch):
    _patch_get(
        monkeypatch,
        [_ok_response(sample.SAMPLE_INDEX, {"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"})],
    )
    assert index.refresh_index() is True
    before = index.fetched_at_iso()

    calls = _patch_get(monkeypatch, [FakeResponse(304)])
    assert index.refresh_index() is True
    assert calls[0][1]["headers"]["If-None-Match"] == '"v1"'
    assert calls[0][1]["headers"]["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert index.index_status()["entries"] == sample.EXPECTED_ENTRIES
    assert index.index_status()["last_error"] is None
    assert index.fetched_at_iso() >= before


def test_blocked_solves_challenge_and_retries_once(monkeypatch):
    calls = _patch_get(monkeypatch, [FakeResponse(403), _ok_response(sample.SAMPLE_INDEX)])
    bypass = MagicMock(return_value="<html>cleared</html>")
    monkeypatch.setattr(index.downloader, "get_bypassed_page", bypass)
    assert index.refresh_index() is True
    assert bypass.call_count == 1
    assert len(calls) == 2
    assert index.get_entry(sample.AMAZING_GRACE_PATH) is not None


def test_blocked_html_body_also_solves(monkeypatch):
    html = b"<html><title>Making sure you're not a bot</title></html>"
    calls = _patch_get(
        monkeypatch,
        [FakeResponse(200, html, {"Content-Type": "text/html"}), _ok_response(sample.SAMPLE_INDEX)],
    )
    bypass = MagicMock(return_value="cleared")
    monkeypatch.setattr(index.downloader, "get_bypassed_page", bypass)
    assert index.refresh_index() is True
    assert bypass.call_count == 1
    assert len(calls) == 2


def test_still_blocked_after_retry_fails(monkeypatch):
    # One mirror only, so the call counts below describe that mirror's chain.
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1])
    _patch_get(monkeypatch, [FakeResponse(403), FakeResponse(503)])
    bypass = MagicMock(return_value="cleared")
    monkeypatch.setattr(index.downloader, "get_bypassed_page", bypass)
    in_page = MagicMock(side_effect=index.page_fetch.PageFetchError("blocked", "challenge"))
    monkeypatch.setattr(index.page_fetch, "fetch_to_file", in_page)
    assert index.refresh_index() is False
    assert bypass.call_count == 1
    assert in_page.call_count == 1
    assert "after one retry" in index.index_status()["last_error"]


def test_still_blocked_over_http_falls_back_to_in_page_fetch(tmp_path, monkeypatch):
    # Seen live: from a home IP Cloudflare kept answering 403 to plain HTTP even right
    # after a solve, while Chrome itself was let through.
    monkeypatch.setattr(index, "TMP_DIR", tmp_path)
    _patch_get(monkeypatch, [FakeResponse(403), FakeResponse(403)])
    monkeypatch.setattr(index.downloader, "get_bypassed_page", MagicMock(return_value="ok"))
    seen = {}

    def fetch_to_file(url, dest, **kwargs):
        seen.update(url=url, origin=kwargs["origin_url"], dest=dest)
        dest.write_bytes(json.dumps(sample.SAMPLE_INDEX).encode())

    monkeypatch.setattr(index.page_fetch, "fetch_to_file", fetch_to_file)
    assert index.refresh_index() is True
    assert (seen["url"], seen["origin"]) == (
        index.index_url(sample.MIRROR_1),
        index.origin_url(sample.MIRROR_1),
    )
    assert not seen["dest"].exists()  # the temp copy is removed after reading
    assert index.get_entry(sample.AMAZING_GRACE_PATH) is not None


def test_bypass_exception_is_recorded_not_leaked(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(403)])
    monkeypatch.setattr(
        index.downloader,
        "get_bypassed_page",
        MagicMock(side_effect=RuntimeError("challenge worker died")),
    )
    assert index.refresh_index() is False
    assert "could not be cleared: challenge worker died" in index.index_status()["last_error"]


def test_refresh_before_load_honours_the_sanity_guard(monkeypatch, tmp_path):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True

    # Fresh process state: the 7-entry cache exists only on disk now.
    sample.reset_index_state()
    tiny = {
        "name": "R",
        "path": ".",
        "folders": [],
        "files": [{"name": "a.epub", "path": "a.epub", "size": 1}],
    }
    _patch_get(monkeypatch, [_ok_response(tiny)])
    assert index.refresh_index() is False
    assert "under half" in index.index_status()["last_error"]
    assert index.index_status()["entries"] == sample.EXPECTED_ENTRIES
    # The disk cache is unchanged too: reloading from it still serves the sample.
    sample.reset_index_state()
    assert index.get_entry(sample.AMAZING_GRACE_PATH) is not None


def test_failed_bypass_keeps_old_index(monkeypatch):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX), FakeResponse(403)])
    assert index.refresh_index() is True
    entries_before = index.index_status()["entries"]

    _patch_get(monkeypatch, [FakeResponse(403)])
    monkeypatch.setattr(index.downloader, "get_bypassed_page", MagicMock(return_value=None))
    assert index.refresh_index() is False
    assert index.index_status()["entries"] == entries_before
    assert "could not be cleared" in index.index_status()["last_error"]


def test_429_notes_rate_limit_and_stops(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(429)])
    note = MagicMock()
    monkeypatch.setattr(index.network, "note_rate_limited", note)
    bypass = MagicMock()
    monkeypatch.setattr(index.downloader, "get_bypassed_page", bypass)
    assert index.refresh_index() is False
    note.assert_called_once_with(index.index_url(sample.MIRROR_1))
    bypass.assert_not_called()


def test_request_exception_is_recorded(monkeypatch):
    def boom(url, **kwargs):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(index.requests, "get", boom)
    assert index.refresh_index() is False
    assert "request failed" in index.index_status()["last_error"]


def test_oversize_index_is_rejected(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(200, b"x" * (index._MAX_INDEX_BYTES + 1))])
    assert index.refresh_index() is False
    assert "32 MiB cap" in index.index_status()["last_error"]


# --- Mirrors -----------------------------------------------------------------------


def _set_mirrors_config(monkeypatch, value):
    """Point ELSCIONE_MIRRORS at ``value``: a list from the tag field or a hand-edited comma string."""
    monkeypatch.setattr(
        index.config,
        "get",
        lambda key, default=None: value if key == "ELSCIONE_MIRRORS" else default,
    )


def test_mirrors_accepts_list_and_comma_string(monkeypatch):
    listed = ["https://a.example", "https://b.example"]
    _set_mirrors_config(monkeypatch, listed)
    assert index.mirrors() == listed

    _set_mirrors_config(monkeypatch, "https://a.example, https://b.example")
    assert index.mirrors() == listed


def test_mirrors_reject_bad_entries_and_dedupe(monkeypatch):
    _set_mirrors_config(
        monkeypatch,
        [
            "https://good.example",
            "good.example",  # bare host: https is supplied
            "https://good.example/",  # trailing slash folds onto the same base
            "http://plain.example",  # not https
            "https://user:pass@good.example",  # userinfo would leak into URLs
            "https://@good.example",  # even empty userinfo is a login
            "https://[::1]",  # mirrors are hostnames, not IP literals
            "https://1.2.3.4",
            "https://go od.example",  # interior whitespace is never a host
            "https://good.example:8443",  # only the default https port
            "https://bad-port.example:not-a-port",
            "https://good.example/with/path",
            "https://good.example?query=1",
            "https://good.example#fragment",
            "   ",
            "",
        ],
    )
    with sample.captured_logs(index.logger) as messages:
        assert index.mirrors() == ["https://good.example"]
    assert len([m for m in messages if "Ignoring invalid Elscione mirrors" in m]) == 1


def test_mirrors_drop_parser_breaking_entries_without_aborting_the_list(monkeypatch):
    # urlsplit (and IDNA encoding) raise outright on these; the rest of the
    # list must still be validated and served.
    _set_mirrors_config(
        monkeypatch,
        ["https://[", "https://good.example／evil", "https://ok.example"],
    )
    with sample.captured_logs(index.logger) as messages:
        assert index.mirrors() == ["https://ok.example"]
    assert any("Ignoring invalid Elscione mirrors" in message for message in messages)


def test_mirrors_canonicalise_trailing_dot_and_idna(monkeypatch):
    # The trailing-dot and Unicode/punycode spellings of one host are the same
    # mirror, so they fold instead of producing near-duplicates.
    _set_mirrors_config(
        monkeypatch,
        [
            "https://good.example",
            "https://good.example.",
            "https://münchen.example",
            "https://xn--mnchen-3ya.example",
        ],
    )
    assert index.mirrors() == ["https://good.example", "https://xn--mnchen-3ya.example"]


def test_mirrors_fall_back_to_the_defaults_when_nothing_valid(monkeypatch):
    _set_mirrors_config(monkeypatch, ["http://only.example"])
    assert index.mirrors() == index.DEFAULT_MIRRORS

    _set_mirrors_config(monkeypatch, [])
    assert index.mirrors() == index.DEFAULT_MIRRORS


def test_refresh_falls_over_to_second_mirror_and_records_it(monkeypatch):
    calls = _patch_get(monkeypatch, [FakeResponse(500), _ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True
    assert [call[0] for call in calls] == [
        index.index_url(sample.MIRROR_1),
        index.index_url(sample.MIRROR_2),
    ]
    status = index.index_status()
    assert status["mirror"] == sample.MIRROR_2
    # The mirror that served becomes the sticky favourite for downloads.
    assert index.download_order()[0] == sample.MIRROR_2


def test_conditional_headers_go_only_to_the_etag_mirror(monkeypatch):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True
    assert index.index_status()["mirror"] == sample.MIRROR_1

    # Try the other mirror first; the sticky favourite would reorder, so the
    # order itself is pinned for this one refresh.
    monkeypatch.setattr(index, "download_order", lambda: [sample.MIRROR_2, sample.MIRROR_1])
    calls = _patch_get(monkeypatch, [FakeResponse(500), FakeResponse(304)])
    assert index.refresh_index() is True
    # Mirror 2 never sees mirror 1's validator: it names a different document.
    assert calls[0][0] == index.index_url(sample.MIRROR_2)
    assert "If-None-Match" not in calls[0][1]["headers"]
    assert calls[1][0] == index.index_url(sample.MIRROR_1)
    assert calls[1][1]["headers"]["If-None-Match"] == '"v1"'
    assert index.index_status()["mirror"] == sample.MIRROR_1


def test_all_mirrors_failing_names_each_one(monkeypatch):
    calls = _patch_get(monkeypatch, [FakeResponse(500), FakeResponse(404)])
    assert index.refresh_index() is False
    assert [call[0] for call in calls] == [
        index.index_url(sample.MIRROR_1),
        index.index_url(sample.MIRROR_2),
    ]
    error = index.index_status()["last_error"]
    assert "all mirrors failed" in error
    assert f"{sample.MIRROR_1}: HTTP 500" in error
    assert f"{sample.MIRROR_2}: HTTP 404" in error


def test_download_order_puts_a_configured_favourite_first(monkeypatch):
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1, sample.MIRROR_2])
    assert index.download_order() == [sample.MIRROR_1, sample.MIRROR_2]

    index.set_preferred_mirror(sample.MIRROR_2)
    assert index.download_order() == [sample.MIRROR_2, sample.MIRROR_1]

    # A favourite that is no longer configured is quietly dropped.
    sample.pin_mirrors(monkeypatch, [sample.MIRROR_1])
    assert index.download_order() == [sample.MIRROR_1]


def test_refresh_names_the_serving_mirror_in_the_log(monkeypatch):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    with sample.captured_logs(index.logger, level=logging.INFO) as messages:
        assert index.refresh_index() is True
    assert any(
        f"Elscione index refreshed from {sample.MIRROR_1}" in message for message in messages
    )


def test_refresh_fallover_logs_the_failed_mirror(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(500), _ok_response(sample.SAMPLE_INDEX)])
    with sample.captured_logs(index.logger, level=logging.INFO) as messages:
        assert index.refresh_index() is True
    assert (
        f"Elscione mirror {sample.MIRROR_1} failed (HTTP 500); trying {sample.MIRROR_2}" in messages
    )


def test_304_with_no_usable_index_is_a_mirror_failure(monkeypatch):
    # An etag left over from a cache that now flattens to nothing (say the
    # size cap was lowered) must not turn a 304 into a false success.
    sample.install_entries({})
    with index._STATE_LOCK:
        index._meta["etag"] = '"v1"'
        index._meta["etag_mirror"] = sample.MIRROR_1

    calls = _patch_get(monkeypatch, [FakeResponse(304), FakeResponse(304)])
    assert index.refresh_index() is False
    # The validator was not even sent, and the 304 counts as a failure.
    assert "If-None-Match" not in calls[0][1]["headers"]
    assert "HTTP 304 with no usable index" in index.index_status()["last_error"]


def test_meta_replace_failure_restores_the_previous_raw_index(monkeypatch, tmp_path):
    _patch_get(monkeypatch, [_ok_response(sample.SAMPLE_INDEX)])
    assert index.refresh_index() is True
    raw_file = tmp_path / "elscione" / "index.json"

    # The metadata destination is a directory, so the tmp writes succeed but
    # only the final metadata replace fails.
    (tmp_path / "elscione" / "index_meta.json").unlink()
    (tmp_path / "elscione" / "index_meta.json").mkdir()
    _patch_get(monkeypatch, [_ok_response(sample.LIST_OF_ROOTS_INDEX)])
    assert index.refresh_index() is False

    status = index.index_status()
    assert "could not persist" in status["last_error"]
    assert status["entries"] == sample.EXPECTED_ENTRIES  # memory kept the old index
    # The old catalogue is back on disk, and no leftovers remain.
    assert json.loads(raw_file.read_bytes()) == sample.SAMPLE_INDEX
    directory = tmp_path / "elscione"
    assert not list(directory.glob("*.tmp"))
    assert not list(directory.glob("*.bak"))


# --- Backoff and single flight -----------------------------------------------------


def test_failure_backoff_blocks_automatic_refresh(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(500)])
    assert index.refresh_index() is False
    with index._STATE_LOCK:
        assert index._backoff_until > time.time()

    with patch.object(index, "refresh_index") as run:
        assert index.start_background_refresh() is False
        run.assert_not_called()

    # The settings button forces its way past the backoff.
    with patch.object(index, "refresh_index") as run:
        assert index.start_background_refresh(force=True) is True
        _wait_until_not_refreshing()
        run.assert_called_once()


def test_backoff_doubles_up_to_the_cap(monkeypatch):
    _patch_get(monkeypatch, [FakeResponse(500)])
    index.refresh_index()
    with index._STATE_LOCK:
        first = index._backoff_until - time.time()
    index.refresh_index()
    with index._STATE_LOCK:
        second = index._backoff_until - time.time()
    assert first > index._BACKOFF_BASE_SECONDS * 0.9
    assert second > first * 1.9

    for _ in range(12):
        index.refresh_index()
    with index._STATE_LOCK:
        final = index._backoff_until - time.time()
    assert final <= index._BACKOFF_MAX_SECONDS + 5


def test_start_background_refresh_is_single_flight():
    with index._STATE_LOCK:
        index._refreshing = True
    assert index.start_background_refresh() is False
    assert index.start_background_refresh(force=True) is False


def _wait_until_not_refreshing():
    for _ in range(500):
        with index._STATE_LOCK:
            if not index._refreshing:
                return
        time.sleep(0.01)


# --- Search-time triggers ----------------------------------------------------------


def test_search_without_index_raises_and_starts_exactly_one_refresh(monkeypatch):
    started = MagicMock(return_value=True)
    monkeypatch.setattr(index, "start_background_refresh", started)
    with pytest.raises(SourceUnavailableError, match="index is being built"):
        index.search("amazing grace", 50)
    started.assert_called_once()


def test_search_with_stale_index_refreshes_in_background(monkeypatch):
    sample.install_entries(sample.sample_entries())
    with index._STATE_LOCK:
        index._meta["fetched_at"] = "2020-01-01T00:00:00+00:00"
    started = MagicMock(return_value=True)
    monkeypatch.setattr(index, "start_background_refresh", started)
    assert index.search("amazing grace", 50)
    started.assert_called_once()


def test_search_with_fresh_index_does_not_refresh(monkeypatch):
    sample.install_entries(sample.sample_entries())
    started = MagicMock(return_value=True)
    monkeypatch.setattr(index, "start_background_refresh", started)
    assert index.search("amazing grace", 50)
    started.assert_not_called()


def test_stale_warning_logged_once_per_hour(monkeypatch):
    sample.install_entries(sample.sample_entries())
    with index._STATE_LOCK:
        index._meta["fetched_at"] = "2020-01-01T00:00:00+00:00"
    monkeypatch.setattr(index, "start_background_refresh", MagicMock(return_value=True))
    with sample.captured_logs(index.logger) as messages:
        index.search("amazing grace", 50)
        index.search("amazing grace", 50)
    warnings = [m for m in messages if "Serving Elscione index from" in m]
    assert len(warnings) == 1


def test_index_status_reports_loaded_state():
    assert index.index_status()["entries"] == 0
    assert index.index_status()["fetched_at"] is None

    sample.install_entries(sample.sample_entries())
    status = index.index_status()
    assert status["entries"] == sample.EXPECTED_ENTRIES
    assert status["fetched_at"] is not None
    assert status["refreshing"] is False
    assert status["last_error"] is None


def test_index_status_reads_cached_disk_index(tmp_path):
    directory = tmp_path / "elscione"
    directory.mkdir()
    (directory / "index.json").write_text(json.dumps(sample.SAMPLE_INDEX), encoding="utf-8")
    (directory / "index_meta.json").write_text(
        json.dumps({"fetched_at": "2024-05-01T00:00:00+00:00", "entries": 7}), encoding="utf-8"
    )
    status = index.index_status()
    assert status["entries"] == sample.EXPECTED_ENTRIES
    assert status["fetched_at"] == "2024-05-01T00:00:00+00:00"
    assert index.get_entry(sample.AMAZING_GRACE_PATH).root == sample.ROOT_NAME
