"""Tests for monitored_attribution_v2 — structured file→book attribution."""

from __future__ import annotations

from shelfmark.core.monitored_attribution_v2 import (
    TITLE_CORE_HIGH,
    decompose_path,
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
        assert any(
            v.value == 4.0 and v.weight == "high" and v.source == "explicit_marker" for v in votes
        )

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
        assert any(
            v.value == 12.0 and v.weight == "high" and v.source == "after_series_name"
            for v in votes
        )

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
        # File is Book 4; only candidate is Book 1 — must REJECT.
        # Title-borne position contradiction (explicit "(Book 4)" marker vs
        # book #1) hard-rejects, leaving pick_best_attribution with no
        # surviving candidate of any tier.
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/04. Rise of the Living Forge (Book 4) - Actus (2025).epub",
            books=[
                _book(
                    "Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge"
                )
            ],
            author_name="Actus",
        )
        assert r.book is None
        assert r.match_reason == "v2_no_candidate"

    def test_rise_of_living_forge_book2_audio_rejected_for_book1_and_book4(self):
        # Folder/file labelled Book 2 — neither #1 nor #4 candidate should attach
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/Rise of the Living Forge (Book 2)/Rise of the Living Forge (Book 2) - Actus (2025).m4b",
            books=[
                _book(
                    "Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge"
                ),
                _book(
                    "Rise of the Living Forge (Book 4)", pos=4.0, series="Rise of the Living Forge"
                ),
            ],
            author_name="Actus",
        )
        assert r.book is None

    def test_rise_of_living_forge_correct_attribution(self):
        r = pick_best_attribution(
            path="/lib/Actus/Rise of the Living Forge/01. Rise of the Living Forge (Book 1) - Actus (2024).epub",
            books=[
                _book(
                    "Rise of the Living Forge (Book 1)", pos=1.0, series="Rise of the Living Forge"
                ),
                _book(
                    "Rise of the Living Forge (Book 4)", pos=4.0, series="Rise of the Living Forge"
                ),
            ],
            author_name="Actus",
        )
        assert r.book is not None
        assert r.book["title"] == "Rise of the Living Forge (Book 1)"

    def test_hwfwm_book01_rejected_for_book12_candidate(self):
        r = pick_best_attribution(
            path="/audiobooks/Fiction/Shirtaloon/He Who Fights with Monsters/He Who Fights with Monsters, Book 01.mp3",
            books=[
                _book(
                    "He Who Fights with Monsters 12", pos=12.0, series="He Who Fights with Monsters"
                )
            ],
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
            books=[
                _book(
                    "The Murderbot Diaries #9 (Untitled)", pos=9.0, series="The Murderbot Diaries"
                )
            ],
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

        embedded = EmbeddedMetadata(title="Some Translation", isbn_13="9780441018864")
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

        embedded = EmbeddedMetadata(title="House of Suns", isbn_13="9999999999999")  # wrong ISBN
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
            books=[
                _book(
                    "In the City of Demons",
                    pos=2.0,
                    series="Dungeon Crawler Carl",
                    isbn_13="9780441018864",
                )
            ],
            author_name="Matt Dinniman",
            embedded=embedded,
        )
        assert r.book is not None
        assert r.evidence.hard_reject is False


