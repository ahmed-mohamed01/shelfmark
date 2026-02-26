from __future__ import annotations

import importlib
import uuid
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


def test_monitored_book_history_endpoint_returns_rows(main_module, client):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    entity = main_module.monitored_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="CasualFarmer",
        settings={},
    )

    provider = "hardcover"
    provider_book_id = f"book-{uuid.uuid4().hex[:8]}"

    main_module.monitored_db.insert_monitored_book_download_history(
        user_id=user["id"],
        entity_id=entity["id"],
        provider=provider,
        provider_book_id=provider_book_id,
        downloaded_at="2026-02-18T00:00:00Z",
        source="direct_download",
        source_display_name="Direct Download",
        title_after_rename="Harvest of Time",
        match_score=94.0,
        downloaded_filename="elantris_2011_sanderson_brandon_TOR_books.epub",
        final_path="/books/ebooks/fiction/Alastair Reynolds/Harvest of Time - Alastair Reynolds (2013).epub",
        overwritten_path="/books/ebooks/fiction/Alastair Reynolds/Harvest of Time - Alastair Reynolds (2013)_1.epub",
    )
    main_module.monitored_db.insert_monitored_book_attempt_history(
        user_id=user["id"],
        entity_id=entity["id"],
        provider=provider,
        provider_book_id=provider_book_id,
        content_type="ebook",
        attempted_at="2026-02-18T00:00:01Z",
        status="queued",
        source="direct_download",
        source_id="aa-md5-123",
        release_title="Harvest of Time",
        match_score=94.0,
    )

    response = client.get(
        f"/api/monitored/{entity['id']}/books/history",
        query_string={"provider": provider, "provider_book_id": provider_book_id, "limit": "20"},
    )

    assert response.status_code == 200
    payload = response.get_json() or {}
    rows = payload.get("history") or []
    attempts = payload.get("attempt_history") or []
    assert len(rows) >= 1
    assert len(attempts) >= 1
    row = rows[0]
    attempt = attempts[0]
    assert row["provider"] == provider
    assert row["provider_book_id"] == provider_book_id
    assert row["downloaded_filename"] == "elantris_2011_sanderson_brandon_TOR_books.epub"
    assert row["title_after_rename"] == "Harvest of Time"
    assert row["source_display_name"] == "Direct Download"
    assert row["final_path"].endswith(".epub")
    assert attempt["provider"] == provider
    assert attempt["provider_book_id"] == provider_book_id
    assert attempt["content_type"] == "ebook"
    assert attempt["status"] == "queued"


def test_monitored_book_history_endpoint_requires_provider_params(main_module, client):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    entity = main_module.monitored_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="Someone",
        settings={},
    )

    response = client.get(f"/api/monitored/{entity['id']}/books/history")
    assert response.status_code == 400
    assert response.get_json()["error"] == "provider and provider_book_id are required"


def test_record_monitored_book_attempt_endpoint_persists_and_is_returned(main_module, client):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    entity = main_module.monitored_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="Matt Dinniman",
        settings={},
    )

    provider = "hardcover"
    provider_book_id = f"book-{uuid.uuid4().hex[:8]}"
    main_module.monitored_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider=provider,
        provider_book_id=provider_book_id,
        title="Dungeon Crawler Carl, Vol. 1",
        authors="Matt Dinniman",
    )

    response = client.post(
        f"/api/monitored/{entity['id']}/books/attempt",
        json={
            "provider": provider,
            "provider_book_id": provider_book_id,
            "content_type": "ebook",
            "status": "no_match",
            "error_message": "no_release_met_auto_download_cutoff",
        },
    )
    assert response.status_code == 200
    assert (response.get_json() or {}).get("ok") is True

    history_response = client.get(
        f"/api/monitored/{entity['id']}/books/history",
        query_string={"provider": provider, "provider_book_id": provider_book_id, "limit": "20"},
    )
    assert history_response.status_code == 200
    payload = history_response.get_json() or {}
    attempts = payload.get("attempt_history") or []
    assert len(attempts) >= 1
    assert attempts[0]["status"] == "no_match"
    assert attempts[0]["provider"] == provider
    assert attempts[0]["provider_book_id"] == provider_book_id
    assert attempts[0]["error_message"] == "no_release_met_auto_download_cutoff"
