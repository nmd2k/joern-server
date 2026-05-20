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

**Limitation:** This is **not** repo-level parsing. Cross-file edges exist only when the single snippet includes them. For multi-file projects use [`POST /parse/repo`](#post-parserepo).

**Errors (file parse):**

| HTTP | `code` | When |
|------|--------|------|
| 400 | `bad_request` | Missing `sample_id` / `source_code` |
| 409 | `cpg_exists` | Output exists, `overwrite=false` |
| 504 | `parse_timeout` | `joern-parse` subprocess timeout |
| 502 | `parse_failed` | Subprocess failed (non-timeout) |

---

### `POST /parse/repo`

Parse a **directory tree** into **one CPG** at `/workspace/cpg-out/<sample_id>`. Remote clients should use **JSONL** or **upload** ingest; `source_root` is ops-only.

**Mutual exclusion:** Each request uses **exactly one** ingest mode:

| Mode | Content-Type | Body |
|------|--------------|------|
| JSONL | `application/x-ndjson` | Stream of `{"path","content"}` lines; metadata in **query params** |
| Upload trigger | `application/json` | `{"sample_id","upload_id","language?","overwrite?"}` |
| Ops | `application/json` | `{"sample_id","source_root","language?","overwrite?"}` |

Do not send JSONL body and JSON `upload_id` in the same request.

#### JSONL ingest (primary)

**Query parameters** (required metadata — not a special first NDJSON line):

| Param | Required | Description |
|-------|----------|-------------|
| `sample_id` | yes | Output name under `/workspace/cpg-out/`; sanitized to `[a-zA-Z0-9._-]` |
| `language` | no | Joern frontend (`c`, `pythonsrc`, `jssrc`, …); aliases normalized like `/parse` |
| `overwrite` | no | Default `false` |

**Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | yes | `application/x-ndjson` |
| `X-Session-Id` | no | Optional; parse is stateless (logging / future use) |

**Body:** one JSON object per line (NDJSON). Blank lines are ignored.

| Field | Type | Rules |
|-------|------|--------|
| `path` | string | Relative POSIX path; no `..`, no leading `/`, no `\0` |
| `content` | string | UTF-8 file body; server normalizes line endings to `\n` |

**Example line:**

```json
{"path":"src/main.c","content":"#include <stdio.h>\nint main(){return 0;}"}
```

**Example request:**

```bash
curl -s -u "joern:change-me" -X POST \
  "http://localhost:8080/parse/repo?sample_id=my-app&language=c&overwrite=false" \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @repo.ndjson
```

**Server pipeline:** stream lines → write files under temp dir → canonical tree `source_hash` → `joern-parse` → `/workspace/cpg-out/<sample_id>` → CPGRegistry.

**Limits (env-configurable):**

| Env | Default | Purpose |
|-----|---------|---------|
| `PARSE_REPO_MAX_FILES` | `2000` | Max NDJSON lines / files |
| `PARSE_REPO_MAX_BYTES` | `50000000` | Total decoded `content` bytes |
| `JOERN_PARSE_REPO_TIMEOUT_SEC` | `1800` | `joern-parse` subprocess timeout |

#### Upload ingest (two steps)

**Step 1 — `POST /parse/repo/upload`**

`multipart/form-data`:

| Field | Required | Description |
|-------|----------|-------------|
| `archive` | yes | `.zip` or `.tar.gz` of repository root |

**Limits:** `PARSE_REPO_MAX_ARCHIVE_MB` (default `500`).

**Response (success):**

```json
{
  "ok": true,
  "upload_id": "550e8400-e29b-41d4-a716-446655440000",
  "expires_at": "2026-05-21T12:00:00Z",
  "bytes_stored": 104857600,
  "file_count_hint": null
}
```

Archive stored at `/workspace/repo-uploads/<upload_id>/` until expiry (default **24h**).

**Step 2 — `POST /parse/repo`** with `Content-Type: application/json`:

```json
{
  "sample_id": "my-app",
  "upload_id": "550e8400-e29b-41d4-a716-446655440000",
  "language": "c",
  "overwrite": false
}
```

Server extracts staged archive → same pipeline as JSONL. Response sets `"ingest_mode": "upload"`.

#### Ops ingest (`source_root`)

For datasets already on the server (bind-mounted SVEN, internal CI). **Not** the default for external integrators.

```json
{
  "sample_id": "sven-abc123",
  "source_root": "/workspace/datasets/sven/abc123",
  "language": "c",
  "overwrite": false
}
```

Allow-list: `PARSE_REPO_ALLOWED_ROOTS` (default `/workspace/datasets`). Response sets `"ingest_mode": "source_root"`.

#### Response envelope (repo, success)

Extends file `/parse` response:

```json
{
  "ok": true,
  "sample_id": "my-app",
  "cpg_path": "/workspace/cpg-out/my-app",
  "language": "c",
  "cache_hit": false,
  "source_hash": "<canonical tree sha256 hex>",
  "parse_mode": "repo",
  "ingest_mode": "jsonl",
  "file_count": 142
}
```

`ingest_mode` is one of `jsonl`, `upload`, `source_root`.

**Cache key (repo):** `source_hash = SHA256( for path in sorted(paths): path + "\0" + SHA256(content) + "\n" )`. Registry may return `cache_hit: true` for identical tree content under a different `sample_id`.

#### Errors (repo parse)

| HTTP | `code` | When |
|------|--------|------|
| 400 | `bad_request` | Malformed JSON, missing `sample_id`, mixed ingest modes |
| 400 | `invalid_path` | `..`, absolute path, illegal characters in `path` |
| 400 | `empty_tree` | Zero files after ingest |
| 400 | `invalid_source_root` | Ops path outside allow-list |
| 404 | `upload_not_found` | Unknown `upload_id` |
| 409 | `cpg_exists` | Output exists, `overwrite=false` |
| 410 | `upload_expired` | Past `expires_at` |
| 413 | `payload_too_large` | File count, bytes, or archive over limit |
| 504 | `parse_timeout` | Subprocess timeout |

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

## Related documentation

| Doc | Purpose |
|-----|---------|
| `docs/ARCHITECTURE.md` | System diagram, parse modes, session model |
| `docs/CLIENT_GUIDE.md` | curl quick start, JSONL builder, sticky sessions |
| `docs/MCP_MIGRATION.md` | Former MCP tools → HTTP/CPGQL |
| `.pms/docs/sdd/sdd_v2_http_platform.md` | Approved design decisions |

**Platform note:** MCP (`mcp-joern/`, port `:9000`) is removed from target production deploy; HTTP `:8080` only.
