import pytest

from shelfmark.core.monitored_release_scoring import score_release_match
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release


@pytest.fixture(autouse=True)
def _use_default_scoring_settings(monkeypatch):
    import shelfmark.core.monitored_release_scoring as release_matcher

    monkeypatch.setattr(
        release_matcher.app_config,
        "get",
        lambda _key, default=None, user_id=None: default,
    )


def _book_four() -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="book-4",
        title="Dungeon Life 4: An Isekai LitRPG",
        search_title="Dungeon Life 4",
        authors=["Khenal"],
        search_author="Khenal",
        series_name="Dungeon Life",
        series_position=4,
    )


def _release(title: str, extra: dict | None = None, fmt: str | None = None) -> Release:
    return Release(
        source="prowlarr",
        source_id=title,
        title=title,
        format=fmt,
        content_type="ebook",
        extra=extra or {"author": "Khenal"},
    )


def test_book_4_scores_higher_than_book_3_and_book_2_titles():
    book = _book_four()

    score_book4 = score_release_match(book, _release("Dungeon Life 4: An Isekai LitRPG"))
    score_book3 = score_release_match(book, _release("Dungeon Life 3: An Isekai LitRPG"))
    score_book2 = score_release_match(book, _release("Dungeon Life 2: An Isekai LitRPG"))

    assert score_book4.breakdown["series_number"] == 22
    assert score_book3.breakdown["series_number"] == -60
    assert score_book2.breakdown["series_number"] == -75

    assert score_book4.score > score_book3.score > score_book2.score
    assert score_book3.confidence == "none"
    assert score_book2.confidence == "none"
    assert score_book3.hard_reject is True
    assert score_book3.reject_reason == "series_number_mismatch"
    assert score_book2.hard_reject is True
    assert score_book2.reject_reason == "series_number_mismatch"


