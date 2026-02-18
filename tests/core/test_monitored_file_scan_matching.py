from __future__ import annotations

import importlib
import uuid
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


def test_scan_files_prefers_subtitle_variant_on_equal_base_title_match(main_module, client, tmp_path: Path):
    user = main_module.user_db.create_user(username=f"reader-{uuid.uuid4().hex[:8]}", role="user")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    author_dir = tmp_path / "CasualFarmer" / "Beware of Chicken"
    author_dir.mkdir(parents=True, exist_ok=True)
    ebook_file = author_dir / "Beware of Chicken 4 - CasualFarmer (2024).epub"
    ebook_file.write_bytes(b"dummy")

    main_module.user_db.set_user_settings(user["id"], {"MONITORED_EBOOK_ROOTS": [str(tmp_path)]})

    entity = main_module.user_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id=f"author-{uuid.uuid4().hex[:8]}",
        name="CasualFarmer",
        settings={"ebook_author_dir": str(author_dir)},
    )

    plain_book_id = f"plain-{uuid.uuid4().hex[:8]}"
    subtitle_book_id = f"sub-{uuid.uuid4().hex[:8]}"

    main_module.user_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id=plain_book_id,
        title="Beware of Chicken 4",
        authors="CasualFarmer",
        publish_year=2024,
    )
    main_module.user_db.upsert_monitored_book(
        user_id=user["id"],
        entity_id=entity["id"],
        provider="hardcover",
        provider_book_id=subtitle_book_id,
        title="Beware of Chicken 4: A Xianxia Cultivation Novel",
        authors="CasualFarmer",
        publish_year=2024,
        series_name="Beware of Chicken",
        series_position=4,
    )

    response = client.post(f"/api/monitored/{entity['id']}/scan-files")

    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("matched"), payload

    match_rows = payload["matched"]
    row = next((m for m in match_rows if str(m.get("path")) == str(ebook_file)), None)
    assert row is not None, payload

    assert row["match"]["provider"] == "hardcover"
    assert row["match"]["provider_book_id"] == subtitle_book_id
