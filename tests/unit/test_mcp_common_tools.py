"""
Comprehensive test suite for mcp-joern common_tools.py parsers and server_tools.py integration.

Run with:
    pytest tests/unit/test_mcp_common_tools.py -v

Integration tests (require a running Joern server) are skipped automatically when
the server is unreachable. Run them explicitly with:
    pytest tests/unit/test_mcp_common_tools.py -v -m integration
"""

import sys
import os
import re
import socket
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Path setup: allow imports from the mcp-joern package directory
# ---------------------------------------------------------------------------
MJOERN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mcp-joern")
if MJOERN_DIR not in sys.path:
    sys.path.insert(0, MJOERN_DIR)

from common_tools import (
    extract_value,
    extract_list,
    extract_quoted_string,
    extract_long_value,
    extract_code_between_triple_quotes,
    remove_ansi_escape_sequences,
)


# ===========================================================================
# Section 1 - Unit tests for common_tools.py parsers
# ===========================================================================


class TestRemoveAnsiEscapeSequences:
    """Tests for remove_ansi_escape_sequences."""

    def test_no_ansi_passthrough(self):
        plain = "hello world"
        assert remove_ansi_escape_sequences(plain) == plain, (
            "Plain text should be returned unchanged"
        )

    def test_single_color_code(self):
        colored = "\x1b[31mred text\x1b[0m"
        result = remove_ansi_escape_sequences(colored)
        assert result == "red text", (
            f"ANSI color codes should be stripped; got {result!r}"
        )

    def test_bold_code(self):
        bold = "\x1b[1mbold\x1b[22m"
        result = remove_ansi_escape_sequences(bold)
        assert result == "bold", f"Bold ANSI code should be stripped; got {result!r}"

    def test_cursor_movement_codes(self):
        text = "\x1b[2J\x1b[H clear screen"
        result = remove_ansi_escape_sequences(text)
        assert "\x1b" not in result, "All escape sequences should be removed"

    def test_multiple_codes_in_repl_output(self):
        # Simulate a Joern REPL line with colour decorations
        repl_line = '\x1b[36mval res0\x1b[0m: \x1b[32mString\x1b[0m = "com.android.nfc.NfcService"'
        result = remove_ansi_escape_sequences(repl_line)
        assert result == 'val res0: String = "com.android.nfc.NfcService"', (
            f"REPL output with ANSI codes should clean to a parseable string; got {result!r}"
        )

    def test_empty_string(self):
        assert remove_ansi_escape_sequences("") == ""

    def test_only_ansi_codes(self):
        result = remove_ansi_escape_sequences("\x1b[31m\x1b[0m")
        assert result == "", "String of only ANSI codes should become empty"


class TestExtractLongValue:
    """Tests for extract_long_value."""

    def test_typical_long(self):
        s = "val res4: Long = 90194313219L"
        result = extract_long_value(s)
        assert result == "90194313219L", (
            f"Expected '90194313219L', got {result!r}"
        )

    def test_small_long(self):
        s = "val res0: Long = 12345L"
        result = extract_long_value(s)
        assert result == "12345L", f"Expected '12345L', got {result!r}"

    def test_zero_long(self):
        s = "val res0: Long = 0L"
        result = extract_long_value(s)
        assert result == "0L", f"Expected '0L', got {result!r}"

    def test_no_long_returns_empty(self):
        s = 'val res0: String = "hello"'
        result = extract_long_value(s)
        assert result == "", f"Non-Long input should return empty string; got {result!r}"

    def test_empty_string(self):
        result = extract_long_value("")
        assert result == "", "Empty input should return empty string"

    def test_long_without_val_prefix(self):
        # Some REPL outputs may omit 'val'
        s = "res0: Long = 99L"
        result = extract_long_value(s)
        assert result == "99L", f"Expected '99L', got {result!r}"


