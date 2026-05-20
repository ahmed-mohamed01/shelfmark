from unittest.mock import MagicMock
from threading import Event

from shelfmark.core.models import DownloadTask, SearchMode


def test_update_download_status_dedupes_identical_events(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-book-id"

    # Ensure clean module-level state
    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "queue_status", lambda: {})

    mock_ws = MagicMock()
    monkeypatch.setattr(orchestrator, "ws_manager", mock_ws)

    times = iter([1.0, 2.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")
    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")

    # Status + message should only be applied/broadcast once.
    assert mock_queue.update_status.call_count == 1
    assert mock_queue.update_status_message.call_count == 1
    assert mock_ws.broadcast_status_update.call_count == 1

    # Duplicate keep-alives should not refresh stall activity.
    assert orchestrator._last_activity[book_id] == 1.0


def test_download_task_records_monitored_attempt_when_postprocess_returns_none(monkeypatch):
    """When post-processing fails, the terminal hook records a monitored attempt."""
    import shelfmark.core.monitored_downloads as monitored_downloads
    from shelfmark.core.models import QueueStatus

    class FakeHistoryDb:
        def __init__(self):
            self.rows = []

        def insert_monitored_book_attempt_history(self, **kwargs):
            self.rows.append(kwargs)

    task = DownloadTask(
        task_id="rel-123",
        source="prowlarr",
        title="A Parade of Horribles",
        content_type="ebook",
        search_mode=SearchMode.UNIVERSAL,
        output_mode="folder",
        output_args={
            "history_context": {
                "entity_id": 17,
                "provider": "hardcover",
                "provider_book_id": "book-42",
                "release_title": "A Parade of Horribles [EPUB]",
                "match_score": 98.0,
            }
        },
        user_id=9,
        status_message="Path '/plex/downloads/...' is not accessible from Shelfmark's container",
    )

    fake_history_db = FakeHistoryDb()
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_history_db)

    # Simulate the terminal hook being fired with ERROR status (as happens in _download_worker)
    monitored_downloads._on_download_terminal("rel-123", QueueStatus.ERROR, task)

    assert len(fake_history_db.rows) == 1
    row = fake_history_db.rows[0]
    assert row["status"] == "download_failed"
    assert row["entity_id"] == 17
    assert row["provider"] == "hardcover"
    assert row["provider_book_id"] == "book-42"
    assert row["user_ids"] == [9]
    assert row["error_message"] == "Path '/plex/downloads/...' is not accessible from Shelfmark's container"


def test_download_task_records_monitored_attempt_when_handler_returns_none(monkeypatch):
    """When the download handler returns None, the terminal hook records a monitored attempt."""
    import shelfmark.core.monitored_downloads as monitored_downloads
    from shelfmark.core.models import QueueStatus

    class FakeHistoryDb:
        def __init__(self):
            self.rows = []

        def insert_monitored_book_attempt_history(self, **kwargs):
            self.rows.append(kwargs)

    task = DownloadTask(
        task_id="rel-456",
        source="prowlarr",
        title="A Parade of Horribles",
        content_type="ebook",
        search_mode=SearchMode.UNIVERSAL,
        output_mode="folder",
        output_args={
            "history_context": {
                "entity_id": 17,
                "provider": "hardcover",
                "provider_book_id": "book-42",
                "release_title": "A Parade of Horribles [EPUB]",
                "match_score": 91.5,
            }
        },
        user_id=9,
        status_message="Path '/plex/downloads/torrents/complete/readarr/...' is not accessible from Shelfmark's container",
    )

    fake_history_db = FakeHistoryDb()
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_history_db)

    # Simulate the terminal hook being fired with ERROR status (as happens in _download_worker)
    monitored_downloads._on_download_terminal("rel-456", QueueStatus.ERROR, task)

    assert len(fake_history_db.rows) == 1
    row = fake_history_db.rows[0]
    assert row["status"] == "download_failed"
    assert row["entity_id"] == 17
    assert row["provider"] == "hardcover"
    assert row["provider_book_id"] == "book-42"
    assert row["user_ids"] == [9]
    assert row["error_message"] == "Path '/plex/downloads/torrents/complete/readarr/...' is not accessible from Shelfmark's container"


def test_update_download_progress_dedupes_identical_progress_for_activity(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-book"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([10.0, 20.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.0)

    assert mock_queue.update_progress.call_count == 2
    assert orchestrator._last_activity[book_id] == 10.0
    assert orchestrator._last_progress_value[book_id] == 0.0


def test_update_download_progress_refreshes_activity_when_progress_changes(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-change"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([30.0, 40.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.5)

    assert orchestrator._last_activity[book_id] == 40.0
    assert orchestrator._last_progress_value[book_id] == 0.5
