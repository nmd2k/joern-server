"""
Unit tests for the find_calls MCP tool in mcp-joern/server_tools.py.

Run with:
    pytest tests/unit/test_find_calls.py -v
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

from common_tools import remove_ansi_escape_sequences


def _import_server():
    """Import server module; cache on first call."""
    import server as srv
    return srv


# ===========================================================================
# Tests for find_calls
# ===========================================================================


class TestFindCalls:
    """Unit tests for find_calls tool with joern_remote mocked."""

    # ------------------------------------------------------------------
    # Test 1: callee_name_pattern only — correct CPGQL sent, result parsed
    # ------------------------------------------------------------------
    def test_callee_pattern_only_correct_query_and_result(self):
        """callee_name_pattern only: correct CPGQL is sent and result is parsed."""
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"callId=100L calleeName=exec containingMethod=com.Foo.main:void() file=Foo.java line=42"'
            ')'
        )
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return mock_output

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            result = srv.find_calls("exec")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        assert 'cpg.call.name("exec")' in query
        assert '.where(' not in query, "No .where() clause expected when method_full_name_pattern is None"
        assert '.map(' in query
        assert isinstance(result, list)
        assert len(result) == 1

    # ------------------------------------------------------------------
    # Test 2: With method_full_name_pattern — .where(_.method.fullName(...)) in query
    # ------------------------------------------------------------------
    def test_with_method_full_name_pattern_includes_where_clause(self):
        """With method_full_name_pattern: .where(_.method.fullName(...)) appears in the query."""
        srv = _import_server()
        mock_output = (
            'val res0: List[String] = List('
            '"callId=200L calleeName=query containingMethod=com.Dao.find:void() file=Dao.java line=10"'
            ')'
        )
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return mock_output

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            result = srv.find_calls("query", method_full_name_pattern="com.Dao.*")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        assert 'cpg.call.name("query")' in query
        assert '.where(_.method.fullName("com.Dao.*"))' in query
        assert isinstance(result, list)
        assert len(result) == 1

    # ------------------------------------------------------------------
    # Test 3: None from joern_remote — returns []
    # ------------------------------------------------------------------
    def test_none_response_returns_empty_list(self):
        """None from joern_remote should return []."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.find_calls("eval")
        assert result == [], f"Expected [], got {result!r}"

    # ------------------------------------------------------------------
    # Test 4: Empty List() — returns []
    # ------------------------------------------------------------------
    def test_empty_list_response_returns_empty_list(self):
        """Empty List() REPL output should return []."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: List[String] = List()'):
            result = srv.find_calls("exec")
        assert result == [], f"Expected [], got {result!r}"

    # ------------------------------------------------------------------
    # Test 5: Single result — parsed correctly
    # ------------------------------------------------------------------
    def test_single_result_parsed_correctly(self):
        """Single result item should be returned in a one-element list."""
        srv = _import_server()
        entry = "callId=999L calleeName=exec containingMethod=com.Shell.run:void() file=Shell.java line=55"
        mock_output = f'val res0: List[String] = List("{entry}")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.find_calls("exec")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == entry

    # ------------------------------------------------------------------
    # Test 6: Multiple results — all parsed
    # ------------------------------------------------------------------
    def test_multiple_results_all_parsed(self):
        """Multiple result items should all be returned."""
        srv = _import_server()
        entries = [
            "callId=1L calleeName=exec containingMethod=com.A.run:void() file=A.java line=10",
            "callId=2L calleeName=exec containingMethod=com.B.run:void() file=B.java line=20",
            "callId=3L calleeName=exec containingMethod=com.C.run:void() file=C.java line=30",
        ]
        inner = ", ".join(f'"{e}"' for e in entries)
        mock_output = f'val res0: List[String] = List({inner})'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.find_calls("exec")
        assert isinstance(result, list)
        assert len(result) == 3
        for entry in entries:
            assert entry in result, f"Entry {entry!r} not found in result {result!r}"

    # ------------------------------------------------------------------
    # Test 7: Pattern with special regex chars (e.g. "Runtime.*") — passed through as-is
    # ------------------------------------------------------------------
    def test_special_regex_chars_passed_through(self):
        """Regex special characters in callee_name_pattern must be passed to Joern unchanged."""
        srv = _import_server()
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return 'val res0: List[String] = List()'

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            srv.find_calls("Runtime.*")

        assert len(captured_queries) == 1
        assert 'cpg.call.name("Runtime.*")' in captured_queries[0]

    # ------------------------------------------------------------------
    # Test 8: Result contains expected fields (callId, calleeName, containingMethod, file, line)
    # ------------------------------------------------------------------
    def test_result_contains_expected_fields(self):
        """Each result string should contain all five required fields."""
        srv = _import_server()
        entry = "callId=42L calleeName=eval containingMethod=com.Script.execute:void() file=Script.java line=7"
        mock_output = f'val res0: List[String] = List("{entry}")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.find_calls("eval")
        assert len(result) == 1
        item = result[0]
        assert "callId=" in item, f"'callId=' not in {item!r}"
        assert "calleeName=" in item, f"'calleeName=' not in {item!r}"
        assert "containingMethod=" in item, f"'containingMethod=' not in {item!r}"
        assert "file=" in item, f"'file=' not in {item!r}"
        assert "line=" in item, f"'line=' not in {item!r}"

    # ------------------------------------------------------------------
    # Test 9: Verify containingMethod filter restricts query (method_full_name_pattern)
    # ------------------------------------------------------------------
    def test_method_full_name_pattern_restricts_query(self):
        """The method_full_name_pattern restricts scope — verify query structure."""
        srv = _import_server()
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return 'val res0: List[String] = List()'

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            srv.find_calls("exec", method_full_name_pattern="com.dangerous.*")

        assert len(captured_queries) == 1
        query = captured_queries[0]
        # Both callee filter and method scope restriction must be present
        assert 'cpg.call.name("exec")' in query
        assert '.where(_.method.fullName("com.dangerous.*"))' in query
        # .where must appear after the .name() call
        name_pos = query.index('.name("exec")')
        where_pos = query.index('.where(')
        assert where_pos > name_pos, "'.where()' must come after '.name()' in the query"

    # ------------------------------------------------------------------
    # Test 10: ANSI codes in response — still parsed correctly
    # ------------------------------------------------------------------
    def test_ansi_codes_in_response_parsed_correctly(self):
        """ANSI escape codes in the response should be stripped and the list parsed."""
        srv = _import_server()
        entry = "callId=77L calleeName=exec containingMethod=com.Foo.bar:void() file=Foo.java line=99"
        # Simulate ANSI-wrapped REPL output (as joern_remote already strips these,
        # we verify extract_list handles a pre-stripped string correctly).
        raw_with_ansi = (
            '\x1b[33mval\x1b[0m \x1b[36mres0\x1b[0m: '
            '\x1b[32mList[String]\x1b[0m = '
            f'List("{entry}")\n'
        )
        cleaned = remove_ansi_escape_sequences(raw_with_ansi)
        # joern_remote returns already-cleaned output; mock it returning cleaned string
        with patch.object(srv, "joern_remote", return_value=cleaned):
            result = srv.find_calls("exec")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == entry, f"Expected {entry!r}, got {result[0]!r}"

    # ------------------------------------------------------------------
    # Additional test: no method_full_name_pattern means no .where() in query
    # ------------------------------------------------------------------
    def test_no_method_filter_omits_where_clause(self):
        """When method_full_name_pattern is None, the query must not contain .where()."""
        srv = _import_server()
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return 'val res0: List[String] = List()'

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            srv.find_calls("query", method_full_name_pattern=None)

        assert len(captured_queries) == 1
        assert '.where(' not in captured_queries[0]

    # ------------------------------------------------------------------
    # Additional test: query ends with .l (Joern list terminator)
    # ------------------------------------------------------------------
    def test_query_ends_with_list_terminator(self):
        """The generated CPGQL query must end with '.l' to materialise the traversal."""
        srv = _import_server()
        captured_queries = []

        def fake_remote(query):
            captured_queries.append(query)
            return 'val res0: List[String] = List()'

        with patch.object(srv, "joern_remote", side_effect=fake_remote):
            srv.find_calls("exec")

        assert captured_queries[0].endswith(".l"), (
            f"Query must end with '.l'; got: {captured_queries[0]!r}"
        )

    # ------------------------------------------------------------------
    # Additional test: empty string joern_remote response returns []
    # ------------------------------------------------------------------
    def test_empty_string_response_returns_empty_list(self):
        """An empty string response from joern_remote should return []."""
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.find_calls("exec")
        assert result == [], f"Expected [], got {result!r}"
