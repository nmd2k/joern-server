"""POST /parse/repo and upload handler logic."""

from __future__ import annotations

import datetime
import json
import re
import shutil
import tempfile
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional

from joern_server.cpg import cpg_copy, cpg_remove, get_hash_lock, safe_sample_id
from joern_server.parse.language import _normalize_language
from joern_server.parse.metrics import record_parse_request
from joern_server.parse.runner import ParseTimeoutError, run_joern_parse
from joern_server.parse.tree import (
    canonical_tree_hash,
    collect_tree_files,
    extract_archive,
    is_under_allowed_root,
    materialize_tree,
    parse_allowed_roots,
    parse_jsonl_repo_files,
    parse_multipart_archive,
)
from joern_server.state import AppState
from joern_server.util.errors import json_error
from joern_server.util.query import query_bool


def _upload_meta_path(state: AppState, upload_id: str) -> Path:
    return Path(state.settings.repo_uploads_dir) / upload_id / "meta.json"


def _upload_tree_path(state: AppState, upload_id: str) -> Path:
    return Path(state.settings.repo_uploads_dir) / upload_id / "tree"


def load_upload_meta(state: AppState, upload_id: str) -> tuple[Optional[dict], Optional[dict]]:
    safe_id = re.sub(r"[^a-zA-Z0-9-]", "", upload_id)
    if not safe_id or safe_id != upload_id:
        return None, json_error("invalid upload_id", code="bad_request")
    meta_path = _upload_meta_path(state, upload_id)
    if not meta_path.exists():
        return None, json_error(f"upload not found: {upload_id}", code="upload_not_found")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, json_error(f"corrupt upload metadata: {exc}", code="bad_request")
    expires_at = meta.get("expires_at", "")
    try:
        exp_dt = datetime.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        if now >= exp_dt:
            return None, json_error(f"upload expired: {upload_id}", code="upload_expired")
    except Exception:
        return None, json_error("invalid upload expiry metadata", code="bad_request")
    return meta, None


