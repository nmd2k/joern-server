import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import httpx


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return default if v is None or v == "" else int(v)


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _upstream_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Headers to forward to the in-container Joern HTTP /query-sync (affinity + auth)."""
    h: dict[str, str] = {"Content-Type": "application/json"}
    auth = handler.headers.get("Authorization")
    if auth:
        h["Authorization"] = auth
    sid = handler.headers.get("X-Session-Id")
    if sid:
        h["X-Session-Id"] = sid
    rid = handler.headers.get("X-Request-Id")
    if rid:
        h["X-Request-Id"] = rid
    return h


def _safe_sample_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", raw.strip())
    return safe or "sample"


# Map common/alias language names to the Joern-recognized language strings.
# Run `joern-parse --list-languages` inside the container to see all valid names.
_LANGUAGE_ALIASES: dict[str, str] = {
    # Python: 'python' triggers missing py2cpg.sh; 'pythonsrc' uses pysrc2cpg (installed).
    "py": "pythonsrc",
    "python": "pythonsrc",
    # JavaScript / TypeScript
    "js": "jssrc",
    "ts": "jssrc",
    "javascript": "jssrc",
    "typescript": "jssrc",
    # C++ — c2cpg handles both C and C++ when given a .cpp/.cc file.
    "cpp": "c",
    "c++": "c",
    "cc": "c",
    "cxx": "c",
    # C# — alias
    "cs": "csharpsrc",
    "csharp": "csharpsrc",
    # Go
    "go": "golang",
    # Java aliases
    "javasrc": "java",
    # Ruby
    "rb": "rubysrc",
    "ruby": "rubysrc",
}


def _normalize_language(language: str) -> str:
    """Translate caller-supplied language alias to the Joern-native name."""
    return _LANGUAGE_ALIASES.get(language.lower(), language) if language else language


def _json_error(msg: str, *, code: str = "bad_request") -> dict[str, str]:
    return {"error": msg, "code": code}


class LRUCache:
    """Thread-safe LRU cache with TTL support for query result caching.

    Metrics: hits, misses, evictions
    """

    def __init__(self, max_size: int = 1000, ttl_sec: int = 300):
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def _make_key(self, session_id: str, query_hash: str) -> str:
        """Create cache key from session_id and md5(query_hash)."""
        return f"{session_id}:{query_hash}"

    def get(self, session_id: str, query_hash: str) -> Optional[dict]:
        """Get cached result if exists and not expired."""
        key = self._make_key(session_id, query_hash)
        current_time = time.time()

        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None

            result, timestamp = self._cache[key]
            if current_time - timestamp > self.ttl_sec:
                # Entry expired
                del self._cache[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self.hits += 1
            return result

    def put(self, session_id: str, query_hash: str, result: dict) -> None:
        """Add result to cache, evicting LRU entries if necessary."""
        key = self._make_key(session_id, query_hash)
        current_time = time.time()

        with self._lock:
            # If max_size is 0, don't cache anything
            if self.max_size <= 0:
                return

            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # Remove oldest (least recently used)
                self.evictions += 1

            self._cache[key] = (result, current_time)

    def get_metrics(self) -> dict:
        """Return cache metrics."""
        with self._lock:
            hit_rate = self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0.0
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "size": len(self._cache),
                "max_size": self.max_size,
                "ttl_sec": self.ttl_sec,
                "hit_rate": hit_rate,
            }


class CPGRegistry:
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
                    _cpg_remove(Path(archive_path))
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


# Per-hash locks to prevent concurrent parses of the same source hash.
_parse_hash_locks: dict[str, threading.Lock] = {}
_parse_hash_locks_lock = threading.Lock()


def _get_hash_lock(source_hash: str) -> threading.Lock:
    with _parse_hash_locks_lock:
        if source_hash not in _parse_hash_locks:
            _parse_hash_locks[source_hash] = threading.Lock()
        return _parse_hash_locks[source_hash]


def _cpg_copy(src: Path, dst: Path) -> None:
    """Copy a CPG — works for both file and directory layouts."""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    else:
        shutil.copytree(str(src), str(dst))


def _cpg_remove(path: Path) -> None:
    """Delete a CPG — works for both file and directory layouts."""
    if path.is_file():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def _cpg_size_bytes(path: Path) -> int:
    """Return byte size of a CPG path — works for both file and directory layouts."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


