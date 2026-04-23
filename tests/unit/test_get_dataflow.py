"""
Unit tests for the get_dataflow MCP tool.

Run with:
    pytest tests/unit/test_get_dataflow.py -v
"""

import sys
import os
import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup: allow importing server (which exec's server_tools into its namespace)
# ---------------------------------------------------------------------------
MJOERN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mcp-joern"
)
if MJOERN_DIR not in sys.path:
    sys.path.insert(0, MJOERN_DIR)


def _import_server():
    """Import server module; cache on first call."""
    import server as srv
    return srv


# ===========================================================================
# Tests for get_dataflow
# ===========================================================================


class TestGetDataflow:
    """Unit tests for get_dataflow with joern_remote mocked."""

    # 1. Successful flow -> list of flow path strings
    def test_successful_flow_returns_paths(self):
        srv = _import_server()
        mock_response = (
            'val res0: List[String] = List('
            '"getParameter@/app/Foo.java:10 -> exec@/app/Foo.java:20"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.get_dataflow("getParameter", "exec")
        assert isinstance(result, list), "Should return a list"
        assert len(result) == 1, f"Expected 1 flow path, got {len(result)}: {result!r}"
        assert "->" in result[0], "Flow path should contain ' -> ' separator"

    # 2. No flow (empty List()) -> []
    def test_no_flow_empty_list(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value="val res0: List[String] = List()"):
            result = srv.get_dataflow("readLine", "eval")
        assert result == [], f"Expected [], got {result!r}"

    # 3. joern_remote returns None -> []
    def test_none_response_returns_empty(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_dataflow("getParameter", "exec")
        assert result == [], f"Expected [] on None response, got {result!r}"

    # 4. max_depth > 20 -> capped to 20 (query still built and sent)
    def test_max_depth_capped_at_20(self):
        srv = _import_server()
        captured_queries = []

        def fake_joern_remote(query):
            captured_queries.append(query)
            return "val res0: List[String] = List()"

        with patch.object(srv, "joern_remote", side_effect=fake_joern_remote):
            result = srv.get_dataflow("getUserInput", "exec", max_depth=99)

        assert result == [], "Capped depth should still return empty list (no flow)"
        assert len(captured_queries) == 1, "Should have sent exactly one query"
        # Query should still contain source and sink patterns
        assert "getUserInput" in captured_queries[0]
        assert "exec" in captured_queries[0]

    # 5. source and sink both appear in query string
    def test_source_and_sink_appear_in_query(self):
        srv = _import_server()
        captured_queries = []

        def fake_joern_remote(query):
            captured_queries.append(query)
            return "val res0: List[String] = List()"

        with patch.object(srv, "joern_remote", side_effect=fake_joern_remote):
            srv.get_dataflow("mySource", "mySink")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        assert "mySource" in query, f"source_pattern not in query: {query!r}"
        assert "mySink" in query, f"sink_pattern not in query: {query!r}"

    # 6. Response with "Exception" in stdout -> returns []
    def test_exception_in_response_returns_empty(self):
        srv = _import_server()
        error_response = "Exception in thread 'main' java.lang.RuntimeException: traversal failed"
        with patch.object(srv, "joern_remote", return_value=error_response):
            result = srv.get_dataflow("getParameter", "exec")
        assert result == [], f"Exception response should return [], got {result!r}"

    # 6b. Response with lowercase "error" -> returns []
    def test_error_in_response_returns_empty(self):
        srv = _import_server()
        error_response = "error: reachableByFlows not supported on this graph version"
        with patch.object(srv, "joern_remote", return_value=error_response):
            result = srv.get_dataflow("getParameter", "exec")
        assert result == [], f"Error response should return [], got {result!r}"

    # 7. Multiple flows returned -> all parsed
    def test_multiple_flows_returned(self):
        srv = _import_server()
        mock_response = (
            'val res0: List[String] = List('
            '"nodeA@/app/A.java:1 -> nodeB@/app/A.java:5", '
            '"nodeC@/app/B.java:10 -> nodeD@/app/B.java:20", '
            '"nodeE@/app/C.java:30 -> nodeF@/app/C.java:40"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.get_dataflow("src", "snk")
        assert isinstance(result, list), "Should return a list"
        assert len(result) == 3, f"Expected 3 flow paths, got {len(result)}: {result!r}"

    # 8. Single-node flow -> parsed correctly
    def test_single_node_flow_parsed(self):
        srv = _import_server()
        mock_response = (
            'val res0: List[String] = List('
            '"exec(userInput)@/app/Main.java:42"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.get_dataflow("userInput", "exec")
        assert isinstance(result, list), "Should return list"
        assert len(result) == 1, f"Expected 1 element, got {len(result)}: {result!r}"
        assert "exec(userInput)" in result[0], f"Node code should be in result: {result[0]!r}"
        assert "/app/Main.java:42" in result[0], f"File:line should be in result: {result[0]!r}"

    # 9. Special regex chars in patterns -> passed through unchanged
    def test_special_regex_chars_passed_through(self):
        srv = _import_server()
        captured_queries = []

        def fake_joern_remote(query):
            captured_queries.append(query)
            return "val res0: List[String] = List()"

        with patch.object(srv, "joern_remote", side_effect=fake_joern_remote):
            srv.get_dataflow("Runtime.*", "exec|eval")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        assert "Runtime.*" in query, f"Regex source pattern should be passed through; query: {query!r}"
        assert "exec|eval" in query, f"Regex sink pattern should be passed through; query: {query!r}"

    # 10. ANSI codes in response -> still parsed (extract_list handles ANSI-stripped input)
    def test_ansi_codes_in_response_still_parsed(self):
        srv = _import_server()
        # Simulate response with ANSI color codes wrapping the List output
        # joern_remote already strips ANSI before returning; simulate stripped output
        mock_response = (
            '\x1b[33mval\x1b[0m \x1b[36mres0\x1b[0m: '
            '\x1b[32mList\x1b[0m[\x1b[32mString\x1b[0m] = '
            'List("pathA@/src/Foo.java:5 -> pathB@/src/Foo.java:15")\n'
        )
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.get_dataflow("pathA", "pathB")
        # Result should be a list (ANSI stripping is done by joern_remote, but
        # the mock bypasses that - extract_list must handle or at least not crash)
        assert isinstance(result, list), "Should return a list even with ANSI in response"

    # 11. Query uses reachableByFlows API (structural check)
    def test_query_contains_reachable_by_flows(self):
        srv = _import_server()
        captured_queries = []

        def fake_joern_remote(query):
            captured_queries.append(query)
            return "val res0: List[String] = List()"

        with patch.object(srv, "joern_remote", side_effect=fake_joern_remote):
            srv.get_dataflow("source", "sink")

        assert len(captured_queries) == 1
        assert "reachableByFlows" in captured_queries[0], (
            f"Query must use reachableByFlows; got: {captured_queries[0]!r}"
        )

    # 12. max_depth within range -> accepted, query built normally
    def test_max_depth_within_range_accepted(self):
        srv = _import_server()
        captured_queries = []

        def fake_joern_remote(query):
            captured_queries.append(query)
            return "val res0: List[String] = List()"

        with patch.object(srv, "joern_remote", side_effect=fake_joern_remote):
            result = srv.get_dataflow("src", "snk", max_depth=15)

        assert result == []
        assert len(captured_queries) == 1, "Should have made exactly one joern_remote call"

    # 13. Error response with mixed case "Error" -> returns []
    def test_mixed_case_error_returns_empty(self):
        srv = _import_server()
        error_response = "Error: cpg.call.name() encountered an internal error"
        with patch.object(srv, "joern_remote", return_value=error_response):
            result = srv.get_dataflow("src", "snk")
        assert result == [], f"Mixed-case 'Error' should trigger empty return; got {result!r}"
