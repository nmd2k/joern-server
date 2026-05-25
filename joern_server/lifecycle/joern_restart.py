"""Request a supervised Joern JVM restart to reclaim retained heap.

Includes staggered-restart logic (random jitter + HAProxy VIP health gate)
to prevent the thundering-herd problem where multiple replicas cross the
memory threshold at the same time and all drain simultaneously.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from joern_server.lifecycle.drain import schedule_joern_restart_after_drain
from joern_server.state import AppState

RESTART_FLAG_PATH = Path(os.getenv("JOERN_RESTART_FLAG_PATH", "/tmp/joern-restart.requested"))


def read_container_memory_bytes() -> float:
    """Return cgroup memory usage for this container (bytes), or 0 if unknown."""
    for base in ("/sys/fs/cgroup",):
        usage_path = os.path.join(base, "memory.current")
        if not os.path.exists(usage_path):
            usage_path = os.path.join(base, "memory", "memory.usage_in_bytes")
        try:
            if os.path.exists(usage_path):
                with open(usage_path) as f:
                    raw = f.read().strip()
                    if raw.isdigit():
                        return float(raw)
        except OSError:
            pass
    return 0.0


def request_joern_restart(*, reason: str) -> None:
    """Signal unified-entrypoint to restart the Joern JVM (and proxy)."""
    payload = {
        "component": "joern-proxy",
        "event": "joern_restart_requested",
        "reason": reason,
        "ts_ms": int(time.time() * 1000),
    }
    try:
        print(json.dumps(payload), flush=True)
        RESTART_FLAG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        print(
            json.dumps({
                **payload,
                "event": "joern_restart_request_failed",
                "error": str(exc),
            }),
            flush=True,
        )


def _check_cluster_health(vip_url: str, *, timeout_sec: float = 3.0) -> bool:
    """Probe the HAProxy VIP to verify the cluster can absorb losing one replica.

    Returns True if the VIP responds 200 (at least one healthy backend).
    Returns True if vip_url is empty (no VIP configured — skip gating).
    Returns False if the VIP is unreachable or returns non-200 (cluster degraded).
    """
    if not vip_url:
        return True
    health_url = urljoin(vip_url.rstrip("/") + "/", "health")
    try:
        resp = httpx.get(health_url, timeout=timeout_sec)
        return resp.status_code == 200
    except Exception:
        return False


def _log_restart_deferred(
    *,
    sample_id: str,
    reason: str,
    usage_mb: float,
    threshold_mb: int,
) -> None:
    print(
        json.dumps({
            "component": "joern-proxy",
            "event": "restart_deferred",
            "sample_id": sample_id,
            "reason": reason,
            "usage_mb": round(usage_mb),
            "threshold_mb": threshold_mb,
        }),
        flush=True,
    )


def _defer_restart(
    state: AppState,
    *,
    sample_id: str,
    reason: str,
    usage_mb: float,
    threshold_mb: int,
) -> None:
    """Log deferral, bump metric, and unlock restart_scheduled so the next cleanup can retry."""
    _log_restart_deferred(
        sample_id=sample_id,
        reason=reason,
        usage_mb=usage_mb,
        threshold_mb=threshold_mb,
    )
    if state.metrics is not None:
        state.metrics.inc(
            "joern_proxy_restart_deferred_total",
            labels={"reason": reason},
        )
    with state.drain_lock:
        state.restart_scheduled = False


def _staggered_restart_worker(
    state: AppState,
    *,
    sample_id: str,
    initial_usage_mb: float,
) -> None:
    """Background thread: jitter, re-check memory, gate on cluster health, then drain."""
    threshold_mb = state.settings.joern_memory_restart_mb
    jitter_sec = state.settings.joern_restart_jitter_sec

    if jitter_sec > 0:
        delay = random.uniform(0, jitter_sec)
        print(
            json.dumps({
                "component": "joern-proxy",
                "event": "restart_jitter_wait",
                "sample_id": sample_id,
                "jitter_sec": round(delay, 1),
            }),
            flush=True,
        )
        time.sleep(delay)

    usage_bytes = read_container_memory_bytes()
    usage_mb = usage_bytes / (1024 * 1024)
    if usage_mb < threshold_mb:
        _defer_restart(
            state, sample_id=sample_id, reason="jitter_memory_recovered",
            usage_mb=usage_mb, threshold_mb=threshold_mb,
        )
        return

    if state.active_cpg_path is not None:
        _defer_restart(
            state, sample_id=sample_id, reason="cpg_loaded_during_jitter",
            usage_mb=usage_mb, threshold_mb=threshold_mb,
        )
        return

    vip_url = state.settings.joern_haproxy_vip
    if not _check_cluster_health(vip_url):
        _defer_restart(
            state, sample_id=sample_id, reason="cluster_degraded",
            usage_mb=usage_mb, threshold_mb=threshold_mb,
        )
        return

    reason = f"cleanup:{sample_id}:usage_mb={usage_mb:.0f}:threshold_mb={threshold_mb}"
    scheduled = schedule_joern_restart_after_drain(state, reason=reason)
    if scheduled:
        if state.metrics is not None:
            state.metrics.inc(
                "joern_proxy_joern_restart_requests",
                labels={"reason": "memory_threshold"},
            )
    else:
        with state.drain_lock:
            state.restart_scheduled = False


def maybe_request_joern_restart_after_cleanup(
    state: AppState,
    *,
    sample_id: str,
) -> bool:
    """Schedule a staggered Joern restart if container memory exceeds threshold.

    The restart is deferred to a background thread that applies random jitter
    and checks cluster health before committing to the drain+restart cycle.
    This prevents the thundering-herd problem where all replicas restart at once.
    """
    threshold_mb = state.settings.joern_memory_restart_mb
    if threshold_mb <= 0:
        return False
    if state.active_cpg_path is not None:
        return False

    usage_bytes = read_container_memory_bytes()
    usage_mb = usage_bytes / (1024 * 1024)
    if usage_mb < threshold_mb:
        return False

    with state.drain_lock:
        if state.draining or state.restart_scheduled:
            return False
        state.restart_scheduled = True

    threading.Thread(
        target=_staggered_restart_worker,
        args=(state,),
        kwargs={"sample_id": sample_id, "initial_usage_mb": usage_mb},
        name="joern-staggered-restart",
        daemon=True,
    ).start()
    return True
