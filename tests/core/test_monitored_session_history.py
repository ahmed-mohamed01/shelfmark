"""Tests for the session_id-based History tab feature.

Covers:
- Schema: monitored_events.session_id and monitored_pending_releases.{session_id, task_id}
  are present on fresh DBs and added by lazy ALTER on legacy DBs.
- Recorders: every monitored_history recorder forwards session_id to insert_event,
  and record_search_started writes the right event_type/status.
- Pending round-trip: session_id and task_id survive a persist→load cycle.
- _queue_next_from_pending sets pending.task_id to the queued release's source_id.
- _record_download_event looks up session_id from in-memory pending state.
- _on_recovery_complete bridges the recovery silent-import path to monitored events.
- upsert_pending_releases COALESCEs session_id but overwrites task_id on conflict.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.core import monitored_downloads, monitored_history
from shelfmark.core.monitored_db import MonitoredDB
from shelfmark.core.user_db import UserDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Initialize a fresh users.db with both UserDB and MonitoredDB schemas."""
    p = str(tmp_path / "users.db")
    UserDB(p).initialize()
    MonitoredDB(p).initialize()
    return p


@pytest.fixture
def monitored_db(db_path: str) -> MonitoredDB:
    return MonitoredDB(db_path)


@pytest.fixture
def seeded_user_and_entity(db_path: str, monitored_db: MonitoredDB) -> dict:
    """Create a real user + monitored author entity so FK constraints don't reject inserts.

    Returns ``{"user_id": int, "entity_id": int}`` for tests that exercise the
    full insert path through monitored_events (which references monitored_entities).
    """
    user = UserDB(db_path).create_user(username="testuser")
    entity = monitored_db.create_monitored_entity(
        user_id=user["id"],
        kind="author",
        provider="hardcover",
        provider_id="author-1",
        name="Test Author",
    )
    return {"user_id": user["id"], "entity_id": entity["id"]}


