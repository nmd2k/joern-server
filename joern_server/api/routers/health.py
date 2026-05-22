"""Health, version, metrics, and cache-metrics routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from joern_server.api.deps import get_state
from joern_server.state import AppState
from joern_server.upstream import joern as upstream

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
def health(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    probe_timeout = float(state.settings.health_probe_timeout_sec)
    headers = upstream.upstream_headers_from_request(request)
    ok, latency_ms, err = upstream.probe_joern(
        state.internal_url,
        headers=headers,
        timeout_sec=probe_timeout,
    )
    if state.metrics is not None:
        state.metrics.set_gauge("joern_proxy_joern_up", 1.0 if ok else 0.0)
    payload: dict[str, Any] = {
        "ok": ok,
        "joern_ok": ok,
        "latency_ms": latency_ms,
    }
    if err:
        payload["error"] = err
    status = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
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
