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
    ebook_last_search_status TEXT,
    audiobook_last_search_status TEXT,
    ebook_last_search_at TIMESTAMP,
    audiobook_last_search_at TIMESTAMP,
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, path, source),
    UNIQUE(entity_id, provider, provider_book_id, file_type, source)
);

CREATE INDEX IF NOT EXISTS idx_monitored_book_files_entity
ON monitored_book_files (entity_id, updated_at DESC);

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
                conn.commit()
            finally:
                conn.close()

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

    def list_monitored_entities(self, *, user_id: int | None) -> List[Dict[str, Any]]:
        """List monitored entities scoped to a user_id."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM monitored_entities
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (user_id,),
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

    def get_monitored_entity(self, *, user_id: int | None, entity_id: int) -> Optional[Dict[str, Any]]:
        """Return a monitored entity by id (scoped to user_id)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
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

    def delete_monitored_entity(self, *, user_id: int | None, entity_id: int) -> bool:
        """Delete a monitored entity scoped to user_id."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
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
                    "UPDATE monitored_entities SET sync_status=? WHERE id=?",
                    (status, entity_id),
                )
                conn.commit()
            finally:
                conn.close()

    # =========================================================================
    # Book CRUD
    # =========================================================================

    def list_monitored_books(self, *, user_id: int | None, entity_id: int) -> List[Dict[str, Any]] | None:
        """List discovered books for a monitored entity (None if entity not found)."""
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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

    def get_best_book_cover_url(self, *, user_id: int | None, entity_id: int) -> str | None:
        """Return the cover_url of the most popular book for a monitored entity.

        Ranked by readers_count DESC, ratings_count DESC, rating DESC, title ASC.
        Returns None if the entity has no books with a cover_url.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT cover_url FROM monitored_books
                WHERE entity_id = ?
                  AND cover_url IS NOT NULL AND cover_url != ''
                ORDER BY COALESCE(readers_count, -1) DESC,
                         COALESCE(ratings_count, -1) DESC,
                         COALESCE(rating, -1) DESC,
                         title ASC
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()
            return row["cover_url"] if row else None
        finally:
            conn.close()

    def set_monitored_book_monitor_flags(
        self,
        *,
        user_id: int | None,
        entity_id: int,
        provider: str,
        provider_book_id: str,
        monitor_ebook: bool | None = None,
        monitor_audiobook: bool | None = None,
    ) -> bool:
        """Update per-format monitor flags for a monitored book."""

        updates: list[str] = []
        params: list[Any] = []
        if monitor_ebook is not None:
            updates.append("monitor_ebook = ?")
            params.append(1 if monitor_ebook else 0)
        if monitor_audiobook is not None:
            updates.append("monitor_audiobook = ?")
            params.append(1 if monitor_audiobook else 0)
        if not updates:
            return False

        with self._lock:
            conn = self._connect()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
                    return False

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

    def set_monitored_book_search_status(
        self,
        *,
        user_id: int | None,
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
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
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
        user_id: int | None,
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
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
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
        user_id: int | None,
        entity_id: int,
        provider: str,
        provider_book_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        """List monitored auto-search attempt rows for a monitored book."""

        safe_limit = max(1, min(int(limit or 50), 200))
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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
        user_id: int | None,
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
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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
        user_id: int | None,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search monitored author book entries."""

        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return []

        safe_limit = max(1, min(int(limit or 20), 100))
        like = f"%{normalized_query}%"
        prefix_like = f"{normalized_query}%"

        conn = self._connect()
        try:
            rows = conn.execute(
                """
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
                WHERE me.user_id = :user_id
                  AND me.kind = 'author'
                  AND (
                    LOWER(mb.title) LIKE :like
                    OR LOWER(COALESCE(mb.authors, '')) LIKE :like
                    OR LOWER(COALESCE(mb.series_name, '')) LIKE :like
                    OR LOWER(me.name) LIKE :like
                  )
                ORDER BY
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like THEN 0 ELSE 1 END,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like THEN LOWER(COALESCE(mb.series_name, '')) END ASC,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like THEN CASE WHEN mb.series_position IS NULL THEN 1 ELSE 0 END END ASC,
                    CASE WHEN LOWER(COALESCE(mb.series_name, '')) LIKE :like THEN mb.series_position END ASC,
                    CASE WHEN LOWER(mb.title) LIKE :prefix_like THEN 0 ELSE 1 END,
                    CASE WHEN LOWER(me.name) LIKE :prefix_like THEN 0 ELSE 1 END,
                    mb.first_seen_at DESC,
                    mb.id DESC
                LIMIT :limit
                """,
                {
                    "user_id": user_id,
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
        user_id: int | None,
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
    ) -> None:
        """Upsert a monitored book snapshot."""
        normalized_title = (title or "").strip()
        if not normalized_title:
            return

        normalized_state = (state or "").strip().lower() or "discovered"
        if normalized_state not in {"discovered", "ignored"}:
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
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
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
                        state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, provider, provider_book_id)
                    DO UPDATE SET
                        title=excluded.title,
                        authors=excluded.authors,
                        publish_year=excluded.publish_year,
                        release_date=excluded.release_date,
                        description=COALESCE(excluded.description, monitored_books.description),
                        isbn_13=COALESCE(excluded.isbn_13, monitored_books.isbn_13),
                        isbn_10=COALESCE(excluded.isbn_10, monitored_books.isbn_10),
                        isbns=COALESCE(excluded.isbns, monitored_books.isbns),
                        asins=COALESCE(excluded.asins, monitored_books.asins),
                        pages=COALESCE(excluded.pages, monitored_books.pages),
                        cached_tags=COALESCE(excluded.cached_tags, monitored_books.cached_tags),
                        cover_url=excluded.cover_url,
                        series_name=COALESCE(NULLIF(excluded.series_name, ''), monitored_books.series_name),
                        series_position=COALESCE(excluded.series_position, monitored_books.series_position),
                        series_count=COALESCE(excluded.series_count, monitored_books.series_count),
                        all_series=COALESCE(excluded.all_series, monitored_books.all_series),
                        language=COALESCE(NULLIF(excluded.language, ''), monitored_books.language),
                        rating=excluded.rating,
                        ratings_count=excluded.ratings_count,
                        readers_count=excluded.readers_count,
                        state=excluded.state
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
                    ),
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

    # =========================================================================
    # File tracking
    # =========================================================================

    def upsert_monitored_book_file(
        self,
        *,
        user_id: int | None,
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
    ) -> None:
        """Upsert a matched file for a monitored book.

        Constraints:
        - one row per (entity_id, path)
        - one row per (entity_id, provider, provider_book_id, file_type, source)
        """

        normalized_path = (path or "").strip()
        if not normalized_path:
            return

        with self._lock:
            conn = self._connect()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
                    raise ValueError("Monitored entity not found")

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
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(entity_id, provider, provider_book_id, file_type, source)
                        DO UPDATE SET
                            path=excluded.path,
                            ext=excluded.ext,
                            size_bytes=excluded.size_bytes,
                            mtime=excluded.mtime,
                            confidence=excluded.confidence,
                            match_reason=excluded.match_reason,
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
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                        ),
                    )

                conn.commit()
            finally:
                conn.close()

    def list_monitored_book_files(
        self,
        *,
        user_id: int | None,
        entity_id: int,
    ) -> list[dict[str, Any]] | None:
        """List matched files for a monitored entity (None if entity not found)."""

        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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
                placeholders = ",".join(["?"] * len(stale_ids))
                conn.execute(
                    f"""
                    DELETE FROM monitored_book_files
                    WHERE entity_id = ?
                      AND id IN ({placeholders})
                    """,
                    (entity_id, *stale_ids),
                )
                conn.commit()

            return existing_rows
        finally:
            conn.close()

    def get_monitored_book_file_match(
        self,
        *,
        user_id: int | None,
        entity_id: int,
        provider: str,
        provider_book_id: str,
    ) -> dict[str, Any] | None:
        """Return the most recent matched file row for a monitored book, if any."""

        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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
        user_id: int | None,
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
                exists = conn.execute(
                    "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                    (entity_id, user_id),
                ).fetchone()
                if not exists:
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
        user_id: int | None,
        entity_id: int,
        provider: str,
        provider_book_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        """List download history entries for a monitored book."""

        safe_limit = max(1, min(int(limit or 50), 200))
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM monitored_entities WHERE id = ? AND user_id = ?",
                (entity_id, user_id),
            ).fetchone()
            if not exists:
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

