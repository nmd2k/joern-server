# HTTP Proxy API Reference

The joern-server HTTP proxy sits between clients and the internal Joern REPL server. It adds authentication, session affinity, query caching, and source-code parsing on top of Joern's native `/query-sync` interface.

**Default base URL:** `http://localhost:8080`

---

## Authentication

All endpoints support HTTP Basic Auth. (default to no auth for development)

```
Authorization: Basic base64(<username>:<password>)
```

Credentials are set via env vars `JOERN_SERVER_AUTH_USERNAME` / `JOERN_SERVER_AUTH_PASSWORD`. If both are unset, auth is disabled (development only). Credentials are forwarded upstream to the internal Joern REPL.

---

## Request Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | No | HTTP Basic Auth credentials |
| `Content-Type` | Yes (POST) | Must be `application/json` |
| `X-Session-Id` | No | Sticky-routing token. All requests sharing this value are routed to the same Joern backend replica. Auto-generated if absent. |
| `X-Request-Id` | No | Arbitrary trace ID, logged but not used for routing. |

---

## Endpoints

### `GET /health`

Liveness check. Returns immediately without touching the upstream Joern process.

**Response `200`**
```json
{"ok": true}
```

---

### `GET /version`

Returns the running Joern version string by evaluating `joern.versionStr` on the REPL.

**Response `200`**
```json
{"stdout": "1.2.17"}
```

---

### `POST /query-sync`

Execute a CPGQL (Joern query language) expression and return its output.

