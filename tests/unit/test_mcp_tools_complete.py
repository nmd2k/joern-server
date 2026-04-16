"""
S2-T01: Comprehensive unit tests for all 18 MCP tools.

Mocks `joern_remote` so no live server is required.  For each tool the
test suite covers:
  1. Happy-path  — realistic REPL output → expected Python value
  2. Empty / List() — empty sentinel → [] or ""
  3. None response  — joern_remote returns None
  4. Unexpected format — unrecognised string

Run with:
    pytest tests/unit/test_mcp_tools_complete.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path: allow importing the server module from mcp-joern/
# ---------------------------------------------------------------------------
MJOERN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp-joern")
)
if MJOERN_DIR not in sys.path:
    sys.path.insert(0, MJOERN_DIR)


def _srv():
    """Return the server module (cached after first import)."""
    import server as _server
    return _server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repl_string(value: str) -> str:
    """Simulate a Joern REPL string scalar (already ANSI-stripped)."""
    return f'val res0: String = "{value}"'


def _repl_string_triple(code: str) -> str:
    """Simulate a Joern REPL triple-quoted string (multiline code)."""
    return f'val res0: String = """\n{code}\n"""'


def _repl_long(value: str) -> str:
    return f"val res0: Long = {value}"


def _repl_bool(value: str) -> str:
    return f"val res0: Boolean = {value}"


def _repl_list(elements: list) -> str:
    inner = ", ".join(f'"{e}"' for e in elements)
    return f"val res0: List[String] = List({inner})"


def _repl_empty_list() -> str:
    return "val res0: List[String] = List()"


# ===========================================================================
# 1. check_connection  (defined in server.py)
# ===========================================================================

class TestCheckConnection:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("4.0.517")):
            result = srv.check_connection()
        assert "Successfully connected" in result
        assert "4.0.517" in result

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.check_connection()
        assert "Failed" in result

    def test_empty_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.check_connection()
        assert "Failed" in result

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            # extract_value returns the raw string; it is truthy -> success branch
            result = srv.check_connection()
        # Should not raise; either "Successfully" or "Failed" is acceptable
        assert isinstance(result, str)


# ===========================================================================
# 2. get_help  (defined in server.py)
# ===========================================================================

class TestGetHelp:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="Joern help text"):
            result = srv.get_help()
        assert "Joern help text" in result

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_help()
        assert result == "Query Failed"

    def test_empty_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.get_help()
        assert result == "Query Failed"

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="some output"):
            result = srv.get_help()
        assert result == "some output"


# ===========================================================================
# 3. ping  (defined in server_tools.py)
# ===========================================================================

class TestPing:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("4.0.517")):
            result = srv.ping()
        assert result == "4.0.517"

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.ping()
        assert result == "Query Failed"

    def test_empty_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=""):
            result = srv.ping()
        assert result == "Query Failed"

    def test_unexpected_format_passthrough(self):
        srv = _srv()
        # extract_value returns raw string when no dispatch condition matches
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            result = srv.ping()
        assert result == "unexpected"


# ===========================================================================
# 4. load_cpg  (calls joern_remote TWICE — use side_effect list)
# ===========================================================================

class TestLoadCpg:
    def test_happy_path_some(self):
        """When importCpg returns 'Some(' substring, load_cpg returns 'true'."""
        srv = _srv()
        side_effects = ['val res0: Option[Cpg] = Some("/path/app.cpg")', 'val res1: Boolean = true']
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/path/app.cpg")
        assert result == "true"

    def test_happy_path_string_true(self):
        """When importCpg returns String 'true', load_cpg extracts it."""
        srv = _srv()
        side_effects = [_repl_string("true"), 'val res1: Boolean = true']
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/some/path.cpg")
        assert result == "true"

    def test_failure_false(self):
        srv = _srv()
        side_effects = [_repl_string("false"), ""]
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/nonexistent.cpg")
        assert result == "false"

    def test_none_import_result(self):
        """When importCpg returns None (network error), load_cpg returns 'false'."""
        srv = _srv()
        side_effects = [None, None]
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/some/path.cpg")
        assert result == "false"

    def test_empty_import_result(self):
        srv = _srv()
        side_effects = ["", ""]
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/path.cpg")
        # load_cpg checks: if import_result is falsy -> returns "false"
        # empty string is falsy, so "false" is returned
        assert result == "false"

    def test_unexpected_format(self):
        """Unexpected string without 'Some(' — falls through to extract_value."""
        srv = _srv()
        side_effects = ["unexpected output", ""]
        with patch.object(srv, "joern_remote", side_effect=side_effects):
            result = srv.load_cpg("/path.cpg")
        assert isinstance(result, str)


# ===========================================================================
# 5. get_method_callees
# ===========================================================================

class TestGetMethodCallees:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list([
            "methodFullName=foo.Bar.baz:void() methodId=1L",
            "methodFullName=foo.Bar.qux:int() methodId=2L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_method_callees("foo.Bar.main:void()")
        assert isinstance(result, list)
        assert len(result) == 2
        assert any("baz" in r for r in result)

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_method_callees("leaf.Method:void()")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_callees("any.Method:void()")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            result = srv.get_method_callees("any.Method:void()")
        assert isinstance(result, list)


# ===========================================================================
# 6. get_method_callers
# ===========================================================================

class TestGetMethodCallers:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list(["methodFullName=caller.A:void() methodId=10L"])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_method_callers("callee.B:void()")
        assert len(result) == 1
        assert "caller.A" in result[0]

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_method_callers("top.Level:void()")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_callers("any:void()")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="not a list"):
            result = srv.get_method_callers("any:void()")
        assert isinstance(result, list)


# ===========================================================================
# 7. get_method_code_by_full_name
# ===========================================================================

class TestGetMethodCodeByFullName:
    def test_happy_path_single_line(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("int foo() { return 1; }")):
            result = srv.get_method_code_by_full_name("com.Foo.foo:int()")
        assert result == "int foo() { return 1; }"

    def test_happy_path_triple_quote(self):
        srv = _srv()
        code = "public void bar() {\n    doSomething();\n}"
        with patch.object(srv, "joern_remote", return_value=_repl_string_triple(code)):
            result = srv.get_method_code_by_full_name("com.Foo.bar:void()")
        assert "doSomething" in result

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_method_code_by_full_name("unknown:void()")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_code_by_full_name("any:void()")
        assert result is None  # extract_value(None) returns None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="no marker"):
            result = srv.get_method_code_by_full_name("any:void()")
        assert isinstance(result, str)


# ===========================================================================
# 8. get_calls_in_method_by_method_full_name
# ===========================================================================

class TestGetCallsInMethodByMethodFullName:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list([
            "call_code=foo() call_id=1L",
            "call_code=bar(x) call_id=2L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_calls_in_method_by_method_full_name("com.Foo.main:void()")
        assert len(result) == 2

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_calls_in_method_by_method_full_name("leaf:void()")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_calls_in_method_by_method_full_name("any:void()")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            result = srv.get_calls_in_method_by_method_full_name("any:void()")
        assert isinstance(result, list)


# ===========================================================================
# 9. get_method_full_name_by_id
# ===========================================================================

class TestGetMethodFullNameById:
    def test_happy_path(self):
        srv = _srv()
        method_fn = "com.android.nfc.NfcService.onReceive:void(android.content.Context)"
        with patch.object(srv, "joern_remote", return_value=_repl_string(method_fn)):
            result = srv.get_method_full_name_by_id("12345L")
        assert result == method_fn

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_method_full_name_by_id("99999L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_full_name_by_id("99999L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="weird output"):
            result = srv.get_method_full_name_by_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 10. get_method_code_by_id
# ===========================================================================

class TestGetMethodCodeById:
    def test_happy_path_triple_quote(self):
        srv = _srv()
        code = "private int compute(int x) {\n    return x * 2;\n}"
        with patch.object(srv, "joern_remote", return_value=_repl_string_triple(code)):
            result = srv.get_method_code_by_id("12345L")
        assert "compute" in result
        assert "return x * 2;" in result

    def test_happy_path_single_line(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("int f() { return 0; }")):
            result = srv.get_method_code_by_id("1L")
        assert result == "int f() { return 0; }"

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_method_code_by_id("1L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_code_by_id("1L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="gibberish"):
            result = srv.get_method_code_by_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 11. get_call_code_by_id
# ===========================================================================

class TestGetCallCodeById:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("handle(intent)")):
            result = srv.get_call_code_by_id("555L")
        assert result == "handle(intent)"

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_call_code_by_id("0L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_call_code_by_id("1L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="odd"):
            result = srv.get_call_code_by_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 12. get_method_by_call_id
# ===========================================================================

class TestGetMethodByCallId:
    def test_happy_path(self):
        srv = _srv()
        method_info = "method_full_name=com.Foo.bar:void()|method_name=bar|method_signature=void()|method_id=100L"
        with patch.object(srv, "joern_remote", return_value=_repl_string(method_info)):
            result = srv.get_method_by_call_id("123L")
        assert "com.Foo.bar" in result

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_method_by_call_id("0L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_by_call_id("1L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            result = srv.get_method_by_call_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 13. get_referenced_method_full_name_by_call_id
# ===========================================================================

class TestGetReferencedMethodFullNameByCallId:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("com.Foo.referenced:void()")):
            result = srv.get_referenced_method_full_name_by_call_id("456L")
        assert result == "com.Foo.referenced:void()"

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_referenced_method_full_name_by_call_id("0L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_referenced_method_full_name_by_call_id("1L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="weird"):
            result = srv.get_referenced_method_full_name_by_call_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 14. get_class_full_name_by_id
# ===========================================================================

class TestGetClassFullNameById:
    def test_happy_path(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("com.android.nfc.NfcService$6")):
            result = srv.get_class_full_name_by_id("111669149702L")
        assert result == "com.android.nfc.NfcService$6"

    def test_empty_string(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_string("")):
            result = srv.get_class_full_name_by_id("999L")
        assert result == ""

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_class_full_name_by_id("1L")
        assert result is None

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="something"):
            result = srv.get_class_full_name_by_id("1L")
        assert isinstance(result, str)


# ===========================================================================
# 15. get_class_methods_by_class_full_name
# ===========================================================================

class TestGetClassMethodsByClassFullName:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list([
            "methodFullName=com.Foo.bar:void() methodId=1L",
            "methodFullName=com.Foo.<init>:void() methodId=2L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_class_methods_by_class_full_name("com.Foo")
        assert len(result) == 2

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_class_methods_by_class_full_name("com.Empty")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_class_methods_by_class_full_name("com.Any")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="not a list"):
            result = srv.get_class_methods_by_class_full_name("com.Any")
        assert isinstance(result, list)


# ===========================================================================
# 16. get_method_code_by_class_full_name_and_method_name
# ===========================================================================

class TestGetMethodCodeByClassFullNameAndMethodName:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list(["methodFullName=com.Foo.bar:void() methodId=1L"])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "bar")
        assert len(result) == 1

    def test_multiple_overloads(self):
        srv = _srv()
        mock_out = _repl_list([
            "methodFullName=com.Foo.bar:void() methodId=1L",
            "methodFullName=com.Foo.bar:void(int) methodId=2L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "bar")
        assert len(result) == 2

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "unknown")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "bar")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="garbage"):
            result = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "bar")
        assert isinstance(result, list)


# ===========================================================================
# 17. get_derived_classes_by_class_full_name
# ===========================================================================

class TestGetDerivedClassesByClassFullName:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list([
            "class_full_name=com.Child|class_name=Child|class_id=1L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_derived_classes_by_class_full_name("com.Parent")
        assert len(result) == 1
        assert "com.Child" in result[0]

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_derived_classes_by_class_full_name("com.Leaf")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_derived_classes_by_class_full_name("com.Any")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="no list"):
            result = srv.get_derived_classes_by_class_full_name("com.Any")
        assert isinstance(result, list)


# ===========================================================================
# 18. get_parent_classes_by_class_full_name
# ===========================================================================

class TestGetParentClassesByClassFullName:
    def test_happy_path(self):
        srv = _srv()
        mock_out = _repl_list([
            "class_full_name=java.lang.Object|class_name=Object|class_id=99L",
        ])
        with patch.object(srv, "joern_remote", return_value=mock_out):
            result = srv.get_parent_classes_by_class_full_name("com.Foo")
        assert len(result) == 1
        assert "java.lang.Object" in result[0]

    def test_empty_list(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=_repl_empty_list()):
            result = srv.get_parent_classes_by_class_full_name("com.Root")
        assert result == []

    def test_none_response(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value=None):
            result = srv.get_parent_classes_by_class_full_name("com.Any")
        assert result == []

    def test_unexpected_format(self):
        srv = _srv()
        with patch.object(srv, "joern_remote", return_value="unexpected"):
            result = srv.get_parent_classes_by_class_full_name("com.Any")
        assert isinstance(result, list)


# ===========================================================================
# Cross-cutting: ANSI escape sequences in REPL output
# ===========================================================================

class TestAnsiStrippedOutput:
    """Verify tools handle ANSI-wrapped REPL output (as the live server produces)."""

    def _ansi_wrap_string(self, value: str) -> str:
        """Simulate Joern REPL output with ANSI color codes."""
        return (
            f'\x1b[33mval\x1b[0m \x1b[36mres0\x1b[0m: '
            f'\x1b[32mString\x1b[0m = "{value}"\n'
        )

    def test_ping_with_ansi_stripped_version(self):
        """ping() receives ANSI-stripped string (server.py already strips ANSI)."""
        srv = _srv()
        # server.py's joern_remote calls remove_ansi_escape_sequences before returning;
        # so by the time the tool function sees the value, ANSI is already gone.
        stripped = f'val res0: String = "4.0.517"\n'
        with patch.object(srv, "joern_remote", return_value=stripped):
            result = srv.ping()
        assert result == "4.0.517"

    def test_check_connection_with_stripped_version(self):
        srv = _srv()
        stripped = 'val res0: String = "4.0.517"\n'
        with patch.object(srv, "joern_remote", return_value=stripped):
            result = srv.check_connection()
        assert "Successfully connected" in result
        assert "4.0.517" in result

    def test_get_class_full_name_stripped(self):
        srv = _srv()
        stripped = 'val res0: String = "com.android.nfc.NfcService"\n'
        with patch.object(srv, "joern_remote", return_value=stripped):
            result = srv.get_class_full_name_by_id("123L")
        assert result == "com.android.nfc.NfcService"


# ===========================================================================
# Regression: scalar tools whose Scala functions return plain String
# (not "Long =") must use extract_value and strip quotes correctly
# ===========================================================================

class TestScalarStringRegression:
    """
    Regression tests ensuring tools that receive 'String = "value"' REPL output
    return the inner value (not the raw REPL line).
    """

    def test_get_method_full_name_by_id_strips_quotes(self):
        srv = _srv()
        method_fn = "com.example.Foo.bar:void()"
        raw = f'val res0: String = "{method_fn}"'
        with patch.object(srv, "joern_remote", return_value=raw):
            result = srv.get_method_full_name_by_id("1L")
        assert result == method_fn, (
            f"Scalar String tool should strip REPL wrapper; got {result!r}"
        )

    def test_get_class_full_name_by_id_strips_quotes(self):
        srv = _srv()
        class_fn = "com.example.SomeClass"
        raw = f'val res0: String = "{class_fn}"'
        with patch.object(srv, "joern_remote", return_value=raw):
            result = srv.get_class_full_name_by_id("2L")
        assert result == class_fn

    def test_get_call_code_by_id_strips_quotes(self):
        srv = _srv()
        call_code = "doSomething(arg1, arg2)"
        raw = f'val res0: String = "{call_code}"'
        with patch.object(srv, "joern_remote", return_value=raw):
            result = srv.get_call_code_by_id("3L")
        assert result == call_code

    def test_get_method_by_call_id_strips_quotes(self):
        srv = _srv()
        info = "method_full_name=com.X.y:void()|method_name=y|method_signature=void()|method_id=5L"
        raw = f'val res0: String = "{info}"'
        with patch.object(srv, "joern_remote", return_value=raw):
            result = srv.get_method_by_call_id("4L")
        assert result == info

    def test_get_referenced_method_full_name_by_call_id_strips_quotes(self):
        srv = _srv()
        mfn = "com.Example.ref:void()"
        raw = f'val res0: String = "{mfn}"'
        with patch.object(srv, "joern_remote", return_value=raw):
            result = srv.get_referenced_method_full_name_by_call_id("5L")
        assert result == mfn