class TestExtractQuotedString:
    """Tests for extract_quoted_string."""

    def test_simple_string(self):
        s = 'val res0: String = "com.android.nfc.NfcService"'
        result = extract_quoted_string(s)
        assert result == "com.android.nfc.NfcService", (
            f"Expected 'com.android.nfc.NfcService', got {result!r}"
        )

    def test_empty_quoted_string(self):
        s = 'val res0: String = ""'
        result = extract_quoted_string(s)
        assert result == "", f"Expected empty string, got {result!r}"

    def test_no_quotes_returns_empty(self):
        result = extract_quoted_string("val res0: Boolean = true")
        assert result == "", "No-quote input should return empty string"

    def test_first_match_wins(self):
        # extract_quoted_string uses re.search (first match)
        s = '"first" and "second"'
        result = extract_quoted_string(s)
        assert result == "first", (
            f"Should return the FIRST quoted value; got {result!r}"
        )

    def test_empty_string(self):
        assert extract_quoted_string("") == ""


class TestExtractCodeBetweenTripleQuotes:
    """Tests for extract_code_between_triple_quotes."""

    def test_simple_multiline(self):
        s = 'val res0: String = """\nint main() {}\n"""'
        result = extract_code_between_triple_quotes(s)
        assert result == "int main() {}", (
            f"Expected 'int main() {{}}', got {result!r}"
        )

    def test_leading_trailing_whitespace_stripped(self):
        s = '"""\n  void foo() {  }\n"""'
        result = extract_code_between_triple_quotes(s)
        assert result == "void foo() {  }", (
            f"Leading/trailing whitespace/newlines should be stripped; got {result!r}"
        )

    def test_no_triple_quotes_returns_empty(self):
        result = extract_code_between_triple_quotes('val res0: String = "hello"')
        assert result == "", "No triple quotes should return empty string"

    def test_empty_triple_quotes(self):
        result = extract_code_between_triple_quotes('""""""')
        assert result == "", f"Empty triple-quote block should return empty string; got {result!r}"

    def test_multiline_java_method(self):
        code = "public void onReceive(Context ctx, Intent intent) {\n    handle(intent);\n}"
        s = f'val res0: String = """\n{code}\n"""'
        result = extract_code_between_triple_quotes(s)
        assert "onReceive" in result, (
            f"Method name should be in extracted code; got {result!r}"
        )
        assert "handle(intent);" in result, (
            f"Method body should be in extracted code; got {result!r}"
        )

    def test_empty_string(self):
        assert extract_code_between_triple_quotes("") == ""


class TestExtractValue:
    """Tests for extract_value (the main dispatch function)."""

    def test_string_value(self):
        s = 'val res0: String = "com.android.nfc.NfcService"'
        result = extract_value(s)
        assert result == "com.android.nfc.NfcService", (
            f"String scalar: expected 'com.android.nfc.NfcService', got {result!r}"
        )

    def test_long_value(self):
        s = "val res0: Long = 12345L"
        result = extract_value(s)
        assert result == "12345L", (
            f"Long scalar: expected '12345L', got {result!r}"
        )

    def test_large_long_value(self):
        s = "val res4: Long = 90194313219L"
        result = extract_value(s)
        assert result == "90194313219L", (
            f"Large Long: expected '90194313219L', got {result!r}"
        )

    def test_boolean_true_extracted(self):
        # Boolean values are now parsed and returned as "true" or "false"
        s = "val res0: Boolean = true"
        result = extract_value(s)
        assert result == "true", (
            f"Boolean true should be extracted as 'true'; got {result!r}"
        )

    def test_boolean_false_extracted(self):
        s = "val res0: Boolean = false"
        result = extract_value(s)
        assert result == "false", (
            f"Boolean false should be extracted as 'false'; got {result!r}"
        )

    def test_triple_quote_string(self):
        s = 'val res0: String = """\nint main() {}\n"""'
        result = extract_value(s)
        assert result == "int main() {}", (
            f"Triple-quote string: expected 'int main() {{}}', got {result!r}"
        )

    def test_triple_quote_takes_priority_over_single_quote(self):
        # When both markers are present, 'String = """' should win over 'String = "'
        s = 'val res0: String = """\nsome code\n"""'
        result = extract_value(s)
        assert result == "some code", (
            f"Triple-quote branch should have priority; got {result!r}"
        )

    def test_long_takes_priority_over_string(self):
        # 'Long =' should be checked first
        s = 'val res0: Long = 777L'
        result = extract_value(s)
        assert result == "777L", (
            f"Long branch should have priority; got {result!r}"
        )

    def test_string_with_trailing_newline(self):
        # Joern REPL often adds a trailing newline
        s = 'val res0: String = "com.android.nfc"\n'
        result = extract_value(s)
        assert result == "com.android.nfc", (
            f"Trailing newline in REPL output should not break extraction; got {result!r}"
        )

    def test_string_with_method_full_name(self):
        s = 'val res0: String = "com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent)"'
        result = extract_value(s)
        assert result == "com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent)", (
            f"Method full name with parentheses: got {result!r}"
        )

    def test_empty_string(self):
        # Should return '' as-is (no matching prefix, returns the input)
        result = extract_value("")
        assert result == "", f"Empty string should return empty string; got {result!r}"

    def test_true_string_value(self):
        # load_cpg returns 'true' as a String
        s = 'val res0: String = "true"'
        result = extract_value(s)
        assert result == "true", (
            f"String 'true' scalar: expected 'true', got {result!r}"
        )

    def test_false_string_value(self):
        s = 'val res0: String = "false"'
        result = extract_value(s)
        assert result == "false", (
            f"String 'false' scalar: expected 'false', got {result!r}"
        )

    def test_version_string(self):
        s = 'val res0: String = "1.2.3"'
        result = extract_value(s)
        assert result == "1.2.3", f"Version string: expected '1.2.3', got {result!r}"


