"""Repo tree validation, collection, archives, and materialization."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

from joern_server.util.env import env_str
from joern_server.util.errors import json_error


def validate_repo_path(path: str) -> Optional[str]:
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


def canonical_tree_hash(files: dict[str, str]) -> str:
    """SHA256 of sorted path + NUL + sha256(content) + newline per file."""
    h = hashlib.sha256()
    for path in sorted(files.keys()):
        content_hash = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(content_hash.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def parse_allowed_roots() -> list[Path]:
    raw = env_str("PARSE_REPO_ALLOWED_ROOTS", "/workspace/datasets")
    roots: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).resolve())
    return roots or [Path("/workspace/datasets").resolve()]


def is_under_allowed_root(candidate: Path, allowed_roots: list[Path]) -> bool:
    resolved = candidate.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def collect_tree_files(
    root: Path,
    *,
    max_files: int,
    max_bytes: int,
    include_extensions: Optional[list[str]] = None,
) -> tuple[Optional[dict[str, str]], Optional[dict]]:
    """Walk root and collect relative path → UTF-8 content. Enforce limits."""
    if not root.is_dir():
        return None, json_error(f"source path is not a directory: {root}", code="invalid_source_root")
    ext_allow: Optional[frozenset[str]] = None
    if include_extensions:
        ext_allow = frozenset(
            e if e.startswith(".") else f".{e}"
            for e in include_extensions
            if isinstance(e, str) and e.strip()
        )
    files: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ext_allow is not None and path.suffix.lower() not in ext_allow:
            continue
        rel = path.relative_to(root).as_posix()
        err = validate_repo_path(rel)
        if err is not None:
            return None, json_error(f"invalid path in tree: {rel}: {err}", code="invalid_path")
        if len(files) >= max_files:
            return None, json_error(
                f"repo exceeds max file count ({max_files})",
                code="payload_too_large",
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return None, json_error(f"failed to read {rel}: {exc}", code="bad_request")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        total_bytes += len(raw)
        if total_bytes > max_bytes:
            return None, json_error(
                f"repo exceeds max total bytes ({max_bytes})",
                code="payload_too_large",
            )
        files[rel] = text
    if not files:
        return None, json_error("repo tree contains no files", code="empty_tree")
    return files, None


def parse_multipart_archive(body: bytes, content_type: str) -> tuple[Optional[bytes], Optional[dict]]:
    """Extract the ``archive`` field from multipart/form-data."""
    if "multipart/form-data" not in (content_type or ""):
        return None, json_error("Content-Type must be multipart/form-data", code="bad_request")
    m = re.search(r"boundary=([^;\s]+)", content_type, re.IGNORECASE)
    if not m:
        return None, json_error("missing multipart boundary", code="bad_request")
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
    return None, json_error('missing multipart field "archive"', code="bad_request")


def extract_archive(archive_bytes: bytes, dest: Path, filename_hint: str = "") -> Optional[dict]:
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
        return json_error(f"failed to extract zip archive: {exc}", code="bad_request")
    try:
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:*") as tf:
            tf.extractall(dest, filter="data")
        return None
    except Exception as exc:
        return json_error(f"unsupported or corrupt archive: {exc}", code="bad_request")


def materialize_tree(files: dict[str, str], dest: Path) -> None:
    for rel_path, content in files.items():
        out_path = dest / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8", newline="\n")


def parse_jsonl_repo_files(
    body: bytes,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[Optional[dict[str, str]], Optional[dict]]:
    """Parse NDJSON body into path → content map."""
    files: dict[str, str] = {}
    total_bytes = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, json_error(f"invalid JSONL line: {exc}", code="bad_request")
        if not isinstance(obj, dict):
            return None, json_error("JSONL line must be a JSON object", code="bad_request")
        rel_path = obj.get("path")
        content = obj.get("content")
        if not isinstance(rel_path, str) or not isinstance(content, str):
            return None, json_error('each JSONL line requires "path" and "content" strings', code="bad_request")
        err = validate_repo_path(rel_path)
        if err is not None:
            return None, json_error(err, code="invalid_path")
        if len(files) >= max_files:
            return None, json_error(
                f"repo exceeds max file count ({max_files})",
                code="payload_too_large",
            )
        encoded_len = len(content.encode("utf-8"))
        total_bytes += encoded_len
        if total_bytes > max_bytes:
            return None, json_error(
                f"repo exceeds max total bytes ({max_bytes})",
                code="payload_too_large",
            )
        files[rel_path.replace("\\", "/")] = content

    if not files:
        return None, json_error("repo tree contains no files", code="empty_tree")
    return files, None
