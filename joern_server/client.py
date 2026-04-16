from __future__ import annotations

import itertools
import time
from threading import Lock
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx


def _normalize_base_urls(urls: str | Sequence[str]) -> list[str]:
    if isinstance(urls, str):
        urls = [urls]
    out: list[str] = []
    for u in urls:
        u = u.strip().rstrip("/")
        if not u:
            continue
        if "://" not in u:
            u = "http://" + u
        out.append(u)
    if not out:
        raise ValueError("At least one non-empty base URL is required")
    return out


def _coerce_success(body: dict[str, Any]) -> bool:
    s = body.get("success")
    if isinstance(s, bool):
        return s
    if isinstance(s, str):
        return s.strip().lower() in ("true", "1", "yes")
    return False


class JoernHTTPQueryExecutor:
    """
    Execute CPGQL against Joern's HTTP ``/query-sync`` endpoint (see Joern server docs).

    Supports multiple base URLs with round-robin load spreading. Uses synchronous
    ``httpx`` (avoids asyncio nesting issues with the official ``cpgqls-client``,
    which targets ``/query`` + WebSocket). For the reference WebSocket client see
    https://github.com/joernio/cpgqls-client-python

    **Session affinity:** pass ``session_id`` so each request includes ``X-Session-Id``.
    Configure your load balancer to pin on that header when one VIP fronts many Joern
    containers. For multiple explicit ``base_urls`` without sticky LB, set
    ``reuse_base=True`` so ``parse_source`` and ``execute`` stay on the first chosen
    base for this executor instance.
    """

    def __init__(
        self,
        base_urls: str | Sequence[str],
        *,
        auth: tuple[str, str] | None = None,
        timeout: float = 600.0,
        retries: int = 2,
        http_client: httpx.Client | None = None,
        session_id: str | None = None,
        reuse_base: bool = False,
    ) -> None:
        self._bases = _normalize_base_urls(base_urls)
        self._auth = auth
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._session_id = session_id.strip() if session_id and str(session_id).strip() else None
        self._reuse_base = bool(reuse_base)
        self._pinned_base: str | None = None
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            headers={"Content-Type": "application/json"},
            follow_redirects=True,
        )
        self._rr: itertools.cycle[str] = itertools.cycle(self._bases)
        self._lock = Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JoernHTTPQueryExecutor:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request_headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._session_id:
            h["X-Session-Id"] = self._session_id
        return h

    def _base_for_request(self) -> str:
        with self._lock:
            if self._reuse_base and self._pinned_base is not None:
                return self._pinned_base
            base = next(self._rr)
            if self._reuse_base:
                self._pinned_base = base
            return base

    def execute(self, cpgql: str) -> dict[str, Any]:
        base = self._base_for_request()
        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme for Joern base: {base!r}")

        url = f"{base}/query-sync"
        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            t0 = time.perf_counter()
            try:
                r = self._client.post(
                    url,
                    json={"query": cpgql},
                    auth=self._auth,
                    headers=self._request_headers(),
                    timeout=self._timeout,
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
                if r.status_code == 401:
                    return {
                        "success": False,
                        "stdout": "",
                        "stderr": "HTTP 401 unauthorized (check JOERN basic auth)",
                        "latency_ms": latency_ms,
                    }
                r.raise_for_status()
                body = r.json()
                if not isinstance(body, dict):
                    return {
                        "success": False,
                        "stdout": "",
                        "stderr": f"unexpected JSON type: {type(body).__name__}",
                        "latency_ms": latency_ms,
                    }
                return {
                    "success": _coerce_success(body),
                    "stdout": str(body.get("stdout", "") or ""),
                    "stderr": str(body.get("stderr", "") or ""),
                    "latency_ms": latency_ms,
                }
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
                if attempt >= self._retries:
                    break
        assert last_err is not None
        return {
            "success": False,
            "stdout": "",
            "stderr": f"request failed after {self._retries + 1} attempt(s): {last_err}",
            "latency_ms": 0.0,
        }

    def parse_source(
        self,
        *,
        sample_id: str,
        source_code: str,
        language: str | None = None,
        filename: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """
        Ask the Joern proxy to parse source code into ``/workspace/cpg-out/<sample_id>``.

        Endpoint: ``POST /parse``.
        """
        base = self._base_for_request()
        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme for Joern base: {base!r}")

        url = f"{base}/parse"
        payload: dict[str, Any] = {
            "sample_id": sample_id,
            "source_code": source_code,
            "overwrite": bool(overwrite),
        }
        if language:
            payload["language"] = language
        if filename:
            payload["filename"] = filename

        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                r = self._client.post(
                    url,
                    json=payload,
                    auth=self._auth,
                    headers=self._request_headers(),
                    timeout=self._timeout,
                )
                if r.status_code == 401:
                    return {"ok": False, "error": "HTTP 401 unauthorized (check JOERN basic auth)"}
                body = r.json()
                if not isinstance(body, dict):
                    return {"ok": False, "error": f"unexpected JSON type: {type(body).__name__}"}
                return body
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
                if attempt >= self._retries:
                    break
        assert last_err is not None
        return {"ok": False, "error": f"request failed after {self._retries + 1} attempt(s): {last_err}"}