def test_series_number_mismatch_hard_rejects_even_with_high_title_author():
    """Primal Hunter 15 should never match Primal Hunter 10, even with perfect
    author and high title score (Hardcover often uses just the series name as the
    title, so the title-containment path gives +60)."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="primal-15",
        title="The Primal Hunter",
        authors=["Zogarth"],
        search_author="Zogarth",
        series_name="The Primal Hunter",
        series_position=15,
    )

    for wrong_num in [10, 9, 5]:
        score = score_release_match(
            book,
            Release(
                source="aa",
                source_id=f"ph{wrong_num}",
                title=f"The Primal Hunter {wrong_num}: A LitRPG Adventure",
                content_type="ebook",
                extra={"author": "Zogarth"},
            ),
        )
        assert score.hard_reject is True, f"Primal Hunter {wrong_num} was not rejected"
        assert score.reject_reason == "series_number_mismatch"

    # Correct number should NOT be rejected
    correct = score_release_match(
        book,
        Release(
            source="aa",
            source_id="ph15",
            title="The Primal Hunter 15: A LitRPG Adventure",
            content_type="ebook",
            extra={"author": "Zogarth"},
        ),
    )
    assert correct.hard_reject is False
    assert correct.breakdown["series_number"] == 22


def test_no_number_release_rejected_for_later_series_position():
    """'The Primal Hunter' (no number) is almost certainly book 1, not book 15."""
    book15 = BookMetadata(
        provider="hardcover",
        provider_id="primal-15",
        title="The Primal Hunter",
        authors=["Zogarth"],
        search_author="Zogarth",
        series_name="The Primal Hunter",
        series_position=15,
    )

    no_num = score_release_match(
        book15,
        Release(
            source="prowlarr",
            source_id="ph-nonum",
            title="The Primal Hunter",
            content_type="audiobook",
            extra={"author": "Zogarth"},
        ),
    )
    assert no_num.hard_reject is True
    assert no_num.reject_reason == "series_number_missing"

    # For book 1, a no-number release IS correct
    book1 = BookMetadata(
        provider="hardcover",
        provider_id="primal-1",
        title="The Primal Hunter",
        authors=["Zogarth"],
        search_author="Zogarth",
        series_name="The Primal Hunter",
        series_position=1,
    )

    no_num_book1 = score_release_match(
        book1,
        Release(
            source="prowlarr",
            source_id="ph-nonum",
            title="The Primal Hunter",
            content_type="audiobook",
            extra={"author": "Zogarth"},
        ),
    )
    assert no_num_book1.hard_reject is False


def test_torznab_seriesnumber_is_used_when_title_lacks_number():
    book = _book_four()

    score_from_torznab_good = score_release_match(
        book,
        _release(
            "Dungeon Life: An Isekai LitRPG",
            {
                "author": "Khenal",
                "torznab_attrs": {
                    "series": "Dungeon Life",
                    "seriesnumber": "4",
                },
            },
        ),
    )

    score_from_torznab_bad = score_release_match(
        book,
        _release(
            "Dungeon Life: An Isekai LitRPG",
            {
                "author": "Khenal",
                "torznab_attrs": {
                    "series": "Dungeon Life",
                    "seriesnumber": "2",
                },
            },
        ),
    )

    assert score_from_torznab_good.breakdown["series_number"] == 22
    assert score_from_torznab_bad.breakdown["series_number"] == -75
    assert score_from_torznab_good.score > score_from_torznab_bad.score


def test_year_mismatch_penalty_is_minus_fifteen_when_year_is_used():
    book = BookMetadata(
        provider="hardcover",
        provider_id="book-year",
        title="Dungeon Life 4: An Isekai LitRPG",
        search_title="Dungeon Life 4",
        authors=["Khenal"],
        search_author="Khenal",
        series_name="Dungeon Life",
        series_position=4,
        publish_year=2026,
    )

    score = score_release_match(
        book,
        _release(
            "Dungeon Life 4: An Isekai LitRPG",
            {
                "author": "Khenal",
                "year": "2019",
            },
        ),
    )

    assert score.breakdown["year"] == -15


def test_low_information_title_without_distinctive_overlap_is_rejected(monkeypatch):
    import shelfmark.core.monitored_release_scoring as release_scorer

    def permissive_config(key, default=None, user_id=None):
        if key in {"RELEASE_MATCH_MIN_TITLE_SCORE", "RELEASE_MATCH_MIN_AUTHOR_SCORE"}:
            return 0
        return default

    monkeypatch.setattr(release_scorer, "_scoring_config_cache", None)
    monkeypatch.setattr(release_scorer.app_config, "get", permissive_config)

    book = BookMetadata(
        provider="hardcover",
        provider_id="azarinth-6",
        title="Azarinth Healer: Book Six",
        search_title="Book Six",
        authors=["Rhaegar"],
        search_author="Rhaegar",
    )

    score = score_release_match(
        book,
        _release(
            "Guild War (Pantheon Online Book 3): a LitRPG adventure",
            {
                "author": "S A Klopfenstein",
            },
        ),
    )

    assert score.hard_reject is True
    assert score.reject_reason == "low_information_title_match"


def test_score_can_exceed_100_and_format_priority_still_applies(monkeypatch):
    import shelfmark.core.monitored_release_scoring as release_scorer

    def configured_get(key, default=None, user_id=None):
        if key == "EBOOK_FORMAT_PRIORITY":
            return [
                {"id": "epub", "enabled": True},
                {"id": "pdf", "enabled": True},
            ]
        return default

    monkeypatch.setattr(release_scorer, "_scoring_config_cache", None)
    monkeypatch.setattr(release_scorer.app_config, "get", configured_get)

    book = _book_four()

    score_epub = score_release_match(
        book,
        _release("Dungeon Life 4: An Isekai LitRPG", fmt="epub"),
    )
    score_pdf = score_release_match(
        book,
        _release("Dungeon Life 4: An Isekai LitRPG", fmt="pdf"),
    )

    assert score_epub.score > 100
    assert score_pdf.score > 100
    assert score_epub.breakdown["format_priority"] > score_pdf.breakdown["format_priority"]
    assert score_epub.score > score_pdf.score


# ---------------------------------------------------------------------------
# Segment-based matching tests
# ---------------------------------------------------------------------------


def test_author_in_title_rescues_from_hard_reject():
    """Release has author in title but _extract_release_author grabs the wrong part."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="unwelcome",
        title="An Unwelcome Quest",
        authors=["Scott Meyer"],
        search_author="Scott Meyer",
        series_name="Magic 2.0",
        series_position=3,
    )

    score = score_release_match(
        book,
        Release(
            source="prowlarr",
            source_id="jackett-1",
            title="An Unwelcome Quest: Magic 2.0, Book 3 - Scott Meyer [M4B] [64 Kbps]",
            content_type="audiobook",
            extra={},
        ),
    )

    assert score.hard_reject is False
    assert score.breakdown["author"] >= 24
    assert score.score >= 80


