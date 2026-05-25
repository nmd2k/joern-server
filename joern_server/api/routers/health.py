"""Health, version, metrics, and cache-metrics routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from joern_server.api.deps import get_state
from joern_server.state import AppState
from joern_server.upstream import joern as upstream

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
def health(
    request: Request,
    deep: bool = Query(default=False, description="If true, probe the Joern REPL with a CPGQL query"),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    t0 = __import__("time").perf_counter()
    if state.draining:
        payload: dict[str, Any] = {
            "ok": False,
            "joern_ok": False,
            "joern_http_ok": False,
            "latency_ms": 0,
            "draining": True,
        }
        return JSONResponse(status_code=HTTPStatus.SERVICE_UNAVAILABLE, content=payload)

    http_ok = upstream.check_joern_tcp(
        state.settings.internal_host,
        state.settings.internal_port,
        timeout_sec=2.0,
    )
    latency_ms = int((__import__("time").perf_counter() - t0) * 1000)

    repl_ok: bool | None = None
    repl_latency_ms: int | None = None
    repl_error: str | None = None

    if deep:
        probe_timeout = float(state.settings.health_probe_timeout_sec)
        headers = upstream.upstream_headers_from_request(request)
        repl_ok, repl_latency_ms, repl_error = upstream.probe_joern(
            state.internal_url,
            headers=headers,
            timeout_sec=probe_timeout,
        )

    overall_ok = http_ok if not deep else (http_ok and repl_ok)

    if state.metrics is not None:
        state.metrics.set_gauge("joern_proxy_joern_up", 1.0 if http_ok else 0.0)
        if repl_ok is not None:
            state.metrics.set_gauge("joern_proxy_joern_repl_up", 1.0 if repl_ok else 0.0)

    payload: dict[str, Any] = {
        "ok": overall_ok,
        "joern_ok": overall_ok,
        "joern_http_ok": http_ok,
        "latency_ms": latency_ms,
    }
    if repl_ok is not None:
        payload["joern_repl_ok"] = repl_ok
        payload["repl_latency_ms"] = repl_latency_ms
    if repl_error:
        payload["repl_error"] = repl_error

    status = HTTPStatus.OK if overall_ok else HTTPStatus.SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status, content=payload)


@router.get("/metrics", response_model=None)
def metrics(state: AppState = Depends(get_state)):
    if state.metrics is None:
        return JSONResponse(status_code=HTTPStatus.NOT_FOUND, content={"error": "metrics not enabled"})
    state.metrics.set_gauge(
        "joern_proxy_affinity_map_size",
        float(len(state.affinity_cpg_path)),
    )
    body = state.metrics.render()
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/version", response_model=None)
def version(request: Request, state: AppState = Depends(get_state)):
    try:
        headers = upstream.upstream_headers_from_request(request)
        resp = upstream.post_query_sync(
            state.internal_url,
            query="version",
            headers=headers,
            timeout_sec=60,
        )
        resp.raise_for_status()
        body = resp.json()
        return {"stdout": body.get("stdout", "")}
    except Exception as exc:
        return JSONResponse(status_code=HTTPStatus.BAD_GATEWAY, content={"error": str(exc)})


@router.post("/cache-metrics")
def cache_metrics(state: AppState = Depends(get_state)) -> dict[str, Any]:
    if state.query_cache:
        return state.query_cache.get_metrics()
    return {"error": "cache not enabled"}
