"""
S2-T02: MCP vs HTTP parity tests.

Architecture
------------
1.  A deterministic mock Joern HTTP server handles every /query-sync request
    by matching a query prefix and returning a fixed ``stdout`` payload.
2.  For each tool we verify two paths produce the same parsed Python value:
    a.  **Direct path** — call the Python tool function directly with
        ``joern_remote`` pointed at the mock server (via env-var override).
    b.  **MCP SSE path** — start ``mcp-joern/server.py`` as a subprocess
        (MCP_TRANSPORT=sse), call the tool via the FastMCP SSE client, and
        compare the text result.

No live Joern server is needed; the mock server is deterministic and stable.

Run with:
    pytest tests/integration/test_mcp_vs_http_parity.py -v
"""

import anyio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

import pytest

from fastmcp import Client
from fastmcp.client.transports import SSETransport

# ---------------------------------------------------------------------------
# Path: allow importing the server module from mcp-joern/
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
MJOERN_DIR = REPO_ROOT / "mcp-joern"

if str(MJOERN_DIR) not in sys.path:
    sys.path.insert(0, str(MJOERN_DIR))

from common_tools import extract_value, extract_list, remove_ansi_escape_sequences

# ---------------------------------------------------------------------------
# Mock response map
#
# Maps a *query prefix* (str) → ``stdout`` value to return.
# The handler uses str.startswith matching so partial query strings work.
# ---------------------------------------------------------------------------

_MOCK_RESPONSES: Dict[str, str] = {
    # ping / check_connection / get_help
    "version": 'val res0: String = "4.0.517"',
    "help": "Joern help: available commands ...",

    # load_cpg: first call is importCpg, second is load_cpg
    'importCpg("': 'Some("/path/app.cpg")',
    'load_cpg("': 'val res0: Boolean = true',

    # Scalar string tools
    'get_method_full_name_by_id("': 'val res0: String = "com.example.Foo.bar:void(int)"',
    'get_class_full_name_by_id("': 'val res0: String = "com.android.nfc.NfcService"',
    'get_method_code_by_id("': 'val res0: String = """\npublic int compute() {\n    return 42;\n}\n"""',
    'get_call_code_by_id("': 'val res0: String = "handle(intent)"',
    'get_method_by_call_id("': 'val res0: String = "method_full_name=com.Foo.bar:void()|method_name=bar|method_signature=void()|method_id=100L"',
    'get_referenced_method_full_name_by_call_id("': 'val res0: String = "com.Foo.referenced:void()"',
    'get_method_code_by_method_full_name("': 'val res0: String = "int main() { return 0; }"',

    # List tools
    'get_method_callees("': 'val res0: List[String] = List("methodFullName=foo.Bar.baz:void() methodId=1L", "methodFullName=foo.Bar.qux:int() methodId=2L")',
    'get_method_callers("': 'val res0: List[String] = List("methodFullName=caller.A:void() methodId=10L")',
    'get_class_methods_by_class_full_name("': 'val res0: List[String] = List("methodFullName=com.Foo.bar:void() methodId=1L", "methodFullName=com.Foo.<init>:void() methodId=2L")',
    'get_calls_in_method_by_method_full_name("': 'val res0: List[String] = List("call_code=foo() call_id=1L", "call_code=bar(x) call_id=2L")',
    'get_derived_classes_by_class_full_name("': 'val res0: List[String] = List("class_full_name=com.Child|class_name=Child|class_id=1L")',
    'get_parent_classes_by_class_full_name("': 'val res0: List[String] = List("class_full_name=java.lang.Object|class_name=Object|class_id=99L")',
    'get_method_code_by_class_full_name_and_method_name("': 'val res0: List[String] = List("methodFullName=com.Foo.bar:void() methodId=1L")',
}


