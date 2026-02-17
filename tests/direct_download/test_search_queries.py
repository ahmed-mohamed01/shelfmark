from shelfmark.metadata_providers import BookMetadata
from shelfmark.core.models import BookInfo
from shelfmark.release_sources.direct_download import DirectDownloadSource
from shelfmark.release_sources.direct_download import _book_info_to_release
from shelfmark.core.search_plan import build_release_search_plan


class TestDirectDownloadSearchQueries:
    def test_uses_search_title_for_english_queries(self, monkeypatch):
        captured: list[str] = []

        def fake_search_books(query: str, filters):
            captured.append(query)
            return []

        import shelfmark.release_sources.direct_download as dd

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, expand_search=True)

        assert "The Final Empire Brandon Sanderson" in captured
        assert "A végső birodalom Brandon Sanderson" in captured
        assert "Mistborn: The Final Empire Brandon Sanderson" not in captured


class TestDirectDownloadReleaseMetadataMapping:
    def test_maps_series_number_from_info_into_release_extra(self):
        book_info = BookInfo(
            id="aa-md5-1",
            title="Dungeon Life 4: An Isekai LitRPG",
            author="Khenal",
            format="epub",
            content="book",
            info={
                "Series": ["Dungeon Life 4"],
                "Year": ["2025"],
            },
            download_urls=["https://example.com/download.epub"],
        )

        release = _book_info_to_release(book_info)

        assert release.extra.get("series_name") == "Dungeon Life"
        assert release.extra.get("series_number") == 4.0
        assert release.extra.get("series_position") == 4.0

    def test_keeps_series_name_when_number_not_present(self):
        book_info = BookInfo(
            id="aa-md5-2",
            title="Dungeon Life: An Isekai LitRPG",
            author="Khenal",
            format="epub",
            content="book",
            info={
                "Series": ["Dungeon Life"],
            },
            download_urls=["https://example.com/download.epub"],
        )

        release = _book_info_to_release(book_info)

        assert release.extra.get("series_name") == "Dungeon Life"
        assert release.extra.get("series_number") is None
