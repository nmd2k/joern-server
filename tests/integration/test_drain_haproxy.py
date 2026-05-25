"""
Live drain + HAProxy transparent retry integration test.

Verifies that while one replica is draining, clients hitting the HAProxy VIP
with *new* affinity keys still get HTTP 200 (HAProxy retries another backend).

Prerequisites:
  - Scale stack up with HAProxy (deploy/compose.scale.yml)
  - JOERN_ENABLE_DRAIN_TEST=1 on joern replicas (test only — never in prod)
  - Recreate HAProxy after haproxy.cfg changes (retry-on 503)

Run:
  NEURALATLAS_RUN_DRAIN_TESTS=1 \\
  NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080 \\
  pytest tests/integration/test_drain_haproxy.py -m integration -v -s
"""

from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

LIVE_URL = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")
RUN_DRAIN = os.environ.get("NEURALATLAS_RUN_DRAIN_TESTS", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
FLOOD_REQUESTS = int(os.environ.get("NEURALATLAS_DRAIN_FLOOD_REQUESTS", "40"))
MIN_BACKENDS = int(os.environ.get("NEURALATLAS_DRAIN_MIN_BACKENDS", "3"))


def _is_haproxy_html(body: str) -> bool:
    lower = body.lower()
    return "<html" in lower and "503" in lower


def _discover_backend_count(base: str, *, samples: int = 30) -> set[str]:
    """Return distinct X-Served-By values seen through the VIP."""
    seen: set[str] = set()
    for _ in range(samples):
        aff = f"disc-{uuid.uuid4().hex}"
        try:
            r = httpx.post(
                f"{base}/query-sync",
                json={"query": "val _health = 1"},
                headers={"X-Affinity-Key": aff},
                timeout=15.0,
            )
            if r.status_code == 200:
                name = r.headers.get("X-Served-By", "")
                if name:
                    seen.add(name)
        except Exception:
            pass
    return seen


def _vip_ok() -> bool:
    try:
        r = httpx.get(f"{LIVE_URL}/health", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def require_vip() -> str:
    if not RUN_DRAIN:
        pytest.skip("Set NEURALATLAS_RUN_DRAIN_TESTS=1 to run live drain tests")
    if not _vip_ok():
        pytest.skip(f"Joern VIP not reachable at {LIVE_URL}")
    return LIVE_URL


def _pin_backend(base: str) -> tuple[str, str]:
    """Return (affinity_key, X-Served-By backend name) for a sticky session."""
    aff = f"drain-pin-{uuid.uuid4().hex}"
    headers = {"X-Affinity-Key": aff}
    served_by = ""
    for _ in range(8):
        r = httpx.post(
            f"{base}/query-sync",
            json={"query": "val _health = 1"},
            headers=headers,
            timeout=30.0,
        )
        r.raise_for_status()
        served_by = r.headers.get("X-Served-By", served_by)
    assert served_by, "expected X-Served-By from HAProxy"
    return aff, served_by


@pytest.mark.integration
def test_haproxy_hides_drain_from_new_sessions(require_vip: str) -> None:
    """New affinity keys must succeed through VIP while one replica drains."""
    base = require_vip

    backends = _discover_backend_count(base)
    if len(backends) < MIN_BACKENDS:
        pytest.skip(
            f"need at least {MIN_BACKENDS} joern backends via HAProxy for redispatch test; "
            f"saw {sorted(backends) or '(none)'}. "
            f"Run: docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d --scale joern=3"
        )

    pin_aff, served_by = _pin_backend(base)

    drain_resp = httpx.post(
        f"{base}/debug/drain",
        headers={"X-Affinity-Key": pin_aff},
        timeout=10.0,
    )
    if drain_resp.status_code == 404:
        pytest.skip("JOERN_ENABLE_DRAIN_TEST=0 on replicas; enable for this test only")
    if _is_haproxy_html(drain_resp.text):
        pytest.fail("HAProxy returned HTML 503 — no healthy backend when triggering drain")
    drain_resp.raise_for_status()
    assert drain_resp.json().get("draining") is True

    failures: list[tuple[int, int, str]] = []
    html_503 = 0
    app_draining = 0
    for i in range(FLOOD_REQUESTS):
        aff = f"drain-flood-{uuid.uuid4().hex}-{i}"
        try:
            r = httpx.post(
                f"{base}/parse",
                json={
                    "sample_id": aff,
                    "source_code": f"int drain_flood_{i}(void){{return {i};}}",
                    "language": "c",
                    "overwrite": True,
                },
                headers={"X-Affinity-Key": aff},
                timeout=120.0,
            )
            if r.status_code != 200 or not r.json().get("ok"):
                snippet = r.text[:120]
                if _is_haproxy_html(r.text):
                    html_503 += 1
                elif r.status_code == 503 and "replica_draining" in r.text:
                    app_draining += 1
                failures.append((i, r.status_code, snippet))
        except Exception as exc:
            failures.append((i, -1, str(exc)[:120]))

    if failures:
        summary = (
            f"failures={len(failures)}/{FLOOD_REQUESTS} during drain on {served_by}; "
            f"haproxy_html_503={html_503}, app_replica_draining={app_draining}, "
            f"backends_seen={sorted(backends)}; "
            f"first={failures[:3]}"
        )
        if html_503:
            summary += (
                ". HAProxy HTML 503 means no backend was available (not the app JSON "
                "replica_draining response). Wait until >=3 backends appear in X-Served-By "
                "after scaling; a recent drain/restart may still be recovering."
            )
        elif len(backends) < MIN_BACKENDS or app_draining == len(failures):
            summary += (
                ". Hint: scale to >=3 replicas and recreate haproxy (retry-on 503)."
            )
        pytest.fail(summary)

    time.sleep(2.0)
    recovery = httpx.get(f"{base}/health", timeout=10.0)
    assert recovery.status_code == 200
