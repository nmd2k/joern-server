"""Unit tests for training.agent.joern_tool_dispatch."""

import pytest

from training.agent.joern_tool_dispatch import build_joern_query, known_tool_names


def test_ping_query() -> None:
    assert build_joern_query("ping", {}) == "version"


def test_check_connection_query() -> None:
    assert build_joern_query("check_connection", {}) == "version"


def test_get_method_callers_escapes_quotes() -> None:
    q = build_joern_query(
        "get_method_callers",
        {"method_full_name": 'foo"bar'},
    )
    assert 'foo\\"bar' in q


def test_unknown_tool() -> None:
    with pytest.raises(KeyError):
        build_joern_query("not_a_real_tool", {})


def test_missing_required_arg() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("load_cpg", {})


def test_known_tools_include_bundle_names() -> None:
    names = known_tool_names()
    for required in (
        "ping",
        "load_cpg",
        "get_method_callers",
        "get_help",
        "get_method_code_by_full_name",
        "cpgql_query",
    ):
        assert required in names


def test_cpgql_query_passes_through() -> None:
    q = 'cpg.method.name("foo").l'
    assert build_joern_query("cpgql_query", {"query": q}) == q


# Sprint 4 dispatch tests

def test_find_methods_name_pattern() -> None:
    q = build_joern_query("find_methods", {"name_pattern": "get.*"})
    assert 'cpg.method.name("get.*")' in q
    assert "m.filename" in q


def test_find_methods_requires_at_least_one_filter() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("find_methods", {})


def test_find_methods_annotation_filter() -> None:
    q = build_joern_query("find_methods", {"annotation": "Override"})
    assert '.where(_.annotation.name("Override"))' in q


def test_find_methods_modifier_uppercased() -> None:
    q = build_joern_query("find_methods", {"modifier": "public"})
    assert '.modifierType("PUBLIC")' in q


def test_find_calls_required_arg() -> None:
    q = build_joern_query("find_calls", {"callee_name_pattern": "exec"})
    assert 'cpg.call.name("exec")' in q
    assert "c.method.file.name" in q


def test_find_calls_missing_required() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("find_calls", {})


def test_find_calls_scope_filter() -> None:
    q = build_joern_query("find_calls", {"callee_name_pattern": "exec", "method_full_name_pattern": "com.*"})
    assert '.where(_.method.fullName("com.*"))' in q


def test_find_calls_escapes_quotes() -> None:
    q = build_joern_query("find_calls", {"callee_name_pattern": 'foo"bar'})
    assert 'foo\\"bar' in q


def test_get_call_arguments_keeps_L_suffix() -> None:
    q = build_joern_query("get_call_arguments", {"call_id": "30064771122L"})
    assert "cpg.call.id(30064771122L)" in q
    assert "evalType" in q


def test_get_call_arguments_missing_required() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("get_call_arguments", {})


def test_find_literals_any_type() -> None:
    q = build_joern_query("find_literals", {"pattern": "password"})
    assert 'cpg.literal.code("password")' in q
    assert "l.file.name" in q
    assert "typeFullName" not in q.split('.code("password")')[1].split(".map")[0]


def test_find_literals_string_type() -> None:
    q = build_joern_query("find_literals", {"pattern": ".*", "literal_type": "string"})
    assert "[Ss]tring" in q


def test_find_literals_int_type() -> None:
    q = build_joern_query("find_literals", {"pattern": ".*", "literal_type": "int"})
    assert "[Ii]nt" in q


def test_find_literals_missing_required() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("find_literals", {})


def test_get_method_location_by_id() -> None:
    q = build_joern_query("get_method_location", {"method_id": "107374182400L"})
    assert "cpg.method.id(107374182400L)" in q
    assert "lineEnd" in q


def test_get_method_location_by_full_name() -> None:
    q = build_joern_query("get_method_location", {"method_full_name": "com.Foo.bar:void()"})
    assert 'cpg.method.fullName("com.Foo.bar:void()")' in q


def test_get_method_location_missing_both() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("get_method_location", {})


def test_get_dataflow_builds_reachable_by_flows() -> None:
    q = build_joern_query("get_dataflow", {"source_pattern": "getUserInput", "sink_pattern": "exec"})
    assert 'cpg.call.name("getUserInput")' in q
    assert 'cpg.call.name("exec")' in q
    assert "reachableByFlows" in q


def test_get_dataflow_missing_source() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_joern_query("get_dataflow", {"sink_pattern": "exec"})


def test_known_tools_include_sprint4() -> None:
    names = known_tool_names()
    for t in ("find_methods", "find_calls", "get_call_arguments",
              "find_literals", "get_method_location", "get_dataflow"):
        assert t in names, f"sprint 4 tool missing from dispatch: {t}"
