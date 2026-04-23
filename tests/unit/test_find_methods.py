"""
Unit tests for the find_methods MCP tool (S4-001).

Tests mock joern_remote so no running Joern server is required.

Run with:
    pytest tests/unit/test_find_methods.py -v
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
# Test helpers
# ---------------------------------------------------------------------------

def _make_list_response(*entries):
    """Build a Joern REPL List[String] response from the given string entries."""
    inner = ", ".join(f'"{e}"' for e in entries)
    return f'val res0: List[String] = List({inner})'


SAMPLE_ENTRY = "id=111L name=onReceive fullName=com.android.nfc.NfcService.onReceive:void() file=NfcService.java lineStart=42"
SAMPLE_ENTRY_2 = "id=222L name=onCreate fullName=com.android.nfc.NfcService.onCreate:void() file=NfcService.java lineStart=100"


# ===========================================================================
# Tests for find_methods
# ===========================================================================

class TestFindMethodsNoFilters:
    """Test 1: No filters provided -> error message returned."""

    def test_no_filters_returns_error(self):
        srv = _import_server()
        result = srv.find_methods()
        assert isinstance(result, list), "Should return a list"
        assert len(result) == 1, f"Expected exactly 1 error message, got {len(result)}: {result!r}"
        assert "Error" in result[0], (
            f"No-filter call should return an error string; got {result[0]!r}"
        )

    def test_no_filters_all_none_explicit(self):
        srv = _import_server()
        result = srv.find_methods(name_pattern=None, annotation=None, modifier=None, full_name_pattern=None)
        assert len(result) == 1
        assert "at least one filter" in result[0].lower() or "Error" in result[0], (
            f"Error message should mention required filters; got {result[0]!r}"
        )


class TestFindMethodsNamePattern:
    """Test 2: name_pattern only -> correct CPGQL, returns parsed list."""

    def test_name_pattern_only_builds_correct_query(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.find_methods(name_pattern="onReceive")

        assert '.name("onReceive")' in captured["query"], (
            f"Query should contain .name(\"onReceive\"); got: {captured['query']!r}"
        )
        assert result == [SAMPLE_ENTRY], f"Expected parsed entry; got {result!r}"

    def test_name_pattern_query_starts_with_cpg_method(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(name_pattern="get.*")

        assert captured["query"].startswith("cpg.method"), (
            f"Query must start with 'cpg.method'; got: {captured['query']!r}"
        )


class TestFindMethodsAnnotation:
    """Test 3: annotation only -> query contains .where(_.annotation.name(...))."""

    def test_annotation_only_builds_correct_query(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.find_methods(annotation="RequestMapping")

        assert '.where(_.annotation.name("RequestMapping"))' in captured["query"], (
            f"Query should contain annotation where-clause; got: {captured['query']!r}"
        )
        assert result == [SAMPLE_ENTRY]

    def test_annotation_no_name_pattern_in_query(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(annotation="Override")

        # The query should not start with cpg.method.name(...) — only .where(_.annotation.name(...)) is expected
        assert captured["query"].startswith("cpg.method.where("), (
            f"Query without name_pattern should go directly to .where(...); got: {captured['query']!r}"
        )


class TestFindMethodsModifier:
    """Test 4: modifier is uppercased in the query."""

    def test_modifier_is_uppercased(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(modifier="public")

        assert '.where(_.modifier.modifierType("PUBLIC"))' in captured["query"], (
            f"Modifier should be uppercased in query; got: {captured['query']!r}"
        )

    def test_modifier_static_uppercased(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(modifier="static")

        assert '"STATIC"' in captured["query"], (
            f"'static' modifier should become 'STATIC' in query; got: {captured['query']!r}"
        )


class TestFindMethodsFullNamePattern:
    """Test 5: full_name_pattern only -> uses .fullName(...)."""

    def test_full_name_pattern_builds_correct_query(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.find_methods(full_name_pattern="com.android.*")

        assert '.fullName("com.android.*")' in captured["query"], (
            f"Query should contain .fullName(...); got: {captured['query']!r}"
        )
        assert result == [SAMPLE_ENTRY]


class TestFindMethodsMultipleFilters:
    """Test 6: multiple filters combined -> all parts appear in query."""

    def test_all_filters_combined(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(
                name_pattern="on.*",
                annotation="Override",
                modifier="public",
                full_name_pattern="com.example.*",
            )

        q = captured["query"]
        assert '.name("on.*")' in q, f"name_pattern missing from query: {q!r}"
        assert '.fullName("com.example.*")' in q, f"full_name_pattern missing from query: {q!r}"
        assert '.where(_.annotation.name("Override"))' in q, f"annotation missing from query: {q!r}"
        assert '.where(_.modifier.modifierType("PUBLIC"))' in q, f"modifier missing from query: {q!r}"

    def test_name_and_modifier_combined(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response(SAMPLE_ENTRY)

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(name_pattern="get.*", modifier="private")

        q = captured["query"]
        assert '.name("get.*")' in q
        assert '"PRIVATE"' in q


class TestFindMethodsNoneResponse:
    """Test 7: joern_remote returns None -> returns []."""

    def test_none_response_returns_empty_list(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.find_methods(name_pattern="any")
        assert result == [], f"None response should give []; got {result!r}"


class TestFindMethodsEmptyList:
    """Test 8: empty List() response -> returns []."""

    def test_empty_list_response(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: List[String] = List()'):
            result = srv.find_methods(name_pattern="nonexistent")
        assert result == [], f"Empty List() should give []; got {result!r}"


class TestFindMethodsSingleResult:
    """Test 9: single result -> parsed correctly."""

    def test_single_result_parsed(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=_make_list_response(SAMPLE_ENTRY)):
            result = srv.find_methods(name_pattern="onReceive")
        assert len(result) == 1, f"Expected 1 result, got {len(result)}: {result!r}"
        assert result[0] == SAMPLE_ENTRY, f"Entry mismatch; got {result[0]!r}"

    def test_single_result_contains_id_field(self):
        srv = _import_server()
        entry = "id=999L name=myMethod fullName=com.Foo.myMethod:void() file=Foo.java lineStart=10"
        with patch.object(srv, "joern_remote", return_value=_make_list_response(entry)):
            result = srv.find_methods(annotation="MyAnnotation")
        assert len(result) == 1
        assert "id=999L" in result[0]
        assert "name=myMethod" in result[0]
        assert "fullName=com.Foo.myMethod:void()" in result[0]
        assert "file=Foo.java" in result[0]
        assert "lineStart=10" in result[0]


class TestFindMethodsMultipleResults:
    """Test 10: multiple results -> all parsed."""

    def test_two_results_both_parsed(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=_make_list_response(SAMPLE_ENTRY, SAMPLE_ENTRY_2)):
            result = srv.find_methods(name_pattern="on.*")
        assert len(result) == 2, f"Expected 2 results, got {len(result)}: {result!r}"
        assert result[0] == SAMPLE_ENTRY, f"First entry mismatch; got {result[0]!r}"
        assert result[1] == SAMPLE_ENTRY_2, f"Second entry mismatch; got {result[1]!r}"

    def test_many_results_all_parsed(self):
        srv = _import_server()
        entries = [
            f"id={i}L name=method{i} fullName=com.Foo.method{i}:void() file=Foo.java lineStart={i * 10}"
            for i in range(1, 6)
        ]
        with patch.object(srv, "joern_remote", return_value=_make_list_response(*entries)):
            result = srv.find_methods(modifier="public")
        assert len(result) == 5, f"Expected 5 results, got {len(result)}: {result!r}"
        for i, entry in enumerate(entries):
            assert result[i] == entry, f"Entry {i} mismatch; got {result[i]!r}"


class TestFindMethodsQueryStructure:
    """Additional tests verifying query structure and map/l suffix."""

    def test_query_ends_with_map_and_l(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response()

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(name_pattern="foo")

        assert captured["query"].endswith(".l"), (
            f"Query must end with '.l'; got: {captured['query']!r}"
        )
        assert ".map(" in captured["query"], (
            f"Query must contain .map(...); got: {captured['query']!r}"
        )

    def test_query_map_contains_all_fields(self):
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response()

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(name_pattern="bar")

        q = captured["query"]
        assert "m.id" in q, f"map should reference m.id; got: {q!r}"
        assert "m.name" in q, f"map should reference m.name; got: {q!r}"
        assert "m.fullName" in q, f"map should reference m.fullName; got: {q!r}"
        assert "m.filename" in q, f"map should reference m.filename; got: {q!r}"
        assert "m.lineNumber" in q, f"map should reference m.lineNumber; got: {q!r}"

    def test_name_pattern_before_full_name_pattern_in_query(self):
        """name_pattern filter appears before full_name_pattern in the query chain."""
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response()

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(name_pattern="foo", full_name_pattern="com.bar.*")

        q = captured["query"]
        name_pos = q.find('.name("foo")')
        full_pos = q.find('.fullName("com.bar.*")')
        assert name_pos < full_pos, (
            f".name() should appear before .fullName() in query; got: {q!r}"
        )

    def test_returns_list_type(self):
        """find_methods always returns a list, even for error case."""
        srv = _import_server()
        result = srv.find_methods()
        assert isinstance(result, list), f"Should always return list; got {type(result)}"

    def test_modifier_already_uppercase_unchanged(self):
        """Uppercasing an already-uppercase modifier doesn't break the query."""
        srv = _import_server()
        captured = {}

        def mock_remote(query):
            captured["query"] = query
            return _make_list_response()

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.find_methods(modifier="PRIVATE")

        assert '"PRIVATE"' in captured["query"], (
            f"Already-uppercase modifier should be preserved; got: {captured['query']!r}"
        )
