"""Tests for Joern JVM restart after cleanup (with staggered restart logic)."""

from __future__ import annotations

import threading
import time as _real_time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from joern_server.lifecycle.joern_restart import (
    _check_cluster_health,
    _staggered_restart_worker,
    maybe_request_joern_restart_after_cleanup,
)
from tests.helpers.app import create_test_app, make_test_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_post(*_a, **_kwargs):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"success": True}
    m.raise_for_status = MagicMock()
    return m


def _wait_for_staggered_thread(timeout: float = 2.0) -> None:
    """Wait for the background staggered-restart thread to complete."""
    deadline = _real_time.monotonic() + timeout
    while _real_time.monotonic() < deadline:
        found = False
        for t in threading.enumerate():
            if t.name == "joern-staggered-restart":
                t.join(timeout=0.05)
                found = True
                break
        if not found:
            return
    # Timeout — thread might have been too fast and already exited


# ---------------------------------------------------------------------------
# Original tests (adapted for staggered restart)
# ---------------------------------------------------------------------------

def test_cleanup_requests_joern_restart_when_memory_high(tmp_path, monkeypatch) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo-restart"
    cpg_dir.mkdir(parents=True)
    (cpg_dir / "metadata.json").write_text("{}", encoding="utf-8")

    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=0,
        joern_haproxy_vip="",
    )
    state.affinity_cpg_path = {"demo-restart": str(cpg_dir)}
    state.active_affinity_key = "demo-restart"
    state.active_cpg_path = str(cpg_dir)

    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    client = TestClient(create_test_app(state=state))

    with patch("joern_server.upstream.joern.httpx.post", side_effect=_fake_post), \
         patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024), \
         patch("time.sleep", side_effect=lambda _s: None):
        response = client.post("/cleanup", json={"sample_id": "demo-restart"})
        _wait_for_staggered_thread()

    assert response.status_code == 200
    assert state.active_cpg_path is None
    assert state.draining is True
    assert flag.is_file()


def test_cleanup_skips_restart_when_threshold_disabled(tmp_path, monkeypatch) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo-no-restart"
    cpg_dir.mkdir(parents=True)

    state = make_test_state(tmp_path, joern_memory_restart_mb=0)
    client = TestClient(create_test_app(state=state))
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    with patch("joern_server.upstream.joern.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        client.post("/cleanup", json={"sample_id": "demo-no-restart"})

    assert not flag.exists()


# ---------------------------------------------------------------------------
# Staggered restart: jitter
# ---------------------------------------------------------------------------

def test_jitter_sleep_is_called_with_random_delay(tmp_path, monkeypatch) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=30,
        joern_haproxy_vip="",
    )
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    sleep_calls: list[float] = []

    with patch("joern_server.lifecycle.joern_restart.random.uniform", return_value=17.5), \
         patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        _staggered_restart_worker(state, sample_id="jitter-test", initial_usage_mb=3000.0)

    assert 17.5 in sleep_calls


# ---------------------------------------------------------------------------
# Staggered restart: memory recovery during jitter
# ---------------------------------------------------------------------------

def test_restart_deferred_when_memory_recovers_during_jitter(tmp_path, monkeypatch) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=10,
        joern_haproxy_vip="",
    )
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)
    state.restart_scheduled = True

    with patch("time.sleep"), \
         patch("joern_server.lifecycle.joern_restart.random.uniform", return_value=1.0), \
         patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=1 * 1024 * 1024 * 1024):
        _staggered_restart_worker(state, sample_id="recover-test", initial_usage_mb=3000.0)

    assert not flag.exists()
    assert state.draining is False
    assert state.restart_scheduled is False


# ---------------------------------------------------------------------------
# Staggered restart: cluster health gate
# ---------------------------------------------------------------------------

def test_check_cluster_health_returns_true_when_vip_healthy() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("joern_server.lifecycle.joern_restart.httpx.get", return_value=mock_resp):
        assert _check_cluster_health("http://fake-haproxy:8080") is True


def test_check_cluster_health_returns_false_when_vip_503() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("joern_server.lifecycle.joern_restart.httpx.get", return_value=mock_resp):
        assert _check_cluster_health("http://fake-haproxy:8080") is False


def test_check_cluster_health_returns_false_on_connection_error() -> None:
    with patch("joern_server.lifecycle.joern_restart.httpx.get", side_effect=ConnectionError):
        assert _check_cluster_health("http://fake-haproxy:8080") is False


def test_check_cluster_health_skips_when_vip_empty() -> None:
    assert _check_cluster_health("") is True


def test_restart_deferred_when_cluster_degraded(tmp_path, monkeypatch) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=0,
        joern_haproxy_vip="http://fake-haproxy:8080",
    )
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)
    state.restart_scheduled = True

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024), \
         patch("joern_server.lifecycle.joern_restart.httpx.get", return_value=mock_resp):
        _staggered_restart_worker(state, sample_id="degraded-test", initial_usage_mb=3000.0)

    assert not flag.exists()
    assert state.draining is False
    assert state.restart_scheduled is False


# ---------------------------------------------------------------------------
# Staggered restart: CPG loaded during jitter
# ---------------------------------------------------------------------------

def test_restart_deferred_when_cpg_loaded_during_jitter(tmp_path, monkeypatch) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=5,
        joern_haproxy_vip="",
    )
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)
    state.restart_scheduled = True

    def simulate_cpg_load(_sec):
        state.active_cpg_path = "/workspace/cpg-out/some-cpg"

    with patch("time.sleep", side_effect=simulate_cpg_load), \
         patch("joern_server.lifecycle.joern_restart.random.uniform", return_value=1.0), \
         patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024):
        _staggered_restart_worker(state, sample_id="cpg-load-test", initial_usage_mb=3000.0)

    assert not flag.exists()
    assert state.draining is False
    assert state.restart_scheduled is False


# ---------------------------------------------------------------------------
# Staggered restart: happy path (jitter + VIP healthy → drain)
# ---------------------------------------------------------------------------

def test_staggered_restart_proceeds_when_all_checks_pass(tmp_path, monkeypatch) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=5,
        joern_haproxy_vip="http://fake-haproxy:8080",
    )
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)
    state.restart_scheduled = True

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("joern_server.lifecycle.joern_restart.random.uniform", return_value=2.0), \
         patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024), \
         patch("joern_server.lifecycle.joern_restart.httpx.get", return_value=mock_resp), \
         patch("time.sleep"):
        _staggered_restart_worker(state, sample_id="happy-test", initial_usage_mb=3000.0)
        for t in threading.enumerate():
            if t.name == "joern-drain-restart":
                t.join(timeout=2.0)
                break

    assert state.draining is True
    assert flag.is_file()


# ---------------------------------------------------------------------------
# maybe_request_joern_restart_after_cleanup: double-schedule guard
# ---------------------------------------------------------------------------

def test_double_schedule_is_prevented(tmp_path) -> None:
    state = make_test_state(
        tmp_path,
        joern_memory_restart_mb=2048,
        joern_restart_jitter_sec=0,
        joern_haproxy_vip="",
    )
    state.restart_scheduled = True

    with patch("joern_server.lifecycle.joern_restart.read_container_memory_bytes",
               return_value=3 * 1024 * 1024 * 1024):
        result = maybe_request_joern_restart_after_cleanup(state, sample_id="dup-test")

    assert result is False
