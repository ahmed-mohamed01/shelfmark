"""Tests for ElscioneSource: availability gating, query building, release mapping."""

import logging
import types
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.elscione import index
from shelfmark.release_sources.elscione import source as elscione_source
from shelfmark.release_sources.elscione.source import ElscioneSource, _build_queries
from tests.elscione import sample_index as sample


@pytest.fixture(autouse=True)
def _fresh_index(tmp_path, monkeypatch):
    """Give every test empty in-memory state and a private CONFIG_DIR."""
    sample.reset_index_state()
    monkeypatch.setattr(index, "CONFIG_DIR", tmp_path)
    yield
    sample.reset_index_state()


def _plan(manual_query=None, variants=None):
    return types.SimpleNamespace(manual_query=manual_query, title_variants=variants or [])


def _variant(title, author="Some Author"):
    return types.SimpleNamespace(title=title, author=author)


def _book(title="Fallback Title"):
    return types.SimpleNamespace(title=title)


def _config(values=None):
    """Patch the shared config singleton for every config.get the source stack makes."""
    values = dict(values or {})
    return patch.object(
        elscione_source.config,
        "get",
        side_effect=lambda key, default=None: values.get(key, default),
    )


_AVAILABLE = {
    "ELSCIONE_ENABLED": True,
    "USE_CF_BYPASS": True,
    "USING_EXTERNAL_BYPASSER": False,
}


class TestIsAvailable:
    @pytest.mark.parametrize(
        "values,expected",
        [
            (_AVAILABLE, True),
            ({**_AVAILABLE, "ELSCIONE_ENABLED": False}, False),
            ({**_AVAILABLE, "ELSCIONE_ENABLED": "true"}, False),  # truthy string must not enable
            ({**_AVAILABLE, "USE_CF_BYPASS": False}, False),
            ({**_AVAILABLE, "USING_EXTERNAL_BYPASSER": True}, False),
        ],
    )
    def test_matrix(self, values, expected):
        with _config(values):
            assert ElscioneSource().is_available() is expected


# --- Query building ----------------------------------------------------------------


def test_build_queries_manual_query_wins_and_is_never_shortened():
    assert _build_queries(_plan(manual_query="  amazing grace "), _book("ignored")) == [
        ("amazing grace", False)
    ]


def test_build_queries_uses_every_variant_title_but_never_author():
    plan = _plan(
        variants=[_variant("Amazing Grace", "Kiito Kiito"), _variant("Vol 1", "Kiito Kiito")]
    )
    queries = _build_queries(plan, _book("x"))
    assert queries == [("Amazing Grace", True), ("Vol 1", True)]
    assert all("Kiito" not in query for query, _fallback in queries)


def test_build_queries_dedups_casefolded_variants():
    plan = _plan(variants=[_variant("Amazing Grace", ""), _variant("AMAZING GRACE", "")])
    assert _build_queries(plan, _book("x")) == [("Amazing Grace", True)]


def test_build_queries_book_title_fallback():
    assert _build_queries(_plan(), _book("Fallback Title")) == [("Fallback Title", True)]


def test_build_queries_no_queries_at_all():
    assert _build_queries(_plan(), _book("")) == []


# --- Search ------------------------------------------------------------------------


def _installed_search(query, *, content_type="ebook", max_results=None, values=None, entries=None):
    """Search through the source with the sample index installed."""
    sample.install_entries(entries if entries is not None else sample.sample_entries())
    overrides = {**_AVAILABLE}
    if max_results is not None:
        overrides["ELSCIONE_MAX_RESULTS"] = max_results
    overrides.update(values or {})
    book = _book("ignored")
    plan = _plan(variants=[_variant(query)]) if isinstance(query, str) else query
    with (
        patch.object(ElscioneSource, "is_available", return_value=True),
        _config(overrides),
    ):
        return ElscioneSource().search(book, plan, content_type=content_type)


def test_search_non_ebook_returns_empty():
    assert _installed_search("Amazing Grace", content_type="audiobook") == []


def test_search_unavailable_returns_empty():
    with patch.object(ElscioneSource, "is_available", return_value=False):
        assert ElscioneSource().search(_book("x"), _plan(variants=[_variant("x")])) == []


def test_search_matches_and_maps_releases():
    releases = _installed_search("Amazing Grace")
    by_id = {release.source_id: release for release in releases}
    complete = by_id[sample.AMAZING_GRACE_PATH]
    assert complete.source == "elscione"
    # No extension in the title: Shelfmark's naming appends ``format`` itself.
    assert complete.title == "Amazing Grace - Complete [enjugroup][Kindle]"
    assert complete.format == "epub"
    assert complete.size == "152.8 KB"
    assert complete.size_bytes == 156484
    assert complete.download_url is None
    assert complete.indexer == "Elscione"
    assert complete.content_type == "ebook"
    assert complete.extra["book_title"] == "Amazing Grace"
    assert complete.extra["root"] == sample.ROOT_NAME
    assert complete.extra["language"] is None
    assert complete.extra["index_fetched_at"] is not None


def test_search_all_terms_must_match():
    releases = _installed_search("Amazing Grace Kindle")
    assert [release.source_id for release in releases] == [sample.AMAZING_GRACE_PATH]


def test_search_no_hits_returns_empty():
    assert _installed_search("Completely Unknown Book") == []


def test_search_natural_ordering():
    releases = _installed_search("Amazing Grace")
    names = [release.title for release in releases]
    assert names.index("Amazing Grace - Vol 2") < names.index("Amazing Grace - Vol 10")


