"""Shared graph pipeline: Joern query → DOT or AST tuples → JSON graph."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable, Optional

import httpx

from joern_server.cpg.paths import safe_sample_id
from joern_server.graph.dot import dot_to_graph, extract_dot_from_stdout
from joern_server.graph.metadata import fetch_node_metadata
from joern_server.graph.scala_parse import _parse_ast_tuples
from joern_server.session.affinity import activate_cpg
from joern_server.session.repl_lock import repl_lock
from joern_server.state import AppState
from joern_server.upstream import joern as upstream
from joern_server.util.errors import json_error


def escape_method_full_name(method_full_name: str) -> str:
    return method_full_name.replace("\\", "\\\\").replace('"', '\\"')


def _parse_upstream_success(resp_json: dict) -> bool:
    success = resp_json.get("success", True)
    if isinstance(success, str):
        return success.strip().lower() in ("true", "1", "yes")
    return bool(success)


def _affinity_key_from_headers(headers: dict[str, str]) -> str:
    raw = headers.get("X-Affinity-Key") or headers.get("x-affinity-key")
    if raw and str(raw).strip():
        return safe_sample_id(str(raw).strip())
    return "default"


def _ensure_cpg_active(
    state: AppState,
    *,
    headers: dict[str, str],
) -> tuple[bool, Optional[tuple[int, dict]]]:
    """Activate affinity-bound CPG in the REPL before graph queries."""
    key = _affinity_key_from_headers(headers)
    ok, err = activate_cpg(state, key, headers=headers)
    if not ok:
        return False, (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            json_error(
                err or "session cpg activation failed",
                code="session_cpg_activation_failed",
            ),
        )
    return True, None


class _CpgActivationFailed(Exception):
    def __init__(self, err: tuple[int, dict]) -> None:
        self.err = err


def _post_query(state: AppState, query: str, *, headers: dict[str, str]) -> httpx.Response:
    with repl_lock(state.repl_semaphore):
        activated, act_err = _ensure_cpg_active(state, headers=headers)
        if not activated and act_err is not None:
            raise _CpgActivationFailed(act_err)
        return upstream.post_query_sync(
            state.internal_url,
            query=query,
            headers=headers,
            timeout_sec=state.settings.query_timeout_sec,
        )


def _is_stub_cfg(graph: dict[str, Any]) -> bool:
    """True when CFG is only METHOD + METHOD_RETURN with no real basic blocks."""
    nodes = graph.get("nodes") or []
    if len(nodes) > 3:
        return False
    labels = [str(n.get("label") or "").upper() for n in nodes]
    if not labels:
        return True
    non_trivial = [
        lab for lab in labels
        if lab and "METHOD" not in lab and "RETURN" not in lab and lab not in ("RET", "<EMPTY>", "")
    ]
    return len(non_trivial) == 0 and len(nodes) <= 3


def fetch_dot(
    state: AppState,
    query: str,
    *,
    headers: dict[str, str],
) -> tuple[Optional[str], Optional[tuple[int, dict]]]:
    """Run a DOT-producing query. Returns (stdout, error_response) on failure."""
    try:
        resp = _post_query(state, query, headers=headers)
        resp.raise_for_status()
        resp_json = resp.json()
        if not _parse_upstream_success(resp_json):
            return None, (HTTPStatus.UNPROCESSABLE_ENTITY, json_error("query failed", code="query_failed"))
        return extract_dot_from_stdout(resp_json.get("stdout", "") or ""), None
    except _CpgActivationFailed as exc:
        return None, exc.err
    except httpx.TimeoutException:
        return None, (HTTPStatus.GATEWAY_TIMEOUT, json_error("query timed out", code="query_timeout"))
    except Exception as exc:
        return None, (HTTPStatus.BAD_GATEWAY, json_error(str(exc), code="joern_error"))


def dot_to_graph_response(
    stdout: str,
    method_full_name: str,
    *,
    state: AppState,
    headers: dict[str, str],
    empty_message: str,
) -> tuple[Optional[dict], Optional[tuple[int, dict]]]:
    """Parse DOT stdout into graph JSON with optional metadata enrichment."""
    graph = dot_to_graph(stdout)
    if not graph["nodes"] and not graph["edges"]:
        return None, (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            json_error(empty_message, code="empty_result"),
        )
    if _is_stub_cfg(graph):
        return None, (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            json_error(
                "Method has no analyzable control flow (unresolved stub). Pick a method from project source.",
                code="stub_method",
            ),
        )

    response_body: dict[str, Any] = {
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "method_full_name": method_full_name,
    }
    node_ids = [n["id"] for n in graph["nodes"]]
    try:
        response_body["metadata"] = fetch_node_metadata(state, node_ids, headers=headers)
    except Exception:
        response_body["metadata"] = {}
    return response_body, None


def _map_query_failed(
    err: tuple[int, dict],
    method_full_name: str,
    *,
    graph_kind: str,
) -> tuple[int, dict]:
    status, body = err
    if status == HTTPStatus.UNPROCESSABLE_ENTITY and body.get("code") == "query_failed":
        return HTTPStatus.UNPROCESSABLE_ENTITY, json_error(
            f"{graph_kind} query failed for method: {method_full_name}",
            code="query_failed",
        )
    return status, body


def handle_cfg(
    state: AppState,
    data: dict,
    *,
    headers: dict[str, str],
    log_event: Optional[Callable[..., None]] = None,
) -> tuple[int, dict]:
    method_full_name = str(data.get("method_full_name", "")).strip()
    if not method_full_name:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: method_full_name")

    sample_id = data.get("sample_id", "")
    if log_event is not None:
        log_event("graph_cfg_request", method_full_name=method_full_name, sample_id=sample_id)

    escaped = escape_method_full_name(method_full_name)
    query = f'cpg.method.fullNameExact("{escaped}").dotCfg.l'
    stdout, err = fetch_dot(state, query, headers=headers)
    if err is not None:
        return _map_query_failed(err, method_full_name, graph_kind="CFG")

    body, err = dot_to_graph_response(
        stdout or "",
        method_full_name,
        state=state,
        headers=headers,
        empty_message=f"No CFG found for method: {method_full_name}",
    )
    if err is not None:
        return err
    return HTTPStatus.OK, body


def handle_pdg(
    state: AppState,
    data: dict,
    *,
    headers: dict[str, str],
    log_event: Optional[Callable[..., None]] = None,
) -> tuple[int, dict]:
    method_full_name = str(data.get("method_full_name", "")).strip()
    if not method_full_name:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: method_full_name")

    sample_id = data.get("sample_id", "")
    if log_event is not None:
        log_event("graph_pdg_request", method_full_name=method_full_name, sample_id=sample_id)

    escaped = escape_method_full_name(method_full_name)
    query = f'cpg.method.fullNameExact("{escaped}").dotPdg.l'
    stdout, err = fetch_dot(state, query, headers=headers)
    if err is not None:
        return _map_query_failed(err, method_full_name, graph_kind="PDG")

    body, err = dot_to_graph_response(
        stdout or "",
        method_full_name,
        state=state,
        headers=headers,
        empty_message=f"No PDG found for method: {method_full_name}",
    )
    if err is not None:
        return err
    return HTTPStatus.OK, body


def handle_dfg(
    state: AppState,
    data: dict,
    *,
    headers: dict[str, str],
    log_event: Optional[Callable[..., None]] = None,
) -> tuple[int, dict]:
    method_full_name = str(data.get("method_full_name", "")).strip()
    if not method_full_name:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: method_full_name")

    sample_id = data.get("sample_id", "")
    source_pattern = str(data.get("source_pattern", "")).strip()
    sink_pattern = str(data.get("sink_pattern", "")).strip()

    escaped = escape_method_full_name(method_full_name)
    if source_pattern and sink_pattern:
        escaped_source = source_pattern.replace("\\", "\\\\").replace('"', '\\"')
        escaped_sink = sink_pattern.replace("\\", "\\\\").replace('"', '\\"')
        query = (
            f'cpg.method.fullNameExact("{escaped}")'
            f'.reachableByFlows(cpg.code("{escaped_source}").l, cpg.code("{escaped_sink}").l).p'
        )
    else:
        query = f'cpg.method.fullNameExact("{escaped}").dotDdg.l'

    if log_event is not None:
        log_event(
            "graph_dfg_request",
            method_full_name=method_full_name,
            sample_id=sample_id,
            source_pattern=source_pattern or None,
            sink_pattern=sink_pattern or None,
        )

    stdout, err = fetch_dot(state, query, headers=headers)
    if err is not None:
        return _map_query_failed(err, method_full_name, graph_kind="DFG")

    if source_pattern and sink_pattern:
        return HTTPStatus.OK, {
            "flows_raw": stdout or "",
            "method_full_name": method_full_name,
            "source_pattern": source_pattern,
            "sink_pattern": sink_pattern,
        }

    body, err = dot_to_graph_response(
        stdout or "",
        method_full_name,
        state=state,
        headers=headers,
        empty_message=f"No DFG found for method: {method_full_name}",
    )
    if err is not None:
        return err
    return HTTPStatus.OK, body


def handle_ast(
    state: AppState,
    data: dict,
    *,
    headers: dict[str, str],
    log_event: Optional[Callable[..., None]] = None,
) -> tuple[int, dict]:
    method_full_name = str(data.get("method_full_name", "")).strip()
    if not method_full_name:
        return HTTPStatus.BAD_REQUEST, json_error("missing required field: method_full_name")

    sample_id = data.get("sample_id", "")
    if log_event is not None:
        log_event("graph_ast_request", method_full_name=method_full_name, sample_id=sample_id)

    escaped = escape_method_full_name(method_full_name)
    query = (
        f'cpg.method.fullNameExact("{escaped}").ast.map(node => '
        f"(node.id, node.code, node.lineNumber, node.columnNumber, "
        f"node.order, node.label, "
        f"node.astParent.id)"
        f").l"
    )

    try:
        resp = _post_query(state, query, headers=headers)
        resp.raise_for_status()
        resp_json = resp.json()
        if not _parse_upstream_success(resp_json):
            return HTTPStatus.UNPROCESSABLE_ENTITY, json_error(
                f"AST query failed for method: {method_full_name}",
                code="query_failed",
            )
        stdout = resp_json.get("stdout", "")
        nodes, edges, metadata = _parse_ast_tuples(stdout)
        if not nodes:
            return HTTPStatus.UNPROCESSABLE_ENTITY, json_error(
                f"No AST found for method: {method_full_name}",
                code="empty_result",
            )
        return HTTPStatus.OK, {
            "nodes": nodes,
            "edges": edges,
            "metadata": metadata,
            "method_full_name": method_full_name,
        }
    except _CpgActivationFailed as exc:
        return exc.err
    except httpx.TimeoutException:
        return HTTPStatus.GATEWAY_TIMEOUT, json_error("query timed out", code="query_timeout")
    except Exception as exc:
        return HTTPStatus.BAD_GATEWAY, json_error(str(exc), code="joern_error")
