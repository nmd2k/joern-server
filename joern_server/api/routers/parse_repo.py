"""POST /parse/repo and /parse/repo/upload routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from joern_server.api.deps import get_state, parse_json_body
from joern_server.parse.repo import (
    handle_parse_repo_json,
    handle_parse_repo_jsonl,
    handle_parse_repo_upload,
    parse_repo_query_params,
)
from joern_server.state import AppState
from joern_server.util.errors import json_error

router = APIRouter(tags=["parse"])


@router.post("/parse/repo", response_model=None)
async def parse_repo(
    request: Request,
    state: AppState = Depends(get_state),
    sample_id: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    overwrite: Optional[str] = Query(None),
) -> JSONResponse:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    raw_body = await request.body()
    if content_type == "application/x-ndjson":
        sid, lang, ow = parse_repo_query_params(sample_id, language, overwrite)
        status, body = handle_parse_repo_jsonl(
            state,
            sample_id_raw=sid,
            language=lang,
            overwrite=ow,
            body=raw_body,
        )
        return JSONResponse(status_code=status, content=body)

    if content_type != "application/json":
        return JSONResponse(
            status_code=HTTPStatus.BAD_REQUEST,
            content=json_error(
                "Content-Type must be application/x-ndjson (JSONL) or application/json (upload_id/source_root)",
                code="bad_request",
            ),
        )

    data, err = parse_json_body(raw_body)
    if err is not None or data is None:
        return JSONResponse(status_code=HTTPStatus.BAD_REQUEST, content=err or json_error("invalid request"))
    status, body = handle_parse_repo_json(state, data)
    return JSONResponse(status_code=status, content=body)


@router.post("/parse/repo/upload", response_model=None)
async def parse_repo_upload(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    content_type = request.headers.get("content-type") or ""
    status, body = handle_parse_repo_upload(
        state,
        body=await request.body(),
        content_type=content_type,
    )
    return JSONResponse(status_code=status, content=body)
