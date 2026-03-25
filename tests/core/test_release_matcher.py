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
