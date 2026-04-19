# Software Requirements Specification (SRS)

# Joern Server — Scalable CPG Analysis Platform

**Version:** 1.0  
**Date:** 2026-04-16  
**Status:** Draft

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for a scalable Joern server platform that provides Code Property Graph (CPG) analysis capabilities via HTTP and Model Context Protocol (MCP) interfaces. The system enables concurrent, containerized deployment of Joern for static code analysis, particularly for security vulnerability detection.

### 1.2 Scope

The Joern Server platform consists of two main components:

1. **HTTP Proxy Server** (`joern_server/`): A Python HTTP server that exposes endpoints for CPG parsing, querying, and lifecycle management.
2. **MCP Server** (`mcp-joern/`): A Model Context Protocol server that exposes 18 Joern analysis tools for AI agent integration.

The system is designed to:

- Deploy as Docker containers
- Scale horizontally to 20+ concurrent instances
- Handle 20+ simultaneous code parsing requests
- Manage CPG artifacts (build, load, query, cleanup)

### 1.3 Definitions and Acronyms


| Term             | Definition                                                                 |
| ---------------- | -------------------------------------------------------------------------- |
| CPG              | Code Property Graph — Joern's intermediate representation of source code   |
| CPGQL            | Joern's query language for traversing CPGs                                 |
| MCP              | Model Context Protocol — standard for AI tool integration                  |
| SSE              | Server-Sent Events — transport protocol for MCP                            |
| HAProxy          | Load balancer for scaled deployments                                       |
| Session Affinity | Routing mechanism ensuring requests with same session ID go to same server |


### 1.4 References

