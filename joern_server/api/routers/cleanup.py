"""POST /cleanup route."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state, parse_json_body
from joern_server.lifecycle.cleanup import handle_cleanup
from joern_server.state import AppState
from joern_server.upstream.joern import upstream_headers_from_request
from joern_server.util.errors import json_error

router = APIRouter(tags=["lifecycle"])


@router.post("/cleanup", response_model=None)
async def cleanup(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=HTTPStatus.BAD_REQUEST, content=err or json_error("invalid request"))
    headers = upstream_headers_from_request(request)
    status, body = handle_cleanup(state, data, request_headers=headers)
    return JSONResponse(status_code=status, content=body)