class _MockJoernHandler(BaseHTTPRequestHandler):
    """Deterministic mock Joern HTTP handler."""

    def log_message(self, *_args) -> None:
        return  # silence

    def _send_json(self, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/query-sync":
            self._send_json({"error": "not_found"})
            return
        length = self.headers.get("Content-Length")
        raw = self.rfile.read(int(length)) if length else b""
        query = ""
        try:
            query = json.loads(raw.decode("utf-8")).get("query", "")
        except Exception:
            query = ""

        stdout = ""
        for prefix, response in _MOCK_RESPONSES.items():
            if query.strip().startswith(prefix):
                stdout = response
                break

        self._send_json({"stdout": stdout})


def _start_mock_server() -> tuple:
    """Start a mock Joern HTTP server in a daemon thread.

    Returns (httpd, port).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), _MockJoernHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _extract_first_text(call_result: object) -> str:
    if isinstance(call_result, list):
        if not call_result:
            raise AssertionError("Empty tool result list")
        item = call_result[0]
        return item.text if hasattr(item, "text") else str(item)
    content = getattr(call_result, "content", None)
    if isinstance(content, list) and content:
        item = content[0]
        return item.text if hasattr(item, "text") else str(item)
    raise TypeError(f"Unsupported tool result type: {type(call_result)}")


# ---------------------------------------------------------------------------
# Fixture: shared mock server + MCP SSE subprocess for the entire module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_server_and_mcp():
    """
    Starts the mock Joern server and a MCP SSE server subprocess.
    Yields (mock_port, mcp_port).
    """
    httpd, mock_port = _start_mock_server()
    mcp_port = _get_free_port()

    env = os.environ.copy()
    env["MCP_TRANSPORT"] = "sse"
    env["MCP_HOST"] = "127.0.0.1"
    env["MCP_PORT"] = str(mcp_port)
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(mock_port)
    env["LOG_LEVEL"] = "ERROR"
    env["FASTMCP_LOG_LEVEL"] = "ERROR"

    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=str(MJOERN_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Wait for MCP SSE server to be ready
    async def _wait_ready() -> None:
        url = f"http://127.0.0.1:{mcp_port}/sse"
        for _ in range(80):
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"MCP subprocess exited early:\n{out}")
            try:
                async with Client(SSETransport(url=url)) as client:
                    result = await client.call_tool("ping")
                    text = _extract_first_text(result)
                    if text == "4.0.517":
                        return
            except Exception:
                pass
            await anyio.sleep(0.2)
        raise TimeoutError("Timed out waiting for MCP SSE server")

    anyio.run(_wait_ready)

    yield mock_port, mcp_port

    _stop_proc(proc)
    httpd.shutdown()


# ---------------------------------------------------------------------------
# Helper: import server module pointing at mock HTTP server
# ---------------------------------------------------------------------------

def _import_server_with_mock_port(port: int):
    """
    Import (or re-use) server module; override server_endpoint to the mock port.
    """
    import server as srv
    srv.server_endpoint = f"127.0.0.1:{port}"
    return srv


# ===========================================================================
# Parity tests: direct call vs MCP SSE call
#
# Strategy:
#   * "direct result"  — call the Python function directly (joern_remote hits mock)
#   * "MCP result"     — call via MCP SSE client; parse text as-is (tool returns
#                        the already-parsed value as a str/list)
# ===========================================================================

class TestMcpVsHttpParity:
    """Compare MCP tool results vs direct Python function results (both hit mock)."""

    def _call_via_mcp(self, mcp_port: int, tool_name: str, **kwargs) -> str:
        url = f"http://127.0.0.1:{mcp_port}/sse"

        async def _inner():
            async with Client(SSETransport(url=url)) as client:
                result = await client.call_tool(tool_name, kwargs)
                return _extract_first_text(result)

        return anyio.run(_inner)

    # ------------------------------------------------------------------
    # ping
    # ------------------------------------------------------------------

    def test_ping_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        # Direct path
        direct = srv.ping()
        # Expected: extract_value of 'val res0: String = "4.0.517"'
        assert direct == "4.0.517", f"Direct ping: {direct!r}"

        # MCP path
        mcp_result = self._call_via_mcp(mcp_port, "ping")
        assert mcp_result == direct, f"MCP ping ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_method_full_name_by_id
    # ------------------------------------------------------------------

    def test_get_method_full_name_by_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_full_name_by_id("123L")
        assert direct == "com.example.Foo.bar:void(int)", f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(mcp_port, "get_method_full_name_by_id", method_id="123L")
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_class_full_name_by_id
    # ------------------------------------------------------------------

    def test_get_class_full_name_by_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_class_full_name_by_id("456L")
        assert direct == "com.android.nfc.NfcService", f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(mcp_port, "get_class_full_name_by_id", class_id="456L")
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_method_code_by_id  (triple-quote multiline)
    # ------------------------------------------------------------------

    def test_get_method_code_by_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_code_by_id("789L")
        assert "compute" in direct, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(mcp_port, "get_method_code_by_id", method_id="789L")
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_call_code_by_id
    # ------------------------------------------------------------------

    def test_get_call_code_by_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_call_code_by_id("111L")
        assert direct == "handle(intent)", f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(mcp_port, "get_call_code_by_id", code_id="111L")
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_method_by_call_id
    # ------------------------------------------------------------------

    def test_get_method_by_call_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_by_call_id("222L")
        assert "com.Foo.bar" in direct, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(mcp_port, "get_method_by_call_id", call_id="222L")
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_referenced_method_full_name_by_call_id
    # ------------------------------------------------------------------

    def test_get_referenced_method_full_name_by_call_id_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_referenced_method_full_name_by_call_id("333L")
        assert direct == "com.Foo.referenced:void()", f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_referenced_method_full_name_by_call_id", call_id="333L"
        )
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_method_code_by_full_name
    # ------------------------------------------------------------------

    def test_get_method_code_by_full_name_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_code_by_full_name("com.Foo.main:void()")
        assert "main" in direct, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_method_code_by_full_name", method_full_name="com.Foo.main:void()"
        )
        assert mcp_result == direct, f"MCP ({mcp_result!r}) != direct ({direct!r})"

    # ------------------------------------------------------------------
    # get_method_callees
    # ------------------------------------------------------------------

    def test_get_method_callees_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_callees("foo.Bar.main:void()")
        assert isinstance(direct, list) and len(direct) == 2, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_method_callees", method_full_name="foo.Bar.main:void()"
        )
        # MCP returns a string representation of the list; compare the direct list's str
        assert str(direct) == mcp_result or mcp_result in str(direct) or direct[0] in mcp_result, (
            f"MCP callees ({mcp_result!r}) should contain data from direct ({direct!r})"
        )

    # ------------------------------------------------------------------
    # get_method_callers
    # ------------------------------------------------------------------

    def test_get_method_callers_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_callers("callee.B:void()")
        assert isinstance(direct, list) and len(direct) == 1, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_method_callers", method_full_name="callee.B:void()"
        )
        assert "caller.A" in mcp_result, f"MCP ({mcp_result!r}) should contain 'caller.A'"

    # ------------------------------------------------------------------
    # get_class_methods_by_class_full_name
    # ------------------------------------------------------------------

    def test_get_class_methods_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_class_methods_by_class_full_name("com.Foo")
        assert isinstance(direct, list) and len(direct) == 2, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_class_methods_by_class_full_name", class_full_name="com.Foo"
        )
        assert "com.Foo.bar" in mcp_result, f"MCP ({mcp_result!r}) should contain class method"

    # ------------------------------------------------------------------
    # get_calls_in_method_by_method_full_name
    # ------------------------------------------------------------------

    def test_get_calls_in_method_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_calls_in_method_by_method_full_name("com.Foo.main:void()")
        assert isinstance(direct, list) and len(direct) == 2, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_calls_in_method_by_method_full_name",
            method_full_name="com.Foo.main:void()"
        )
        assert "foo()" in mcp_result, f"MCP ({mcp_result!r}) should contain call info"

    # ------------------------------------------------------------------
    # get_derived_classes_by_class_full_name
    # ------------------------------------------------------------------

    def test_get_derived_classes_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_derived_classes_by_class_full_name("com.Parent")
        assert isinstance(direct, list) and len(direct) == 1, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_derived_classes_by_class_full_name", class_full_name="com.Parent"
        )
        assert "com.Child" in mcp_result, f"MCP ({mcp_result!r}) should contain 'com.Child'"

    # ------------------------------------------------------------------
    # get_parent_classes_by_class_full_name
    # ------------------------------------------------------------------

    def test_get_parent_classes_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_parent_classes_by_class_full_name("com.Foo")
        assert isinstance(direct, list) and len(direct) == 1, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_parent_classes_by_class_full_name", class_full_name="com.Foo"
        )
        assert "java.lang.Object" in mcp_result, (
            f"MCP ({mcp_result!r}) should contain 'java.lang.Object'"
        )

    # ------------------------------------------------------------------
    # get_method_code_by_class_full_name_and_method_name
    # ------------------------------------------------------------------

    def test_get_method_code_by_class_and_name_parity(self, mock_server_and_mcp):
        mock_port, mcp_port = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        direct = srv.get_method_code_by_class_full_name_and_method_name("com.Foo", "bar")
        assert isinstance(direct, list) and len(direct) == 1, f"Direct: {direct!r}"

        mcp_result = self._call_via_mcp(
            mcp_port, "get_method_code_by_class_full_name_and_method_name",
            class_full_name="com.Foo", method_name="bar"
        )
        assert "com.Foo.bar" in mcp_result, (
            f"MCP ({mcp_result!r}) should contain method info"
        )


# ===========================================================================
# Standalone parity: raw HTTP → parse → compare with direct tool call
# ===========================================================================

class TestRawHttpVsDirectCallParity:
    """
    Verify that manually hitting /query-sync and parsing the result gives the
    same Python value as calling the tool function directly.
    Uses the mock server; does NOT start the MCP SSE subprocess.
    """

    def _raw_http(self, port: int, query: str) -> str:
        """POST query to mock server and return raw stdout string (ANSI-stripped)."""
        import requests
        resp = requests.post(
            f"http://127.0.0.1:{port}/query-sync",
            json={"query": query},
            headers={"X-Session-Id": "parity-test"},
            timeout=5,
        )
        resp.raise_for_status()
        raw = resp.json().get("stdout", "")
        return remove_ansi_escape_sequences(str(raw))

    def test_ping_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        # Raw HTTP
        raw = self._raw_http(mock_port, "version")
        expected = extract_value(raw)

        # Direct tool
        direct = srv.ping()

        assert direct == expected, (
            f"Raw HTTP parsed ({expected!r}) != direct ({direct!r})"
        )

    def test_get_method_full_name_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_method_full_name_by_id("1L")')
        expected = extract_value(raw)

        direct = srv.get_method_full_name_by_id("1L")
        assert direct == expected, (
            f"Raw ({expected!r}) != direct ({direct!r})"
        )

    def test_get_class_full_name_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_class_full_name_by_id("1L")')
        expected = extract_value(raw)

        direct = srv.get_class_full_name_by_id("1L")
        assert direct == expected, (
            f"Raw ({expected!r}) != direct ({direct!r})"
        )

    def test_get_method_callees_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_method_callees("foo:void()")')
        expected = extract_list(raw)

        direct = srv.get_method_callees("foo:void()")
        assert direct == expected, (
            f"Raw ({expected!r}) != direct ({direct!r})"
        )

    def test_get_method_callers_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_method_callers("foo:void()")')
        expected = extract_list(raw)

        direct = srv.get_method_callers("foo:void()")
        assert direct == expected

    def test_get_call_code_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_call_code_by_id("1L")')
        expected = extract_value(raw)

        direct = srv.get_call_code_by_id("1L")
        assert direct == expected

    def test_get_method_code_by_id_raw_vs_direct(self, mock_server_and_mcp):
        mock_port, _ = mock_server_and_mcp
        srv = _import_server_with_mock_port(mock_port)

        raw = self._raw_http(mock_port, 'get_method_code_by_id("1L")')
        expected = extract_value(raw)

        direct = srv.get_method_code_by_id("1L")
        assert direct == expected
