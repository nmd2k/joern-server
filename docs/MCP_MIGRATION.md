# MCP → HTTP migration (Sprint 9)

The `mcp-joern` FastMCP layer (`:9000` SSE / stdio) is **removed from production deploy**. Integrate directly with the HTTP proxy on **`:8080`**.

**Client guide:** [CLIENT_GUIDE.md](CLIENT_GUIDE.md) · **API reference:** [`.pms/docs/api/http_api.md`](../.pms/docs/api/http_api.md)

---

## Transport change

| Before (MCP) | After (HTTP) |
|--------------|--------------|
| MCP client → `:9000` SSE or stdio | Any HTTP client → `:8080` |
| `parse_source` tool | `POST /parse` or `POST /parse/repo` |
| `joern_remote('…')` via MCP | `POST /query-sync` with `{"query":"…"}` |
| Implicit MCP session id | Explicit `X-Session-Id` header |

Set HTTP Basic auth when `JOERN_SERVER_AUTH_USERNAME` / `JOERN_SERVER_AUTH_PASSWORD` are configured (same credentials previously passed to MCP via env).

---

## Tool → HTTP mapping

### Core / connectivity

| Former MCP tool | HTTP / CPGQL equivalent | Notes |
|-----------------|-------------------------|-------|
| `check_connection` | `GET /health` or `GET /version` | Liveness vs Joern version string |
| `ping` | `GET /version` or `POST /query-sync` `{"query":"version"}` | |
| `get_help` | `POST /query-sync` `{"query":"help"}` | |
| `parse_source` | `POST /parse` then `POST /query-sync` `importCpg("…")` | Repo: use `POST /parse/repo` instead of one file |
| `load_cpg` | `POST /query-sync` `importCpg("/workspace/cpg-out/…")` and optionally `load_cpg("…")` | `importCpg` sets console `cpg.*`; `load_cpg` updates script helpers |

### Analysis helpers (`server_tools.sc`)

These ran as CPGQL through MCP’s `joern_remote`. Call the **same function names** via `/query-sync` after `importCpg` (and `load_cpg` if you rely on script-level state):

| Former MCP tool | `POST /query-sync` query |
|-----------------|--------------------------|
| `get_method_callees` | `get_method_callees("<method_full_name>")` |
| `get_method_callers` | `get_method_callers("<method_full_name>")` |
| `get_class_full_name_by_id` | `get_class_full_name_by_id("<id>")` |
| `get_class_methods_by_class_full_name` | `get_class_methods_by_class_full_name("<class>")` |
| `get_method_code_by_full_name` | `get_method_code_by_method_full_name("<method>")` |
| `get_method_code_by_id` | `get_method_code_by_id("<id>")` |
| `get_method_full_name_by_id` | `get_method_full_name_by_id("<id>")` |
| `get_call_code_by_id` | `get_call_code_by_id("<id>")` |
| `get_method_code_by_class_full_name_and_method_name` | `get_method_code_by_class_full_name_and_method_name("<class>","<name>")` |
| `get_derived_classes_by_class_full_name` | `get_derived_classes_by_class_full_name("<class>")` |
| `get_parent_classes_by_class_full_name` | `get_parent_classes_by_class_full_name("<class>")` |
| `get_method_by_call_id` | `get_method_by_call_id("<call_id>")` |
| `get_referenced_method_full_name_by_call_id` | `get_referenced_method_full_name_by_call_id("<call_id>")` |
| `get_calls_in_method_by_method_full_name` | `get_calls_in_method_by_method_full_name("<method>")` |
| `find_methods` | Inline `cpg.method…` query (see MCP `find_methods` implementation) |
| `find_calls` | Inline `cpg.call…` query |
| `get_call_arguments` | `cpg.call.id(<id>).argument…` or equivalent CPGQL |
| `find_literals` | `cpg.literal.code("…")…` |
| `get_method_location` | `cpg.method.id(…)` or `.fullName(…)` location map |
| `get_dataflow` | `reachableByFlows` CPGQL (see tool source for pattern) |

### Graphs (prefer HTTP graph endpoints)

| Former approach | HTTP equivalent |
|-----------------|-----------------|
| Manual DOT / `joern-export` via MCP | `POST /graph/cfg`, `/graph/dfg`, `/graph/ddg`, `/graph/pdg`, `/graph/ast` with `{"method_full_name":"…"}` |

Requires CPG loaded for session (`importCpg` + `X-Session-Id`).

---

## Typical MCP workflow → HTTP

**Before (MCP `parse_source`):**

1. MCP `parse_source(source_code, sample_id)` → proxy `/parse` + auto `importCpg` / `load_cpg`

**After (HTTP):**

```bash
# 1. Parse (file or repo)
curl -s -u joern:change-me -H 'Content-Type: application/json' \
  -d '{"sample_id":"demo","source_code":"…","language":"c","overwrite":true}' \
  http://localhost:8080/parse

# 2. Import for session
curl -s -u joern:change-me \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-session' \
  -d '{"query":"importCpg(\"/workspace/cpg-out/demo\")"}' \
  http://localhost:8080/query-sync

# 3. Query
curl -s -u joern:change-me \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-session' \
  -d '{"query":"cpg.method.name.l"}' \
  http://localhost:8080/query-sync
```

**Multi-file project (new in Sprint 9):**

```bash
# Build NDJSON locally, then:
curl -s -u joern:change-me -X POST \
  'http://localhost:8080/parse/repo?sample_id=my-app&language=c' \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @repo.ndjson
```

Do **not** loop `POST /parse` per file.

---

## Playground

The playground MCP bridge (`/mcp/tools/*`) is removed (S9-002). Use:

- Direct CPGQL in the UI via `/api/query-sync`, or
- Documented JSONL repo workflow in [CLIENT_GUIDE.md](CLIENT_GUIDE.md)

---

## Package status

| Component | Sprint 9 status |
|-----------|-----------------|
| `joern_server/proxy.py` | Production HTTP API |
| `mcp-joern/` | Deprecated; not started in default image/compose |
| Port `:9000` | Not exposed in target architecture |
