import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from joern_server.cpg.storage import cpg_copy, cpg_remove


class FileCPGRegistry:
    """Filesystem-based registry for tracking compiled Code Property Graphs (CPGs).

    Uses the filesystem as the database, storing metadata in ``.meta.json``
    files within per-hash directories under ``archive_dir``.
    """

    def __init__(self, archive_dir: Path, archive_max_count: int = 100, archive_max_gb: float = 50.0):
        self._archive_dir = archive_dir
        self.archive_max_count = archive_max_count
        self.archive_max_gb = archive_max_gb
        self._lock = threading.Lock()

        self._check_migration()

    def _check_migration(self) -> None:
        """Log a warning if old SQLite registry files are detected."""
        sqlite_json = self._archive_dir / "cpg-registry.json"
        sqlite_db = self._archive_dir / "cpg-registry.json.db"
        if sqlite_json.exists() or sqlite_db.exists():
            print(
                json.dumps({
                    "component": "joern-proxy",
                    "event": "file_registry_migrate_check",
                    "note": "old SQLite registry detected, manual migration may be needed",
                }),
                flush=True,
            )

    def lookup(self, source_hash: str) -> Optional[dict]:
        with self._lock:
            archive_path = self._archive_dir / source_hash
            if not archive_path.is_dir():
                return None
            evicting_marker = archive_path / ".evicting"
            if evicting_marker.exists():
                return None
            meta_path = archive_path / ".meta.json"
            if not meta_path.is_file():
                return None
            try:
                entry = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            entry["archive_path"] = str(archive_path)
            os.utime(str(archive_path), None)
            return entry

    def register(self, source_hash: str, entry: dict) -> None:
        target = self._archive_dir / source_hash
        tmp_path = self._archive_dir / f".{source_hash}.tmp"

        with self._lock:
            if target.exists():
                return

        if tmp_path.exists():
            cpg_remove(tmp_path)

        src_path = Path(entry.get("archive_path", ""))
        try:
            cpg_copy(src_path, tmp_path)
        except Exception:
            cpg_remove(tmp_path)
            raise

        now = time.time()
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
        meta = {
            "sample_id": entry.get("sample_id", ""),
            "archived_at": entry.get("archived_at", iso_now),
            "last_used": entry.get("last_used", iso_now),
            "size_bytes": entry.get("size_bytes", 0),
            "archive_path": str(target),
        }
        try:
            (tmp_path / ".meta.json").write_text(json.dumps(meta), encoding="utf-8")
        except Exception:
            cpg_remove(tmp_path)
            raise

        try:
            os.rename(str(tmp_path), str(target))
        except OSError:
            cpg_remove(tmp_path)

    def remove(self, source_hash: str) -> None:
        with self._lock:
            archive_path = self._archive_dir / source_hash
            if archive_path.exists():
                cpg_remove(archive_path)

    def all_entries(self) -> list[tuple[str, dict]]:
        with self._lock:
            entries: list[tuple[str, dict]] = []
            if not self._archive_dir.is_dir():
                return entries
            for entry_dir in sorted(self._archive_dir.iterdir()):
                if not entry_dir.is_dir():
                    continue
                name = entry_dir.name
                if name.startswith("."):
                    continue
                meta_path = entry_dir / ".meta.json"
                if not meta_path.is_file():
                    continue
                try:
                    entry = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                entry["archive_path"] = str(entry_dir)
                entries.append((name, entry))
            return entries

    def evict_if_needed(self) -> int:
        evicted = 0
        with self._lock:
            if not self._archive_dir.is_dir():
                return 0

            dirs: list[tuple[Path, float, int]] = []
            for entry_path in self._archive_dir.iterdir():
                if not entry_path.is_dir():
                    continue
                if entry_path.name.startswith("."):
                    continue
                meta_path = entry_path / ".meta.json"
                if not meta_path.is_file():
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                size_bytes = meta.get("size_bytes", 0)
                mtime = entry_path.stat().st_mtime
                dirs.append((entry_path, mtime, size_bytes))

            dirs.sort(key=lambda x: x[1])

            total_bytes = sum(d[2] for d in dirs)
            count = len(dirs)
            total_gb = total_bytes / 1e9

            idx = 0
            while count > self.archive_max_count or total_gb > self.archive_max_gb:
                if idx >= len(dirs):
                    break

                dir_path, _, size_bytes = dirs[idx]
                evicting_marker = dir_path / ".evicting"
                try:
                    fd = os.open(str(evicting_marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                except OSError:
                    idx += 1
                    continue

                entry = {}
                try:
                    meta_path = dir_path / ".meta.json"
                    if meta_path.is_file():
                        entry = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

                cpg_remove(dir_path)
                evicted += 1
                count -= 1
                total_bytes -= size_bytes
                total_gb = total_bytes / 1e9
                idx += 1

                print(
                    json.dumps({
                        "component": "joern-proxy",
                        "event": "cpg_eviction",
                        "source_hash": dir_path.name,
                        "archive_path": str(dir_path),
                        "sample_id": entry.get("sample_id"),
                    }),
                    flush=True,
                )

        return evicted

    def save(self) -> None:
        pass