def test_short_title_embedded_in_phrase_is_penalized():
    """Single-word title appearing inside a longer phrase should not get full score."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="anarchist",
        title="Anarchist",
        authors=["Alexander Olson"],
        search_author="Alexander Olson",
    )

    score = score_release_match(
        book,
        Release(
            source="prowlarr",
            source_id="jackett-2",
            title="The Art of Not Being Governed: An Anarchist History of Upland Southeast Asia",
            content_type="audiobook",
            extra={},
        ),
    )

    # Title "Anarchist" is embedded in a phrase, not an isolated segment.
    # Score is capped at 24 — well below auto-download threshold (75).
    assert score.breakdown["title"] <= 24
    assert score.score <= 24


def test_short_title_as_isolated_segment_gets_full_score():
    """Single-word title that IS its own segment should get full score."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="anarchist",
        title="Anarchist",
        authors=["Alexander Olson"],
        search_author="Alexander Olson",
    )

    score = score_release_match(
        book,
        Release(
            source="prowlarr",
            source_id="jackett-3",
            title="Anarchist - Alexander Olson [M4B]",
            content_type="audiobook",
            extra={},
        ),
    )

    assert score.breakdown["title"] == 60
    assert score.hard_reject is False
    assert score.score >= 80


def test_multiword_title_phrase_match_unchanged():
    """Multi-word titles matching as a phrase should still get full score."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="hail-mary",
        title="Project Hail Mary",
        authors=["Andy Weir"],
        search_author="Andy Weir",
    )

    score = score_release_match(
        book,
        Release(
            source="prowlarr",
            source_id="test-4",
            title="Project Hail Mary - Andy Weir [MP3]",
            content_type="audiobook",
            extra={},
        ),
    )

    assert score.breakdown["title"] == 60
    assert score.hard_reject is False


def test_distinct_title_not_rejected_for_missing_series_number():
    """Book with title distinct from series name should not require series number."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="deceptions",
        title="Deceptions",
        authors=["Craig Alanson"],
        search_author="Craig Alanson",
        series_name="Ascendant",
        series_position=3,
    )

    score = score_release_match(
        book,
        Release(
            source="direct_download",
            source_id="dd-1",
            title="Deceptions (Ascendant)",
            content_type="ebook",
            extra={"author": "Craig Alanson"},
        ),
    )

    assert score.hard_reject is False
    assert score.reject_reason is None
    assert score.score >= 90


def test_title_containing_series_still_rejected_for_missing_number():
    """Books where the title contains the series name still need series number."""
    book = BookMetadata(
        provider="hardcover",
        provider_id="primal-15",
        title="The Primal Hunter 15",
        search_title="The Primal Hunter 15",
        authors=["Zogarth"],
        search_author="Zogarth",
        series_name="The Primal Hunter",
        series_position=15,
    )

    score = score_release_match(
        book,
        _release("The Primal Hunter", extra={"author": "Zogarth"}),
    )

    assert score.hard_reject is True
    assert score.reject_reason == "series_number_missing"
