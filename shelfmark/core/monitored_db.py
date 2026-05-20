"""SQLite database for monitored entities, books, and file tracking.

This module manages the monitored_* tables in the same users.db file as UserDB.
It operates as an independent connection — no coupling to UserDB at runtime.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

_CREATE_MONITORED_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS monitored_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    provider TEXT,
    provider_id TEXT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    settings_json TEXT NOT NULL DEFAULT '{}',
    last_checked_at TIMESTAMP,
    last_error TEXT,
    sync_status TEXT NOT NULL DEFAULT 'idle',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, kind, provider, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_monitored_entities_user_kind
ON monitored_entities (user_id, kind, created_at DESC);

CREATE TABLE IF NOT EXISTS monitored_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
    provider TEXT,
    provider_book_id TEXT,
    title TEXT NOT NULL,
    authors TEXT,
    publish_year INTEGER,
    release_date TEXT,
    description TEXT,
    isbn_13 TEXT,
    isbn_10 TEXT,
    isbns TEXT,
    asins TEXT,
    pages INTEGER,
    cached_tags TEXT,
    cover_url TEXT,
    series_name TEXT,
    series_position REAL,
    series_count INTEGER,
    all_series TEXT,
    language TEXT,
    rating REAL,
    ratings_count INTEGER,
    readers_count INTEGER,
    state TEXT NOT NULL DEFAULT 'discovered',
    monitor_ebook INTEGER NOT NULL DEFAULT 1,
    monitor_audiobook INTEGER NOT NULL DEFAULT 1,
    monitor_locked INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    ebook_last_search_status TEXT,
    audiobook_last_search_status TEXT,
    ebook_last_search_at TIMESTAMP,
    audiobook_last_search_at TIMESTAMP,
    release_date_checked_at TIMESTAMP,
    release_date_manual INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, provider, provider_book_id)
);

CREATE INDEX IF NOT EXISTS idx_monitored_books_entity_state
ON monitored_books (entity_id, state, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS monitored_book_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
    provider TEXT,
    provider_book_id TEXT,
    path TEXT NOT NULL,
    ext TEXT,
    file_type TEXT,
    source TEXT NOT NULL DEFAULT 'filesystem',
    size_bytes INTEGER,
    mtime TIMESTAMP,
    confidence REAL,
    match_reason TEXT,
    evidence_json TEXT,
    manual_override INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, path, source),
    UNIQUE(entity_id, provider, provider_book_id, file_type, source)
);

CREATE INDEX IF NOT EXISTS idx_monitored_book_files_entity
ON monitored_book_files (entity_id, updated_at DESC);

-- User-rejected (file, book) attribution pairs. When the user detaches an
-- attribution via "Fix match → Detach", the (entity, source, path, book) tuple
-- is recorded here so future syncs won't re-attribute the same file to the
-- same book. Other books may still be considered for this file.
CREATE TABLE IF NOT EXISTS monitored_file_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_book_id TEXT NOT NULL,
    rejected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, source, path, provider, provider_book_id)
);

CREATE INDEX IF NOT EXISTS idx_monitored_file_rejections_lookup
ON monitored_file_rejections (entity_id, source, path);

CREATE TABLE IF NOT EXISTS monitored_book_download_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_book_id TEXT NOT NULL,
    downloaded_at TIMESTAMP NOT NULL,
    source TEXT,
    source_display_name TEXT,
    title_after_rename TEXT,
    match_score REAL,
    downloaded_filename TEXT,
    final_path TEXT,
    overwritten_path TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_monitored_book_download_history_lookup
ON monitored_book_download_history (entity_id, provider, provider_book_id, downloaded_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS monitored_book_attempt_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_book_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    attempted_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL,
    source TEXT,
    source_id TEXT,
    release_title TEXT,
    match_score REAL,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_monitored_book_attempt_history_lookup
ON monitored_book_attempt_history (entity_id, provider, provider_book_id, content_type, attempted_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS monitored_pending_releases (
    pending_key TEXT PRIMARY KEY,
    release_data TEXT NOT NULL,
    user_id INTEGER,
    entity_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_book_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    destination_override TEXT,
    file_organization_override TEXT,
    template_override TEXT,
    series_name TEXT,
    series_position REAL,
    current_source_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    post_process_retries INTEGER NOT NULL DEFAULT 0,
    session_id TEXT,
    task_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS monitored_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    entity_id INTEGER REFERENCES monitored_entities(id) ON DELETE SET NULL,
    book_provider TEXT,
    book_provider_id TEXT,
    book_title TEXT,
    author_name TEXT,
    content_type TEXT,
    source TEXT,
    source_display_name TEXT,
    status TEXT,
    message TEXT,
    metadata_json TEXT,
    session_id TEXT,
    user_id INTEGER,
    book_cover_url TEXT,
    author_photo_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_monitored_events_entity
ON monitored_events (entity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_events_book
ON monitored_events (entity_id, book_provider, book_provider_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_events_type
ON monitored_events (event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_events_date
ON monitored_events (created_at DESC);
"""

# Index that depends on the session_id column. Created AFTER the lazy ALTER below
# so that databases predating the session_id column still initialize cleanly.
_SESSION_ID_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_monitored_events_session
ON monitored_events (session_id, created_at ASC)
WHERE session_id IS NOT NULL;
"""


def _migrate_monitored_book_files_v2(conn: sqlite3.Connection) -> None:
    """Add source column and update UNIQUE constraint to include source.

    The original UNIQUE(entity_id, provider, provider_book_id, file_type) constraint
    does not allow one filesystem record AND one ABS record per book.  We recreate the
    table with the updated constraint UNIQUE(…, file_type, source) so both can coexist.
    Idempotent — safe to call multiple times.
    """
    existing_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(monitored_book_files)").fetchall()
    }
    if not existing_cols:
        return  # table doesn't exist yet; CREATE TABLE will handle it
    if "source" in existing_cols:
        return  # already migrated
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE monitored_book_files RENAME TO monitored_book_files_old;
        CREATE TABLE monitored_book_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
            provider TEXT,
            provider_book_id TEXT,
            path TEXT NOT NULL,
            ext TEXT,
            file_type TEXT,
            source TEXT NOT NULL DEFAULT 'filesystem',
            size_bytes INTEGER,
            mtime TIMESTAMP,
            confidence REAL,
            match_reason TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_id, path),
            UNIQUE(entity_id, provider, provider_book_id, file_type, source)
        );
        INSERT INTO monitored_book_files
            (id, entity_id, provider, provider_book_id, path, ext, file_type, source,
             size_bytes, mtime, confidence, match_reason, created_at, updated_at)
        SELECT id, entity_id, provider, provider_book_id, path, ext, file_type,
               'filesystem', size_bytes, mtime, confidence, match_reason, created_at, updated_at
        FROM monitored_book_files_old;
        DROP TABLE monitored_book_files_old;
        PRAGMA foreign_keys = ON;
    """)
    conn.commit()


def _migrate_monitored_book_files_v3(conn: sqlite3.Connection) -> None:
    """Widen UNIQUE(entity_id, path) to UNIQUE(entity_id, path, source).

    Allows a filesystem record and an ABS record for the exact same file path to
    coexist (relevant when ABS and shelfmark share the same mounted filesystem).
    Idempotent — safe to call multiple times.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='monitored_book_files'"
    ).fetchone()
    if not row:
        return  # table doesn't exist yet; CREATE TABLE will handle it
    schema: str = row[0] if isinstance(row, tuple) else row["sql"]
    if "entity_id, path, source" in schema:
        return  # already migrated
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE monitored_book_files RENAME TO monitored_book_files_v3_old;
        CREATE TABLE monitored_book_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES monitored_entities(id) ON DELETE CASCADE,
            provider TEXT,
            provider_book_id TEXT,
            path TEXT NOT NULL,
            ext TEXT,
            file_type TEXT,
            source TEXT NOT NULL DEFAULT 'filesystem',
            size_bytes INTEGER,
            mtime TIMESTAMP,
            confidence REAL,
            match_reason TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_id, path, source),
            UNIQUE(entity_id, provider, provider_book_id, file_type, source)
        );
        INSERT INTO monitored_book_files
            (id, entity_id, provider, provider_book_id, path, ext, file_type, source,
             size_bytes, mtime, confidence, match_reason, created_at, updated_at)
        SELECT id, entity_id, provider, provider_book_id, path, ext, file_type, source,
               size_bytes, mtime, confidence, match_reason, created_at, updated_at
        FROM monitored_book_files_v3_old;
        DROP TABLE monitored_book_files_v3_old;
        PRAGMA foreign_keys = ON;
    """)
    conn.commit()


