"""Tests for monitored_files.normalize_candidate_title.

Covers author name stripping with dots, dashes, and various formatting
conventions, ensuring the function produces a clean title suitable for
fuzzy matching against known book titles.
"""
from __future__ import annotations

import pytest

from shelfmark.core.monitored_files import normalize_candidate_title


class TestBasicStripping:
    """Author name removed, tags/year removed, clean title returned."""

    def test_title_dash_author_with_year(self):
        assert normalize_candidate_title(
            "Earthside - Dennis E. Taylor (2023)", "Dennis E. Taylor"
        ) == "Earthside"

    def test_title_dash_author_no_year(self):
        assert normalize_candidate_title(
            "Earthside - Dennis E. Taylor", "Dennis E. Taylor"
        ) == "Earthside"

    def test_author_dash_title_with_year(self):
        assert normalize_candidate_title(
            "Dennis E. Taylor - Earthside (2023)", "Dennis E. Taylor"
        ) == "Earthside"

    def test_author_dash_title_no_year(self):
        assert normalize_candidate_title(
            "Dennis E. Taylor - Earthside", "Dennis E. Taylor"
        ) == "Earthside"

    def test_no_author_just_title_and_year(self):
        assert normalize_candidate_title(
            "Earthside (2023)", "Dennis E. Taylor"
        ) == "Earthside"

    def test_title_only(self):
        assert normalize_candidate_title("Earthside", "Dennis E. Taylor") == "Earthside"


class TestDotNormalization:
    """Author names with dots (initials) must match after dot→space normalization."""

    def test_middle_initial_with_dot(self):
        # Filename has "E." but normalize replaces dots with spaces
        assert normalize_candidate_title(
            "Unsouled - Will Wight (2016)", "Will Wight"
        ) == "Unsouled"

    def test_jk_rowling(self):
        assert normalize_candidate_title(
            "Harry Potter - J.K. Rowling (1997)", "J.K. Rowling"
        ) == "Harry Potter"

    def test_jrr_tolkien(self):
        assert normalize_candidate_title(
            "The Hobbit - J. R. R. Tolkien (1937)", "J. R. R. Tolkien"
        ) == "The Hobbit"

    def test_author_with_dot_at_start(self):
        assert normalize_candidate_title(
            "J.K. Rowling - Harry Potter (1997)", "J.K. Rowling"
        ) == "Harry Potter"

    def test_dots_only_author_is_ignored(self):
        """An author name that normalizes to empty should not strip anything."""
        result = normalize_candidate_title("My Book - ... (2023)", "...")
        # The author "..." normalizes to "" so no author stripping happens;
        # year/tags still removed
        assert "My Book" in result


class TestMultipleAuthors:
    """Multi-author fallback strips the dash-delimited block containing the author."""

    def test_two_authors_comma_separated(self):
        result = normalize_candidate_title(
            "The Book - Author One, Dennis E. Taylor (2020)", "Dennis E. Taylor"
        )
        assert result == "The Book"

    def test_author_not_present(self):
        """When author doesn't appear in filename, title remains with extra text."""
        result = normalize_candidate_title(
            "The Book - Someone Else (2020)", "Dennis E. Taylor"
        )
        assert "The Book" in result
        assert "Someone Else" in result


class TestEdgeCases:
    def test_empty_raw(self):
        assert normalize_candidate_title("", "Author") == ""

    def test_empty_author(self):
        result = normalize_candidate_title("Title - Author (2020)", "")
        assert "Title" in result

    def test_underscores_replaced(self):
        assert normalize_candidate_title(
            "My_Book_Title", "Author"
        ) == "My Book Title"

    def test_brackets_removed(self):
        result = normalize_candidate_title("Title [retail] (2023)", "Author")
        assert "retail" not in result
        assert "2023" not in result

    def test_format_words_removed(self):
        result = normalize_candidate_title("Title epub retail repack", "Author")
        assert result == "Title"

    def test_emdash_separator(self):
        assert normalize_candidate_title(
            "Title — Dennis E. Taylor (2023)", "Dennis E. Taylor"
        ) == "Title"

    def test_endash_separator(self):
        assert normalize_candidate_title(
            "Title – Dennis E. Taylor (2023)", "Dennis E. Taylor"
        ) == "Title"

    def test_colon_separator(self):
        assert normalize_candidate_title(
            "Title: Dennis E. Taylor (2023)", "Dennis E. Taylor"
        ) == "Title"
