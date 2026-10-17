"""Tests for ElscioneSource: availability gating, query building, release mapping."""

import logging
import math
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
    # Covers pass through unproxied unless a test turns the cover cache on itself.
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: False)
    yield
    sample.reset_index_state()


def _plan(manual_query=None, variants=None):
    return types.SimpleNamespace(manual_query=manual_query, title_variants=variants or [])


def _variant(title, author="Some Author"):
    return types.SimpleNamespace(title=title, author=author)


def _book(title="Fallback Title", series_position=None, cover_url=None):
    return types.SimpleNamespace(title=title, series_position=series_position, cover_url=cover_url)


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


def _installed_search(
    query, *, content_type="ebook", max_results=None, values=None, entries=None, book=None
):
    """Search through the source with the sample index installed."""
    sample.install_entries(entries if entries is not None else sample.sample_entries())
    overrides = {**_AVAILABLE}
    if max_results is not None:
        overrides["ELSCIONE_MAX_RESULTS"] = max_results
    overrides.update(values or {})
    book = book if book is not None else _book("ignored")
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
    assert searches == ["Searching Elscione for: 7th Time Loop (volume 1.0 from title, 1 hits)"]


# --- Series position and covers --------------------------------------------------------


def test_series_position_narrows_to_the_requested_volume():
    # The provider moved the volume into the series position ("#1 of 7"), so
    # the title variants carry none: the metadata's number is the only volume
    # the request has.
    plan = _plan(variants=[_variant("7th Time Loop")])
    book = _book("7th Time Loop", series_position=1.0)
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    assert [release.source_id for release in releases] == [
        "7th Time Loop/7th Time Loop - Volume 01 [Seven Seas][Kobo].epub"
    ]


def test_series_position_falls_back_when_no_file_matches_it():
    # The metadata's position 3 may be the site's "Volume 02": a preference,
    # not a hard filter, so every file the series name matched comes back.
    plan = _plan(variants=[_variant("Trilogia")])
    book = _book("Trilogia", series_position=3.0)
    with sample.captured_logs(index.logger, level=logging.INFO) as messages:
        releases = _installed_search(plan, book=book, entries=sample.two_volume_entries())
    assert [release.source_id for release in releases] == [
        "Trilogia/Trilogia - Volume 01.epub",
        "Trilogia/Trilogia - Volume 02.epub",
    ]
    searches = [m for m in messages if m.startswith("Searching Elscione for:")]
    assert searches == [
        "Searching Elscione for: Trilogia (volume 3.0 from series position, unmatched, 2 hits)"
    ]


def test_fractional_series_position_is_ignored():
    # 1.5 is a novella no "Volume NN" file carries, so it narrows nothing.
    plan = _plan(variants=[_variant("Trilogia")])
    book = _book("Trilogia", series_position=1.5)
    releases = _installed_search(plan, book=book, entries=sample.two_volume_entries())
    assert [release.source_id for release in releases] == [
        "Trilogia/Trilogia - Volume 01.epub",
        "Trilogia/Trilogia - Volume 02.epub",
    ]


def test_title_volume_stays_a_hard_filter():
    # A volume parsed from the title is what the metadata says to fetch, so
    # unlike a series position it hides entries that do not cover it.
    releases = _installed_search("Trilogia, Vol. 3", entries=sample.two_volume_entries())
    assert releases == []


def test_preference_fallback_rows_get_generated_covers():
    # The fallback rows do not cover position 3, so none of them is the
    # requested volume and none claims the book's cover; all get generated ones.
    plan = _plan(variants=[_variant("Trilogia")])
    book = _book("Trilogia", series_position=3.0, cover_url="https://covers.example/tri.jpg")
    releases = _installed_search(plan, book=book, entries=sample.two_volume_entries())
    assert releases
    assert all(
        release.extra["preview"].startswith("data:image/svg+xml;utf8,") for release in releases
    )


def test_covering_rows_keep_the_book_cover_while_others_get_generated():
    # A range request: only the omnibus (1-17) covers "Vol. 1-3", so the
    # scalar volumes it merely overlaps get generated covers instead.
    releases = _installed_search(
        "Range Series, Vol. 1-3",
        book=_book(cover_url="https://covers.example/range.jpg"),
        entries=sample.seventh_time_loop_entries(),
    )
    by_id = {release.source_id: release for release in releases}
    assert (
        by_id["Range Series/Range Series - Volume 01-17.epub"].extra["preview"]
        == "https://covers.example/range.jpg"
    )
    others = [release for path, release in by_id.items() if not path.endswith("01-17.epub")]
    assert len(others) == 3
    assert all(
        release.extra["preview"].startswith("data:image/svg+xml;utf8,") for release in others
    )


def test_series_position_none_returns_every_volume():
    plan = _plan(variants=[_variant("7th Time Loop")])
    releases = _installed_search(plan, entries=sample.seventh_time_loop_entries())
    assert len(releases) == 4


