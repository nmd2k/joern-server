"""Deep /health probes Joern upstream."""

from http import HTTPStatus
from io import BytesIO
from unittest.mock import MagicMock, patch

import httpx

from joern_server.proxy import JoernProxyHandler


def _handler() -> JoernProxyHandler:
    JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
    JoernProxyHandler.health_probe_timeout_sec = 5
    JoernProxyHandler.metrics = None
    h = JoernProxyHandler.__new__(JoernProxyHandler)
    h.path = "/health"
    h.headers = {}
    h.wfile = BytesIO()
    h.server = MagicMock()
    h.client_address = ("127.0.0.1", 1)
    return h


def test_health_ok_when_joern_up() -> None:
    handler = _handler()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "stdout": "1"}

    sent: list[tuple[int, dict]] = []

    def capture(status: int, payload: object) -> None:
        sent.append((status, payload))  # type: ignore[arg-type]

    with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
        with patch.object(handler, "_send_json", side_effect=capture):
            handler.do_GET()

    assert sent[0][0] == HTTPStatus.OK
    assert sent[0][1]["joern_ok"] is True


def test_health_503_when_joern_down() -> None:
    handler = _handler()
    sent: list[tuple[int, dict]] = []

    def capture(status: int, payload: object) -> None:
        sent.append((status, payload))  # type: ignore[arg-type]

    with patch("joern_server.proxy.httpx.post", side_effect=httpx.ConnectError("refused")):
        with patch.object(handler, "_send_json", side_effect=capture):
            handler.do_GET()

    assert sent[0][0] == HTTPStatus.SERVICE_UNAVAILABLE
    assert sent[0][1]["joern_ok"] is False
