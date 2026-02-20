from __future__ import annotations

import importlib
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user_id: str, db_user_id: int, is_admin: bool = False) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["db_user_id"] = db_user_id
        sess["is_admin"] = is_admin


def test_books_endpoint_includes_no_release_date_and_applies_monitor_modes(main_module, client, tmp_path: Path):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    ebook_dir = tmp_path / "ebooks"
    audio_dir = tmp_path / "audio"
    ebook_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    main_module.user_db.set_user_settings(
        user["id"],
        {
            "MONITORED_EBOOK_ROOTS": [str(ebook_dir)],
            "MONITORED_AUDIOBOOK_ROOTS": [str(audio_dir)],
        },
    )

    entity = main_module.user_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="Mode Tester",
        settings={
            "ebook_author_dir": str(ebook_dir),
            "audiobook_author_dir": str(audio_dir),
            "monitor_ebook_mode": "upcoming",
            "monitor_audiobook_mode": "missing",
        },
    )

    future_date = (date.today() + timedelta(days=20)).isoformat()

    main_module.user_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id="book-future",
        title="Future Book",
        authors="Author One",
        release_date=future_date,
    )
    main_module.user_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id="book-nodate",
        title="Unknown Date Book",
        authors="Author One",
        release_date=None,
    )

    scan_response = client.post(f"/api/monitored/{entity['id']}/scan-files")
    assert scan_response.status_code == 200

    books_response = client.get(f"/api/monitored/{entity['id']}/books")
    assert books_response.status_code == 200
    payload = books_response.get_json() or {}
    books = payload.get("books") or []

    by_book_id = {str(row.get("provider_book_id")): row for row in books}
    future_row = by_book_id["book-future"]
    nodate_row = by_book_id["book-nodate"]

    assert future_row.get("monitor_ebook") in (1, True)
    assert nodate_row.get("monitor_ebook") in (0, False)
    assert nodate_row.get("no_release_date") is True


def test_monitored_search_endpoint_returns_summary_for_empty_candidate_set(main_module, client, tmp_path: Path):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    ebook_dir = tmp_path / "ebooks2"
    audio_dir = tmp_path / "audio2"
    ebook_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    main_module.user_db.set_user_settings(
        user["id"],
        {
            "MONITORED_EBOOK_ROOTS": [str(ebook_dir)],
            "MONITORED_AUDIOBOOK_ROOTS": [str(audio_dir)],
        },
    )

    entity = main_module.user_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="Search Tester",
        settings={
            "ebook_author_dir": str(ebook_dir),
            "audiobook_author_dir": str(audio_dir),
            "monitor_ebook_mode": "missing",
            "monitor_audiobook_mode": "missing",
        },
    )

    main_module.user_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id="book-has-ebook",
        title="Has EBook",
        authors="Author One",
        release_date="2025-01-01",
    )
    main_module.user_db.upsert_monitored_book_file(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id="book-has-ebook",
        path=str(ebook_dir / "Has EBook.epub"),
        ext="epub",
        file_type="epub",
        size_bytes=123,
        mtime="2026-02-18T00:00:00Z",
        confidence=1.0,
        match_reason="test",
    )
    (ebook_dir / "Has EBook.epub").write_text("test", encoding="utf-8")

    response = client.post(
        f"/api/monitored/{entity['id']}/search",
        json={"content_type": "ebook"},
    )
    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("content_type") == "ebook"
    assert payload.get("total_candidates") == 0
    assert payload.get("queued") == 0
    assert payload.get("failed") == 0
