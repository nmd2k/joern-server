"""
Unit tests for the get_call_arguments MCP tool.

Run with:
    pytest tests/unit/test_get_call_arguments.py -v
"""

import sys
import os
import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup: allow imports from the mcp-joern package directory
# ---------------------------------------------------------------------------
MJOERN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mcp-joern")
if MJOERN_DIR not in sys.path:
    sys.path.insert(0, MJOERN_DIR)


def _import_server():
    """Import server module; cache on first call."""
    import server as srv
    return srv


class TestGetCallArguments:
    """Unit tests for get_call_arguments with joern_remote mocked."""

    # Test 1: Valid call_id with L suffix -> L stripped in query, results parsed
    def test_call_id_with_l_suffix_strips_l_in_query(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=userInput typeFullName=java.lang.String nodeId=222L"'
            ')'
        )
        captured_queries = []

        def mock_remote(query):
            captured_queries.append(query)
            return mock_output

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_call_arguments("111669149702L")

        assert len(captured_queries) == 1
        assert "111669149702" in captured_queries[0]
        assert "111669149702L" not in captured_queries[0], (
            "The L suffix should be stripped before embedding in the query"
        )
        assert isinstance(result, list)
        assert len(result) == 1

    # Test 2: Valid call_id without L suffix -> works correctly
    def test_call_id_without_l_suffix_works(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=x typeFullName=int nodeId=100L"'
            ')'
        )
        captured_queries = []

        def mock_remote(query):
            captured_queries.append(query)
            return mock_output

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_call_arguments("999")

        assert len(captured_queries) == 1
        assert "999" in captured_queries[0]
        assert isinstance(result, list)
        assert len(result) == 1

    # Test 3: Empty arguments (no args) -> []
    def test_empty_arguments_returns_empty_list(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: List[String] = List()'):
            result = srv.get_call_arguments("111L")
        assert result == [], f"Expected [], got {result!r}"

    # Test 4: joern_remote returns None -> []
    def test_joern_remote_none_returns_empty_list(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_call_arguments("111L")
        assert result == [], f"Expected [] when joern_remote returns None; got {result!r}"

    # Test 5: Single argument -> parsed correctly with correct fields
    def test_single_argument_parsed_correctly(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=request typeFullName=javax.servlet.http.HttpServletRequest nodeId=500L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("200L")

        assert len(result) == 1
        entry = result[0]
        assert "argIndex=1" in entry
        assert "code=request" in entry
        assert "typeFullName=javax.servlet.http.HttpServletRequest" in entry
        assert "nodeId=500L" in entry

    # Test 6: Multiple arguments -> all parsed, correct argIndex values
    def test_multiple_arguments_all_parsed(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=userInput typeFullName=java.lang.String nodeId=301L", '
            '"argIndex=2 code=42 typeFullName=int nodeId=302L", '
            '"argIndex=3 code=flag typeFullName=boolean nodeId=303L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("111669149702L")

        assert len(result) == 3, f"Expected 3 arguments, got {len(result)}: {result!r}"
        indices = [int(e.split("argIndex=")[1].split()[0]) for e in result]
        assert 1 in indices
        assert 2 in indices
        assert 3 in indices

    # Test 7: Argument with spaces in code -> parsed correctly
    def test_argument_with_spaces_in_code_parsed(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=foo bar baz typeFullName=java.lang.String nodeId=400L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("123L")

        assert len(result) == 1
        assert "foo bar baz" in result[0], (
            f"Code with spaces should be preserved; got {result[0]!r}"
        )

    # Test 8: Literal argument (number/string constant) -> parsed
    def test_literal_argument_parsed(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=42 typeFullName=int nodeId=600L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("777L")

        assert len(result) == 1
        assert "code=42" in result[0]
        assert "typeFullName=int" in result[0]

    # Test 9: Complex type name in typeFullName -> parsed
    def test_complex_type_full_name_parsed(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=list typeFullName=java.util.List nodeId=700L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("888L")

        assert len(result) == 1
        assert "typeFullName=java.util.List" in result[0], (
            f"Complex generic type name should be preserved; got {result[0]!r}"
        )

    # Test 10: call_id with only numeric part -> L-stripping handles it gracefully
    def test_numeric_only_call_id_handled(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=1 code=x typeFullName=int nodeId=901L"'
            ')'
        )
        captured_queries = []

        def mock_remote(query):
            captured_queries.append(query)
            return mock_output

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_call_arguments("12345")

        assert "12345" in captured_queries[0]
        # Should not double-strip or break
        assert isinstance(result, list)
        assert len(result) == 1

    # Test 11: Returned list items contain all four expected fields
    def test_result_items_contain_all_expected_fields(self):
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"argIndex=0 code=this typeFullName=com.example.MyClass nodeId=1000L"'
            ')'
        )
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_call_arguments("555L")

        assert len(result) == 1
        entry = result[0]
        for field in ("argIndex=", "code=", "typeFullName=", "nodeId="):
            assert field in entry, f"Field '{field}' missing from entry: {entry!r}"

    # Test 12: Query structure contains expected CPGQL pattern
    def test_query_contains_expected_cpgql_pattern(self):
        srv = _import_server()
        captured_queries = []

        def mock_remote(query):
            captured_queries.append(query)
            return 'val res0: List[String] = List()'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.get_call_arguments("98765L")

        query = captured_queries[0]
        assert "cpg.call.id(98765)" in query, (
            f"Query should contain 'cpg.call.id(98765)'; got {query!r}"
        )
        assert ".argument" in query
        assert ".map(" in query
        assert "argIndex=" in query
        assert "typeFullName=" in query
        assert "nodeId=" in query