def test_manual_query_ignores_series_position():
    # What the user typed is searched as typed: the metadata's position must
    # not narrow it.
    plan = _plan(manual_query="7th Time Loop")
    book = _book(series_position=1.0)
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    assert len(releases) == 4


def test_invalid_series_position_is_ignored():
    for position in (0.0, -1.0, math.inf, math.nan):
        plan = _plan(variants=[_variant("7th Time Loop")])
        book = _book(series_position=position)
        releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
        assert len(releases) == 4


def test_series_position_releases_carry_the_book_cover():
    # The modal's leading cell renders extra.preview; the entry that is the
    # requested volume carries the book's cover.
    plan = _plan(variants=[_variant("7th Time Loop")])
    book = _book("7th Time Loop", series_position=1.0, cover_url="https://covers.example/7th.jpg")
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    assert [release.extra["preview"] for release in releases] == ["https://covers.example/7th.jpg"]


def test_cached_book_cover_uses_a_slash_free_cache_id(monkeypatch):
    # The /api/covers/<id> route cannot match a file path, so the source proxies the
    # cover itself under an id with no slashes; the serializer leaves local URLs alone.
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    plan = _plan(variants=[_variant("7th Time Loop")])
    book = _book("7th Time Loop", series_position=1.0, cover_url="https://covers.example/7th.jpg")
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    preview = releases[0].extra["preview"]
    assert preview.startswith("/api/covers/elscione_")
    cache_id = preview.split("/api/covers/", 1)[1].split("?", 1)[0]
    assert "/" not in cache_id
    assert len(cache_id) == len("elscione_") + 16


def test_cover_cache_id_mixes_the_entry_and_the_cover(monkeypatch):
    # One omnibus entry (Volume 01-17) serves books 1 and 2: keying the cover
    # cache on the entry alone would serve book #1's image to book #2 forever.
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    plan = _plan(variants=[_variant("Omnibus Collection, Vol. 5")])
    entries = sample.seventh_time_loop_entries()
    ids = set()
    for cover_url in ("https://covers.example/book1.jpg", "https://covers.example/book2.jpg"):
        releases = _installed_search(plan, book=_book(cover_url=cover_url), entries=entries)
        assert [release.source_id for release in releases] == [
            "Omnibus Collection/Omnibus Collection - Volume 01-17.epub"
        ]
        preview = releases[0].extra["preview"]
        ids.add(preview.split("/api/covers/", 1)[1].split("?", 1)[0])
    assert len(ids) == 2


def test_absurd_volume_entry_declares_no_volume_and_the_mapping_survives():
    # A 309-digit "volume" parses to inf, which no filter or cover can use:
    # the absurd entry declares no volume (like a "Complete" omnibus), so it
    # drops out of a volume-filtered search while the normal volumes stay,
    # and an unfiltered search still maps its row with a rendered cover.
    entries = sample.absurd_volume_entries()
    assert index.entry_volumes(sample.ABSURD_VOLUME_NAME) is None
    releases = _installed_search("Weird Series, Vol. 1", entries=entries)
    assert [release.source_id for release in releases] == [
        "Weird Series/Weird Series - Volume 01.epub"
    ]
    releases = _installed_search("Weird Series", entries=entries)
    by_id = {release.source_id: release for release in releases}
    assert by_id[sample.ABSURD_VOLUME_PATH].extra["preview"].startswith("data:image/svg+xml;utf8,")


def test_parsed_volume_releases_carry_the_book_cover():
    releases = _installed_search(
        sample.SEVENTH_TIME_LOOP_TITLE,
        book=_book(cover_url="https://covers.example/7th.jpg"),
        entries=sample.seventh_time_loop_entries(),
    )
    assert releases
    assert all(release.extra["preview"] == "https://covers.example/7th.jpg" for release in releases)


def test_no_requested_volume_rows_carry_generated_covers():
    # A book cover cannot be pinned to a volume the request never named, but
    # the grey "No Cover" box is worse: every row gets a generated cover.
    plan = _plan(variants=[_variant("7th Time Loop")])
    book = _book(cover_url="https://covers.example/7th.jpg")
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    assert releases
    assert all(
        release.extra["preview"].startswith("data:image/svg+xml;utf8,") for release in releases
    )


def test_requested_volume_without_a_cover_url_gets_a_generated_cover():
    plan = _plan(variants=[_variant("7th Time Loop")])
    book = _book(series_position=1.0)
    releases = _installed_search(plan, book=book, entries=sample.seventh_time_loop_entries())
    assert [release.source_id for release in releases] == [
        "7th Time Loop/7th Time Loop - Volume 01 [Seven Seas][Kobo].epub"
    ]
    assert releases[0].extra["preview"].startswith("data:image/svg+xml;utf8,")


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
