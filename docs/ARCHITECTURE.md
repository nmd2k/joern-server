# Joern-server architecture (HTTP-first)

NeuralAtlas exposes Joern as a **stateless parse API** plus a **session-scoped query REPL** behind a single HTTP entry point. Remote clients never talk to Joern’s internal port directly.

**Related docs:** [CLIENT_GUIDE.md](CLIENT_GUIDE.md) · [deploy/README.md](../deploy/README.md) · [`.pms/docs/api/http_api.md`](../.pms/docs/api/http_api.md)

---

## Request flow

```
┌─────────────────────────────────────────────────────────────┐
│  HTTP clients (agents, CI, playground, curl)                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │  :8080  (optional HAProxy VIP)
                           │  GET  /health, /version
                           │  POST /parse, /parse/repo, /parse/repo/upload
                           │  POST /query-sync, /graph/*, /cleanup
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  joern_server/proxy.py  (:8080)                             │
│  • HTTP Basic auth (optional)                               │
│  • Parse: materialize source → joern-parse → cpg-out      │
│  • Session CPG activation (X-Session-Id)                    │
│  • Graph JSON helpers (/graph/cfg, …)                       │
│  • Query timeout + LRU cache (optional)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │  forwards CPGQL
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Joern REPL  (in-container, e.g. :18080)                  │
│  • importCpg / load_cpg / cpg.* traversals                  │
│  • One active CPG per OS process                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
              /workspace/cpg-out/<sample_id>     ← built CPGs
              /workspace/repo-uploads/<id>/      ← staged archives (TTL)
```

**Out of scope:** MCP-over-SSE, named “tool” wrappers (`get_method_callees`, etc.). Clients send **raw CPGQL** via `/query-sync`. Production deploy is **HTTP `:8080` only**.

---

## Parse modes

| Mode | Endpoint | Input | CPG shape |
|------|----------|-------|-----------|
| **File / snippet** | `POST /parse` | JSON: one `source_code` string | Single compilation unit; cross-file edges only if present in that one file |
| **Repo (JSONL)** | `POST /parse/repo?…` | NDJSON stream: one file per line | Full project tree → **one** `joern-parse` → **one** CPG |
| **Repo (upload)** | `POST /parse/repo/upload` then `POST /parse/repo` | Multipart archive, then JSON `upload_id` | Same as JSONL after server extracts archive |
| **Repo (ops)** | `POST /parse/repo` | JSON `source_root` on allow-listed server path | Same pipeline; for bind-mounted datasets only |

**Anti-pattern:** Calling `POST /parse` once per file in a multi-file project. That produces **N unrelated CPGs**, not a repository graph. Use repo ingest instead.

### File mode (`POST /parse`)

1. Proxy writes `source_code` to a temp directory (optional `filename`).
2. Runs `joern-parse` with optional `language`.
3. Output directory: `/workspace/cpg-out/<sample_id>`.
4. Registry may return `cache_hit: true` when `source_hash` (SHA-256 of content) was seen before.

### Repo mode (`POST /parse/repo`)

All ingest paths converge on the same server pipeline:

```
HTTP ingest → validate paths/limits → temp source tree → joern-parse → /workspace/cpg-out/<sample_id>
                                      ↓
                               CPGRegistry (canonical tree source_hash)
```

- **JSONL (primary for remote clients):** `Content-Type: application/x-ndjson`, query params `sample_id`, `language`, `overwrite`. Each line: `{"path":"relative/posix/file","content":"…"}`.
- **Upload:** Stage zip/tar.gz under `/workspace/repo-uploads/<upload_id>/`, then trigger parse with JSON body.
- **Ops `source_root`:** Skip client upload; read from e.g. `/workspace/datasets/…` (allow-list enforced).

---

## Storage layout

| Path | Purpose |
|------|---------|
| `/workspace/cpg-out/<sample_id>/` | Parsed CPG output (shared volume in compose; survives replica restarts) |
| `/workspace/repo-uploads/<upload_id>/` | Staged archives between upload and parse; expired after TTL (default 24h) |
| CPG registry archive | On-disk index keyed by `source_hash` for parse deduplication |

Clients reference CPGs in CPGQL as:

```scala
importCpg("/workspace/cpg-out/my-app")
```

---

## Session isolation (`X-Session-Id`)

Joern keeps **one active CPG per REPL process**. The proxy adds **logical** multi-tenant isolation:

| Concern | Behavior |
|---------|----------|
| Header | `X-Session-Id` (recommended). Defaults to `"default"` if omitted. |
| `importCpg("…")` | On success, proxy records that path for the session. Later `/query-sync` and `/graph/*` re-activate it before forwarding. |
| No prior import | Proxy issues a best-effort `close` so another session’s graph is not left active. |
| Scaled deploy | Send the **same** `X-Session-Id` on every request so HAProxy pins to one replica (sticky routing). |
| Concurrency | One REPL per replica; queries serialized via an internal semaphore. Isolation is proxy-managed activation, not separate processes per session. |

Parse endpoints (`/parse`, `/parse/repo`) are **stateless** with respect to the REPL; session header is optional there (useful for logging). **Query and graph endpoints require a loaded CPG** for the session.

See [CLIENT_GUIDE.md](CLIENT_GUIDE.md) for a full `importCpg` + `query-sync` walkthrough.

---

## Graph and query surfaces

| Surface | Role |
|---------|------|
| `POST /query-sync` | Arbitrary CPGQL; preferred for analysis after `importCpg` |
| `POST /graph/cfg` \| `dfg` \| `ddg` \| `pdg` \| `ast` | Structured graph JSON for a `method_full_name` |
| `POST /cleanup` | Remove or archive a CPG by `sample_id` |

Use standard Joern CPGQL (e.g. `cpg.method.name.l`, `cpg.call.name("exec").l`) via `/query-sync` after `importCpg`.

---

## Deployment (summary)

| Profile | Use |
|---------|-----|
| Single container | Local dev |
| HAProxy + N replicas | Production / multi-agent (sticky `X-Session-Id`) |

Details: [deploy/README.md](../deploy/README.md). Sprint 9 consolidates compose files to two profiles (`compose.dev.yml`, `compose.scale.yml`).

---

## Design references

- Approved API & limits: `.pms/docs/sdd/sdd_v2_http_platform.md`
- Sprint backlog: `.pms/backlog/sprint9.md`
- HTTP API reference: `.pms/docs/api/http_api.md`