- [Joern Documentation](https://docs.joern.io/)
- [Joern HTTP Server](https://docs.joern.io/server/)
- [FastMCP Library](https://github.com/jlowin/fastmcp)

---

## 2. Overall Description

### 2.1 Product Perspective

The Joern Server is a self-contained platform that wraps Joern's functionality in network services. It sits between:

- **Upstream**: Joern CLI (`joern-parse`) and Joern HTTP server (`/query-sync`)
- **Downstream**: HTTP clients and MCP-enabled AI agents

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Layer                            │
│  ┌──────────────┐         ┌──────────────────────────────┐  │
│  │ HTTP Clients │         │ AI Agents (via MCP Client)   │  │
│  └──────┬───────┘         └──────────────┬───────────────┘  │
└─────────┼────────────────────────────────┼──────────────────┘
          │                                │
┌─────────▼────────────────────────────────▼──────────────────┐
│                   Load Balancer (HAProxy)                   │
│         (for scaled deployments, session affinity)          │
└─────────┬────────────────────────────────┬──────────────────┘
          │                                │
┌─────────▼────────────────────────────────▼──────────────────┐
│                  Joern Server Container                      │
│  ┌────────────────────┐       ┌──────────────────────────┐  │
│  │  HTTP Proxy        │       │  MCP Server (FastMCP)    │  │
│  │  (joern_server/)   │       │  (mcp-joern/)            │  │
│  │  :8080             │       │  :9000 (SSE)             │  │
│  └────────┬───────────┘       └────────────┬─────────────┘  │
│           │                                │                 │
│  ┌────────▼────────────────────────────────▼────────┐       │
│  │          Joern HTTP Server (:18080)              │       │
│  │          (internal, handles CPGQL queries)       │       │
│  └────────┬─────────────────────────────────────────┘       │
│           │                                                  │
│  ┌────────▼─────────────────────────────────────────┐       │
│  │          Joern CLI (joern-parse)                 │       │
│  │          (invoked for CPG generation)            │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Shared Volume: /workspace/cpg-out               │       │
│  │  (CPG artifacts, accessible across replicas)     │       │
│  └──────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Product Features


| Feature ID | Feature                                               | Priority |
| ---------- | ----------------------------------------------------- | -------- |
| F-001      | HTTP `/query-sync` endpoint for CPGQL queries         | High     |
| F-002      | HTTP `/parse` endpoint for CPG generation             | High     |
| F-003      | HTTP `/cleanup` endpoint for CPG artifact deletion    | High     |
| F-004      | HTTP `/health` and `/version` endpoints               | Medium   |
| F-005      | MCP server with 18 Joern analysis tools               | High     |
| F-006      | Session affinity via `X-Session-Id` header            | High     |
| F-007      | Horizontal scaling with HAProxy load balancing        | High     |
| F-008      | Concurrent request handling (ThreadingHTTPServer)     | High     |
| F-009      | CPG lifecycle management (build, load, query, delete) | High     |
| F-010      | Comprehensive MCP tool testing                        | High     |
| F-011      | CPG archive with hash-based deduplication (skip re-parse on cache hit) | High     |
| F-012      | LRU disk eviction for CPG archive (configurable by count and size)     | High     |


### 2.3 User Classes


| User Class      | Description                                             |
| --------------- | ------------------------------------------------------- |
| AI Agent        | Uses MCP tools to query CPGs for vulnerability analysis |
| Developer       | Uses HTTP API for programmatic CPG access               |
| DevOps Engineer | Deploys and scales the platform using Docker Compose    |


### 2.4 Operating Environment


| Component | Requirement                                                         |
| --------- | ------------------------------------------------------------------- |
| OS        | Linux (Docker-compatible)                                           |
| Docker    | 20.10+ with Compose support                                         |
| Memory    | 12GB per container (default), configurable via `JOERN_MEMORY_LIMIT` |
| JVM Heap  | 4GB default, configurable via `JOERN_JAVA_XMX`                      |
| Network   | Ports 8080 (HTTP), 9000 (MCP SSE), 18080 (internal Joern)           |


### 2.5 Design and Implementation Constraints

1. **Joern Version Dependency**: Must use Joern CLI and HTTP server from official Docker image
2. **Session State**: Joern REPL is stateful; session affinity is required for multi-replica deployments
3. **Shared Storage**: CPG artifacts must be stored in shared volume for cross-replica access
4. **Timeout Handling**: Long-running operations (parse, importCpg) require configurable timeouts (default 600s)

---

## 3. Functional Requirements

### 3.1 HTTP Proxy Server (`joern_server/proxy.py`)

#### FR-HTTP-001: Query Sync Endpoint


| Attribute        | Value                                                           |
| ---------------- | --------------------------------------------------------------- |
| **Endpoint**     | `POST /query-sync`                                              |
| **Description**  | Forward CPGQL queries to Joern HTTP server                      |
| **Request Body** | `{"query": "<CPGQL string>"}`                                   |
| **Response**     | Joern's native JSON response with `stdout`, `stderr`, `success` |
| **Headers**      | Forwards `Authorization`, `X-Session-Id`, `X-Request-Id`        |
| **Timeout**      | Configurable via `JOERN_QUERY_TIMEOUT_SEC` (default 600s)       |


#### FR-HTTP-002: Parse Endpoint


| Attribute           | Value                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| **Endpoint**        | `POST /parse`                                                                                                                           |
| **Description**     | Parse source code into CPG using `joern-parse`; returns cached CPG if `source_hash` matches archive                                     |
| **Request Body**    | JSON with `sample_id`, `source_code`, optional `language`, `filename`, `overwrite`                                                      |
| **Response**        | `{"ok": bool, "sample_id": str, "cpg_path": str, "return_code": int, "stdout": str, "stderr": str, "cache_hit": bool, "source_hash": str}` |
| **CPG Output Path** | `/workspace/cpg-out/<sample_id>`                                                                                                        |
| **Timeout**         | Configurable via `JOERN_PARSE_TIMEOUT_SEC` (default 900s)                                                                               |


**Supported Languages** (with aliases):


| Language   | Joern Value | Aliases                                |
| ---------- | ----------- | -------------------------------------- |
| C          | `C`         | `cpp`, `c++`, `cc`, `cxx`              |
| C#         | `CSHARPSRC` | `cs`, `csharp`                         |
| Go         | `GOLANG`    | `go`                                   |
| Java       | `JAVA`      | `javasrc`                              |
| JavaScript | `JSSRC`     | `js`, `ts`, `javascript`, `typescript` |
| Python     | `PYTHONSRC` | `py`, `python`                         |
| Ruby       | `RUBYSRC`   | `rb`, `ruby`                           |


#### FR-HTTP-003: Cleanup Endpoint


| Attribute        | Value                                                              |
| ---------------- | ------------------------------------------------------------------ |
| **Endpoint**     | `POST /cleanup`                                                                       |
| **Description**  | Delete or archive CPG artifact for given sample_id                                    |
| **Request Body** | `{"sample_id": "<id>", "archive": bool}` — `archive` defaults to `false`             |
| **Response**     | `{"ok": true, "sample_id": str, "cpg_path": str, "deleted": bool, "archived": bool}` |


#### FR-HTTP-004: Health and Version Endpoints


| Endpoint   | Method | Description                                            |
| ---------- | ------ | ------------------------------------------------------ |
| `/health`  | GET    | Returns `{"ok": true}` for Docker health checks        |
| `/version` | GET    | Executes Joern `version` query, returns version string |


#### FR-HTTP-005: Concurrent Request Handling


| Attribute          | Value                                                           |
| ------------------ | --------------------------------------------------------------- |
| **Implementation** | `ThreadingHTTPServer` from Python `http.server`                 |
| **Requirement**    | Must handle 20+ simultaneous requests without blocking          |
| **Thread Safety**  | Each request handled in separate thread; shared state protected |


### 3.2 MCP Server (`mcp-joern/server.py`)

#### FR-MCP-001: Transport Support


| Attribute         | Value                                          |
| ----------------- | ---------------------------------------------- |
| **Transports**    | SSE (Server-Sent Events), stdio                |
| **SSE Endpoint**  | `http://<host>:9000/sse`                       |
| **Configuration** | Via `MCP_TRANSPORT` env var (`sse` or `stdio`) |


#### FR-MCP-002: Tool Implementation

The MCP server must implement the following 18 tools:


| Tool ID | Tool Name                                            | Description                                       |
| ------- | ---------------------------------------------------- | ------------------------------------------------- |
| T-001   | `ping`                                               | Check Joern server connectivity via version query |
| T-002   | `check_connection`                                   | Full connection verification with error messages  |
| T-003   | `get_help`                                           | List available Joern tools                        |
| T-004   | `load_cpg`                                           | Load CPG via `importCpg()` and `load_cpg()`       |
| T-005   | `get_method_callees`                                 | Methods called by specified method                |
| T-006   | `get_method_callers`                                 | Methods that call specified method                |
| T-007   | `get_method_code_by_full_name`                       | Source code by method full name                   |
| T-008   | `get_method_code_by_id`                              | Source code by method node ID                     |
| T-009   | `get_method_full_name_by_id`                         | Full name from method node ID                     |
| T-010   | `get_calls_in_method_by_method_full_name`            | Call sites inside method                          |
| T-011   | `get_call_code_by_id`                                | Code snippet for call node                        |
| T-012   | `get_method_by_call_id`                              | Enclosing method for call node                    |
| T-013   | `get_referenced_method_full_name_by_call_id`         | Callee full name for call node                    |
| T-014   | `get_class_methods_by_class_full_name`               | All methods of a typeDecl                         |
| T-015   | `get_method_code_by_class_full_name_and_method_name` | Method code by class + name                       |
| T-016   | `get_class_full_name_by_id`                          | Class full name from node ID                      |
| T-017   | `get_derived_classes_by_class_full_name`             | Subclasses of a type                              |
| T-018   | `get_parent_classes_by_class_full_name`              | Supertypes of a type                              |


#### FR-MCP-003: Session Management


| Attribute           | Value                                                         |
| ------------------- | ------------------------------------------------------------- |
| **Session ID**      | Generated via `JOERN_SESSION_ID` env var or UUID              |
| **Header**          | `X-Session-Id` included in all `/query-sync` requests         |
| **Sticky Recovery** | If CPG vanishes (NullPointerException), auto-reload and retry |


#### FR-MCP-004: Error Handling


| Scenario                 | Behavior                              |
| ------------------------ | ------------------------------------- |
| Joern server unreachable | Return descriptive error message      |
| Invalid CPGQL syntax     | Forward Joern error to client         |
| Null CPG error           | Auto-reload last known CPG and retry  |
| Timeout                  | Return error with timeout information |


### 3.3 Deployment and Scaling

#### FR-DEP-001: Single Container Deployment


| Attribute         | Value                                      |
| ----------------- | ------------------------------------------ |
| **Configuration** | `deploy/docker-compose.yml`                |
| **Services**      | Single `joern` container with HTTP + MCP   |
| **Ports**         | 8080 (HTTP), 9000 (MCP)                    |
| **Auto-heal**     | Enabled via `willfarrell/autoheal` sidecar |


#### FR-DEP-002: Scaled Deployment


| Attribute            | Value                                                  |
| -------------------- | ------------------------------------------------------ |
| **Configuration**    | `deploy/docker-compose.haproxy-scale.yml`              |
| **Scaling**          | `docker compose up -d --scale joern=<N>` (N up to 20+) |
| **Load Balancer**    | HAProxy with Docker DNS discovery                      |
| **Session Affinity** | HAProxy sticky table on `X-Session-Id` header          |
| **Shared Volume**    | `cpg-out` volume for cross-replica CPG access          |


#### FR-DEP-003: Resource Management


| Resource         | Default | Configurable Via          |
| ---------------- | ------- | ------------------------- |
| JVM Heap         | 4GB     | `JOERN_JAVA_XMX`          |
| Container Memory | 12GB    | `JOERN_MEMORY_LIMIT`      |
| File Descriptors | 65536   | Docker ulimits            |
| Query Timeout    | 600s    | `JOERN_QUERY_TIMEOUT_SEC` |
| Parse Timeout    | 900s    | `JOERN_PARSE_TIMEOUT_SEC` |


---

## 4. Non-Functional Requirements

### 4.1 Performance Requirements


| Requirement                     | Target            |
| ------------------------------- | ----------------- |
| Concurrent parse requests       | 20+ simultaneous  |
| Query response time (simple)    | < 1 second        |
| Query response time (complex)   | < 60 seconds      |
| CPG parse time (medium project) | < 15 minutes      |
| Horizontal scale                | Up to 20 replicas |


### 4.2 Reliability Requirements


| Requirement             | Target                     |
| ----------------------- | -------------------------- |
| Uptime                  | 99.9% (with auto-heal)     |
| Health check interval   | 30 seconds                 |
| Health check timeout    | 15 seconds                 |
| Auto-restart on failure | Enabled                    |
| Graceful degradation    | Return errors, don't crash |


### 4.3 Security Requirements


| Requirement               | Implementation                                            |
| ------------------------- | --------------------------------------------------------- |
| Authentication            | HTTP Basic Auth via `JOERN_SERVER_AUTH_USERNAME/PASSWORD` |
| Request isolation         | Session-based via `X-Session-Id`                          |
| Input validation          | Validate JSON structure, required fields                  |
| Path traversal prevention | Sanitize `sample_id` (alphanumeric, `.`, `_`, `-` only)   |


### 4.4 Maintainability Requirements


| Requirement   | Implementation                                          |
| ------------- | ------------------------------------------------------- |
| Logging       | Structured JSON logs to stdout                          |
| Configuration | Environment variables only (no config files at runtime) |
| Testing       | Unit tests, integration tests, functional tests         |
| Documentation | README.md in each component directory                   |


---

## 5. CPG Lifecycle Management

### 5.1 Lifecycle States

```
┌─────────┐    parse    ┌─────────┐    importCpg    ┌──────────┐
│  None   │ ─────────> │  File   │ ─────────────> │  Loaded  │
└─────────┘            └─────────┘                └────┬──────┘
      ▲                                                │ query
      │  cache hit (source_hash match)                 ▼
      │                                          ┌──────────┐
┌──────────┐   cleanup (archive=true)            │  Active  │
│ Archived │ <────────────────────────────────── └────┬─────┘
└──────────┘                                          │ cleanup (archive=false)
                                                      │
                                               ┌──────────┐
                                               │  Deleted │
                                               └──────────┘
```

State descriptions:

- **None** — no CPG artifact exists for this `sample_id`
- **File** — CPG files exist on disk in `CPG_OUT_DIR/<sample_id>` but are not loaded into the Joern REPL
- **Loaded** — CPG is imported into the Joern REPL (`importCpg`) but no query has been executed yet
- **Active** — CPG is loaded and has been queried at least once in the current session
- **Archived** — CPG files have been moved to `CPG_ARCHIVE_DIR/<source_hash>/`; the registry maps the hash back to the archived path so a future `/parse` with the same `source_code` can restore it without re-running `joern-parse`
- **Deleted** — CPG files have been permanently removed from disk

### 5.2 State Transitions


| Transition          | Trigger                                          | Endpoint/Tool                              |
| ------------------- | ------------------------------------------------ | ------------------------------------------ |
| None → File         | Parse source code (cache miss)                   | `POST /parse`                              |
| Archived → File     | Parse source code with matching `source_hash` (cache hit) | `POST /parse`                     |
| File → Loaded       | Import CPG into Joern REPL                       | `importCpg()` query or `load_cpg` MCP tool |
| Loaded → Active     | Execute queries                                  | `POST /query-sync` or MCP tools            |
| Active → Archived   | Cleanup with archive flag                        | `POST /cleanup` with `"archive": true`     |
| Active → Deleted    | Cleanup without archive flag                     | `POST /cleanup` with `"archive": false` (default) |


### 5.3 Artifact Storage


| Path                                    | Purpose                               | Persistence                            |
| --------------------------------------- | ------------------------------------- | -------------------------------------- |
| `/workspace/cpg-out/<sample_id>`        | Active CPG output files               | Docker volume (shared across replicas) |
| `/workspace/cpg-archive/<sha256>/`      | Archived CPGs; LRU-evicted            | Docker volume (shared across replicas) |
| `/workspace/cpg-registry.json`          | Hash → archive path index             | Docker volume (shared across replicas) |
| `/workspace/joern-workspace`            | Joern internal workspace              | Docker volume                          |


---

## 6. Testing Requirements

### 6.1 HTTP Proxy Testing


| Test Type   | Scope              | Location                 |
| ----------- | ------------------ | ------------------------ |
| Smoke Test  | Basic connectivity | `joern_server/smoke.py`  |
| Integration | Parse + query flow | `tests/` (to be created) |


### 6.2 MCP Server Testing


| Test Type   | Scope                         | Location                                 |
| ----------- | ----------------------------- | ---------------------------------------- |
| Unit Tests  | Tool wrappers, parsers        | `mcp-joern/tests/test_tools.py`          |
| Integration | MCP client → server → Joern   | `mcp-joern/tests/test_mcp_client.py`     |
| Functional  | All 18 tools against real CPG | `mcp-joern/tests/test_mcp_functional.py` |


### 6.3 Functional Test Requirements

The functional test (`test_mcp_functional.py`) must:

1. **CPG Selection**: Automatically select a CPG with sufficient complexity (methods, calls)
2. **Tool Coverage**: Test all 18 MCP tools
3. **Result Classification**:
  - `PASS`: Non-empty, meaningful output
  - `EMPTY`: Tool ran but returned nothing (with debug explanation)
  - `FAIL`: Python exception or server error
  - `SKIP`: Required ID not discoverable for this CPG
  - `EXPECTED_EMPTY`: Empty is correct (e.g., no inheritance in C)
4. **Debug Diagnostics**: For EMPTY/FAIL results, run follow-up CPGQL queries
5. **Report Generation**: Markdown report with tool results and debug details

### 6.4 Scaling Test Requirements


| Test               | Description                                       |
| ------------------ | ------------------------------------------------- |
| Concurrent Parsers | 20 simultaneous `POST /parse` requests            |
| Concurrent Queries | 20 simultaneous `POST /query-sync` requests       |
| Session Affinity   | Verify same `X-Session-Id` routes to same replica |
| Load Balancer      | HAProxy correctly distributes across N replicas   |


---

## 7. Environment Variables

### 7.1 HTTP Proxy


| Variable                  | Default                            | Description                            |
| ------------------------- | ---------------------------------- | -------------------------------------- |
| `PROXY_HOST`              | `0.0.0.0`                          | Proxy bind address                     |
| `PROXY_PORT`              | `8080`                             | Proxy port (also `JOERN_PUBLISH_PORT`) |
| `JOERN_INTERNAL_HOST`     | `127.0.0.1`                        | Internal Joern server host             |
| `JOERN_INTERNAL_PORT`     | `18080`                            | Internal Joern server port             |
| `JOERN_PARSE_BIN`         | `/opt/joern/joern-cli/joern-parse` | Path to joern-parse binary             |
| `CPG_OUT_DIR`             | `/workspace/cpg-out`               | CPG output directory                   |
| `JOERN_PARSE_TIMEOUT_SEC` | `900`                              | Parse operation timeout                |
| `JOERN_QUERY_TIMEOUT_SEC` | `600`                              | Query operation timeout                |


### 7.2 MCP Server


| Variable              | Default     | Description                       |
| --------------------- | ----------- | --------------------------------- |
| `MCP_TRANSPORT`       | `stdio`     | Transport mode (`sse` or `stdio`) |
| `MCP_HOST`            | `0.0.0.0`   | MCP server bind address           |
| `MCP_PORT`            | `9000`      | MCP server port                   |
| `HOST`                | `127.0.0.1` | Joern server host                 |
| `PORT`                | `8080`      | Joern server port                 |
| `JOERN_AUTH_USERNAME` | -           | HTTP basic auth username          |
| `JOERN_AUTH_PASSWORD` | -           | HTTP basic auth password          |
| `TIMEOUT`             | `300`       | Request timeout (seconds)         |
| `LOG_LEVEL`           | `ERROR`     | Python log level                  |
| `JOERN_SESSION_ID`    | UUID        | Session ID for affinity           |


### 7.3 Deployment


| Variable                     | Default | Description                        |
| ---------------------------- | ------- | ---------------------------------- |
| `JOERN_JAVA_XMX`             | `4g`    | JVM maximum heap size              |
| `JOERN_MEMORY_LIMIT`         | `12g`   | Container memory limit             |
| `JOERN_SERVER_AUTH_USERNAME` | -       | HTTP basic auth username           |
| `JOERN_SERVER_AUTH_PASSWORD` | -       | HTTP basic auth password           |
| `JOERN_IMAGE_TAG`            | `local` | Docker image tag                   |
| `AUTOHEAL_INTERVAL`          | `10`    | Auto-heal check interval (seconds) |
| `AUTOHEAL_START_PERIOD`      | `180`   | Auto-heal start period (seconds)   |


---

## 8. API Specifications

### 8.1 HTTP API

#### POST /query-sync

**Request:**

```json
{
  "query": "cpg.method.name.l"
}
```

**Headers:**

- `Content-Type: application/json`
- `Authorization: Basic <base64>` (optional)
- `X-Session-Id: <session-id>` (recommended for affinity)
- `X-Request-Id: <request-id>` (optional, for tracing)

**Response:**

```json
{
  "success": true,
  "stdout": "List(...)",
  "stderr": ""
}
```

#### POST /parse

**Request:**

```json
{
  "sample_id": "my-project",
  "source_code": "int main(){return 0;}",
  "language": "C",
  "filename": "main.c",
  "overwrite": true
}
```

**Response (Success):**

```json
{
  "ok": true,
  "sample_id": "my-project",
  "cpg_path": "/workspace/cpg-out/my-project",
  "language": "C",
  "return_code": 0,
  "stdout": "...",
  "stderr": "",
  "cache_hit": false,
  "source_hash": "a3f1c2..."
}
```

**Response (Conflict):**

```json
{
  "error": "CPG output already exists at /workspace/cpg-out/my-project; pass overwrite=true to replace",
  "code": "cpg_exists"
}
```

#### POST /cleanup

**Request:**

```json
{
  "sample_id": "my-project",
  "archive": true
}
```

**Response:**

```json
{
  "ok": true,
  "sample_id": "my-project",
  "cpg_path": "/workspace/cpg-out/my-project",
  "deleted": false,
  "archived": true
}
```

#### GET /health

**Response:**

```json
{
  "ok": true
}
```

#### GET /version

**Response:**

```json
{
  "stdout": "2.2.1123"
}
```

### 8.2 MCP Tools Reference

See Section 3.2 for tool list. Each tool:

- Accepts typed parameters (strings, IDs)
- Returns string or list of strings
- Handles errors gracefully with descriptive messages

---

## 9. Future Considerations (Out of Scope)


| Feature                     | Notes                                               |
| --------------------------- | --------------------------------------------------- |
| WebSocket `/query` endpoint | Current implementation uses sync `/query-sync` only |
| ~~CPG caching layer~~       | ~~No external cache; CPGs stored on disk~~ **Implemented in Sprint 3** — see F-011, F-012 |
| Multi-tenant isolation      | Session-based only; no hard isolation               |
| Metrics/observability       | Basic logging only; no Prometheus/Grafana           |
| gRPC interface              | HTTP/JSON only                                      |


---

## Appendix A: Directory Structure

```
joern-server/
├── .pms/                          # Project management state
│   ├── docs/
│   │   ├── srs/
│   │   │   └── srs_v1.md          # This document
│   │   ├── sdd/                   # Software Design Documents
│   │   └── api/                   # API documentation
│   ├── backlog/                   # Sprint backlogs
│   └── report/                    # Sprint reports
├── joern_server/                  # HTTP Proxy Server
│   ├── __init__.py
│   ├── __main__.py
│   ├── proxy.py                   # Main HTTP server
│   ├── client.py                  # HTTP client library
│   ├── smoke.py                   # Smoke test CLI
│   └── README.md
├── mcp-joern/                     # MCP Server
│   ├── server.py                  # MCP server entry point
│   ├── server_tools.py            # Auto-generated tool wrappers
│   ├── server_tools.sc            # Scala CPGQL helpers
│   ├── common_tools.py            # Shared utilities
│   ├── requirements.txt
│   ├── mcp_settings.json
│   └── tests/
│       ├── test_tools.py
│       ├── test_mcp_client.py
│       └── test_mcp_functional.py
├── deploy/                        # Deployment configurations
│   ├── docker-compose.yml         # Single container
│   ├── docker-compose.haproxy-scale.yml  # Scaled deployment
│   ├── haproxy-joern.cfg
│   ├── haproxy-joern-mcp-scale.cfg
│   ├── run-joern.sh
│   └── .env.example
└── docker/                        # Docker build context
    └── Dockerfile
```

---

## Appendix B: Quick Start

### Single Container

```bash
cd deploy
cp .env.example .env
# Edit .env (set JOERN_SERVER_AUTH_PASSWORD)
docker compose up -d
```

### Scaled Deployment

```bash
cd deploy
docker compose -f docker-compose.haproxy-scale.yml up -d --scale joern=4
```

### Test MCP Tools

```bash
cd mcp-joern
uv run python tests/test_mcp_functional.py \
    --http-url http://localhost:8080 \
    --mcp-url http://localhost:9000/sse \
    --sven /path/to/sven.jsonl \
    --report mcp_functional_report.md
```

