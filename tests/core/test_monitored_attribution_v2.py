"""Tests for monitored_attribution_v2 — structured file→book attribution."""

from __future__ import annotations

import pytest

from shelfmark.core.monitored_attribution_v2 import (
    decompose_path,
    evaluate_match,
    extract_position_signals,
    extract_title_core,
    pick_best_attribution,
)


# ---------------------------------------------------------------------------
# Path decomposition
# ---------------------------------------------------------------------------


class TestDecomposePath:
    def test_ebook_full_layout(self):
        d = decompose_path(
            "/lib/Author/Series/01. Title - Author (2024).epub",
            author_name="Author",
        )
        assert d.leaf == "01. Title - Author (2024).epub"
        assert d.leaf_is_file is True
        assert d.ext == ".epub"
        assert d.book_folder == "Series"
        assert d.series_folder == "Series"
        assert d.author_folder == "Author"

    def test_audiobook_folder_layout(self):
        d = decompose_path(
            "/audiobooks/Author/Series/Book Title",
            author_name="Author",
        )
        assert d.leaf == "Book Title"
        assert d.leaf_is_file is False
        assert d.book_folder == "Book Title"
        assert d.series_folder == "Series"
        assert d.author_folder == "Author"

    def test_standalone_book_no_series(self):
        d = decompose_path(
            "/lib/Author/The Standalone Book.epub",
            author_name="Author",
        )
        assert d.author_folder == "Author"
        assert d.series_folder is None
        assert d.leaf_is_file is True

    def test_extension_with_dots_in_name(self):
        # "Dennis E. Taylor - Earthside.m4b" — multiple dots; only last is ext.
        d = decompose_path(
            "/lib/Dennis E. Taylor/Quantum Earth/Dennis E. Taylor - Earthside.m4b",
            author_name="Dennis E. Taylor",
        )
        assert d.ext == ".m4b"
        assert d.leaf_is_file is True

    def test_author_anchor_handles_deep_libraries(self):
        # Library may have wrapper dirs before the author folder
        d = decompose_path(
            "/mnt/library/audiobooks/Fiction/Adrian Tchaikovsky/Children of Time/Book 2.m4b",
            author_name="Adrian Tchaikovsky",
        )
        assert d.author_folder == "Adrian Tchaikovsky"
        assert d.series_folder == "Children of Time"

    def test_directory_with_dotted_name_does_not_get_truncated(self):
        # Path with dots in directory name shouldn't slip into "ext"
        d = decompose_path("/lib/Author/Series 1.5/Book.epub", author_name="Author")
        assert d.ext == ".epub"
        assert d.leaf_is_file is True


# ---------------------------------------------------------------------------
# Position signal extraction
# ---------------------------------------------------------------------------


class TestPositionSignals:
    def test_leading_nn_dot(self):
        votes = extract_position_signals("04. Title - Author (2024).epub", is_filename=True)
        values = {v.value for v in votes if v.weight == "high"}
        assert 4.0 in values

    def test_leading_nn_dash(self):
        votes = extract_position_signals("12 - Title.epub", is_filename=True)
        assert any(v.value == 12.0 and v.weight == "high" for v in votes)

    def test_explicit_book_marker(self):
        votes = extract_position_signals("Title (Book 4) - Author.epub", is_filename=True)
        assert any(v.value == 4.0 and v.weight == "high" and v.source == "explicit_marker" for v in votes)

    def test_explicit_vol_marker(self):
        votes = extract_position_signals("Cradle Vol 1.5 - Title.epub", is_filename=True)
        assert any(v.value == 1.5 and v.weight == "high" for v in votes)

    def test_hash_marker(self):
        votes = extract_position_signals("Title #15.epub", is_filename=True)
        assert any(v.value == 15.0 and v.weight == "high" for v in votes)

    def test_after_series_name_high_weight(self):
        votes = extract_position_signals(
            "He Who Fights with Monsters 12.epub",
            series_name="He Who Fights with Monsters",
            is_filename=True,
        )
        # The bare "12" after series name is HIGH weight via after_series_name source.
        assert any(v.value == 12.0 and v.weight == "high" and v.source == "after_series_name" for v in votes)

    def test_year_in_parens_is_ignored(self):
        votes = extract_position_signals("Title - Author (2024).epub", is_filename=True)
        assert not any(v.value == 2024.0 for v in votes)

    def test_year_bare_is_ignored(self):
        votes = extract_position_signals("Some Story 2024.epub", is_filename=True)
        assert not any(v.value == 2024.0 for v in votes)

    def test_long_number_no_partial_match(self):
        # "1099" inside "1099212" must NOT extract — boundary protection
        votes = extract_position_signals("Chapter 1099212.epub", is_filename=True)
        # 1099212 is > 999 (digit cap) so shouldn't extract a 3-digit slice
        assert not any(v.value == 1099.0 for v in votes)

    def test_long_three_digit_series_position(self):
        # Wandering Inn ch 100+ should still extract via bare-number 3-digit
        votes = extract_position_signals("100. The Wandering Inn.epub", is_filename=True)
        assert any(v.value == 100.0 and v.weight == "high" for v in votes)

    def test_decimal_isolated(self):
        votes = extract_position_signals("Cradle 1.5 - Title.epub", is_filename=True)
        assert any(v.value == 1.5 for v in votes)

    def test_decimal_inside_digit_run_skipped(self):
        # "1.2" inside "101.201.filename" should NOT extract as a position
        votes = extract_position_signals("101.201.filename.epub", is_filename=True)
        # Should not produce 1.2 as a vote
        decimal_votes = [v for v in votes if v.weight == "medium" and v.source == "decimal"]
        assert not decimal_votes

    def test_word_number_marker(self):
        votes = extract_position_signals("Book Three.epub", is_filename=True)
        assert any(v.value == 3.0 and v.weight == "high" for v in votes)

    def test_roman_numeral_marker(self):
        votes = extract_position_signals("Book IV.epub", is_filename=True)
        assert any(v.value == 4.0 for v in votes)


