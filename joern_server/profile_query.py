from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

from joern_server.client import JoernHTTPQueryExecutor

def _preview(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


def _run_stage(executor: JoernHTTPQueryExecutor, *, run_id: str, stage: str, hypothesis_id: str, query: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = executor.execute(query)
        success = bool(result.get("success"))
        executor_latency_ms = round(float(result.get("latency_ms", 0.0)), 2)
        stdout = str(result.get("stdout", "") or "")
        stderr = str(result.get("stderr", "") or "")
    except BaseException as e:
        wall_ms = round((time.perf_counter() - started) * 1000.0, 2)
        summary = {
            "stage": stage,
            "success": False,
            "wall_ms": wall_ms,
            "executor_latency_ms": None,
            "stdout_len": 0,
            "stderr_len": 0,
            "stdout_preview": "",
            "stderr_preview": "",
            "exception": f"{type(e).__name__}: {e}",
        }
        print(f"[joern-profile] {stage}: success=False wall_ms={wall_ms} exc={type(e).__name__}")
        return summary

    wall_ms = round((time.perf_counter() - started) * 1000.0, 2)
    summary = {
        "stage": stage,
        "success": success,
        "wall_ms": wall_ms,
        "executor_latency_ms": executor_latency_ms,
        "stdout_len": len(stdout),
        "stderr_len": len(stderr),
        "stdout_preview": _preview(stdout),
        "stderr_preview": _preview(stderr),
    }
    print(f"[joern-profile] {stage}: success={summary['success']} wall_ms={wall_ms} stderr_len={summary['stderr_len']}")
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Profile Joern /query-sync stages for a potentially slow CPGQL query.")
    p.add_argument("--url", default=os.environ.get("JOERN_HTTP_URL", "http://127.0.0.1:8080"))
    p.add_argument("--user", default=os.environ.get("JOERN_SERVER_AUTH_USERNAME"))
    p.add_argument("--password", default=os.environ.get("JOERN_SERVER_AUTH_PASSWORD"))
    p.add_argument("--cpg-path", required=True, help='Container path, e.g. "/workspace/cpg-out/csharp"')
    p.add_argument("--source-query", default='cpg.call.name("<operator>.fieldAccess").code(".*user.Email.*")')
    p.add_argument("--sink-query", default='cpg.call.name("View").argument')
    p.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    args = p.parse_args()

    auth = (args.user, args.password) if args.user and args.password else None
    run_id = f"profile-{int(time.time())}"
    import_prefix = f'importCpg("{args.cpg_path}"); '
    stages = [
        ("healthcheck", "H5", 'val _health = "ok"'),
        ("import_only", "H1", import_prefix + 'val _imported = "ok"'),
        ("source_probe", "H2", import_prefix + args.source_query + ".take(20).code.l"),
        ("sink_probe", "H3", import_prefix + args.sink_query + ".take(20).code.l"),
        (
            "flow_query",
            "H4",
            import_prefix + f"def source = {args.source_query}; def sink = {args.sink_query}; sink.reachableByFlows(source).p",
        ),
    ]

    with JoernHTTPQueryExecutor(args.url, auth=auth, timeout=args.timeout, retries=0) as executor:
        summaries = [_run_stage(executor, run_id=run_id, stage=stage, hypothesis_id=hypothesis_id, query=query) for stage, hypothesis_id, query in stages]

    print(json.dumps({"run_id": run_id, "stages": summaries}, indent=2))


if __name__ == "__main__":
    main()
