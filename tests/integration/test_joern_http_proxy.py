"""Integration tests for Joern HTTP proxy with mocked backend."""

import json
import os
import re
import signal
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import httpx
import pytest


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockJoernHandler(BaseHTTPRequestHandler):
    received_queries: list[str] = []
    last_upstream_headers: dict[str, str] = {}

    def log_message(self, *_args) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/query-sync":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        length = self.headers.get("Content-Length")
        raw = self.rfile.read(int(length)) if length else b""
        query = ""
        try:
            query = json.loads(raw.decode("utf-8")).get("query", "")
        except Exception:
            query = ""

        MockJoernHandler.received_queries.append(query)
        MockJoernHandler.last_upstream_headers = {k: v for k, v in self.headers.items()}

        if query.strip() == "version":
            self._send_json(HTTPStatus.OK, {"stdout": "mock-version"})
        elif "val _health" in query or query.strip() == "val _health = 1":
            self._send_json(HTTPStatus.OK, {"stdout": "1"})
        else:
            self._send_json(HTTPStatus.OK, {"stdout": "ok"})


def start_mock_joern_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), MockJoernHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def _wait_for_http_ok(url: str, *, timeout_s: float = 8.0, auth: Optional[tuple[str, str]] = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0, auth=auth)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {url}")


def start_proxy_server(*, proxy_port: int, internal_port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PROXY_PORT"] = str(proxy_port)
    env["JOERN_INTERNAL_HOST"] = "127.0.0.1"
    env["JOERN_INTERNAL_PORT"] = str(internal_port)
    env["PYTHONPATH"] = REPO_ROOT
    proc = subprocess.Popen(
        [os.environ.get("PYTHON_BIN", "python"), "-m", "joern_server.proxy"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    _wait_for_http_ok(f"http://127.0.0.1:{proxy_port}/health")
    return proc


def stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_joern_http_proxy_endpoints_and_forwarding() -> None:
    # Reset class-level capture between tests.
    MockJoernHandler.received_queries = []
    MockJoernHandler.last_upstream_headers = {}

    internal_port = _get_free_port()
    proxy_port = _get_free_port()

    mock = start_mock_joern_server(internal_port)
    proxy_proc = start_proxy_server(proxy_port=proxy_port, internal_port=internal_port)
    try:
        _wait_for_http_ok(f"http://127.0.0.1:{proxy_port}/health")

        r = httpx.get(
            f"http://127.0.0.1:{proxy_port}/version",
            timeout=5,
            headers={"X-Session-Id": "sess-version"},
        )
        assert r.status_code == 200
        assert r.json()["stdout"] == "mock-version"

        r = httpx.post(
            f"http://127.0.0.1:{proxy_port}/query-sync",
            json={"query": "val _health = 1"},
            timeout=5,
            headers={"X-Session-Id": "sess-abc", "X-Request-Id": "req-1"},
        )
        assert r.status_code == 200
        assert r.json()["stdout"] == "1"

        # Explicitly verify raw CPGQL was forwarded to the Joern HTTP layer.
        assert "val _health = 1" in MockJoernHandler.received_queries
        h = MockJoernHandler.last_upstream_headers
        assert h.get("X-Session-Id") == "sess-abc"
        assert h.get("X-Request-Id") == "req-1"
    finally:
        stop_proc(proxy_proc)
        mock.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("NEURALATLAS_RUN_REAL_JOERN_HTTP", "0") != "1",
    reason="Set NEURALATLAS_RUN_REAL_JOERN_HTTP=1 to run against a real Joern HTTP service.",
)
def test_joern_http_query_sync_load_sample_cpg() -> None:
    """
    Real integration test against Joern /query-sync using sample CPG from `samples/c/hello.cpg`.

    Assumes the Joern HTTP server is already running and has `server_tools.sc` imported
    so that `load_cpg(...)` and `get_method_full_name_by_id(...)` are available.
    """

    host = os.getenv("NEURALATLAS_JOERN_HOST", "127.0.0.1")
    port = os.getenv("NEURALATLAS_JOERN_PORT", "8080")
    url = os.getenv("NEURALATLAS_JOERN_HTTP_URL", f"http://{host}:{port}")

    auth_user = os.getenv("NEURALATLAS_JOERN_AUTH_USERNAME", "")
    auth_pass = os.getenv("NEURALATLAS_JOERN_AUTH_PASSWORD", "")
    auth: Optional[tuple[str, str]] = (auth_user, auth_pass) if auth_user and auth_pass else None

    # In-container CPG path (important: Joern runs in a container).
    #
    # Common setup:
    # - running `./deploy/run-joern.sh` mounts repo at `/app`, so `/app/samples/c/hello.cpg` works.
    # - running `docker compose` may require setting NEURALATLAS_JOERN_REPO_MOUNT to match your mount.
    repo_mount = os.getenv("NEURALATLAS_JOERN_REPO_MOUNT", "/app").rstrip("/")
    test_cpg_path = os.getenv(
        "TEST_JOERN_CPG_PATH",
        f"{repo_mount}/samples/c/hello.cpg",
    )

    async def _post(query: str) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{url}/query-sync",
                json={"query": query},
                auth=auth,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()

    load_query = f'load_cpg("{test_cpg_path}")'
    # Call synchronously via asyncio runner for simple test.
    import anyio  # local import to keep top of file minimal

    async def _run() -> tuple[dict, dict]:
        first = await _post(load_query)
        method_id = "107374182400L"
        q2 = f'get_method_full_name_by_id("{method_id}")'
        second = await _post(q2)
        return first, second

    result, result2 = anyio.run(_run)
    stdout = _strip_ansi(result.get("stdout", ""))
    assert "Boolean = true" in stdout or "Loading CPG" in stdout, stdout

    # Stable method id from committed `samples/c/hello.cpg` (see mcp-joern/test_mcp_client.py).
    # Joern returns the bare name as a String: e.g. `val res5: String = "main"`
    stdout2 = _strip_ansi(result2.get("stdout", ""))
    assert '"main"' in stdout2 or "main" in stdout2, stdout2
