"""Debug endpoints for integration testing (disabled in production)."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state
from joern_server.lifecycle.drain import schedule_joern_restart_after_drain
from joern_server.state import AppState
from joern_server.util.errors import json_error

router = APIRouter(tags=["debug"], include_in_schema=False)


@router.get("/debug/drain/enabled")
def debug_drain_enabled(state: AppState = Depends(get_state)) -> JSONResponse:
    """Report whether drain test hooks are enabled (no side effects)."""
    return JSONResponse(status_code=HTTPStatus.OK, content={"enabled": state.settings.enable_drain_test})


@router.post("/debug/drain")
def debug_drain(state: AppState = Depends(get_state)) -> JSONResponse:
    """Start drain+restart cycle on this replica (requires JOERN_ENABLE_DRAIN_TEST=1)."""
    if not state.settings.enable_drain_test:
        return JSONResponse(
            status_code=HTTPStatus.NOT_FOUND,
            content=json_error("not found", code="not_found"),
        )
    scheduled = schedule_joern_restart_after_drain(state, reason="debug_drain_test")
    return JSONResponse(
        status_code=HTTPStatus.OK,
        content={
            "ok": True,
            "draining": state.draining,
            "scheduled": scheduled,
            "drain_sec": state.settings.joern_drain_sec,
        },
    )
