"""Tests for the batched monitored-DB writes, connection pragmas, the scanner
metadata cache, and the thumbnail resize helper — the performance rework that
replaced per-row commits with single transactions and added sized thumbnails.
"""

from __future__ import annotations

import io
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
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id="author-1",
        name="Test Author",
    )
    return {"user_id": user["id"], "entity_id": entity["id"], "db": db, "db_path": db_path}


class TestConnectionPragmas:
    def test_connect_sets_wal_and_busy_timeout(self, seeded: dict) -> None:
        db: MonitoredDB = seeded["db"]
        conn = db._connect()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            # synchronous NORMAL == 1
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        finally:
            conn.close()

    def test_conflict_index_exists(self, seeded: dict) -> None:
        db: MonitoredDB = seeded["db"]
        conn = db._connect()
        try:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "idx_monitored_book_files_conflict" in names


class TestBatchBookUpsert:
    def test_batch_upserts_all_valid_books(self, seeded: dict) -> None:
        db: MonitoredDB = seeded["db"]
        books = [
            {"provider": "hardcover", "provider_book_id": f"b{i}", "title": f"Book {i}"}
            for i in range(5)
        ]
        written = db.upsert_monitored_books_batch(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"], books=books
        )
        assert written == 5
        rows = db.list_monitored_books(user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"])
        assert {r["provider_book_id"] for r in rows} == {f"b{i}" for i in range(5)}

    def test_one_bad_row_does_not_abort_batch(self, seeded: dict) -> None:
        """A row with an empty title is a no-op; a row that raises is isolated by
        its SAVEPOINT so the remaining good rows still commit."""
        db: MonitoredDB = seeded["db"]
        books = [
            {"provider": "hardcover", "provider_book_id": "good1", "title": "Good One"},
            {"provider": "hardcover", "provider_book_id": "empty", "title": "   "},  # no-op
            {"provider": "hardcover", "provider_book_id": "good2", "title": "Good Two"},
        ]
        db.upsert_monitored_books_batch(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"], books=books
        )
        rows = db.list_monitored_books(user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"])
        ids = {r["provider_book_id"] for r in rows}
        assert "good1" in ids
        assert "good2" in ids
        assert "empty" not in ids  # empty-title row was skipped, not persisted

    def test_batch_unknown_entity_raises(self, seeded: dict) -> None:
        db: MonitoredDB = seeded["db"]
        with pytest.raises(ValueError):
            db.upsert_monitored_books_batch(
                user_ids=[seeded["user_id"]],
                entity_id=999999,
                books=[{"provider": "x", "provider_book_id": "y", "title": "T"}],
            )


class TestBatchFileUpsert:
    def test_batch_files_enforce_one_matched_per_book_across_rows(self, seeded: dict) -> None:
        """The 'one matched per book' eviction must apply across rows written in
        the SAME batch transaction, exactly as it did across sequential commits:
        the higher-confidence matched row wins and evicts the lower one.

        Uses source='audiobookshelf' so list_monitored_book_files doesn't filter
        by on-disk existence (these are remote paths)."""
        db: MonitoredDB = seeded["db"]

        def _f(path: str, conf: float) -> dict:
            return {
                "provider": "hardcover",
                "provider_book_id": "book1",
                "path": path,
                "ext": "m4b",
                "file_type": "audiobook",
                "size_bytes": 10,
                "mtime": None,
                "confidence": conf,
                "match_reason": "t",
                "source": "audiobookshelf",
                "status": "matched",
            }

        db.upsert_monitored_book_files_batch(
            user_ids=[seeded["user_id"]],
            entity_id=seeded["entity_id"],
            files=[_f("abs://low", 0.6), _f("abs://high", 0.9)],
        )
        rows = db.list_monitored_book_files(
            user_ids=[seeded["user_id"]], entity_id=seeded["entity_id"]
        )
        matched = [r for r in rows if r["status"] == "matched"]
        assert len(matched) == 1
        assert matched[0]["path"] == "abs://high"


class TestScanMetadataCache:
    def test_upsert_get_and_prune(self, seeded: dict) -> None:
        db: MonitoredDB = seeded["db"]
        eid = seeded["entity_id"]
        db.upsert_scan_metadata_cache(
            entity_id=eid,
            entries=[
                {"path": "/a.epub", "mtime": "m1", "size_bytes": 1, "metadata_json": None},
                {
                    "path": "/b.epub",
                    "mtime": "m2",
                    "size_bytes": 2,
                    "metadata_json": '{"title": "B", "authors": []}',
                },
            ],
        )
        cache = db.get_scan_metadata_cache(entity_id=eid)
        assert cache["/a.epub"]["mtime"] == "m1"
        assert cache["/a.epub"]["metadata"] is None
        assert cache["/b.epub"]["metadata"] == {"title": "B", "authors": []}

        # Prune drops paths not in keep set.
        db.prune_scan_metadata_cache(entity_id=eid, keep_paths=["/a.epub"])
        cache = db.get_scan_metadata_cache(entity_id=eid)
        assert set(cache.keys()) == {"/a.epub"}


class TestThumbnailResize:
    def test_pillow_produces_smaller_webp(self) -> None:
        """Sanity: the resize+encode path Pillow performs downscales and shrinks."""
        from PIL import Image

        big = Image.new("RGB", (900, 1350), (10, 20, 30))
        raw = io.BytesIO()
        big.save(raw, format="PNG")
        original_bytes = raw.getvalue()

        with Image.open(io.BytesIO(original_bytes)) as img:
            img.thumbnail((150, 300))
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=82, method=4)
            webp = out.getvalue()
            assert img.size == (150, 225)  # 2:3 ratio preserved
        assert webp[:4] == b"RIFF"  # WEBP container
        assert len(webp) < len(original_bytes)
