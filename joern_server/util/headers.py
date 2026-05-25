import uuid
from http.server import BaseHTTPRequestHandler

from starlette.requests import Request

from joern_server.cpg.paths import safe_sample_id


def upstream_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Headers to forward to the in-container Joern HTTP /query-sync (affinity + auth)."""
    h: dict[str, str] = {"Content-Type": "application/json"}
    auth = handler.headers.get("Authorization")
    if auth:
        h["Authorization"] = auth
    sid = handler.headers.get("X-Session-Id")
    if sid:
        h["X-Session-Id"] = sid
    aff = handler.headers.get("X-Affinity-Key")
    if aff:
        h["X-Affinity-Key"] = aff
    rid = handler.headers.get("X-Request-Id")
    if rid:
        h["X-Request-Id"] = rid
    return h


def affinity_key(handler: BaseHTTPRequestHandler) -> str:
    """Routing/REPL/cache key — typically sample_id (see X-Affinity-Key)."""
    raw = handler.headers.get("X-Affinity-Key")
    if raw and str(raw).strip():
        return safe_sample_id(str(raw).strip())
    return "default"


def request_id(handler: BaseHTTPRequestHandler) -> str:
    raw = handler.headers.get("X-Request-Id")
    if raw and str(raw).strip():
        return str(raw).strip()
    return uuid.uuid4().hex


def affinity_key_from_request(request: Request) -> str:
    """Routing/REPL/cache key from FastAPI/Starlette request headers."""
    raw = request.headers.get("X-Affinity-Key")
    if raw and str(raw).strip():
        return safe_sample_id(str(raw).strip())
    return "default"


def request_id_from_request(request: Request) -> str:
    raw = request.headers.get("X-Request-Id")
    if raw and str(raw).strip():
        return str(raw).strip()
    return uuid.uuid4().hex
