# API reference

HTTP API exposed on port **8080** (via HAProxy in scale mode). Optional HTTP Basic auth when `JOERN_SERVER_AUTH_USERNAME` and `JOERN_SERVER_AUTH_PASSWORD` are set.

---

## Common headers

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | Yes (JSON bodies) | `application/json` |
| `Authorization` | If auth enabled | HTTP Basic |
| `X-Affinity-Key` | Yes for stateful calls | Usually `sample_id` |
| `X-Session-Id` | Recommended | Agent run id for logs |
| `X-Request-Id` | Optional | Correlation id; echoed on errors |

---

## Endpoints

### `GET /health`

Deep health check. By default runs a TCP connectivity check against internal Joern. Add `?deep=true` to also probe Joern with `val _health = 1`.

**200** (default)

```json
{"ok": true, "joern_ok": true, "joern_http_ok": true, "latency_ms": 5}
```

**200** (`?deep=true`)

```json
{"ok": true, "joern_ok": true, "joern_http_ok": true, "joern_repl_ok": true, "latency_ms": 5, "repl_latency_ms": 30}
```

**503** — Joern unreachable

```json
{"ok": false, "joern_ok": false, "joern_http_ok": false, "latency_ms": 5, "error": "..."}
```

---

### `GET /metrics`

Prometheus text exposition (when metrics enabled in `main()`).

---

### `POST /cache-metrics`

LRU query cache statistics, or `{"error": "cache not enabled"}` when `QUERY_CACHE_MAX_SIZE=0`.

Returns `hits`, `misses`, `evictions`, `size`, `max_size`, `ttl_sec`, and computed `hit_rate`.

---

### `GET /version`

Runs Joern `version` query; returns `{"stdout": "..."}`.

---

### `POST /parse`

Build a CPG from a single source string.

**Body**

| Field | Type | Required |
|-------|------|----------|
| `sample_id` | string | yes |
| `source_code` | string | yes |
| `language` | string | no |
| `filename` | string | no |
| `overwrite` | bool | no |

**Response (200)**

```json
{
  "ok": true,
  "cpg_path": "/workspace/cpg-out/<sample_id>",
  "source_hash": "<sha256>",
  "cache_hit": false
}
```

---

### `POST /parse/repo`

Repo ingest via NDJSON body, `upload_id`, or `source_root` (ops). Query params: `sample_id`, `language`, `overwrite`.

Content-Type for NDJSON: `application/x-ndjson`.

---

### `POST /parse/repo/upload`

Multipart upload of zip/tar.gz. Returns `upload_id` and `expires_at`.

---

### `POST /query-sync`

Execute CPGQL synchronously.

**Body**

```json
{"query": "cpg.method.name.l"}
```

**Response (200)** — Joern shape:

```json
{
  "success": true,
  "stdout": "...",
  "stderr": "",
  "uuid": "..."
}
```

**422** — query error or activation failure (`code`: `session_cpg_activation_failed`)

**502 / 504** — gateway errors with `error`, `code`, `request_id`

---

### `POST /graph/cfg` | `/graph/dfg` | `/graph/ddg` | `/graph/pdg` | `/graph/ast`

**Body**

```json
{
  "method_full_name": "com.example.Foo.bar:void()",
  "sample_id": "optional"
}
```

Returns `{nodes, edges, metadata, method_full_name}`.

---

### `POST /cleanup`

**Body**

```json
{
  "sample_id": "my-sample",
  "archive": false
}
```

**Response (200)**

```json
{
  "ok": true,
  "sample_id": "my-sample",
  "deleted": true,
  "archived": false
}
```

Also clears in-memory affinity state for that `sample_id` on the handling replica.

---

## Python modules (generated reference)

### Client

::: joern_server.client

### Application

::: joern_server.app

### HTTP routers

Routers are registered in `joern_server.app.create_app()`:

| Module | Routes |
|--------|--------|
| `joern_server.api.routers.health` | `GET /health`, `/version`, `/metrics`; `POST /cache-metrics` |
| `joern_server.api.routers.query` | `POST /query-sync` |
| `joern_server.api.routers.parse` | `POST /parse` |
| `joern_server.api.routers.parse_repo` | `POST /parse/repo`, `/parse/repo/upload` |
| `joern_server.api.routers.graph` | `POST /graph/cfg`, `/graph/dfg`, `/graph/ddg`, `/graph/pdg`, `/graph/ast` |
| `joern_server.api.routers.cleanup` | `POST /cleanup` |

---

## Related

- [Query guide](query_guide.md)
- [Architecture](ARCHITECTURE.md)
