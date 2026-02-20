from unittest.mock import MagicMock
from threading import Event

from shelfmark.core.models import DownloadTask, SearchMode


def test_update_download_status_dedupes_identical_events(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-book-id"

    # Ensure clean module-level state
    orchestrator._last_activity.clear()
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

    # Activity timestamp should still be updated on the duplicate keep-alive call.
    assert orchestrator._last_activity[book_id] == 2.0


def test_download_task_records_monitored_attempt_when_postprocess_returns_none(monkeypatch, tmp_path):
    import shelfmark.download.orchestrator as orchestrator

    class FakeHistoryDb:
        def __init__(self):
            self.rows = []

        def insert_monitored_book_attempt_history(self, **kwargs):
            self.rows.append(kwargs)

    temp_file = tmp_path / "failed.epub"
    temp_file.write_text("dummy", encoding="utf-8")

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

    fake_handler = MagicMock()
    fake_handler.download.return_value = str(temp_file)

    fake_history_db = FakeHistoryDb()
    monkeypatch.setattr(orchestrator, "_history_user_db", fake_history_db)
    monkeypatch.setattr(orchestrator.book_queue, "get_task", lambda _task_id: task)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: fake_handler)
    monkeypatch.setattr(orchestrator, "run_blocking_io", lambda func, *args, **kwargs: func(*args, **kwargs))
    monkeypatch.setattr(orchestrator, "post_process_download", lambda *_args, **_kwargs: None)

    result = orchestrator._download_task("rel-123", Event())

    assert result is None
    assert len(fake_history_db.rows) == 1
    row = fake_history_db.rows[0]
    assert row["status"] == "download_failed"
    assert row["entity_id"] == 17
    assert row["provider"] == "hardcover"
    assert row["provider_book_id"] == "book-42"
    assert row["user_id"] == 9
    assert row["error_message"] == "Path '/plex/downloads/...' is not accessible from Shelfmark's container"


def test_download_task_records_monitored_attempt_when_handler_returns_none(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

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

    fake_handler = MagicMock()
    fake_handler.download.return_value = None

    fake_history_db = FakeHistoryDb()
    monkeypatch.setattr(orchestrator, "_history_user_db", fake_history_db)
    monkeypatch.setattr(orchestrator.book_queue, "get_task", lambda _task_id: task)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: fake_handler)

    result = orchestrator._download_task("rel-456", Event())

    assert result is None
    assert len(fake_history_db.rows) == 1
    row = fake_history_db.rows[0]
    assert row["status"] == "download_failed"
    assert row["entity_id"] == 17
    assert row["provider"] == "hardcover"
    assert row["provider_book_id"] == "book-42"
    assert row["user_id"] == 9
    assert row["error_message"] == "Path '/plex/downloads/torrents/complete/readarr/...' is not accessible from Shelfmark's container"

