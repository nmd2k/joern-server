"""S7-010: Unit tests for graph endpoints (PDG, AST, metadata enrichment, aliases).

Tests /graph/pdg, /graph/ast, fetch_node_metadata, and /graph/ddg alias routing.
All tests use mocked upstream responses — no live Joern server required.

Run with:
    pytest tests/unit/test_graph_endpoints.py -v
"""

from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from joern_server.graph.metadata import fetch_node_metadata
from tests.helpers.app import create_test_app, make_test_state

_GRAPH_BODY = {"method_full_name": "com.example.Foo.main:void()"}
_HEADERS = {
    "X-Session-Id": "test-session",
    "X-Request-Id": "req-1",
    "Content-Type": "application/json",
}


def _client(tmp_path) -> TestClient:
    return TestClient(create_test_app(state=make_test_state(tmp_path)))


def _mock_resp(stdout: str, *, success: bool = True) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"stdout": stdout, "success": success}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ---------------------------------------------------------------------------
# /graph/pdg tests
# ---------------------------------------------------------------------------


class TestGraphPdg:
    """Tests for POST /graph/pdg endpoint."""

    DOT_PDG = '''digraph "PDG" {
  "1" [label="ENTRY" shape="box"]
  "2" [label="x = 1" shape="box"]
  "3" [label="CALL: foo" shape="box"]
  "1" -> "2" [label="data"]
  "2" -> "3" [label="control"]
}'''

    def test_pdg_endpoint_parses_dot_output(self, tmp_path):
        """Mock Joern query-sync to return DOT string, verify {nodes, edges, metadata}."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.DOT_PDG)

        with patch("joern_server.graph.service.fetch_node_metadata", return_value={}):
            with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
                response = client.post("/graph/pdg", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert "nodes" in body
        assert "edges" in body
        assert "metadata" in body
        assert len(body["nodes"]) == 3
        assert all("id" in n and "label" in n and "shape" in n for n in body["nodes"])
        assert len(body["edges"]) == 2
        assert all("source" in e and "target" in e and "label" in e for e in body["edges"])

    def test_pdg_endpoint_empty_result_returns_422(self, tmp_path):
        """Mock empty DOT string, verify 422 response."""
        client = _client(tmp_path)
        mock_resp = _mock_resp("")

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/pdg", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_pdg_endpoint_timeout_returns_504(self, tmp_path):
        """Mock httpx.TimeoutException, verify 504 Gateway Timeout."""
        client = _client(tmp_path)

        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = client.post("/graph/pdg", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT

    def test_pdg_endpoint_joern_error_returns_502(self, tmp_path):
        """Mock generic exception, verify 502 Bad Gateway."""
        client = _client(tmp_path)

        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=Exception("something broke"),
        ):
            response = client.post("/graph/pdg", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.BAD_GATEWAY

    def test_pdg_endpoint_metadata_included(self, tmp_path):
        """Verify response includes metadata map keyed by node ID."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.DOT_PDG)
        fake_metadata = {
            "1": {"code": "void main()", "line_number": 1, "node_type": "METHOD"},
            "2": {"code": "x = 1", "line_number": 2, "node_type": "ASSIGNMENT"},
            "3": {"code": "foo()", "line_number": 3, "node_type": "CALL"},
        }

        with patch("joern_server.graph.service.fetch_node_metadata", return_value=fake_metadata):
            with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
                response = client.post("/graph/pdg", json=_GRAPH_BODY, headers=_HEADERS)

        body = response.json()
        assert "metadata" in body
        assert body["metadata"] == fake_metadata
        assert "1" in body["metadata"]
        assert "2" in body["metadata"]
        assert "3" in body["metadata"]


# ---------------------------------------------------------------------------
# /graph/ast tests
# ---------------------------------------------------------------------------


