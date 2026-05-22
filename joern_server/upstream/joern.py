"""httpx helpers for the in-container Joern /query-sync endpoint."""

from __future__ import annotations

import time
from typing import Optional

import httpx
from starlette.requests import Request


def upstream_headers_from_request(request: Request) -> dict[str, str]:
    """Headers to forward to Joern (affinity + auth)."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    for name in ("Authorization", "X-Session-Id", "X-Affinity-Key", "X-Request-Id"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def post_query_sync(
    internal_url: str,
    *,
    query: str | None = None,
    content: bytes | None = None,
    headers: dict[str, str],
    timeout_sec: float,
) -> httpx.Response:
    if content is not None:
        return httpx.post(
            internal_url,
            content=content,
            headers=headers,
            timeout=timeout_sec,
        )
    return httpx.post(
        internal_url,
        json={"query": query},
        headers=headers,
        timeout=timeout_sec,
    )


def probe_joern(
    internal_url: str,
    *,
    headers: dict[str, str],
    timeout_sec: float = 5.0,
) -> tuple[bool, int, Optional[str]]:
    """Return (ok, latency_ms, error_message)."""
    t0 = time.perf_counter()
    try:
        resp = post_query_sync(
            internal_url,
            query="val _health = 1",
            headers=headers,
            timeout_sec=timeout_sec,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        if resp.status_code != 200:
            return False, latency_ms, f"upstream status {resp.status_code}"
        body = resp.json()
        if isinstance(body, dict) and body.get("success") is False:
            return False, latency_ms, "upstream success=false"
        return True, latency_ms, None
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        return False, latency_ms, str(exc)
