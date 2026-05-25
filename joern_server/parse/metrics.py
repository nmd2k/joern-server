"""Prometheus metrics helpers for parse endpoints."""

from __future__ import annotations

from typing import Optional

from joern_server.state import AppState


def record_parse_request(
    state: AppState,
    *,
    status: str,
    language: Optional[str],
    cache_hit: bool,
    duration_sec: float,
) -> None:
    if state.metrics is None:
        return
    state.metrics.inc(
        "joern_proxy_parse_requests",
        labels={
            "status": status,
            "language": language or "unknown",
            "cache_hit": str(cache_hit).lower(),
        },
    )
    state.metrics.observe("joern_proxy_parse_duration_seconds", duration_sec)


def record_cleanup_request(
    state: AppState,
    *,
    status: str,
    archived: bool,
) -> None:
    if state.metrics is None:
        return
    state.metrics.inc(
        "joern_proxy_cleanup_requests",
        labels={"status": status, "archived": str(archived).lower()},
    )
