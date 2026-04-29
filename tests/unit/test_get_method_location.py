"""
Unit tests for the get_method_location tool in server_tools.py.

Run with:
    pytest tests/unit/test_get_method_location.py -v
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


# ===========================================================================
# Tests for get_method_location
# ===========================================================================


class TestGetMethodLocation:
    """Unit tests for get_method_location with joern_remote mocked."""

    # Test 1: Neither method_id nor method_full_name → returns error string
    def test_no_params_returns_error(self):
        srv = _import_server()
        result = srv.get_method_location()
        assert "Error" in result, (
            f"Calling with no params should return an error string; got {result!r}"
        )

    # Test 2: method_id with L suffix → L stripped, id used in query
    def test_method_id_with_l_suffix_strips_l(self):
        srv = _import_server()
        captured_query = []

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=Foo.java lineStart=10 lineEnd=25 columnStart=4 columnEnd=1"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_method_location(method_id="111669149702L")

        assert len(captured_query) == 1
        query = captured_query[0]
        assert "111669149702L" in query, f"L suffix should be preserved in query; got {query!r}"
        assert "cpg.method.id(" in query, f"Query should use .id() traversal; got {query!r}"

    # Test 3: method_id without L suffix → works correctly
    def test_method_id_without_l_suffix(self):
        srv = _import_server()
        captured_query = []

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=Bar.java lineStart=5 lineEnd=20 columnStart=2 columnEnd=0"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_method_location(method_id="999999")

        assert len(captured_query) == 1
        query = captured_query[0]
        assert "999999" in query, f"Numeric ID should appear in query; got {query!r}"
        assert "cpg.method.id(" in query, f"Query should use .id() traversal; got {query!r}"
        assert "lineStart" in result, f"Result should contain location fields; got {result!r}"

    # Test 4: method_full_name → fullName used in query
    def test_method_full_name_uses_full_name_traversal(self):
        srv = _import_server()
        captured_query = []

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=Foo.java lineStart=10 lineEnd=25 columnStart=4 columnEnd=1"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_method_location(method_full_name="com.example.Foo.bar:void()")

        assert len(captured_query) == 1
        query = captured_query[0]
        assert "cpg.method.fullName(" in query, f"Query should use .fullName() traversal; got {query!r}"
        assert "com.example.Foo.bar:void()" in query, f"Full name should appear in query; got {query!r}"

    # Test 5: joern_remote returns None → returns ""
    def test_joern_remote_none_returns_empty_string(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_location(method_id="123L")
        assert result == "", (
            f"When joern_remote returns None, result should be ''; got {result!r}"
        )

    # Test 6: Method not found (empty string from Joern) → returns ""
    def test_method_not_found_returns_empty_string(self):
        srv = _import_server()
        # Joern returns empty string for headOption.getOrElse("") when method not found
        with patch.object(srv, "joern_remote", return_value='val res0: String = ""'):
            result = srv.get_method_location(method_full_name="com.NonExistent.method:void()")
        assert result == "", (
            f"When method not found (Joern returns empty string), result should be ''; got {result!r}"
        )

    # Test 7: Successful result → all 5 fields present
    def test_successful_result_contains_all_fields(self):
        srv = _import_server()
        location_str = "file=Foo.java lineStart=10 lineEnd=25 columnStart=4 columnEnd=1"
        with patch.object(srv, "joern_remote", return_value=f'val res0: String = "{location_str}"'):
            result = srv.get_method_location(method_id="111669149702L")

        assert "file=" in result, f"Result should contain 'file='; got {result!r}"
        assert "lineStart=" in result, f"Result should contain 'lineStart='; got {result!r}"
        assert "lineEnd=" in result, f"Result should contain 'lineEnd='; got {result!r}"
        assert "columnStart=" in result, f"Result should contain 'columnStart='; got {result!r}"
        assert "columnEnd=" in result, f"Result should contain 'columnEnd='; got {result!r}"

    # Test 8: Method with negative line numbers (getOrElse(-1)) → parsed correctly
    def test_negative_line_numbers_parsed_correctly(self):
        srv = _import_server()
        location_str = "file=Unknown.java lineStart=-1 lineEnd=-1 columnStart=-1 columnEnd=-1"
        with patch.object(srv, "joern_remote", return_value=f'val res0: String = "{location_str}"'):
            result = srv.get_method_location(method_id="42L")

        assert "lineStart=-1" in result, f"Negative lineStart should be preserved; got {result!r}"
        assert "lineEnd=-1" in result, f"Negative lineEnd should be preserved; got {result!r}"
        assert "columnStart=-1" in result, f"Negative columnStart should be preserved; got {result!r}"
        assert "columnEnd=-1" in result, f"Negative columnEnd should be preserved; got {result!r}"

    # Test 9: method_id and method_full_name both provided → uses method_id (id takes precedence)
    def test_method_id_takes_precedence_over_full_name(self):
        srv = _import_server()
        captured_query = []

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=Foo.java lineStart=10 lineEnd=25 columnStart=4 columnEnd=1"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_method_location(
                method_id="111669149702L",
                method_full_name="com.example.Foo.bar:void()",
            )

        assert len(captured_query) == 1
        query = captured_query[0]
        assert "cpg.method.id(" in query, f"ID should take precedence; query uses .id(); got {query!r}"
        assert "fullName" not in query, f"fullName should not appear when id is provided; got {query!r}"

    # Test 10: Result with file path containing slashes → parsed correctly
    def test_file_path_with_slashes_parsed_correctly(self):
        srv = _import_server()
        location_str = "file=/src/main/java/com/example/Foo.java lineStart=15 lineEnd=30 columnStart=0 columnEnd=5"
        with patch.object(srv, "joern_remote", return_value=f'val res0: String = "{location_str}"'):
            result = srv.get_method_location(method_full_name="com.example.Foo.doSomething:void()")

        assert "/src/main/java/com/example/Foo.java" in result, (
            f"File path with slashes should be preserved; got {result!r}"
        )
        assert "lineStart=15" in result, f"lineStart should be correct; got {result!r}"

    # Bonus test 11: Query contains the correct map expression with all Scala interpolation fields
    def test_query_contains_correct_map_expression(self):
        srv = _import_server()
        captured_query = []

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=A.java lineStart=1 lineEnd=2 columnStart=0 columnEnd=0"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            srv.get_method_location(method_id="100L")

        query = captured_query[0]
        assert "m.filename" in query, f"Query should reference m.filename; got {query!r}"
        assert "m.lineNumber" in query, f"Query should reference m.lineNumber; got {query!r}"
        assert "m.lineNumberEnd" in query, f"Query should reference m.lineNumberEnd; got {query!r}"
        assert "m.columnNumber" in query, f"Query should reference m.columnNumber; got {query!r}"
        assert "m.columnNumberEnd" in query, f"Query should reference m.columnNumberEnd; got {query!r}"
        assert "headOption.getOrElse" in query, f"Query should use headOption.getOrElse; got {query!r}"

    # Bonus test 12: method_full_name containing special characters
    def test_method_full_name_with_special_chars(self):
        srv = _import_server()
        captured_query = []
        full_name = "com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent)"

        def mock_remote(query):
            captured_query.append(query)
            return 'val res0: String = "file=NfcService.java lineStart=100 lineEnd=120 columnStart=4 columnEnd=5"'

        with patch.object(srv, "joern_remote", side_effect=mock_remote):
            result = srv.get_method_location(method_full_name=full_name)

        query = captured_query[0]
        assert full_name in query, f"Full method name with special chars should appear in query; got {query!r}"
        assert "lineStart=100" in result, f"lineStart should be extracted; got {result!r}"
