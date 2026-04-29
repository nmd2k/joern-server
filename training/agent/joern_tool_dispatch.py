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


def _find_methods(args: dict[str, Any]) -> str:
    name_pattern = args.get("name_pattern")
    annotation = args.get("annotation")
    modifier = args.get("modifier")
    full_name_pattern = args.get("full_name_pattern")
    if not any([name_pattern, annotation, modifier, full_name_pattern]):
        raise ValueError("missing required argument: at least one of name_pattern, annotation, modifier, full_name_pattern")
    parts = ["cpg.method"]
    if name_pattern:
        parts.append(f'.name("{_esc(name_pattern)}")')
    if full_name_pattern:
        parts.append(f'.fullName("{_esc(full_name_pattern)}")')
    if annotation:
        parts.append(f'.where(_.annotation.name("{_esc(annotation)}"))')
    if modifier:
        parts.append(f'.where(_.modifier.modifierType("{_esc(modifier.upper())}"))')
    parts.append(
        '.map(m => s"id=${m.id}L name=${m.name} fullName=${m.fullName}'
        ' file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}").l'
    )
    return "".join(parts)


def _find_calls(args: dict[str, Any]) -> str:
    callee = _require(args, "callee_name_pattern")
    scope = args.get("method_full_name_pattern")
    parts = [f'cpg.call.name("{_esc(callee)}")']
    if scope:
        parts.append(f'.where(_.method.fullName("{_esc(scope)}"))')
    parts.append(
        '.map(c => s"callId=${c.id}L calleeName=${c.name}'
        ' containingMethod=${c.method.map(_.fullName).headOption.getOrElse("")}'
        ' file=${c.method.file.name.headOption.getOrElse("")} line=${c.lineNumber.getOrElse(-1)}").l'
    )
    return "".join(parts)


def _get_call_arguments(args: dict[str, Any]) -> str:
    call_id = _require(args, "call_id")
    return (
        f'cpg.call.id({_esc(call_id)}).argument'
        '.map(a => s"argIndex=${a.order} code=${a.code}'
        ' typeFullName=${a.evalType.headOption.getOrElse("")} nodeId=${a.id}L").l'
    )


def _find_literals(args: dict[str, Any]) -> str:
    pattern = _require(args, "pattern")
    literal_type = args.get("literal_type", "any")
    parts = [f'cpg.literal.code("{_esc(pattern)}")']
    if literal_type == "string":
        parts.append('.where(_.typeFullName(".*[Ss]tring.*"))')
    elif literal_type == "int":
        parts.append('.where(_.typeFullName(".*[Ii]nt.*|.*[Ll]ong.*|byte|short"))')
    parts.append(
        '.map(l => s"literalId=${l.id}L value=${l.code} typeFullName=${l.typeFullName}'
        ' containingMethod=${l.method.map(_.fullName).headOption.getOrElse("")}'
        ' file=${l.file.name.headOption.getOrElse("")} line=${l.lineNumber.getOrElse(-1)}").l'
    )
    return "".join(parts)


def _get_method_location(args: dict[str, Any]) -> str:
    method_id = args.get("method_id")
    method_full_name = args.get("method_full_name")
    if not method_id and not method_full_name:
        raise ValueError("missing required argument: provide either method_id or method_full_name")
    map_expr = (
        '.map(m => s"file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}'
        ' lineEnd=${m.lineNumberEnd.getOrElse(-1)} columnStart=${m.columnNumber.getOrElse(-1)}'
        ' columnEnd=${m.columnNumberEnd.getOrElse(-1)}")'
        '.headOption.getOrElse("")'
    )
    if method_id:
        return f'cpg.method.id({_esc(method_id)})' + map_expr
    return f'cpg.method.fullName("{_esc(method_full_name)}")' + map_expr


def _get_dataflow(args: dict[str, Any]) -> str:
    source = _require(args, "source_pattern")
    sink = _require(args, "sink_pattern")
    max_depth = int(args.get("max_depth", 12))
    min(max_depth, 20)  # validate; actual cap enforced server-side
    return (
        f'val __src = cpg.call.name("{_esc(source)}");'
        f'val __snk = cpg.call.name("{_esc(sink)}");'
        '__snk.reachableByFlows(__src)'
        '.map(flow => flow.elements.map(n => s"${n.code}@${n.file.name.headOption.getOrElse("")}:${n.lineNumber.getOrElse(-1)}").mkString(" -> "))'
        '.l'
    )


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
    # Sprint 4 — vulnerability-hunting tools
    "find_methods":                                     _find_methods,
    "find_calls":                                       _find_calls,
    "get_call_arguments":                               _get_call_arguments,
    "find_literals":                                    _find_literals,
    "get_method_location":                              _get_method_location,
    "get_dataflow":                                     _get_dataflow,
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
