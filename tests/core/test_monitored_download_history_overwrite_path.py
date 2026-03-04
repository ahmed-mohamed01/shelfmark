from __future__ import annotations

from shelfmark.core.models import DownloadTask
from shelfmark.core import monitored_downloads


class _FakeMonitoredDB:
    def __init__(self, previous_match: dict | None):
        self.previous_match = previous_match
        self.inserts: list[dict] = []

    def get_monitored_book_file_match(self, **_: object) -> dict | None:
        return self.previous_match

    def insert_monitored_book_download_history(self, **kwargs: object) -> None:
        self.inserts.append(dict(kwargs))


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
