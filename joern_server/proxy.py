import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

try:
    from joern_server.metrics import PrometheusMetrics
except ModuleNotFoundError:
    from metrics import PrometheusMetrics  # docker: python3 /app/joern_server/proxy.py


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
    aff = handler.headers.get("X-Affinity-Key")
    if aff:
        h["X-Affinity-Key"] = aff
    rid = handler.headers.get("X-Request-Id")
    if rid:
        h["X-Request-Id"] = rid
    return h


def _affinity_key(handler: BaseHTTPRequestHandler) -> str:
    """Routing/REPL/cache key — typically sample_id (see X-Affinity-Key)."""
    raw = handler.headers.get("X-Affinity-Key")
    if raw and str(raw).strip():
        return _safe_sample_id(str(raw).strip())
    return "default"


def _request_id(handler: BaseHTTPRequestHandler) -> str:
    raw = handler.headers.get("X-Request-Id")
    if raw and str(raw).strip():
        return str(raw).strip()
    return uuid.uuid4().hex


def _sample_id_from_cpg_path(path: str) -> Optional[str]:
    """Extract sample_id from /workspace/cpg-out/<sample_id> style paths."""
    p = (path or "").strip().rstrip("/")
    if not p:
        return None
    parts = Path(p).parts
    for i, part in enumerate(parts):
        if part == "cpg-out" and i + 1 < len(parts):
            return _safe_sample_id(parts[i + 1])
    return _safe_sample_id(Path(p).name) if p else None


def _safe_sample_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", raw.strip())
    return safe or "sample"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_IMPORT_CPG_RE = re.compile(r"""^\s*importCpg\(\s*["']([^"']+)["']\s*\)\s*$""")


def _strip_ansi(text: str) -> str:
    """Strip ANSI color/style escape sequences from Joern REPL stdout."""
    return _ANSI_RE.sub("", text) if text else text


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


def _dot_to_graph(dot_text: str) -> dict:
    """Parse Joern DOT output into {nodes, edges} JSON structure."""
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    if not dot_text:
        return {"nodes": nodes, "edges": edges}

    text = dot_text.strip()
    idx = text.find("digraph")
    if idx == -1:
        return {"nodes": nodes, "edges": edges}
    text = text[idx:]

    m = re.match(r'digraph\s+"([^"]*)"\s*\{', text)
    if not m:
        return {"nodes": nodes, "edges": edges}

    content_start = m.end()
    depth = 1
    content_end = content_start
    for i, ch in enumerate(text[content_start:], start=content_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                content_end = i
                break

    content = text[content_start:content_end].strip()
    if not content:
        return {"nodes": nodes, "edges": edges}

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.endswith(";"):
            line = line[:-1].strip()
        if not line:
            continue

        edge_m = re.match(r'"([^"]*)"\s*->\s*"([^"]*)"(?:\s*\[([^\]]*)\])?', line)
        if edge_m:
            attrs_str = edge_m.group(3) or ""
            label_m = re.search(r'label="([^"]*)"', attrs_str)
            edges.append({
                "source": edge_m.group(1),
                "target": edge_m.group(2),
                "label": label_m.group(1) if label_m else "",
            })
            continue

        node_m = re.match(r'"([^"]*)"(?:\s*\[([^\]]*)\])?', line)
        if node_m:
            attrs_str = node_m.group(2) or ""
            label_m = re.search(r'label="([^"]*)"', attrs_str)
            shape_m = re.search(r'shape="([^"]*)"', attrs_str)
            nodes.append({
                "id": node_m.group(1),
                "label": label_m.group(1) if label_m else "",
                "shape": shape_m.group(1) if shape_m else "",
            })

    declared_ids = {n["id"] for n in nodes}
    for e in edges:
        for nid in (e["source"], e["target"]):
            if nid not in declared_ids:
                nodes.append({"id": nid, "label": "", "shape": ""})
                declared_ids.add(nid)

    return {"nodes": nodes, "edges": edges}


_LANGUAGE_EXT: dict[str, str] = {
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "newc": ".c",
    "jssrc": ".js",
    "javascript": ".js",
    "typescript": ".ts",
    "pythonsrc": ".py",
    "python": ".py",
    "java": ".java",
    "javasrc": ".java",
    "rubysrc": ".rb",
    "ruby": ".rb",
    "php": ".php",
    "csharpsrc": ".cs",
    "csharp": ".cs",
    "swiftsrc": ".swift",
    "golang": ".go",
    "kotlin": ".kt",
    "rust": ".rs",
    "llvm": ".ll",
    "ghidra": ".c",
}


def _default_filename(language: str) -> str:
    """Return a filename with an extension appropriate for the language frontend."""
    normalized = _normalize_language(language)
    ext = _LANGUAGE_EXT.get(normalized, ".txt")
    return f"snippet{ext}"


def _json_error(msg: str, *, code: str = "bad_request") -> dict[str, str]:
    return {"error": msg, "code": code}


def _query_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _validate_repo_path(path: str) -> Optional[str]:
    """Return error message if path is invalid for repo ingest, else None."""
    if not path or not isinstance(path, str):
        return "path must be a non-empty string"
    if "\0" in path:
        return "path contains null byte"
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return "path must be relative (no leading /)"
    parts = normalized.split("/")
    if ".." in parts:
        return "path must not contain .."
    return None


def _canonical_tree_hash(files: dict[str, str]) -> str:
    """SHA256 of sorted path + NUL + sha256(content) + newline per file."""
    h = hashlib.sha256()
    for path in sorted(files.keys()):
        content_hash = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(content_hash.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _parse_allowed_roots() -> list[Path]:
    raw = _env_str("PARSE_REPO_ALLOWED_ROOTS", "/workspace/datasets")
    roots: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).resolve())
    return roots or [Path("/workspace/datasets").resolve()]