def _migrate_monitored_book_files_v4(conn: sqlite3.Connection) -> None:
    """Add evidence_json column for v2 structured attribution evidence.

    Additive only — no data loss. Stores per-row evidence vector (positives,
    penalties, position_votes, etc.) as JSON, surfaced in the BookDetailsModal
    "Why?" UI.
    """
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(monitored_book_files)").fetchall()
    }
    if "evidence_json" in cols:
        return  # already migrated
    conn.execute("ALTER TABLE monitored_book_files ADD COLUMN evidence_json TEXT")
    conn.commit()


def _migrate_monitored_book_files_v5(conn: sqlite3.Connection) -> None:
    """Wipe pre-unified-matcher attributions so next scan/sync repopulates under v2.

    Targets rows from sources whose attribution algorithms changed shape (now
    all running through the unified ``pick_best_attribution`` evaluator):

      * ``filesystem`` — was the v2 path matcher; now also runs through the
        same unified evaluator with the same shape (re-derivation is identical
        for identifier-matched rows, fresh for everything else).
      * ``audiobookshelf`` / ``booklore`` — were three-phase decision trees
        with the old looser title-confirmation threshold. Now metadata-fed
        into the unified evaluator. Wholesale wipe; sync will re-derive.

    ``download`` rows survive — those were attributed at download time with
    a known target book; the path is canonical by construction.

    Gated by ``PRAGMA user_version`` in ``initialize()`` so it runs exactly
    once per DB.
    """
    cur = conn.execute(
        """
        DELETE FROM monitored_book_files
        WHERE source IN ('filesystem', 'audiobookshelf', 'booklore')
        """
    )
    deleted = cur.rowcount or 0
    conn.commit()
    if deleted:
        logger.info(
            "Migration v5: wiped %d legacy attribution rows; next scan/sync "
            "will repopulate under the unified matcher.", deleted,
        )


def _migrate_monitored_book_files_v6(conn: sqlite3.Connection) -> None:
    """Add manual_override column.

    When set to 1, the row was set by a user via the "Fix match" UI; the
    scanner and integration sync loops must not overwrite it. Centralised
    enforcement lives in ``upsert_monitored_book_file``.
    """
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(monitored_book_files)").fetchall()
    }
    if "manual_override" in cols:
        return
    conn.execute(
        "ALTER TABLE monitored_book_files ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()


def _migrate_monitored_events_backfill_thumbnails(conn: sqlite3.Connection) -> None:
    """Backfill ``book_cover_url`` / ``author_photo_url`` from current state.

    Pre-existing event rows have NULL for these columns. Re-derive from
    ``monitored_entities.settings_json`` and ``monitored_books.cover_url``
    where the referenced rows still exist. Idempotent; safe to skip when
    those rows have already been unmonitored.
    """
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='monitored_events'").fetchone():
        return
    conn.execute(
        """
        UPDATE monitored_events
        SET author_photo_url = (
            SELECT json_extract(settings_json, '$.photo_url')
            FROM monitored_entities
            WHERE monitored_entities.id = monitored_events.entity_id
        )
        WHERE author_photo_url IS NULL AND entity_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE monitored_events
        SET book_cover_url = (
            SELECT cover_url FROM monitored_books
            WHERE monitored_books.entity_id = monitored_events.entity_id
              AND monitored_books.provider = monitored_events.book_provider
              AND monitored_books.provider_book_id = monitored_events.book_provider_id
        )
        WHERE book_cover_url IS NULL
          AND entity_id IS NOT NULL
          AND book_provider IS NOT NULL
          AND book_provider_id IS NOT NULL
        """
    )
    conn.commit()


def _migrate_monitored_events_backfill_user_id(conn: sqlite3.Connection) -> None:
    """Backfill ``user_id`` on events recorded before the column was populated.

    Events are now scoped by ``user_id`` for multi-user isolation. Older rows
    may have NULL because ``_record_sync_success/_record_sync_failure`` did not
    pass it through. Recover via the FK to ``monitored_entities`` where possible.
    Idempotent.
    """
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='monitored_events'").fetchone():
        return
    conn.execute(
        """
        UPDATE monitored_events
        SET user_id = (
            SELECT user_id FROM monitored_entities
            WHERE monitored_entities.id = monitored_events.entity_id
        )
        WHERE user_id IS NULL AND entity_id IS NOT NULL
        """
    )
    conn.commit()


def _migrate_monitored_events_cascade_to_set_null(conn: sqlite3.Connection) -> None:
    """Change monitored_events.entity_id FK from CASCADE to SET NULL.

    Events should be preserved as an audit trail when an author is deleted.
    Idempotent — safe to call multiple times.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='monitored_events'"
    ).fetchone()
    if not row:
        return  # table doesn't exist yet; CREATE TABLE will handle it
    schema: str = row[0] if isinstance(row, tuple) else row["sql"]
    if "ON DELETE SET NULL" in schema:
        return  # already migrated
    if "ON DELETE CASCADE" not in schema:
        return  # unexpected schema, skip
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE monitored_events RENAME TO monitored_events_old;
        CREATE TABLE monitored_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_id INTEGER REFERENCES monitored_entities(id) ON DELETE SET NULL,
            book_provider TEXT,
            book_provider_id TEXT,
            book_title TEXT,
            author_name TEXT,
            content_type TEXT,
            source TEXT,
            source_display_name TEXT,
            status TEXT,
            message TEXT,
            metadata_json TEXT,
            user_id INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO monitored_events SELECT * FROM monitored_events_old;
        DROP TABLE monitored_events_old;
        CREATE INDEX IF NOT EXISTS idx_monitored_events_entity ON monitored_events (entity_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_monitored_events_book ON monitored_events (entity_id, book_provider, book_provider_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_monitored_events_type ON monitored_events (event_type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_monitored_events_date ON monitored_events (created_at DESC);
        PRAGMA foreign_keys = ON;
    """)
    conn.commit()


