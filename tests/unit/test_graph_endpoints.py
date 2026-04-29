"""S7-010: Unit tests for new graph endpoints (PDG, AST, metadata enrichment, aliases).

Tests /graph/pdg, /graph/ast, _fetch_node_metadata, and /graph/ddg alias routing.
All tests use mocked httpx responses — no live Joern server required.

Run with:
    pytest tests/unit/test_graph_endpoints.py -v
"""

import json
import threading
from http import HTTPStatus
from io import BytesIO
from unittest.mock import MagicMock, patch

import httpx
import pytest

from joern_server.proxy import JoernProxyHandler


def _make_graph_handler(path="/graph/pdg", body_dict=None):
    """Create a JoernProxyHandler pre-configured for graph endpoint testing."""
    JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
    JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
    JoernProxyHandler.query_cache = None
    JoernProxyHandler.parse_bin = "/bin/false"
    JoernProxyHandler.cpg_out_dir = "/tmp"
    JoernProxyHandler.parse_timeout_sec = 30
    JoernProxyHandler.query_timeout_sec = 5

    handler = JoernProxyHandler.__new__(JoernProxyHandler)
    handler.path = path
    handler.headers = {
        "X-Session-Id": "test-session",
        "X-Request-Id": "req-1",
        "Content-Type": "application/json",
    }
    handler.wfile = BytesIO()
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.server = MagicMock()
    handler.client_address = ("127.0.0.1", 9999)

    if body_dict is None:
        body_dict = {"method_full_name": "com.example.Foo.main:void()"}

    body = json.dumps(body_dict).encode("utf-8")
    handler.headers["Content-Length"] = str(len(body))

    def _read_body():
        return body

    handler._read_body = _read_body
    return handler


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

    @pytest.fixture(autouse=True)
    def _setup_class_vars(self):
        JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
        JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
        JoernProxyHandler.query_timeout_sec = 5

    def test_pdg_endpoint_parses_dot_output(self):
        """Mock Joern query-sync to return DOT string, verify {nodes, edges, metadata}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.DOT_PDG, "success": True}

        sent_status = []
        sent_data = []

        def capture_send_json(status, data):
            sent_status.append(status)
            sent_data.append(data)

        handler = _make_graph_handler("/graph/pdg")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch.object(handler, "_fetch_node_metadata", return_value={}):
                with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                    handler.do_POST()

        assert sent_status == [HTTPStatus.OK]
        response = sent_data[0]
        assert "nodes" in response
        assert "edges" in response
        assert "metadata" in response
        assert len(response["nodes"]) == 3
        assert all("id" in n and "label" in n and "shape" in n for n in response["nodes"])
        assert len(response["edges"]) == 2
        assert all("source" in e and "target" in e and "label" in e for e in response["edges"])

    def test_pdg_endpoint_empty_result_returns_422(self):
        """Mock empty DOT string, verify 422 response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": "", "success": True}

        sent_status = []

        def capture_send_json(status, data):
            sent_status.append(status)

        handler = _make_graph_handler("/graph/pdg")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                handler.do_POST()

        assert sent_status == [HTTPStatus.UNPROCESSABLE_ENTITY]

    def test_pdg_endpoint_timeout_returns_504(self):
        """Mock httpx.TimeoutException, verify 504 Gateway Timeout."""
        sent_status = []

        def capture_send_json(status, data):
            sent_status.append(status)

        handler = _make_graph_handler("/graph/pdg")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", side_effect=httpx.TimeoutException("timed out")):
                handler.do_POST()

        assert sent_status == [HTTPStatus.GATEWAY_TIMEOUT]

    def test_pdg_endpoint_joern_error_returns_502(self):
        """Mock generic exception, verify 502 Bad Gateway."""
        sent_status = []

        def capture_send_json(status, data):
            sent_status.append(status)

        handler = _make_graph_handler("/graph/pdg")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", side_effect=Exception("something broke")):
                handler.do_POST()

        assert sent_status == [HTTPStatus.BAD_GATEWAY]

    def test_pdg_endpoint_metadata_included(self):
        """Verify response includes metadata map keyed by node ID."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.DOT_PDG, "success": True}

        fake_metadata = {
            "1": {"code": "void main()", "line_number": 1, "node_type": "METHOD"},
            "2": {"code": "x = 1", "line_number": 2, "node_type": "ASSIGNMENT"},
            "3": {"code": "foo()", "line_number": 3, "node_type": "CALL"},
        }

        sent_data = []

        def capture_send_json(status, data):
            sent_data.append(data)

        handler = _make_graph_handler("/graph/pdg")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch.object(handler, "_fetch_node_metadata", return_value=fake_metadata):
                with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                    handler.do_POST()

        response = sent_data[0]
        assert "metadata" in response
        assert response["metadata"] == fake_metadata
        assert "1" in response["metadata"]
        assert "2" in response["metadata"]
        assert "3" in response["metadata"]


# ---------------------------------------------------------------------------
# /graph/ast tests
# ---------------------------------------------------------------------------


class TestGraphAst:
    """Tests for POST /graph/ast endpoint."""

    # Simulated Joern stdout returning Scala tuples for AST (7 fields, no argumentIndex)
    AST_TUPLES_STDOUT = (
        'val res0: List[(Long, String, Option[Int], Option[Int], Int, String, Option[Long])] = List(\n'
        '(1, "void main()", Some(1), Some(1), 1, "METHOD", None),\n'
        '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT", Some(1)),\n'
        '(3, "x", Some(2), Some(3), 3, "IDENTIFIER", Some(2)),\n'
        '(4, "1", Some(2), Some(7), 4, "LITERAL", Some(2)),\n'
        '(5, "foo()", Some(3), Some(3), 5, "CALL", Some(1))\n'
        ')'
    )

    @pytest.fixture(autouse=True)
    def _setup_class_vars(self):
        JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
        JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
        JoernProxyHandler.query_timeout_sec = 5

    def test_ast_endpoint_parses_scala_tuples(self):
        """Mock query-sync returning Scala tuple list, verify {nodes, edges, metadata}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.AST_TUPLES_STDOUT, "success": True}

        sent_data = []

        def capture_send_json(status, data):
            sent_data.append(data)

        handler = _make_graph_handler("/graph/ast")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                handler.do_POST()

        response = sent_data[0]
        assert "nodes" in response
        assert "edges" in response
        assert "metadata" in response
        assert "method_full_name" in response
        assert len(response["nodes"]) == 5
        # node 1 is the root METHOD node
        assert response["nodes"][0]["id"] == "1"
        assert response["nodes"][0]["label"] == "METHOD"

    def test_ast_endpoint_empty_result_returns_422(self):
        """Mock empty tuple list, verify 422."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": "val res0: List[...] = List()", "success": True}

        sent_status = []

        def capture_send_json(status, data):
            sent_status.append(status)

        handler = _make_graph_handler("/graph/ast")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                handler.do_POST()

        assert sent_status == [HTTPStatus.UNPROCESSABLE_ENTITY]

    def test_ast_endpoint_timeout_returns_504(self):
        """Mock httpx.TimeoutException for AST, verify 504."""
        sent_status = []

        def capture_send_json(status, data):
            sent_status.append(status)

        handler = _make_graph_handler("/graph/ast")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", side_effect=httpx.TimeoutException("timed out")):
                handler.do_POST()

        assert sent_status == [HTTPStatus.GATEWAY_TIMEOUT]

    def test_ast_endpoint_edges_connect_parents(self):
        """Verify edges properly link astParent.id -> node.id."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.AST_TUPLES_STDOUT, "success": True}

        sent_data = []

        def capture_send_json(status, data):
            sent_data.append(data)

        handler = _make_graph_handler("/graph/ast")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                handler.do_POST()

        response = sent_data[0]
        edges = response["edges"]

        # Node 2 has astParent=1 -> edge 1->2
        # Node 3 has astParent=2 -> edge 2->3
        # Node 4 has astParent=2 -> edge 2->4
        # Node 5 has astParent=1 -> edge 1->5
        # Node 1 has astParent=None -> no edge for 1 as child
        assert len(edges) == 4
        edge_sources = {e["source"] for e in edges}
        edge_targets = {e["target"] for e in edges}
        assert "1" in edge_sources  # parent of 2 and 5
        assert "2" in edge_sources  # parent of 3 and 4
        assert "2" in edge_targets  # child of 1
        assert "3" in edge_targets  # child of 2
        assert "4" in edge_targets  # child of 2
        assert "5" in edge_targets  # child of 1

        # Verify specific edges
        edge_pairs = {(e["source"], e["target"]) for e in edges}
        assert ("1", "2") in edge_pairs
        assert ("2", "3") in edge_pairs
        assert ("2", "4") in edge_pairs
        assert ("1", "5") in edge_pairs

    def test_ast_endpoint_metadata_inline(self):
        """Verify nodes include metadata fields in the metadata map."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.AST_TUPLES_STDOUT, "success": True}

        sent_data = []

        def capture_send_json(status, data):
            sent_data.append(data)

        handler = _make_graph_handler("/graph/ast")
        with patch.object(handler, "_send_json", side_effect=capture_send_json):
            with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                handler.do_POST()

        response = sent_data[0]
        metadata = response["metadata"]

        # Check metadata for node 1 (METHOD)
        assert "1" in metadata
        meta1 = metadata["1"]
        assert "code" in meta1
        assert meta1["code"] == "void main()"
        assert "line_number" in meta1
        assert meta1["line_number"] == 1
        assert "column_number" in meta1
        assert "order" in meta1
        assert "argument_index" in meta1
        assert "node_type" in meta1
        assert meta1["node_type"] == "METHOD"

        # Check metadata for node 3 (IDENTIFIER)
        assert "3" in metadata
        meta3 = metadata["3"]
        assert meta3["code"] == "x"
        assert meta3["node_type"] == "IDENTIFIER"
        assert meta3["argument_index"] == -1
        assert meta3["order"] == 3


# ---------------------------------------------------------------------------
# metadata enrichment (_fetch_node_metadata) tests
# ---------------------------------------------------------------------------


class TestFetchNodeMetadata:
    """Tests for _fetch_node_metadata() helper."""

    METADATA_STDOUT = (
        'val res0: List[(Long, String, Option[Int], Option[Int], Int, String)] = List(\n'
        '(1, "void main()", Some(1), Some(1), 1, "METHOD"),\n'
        '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT")\n'
        ')'
    )

    @pytest.fixture(autouse=True)
    def _setup_class_vars(self):
        JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
        JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
        JoernProxyHandler.query_timeout_sec = 5

    def test_fetch_node_metadata_returns_map(self):
        """Verify _fetch_node_metadata() returns proper dict structure."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.METADATA_STDOUT, "success": True}

        handler = JoernProxyHandler.__new__(JoernProxyHandler)
        handler.headers = {}
        handler.internal_url = "http://127.0.0.1:18080/query-sync"
        handler.query_timeout_sec = 5
        handler.repl_semaphore = threading.Semaphore(1)

        with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
            result = handler._fetch_node_metadata(["1", "2"])

        assert isinstance(result, dict)
        assert "1" in result
        assert "2" in result
        assert result["1"]["code"] == "void main()"
        assert result["1"]["line_number"] == 1
        assert result["1"]["node_type"] == "METHOD"
        assert result["2"]["code"] == "x = 1"
        assert result["2"]["line_number"] == 2
        assert result["2"]["node_type"] == "ASSIGNMENT"

    def test_fetch_node_metadata_batches_large_sets(self):
        """Mock 100+ nodes, verify chunking into batches of 50."""
        # Create 120 node IDs
        node_ids = [str(i) for i in range(1, 121)]

        # Build mock stdout for a single batch of 50 tuples (6 fields)
        tuples_lines = []
        for i in range(1, 51):
            tuples_lines.append(f'({i}, "node_{i}", Some({i}), Some(1), {i}, "TYPE_{i}")')
        batch_stdout = f'val res0: List[...] = List(\n{",\n".join(tuples_lines)}\n)'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": batch_stdout, "success": True}

        handler = JoernProxyHandler.__new__(JoernProxyHandler)
        handler.headers = {}
        handler.internal_url = "http://127.0.0.1:18080/query-sync"
        handler.query_timeout_sec = 5
        handler.repl_semaphore = threading.Semaphore(1)

        call_count = [0]

        def counting_post(*args, **kwargs):
            call_count[0] += 1
            return mock_resp

        with patch("joern_server.proxy.httpx.post", side_effect=counting_post):
            result = handler._fetch_node_metadata(node_ids)

        # 120 nodes -> batch sizes of 50 -> 3 batches (50, 50, 20)
        assert call_count[0] == 3
        # Each batch returns 50 entries; the last batch (20 nodes) also gets 50 entries
        # because the mock always returns 50 entries for the first 50 ids query
        assert len(result) >= 50

    def test_fetch_node_metadata_handles_failure_gracefully(self):
        """Mock metadata query failure, verify empty dict returned without exception."""
        handler = JoernProxyHandler.__new__(JoernProxyHandler)
        handler.headers = {}
        handler.internal_url = "http://127.0.0.1:18080/query-sync"
        handler.query_timeout_sec = 5
        handler.repl_semaphore = threading.Semaphore(1)

        with patch("joern_server.proxy.httpx.post", side_effect=Exception("connection error")):
            result = handler._fetch_node_metadata(["1", "2", "3"])

        assert isinstance(result, dict)
        assert result == {}

    def test_fetch_node_metadata_empty_input(self):
        """Verify empty node_ids list returns empty dict."""
        handler = JoernProxyHandler.__new__(JoernProxyHandler)
        result = handler._fetch_node_metadata([])
        assert result == {}

    def test_fetch_node_metadata_non_numeric_skipped(self):
        """Verify non-numeric IDs are gracefully skipped."""
        handler = JoernProxyHandler.__new__(JoernProxyHandler)
        handler.headers = {}
        handler.internal_url = "http://127.0.0.1:18080/query-sync"
        handler.query_timeout_sec = 5
        handler.repl_semaphore = threading.Semaphore(1)

        # Only non-numeric IDs — no httpx call should be made
        result = handler._fetch_node_metadata(["abc", "xyz"])
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

    @pytest.fixture(autouse=True)
    def _setup_class_vars(self):
        JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
        JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
        JoernProxyHandler.query_timeout_sec = 5

    def test_ddg_alias_routes_to_dfg_handler(self):
        """Verify /graph/ddg calls same handler as /graph/dfg."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stdout": self.DOT_DFG, "success": True}

        sent_data = []

        def capture_send_json(status, data):
            sent_data.append(data)

        # Test via /graph/ddg path
        handler_ddg = _make_graph_handler("/graph/ddg")
        with patch.object(handler_ddg, "_send_json", side_effect=capture_send_json):
            with patch.object(handler_ddg, "_fetch_node_metadata", return_value={}):
                with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                    handler_ddg.do_POST()

        response_ddg = sent_data[0]
        assert "nodes" in response_ddg
        assert len(response_ddg["nodes"]) == 2  # 10 and 20

        # Test via /graph/dfg path — should produce same structure
        sent_data_dfg = []

        def capture_send_json_dfg(status, data):
            sent_data_dfg.append(data)

        handler_dfg = _make_graph_handler("/graph/dfg")
        with patch.object(handler_dfg, "_send_json", side_effect=capture_send_json_dfg):
            with patch.object(handler_dfg, "_fetch_node_metadata", return_value={}):
                with patch("joern_server.proxy.httpx.post", return_value=mock_resp):
                    handler_dfg.do_POST()

        response_dfg = sent_data_dfg[0]
        # Both should return the same node/edge structure
        assert len(response_ddg["nodes"]) == len(response_dfg["nodes"])
        assert len(response_ddg["edges"]) == len(response_dfg["edges"])
        assert response_ddg["nodes"] == response_dfg["nodes"]
        assert response_ddg["edges"] == response_dfg["edges"]
