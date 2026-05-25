"""POST /cleanup handler logic."""

from __future__ import annotations

import datetime
import json
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional

from joern_server.cpg import (
    cpg_copy,
    cpg_remove,
    cpg_size_bytes,
    get_hash_lock,
    joern_hash_sidecar,
    safe_sample_id,
)
from joern_server.parse.metrics import record_cleanup_request
from joern_server.state import AppState
from joern_server.upstream import joern as upstream
from joern_server.util.errors import json_error


def clear_affinity_state(state: AppState, sample_id: str, *, request_headers: dict[str, str]) -> None:
    """Drop in-memory CPG binding for sample_id and best-effort close REPL graph."""
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    target = str(cpg_out)
    to_remove: list[str] = []
    for key, path in list(state.affinity_cpg_path.items()):
        if key == sample_id or path.rstrip("/") == target.rstrip("/"):
            to_remove.append(key)
    for key in to_remove:
        state.affinity_cpg_path.pop(key, None)

    active_path = state.active_cpg_path
    if active_path and active_path.rstrip("/") == target.rstrip("/"):
        with state.repl_semaphore:
            try:
                upstream.post_query_sync(
                    state.internal_url,
                    query="close",
                    headers=request_headers,
                    timeout_sec=min(30, state.settings.query_timeout_sec),
                )
            except Exception:
                pass
        state.active_cpg_path = None
        state.active_affinity_key = None

    if state.metrics is not None:
        state.metrics.set_gauge(
            "joern_proxy_affinity_map_size",
            float(len(state.affinity_cpg_path)),
        )
        state.metrics.set_gauge(
            "joern_proxy_active_cpg_loaded",
            1.0 if state.active_cpg_path is not None else 0.0,
        )


def handle_cleanup(
    state: AppState,
    data: dict[str, Any],
    *,
    request_headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    sample_id_raw = str(data.get("sample_id", "")).strip()
    if not sample_id_raw:
        record_cleanup_request(state, status="400", archived=False)
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: sample_id")

    archive_flag = bool(data.get("archive", False))
    sample_id = safe_sample_id(sample_id_raw)
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    existed = cpg_out.exists()
    try:
        if archive_flag and existed and state.cpg_registry is not None:
            source_hash: Optional[str] = None
            with state.sid_hash_lock:
                source_hash = state.sid_to_hash.get(sample_id)
            if source_hash is None:
                hash_file = joern_hash_sidecar(cpg_out)
                try:
                    if hash_file.exists():
                        source_hash = hash_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
            if source_hash is None:
                try:
                    for h, entry in state.cpg_registry.all_entries():
                        if entry.get("sample_id") == sample_id:
                            source_hash = h
                            break
                except Exception as exc:
                    print(json.dumps({"component":"joern-proxy","event":"registry_all_entries_error","error":str(exc)}), flush=True)

            if source_hash is not None:
                archive_path = Path(state.settings.cpg_archive_dir) / source_hash
                hash_lock = get_hash_lock(source_hash)
                with hash_lock:
                    if archive_path.exists():
                        size_bytes = cpg_size_bytes(archive_path)
                    else:
                        tmp_archive = archive_path.with_suffix(".tmp")
                        if tmp_archive.exists():
                            cpg_remove(tmp_archive)
                        cpg_copy(cpg_out, tmp_archive)
                        tmp_archive.rename(archive_path)
                        size_bytes = cpg_size_bytes(archive_path)
                    now = datetime.datetime.utcnow().isoformat() + "Z"
                    try:
                        state.cpg_registry.register(source_hash, {
                            "archive_path": str(archive_path),
                            "sample_id": sample_id,
                            "archived_at": now,
                            "last_used": now,
                            "size_bytes": size_bytes,
                        })
                    except Exception as exc:
                        print(json.dumps({"component":"joern-proxy","event":"registry_register_error","error":str(exc)}), flush=True)
                cpg_remove(cpg_out)
                cpg_remove(joern_hash_sidecar(cpg_out))
                cpg_remove(Path(state.settings.cpg_out_dir).parent / ".joern-src" / sample_id)
                try:
                    state.cpg_registry.evict_if_needed()
                except Exception as exc:
                    print(json.dumps({"component":"joern-proxy","event":"registry_evict_error","error":str(exc)}), flush=True)
                with state.sid_hash_lock:
                    state.sid_to_hash.pop(sample_id, None)
                clear_affinity_state(state, sample_id, request_headers=request_headers)
                record_cleanup_request(state, status="200", archived=True)
                return HTTPStatus.OK, {
                    "ok": True,
                    "sample_id": sample_id,
                    "cpg_path": str(cpg_out),
                    "deleted": False,
                    "archived": True,
                    "source_hash": source_hash,
                    "archive_path": str(archive_path),
                }

        if existed:
            cpg_remove(cpg_out)
            cpg_remove(joern_hash_sidecar(cpg_out))
            cpg_remove(Path(state.settings.cpg_out_dir).parent / ".joern-src" / sample_id)
        with state.sid_hash_lock:
            state.sid_to_hash.pop(sample_id, None)
        clear_affinity_state(state, sample_id, request_headers=request_headers)
        record_cleanup_request(state, status="200", archived=False)
        return HTTPStatus.OK, {
            "ok": True,
            "sample_id": sample_id,
            "cpg_path": str(cpg_out),
            "deleted": bool(existed),
            "archived": False,
        }
    except Exception as exc:
        record_cleanup_request(state, status="502", archived=archive_flag)
        return HTTPStatus.BAD_GATEWAY, json_error(str(exc), code="cleanup_failed")
