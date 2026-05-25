"""Unit tests for POST /graph/cfg (FastAPI TestClient)."""

from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tests.helpers.app import make_test_client

_GRAPH_BODY = {"method_full_name": "com.example.Foo.main:void()"}

DOT_CFG = '''digraph "CFG" {
  "1" [label="ENTRY" shape="box"]
  "2" [label="x = 1" shape="box"]
  "1" -> "2" [label="control"]
}'''


@pytest.fixture
def client(tmp_path):
    return make_test_client(tmp_path=tmp_path)


class TestGraphCfg:
    def test_cfg_endpoint_parses_dot_output(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": DOT_CFG, "success": True}
        mock_resp.raise_for_status = MagicMock()

        with patch("joern_server.graph.metadata.fetch_node_metadata", return_value={}):
            with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
                response = client.post("/graph/cfg", json=_GRAPH_BODY)

        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert "nodes" in body
        assert "edges" in body
        assert "metadata" in body
        assert len(body["nodes"]) == 2
        assert all("id" in n and "label" in n and "shape" in n for n in body["nodes"])
        assert len(body["edges"]) == 1

    def test_cfg_endpoint_empty_result_returns_422(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": "", "success": True}
        mock_resp.raise_for_status = MagicMock()

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/cfg", json=_GRAPH_BODY)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_cfg_endpoint_timeout_returns_504(self, client):
        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = client.post("/graph/cfg", json=_GRAPH_BODY)

        assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT

    def test_cfg_endpoint_joern_error_returns_502(self, client):
        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=Exception("something broke"),
        ):
            response = client.post("/graph/cfg", json=_GRAPH_BODY)

        assert response.status_code == HTTPStatus.BAD_GATEWAY

    def test_cfg_missing_method_full_name_returns_400(self, client):
        response = client.post("/graph/cfg", json={})
        assert response.status_code == HTTPStatus.BAD_REQUEST
