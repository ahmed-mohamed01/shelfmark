"""Tests for list_monitored_book_files' verify_paths parameter.

The default (False) returns rows as stored — no filesystem checks, no
deletions — so hot read routes stay off the syscall path. verify_paths=True
is the availability/correctness mode: rows whose path no longer exists are
filtered out AND pruned from the table (used by compute_book_availability
and the monitor-mode PATCH path, where a stale row would suppress
re-downloads or flip monitor flags off).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    p = str(tmp_path / "users.db")
    UserDB(p).initialize()
    MonitoredDB(p).initialize()
    return p


@pytest.fixture
def seeded(db_path: str, tmp_path: Path) -> dict:
    user = UserDB(db_path).create_user(username="testuser")
    db = MonitoredDB(db_path)
    entity = db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id="author-1",
        name="Test Author",
    )
    real_file = tmp_path / "real.m4b"
    real_file.write_bytes(b"x")
    for pbid, path in (("1", str(real_file)), ("2", str(tmp_path / "gone.m4b"))):
        db.upsert_monitored_book_file(
            user_ids=[user["id"]],
            entity_id=entity["id"],
            provider="hardcover",
            provider_book_id=pbid,
            path=path,
            ext="m4b",
            file_type="audiobook",
            size_bytes=None,
            mtime=None,
            confidence=1.0,
            match_reason="test",
        )
    return {
        "user_id": user["id"],
        "entity_id": entity["id"],
        "db": db,
        "real_path": str(real_file),
    }


class TestVerifyPaths:
    def test_default_returns_rows_without_filesystem_checks(self, seeded: dict) -> None:
        rows = seeded["db"].list_monitored_book_files(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"]
        )
        assert {r["provider_book_id"] for r in rows} == {"1", "2"}

    def test_verify_paths_filters_and_prunes_stale_rows(self, seeded: dict) -> None:
        rows = seeded["db"].list_monitored_book_files(
            user_ids=[seeded["user_id"]],
            entity_id=seeded["entity_id"],
            verify_paths=True,
        )
        assert [r["path"] for r in rows] == [seeded["real_path"]]

        # The stale row was deleted, not merely filtered: a later default
        # (non-verifying) read no longer sees it.
        rows_after = seeded["db"].list_monitored_book_files(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"]
        )
        assert {r["provider_book_id"] for r in rows_after} == {"1"}
