"""
Mock tests for Joern VIP stickiness: same X-Session-Id must reach the same logical backend.

Real HAProxy/nginx is not started; httpx.MockTransport simulates:
- StickyLBMocker: assigns a backend per session id and keeps it stable.
- PerRequestRoundRobinLB: ignores session (broken stickiness); rotates each request.
"""

from __future__ import annotations

import httpx

from joern_server.client import JoernHTTPQueryExecutor


def _parse_backend(stdout: str) -> str:
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("BACKEND="):
            return line.split("=", 1)[1]
    raise AssertionError(f"no BACKEND= in stdout: {stdout!r}")


class StickyLBMocker:
    """Simulates LB that pins X-Session-Id to the first chosen backend."""

    def __init__(self, backends: tuple[str, ...]) -> None:
        if len(backends) < 2:
            raise ValueError("need at least 2 backends to test stickiness")
        self._backends = backends
        self._session_to_backend: dict[str, str] = {}
        self._rr = 0

    def backend_for(self, session_id: str | None) -> str:
        if not session_id:
            b = self._backends[self._rr % len(self._backends)]
            self._rr += 1
            return b
        if session_id not in self._session_to_backend:
            self._session_to_backend[session_id] = self._backends[self._rr % len(self._backends)]
            self._rr += 1
        return self._session_to_backend[session_id]


class PerRequestRoundRobinLB:
    """Simulates broken LB: new backend every request even if X-Session-Id repeats."""

    def __init__(self, backends: tuple[str, ...]) -> None:
        self._backends = backends
        self._i = 0

    def backend_for(self, session_id: str | None) -> str:
        _ = session_id
        b = self._backends[self._i % len(self._backends)]
        self._i += 1
        return b


def _make_transport(lb: StickyLBMocker | PerRequestRoundRobinLB) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query-sync"
        sid = request.headers.get("x-session-id") or request.headers.get("X-Session-Id")
        b = lb.backend_for(sid)
        return httpx.Response(
            200,
            json={
                "success": True,
                "stdout": f"BACKEND={b}\n",
                "stderr": "",
            },
        )

    return httpx.MockTransport(handler)


def test_mock_sticky_lb_same_session_always_same_backend() -> None:
    lb = StickyLBMocker(("alpha", "beta", "gamma"))
    transport = _make_transport(lb)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            "http://vip.example:8080",
            http_client=hc,
            session_id="row-42-session",
            retries=0,
            timeout=30.0,
        )
        backends = [_parse_backend(ex.execute(f"q{i}")["stdout"]) for i in range(6)]

    assert len(set(backends)) == 1, f"expected one backend, got {backends}"


def test_mock_sticky_lb_two_sessions_can_land_on_different_backends() -> None:
    lb = StickyLBMocker(("alpha", "beta", "gamma"))
    transport = _make_transport(lb)
    with httpx.Client(transport=transport) as hc:
        ex_a = JoernHTTPQueryExecutor(
            "http://vip.example:8080",
            http_client=hc,
            session_id="session-a",
            retries=0,
            timeout=30.0,
        )
        ex_b = JoernHTTPQueryExecutor(
            "http://vip.example:8080",
            http_client=hc,
            session_id="session-b",
            retries=0,
            timeout=30.0,
        )
        b_a = _parse_backend(ex_a.execute("1")["stdout"])
        b_b = _parse_backend(ex_b.execute("1")["stdout"])
        # First session grabs alpha, second grabs beta by RR assignment order.
        assert b_a == "alpha"
        assert b_b == "beta"
        assert _parse_backend(ex_a.execute("2")["stdout"]) == "alpha"
        assert _parse_backend(ex_b.execute("2")["stdout"]) == "beta"


def test_mock_broken_lb_round_robin_per_request_not_sticky() -> None:
    lb = PerRequestRoundRobinLB(("alpha", "beta"))
    transport = _make_transport(lb)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            "http://vip.example:8080",
            http_client=hc,
            session_id="same-session-id",
            retries=0,
            timeout=30.0,
        )
        backends = [_parse_backend(ex.execute(f"x{i}")["stdout"]) for i in range(4)]

    # Without stickiness, even a constant session id rotates backends.
    assert backends == ["alpha", "beta", "alpha", "beta"]


def test_sticky_lb_single_vip_url_session_id_pins_backend() -> None:
    """One VIP in base_urls; mock LB pins by X-Session-Id from JoernHTTPQueryExecutor."""
    lb = StickyLBMocker(("alpha", "beta"))
    transport = _make_transport(lb)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            ["http://vip.example:8080"],
            http_client=hc,
            session_id="pin-test",
            reuse_base=True,
            retries=0,
            timeout=30.0,
        )
        b1 = _parse_backend(ex.execute("a")["stdout"])
        b2 = _parse_backend(ex.execute("b")["stdout"])
    assert b1 == b2 == "alpha"


def test_no_session_id_sticky_lb_rotates_each_call() -> None:
    """Without X-Session-Id, the mock LB has no key - simulates weak stickiness (e.g. IP-only) not tested here."""
    lb = StickyLBMocker(("alpha", "beta"))
    transport = _make_transport(lb)
    with httpx.Client(transport=transport) as hc:
        ex = JoernHTTPQueryExecutor(
            "http://vip.example:8080",
            http_client=hc,
            session_id=None,
            retries=0,
            timeout=30.0,
        )
        backends = [_parse_backend(ex.execute(str(i))["stdout"]) for i in range(4)]
    assert backends == ["alpha", "beta", "alpha", "beta"]
