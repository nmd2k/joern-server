"""
HAProxy stickiness integration (live stack required).

Verifies the same X-Affinity-Key reaches the same backend (X-Served-By).

Run:
  NEURALATLAS_RUN_HAPROXY_TESTS=1 \\
  NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080 \\
  pytest tests/integration/test_haproxy_stickiness.py -m integration -v
"""

from __future__ import annotations

import os

import httpx
import pytest

LIVE_URL = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")
RUN_HAPROXY = os.environ.get("NEURALATLAS_RUN_HAPROXY_TESTS", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
STICKY_REQUESTS = int(os.environ.get("NEURALATLAS_HAPROXY_STICKY_REQUESTS", "15"))


def _vip_reachable() -> bool:
    try:
        r = httpx.get(f"{LIVE_URL}/health", timeout=5.0)
        if r.status_code != 200:
            return False
        body = r.json()
        return body.get("joern_ok", body.get("ok")) is not False
    except Exception:
        return False


@pytest.fixture(scope="module")
def require_haproxy_vip() -> str:
    if not RUN_HAPROXY:
        pytest.skip("Set NEURALATLAS_RUN_HAPROXY_TESTS=1 to run HAProxy stickiness tests")
    if not _vip_reachable():
        pytest.skip(f"VIP not reachable or unhealthy at {LIVE_URL}/health")
    return LIVE_URL


@pytest.mark.integration
def test_deep_health_on_vip(require_haproxy_vip: str) -> None:
    r = httpx.get(f"{require_haproxy_vip}/health", timeout=10.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("joern_ok") is True, body
    assert body.get("ok") is True, body


@pytest.mark.integration
def test_affinity_key_sticky_x_served_by(require_haproxy_vip: str) -> None:
    """Same X-Affinity-Key must hit the same HAProxy backend."""
    url = require_haproxy_vip
    affinity = "haproxy-stick-test-affinity"
    session = "haproxy-stick-test-session"
    served_by: list[str] = []

    with httpx.Client(timeout=60.0) as client:
        for _ in range(STICKY_REQUESTS):
            r = client.post(
                f"{url}/query-sync",
                json={"query": "version"},
                headers={
                    "Content-Type": "application/json",
                    "X-Affinity-Key": affinity,
                    "X-Session-Id": session,
                },
            )
            r.raise_for_status()
            body = r.json()
            assert body.get("success") is True, body
            backend = r.headers.get("x-served-by") or r.headers.get("X-Served-By")
            assert backend, "missing X-Served-By (is joern-haproxy running?)"
            served_by.append(backend.strip())

    assert len(set(served_by)) == 1, f"stickiness broken: backends={set(served_by)!r} sequence={served_by!r}"


@pytest.mark.integration
def test_different_affinity_keys_may_use_different_backends(require_haproxy_vip: str) -> None:
    """Two affinity keys can land on different replicas (not asserted equal; smoke only)."""
    url = require_haproxy_vip
    backends: set[str] = set()
    with httpx.Client(timeout=60.0) as client:
        for key in ("affinity-a-smoke", "affinity-b-smoke"):
            r = client.post(
                f"{url}/query-sync",
                json={"query": "version"},
                headers={
                    "Content-Type": "application/json",
                    "X-Affinity-Key": key,
                    "X-Session-Id": f"sess-{key}",
                },
            )
            r.raise_for_status()
            b = r.headers.get("x-served-by") or r.headers.get("X-Served-By")
            if b:
                backends.add(b.strip())
    assert backends, "expected at least one X-Served-By"
