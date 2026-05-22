"""GET /metrics Prometheus text exposition."""

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_metrics_returns_joern_proxy_info(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "joern_proxy_info" in response.text
    assert "joern_proxy_uptime_seconds" in response.text


def test_metrics_not_found_when_disabled(tmp_path) -> None:
    state = make_test_state(tmp_path)
    state.metrics = None
    client = TestClient(create_test_app(state=state))
    response = client.get("/metrics")
    assert response.status_code == 404
    assert response.json() == {"error": "metrics not enabled"}
