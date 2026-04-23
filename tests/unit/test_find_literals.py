"""
Unit tests for the find_literals MCP tool (S4-005).

Run with:
    pytest tests/unit/test_find_literals.py -v
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


# ---------------------------------------------------------------------------
# Helper: produce a realistic Joern REPL list response
# ---------------------------------------------------------------------------

def _list_response(*items):
    """Format items as a Joern REPL List[String] response."""
    inner = ", ".join(f'"{item}"' for item in items)
    return f'val res0: List[String] = List({inner})'


def _empty_list_response():
    return 'val res0: List[String] = List()'


# ===========================================================================
# Test 1: pattern + "any" → no type filter in query
# ===========================================================================

class TestFindLiteralsQueryBuilding:

    def test_any_type_no_filter_in_query(self):
        """literal_type='any' must NOT add a .where() type filter."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("password", literal_type="any")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        assert '.where(_.typeFullName' not in query, (
            f"'any' literal_type should produce no type filter; got query: {query!r}"
        )
        assert 'cpg.literal.code("password")' in query, (
            f"Query should start with cpg.literal.code(pattern); got {query!r}"
        )

    # Test 2: pattern + "string" → .where(_.typeFullName(".*[Ss]tring.*")) in query
    def test_string_type_adds_string_filter(self):
        """literal_type='string' must add the String typeFullName filter."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("password", literal_type="string")

        query = captured_queries[0]
        assert '.where(_.typeFullName(".*[Ss]tring.*"))' in query, (
            f"'string' literal_type should add String type filter; got query: {query!r}"
        )

    # Test 3: pattern + "int" → .where(_.typeFullName(...int...)) in query
    def test_int_type_adds_int_filter(self):
        """literal_type='int' must add the int/long typeFullName filter."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("42", literal_type="int")

        query = captured_queries[0]
        assert '.where(_.typeFullName(' in query, (
            f"'int' literal_type should add a type filter; got query: {query!r}"
        )
        assert '[Ii]nt' in query or 'int' in query.lower(), (
            f"'int' literal_type filter should reference int type; got query: {query!r}"
        )
        assert '[Ll]ong' in query or 'Long' in query or 'long' in query, (
            f"'int' literal_type filter should include Long; got query: {query!r}"
        )

    # Test 4: Unknown literal_type (e.g. "float") → treated as "any" (no filter)
    def test_unknown_literal_type_treated_as_any(self):
        """Unknown literal_type value must fall through to 'any' behavior (no filter)."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("magic", literal_type="float")

        query = captured_queries[0]
        assert '.where(_.typeFullName' not in query, (
            f"Unknown literal_type should behave like 'any' (no filter); got: {query!r}"
        )

    # Test 10: Default literal_type is "any" → no type filter
    def test_default_literal_type_is_any(self):
        """Calling find_literals with only pattern should default to 'any' (no filter)."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("secret")

        query = captured_queries[0]
        assert '.where(_.typeFullName' not in query, (
            f"Default literal_type should be 'any' (no filter); got: {query!r}"
        )

    # Test 9: Literal value with special chars in pattern → passed through
    def test_special_chars_pattern_passed_through(self):
        """Regex special characters in pattern must be passed through to the query unchanged."""
        srv = _import_server()
        captured_queries = []

        def capture(query):
            captured_queries.append(query)
            return _empty_list_response()

        with patch.object(srv, "joern_remote", side_effect=capture):
            srv.find_literals("SELECT.*FROM", literal_type="any")

        query = captured_queries[0]
        assert 'SELECT.*FROM' in query, (
            f"Pattern 'SELECT.*FROM' should be embedded as-is in query; got: {query!r}"
        )


# ===========================================================================
# Tests for return-value handling
# ===========================================================================

