"""POST /query-sync — synchronous Joern REPL query proxy."""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state
from joern_server.cache.query_policy import (
    classify_query,
    preview_query,
    query_hash,
    should_cache,
)
from joern_server.session.affinity import activate_cpg, record_import_cpg_success
from joern_server.session.repl_lock import repl_lock
from joern_server.state import AppState
from joern_server.upstream import joern as upstream
from joern_server.util.ansi import strip_ansi
from joern_server.util.errors import json_error
from joern_server.util.headers import affinity_key_from_request, request_id_from_request

router = APIRouter(tags=["query"])


def _log_event(
    request: Request,
    event: str,
    *,
    affinity_key: str,
    req_id: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "component": "joern-server",
        "event": event,
        "path": str(request.url.path),
        "session_id": request.headers.get("X-Session-Id"),
        "affinity_key": affinity_key,
        "request_id": req_id,
        "ts_ms": int(time.time() * 1000),
    }
    payload.update(fields)
    try:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        return


def _parse_upstream_success(resp_json: dict) -> bool | None:
    raw_success = resp_json.get("success")
    if isinstance(raw_success, bool):
        return raw_success
    if isinstance(raw_success, str):
        return raw_success.strip().lower() in ("true", "1", "yes")
    return None


@router.post("/query-sync", response_model=None)
async def query_sync(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    body = await request.body()
    t0 = time.perf_counter()
    query_class = "unknown"
    query_preview = ""
    query_str = ""
    req_id = request_id_from_request(request)
    affinity_key = affinity_key_from_request(request)

    try:
        try:
            req = json.loads(body.decode("utf-8") if body else "{}")
            if isinstance(req, dict):
                query_str = str(req.get("query", "") or "")
                query_class = classify_query(query_str)
                query_preview = preview_query(query_str)
        except Exception:
            query_class = "invalid_json"

        headers = upstream.upstream_headers_from_request(request)

        if state.query_cache and should_cache(query_class):
            q_hash = query_hash(query_str)
            cached_result = state.query_cache.get(affinity_key, q_hash)
            if cached_result is not None:
                _log_event(
                    request,
                    "query_sync",
                    affinity_key=affinity_key,
                    req_id=req_id,
                    query_class=query_class,
                    query_preview=query_preview,
                    status_code=200,
                    success=True,
                    latency_ms=0,
                    cache_hit=True,
                )
                return JSONResponse(status_code=HTTPStatus.OK, content=cached_result)

        with repl_lock(state.repl_semaphore):
            if query_class == "importCpg":
                if state.active_cpg_path is not None:
                    try:
                        upstream.post_query_sync(
                            state.internal_url,
                            query="close",
                            headers=headers,
                            timeout_sec=min(30.0, state.settings.query_timeout_sec),
                        )
                    except Exception:
                        pass
                    state.active_cpg_path = None
                    if state.metrics is not None:
                        state.metrics.set_gauge("joern_proxy_active_cpg_loaded", 0.0)
            else:
                ok, activate_err = activate_cpg(
                    state,
                    affinity_key,
                    headers=headers,
                    log_event=lambda event, **kw: _log_event(
                        request,
                        event,
                        affinity_key=affinity_key,
                        req_id=req_id,
                        **kw,
                    ),
                )
                if not ok:
                    return JSONResponse(
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                        content=json_error(
                            activate_err or "session cpg activation failed",
                            code="session_cpg_activation_failed",
                        ),
                    )
            resp = upstream.post_query_sync(
                state.internal_url,
                content=body,
                headers=headers,
                timeout_sec=state.settings.query_timeout_sec,
            )

        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        if state.metrics is not None:
            state.metrics.observe(
                "joern_proxy_query_sync_duration_seconds",
                latency_ms / 1000.0,
                labels={"query_class": query_class},
            )
            state.metrics.inc(
                "joern_proxy_query_sync_requests",
                labels={"status": str(resp.status_code), "query_class": query_class},
            )

        resp_json = resp.json()
        if isinstance(resp_json, dict) and "stdout" in resp_json and isinstance(resp_json["stdout"], str):
            resp_json["stdout"] = strip_ansi(resp_json["stdout"])

        success = _parse_upstream_success(resp_json) if isinstance(resp_json, dict) else None
        out_status = resp.status_code
        if resp.status_code == 200 and success is False:
            out_status = HTTPStatus.UNPROCESSABLE_ENTITY

        if state.query_cache and should_cache(query_class) and out_status == 200:
            state.query_cache.put(affinity_key, query_hash(query_str), resp_json)

        if query_class == "importCpg" and out_status == 200:
            record_import_cpg_success(state, affinity_key, query_str)

        _log_event(
            request,
            "query_sync",
            affinity_key=affinity_key,
            req_id=req_id,
            query_class=query_class,
            query_preview=query_preview,
            status_code=out_status,
            success=success,
            latency_ms=latency_ms,
            cache_hit=False,
        )
        return JSONResponse(status_code=out_status, content=resp_json)

    except httpx.TimeoutException as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        _log_event(
            request,
            "query_sync_error",
            affinity_key=affinity_key,
            req_id=req_id,
            query_class=query_class,
            latency_ms=latency_ms,
            error_type="TimeoutException",
            error=str(exc),
        )
        if state.metrics is not None:
            state.metrics.inc(
                "joern_proxy_query_sync_requests",
                labels={"status": "504", "query_class": query_class},
            )
        return JSONResponse(
            status_code=HTTPStatus.GATEWAY_TIMEOUT,
            content={**json_error(str(exc), code="upstream_timeout"), "request_id": req_id},
        )
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        _log_event(
            request,
            "query_sync_error",
            affinity_key=affinity_key,
            req_id=req_id,
            query_class=query_class,
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if state.metrics is not None:
            state.metrics.inc(
                "joern_proxy_query_sync_requests",
                labels={"status": "502", "query_class": query_class},
            )
        return JSONResponse(
            status_code=HTTPStatus.BAD_GATEWAY,
            content={**json_error(str(exc), code="upstream_unreachable"), "request_id": req_id},
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        _log_event(
            request,
            "query_sync_error",
            affinity_key=affinity_key,
            req_id=req_id,
            query_class=query_class,
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if state.metrics is not None:
            state.metrics.inc(
                "joern_proxy_query_sync_requests",
                labels={"status": "502", "query_class": query_class},
            )
        return JSONResponse(
            status_code=HTTPStatus.BAD_GATEWAY,
            content={**json_error(str(exc), code="upstream_error"), "request_id": req_id},
        )