# ---------------------------------------------------------------------------
# Title core extraction
# ---------------------------------------------------------------------------


class TestTitleCore:
    def test_strips_year_and_author_and_position(self):
        core = extract_title_core(
            "01. The Way of Kings - Brandon Sanderson (2010).epub",
            series_name="The Stormlight Archive",
            author_name="Brandon Sanderson",
        )
        assert "way of kings" in core.lower()

    def test_strips_book_marker(self):
        core = extract_title_core(
            "Rise of the Living Forge (Book 4) - Actus (2025).epub",
            series_name="Rise of the Living Forge",
            author_name="Actus",
        )
        # series name == title core base, so result should be empty
        assert core.strip() == ""

    def test_keeps_distinct_title(self):
        core = extract_title_core(
            "Chasm City - Alastair Reynolds (2001).epub",
            series_name="Revelation Space",
            author_name="Alastair Reynolds",
        )
        assert "chasm city" in core.lower()

    def test_strips_subtitle_when_series_present(self):
        # "Mistborn: The Final Empire" — title core comparison should still
        # work after stripping series prefix.
        core = extract_title_core(
            "Mistborn The Final Empire - Brandon Sanderson (2006).epub",
            series_name="Mistborn",
            author_name="Brandon Sanderson",
        )
        assert "final empire" in core.lower()


# ---------------------------------------------------------------------------
# evaluate_match / pick_best_attribution — end-to-end attribution decisions
# ---------------------------------------------------------------------------


def _book(title, *, pos=None, series=None, **kw):
    return {"title": title, "series_position": pos, "series_name": series, **kw}


