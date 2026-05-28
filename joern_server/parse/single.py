"""POST /parse handler logic."""

from __future__ import annotations

import datetime
import hashlib
import json
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional

from joern_server.cpg import cpg_copy, cpg_remove, get_hash_lock, joern_hash_sidecar, safe_sample_id
from joern_server.parse.language import _default_filename, _normalize_language
from joern_server.parse.metrics import record_parse_request
from joern_server.parse.runner import ParseTimeoutError, run_joern_parse
from joern_server.state import AppState
from joern_server.util.errors import json_error


def _restore_from_archive(
    state: AppState,
    sample_id: str,
    source_hash: str,
) -> Optional[Path]:
    """Copy CPG from archive to cpg-out/<sample_id> if archive entry exists. Returns cpg_out path on success, None on miss/error."""
    if state.cpg_registry is None:
        return None
    try:
        entry = state.cpg_registry.lookup(source_hash)
    except Exception as exc:
        print(json.dumps({"component": "joern-proxy", "event": "registry_lookup_error", "error": str(exc)}), flush=True)
        return None
    if entry is None:
        return None
    archive_path = Path(entry["archive_path"])
    if not archive_path.exists():
        return None
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    try:
        cpg_copy(archive_path, cpg_out)
        now = datetime.datetime.utcnow().isoformat() + "Z"
        entry["last_used"] = now
        try:
            state.cpg_registry.register(source_hash, entry)
        except Exception as exc:
            print(json.dumps({"component": "joern-proxy", "event": "registry_register_error", "error": str(exc)}), flush=True)
        state.record_sid_hash(sample_id, source_hash)
        try:
            joern_hash_sidecar(cpg_out).write_text(source_hash, encoding="utf-8")
        except Exception:
            pass
        return cpg_out
    except Exception:
        cpg_remove(cpg_out)
        return None


