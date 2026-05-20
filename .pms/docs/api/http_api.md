# Joern HTTP Proxy API

The `joern_server` proxy exposes a stable HTTP API in front of Joern's internal `/query-sync` REPL. Clients should call the **proxy** (`:8080`), not Joern directly.

**Base URL:** `http://<host>:8080` (or HAProxy VIP)  
**Content-Type:** `application/json`  
**Auth:** HTTP Basic (when `JOERN_SERVER_AUTH_*` is configured)

---

## Session semantics (`X-Session-Id`)

Joern's REPL keeps **one active CPG per process**. The proxy enforces **session-scoped** behavior so concurrent clients do not leak or trample each other's graphs.

| Header | Required | Description |
|--------|----------|-------------|
| `X-Session-Id` | Recommended | Opaque string identifying a client conversation. Defaults to `"default"` if omitted. |
| `X-Request-Id` | Optional | Correlation ID for logs. |

### Rules

1. **`importCpg("…")`** — On success, the proxy records that CPG path for this `X-Session-Id`. Subsequent queries for the same session re-activate that CPG before execution.
2. **Other queries** — Before forwarding, the proxy ensures the session's recorded CPG is active (`importCpg` if needed). If the session has **no** imported CPG, the proxy issues a best-effort `close` to clear a prior session's active graph and avoid cross-session leakage.
3. **Sticky routing** — In scaled deployments, send the **same** `X-Session-Id` on every request so HAProxy pins you to one replica (see `deploy/README.md`).
4. **One REPL, many sessions** — Isolation is **logical** (proxy-managed activation), not separate OS processes per session. Throughput is serialized per replica via an internal semaphore.

### Example flow

```bash
SESSION=my-analysis-1

# Parse (no session required, but recommended for logging)
curl -s -X POST http://localhost:8080/parse \
  -H 'Content-Type: application/json' \
  -d '{"sample_id":"demo","source_code":"int main(){return 0;}","language":"c"}'

# Load CPG for this session
curl -s -X POST http://localhost:8080/query-sync \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: $SESSION" \
  -d '{"query":"importCpg(\"/workspace/cpg-out/demo\")"}'

# Query — only this session's CPG is active
curl -s -X POST http://localhost:8080/query-sync \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: $SESSION" \
  -d '{"query":"cpg.method.name.l"}'
```

### Error codes (session)

| HTTP | `code` | Meaning |
|------|--------|---------|
| 422 | `session_cpg_activation_failed` | Proxy could not re-activate the session's CPG |

---

## Endpoints

### `GET /health`

Liveness check.

**Response:** `{"ok": true}`

---

### `GET /version`

Runs Joern `version` via upstream query.

**Response:** `{"stdout": "<version string>"}`

---

### `POST /parse` (file / snippet mode)

Parse **inline source** into a CPG. One request = one source file written to a temp directory → `joern-parse` → output at `/workspace/cpg-out/<sample_id>`.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sample_id` | string | yes | Logical name; sanitized to `[a-zA-Z0-9._-]` |
| `source_code` | string | yes | Full file contents |
| `language` | string | no | Joern frontend (`c`, `pythonsrc`, `jssrc`, …); aliases normalized |
| `filename` | string | no | Temp filename hint (default `snippet.<ext>`) |
| `overwrite` | bool | no | Replace existing output (default `false`) |

**Response (success):** `ok`, `sample_id`, `cpg_path`, `language`, `cache_hit`, `source_hash`  
**Caching:** SHA-256 of `source_code`; archive registry may return `cache_hit: true` without re-parsing.

**Limitation:** This is **not** repo-level parsing. Cross-file edges exist only when the single snippet includes them. See Sprint 9 design for `POST /parse/repo`.

---

### `POST /cleanup`

Remove or archive a CPG by `sample_id`.

**Request body:** `sample_id`, optional `archive` (default `false`)

---

### `POST /query-sync`

Forward CPGQL to Joern. Body: `{"query": "<CPGQL>"}`.

**Response:** Joern JSON (`stdout`, `stderr`, `success`, …). ANSI escapes stripped from `stdout`. HTTP 422 when Joern returns `success: false`.

**Session:** See [Session semantics](#session-semantics-x-session-id).

---

### `POST /graph/cfg` | `/graph/dfg` | `/graph/ddg` | `/graph/pdg` | `/graph/ast`

Return graph JSON for a method. Body: `{"method_full_name": "<fqn>"}`.

**Response:** `{ "nodes", "edges", "metadata", "method_full_name" }`  
**Requires:** CPG already loaded for the session (via `importCpg`).

---

### `GET /cache-metrics`

Query LRU cache stats (when `QUERY_CACHE_MAX_SIZE` > 0).

---

## Planned (Sprint 9+)

| Endpoint | Purpose |
|----------|---------|
| `POST /parse/repo` | Parse a directory tree (mounted path or uploaded archive) into one CPG |
| Deprecation | MCP layer (`mcp-joern/`) removed; HTTP-only clients |

See `.pms/backlog/sprint9.md` and `.pms/docs/sdd/sdd_v2_http_platform.md`.
