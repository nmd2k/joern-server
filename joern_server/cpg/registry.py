import json
import threading
from pathlib import Path
from typing import Optional

from joern_server.cpg.storage import cpg_remove


class CPGRegistry:
    """
    Registry for tracking compiled Code Property Graphs (CPGs).

    Manages persistent metadata storage, lookup by source hash,
    and automatic LRU eviction based on count and disk space limits.
    """
    def __init__(self, registry_path: Path, archive_max_count: int = 100, archive_max_gb: float = 50.0):
        self._path = registry_path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self.archive_max_count = archive_max_count
        self.archive_max_gb = archive_max_gb
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._path.exists():
            self._loaded = True
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("registry not a dict")
            cleaned: dict[str, dict] = {}
            for k, v in data.items():
                if isinstance(v, dict) and Path(v.get("archive_path", "")).exists():
                    cleaned[k] = v
                elif isinstance(v, dict):
                    pass  # skip missing paths (self-healing)
            self._data = cleaned
        except Exception as exc:
            print(
                json.dumps({"component": "joern-proxy", "event": "registry_load_warning", "error": str(exc)}),
                flush=True,
            )
            self._data = {}
        self._loaded = True

    def lookup(self, source_hash: str) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded()
            return self._data.get(source_hash)

    def register(self, source_hash: str, entry: dict) -> None:
        with self._lock:
            self._ensure_loaded()
            self._data[source_hash] = entry
            self._save_locked()

    def remove(self, source_hash: str) -> None:
        with self._lock:
            self._ensure_loaded()
            self._data.pop(source_hash, None)
            self._save_locked()

    def all_entries(self) -> list:
        with self._lock:
            self._ensure_loaded()
            return list(self._data.items())

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            print(
                json.dumps({"component": "joern-proxy", "event": "registry_save_error", "error": str(exc)}),
                flush=True,
            )

    def evict_if_needed(self) -> int:
        evicted = 0
        with self._lock:
            self._ensure_loaded()
            while True:
                count = len(self._data)
                total_bytes = sum(e.get("size_bytes", 0) for e in self._data.values())
                total_gb = total_bytes / (1024 ** 3)
                if count <= self.archive_max_count and total_gb <= self.archive_max_gb:
                    break
                if not self._data:
                    break
                lru_hash = min(self._data, key=lambda h: self._data[h].get("last_used", ""))
                entry = self._data.pop(lru_hash)
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
            if evicted:
                self._save_locked()
        return evicted