class TestGraphAst:
    """Tests for POST /graph/ast endpoint."""

    AST_TUPLES_STDOUT = (
        'val res0: List[(Long, String, Option[Int], Option[Int], Int, String, Option[Long])] = List(\n'
        '(1, "void main()", Some(1), Some(1), 1, "METHOD", None),\n'
        '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT", Some(1)),\n'
        '(3, "x", Some(2), Some(3), 3, "IDENTIFIER", Some(2)),\n'
        '(4, "1", Some(2), Some(7), 4, "LITERAL", Some(2)),\n'
        '(5, "foo()", Some(3), Some(3), 5, "CALL", Some(1))\n'
        ')'
    )

    def test_ast_endpoint_parses_scala_tuples(self, tmp_path):
        """Mock query-sync returning Scala tuple list, verify {nodes, edges, metadata}."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.AST_TUPLES_STDOUT)

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/ast", json=_GRAPH_BODY, headers=_HEADERS)

        body = response.json()
        assert "nodes" in body
        assert "edges" in body
        assert "metadata" in body
        assert "method_full_name" in body
        assert len(body["nodes"]) == 5
        assert body["nodes"][0]["id"] == "1"
        assert body["nodes"][0]["label"] == "METHOD"

    def test_ast_endpoint_empty_result_returns_422(self, tmp_path):
        """Mock empty tuple list, verify 422."""
        client = _client(tmp_path)
        mock_resp = _mock_resp("val res0: List[...] = List()")

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/ast", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_ast_endpoint_timeout_returns_504(self, tmp_path):
        """Mock httpx.TimeoutException for AST, verify 504."""
        client = _client(tmp_path)

        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            response = client.post("/graph/ast", json=_GRAPH_BODY, headers=_HEADERS)

        assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT

    def test_ast_endpoint_edges_connect_parents(self, tmp_path):
        """Verify edges properly link astParent.id -> node.id."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.AST_TUPLES_STDOUT)

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/ast", json=_GRAPH_BODY, headers=_HEADERS)

        edges = response.json()["edges"]
        assert len(edges) == 4
        edge_sources = {e["source"] for e in edges}
        edge_targets = {e["target"] for e in edges}
        assert "1" in edge_sources
        assert "2" in edge_sources
        assert "2" in edge_targets
        assert "3" in edge_targets
        assert "4" in edge_targets
        assert "5" in edge_targets

        edge_pairs = {(e["source"], e["target"]) for e in edges}
        assert ("1", "2") in edge_pairs
        assert ("2", "3") in edge_pairs
        assert ("2", "4") in edge_pairs
        assert ("1", "5") in edge_pairs

    def test_ast_endpoint_metadata_inline(self, tmp_path):
        """Verify nodes include metadata fields in the metadata map."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.AST_TUPLES_STDOUT)

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            response = client.post("/graph/ast", json=_GRAPH_BODY, headers=_HEADERS)

        metadata = response.json()["metadata"]
        assert "1" in metadata
        meta1 = metadata["1"]
        assert meta1["code"] == "void main()"
        assert meta1["line_number"] == 1
        assert meta1["node_type"] == "METHOD"

        assert "3" in metadata
        meta3 = metadata["3"]
        assert meta3["code"] == "x"
        assert meta3["node_type"] == "IDENTIFIER"
        assert meta3["argument_index"] == -1
        assert meta3["order"] == 3


# ---------------------------------------------------------------------------
# metadata enrichment (fetch_node_metadata) tests
# ---------------------------------------------------------------------------


class TestFetchNodeMetadata:
    """Tests for fetch_node_metadata()."""

    METADATA_STDOUT = (
        'val res0: List[(Long, String, Option[Int], Option[Int], Int, String)] = List(\n'
        '(1, "void main()", Some(1), Some(1), 1, "METHOD"),\n'
        '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT")\n'
        ')'
    )

    def test_fetch_node_metadata_returns_map(self, tmp_path):
        """Verify fetch_node_metadata() returns proper dict structure."""
        state = make_test_state(tmp_path)
        mock_resp = _mock_resp(self.METADATA_STDOUT)

        with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
            result = fetch_node_metadata(state, ["1", "2"], headers={})

        assert isinstance(result, dict)
        assert "1" in result
        assert "2" in result
        assert result["1"]["code"] == "void main()"
        assert result["1"]["line_number"] == 1
        assert result["1"]["node_type"] == "METHOD"
        assert result["2"]["code"] == "x = 1"
        assert result["2"]["line_number"] == 2
        assert result["2"]["node_type"] == "ASSIGNMENT"

    def test_fetch_node_metadata_batches_large_sets(self, tmp_path):
        """Mock 100+ nodes, verify chunking into batches of 50."""
        state = make_test_state(tmp_path)
        node_ids = [str(i) for i in range(1, 121)]

        tuples_lines = []
        for i in range(1, 51):
            tuples_lines.append(f'({i}, "node_{i}", Some({i}), Some(1), {i}, "TYPE_{i}")')
        batch_stdout = f'val res0: List[...] = List(\n{",\n".join(tuples_lines)}\n)'
        mock_resp = _mock_resp(batch_stdout)

        call_count = [0]

        def counting_post(*args, **kwargs):
            call_count[0] += 1
            return mock_resp

        with patch("joern_server.upstream.joern.post_query_sync", side_effect=counting_post):
            result = fetch_node_metadata(state, node_ids, headers={})

        assert call_count[0] == 3
        assert len(result) >= 50

    def test_fetch_node_metadata_handles_failure_gracefully(self, tmp_path):
        """Mock metadata query failure, verify empty dict returned without exception."""
        state = make_test_state(tmp_path)

        with patch(
            "joern_server.upstream.joern.post_query_sync",
            side_effect=Exception("connection error"),
        ):
            result = fetch_node_metadata(state, ["1", "2", "3"], headers={})

        assert isinstance(result, dict)
        assert result == {}

    def test_fetch_node_metadata_empty_input(self, tmp_path):
        """Verify empty node_ids list returns empty dict."""
        state = make_test_state(tmp_path)
        result = fetch_node_metadata(state, [], headers={})
        assert result == {}

    def test_fetch_node_metadata_non_numeric_skipped(self, tmp_path):
        """Verify non-numeric IDs are gracefully skipped."""
        state = make_test_state(tmp_path)
        result = fetch_node_metadata(state, ["abc", "xyz"], headers={})
        assert result == {}


# ---------------------------------------------------------------------------
# DDG alias test
# ---------------------------------------------------------------------------


class TestGraphDdgAlias:
    """Test that /graph/ddg routes to the DFG handler."""

    DOT_DFG = '''digraph "DFG" {
  "10" [label="param: x" shape="box"]
  "20" [label="x = x + 1" shape="box"]
  "10" -> "20" [label="data"]
}'''

    def test_ddg_alias_routes_to_dfg_handler(self, tmp_path):
        """Verify /graph/ddg calls same handler as /graph/dfg."""
        client = _client(tmp_path)
        mock_resp = _mock_resp(self.DOT_DFG)

        with patch("joern_server.graph.service.fetch_node_metadata", return_value={}):
            with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
                response_ddg = client.post("/graph/ddg", json=_GRAPH_BODY, headers=_HEADERS)
                response_dfg = client.post("/graph/dfg", json=_GRAPH_BODY, headers=_HEADERS)

        body_ddg = response_ddg.json()
        body_dfg = response_dfg.json()
        assert "nodes" in body_ddg
        assert len(body_ddg["nodes"]) == 2
        assert len(body_ddg["nodes"]) == len(body_dfg["nodes"])
        assert len(body_ddg["edges"]) == len(body_dfg["edges"])
        assert body_ddg["nodes"] == body_dfg["nodes"]
        assert body_ddg["edges"] == body_dfg["edges"]
