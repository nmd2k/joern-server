import fcntl
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from joern_server.cpg.storage import cpg_remove

from joern_server.cpg.storage import cpg_remove


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS cpg_cache (
    source_hash TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    last_used TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    extra TEXT
)"""

_KNOWN_KEYS = frozenset({"archive_path", "sample_id", "archived_at", "last_used", "size_bytes"})


class CPGRegistry:
    """
    Registry for tracking compiled Code Property Graphs (CPGs).

    Manages persistent metadata storage, lookup by source hash,
    and automatic LRU eviction based on count and disk space limits.
    """
    def __init__(self, registry_path: Path, archive_max_count: int = 100, archive_max_gb: float = 50.0, *, legacy_path: Optional[Path] = None):
        self._path = registry_path
        self._legacy_path = legacy_path
        self._lock = threading.Lock()
        self._write_lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
        self._write_lock_fd: Optional[int] = None
        self.archive_max_count = archive_max_count
        self.archive_max_gb = archive_max_gb
        self._loaded = False
        self._conn: Optional[sqlite3.Connection] = None

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        if self._write_lock_fd is None:
            self._write_lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_lock_fd = os.open(str(self._write_lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._write_lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(self._write_lock_fd, fcntl.LOCK_UN)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._open_db()
        self._migrate_legacy()
        self._loaded = True

    def _open_db(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(_SCHEMA)
            self._conn.commit()
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            print(
                json.dumps({"component": "joern-proxy", "event": "registry_load_warning", "error": str(exc)}),
                flush=True,
            )
            if self._conn is not None:
                self._conn.close()
            self._path.unlink(missing_ok=True)
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_SCHEMA)
            self._conn.commit()

        self._self_heal()

    def _self_heal(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT source_hash, archive_path FROM cpg_cache"
            ).fetchall()
            for source_hash, archive_path in rows:
                if not Path(archive_path).exists():
                    self._conn.execute(
                        "DELETE FROM cpg_cache WHERE source_hash = ?", (source_hash,)
                    )
            self._conn.commit()
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            self._conn.rollback()

    def _migrate_legacy(self) -> None:
        if self._legacy_path is None or not self._legacy_path.exists():
            return
        try:
            legacy_raw = self._legacy_path.read_text(encoding="utf-8")
            legacy_data = json.loads(legacy_raw)
            if not isinstance(legacy_data, dict):
                return
        except Exception:
            return

        try:
            for k, v in legacy_data.items():
                if not isinstance(v, dict):
                    continue
                if not Path(v.get("archive_path", "")).exists():
                    continue
                self._insert_entry(k, v)
            self._conn.commit()
            self._legacy_path.unlink(missing_ok=True)
        except Exception as exc:
            print(
                json.dumps({"component": "joern-proxy", "event": "registry_migrate_warning", "error": str(exc)}),
                flush=True,
            )

    @staticmethod
    def _entry_to_db(entry: dict) -> dict:
        extra = {k: v for k, v in entry.items() if k not in _KNOWN_KEYS}
        return {
            "sample_id": entry.get("sample_id", ""),
            "archive_path": entry.get("archive_path", ""),
            "archived_at": entry.get("archived_at", ""),
            "last_used": entry.get("last_used", ""),
            "size_bytes": entry.get("size_bytes", 0),
            "extra": json.dumps(extra) if extra else None,
        }

    @staticmethod
    def _row_to_entry(row: tuple) -> dict:
        source_hash, sample_id, archive_path, archived_at, last_used, size_bytes, extra = row
        entry = {
            "archive_path": archive_path,
            "sample_id": sample_id,
            "archived_at": archived_at,
            "last_used": last_used,
            "size_bytes": size_bytes,
        }
        if extra:
            try:
                extra_dict = json.loads(extra)
                if isinstance(extra_dict, dict):
                    entry.update(extra_dict)
            except (json.JSONDecodeError, TypeError):
                pass
        return entry

    def _insert_entry(self, source_hash: str, entry: dict) -> None:
        db = self._entry_to_db(entry)
        self._conn.execute(
            """INSERT OR REPLACE INTO cpg_cache
               (source_hash, sample_id, archive_path, archived_at, last_used, size_bytes, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_hash, db["sample_id"], db["archive_path"], db["archived_at"],
             db["last_used"], db["size_bytes"], db["extra"]),
        )

    def lookup(self, source_hash: str) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded()
            row = self._conn.execute(
                """SELECT source_hash, sample_id, archive_path, archived_at,
                   last_used, size_bytes, extra
                   FROM cpg_cache WHERE source_hash = ?""",
                (source_hash,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_entry(row)

    def register(self, source_hash: str, entry: dict) -> None:
        with self._lock, self._write_lock():
            self._ensure_loaded()
            self._conn.execute("BEGIN")
            try:
                self._insert_entry(source_hash, entry)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def remove(self, source_hash: str) -> None:
        with self._lock, self._write_lock():
            self._ensure_loaded()
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "DELETE FROM cpg_cache WHERE source_hash = ?", (source_hash,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def all_entries(self) -> list:
        with self._lock:
            self._ensure_loaded()
            rows = self._conn.execute(
                """SELECT source_hash, sample_id, archive_path, archived_at,
                   last_used, size_bytes, extra
                   FROM cpg_cache"""
            ).fetchall()
            return [(row[0], self._row_to_entry(row)) for row in rows]

    def save(self) -> None:
        pass

    def evict_if_needed(self) -> int:
        evicted = 0
        with self._lock, self._write_lock():
            self._ensure_loaded()
            while True:
                count_row = self._conn.execute(
                    "SELECT COUNT(*) FROM cpg_cache"
                ).fetchone()
                count = count_row[0]
                total_bytes_row = self._conn.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM cpg_cache"
                ).fetchone()
                total_bytes = total_bytes_row[0]
                total_gb = total_bytes / (1024 ** 3)
                if count <= self.archive_max_count and total_gb <= self.archive_max_gb:
                    break
                if count == 0:
                    break

                lru_row = self._conn.execute(
                    "SELECT source_hash FROM cpg_cache ORDER BY last_used ASC LIMIT 1"
                ).fetchone()
                if lru_row is None:
                    break
                lru_hash = lru_row[0]

                entry_row = self._conn.execute(
                    """SELECT source_hash, sample_id, archive_path, archived_at,
                       last_used, size_bytes, extra
                       FROM cpg_cache WHERE source_hash = ?""",
                    (lru_hash,),
                ).fetchone()
                entry = self._row_to_entry(entry_row) if entry_row else {}

                self._conn.execute("BEGIN")
                try:
                    self._conn.execute(
                        "DELETE FROM cpg_cache WHERE source_hash = ?", (lru_hash,)
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    break

                archive_path = entry.get("archive_path", "")
                if archive_path and Path(archive_path).exists():
                    cpg_remove(Path(archive_path))
                evicted += 1
                print(
                    json.dumps({
                        "component": "joern-proxy",
                        "event": "cpg_eviction",
                        "source_hash": lru_hash,
                        "archive_path": archive_path,
                        "sample_id": entry.get("sample_id"),
                    }),
                    flush=True,
                )
        return evicted
