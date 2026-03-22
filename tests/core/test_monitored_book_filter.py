"""Tests for monitored_book_filter — split detection and noise filtering."""
import json

import pytest

from shelfmark.core.monitored_book_filter import (
    classify_noise,
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

def _book(title, *, users_read=100, lang="en", contrib=1):
    """Helper to build a minimal book dict for noise filter tests."""
    b = {
        "title": title,
        "users_read_count": users_read,
        "contributions_aggregate": {"aggregate": {"count": contrib}},
    }
    if lang:
        b["lang_editions"] = [{"language": {"code2": lang}}]
    else:
        b["lang_editions"] = []
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
])
def test_title_pattern_filters_noise(title):
    reason = classify_noise(_book(title), lang_codes=["en"])
    assert reason is not None, f"Expected '{title}' to be classified as noise"
    assert reason.startswith("title:"), f"Expected title pattern match, got: {reason}"


@pytest.mark.parametrize("title", [
    "Scion",
    "The Way of Kings",
    "Mistborn: The Final Empire",
    "Edge of the Dream: An Epic Fantasy Adventure",
    "Snapshot / Dreamer",
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
    reason = classify_noise(_book(title), lang_codes=["en"])
    assert reason is None, f"Real book '{title}' was wrongly classified as noise: {reason}"


# --- Language filter tests ---

def test_lang_filter_removes_non_english_released():
    book = _book("La búsqueda del asesino", lang="es", users_read=5)
    reason = classify_noise(book, lang_codes=["en"])
    assert reason is not None
    assert reason.startswith("lang:")


def test_lang_filter_keeps_english():
    book = _book("The Way of Kings", lang="en", users_read=4000)
    assert classify_noise(book, lang_codes=["en"]) is None


def test_lang_filter_keeps_unreleased_without_editions():
    """Unreleased books (0 readers) with no lang data should not be filtered."""
    book = _book("Scion", lang=None, users_read=0)
    assert classify_noise(book, lang_codes=["en"]) is None


def test_lang_filter_keeps_non_english_unreleased():
    """Non-English book with 0 readers might be upcoming — keep it."""
    book = _book("Untitled", lang="fr", users_read=0)
    assert classify_noise(book, lang_codes=["en"]) is None


def test_lang_filter_respects_preferred_languages():
    """If user prefers French, French books should pass."""
    book = _book("L'Assassin royal", lang="fr", users_read=50)
    assert classify_noise(book, lang_codes=["en", "fr"]) is None


# --- Non-Latin title tests ---

def test_non_latin_cyrillic_filtered():
    book = _book("Имя ветра", lang="en", users_read=3)
    reason = classify_noise(book, lang_codes=["en"])
    assert reason == "non_latin_title"


def test_non_latin_kept_if_unreleased():
    book = _book("Имя ветра", lang="en", users_read=0)
    assert classify_noise(book, lang_codes=["en"]) is None


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
        _book("Die Gabe der Könige", lang="de", users_read=6),  # lang noise
        _book("Unfettered III", contrib=31),                 # auto-hide
    ]
    kept, noise, auto_hide = filter_noise_books(books, lang_codes=["en"])
    assert [b["title"] for b in kept] == ["Real Novel"]
    assert len(noise) == 2
    assert {b["title"] for b in noise} == {"Guards! Guards!: The Play", "Die Gabe der Könige"}
    assert [b["title"] for b in auto_hide] == ["Unfettered III"]


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
