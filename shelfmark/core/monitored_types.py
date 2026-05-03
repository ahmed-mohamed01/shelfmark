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
    """Base provider error — includes error_type for structured handling."""

    def __init__(self, message: str, *, error_type: str = "unknown") -> None:
        super().__init__(message)
        self.error_type = error_type


class MonitoredProviderNetworkError(MonitoredProviderError):
    """Network unreachable, DNS failure, connection refused."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="network")


class MonitoredProviderTimeoutError(MonitoredProviderError):
    """Request timed out."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="timeout")


class MonitoredProviderRateLimitError(MonitoredProviderError):
    """HTTP 429 — rate limited."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="rate_limit")


class MonitoredProviderAuthError(MonitoredProviderError):
    """HTTP 401/403 — invalid or expired API key."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="auth")


class MonitoredProviderAPIError(MonitoredProviderError):
    """Server error (5xx) or GraphQL errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="api_error")


class MonitoredPathError(MonitoredError):
    """Raised when a configured path is invalid, missing, or outside allowed roots."""


# =============================================================================
# Transient error detection
# =============================================================================

#: Error types that may succeed on retry (network glitches, rate limits, server errors).
TRANSIENT_ERROR_TYPES = frozenset({"network", "timeout", "rate_limit", "api_error"})


def is_transient_provider_error(exc: BaseException) -> bool:
    """Return True if *exc* is a provider error that may succeed on retry."""
    return isinstance(exc, MonitoredProviderError) and exc.error_type in TRANSIENT_ERROR_TYPES


# =============================================================================
# Result dataclasses
# =============================================================================


@dataclass
class DiffResult:
    """Result of diff_sync_books() — tracks provider-side changes."""
    added: int = 0
    removed: int = 0
    removed_titles: list[str] = field(default_factory=list)


@dataclass
class RefreshResult:
    """Result of an author metadata sync."""
    books_upserted: int = 0
    books_added: int = 0
    books_removed: int = 0
    removed_titles: list[str] = field(default_factory=list)


@dataclass
class BatchSyncResult:
    """Result of _run_batch_sync()."""
    total: int = 0
    successful: int = 0
    failed: int = 0
    info: list[dict[str, Any]] = field(default_factory=list)
    retried: int = 0
    retry_succeeded: int = 0


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
    skipped_history_final_path_exists: int = 0
    skipped_existing_file: int = 0
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
