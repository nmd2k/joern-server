"""HTTP middleware for replica drain mode."""

from __future__ import annotations

from http import HTTPStatus

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from joern_server.state import AppState
from joern_server.util.errors import json_error

# Health is allowed through so the router can return a structured 503 for HAProxy.
# Debug probe endpoint is allowed so staging can verify JOERN_ENABLE_DRAIN_TEST without draining.
_DRAIN_ALLOW_PREFIXES = ("/metrics", "/health", "/debug/drain/enabled")


class DrainMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        state: AppState | None = getattr(request.app.state, "app_state", None)
        if state is not None and state.draining:
            path = request.url.path
            if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in _DRAIN_ALLOW_PREFIXES):
                return JSONResponse(
                    status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    content=json_error(
                        "replica draining for joern restart; retry on another node",
                        code="replica_draining",
                    ),
                )
        return await call_next(request)
