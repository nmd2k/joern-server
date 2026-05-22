"""POST /parse route."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state, parse_json_body
from joern_server.parse.single import handle_parse
from joern_server.state import AppState
from joern_server.util.errors import json_error

router = APIRouter(tags=["parse"])


@router.post("/parse", response_model=None)
async def parse_snippet(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=HTTPStatus.BAD_REQUEST, content=err or json_error("invalid request"))
    status, body = handle_parse(state, data)
    return JSONResponse(status_code=status, content=body)
