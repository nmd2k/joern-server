"""Deep /health probes Joern upstream."""

from http import HTTPStatus
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_health_ok_when_joern_tcp_up(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    with patch("joern_server.upstream.joern.check_joern_tcp", return_value=True):
        response = client.get("/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["joern_ok"] is True
    assert response.json()["joern_http_ok"] is True


def test_health_503_when_joern_tcp_down(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    with patch("joern_server.upstream.joern.check_joern_tcp", return_value=False):
        response = client.get("/health")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["joern_ok"] is False
    assert response.json()["joern_http_ok"] is False


def test_health_deep_ok_when_repl_responds(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    mock_resp = httpx.Response(200, json={"success": True, "stdout": "1"})

    with (
        patch("joern_server.upstream.joern.check_joern_tcp", return_value=True),
        patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp),
    ):
        response = client.get("/health?deep=true")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["joern_ok"] is True
    assert body["joern_http_ok"] is True
    assert body["joern_repl_ok"] is True


def test_health_deep_503_when_repl_fails(tmp_path) -> None:
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    with (
        patch("joern_server.upstream.joern.check_joern_tcp", return_value=True),
        patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=httpx.ConnectError("refused"),
        ),
    ):
        response = client.get("/health?deep=true")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = response.json()
    assert body["joern_ok"] is False
    assert body["joern_http_ok"] is True
    assert body["joern_repl_ok"] is False
