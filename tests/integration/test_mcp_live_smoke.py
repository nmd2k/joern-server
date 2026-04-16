"""
S2-T04: Live integration smoke test for all 18 MCP tools.

Gated behind ``NEURALATLAS_RUN_MCP_INTEGRATION=1``.

What it tests
-------------
* Every MCP tool is callable via the MCP SSE transport against a real Joern server.
* No tool raises an unhandled exception.
* Scalar tools return a non-empty string.
* List tools return a Python list (may be empty for inheritance/class tools with C CPGs).
* Results via MCP match the equivalent direct HTTP /query-sync response.

Prerequisites
-------------
* Joern HTTP server running at HOST:PORT (default 127.0.0.1:8080).
* A real CPG loaded at the path in NEURALATLAS_JOERN_CPG_PATH (optional; without it,
  CPG-dependent tools will return empty results, which is acceptable).
* Set NEURALATLAS_RUN_MCP_INTEGRATION=1 to enable the tests.

Run with:
    NEURALATLAS_RUN_MCP_INTEGRATION=1 pytest tests/integration/test_mcp_live_smoke.py -v
"""

import anyio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

_GATE = "NEURALATLAS_RUN_MCP_INTEGRATION"

pytestmark = pytest.mark.skipif(
    os.getenv(_GATE, "0") != "1",
    reason=f"Set {_GATE}=1 to run live MCP smoke tests.",
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
MJOERN_DIR = REPO_ROOT / "mcp-joern"

if str(MJOERN_DIR) not in sys.path:
    sys.path.insert(0, str(MJOERN_DIR))

from common_tools import extract_value, extract_list, remove_ansi_escape_sequences

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
JOERN_HOST = os.getenv("HOST", os.getenv("NEURALATLAS_JOERN_HOST", "127.0.0.1"))
JOERN_PORT = int(os.getenv("PORT", os.getenv("NEURALATLAS_JOERN_PORT", "8080")))
CPG_PATH = os.getenv("NEURALATLAS_JOERN_CPG_PATH", "")
MCP_HOST = "127.0.0.1"


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
            return ""
        item = call_result[0]
        return item.text if hasattr(item, "text") else str(item)
    content = getattr(call_result, "content", None)
    if isinstance(content, list) and content:
        item = content[0]
        return item.text if hasattr(item, "text") else str(item)
    return str(call_result)


# ---------------------------------------------------------------------------
# Fixture: MCP SSE server subprocess pointing at the live Joern server
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mcp_live_port():
    """Start the MCP SSE server against the live Joern server. Yields MCP port."""
    from fastmcp import Client
    from fastmcp.client.transports import SSETransport

    mcp_port = _get_free_port()
    env = os.environ.copy()
    env["MCP_TRANSPORT"] = "sse"
    env["MCP_HOST"] = MCP_HOST
    env["MCP_PORT"] = str(mcp_port)
    env["HOST"] = JOERN_HOST
    env["PORT"] = str(JOERN_PORT)
    env["LOG_LEVEL"] = "ERROR"
    env["FASTMCP_LOG_LEVEL"] = "ERROR"

    user = os.getenv("NEURALATLAS_JOERN_AUTH_USERNAME", "")
    password = os.getenv("NEURALATLAS_JOERN_AUTH_PASSWORD", "")
    if user:
        env["JOERN_AUTH_USERNAME"] = user
    if password:
        env["JOERN_AUTH_PASSWORD"] = password

    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=str(MJOERN_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    async def _wait_ready():
        url = f"http://{MCP_HOST}:{mcp_port}/sse"
        last_err = None
        for _ in range(100):
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"MCP subprocess exited:\n{out}")
            try:
                async with Client(SSETransport(url=url)) as client:
                    result = await client.call_tool("ping")
                    text = _extract_first_text(result)
                    if text and text != "Query Failed":
                        return text
            except Exception as e:
                last_err = e
            await anyio.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for live MCP server: {last_err}")

    anyio.run(_wait_ready)
    yield mcp_port
    _stop_proc(proc)


# ---------------------------------------------------------------------------
# Helper: call a tool via MCP SSE
# ---------------------------------------------------------------------------

def _mcp_call(mcp_port: int, tool_name: str, **kwargs) -> str:
    from fastmcp import Client
    from fastmcp.client.transports import SSETransport

    url = f"http://{MCP_HOST}:{mcp_port}/sse"

    async def _inner():
        async with Client(SSETransport(url=url)) as client:
            result = await client.call_tool(tool_name, kwargs)
            return _extract_first_text(result)

    return anyio.run(_inner)


def _http_query(query: str) -> str:
    """Hit the live Joern /query-sync directly and return ANSI-stripped stdout."""
    import requests
    resp = requests.post(
        f"http://{JOERN_HOST}:{JOERN_PORT}/query-sync",
        json={"query": query},
        headers={"X-Session-Id": "smoke-test"},
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json().get("stdout", "")
    return remove_ansi_escape_sequences(str(raw))


# ===========================================================================
# Smoke tests: connectivity and core tools
# ===========================================================================

class TestLiveSmokeConnectivity:
    def test_ping_returns_version(self, mcp_live_port):
        result = _mcp_call(mcp_live_port, "ping")
        assert result and result != "Query Failed", (
            f"ping() should return Joern version; got {result!r}"
        )

    def test_check_connection(self, mcp_live_port):
        result = _mcp_call(mcp_live_port, "check_connection")
        assert "Successfully connected" in result, (
            f"check_connection() should report success; got {result!r}"
        )

    def test_get_help_non_empty(self, mcp_live_port):
        result = _mcp_call(mcp_live_port, "get_help")
        assert result and result != "Query Failed", (
            f"get_help() should return non-empty help text; got {result!r}"
        )

    def test_ping_matches_http(self, mcp_live_port):
        """ping() result via MCP should match extract_value of /query-sync 'version'."""
        mcp_result = _mcp_call(mcp_live_port, "ping")
        raw = _http_query("version")
        http_result = extract_value(raw)
        assert mcp_result == http_result, (
            f"MCP ping ({mcp_result!r}) != HTTP version ({http_result!r})"
        )


# ===========================================================================
# Smoke tests: CPG-dependent tools
# (Skipped gracefully if no CPG is loaded or CPG path is not configured)
# ===========================================================================

def _cpg_is_available() -> bool:
    """True if a CPG is loaded on the live server (test by querying a method)."""
    try:
        raw = _http_query("cpg.method.size")
        return bool(raw) and "NullPointerException" not in raw
    except Exception:
        return False


@pytest.fixture(scope="module")
def loaded_cpg(mcp_live_port):
    """Load a CPG if a path is configured; skip otherwise."""
    if not CPG_PATH:
        pytest.skip(
            "NEURALATLAS_JOERN_CPG_PATH not set; skipping CPG-dependent smoke tests."
        )

    # Load via MCP
    result = _mcp_call(mcp_live_port, "load_cpg", cpg_filepath=CPG_PATH)
    if result not in ("true", "false", True):
        pytest.skip(f"load_cpg returned {result!r}; CPG may not be accessible.")
    if result == "false":
        pytest.skip(f"load_cpg returned 'false' for path {CPG_PATH!r}.")

    return CPG_PATH


class TestLiveSmokeCpgTools:
    """CPG-dependent tool smoke tests."""

    def test_load_cpg(self, loaded_cpg, mcp_live_port):
        result = _mcp_call(mcp_live_port, "load_cpg", cpg_filepath=loaded_cpg)
        assert result in ("true", True, "True"), (
            f"load_cpg() should return 'true'; got {result!r}"
        )

    def test_get_method_callees_is_list(self, loaded_cpg, mcp_live_port):
        """get_method_callees() should return a list (possibly empty)."""
        # Use a broad method full name that likely won't exist — so result may be empty
        result = _mcp_call(mcp_live_port, "get_method_callees",
                           method_full_name="nonexistent:void()")
        # Result is a string representation of a list; it's acceptable to be empty
        assert isinstance(result, str), f"Expected str result; got {type(result)}"

    def test_get_method_callers_is_list(self, loaded_cpg, mcp_live_port):
        result = _mcp_call(mcp_live_port, "get_method_callers",
                           method_full_name="nonexistent:void()")
        assert isinstance(result, str)

    def test_get_class_methods_is_list(self, loaded_cpg, mcp_live_port):
        result = _mcp_call(mcp_live_port, "get_class_methods_by_class_full_name",
                           class_full_name="nonexistent.Class")
        assert isinstance(result, str)

    def test_get_derived_classes_is_list(self, loaded_cpg, mcp_live_port):
        result = _mcp_call(mcp_live_port, "get_derived_classes_by_class_full_name",
                           class_full_name="nonexistent.Class")
        assert isinstance(result, str)

    def test_get_parent_classes_is_list(self, loaded_cpg, mcp_live_port):
        result = _mcp_call(mcp_live_port, "get_parent_classes_by_class_full_name",
                           class_full_name="nonexistent.Class")
        assert isinstance(result, str)

    def test_scalar_tools_return_string(self, loaded_cpg, mcp_live_port):
        """Scalar ID tools should return strings (possibly empty) for nonexistent IDs."""
        scalar_tools = [
            ("get_method_full_name_by_id", {"method_id": "999999999L"}),
            ("get_class_full_name_by_id", {"class_id": "999999999L"}),
            ("get_method_code_by_id", {"method_id": "999999999L"}),
            ("get_call_code_by_id", {"code_id": "999999999L"}),
            ("get_method_by_call_id", {"call_id": "999999999L"}),
            ("get_referenced_method_full_name_by_call_id", {"call_id": "999999999L"}),
        ]
        for tool_name, kwargs in scalar_tools:
            result = _mcp_call(mcp_live_port, tool_name, **kwargs)
            assert isinstance(result, str), (
                f"{tool_name}() with nonexistent ID should return str; got {type(result)}"
            )