@pytest.fixture(autouse=True)
def reset_monitored_state():
    """Reset module-level state in monitored_downloads between tests."""
    monitored_downloads._pending_releases.clear()
    monitored_downloads._deferred_history.clear()
    monitored_downloads._deferred_file_imported.clear()
    yield
    monitored_downloads._pending_releases.clear()
    monitored_downloads._deferred_history.clear()
    monitored_downloads._deferred_file_imported.clear()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_fresh_db_has_session_id_on_monitored_events(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(monitored_events)")]
        assert "session_id" in cols

    def test_fresh_db_has_session_id_and_task_id_on_pending_releases(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(monitored_pending_releases)")]
        assert "session_id" in cols
        assert "task_id" in cols

    def test_session_id_partial_index_created(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        idx = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='monitored_events'"
            )
        ]
        assert "idx_monitored_events_session" in idx

    def test_lazy_migration_adds_columns_to_legacy_db(self, db_path: str) -> None:
        """Pre-existing DBs without session_id/task_id columns get them added."""
        # Recreate the tables in their pre-feature shape (no session_id, no task_id)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE monitored_events RENAME TO _me_old")
        conn.execute(
            """
            CREATE TABLE monitored_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_id INTEGER REFERENCES monitored_entities(id) ON DELETE SET NULL,
                book_provider TEXT, book_provider_id TEXT, book_title TEXT,
                author_name TEXT, content_type TEXT, source TEXT,
                source_display_name TEXT, status TEXT, message TEXT,
                metadata_json TEXT, user_id INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO monitored_events (id, event_type, message) VALUES (1, 'search_queued', 'legacy row')"
        )
        conn.execute("DROP TABLE _me_old")
        conn.execute("ALTER TABLE monitored_pending_releases DROP COLUMN session_id")
        conn.execute("ALTER TABLE monitored_pending_releases DROP COLUMN task_id")
        conn.execute("DROP INDEX IF EXISTS idx_monitored_events_session")
        conn.commit()
        conn.close()

        # Re-running initialize must not raise and must add the columns
        MonitoredDB(db_path).initialize()

        conn = sqlite3.connect(db_path)
        events_cols = [r[1] for r in conn.execute("PRAGMA table_info(monitored_events)")]
        pending_cols = [r[1] for r in conn.execute("PRAGMA table_info(monitored_pending_releases)")]
        assert "session_id" in events_cols
        assert "session_id" in pending_cols
        assert "task_id" in pending_cols
        # Legacy data preserved (session_id NULL for old rows)
        row = conn.execute(
            "SELECT message, session_id FROM monitored_events WHERE id=1"
        ).fetchone()
        assert row[0] == "legacy row"
        assert row[1] is None


# ---------------------------------------------------------------------------
# insert_event / upsert_pending_releases
# ---------------------------------------------------------------------------


class TestInsertEvent:
    def test_insert_event_persists_session_id(self, monitored_db: MonitoredDB) -> None:
        sid = "sess-1"
        monitored_db.insert_event(
            event_type="search_started",
            book_title="Book",
            session_id=sid,
            user_id=42,
        )
        rows, _ = monitored_db.list_events(user_ids=[42])
        assert len(rows) == 1
        assert rows[0]["session_id"] == sid
        assert rows[0]["event_type"] == "search_started"

    def test_insert_event_session_id_defaults_to_null(self, monitored_db: MonitoredDB) -> None:
        monitored_db.insert_event(event_type="author_added", user_id=1)
        rows, _ = monitored_db.list_events(user_ids=[1])
        assert rows[0]["session_id"] is None


class TestUpsertPendingReleases:
    def _upsert(self, db: MonitoredDB, *, session_id=None, task_id=None, attempts=0):
        db.upsert_pending_releases(
            pending_key="1:hardcover:b1:ebook",
            release_data_json="[]",
            user_id=1,
            entity_id=1,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            attempts=attempts,
            session_id=session_id,
            task_id=task_id,
        )

    def test_persists_session_id_and_task_id(self, monitored_db: MonitoredDB) -> None:
        self._upsert(monitored_db, session_id="S1", task_id="T1")
        rows = monitored_db.load_all_pending_releases()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "S1"
        assert rows[0]["task_id"] == "T1"

    def test_session_id_coalesce_preserves_first_value(self, monitored_db: MonitoredDB) -> None:
        """An update without session_id must NOT clobber the original."""
        self._upsert(monitored_db, session_id="S1", task_id="T1")
        self._upsert(monitored_db, session_id=None, task_id="T2", attempts=1)
        rows = monitored_db.load_all_pending_releases()
        assert rows[0]["session_id"] == "S1"  # preserved via COALESCE
        assert rows[0]["task_id"] == "T2"  # overwritten by excluded.task_id
        assert rows[0]["attempts"] == 1


# ---------------------------------------------------------------------------
# Recorders
# ---------------------------------------------------------------------------


class _FakeRecorderDB:
    """Captures every insert_event call so recorder tests can assert the kwargs."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def insert_event(self, **kwargs: object) -> int:
        self.events.append(dict(kwargs))
        return len(self.events)


@pytest.fixture
def fake_recorder_db(monkeypatch):
    db = _FakeRecorderDB()
    monkeypatch.setattr(monitored_history, "_db", db)
    return db


class TestRecorders:
    def test_record_search_started_writes_correct_event(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_search_started(
            entity_id=10,
            book_provider="hardcover",
            book_provider_id="b1",
            book_title="Mistborn",
            content_type="ebook",
            session_id="S1",
            user_id=7,
        )
        assert len(fake_recorder_db.events) == 1
        ev = fake_recorder_db.events[0]
        assert ev["event_type"] == "search_started"
        assert ev["status"] == "info"
        assert ev["session_id"] == "S1"
        assert "Mistborn" in ev["message"]

    def test_record_download_queued_forwards_session_id(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_download_queued(
            entity_id=10, book_provider="hardcover", book_provider_id="b1",
            session_id="S1", user_id=1,
        )
        assert fake_recorder_db.events[0]["session_id"] == "S1"
        assert fake_recorder_db.events[0]["event_type"] == "download_queued"

    def test_record_download_complete_forwards_session_id(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_download_complete(
            entity_id=10, book_provider="hardcover", book_provider_id="b1",
            download_path="/lib/book.epub", session_id="S1", user_id=1,
        )
        assert fake_recorder_db.events[0]["session_id"] == "S1"
        assert fake_recorder_db.events[0]["event_type"] == "download_complete"
        assert fake_recorder_db.events[0]["status"] == "success"

    def test_record_download_failed_forwards_session_id(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_download_failed(
            entity_id=10, book_provider="hardcover", book_provider_id="b1",
            error_message="boom", session_id="S1", user_id=1,
        )
        assert fake_recorder_db.events[0]["session_id"] == "S1"
        assert fake_recorder_db.events[0]["event_type"] == "download_failed"
        assert fake_recorder_db.events[0]["status"] == "error"

    def test_record_search_result_forwards_session_id(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_search_result(
            entity_id=10, book_provider="hardcover", book_provider_id="b1",
            search_status="queued", session_id="S1", user_id=1,
        )
        assert fake_recorder_db.events[0]["session_id"] == "S1"
        assert fake_recorder_db.events[0]["event_type"] == "search_queued"

    def test_record_file_imported_forwards_session_id(self, fake_recorder_db: _FakeRecorderDB) -> None:
        monitored_history.record_file_imported(
            entity_id=10, book_provider="hardcover", book_provider_id="b1",
            final_path="/lib/book.epub", session_id="S1", user_id=1,
        )
        assert fake_recorder_db.events[0]["session_id"] == "S1"
        assert fake_recorder_db.events[0]["event_type"] == "file_imported"


# ---------------------------------------------------------------------------
# PendingDownload round-trip
# ---------------------------------------------------------------------------


class TestPendingRoundTrip:
    def test_session_id_and_task_id_persist_and_restore(
        self, monitored_db: MonitoredDB, monkeypatch
    ) -> None:
        monkeypatch.setattr(monitored_downloads, "_user_db", monitored_db)

        key = "1:hardcover:b1:ebook"
        original = monitored_downloads.PendingDownload(
            releases=[{"source_id": "T1"}],
            user_id=1,
            entity_id=1,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            session_id="S1",
            task_id="T1",
        )
        monitored_downloads._persist_pending(key, original)

        # Wipe in-memory state and reload from DB
        monitored_downloads._pending_releases.clear()
        monitored_downloads.load_pending_releases_from_db()

        restored = monitored_downloads._pending_releases[key]
        assert restored.session_id == "S1"
        assert restored.task_id == "T1"


# ---------------------------------------------------------------------------
# _queue_next_from_pending updates pending.task_id
# ---------------------------------------------------------------------------


class TestQueueNextSetsTaskId:
    def test_pending_task_id_updated_to_release_source_id(self, monkeypatch) -> None:
        # Stub user_db (called by _persist_pending and _delete_pending_from_db)
        captured_persists: list[monitored_downloads.PendingDownload] = []

        class _StubDB:
            def upsert_pending_releases(self, **kwargs):
                captured_persists.append(kwargs)
            def delete_pending_releases(self, key):
                pass
        monkeypatch.setattr(monitored_downloads, "_user_db", _StubDB())

        # Stub orchestrator.queue_release: succeed for source_id="T-NEW"
        import shelfmark.download.orchestrator as orch
        monkeypatch.setattr(orch, "queue_release", lambda release, user_id: (True, "ok"))

        # Stub record_download_queued so we don't need monitored_history's _db wired up
        import shelfmark.core.monitored_history as mh
        monkeypatch.setattr(mh, "record_download_queued", lambda **_: None)

        key = "1:hardcover:b1:ebook"
        pending = monitored_downloads.PendingDownload(
            releases=[{"source_id": "T-NEW", "title": "Book"}],
            user_id=1,
            entity_id=1,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            session_id="S1",
            task_id=None,  # no task_id yet
        )
        monitored_downloads._pending_releases[key] = pending

        success, _ = monitored_downloads._queue_next_from_pending(key)
        assert success
        # pending.task_id mirrors the queued release's source_id
        assert pending.task_id == "T-NEW"
        # Persisted with the new task_id
        assert captured_persists[-1]["task_id"] == "T-NEW"


# ---------------------------------------------------------------------------
# _record_download_event resolves session_id from in-memory pending
# ---------------------------------------------------------------------------


class TestRecordDownloadEventResolvesSession:
    def _build_task(self) -> DownloadTask:
        return DownloadTask(
            task_id="T1",
            source="direct_download",
            title="Mistborn",
            author="Brandon Sanderson",
            content_type="ebook",
            user_id=7,
            output_args={
                "history_context": {
                    "entity_id": 10,
                    "provider": "hardcover",
                    "provider_book_id": "b1",
                    "downloaded_filename": "mistborn.epub",
                }
            },
            download_path="/lib/mistborn.epub",
        )

    def test_complete_event_carries_session_id_from_pending(self, monkeypatch) -> None:
        captured: list[dict] = []

        def fake_complete(**kwargs):
            captured.append(("complete", kwargs))

        def fake_imported(**kwargs):
            captured.append(("imported", kwargs))

        def fake_failed(**kwargs):
            captured.append(("failed", kwargs))

        import shelfmark.core.monitored_history as mh
        monkeypatch.setattr(mh, "record_download_complete", fake_complete)
        monkeypatch.setattr(mh, "record_file_imported", fake_imported)
        monkeypatch.setattr(mh, "record_download_failed", fake_failed)

        key = "10:hardcover:b1:ebook"
        monitored_downloads._pending_releases[key] = monitored_downloads.PendingDownload(
            releases=[],
            user_id=7,
            entity_id=10,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            session_id="S-TEST",
            task_id="T1",
        )

        task = self._build_task()
        monitored_downloads._record_download_event(task, "complete")

        # Both download_complete and file_imported should have been emitted
        kinds = [k for k, _ in captured]
        assert "complete" in kinds
        assert "imported" in kinds
        # Both carry the same session_id
        for _, kwargs in captured:
            assert kwargs.get("session_id") == "S-TEST"

    def test_failed_event_carries_session_id_from_pending(self, monkeypatch) -> None:
        captured: list[dict] = []
        import shelfmark.core.monitored_history as mh
        monkeypatch.setattr(mh, "record_download_complete", lambda **_: None)
        monkeypatch.setattr(mh, "record_file_imported", lambda **_: None)
        monkeypatch.setattr(mh, "record_download_failed", lambda **kw: captured.append(kw))

        key = "10:hardcover:b1:ebook"
        monitored_downloads._pending_releases[key] = monitored_downloads.PendingDownload(
            releases=[],
            user_id=7,
            entity_id=10,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            session_id="S-FAIL",
        )
        task = self._build_task()
        task.status_message = "boom"
        monitored_downloads._record_download_event(task, "failed")
        assert captured and captured[0]["session_id"] == "S-FAIL"


# ---------------------------------------------------------------------------
# Deferred file_imported flush
# ---------------------------------------------------------------------------


class TestDeferredFileImportedFlush:
    """Regression tests for _flush_deferred_history's file_imported handling.

    The deferred entry must be emitted even if final_path arrives empty —
    silently dropping the popped entry would lose the timeline event.
    """

    def test_flush_emits_event_with_normal_path(
        self,
        monitored_db: MonitoredDB,
        seeded_user_and_entity: dict,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(monitored_history, "_db", monitored_db)
        monitored_downloads._deferred_file_imported["task-X"] = dict(
            entity_id=seeded_user_and_entity["entity_id"],
            book_provider="hardcover",
            book_provider_id="b1",
            book_title="Title",
            content_type="ebook",
            session_id="S1",
            user_id=seeded_user_and_entity["user_id"],
        )

        monitored_downloads._flush_deferred_history("task-X", "/lib/book.epub")

        rows, _ = monitored_db.list_events(user_ids=[seeded_user_and_entity["user_id"]])
        assert len(rows) == 1
        assert rows[0]["event_type"] == "file_imported"
        assert rows[0]["session_id"] == "S1"
        assert "task-X" not in monitored_downloads._deferred_file_imported

    def test_flush_still_emits_event_when_final_path_empty(
        self,
        monitored_db: MonitoredDB,
        seeded_user_and_entity: dict,
        monkeypatch,
    ) -> None:
        """Even if final_path is empty, the popped deferred entry must still fire."""
        monkeypatch.setattr(monitored_history, "_db", monitored_db)
        monitored_downloads._deferred_file_imported["task-X"] = dict(
            entity_id=seeded_user_and_entity["entity_id"],
            book_provider="hardcover",
            book_provider_id="b1",
            book_title="Title",
            content_type="ebook",
            session_id="S1",
            user_id=seeded_user_and_entity["user_id"],
        )

        monitored_downloads._flush_deferred_history("task-X", "")

        rows, _ = monitored_db.list_events(user_ids=[seeded_user_and_entity["user_id"]])
        assert len(rows) == 1
        assert rows[0]["event_type"] == "file_imported"
        assert rows[0]["session_id"] == "S1"
        # Empty path → metadata is None (recorder omits final_path when falsy)
        assert rows[0]["metadata_json"] is None
        assert "task-X" not in monitored_downloads._deferred_file_imported


# ---------------------------------------------------------------------------
# Recovery hook: _on_recovery_complete
# ---------------------------------------------------------------------------


class TestRecoveryHook:
    def _seed_pending(
        self,
        *,
        user_id: int,
        entity_id: int,
        task_id: str,
        session_id: str,
    ) -> str:
        key = f"{entity_id}:hardcover:b1:ebook"
        monitored_downloads._pending_releases[key] = monitored_downloads.PendingDownload(
            releases=[],
            user_id=user_id,
            entity_id=entity_id,
            provider="hardcover",
            provider_book_id="b1",
            content_type="ebook",
            session_id=session_id,
            task_id=task_id,
        )
        return key

    def test_emits_complete_and_imported_for_matching_pending(
        self,
        monitored_db: MonitoredDB,
        seeded_user_and_entity: dict,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(monitored_downloads, "_user_db", monitored_db)
        monkeypatch.setattr(monitored_history, "_db", monitored_db)

        self._seed_pending(
            user_id=seeded_user_and_entity["user_id"],
            entity_id=seeded_user_and_entity["entity_id"],
            task_id="T-RECOVER",
            session_id="S-RECOVER",
        )

        monitored_downloads._on_recovery_complete("T-RECOVER", "/lib/book.epub")

        rows, _ = monitored_db.list_events(user_ids=[seeded_user_and_entity["user_id"]])
        types = [r["event_type"] for r in rows]
        assert "download_complete" in types
        assert "file_imported" in types
        # Both events share the same session_id
        for r in rows:
            assert r["session_id"] == "S-RECOVER"
        # file_imported metadata carries the final_path
        imported = next(r for r in rows if r["event_type"] == "file_imported")
        assert imported["metadata_json"] is not None
        import json as _json
        assert _json.loads(imported["metadata_json"])["final_path"] == "/lib/book.epub"

    def test_clears_pending_after_emitting(
        self,
        monitored_db: MonitoredDB,
        seeded_user_and_entity: dict,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(monitored_downloads, "_user_db", monitored_db)
        monkeypatch.setattr(monitored_history, "_db", monitored_db)

        key = self._seed_pending(
            user_id=seeded_user_and_entity["user_id"],
            entity_id=seeded_user_and_entity["entity_id"],
            task_id="T-RECOVER",
            session_id="S-RECOVER",
        )
        # Also persist so we can verify DB row is removed
        monitored_downloads._persist_pending(key, monitored_downloads._pending_releases[key])

        monitored_downloads._on_recovery_complete("T-RECOVER", "/lib/book.epub")

        assert key not in monitored_downloads._pending_releases
        assert monitored_db.load_all_pending_releases() == []

    def test_no_op_for_unknown_task_id(
        self,
        monitored_db: MonitoredDB,
        seeded_user_and_entity: dict,
        monkeypatch,
    ) -> None:
        """A general (non-monitored) download id must not produce events or errors."""
        monkeypatch.setattr(monitored_downloads, "_user_db", monitored_db)
        monkeypatch.setattr(monitored_history, "_db", monitored_db)

        # No pending entries seeded → no monitored mapping
        monitored_downloads._on_recovery_complete("unknown-task", "/tmp/file.epub")

        rows, _ = monitored_db.list_events(user_ids=[seeded_user_and_entity["user_id"]])
        assert rows == []
