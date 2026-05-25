"""Environment-backed settings for the Joern Server FastAPI app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from joern_server.util.env import env_bool, env_int, env_str


@dataclass(frozen=True)
class Settings:
    proxy_host: str
    proxy_port: int
    internal_host: str
    internal_port: int
    parse_bin: str
    cpg_out_dir: str
    cpg_archive_dir: str
    cpg_archive_max_count: int
    cpg_archive_max_gb: int
    parse_timeout_sec: int
    parse_repo_timeout_sec: int
    parse_repo_max_files: int
    parse_repo_max_bytes: int
    parse_repo_max_archive_bytes: int
    parse_repo_upload_ttl_hours: int
    repo_uploads_dir: str
    query_timeout_sec: int
    health_probe_timeout_sec: int
    query_cache_max_size: int
    query_cache_ttl_sec: int
    parse_jvm_xmx: str
    joern_memory_restart_mb: int
    joern_drain_sec: int
    joern_restart_jitter_sec: int
    joern_restart_min_peers: int
    joern_haproxy_vip: str
    enable_drain_test: bool

    @property
    def internal_url(self) -> str:
        return f"http://{self.internal_host}:{self.internal_port}/query-sync"

    @property
    def cpg_registry_path(self) -> Path:
        # Deprecated: FileCPGRegistry uses cpg_archive_dir directly.
        # Kept for backward compatibility.
        return Path(self.cpg_archive_dir) / "cpg-registry.json"

    @property
    def _cpg_registry_legacy_path(self) -> Path:
        return Path(self.cpg_out_dir).parent / "cpg-registry.json"

    @classmethod
    def from_env(cls) -> Settings:
        parse_repo_max_archive_mb = env_int("PARSE_REPO_MAX_ARCHIVE_MB", 500)
        return cls(
            proxy_host=env_str("PROXY_HOST", "0.0.0.0"),
            proxy_port=env_int("PROXY_PORT", env_int("JOERN_PUBLISH_PORT", 8080)),
            internal_host=env_str("JOERN_INTERNAL_HOST", "127.0.0.1"),
            internal_port=env_int("JOERN_INTERNAL_PORT", 18080),
            parse_bin=env_str("JOERN_PARSE_BIN", "/opt/joern/joern-cli/joern-parse"),
            cpg_out_dir=env_str("CPG_OUT_DIR", "/workspace/cpg-out"),
            cpg_archive_dir=env_str("CPG_ARCHIVE_DIR", "/workspace/cpg-archive"),
            cpg_archive_max_count=env_int("CPG_ARCHIVE_MAX_COUNT", 100),
            cpg_archive_max_gb=env_int("CPG_ARCHIVE_MAX_GB", 50),
            parse_timeout_sec=env_int("JOERN_PARSE_TIMEOUT_SEC", 900),
            parse_repo_timeout_sec=env_int("JOERN_PARSE_REPO_TIMEOUT_SEC", 1800),
            parse_repo_max_files=env_int("PARSE_REPO_MAX_FILES", 2000),
            parse_repo_max_bytes=env_int("PARSE_REPO_MAX_BYTES", 50_000_000),
            parse_repo_max_archive_bytes=parse_repo_max_archive_mb * 1024 * 1024,
            parse_repo_upload_ttl_hours=env_int("PARSE_REPO_UPLOAD_TTL_HOURS", 24),
            repo_uploads_dir=env_str("PARSE_REPO_UPLOADS_DIR", "/workspace/repo-uploads"),
            query_timeout_sec=env_int("JOERN_QUERY_TIMEOUT_SEC", 600),
            health_probe_timeout_sec=env_int("JOERN_HEALTH_PROBE_TIMEOUT_SEC", 5),
            query_cache_max_size=env_int("QUERY_CACHE_MAX_SIZE", 1000),
            query_cache_ttl_sec=env_int("QUERY_CACHE_TTL_SEC", 300),
            parse_jvm_xmx=env_str("PARSE_JVM_XMX", "2g"),
            joern_memory_restart_mb=env_int("JOERN_MEMORY_RESTART_MB", 3072),
            joern_drain_sec=env_int("JOERN_DRAIN_SEC", 7),
            joern_restart_jitter_sec=env_int("JOERN_RESTART_JITTER_SEC", 30),
            joern_restart_min_peers=env_int("JOERN_RESTART_MIN_PEERS", 2),
            joern_haproxy_vip=env_str("JOERN_HAPROXY_VIP", "http://joern-haproxy:8080"),
            enable_drain_test=env_bool("JOERN_ENABLE_DRAIN_TEST", False),
        )
