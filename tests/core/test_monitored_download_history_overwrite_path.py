from __future__ import annotations

from shelfmark.core.models import DownloadTask
from shelfmark.core import monitored_downloads


class _FakeMonitoredDB:
    def __init__(self, previous_match: dict | None):
        self.previous_match = previous_match
        self.inserts: list[dict] = []
        self.file_upserts: list[dict] = []

    def get_monitored_book_file_match(self, **_: object) -> dict | None:
        return self.previous_match

    def insert_monitored_book_download_history(self, **kwargs: object) -> None:
        self.inserts.append(dict(kwargs))

    def upsert_monitored_book_file(self, **kwargs: object) -> None:
        self.file_upserts.append(dict(kwargs))


def _build_task(*, content_type: str) -> DownloadTask:
    return DownloadTask(
        task_id="task-1",
        source="direct_download",
        title="Book",
        content_type=content_type,
        user_id=1,
        output_args={
            "history_context": {
                "entity_id": 10,
                "provider": "hardcover",
                "provider_book_id": "book-1",
                "downloaded_filename": "book.epub",
            }
        },
        download_path="/books/ebooks/book.epub",
    )


def test_record_download_history_does_not_mark_cross_content_type_overwrite(monkeypatch):
    fake_db = _FakeMonitoredDB(previous_match={
        "path": "/audiobooks/book.m4b",
        "file_type": "m4b",
        "ext": "m4b",
    })
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_db)
    monkeypatch.setattr(monitored_downloads, "_infer_monitored_match_content_type", lambda **_: "audiobook")

    task = _build_task(content_type="ebook")
    monitored_downloads._record_download_history(task)

    assert len(fake_db.inserts) == 1
    assert fake_db.inserts[0].get("overwritten_path") is None


def test_record_download_history_marks_same_content_type_overwrite(monkeypatch):
    fake_db = _FakeMonitoredDB(previous_match={
        "path": "/books/ebooks/book-old.epub",
        "file_type": "epub",
        "ext": "epub",
    })
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_db)
    monkeypatch.setattr(monitored_downloads, "_infer_monitored_match_content_type", lambda **_: "ebook")

    task = _build_task(content_type="ebook")
    monitored_downloads._record_download_history(task)

    assert len(fake_db.inserts) == 1
    assert fake_db.inserts[0].get("overwritten_path") == "/books/ebooks/book-old.epub"


def test_record_download_history_upserts_file_match_for_ebook(monkeypatch):
    """Completing an ebook download should immediately register the file for availability."""
    fake_db = _FakeMonitoredDB(previous_match=None)
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_db)

    task = _build_task(content_type="ebook")
    monitored_downloads._record_download_history(task)

    assert len(fake_db.file_upserts) == 1
    upsert = fake_db.file_upserts[0]
    assert upsert["path"] == "/books/ebooks/book.epub"
    assert upsert["file_type"] == "ebook"
    assert upsert["ext"] == "epub"
    assert upsert["confidence"] == 1.0
    assert upsert["source"] == "download"
    assert upsert["entity_id"] == 10
    assert upsert["provider"] == "hardcover"
    assert upsert["provider_book_id"] == "book-1"


def test_record_download_history_upserts_file_match_for_audiobook(monkeypatch):
    """Completing an audiobook download should register with file_type='audiobook'."""
    fake_db = _FakeMonitoredDB(previous_match=None)
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_db)

    task = DownloadTask(
        task_id="task-2",
        source="audiobookshelf",
        title="Book",
        content_type="audiobook",
        user_id=1,
        output_args={
            "history_context": {
                "entity_id": 10,
                "provider": "hardcover",
                "provider_book_id": "book-1",
                "downloaded_filename": "book.m4b",
            }
        },
        download_path="/audiobooks/book.m4b",
    )
    monitored_downloads._record_download_history(task)

    assert len(fake_db.file_upserts) == 1
    assert fake_db.file_upserts[0]["file_type"] == "audiobook"
    assert fake_db.file_upserts[0]["ext"] == "m4b"


def test_flush_deferred_history_upserts_file_match(monkeypatch):
    """Deferred history flush should also register the file match."""
    fake_db = _FakeMonitoredDB(previous_match=None)
    monkeypatch.setattr(monitored_downloads, "_user_db", fake_db)

    # Simulate deferred state
    kwargs = {
        "user_ids": [1],
        "entity_id": 10,
        "provider": "hardcover",
        "provider_book_id": "book-1",
        "downloaded_at": "2026-01-01T00:00:00Z",
        "source": "direct_download",
        "source_display_name": "Direct Download",
    }
    with monitored_downloads._deferred_lock:
        monitored_downloads._deferred_history["task-flush"] = dict(kwargs)

    monitored_downloads._flush_deferred_history("task-flush", "/books/ebooks/flushed.epub")

    assert len(fake_db.inserts) == 1
    assert len(fake_db.file_upserts) == 1
    assert fake_db.file_upserts[0]["path"] == "/books/ebooks/flushed.epub"
    assert fake_db.file_upserts[0]["file_type"] == "ebook"