class TestAttributionDecisions:
    """Reproduces each known mis-attribution from the user's screenshots/audit."""

    def test_rise_of_living_forge_book4_rejected_for_book1_candidate(self):
        # File is Book 4; only candidate is Book 1 — must REJECT
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/04. Rise of the Living Forge (Book 4) - Actus (2025).epub",
            books=[_book("Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge")],
            author_name="Actus",
        )
        assert r.book is None
        assert r.match_reason == "v2_below_floor"

    def test_rise_of_living_forge_book2_audio_rejected_for_book1_and_book4(self):
        # Folder/file labelled Book 2 — neither #1 nor #4 candidate should attach
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/Rise of the Living Forge (Book 2)/Rise of the Living Forge (Book 2) - Actus (2025).m4b",
            books=[
                _book("Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge"),
                _book("Rise of the Living Forge (Book 4)", pos=4.0, series="Rise of the Living Forge"),
            ],
            author_name="Actus",
        )
        assert r.book is None

    def test_rise_of_living_forge_correct_attribution(self):
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/01. Rise of the Living Forge (Book 1) - Actus (2024).epub",
            books=[
                _book("Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge"),
                _book("Rise of the Living Forge (Book 4)", pos=4.0, series="Rise of the Living Forge"),
            ],
            author_name="Actus",
        )
        assert r.book is not None
        assert r.book["title"] == "Rise of the Living Forge (Book 1)"

    def test_hwfwm_book01_rejected_for_book12_candidate(self):
        r = pick_best_attribution(
            path="/audiobooks/Fiction/Shirtaloon/He Who Fights with Monsters/He Who Fights with Monsters, Book 01.mp3",
            books=[_book("He Who Fights with Monsters 12", pos=12.0,
                         series="He Who Fights with Monsters")],
            author_name="Shirtaloon",
        )
        assert r.book is None

    def test_chasm_city_rejected_for_revelation_space_1(self):
        r = pick_best_attribution(
            path="/audiobooks/Fiction/Alastair Reynolds/Revelation Space/Revelation Space #2 - Chasm City.m4b",
            books=[_book("Revelation Space", pos=1.0, series="Revelation Space")],
            author_name="Alastair Reynolds",
        )
        assert r.book is None

    def test_murderbot_book2_file_rejected_for_book9_untitled(self):
        r = pick_best_attribution(
            path="/lib/Martha Wells/The Murderbot Diaries Book 1-4/The Murderbot Diaries Book 1-4 - Martha Wells/Murderbot Diaries Book 2 ArtificialCondition.m4b",
            books=[_book("The Murderbot Diaries #9 (Untitled)", pos=9.0,
                         series="The Murderbot Diaries")],
            author_name="Martha Wells",
        )
        assert r.book is None

    def test_legit_cross_source_attaches_at_lower_confidence(self):
        # Children of Ruin is series_position=2 in Hardcover but the user folder
        # is labelled "01." — soft-reject design says: attach at reduced confidence
        # because title agrees strongly.
        r = pick_best_attribution(
            path="/audiobooks/Fiction/Adrian Tchaikovsky/Adrian Tchaikovsky/01. Children of Ruin - Adrian Tchaikovsky (2019)",
            books=[_book("Children of Ruin", pos=2.0, series="Children of Time")],
            author_name="Adrian Tchaikovsky",
        )
        assert r.book is not None
        assert r.book["title"] == "Children of Ruin"
        # Confidence should be visibly lower than a clean match
        assert r.confidence < 0.7

    def test_isbn_identifier_match_overrides_all(self):
        # When embedded ISBN matches the book's ISBN, we attach at high confidence
        # regardless of weaker path signals.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(title="Some Translation",
                                    isbn_13="9780441018864")
        r = pick_best_attribution(
            path="/lib/random/folder/some_other_title.epub",
            books=[_book("House of Suns", pos=1.0, isbn_13="9780441018864")],
            author_name="Alastair Reynolds",
            embedded=embedded,
        )
        assert r.book is not None
        assert r.match_reason == "v2_identifier"

    def test_isbn_contradiction_hard_rejects(self):
        # When embedded ISBN exists AND mismatches the book's ISBN, hard-reject
        # regardless of how strong the path signals are.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(title="House of Suns",
                                    isbn_13="9999999999999")  # wrong ISBN
        r = pick_best_attribution(
            path="/lib/Alastair Reynolds/House of Suns/House of Suns - Alastair Reynolds (2008).epub",
            books=[_book("House of Suns", pos=1.0, isbn_13="9780441018864")],
            author_name="Alastair Reynolds",
            embedded=embedded,
        )
        assert r.book is None
        assert r.evidence.hard_reject is True

    def test_title_disagreement_both_sides_hard_rejects(self):
        # Filename title AND embedded title both strongly disagree with the
        # candidate book — even when other signals (author folder, author
        # trailer, position) align, this is the file unambiguously naming a
        # different book. Reproduces the "In the City of Demons" vs file
        # "Carl's Doomsday Scenario" scenario where book_spos happened to
        # coincide.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(
            title="Carl's Doomsday Scenario: Dungeon Crawler Carl Book 2",
            authors=["Matt Dinniman"],
        )
        r = pick_best_attribution(
            path="/books/ebooks/fiction/Matt Dinniman/Dungeon Crawler Carl/2. Carl's Doomsday Scenario Dungeon Crawler Carl Book 2 - Matt Dinniman (2021).epub",
            books=[_book("In the City of Demons", pos=2.0, series="Dungeon Crawler Carl")],
            author_name="Matt Dinniman",
            embedded=embedded,
        )
        assert r.book is None
        assert r.evidence.hard_reject is True
        assert r.evidence.hard_reject_reason == "title_mismatch_both_sides"

    def test_title_disagreement_rescued_by_identifier(self):
        # Same dual title disagreement as above, but the embedded ISBN matches
        # the candidate book — identifier is a hard identity claim and should
        # override the title-mismatch hard-reject. (Mirrors
        # test_isbn_identifier_match_overrides_all but with both titles wrong.)
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(
            title="Carl's Doomsday Scenario",
            authors=["Matt Dinniman"],
            isbn_13="9780441018864",
        )
        r = pick_best_attribution(
            path="/lib/Matt Dinniman/Dungeon Crawler Carl/Carl's Doomsday Scenario - Matt Dinniman.epub",
            books=[_book("In the City of Demons", pos=2.0,
                         series="Dungeon Crawler Carl", isbn_13="9780441018864")],
            author_name="Matt Dinniman",
            embedded=embedded,
        )
        assert r.book is not None
        assert r.evidence.hard_reject is False