class MonitoredDB:
    """Thread-safe SQLite interface for monitored_* tables.

    Opens the same users.db file as UserDB, but manages only the monitored tables.
    UserDB.initialize() must be called first (to create the users table that
    monitored_entities references via FK).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create monitored tables if they don't exist."""
        with self._lock:
            conn = self._connect()
            try:
                _migrate_monitored_book_files_v2(conn)
                _migrate_monitored_book_files_v3(conn)
                conn.executescript(_CREATE_MONITORED_TABLES_SQL)
                _migrate_monitored_book_files_v4(conn)
                # v5 + v6: gated by PRAGMA user_version so they run exactly
                # once per DB. v5 wipes legacy attributions; v6 adds the
                # manual_override column.
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                if user_version < 6:
                    if user_version < 5:
                        _migrate_monitored_book_files_v5(conn)
                    _migrate_monitored_book_files_v6(conn)
                    conn.execute("PRAGMA user_version = 6")
                    conn.commit()
                _migrate_monitored_events_cascade_to_set_null(conn)
                _migrate_monitored_events_backfill_user_id(conn)
                # Lazy migration: column is in CREATE TABLE for new DBs but
                # existing tables need ALTER TABLE (CREATE IF NOT EXISTS is a no-op).
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN release_date_checked_at TIMESTAMP")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN release_date_manual INTEGER NOT NULL DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN saved_monitor_ebook INTEGER")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN saved_monitor_audiobook INTEGER")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_books ADD COLUMN monitor_locked INTEGER NOT NULL DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                # One-time backfill: prior versions of upsert_monitored_book
                # set hidden=1 without zeroing the monitor flags, leaving rows
                # with hidden=1 AND monitor_*=1 — visible as "Wanted" on the
                # author page but excluded from the Monitored Books tab.
                conn.execute(
                    """
                    UPDATE monitored_books
                    SET saved_monitor_ebook = COALESCE(saved_monitor_ebook, monitor_ebook),
                        saved_monitor_audiobook = COALESCE(saved_monitor_audiobook, monitor_audiobook),
                        monitor_ebook = 0,
                        monitor_audiobook = 0
                    WHERE hidden = 1 AND (monitor_ebook = 1 OR monitor_audiobook = 1)
                    """
                )
                conn.commit()
                try:
                    conn.execute("ALTER TABLE monitored_events ADD COLUMN session_id TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_events ADD COLUMN book_cover_url TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_events ADD COLUMN author_photo_url TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_pending_releases ADD COLUMN session_id TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE monitored_pending_releases ADD COLUMN task_id TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                # Create the session_id index now that the column is guaranteed to exist.
                conn.executescript(_SESSION_ID_INDEX_SQL)
                conn.commit()
                # v7: backfill snapshotted thumbnails on pre-existing event rows.
                if user_version < 7:
                    _migrate_monitored_events_backfill_thumbnails(conn)
                    conn.execute("PRAGMA user_version = 7")
                    conn.commit()
            finally:
                conn.close()

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _user_id_clause(user_ids: list[int]) -> tuple[str, list[int]]:
        """Build a ``user_id IN (?, …)`` fragment and matching params."""
        if not user_ids:
            return "1 = 0", []  # always-false — no user ids to match
        if len(user_ids) == 1:
            return "user_id = ?", list(user_ids)
        placeholders = ",".join("?" * len(user_ids))
        return f"user_id IN ({placeholders})", list(user_ids)

    def _check_entity_access(
        self,
        conn: sqlite3.Connection,
        entity_id: int,
        user_ids: list[int],
    ) -> dict | None:
        """Return entity row if owned by any of *user_ids*, else None."""
        clause, params = self._user_id_clause(user_ids)
        row = conn.execute(
            f"SELECT * FROM monitored_entities WHERE id = ? AND {clause}",
            (entity_id, *params),
        ).fetchone()
        return dict(row) if row else None

    def _entity_exists(
        self,
        conn: sqlite3.Connection,
        entity_id: int,
        user_ids: list[int],
    ) -> bool:
        """Return True if entity is owned by any of *user_ids*."""
        clause, params = self._user_id_clause(user_ids)
        return bool(
            conn.execute(
                f"SELECT 1 FROM monitored_entities WHERE id = ? AND {clause}",
                (entity_id, *params),
            ).fetchone()
        )

    # =========================================================================
    # Entity CRUD
    # =========================================================================

    def prune_monitored_book_files(
        self,
        *,
        entity_id: int,
        keep_paths: list[str],
        source: str = "filesystem",
    ) -> int:
        """Delete monitored_book_files for an entity that are not in keep_paths.

        Only rows matching *source* are considered, so filesystem scans never
        prune audiobookshelf records and vice versa.

        Returns the number of deleted rows.
        """

        keep_paths = [p for p in keep_paths if isinstance(p, str) and p]
        with self._lock:
            conn = self._connect()
            try:
                if not keep_paths:
                    cur = conn.execute(
                        """
                        DELETE FROM monitored_book_files
                        WHERE entity_id = ?
                          AND source = ?
                        """,
                        (entity_id, source),
                    )
                    conn.commit()
                    return int(cur.rowcount or 0)

                # SQLite bind variable limit is 999; use a temp table for large sets.
                _SQLITE_BIND_LIMIT = 900
                if len(keep_paths) > _SQLITE_BIND_LIMIT:
                    conn.execute(
                        "CREATE TEMP TABLE IF NOT EXISTS _prune_keep_paths (path TEXT PRIMARY KEY)"
                    )
                    conn.execute("DELETE FROM _prune_keep_paths")
                    conn.executemany(
                        "INSERT OR IGNORE INTO _prune_keep_paths VALUES (?)",
                        [(p,) for p in keep_paths],
                    )
                    cur = conn.execute(
                        """
                        DELETE FROM monitored_book_files
                        WHERE entity_id = ?
                          AND source = ?
                          AND path NOT IN (SELECT path FROM _prune_keep_paths)
                        """,
                        (entity_id, source),
                    )
                    conn.execute("DROP TABLE IF EXISTS _prune_keep_paths")
                else:
                    placeholders = ",".join(["?"] * len(keep_paths))
                    cur = conn.execute(
                        f"""
                        DELETE FROM monitored_book_files
                        WHERE entity_id = ?
                          AND source = ?
                          AND path NOT IN ({placeholders})
                        """,
                        (entity_id, source, *keep_paths),
                    )
                conn.commit()
                return int(cur.rowcount or 0)
            finally:
                conn.close()

    def list_monitored_entities(self, *, user_ids: list[int]) -> List[Dict[str, Any]]:
        """List monitored entities visible to any of *user_ids*."""
        conn = self._connect()
        try:
            clause, params = self._user_id_clause(user_ids)
            rows = conn.execute(
                f"""
                SELECT *
                FROM monitored_entities
                WHERE {clause}
                ORDER BY created_at DESC, id DESC
                """,
                params,
            ).fetchall()
            results: List[Dict[str, Any]] = []
            for row in rows:
                payload = dict(row)
                raw_settings = payload.get("settings_json")
                if isinstance(raw_settings, str) and raw_settings:
                    try:
                        payload["settings"] = json.loads(raw_settings)
                    except Exception:
                        payload["settings"] = {}
                else:
                    payload["settings"] = {}
                payload.pop("settings_json", None)
                results.append(payload)
            return results
        finally:
            conn.close()

    def get_monitored_entity(self, *, user_ids: list[int], entity_id: int) -> Optional[Dict[str, Any]]:
        """Return a monitored entity by id (visible to any of *user_ids*)."""
        conn = self._connect()
        try:
            clause, params = self._user_id_clause(user_ids)
            row = conn.execute(
                f"SELECT * FROM monitored_entities WHERE id = ? AND {clause}",
                (entity_id, *params),
            ).fetchone()
            if not row:
                return None
            payload = dict(row)
            raw_settings = payload.get("settings_json")
            if isinstance(raw_settings, str) and raw_settings:
                try:
                    payload["settings"] = json.loads(raw_settings)
                except Exception:
                    payload["settings"] = {}
            else:
                payload["settings"] = {}
            payload.pop("settings_json", None)
            return payload
        finally:
            conn.close()

    @staticmethod
    def _serialize_json(value: Any, field: str) -> Optional[str]:
        if value is None:
            return None
        try:
            return json.dumps(value)
        except Exception as e:
            raise ValueError(f"Failed to serialize {field} to JSON: {e}") from e

    def create_monitored_entity(
        self,
        *,
        user_id: int | None,
        kind: str,
        provider: str | None,
        provider_id: str | None,
        name: str,
        enabled: bool = True,
        settings: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Create or return existing monitored entity."""
        normalized_kind = (kind or "").strip().lower()
        if normalized_kind not in {"author", "book"}:
            raise ValueError("kind must be 'author' or 'book'")

        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("name is required")

        settings_json = self._serialize_json(settings or {}, "settings") or "{}"
        enabled_value = 1 if enabled else 0

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO monitored_entities (
                        user_id,
                        kind,
                        provider,
                        provider_id,
                        name,
                        enabled,
                        settings_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, kind, provider, provider_id)
                    DO UPDATE SET
                        name=excluded.name,
                        enabled=excluded.enabled,
                        settings_json=excluded.settings_json,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        user_id,
                        normalized_kind,
                        provider,
                        provider_id,
                        normalized_name,
                        enabled_value,
                        settings_json,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT *
                    FROM monitored_entities
                    WHERE user_id = ? AND kind = ? AND provider IS ? AND provider_id IS ?
                    """,
                    (user_id, normalized_kind, provider, provider_id),
                ).fetchone()
                if not row:
                    raise ValueError("Failed to create monitored entity")
                payload = dict(row)
                payload["settings"] = json.loads(payload.get("settings_json") or "{}")
                payload.pop("settings_json", None)
                return payload
            finally:
                conn.close()

    def delete_monitored_entity(self, *, user_ids: list[int], entity_id: int) -> bool:
        """Delete a monitored entity owned by any of *user_ids*."""
        with self._lock:
            conn = self._connect()
            try:
                clause, params = self._user_id_clause(user_ids)
                cursor = conn.execute(
                    f"DELETE FROM monitored_entities WHERE id = ? AND {clause}",
                    (entity_id, *params),
                )
                conn.commit()
                return bool(cursor.rowcount)
            finally:
                conn.close()

    def update_monitored_entity_check(self, *, entity_id: int, last_error: str | None) -> None:
        """Update last_checked_at and last_error for a monitored entity."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE monitored_entities
                    SET last_checked_at=CURRENT_TIMESTAMP, last_error=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (last_error, entity_id),
                )
                conn.commit()
            finally:
                conn.close()

    def update_entity_sync_status(self, entity_id: int, status: str) -> None:
        """Update sync_status for a monitored entity."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE monitored_entities SET sync_status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, entity_id),
                )
                conn.commit()
            finally:
                conn.close()

    def find_entity_id_by_provider(
        self,
        *,
        user_id: int,
        kind: str,
        provider: str | None,
        provider_id: str | None,
    ) -> int | None:
        """Return the entity id for a given (user_id, kind, provider, provider_id), or None."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM monitored_entities WHERE user_id = ? AND kind = ? AND provider IS ? AND provider_id IS ?",
                (user_id, kind, provider, provider_id),
            ).fetchone()
            return int(row["id"]) if row else None
        finally:
            conn.close()

    def reassign_entity_owner(
        self,
        *,
        entity_id: int,
        old_user_id: int,
        new_user_id: int,
    ) -> bool:
        """Move a monitored entity from *old_user_id* to *new_user_id*.

        If *new_user_id* already owns the same (kind, provider, provider_id),
        merges child rows into the existing entity and deletes the old one.
        Returns True if the reassignment (or merge) succeeded.
        """
        if old_user_id == new_user_id:
            return True
        with self._lock:
            conn = self._connect()
            try:
                src = conn.execute(
                    "SELECT * FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, old_user_id),
                ).fetchone()
                if not src:
                    return False
                src = dict(src)

                # Check if new_user_id already owns the same entity
                existing = conn.execute(
                    "SELECT id FROM monitored_entities WHERE user_id = ? AND kind = ? AND provider IS ? AND provider_id IS ?",
                    (new_user_id, src["kind"], src["provider"], src["provider_id"]),
                ).fetchone()

                if existing:
                    # Merge: re-point child rows to existing entity, then delete old
                    target_id = existing["id"]
                    for table in (
                        "monitored_books",
                        "monitored_book_files",
                        "monitored_book_download_history",
                        "monitored_book_attempt_history",
                    ):
                        conn.execute(
                            f"UPDATE OR IGNORE {table} SET entity_id = ? WHERE entity_id = ?",
                            (target_id, entity_id),
                        )
                        # Delete any rows that conflicted (already exist under target)
                        conn.execute(
                            f"DELETE FROM {table} WHERE entity_id = ?",
                            (entity_id,),
                        )
                    conn.execute(
                        "DELETE FROM monitored_entities WHERE id = ?",
                        (entity_id,),
                    )
                else:
                    # Simple reassign
                    conn.execute(
                        "UPDATE monitored_entities SET user_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_user_id, entity_id),
                    )
                conn.commit()
                return True
            finally:
                conn.close()

    # =========================================================================
    # Book CRUD
    # =========================================================================

    def list_monitored_books(self, *, user_ids: list[int], entity_id: int) -> List[Dict[str, Any]] | None:
        """List discovered books for a monitored entity (None if entity not found)."""
        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return None
            rows = conn.execute(
                """
                SELECT *
                FROM monitored_books
                WHERE entity_id = ?
                ORDER BY first_seen_at DESC, id DESC
                """,
                (entity_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_best_book_cover_urls_batch(
        self, *, user_ids: list[int], entity_ids: list[int]
    ) -> dict[int, dict[str, str]]:
        """Return best cover_url (with provider info) for multiple entities in one query.

        Uses a window function to pick the most popular book per entity.
        Returns a dict mapping entity_id → {cover_url, provider, provider_book_id}.
        Omits entities that have no books with a cover_url.
        Only returns results for entities owned by the given user_ids.
        """
        if not entity_ids:
            return {}
        uid_clause, uid_params = self._user_id_clause(user_ids)
        entity_placeholders = ",".join("?" * len(entity_ids))
        conn = self._connect()
        try:
            # Filter entity_ids to only those owned by the user
            owned_rows = conn.execute(
                f"""
                SELECT id FROM monitored_entities
                WHERE id IN ({entity_placeholders}) AND {uid_clause}
                """,
                [*entity_ids, *uid_params],
            ).fetchall()
            owned_ids = [r["id"] for r in owned_rows]
            if not owned_ids:
                return {}
            placeholders = ",".join("?" * len(owned_ids))
            rows = conn.execute(
                f"""
                SELECT entity_id, cover_url, provider, provider_book_id FROM (
                    SELECT entity_id, cover_url, provider, provider_book_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY entity_id
                               ORDER BY COALESCE(readers_count, -1) DESC,
                                        COALESCE(ratings_count, -1) DESC,
                                        COALESCE(rating, -1) DESC,
                                        title ASC
                           ) AS rn
                    FROM monitored_books
                    WHERE entity_id IN ({placeholders})
                      AND cover_url IS NOT NULL AND cover_url != ''
                ) WHERE rn = 1
                """,
                owned_ids,
            ).fetchall()
            return {
                row["entity_id"]: {
                    "cover_url": row["cover_url"],
                    "provider": row["provider"] or "",
                    "provider_book_id": row["provider_book_id"] or "",
                }
                for row in rows
            }
        finally:
            conn.close()

    def set_monitored_book_monitor_flags(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        monitor_ebook: bool | None = None,
        monitor_audiobook: bool | None = None,
        hidden: bool | None = None,
        monitor_locked: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update per-format monitor flags for a monitored book.

        Returns a dict with the effective ``monitor_ebook`` and
        ``monitor_audiobook`` values after the update, or *None* if
        nothing was changed (entity not found / no matching row).

        When *hidden=True*: saves current monitor flags to
        ``saved_monitor_*`` columns, then zeros them out.
        When *hidden=False*: restores from ``saved_monitor_*``, then
        clears the saved columns.
        """

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return None

                updates: list[str] = []
                params: list[Any] = []

                if hidden is not None:
                    # Read current row to save / restore monitor flags.
                    row = conn.execute(
                        """
                        SELECT monitor_ebook, monitor_audiobook,
                               saved_monitor_ebook, saved_monitor_audiobook
                        FROM monitored_books
                        WHERE entity_id = ? AND provider = ? AND provider_book_id = ?
                        """,
                        (entity_id, provider, provider_book_id),
                    ).fetchone()
                    if row is None:
                        return None

                    updates.append("hidden = ?")
                    params.append(1 if hidden else 0)

                    if hidden:
                        # Save current flags, then zero them out
                        updates.append("saved_monitor_ebook = ?")
                        params.append(int(row["monitor_ebook"] or 0))
                        updates.append("saved_monitor_audiobook = ?")
                        params.append(int(row["monitor_audiobook"] or 0))
                        monitor_ebook = False
                        monitor_audiobook = False
                    else:
                        # Unhide: restore previously-saved flags (fall
                        # back to 1 for books hidden before the migration)
                        updates.append("saved_monitor_ebook = NULL")
                        updates.append("saved_monitor_audiobook = NULL")
                        saved_eb = row["saved_monitor_ebook"]
                        saved_ab = row["saved_monitor_audiobook"]
                        if monitor_ebook is None:
                            monitor_ebook = bool(saved_eb) if saved_eb is not None else True
                        if monitor_audiobook is None:
                            monitor_audiobook = bool(saved_ab) if saved_ab is not None else True

                if monitor_ebook is not None:
                    updates.append("monitor_ebook = ?")
                    params.append(1 if monitor_ebook else 0)
                if monitor_audiobook is not None:
                    updates.append("monitor_audiobook = ?")
                    params.append(1 if monitor_audiobook else 0)
                if monitor_locked is not None:
                    updates.append("monitor_locked = ?")
                    params.append(1 if monitor_locked else 0)
                if not updates:
                    return None

                params.extend([entity_id, provider, provider_book_id])
                cur = conn.execute(
                    f"""
                    UPDATE monitored_books
                    SET {", ".join(updates)}
                    WHERE entity_id = ?
                      AND provider = ?
                      AND provider_book_id = ?
                    """,
                    params,
                )
                conn.commit()
                if not cur.rowcount:
                    return None

                result: dict[str, Any] = {}
                if monitor_ebook is not None:
                    result["monitor_ebook"] = 1 if monitor_ebook else 0
                if monitor_audiobook is not None:
                    result["monitor_audiobook"] = 1 if monitor_audiobook else 0
                return result
            finally:
                conn.close()

    def unlock_all_monitor_flags(self, *, entity_id: int) -> None:
        """Reset monitor_locked to 0 for all books in an entity.

        Called when the user changes the entity's monitor mode so the
        new mode applies to all books, overriding previous manual overrides.
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE monitored_books SET monitor_locked = 0 WHERE entity_id = ?",
                    (entity_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def update_book_release_date(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        release_date: str | None,
        audible_asin: str | None = None,
    ) -> bool:
        """Update release_date (and optionally ASIN) for a monitored book."""

        # Derive publish_year from release_date
        publish_year: int | None = None
        if release_date and isinstance(release_date, str):
            try:
                publish_year = int(release_date[:4])
            except (ValueError, TypeError):
                pass

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return False

                updates = ["release_date = ?", "publish_year = ?", "release_date_checked_at = CURRENT_TIMESTAMP", "release_date_manual = ?"]
                params: list[Any] = [release_date, publish_year, 1 if release_date else 0]

                if audible_asin:
                    updates.append("asins = ?")
                    params.append(json.dumps([audible_asin]))

                params.extend([entity_id, provider, provider_book_id])
                cur = conn.execute(
                    f"""
                    UPDATE monitored_books
                    SET {", ".join(updates)}
                    WHERE entity_id = ?
                      AND provider = ?
                      AND provider_book_id = ?
                    """,
                    params,
                )
                conn.commit()
                return bool(cur.rowcount)
            finally:
                conn.close()

    def mark_release_date_checked(
        self,
        *,
        entity_id: int,
        provider: str,
        provider_book_id: str,
    ) -> None:
        """Mark that we attempted a release-date lookup (no ownership check — internal use)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """UPDATE monitored_books
                       SET release_date_checked_at = CURRENT_TIMESTAMP
                       WHERE entity_id = ? AND provider = ? AND provider_book_id = ?""",
                    (entity_id, provider, provider_book_id),
                )
                conn.commit()
            finally:
                conn.close()

    def set_monitored_book_search_status(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        content_type: str,
        status: str | None,
        searched_at: str,
    ) -> bool:
        """Persist last monitored-search status per format for a monitored book."""

        ct = (content_type or "").strip().lower()
        if ct not in {"ebook", "audiobook"}:
            return False
        status_col = "ebook_last_search_status" if ct == "ebook" else "audiobook_last_search_status"
        at_col = "ebook_last_search_at" if ct == "ebook" else "audiobook_last_search_at"

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return False

                cur = conn.execute(
                    f"""
                    UPDATE monitored_books
                    SET {status_col} = ?,
                        {at_col} = ?
                    WHERE entity_id = ?
                      AND provider = ?
                      AND provider_book_id = ?
                    """,
                    (status, searched_at, entity_id, provider, provider_book_id),
                )
                conn.commit()
                return bool(cur.rowcount)
            finally:
                conn.close()

    def insert_monitored_book_attempt_history(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        content_type: str,
        attempted_at: str,
        status: str,
        source: str | None = None,
        source_id: str | None = None,
        release_title: str | None = None,
        match_score: float | None = None,
        error_message: str | None = None,
    ) -> None:
        """Insert a monitored auto-search attempt row."""

        ct = (content_type or "").strip().lower()
        if ct not in {"ebook", "audiobook"}:
            return
        if not provider or not provider_book_id:
            return

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):

                    return

                conn.execute(
                    """
                    INSERT INTO monitored_book_attempt_history (
                        entity_id,
                        provider,
                        provider_book_id,
                        content_type,
                        attempted_at,
                        status,
                        source,
                        source_id,
                        release_title,
                        match_score,
                        error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        provider,
                        provider_book_id,
                        ct,
                        attempted_at,
                        status,
                        source,
                        source_id,
                        release_title,
                        match_score,
                        error_message,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_monitored_book_attempt_history(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        """List monitored auto-search attempt rows for a monitored book."""

        safe_limit = max(1, min(int(limit or 50), 200))
        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return None

            rows = conn.execute(
                """
                SELECT
                    id,
                    entity_id,
                    provider,
                    provider_book_id,
                    content_type,
                    attempted_at,
                    status,
                    source,
                    source_id,
                    release_title,
                    match_score,
                    error_message,
                    created_at
                FROM monitored_book_attempt_history
                WHERE entity_id = ?
                  AND provider = ?
                  AND provider_book_id = ?
                ORDER BY attempted_at DESC, id DESC
                LIMIT ?
                """,
                (entity_id, provider, provider_book_id, safe_limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_monitored_failed_candidate_source_ids(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        content_type: str,
    ) -> set[tuple[str, str]]:
        """Return permanently failed candidate keys for suppression."""

        ct = (content_type or "").strip().lower()
        if ct not in {"ebook", "audiobook"}:
            return set()

        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return set()

            rows = conn.execute(
                """
                SELECT source, source_id
                FROM monitored_book_attempt_history
                WHERE entity_id = ?
                  AND provider = ?
                  AND provider_book_id = ?
                  AND content_type = ?
                  AND status = 'download_failed'
                  AND source IS NOT NULL
                  AND source_id IS NOT NULL
                """,
                (entity_id, provider, provider_book_id, ct),
            ).fetchall()
            out: set[tuple[str, str]] = set()
            for row in rows:
                src = str(row["source"] or "").strip()
                src_id = str(row["source_id"] or "").strip()
                if src and src_id:
                    out.add((src, src_id))
            return out
        finally:
            conn.close()

    def search_monitored_author_books(
        self,
        *,
        user_ids: list[int],
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search monitored author book entries."""

        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return []

        safe_limit = max(1, min(int(limit or 20), 100))
        # Escape LIKE wildcard characters in user input
        escaped_query = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped_query}%"
        prefix_like = f"{escaped_query}%"

        if not user_ids:
            return []

        # Build user_id IN clause with named params for this named-param query
        uid_names = [f":uid_{i}" for i in range(len(user_ids))]
        uid_clause = f"me.user_id IN ({','.join(uid_names)})" if len(user_ids) > 1 else f"me.user_id = {uid_names[0]}"
        uid_binds = {f"uid_{i}": uid for i, uid in enumerate(user_ids)}

        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT
                    me.id AS entity_id,
                    me.name AS author_name,
                    me.provider AS author_provider,
                    me.provider_id AS author_provider_id,
                    json_extract(me.settings_json, '$.photo_url') AS author_photo_url,
                    mb.provider AS book_provider,
                    mb.provider_book_id AS book_provider_id,
                    mb.title AS book_title,
                    mb.authors AS book_authors,
                    mb.publish_year AS publish_year,
                    mb.cover_url AS cover_url,
                    mb.series_name AS series_name,
                    mb.series_position AS series_position,
                    mb.series_count AS series_count
                FROM monitored_entities me
                JOIN monitored_books mb
                  ON mb.entity_id = me.id
                WHERE {uid_clause}
                  AND me.kind = 'author'
                  AND (
                    LOWER(mb.title) LIKE :like ESCAPE '\\'
                    OR LOWER(COALESCE(mb.authors, '')) LIKE :like ESCAPE '\\'
                    OR LOWER(COALESCE(mb.series_name, '')) LIKE :like ESCAPE '\\'
                    OR LOWER(me.name) LIKE :like ESCAPE '\\'
                  )
                ORDER BY
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like ESCAPE '\\' THEN 0 ELSE 1 END,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like ESCAPE '\\' THEN LOWER(COALESCE(mb.series_name, '')) END ASC,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like ESCAPE '\\' THEN CASE WHEN mb.series_position IS NULL THEN 1 ELSE 0 END END ASC,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like ESCAPE '\\' THEN mb.series_position END ASC,
                    CASE WHEN LOWER(mb.title) LIKE :prefix_like ESCAPE '\\' THEN 0 ELSE 1 END,
                    CASE WHEN LOWER(me.name) LIKE :prefix_like ESCAPE '\\' THEN 0 ELSE 1 END,
                    mb.first_seen_at DESC,
                    mb.id DESC
                LIMIT :limit
                """,
                {
                    **uid_binds,
                    "like": like,
                    "prefix_like": prefix_like,
                    "limit": safe_limit,
                },
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def upsert_monitored_book(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str | None,
        provider_book_id: str | None,
        title: str,
        authors: str | None,
        publish_year: Any = None,
        release_date: str | None = None,
        description: str | None = None,
        isbn_13: str | None = None,
        isbn_10: str | None = None,
        isbns: list | None = None,
        asins: list | None = None,
        pages: int | None = None,
        cached_tags: Any = None,
        cover_url: str | None = None,
        series_name: str | None = None,
        series_position: float | None = None,
        series_count: int | None = None,
        all_series: list | None = None,
        language: str | None = None,
        rating: float | None = None,
        ratings_count: int | None = None,
        readers_count: int | None = None,
        state: str = "discovered",
        hidden: bool | None = None,
    ) -> None:
        """Upsert a monitored book snapshot."""
        normalized_title = (title or "").strip()
        if not normalized_title:
            return

        normalized_state = (state or "").strip().lower() or "discovered"
        if normalized_state not in {"discovered", "ignored", "removed_from_provider"}:
            normalized_state = "discovered"

        year_value: int | None = None
        if publish_year is not None:
            try:
                year_value = int(publish_year)
            except (TypeError, ValueError):
                year_value = None

        release_date_value: str | None = None
        if release_date is not None:
            candidate = str(release_date).strip()
            if candidate:
                release_date_value = candidate

        description_value: str | None = None
        if description is not None:
            candidate = str(description).strip()
            if candidate:
                description_value = candidate

        language_value: str | None = None
        if language is not None:
            candidate = str(language).strip().lower()
            if candidate:
                language_value = candidate

        rating_value: float | None = None
        if rating is not None:
            try:
                rating_value = float(rating)
            except (TypeError, ValueError):
                rating_value = None

        ratings_count_value: int | None = None
        if ratings_count is not None:
            try:
                ratings_count_value = int(ratings_count)
            except (TypeError, ValueError):
                ratings_count_value = None

        readers_count_value: int | None = None
        if readers_count is not None:
            try:
                readers_count_value = int(readers_count)
            except (TypeError, ValueError):
                readers_count_value = None

        pages_value: int | None = None
        if pages is not None:
            try:
                pages_value = int(pages)
            except (TypeError, ValueError):
                pages_value = None

        isbns_json: str | None = None
        if isbns is not None:
            try:
                isbns_json = json.dumps(isbns)
            except Exception:
                isbns_json = None

        asins_json: str | None = None
        if asins is not None:
            try:
                asins_json = json.dumps(asins)
            except Exception:
                asins_json = None

        all_series_json: str | None = None
        if all_series is not None:
            try:
                all_series_json = json.dumps(all_series)
            except Exception:
                all_series_json = None

        cached_tags_json: str | None = None
        if cached_tags is not None:
            if isinstance(cached_tags, str):
                cached_tags_json = cached_tags
            else:
                try:
                    cached_tags_json = json.dumps(cached_tags)
                except Exception:
                    cached_tags_json = None

        with self._lock:
            conn = self._connect()
            try:
                # Ensure entity exists and is scoped correctly.
                if not self._entity_exists(conn, entity_id, user_ids):
                    raise ValueError("Monitored entity not found")

                conn.execute(
                    """
                    INSERT INTO monitored_books (
                        entity_id,
                        provider,
                        provider_book_id,
                        title,
                        authors,
                        publish_year,
                        release_date,
                        description,
                        isbn_13,
                        isbn_10,
                        isbns,
                        asins,
                        pages,
                        cached_tags,
                        cover_url,
                        series_name,
                        series_position,
                        series_count,
                        all_series,
                        language,
                        rating,
                        ratings_count,
                        readers_count,
                        state,
                        hidden
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, provider, provider_book_id)
                    DO UPDATE SET
                        title=excluded.title,
                        authors=excluded.authors,
                        publish_year=CASE
                            WHEN monitored_books.release_date_manual = 1 AND monitored_books.publish_year IS NOT NULL THEN monitored_books.publish_year
                            ELSE COALESCE(excluded.publish_year, monitored_books.publish_year)
                        END,
                        release_date=CASE
                            WHEN monitored_books.release_date_manual = 1 AND monitored_books.release_date IS NOT NULL THEN monitored_books.release_date
                            ELSE COALESCE(excluded.release_date, monitored_books.release_date)
                        END,
                        description=COALESCE(excluded.description, monitored_books.description),
                        isbn_13=COALESCE(excluded.isbn_13, monitored_books.isbn_13),
                        isbn_10=COALESCE(excluded.isbn_10, monitored_books.isbn_10),
                        isbns=COALESCE(excluded.isbns, monitored_books.isbns),
                        asins=COALESCE(excluded.asins, monitored_books.asins),
                        pages=COALESCE(excluded.pages, monitored_books.pages),
                        cached_tags=COALESCE(excluded.cached_tags, monitored_books.cached_tags),
                        cover_url=COALESCE(excluded.cover_url, monitored_books.cover_url),
                        series_name=COALESCE(NULLIF(excluded.series_name, ''), monitored_books.series_name),
                        series_position=COALESCE(excluded.series_position, monitored_books.series_position),
                        series_count=COALESCE(excluded.series_count, monitored_books.series_count),
                        all_series=COALESCE(excluded.all_series, monitored_books.all_series),
                        language=COALESCE(NULLIF(excluded.language, ''), monitored_books.language),
                        rating=excluded.rating,
                        ratings_count=excluded.ratings_count,
                        readers_count=excluded.readers_count,
                        hidden=CASE
                            WHEN excluded.hidden = 1 THEN 1
                            ELSE monitored_books.hidden
                        END,
                        state=CASE
                            WHEN monitored_books.state = 'ignored' THEN 'ignored'
                            WHEN monitored_books.state = 'removed_from_provider'
                                 AND excluded.state = 'discovered' THEN 'discovered'
                            ELSE excluded.state
                        END
                    """,
                    (
                        entity_id,
                        provider,
                        provider_book_id,
                        normalized_title,
                        authors,
                        year_value,
                        release_date_value,
                        description_value,
                        isbn_13,
                        isbn_10,
                        isbns_json,
                        asins_json,
                        pages_value,
                        cached_tags_json,
                        cover_url,
                        series_name,
                        series_position,
                        series_count,
                        all_series_json,
                        language_value,
                        rating_value,
                        ratings_count_value,
                        readers_count_value,
                        normalized_state,
                        1 if hidden else 0,
                    ),
                )
                if hidden:
                    # Keep monitor flags consistent with hidden=1 (matches the
                    # explicit hide path in set_monitored_book_monitor_flags).
                    # Save current flags to saved_monitor_* the first time we
                    # hide so an unhide can restore them.
                    conn.execute(
                        """
                        UPDATE monitored_books
                        SET saved_monitor_ebook = COALESCE(saved_monitor_ebook, monitor_ebook),
                            saved_monitor_audiobook = COALESCE(saved_monitor_audiobook, monitor_audiobook),
                            monitor_ebook = 0,
                            monitor_audiobook = 0
                        WHERE entity_id = ?
                          AND provider = ?
                          AND provider_book_id = ?
                          AND hidden = 1
                          AND (monitor_ebook = 1 OR monitor_audiobook = 1)
                        """,
                        (entity_id, provider, provider_book_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def delete_monitored_book(
        self,
        *,
        entity_id: int,
        provider: str,
        provider_book_id: str,
    ) -> bool:
        """Delete a monitored book and cascade-delete its file matches and history records."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    DELETE FROM monitored_book_files
                    WHERE entity_id = ? AND provider = ? AND provider_book_id = ?
                    """,
                    (entity_id, provider, provider_book_id),
                )
                conn.execute(
                    """
                    DELETE FROM monitored_book_download_history
                    WHERE entity_id = ? AND provider = ? AND provider_book_id = ?
                    """,
                    (entity_id, provider, provider_book_id),
                )
                conn.execute(
                    """
                    DELETE FROM monitored_book_attempt_history
                    WHERE entity_id = ? AND provider = ? AND provider_book_id = ?
                    """,
                    (entity_id, provider, provider_book_id),
                )
                cursor = conn.execute(
                    """
                    DELETE FROM monitored_books
                    WHERE entity_id = ? AND provider = ? AND provider_book_id = ?
                    """,
                    (entity_id, provider, provider_book_id),
                )
                conn.commit()
                return bool(cursor.rowcount)
            finally:
                conn.close()

    def bulk_update_monitored_book_state(
        self,
        *,
        entity_id: int,
        keys: list[tuple[str, str]],
        state: str,
    ) -> int:
        """Batch-update state for multiple books. *keys* is a list of (provider, provider_book_id).

        Returns the total number of rows updated.
        """
        if not keys:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                updated = 0
                # SQLite has a variable limit (~999); batch in chunks of 400
                for i in range(0, len(keys), 400):
                    chunk = keys[i:i + 400]
                    or_clauses = " OR ".join(
                        ["(provider = ? AND provider_book_id = ?)"] * len(chunk)
                    )
                    params: list = [state, entity_id]
                    for prov, pid in chunk:
                        params.extend([prov, pid])
                    params.append(state)
                    cursor = conn.execute(
                        f"""
                        UPDATE monitored_books
                        SET state = ?
                        WHERE entity_id = ?
                          AND ({or_clauses})
                          AND state != ?
                        """,
                        params,
                    )
                    updated += cursor.rowcount or 0
                conn.commit()
                return updated
            finally:
                conn.close()

    # =========================================================================
    # File tracking
    # =========================================================================

    def upsert_monitored_book_file(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str | None,
        provider_book_id: str | None,
        path: str,
        ext: str | None,
        file_type: str | None,
        size_bytes: int | None,
        mtime: str | None,
        confidence: float | None,
        match_reason: str | None,
        source: str = "filesystem",
        evidence_json: str | None = None,
        manual_override: bool = False,
    ) -> None:
        """Upsert a matched file for a monitored book.

        Constraints:
        - one row per (entity_id, path)
        - one row per (entity_id, provider, provider_book_id, file_type, source)

        Manual-override guard:
        - When the row at (entity_id, path, source) currently has
          ``manual_override = 1`` and the caller is NOT setting it (i.e.
          ``manual_override=False``), the upsert is a no-op. Scanners /
          integration sync loops therefore leave user-confirmed rows alone.
        - When ``manual_override=True``, the row is written through normally
          and the flag is set on insert/update — this is the "Fix match"
          endpoint's path.
        """

        normalized_path = (path or "").strip()
        if not normalized_path:
            return

        manual_flag = 1 if manual_override else 0

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    raise ValueError("Monitored entity not found")

                # Manual-override guard: scanners and integration sync loops
                # must not clobber a user-confirmed attribution. Skip when an
                # existing row at this (entity_id, path, source) is marked
                # manual_override AND the caller isn't setting it themselves.
                if not manual_override:
                    existing = conn.execute(
                        """
                        SELECT manual_override FROM monitored_book_files
                        WHERE entity_id = ? AND path = ? AND source = ?
                        """,
                        (entity_id, normalized_path, source),
                    ).fetchone()
                    if existing and (existing["manual_override"] if isinstance(existing, sqlite3.Row) else existing[0]):
                        return

                if provider and provider_book_id and file_type:
                    # Prevent path-key collisions when re-pointing an existing
                    # (provider, provider_book_id, file_type, source) match to a new file path.
                    conn.execute(
                        """
                        DELETE FROM monitored_book_files
                        WHERE entity_id = ?
                          AND path = ?
                          AND source = ?
                          AND NOT (
                            provider = ?
                            AND provider_book_id = ?
                            AND file_type = ?
                          )
                        """,
                        (entity_id, normalized_path, source, provider, provider_book_id, file_type),
                    )

                    conn.execute(
                        """
                        INSERT INTO monitored_book_files (
                            entity_id,
                            provider,
                            provider_book_id,
                            path,
                            ext,
                            file_type,
                            source,
                            size_bytes,
                            mtime,
                            confidence,
                            match_reason,
                            evidence_json,
                            manual_override,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(entity_id, provider, provider_book_id, file_type, source)
                        DO UPDATE SET
                            path=excluded.path,
                            ext=excluded.ext,
                            size_bytes=excluded.size_bytes,
                            mtime=excluded.mtime,
                            confidence=excluded.confidence,
                            match_reason=excluded.match_reason,
                            evidence_json=excluded.evidence_json,
                            manual_override=excluded.manual_override,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            entity_id,
                            provider,
                            provider_book_id,
                            normalized_path,
                            ext,
                            file_type,
                            source,
                            size_bytes,
                            mtime,
                            confidence,
                            match_reason,
                            evidence_json,
                            manual_flag,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO monitored_book_files (
                            entity_id,
                            provider,
                            provider_book_id,
                            path,
                            ext,
                            file_type,
                            source,
                            size_bytes,
                            mtime,
                            confidence,
                            match_reason,
                            evidence_json,
                            manual_override,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(entity_id, path, source)
                        DO UPDATE SET
                            provider=excluded.provider,
                            provider_book_id=excluded.provider_book_id,
                            ext=excluded.ext,
                            file_type=excluded.file_type,
                            size_bytes=excluded.size_bytes,
                            mtime=excluded.mtime,
                            confidence=excluded.confidence,
                            match_reason=excluded.match_reason,
                            evidence_json=excluded.evidence_json,
                            manual_override=excluded.manual_override,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            entity_id,
                            provider,
                            provider_book_id,
                            normalized_path,
                            ext,
                            file_type,
                            source,
                            size_bytes,
                            mtime,
                            confidence,
                            match_reason,
                            evidence_json,
                            manual_flag,
                        ),
                    )

                conn.commit()
            finally:
                conn.close()

    def delete_monitored_book_file_by_id(
        self, *, user_ids: list[int], entity_id: int, file_id: int,
    ) -> bool:
        """Delete a monitored_book_files row by id.

        Returns True if a row was deleted. Verifies the row belongs to the
        given entity AND that the entity is visible to ``user_ids`` — so a
        caller can't delete another user's row by guessing file_ids.
        """
        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return False
                cur = conn.execute(
                    "DELETE FROM monitored_book_files WHERE id = ? AND entity_id = ?",
                    (file_id, entity_id),
                )
                conn.commit()
                return (cur.rowcount or 0) > 0
            finally:
                conn.close()

    def record_file_rejection(
        self, *, user_ids: list[int], entity_id: int, source: str, path: str,
        provider: str, provider_book_id: str,
    ) -> bool:
        """Record that the user rejected attributing ``(source, path)`` to
        ``(provider, provider_book_id)``. Idempotent — duplicate rejections
        update ``rejected_at`` but don't error.

        Returns True on insert/update, False if the entity isn't visible to
        ``user_ids`` or all required fields are empty.
        """
        path = (path or "").strip()
        source = (source or "").strip()
        provider = (provider or "").strip()
        provider_book_id = (provider_book_id or "").strip()
        if not (path and source and provider and provider_book_id):
            return False
        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return False
                conn.execute(
                    """
                    INSERT INTO monitored_file_rejections
                        (entity_id, source, path, provider, provider_book_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, source, path, provider, provider_book_id)
                    DO UPDATE SET rejected_at = CURRENT_TIMESTAMP
                    """,
                    (entity_id, source, path, provider, provider_book_id),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def list_file_rejections_for_entity(
        self, *, entity_id: int,
    ) -> set[tuple[str, str, str, str]]:
        """Return the set of rejected ``(source, path, provider, provider_book_id)``
        tuples for an entity. Sync code consults this to skip re-attributing
        a file to a book the user has previously rejected.
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT source, path, provider, provider_book_id
                    FROM monitored_file_rejections
                    WHERE entity_id = ?
                    """,
                    (entity_id,),
                ).fetchall()
                return {
                    (r["source"], r["path"], r["provider"], r["provider_book_id"])
                    for r in rows
                }
            finally:
                conn.close()

    def list_monitored_book_files(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
    ) -> list[dict[str, Any]] | None:
        """List matched files for a monitored entity (None if entity not found)."""

        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return None

            rows = conn.execute(
                """
                SELECT *
                FROM monitored_book_files
                WHERE entity_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (entity_id,),
            ).fetchall()
        finally:
            conn.close()

        stale_ids: list[int] = []
        existing_rows: list[dict[str, Any]] = []

        for row in rows:
            row_dict = dict(row)
            path = row_dict.get("path")
            file_id = row_dict.get("id")
            row_source = row_dict.get("source") or "filesystem"

            # Non-filesystem records (e.g. audiobookshelf) have remote paths
            # that won't exist locally — always treat them as present.
            if row_source != "filesystem":
                existing_rows.append(row_dict)
                continue

            path_exists = False
            if isinstance(path, str) and path.strip():
                try:
                    path_exists = Path(path).exists()
                except Exception:
                    path_exists = False

            if path_exists:
                existing_rows.append(row_dict)
            elif isinstance(file_id, int):
                stale_ids.append(file_id)

        if stale_ids:
            with self._lock:
                cleanup_conn = self._connect()
                try:
                    placeholders = ",".join(["?"] * len(stale_ids))
                    cleanup_conn.execute(
                        f"""
                        DELETE FROM monitored_book_files
                        WHERE entity_id = ?
                          AND id IN ({placeholders})
                        """,
                        (entity_id, *stale_ids),
                    )
                    cleanup_conn.commit()
                finally:
                    cleanup_conn.close()

        return existing_rows

    def get_monitored_book_file_match(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
    ) -> dict[str, Any] | None:
        """Return the most recent matched file row for a monitored book, if any."""

        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return None

            row = conn.execute(
                """
                SELECT *
                FROM monitored_book_files
                WHERE entity_id = ?
                  AND provider = ?
                  AND provider_book_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (entity_id, provider, provider_book_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # =========================================================================
    # Download history
    # =========================================================================

    def insert_monitored_book_download_history(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        downloaded_at: str,
        source: str | None,
        source_display_name: str | None,
        title_after_rename: str | None,
        match_score: float | None,
        downloaded_filename: str | None,
        final_path: str | None,
        overwritten_path: str | None,
    ) -> None:
        """Insert a monitored-book download history event."""

        if not provider or not provider_book_id:
            return

        with self._lock:
            conn = self._connect()
            try:
                if not self._entity_exists(conn, entity_id, user_ids):
                    return

                conn.execute(
                    """
                    INSERT INTO monitored_book_download_history (
                        entity_id,
                        provider,
                        provider_book_id,
                        downloaded_at,
                        source,
                        source_display_name,
                        title_after_rename,
                        match_score,
                        downloaded_filename,
                        final_path,
                        overwritten_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        provider,
                        provider_book_id,
                        downloaded_at,
                        source,
                        source_display_name,
                        title_after_rename,
                        match_score,
                        downloaded_filename,
                        final_path,
                        overwritten_path,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def list_monitored_book_download_history(
        self,
        *,
        user_ids: list[int],
        entity_id: int,
        provider: str,
        provider_book_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        """List download history entries for a monitored book."""

        safe_limit = max(1, min(int(limit or 50), 200))
        conn = self._connect()
        try:
            if not self._entity_exists(conn, entity_id, user_ids):
                return None

            rows = conn.execute(
                """
                SELECT
                    id,
                    entity_id,
                    provider,
                    provider_book_id,
                    downloaded_at,
                    source,
                    source_display_name,
                    title_after_rename,
                    match_score,
                    downloaded_filename,
                    final_path,
                    overwritten_path,
                    created_at
                FROM monitored_book_download_history
                WHERE entity_id = ?
                  AND provider = ?
                  AND provider_book_id = ?
                ORDER BY downloaded_at DESC, id DESC
                LIMIT ?
                """,
                (entity_id, provider, provider_book_id, safe_limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # =========================================================================
    # Pending Releases (persist monitored download fallback queue)
    # =========================================================================

    def upsert_pending_releases(
        self,
        *,
        pending_key: str,
        release_data_json: str,
        user_id: int | None,
        entity_id: int,
        provider: str,
        provider_book_id: str,
        content_type: str,
        destination_override: str | None = None,
        file_organization_override: str | None = None,
        template_override: str | None = None,
        series_name: str | None = None,
        series_position: float | None = None,
        current_source_id: str | None = None,
        attempts: int = 0,
        post_process_retries: int = 0,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Insert or update a pending releases record."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO monitored_pending_releases (
                        pending_key, release_data, user_id, entity_id,
                        provider, provider_book_id, content_type,
                        destination_override, file_organization_override, template_override,
                        series_name, series_position, current_source_id,
                        attempts, post_process_retries, session_id, task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pending_key) DO UPDATE SET
                        release_data = excluded.release_data,
                        current_source_id = excluded.current_source_id,
                        attempts = excluded.attempts,
                        post_process_retries = excluded.post_process_retries,
                        session_id = COALESCE(monitored_pending_releases.session_id, excluded.session_id),
                        task_id = excluded.task_id
                    """,
                    (
                        pending_key,
                        release_data_json,
                        user_id,
                        entity_id,
                        provider,
                        provider_book_id,
                        content_type,
                        destination_override,
                        file_organization_override,
                        template_override,
                        series_name,
                        series_position,
                        current_source_id,
                        attempts,
                        post_process_retries,
                        session_id,
                        task_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_pending_releases(self, pending_key: str) -> None:
        """Remove a pending releases record."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM monitored_pending_releases WHERE pending_key = ?",
                    (pending_key,),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all_pending_releases(self) -> list[dict[str, Any]]:
        """Load all pending release records (for startup recovery)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM monitored_pending_releases ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # =========================================================================
    # Monitored Events (unified history / activity log)
    # =========================================================================

    def insert_event(
        self,
        *,
        event_type: str,
        entity_id: int | None = None,
        book_provider: str | None = None,
        book_provider_id: str | None = None,
        book_title: str | None = None,
        author_name: str | None = None,
        content_type: str | None = None,
        source: str | None = None,
        source_display_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
        metadata_json: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
    ) -> int | None:
        """Insert a history event and return its id.

        Snapshots ``book_cover_url`` (from ``monitored_books.cover_url``) and
        ``author_photo_url`` (from ``monitored_entities.settings_json.photo_url``)
        at write time so the History UI keeps thumbnails even after the underlying
        author/book is unmonitored.
        """
        with self._lock:
            conn = self._connect()
            try:
                book_cover_url: str | None = None
                author_photo_url: str | None = None
                if entity_id is not None:
                    row = conn.execute(
                        "SELECT json_extract(settings_json, '$.photo_url') AS photo_url "
                        "FROM monitored_entities WHERE id = ?",
                        (entity_id,),
                    ).fetchone()
                    if row and row["photo_url"]:
                        author_photo_url = row["photo_url"]
                    if book_provider and book_provider_id:
                        book_row = conn.execute(
                            "SELECT cover_url FROM monitored_books "
                            "WHERE entity_id = ? AND provider = ? AND provider_book_id = ?",
                            (entity_id, book_provider, book_provider_id),
                        ).fetchone()
                        if book_row and book_row["cover_url"]:
                            book_cover_url = book_row["cover_url"]
                cursor = conn.execute(
                    """
                    INSERT INTO monitored_events (
                        event_type, entity_id, book_provider, book_provider_id,
                        book_title, author_name, content_type,
                        source, source_display_name, status, message,
                        metadata_json, session_id, user_id,
                        book_cover_url, author_photo_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type, entity_id, book_provider, book_provider_id,
                        book_title, author_name, content_type,
                        source, source_display_name, status, message,
                        metadata_json, session_id, user_id,
                        book_cover_url, author_photo_url,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()

    def list_events(
        self,
        *,
        user_ids: list[int],
        entity_id: int | None = None,
        book_provider: str | None = None,
        book_provider_id: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated events scoped to *user_ids*. Returns (rows, total_count)."""
        if not user_ids:
            return [], 0
        conn = self._connect()
        try:
            user_clause, user_params = self._user_id_clause(user_ids)
            conditions: list[str] = [user_clause]
            params: list[Any] = list(user_params)

            if entity_id is not None:
                conditions.append("entity_id = ?")
                params.append(entity_id)
            if book_provider is not None and book_provider_id is not None:
                conditions.append("book_provider = ? AND book_provider_id = ?")
                params.extend([book_provider, book_provider_id])
            if event_types:
                placeholders = ",".join("?" * len(event_types))
                conditions.append(f"event_type IN ({placeholders})")
                params.extend(event_types)
            if since:
                conditions.append("created_at >= ?")
                params.append(since)
            if until:
                conditions.append("created_at <= ?")
                params.append(until)

            where = f" WHERE {' AND '.join(conditions)}"

            count_row = conn.execute(
                f"SELECT COUNT(*) FROM monitored_events{where}", params,
            ).fetchone()
            total = count_row[0] if count_row else 0

            query_params = list(params)
            query_params.extend([limit, offset])
            rows = conn.execute(
                f"SELECT * FROM monitored_events{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                query_params,
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    def count_events_by_type(self, *, user_ids: list[int], since: str | None = None) -> dict[str, int]:
        """Return event counts grouped by type, scoped to *user_ids*."""
        if not user_ids:
            return {}
        conn = self._connect()
        try:
            user_clause, user_params = self._user_id_clause(user_ids)
            params: list[Any] = list(user_params)
            where = f"WHERE {user_clause}"
            if since:
                where += " AND created_at >= ?"
                params.append(since)
            rows = conn.execute(
                f"SELECT event_type, COUNT(*) as cnt FROM monitored_events {where} GROUP BY event_type",
                params,
            ).fetchall()
            return {row["event_type"]: row["cnt"] for row in rows}
        finally:
            conn.close()

    def count_sync_batches(self, *, user_ids: list[int], since: str | None = None) -> int:
        """Count distinct sync batches, scoped to *user_ids*."""
        if not user_ids:
            return 0
        conn = self._connect()
        try:
            user_clause, user_params = self._user_id_clause(user_ids)
            params: list[Any] = list(user_params)
            where = (
                f"WHERE {user_clause} "
                "AND event_type IN ('author_synced', 'author_sync_failed') "
                "AND json_extract(metadata_json, '$.batch_id') IS NOT NULL"
            )
            if since:
                where += " AND created_at >= ?"
                params.append(since)
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT json_extract(metadata_json, '$.batch_id')) as cnt
                FROM monitored_events {where}
                """,
                params,
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def delete_events(
        self,
        *,
        user_ids: list[int],
        entity_id: int | None = None,
        before: str | None = None,
    ) -> int:
        """Delete events scoped to *user_ids*. Returns number of rows deleted."""
        if not user_ids:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                user_clause, user_params = self._user_id_clause(user_ids)
                conditions: list[str] = [user_clause]
                params: list[Any] = list(user_params)
                if entity_id is not None:
                    conditions.append("entity_id = ?")
                    params.append(entity_id)
                if before:
                    conditions.append("created_at < ?")
                    params.append(before)
                where = f" WHERE {' AND '.join(conditions)}"
                cursor = conn.execute(f"DELETE FROM monitored_events{where}", params)
                conn.commit()
                return cursor.rowcount or 0
            finally:
                conn.close()

    def export_events(
        self,
        *,
        user_ids: list[int],
        entity_id: int | None = None,
        event_types: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all matching events scoped to *user_ids* (no pagination)."""
        if not user_ids:
            return []
        conn = self._connect()
        try:
            user_clause, user_params = self._user_id_clause(user_ids)
            conditions: list[str] = [user_clause]
            params: list[Any] = list(user_params)
            if entity_id is not None:
                conditions.append("entity_id = ?")
                params.append(entity_id)
            if event_types:
                placeholders = ",".join("?" * len(event_types))
                conditions.append(f"event_type IN ({placeholders})")
                params.extend(event_types)
            if since:
                conditions.append("created_at >= ?")
                params.append(since)
            if until:
                conditions.append("created_at <= ?")
                params.append(until)
            where = f" WHERE {' AND '.join(conditions)}"
            rows = conn.execute(
                f"SELECT * FROM monitored_events{where} ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