class JoernProxyHandler(BaseHTTPRequestHandler):
    internal_url: str = ""
    parse_bin: str = "/opt/joern/joern-cli/joern-parse"
    cpg_out_dir: str = "/workspace/cpg-out"
    cpg_archive_dir: str = "/workspace/cpg-archive"
    parse_timeout_sec: int = 900
    query_timeout_sec: int = 600
    query_cache: Optional[LRUCache] = None
    cpg_registry: Optional[CPGRegistry] = None
    # Maps sample_id → source_hash for in-flight/recent parses (thread-safe via _sid_hash_lock)
    _sid_to_hash: dict = {}
    _sid_hash_lock: threading.Lock = threading.Lock()

    def _log_event(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "component": "joern-proxy",
            "event": event,
            "path": self.path,
            "session_id": self.headers.get("X-Session-Id"),
            "ts_ms": int(time.time() * 1000),
        }
        payload.update(fields)
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except Exception:
            return

    @staticmethod
    def _classify_query(query: str) -> str:
        q = (query or "").strip()
        if not q:
            return "empty"
        if q.startswith("load_cpg("):
            return "load_cpg"
        if q.startswith("importCpg("):
            return "importCpg"
        if q == "version":
            return "version"
        if q == "help":
            return "help"
        m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", q)
        if m:
            return m.group(1)
        return "cpgql"

    @staticmethod
    def _should_cache(query_class: str) -> bool:
        """Determine if query should be cached. Skip load_cpg, importCpg, cleanup."""
        skip_classes = {"load_cpg", "importCpg", "cleanup", "empty", "unknown", "invalid_json"}
        return query_class not in skip_classes

    @staticmethod
    def _query_hash(query: str) -> str:
        """Generate md5 hash of query for cache key."""
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    @staticmethod
    def _preview_query(query: str, limit: int = 180) -> str:
        q = re.sub(r"\s+", " ", (query or "").strip())
        if len(q) <= limit:
            return q
        return q[:limit] + "...(truncated)"

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _parse_request_json(self) -> tuple[Optional[dict], Optional[dict]]:
        try:
            raw = self._read_body()
            data = json.loads(raw.decode("utf-8") if raw else "{}")
        except Exception:
            return None, _json_error("invalid JSON body")
        if not isinstance(data, dict):
            return None, _json_error("JSON body must be an object")
        return data, None

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/version":
            # Implement /version using a cheap Joern query.
            try:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": "version"},
                    headers=_upstream_headers(self),
                    timeout=60,
                )
                resp.raise_for_status()
                body = resp.json()
                stdout = body.get("stdout", "")
                self._send_json(HTTPStatus.OK, {"stdout": stdout})
            except Exception as e:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(e)})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _handle_parse(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        sample_id_raw = str(data.get("sample_id", "")).strip()
        source_code = data.get("source_code")
        language = _normalize_language(str(data.get("language", "")).strip())
        filename = str(data.get("filename", "")).strip() or "snippet.txt"
        overwrite = bool(data.get("overwrite", False))

        if not sample_id_raw:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: sample_id"))
            return
        if not isinstance(source_code, str) or not source_code.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: source_code"))
            return

        sample_id = _safe_sample_id(sample_id_raw)
        cpg_out = Path(self.cpg_out_dir) / sample_id
        source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()

        self._log_event(
            "parse_request",
            sample_id=sample_id,
            language=(language or None),
            overwrite=overwrite,
            source_hash=source_hash,
        )
        if cpg_out.exists() and not overwrite:
            self._send_json(
                HTTPStatus.CONFLICT,
                _json_error(
                    f"CPG output already exists at {cpg_out}; pass overwrite=true to replace",
                    code="cpg_exists",
                ),
            )
            return
        if cpg_out.exists() and overwrite:
            _cpg_remove(cpg_out)

        # Store sample_id → source_hash mapping for archive-on-cleanup
        with self.__class__._sid_hash_lock:
            self.__class__._sid_to_hash[sample_id] = source_hash

        # Try cache hit (per-hash lock prevents double-parse on concurrent same-hash requests)
        hash_lock = _get_hash_lock(source_hash)
        with hash_lock:
            if self.cpg_registry is not None:
                entry = self.cpg_registry.lookup(source_hash)
                if entry is not None:
                    archive_path = Path(entry["archive_path"])
                    if archive_path.exists():
                        try:
                            _cpg_copy(archive_path, cpg_out)
                            now = datetime.datetime.utcnow().isoformat() + "Z"
                            entry["last_used"] = now
                            self.cpg_registry.register(source_hash, entry)
                            self._log_event(
                                "parse_result",
                                sample_id=sample_id,
                                ok=True,
                                cache_hit=True,
                                source_hash=source_hash,
                            )
                            self._send_json(
                                HTTPStatus.OK,
                                {
                                    "ok": True,
                                    "sample_id": sample_id,
                                    "cpg_path": str(cpg_out),
                                    "language": language or None,
                                    "cache_hit": True,
                                    "source_hash": source_hash,
                                },
                            )
                            return
                        except Exception:
                            # Archive copy failed — fall through to full parse
                            _cpg_remove(cpg_out)

            tmp_src_dir = Path(tempfile.mkdtemp(prefix=f"joern-src-{sample_id}-"))
            try:
                src_path = tmp_src_dir / Path(filename).name
                src_path.write_text(source_code, encoding="utf-8", newline="\n")
                cmd = [
                    self.parse_bin,
                    str(tmp_src_dir),
                    "--output",
                    str(cpg_out),
                ]
                if language:
                    cmd.extend(["--language", language])

                proc = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    timeout=self.parse_timeout_sec,
                    check=False,
                )
                ok = proc.returncode == 0 and cpg_out.exists()
                status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
                self._log_event(
                    "parse_result",
                    sample_id=sample_id,
                    ok=ok,
                    return_code=proc.returncode,
                    cache_hit=False,
                    source_hash=source_hash,
                )
                self._send_json(
                    status,
                    {
                        "ok": ok,
                        "sample_id": sample_id,
                        "cpg_path": str(cpg_out),
                        "language": language or None,
                        "return_code": proc.returncode,
                        "stdout": proc.stdout[-100_000:],
                        "stderr": proc.stderr[-100_000:],
                        "cache_hit": False,
                        "source_hash": source_hash,
                    },
                )
            except subprocess.TimeoutExpired:
                self._send_json(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    _json_error(
                        f"joern-parse timed out after {self.parse_timeout_sec}s",
                        code="parse_timeout",
                    ),
                )
            except Exception as e:
                self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="parse_failed"))
            finally:
                shutil.rmtree(tmp_src_dir, ignore_errors=True)

    def _handle_cleanup(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        sample_id_raw = str(data.get("sample_id", "")).strip()
        if not sample_id_raw:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: sample_id"))
            return

        archive_flag = bool(data.get("archive", False))
        sample_id = _safe_sample_id(sample_id_raw)
        cpg_out = Path(self.cpg_out_dir) / sample_id
        existed = cpg_out.exists()
        self._log_event("cleanup_request", sample_id=sample_id, existed=bool(existed), archive=archive_flag)
        try:
            if archive_flag and existed and self.cpg_registry is not None:
                # Resolve source_hash: check in-flight map first, then registry reverse lookup
                source_hash: Optional[str] = None
                with self.__class__._sid_hash_lock:
                    source_hash = self.__class__._sid_to_hash.get(sample_id)
                if source_hash is None:
                    for h, entry in self.cpg_registry.all_entries():
                        if entry.get("sample_id") == sample_id:
                            source_hash = h
                            break

                if source_hash is not None:
                    archive_path = Path(self.cpg_archive_dir) / source_hash
                    if archive_path.exists():
                        _cpg_remove(archive_path)
                    _cpg_copy(cpg_out, archive_path)
                    size_bytes = _cpg_size_bytes(archive_path)
                    now = datetime.datetime.utcnow().isoformat() + "Z"
                    self.cpg_registry.register(source_hash, {
                        "archive_path": str(archive_path),
                        "sample_id": sample_id,
                        "archived_at": now,
                        "last_used": now,
                        "size_bytes": size_bytes,
                    })
                    _cpg_remove(cpg_out)
                    self.cpg_registry.evict_if_needed()
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "sample_id": sample_id,
                            "cpg_path": str(cpg_out),
                            "deleted": False,
                            "archived": True,
                            "source_hash": source_hash,
                            "archive_path": str(archive_path),
                        },
                    )
                    return
                # source_hash unknown — fall through to delete
            if existed:
                _cpg_remove(cpg_out)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "sample_id": sample_id,
                    "cpg_path": str(cpg_out),
                    "deleted": bool(existed),
                    "archived": False,
                },
            )
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="cleanup_failed"))

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/parse":
            self._handle_parse()
            return

        if self.path == "/cleanup":
            self._handle_cleanup()
            return

        if self.path == "/cache-metrics":
            # Return cache metrics for monitoring
            if self.query_cache:
                self._send_json(HTTPStatus.OK, self.query_cache.get_metrics())
            else:
                self._send_json(HTTPStatus.OK, {"error": "cache not enabled"})
            return

        if self.path != "/query-sync":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        body = self._read_body()
        t0 = time.perf_counter()
        try:
            query_class = "unknown"
            query_preview = ""
            query_str = ""
            try:
                req = json.loads(body.decode("utf-8") if body else "{}")
                if isinstance(req, dict):
                    query_str = str(req.get("query", "") or "")
                    query_class = self._classify_query(query_str)
                    query_preview = self._preview_query(query_str)
            except Exception:
                query_class = "invalid_json"
            request_id = self.headers.get("X-Request-Id")
            session_id = self.headers.get("X-Session-Id") or "default"

            # Try cache hit for cacheable queries
            if self.query_cache and self._should_cache(query_class):
                query_hash = self._query_hash(query_str)
                cached_result = self.query_cache.get(session_id, query_hash)
                if cached_result is not None:
                    self._log_event(
                        "query_sync",
                        request_id=request_id,
                        query_class=query_class,
                        query_preview=query_preview,
                        status_code=200,
                        success=True,
                        latency_ms=0,
                        cache_hit=True,
                    )
                    self._send_json(HTTPStatus.OK, cached_result)
                    return

            with self.repl_semaphore:
                resp = httpx.post(
                    self.internal_url,
                    content=body,
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            # Preserve status and body; clients expect Joern's /query-sync JSON.
            resp_json = resp.json()

            # Cache successful responses for cacheable queries
            if self.query_cache and self._should_cache(query_class) and resp.status_code == 200:
                query_hash = self._query_hash(query_str)
                self.query_cache.put(session_id, query_hash, resp_json)

            success = None
            if isinstance(resp_json, dict):
                success = resp_json.get("success")
            self._log_event(
                "query_sync",
                request_id=request_id,
                query_class=query_class,
                query_preview=query_preview,
                status_code=resp.status_code,
                success=success,
                latency_ms=latency_ms,
                cache_hit=False,
            )
            self._send_json(resp.status_code, resp_json)
        except httpx.TimeoutException as e:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            self._log_event(
                "query_sync_error",
                query_class=query_class,
                latency_ms=latency_ms,
                error_type="TimeoutException",
                error=str(e),
            )
            self._send_json(HTTPStatus.GATEWAY_TIMEOUT, {"error": str(e)})
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            self._log_event(
                "query_sync_error",
                query_class=query_class,
                latency_ms=latency_ms,
                error_type=type(e).__name__,
                error=str(e),
            )
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(e)})

    def log_message(self, fmt: str, *args) -> None:
        # Silence default http.server logging in container logs.
        return


