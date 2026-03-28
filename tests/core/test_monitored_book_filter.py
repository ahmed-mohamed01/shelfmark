"""Tests for monitored_book_filter — split detection, dedup, and noise filtering."""
import json

import pytest

from shelfmark.core.monitored_book_filter import (
    classify_noise,
    compute_data_quality,
    deduplicate_books,
    filter_noise_books,
    filter_split_books,
)

# ---------------------------------------------------------------------------
# Fixture: Brandon Sanderson books from Hardcover GraphQL (trimmed to fields
# used by the filter: title, book_series, users_read_count)
# ---------------------------------------------------------------------------

SANDERSON_BOOKS = [
    {"title": "Mistborn: The Final Empire", "users_read_count": 5276, "book_series": [
        {"position": 1, "series": {"name": "The Mistborn Saga"}},
        {"position": 2, "series": {"name": "The Cosmere"}},
        {"position": 1, "series": {"name": "The Mistborn Saga: The Original Trilogy"}},
    ]},
    {"title": "The Way of Kings", "users_read_count": 4016, "book_series": [
        {"position": 1, "series": {"name": "The Stormlight Archive"}},
        {"position": 7, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "The Well of Ascension", "users_read_count": 4176, "book_series": [
        {"position": 2, "series": {"name": "The Mistborn Saga"}},
        {"position": 4, "series": {"name": "The Cosmere"}},
        {"position": 2, "series": {"name": "The Mistborn Saga: The Original Trilogy"}},
    ]},
    {"title": "The Hero of Ages", "users_read_count": 3824, "book_series": [
        {"position": 3, "series": {"name": "The Mistborn Saga"}},
        {"position": 5, "series": {"name": "The Cosmere"}},
        {"position": 3, "series": {"name": "The Mistborn Saga: The Original Trilogy"}},
    ]},
    {"title": "Words of Radiance", "users_read_count": 3352, "book_series": [
        {"position": 2, "series": {"name": "The Stormlight Archive"}},
        {"position": 12, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Tress of the Emerald Sea", "users_read_count": 1500, "book_series": [
        {"position": 29, "series": {"name": "The Cosmere"}},
        {"position": 1, "series": {"name": "Secret Projects"}},
        {"position": 1, "series": {"name": "Hoid's Travails"}},
    ]},
    {"title": "Oathbringer", "users_read_count": 2800, "book_series": [
        {"position": 3, "series": {"name": "The Stormlight Archive"}},
        {"position": 21, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Elantris", "users_read_count": 3000, "book_series": [
        {"position": 1, "series": {"name": "Elantris"}},
        {"position": 1, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Rhythm of War", "users_read_count": 2200, "book_series": [
        {"position": 4, "series": {"name": "The Stormlight Archive"}},
        {"position": 25, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "The Alloy of Law", "users_read_count": 2000, "book_series": [
        {"position": 4, "series": {"name": "The Mistborn Saga"}},
        {"position": 8, "series": {"name": "The Cosmere"}},
        {"position": 1, "series": {"name": "Mistborn: Wax & Wayne"}},
    ]},
    {"title": "Warbreaker", "users_read_count": 2500, "book_series": [
        {"position": 1, "series": {"name": "Warbreaker"}},
        {"position": 6, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Shadows of Self", "users_read_count": 1800, "book_series": [
        {"position": 5, "series": {"name": "The Mistborn Saga"}},
        {"position": 15, "series": {"name": "The Cosmere"}},
        {"position": 2, "series": {"name": "Mistborn: Wax & Wayne"}},
    ]},
    {"title": "The Bands of Mourning", "users_read_count": 1600, "book_series": [
        {"position": 6, "series": {"name": "The Mistborn Saga"}},
        {"position": 16, "series": {"name": "The Cosmere"}},
        {"position": 3, "series": {"name": "Mistborn: Wax & Wayne"}},
    ]},
    {"title": "Edgedancer", "users_read_count": 1400, "book_series": [
        {"position": 2.5, "series": {"name": "The Stormlight Archive"}},
        {"position": 19, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "The Lost Metal", "users_read_count": 1200, "book_series": [
        {"position": 7, "series": {"name": "The Mistborn Saga"}},
        {"position": 27, "series": {"name": "The Cosmere"}},
        {"position": 4, "series": {"name": "Mistborn: Wax & Wayne"}},
    ]},
    {"title": "Skyward", "users_read_count": 1500, "book_series": [
        {"position": 1, "series": {"name": "Skyward"}},
        {"position": 1, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Wind and Truth", "users_read_count": 1000, "book_series": [
        {"position": 5, "series": {"name": "The Stormlight Archive"}},
        {"position": 33, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Yumi and the Nightmare Painter", "users_read_count": 800, "book_series": [
        {"position": 31, "series": {"name": "The Cosmere"}},
        {"position": 3, "series": {"name": "Secret Projects"}},
        {"position": 2, "series": {"name": "Hoid's Travails"}},
    ]},
    {"title": "Dawnshard", "users_read_count": 900, "book_series": [
        {"position": 3.5, "series": {"name": "The Stormlight Archive"}},
        {"position": 26, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "The Emperor's Soul", "users_read_count": 1100, "book_series": [
        {"position": None, "series": {"name": "Elantris"}},
        {"position": 10, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "The Sunlit Man", "users_read_count": 600, "book_series": [
        {"position": 32, "series": {"name": "The Cosmere"}},
        {"position": 4, "series": {"name": "Secret Projects"}},
    ]},
    {"title": "Steelheart", "users_read_count": 1300, "book_series": [
        {"position": 1, "series": {"name": "The Reckoners"}},
    ]},
    {"title": "The Frugal Wizard's Handbook for Surviving Medieval England", "users_read_count": 500, "book_series": [
        {"position": 2, "series": {"name": "Secret Projects"}},
    ]},
    {"title": "The Gathering Storm", "users_read_count": 700, "book_series": [
        {"position": 12, "series": {"name": "The Wheel of Time"}},
    ]},
    {"title": "Starsight", "users_read_count": 800, "book_series": [
        {"position": 2, "series": {"name": "Skyward"}},
        {"position": 2, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Towers of Midnight", "users_read_count": 600, "book_series": [
        {"position": 13, "series": {"name": "The Wheel of Time"}},
    ]},
    {"title": "A Memory of Light", "users_read_count": 550, "book_series": [
        {"position": 14, "series": {"name": "The Wheel of Time"}},
    ]},
    {"title": "Cytonic", "users_read_count": 500, "book_series": [
        {"position": 3, "series": {"name": "Skyward"}},
        {"position": 3, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Firefight", "users_read_count": 900, "book_series": [
        {"position": 2, "series": {"name": "The Reckoners"}},
    ]},
    {"title": "Calamity", "users_read_count": 800, "book_series": [
        {"position": 3, "series": {"name": "The Reckoners"}},
    ]},
    {"title": "The Rithmatist", "users_read_count": 700, "book_series": [
        {"position": 1, "series": {"name": "Rithmatist"}},
    ]},
    {"title": "Isles of the Emberdark", "users_read_count": 300, "book_series": [
        {"position": 34, "series": {"name": "The Cosmere"}},
        {"position": 5, "series": {"name": "Secret Projects"}},
    ]},
    {"title": "The Hope of Elantris", "users_read_count": 400, "book_series": [
        {"position": 1.5, "series": {"name": "Elantris"}},
        {"position": 3, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Defiant", "users_read_count": 400, "book_series": [
        {"position": 4, "series": {"name": "Skyward"}},
        {"position": 4, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Shadows for Silence in the Forests of Hell", "users_read_count": 500, "book_series": [
        {"position": 11, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Sixth of the Dusk", "users_read_count": 400, "book_series": [
        {"position": 13, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "White Sand, Vol. 1", "users_read_count": 300, "book_series": [
        {"position": 1, "series": {"name": "White Sand"}},
        {"position": 20, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Mitosis: A Reckoners Story", "users_read_count": 200, "book_series": [
        {"position": 1.5, "series": {"name": "The Reckoners"}},
    ]},
    {"title": "Sunreach", "users_read_count": 200, "book_series": [
        {"position": 2.1, "series": {"name": "Skyward"}},
        {"position": 1, "series": {"name": "Skyward Flight"}},
        {"position": 2.1, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "ReDawn", "users_read_count": 180, "book_series": [
        {"position": 2.2, "series": {"name": "Skyward"}},
        {"position": 2, "series": {"name": "Skyward Flight"}},
        {"position": 2.2, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Evershore", "users_read_count": 170, "book_series": [
        {"position": 3.1, "series": {"name": "Skyward"}},
        {"position": 3, "series": {"name": "Skyward Flight"}},
        {"position": 3.1, "series": {"name": "Cytoverse"}},
    ]},
    {"title": "Alcatraz vs. the Evil Librarians", "users_read_count": 500, "book_series": [
        {"position": 1, "series": {"name": "Alcatraz vs. the Evil Librarians"}},
    ]},
    {"title": "White Sand, Vol. 2", "users_read_count": 200, "book_series": [
        {"position": 2, "series": {"name": "White Sand"}},
        {"position": 23, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Snapshot", "users_read_count": 300, "book_series": []},
    {"title": "White Sand, Vol. 3", "users_read_count": 150, "book_series": [
        {"position": 3, "series": {"name": "White Sand"}},
        {"position": 24, "series": {"name": "The Cosmere"}},
    ]},
    {"title": "Legion: Skin Deep", "users_read_count": 200, "book_series": [
        {"position": 2, "series": {"name": "Legion"}},
    ]},
    {"title": "Perfect State", "users_read_count": 200, "book_series": []},
    {"title": "Defending Elysium", "users_read_count": 150, "book_series": [
        {"position": 0.5, "series": {"name": "Cytoverse"}},
    ]},
    # --- Split books that should be filtered ---
    {"title": "The Way of Kings, Part 1", "users_read_count": 213, "book_series": [
        {"position": 1.1, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "The Way of Kings, Part 2", "users_read_count": 27, "book_series": [
        {"position": 1.2, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": " Words of Radiance, Part 2", "users_read_count": 69, "book_series": [
        {"position": 2.2, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Oathbringer Part One", "users_read_count": 50, "book_series": [
        {"position": 3.1, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Oathbringer Part Two", "users_read_count": 40, "book_series": [
        {"position": 3.2, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Rhythm of War Part One", "users_read_count": 30, "book_series": [
        {"position": 4.1, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Rhythm of War, Part Two", "users_read_count": 25, "book_series": [
        {"position": 4.2, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "The Well of Ascension, Part 1", "users_read_count": 18, "book_series": [
        {"position": 2, "series": {"name": "The Mistborn Saga: The Original Trilogy"}},
    ]},
    {"title": "The Bands of Mourning, Part 1", "users_read_count": 15, "book_series": [
        {"position": 6, "series": {"name": "Mistborn GraphicAudio"}},
    ]},
    {"title": "The Bands of Mourning, Part 2", "users_read_count": 12, "book_series": [
        {"position": 6, "series": {"name": "Mistborn GraphicAudio"}},
    ]},
    # --- Non-split books that should be kept ---
    {"title": "The Original", "users_read_count": 100, "book_series": []},
    {"title": "The Scrivener's Bones", "users_read_count": 200, "book_series": [
        {"position": 2, "series": {"name": "Alcatraz vs. the Evil Librarians"}},
    ]},
    {"title": "The Way of Kings Prime", "users_read_count": 100, "book_series": [
        {"position": 0.1, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Elsecaller / King Lopen The First of Alethkar", "users_read_count": 6, "book_series": [
        {"position": 2.1, "series": {"name": "The Stormlight Archive"}},
    ]},
    {"title": "Ghostbloods 1", "users_read_count": 50, "book_series": [
        {"position": 8, "series": {"name": "The Mistborn Saga"}},
        {"position": 1, "series": {"name": "Mistborn: Ghostbloods"}},
    ]},
    {"title": "Dark One: Forgotten", "users_read_count": 30, "book_series": [
        {"position": None, "series": {"name": "Dark One"}},
    ]},
    {"title": "Songs of the Dead", "users_read_count": 50, "book_series": []},
    {"title": "Dreamer", "users_read_count": 40, "book_series": []},
]


EXPECTED_FILTERED_TITLES = {
    "The Way of Kings, Part 1",
    "The Way of Kings, Part 2",
    " Words of Radiance, Part 2",
    "Oathbringer Part One",
    "Oathbringer Part Two",
    "Rhythm of War Part One",
    "Rhythm of War, Part Two",
    "The Well of Ascension, Part 1",
    "The Bands of Mourning, Part 1",
    "The Bands of Mourning, Part 2",
}


def test_filter_split_books_sanderson():
    canonical, filtered = filter_split_books(SANDERSON_BOOKS)

    filtered_titles = {b["title"] for b in filtered}
    canonical_titles = {b["title"] for b in canonical}

    assert filtered_titles == EXPECTED_FILTERED_TITLES, (
        f"Unexpected filtered set.\n"
        f"  Missing from filtered: {EXPECTED_FILTERED_TITLES - filtered_titles}\n"
        f"  Wrongly filtered: {filtered_titles - EXPECTED_FILTERED_TITLES}"
    )

    # Verify no canonical books were wrongly filtered
    for title in [
        "Mistborn: The Final Empire",
        "The Way of Kings",
        "Words of Radiance",
        "Oathbringer",
        "Rhythm of War",
        "The Well of Ascension",
        "The Bands of Mourning",
        "Edgedancer",
        "Dawnshard",
        "The Way of Kings Prime",
        "Elsecaller / King Lopen The First of Alethkar",
        "Snapshot",
        "Perfect State",
        "The Original",
        "Sunreach",
        "ReDawn",
        "Evershore",
        "White Sand, Vol. 1",
        "White Sand, Vol. 2",
        "White Sand, Vol. 3",
        "Defending Elysium",
        "The Hope of Elantris",
        "Mitosis: A Reckoners Story",
    ]:
        assert title in canonical_titles, f"Canonical book '{title}' was wrongly filtered out"


def test_filter_split_books_empty():
    canonical, filtered = filter_split_books([])
    assert canonical == []
    assert filtered == []


def test_filter_split_books_no_splits():
    books = [
        {"title": "Book One", "users_read_count": 100, "book_series": [
            {"position": 1, "series": {"name": "My Series"}},
        ]},
        {"title": "Book Two", "users_read_count": 80, "book_series": [
            {"position": 2, "series": {"name": "My Series"}},
        ]},
    ]
    canonical, filtered = filter_split_books(books)
    assert len(canonical) == 2
    assert len(filtered) == 0


def test_filter_works_with_db_shape():
    """Verify filter works with DB row shape (all_series JSON, readers_count)."""
    books = [
        {
            "title": "The Way of Kings",
            "readers_count": 4016,
            "all_series": json.dumps([
                {"name": "The Stormlight Archive", "position": 1, "count": 10},
            ]),
        },
        {
            "title": "The Way of Kings, Part 1",
            "readers_count": 213,
            "all_series": json.dumps([
                {"name": "The Stormlight Archive", "position": 1.1, "count": 10},
            ]),
        },
    ]
    canonical, filtered = filter_split_books(books)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "The Way of Kings, Part 1"


def test_novellas_not_filtered():
    """Novellas at .5 positions must not be treated as splits."""
    books = [
        {"title": "Words of Radiance", "users_read_count": 3352, "book_series": [
            {"position": 2, "series": {"name": "The Stormlight Archive"}},
        ]},
        {"title": "Edgedancer", "users_read_count": 1400, "book_series": [
            {"position": 2.5, "series": {"name": "The Stormlight Archive"}},
        ]},
        {"title": "Oathbringer", "users_read_count": 2800, "book_series": [
            {"position": 3, "series": {"name": "The Stormlight Archive"}},
        ]},
    ]
    canonical, filtered = filter_split_books(books)
    assert len(filtered) == 0
    assert {b["title"] for b in canonical} == {"Words of Radiance", "Edgedancer", "Oathbringer"}


# ---------------------------------------------------------------------------
# Noise filter tests
# ---------------------------------------------------------------------------

def _book(title, *, users_read=100, users_count=100, lang="en", contrib=1):
    """Helper to build a book dict suitable for both classify_noise and filter_noise_books.

    Includes enough fields for compute_data_quality() to produce a robust
    score above the threshold when lang="en" (preferred_isbns + lang match
    gives 55 points alone).
    """
    b = {
        "title": title,
        "users_count": users_count,
        "users_read_count": users_read,
        "contributions_aggregate": {"aggregate": {"count": contrib}},
        "preferred_isbns": [{"isbn_13": "9780000000000"}] if lang == "en" else [],
        "preferred_asins": [],
        "description": "Test book description",
        "image": {"url": "https://example.com/cover.jpg"},
        "default_physical_edition": {"isbn_13": "9780000000000", "pages": 300},
        "rating": 4.0,
        "cached_tags": [{"tag": "fiction"}],
    }
    if lang:
        b["lang_editions"] = [{"language": {"code2": lang}}]
    else:
        b["lang_editions"] = []
        b["preferred_isbns"] = []  # no lang → no preferred editions
    return b


# --- Title pattern tests ---

@pytest.mark.parametrize("title", [
    "The Way of Kings (1 of 5) [Dramatized Adaptation]",
    "Red Rising (2 of 2) Dramatized Adaptation",
    "Stormlight Archive MM Boxed Set I, Books 1-3",
    "The Farseer Trilogy 3-Book Bundle",
    "Sneak Peek for Witch King",
    "The Emperor's Blades: Chapters-1-7",
    "Mistborn: The Final Empire - Annotations",
    "Unseen Academicals: The Play",
    "Jingo: The Play",
    "Way of Shadows: The Graphic Novel",
    "Discworld's Ankh-Morpork City Watch Diary 1999",
    "Lu-Tze's Yearbook of Enlightenment 2008",
    "Ankh-Morpork Post Office Handbook 2007",
    "A Court of Thorns and Roses Colouring Book",
    "Terry Pratchett's Discworld Coloring Book",
    "The Unseen University Challenge Quizbook",
    "GURPS Discworld",
    "Grimdark Magazine Issue #4",
    "Lightspeed Magazine, February 2015",
    "Steelheart Chapter Sampler",
    "Mistborn Adventure Game",
    "Snapshot / Dreamer",
    "Elsecaller / King Lopen The First of Alethkar",
])
def test_title_pattern_filters_noise(title):
    reason = classify_noise(_book(title))
    assert reason is not None, f"Expected '{title}' to be classified as noise"
    assert reason.startswith("title:"), f"Expected title pattern match, got: {reason}"


@pytest.mark.parametrize("title", [
    "Scion",
    "The Way of Kings",
    "Mistborn: The Final Empire",
    "Edge of the Dream: An Epic Fantasy Adventure",
    "The Sunlit Man",
    "Trailer Park Fairy Tales",
    "The Unholy Consult",
    "Allomancer Jak and the Pits of Eltania",
    "The Eleventh Metal",
    "Forsworn",
    "Messenger's Legacy",
    "He Who Fights with Monsters",
    "Dungeon Crawler Carl",
])
def test_title_pattern_keeps_real_books(title):
    reason = classify_noise(_book(title))
    assert reason is None, f"Real book '{title}' was wrongly classified as noise: {reason}"


# --- Data quality score tests ---

def _rich_book(title, *, lang="en", users_count=100, users_read=50,
               preferred_isbns=True, preferred_asins=False,
               description=True, cover=True, isbn=True, pages=True,
               rating=True, tags=True, contrib=1):
    """Build a book dict with full GraphQL-shape fields for quality tests."""
    b = {
        "title": title,
        "users_count": users_count,
        "users_read_count": users_read,
        "contributions_aggregate": {"aggregate": {"count": contrib}},
        "preferred_isbns": [{"isbn_13": "9780123456789"}] if preferred_isbns else [],
        "preferred_asins": [{"asin": "B00TEST"}] if preferred_asins else [],
        "description": "A great book" if description else "",
        "image": {"url": "https://example.com/cover.jpg"} if cover else {},
        "default_physical_edition": {
            "isbn_13": "9780123456789" if isbn else None,
            "pages": 300 if pages else None,
        },
        "rating": 4.2 if rating else None,
        "cached_tags": [{"tag": "fantasy"}] if tags else None,
    }
    if lang:
        b["lang_editions"] = [{"language": {"code2": lang}}]
    else:
        b["lang_editions"] = []
    return b


def test_quality_high_for_english_book_with_editions():
    book = _rich_book("Mistborn: The Final Empire", lang="en",
                      preferred_isbns=True, preferred_asins=True, users_count=5000)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q >= 80, f"Expected high quality for full English book, got {q}"


def test_quality_low_for_translation_no_data():
    """Translation with no editions, no lang data → very low quality."""
    book = _rich_book("A Liga da Lei", lang=None, users_count=2, users_read=0,
                      preferred_isbns=False, preferred_asins=False,
                      description=False, cover=True, isbn=False,
                      pages=False, rating=False, tags=False)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q < 20, f"Expected low quality for bare translation, got {q}"


def test_quality_low_for_confirmed_non_english():
    """Confirmed Spanish book → penalty drives quality down."""
    book = _rich_book("La búsqueda del asesino", lang="es", users_count=5, users_read=5,
                      preferred_isbns=False, preferred_asins=False,
                      description=True, cover=True, isbn=True, pages=True, rating=True, tags=True)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q < 20, f"Expected low quality for confirmed non-English book, got {q}"


def test_quality_high_for_popular_book_without_editions():
    """Popular English book that just lacks edition data on Hardcover."""
    book = _rich_book("Warbreaker", lang=None, users_count=46, users_read=37,
                      preferred_isbns=False, preferred_asins=False,
                      description=False, cover=False, isbn=False,
                      pages=False, rating=True, tags=True)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q >= 20, f"Expected passing quality for popular book, got {q}"


def test_quality_penalizes_non_latin_title():
    book = _rich_book("Имя ветра", lang="en", users_count=3, users_read=3,
                      preferred_isbns=False, preferred_asins=False,
                      description=False, cover=False, isbn=False,
                      pages=False, rating=True, tags=False)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q < 20, f"Non-Latin title should have low quality, got {q}"


def test_quality_penalizes_diacritics():
    book = _rich_book("Coração de aço", lang=None, users_count=2, users_read=2,
                      preferred_isbns=False, preferred_asins=False,
                      description=False, cover=True, isbn=False,
                      pages=False, rating=True, tags=False)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q < 20, f"Diacritic title should have low quality, got {q}"


def test_quality_respects_preferred_languages():
    """If user prefers French, French books should score well."""
    book = _rich_book("L'Assassin royal", lang="fr", users_count=50, users_read=50,
                      preferred_isbns=True, preferred_asins=False)
    q = compute_data_quality(book, lang_codes=["en", "fr"])
    assert q >= 50, f"French book with French preference should score well, got {q}"


# --- Contributor count / auto-hide tests ---

def test_high_contrib_auto_hidden():
    books = [
        _book("Unfettered", contrib=24),
        _book("Year's Best SF", contrib=51),
        _book("The Way of Kings", contrib=1),
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert len(auto_hide) == 2
    assert {b["title"] for b in auto_hide} == {"Unfettered", "Year's Best SF"}
    assert len(kept) == 1
    assert kept[0]["title"] == "The Way of Kings"


def test_contrib_threshold_boundary():
    """c=10 should pass, c=11 should be auto-hidden."""
    books = [
        _book("Ten Contributors", contrib=10),
        _book("Eleven Contributors", contrib=11),
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert len(kept) == 1
    assert kept[0]["title"] == "Ten Contributors"
    assert len(auto_hide) == 1
    assert auto_hide[0]["title"] == "Eleven Contributors"


def test_coauthored_books_pass():
    """Books with 2-3 contributors (co-authored) should not be auto-hidden."""
    books = [
        _book("Iron Prince", contrib=2),
        _book("Good Omens", contrib=2),
        _book("Sandman: Overture #4", contrib=3),
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert len(kept) == 3
    assert len(auto_hide) == 0


# --- filter_noise_books integration ---

def test_filter_noise_books_three_way_split():
    """Verify the three output lists are correctly populated."""
    books = [
        _book("Real Novel", contrib=1),
        _book("Guards! Guards!: The Play", contrib=2),      # title noise
        _book("Die Gabe der Könige", lang="de", users_read=6),  # auto-hide (low quality)
        _book("Unfettered III", contrib=31),                 # auto-hide (contrib count)
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert [b["title"] for b in kept] == ["Real Novel"]
    assert len(noise) == 1
    assert noise[0]["title"] == "Guards! Guards!: The Play"
    assert len(auto_hide) == 2
    assert {b["title"] for b in auto_hide} == {"Die Gabe der Könige", "Unfettered III"}


def test_filter_noise_books_empty():
    kept, noise, auto_hide = filter_noise_books([], lang_codes=["en"])
    assert kept == []
    assert noise == []
    assert auto_hide == []


def test_noise_filter_priority_title_over_contrib():
    """Title pattern match takes priority — book is noise, not auto-hide."""
    book = _book("Grimdark Magazine Issue #4", contrib=20)
    kept, noise, auto_hide = filter_noise_books([book], lang_codes=["en"])
    assert len(noise) == 1
    assert len(auto_hide) == 0


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


def test_dedup_subtitle_variants():
    """Books differing only by subtitle should be merged, keeping higher users."""
    books = [
        {"id": 1, "title": "The Age of Diagnosis: Sickness, Health and How Modern Medicine Has Gone Too Far", "users_count": 6},
        {"id": 2, "title": "The Age of Diagnosis", "users_count": 3},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 1
    assert len(deduped) == 1
    assert deduped[0]["id"] == 1  # higher users_count kept


def test_dedup_keeps_distinct_books():
    """Books with genuinely different titles should all be kept."""
    books = [
        {"id": 1, "title": "The Sleeping Beauties", "users_count": 36},
        {"id": 2, "title": "It's All in Your Head", "users_count": 23},
        {"id": 3, "title": "Brainstorm", "users_count": 7},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 0
    assert len(deduped) == 3


def test_dedup_empty():
    deduped, count = deduplicate_books([])
    assert deduped == []
    assert count == 0


def test_dedup_single_book():
    books = [{"id": 1, "title": "Only Book", "users_count": 10}]
    deduped, count = deduplicate_books(books)
    assert count == 0
    assert len(deduped) == 1


def test_dedup_exact_match_merges():
    """Exact normalised title match should merge."""
    books = [
        {"id": 1, "title": "The Hunger Games", "users_count": 9671},
        {"id": 2, "title": "Hunger games", "users_count": 39},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 1
    assert deduped[0]["id"] == 1


def test_dedup_preserves_order():
    """Kept books should appear in their original order."""
    books = [
        {"id": 1, "title": "Alpha Rising", "users_count": 10},
        {"id": 2, "title": "Beta Falling: A Long Subtitle", "users_count": 5},
        {"id": 3, "title": "Beta Falling", "users_count": 20},
        {"id": 4, "title": "Gamma Setting", "users_count": 15},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 1
    assert [b["id"] for b in deduped] == [1, 3, 4]  # Beta Falling:subtitle dropped, Beta Falling kept


def test_dedup_article_insensitive():
    """Articles (the/a/an) should not affect dedup matching."""
    books = [
        {"id": 1, "title": "The Invention of Power", "users_count": 10},
        {"id": 2, "title": "Invention of Power: Popes, Kings, and the Birth of the West", "users_count": 5},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 1
    assert len(deduped) == 1
    assert deduped[0]["id"] == 1


def test_dedup_single_word_prefix_not_merged():
    """Different books sharing a single-word series prefix must NOT be merged."""
    books = [
        {"id": 1, "title": "Mistborn: The Final Empire", "users_count": 5000},
        {"id": 2, "title": "Mistborn: Secret History", "users_count": 800},
        {"id": 3, "title": "Mistborn: The Well of Ascension", "users_count": 4000},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 0, "Single-word prefix should not cause dedup"
    assert len(deduped) == 3


def test_dedup_series_colon_books_not_merged():
    """Series books using 'Series: Book N' format must NOT be merged."""
    books = [
        {"id": 1, "title": "Azarinth Healer: Book One", "users_count": 135},
        {"id": 2, "title": "Azarinth Healer: Book Two", "users_count": 98},
        {"id": 3, "title": "Azarinth Healer: Book Three", "users_count": 86},
        {"id": 4, "title": "Azarinth Healer: Book Four", "users_count": 67},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 0, "Series books should not be merged"
    assert len(deduped) == 4


def test_dedup_subtitle_variant_still_merges():
    """A short title should still merge with its longer subtitle variant."""
    books = [
        {"id": 1, "title": "The Dictator's Handbook", "users_count": 241},
        {"id": 2, "title": "The Dictator's Handbook: Why Bad Behavior is Almost Always Good Politics", "users_count": 50},
    ]
    deduped, count = deduplicate_books(books)
    assert count == 1
    assert deduped[0]["id"] == 1


# --- Additional title pattern tests ---


def test_slash_combined_is_noise():
    """Slash-combined volumes like 'Snapshot / Dreamer' should be noise."""
    book = _book("Snapshot / Dreamer")
    reason = classify_noise(book)
    assert reason is not None
    assert reason.startswith("title:")


def test_chapter_sampler_is_noise():
    book = _book("Steelheart Chapter Sampler")
    reason = classify_noise(book)
    assert reason is not None
    assert reason.startswith("title:")


def test_adventure_game_is_noise():
    book = _book("Mistborn Adventure Game")
    reason = classify_noise(book)
    assert reason is not None
    assert reason.startswith("title:")


def test_free_preview_is_noise():
    book = _book("Free Preview: The Way of Kings")
    reason = classify_noise(book)
    assert reason is not None
    assert reason.startswith("title:")


def test_short_slash_title_not_noise():
    """Short titles with slash should NOT be caught (e.g. 'Us / Them')."""
    book = _book("Us / Them")
    reason = classify_noise(book)
    assert reason is None, f"Short slash title wrongly classified as noise: {reason}"


# ---------------------------------------------------------------------------
# Compilation auto-hide tests
# ---------------------------------------------------------------------------


def test_compilation_flag_auto_hides():
    """Books with compilation=True should be auto-hidden, not noise-filtered."""
    books = [
        {**_book("Arcanum Unbounded"), "compilation": True},
        _book("Mistborn: The Final Empire"),
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert len(kept) == 1
    assert kept[0]["title"] == "Mistborn: The Final Empire"
    assert len(auto_hide) == 1
    assert auto_hide[0]["title"] == "Arcanum Unbounded"
    assert len(noise) == 0


# ---------------------------------------------------------------------------
# Quality score clamping tests
# ---------------------------------------------------------------------------


def test_quality_score_clamps_to_zero():
    """Heavily penalised book should return 0, not negative."""
    book = _rich_book("Пепел и сталь", lang="ru", users_count=2, users_read=0,
                      preferred_isbns=False, preferred_asins=False,
                      description=False, cover=False, isbn=False,
                      pages=False, rating=False, tags=False)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q == 0


def test_quality_score_clamps_to_100():
    """Maximally rich book should not exceed 100."""
    book = _rich_book("Perfect Book", lang="en", users_count=10000, users_read=5000,
                      preferred_isbns=True, preferred_asins=True,
                      description=True, cover=True, isbn=True,
                      pages=True, rating=True, tags=True)
    q = compute_data_quality(book, lang_codes=["en"])
    assert q == 100


# ---------------------------------------------------------------------------
# Split filter boundary tests
# ---------------------------------------------------------------------------


def test_split_filter_parent_less_than_2x_readers():
    """Parent with <2x readers should NOT cause the split to be filtered."""
    books = [
        {"title": "The Way of Kings", "users_read_count": 150, "book_series": []},
        {"title": "The Way of Kings, Part 1", "users_read_count": 100, "book_series": []},
    ]
    canonical, filtered = filter_split_books(books)
    assert len(filtered) == 0, "Part should not be filtered when parent has <2x readers"
    assert len(canonical) == 2


def test_split_filter_parent_exactly_2x_readers():
    """Parent with exactly 2x readers SHOULD cause the split to be filtered."""
    books = [
        {"title": "The Way of Kings", "users_read_count": 200, "book_series": []},
        {"title": "The Way of Kings, Part 1", "users_read_count": 100, "book_series": []},
    ]
    canonical, filtered = filter_split_books(books)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "The Way of Kings, Part 1"


def test_split_filter_bad_series_position_no_crash():
    """Non-numeric series position should not crash the filter."""
    books = [
        {"title": "Book One", "users_read_count": 100, "book_series": [
            {"position": "invalid", "series": {"name": "My Series"}},
        ]},
        {"title": "Book Two", "users_read_count": 80, "book_series": [
            {"position": None, "series": {"name": "My Series"}},
        ]},
    ]
    canonical, filtered = filter_split_books(books)
    assert len(canonical) == 2
    assert len(filtered) == 0
