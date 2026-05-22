"""Query classification and cache key helpers for /query-sync."""

from __future__ import annotations

import hashlib
import re

_IMPORT_CPG_RE = re.compile(r"""^\s*importCpg\(\s*["']([^"']+)["']\s*\)\s*$""")


def classify_query(query: str) -> str:
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


def should_cache(query_class: str) -> bool:
    """Determine if query should be cached. Skip load_cpg, importCpg, cleanup."""
    skip_classes = {"load_cpg", "importCpg", "cleanup", "empty", "unknown", "invalid_json"}
    return query_class not in skip_classes


def query_hash(query: str) -> str:
    """Generate md5 hash of query for cache key."""
    return hashlib.md5(query.encode("utf-8")).hexdigest()


def preview_query(query: str, limit: int = 180) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip())
    if len(q) <= limit:
        return q
    return q[:limit] + "...(truncated)"


def extract_import_cpg_path(query: str) -> str | None:
    m = _IMPORT_CPG_RE.match((query or "").strip())
    if not m:
        return None
    return m.group(1).strip() or None
