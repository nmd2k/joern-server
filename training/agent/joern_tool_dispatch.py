"""Map MCP tool names to their underlying CPGQL query strings.

Provides two public functions:

    build_joern_query(tool_name, args) -> str
        Returns the CPGQL string that the named tool would send to Joern.
        Raises KeyError for unknown tools, ValueError (with "missing") for
        missing required arguments.

    known_tool_names() -> frozenset[str]
        Returns the complete set of recognised tool names.
"""

from __future__ import annotations

from typing import Any


def _esc(s: str) -> str:
    """Escape double-quotes and backslashes inside a CPGQL string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _require(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if val is None:
        raise ValueError(f"missing required argument: '{key}'")
    return str(val)


# ---------------------------------------------------------------------------
# Dispatch table: tool_name -> callable(args) -> cpgql_string
# ---------------------------------------------------------------------------

def _ping(args: dict[str, Any]) -> str:
    return "version"


def _check_connection(args: dict[str, Any]) -> str:
    return "version"


def _get_help(args: dict[str, Any]) -> str:
    return "help"


def _load_cpg(args: dict[str, Any]) -> str:
    path = _require(args, "cpg_filepath")
    return f'importCpg("{_esc(path)}")'


def _get_method_callees(args: dict[str, Any]) -> str:
    name = _require(args, "method_full_name")
    return f'get_method_callees("{_esc(name)}")'


def _get_method_callers(args: dict[str, Any]) -> str:
    name = _require(args, "method_full_name")
    return f'get_method_callers("{_esc(name)}")'


def _get_method_code_by_full_name(args: dict[str, Any]) -> str:
    name = _require(args, "method_full_name")
    return f'get_method_code_by_method_full_name("{_esc(name)}")'


def _get_calls_in_method_by_method_full_name(args: dict[str, Any]) -> str:
    name = _require(args, "method_full_name")
    return f'get_calls_in_method_by_method_full_name("{_esc(name)}")'


def _get_method_full_name_by_id(args: dict[str, Any]) -> str:
    mid = _require(args, "method_id")
    return f'get_method_full_name_by_id("{_esc(mid)}")'


def _get_method_code_by_id(args: dict[str, Any]) -> str:
    mid = _require(args, "method_id")
    return f'get_method_code_by_id("{_esc(mid)}")'


def _get_call_code_by_id(args: dict[str, Any]) -> str:
    cid = _require(args, "code_id")
    return f'get_call_code_by_id("{_esc(cid)}")'


def _get_method_by_call_id(args: dict[str, Any]) -> str:
    cid = _require(args, "call_id")
    return f'get_method_by_call_id("{_esc(cid)}")'


def _get_referenced_method_full_name_by_call_id(args: dict[str, Any]) -> str:
    cid = _require(args, "call_id")
    return f'get_referenced_method_full_name_by_call_id("{_esc(cid)}")'


def _get_class_full_name_by_id(args: dict[str, Any]) -> str:
    cid = _require(args, "class_id")
    return f'get_class_full_name_by_id("{_esc(cid)}")'


def _get_class_methods_by_class_full_name(args: dict[str, Any]) -> str:
    name = _require(args, "class_full_name")
    return f'get_class_methods_by_class_full_name("{_esc(name)}")'


def _get_method_code_by_class_full_name_and_method_name(args: dict[str, Any]) -> str:
    cls = _require(args, "class_full_name")
    method = _require(args, "method_name")
    return f'get_method_code_by_class_full_name_and_method_name("{_esc(cls)}", "{_esc(method)}")'


def _get_derived_classes_by_class_full_name(args: dict[str, Any]) -> str:
    name = _require(args, "class_full_name")
    return f'get_derived_classes_by_class_full_name("{_esc(name)}")'


def _get_parent_classes_by_class_full_name(args: dict[str, Any]) -> str:
    name = _require(args, "class_full_name")
    return f'get_parent_classes_by_class_full_name("{_esc(name)}")'


def _cpgql_query(args: dict[str, Any]) -> str:
    return _require(args, "query")


_DISPATCH: dict[str, Any] = {
    "ping":                                             _ping,
    "check_connection":                                 _check_connection,
    "get_help":                                         _get_help,
    "load_cpg":                                         _load_cpg,
    "get_method_callees":                               _get_method_callees,
    "get_method_callers":                               _get_method_callers,
    "get_method_code_by_full_name":                     _get_method_code_by_full_name,
    "get_calls_in_method_by_method_full_name":          _get_calls_in_method_by_method_full_name,
    "get_method_full_name_by_id":                       _get_method_full_name_by_id,
    "get_method_code_by_id":                            _get_method_code_by_id,
    "get_call_code_by_id":                              _get_call_code_by_id,
    "get_method_by_call_id":                            _get_method_by_call_id,
    "get_referenced_method_full_name_by_call_id":       _get_referenced_method_full_name_by_call_id,
    "get_class_full_name_by_id":                        _get_class_full_name_by_id,
    "get_class_methods_by_class_full_name":             _get_class_methods_by_class_full_name,
    "get_method_code_by_class_full_name_and_method_name": _get_method_code_by_class_full_name_and_method_name,
    "get_derived_classes_by_class_full_name":           _get_derived_classes_by_class_full_name,
    "get_parent_classes_by_class_full_name":            _get_parent_classes_by_class_full_name,
    "cpgql_query":                                      _cpgql_query,
}


def build_joern_query(tool_name: str, args: dict[str, Any]) -> str:
    """Return the CPGQL string for the given MCP tool name and arguments.

    Raises:
        KeyError: tool_name is not recognised.
        ValueError: a required argument is absent (message contains "missing").
    """
    handler = _DISPATCH[tool_name]  # KeyError on unknown tool
    return handler(args)


def known_tool_names() -> frozenset[str]:
    """Return the set of all recognised tool names."""
    return frozenset(_DISPATCH)
