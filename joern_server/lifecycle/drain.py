"""Graceful drain before supervised Joern JVM restart."""

from __future__ import annotations

import json
import threading
import time

from joern_server.state import AppState


def is_draining(state: AppState) -> bool:
    return state.draining


def begin_draining(state: AppState) -> bool:
    """Mark replica as draining. Returns False if already draining or restart scheduled."""
    with state.drain_lock:
        if state.draining or state.restart_scheduled:
            return False
        state.draining = True
        state.restart_scheduled = True
    if state.metrics is not None:
        state.metrics.set_gauge("joern_proxy_draining", 1.0)
    print(
        json.dumps({
            "component": "joern-proxy",
            "event": "replica_draining_started",
            "drain_sec": state.settings.joern_drain_sec,
        }),
        flush=True,
    )
    return True


def schedule_joern_restart_after_drain(state: AppState, *, reason: str) -> bool:
    """Reject new work immediately, wait for HAProxy mark-down, then restart Joern."""
    if not begin_draining(state):
        return False

    drain_sec = state.settings.joern_drain_sec

    def _worker() -> None:
        time.sleep(drain_sec)
        from joern_server.lifecycle.joern_restart import request_joern_restart

        print(
            json.dumps({
                "component": "joern-proxy",
                "event": "replica_drain_complete",
                "drain_sec": drain_sec,
                "reason": reason,
            }),
            flush=True,
        )
        request_joern_restart(reason=reason)

    threading.Thread(
        target=_worker,
        name="joern-drain-restart",
        daemon=True,
    ).start()
    return True
