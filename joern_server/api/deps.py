"""FastAPI dependencies."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import Request

from joern_server.state import AppState
from joern_server.util.errors import json_error


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def parse_json_body(raw: bytes) -> tuple[Optional[dict], Optional[dict]]:
    try:
        data = json.loads(raw.decode("utf-8") if raw else "{}")
    except Exception:
        return None, json_error("invalid JSON body")
    if not isinstance(data, dict):
        return None, json_error("JSON body must be an object")
    return data, None
