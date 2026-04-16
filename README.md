# Joern Server

A comprehensive HTTP proxy and MCP (Model Context Protocol) server for Joern CPG (Code Property Graph) analysis. Enables AI agents and IDE tools to query code structure and detect vulnerabilities through standardized interfaces.

## Architecture

```
AI Agent / IDE Client
      │  MCP over SSE or stdio
      ▼
  mcp-joern/server.py  (FastMCP)
      │  HTTP tool calls
      ▼
  mcp-joern/server_tools.py  (Python wrappers)
      │  POST /query-sync
      ▼
  joern_server/proxy.py  (HTTP Proxy)
      │  CPGQL on loaded CPG
      ▼
  Joern HTTP Server (Docker)
      │
      ▼
  /workspace/cpg-out/<sample_id>
```

## Project Structure

```
joern-server/
├── joern_server/           # Core HTTP proxy and client library
│   ├── proxy.py            # HTTP proxy with session affinity
│   ├── client.py           # JoernHTTPQueryExecutor client
│   └── __init__.py
├── mcp-joern/              # MCP server implementation
│   ├── server.py           # MCP server entry point (FastMCP, SSE + stdio)
│   ├── server_tools.py     # Python tool wrappers
│   ├── server_tools.sc     # Scala CPGQL helpers for Joern
│   ├── common_tools.py     # Shared HTTP utilities and parsers
│   ├── requirements.txt    # Python dependencies
│   └── tests/              # Legacy tests (migrating to tests/)
├── tests/                  # Consolidated test suite
│   ├── unit/               # Unit tests with mocks
│   │   ├── test_client_affinity.py
│   │   ├── test_sticky_routing.py
│   │   ├── test_joern_tool_dispatch.py
│   │   └── test_mcp_common_tools.py
│   ├── integration/        # Integration tests
│   │   ├── test_joern_http_proxy.py
│   │   ├── test_mcp_client_integration.py
│   │   └── test_mcp_functional.py
│   └── stress/             # Load and stress tests
│       └── test_joern_live_stress.py
├── deploy/                 # Deployment configurations
│   ├── docker-compose.yml
│   └── run-joern.sh
├── pytest.ini              # Pytest configuration
└── README.md
```

## Quick Start

### Option A: Docker Compose (Recommended)

```bash
# Copy and configure environment
cp deploy/.env.example deploy/.env
# Edit deploy/.env (set JOERN_SERVER_AUTH_PASSWORD at minimum)

# Start all services (Joern + Proxy + MCP-over-SSE)
docker compose -f deploy/docker-compose.yml up -d

# MCP SSE endpoint: http://127.0.0.1:9000/sse
# Proxy endpoint: http://127.0.0.1:8080
```

### Option B: Local Development

```bash
# Start Joern with MCP helpers
./deploy/run-joern.sh

# Or start just the proxy (requires external Joern)
cd joern_server
python -m joern_server.proxy
```

### Option C: Scaled Deployment (Multiple Replicas)

```bash
# Multiple Joern replicas behind HAProxy with sticky sessions
docker compose -f deploy/docker-compose.haproxy-scale.yml up -d --scale joern=4
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Joern server host |
| `PORT` | `16162` | Joern server port |
| `USER_NAME` | `joern` | HTTP basic auth username |
| `PASSWORD` | `joern` | HTTP basic auth password |
| `LOG_LEVEL` | `ERROR` | Python log level |
| `TIMEOUT` | `1800` | Request timeout (seconds) |
| `PROXY_PORT` | `8080` | Proxy listen port |
| `MCP_PORT` | `9000` | MCP SSE port |

## Testing

### Run All Tests

```bash
# All tests (skips integration by default)
pytest

# Include integration tests
pytest -m integration

# Exclude stress tests
pytest -m "not stress"
```

### Run Specific Test Categories

```bash
# Unit tests only (no external dependencies)
pytest tests/unit/ -v

# Integration tests (requires running services)
pytest tests/integration/ -v -m integration

# Stress tests (production validation)
pytest tests/stress/ -v -m integration
```

### Run Individual Test Files

```bash
# Client affinity tests
pytest tests/unit/test_client_affinity.py -v

# MCP tool parsers
pytest tests/unit/test_mcp_common_tools.py -v

# HTTP proxy forwarding
pytest tests/integration/test_joern_http_proxy.py -v

# Full MCP functional test
pytest tests/integration/test_mcp_functional.py -v -- -m integration
```

### Test Coverage Report

```bash
pytest --cov=joern_server --cov=mcp-joern --cov-report=html
```

## MCP Tool Reference

| Tool | Description |
|------|-------------|
| `ping` | Check Joern server version / connectivity |
| `check_connection` | Full connection verification |
| `get_help` | List available tools |
| `load_cpg` | Load a CPG into Joern via `importCpg()` |
| `get_method_callees` | Methods called by a given method |
| `get_method_callers` | Methods that call a given method |
| `get_method_code_by_full_name` | Source code of a method |
| `get_method_code_by_id` | Source code by method node ID |
| `get_method_full_name_by_id` | Full name from node ID |
| `get_calls_in_method_by_method_full_name` | Call sites inside a method |
| `get_call_code_by_id` | Code snippet for a call node |
| `get_method_by_call_id` | Enclosing method for a call node |
| `get_referenced_method_full_name_by_call_id` | Callee full name for a call node |
| `get_class_methods_by_class_full_name` | All methods of a class/typeDecl |
| `get_method_code_by_class_full_name_and_method_name` | Method code by class + method name |
| `get_class_full_name_by_id` | Class full name from node ID |
| `get_derived_classes_by_class_full_name` | Subclasses of a type |
| `get_parent_classes_by_class_full_name` | Supertypes of a type |

## IDE Integration

### Cline / Cursor MCP Settings

```json
{
  "mcpServers": {
    "joern": {
      "command": "python",
      "args": ["-m", "mcp-joern.server"],
      "env": {
        "HOST": "127.0.0.1",
        "PORT": "8080"
      }
    }
  }
}
```

See `mcp-joern/sample_cline_mcp_settings.json` for a ready-to-paste configuration.

## Development

### Setting Up Development Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r mcp-joern/requirements.txt
```

### Regenerating Tool Schemas

After editing `server_tools.sc`, regenerate the Python wrappers:

```bash
cd mcp-joern
python scripts/export_tool_schemas.py
```

## Session Affinity

The proxy supports sticky sessions via `X-Session-Id` header. This ensures that all queries from a logical session are routed to the same Joern backend, maintaining CPG state consistency.

```python
from joern_server.client import JoernHTTPQueryExecutor

# Create executor with session affinity
executor = JoernHTTPQueryExecutor(
    "http://127.0.0.1:8080",
    session_id="my-analysis-session",
    retries=3,
    timeout=120.0
)

# All queries in this session hit the same backend
result = executor.execute("version")
result = executor.execute('cpg.method.name.l')
```

## References

- [Joern HTTP Server Docs](https://docs.joern.io/server/)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- Upstream fork: [sfncat/mcp-joern](https://github.com/sfncat/mcp-joern)

## License

MIT License - see LICENSE file for details.