def handle_restore_by_affinity(state: AppState, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Handle parse request where only sample_id is provided (no source_code).

    Looks up sample_id → source_hash (registry sid_map), then restores CPG from
    archive to cpg-out. Returns cpg_path on success, 404 if not in archive.
    """
    sample_id_raw = str(data.get("sample_id", "")).strip()
    if not sample_id_raw:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: sample_id")
    sample_id = safe_sample_id(sample_id_raw)

    # Look up source_hash from persistent sid_map
    source_hash: Optional[str] = None
    with state.sid_hash_lock:
        source_hash = state.sid_to_hash.get(sample_id)
    if source_hash is None and state.cpg_registry is not None:
        try:
            source_hash = state.cpg_registry.lookup_by_sample_id(sample_id)
        except Exception:
            pass

    if source_hash is None:
        return HTTPStatus.NOT_FOUND, json_error(
            f"no archive entry for sample_id '{sample_id}'; send source_code or source_root to parse first",
            code="not_archived",
        )

    # If cpg-out already has the right CPG, skip copy
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    if cpg_out.exists():
        try:
            sidecar = joern_hash_sidecar(cpg_out)
            existing = sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else None
        except Exception:
            existing = None
        if existing == source_hash:
            return HTTPStatus.OK, {
                "ok": True,
                "sample_id": sample_id,
                "cpg_path": str(cpg_out),
                "cache_hit": True,
                "source_hash": source_hash,
                "restore": "cpg_out_hit",
            }

    hash_lock = get_hash_lock(source_hash)
    with hash_lock:
        restored = _restore_from_archive(state, sample_id, source_hash)

    if restored is None:
        return HTTPStatus.NOT_FOUND, json_error(
            f"archive entry for sample_id '{sample_id}' no longer exists on disk; re-parse required",
            code="archive_missing",
        )

    return HTTPStatus.OK, {
        "ok": True,
        "sample_id": sample_id,
        "cpg_path": str(restored),
        "cache_hit": True,
        "source_hash": source_hash,
        "restore": "archive",
    }


def handle_parse(state: AppState, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Run single-snippet parse; return (HTTP status, JSON body)."""
    t0 = time.perf_counter()
    language: Optional[str] = None
    cache_hit = False
    status_label = "400"

    def finish(http_status: int, body: dict[str, Any], *, cache: bool = False, lang: Optional[str] = None) -> tuple[int, dict[str, Any]]:
        nonlocal status_label, cache_hit, language
        cache_hit = cache
        language = lang
        status_label = str(http_status.value)
        record_parse_request(
            state,
            status=status_label,
            language=language,
            cache_hit=cache_hit,
            duration_sec=time.perf_counter() - t0,
        )
        return http_status, body

    sample_id_raw = str(data.get("sample_id", "")).strip()
    source_code = data.get("source_code")
    language = _normalize_language(str(data.get("language", "")).strip())
    filename = str(data.get("filename", "")).strip() or _default_filename(language)
    overwrite = bool(data.get("overwrite", False))

    if not sample_id_raw:
        return finish(HTTPStatus.BAD_REQUEST, json_error("missing required field: sample_id"))

    # Restore-by-affinity path: no source_code provided → look up archive by sample_id
    if source_code is None or (isinstance(source_code, str) and not source_code.strip()):
        status, body = handle_restore_by_affinity(state, data)
        # wrap through finish for metrics recording
        return finish(status, body, cache=body.get("cache_hit", False), lang=language or None)

    if not isinstance(source_code, str) or not source_code.strip():
        return finish(HTTPStatus.BAD_REQUEST, json_error("missing required field: source_code"))

    sample_id = safe_sample_id(sample_id_raw)
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()

    hash_lock = get_hash_lock(source_hash)
    with hash_lock:
        if state.cpg_registry is not None:
            try:
                entry = state.cpg_registry.lookup(source_hash)
            except Exception as exc:
                entry = None
                print(json.dumps({"component": "joern-proxy", "event": "registry_lookup_error", "error": str(exc)}), flush=True)
            if entry is not None:
                restored = _restore_from_archive(state, sample_id, source_hash)
                if restored is not None:
                    return finish(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "sample_id": sample_id,
                            "cpg_path": str(restored),
                            "language": language or None,
                            "cache_hit": True,
                            "source_hash": source_hash,
                        },
                        cache=True,
                        lang=language or None,
                    )

    if cpg_out.exists() and not overwrite:
        with state.sid_hash_lock:
            existing_hash = state.sid_to_hash.get(sample_id)
        if existing_hash is None:
            sidecar = joern_hash_sidecar(cpg_out)
            try:
                if sidecar.exists():
                    existing_hash = sidecar.read_text(encoding="utf-8").strip() or None
                    if existing_hash:
                        state.record_sid_hash(sample_id, existing_hash)
            except Exception:
                existing_hash = None
        if existing_hash == source_hash:
            state.record_sid_hash(sample_id, source_hash)
            return finish(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "sample_id": sample_id,
                    "cpg_path": str(cpg_out),
                    "language": language or None,
                    "cache_hit": True,
                    "source_hash": source_hash,
                },
                cache=True,
                lang=language or None,
            )
        return finish(
            HTTPStatus.CONFLICT,
            json_error(
                f"CPG output already exists at {cpg_out}; pass overwrite=true to replace",
                code="cpg_exists",
            ),
        )
    if cpg_out.exists() and overwrite:
        cpg_remove(cpg_out)

    src_base = Path(state.settings.cpg_out_dir).parent / ".joern-src"
    src_dir = src_base / sample_id
    if src_dir.exists():
        cpg_remove(src_dir)
    src_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_path = src_dir / Path(filename).name
        src_path.write_text(source_code, encoding="utf-8", newline="\n")
        with state.parse_semaphore:
            try:
                result = run_joern_parse(
                    state.settings.parse_bin,
                    src_dir,
                    cpg_out,
                    language=language,
                    timeout_sec=state.settings.parse_timeout_sec,
                    jvm_xmx=state.settings.parse_jvm_xmx,
                )
            except ParseTimeoutError as exc:
                return finish(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    json_error(str(exc), code="parse_timeout"),
                )
        http_status = HTTPStatus.OK if result.ok else HTTPStatus.BAD_GATEWAY
        body: dict[str, Any] = {
            "ok": result.ok,
            "sample_id": sample_id,
            "cpg_path": str(cpg_out),
            "language": language or None,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "cache_hit": False,
            "source_hash": source_hash,
        }
        if result.ok:
            state.record_sid_hash(sample_id, source_hash)
            try:
                joern_hash_sidecar(cpg_out).write_text(source_hash, encoding="utf-8")
            except Exception:
                pass
        return finish(http_status, body, lang=language or None)
    except Exception as exc:
        return finish(HTTPStatus.BAD_GATEWAY, json_error(str(exc), code="parse_failed"))
