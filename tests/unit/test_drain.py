"""Tests for graceful drain before Joern restart."""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from joern_server.lifecycle.drain import begin_draining, schedule_joern_restart_after_drain
from tests.helpers.app import create_test_app, make_test_state


def test_begin_draining_is_idempotent(tmp_path) -> None:
    state = make_test_state(tmp_path)
    assert begin_draining(state) is True
    assert state.draining is True
    assert begin_draining(state) is False


def test_draining_middleware_returns_503_on_parse(tmp_path) -> None:
    state = make_test_state(tmp_path)
    state.draining = True
    client = TestClient(create_test_app(state=state))

    response = client.post("/parse", json={"sample_id": "s1", "source_code": "int x;"})
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == "replica_draining"


def test_draining_health_returns_503(tmp_path) -> None:
    state = make_test_state(tmp_path)
    state.draining = True
    client = TestClient(create_test_app(state=state))

    response = client.get("/health")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["draining"] is True


def test_metrics_still_available_while_draining(tmp_path) -> None:
    state = make_test_state(tmp_path)
    state.draining = True
    client = TestClient(create_test_app(state=state))

    response = client.get("/metrics")
    assert response.status_code == HTTPStatus.OK


def test_schedule_restart_after_drain_writes_flag(tmp_path, monkeypatch) -> None:
    state = make_test_state(tmp_path, joern_memory_restart_mb=2048, joern_drain_sec=1)
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    with patch("joern_server.lifecycle.drain.time.sleep", side_effect=lambda _s: None):
        assert schedule_joern_restart_after_drain(state, reason="test") is True
        for thread in threading.enumerate():
            if thread.name == "joern-drain-restart":
                thread.join(timeout=2.0)
                break

    assert state.draining is True
    assert flag.is_file()


def test_cleanup_schedules_drain_not_immediate_restart(tmp_path, monkeypatch) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo-drain"
    cpg_dir.mkdir(parents=True)

    state = make_test_state(tmp_path, joern_memory_restart_mb=2048, joern_drain_sec=60)
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    client = TestClient(create_test_app(state=state))

    with patch("joern_server.upstream.joern.httpx.post") as mock_post, patch(
        "joern_server.lifecycle.joern_restart.read_container_memory_bytes",
        return_value=3 * 1024 * 1024 * 1024,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        response = client.post("/cleanup", json={"sample_id": "demo-drain"})

    assert response.status_code == HTTPStatus.OK
    assert state.draining is True
    assert not flag.exists()

    follow_up = client.post("/parse", json={"sample_id": "s2", "source_code": "int y;"})
    assert follow_up.status_code == HTTPStatus.SERVICE_UNAVAILABLE
