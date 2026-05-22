"""Deep /health probes Joern upstream."""

from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_health_ok_when_joern_up_testclient(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "stdout": "1"}

    with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
        response = client.get("/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["joern_ok"] is True


def test_health_503_when_joern_down_testclient(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    with patch(
        "joern_server.upstream.joern.post_query_sync",
        side_effect=httpx.ConnectError("refused"),
    ):
        response = client.get("/health")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["joern_ok"] is False
