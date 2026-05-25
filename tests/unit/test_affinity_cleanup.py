"""Affinity key and cleanup clearing in-memory CPG state."""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_cleanup_closes_when_active_affinity_matches_flat_file(tmp_path) -> None:
    """Close REPL when active_affinity_key matches even if path strings differ slightly."""
    cpg_file = tmp_path / "cpg-out" / "hash-sample"
    cpg_file.parent.mkdir(parents=True, exist_ok=True)
    cpg_file.write_bytes(b"cpg")
    cpg_path = str(cpg_file)

    state = make_test_state(tmp_path)
    state.affinity_cpg_path = {"hash-sample": cpg_path}
    state.active_affinity_key = "hash-sample"
    state.active_cpg_path = cpg_path + "/"

    client = TestClient(create_test_app(state=state))
    close_calls: list[str] = []

    def fake_post(*_a, **kwargs):
        close_calls.append(kwargs.get("query", ""))
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"success": True}
        m.raise_for_status = MagicMock()
        return m

    with patch("joern_server.upstream.joern.post_query_sync", side_effect=fake_post), patch(
        "joern_server.lifecycle.joern_restart.maybe_request_joern_restart_after_cleanup",
        return_value=False,
    ):
        response = client.post("/cleanup", json={"sample_id": "hash-sample"})

    assert response.status_code == 200
    assert "close" in close_calls
    assert state.active_cpg_path is None


def test_cleanup_clears_affinity_map(tmp_path) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo1"
    cpg_dir.mkdir(parents=True)
    (cpg_dir / "metadata.json").write_text("{}", encoding="utf-8")
    cpg_path = str(cpg_dir)

    state = make_test_state(tmp_path)
    state.affinity_cpg_path = {"demo1": cpg_path}
    state.active_affinity_key = "demo1"
    state.active_cpg_path = cpg_path

    client = TestClient(create_test_app(state=state))
    close_calls: list[str] = []

    def fake_post(*_a, **kwargs):
        close_calls.append(kwargs.get("json", {}).get("query", ""))
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"success": True}
        m.raise_for_status = MagicMock()
        return m

    with patch("joern_server.upstream.joern.httpx.post", side_effect=fake_post), patch(
        "joern_server.lifecycle.joern_restart.maybe_request_joern_restart_after_cleanup",
        return_value=False,
    ):
        response = client.post("/cleanup", json={"sample_id": "demo1"})

    assert response.status_code == 200
    assert "demo1" not in state.affinity_cpg_path
    assert state.active_cpg_path is None
    assert "close" in close_calls


def test_cleanup_records_metrics(tmp_path) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo2"
    cpg_dir.mkdir(parents=True)
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    with patch("joern_server.upstream.joern.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        client.post("/cleanup", json={"sample_id": "demo2"})

    metrics = client.get("/metrics")
    assert "joern_proxy_cleanup_requests_total" in metrics.text