def execute_repo_parse(
    state: AppState,
    *,
    sample_id: str,
    files: dict[str, str],
    language: str,
    overwrite: bool,
    ingest_mode: str,
) -> tuple[int, dict[str, Any]]:
    t0 = time.perf_counter()
    source_hash = canonical_tree_hash(files)
    cpg_out = Path(state.settings.cpg_out_dir) / sample_id
    file_count = len(files)

    def finish(http_status: int, body: dict[str, Any], *, cache: bool = False) -> tuple[int, dict[str, Any]]:
        record_parse_request(
            state,
            status=str(http_status),
            language=language or None,
            cache_hit=cache,
            duration_sec=time.perf_counter() - t0,
        )
        return http_status, body

    hash_lock = get_hash_lock(source_hash)
    with hash_lock:
        if state.cpg_registry is not None:
            entry = state.cpg_registry.lookup(source_hash)
            if entry is not None:
                archive_path = Path(entry["archive_path"])
                if archive_path.exists():
                    try:
                        cpg_copy(archive_path, cpg_out)
                        now = datetime.datetime.utcnow().isoformat() + "Z"
                        entry["last_used"] = now
                        state.cpg_registry.register(source_hash, entry)
                        with state.sid_hash_lock:
                            state.sid_to_hash[sample_id] = source_hash
                        return finish(
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
                            cache=True,
                        )
                    except Exception:
                        cpg_remove(cpg_out)

    if cpg_out.exists() and not overwrite:
        with state.sid_hash_lock:
            existing_hash = state.sid_to_hash.get(sample_id)
        if existing_hash == source_hash:
            with state.sid_hash_lock:
                state.sid_to_hash[sample_id] = source_hash
            return finish(
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
                cache=True,
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

    tmp_src_dir = Path(tempfile.mkdtemp(prefix=f"joern-repo-{sample_id}-"))
    try:
        materialize_tree(files, tmp_src_dir)
        try:
            result = run_joern_parse(
                state.settings.parse_bin,
                tmp_src_dir,
                cpg_out,
                language=language,
                timeout_sec=state.settings.parse_repo_timeout_sec,
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
            "parse_mode": "repo",
            "ingest_mode": ingest_mode,
            "file_count": file_count,
        }
        if result.ok:
            with state.sid_hash_lock:
                state.sid_to_hash[sample_id] = source_hash
        return finish(http_status, body)
    except Exception as exc:
        return finish(HTTPStatus.BAD_GATEWAY, json_error(str(exc), code="parse_failed"))
    finally:
        shutil.rmtree(tmp_src_dir, ignore_errors=True)


def handle_parse_repo_jsonl(
    state: AppState,
    *,
    sample_id_raw: str,
    language: str,
    overwrite: bool,
    body: bytes,
) -> tuple[int, dict[str, Any]]:
    if not sample_id_raw:
        return HTTPStatus.BAD_REQUEST, json_error("missing required query param: sample_id")
    sample_id = safe_sample_id(sample_id_raw)
    files, err = parse_jsonl_repo_files(
        body,
        max_files=state.settings.parse_repo_max_files,
        max_bytes=state.settings.parse_repo_max_bytes,
    )
    if err is not None or files is None:
        status = (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            if err and err.get("code") == "payload_too_large"
            else HTTPStatus.BAD_REQUEST
        )
        return status, err or json_error("invalid request")
    return execute_repo_parse(
        state,
        sample_id=sample_id,
        files=files,
        language=language,
        overwrite=overwrite,
        ingest_mode="jsonl",
    )


def handle_parse_repo_json(state: AppState, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    has_upload = bool(str(data.get("upload_id", "")).strip())
    has_source_root = bool(str(data.get("source_root", "")).strip())
    if has_upload and has_source_root:
        return HTTPStatus.BAD_REQUEST, json_error("upload_id and source_root are mutually exclusive", code="bad_request")
    if not has_upload and not has_source_root:
        return HTTPStatus.BAD_REQUEST, json_error("JSON body requires upload_id or source_root", code="bad_request")

    sample_id_raw = str(data.get("sample_id", "")).strip()
    if not sample_id_raw:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: sample_id")
    sample_id = safe_sample_id(sample_id_raw)
    language = _normalize_language(str(data.get("language", "")).strip())
    overwrite = bool(data.get("overwrite", False))

    if has_upload:
        upload_id = str(data.get("upload_id", "")).strip()
        _meta, err = load_upload_meta(state, upload_id)
        if err is not None:
            code = err.get("code", "bad_request")
            if code == "upload_not_found":
                status = HTTPStatus.NOT_FOUND
            elif code == "upload_expired":
                status = HTTPStatus.GONE
            else:
                status = HTTPStatus.BAD_REQUEST
            return status, err
        tree_root = _upload_tree_path(state, upload_id)
        files, err = collect_tree_files(
            tree_root,
            max_files=state.settings.parse_repo_max_files,
            max_bytes=state.settings.parse_repo_max_bytes,
        )
        if err is not None or files is None:
            status = (
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if err and err.get("code") == "payload_too_large"
                else HTTPStatus.BAD_REQUEST
            )
            return status, err or json_error("invalid request")
        return execute_repo_parse(
            state,
            sample_id=sample_id,
            files=files,
            language=language,
            overwrite=overwrite,
            ingest_mode="upload",
        )

    source_root = Path(str(data.get("source_root", "")).strip())
    allowed = parse_allowed_roots()
    if not source_root.exists():
        return HTTPStatus.BAD_REQUEST, json_error(
            f"source_root does not exist: {source_root}",
            code="invalid_source_root",
        )
    if not is_under_allowed_root(source_root, allowed):
        return HTTPStatus.BAD_REQUEST, json_error(
            f"source_root must be under allowed roots: {[str(r) for r in allowed]}",
            code="invalid_source_root",
        )
    files, err = collect_tree_files(
        source_root,
        max_files=state.settings.parse_repo_max_files,
        max_bytes=state.settings.parse_repo_max_bytes,
    )
    if err is not None or files is None:
        status = (
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            if err and err.get("code") == "payload_too_large"
            else HTTPStatus.BAD_REQUEST
        )
        return status, err or json_error("invalid request")
    return execute_repo_parse(
        state,
        sample_id=sample_id,
        files=files,
        language=language,
        overwrite=overwrite,
        ingest_mode="source_root",
    )


def handle_parse_repo_upload(
    state: AppState,
    *,
    body: bytes,
    content_type: str,
) -> tuple[int, dict[str, Any]]:
    if len(body) > state.settings.parse_repo_max_archive_bytes:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, json_error(
            f"archive exceeds max size ({state.settings.parse_repo_max_archive_bytes} bytes)",
            code="payload_too_large",
        )
    archive_bytes, err = parse_multipart_archive(body, content_type)
    if err is not None or archive_bytes is None:
        return HTTPStatus.BAD_REQUEST, err or json_error("invalid request")
    if not archive_bytes:
        return HTTPStatus.BAD_REQUEST, json_error("empty archive", code="bad_request")

    upload_id = str(uuid.uuid4())
    upload_dir = Path(state.settings.repo_uploads_dir) / upload_id
    tree_dir = upload_dir / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    extract_err = extract_archive(archive_bytes, tree_dir)
    if extract_err is not None:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return HTTPStatus.BAD_REQUEST, extract_err

    expires_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=state.settings.parse_repo_upload_ttl_hours)
    ).isoformat().replace("+00:00", "Z")
    meta = {
        "upload_id": upload_id,
        "expires_at": expires_at,
        "bytes_stored": len(archive_bytes),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _upload_meta_path(state, upload_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return HTTPStatus.OK, {
        "ok": True,
        "upload_id": upload_id,
        "expires_at": expires_at,
        "bytes_stored": len(archive_bytes),
    }


def parse_repo_query_params(
    sample_id: Optional[str],
    language: Optional[str],
    overwrite: Optional[str],
) -> tuple[str, str, bool]:
    return (
        (sample_id or "").strip(),
        _normalize_language((language or "").strip()),
        query_bool(overwrite, default=False),
    )