def test_search_cap():
    assert len(_installed_search("Amazing Grace", max_results=2)) == 2


def test_search_unicode_accents_match():
    assert _installed_search("Amazíng Gràce")


def test_search_full_width_matches():
    assert _installed_search("Ａｍａｚｉｎｇ")


def test_search_punctuation_noise_matches():
    assert _installed_search("Amazing: Grace!")


def test_search_tries_queries_in_order_until_hits():
    plan = _plan(variants=[_variant("No Such Book Anywhere"), _variant("Amazing Grace")])
    releases = _installed_search(plan)
    ids = [release.source_id for release in releases]
    assert sample.AMAZING_GRACE_PATH in ids


def test_search_never_uses_author_terms():
    plan = _plan(variants=[_variant("Amazing Grace", "An Unknown Author")])
    releases = _installed_search(plan)
    assert releases


def test_search_without_index_raises_and_starts_exactly_one_refresh():
    started = MagicMock(return_value=True)
    with (
        patch.object(ElscioneSource, "is_available", return_value=True),
        _config(_AVAILABLE),
        patch.object(index, "start_background_refresh", started),
    ):
        with pytest.raises(SourceUnavailableError, match="index is being built"):
            ElscioneSource().search(_book("x"), _plan(variants=[_variant("Amazing Grace")]))
    started.assert_called_once()


# --- Metadata-style titles and volume matching ---------------------------------------


def _seventh_time_loop_search(query):
    return _installed_search(query, entries=sample.seventh_time_loop_entries())


def test_search_long_metadata_title_falls_back_to_the_series_name():
    # The full metadata title matches nothing (the files never carry the
    # subtitle), so the "7th Time Loop" variant must take over.
    releases = _seventh_time_loop_search(sample.SEVENTH_TIME_LOOP_TITLE)
    assert [release.source_id for release in releases] == [
        "7th Time Loop/7th Time Loop - Volume 01 [Seven Seas][Kobo].epub"
    ]


def test_search_volume_request_skips_other_volumes_and_unnumbered_files():
    releases = _seventh_time_loop_search(sample.SEVENTH_TIME_LOOP_TITLE)
    ids = [release.source_id for release in releases]
    assert not any("Volume 02" in path or "Volume 10" in path for path in ids)
    # The omnibus-style "Complete" file declares no volume, so it cannot be
    # assumed to contain volume 1.
    assert not any("Complete" in path for path in ids)


def test_search_without_volume_returns_every_file():
    releases = _seventh_time_loop_search("7th Time Loop")
    assert len(releases) == 4


def test_search_volume_range_entry_covers_the_requested_volume():
    releases = _seventh_time_loop_search("Omnibus Collection, Vol. 5")
    assert [release.source_id for release in releases] == [
        "Omnibus Collection/Omnibus Collection - Volume 01-17.epub"
    ]


def test_search_volume_excludes_entries_whose_range_misses_it():
    releases = _seventh_time_loop_search("Omnibus Collection, Vol. 18")
    assert releases == []


def test_search_logs_the_variant_that_hit_and_its_volume():
    _seventh_time_loop_search(sample.SEVENTH_TIME_LOOP_TITLE)
    with sample.captured_logs(index.logger, level=logging.INFO) as messages:
        _seventh_time_loop_search(sample.SEVENTH_TIME_LOOP_TITLE)
    searches = [m for m in messages if m.startswith("Searching Elscione for:")]
    assert searches == ["Searching Elscione for: 7th Time Loop (volume 1.0, 1 hits)"]


def test_search_short_variant_never_widens_to_unrelated_series():
    # "Magic: Missing Subtitle" is neither "Magic Academy" nor "Magic Knight",
    # so the one-term "Magic" fallback must not fire.
    releases = _installed_search(
        "Magic: Missing Subtitle", entries=sample.seventh_time_loop_entries()
    )
    assert releases == []


def test_manual_query_is_never_shortened():
    # What the user typed is searched as typed (brackets and volume aside);
    # the "7th Time Loop" fallback a metadata title gets would silently
    # change the search.
    plan = _plan(manual_query=sample.SEVENTH_TIME_LOOP_TITLE)
    releases = _installed_search(plan, entries=sample.seventh_time_loop_entries())
    assert releases == []


def test_search_volume_range_request_matches_overlapping_entries():
    releases = _seventh_time_loop_search("Range Series, Vol. 1-3")
    ids = {release.source_id for release in releases}
    assert ids == {
        "Range Series/Range Series - Volume 01.epub",
        "Range Series/Range Series - Volume 02.epub",
        "Range Series/Range Series - Volume 03.epub",
        "Range Series/Range Series - Volume 01-17.epub",
    }


# --- Records and columns -------------------------------------------------------------


def test_search_results_are_releases():
    assert ElscioneSource().search_results_are_releases() is True


def test_get_record_for_indexed_path():
    sample.install_entries(sample.sample_entries())
    record = ElscioneSource().get_record(sample.AMAZING_GRACE_PATH)
    assert record is not None
    assert record.id == sample.AMAZING_GRACE_PATH
    assert record.source == "elscione"
    assert record.format == "epub"


def test_get_record_unknown_path_is_none():
    sample.install_entries(sample.sample_entries())
    assert ElscioneSource().get_record("../not-a-file.epub") is None


def test_column_config_has_no_language_column():
    columns = ElscioneSource().get_column_config().columns
    assert [column.key for column in columns] == ["format", "size"]
    config = ElscioneSource().get_column_config()
    assert config.supported_filters == ["format"]
    assert config.grid_template == "minmax(0,2fr) 80px 80px"