def _is_under_allowed_root(candidate: Path, allowed_roots: list[Path]) -> bool:
    resolved = candidate.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _collect_tree_files(
    root: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[Optional[dict[str, str]], Optional[dict]]:
    """Walk root and collect relative path → UTF-8 content. Enforce limits."""
    if not root.is_dir():
        return None, _json_error(f"source path is not a directory: {root}", code="invalid_source_root")
    files: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        err = _validate_repo_path(rel)
        if err is not None:
            return None, _json_error(f"invalid path in tree: {rel}: {err}", code="invalid_path")
        if len(files) >= max_files:
            return None, _json_error(
                f"repo exceeds max file count ({max_files})",
                code="payload_too_large",
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return None, _json_error(f"failed to read {rel}: {exc}", code="bad_request")
        total_bytes += len(raw)
        if total_bytes > max_bytes:
            return None, _json_error(
                f"repo exceeds max total bytes ({max_bytes})",
                code="payload_too_large",
            )
        files[rel] = raw.decode("utf-8")
    if not files:
        return None, _json_error("repo tree contains no files", code="empty_tree")
    return files, None


def _parse_multipart_archive(body: bytes, content_type: str) -> tuple[Optional[bytes], Optional[dict]]:
    """Extract the ``archive`` field from multipart/form-data."""
    if "multipart/form-data" not in (content_type or ""):
        return None, _json_error("Content-Type must be multipart/form-data", code="bad_request")
    m = re.search(r"boundary=([^;\s]+)", content_type, re.IGNORECASE)
    if not m:
        return None, _json_error("missing multipart boundary", code="bad_request")
    boundary = m.group(1).strip().strip('"')
    delimiter = ("--" + boundary).encode("utf-8")
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_blob = part[:header_end].decode("utf-8", errors="replace")
        payload = part[header_end + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        name_m = re.search(r'name="([^"]+)"', headers_blob)
        if not name_m or name_m.group(1) != "archive":
            continue
        return payload, None
    return None, _json_error('missing multipart field "archive"', code="bad_request")


def _extract_archive(archive_bytes: bytes, dest: Path, filename_hint: str = "") -> Optional[dict]:
    """Extract zip or tar.gz archive into dest. Returns error dict on failure."""
    dest.mkdir(parents=True, exist_ok=True)
    lower = filename_hint.lower()
    buf = io.BytesIO(archive_bytes)
    try:
        if lower.endswith(".zip") or archive_bytes[:2] == b"PK":
            with zipfile.ZipFile(buf) as zf:
                zf.extractall(dest)
            return None
    except zipfile.BadZipFile:
        buf.seek(0)
    except Exception as exc:
        return _json_error(f"failed to extract zip archive: {exc}", code="bad_request")
    try:
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:*") as tf:
            tf.extractall(dest, filter="data")
        return None
    except Exception as exc:
        return _json_error(f"unsupported or corrupt archive: {exc}", code="bad_request")


def _extract_scala_tuples(stdout: str) -> list[str]:
    """Extract individual tuple strings from Scala `List(...)` output."""
    if not stdout:
        return []
    # Find "= List(" — the value list, skipping the type-annotation List(...)
    idx = stdout.find("= List(")
    if idx != -1:
        content_start = idx + 7
    else:
        idx = stdout.find("List(")
        if idx == -1:
            return []
        content_start = idx + 5
    depth = 1
    pos = content_start
    while pos < len(stdout) and depth > 0:
        if stdout[pos] == "(":
            depth += 1
        elif stdout[pos] == ")":
            depth -= 1
        pos += 1
    content = stdout[content_start:pos - 1]
    tuples: list[str] = []
    i = 0
    while i < len(content):
        if content[i] == "(":
            d = 1
            j = i + 1
            while j < len(content) and d > 0:
                if content[j] == "(":
                    d += 1
                elif content[j] == ")":
                    d -= 1
                j += 1
            if d == 0:
                tuples.append(content[i:j])
                i = j
                continue
        i += 1
    return tuples


def _split_scala_tuple(tuple_str: str) -> list[str]:
    """Split a Scala tuple string by top-level commas into fields."""
    inner = tuple_str[1:-1] if tuple_str.startswith("(") and tuple_str.endswith(")") else tuple_str
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            current.append(ch)
            i += 1
            continue
        if in_string:
            current.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            current.append(ch)
            i += 1
            continue
        if ch == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        fields.append("".join(current).strip())
    return fields


def _parse_scala_field(field: str):
    """Parse a single Scala value (string, int, Some(value=...), None) into Python."""
    field = field.strip()
    if not field:
        return None
    # Strip trailing "L" suffix from Scala Long literals
    long_suffix = False
    if field.endswith("L") and len(field) > 1:
        rest = field[:-1]
        if rest.isdigit() or (rest.startswith("-") and rest[1:].isdigit()):
            field = rest
            long_suffix = True
    if field.startswith('"') and field.endswith('"') and len(field) >= 2:
        inner = field[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if field == "None":
        return None
    if field.startswith("Some(") and field.endswith(")"):
        inner_val = field[5:-1].strip()
        # Handle `Some(value = 42)` format from Option[Int] output
        eq_idx = inner_val.find(" = ")
        if eq_idx != -1:
            num_str = inner_val[eq_idx + 3:].strip()
            try:
                return int(num_str)
            except ValueError:
                return inner_val
        try:
            return int(inner_val)
        except ValueError:
            return inner_val
    try:
        return int(field)
    except ValueError:
        return field


def _parse_metadata_tuples(stdout: str) -> dict[str, dict]:
    """Parse 6-field metadata tuples (id, code, line, column, order, label) into {nodeId: metadata}."""
    metadata: dict[str, dict] = {}
    for t in _extract_scala_tuples(stdout):
        fields = _split_scala_tuple(t)
        if len(fields) < 6:
            continue
        try:
            fid = _parse_scala_field(fields[0])
            code = _parse_scala_field(fields[1])
            line_num = _parse_scala_field(fields[2])
            col_num = _parse_scala_field(fields[3])
            order = _parse_scala_field(fields[4])
            node_type = _parse_scala_field(fields[5])
            metadata[str(fid)] = {
                "code": code if isinstance(code, str) else str(code) if code is not None else "",
                "line_number": line_num,
                "column_number": col_num,
                "order": order if order is not None else -1,
                "argument_index": -1,
                "node_type": node_type if isinstance(node_type, str) else "",
            }
        except Exception:
            continue
    return metadata


def _parse_ast_tuples(stdout: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Parse 7-field AST tuples (id, code, line, column, order, label, parentId) into (nodes, edges, metadata)."""
    nodes: list[dict] = []
    edges: list[dict] = []
    metadata: dict[str, dict] = {}
    for t in _extract_scala_tuples(stdout):
        fields = _split_scala_tuple(t)
        if len(fields) < 7:
            continue
        try:
            fid = _parse_scala_field(fields[0])
            code = _parse_scala_field(fields[1])
            line_num = _parse_scala_field(fields[2])
            col_num = _parse_scala_field(fields[3])
            order = _parse_scala_field(fields[4])
            node_type = _parse_scala_field(fields[5])
            parent_id = _parse_scala_field(fields[6])
            node_id_str = str(fid)
            nodes.append({
                "id": node_id_str,
                "label": node_type if isinstance(node_type, str) else str(node_type) if node_type is not None else "",
            })
            metadata[node_id_str] = {
                "code": code if isinstance(code, str) else str(code) if code is not None else "",
                "line_number": line_num,
                "column_number": col_num,
                "order": order if order is not None else -1,
                "argument_index": -1,
                "node_type": node_type if isinstance(node_type, str) else "",
            }
            if parent_id is not None:
                edges.append({
                    "source": str(parent_id),
                    "target": node_id_str,
                    "label": "",
                })
        except Exception:
            continue

    node_ids = {n["id"] for n in nodes}
    for e in edges:
        for nid in (e["source"], e["target"]):
            if nid not in node_ids:
                nodes.append({"id": nid, "label": "", "shape": ""})
                metadata[nid] = {}
                node_ids.add(nid)

    return nodes, edges, metadata


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

    def _make_key(self, affinity_key: str, query_hash: str) -> str:
        """Create cache key from affinity_key (sample_id) and md5(query_hash)."""
        return f"{affinity_key}:{query_hash}"

    def get(self, affinity_key: str, query_hash: str) -> Optional[dict]:
        """Get cached result if exists and not expired."""
        key = self._make_key(affinity_key, query_hash)
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

    def put(self, affinity_key: str, query_hash: str, result: dict) -> None:
        """Add result to cache, evicting LRU entries if necessary."""
        key = self._make_key(affinity_key, query_hash)
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
    """
    HTTP request handler implementing the Joern Server API.

    Coordinates authentication, session-sticky query routing, cache integration,
    repository/archive ingestion, and graph serialization.
    """
    internal_url: str = ""
    parse_bin: str = "/opt/joern/joern-cli/joern-parse"
    cpg_out_dir: str = "/workspace/cpg-out"
    cpg_archive_dir: str = "/workspace/cpg-archive"
    repo_uploads_dir: str = "/workspace/repo-uploads"
    parse_timeout_sec: int = 900
    parse_repo_timeout_sec: int = 1800
    parse_repo_max_files: int = 2000
    parse_repo_max_bytes: int = 50_000_000
    parse_repo_max_archive_bytes: int = 500 * 1024 * 1024
    parse_repo_upload_ttl_hours: int = 24
    query_timeout_sec: int = 600
    query_cache: Optional[LRUCache] = None
    cpg_registry: Optional[CPGRegistry] = None
    # Maps sample_id → source_hash for in-flight/recent parses (thread-safe via _sid_hash_lock)
    _sid_to_hash: dict = {}
    _sid_hash_lock: threading.Lock = threading.Lock()
    # Affinity-scoped CPG import state (key = X-Affinity-Key / sample_id).
    _affinity_cpg_path: dict[str, str] = {}
    _active_affinity_key: Optional[str] = None
    _active_cpg_path: Optional[str] = None
    metrics: Optional[PrometheusMetrics] = None

    def _log_event(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "component": "joern-proxy",
            "event": event,
            "path": self.path,
            "session_id": self.headers.get("X-Session-Id"),
            "affinity_key": _affinity_key(self),
            "request_id": _request_id(self),
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

    @staticmethod
    def _extract_import_cpg_path(query: str) -> Optional[str]:
        m = _IMPORT_CPG_RE.match((query or "").strip())
        if not m:
            return None
        return m.group(1).strip() or None

    def _post_query_sync(self, *, query: str, timeout_sec: int) -> httpx.Response:
        return httpx.post(
            self.internal_url,
            json={"query": query},
            headers=_upstream_headers(self),
            timeout=timeout_sec,
        )

    def _probe_joern(self, timeout_sec: float = 5.0) -> tuple[bool, int, Optional[str]]:
        """Return (ok, latency_ms, error_message)."""
        t0 = time.perf_counter()
        try:
            resp = httpx.post(
                self.internal_url,
                json={"query": "val _health = 1"},
                headers=_upstream_headers(self),
                timeout=timeout_sec,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            if resp.status_code != 200:
                return False, latency_ms, f"upstream status {resp.status_code}"
            body = resp.json()
            if isinstance(body, dict) and body.get("success") is False:
                return False, latency_ms, "upstream success=false"
            return True, latency_ms, None
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            return False, latency_ms, str(exc)

    def _clear_affinity_state(self, sample_id: str) -> None:
        """Drop in-memory CPG binding for sample_id and best-effort close REPL graph."""
        cpg_out = Path(self.cpg_out_dir) / sample_id
        target = str(cpg_out)
        to_remove: list[str] = []
        for key, path in list(self.__class__._affinity_cpg_path.items()):
            if key == sample_id or path.rstrip("/") == target.rstrip("/"):
                to_remove.append(key)
        for key in to_remove:
            self.__class__._affinity_cpg_path.pop(key, None)

        active_path = self.__class__._active_cpg_path
        if active_path and active_path.rstrip("/") == target.rstrip("/"):
            if self.repl_semaphore is not None:
                with self.repl_semaphore:
                    try:
                        self._post_query_sync(query="close", timeout_sec=min(30, self.query_timeout_sec))
                    except Exception:
                        pass
            self.__class__._active_cpg_path = None
            self.__class__._active_affinity_key = None

        if self.metrics is not None:
            self.metrics.set_gauge("joern_proxy_affinity_map_size", float(len(self.__class__._affinity_cpg_path)))

    def _activate_session_cpg_if_needed(self, affinity_key: str) -> tuple[bool, Optional[str]]:
        desired_cpg = self.__class__._affinity_cpg_path.get(affinity_key)
        active_cpg = self.__class__._active_cpg_path
        active_key = self.__class__._active_affinity_key

        if desired_cpg:
            if active_cpg != desired_cpg:
                resp = self._post_query_sync(query=f'importCpg("{desired_cpg}")', timeout_sec=self.query_timeout_sec)
                body = resp.json()
                success = body.get("success", True)
                if isinstance(success, str):
                    success = success.strip().lower() in ("true", "1", "yes")
                if resp.status_code != 200 or not success:
                    self._log_event(
                        "session_cpg_activate_failed",
                        affinity_key=affinity_key,
                        desired_cpg=desired_cpg,
                        status_code=resp.status_code,
                    )
                    self.__class__._affinity_cpg_path.pop(affinity_key, None)
                    self.__class__._active_affinity_key = None
                    self.__class__._active_cpg_path = None
                    return False, f"failed to activate CPG for affinity {affinity_key}"
                self.__class__._active_cpg_path = desired_cpg
            self.__class__._active_affinity_key = affinity_key
            return True, None

        # No imported CPG for this affinity; clear prior active CPG to prevent leakage.
        if active_cpg is not None and active_key != affinity_key:
            try:
                self._post_query_sync(query="close", timeout_sec=self.query_timeout_sec)
            except Exception:
                pass
            self.__class__._active_cpg_path = None
        self.__class__._active_affinity_key = affinity_key
        return True, None

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
            probe_timeout = float(getattr(self, "health_probe_timeout_sec", 5))
            ok, latency_ms, err = self._probe_joern(timeout_sec=probe_timeout)
            if self.metrics is not None:
                self.metrics.set_gauge("joern_proxy_joern_up", 1.0 if ok else 0.0)
            payload: dict[str, Any] = {
                "ok": ok,
                "joern_ok": ok,
                "latency_ms": latency_ms,
            }
            if err:
                payload["error"] = err
            status = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
            self._send_json(status, payload)
            return

        if self.path == "/metrics":
            if self.metrics is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "metrics not enabled"})
                return
            if self.metrics is not None:
                self.metrics.set_gauge(
                    "joern_proxy_affinity_map_size",
                    float(len(self.__class__._affinity_cpg_path)),
                )
            body = self.metrics.render().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
        filename = str(data.get("filename", "")).strip() or _default_filename(language)
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

        # Store sample_id → source_hash mapping for archive-on-cleanup
        # ONLY set after confirming this is a successful parse/cache_hit, not speculatively.

        # Try cache hit BEFORE the exists/guard check.
        # If the registry has this source_hash, the result is identical regardless
        # of whether cpg_out already exists on disk — return cache_hit straight away.
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
                            with self.__class__._sid_hash_lock:
                                self.__class__._sid_to_hash[sample_id] = source_hash
                            return
                        except Exception:
                            # Archive copy failed — fall through to full parse
                            _cpg_remove(cpg_out)

        # No archive cache hit — check if existing CPG on disk matches this source.
        # Idempotent hit: same sample_id + same source_hash = same CPG, return immediately.
        if cpg_out.exists() and not overwrite:
            with self.__class__._sid_hash_lock:
                existing_hash = self.__class__._sid_to_hash.get(sample_id)
            if existing_hash == source_hash:
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
                with self.__class__._sid_hash_lock:
                    self.__class__._sid_to_hash[sample_id] = source_hash
                return
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
            if ok:
                with self.__class__._sid_hash_lock:
                    self.__class__._sid_to_hash[sample_id] = source_hash
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

    def _request_path(self) -> str:
        return urlparse(self.path).path

    def _request_query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query, keep_blank_values=True)

    def _materialize_tree(self, files: dict[str, str], dest: Path) -> None:
        for rel_path, content in files.items():
            out_path = dest / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8", newline="\n")

    def _read_jsonl_repo_files(self) -> tuple[Optional[dict[str, str]], Optional[dict]]:
        max_files = self.parse_repo_max_files
        max_bytes = self.parse_repo_max_bytes
        files: dict[str, str] = {}
        total_bytes = 0
        length_hdr = self.headers.get("Content-Length")
        try:
            remaining = int(length_hdr) if length_hdr else None
        except ValueError:
            return None, _json_error("invalid Content-Length", code="bad_request")

        while True:
            if remaining is not None and remaining <= 0:
                break
            line = self.rfile.readline()
            if not line:
                break
            if remaining is not None:
                remaining -= len(line)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return None, _json_error(f"invalid JSONL line: {exc}", code="bad_request")
            if not isinstance(obj, dict):
                return None, _json_error("JSONL line must be a JSON object", code="bad_request")
            rel_path = obj.get("path")
            content = obj.get("content")
            if not isinstance(rel_path, str) or not isinstance(content, str):
                return None, _json_error('each JSONL line requires "path" and "content" strings', code="bad_request")
            err = _validate_repo_path(rel_path)
            if err is not None:
                return None, _json_error(err, code="invalid_path")
            if len(files) >= max_files:
                return None, _json_error(
                    f"repo exceeds max file count ({max_files})",
                    code="payload_too_large",
                )
            encoded_len = len(content.encode("utf-8"))
            total_bytes += encoded_len
            if total_bytes > max_bytes:
                return None, _json_error(
                    f"repo exceeds max total bytes ({max_bytes})",
                    code="payload_too_large",
                )
            files[rel_path.replace("\\", "/")] = content

        if not files:
            return None, _json_error("repo tree contains no files", code="empty_tree")
        return files, None

    def _upload_meta_path(self, upload_id: str) -> Path:
        return Path(self.repo_uploads_dir) / upload_id / "meta.json"

    def _upload_tree_path(self, upload_id: str) -> Path:
        return Path(self.repo_uploads_dir) / upload_id / "tree"

    def _load_upload_meta(self, upload_id: str) -> tuple[Optional[dict], Optional[dict]]:
        safe_id = re.sub(r"[^a-zA-Z0-9-]", "", upload_id)
        if not safe_id or safe_id != upload_id:
            return None, _json_error("invalid upload_id", code="bad_request")
        meta_path = self._upload_meta_path(upload_id)
        if not meta_path.exists():
            return None, _json_error(f"upload not found: {upload_id}", code="upload_not_found")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, _json_error(f"corrupt upload metadata: {exc}", code="bad_request")
        expires_at = meta.get("expires_at", "")
        try:
            exp_dt = datetime.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if now >= exp_dt:
                return None, _json_error(f"upload expired: {upload_id}", code="upload_expired")
        except Exception:
            return None, _json_error("invalid upload expiry metadata", code="bad_request")
        return meta, None

    def _execute_repo_parse(
        self,
        *,
        sample_id: str,
        files: dict[str, str],
        language: str,
        overwrite: bool,
        ingest_mode: str,
    ) -> None:
        source_hash = _canonical_tree_hash(files)
        cpg_out = Path(self.cpg_out_dir) / sample_id
        file_count = len(files)

        self._log_event(
            "parse_repo_request",
            sample_id=sample_id,
            language=(language or None),
            overwrite=overwrite,
            source_hash=source_hash,
            ingest_mode=ingest_mode,
            file_count=file_count,
        )

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
                                "parse_repo_result",
                                sample_id=sample_id,
                                ok=True,
                                cache_hit=True,
                                source_hash=source_hash,
                                ingest_mode=ingest_mode,
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
                                    "parse_mode": "repo",
                                    "ingest_mode": ingest_mode,
                                    "file_count": file_count,
                                },
                            )
                            with self.__class__._sid_hash_lock:
                                self.__class__._sid_to_hash[sample_id] = source_hash
                            return
                        except Exception:
                            _cpg_remove(cpg_out)

        if cpg_out.exists() and not overwrite:
            with self.__class__._sid_hash_lock:
                existing_hash = self.__class__._sid_to_hash.get(sample_id)
            if existing_hash == source_hash:
                self._log_event(
                    "parse_repo_result",
                    sample_id=sample_id,
                    ok=True,
                    cache_hit=True,
                    source_hash=source_hash,
                    ingest_mode=ingest_mode,
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
                        "parse_mode": "repo",
                        "ingest_mode": ingest_mode,
                        "file_count": file_count,
                    },
                )
                with self.__class__._sid_hash_lock:
                    self.__class__._sid_to_hash[sample_id] = source_hash
                return
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

        tmp_src_dir = Path(tempfile.mkdtemp(prefix=f"joern-repo-{sample_id}-"))
        try:
            self._materialize_tree(files, tmp_src_dir)
            cmd = [self.parse_bin, str(tmp_src_dir), "--output", str(cpg_out)]
            if language:
                cmd.extend(["--language", language])
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=self.parse_repo_timeout_sec,
                check=False,
            )
            ok = proc.returncode == 0 and cpg_out.exists()
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
            self._log_event(
                "parse_repo_result",
                sample_id=sample_id,
                ok=ok,
                return_code=proc.returncode,
                cache_hit=False,
                source_hash=source_hash,
                ingest_mode=ingest_mode,
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
                    "parse_mode": "repo",
                    "ingest_mode": ingest_mode,
                    "file_count": file_count,
                },
            )
            if ok:
                with self.__class__._sid_hash_lock:
                    self.__class__._sid_to_hash[sample_id] = source_hash
        except subprocess.TimeoutExpired:
            self._send_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                _json_error(
                    f"joern-parse timed out after {self.parse_repo_timeout_sec}s",
                    code="parse_timeout",
                ),
            )
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="parse_failed"))
        finally:
            shutil.rmtree(tmp_src_dir, ignore_errors=True)

    def _handle_parse_repo(self) -> None:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        is_jsonl = content_type == "application/x-ndjson"

        if is_jsonl:
            query = self._request_query()
            sample_id_raw = (query.get("sample_id") or [""])[0].strip()
            language = _normalize_language((query.get("language") or [""])[0].strip())
            overwrite = _query_bool((query.get("overwrite") or [None])[0], default=False)
            if not sample_id_raw:
                self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required query param: sample_id"))
                return
            sample_id = _safe_sample_id(sample_id_raw)
            files, err = self._read_jsonl_repo_files()
            if err is not None or files is None:
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if err and err.get("code") == "payload_too_large" else HTTPStatus.BAD_REQUEST
                self._send_json(status, err or _json_error("invalid request"))
                return
            self._execute_repo_parse(
                sample_id=sample_id,
                files=files,
                language=language,
                overwrite=overwrite,
                ingest_mode="jsonl",
            )
            return

        if content_type != "application/json":
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _json_error(
                    "Content-Type must be application/x-ndjson (JSONL) or application/json (upload_id/source_root)",
                    code="bad_request",
                ),
            )
            return

        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        has_upload = bool(str(data.get("upload_id", "")).strip())
        has_source_root = bool(str(data.get("source_root", "")).strip())
        if has_upload and has_source_root:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _json_error("upload_id and source_root are mutually exclusive", code="bad_request"),
            )
            return
        if not has_upload and not has_source_root:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _json_error("JSON body requires upload_id or source_root", code="bad_request"),
            )
            return

        sample_id_raw = str(data.get("sample_id", "")).strip()
        if not sample_id_raw:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: sample_id"))
            return
        sample_id = _safe_sample_id(sample_id_raw)
        language = _normalize_language(str(data.get("language", "")).strip())
        overwrite = bool(data.get("overwrite", False))

        if has_upload:
            upload_id = str(data.get("upload_id", "")).strip()
            meta, err = self._load_upload_meta(upload_id)
            if err is not None:
                code = err.get("code", "bad_request")
                if code == "upload_not_found":
                    status = HTTPStatus.NOT_FOUND
                elif code == "upload_expired":
                    status = HTTPStatus.GONE
                else:
                    status = HTTPStatus.BAD_REQUEST
                self._send_json(status, err)
                return
            tree_root = self._upload_tree_path(upload_id)
            files, err = _collect_tree_files(
                tree_root,
                max_files=self.parse_repo_max_files,
                max_bytes=self.parse_repo_max_bytes,
            )
            if err is not None or files is None:
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if err and err.get("code") == "payload_too_large" else HTTPStatus.BAD_REQUEST
                self._send_json(status, err or _json_error("invalid request"))
                return
            self._execute_repo_parse(
                sample_id=sample_id,
                files=files,
                language=language,
                overwrite=overwrite,
                ingest_mode="upload",
            )
            return

        source_root = Path(str(data.get("source_root", "")).strip())
        allowed = _parse_allowed_roots()
        if not source_root.exists():
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _json_error(f"source_root does not exist: {source_root}", code="invalid_source_root"),
            )
            return
        if not _is_under_allowed_root(source_root, allowed):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _json_error(
                    f"source_root must be under allowed roots: {[str(r) for r in allowed]}",
                    code="invalid_source_root",
                ),
            )
            return
        files, err = _collect_tree_files(
            source_root,
            max_files=self.parse_repo_max_files,
            max_bytes=self.parse_repo_max_bytes,
        )
        if err is not None or files is None:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if err and err.get("code") == "payload_too_large" else HTTPStatus.BAD_REQUEST
            self._send_json(status, err or _json_error("invalid request"))
            return
        self._execute_repo_parse(
            sample_id=sample_id,
            files=files,
            language=language,
            overwrite=overwrite,
            ingest_mode="source_root",
        )

    def _handle_parse_repo_upload(self) -> None:
        content_type = self.headers.get("Content-Type") or ""
        body = self._read_body()
        if len(body) > self.parse_repo_max_archive_bytes:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                _json_error(
                    f"archive exceeds max size ({self.parse_repo_max_archive_bytes} bytes)",
                    code="payload_too_large",
                ),
            )
            return
        archive_bytes, err = _parse_multipart_archive(body, content_type)
        if err is not None or archive_bytes is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return
        if not archive_bytes:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("empty archive", code="bad_request"))
            return

        upload_id = str(uuid.uuid4())
        upload_dir = Path(self.repo_uploads_dir) / upload_id
        tree_dir = upload_dir / "tree"
        tree_dir.mkdir(parents=True, exist_ok=True)
        extract_err = _extract_archive(archive_bytes, tree_dir)
        if extract_err is not None:
            shutil.rmtree(upload_dir, ignore_errors=True)
            self._send_json(HTTPStatus.BAD_REQUEST, extract_err)
            return

        expires_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=self.parse_repo_upload_ttl_hours)
        ).isoformat().replace("+00:00", "Z")
        meta = {
            "upload_id": upload_id,
            "expires_at": expires_at,
            "bytes_stored": len(archive_bytes),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._upload_meta_path(upload_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._log_event("parse_repo_upload", upload_id=upload_id, bytes_stored=len(archive_bytes))
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "upload_id": upload_id,
                "expires_at": expires_at,
                "bytes_stored": len(archive_bytes),
            },
        )

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
                    self._clear_affinity_state(sample_id)
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
            self._clear_affinity_state(sample_id)
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="cleanup_failed"))

    def _fetch_node_metadata(self, node_ids: list[str]) -> dict[str, dict]:
        """Batch-fetch metadata for a list of node IDs from Joern.

        Returns a dict mapping node id (str) to metadata dict.
        Falls back to empty dict on any failure.
        """
        if not node_ids:
            return {}
        numeric_ids: list[int] = []
        for nid in node_ids:
            try:
                numeric_ids.append(int(nid))
            except (ValueError, TypeError):
                pass
        if not numeric_ids:
            return {}

        metadata: dict[str, dict] = {}
        max_batch = 50

        for i in range(0, len(numeric_ids), max_batch):
            batch = numeric_ids[i:i + max_batch]
            id_list = ", ".join(f"{nid}L" for nid in batch)
            query = (
                f"cpg.all.id({id_list}).collectAll[AstNode].map(n =>"
                f" (n.id, n.code, n.lineNumber, n.columnNumber,"
                f" n.order, n.label)"
                f").l"
            )
            try:
                with self.repl_semaphore:
                    resp = httpx.post(
                        self.internal_url,
                        json={"query": query},
                        headers=_upstream_headers(self),
                        timeout=self.query_timeout_sec,
                    )
                resp.raise_for_status()
                resp_json = resp.json()
                success = resp_json.get("success", True)
                if isinstance(success, str):
                    success = success.strip().lower() in ("true", "1", "yes")
                if not success:
                    continue
                stdout = resp_json.get("stdout", "")
                batch_meta = _parse_metadata_tuples(stdout)
                metadata.update(batch_meta)
            except Exception:
                continue

        return metadata

    def _handle_graph_cfg(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        method_full_name = str(data.get("method_full_name", "")).strip()
        if not method_full_name:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: method_full_name"))
            return

        sample_id = data.get("sample_id", "")
        escaped = method_full_name.replace("\\", "\\\\").replace('"', '\\"')
        query = f'cpg.method.fullName("{escaped}").dotCfg.l'

        self._log_event("graph_cfg_request", method_full_name=method_full_name, sample_id=sample_id)

        try:
            with self.repl_semaphore:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": query},
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )

            resp.raise_for_status()
            resp_json = resp.json()
            success = resp_json.get("success", True)
            if isinstance(success, str):
                success = success.strip().lower() in ("true", "1", "yes")
            if not success:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"CFG query failed for method: {method_full_name}", code="query_failed"
                ))
                return

            stdout = resp_json.get("stdout", "")
            graph = _dot_to_graph(stdout)

            if not graph["nodes"] and not graph["edges"]:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"No CFG found for method: {method_full_name}", code="empty_result"
                ))
                return

            response_body: dict = {
                "nodes": graph["nodes"],
                "edges": graph["edges"],
                "method_full_name": method_full_name,
            }
            node_ids = [n["id"] for n in graph["nodes"]]
            try:
                response_body["metadata"] = self._fetch_node_metadata(node_ids)
            except Exception:
                response_body["metadata"] = {}

            self._send_json(HTTPStatus.OK, response_body)
        except httpx.TimeoutException:
            self._send_json(HTTPStatus.GATEWAY_TIMEOUT, _json_error("query timed out", code="query_timeout"))
            return
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="joern_error"))
            return

    def _handle_graph_dfg(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        method_full_name = str(data.get("method_full_name", "")).strip()
        if not method_full_name:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: method_full_name"))
            return

        sample_id = data.get("sample_id", "")
        source_pattern = str(data.get("source_pattern", "")).strip()
        sink_pattern = str(data.get("sink_pattern", "")).strip()

        escaped = method_full_name.replace("\\", "\\\\").replace('"', '\\"')

        if source_pattern and sink_pattern:
            escaped_source = source_pattern.replace("\\", "\\\\").replace('"', '\\"')
            escaped_sink = sink_pattern.replace("\\", "\\\\").replace('"', '\\"')
            query = (
                f'cpg.method.fullName("{escaped}")'
                f'.reachableByFlows(cpg.code("{escaped_source}").l, cpg.code("{escaped_sink}").l).p'
            )
        else:
            query = f'cpg.method.fullName("{escaped}").dotDdg.l'

        self._log_event(
            "graph_dfg_request",
            method_full_name=method_full_name,
            sample_id=sample_id,
            source_pattern=source_pattern or None,
            sink_pattern=sink_pattern or None,
        )

        try:
            with self.repl_semaphore:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": query},
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )

            resp.raise_for_status()
            resp_json = resp.json()
            success = resp_json.get("success", True)
            if isinstance(success, str):
                success = success.strip().lower() in ("true", "1", "yes")
            if not success:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"DFG query failed for method: {method_full_name}", code="query_failed"
                ))
                return

            stdout = resp_json.get("stdout", "")

            if source_pattern and sink_pattern:
                self._send_json(HTTPStatus.OK, {
                    "flows_raw": stdout,
                    "method_full_name": method_full_name,
                    "source_pattern": source_pattern,
                    "sink_pattern": sink_pattern,
                })
            else:
                graph = _dot_to_graph(stdout)
                if not graph["nodes"] and not graph["edges"]:
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                        f"No DFG found for method: {method_full_name}", code="empty_result"
                    ))
                    return
                response_body: dict = {
                    "nodes": graph["nodes"],
                    "edges": graph["edges"],
                    "method_full_name": method_full_name,
                }
                node_ids = [n["id"] for n in graph["nodes"]]
                try:
                    response_body["metadata"] = self._fetch_node_metadata(node_ids)
                except Exception:
                    response_body["metadata"] = {}
                self._send_json(HTTPStatus.OK, response_body)
        except httpx.TimeoutException:
            self._send_json(HTTPStatus.GATEWAY_TIMEOUT, _json_error("query timed out", code="query_timeout"))
            return
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="joern_error"))
            return

    def _handle_graph_pdg(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        method_full_name = str(data.get("method_full_name", "")).strip()
        if not method_full_name:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: method_full_name"))
            return

        sample_id = data.get("sample_id", "")
        escaped = method_full_name.replace("\\", "\\\\").replace('"', '\\"')
        query = f'cpg.method.fullName("{escaped}").dotPdg.l'

        self._log_event("graph_pdg_request", method_full_name=method_full_name, sample_id=sample_id)

        try:
            with self.repl_semaphore:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": query},
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )

            resp.raise_for_status()
            resp_json = resp.json()
            success = resp_json.get("success", True)
            if isinstance(success, str):
                success = success.strip().lower() in ("true", "1", "yes")
            if not success:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"PDG query failed for method: {method_full_name}", code="query_failed"
                ))
                return

            stdout = resp_json.get("stdout", "")
            graph = _dot_to_graph(stdout)

            if not graph["nodes"] and not graph["edges"]:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"No PDG found for method: {method_full_name}", code="empty_result"
                ))
                return

            response_body: dict = {
                "nodes": graph["nodes"],
                "edges": graph["edges"],
                "method_full_name": method_full_name,
            }
            node_ids = [n["id"] for n in graph["nodes"]]
            try:
                response_body["metadata"] = self._fetch_node_metadata(node_ids)
            except Exception:
                response_body["metadata"] = {}

            self._send_json(HTTPStatus.OK, response_body)
        except httpx.TimeoutException:
            self._send_json(HTTPStatus.GATEWAY_TIMEOUT, _json_error("query timed out", code="query_timeout"))
            return
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="joern_error"))
            return

    def _handle_graph_ast(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        method_full_name = str(data.get("method_full_name", "")).strip()
        if not method_full_name:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: method_full_name"))
            return

        sample_id = data.get("sample_id", "")
        escaped = method_full_name.replace("\\", "\\\\").replace('"', '\\"')
        query = (
            f'cpg.method.fullName("{escaped}").ast.map(node => '
            f"(node.id, node.code, node.lineNumber, node.columnNumber, "
            f"node.order, node.label, "
            f"node.astParent.id)"
            f").l"
        )

        self._log_event("graph_ast_request", method_full_name=method_full_name, sample_id=sample_id)

        try:
            with self.repl_semaphore:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": query},
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )

            resp.raise_for_status()
            resp_json = resp.json()
            success = resp_json.get("success", True)
            if isinstance(success, str):
                success = success.strip().lower() in ("true", "1", "yes")
            if not success:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"AST query failed for method: {method_full_name}", code="query_failed"
                ))
                return

            stdout = resp_json.get("stdout", "")
            nodes, edges, metadata = _parse_ast_tuples(stdout)

            if not nodes:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, _json_error(
                    f"No AST found for method: {method_full_name}", code="empty_result"
                ))
                return

            self._send_json(HTTPStatus.OK, {
                "nodes": nodes,
                "edges": edges,
                "metadata": metadata,
                "method_full_name": method_full_name,
            })
        except httpx.TimeoutException:
            self._send_json(HTTPStatus.GATEWAY_TIMEOUT, _json_error("query timed out", code="query_timeout"))
            return
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="joern_error"))
            return

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        req_path = self._request_path()

        if req_path == "/parse/repo":
            self._handle_parse_repo()
            return

        if req_path == "/parse/repo/upload":
            self._handle_parse_repo_upload()
            return

        if req_path == "/parse":
            self._handle_parse()
            return

        if req_path == "/cleanup":
            self._handle_cleanup()
            return

        if self.path == "/graph/cfg":
            self._handle_graph_cfg()
            return

        if self.path == "/graph/dfg" or self.path == "/graph/ddg":
            self._handle_graph_dfg()
            return

        if self.path == "/graph/pdg":
            self._handle_graph_pdg()
            return

        if self.path == "/graph/ast":
            self._handle_graph_ast()
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
            request_id = _request_id(self)
            affinity_key = _affinity_key(self)

            # Try cache hit for cacheable queries
            if self.query_cache and self._should_cache(query_class):
                query_hash = self._query_hash(query_str)
                cached_result = self.query_cache.get(affinity_key, query_hash)
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
                if query_class != "importCpg":
                    ok, activate_err = self._activate_session_cpg_if_needed(affinity_key)
                    if not ok:
                        self._send_json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            _json_error(
                                activate_err or "session cpg activation failed",
                                code="session_cpg_activation_failed",
                            ),
                        )
                        return
                resp = httpx.post(
                    self.internal_url,
                    content=body,
                    headers=_upstream_headers(self),
                    timeout=self.query_timeout_sec,
                )
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            if self.metrics is not None:
                self.metrics.observe(
                    "joern_proxy_query_sync_duration_seconds",
                    latency_ms / 1000.0,
                    labels={"query_class": query_class},
                )
                self.metrics.inc(
                    "joern_proxy_query_sync_requests",
                    labels={"status": str(resp.status_code), "query_class": query_class},
                )
            # Preserve body; clients expect Joern's /query-sync JSON shape.
            resp_json = resp.json()

            # Strip ANSI escapes from stdout — Joern REPL wraps output in
            # terminal color codes meaningless to API consumers. Schema unchanged.
            if isinstance(resp_json, dict) and "stdout" in resp_json and isinstance(resp_json["stdout"], str):
                resp_json["stdout"] = _strip_ansi(resp_json["stdout"])

            # Extract success flag — Joern signals query errors via success=false at HTTP 200.
            success = None
            if isinstance(resp_json, dict):
                raw_success = resp_json.get("success")
                if isinstance(raw_success, bool):
                    success = raw_success
                elif isinstance(raw_success, str):
                    success = raw_success.strip().lower() in ("true", "1", "yes")

            # Map Joern's HTTP 200 + success=false to 422 so callers can detect query errors
            # without parsing the body (wrong syntax, runtime errors, etc.).
            out_status = resp.status_code
            if resp.status_code == 200 and success is False:
                out_status = HTTPStatus.UNPROCESSABLE_ENTITY

            # Cache only genuinely successful responses.
            if self.query_cache and self._should_cache(query_class) and out_status == 200:
                query_hash = self._query_hash(query_str)
                self.query_cache.put(affinity_key, query_hash, resp_json)

            if query_class == "importCpg" and out_status == 200:
                imported_path = self._extract_import_cpg_path(query_str)
                if imported_path:
                    key = affinity_key
                    if key == "default":
                        derived = _sample_id_from_cpg_path(imported_path)
                        if derived:
                            key = derived
                    self.__class__._affinity_cpg_path[key] = imported_path
                    self.__class__._active_affinity_key = key
                    self.__class__._active_cpg_path = imported_path
                    if self.metrics is not None:
                        self.metrics.set_gauge(
                            "joern_proxy_affinity_map_size",
                            float(len(self.__class__._affinity_cpg_path)),
                        )

            self._log_event(
                "query_sync",
                request_id=request_id,
                query_class=query_class,
                query_preview=query_preview,
                status_code=out_status,
                success=success,
                latency_ms=latency_ms,
                cache_hit=False,
            )
            self._send_json(out_status, resp_json)
        except httpx.TimeoutException as e:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            self._log_event(
                "query_sync_error",
                query_class=query_class,
                latency_ms=latency_ms,
                error_type="TimeoutException",
                error=str(e),
            )
            if self.metrics is not None:
                self.metrics.inc("joern_proxy_query_sync_requests", labels={"status": "504", "query_class": query_class})
            self._send_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                {**_json_error(str(e), code="upstream_timeout"), "request_id": request_id},
            )
        except httpx.HTTPError as e:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            self._log_event(
                "query_sync_error",
                request_id=request_id,
                query_class=query_class,
                latency_ms=latency_ms,
                error_type=type(e).__name__,
                error=str(e),
            )
            if self.metrics is not None:
                self.metrics.inc("joern_proxy_query_sync_requests", labels={"status": "502", "query_class": query_class})
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {**_json_error(str(e), code="upstream_unreachable"), "request_id": request_id},
            )
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            self._log_event(
                "query_sync_error",
                request_id=request_id,
                query_class=query_class,
                latency_ms=latency_ms,
                error_type=type(e).__name__,
                error=str(e),
            )
            if self.metrics is not None:
                self.metrics.inc("joern_proxy_query_sync_requests", labels={"status": "502", "query_class": query_class})
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {**_json_error(str(e), code="upstream_error"), "request_id": request_id},
            )

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
    parse_repo_timeout_sec = _env_int("JOERN_PARSE_REPO_TIMEOUT_SEC", 1800)
    parse_repo_max_files = _env_int("PARSE_REPO_MAX_FILES", 2000)
    parse_repo_max_bytes = _env_int("PARSE_REPO_MAX_BYTES", 50_000_000)
    parse_repo_max_archive_mb = _env_int("PARSE_REPO_MAX_ARCHIVE_MB", 500)
    parse_repo_upload_ttl_hours = _env_int("PARSE_REPO_UPLOAD_TTL_HOURS", 24)
    repo_uploads_dir = _env_str("PARSE_REPO_UPLOADS_DIR", "/workspace/repo-uploads")
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
    JoernProxyHandler.parse_repo_timeout_sec = parse_repo_timeout_sec
    JoernProxyHandler.parse_repo_max_files = parse_repo_max_files
    JoernProxyHandler.parse_repo_max_bytes = parse_repo_max_bytes
    JoernProxyHandler.parse_repo_max_archive_bytes = parse_repo_max_archive_mb * 1024 * 1024
    JoernProxyHandler.parse_repo_upload_ttl_hours = parse_repo_upload_ttl_hours
    JoernProxyHandler.repo_uploads_dir = repo_uploads_dir
    JoernProxyHandler.query_timeout_sec = query_timeout_sec
    JoernProxyHandler.health_probe_timeout_sec = _env_int("JOERN_HEALTH_PROBE_TIMEOUT_SEC", 5)
    JoernProxyHandler.metrics = PrometheusMetrics()
    Path(repo_uploads_dir).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((proxy_host, proxy_port), JoernProxyHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

