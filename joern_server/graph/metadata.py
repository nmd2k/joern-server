"""Batch-fetch AST node metadata from Joern query-sync."""

from __future__ import annotations

import threading

from joern_server.graph.scala_parse import _parse_metadata_tuples
from joern_server.session.repl_lock import repl_lock
from joern_server.state import AppState
from joern_server.upstream import joern as upstream


def _parse_upstream_success(resp_json: dict) -> bool:
    success = resp_json.get("success", True)
    if isinstance(success, str):
        return success.strip().lower() in ("true", "1", "yes")
    return bool(success)


def fetch_node_metadata(
    state: AppState,
    node_ids: list[str],
    *,
    headers: dict[str, str],
) -> dict[str, dict]:
    """Batch-fetch metadata for node IDs. Returns empty dict on failure."""
    if not node_ids:
        return {}

    numeric_ids: list[int] = []
    for nid in node_ids:
        try:
            numeric_ids.append(int(nid))
        except (ValueError, TypeError):
            pass
    if not numeric_ids:
        return {}

    metadata: dict[str, dict] = {}
    max_batch = 50

    for i in range(0, len(numeric_ids), max_batch):
        batch = numeric_ids[i : i + max_batch]
        id_list = ", ".join(f"{nid}L" for nid in batch)
        query = (
            f"cpg.all.id({id_list}).collectAll[AstNode].map(n =>"
            f" (n.id, n.code, n.lineNumber, n.columnNumber,"
            f" n.order, n.label)"
            f").l"
        )
        try:
            with repl_lock(state.repl_semaphore):
                resp = upstream.post_query_sync(
                    state.internal_url,
                    query=query,
                    headers=headers,
                    timeout_sec=state.settings.query_timeout_sec,
                )
            resp.raise_for_status()
            resp_json = resp.json()
            if not _parse_upstream_success(resp_json):
                continue
            stdout = resp_json.get("stdout", "")
            batch_meta = _parse_metadata_tuples(stdout)
            metadata.update(batch_meta)
        except Exception:
            continue

    return metadata