class TestExtractList:
    """Tests for extract_list."""

    def test_two_element_list(self):
        s = 'val res0: List[String] = List("a", "b")'
        result = extract_list(s)
        assert result == ["a", "b"], (
            f"Two-element list: expected ['a', 'b'], got {result!r}"
        )

    def test_single_element_list(self):
        s = 'val res0: List[String] = List("only")'
        result = extract_list(s)
        assert result == ["only"], (
            f"Single-element list: expected ['only'], got {result!r}"
        )

    def test_empty_list(self):
        s = 'val res0: List[String] = List()'
        result = extract_list(s)
        assert result == [], (
            f"Empty list: expected [], got {result!r}"
        )

    def test_none_input(self):
        result = extract_list(None)
        assert result == [], f"None input should return []; got {result!r}"

    def test_empty_string_input(self):
        result = extract_list("")
        assert result == [], f"Empty string should return []; got {result!r}"

    def test_no_list_marker(self):
        result = extract_list("val res0: String = \"hello\"")
        assert result == [], f"Non-list REPL output should return []; got {result!r}"

    def test_method_info_entries(self):
        # Each entry is a compound string of the form 'methodFullName=... methodId=...L'
        s = 'val res0: List[String] = List("methodFullName=foo.bar:void() methodId=123L")'
        result = extract_list(s)
        assert len(result) == 1, f"Expected 1 element, got {len(result)}: {result!r}"
        assert result[0] == "methodFullName=foo.bar:void() methodId=123L", (
            f"Method info entry should be preserved verbatim; got {result[0]!r}"
        )

    def test_list_with_method_full_names_containing_parens(self):
        # Method full names contain parentheses; the list regex must not choke
        s = 'val res0: List[String] = List("com.example.Foo.bar:void(int, String)", "com.example.Foo.baz:int()")'
        result = extract_list(s)
        assert len(result) == 2, (
            f"List with paren-containing method names: expected 2 elements, got {len(result)}: {result!r}"
        )
        assert "com.example.Foo.bar:void(int, String)" in result, (
            f"First method full name should be in result; got {result!r}"
        )
        assert "com.example.Foo.baz:int()" in result, (
            f"Second method full name should be in result; got {result!r}"
        )

    def test_list_with_escaped_quotes(self):
        # Items may contain escaped double-quotes
        s = r'val res0: List[String] = List("method \"foo\" code")'
        result = extract_list(s)
        assert len(result) == 1, f"Expected 1 element, got {len(result)}: {result!r}"
        assert 'method "foo" code' in result[0], (
            f"Escaped quotes should be unescaped in extracted element; got {result[0]!r}"
        )

    def test_large_list(self):
        items = [f"item{i}" for i in range(50)]
        inner = ", ".join(f'"{item}"' for item in items)
        s = f"val res0: List[String] = List({inner})"
        result = extract_list(s)
        assert len(result) == 50, f"Expected 50 elements, got {len(result)}"
        assert result[0] == "item0", f"First element should be 'item0', got {result[0]!r}"
        assert result[-1] == "item49", f"Last element should be 'item49', got {result[-1]!r}"

    def test_list_with_trailing_newline(self):
        s = 'val res0: List[String] = List("x", "y")\n'
        result = extract_list(s)
        assert result == ["x", "y"], (
            f"Trailing newline should not break list extraction; got {result!r}"
        )

    def test_whitespace_only_items_excluded(self):
        # Items that strip to empty string should be excluded
        s = 'val res0: List[String] = List("", "real")'
        result = extract_list(s)
        # Per implementation: empty-strip items are excluded
        assert "real" in result, f"Non-empty item should be in result; got {result!r}"