**Request body**
```json
{"query": "cpg.method.name.l"}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Any valid CPGQL expression |

**Response `200`**
```json
{
  "success": true,
  "stdout": "res0: List[String] = List(\"main\", \"helper\")",
  "stderr": "",
  "latency_ms": 145
}
```

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | `true` if the REPL produced no error |
| `stdout` | string | Raw REPL standard output |
| `stderr` | string | Raw REPL standard error |
| `latency_ms` | int | End-to-end proxy latency |

**Caching behaviour**

Responses are LRU-cached (default: 1000 entries, 300 s TTL). The cache key is `{session_id}:{md5(query)}`. The following query patterns bypass the cache:

- `load_cpg(...)` / `importCpg(...)` — mutate server state
- `cleanup(...)` — mutates server state
- Empty or syntactically invalid queries
- `version` / `help` introspection queries

**Error responses**

| Status | Meaning |
|--------|---------|
| 400 | Malformed JSON or missing `query` field |
| 401 | Invalid or missing credentials |
| 502 | Upstream Joern returned an error |
| 504 | Query exceeded `JOERN_QUERY_TIMEOUT_SEC` |

---

### `POST /parse`

Parse source code into a Code Property Graph (CPG) and persist it on disk.

**Request body**
```json
{
  "sample_id": "my-sample",
  "source_code": "int main() { return 0; }",
  "language": "C",
  "filename": "main.c",
  "overwrite": false
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `sample_id` | string | Yes | — | Output directory name under `CPG_OUT_DIR` |
| `source_code` | string | Yes | — | Raw source text to parse |
| `language` | string | No | auto-detect | Target language (see table below) |
| `filename` | string | No | `snippet.txt` | Filename written to the temp dir before parsing |
| `overwrite` | bool | No | `false` | Replace an existing CPG with the same `sample_id` |

**Supported `language` values**

Aliases are normalised before being passed to `joern-parse`.

| Accepted alias | Joern canonical |
|----------------|----------------|
| `c`, `cpp`, `c++`, `cc`, `cxx` | `C` |
| `java` | `JAVASRC` |
| `py`, `python` | `PYTHONSRC` |
| `js`, `ts`, `javascript`, `typescript` | `JSSRC` |
| `cs`, `csharp` | `CSHARPSRC` |
| `go` | `GOLANG` |
| `rb`, `ruby` | `RUBYSRC` |

**Response `200`**
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

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | `true` on success |
| `sample_id` | string | Echo of the request `sample_id` |
| `cpg_path` | string | Absolute path to the CPG output directory |
| `language` | string | Resolved language used for parsing |
| `return_code` | int | Exit code from `joern-parse` (0 = success); `null` on cache hit |
| `stdout` | string | `joern-parse` stdout; empty string on cache hit |
| `stderr` | string | `joern-parse` stderr; empty string on cache hit |
| `cache_hit` | bool | `true` if the CPG was loaded from the archive (joern-parse was skipped) |
| `source_hash` | string | SHA-256 hex digest of `source_code`, used as the archive cache key |

**Error responses**

| Status | Meaning |
|--------|---------|
| 400 | Missing required fields |
| 409 | CPG for `sample_id` already exists; set `overwrite: true` to replace |
| 504 | Parsing exceeded `JOERN_PARSE_TIMEOUT_SEC` |

---

### `POST /cleanup`

Delete (or archive) a previously parsed CPG from disk.

**Request body**
```json
{"sample_id": "my-project", "archive": true}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `sample_id` | string | Yes | — | ID of the CPG to remove |
| `archive` | bool | No | `false` | If `true`, move CPG to the archive instead of deleting it permanently |

**Response `200`**
```json
{
  "ok": true,
  "sample_id": "my-project",
  "cpg_path": "/workspace/cpg-out/my-project",
  "deleted": false,
  "archived": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | Always `true` on a 200 response |
| `sample_id` | string | Echo of the request `sample_id` |
| `cpg_path` | string | Path that was removed or archived |
| `deleted` | bool | `true` if the CPG was permanently deleted; `false` if archived or not found |
| `archived` | bool | `true` if the CPG was moved to the archive; `false` otherwise |

`deleted` and `archived` are both `false` when no CPG was found for the given `sample_id` (idempotent).

When `archive=true` the CPG is moved to `CPG_ARCHIVE_DIR/<source_hash>/` and recorded in the registry. Disk-LRU eviction runs immediately after each archive operation.

---

### `GET /cache-metrics`

Return query-cache statistics.

**Request body** — empty `{}` or omit body.

**Response `200`** — cache counters dict (hits, misses, size, evictions).

> **Note — CPG archive stats:** `/cache-metrics` covers the in-memory CPGQL query cache (Sprint 1). CPG archive statistics (total archived entries, eviction count, disk usage) are recorded in the registry file at `<parent of CPG_OUT_DIR>/cpg-registry.json`, not in this endpoint. A future sprint may expose archive stats here via an `"archive"` sub-key.

---

### CPG Archive — Environment Variables

The CPG archive cache is configured via the following environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `CPG_ARCHIVE_DIR` | `/workspace/cpg-archive` | Root directory where archived CPGs are stored (one sub-directory per `source_hash`) |
| `CPG_ARCHIVE_MAX_COUNT` | `100` | Maximum number of archived CPGs retained; oldest by `last_used` are evicted first (LRU) |
| `CPG_ARCHIVE_MAX_GB` | `50` | Maximum total disk space (GB) consumed by the archive; LRU eviction runs until within limit |

The archive registry is stored at `<parent of CPG_OUT_DIR>/cpg-registry.json` (i.e. `/workspace/cpg-registry.json` by default). It maps `source_hash → {archive_path, sample_id, archived_at, last_used, size_bytes}` and is protected by a threading lock to prevent corruption under concurrent access.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_HOST` | `0.0.0.0` | Proxy bind address |
| `PROXY_PORT` | `8080` | Proxy listen port |
| `JOERN_INTERNAL_HOST` | `127.0.0.1` | Internal Joern REPL host |
| `JOERN_INTERNAL_PORT` | `18080` | Internal Joern REPL port |
| `JOERN_PARSE_BIN` | `/opt/joern/joern-parse` | Path to `joern-parse` binary |
| `CPG_OUT_DIR` | `/workspace/cpg-out` | Root dir for parsed CPGs |
| `JOERN_PARSE_TIMEOUT_SEC` | `900` | Max seconds for a `/parse` request |
| `JOERN_QUERY_TIMEOUT_SEC` | `600` | Max seconds for a `/query-sync` request |
| `QUERY_CACHE_MAX_SIZE` | `1000` | LRU cache capacity (entries) |
| `QUERY_CACHE_TTL_SEC` | `300` | LRU cache entry TTL (seconds) |
| `JOERN_SERVER_AUTH_USERNAME` | *(unset)* | Basic auth username |
| `JOERN_SERVER_AUTH_PASSWORD` | *(unset)* | Basic auth password |
| `CPG_ARCHIVE_DIR` | `/workspace/cpg-archive` | Directory for archived CPGs (hash-keyed sub-dirs) |
| `CPG_ARCHIVE_MAX_COUNT` | `100` | Max number of archived CPGs before LRU eviction |
| `CPG_ARCHIVE_MAX_GB` | `50` | Max total archive disk size in GB before LRU eviction |

---

## Python Client

`joern_server.client.JoernHTTPQueryExecutor` wraps the HTTP API.

```python
from joern_server.client import JoernHTTPQueryExecutor

with JoernHTTPQueryExecutor(
    "http://127.0.0.1:8080",
    auth=("joern", "password"),
    timeout=600.0,
    retries=2,
    session_id="my-session",  # sticky routing
) as ex:
    # Execute a CPGQL query
    result = ex.execute("cpg.method.name.l")
    # {"success": True, "stdout": "...", "stderr": "", "latency_ms": 123}

    # Parse source code
    parse = ex.parse_source(
        sample_id="my-sample",
        source_code="int main(){}",
        language="C",
        filename="main.c",
        overwrite=False,
    )
    # {"ok": True, "sample_id": "my-sample", "cpg_path": "...", ...}
```

Multiple base URLs are accepted; the client round-robins across them:

```python
ex = JoernHTTPQueryExecutor(
    ["http://host1:8080", "http://host2:8080"],
    auth=("joern", "password"),
)
```