class TestFindLiteralsReturnValues:

    # Test 5: joern_remote returns None → []
    def test_none_response_returns_empty_list(self):
        """When joern_remote returns None, find_literals must return []."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.find_literals("password")
        assert result == [], (
            f"None response should yield []; got {result!r}"
        )

    # Test 6: Empty List() → []
    def test_empty_list_response_returns_empty_list(self):
        """When Joern returns an empty List(), find_literals must return []."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=_empty_list_response()):
            result = srv.find_literals("nonexistent")
        assert result == [], (
            f"Empty Joern List() should yield []; got {result!r}"
        )

    # Test 7: Single literal result → parsed correctly with all fields
    def test_single_result_all_fields_parsed(self):
        """A single literal entry must be parsed and all expected fields present."""
        srv = _import_server()
        entry = (
            "literalId=12345L value=password typeFullName=java.lang.String "
            "containingMethod=com.example.Auth.login:void() "
            "file=Auth.java line=42"
        )
        mock_response = _list_response(entry)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals("password")

        assert len(result) == 1, f"Expected 1 result, got {len(result)}: {result!r}"
        item = result[0]
        assert "literalId=12345L" in item, f"literalId missing in: {item!r}"
        assert "value=password" in item, f"value missing in: {item!r}"
        assert "typeFullName=java.lang.String" in item, f"typeFullName missing in: {item!r}"
        assert "containingMethod=com.example.Auth.login:void()" in item, f"containingMethod missing in: {item!r}"
        assert "file=Auth.java" in item, f"file missing in: {item!r}"
        assert "line=42" in item, f"line missing in: {item!r}"

    # Test 8: Multiple results → all parsed
    def test_multiple_results_all_parsed(self):
        """Multiple literal entries must all be parsed and returned."""
        srv = _import_server()
        entry1 = (
            "literalId=100L value=admin typeFullName=java.lang.String "
            "containingMethod=com.example.A.foo:void() file=A.java line=10"
        )
        entry2 = (
            "literalId=200L value=secret typeFullName=java.lang.String "
            "containingMethod=com.example.B.bar:void() file=B.java line=20"
        )
        entry3 = (
            "literalId=300L value=token typeFullName=java.lang.String "
            "containingMethod=com.example.C.baz:void() file=C.java line=30"
        )
        mock_response = _list_response(entry1, entry2, entry3)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals(".*")

        assert len(result) == 3, f"Expected 3 results, got {len(result)}: {result!r}"
        assert any("literalId=100L" in r for r in result), "entry1 missing"
        assert any("literalId=200L" in r for r in result), "entry2 missing"
        assert any("literalId=300L" in r for r in result), "entry3 missing"

    def test_int_type_result_contains_int_typename(self):
        """find_literals with literal_type='int' should return int-typed literals."""
        srv = _import_server()
        entry = (
            "literalId=999L value=42 typeFullName=int "
            "containingMethod=com.example.Math.compute:int() file=Math.java line=5"
        )
        mock_response = _list_response(entry)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals("42", literal_type="int")

        assert len(result) == 1, f"Expected 1 result, got {len(result)}: {result!r}"
        assert "typeFullName=int" in result[0], f"Expected int typeFullName; got {result[0]!r}"

    def test_string_type_result_contains_string_typename(self):
        """find_literals with literal_type='string' should return string-typed literals."""
        srv = _import_server()
        entry = (
            "literalId=888L value=SELECT * FROM users typeFullName=java.lang.String "
            "containingMethod=com.example.Dao.query:void() file=Dao.java line=15"
        )
        mock_response = _list_response(entry)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals("SELECT.*FROM", literal_type="string")

        assert len(result) == 1, f"Expected 1 result, got {len(result)}: {result!r}"
        assert "typeFullName=java.lang.String" in result[0], (
            f"Expected String typeFullName; got {result[0]!r}"
        )

    def test_returns_list_type(self):
        """find_literals must always return a list (never None or other type)."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=_empty_list_response()):
            result = srv.find_literals("anything")
        assert isinstance(result, list), (
            f"find_literals must return list; got {type(result)}"
        )

    def test_line_minus_one_when_no_line_number(self):
        """When Joern cannot determine line number, entry should contain line=-1."""
        srv = _import_server()
        entry = (
            "literalId=777L value=hardcoded typeFullName=java.lang.String "
            "containingMethod=com.example.Foo.bar:void() file=Foo.java line=-1"
        )
        mock_response = _list_response(entry)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals("hardcoded")

        assert len(result) == 1
        assert "line=-1" in result[0], (
            f"line=-1 sentinel should be preserved; got {result[0]!r}"
        )

    def test_empty_containing_method_when_global(self):
        """When a literal has no containing method, containingMethod should be empty string."""
        srv = _import_server()
        entry = (
            "literalId=555L value=global_const typeFullName=java.lang.String "
            "containingMethod= file=Global.java line=1"
        )
        mock_response = _list_response(entry)
        with patch.object(srv, "joern_remote", return_value=mock_response):
            result = srv.find_literals("global_const")

        assert len(result) == 1
        # The entry should come through; containingMethod= is an empty field
        assert "literalId=555L" in result[0], (
            f"literalId should be present even when containingMethod is empty; got {result[0]!r}"
        )