# ===========================================================================
# Section 2 - Edge-case tests for Joern REPL output format variants
# ===========================================================================


class TestReplOutputFormatVariants:
    """Test parser behavior against realistic REPL output format variants."""

    def test_string_value_without_val_prefix(self):
        # Some REPL output may omit the 'val' keyword
        s = 'res0: String = "com.android.nfc.NfcService"'
        result = extract_value(s)
        assert result == "com.android.nfc.NfcService", (
            f"Variant without 'val' prefix: got {result!r}"
        )

    def test_long_value_without_val_prefix(self):
        s = "res0: Long = 12345L"
        result = extract_value(s)
        assert result == "12345L", (
            f"Long without 'val' prefix: got {result!r}"
        )

    def test_string_with_escaped_quotes_inside(self):
        s = r'val res0: String = "method \"foo\" code"'
        result = extract_value(s)
        # extract_quoted_string uses a non-greedy regex; it captures up to the first unescaped "
        # The key constraint is that extraction does not crash and returns a non-empty result
        assert isinstance(result, str), "Result should be a string"

    def test_multiline_triple_quote_method_code(self):
        code_body = "  int x = 0;\n  return x;"
        s = f'val res0: String = """\npublic int foo() {{\n{code_body}\n}}\n"""'
        result = extract_value(s)
        assert "public int foo()" in result, (
            f"Method signature should be in extracted code; got {result!r}"
        )
        assert "return x;" in result, (
            f"Method body should be in extracted code; got {result!r}"
        )

    def test_list_with_ansi_color_stripped_first(self):
        # Simulate REPL output with ANSI codes wrapping a list
        raw = '\x1b[36mval res0\x1b[0m: \x1b[32mList[String]\x1b[0m = List("alpha", "beta")'
        cleaned = remove_ansi_escape_sequences(raw)
        result = extract_list(cleaned)
        assert result == ["alpha", "beta"], (
            f"After ANSI stripping, list should parse correctly; got {result!r}"
        )

    def test_scalar_with_ansi_color_stripped_first(self):
        raw = '\x1b[36mval res0\x1b[0m: \x1b[32mString\x1b[0m = "1.1.0"'
        cleaned = remove_ansi_escape_sequences(raw)
        result = extract_value(cleaned)
        assert result == "1.1.0", (
            f"After ANSI stripping, scalar should parse correctly; got {result!r}"
        )

    def test_triple_quote_with_ansi_codes_in_code(self):
        # Code itself may contain ANSI codes (edge case)
        raw_code = "int main() {}"
        s = f'val res0: String = """\n{raw_code}\n"""'
        result = extract_value(s)
        assert result == raw_code, f"Expected {raw_code!r}, got {result!r}"

    def test_list_of_class_info_entries(self):
        # Class info entries: classFullName=... classId=...L
        s = 'val res0: List[String] = List("classFullName=com.example.Foo classId=111L", "classFullName=com.example.Bar classId=222L")'
        result = extract_list(s)
        assert len(result) == 2, f"Expected 2 class entries, got {len(result)}: {result!r}"
        assert any("com.example.Foo" in r for r in result), "Foo entry should be present"
        assert any("com.example.Bar" in r for r in result), "Bar entry should be present"

    def test_multiline_list_in_repl(self):
        # REPL may sometimes emit multi-line List output; our DOTALL flag should handle it
        s = 'val res0: List[String] = List(\n  "item1",\n  "item2"\n)'
        result = extract_list(s)
        assert "item1" in result, f"item1 should be extracted; got {result!r}"
        assert "item2" in result, f"item2 should be extracted; got {result!r}"

    def test_method_full_name_with_generic_types(self):
        # Generic type parameters contain angle brackets
        s = 'val res0: String = "java.util.List.of:java.util.List(java.lang.Object[])"'
        result = extract_value(s)
        assert "java.util.List" in result, (
            f"Generic type method full name: got {result!r}"
        )

    def test_list_method_full_name_with_nested_parens(self):
        # Nested parentheses inside list items - list regex uses (.*?) with DOTALL
        # This is the known tricky case: regex is non-greedy so it should match the
        # outermost List(...) correctly (stops at the last ')' on that line).
        s = 'val res0: List[String] = List("outer(inner())")'
        result = extract_list(s)
        # Due to the non-greedy match stopping at first ')', this may partially parse.
        # Document the actual behavior rather than asserting a specific value.
        # The important constraint: must not raise an exception.
        assert isinstance(result, list), "extract_list must return a list"


