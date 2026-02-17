import pytest

from shelfmark.core.release_matcher import score_release_match
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release


@pytest.fixture(autouse=True)
def _use_default_scoring_settings(monkeypatch):
    import shelfmark.core.release_matcher as release_matcher

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


def _release(title: str, extra: dict | None = None) -> Release:
    return Release(
        source="prowlarr",
        source_id=title,
        title=title,
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
    import shelfmark.core.release_matcher as release_matcher

    def permissive_config(key, default=None, user_id=None):
        if key in {"RELEASE_MATCH_MIN_TITLE_SCORE", "RELEASE_MATCH_MIN_AUTHOR_SCORE"}:
            return 0
        return default

    monkeypatch.setattr(release_matcher.app_config, "get", permissive_config)

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
