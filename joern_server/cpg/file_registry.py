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
    sidecars. Supports two archive layouts:

    - **Directory**: ``<archive_dir>/<hash>/`` with ``.meta.json`` inside
    - **Flat file**: ``<archive_dir>/<hash>`` (CPG file) + ``<hash>.meta.json``
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

    @staticmethod
    def _is_sha256_hex(name: str) -> bool:
        return len(name) == 64 and all(c in "0123456789abcdef" for c in name.lower())

    def _meta_path(self, source_hash: str, archive_path: Path) -> Path:
        if archive_path.is_dir():
            return archive_path / ".meta.json"
        return self._archive_dir / f"{source_hash}.meta.json"

    def _evicting_marker(self, source_hash: str, archive_path: Path) -> Path:
        if archive_path.is_dir():
            return archive_path / ".evicting"
        return self._archive_dir / f"{source_hash}.evicting"

    def _build_meta(self, archive_path: Path, entry: dict) -> dict:
        now = time.time()
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
        return {
            "sample_id": entry.get("sample_id", ""),
            "archived_at": entry.get("archived_at", iso_now),
            "last_used": entry.get("last_used", iso_now),
            "size_bytes": entry.get("size_bytes", 0),
            "archive_path": str(archive_path),
        }

    def _write_meta(self, source_hash: str, archive_path: Path, entry: dict) -> None:
        meta = self._build_meta(archive_path, entry)
        meta_path = self._meta_path(source_hash, archive_path)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def _read_meta(self, meta_path: Path) -> Optional[dict]:
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _legacy_entry(self, archive_path: Path) -> dict:
        try:
            size_bytes = archive_path.stat().st_size
        except OSError:
            size_bytes = 0
        return {
            "sample_id": "",
            "archived_at": "",
            "last_used": "",
            "size_bytes": size_bytes,
            "archive_path": str(archive_path),
        }

    def lookup(self, source_hash: str) -> Optional[dict]:
        with self._lock:
            archive_path = self._archive_dir / source_hash
            if archive_path.is_dir():
                if (archive_path / ".evicting").exists():
                    return None
                meta_path = archive_path / ".meta.json"
                if not meta_path.is_file():
                    return None
                entry = self._read_meta(meta_path)
                if entry is None:
                    return None
                entry["archive_path"] = str(archive_path)
                os.utime(str(archive_path), None)
                return entry

            if archive_path.is_file():
                if self._evicting_marker(source_hash, archive_path).exists():
                    return None
                meta_path = self._meta_path(source_hash, archive_path)
                if meta_path.is_file():
                    entry = self._read_meta(meta_path)
                    if entry is None:
                        return None
                    entry["archive_path"] = str(archive_path)
                    os.utime(str(archive_path), None)
                    return entry
                if self._is_sha256_hex(source_hash):
                    os.utime(str(archive_path), None)
                    return self._legacy_entry(archive_path)
                return None

            return None

    def register(self, source_hash: str, entry: dict) -> None:
        target = self._archive_dir / source_hash
        tmp_path = self._archive_dir / f".{source_hash}.tmp"
        src_path = Path(entry.get("archive_path", ""))

        with self._lock:
            if target.exists():
                self._write_meta(source_hash, target, entry)
                return

        if tmp_path.exists():
            cpg_remove(tmp_path)

        try:
            cpg_copy(src_path, tmp_path)
        except Exception:
            cpg_remove(tmp_path)
            raise

        if tmp_path.is_dir():
            try:
                (tmp_path / ".meta.json").write_text(
                    json.dumps(self._build_meta(target, entry)),
                    encoding="utf-8",
                )
            except Exception:
                cpg_remove(tmp_path)
                raise
            try:
                os.rename(str(tmp_path), str(target))
            except OSError:
                cpg_remove(tmp_path)
                raise
        else:
            try:
                os.rename(str(tmp_path), str(target))
            except OSError:
                cpg_remove(tmp_path)
                raise
            with self._lock:
                self._write_meta(source_hash, target, entry)

    def remove(self, source_hash: str) -> None:
        with self._lock:
            archive_path = self._archive_dir / source_hash
            if archive_path.exists():
                cpg_remove(archive_path)
            meta_sidecar = self._archive_dir / f"{source_hash}.meta.json"
            if meta_sidecar.is_file():
                meta_sidecar.unlink(missing_ok=True)
            evicting = self._archive_dir / f"{source_hash}.evicting"
            if evicting.is_file():
                evicting.unlink(missing_ok=True)

    def _load_entry(self, source_hash: str, archive_path: Path) -> Optional[dict]:
        if archive_path.is_dir():
            meta_path = archive_path / ".meta.json"
            if not meta_path.is_file():
                return None
            entry = self._read_meta(meta_path)
            if entry is None:
                return None
            entry["archive_path"] = str(archive_path)
            return entry

        if archive_path.is_file():
            meta_path = self._meta_path(source_hash, archive_path)
            if meta_path.is_file():
                entry = self._read_meta(meta_path)
                if entry is None:
                    return None
                entry["archive_path"] = str(archive_path)
                return entry
            if self._is_sha256_hex(source_hash):
                return self._legacy_entry(archive_path)
        return None

    def _collect_entries(self) -> list[tuple[str, dict]]:
        entries: list[tuple[str, dict]] = []
        seen: set[str] = set()
        if not self._archive_dir.is_dir():
            return entries

        for item in sorted(self._archive_dir.iterdir()):
            name = item.name
            if name.startswith("."):
                continue
            if item.is_dir():
                entry = self._load_entry(name, item)
                if entry is not None:
                    entries.append((name, entry))
                    seen.add(name)
            elif item.is_file() and name.endswith(".meta.json"):
                source_hash = name[: -len(".meta.json")]
                if source_hash in seen:
                    continue
                archive_path = self._archive_dir / source_hash
                if not archive_path.is_file():
                    continue
                entry = self._load_entry(source_hash, archive_path)
                if entry is not None:
                    entries.append((source_hash, entry))
                    seen.add(source_hash)

        for item in sorted(self._archive_dir.iterdir()):
            if not item.is_file() or item.name.startswith("."):
                continue
            if item.name.endswith(".meta.json") or item.name.endswith(".evicting"):
                continue
            source_hash = item.name
            if source_hash in seen:
                continue
            if not self._is_sha256_hex(source_hash):
                continue
            entry = self._load_entry(source_hash, item)
            if entry is not None:
                entries.append((source_hash, entry))
                seen.add(source_hash)

        return entries

    def all_entries(self) -> list[tuple[str, dict]]:
        with self._lock:
            return self._collect_entries()

    def evict_if_needed(self) -> int:
        evicted = 0
        with self._lock:
            if not self._archive_dir.is_dir():
                return 0

            candidates: list[tuple[str, Path, float, int]] = []
            for source_hash, entry in self._collect_entries():
                archive_path = Path(entry["archive_path"])
                if not archive_path.exists():
                    continue
                try:
                    mtime = archive_path.stat().st_mtime
                except OSError:
                    continue
                size_bytes = entry.get("size_bytes", 0)
                candidates.append((source_hash, archive_path, mtime, size_bytes))

            candidates.sort(key=lambda x: x[2])

            total_bytes = sum(c[3] for c in candidates)
            count = len(candidates)
            total_gb = total_bytes / 1e9

            idx = 0
            while count > self.archive_max_count or total_gb > self.archive_max_gb:
                if idx >= len(candidates):
                    break

                source_hash, archive_path, _, size_bytes = candidates[idx]
                evicting_marker = self._evicting_marker(source_hash, archive_path)
                try:
                    fd = os.open(str(evicting_marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                except OSError:
                    idx += 1
                    continue

                entry = {}
                meta_path = self._meta_path(source_hash, archive_path)
                if meta_path.is_file():
                    entry = self._read_meta(meta_path) or {}

                cpg_remove(archive_path)
                if meta_path.is_file() and not archive_path.is_dir():
                    meta_path.unlink(missing_ok=True)
                if evicting_marker.is_file():
                    evicting_marker.unlink(missing_ok=True)

                evicted += 1
                count -= 1
                total_bytes -= size_bytes
                total_gb = total_bytes / 1e9
                idx += 1

                print(
                    json.dumps({
                        "component": "joern-proxy",
                        "event": "cpg_eviction",
                        "source_hash": source_hash,
                        "archive_path": str(archive_path),
                        "sample_id": entry.get("sample_id"),
                    }),
                    flush=True,
                )

        return evicted

    def save(self) -> None:
        pass

    # ------------------------------------------------------------------
    # sample_id → source_hash mapping (persisted as .sid-map.json)
    # ------------------------------------------------------------------

    @property
    def _sid_map_path(self) -> Path:
        return self._archive_dir / ".sid-map.json"

    def _load_sid_map(self) -> dict[str, str]:
        try:
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            raw = self._sid_map_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_sid_map(self, mapping: dict[str, str]) -> None:
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._archive_dir / ".sid-map.json.tmp"
        tmp.write_text(json.dumps(mapping), encoding="utf-8")
        os.replace(str(tmp), str(self._sid_map_path))

    def register_sample_id(self, sample_id: str, source_hash: str) -> None:
        """Persist sample_id → source_hash mapping so it survives process restart."""
        with self._lock:
            mapping = self._load_sid_map()
            mapping[sample_id] = source_hash
            self._save_sid_map(mapping)

    def lookup_by_sample_id(self, sample_id: str) -> Optional[str]:
        """Return the source_hash for a known sample_id, or None if not recorded."""
        with self._lock:
            mapping = self._load_sid_map()
            return mapping.get(sample_id)

    def all_sid_entries(self) -> list[tuple[str, str]]:
        """Return all (sample_id, source_hash) pairs from the persistent sid map."""
        with self._lock:
            mapping = self._load_sid_map()
            return list(mapping.items())