class TestTierClassification:
    """Three-tier outcome: confirmed / candidate / rejected."""

    def test_tier_confirmed_full_agreement(self):
        # All four signals agree (author + series + position + path title), no
        # contradicting metadata → confirmed.
        r = pick_best_attribution(
            path="/lib/Brandon Sanderson/Mistborn/01. The Final Empire - Brandon Sanderson (2006).epub",
            books=[_book("The Final Empire", pos=1.0, series="Mistborn")],
            author_name="Brandon Sanderson",
        )
        assert r.tier == "confirmed"
        assert r.evidence.tier == "confirmed"
        assert r.evidence.accept is True
        assert r.book is not None

    def test_tier_confirmed_identifier_overrides(self):
        # Identifier match alone confirms — file's filename can be totally
        # wrong; the ISBN/ASIN is a hard identity claim (priority 1).
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(title="Some Translation", isbn_13="9780441018864")
        r = pick_best_attribution(
            path="/lib/random/folder/some_other_title.epub",
            books=[_book("House of Suns", pos=1.0, isbn_13="9780441018864")],
            author_name="Alastair Reynolds",
            embedded=embedded,
        )
        assert r.tier == "confirmed"
        assert r.book is not None

    def test_tier_candidate_strong_title_no_position(self):
        # Strong path-side title + author match, but no series folder and
        # position disagrees (legit cross-source numbering). Falls into
        # Candidate — Children-of-Ruin case.
        r = pick_best_attribution(
            path="/audiobooks/Fiction/Adrian Tchaikovsky/Adrian Tchaikovsky/01. Children of Ruin - Adrian Tchaikovsky (2019)",
            books=[_book("Children of Ruin", pos=2.0, series="Children of Time")],
            author_name="Adrian Tchaikovsky",
        )
        assert r.tier == "candidate"
        assert r.book is not None
        assert r.evidence.accept is False  # Candidate doesn't count toward owned.

    def test_tier_rejected_title_borne_position_explicit_marker(self):
        # File explicitly names Book 2 via "(Book 2)" marker; candidate is at
        # series_position=1. Title-borne position mismatch must hard-reject
        # regardless of other matching signals.
        r = pick_best_attribution(
            path="/lib/Rhaegar/Azarinth Healer/Azarinth Healer (Book 2) - Rhaegar.m4b",
            books=[_book("Azarinth Healer (Book 1)", pos=1.0, series="Azarinth Healer")],
            author_name="Rhaegar",
        )
        assert r.tier == "rejected"
        assert r.book is None
        assert r.evidence.hard_reject is True
        assert r.evidence.hard_reject_reason == "title_borne_position_mismatch"

    def test_leading_num_disagreement_does_not_trigger_title_borne_reject(self):
        # "01." in the folder is the user's local numbering, NOT a title-borne
        # marker. Should still attach (Children-of-Ruin pattern) — leading_num
        # alone must not trigger title_borne_position_mismatch.
        r = pick_best_attribution(
            path="/audiobooks/Adrian Tchaikovsky/01. Children of Ruin/01. Children of Ruin - Adrian Tchaikovsky.m4b",
            books=[_book("Children of Ruin", pos=2.0, series="Children of Time")],
            author_name="Adrian Tchaikovsky",
        )
        assert r.evidence.hard_reject is False
        assert r.tier in ("candidate", "confirmed")
        assert r.book is not None

    def test_low_band_stopword_only_overlap_demoted(self):
        # "The Strength of the Few" vs "The Justice of One" — char-fuzz 0.564
        # is just above LOW threshold, but ONLY because of "The/of/the"
        # stopword overlap. Guard 3a must demote both LOW positives to
        # title_mismatch, triggering the both-sides hard-reject.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(
            title="The Strength of the Few",
            authors=["James Islington"],
        )
        r = pick_best_attribution(
            path="/lib/James Islington/Hierarchy/The Strength of the Few - James Islington (2025).m4b",
            books=[_book("The Justice of One", pos=None, series=None)],
            author_name="James Islington",
            embedded=embedded,
        )
        assert r.tier == "rejected"
        assert r.evidence.hard_reject is True
        assert r.evidence.hard_reject_reason == "title_mismatch_both_sides"

    def test_embedded_position_disagree_strips_series_before_fuzz(self):
        # File path: "Azarinth Healer Book 2", embedded: "Azarinth Healer Book 2",
        # candidate book: "Azarinth Healer (Book 1)" pos=1.
        # Without the symmetric series-strip the raw embedded-vs-book fuzz is
        # ~0.95 → +1.0 carry-the-match. With it, both strip to bare position
        # markers → fuzz drops → embedded_title_mismatch fires → hard reject.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(
            title="Azarinth Healer Book 2",
            authors=["Rhaegar"],
            series_name="Azarinth Healer",
            series_position=2.0,
        )
        r = pick_best_attribution(
            path="/audiobooks/Rhaegar/Azarinth Healer/Azarinth Healer Book 2 - Rhaegar.m4b",
            books=[_book("Azarinth Healer (Book 1)", pos=1.0, series="Azarinth Healer")],
            author_name="Rhaegar",
            embedded=embedded,
        )
        assert r.tier == "rejected"
        assert r.evidence.hard_reject is True
        assert r.evidence.hard_reject_reason in (
            "title_borne_position_mismatch",
            "title_mismatch_both_sides",
        )

    def test_bare_number_inside_alphanumeric_id_not_extracted(self):
        # B0B75MS6F3 contains digits but they're embedded in an alphanumeric
        # ID — the bare-number regex must not emit phantom votes.
        votes = extract_position_signals(
            "Dawnshard: Stormlight Archive [B0B75MS6F3].m4b",
            series_name="The Stormlight Archive",
            is_filename=True,
        )
        bare = [v for v in votes if v.source == "bare_number"]
        assert bare == [], f"unexpected bare-number votes from ASIN string: {bare}"

    def test_review_of_lord_of_the_rings_not_low_match(self):
        # "Review of Lord of the Rings" vs "Lord of the Rings": share content
        # tokens in same order, but different books — content-token equality
        # check must demote the LOW positive to mismatch.
        r = pick_best_attribution(
            path="/lib/J.R.R. Tolkien/Review of Lord of the Rings - J.R.R. Tolkien (2001).epub",
            books=[_book("Lord of the Rings", pos=None, series=None)],
            author_name="J.R.R. Tolkien",
        )
        assert r.tier != "confirmed"

    def test_lord_of_the_rings_vs_rings_of_the_lord_demoted(self):
        # Reverse-order same-content-tokens must not pass the LOW band guard.
        r = pick_best_attribution(
            path="/lib/J.R.R. Tolkien/Rings of the Lord - J.R.R. Tolkien.epub",
            books=[_book("Lord of the Rings", pos=None, series=None)],
            author_name="J.R.R. Tolkien",
        )
        assert r.tier != "confirmed"

    def test_multi_series_position_not_penalized(self):
        # "The Alloy of Law" is Wax & Wayne #1 AND Mistborn Saga #4 AND
        # Cosmere #8. ABS catalogs it as Mistborn #4 — must not be penalised
        # for picking the alternate-series numbering.
        import json as _json

        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "The Alloy of Law",
            "series_name": "Mistborn: Wax & Wayne",
            "series_position": 1.0,
            "all_series": _json.dumps(
                [
                    {"name": "The Mistborn Saga", "position": 4, "count": 10},
                    {"name": "The Cosmere", "position": 8, "count": 34},
                    {"name": "Mistborn: Wax & Wayne", "position": 1, "count": 4},
                ]
            ),
        }
        src = SourceMetadata(
            title="The Alloy of Law",
            author="Brandon Sanderson",
            series_name="Mistborn",
            series_position=4.0,
            source_label="abs",
        )
        r = pick_best_attribution(
            path=None,
            books=[book],
            author_name="Brandon Sanderson",
            embedded=None,
            source_metadata=src,
        )
        assert r.tier == "confirmed"
        assert not any(p["name"].endswith("_position_disagree") for p in r.evidence.penalties)

    def test_multi_series_path_alternate_numbering_not_rejected(self):
        # Filename uses Mistborn Saga numbering ("04") for a book whose
        # primary series_position is 1 (Wax & Wayne). Must not trigger the
        # title-borne position hard-reject because 4 matches an alternate
        # series position.
        import json as _json

        book = {
            "title": "The Alloy of Law",
            "series_name": "Mistborn: Wax & Wayne",
            "series_position": 1.0,
            "all_series": _json.dumps(
                [
                    {"name": "The Mistborn Saga", "position": 4, "count": 10},
                    {"name": "Mistborn: Wax & Wayne", "position": 1, "count": 4},
                ]
            ),
        }
        r = pick_best_attribution(
            path="/audiobooks/Brandon Sanderson/Mistborn/Mistborn 04 - The Alloy of Law - Brandon Sanderson.m4b",
            books=[book],
            author_name="Brandon Sanderson",
        )
        assert r.evidence.hard_reject is False
        assert r.evidence.hard_reject_reason != "title_borne_position_mismatch"

    def test_author_with_initials_stripped_from_filename(self):
        # Filename: "Dennis E. Taylor - Quantum Earth, Book 1 - Outland".
        # The author name has a middle initial with a dot — previously the
        # \\s+ pattern in extract_title_core couldn't span the dot, leaving
        # "Dennis E. Taylor - " in title_core and tanking the fuzz to ~0.47.
        from shelfmark.core.monitored_attribution_v2 import (
            SourceMetadata,
            extract_title_core,
        )

        title_core = extract_title_core(
            "Dennis E. Taylor - Quantum Earth, Book 1 - Outland",
            series_name="Quantum Earth",
            author_name="Dennis E. Taylor",
        )
        assert title_core == "Outland"

        book = {"title": "Outland", "series_position": 1.0, "series_name": "Quantum Earth"}
        src = SourceMetadata(
            title="Outland",
            author="Dennis E. Taylor",
            series_name="Quantum Earth",
            series_position=1.0,
            source_label="abs",
        )
        r = pick_best_attribution(
            path="/audiobooks/Audiobooks - Fiction/DennisETaylor/Quantum Earth/Dennis E. Taylor - Quantum Earth, Book 1 - Outland.m4b",
            books=[book],
            author_name="Dennis E. Taylor",
            source_metadata=src,
        )
        assert r.tier == "confirmed"

    def test_subtitle_after_colon_stripped_for_fuzz(self):
        # Metadata title has a subtitle ("Beware of Chicken 2: A Xianxia
        # Cultivation Novel") that the canonical book title lacks
        # ("Beware of Chicken 2"). Must not trigger title_mismatch.
        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "Beware of Chicken 2",
            "series_position": 2.0,
            "series_name": "Beware of Chicken",
        }
        src = SourceMetadata(
            title="Beware of Chicken 2: A Xianxia Cultivation Novel",
            author="Casualfarmer",
            series_name="Beware of Chicken",
            series_position=2.0,
            source_label="abs",
        )
        r = pick_best_attribution(
            path="/audiobooks/CasualFarmer/Beware of Chicken/Beware of Chicken 2.m4b",
            books=[book],
            author_name="CasualFarmer",
            source_metadata=src,
        )
        assert r.tier == "confirmed"
        assert not any(p["name"].endswith("_title_mismatch") for p in r.evidence.penalties)

    def test_parenthetical_series_info_stripped_for_fuzz(self):
        # Metadata title carries the series + book number in parens
        # ("Critical Mass (Expeditionary Force Book 10)") that Hardcover's
        # canonical title ("Critical Mass") doesn't. Must not penalise.
        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "Critical Mass",
            "series_position": 10.0,
            "series_name": "Expeditionary Force",
        }
        src = SourceMetadata(
            title="Critical Mass (Expeditionary Force Book 10)",
            author="Craig Alanson",
            series_name="Expeditionary Force",
            series_position=10.0,
            source_label="booklore",
        )
        r = pick_best_attribution(
            path="/Library/fiction/Craig Alanson/Expeditionary Force/Critical Mass (Expeditionary Force Book 10) - Craig Alanson (2020).epub",
            books=[book],
            author_name="Craig Alanson",
            source_metadata=src,
        )
        assert r.tier == "confirmed"
        assert not any(p["name"].endswith("_title_mismatch") for p in r.evidence.penalties)

    def test_author_in_title_stripped_across_name_variants(self):
        # The metadata title can carry the author name in any of several
        # surface forms — "First Last", "Last, First", "by Author", etc.
        # Each should strip cleanly so the residual matches the book title.
        from shelfmark.core.monitored_attribution_v2 import _title_core_fuzz

        cases = [
            ("Dennis E. Taylor - Outland", "Outland"),
            ("Taylor, Dennis - Outland", "Outland"),
            ("Taylor, Dennis E. - Outland", "Outland"),
            ("Outland by Dennis E. Taylor", "Outland"),
            ("Outland — Dennis E. Taylor", "Outland"),
        ]
        for raw, canonical in cases:
            fuzz = _title_core_fuzz(raw, canonical, author_name="Dennis E. Taylor")
            assert fuzz >= TITLE_CORE_HIGH, f"{raw!r} vs {canonical!r}: fuzz={fuzz:.2f}"

    def test_fuzz_author_matches_bibliographic_and_initial_forms(self):
        # _fuzz_author must match the canonical author against every common
        # surface form catalogs use: "Last, First", "Last, F.", "F. Last".
        from shelfmark.core.monitored_attribution_v2 import _fuzz_author

        canonical = "Dennis E. Taylor"
        for candidate in (
            "Dennis E. Taylor",
            "DennisETaylor",
            "Taylor, Dennis E.",
            "Taylor, Dennis",
            "D. Taylor",
        ):
            assert _fuzz_author(candidate, canonical) == 1.0, candidate

    def test_filename_trailer_recognises_author_variants(self):
        # The trailer recognizer must handle every common author form used
        # in filenames.
        from shelfmark.core.monitored_attribution_v2 import _author_in_filename_trailer

        canonical = "Dennis E. Taylor"
        for leaf in (
            "Outland - Dennis E. Taylor",
            "Outland - Taylor, Dennis E.",
            "Outland by Dennis E. Taylor",
            "Dennis E. Taylor - Outland",
            "Taylor, Dennis - Outland",
            "Outland - Dennis E. Taylor (2019)",
        ):
            assert _author_in_filename_trailer(leaf, canonical), leaf

    def test_metadata_with_multiple_series_pairs_picks_matching_one(self):
        # ABS commonly returns multi-series strings like
        # "Stormlight Archive #5, Cosmere #19". When the book belongs to
        # both series at different numbering schemes per catalog, the
        # scorer must check every metadata pair against the book's all_series
        # and pick the pair that matches.
        import json as _json

        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "Wind and Truth",
            "series_name": "The Stormlight Archive",
            "series_position": 5.0,
            "all_series": _json.dumps(
                [
                    {"name": "The Stormlight Archive", "position": 5, "count": 10},
                    {"name": "The Cosmere", "position": 33, "count": 34},
                ]
            ),
        }
        src = SourceMetadata(
            title="Wind and Truth",
            author="Brandon Sanderson",
            series_name="Stormlight Archive",
            series_position=5.0,
            all_series_pairs=[("Stormlight Archive", 5.0), ("Cosmere", 19.0)],
            source_label="abs",
        )
        r = pick_best_attribution(
            path=None,
            books=[book],
            author_name="Brandon Sanderson",
            source_metadata=src,
        )
        assert r.tier == "confirmed"
        assert not any(p["name"].endswith("_position_disagree") for p in r.evidence.penalties)

    def test_title_equals_series_name_does_not_mismatch_on_position_disagree(self):
        # Warbreaker: title == series_name. When metadata's position
        # disagrees with book's position, the symmetric series-strip
        # leaves both sides empty — but the original titles ARE equal,
        # so this must be a match (title_agree), not a mismatch.
        import json as _json

        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "Warbreaker",
            "series_name": "Warbreaker",
            "series_position": 1.0,
            "all_series": _json.dumps(
                [
                    {"name": "Warbreaker", "position": 1, "count": 2},
                    {"name": "The Cosmere", "position": 6, "count": 34},
                ]
            ),
        }
        src = SourceMetadata(
            title="Warbreaker",
            author="Brandon Sanderson",
            series_name="Cosmere",
            series_position=5.0,
            all_series_pairs=[("Cosmere", 5.0)],
            source_label="abs",
        )
        r = pick_best_attribution(
            path=None,
            books=[book],
            author_name="Brandon Sanderson",
            source_metadata=src,
        )
        assert not any(p["name"].endswith("_title_mismatch") for p in r.evidence.penalties), (
            f"unexpected title_mismatch: {r.evidence.penalties}"
        )
        assert any(p["name"].endswith("_title_agree") for p in r.evidence.positives)

    def test_source_metadata_all_series_pairs_survive_json_roundtrip(self):
        # The Fix-match dialog rebuilds SourceMetadata from evidence_json's
        # source_data. all_series_pairs must round-trip cleanly so multi-
        # series matching benefits interactive re-scoring the same way it
        # benefits the original sync.
        import json as _json

        from shelfmark.core.monitored_attribution_v2 import (
            SourceMetadata,
            _metadata_to_dict,
        )

        pairs = [("Stormlight Archive", 5.0), ("Cosmere", 19.0)]
        serialized = _metadata_to_dict(
            title="Wind and Truth",
            authors=["Brandon Sanderson"],
            series_name="Stormlight Archive",
            series_position=5.0,
            isbn_13=None,
            isbn_10=None,
            asin=None,
            year=None,
            all_series_pairs=pairs,
        )
        roundtripped = _json.loads(_json.dumps(serialized))
        assert "all_series_pairs" in roundtripped
        assert roundtripped["all_series_pairs"] == [["Stormlight Archive", 5.0], ["Cosmere", 19.0]]

        restored = SourceMetadata(
            title=roundtripped["title"],
            series_name=roundtripped["series_name"],
            series_position=roundtripped["series_position"],
            all_series_pairs=[(p[0], float(p[1])) for p in roundtripped["all_series_pairs"]],
        )
        assert restored.all_series_pairs == pairs

    def test_brackets_stripped_from_title_core(self):
        # Catalog identifiers (`[B0B75MS6F3]`), Graphicaudio markers
        # (`[GA]`), and edition tags (`[Unabridged]`) must NOT leak into
        # title_core or into the canonical-title forms used for fuzz.
        from shelfmark.core.monitored_attribution_v2 import _canonical_title_forms

        assert (
            extract_title_core(
                "Tress of the Emerald Sea [GA]",
                series_name="The Cosmere",
                author_name="Brandon Sanderson",
            )
            == "Tress of the Emerald Sea"
        )

        assert (
            extract_title_core(
                "Dawnshard: Stormlight Archive [B0B75MS6F3].m4b",
                series_name="The Stormlight Archive",
                author_name="Brandon Sanderson",
            )
            == "Dawnshard: Stormlight Archive"
        )

        # Canonical-title forms must also strip brackets so metadata-side
        # titles with embedded ASIN codes don't tank the fuzz.
        forms = _canonical_title_forms(
            "Critical Mass [B0CHMZX4DK]",
            series_name="Expeditionary Force",
        )
        assert "Critical Mass" in forms, forms

    def test_multi_author_folder_matches_canonical(self):
        # Folder name contains multiple comma-separated authors; the canonical
        # author for the monitored entity is one of them. _fuzz_author must
        # split on commas and treat each token independently.
        from shelfmark.core.monitored_attribution_v2 import _fuzz_author

        # Wheel of Time collaboratively finished by Sanderson.
        folder = "Robert Jordan, Brandon Sanderson"
        assert _fuzz_author(folder, "Brandon Sanderson") == 1.0
        assert _fuzz_author(folder, "Robert Jordan") == 1.0
        # Also ampersand / "and" / "with"
        assert _fuzz_author("Brandon Sanderson & Janci Patterson", "Brandon Sanderson") == 1.0
        assert _fuzz_author("Brandon Sanderson and Janci Patterson", "Brandon Sanderson") == 1.0
        # Unrelated author still returns low fuzz.
        assert _fuzz_author("Some Other Author", "Brandon Sanderson") < 0.5

    def test_series_folder_matches_via_substring_containment(self):
        # Folder uses a fuller / longer form than canonical
        # ("The Mistborn Saga_ The Original Trilogy" vs canonical
        # "The Mistborn Saga"), or filesystem substitutes underscore for
        # colon. Plain SequenceMatcher fuzz drops below threshold here;
        # substring-containment fallback must rescue.
        book = {
            "title": "The Final Empire",
            "series_name": "The Mistborn Saga",
            "series_position": 1.0,
        }
        r = pick_best_attribution(
            path="/books/ebooks/fiction/Brandon Sanderson/The Mistborn Saga_ The Original Trilogy/Mistborn_ The Final Empire - Brandon Sanderson (2006).epub",
            books=[book],
            author_name="Brandon Sanderson",
        )
        # series_folder should fire via the substring rescue.
        assert any(p["name"] == "series_folder" for p in r.evidence.positives), (
            f"series_folder did not match: {r.evidence.positives}"
        )

    def test_tier_confirmed_when_position_disagree_is_cancelled_by_agreement(self):
        # Real case: "The Sins of Our Fathers" novella, series_position=9.5.
        # The filename "The Expanse 9.5 - The Sins of Our Fathers (novella) -
        # 03 Part Two.mp3" extracts MULTIPLE position votes:
        #   * explicit_marker  @ 9.5   (high, AGREES)
        #   * after_series_name @ 9.5  (high, AGREES)
        #   * word_number_marker @ 2  ("Part **Two**") (high, DISAGREES)
        # The wrong vote raises the raw `position_disagree_high` flag but
        # cancels against the agreement votes -- no `_position_disagree`
        # penalty is emitted. The tier classifier must rely on penalty
        # presence (the actual scored signal), not the raw flag. Otherwise
        # an obviously-correct match falls to candidate.
        book = {
            "title": "The Sins of Our Fathers",
            "series_name": "The Expanse",
            "series_position": 9.5,
        }
        r = pick_best_attribution(
            path=(
                "/books/audiobooks/fiction/James S A Correy/The Expanse/"
                "The Sins of Our Fathers (The Expanse #9.5)/"
                "Corey, James S. A. - The Expanse 9.5 - "
                "The Sins of Our Fathers (novella) - 03 Part Two.mp3"
            ),
            books=[book],
            author_name="James S. A. Corey",
        )
        # Sanity: the wrong vote IS extracted (confirms the test reproduces
        # the original conditions), but the agreement votes also fire and
        # cancel it -- so no penalty is emitted.
        position_votes = {round(v["value"], 4) for v in r.evidence.position_votes}
        assert 2.0 in position_votes, (
            f"expected stray vote at 2.0 from 'Part Two': {position_votes}"
        )
        assert 9.5 in position_votes, f"expected correct vote at 9.5: {position_votes}"
        position_disagree_penalty = [
            p for p in r.evidence.penalties if p["name"].endswith("_position_disagree")
        ]
        assert not position_disagree_penalty, (
            f"penalty must not emit when agreement cancels: {position_disagree_penalty}"
        )
        # Real assertion: this should confirm cleanly.
        assert r.tier == "confirmed", (
            f"expected confirmed, got {r.tier} "
            f"(net_score={r.evidence.net_score:.2f}, "
            f"position_agree_high={r.evidence.position_agree_high}, "
            f"position_disagree_high={r.evidence.position_disagree_high})"
        )
        assert r.book is not None
        assert r.evidence.accept is True

    def test_position_baked_into_title_does_not_match_different_position(self):
        # Real case: He Who Fights with Monsters. Hardcover stores book titles
        # with the position number baked into the title itself
        # ("He Who Fights with Monsters 2: A LitRPG Adventure"). The file tag's
        # title is the same shape ("He Who Fights with Monsters 12: A LitRPG
        # Adventure"). After the existing series-name strip, residues are
        # "2: A LitRPG Adventure" vs "12: A LitRPG Adventure" — char fuzz=0.95,
        # which previously emitted source_title_agree (+1.00) even though
        # the books are unambiguously different (positions 2 vs 12). The
        # position-marker strip removes the leading "N:" prefix so the
        # residues become identical generic subtitles → both-empty fallback
        # detects the conflict and emits title_mismatch instead.
        from shelfmark.core.monitored_attribution_v2 import SourceMetadata

        book = {
            "title": "He Who Fights with Monsters 2: A LitRPG Adventure",
            "series_name": "He Who Fights with Monsters",
            "series_position": 2.0,
        }
        # File tags for book 12, masquerading as monitored book 2.
        source_meta = SourceMetadata(
            title="He Who Fights with Monsters 12: A LitRPG Adventure",
            author="Shirtaloon",
            series_name="He Who Fights with Monsters 12: A LitRPG Adventure",
            series_position=12.0,
            source_label="source_filetag",
        )
        r = pick_best_attribution(
            path="/books/audiobooks/fiction/Shirtaloon/He Who Fights with Monsters/He Who Fights with Monsters 12/He Who Fights with Monsters 12 - Shirtaloon (2025).m4b",
            books=[book],
            author_name="Shirtaloon",
            source_metadata=source_meta,
        )
        # The metadata-side title MUST NOT register as agreeing — the books
        # are different positions and the title overlap is only the series
        # name + a different position number.
        title_agree_positives = [
            p
            for p in r.evidence.positives
            if p["name"].endswith("_title_agree") or p["name"].endswith("_title_agree_med")
        ]
        assert not title_agree_positives, (
            f"file-tag title for book 12 must not register as agreeing with book 2: "
            f"{title_agree_positives}"
        )
        # Should land as candidate or rejected — not confirmed.
        assert r.tier != "confirmed", (
            f"book 12 attached to book 2 monitored entity as confirmed: "
            f"net_score={r.evidence.net_score:.2f}, "
            f"positives={[p['name'] for p in r.evidence.positives]}"
        )

    def test_title_borne_position_falls_back_to_book_title_when_series_position_missing(self):
        # Real case: Hardcover stored the He Who Fights with Monsters entries
        # with the position baked into the title ("He Who Fights with Monsters,
        # Book 2") but didn't populate series_position. Without a fallback,
        # `_all_book_positions` returned [], the position-scoring block was
        # skipped entirely, and the Layer 2 title-borne reject couldn't fire.
        # Result: a Book 9 audiobook attached to a Book 2 monitored entity as
        # a 53% candidate. The fallback parses the position out of the title
        # using high-confidence sources only.
        book = {
            "title": "He Who Fights with Monsters, Book 2",
            "series_name": "He Who Fights with Monsters",
            "series_position": None,  # Hardcover didn't populate it
        }
        r = pick_best_attribution(
            path="/books/audiobooks/fiction/Shirtaloon/He Who Fights with Monsters/He Who Fights with Monsters, Book 09/He Who Fights with Monsters, Book 09.m4b",
            books=[book],
            author_name="Shirtaloon",
        )
        # File explicitly says Book 9 via "Book 09" — explicit_marker high.
        # Book title says Book 2 (parsed via fallback) — also explicit_marker high.
        # Layer 2 must hard-reject (title-borne position mismatch).
        assert r.evidence.hard_reject is True, (
            f"expected hard reject, got tier={r.tier}, "
            f"hard_reject={r.evidence.hard_reject}, "
            f"reason={r.evidence.hard_reject_reason}"
        )
        assert r.evidence.hard_reject_reason == "title_borne_position_mismatch"
        assert r.tier == "rejected"
        assert r.book is None

    def test_book_title_position_fallback_uses_only_high_confidence_sources(self):
        # Sanity: the fallback must NOT extract noise like bare digits inside
        # subtitles or years. Only explicit Book/Vol markers, after-series-name
        # tokens, word-number-markers, and roman numerals are trusted.
        from shelfmark.core.monitored_attribution_v2 import _all_book_positions

        # Position correctly parsed via explicit_marker:
        assert _all_book_positions(
            {
                "title": "He Who Fights with Monsters, Book 2",
                "series_name": "He Who Fights with Monsters",
            }
        ) == [2.0]
        # Position correctly parsed via #N marker:
        assert _all_book_positions(
            {"title": "Mistborn: Wax & Wayne #1", "series_name": "Mistborn: Wax & Wayne"}
        ) == [1.0]
        # No-position titles must NOT yield phantom positions:
        assert (
            _all_book_positions({"title": "Children of Time", "series_name": "Children of Time"})
            == []
        )
        assert (
            _all_book_positions(
                {"title": "The Alloy of Law", "series_name": "Mistborn: Wax & Wayne"}
            )
            == []
        )
        # Year in title must not be extracted as a position:
        assert _all_book_positions({"title": "Tor.com 2024 Best of"}) == []
        # Fallback ONLY runs when structured fields are empty. Explicit
        # series_position wins:
        assert _all_book_positions({"title": "Foo, Book 2", "series_position": 7.0}) == [7.0]

    def test_companion_book_does_not_match_untitled_series_placeholder(self):
        # Real case: monitored entity is Hardcover's placeholder "Untitled
        # Stormlight Archive #10" (unreleased book). A companion guidebook
        # in the same series ("The Stormlight Archive: A Pocket Companion
        # to The Way of Kings and Words of Radiance") was incorrectly
        # surfaced as a candidate at 53%.
        #
        # Root cause: `_canonical_title_forms` Channel 2 splits the meta
        # title on ":" and emits the before-colon part as a canonical form,
        # producing "The Stormlight Archive" -- which is just the series
        # name. That variant then matched against book variants (also
        # containing "Stormlight Archive") at high fuzz. The series name
        # alone can't distinguish books WITHIN the series, so such forms
        # are now filtered out.
        book = {
            "title": "Untitled Stormlight Archive #10",
            "series_name": "The Stormlight Archive",
            "series_position": 10.0,
        }
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        embedded = EmbeddedMetadata(
            title="The Stormlight Archive: A Pocket Companion to The Way of Kings and Words of Radiance",
            authors=["Brandon Sanderson"],
            isbn_13="9780765393043",
        )
        r = pick_best_attribution(
            path="/books/ebooks/fiction/Brandon Sanderson/The Stormlight Archive/The Stormlight Archive_ A Pocket Companion to The Way of Kings and Words of Radiance/The Stormlight Archive_ A Pocket Companion to The Way of Kings and Words of Radiance - Brandon Sanderson.epub",
            books=[book],
            author_name="Brandon Sanderson",
            embedded=embedded,
        )
        # No title-agree positive should fire on either channel -- the
        # apparent overlap was pure series-name overlap.
        title_agree_positives = [
            p
            for p in r.evidence.positives
            if p["name"].endswith("_title_agree") or p["name"].endswith("_title_agree_med")
        ]
        assert not title_agree_positives, (
            f"title-agree must not fire from series-name overlap: {title_agree_positives}"
        )
        assert r.tier != "confirmed", (
            f"companion book attached to unreleased #10 entity: "
            f"tier={r.tier}, net_score={r.evidence.net_score:.2f}"
        )

    def test_canonical_title_forms_skips_series_name_only_variants(self):
        # Unit-level coverage for the Channel 2 filter: when subtitle-strip
        # yields just the series name AND the subtitle carries real
        # distinguishing content, that variant must be dropped.
        from shelfmark.core.monitored_attribution_v2 import _canonical_title_forms

        forms = _canonical_title_forms(
            "The Stormlight Archive: A Pocket Companion to The Way of Kings",
            series_name="The Stormlight Archive",
        )
        # Original is always kept (out[0]); the bare series-name variant
        # produced by Channel 2 must NOT be in the list.
        from shelfmark.core.monitored_attribution_v2 import _norm

        series_norm = _norm("The Stormlight Archive")
        bare_series_variants = [f for f in forms if _norm(f) == series_norm]
        assert not bare_series_variants, (
            f"series-name-only canonical forms must be filtered: {bare_series_variants}"
        )

    def test_subtitle_difference_with_position_disagreement_does_not_mismatch(self):
        # Real production case: Arcanum Unbounded.
        #   Hardcover: "Arcanum Unbounded: The Cosmere Collection"
        #              series_name="The Cosmere", series_position=18.0
        #   ABS:       title="Arcanum Unbounded" (no subtitle)
        #              series_name="Cosmere", series_position=8.0
        # Same book, different cross-catalog metadata. The HWFWM guard
        # (when meta_will_disagree AND stripped-residue fuzz is high)
        # was firing source_abs_title_mismatch even though the originals
        # differ by a real descriptive subtitle, not by a position digit.
        # The fix tightens the guard to raw fuzz >= 0.90 — HWFWM's
        # "...12: A LitRPG Adventure" vs "...2: A LitRPG Adventure"
        # has raw fuzz ~0.99 (passes); Arcanum's raw fuzz is ~0.59
        # (correctly excluded).
        from shelfmark.core.monitored_attribution_v2 import (
            SourceMetadata,
            evaluate_match,
        )

        book = {
            "title": "Arcanum Unbounded: The Cosmere Collection",
            "series_name": "The Cosmere",
            "series_position": 18.0,
        }
        abs_src = SourceMetadata(
            title="Arcanum Unbounded",
            author="Brandon Sanderson",
            series_name="Cosmere",
            series_position=8.0,
            asin="B01LX6S0XM",
            source_label="abs",
        )
        ev = evaluate_match(
            path="/audiobooks/Audiobooks - Fiction/Brandon Sanderson/Arcanum Unbounded",
            book=book,
            author_name="Brandon Sanderson",
            source_metadata=abs_src,
        )
        # The metadata-side title MUST register as agreeing, not as
        # mismatching. The originals differ by a descriptive subtitle,
        # not by a conflicting position number.
        title_mismatch = [p for p in ev.penalties if p["name"].endswith("_title_mismatch")]
        assert not title_mismatch, (
            f"subtitle-only difference must not produce title mismatch: {title_mismatch}"
        )
        title_agree = [p for p in ev.positives if p["name"].endswith("_title_agree")]
        assert title_agree, (
            f"meta-side title_agree should fire when originals share the core title: "
            f"positives={[p['name'] for p in ev.positives]}"
        )

    def test_book_asins_json_list_of_dicts_matches_embedded_asin(self):
        # Real production case: Hardcover stores asins/isbns as a JSON
        # list-of-dicts on the monitored_books row:
        #   asins:  [{"asin": "B09MV3G8PG"}, ...]
        #   isbns:  [{"isbn_13": "9..."}, ...]
        # The DB column type is TEXT so the row dict gets the raw JSON
        # string. Prior _parse_book_identifiers only handled legacy CSV
        # strings; the JSON form yielded an empty set, so the embedded
        # ASIN/ISBN in the EPUB never "matched" the book — instead the
        # OTHER branch fired (`book_isbns or book_asins` non-empty via
        # the isbn_13 scalar column) and the embedded_identifier_mismatch
        # hard-reject killed the correct match.
        #
        # Symptom on the user's server: a Book 1 EPUB file was
        # hard-rejected for Book 1 (its actual book), then fell through
        # to Book 16 (the highest-numbered unreleased placeholder with
        # no stored identifiers, so it wasn't hard-rejected) as a
        # candidate. UI showed the Book 1 file polluting Book 16's
        # candidate list.
        from shelfmark.core.monitored_attribution_v2 import (
            EmbeddedMetadata,
            _parse_book_identifiers,
            evaluate_match,
        )

        book = {
            "title": "The Primal Hunter",
            "series_name": "The Primal Hunter",
            "series_position": 1.0,
            # Note: the scalar isbn_13 is one edition's ISBN;
            # the asins/isbns JSON lists carry the actual file's IDs.
            "isbn_13": "9788426232427",
            "isbn_10": "8426232426",
            "asins": '[{"asin": "B09MV3G8PG"}, {"asin": "B09MWNZ94S"}]',
            "isbns": '[{"isbn_13": "9798835275045"}, {"isbn_13": "9788426232427"}]',
        }

        # Unit: identifier parsing must recover the ASIN list.
        isbns, asins = _parse_book_identifiers(book)
        assert "B09MV3G8PG" in asins, f"expected B09MV3G8PG in parsed asins, got {asins}"
        assert "B09MWNZ94S" in asins, f"expected B09MWNZ94S in parsed asins, got {asins}"
        assert "9798835275045" in isbns, f"expected ISBN parsed from JSON list, got {isbns}"

        # Integration: file with embedded ASIN matching the book MUST
        # confirm via identifier (not hard-reject).
        embedded = EmbeddedMetadata(
            title="The Primal Hunter",
            authors=["Zogarth"],
            series_name="The Primal Hunter",
            series_position=1.0,
            isbn_13="9798426232426",  # not in the book's isbns list
            asin="B09MV3G8PG",  # IS in the book's asins list
            year=2022,
        )
        ev = evaluate_match(
            path="/books/ebooks/fiction/Zogarth/The Primal Hunter/01. The Primal Hunter - Zogarth (2022).epub",
            book=book,
            author_name="Zogarth",
            embedded=embedded,
        )
        assert not ev.hard_reject, (
            f"file's embedded ASIN matches book's asins[].asin — must NOT hard-reject: "
            f"reason={ev.hard_reject_reason!r}"
        )
        assert ev.tier == "confirmed", (
            f"identifier-match should confirm: tier={ev.tier}, score={ev.net_score:.2f}"
        )
        assert any(p["name"] == "embedded_identifier" for p in ev.positives), (
            f"expected embedded_identifier positive in {[p['name'] for p in ev.positives]}"
        )

    def test_book_identifiers_legacy_csv_format_still_parsed(self):
        # Back-compat: older rows may have asins/isbns as a comma-separated
        # string, not JSON. Must continue to parse.
        from shelfmark.core.monitored_attribution_v2 import _parse_book_identifiers

        book = {
            "asins": "B09MV3G8PG, B09MWNZ94S",
            "isbns": "9798835275045 9788426232427",
        }
        isbns, asins = _parse_book_identifiers(book)
        assert asins == {"B09MV3G8PG", "B09MWNZ94S"}
        assert isbns == {"9798835275045", "9788426232427"}

    def test_bare_series_name_file_picks_position_one_not_arbitrary_book(self):
        # Real case (server-only manifestation): The Primal Hunter Book 1
        # audiobook is named just "The Primal Hunter - Zogarth (2022).m4b"
        # — no position number. Books 1-16 all exist in monitored_books.
        # Every book passes the title_core_high threshold (Book 1's title
        # fuzzes at 1.00, Book 2 at 0.94, Book 16 at 0.92) and yields the
        # same net_score (3.40). Without a tiebreaker, pick_best_attribution
        # picked whichever book iteration encountered first — Book 1 on
        # localhost (alphabetical), Book 16 on the server (different order).
        #
        # The tiebreaker on title fuzz makes the most-precise title match
        # (Book 1, fuzz=1.00 exact) the principled winner regardless of
        # iteration order.
        books = [
            {
                "id": 1,
                "title": "The Primal Hunter",
                "series_name": "The Primal Hunter",
                "series_position": 1.0,
            },
            {
                "id": 2,
                "title": "The Primal Hunter 2",
                "series_name": "The Primal Hunter",
                "series_position": 2.0,
            },
            {
                "id": 16,
                "title": "The Primal Hunter 16",
                "series_name": "The Primal Hunter",
                "series_position": 16.0,
            },
        ]
        path = "/books/audiobooks/fiction/Zogarth/The Primal Hunter/The Primal Hunter/The Primal Hunter - Zogarth (2022).m4b"
        # Try both iteration orders — winner must be the same.
        for orderlabel, ordered in (("ascending", books), ("reverse", list(reversed(books)))):
            r = pick_best_attribution(path=path, books=ordered, author_name="Zogarth")
            assert r.book is not None, f"no winner ({orderlabel})"
            assert r.book["id"] == 1, (
                f"with books in {orderlabel} order, expected Book 1 (exact title match), "
                f"got book #{r.book['id']} '{r.book['title']}'"
            )

    def test_dash_subtitle_separator_treated_same_as_colon(self):
        # Real case: The Primal Hunter 7 by Zogarth. Hardcover book title
        # is "The Primal Hunter 7"; Booklore metadata writes it as
        # "The Primal Hunter 7: A LitRPG Adventure" (colon) and ABS as
        # "The Primal Hunter 7 - A LitRPG Adventure" (dash). Channel 2
        # previously only split on ":" so the Booklore variant got a
        # bare 'The Primal Hunter 7' canonical form and matched at
        # fuzz=1.0, but the ABS variant did NOT and dropped to fuzz=0.67
        # → source_abs_title_mismatch → tier=candidate.
        from shelfmark.core.monitored_attribution_v2 import (
            SourceMetadata,
            _canonical_title_forms,
            _norm,
        )

        # Unit: ABS dash-form must produce the bare 'The Primal Hunter 7'
        # canonical, same as the colon-form does.
        forms_dash = _canonical_title_forms(
            "The Primal Hunter 7 - A LitRPG Adventure",
            series_name="The Primal Hunter",
            author_name="Zogarth",
        )
        forms_colon = _canonical_title_forms(
            "The Primal Hunter 7: A LitRPG Adventure",
            series_name="The Primal Hunter",
            author_name="Zogarth",
        )
        bare = _norm("The Primal Hunter 7")
        assert any(_norm(f) == bare for f in forms_dash), (
            f"dash subtitle separator must produce bare-before form: {forms_dash}"
        )
        assert any(_norm(f) == bare for f in forms_colon), (
            f"colon subtitle separator must produce bare-before form: {forms_colon}"
        )

        # Integration: the ABS-shaped match must now confirm.
        book = {
            "title": "The Primal Hunter 7",
            "series_name": "The Primal Hunter",
            "series_position": 7.0,
        }
        abs_src = SourceMetadata(
            title="The Primal Hunter 7 - A LitRPG Adventure",
            author="Zogarth",
            series_name="The Primal Hunter",
            series_position=7.0,
            source_label="abs",
        )
        r = pick_best_attribution(
            path="/audiobooks/Audiobooks - Fiction/Zogarth/The Primal Hunter/7 - The Primal Hunter 7 (The Primal Hunter 7)",
            books=[book],
            author_name="Zogarth",
            source_metadata=abs_src,
        )
        assert r.tier == "confirmed", (
            f"ABS-titled book with dash-separator subtitle must confirm: "
            f"tier={r.tier}, confidence={r.evidence.confidence:.2f}"
        )
        # And NO source_abs_title_mismatch penalty should remain.
        title_mismatch = [p for p in r.evidence.penalties if p["name"].endswith("_title_mismatch")]
        assert not title_mismatch, (
            f"dash-separator suffix must not trigger title mismatch: {title_mismatch}"
        )

    def test_standalone_book_with_generic_subtitle_keeps_bare_form(self):
        # Real case: Dominion of Blades by Matt Dinniman. The book is a
        # one-book "series" — book.title == series_name == "Dominion of
        # Blades". The EPUB's embedded title carries a generic genre
        # descriptor subtitle ("Dominion of Blades: A LitRPG Adventure").
        #
        # Without companion-aware suppression, the bare "Dominion of
        # Blades" form from Channel 2 colon-split is dropped (after-content
        # has 2 distinguishing tokens — "litrpg", "adventure"), leaving
        # only the long-form embedded title which fuzzes against the book
        # title at 0.65 → embedded_title_mismatch → tier=candidate.
        #
        # The companion-aware fix: when the OTHER side being compared has
        # at most 1 distinguishing content token (i.e., it's essentially
        # just the series name itself — the standalone case), the bare
        # form is the only sensible match and must be kept.
        from shelfmark.core.monitored_attribution_v2 import EmbeddedMetadata

        book = {
            "title": "Dominion of Blades",
            "series_name": "Dominion of Blades",  # series_name == title (standalone)
            "series_position": 1.0,
        }
        embedded = EmbeddedMetadata(
            title="Dominion of Blades: A LitRPG Adventure",
            authors=["Matt Dinniman"],
            year=2017,
        )
        r = pick_best_attribution(
            path="/books/ebooks/fiction/Matt Dinniman/Dominion of Blades/Dominion of Blades - Matt Dinniman (2017).epub",
            books=[book],
            author_name="Matt Dinniman",
            embedded=embedded,
        )
        assert r.tier == "confirmed", (
            f"standalone-titled book must confirm at fuzz=1.0 via bare form: "
            f"tier={r.tier}, confidence={r.evidence.confidence:.2f}"
        )
        title_mismatch = [p for p in r.evidence.penalties if p["name"].endswith("_title_mismatch")]
        assert not title_mismatch, (
            f"generic genre subtitle on standalone book must not produce "
            f"a title mismatch penalty: {title_mismatch}"
        )

    def test_canonical_title_forms_keeps_bare_series_when_subtitle_is_only_position(self):
        # Inverse of the Pocket Companion case: when the subtitle is just
        # position + series metadata ("Book One, Part One of the Wandering
        # Inn Series"), the bare-series form IS the legitimate short title
        # and must be kept. The pirateaba file
        # "1. The Wandering Inn - Pirateaba.epub" needs to match book
        # "The Wandering Inn: Book One, Part One of the Wandering Inn
        # Series" at fuzz=1.0 via this variant. An over-aggressive filter
        # that drops every series-name-only form regresses this match.
        from shelfmark.core.monitored_attribution_v2 import (
            _canonical_title_forms,
            _norm,
        )

        forms = _canonical_title_forms(
            "The Wandering Inn: Book One, Part One of the Wandering Inn Series",
            series_name="The Wandering Inn",
        )
        series_norm = _norm("The Wandering Inn")
        bare_series_variants = [f for f in forms if _norm(f) == series_norm]
        assert bare_series_variants, (
            f"bare-series form must be kept when subtitle has no distinguishing "
            f"content (only position + series metadata): forms={forms}"
        )

    def test_prequel_position_0_and_half_are_compatible(self):
        # Real case: The Daughters' War. Hardcover stores series_position=0.0,
        # ABS metadata says series_position=0.5. Both conventions mean
        # "prequel novella before Book 1" -- forcing exact equality emits a
        # spurious source_abs_position_disagree penalty that demotes an
        # otherwise unanimous match to candidate. The novella-tolerance rule
        # in _position_matches_book treats any two positions in [0, 1) as
        # compatible.
        from shelfmark.core.monitored_attribution_v2 import SourceMetadata, _position_matches_book

        # Helper-level check.
        assert _position_matches_book(0.5, [0.0]) is True
        assert _position_matches_book(0.0, [0.5]) is True
        # Tolerance is narrow: 1.0 vs 1.5 are different works (novella
        # between books 1 and 2 vs book 1 itself) and must NOT match.
        assert _position_matches_book(1.0, [1.5]) is False
        assert _position_matches_book(1.5, [1.0]) is False
        # Two positions both in the prequel band:
        assert _position_matches_book(0.25, [0.75]) is True
        # End-to-end: Daughters' War scenario through pick_best_attribution.
        book = {
            "title": "The Daughters' War",
            "series_name": "Blacktongue",
            "series_position": 0.0,
        }
        source_meta = SourceMetadata(
            title="The Daughters' War",
            author="Christopher Buehlman",
            series_name="Blacktongue",
            series_position=0.5,
            asin="B0CH1G8NTL",
            source_label="source_abs",
        )
        r = pick_best_attribution(
            path=None,
            books=[book],
            author_name="Christopher Buehlman",
            source_metadata=source_meta,
        )
        # No position-disagree penalty should emit.
        position_disagrees = [
            p for p in r.evidence.penalties if p["name"].endswith("_position_disagree")
        ]
        assert not position_disagrees, (
            f"0.0 vs 0.5 should not emit position-disagree: {position_disagrees}"
        )
        # Should confirm (full title + author + series + tolerated position).
        assert r.tier == "confirmed", (
            f"expected confirmed, got {r.tier} (net_score={r.evidence.net_score:.2f})"
        )
