"""Tests for Joern JVM restart after cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_cleanup_requests_joern_restart_when_memory_high(tmp_path, monkeypatch) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo-restart"
    cpg_dir.mkdir(parents=True)
    (cpg_dir / "metadata.json").write_text("{}", encoding="utf-8")

    state = make_test_state(tmp_path, joern_memory_restart_mb=2048)
    state.affinity_cpg_path = {"demo-restart": str(cpg_dir)}
    state.active_affinity_key = "demo-restart"
    state.active_cpg_path = str(cpg_dir)

    flag = tmp_path / "restart.flag"
    monkeypatch.setattr("joern_server.lifecycle.joern_restart.RESTART_FLAG_PATH", flag)

    client = TestClient(create_test_app(state=state))

    def fake_post(*_a, **_kwargs):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"success": True}
        m.raise_for_status = MagicMock()
        return m

    with patch("joern_server.upstream.joern.httpx.post", side_effect=fake_post), patch(
        "joern_server.lifecycle.joern_restart.read_container_memory_bytes",
        return_value=3 * 1024 * 1024 * 1024,
    ), patch("joern_server.lifecycle.drain.time.sleep", side_effect=lambda _s: None):
        response = client.post("/cleanup", json={"sample_id": "demo-restart"})

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
