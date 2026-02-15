"""Persistent file-based metadata cache for author/book details.

Stores JSON metadata in CONFIG_DIR/cache/metadata/ to survive container
restarts.  The in-memory CacheService handles hot lookups; this layer
adds persistence so cold starts are instant for previously-fetched data.

Directory layout:
    cache/metadata/
        authors/<provider>/<id>.json
        books/<provider>/<id>.json
"""

import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Default TTL: 7 days
DEFAULT_TTL = 7 * 24 * 60 * 60


class MetadataFileCache:
    """Thread-safe file-based JSON cache with TTL."""

    def __init__(self, cache_dir: Path, ttl_seconds: int = DEFAULT_TTL):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create cache directory structure."""
        try:
            for sub in ("authors", "books"):
                (self.cache_dir / sub).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Could not create metadata cache dirs: {e}")

    def _path(self, kind: str, provider: str, item_id: str) -> Path:
        """Return file path for a cache entry."""
        safe_id = str(item_id).replace("/", "_")
        return self.cache_dir / kind / provider / f"{safe_id}.json"

    def _mem_key(self, kind: str, provider: str, item_id: str) -> str:
        return f"{kind}:{provider}:{item_id}"

    def get(self, kind: str, provider: str, item_id: str) -> Optional[Dict[str, Any]]:
        """Get cached metadata, checking memory first then disk.

        Returns the cached payload dict, or None if missing/expired.
        """
        key = self._mem_key(kind, provider, item_id)

        with self._lock:
            # Check in-memory first
            entry = self._mem.get(key)
            if entry is not None:
                if self._is_valid(entry):
                    return entry.get("data")
                del self._mem[key]

        # Check disk
        path = self._path(kind, provider, item_id)
        if not path.exists():
            return None

        try:
            entry = json.loads(path.read_text())
            if not self._is_valid(entry):
                # Expired — remove file
                try:
                    path.unlink()
                except OSError:
                    pass
                return None

            # Promote to memory
            with self._lock:
                self._mem[key] = entry
            return entry.get("data")
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Metadata cache read error for {kind}/{provider}/{item_id}: {e}")
            return None

    def set(self, kind: str, provider: str, item_id: str, data: Dict[str, Any]) -> None:
        """Store metadata to memory and disk."""
        entry = {
            "data": data,
            "cached_at": time.time(),
            "ttl": self.ttl_seconds,
        }
        key = self._mem_key(kind, provider, item_id)

        with self._lock:
            self._mem[key] = entry

        # Write to disk
        path = self._path(kind, provider, item_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry, indent=2))
        except OSError as e:
            logger.warning(f"Metadata cache write error for {kind}/{provider}/{item_id}: {e}")

    def invalidate(self, kind: str, provider: str, item_id: str) -> None:
        """Remove a cache entry from memory and disk."""
        key = self._mem_key(kind, provider, item_id)
        with self._lock:
            self._mem.pop(key, None)

        path = self._path(kind, provider, item_id)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _is_valid(self, entry: Dict[str, Any]) -> bool:
        """Check if a cache entry is still within TTL."""
        cached_at = entry.get("cached_at", 0)
        ttl = entry.get("ttl", self.ttl_seconds)
        if ttl <= 0:
            return True  # TTL 0 = forever
        return (time.time() - cached_at) < ttl

    def cleanup_expired(self) -> int:
        """Remove expired entries from memory and disk. Returns count removed."""
        removed = 0

        # Clean memory
        with self._lock:
            expired_keys = [k for k, v in self._mem.items() if not self._is_valid(v)]
            for k in expired_keys:
                del self._mem[k]
                removed += 1

        # Clean disk
        for kind_dir in self.cache_dir.iterdir():
            if not kind_dir.is_dir():
                continue
            for provider_dir in kind_dir.iterdir():
                if not provider_dir.is_dir():
                    continue
                for path in provider_dir.glob("*.json"):
                    try:
                        entry = json.loads(path.read_text())
                        if not self._is_valid(entry):
                            path.unlink()
                            removed += 1
                    except (json.JSONDecodeError, OSError):
                        pass

        return removed

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        mem_count = len(self._mem)
        disk_count = 0
        disk_size = 0
        for kind_dir in self.cache_dir.iterdir():
            if not kind_dir.is_dir():
                continue
            for provider_dir in kind_dir.iterdir():
                if not provider_dir.is_dir():
                    continue
                for path in provider_dir.glob("*.json"):
                    disk_count += 1
                    try:
                        disk_size += path.stat().st_size
                    except OSError:
                        pass

        return {
            "memory_entries": mem_count,
            "disk_entries": disk_count,
            "disk_size_kb": round(disk_size / 1024, 1),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: Optional[MetadataFileCache] = None
_instance_lock = threading.Lock()


def get_metadata_file_cache() -> MetadataFileCache:
    """Get the singleton metadata file cache."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from shelfmark.config.env import CONFIG_DIR
                from shelfmark.core.config import config

                cache_dir = CONFIG_DIR / "cache" / "metadata"
                ttl_days = config.get("METADATA_FILE_CACHE_TTL_DAYS", 7)
                ttl_seconds = int(ttl_days) * 86400 if ttl_days else DEFAULT_TTL

                _instance = MetadataFileCache(
                    cache_dir=cache_dir,
                    ttl_seconds=ttl_seconds,
                )
                logger.debug(
                    f"Initialized metadata file cache: {cache_dir} "
                    f"(TTL {ttl_days} days)"
                )
    return _instance


def reset_metadata_file_cache() -> None:
    """Reset the singleton (for testing or config changes)."""
    global _instance
    with _instance_lock:
        _instance = None
