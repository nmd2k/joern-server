"""Graph endpoints: CFG, PDG, DFG/DDG, AST."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state, parse_json_body
from joern_server.graph import service as graph_service
from joern_server.state import AppState
from joern_server.upstream import joern as upstream

router = APIRouter(tags=["graph"])


@router.post("/graph/cfg", response_model=None)
async def graph_cfg(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=400, content=err)
    headers = upstream.upstream_headers_from_request(request)
    status, body = graph_service.handle_cfg(state, data, headers=headers)
    return JSONResponse(status_code=status, content=body)


@router.post("/graph/pdg", response_model=None)
async def graph_pdg(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=400, content=err)
    headers = upstream.upstream_headers_from_request(request)
    status, body = graph_service.handle_pdg(state, data, headers=headers)
    return JSONResponse(status_code=status, content=body)


@router.post("/graph/dfg", response_model=None)
@router.post("/graph/ddg", response_model=None)
async def graph_dfg(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=400, content=err)
    headers = upstream.upstream_headers_from_request(request)
    status, body = graph_service.handle_dfg(state, data, headers=headers)
    return JSONResponse(status_code=status, content=body)


@router.post("/graph/ast", response_model=None)
async def graph_ast(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    data, err = parse_json_body(await request.body())
    if err is not None or data is None:
        return JSONResponse(status_code=400, content=err)
    headers = upstream.upstream_headers_from_request(request)
    status, body = graph_service.handle_ast(state, data, headers=headers)
    return JSONResponse(status_code=status, content=body)
