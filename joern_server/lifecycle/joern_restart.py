"""Request a supervised Joern JVM restart to reclaim retained heap."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

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


def maybe_request_joern_restart_after_cleanup(
    state: AppState,
    *,
    sample_id: str,
) -> bool:
    """Restart Joern when idle after cleanup if container memory exceeds threshold."""
    threshold_mb = state.settings.joern_memory_restart_mb
    if threshold_mb <= 0:
        return False
    if state.active_cpg_path is not None:
        return False

    usage_bytes = read_container_memory_bytes()
    usage_mb = usage_bytes / (1024 * 1024)
    if usage_mb < threshold_mb:
        return False

    reason = f"cleanup:{sample_id}:usage_mb={usage_mb:.0f}:threshold_mb={threshold_mb}"
    scheduled = schedule_joern_restart_after_drain(state, reason=reason)
    if scheduled and state.metrics is not None:
        state.metrics.inc(
            "joern_proxy_joern_restart_requests",
            labels={"reason": "memory_threshold"},
        )
    return scheduled
