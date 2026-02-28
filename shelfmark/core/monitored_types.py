"""Typed result objects and exceptions for the monitored feature operations layer.

Import this module for all result types and exceptions used across
monitored_db_ops, monitored_operations, and monitored_routes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# Exceptions
# =============================================================================


class MonitoredError(Exception):
    """Base exception for all monitored feature errors."""


class MonitoredEntityNotFound(MonitoredError):
    """Raised when a monitored entity cannot be located."""


class MonitoredProviderError(MonitoredError):
    """Raised when a metadata provider is unavailable or returns an error."""


class MonitoredPathError(MonitoredError):
    """Raised when a configured path is invalid, missing, or outside allowed roots."""


# =============================================================================
# Result dataclasses
# =============================================================================


@dataclass
class RefreshResult:
    """Result of refresh_author()."""
    books_upserted: int = 0
    books_pruned: int = 0


@dataclass
class ScanResult:
    """Result of update_file_availability()."""
    entity_id: int = 0
    matched: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    missing_books: list[dict[str, Any]] = field(default_factory=list)
    scanned_ebook_files: int = 0
    scanned_audio_folders: int = 0
    ebook_dir: str | None = None
    audiobook_dir: str | None = None
    warnings: dict[str, str] = field(default_factory=dict)


@dataclass
class SearchSummary:
    """Result of search_missing_books()."""
    entity_id: int = 0
    content_type: str = "ebook"
    total_candidates: int = 0
    queued: int = 0
    unreleased: int = 0
    no_match: int = 0
    below_cutoff: int = 0
    failed: int = 0


@dataclass
class AvailabilityData:
    """Result of compute_book_availability()."""
    books: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    availability_by_book: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
