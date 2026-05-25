"""Session affinity CPG binding and activation."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from joern_server.cache.query_policy import extract_import_cpg_path
from joern_server.cpg.paths import sample_id_from_cpg_path
from joern_server.session.repl_lock import repl_lock
from joern_server.state import AppState
from joern_server.upstream import joern as upstream

LogFn = Callable[..., None]


def _parse_success(body: dict) -> bool:
    success = body.get("success", True)
    if isinstance(success, str):
        return success.strip().lower() in ("true", "1", "yes")
    return bool(success)


def activate_cpg(
    state: AppState,
    affinity_key: str,
    *,
    headers: dict[str, str],
    log_event: Optional[LogFn] = None,
) -> tuple[bool, Optional[str]]:
    """Ensure the REPL has the CPG bound to ``affinity_key`` before a query runs."""
    desired_cpg = state.affinity_cpg_path.get(affinity_key)
    active_cpg = state.active_cpg_path
    active_key = state.active_affinity_key
    timeout_sec = state.settings.query_timeout_sec

    if desired_cpg:
        if active_cpg != desired_cpg:
            if active_cpg is not None:
                try:
                    upstream.post_query_sync(
                        state.internal_url,
                        query="close",
                        headers=headers,
                        timeout_sec=min(30, timeout_sec),
                    )
                except Exception:
                    pass
                state.active_cpg_path = None
                if state.metrics is not None:
                    state.metrics.set_gauge("joern_proxy_active_cpg_loaded", 0.0)
            resp = upstream.post_query_sync(
                state.internal_url,
                query=f'importCpg("{desired_cpg}")',
                headers=headers,
                timeout_sec=timeout_sec,
            )
            body = resp.json()
            if resp.status_code != 200 or not _parse_success(body):
                if log_event is not None:
                    log_event(
                        "session_cpg_activate_failed",
                        affinity_key=affinity_key,
                        desired_cpg=desired_cpg,
                        status_code=resp.status_code,
                    )
                state.affinity_cpg_path.pop(affinity_key, None)
                state.active_affinity_key = None
                state.active_cpg_path = None
                if state.metrics is not None:
                    state.metrics.set_gauge("joern_proxy_active_cpg_loaded", 0.0)
                return False, f"failed to activate CPG for affinity {affinity_key}"
            state.active_cpg_path = desired_cpg
            if state.metrics is not None:
                state.metrics.set_gauge("joern_proxy_active_cpg_loaded", 1.0)
        state.active_affinity_key = affinity_key
        return True, None

    if active_cpg is not None and active_key != affinity_key:
        try:
            upstream.post_query_sync(
                state.internal_url,
                query="close",
                headers=headers,
                timeout_sec=timeout_sec,
            )
        except Exception:
            pass
        state.active_cpg_path = None
    state.active_affinity_key = affinity_key
    return True, None


def record_import_cpg_success(
    state: AppState,
    affinity_key: str,
    query: str,
) -> None:
    """Update affinity map after a successful importCpg query."""
    imported_path = extract_import_cpg_path(query)
    if not imported_path:
        return
    key = affinity_key
    if key == "default":
        derived = sample_id_from_cpg_path(imported_path)
        if derived:
            key = derived
    state.affinity_cpg_path[key] = imported_path
    state.active_affinity_key = key
    state.active_cpg_path = imported_path
    if state.metrics is not None:
        state.metrics.set_gauge(
            "joern_proxy_affinity_map_size",
            float(len(state.affinity_cpg_path)),
        )
        state.metrics.set_gauge("joern_proxy_active_cpg_loaded", 1.0)


def clear_affinity(
    state: AppState,
    sample_id: str,
    *,
    headers: dict[str, str],
) -> None:
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
        with repl_lock(state.repl_semaphore):
            try:
                upstream.post_query_sync(
                    state.internal_url,
                    query="close",
                    headers=headers,
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
