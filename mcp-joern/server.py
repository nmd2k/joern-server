import json
import sys
import os
import re
import uuid
from typing import Dict, Any, Optional, Tuple

import requests
from dotenv import load_dotenv
from common_tools import *

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
# Load .env from this package directory (not the client cwd).
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))


def load_server_config(config_path: str) -> Dict[str, Any]:
    """load server config from config file"""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config.get("mcpServers", {}).get("joern", {}).get("config") or {}
    except FileNotFoundError:
        print(f"config file {config_path} not exist", file=sys.stderr)
        return {}
    except json.JSONDecodeError:
        print(f"config file {config_path} format error", file=sys.stderr)
        return {}


joern_config = load_server_config(os.path.join(SCRIPT_DIR, "mcp_settings.json"))

# MCP transport/HTTP settings (transport connects AI clients; HOST/PORT below still refer to the Joern HTTP target)
MCP_TRANSPORT = (os.getenv("MCP_TRANSPORT") or "stdio").lower()
MCP_HOST = os.getenv("MCP_HOST") or "0.0.0.0"
MCP_PORT = int(os.getenv("MCP_PORT") or "9000")

_host = os.getenv("HOST") or joern_config.get("host") or "127.0.0.1"
_port = os.getenv("PORT") or joern_config.get("port") or "8080"
server_endpoint = f"{_host}:{_port}"
log_level = os.getenv("LOG_LEVEL") or joern_config.get("log_level", "ERROR")
# FastMCP has changed how log levels are configured across versions.
# Use FASTMCP_LOG_LEVEL (and avoid passing log_level into the constructor).
os.environ.setdefault("FASTMCP_LOG_LEVEL", str(log_level))
# Override (not setdefault): test/CI environments may already set these defaults.
os.environ["FASTMCP_LOG_LEVEL"] = str(log_level)
os.environ["FASTMCP_HOST"] = str(MCP_HOST)
os.environ["FASTMCP_PORT"] = str(MCP_PORT)
from fastmcp import FastMCP
joern_mcp = FastMCP("joern-mcp")
# Never print to stdout here: stdio transport uses stdout for MCP JSON-RPC.

_user = os.getenv("JOERN_AUTH_USERNAME") or os.getenv("USER_NAME")
_pass = os.getenv("JOERN_AUTH_PASSWORD") or os.getenv("PASSWORD")
basic_auth: Optional[Tuple[str, str]] = (
    (_user, _pass) if _user and _pass else None
)
_to = joern_config.get("timeout", 300)
timeout = int(os.getenv("TIMEOUT") or _to)
JOERN_SESSION_ID = os.getenv("JOERN_SESSION_ID") or f"mcp-joern-{uuid.uuid4().hex[:12]}"
_LAST_CPG_FILEPATH: Optional[str] = None


def _extract_load_cpg_path(query: str) -> Optional[str]:
    m = re.search(r'load_cpg\("((?:[^"\\]|\\.)*)"\)', query)
    if not m:
        return None
    raw = m.group(1)
    return bytes(raw, "utf-8").decode("unicode_escape")


def _looks_like_null_cpg_error(result: Dict[str, Any]) -> bool:
    txt = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    return (
        "NullPointerException" in txt
        and "wrappedCpg()" in txt
        and "CpgNodeStarters" in txt
    )

def joern_remote(query):
    """
    Execute remote query and return results
    
    Parameters:
    query -- The query string to execute
    
    Returns:
    Returns the server response stdout content on success
    Returns None on failure, error message will be output to stderr
    """
    global _LAST_CPG_FILEPATH
    data = {"query": query}
    headers = {
        "Content-Type": "application/json",
        "X-Session-Id": JOERN_SESSION_ID,
    }

    try:
        load_path = _extract_load_cpg_path(query)
        if load_path:
            _LAST_CPG_FILEPATH = load_path

        post_kwargs: Dict[str, Any] = {
            "data": json.dumps(data),
            "headers": headers,
            "timeout": timeout,
        }
        if basic_auth is not None:
            post_kwargs["auth"] = basic_auth
        response = requests.post(
            f"http://{server_endpoint}/query-sync",
            **post_kwargs,
        )
        response.raise_for_status()  
        
        result = response.json()
        if isinstance(result, dict) and _looks_like_null_cpg_error(result) and _LAST_CPG_FILEPATH:
            # Sticky recovery: if active CPG vanished, reload once in this session and retry.
            recovery_query = f'load_cpg("{_LAST_CPG_FILEPATH}")'
            recovery_data = {"query": recovery_query}
            recovery_kwargs: Dict[str, Any] = {
                "data": json.dumps(recovery_data),
                "headers": headers,
                "timeout": timeout,
            }
            if basic_auth is not None:
                recovery_kwargs["auth"] = basic_auth
            recovery = requests.post(
                f"http://{server_endpoint}/query-sync",
                **recovery_kwargs,
            )
            recovery.raise_for_status()

            retry = requests.post(
                f"http://{server_endpoint}/query-sync",
                **post_kwargs,
            )
            retry.raise_for_status()
            retry_result = retry.json()
            return remove_ansi_escape_sequences(str(retry_result.get("stdout", "")))

        return remove_ansi_escape_sequences(str(result.get("stdout", "")))
        
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Request Error: {str(e)}\n")
    except json.JSONDecodeError:
        sys.stderr.write("Error: Invalid JSON response\n")
    
    return None


@joern_mcp.tool()
def get_help():
    """Get help information from joern server"""
    response = joern_remote('help')
    if response:
        return response
    else:
        return 'Query Failed'


@joern_mcp.tool()
def check_connection() -> str:
    """Check if the Joern MCP plugin is running"""
    try:
        metadata = extract_value(joern_remote("version"))
        if not metadata:
            return f"Failed to connect to Joern MCP! Make sure the Joern MCP server is running."
        return f"Successfully connected to Joern MCP, joern server version is {metadata}"
    except Exception as e:
        return f"Failed to connect to Joern MCP! Make sure the Joern MCP server is running."

GENERATED_PY = os.path.join(SCRIPT_DIR, "server_tools.py")
def generate():
    """Generate and execute additional server tools from server_tools.py file.
    
    This function reads the content of server_tools.py and executes it to add
    more functionality to the server.
    """
    with open(GENERATED_PY, "r") as f:
        code = f.read()
        exec(compile(code, GENERATED_PY, "exec"), globals())

generate()


def main():
    """Start the MCP server using stdio transport.
    
    This is the main entry point for running the Joern MCP server.
    """
    transport = "sse" if MCP_TRANSPORT == "sse" else "stdio"
    joern_mcp.run(transport=transport)

if __name__ == "__main__":
    main()
