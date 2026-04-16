"""MCP client integration tests."""

import anyio
import json
import os
import runpy
import signal
import socket
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fastmcp import Client
from fastmcp.client.transports import SSETransport


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("NEURALATLAS_RUN_MCP_INTEGRATION", "0") != "1",
    reason="Set NEURALATLAS_RUN_MCP_INTEGRATION=1 to run MCP/Joern integration.",
)
def test_mcp_client_script() -> None:
    """
    Runs the original MCP integration script:
      `mcp-joern/test_mcp_client.py`

    This requires a real Joern HTTP server reachable at the configured `HOST`/`PORT`
    (or whatever mcp-joern/server.py defaults to).
    """

    # Allow overriding the Joern server target for the MCP server subprocess.
    # mcp-joern/server.py reads HOST/PORT and optional auth vars.
    host = os.getenv("NEURALATLAS_JOERN_HOST", "127.0.0.1")
    # Unified service exposes HTTP on 8080 by default; keep env override for custom deployments.
    port = os.getenv("NEURALATLAS_JOERN_PORT", "8080")
    os.environ["HOST"] = host
    os.environ["PORT"] = port

    # mcp-joern/test_mcp_client.py loads `samples/c/hello.cpg` via the Joern server's view
    # of the filesystem. When using `./deploy/run-joern.sh`, the repo is usually mounted at `/app`.
    # For other setups, set NEURALATLAS_JOERN_REPO_MOUNT to the in-container repo path.
    repo_mount = os.getenv("NEURALATLAS_JOERN_REPO_MOUNT", "/app").rstrip("/")
    os.environ.setdefault("JOERN_REPO_MOUNT", repo_mount)
    os.environ.setdefault("TEST_JOERN_CPG_PATH", f"{repo_mount}/samples/c/hello.cpg")

    user = os.getenv("NEURALATLAS_JOERN_AUTH_USERNAME", "")
    password = os.getenv("NEURALATLAS_JOERN_AUTH_PASSWORD", "")
    if user and password:
        os.environ["JOERN_AUTH_USERNAME"] = user
        os.environ["JOERN_AUTH_PASSWORD"] = password

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "mcp-joern" / "test_mcp_client.py"
    runpy.run_path(str(script_path), run_name="__main__")


def _get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _extract_first_text(call_result: object) -> str:
    if isinstance(call_result, list):
        if not call_result:
            raise AssertionError("Empty tool result list")
        return call_result[0].text  # type: ignore[attr-defined]

    content = getattr(call_result, "content", None)
    if isinstance(content, list) and content:
        return content[0].text  # type: ignore[attr-defined]

    raise TypeError(f"Unsupported tool result type: {type(call_result)}")


class MockJoernHTTPHandler(BaseHTTPRequestHandler):
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

        if query.strip() == "version":
            self._send_json(HTTPStatus.OK, {"stdout": "mock-version"})
        else:
            self._send_json(HTTPStatus.OK, {"stdout": "ok"})


def start_mock_joern_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), MockJoernHTTPHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_mcp_sse_ping_unit() -> None:
    """
    Lightweight unit test for MCP-over-SSE transport.

    It uses a mocked Joern HTTP server that responds to `query-sync`
    so `mcp-joern/server.py` can execute the `ping` tool.
    """

    internal_port = _get_free_port()
    mcp_port = _get_free_port()

    mock = start_mock_joern_server(internal_port)
    env = os.environ.copy()
    env["MCP_TRANSPORT"] = "sse"
    env["MCP_HOST"] = "127.0.0.1"
    env["MCP_PORT"] = str(mcp_port)
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(internal_port)
    env["LOG_LEVEL"] = "ERROR"
    env["FASTMCP_LOG_LEVEL"] = "ERROR"

    repo_root = Path(__file__).resolve().parents[2]
    mcp_joern_dir = repo_root / "mcp-joern"

    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=mcp_joern_dir.as_posix(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    async def _wait_for_ping() -> None:
        url = f"http://127.0.0.1:{mcp_port}/sse"
        last_err: Exception | None = None
        for _ in range(80):
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout is not None else ""
                raise RuntimeError(f"MCP subprocess exited early. Output:\n{out}")
            try:
                async with Client(SSETransport(url=url)) as client:
                    out = await client.call_tool("ping")
                    if _extract_first_text(out) == "mock-version":
                        return
            except Exception as e:  # noqa: BLE001
                last_err = e
            await anyio.sleep(0.2)
        raise TimeoutError(f"Timed out waiting for MCP SSE ping: {last_err}")

    try:
        anyio.run(_wait_for_ping)
    finally:
        stop_proc(proc)
        mock.shutdown()