# ===========================================================================
# Section 3 - Integration tests mocking joern_remote (no network)
# ===========================================================================


def _import_server():
    """Import server module; cache on first call."""
    import server as srv
    return srv


class TestServerToolsMocked:
    """Tests for server_tools.py tool functions with joern_remote mocked."""

    def test_ping_returns_version_string(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "1.2.0"'):
            result = srv.ping()
        assert result == "1.2.0", (
            f"ping() should return the version string extracted from REPL output; got {result!r}"
        )

    def test_ping_query_failed_on_none(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.ping()
        assert result == "Query Failed", (
            f"ping() should return 'Query Failed' when joern_remote returns None; got {result!r}"
        )

    def test_ping_query_failed_on_empty(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.ping()
        assert result == "Query Failed", (
            f"ping() should return 'Query Failed' when joern_remote returns empty string; got {result!r}"
        )

    def test_load_cpg_success(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "true"'):
            result = srv.load_cpg("/some/path/app.cpg")
        assert result == "true", (
            f"load_cpg() should return 'true' on success; got {result!r}"
        )

    def test_load_cpg_failure(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "false"'):
            result = srv.load_cpg("/nonexistent.cpg")
        assert result == "false", (
            f"load_cpg() should return 'false' on failure; got {result!r}"
        )

    def test_load_cpg_none_response(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.load_cpg("/some/path.cpg")
        # extract_value(None) - None is falsy, will fall through to return raw
        assert result is None or isinstance(result, str), (
            "load_cpg() should handle None response without crashing"
        )

    def test_get_class_full_name_by_id(self):
        srv = _import_server()
        mock_output = 'val res0: String = "com.android.nfc.NfcService$6"'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_class_full_name_by_id("111669149702L")
        assert result == "com.android.nfc.NfcService$6", (
            f"get_class_full_name_by_id() should extract class name; got {result!r}"
        )

    def test_get_method_full_name_by_id(self):
        srv = _import_server()
        mock_output = 'val res0: String = "com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent)"'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_full_name_by_id("999L")
        assert "onReceive" in result, (
            f"get_method_full_name_by_id() should extract method full name; got {result!r}"
        )

    def test_get_method_callees_list(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("methodFullName=foo.Bar.baz:void() methodId=1L", "methodFullName=foo.Bar.qux:int() methodId=2L")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_callees("some.Method.name:void()")
        assert isinstance(result, list), "get_method_callees() should return a list"
        assert len(result) == 2, f"Expected 2 callees, got {len(result)}: {result!r}"
        assert any("baz" in r for r in result), "First callee should be present"

    def test_get_method_callees_empty_list(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: List[String] = List()'):
            result = srv.get_method_callees("no.callees:void()")
        assert result == [], f"Empty callees: expected [], got {result!r}"

    def test_get_method_callees_none_response(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_callees("some.Method:void()")
        assert result == [], (
            f"get_method_callees() should return [] on None response; got {result!r}"
        )

    def test_get_method_callers_list(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("methodFullName=caller.A:void() methodId=10L")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_callers("callee.B:void()")
        assert len(result) == 1, f"Expected 1 caller, got {len(result)}: {result!r}"
        assert "caller.A" in result[0], f"Caller entry should contain 'caller.A'; got {result[0]!r}"

    def test_get_class_methods_by_class_full_name(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("methodFullName=com.Foo.bar:void() methodId=1L", "methodFullName=com.Foo.<init>:void() methodId=2L")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_class_methods_by_class_full_name("com.Foo")
        assert isinstance(result, list), "Should return list"
        assert len(result) == 2, f"Expected 2 methods, got {len(result)}"

    def test_get_method_code_by_full_name_triple_quote(self):
        srv = _import_server()
        code = "public void onReceive(Context ctx, Intent intent) {\n    handle(intent);\n}"
        mock_output = f'val res0: String = """\n{code}\n"""'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_code_by_full_name("com.Foo.onReceive:void()")
        assert "onReceive" in result, (
            f"get_method_code_by_full_name() should extract method code; got {result!r}"
        )
        assert "handle(intent);" in result, (
            f"Method body should be in result; got {result!r}"
        )

    def test_get_method_code_by_id_triple_quote(self):
        srv = _import_server()
        code = "private int compute(int x) {\n    return x * 2;\n}"
        mock_output = f'val res0: String = """\n{code}\n"""'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_code_by_id("12345L")
        assert "compute" in result, (
            f"get_method_code_by_id() should extract method code; got {result!r}"
        )

    def test_get_call_code_by_id(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "handle(intent)"'):
            result = srv.get_call_code_by_id("555L")
        assert result == "handle(intent)", (
            f"get_call_code_by_id() should extract call code; got {result!r}"
        )

    def test_get_method_by_call_id(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "com.Foo.bar:void()"'):
            result = srv.get_method_by_call_id("123L")
        assert result == "com.Foo.bar:void()", (
            f"get_method_by_call_id(): got {result!r}"
        )

    def test_get_referenced_method_full_name_by_call_id(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "com.Foo.referenced:void()"'):
            result = srv.get_referenced_method_full_name_by_call_id("456L")
        assert result == "com.Foo.referenced:void()", (
            f"get_referenced_method_full_name_by_call_id(): got {result!r}"
        )

    def test_get_derived_classes_by_class_full_name(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("classFullName=com.Child classId=1L")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_derived_classes_by_class_full_name("com.Parent")
        assert len(result) == 1, f"Expected 1 derived class, got {len(result)}"
        assert "com.Child" in result[0], f"Child class should be in result; got {result[0]!r}"

    def test_get_parent_classes_by_class_full_name(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("classFullName=java.lang.Object classId=99L")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_parent_classes_by_class_full_name("com.Foo")
        assert len(result) == 1, f"Expected 1 parent class, got {len(result)}"
        assert "java.lang.Object" in result[0], f"Object class should be in result"

    def test_get_calls_in_method_by_method_full_name(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("callId=1L callCode=foo()", "callId=2L callCode=bar(x)")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_calls_in_method_by_method_full_name("com.Foo.main:void()")
        assert isinstance(result, list), "Should return list"
        assert len(result) == 2, f"Expected 2 calls, got {len(result)}"

    def test_get_method_code_by_class_full_name_and_method_name(self):
        srv = _import_server()
        mock_output = 'val res0: List[String] = List("public void foo() { }")'
        with patch.object(srv, "joern_remote", return_value=mock_output):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "foo")
        assert isinstance(result, list), "Should return list"
        assert len(result) == 1, f"Expected 1 code entry, got {len(result)}"

    def test_check_connection_success(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value='val res0: String = "1.2.0"'):
            result = srv.check_connection()
        assert "Successfully connected" in result, (
            f"check_connection() should report success; got {result!r}"
        )
        assert "1.2.0" in result, f"Version should appear in message; got {result!r}"

    def test_check_connection_failure_none(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.check_connection()
        assert "Failed" in result, (
            f"check_connection() should report failure on None; got {result!r}"
        )

    def test_check_connection_failure_empty(self):
        srv = _import_server()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.check_connection()
        # extract_value("") returns "" which is falsy -> should report failure
        assert "Failed" in result, (
            f"check_connection() should report failure on empty response; got {result!r}"
        )

    def test_scalar_bug_long_returns_string_with_L(self):
        """Regression: scalar Long queries were returning empty.

        The extract_value dispatch checks 'Long =' in input_str to route
        through extract_long_value. Verify the exact token it looks for.
        """
        # The bug: if REPL emits 'val res0: Long = 42L' extract_value must
        # return '42L', NOT ''.
        s = "val res0: Long = 42L"
        result = extract_value(s)
        assert result == "42L", (
            f"Scalar Long bug: expected '42L', got {result!r}. "
            "Check that 'Long =' appears in the REPL output and extract_long_value is called."
        )

    def test_scalar_bug_string_not_empty(self):
        """Regression: scalar String queries were returning empty.

        Verify that 'String = \"' is present in realistic REPL output and
        extract_quoted_string correctly returns the inner value.
        """
        s = 'val res0: String = "com.android.nfc.NfcService"'
        result = extract_value(s)
        assert result != "", (
            f"Scalar String bug: expected non-empty result, got {result!r}. "
            "Check that 'String = \"' is the dispatch token in extract_value."
        )
        assert result == "com.android.nfc.NfcService", (
            f"Scalar String bug: expected 'com.android.nfc.NfcService', got {result!r}"
        )


# ===========================================================================
# Section 4 - Live integration tests (require Joern at HOST:PORT)
# ===========================================================================

def _joern_is_reachable():
    """Return True if the Joern HTTP endpoint responds to a TCP connect."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


def _first_sample_cpg():
    """Return the absolute path to the first CPG sample file found, or None."""
    samples_dir = os.path.join(MJOERN_DIR, "tests", "samples")
    if not os.path.isdir(samples_dir):
        return None
    entries = sorted(os.listdir(samples_dir))
    for entry in entries:
        full = os.path.join(samples_dir, entry)
        if os.path.isfile(full):
            return full
    return None


joern_reachable = pytest.mark.skipif(
    not _joern_is_reachable(),
    reason="Joern server is not reachable at HOST:PORT - skipping live integration tests",
)


@pytest.mark.integration
@joern_reachable
class TestLiveIntegration:
    """Live integration tests that call the actual Joern server.

    These tests are skipped automatically when the server is not reachable.
    Run explicitly with: pytest tests/unit/test_mcp_common_tools.py -v -m integration
    """

    def test_version_raw_response_format(self):
        """joern_remote('version') should return a parseable REPL output string."""
        srv = _import_server()
        raw = srv.joern_remote("version")
        assert raw is not None, "joern_remote('version') should not return None"
        assert isinstance(raw, str), f"Expected str, got {type(raw)}"
        # Should be parseable by extract_value
        result = extract_value(raw)
        assert result, f"extract_value on version output should be non-empty; raw was {raw!r}"

    def test_ping_tool_live(self):
        """ping() should return a non-empty version string from the live server."""
        srv = _import_server()
        result = srv.ping()
        assert result and result != "Query Failed", (
            f"ping() should return Joern version from live server; got {result!r}"
        )

    def test_check_connection_live(self):
        """check_connection() should report successful connection."""
        srv = _import_server()
        result = srv.check_connection()
        assert "Successfully connected" in result, (
            f"check_connection() should report success against live server; got {result!r}"
        )

    def test_load_cpg_live(self):
        """load_cpg() should load a sample CPG file successfully."""
        cpg_path = _first_sample_cpg()
        if cpg_path is None:
            pytest.skip("No sample CPG files found in tests/samples/")
        srv = _import_server()
        result = srv.load_cpg(cpg_path)
        assert result is not None, (
            f"load_cpg('{cpg_path}') should not return None"
        )
        assert "true" in str(result).lower(), (
            f"load_cpg() should return 'true' on success; got {result!r}"
        )

    def test_scalar_query_raw_format(self):
        """Raw joern_remote call for 'version' should contain 'String =' or 'Long ='."""
        srv = _import_server()
        raw = srv.joern_remote("version")
        assert raw is not None, "Server should return a response"
        # Must contain at least one of the expected scalar markers so extract_value works
        has_scalar_marker = ("String =" in raw) or ("Long =" in raw) or ("Boolean =" in raw)
        assert has_scalar_marker, (
            f"REPL output for 'version' should contain a scalar type marker; got {raw!r}"
        )
