"""Tests for prune_monitored_book_files: manual_override=1 rows must survive
prune sweeps that wouldn't otherwise keep their path.

Regression: prior to the fix, sync would build a kept_paths list of items
it just upserted, then call prune_monitored_book_files. Rows the user had
manually attached (Fix Match) but whose path wasn't in kept_paths got
deleted, silently un-doing the user's choice.
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
def seeded(db_path: str) -> dict:
    user = UserDB(db_path).create_user(username="testuser")
    db = MonitoredDB(db_path)
    entity = db.create_monitored_entity(
        user_id=user["id"], kind="author", provider="hardcover",
        provider_id="author-1", name="Test Author",
    )
    return {"user_id": user["id"], "entity_id": entity["id"], "db": db}


def _upsert(db: MonitoredDB, *, user_id: int, entity_id: int, path: str,
            source: str, pbid: str, manual: bool = False) -> None:
    db.upsert_monitored_book_file(
        user_ids=[user_id], entity_id=entity_id,
        provider="hardcover", provider_book_id=pbid,
        path=path, ext="m4b", file_type="audiobook",
        size_bytes=None, mtime=None, confidence=1.0 if manual else 0.5,
        match_reason="manual_override" if manual else "abs_match",
        source=source, manual_override=manual,
    )


class TestPruneManualOverride:
    def test_manual_row_survives_empty_keep_paths(self, seeded: dict) -> None:
        """Empty kept_paths (e.g., integration disabled) must not delete manual rows."""
        db = seeded["db"]
        _upsert(db, user_id=seeded["user_id"], entity_id=seeded["entity_id"],
                path="/abs/manual.m4b", source="audiobookshelf", pbid="100",
                manual=True)
        _upsert(db, user_id=seeded["user_id"], entity_id=seeded["entity_id"],
                path="/abs/auto.m4b", source="audiobookshelf", pbid="200",
                manual=False)

        deleted = db.prune_monitored_book_files(
            entity_id=seeded["entity_id"], keep_paths=[], source="audiobookshelf",
        )
        assert deleted == 1  # only the auto row

        rows = db.list_monitored_book_files(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"],
        )
        assert rows is not None
        paths = {r["path"] for r in rows}
        assert "/abs/manual.m4b" in paths
        assert "/abs/auto.m4b" not in paths

    def test_manual_row_survives_unrelated_keep_paths(self, seeded: dict) -> None:
        """When kept_paths has values but the manual row's path isn't among them,
        the manual row must still survive."""
        db = seeded["db"]
        _upsert(db, user_id=seeded["user_id"], entity_id=seeded["entity_id"],
                path="/abs/manual.m4b", source="audiobookshelf", pbid="100",
                manual=True)
        _upsert(db, user_id=seeded["user_id"], entity_id=seeded["entity_id"],
                path="/abs/auto-stale.m4b", source="audiobookshelf", pbid="200",
                manual=False)

        # Sync says it kept a totally different path — would normally wipe both
        # of the above, but the manual one survives.
        deleted = db.prune_monitored_book_files(
            entity_id=seeded["entity_id"], keep_paths=["/abs/something-else.m4b"],
            source="audiobookshelf",
        )
        assert deleted == 1
        rows = db.list_monitored_book_files(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"],
        )
        assert rows is not None
        paths = {r["path"] for r in rows}
        assert "/abs/manual.m4b" in paths
        assert "/abs/auto-stale.m4b" not in paths

    def test_prune_only_affects_matching_source(self, seeded: dict, db_path: str) -> None:
        """A manual filesystem row must not be touched by an audiobookshelf prune.
        Queries the DB directly because ``list_monitored_book_files`` filters
        out filesystem rows whose path doesn't exist on disk.
        """
        import sqlite3
        db = seeded["db"]
        _upsert(db, user_id=seeded["user_id"], entity_id=seeded["entity_id"],
                path="/library/manual.epub", source="filesystem", pbid="100",
                manual=True)

        db.prune_monitored_book_files(
            entity_id=seeded["entity_id"], keep_paths=[], source="audiobookshelf",
        )
        # Raw DB check — bypass list_monitored_book_files' stale-file filtering.
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT path, source FROM monitored_book_files WHERE entity_id = ?",
            (seeded["entity_id"],),
        ).fetchall()
        assert any(r[0] == "/library/manual.epub" and r[1] == "filesystem" for r in rows)
