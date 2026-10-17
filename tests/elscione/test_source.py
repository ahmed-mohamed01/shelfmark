"""Tests for ElscioneSource: availability gating, query building, release mapping."""

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


def test_build_queries_manual_query_wins():
    assert _build_queries(_plan(manual_query="  amazing grace "), _book("ignored")) == [
        "amazing grace"
    ]


def test_build_queries_uses_every_variant_title_but_never_author():
    plan = _plan(
        variants=[_variant("Amazing Grace", "Kiito Kiito"), _variant("Vol 1", "Kiito Kiito")]
    )
    queries = _build_queries(plan, _book("x"))
    assert queries == ["Amazing Grace", "Vol 1"]
    assert all("Kiito" not in query for query in queries)


def test_build_queries_dedups_casefolded_variants():
    plan = _plan(variants=[_variant("Amazing Grace", ""), _variant("AMAZING GRACE", "")])
    assert _build_queries(plan, _book("x")) == ["Amazing Grace"]


def test_build_queries_book_title_fallback():
    assert _build_queries(_plan(), _book("Fallback Title")) == ["Fallback Title"]


def test_build_queries_no_queries_at_all():
    assert _build_queries(_plan(), _book("")) == []


# --- Search ------------------------------------------------------------------------


def _installed_search(query, *, content_type="ebook", max_results=None, values=None):
    """Search through the source with the sample index installed."""
    sample.install_entries(sample.sample_entries())
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