def main() -> None:
    proxy_host = _env_str("PROXY_HOST", "0.0.0.0")
    proxy_port = _env_int("PROXY_PORT", _env_int("JOERN_PUBLISH_PORT", 8080))

    internal_host = _env_str("JOERN_INTERNAL_HOST", "127.0.0.1")
    internal_port = _env_int("JOERN_INTERNAL_PORT", 18080)
    parse_bin = _env_str("JOERN_PARSE_BIN", "/opt/joern/joern-cli/joern-parse")
    cpg_out_dir = _env_str("CPG_OUT_DIR", "/workspace/cpg-out")
    cpg_archive_dir = _env_str("CPG_ARCHIVE_DIR", "/workspace/cpg-archive")
    cpg_archive_max_count = _env_int("CPG_ARCHIVE_MAX_COUNT", 100)
    cpg_archive_max_gb = _env_int("CPG_ARCHIVE_MAX_GB", 50)
    parse_timeout_sec = _env_int("JOERN_PARSE_TIMEOUT_SEC", 900)
    # Default aligns with training/agent --joern-timeout (600s); router HAProxy allows up to 3600s.
    query_timeout_sec = _env_int("JOERN_QUERY_TIMEOUT_SEC", 600)
    # Joern HTTP server endpoint inside the container.
    internal_url = f"http://{internal_host}:{internal_port}/query-sync"

    # Query cache configuration
    cache_max_size = _env_int("QUERY_CACHE_MAX_SIZE", 1000)
    cache_ttl_sec = _env_int("QUERY_CACHE_TTL_SEC", 300)
    JoernProxyHandler.query_cache = LRUCache(max_size=cache_max_size, ttl_sec=cache_ttl_sec)

    # CPG registry (hash → archive path)
    registry_path = Path(cpg_out_dir).parent / "cpg-registry.json"
    JoernProxyHandler.cpg_registry = CPGRegistry(
        registry_path,
        archive_max_count=cpg_archive_max_count,
        archive_max_gb=float(cpg_archive_max_gb),
    )

    JoernProxyHandler.internal_url = internal_url
    # One slot per proxy process: the internal Joern REPL is single-threaded.
    JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
    JoernProxyHandler.parse_bin = parse_bin
    JoernProxyHandler.cpg_out_dir = cpg_out_dir
    JoernProxyHandler.cpg_archive_dir = cpg_archive_dir
    JoernProxyHandler.parse_timeout_sec = parse_timeout_sec
    JoernProxyHandler.query_timeout_sec = query_timeout_sec
    httpd = ThreadingHTTPServer((proxy_host, proxy_port), JoernProxyHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

