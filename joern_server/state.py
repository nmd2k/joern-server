"""Process-wide application state attached to the FastAPI app."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from joern_server.cache import LRUCache
from joern_server.config import Settings
from joern_server.cpg import FileCPGRegistry
from joern_server.metrics import PrometheusMetrics


@dataclass
class AppState:
    settings: Settings
    metrics: Optional[PrometheusMetrics]
    query_cache: Optional[LRUCache]
    cpg_registry: Optional[FileCPGRegistry]
    repl_semaphore: threading.Semaphore
    parse_semaphore: threading.Semaphore
    affinity_cpg_path: dict[str, str] = field(default_factory=dict)
    active_affinity_key: Optional[str] = None
    active_cpg_path: Optional[str] = None
    sid_to_hash: dict[str, str] = field(default_factory=dict)
    sid_hash_lock: threading.Lock = field(default_factory=threading.Lock)
    draining: bool = False
    restart_scheduled: bool = False
    drain_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def internal_url(self) -> str:
        return self.settings.internal_url

    @classmethod
    def from_settings(cls, settings: Settings, *, metrics: Optional[PrometheusMetrics] = None) -> AppState:
        query_cache: Optional[LRUCache] = None
        if settings.query_cache_max_size > 0:
            query_cache = LRUCache(
                max_size=settings.query_cache_max_size,
                ttl_sec=settings.query_cache_ttl_sec,
            )

        cpg_registry = FileCPGRegistry(
            Path(settings.cpg_archive_dir),
            archive_max_count=settings.cpg_archive_max_count,
            archive_max_gb=float(settings.cpg_archive_max_gb),
        )

        state = cls(
            settings=settings,
            metrics=metrics if metrics is not None else PrometheusMetrics(),
            query_cache=query_cache,
            cpg_registry=cpg_registry,
            repl_semaphore=threading.Semaphore(1),
            parse_semaphore=threading.Semaphore(1),
        )
        state._rebuild_sid_to_hash()
        return state

    def _rebuild_sid_to_hash(self) -> None:
        """Scan .joern_hash sidecars in cpg-out to restore sid_to_hash after restart."""
        from joern_server.cpg import joern_hash_sidecar

        cpg_out_dir = Path(self.settings.cpg_out_dir)
        if not cpg_out_dir.is_dir():
            return
        count = 0
        try:
            for item in cpg_out_dir.iterdir():
                if item.name.startswith(".") or item.name.endswith(".joern_hash"):
                    continue
                sidecar = joern_hash_sidecar(item)
                if sidecar.is_file():
                    try:
                        source_hash = sidecar.read_text(encoding="utf-8").strip()
                        if source_hash and len(source_hash) == 64:
                            self.sid_to_hash[item.name] = source_hash
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
        if count > 0:
            print(
                json.dumps({
                    "component": "joern-proxy",
                    "event": "sid_to_hash_rebuilt",
                    "count": count,
                }),
                flush=True,
            )

    @classmethod
    def for_test(cls, tmp_path: Path, **overrides: object) -> AppState:
        """Build minimal AppState for unit tests under ``tmp_path``."""
        defaults: dict[str, object] = {
            "proxy_host": "127.0.0.1",
            "proxy_port": 8080,
            "internal_host": "127.0.0.1",
            "internal_port": 18080,
            "parse_bin": "/bin/false",
            "cpg_out_dir": str(tmp_path / "cpg-out"),
            "cpg_archive_dir": str(tmp_path / "cpg-archive"),
            "cpg_archive_max_count": 10,
            "cpg_archive_max_gb": 1,
            "parse_timeout_sec": 30,
            "parse_repo_timeout_sec": 60,
            "parse_repo_max_files": 100,
            "parse_repo_max_bytes": 1_000_000,
            "parse_repo_max_archive_bytes": 1_000_000,
            "parse_repo_upload_ttl_hours": 1,
            "repo_uploads_dir": str(tmp_path / "repo-uploads"),
            "query_timeout_sec": 5,
            "health_probe_timeout_sec": 5,
            "query_cache_max_size": 100,
            "query_cache_ttl_sec": 300,
            "parse_jvm_xmx": "2g",
            "joern_memory_restart_mb": 0,
            "joern_drain_sec": 0,
            "joern_restart_jitter_sec": 0,
            "joern_restart_min_peers": 2,
            "joern_haproxy_vip": "",
            "enable_drain_test": False,
        }
        defaults.update(overrides)
        settings = Settings(**defaults)  # type: ignore[arg-type]
        (tmp_path / "cpg-out").mkdir(parents=True, exist_ok=True)
        (tmp_path / "cpg-archive").mkdir(parents=True, exist_ok=True)
        (tmp_path / "repo-uploads").mkdir(parents=True, exist_ok=True)
        return cls.from_settings(settings)
