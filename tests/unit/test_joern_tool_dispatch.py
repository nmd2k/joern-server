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
