"""Tests for the AudioBookShelf integration's author resolution.

The hot path here is `_find_abs_author_items`: given a monitored author's
canonical name, return every library item filed under that author in ABS.
ABS commonly stores the same person under multiple author entities when
items were originally added with slightly different name spellings
("James S. A. Corey" vs "James S A Corey" vs "James S.A. Corey"). The
integration must merge items from all of them.
"""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core.monitored_audiobookshelf_integration import (
    _find_abs_author_items,
)


@pytest.fixture
def mock_abs(monkeypatch):
    """Stub `_abs_get` with a configurable in-memory ABS response map.

    The fixture returns a setup function: callers describe the library's
    author list and per-author items, and the fixture wires up `_abs_get`
    to serve from that map.
    """

    def setup(*, authors: list[dict[str, Any]], items_by_author_id: dict[str, list[dict]]):
        # The integration caches library/author lists across a batch (keyed on
        # url/token/library_id). Tests reuse the same key, so clear it to keep
        # each case isolated from the previous one's cached authors.
        from shelfmark.core import monitored_audiobookshelf_integration as _abs_mod

        _abs_mod._abs_list_cache.clear()

        def fake_get(_url: str, _token: str, path: str, timeout: int = 10) -> Any:
            if path.endswith("/authors"):
                return {"authors": authors}
            if path.startswith("/api/authors/"):
                author_id = path.split("/")[3].split("?")[0]
                return {"libraryItems": items_by_author_id.get(author_id, [])}
            raise AssertionError(f"unexpected ABS path: {path}")

        monkeypatch.setattr(
            "shelfmark.core.monitored_audiobookshelf_integration._abs_get",
            fake_get,
        )

    return setup


class TestFindAbsAuthorItems:
    def test_multiple_top_ratio_author_entities_are_all_queried(self, mock_abs):
        # Real case (user's library): ABS has 3 separate author entities for
        # James S. A. Corey at ratio 1.00 — different surface spellings, each
        # holding a subset of the actual catalog. Prior to the merge fix the
        # integration grabbed the first 1.00-ratio entity and silently
        # dropped books filed under the others.
        authors = [
            {"id": "id-stub", "name": "James S.A. Corey", "numBooks": 0},
            {"id": "id-expanse", "name": "James S A Corey", "numBooks": 12},
            {"id": "id-captives", "name": "James S. A. Corey", "numBooks": 10},
        ]
        items_by_author_id = {
            "id-stub": [],
            "id-expanse": [
                {
                    "id": "abs-item-leviathan-wakes",
                    "media": {"metadata": {"title": "Leviathan Wakes"}},
                },
                {"id": "abs-item-calibans-war", "media": {"metadata": {"title": "Caliban's War"}}},
            ],
            "id-captives": [
                # The book the user can't find — only exists under the third entity.
                {
                    "id": "abs-item-faith-of-beasts",
                    "media": {"metadata": {"title": "The Faith of Beasts"}},
                },
                {
                    "id": "abs-item-mercy-of-gods",
                    "media": {"metadata": {"title": "The Mercy of Gods"}},
                },
            ],
        }
        mock_abs(authors=authors, items_by_author_id=items_by_author_id)
        items = _find_abs_author_items("http://abs", "tok", "lib-id", "James S. A. Corey")
        titles = sorted(it["media"]["metadata"]["title"] for it in items)
        assert titles == [
            "Caliban's War",
            "Leviathan Wakes",
            "The Faith of Beasts",
            "The Mercy of Gods",
        ], f"all three top-ratio entities should be merged: got {titles}"

    def test_single_top_match_below_tied_threshold_still_returned(self, mock_abs):
        # When only one author entity matches at the top ratio, fall back to
        # legacy single-entity behaviour. Lower-ratio false positives like
        # "James Comey" (0.77 vs "James S. A. Corey") MUST NOT be pulled in.
        authors = [
            {"id": "id-corey", "name": "James S. A. Corey", "numBooks": 5},
            {"id": "id-comey", "name": "James Comey", "numBooks": 2},
        ]
        items_by_author_id = {
            "id-corey": [
                {"id": "abs-1", "media": {"metadata": {"title": "Leviathan Wakes"}}},
            ],
            "id-comey": [
                {"id": "abs-2", "media": {"metadata": {"title": "A Higher Loyalty"}}},
            ],
        }
        mock_abs(authors=authors, items_by_author_id=items_by_author_id)
        items = _find_abs_author_items("http://abs", "tok", "lib-id", "James S. A. Corey")
        titles = sorted(it["media"]["metadata"]["title"] for it in items)
        assert titles == ["Leviathan Wakes"], (
            f"only top-ratio entity should be queried; James Comey leaked in: {titles}"
        )

    def test_no_author_match_returns_empty_when_below_threshold(self, mock_abs):
        # No author in the library matches above 0.70 → empty list, no
        # downstream item fetch.
        authors = [{"id": "id-other", "name": "Some Other Person", "numBooks": 3}]
        mock_abs(authors=authors, items_by_author_id={"id-other": [{"id": "x"}]})
        items = _find_abs_author_items("http://abs", "tok", "lib-id", "James S. A. Corey")
        assert items == []

    def test_dedupes_items_by_id_across_entities(self, mock_abs):
        # Defensive: if the same item appears under two tied entities (rare
        # but possible after a partial ABS merge), only one copy should be
        # returned.
        authors = [
            {"id": "id-a", "name": "James S A Corey", "numBooks": 1},
            {"id": "id-b", "name": "James S. A. Corey", "numBooks": 1},
        ]
        items_by_author_id = {
            "id-a": [{"id": "shared-item", "media": {"metadata": {"title": "Shared"}}}],
            "id-b": [{"id": "shared-item", "media": {"metadata": {"title": "Shared"}}}],
        }
        mock_abs(authors=authors, items_by_author_id=items_by_author_id)
        items = _find_abs_author_items("http://abs", "tok", "lib-id", "James S. A. Corey")
        assert len(items) == 1, f"dedupe failed: {[it['id'] for it in items]}"
