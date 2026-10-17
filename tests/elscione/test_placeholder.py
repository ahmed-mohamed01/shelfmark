"""Tests for the generated Bauhaus placeholder covers (spec 5.4)."""

import re
import urllib.parse

from shelfmark.release_sources.elscione import placeholder

_PREFIX = "data:image/svg+xml;utf8,"


def _svg(cover: str) -> str:
    """Decode a cover data URL back to its SVG document."""
    assert cover.startswith(_PREFIX)
    return urllib.parse.unquote(cover[len(_PREFIX) :])


def _background(cover: str) -> str:
    """The cover's ground colour, so two covers can be compared as a set."""
    match = re.search(r'<rect width="400" height="600" fill="([^"]+)"', _svg(cover))
    assert match
    return match.group(1)


def _volume(cover: str) -> str:
    """The numeral a cover draws (the end-anchored text is the volume)."""
    match = re.search(r'text-anchor="end"[^>]*>([^<]*)</text>', _svg(cover))
    assert match
    return match.group(1)


def test_fnv1a_matches_bookorbit_vectors():
    # Computed with node from BookOrbit's hash() over the same strings; the
    # last one mixes astral characters, which hash as UTF-16 surrogate pairs.
    assert placeholder.fnv1a("series:spice and wolf") == 3950657011
    assert (
        placeholder.fnv1a("spice and wolf|1|Spice and Wolf/Spice and Wolf - Volume 01.epub")
        == 1972832230
    )
    assert placeholder.fnv1a("series:7th time loop") == 1845650362
    assert placeholder.fnv1a("spice and wolf|1-17|Spice and Wolf/omnibus.epub") == 2704890743
    assert placeholder.fnv1a("title:a🎉x b𝄞c") == 2731995618


def test_mulberry32_matches_bookorbit_vectors():
    # The exact float sequence BookOrbit's rng() yields for these seeds.
    r1 = placeholder.mulberry32(3950657011)
    assert [r1(), r1(), r1()] == [0.4641873000655323, 0.5740272705443203, 0.7769747283309698]
    r2 = placeholder.mulberry32(1972832230)
    assert [r2(), r2(), r2()] == [0.7518614560831338, 0.5666089756414294, 0.20680709858424962]
    r5 = placeholder.mulberry32(2731995618)
    assert [r5(), r5(), r5()] == [0.9555300530046225, 0.3876969497650862, 0.49683084711432457]


def test_same_series_shares_its_ground_across_volumes():
    # The palette is keyed by the series, so its volumes read as a set...
    v1 = placeholder.placeholder_cover(
        "Spice and Wolf",
        "Spice and Wolf",
        (1.0, 1.0),
        "Spice and Wolf/Spice and Wolf - Volume 01.epub",
    )
    v2 = placeholder.placeholder_cover(
        "Spice and Wolf",
        "Spice and Wolf",
        (2.0, 2.0),
        "Spice and Wolf/Spice and Wolf - Volume 02.epub",
    )
    assert _background(v1) == _background(v2)
    # ...while the item seed (volume text plus path) keeps them distinct.
    assert _volume(v1) == "1"
    assert _volume(v2) == "2"


def test_volume_text_drops_leading_zeros_and_joins_ranges():
    # "Volume 03" parses to 3.0, "01-17" to a range, 1.5 stays decimal.
    assert (
        _volume(placeholder.placeholder_cover("S", "S", (3.0, 3.0), "S/S - Volume 03.epub")) == "3"
    )
    assert (
        _volume(placeholder.placeholder_cover("S", "S", (1.0, 17.0), "S/S - Volume 01-17.epub"))
        == "1-17"
    )
    assert (
        _volume(placeholder.placeholder_cover("S", "S", (1.5, 1.5), "S/S - Volume 1.5.epub"))
        == "1.5"
    )


def test_volume_formatting_never_raises():
    # Index parsing treats a non-finite number as no volume, but the formatter
    # runs on whatever floats reach it and must not abort the mapping.
    assert placeholder._num(float("inf")) == "inf"
    assert placeholder._num(float("-inf")) == "-inf"
    assert placeholder._num(float("nan")) == "nan"
    cover = placeholder.placeholder_cover("S", "S", (float("inf"), float("inf")), "S/S - Vol.epub")
    assert cover.startswith("data:image/svg+xml;utf8,")
    assert _volume(cover) == "inf"


def test_deterministic_output():
    args = (
        "Spice and Wolf",
        "Spice and Wolf",
        (1.0, 1.0),
        "Spice and Wolf/Spice and Wolf - Volume 01.epub",
    )
    placeholder.placeholder_cover.cache_clear()
    first = placeholder.placeholder_cover(*args)
    placeholder.placeholder_cover.cache_clear()
    second = placeholder.placeholder_cover(*args)
    assert first == second


def test_xml_escape():
    assert placeholder._xml_escape('<&>"') == "&lt;&amp;&gt;&quot;"


def test_data_url_prefix_and_size_cap():
    covers = [
        placeholder.placeholder_cover(
            "Spice and Wolf", "Spice and Wolf", (1.0, 1.0), "Spice and Wolf/a.epub"
        ),
        placeholder.placeholder_cover("S", "S", (1.0, 17.0), "S/S - Volume 01-17.epub"),
        placeholder.placeholder_cover(
            "Amazing Grace", "Amazing Grace", None, "Amazing Grace/complete.epub"
        ),
    ]
    for cover in covers:
        svg = _svg(cover)
        assert svg.startswith("<svg ")
        assert svg.endswith("</svg>")
        assert len(svg) < 3072
        assert len(cover) < 3072


def test_monogram_skips_leading_article():
    assert placeholder._monogram("The Spice and Wolf") == "S"
    assert placeholder._monogram("An Ember in the Ashes") == "E"
    assert placeholder._monogram("A Certain Magical Index") == "C"
    # A lone article is still the only significant word there is.
    assert placeholder._monogram("The") == "T"
    assert placeholder._monogram("7th Time Loop") == "7"


def test_unnumbered_entry_gets_the_sun_motif():
    # No volume text at all: the sun motif and its companion circle, no numeral.
    cover = placeholder.placeholder_cover(
        "Amazing Grace", "Amazing Grace", None, "Amazing Grace/Amazing Grace - Complete.epub"
    )
    svg = _svg(cover)
    assert 'text-anchor="end"' not in svg
    assert svg.count("<circle") == 2


def test_numbered_entry_gets_the_numeral_motif():
    cover = placeholder.placeholder_cover(
        "Spice and Wolf", "Spice and Wolf", (1.0, 1.0), "Spice and Wolf/a.epub"
    )
    svg = _svg(cover)
    assert svg.count("<circle") == 1
    assert 'text-anchor="end"' in svg
    assert 'font-weight="900"' in svg
    assert 'font-weight="800"' in svg  # the monogram


def test_all_three_grounds_are_reachable():
    # BookOrbit's weighted mix (paper 40 / ink 35 / color 25) must be able to
    # answer every ground, so the rows a search maps cover the full style.
    grounds = {
        _background(placeholder.placeholder_cover(s, s, (1.0, 1.0), f"{s}/{s} - Volume 01.epub"))
        for s in ("Spice and Wolf", "7th Time Loop", "Amazing Grace", "Trilogia", "Range Series")
    }
    assert len(grounds) >= 3
