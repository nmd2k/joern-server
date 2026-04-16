# mcp-joern — Joern MCP Server

MCP (Model Context Protocol) server that exposes Joern CPG analysis tools over SSE or stdio transports. Used by the NeuralAtlas agent loop to query Code Property Graphs during vulnerability analysis.

## Architecture

```
AI Agent / IDE Client
      │  MCP over SSE or stdio
      ▼
  server.py  (FastMCP)
      │  HTTP tool calls
      ▼
  server_tools.py  (Python wrappers)
      │  POST /query-sync
      ▼
  Joern HTTP server  (proxy.py inside Docker)
      │  CPGQL on loaded CPG
      ▼
  /workspace/cpg-out/<sample_id>
```

## Project Structure

```
mcp-joern/
├── server.py                  # MCP server entry point (FastMCP, SSE + stdio)
├── server_tools.py            # Python tool wrappers (auto-generated from server_tools.sc)
├── server_tools.sc            # Scala CPGQL helpers loaded into Joern at startup
├── common_tools.py            # Shared HTTP utilities (joern_remote, extract_value)
├── pyproject.toml             # Python dependencies (managed by uv or pip)
├── environment.yml            # Conda environment (alternative to uv)
├── .env.example               # Environment variable template
├── mcp_settings.json          # Example MCP server config for IDE integration
├── sample_cline_mcp_settings.json  # Cline-specific MCP settings example
├── prompts_en.md              # Example prompts for AI clients
├── scripts/
│   └── export_tool_schemas.py # Regenerates tool schema bundle for agent prompts
└── tests/
    ├── test_tools.py          # Legacy unit tests (migrated to ../tests/)
    ├── test_mcp_client.py     # Legacy integration test (migrated to ../tests/)
    ├── test_mcp_functional.py # Legacy functional test (migrated to ../tests/)
    └── samples/               # Sample CPGs and source files for tests

**Note:** Tests have been consolidated into the main `../tests/` directory.
See the main README.md for the current test structure.
```

## Setup
```bash
cd mcp-joern
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Environment Variables

Copy the example and fill in your Joern connection details:

```bash
cp mcp-joern/.env.example mcp-joern/.env
```


| Variable    | Default     | Description               |
| ----------- | ----------- | ------------------------- |
| `HOST`      | `127.0.0.1` | Joern server host         |
| `PORT`      | `16162`     | Joern server port         |
| `USER_NAME` | `joern`     | HTTP basic auth username  |
| `PASSWORD`  | `joern`     | HTTP basic auth password  |
| `LOG_LEVEL` | `ERROR`     | Python log level          |
| `TIMEOUT`   | `1800`      | Request timeout (seconds) |


## Starting the Joern backend

**Docker (recommended)** — starts Joern + proxy + MCP-over-SSE together:

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env (set JOERN_SERVER_AUTH_PASSWORD at minimum)
docker compose -f deploy/docker-compose.yml up -d
```

MCP SSE endpoint: `http://127.0.0.1:${MCP_PUBLISH_PORT:-9000}/sse`

**Local / one-liner** (imports `server_tools.sc`):

```bash
./deploy/run-joern.sh
```

**Scaled (multiple replicas behind HAProxy)**:

```bash
docker compose -f deploy/docker-compose.haproxy-scale.yml up -d --scale joern=4
```

## Running as stdio server (IDE integration)

Point your MCP client at the server script:

```bash
cd mcp-joern && uv run python server.py
```

See `sample_cline_mcp_settings.json` for a ready-to-paste Cline configuration.

## Testing

**Note:** Tests have been consolidated into the main `../tests/` directory.
See the main README.md for the complete testing guide.

### Running Tests from Repo Root

```bash
# Unit tests (no external dependencies)
pytest tests/unit/ -v

# Integration tests (requires running services)
pytest tests/integration/ -v -m integration

# All tests
pytest
```

### Legacy Test Scripts

The scripts in `mcp-joern/tests/` are deprecated. Use the consolidated
test suite in `../tests/` instead.

### Functional test (all 18 MCP tools)

```bash
python tests/integration/test_mcp_functional.py \
    --http-url http://localhost:8080 \
    --mcp-url  http://localhost:9000/sse \
    --cpg-dir  /datadrive/data/raw/sven/file \
    --report   mcp_functional_report.md
```

Options:

- `--cpg-dir <path>` — directory of per-sample source dirs (each sub-dir name = sample_id); default `/datadrive/data/raw/sven/file`
- `--sample-id <hex>` — skip auto-selection, use a specific sample directory under `--cpg-dir`
- `--candidates N` — scan N source dirs to find richest CPG (default 30)

## Tool reference


| Tool                                                 | Description                               |
| ---------------------------------------------------- | ----------------------------------------- |
| `ping`                                               | Check Joern server version / connectivity |
| `check_connection`                                   | Full connection verification              |
| `get_help`                                           | List available tools                      |
| `load_cpg`                                           | Load a CPG into Joern via `importCpg()`   |
| `get_method_callees`                                 | Methods called by a given method          |
| `get_method_callers`                                 | Methods that call a given method          |
| `get_method_code_by_full_name`                       | Source code of a method                   |
| `get_method_code_by_id`                              | Source code by method node ID             |
| `get_method_full_name_by_id`                         | Full name from node ID                    |
| `get_calls_in_method_by_method_full_name`            | Call sites inside a method                |
| `get_call_code_by_id`                                | Code snippet for a call node              |
| `get_method_by_call_id`                              | Enclosing method for a call node          |
| `get_referenced_method_full_name_by_call_id`         | Callee full name for a call node          |
| `get_class_methods_by_class_full_name`               | All methods of a class/typeDecl           |
| `get_method_code_by_class_full_name_and_method_name` | Method code by class + method name        |
| `get_class_full_name_by_id`                          | Class full name from node ID              |
| `get_derived_classes_by_class_full_name`             | Subclasses of a type                      |
| `get_parent_classes_by_class_full_name`              | Supertypes of a type                      |


## Development

Tools are defined in three places that must stay in sync:

1. `server_tools.sc` — Scala CPGQL implementation (loaded into Joern at startup)
2. `server_tools.py` — Python wrapper that calls `joern_remote()` (auto-generated)
3. `server.py` — FastMCP `@joern_mcp.tool()` registration

After editing `.sc`, regenerate the Python wrappers and update the agent tool-schema bundle:

```bash
# Regenerate tool schema bundle for agent prompts
cd mcp-joern && uv run python scripts/export_tool_schemas.py
```

## References

- [Joern HTTP server docs](https://docs.joern.io/server/)
- [FastMCP](https://github.com/jlowin/fastmcp)
- Upstream fork: [sfncat/mcp-joern](https://github.com/sfncat/mcp-joern)

## Acknowledgements
- Thanks to authors of `mcp-joern`: [sfncat/mcp-joern](https://github.com/sfncat/mcp-joern)