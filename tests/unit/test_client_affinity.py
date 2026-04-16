"""Joern HTTP client: session header and optional base pinning."""

from __future__ import annotations

import httpx

from joern_server.client import JoernHTTPQueryExecutor


def test_joern_client_sends_x_session_id() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True, "stdout": "ok", "stderr": ""})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            "http://127.0.0.1:8080",
            http_client=hc,
            session_id="affinity-session-1",
            retries=0,
        )
        out = ex.execute("1+1")
    assert out["success"] is True
    assert len(captured) == 1
    assert captured[0].headers.get("X-Session-Id") == "affinity-session-1"


def test_joern_client_reuse_base_pins_first_host() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"success": True, "stdout": "ok", "stderr": ""})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            ["http://host-a:8080", "http://host-b:8080"],
            http_client=hc,
            reuse_base=True,
            retries=0,
        )
        ex.execute("a")
        ex.execute("b")
    assert captured_urls == [
        "http://host-a:8080/query-sync",
        "http://host-a:8080/query-sync",
    ]
