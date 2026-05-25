# Architecture

Joern Server exposes Joern as an **HTTP API** on port **8080**. Each container runs a **FastAPI** service (`joern_server.app:app` via uvicorn) in front of a **single Joern REPL** (JVM). Multi-replica deployments use **HAProxy** with sticky routing so each `sample_id` stays on one replica.

---

## System context

```mermaid
flowchart TB
  subgraph clients [Clients]
    Agent[LLM agent / CI]
    PG[playground-server optional]
  end

  subgraph edge [Scale profile only]
    HA[HAProxy :8080]
  end

  subgraph container [Each joern container]
    API[joern_server FastAPI :8080]
    Sem[repl_semaphore]
    Joern[Joern --server :18080]
    Affinity[AppState affinity map]
    API --> Sem --> Joern
    API --> Affinity
  end

  subgraph volumes [Shared Docker volumes]
    Out[cpg-out]
    Arc[cpg-archive]
  end

  Agent --> HA
  PG --> HA
  HA --> API
  API --> Out
  API --> Arc
  Joern --> Out
```

| Profile | Entry | Replicas |
|---------|-------|----------|
| Dev | `deploy/compose.dev.yml` | 1 |
| Scale | `deploy/compose.scale.yml` + HAProxy | N (`--scale joern=N`) |

---

## Process model (one container)

Started by `docker/unified-entrypoint.sh`:

1. Start Joern `--server` on `JOERN_INTERNAL_PORT` (default **18080**).
2. Wait until Joern accepts `POST /query-sync` with `val _health = 1`.
3. Start **uvicorn** `joern_server.app:app` on **8080** (`PYTHONPATH=/app`).
4. Start **watchdog** that probes Joern every `JOERN_WATCHDOG_INTERVAL_SEC` and triggers restart on consecutive failures.
5. Supervise: if Joern exits, restart Joern and the HTTP service (bounded by `JOERN_MAX_RESTARTS`).

| Process | Port | Role |
|---------|------|------|
| Joern JVM | 18080 (internal) | CPGQL REPL; one active CPG in memory |
| FastAPI (uvicorn) | 8080 (published via HAProxy in scale) | HTTP API, parse subprocess, `AppState` (affinity, cache, registry) |
| Watchdog (shell) | — | Probes Joern health; triggers restart on failure |

Clients never connect to 18080 directly.

---

## Request flow: `/query-sync`

```mermaid
sequenceDiagram
  participant C as Client
  participant H as HAProxy
  participant P as joern_server API
  participant J as Joern REPL

  C->>H: POST /query-sync\nX-Affinity-Key: sample_id
  H->>P: sticky backend
  P->>P: optional query LRU cache
  P->>P: repl_semaphore
  P->>P: activate CPG for affinity key
  P->>J: POST /query-sync
  J-->>P: JSON stdout/stderr/success
  P-->>C: 200 / 422 / 502 / 504
```

| Step | Behavior |
|------|----------|
| Cache | Optional LRU keyed by `{affinity_key}:{md5(query)}`; off when `QUERY_CACHE_MAX_SIZE=0` |
| Semaphore | `repl_semaphore(1)` — one REPL operation at a time per container |
| Activation | If affinity key has a stored `importCpg` path, API re-imports before forwarding |
| Errors | Joern `success=false` at HTTP 200 → **422**; timeout → **504**; upstream down → **502** with `code` |

---

## HTTP headers

Two headers serve different purposes.

| Header | Typical value | Purpose |
|--------|---------------|---------|
| `X-Session-Id` | Agent run UUID | Logging; forwarded to Joern |
| `X-Affinity-Key` | `sample_id` | HAProxy stickiness, `_affinity_cpg_path`, query cache |

HAProxy stickiness order (`deploy/haproxy.cfg`):

1. `X-Affinity-Key`
2. `X-Session-Id` (fallback)
3. Client IP (fallback)

**Rule for clients:** after `POST /parse`, send the same `X-Affinity-Key: <sample_id>` on every `/query-sync` and `/cleanup` for that CPG.

Optional: `X-Request-Id` for correlation (returned on error responses).

---

## Parse pipeline (stateless)

| Endpoint | Input | Output |
|----------|-------|--------|
| `POST /parse` | JSON `source_code` | `/workspace/cpg-out/<sample_id>/` |
| `POST /parse/repo` | NDJSON or `upload_id` | One CPG per repo |
| `POST /parse/repo/upload` | Multipart archive | Staged under `repo-uploads/` |

Parse runs `joern-parse` in a **subprocess** (not under `repl_semaphore`). Any replica can parse; output is visible on all replicas via the shared **`cpg-out`** volume.

**Parse deduplication:** `FileCPGRegistry` keys archives by `source_hash` (SHA-256 of source). Supports flat-file and directory CPG layouts under `cpg-archive/`. Cache hits copy from archive to `cpg-out`. All replicas share the same archive volume.

---

## Storage

| Path | Scope | Purpose |
|------|-------|---------|
| `/workspace/cpg-out/<sample_id>/` | Shared volume | Built CPG directories |
| `/workspace/cpg-archive/` | Shared volume | Archived CPGs by `source_hash` (flat file or directory + `.meta.json`) |
| `/workspace/repo-uploads/<upload_id>/` | Shared volume | Staged uploads (TTL) |
| `_affinity_cpg_path` | Per replica (RAM) | Affinity key → last successful `importCpg` path |

Reference CPGs in CPGQL:

```scala
importCpg("/workspace/cpg-out/my-sample")
```

---

## Cleanup

`POST /cleanup` with `sample_id`:

1. Delete or archive files under `cpg-out/<sample_id>` (optional `archive: true`).
2. Remove matching entries from `_affinity_cpg_path`.
3. Best-effort `close` on the REPL if that CPG was active; clears proxy active-CPG state on success.
4. If no CPG remains loaded and container memory exceeds `JOERN_MEMORY_RESTART_MB`, schedule **drain + Joern JVM restart** to reclaim retained heap.

After cleanup, run `importCpg` again before further queries.

---

## Drain and JVM restart

Joern retains heap after `close`; long-running replicas can grow to the cgroup limit even with no active CPG. The entrypoint supervises restarts:

```mermaid
sequenceDiagram
  participant C as Client
  participant H as HAProxy
  participant P as joern_server
  participant E as unified-entrypoint

  Note over P: cleanup completes, no active CPG
  P->>P: cgroup RSS > JOERN_MEMORY_RESTART_MB
  P->>P: draining=true; reject new work (503)
  P->>H: GET /health → 503 draining
  H->>H: mark backend down; redispatch new keys
  P->>P: sleep JOERN_DRAIN_SEC
  P->>E: write /tmp/joern-restart.requested
  E->>E: restart Joern JVM + proxy
```

| Phase | Client impact (via HAProxy) |
|-------|----------------------------|
| Drain starts | New sessions with new `X-Affinity-Key` retried on other replicas |
| Same affinity key on draining replica | 503 JSON `replica_draining` until restart completes |
| After restart | Replica re-joins pool when `/health` returns 200 |

Staging-only: `POST /debug/drain` triggers the same cycle when `JOERN_ENABLE_DRAIN_TEST=1`.

---

## Concurrency and capacity

| Constraint | Implication |
|------------|-------------|
| One REPL per container | All affinity keys on that replica share one JVM graph at a time |
| `repl_semaphore(1)` | Queries serialize per replica |
| Stickiness | N parallel **active** CPG workloads need up to N replicas (with even spread) |

Example: 8 parallel sessions on 10 replicas ≈ low queue depth if affinity keys are distinct and spread evenly.

---

## Health and metrics

| Endpoint | Behavior |
|----------|----------|
| `GET /health` | TCP probe by default; `?deep=true` overlays a CPGQL health query against Joern; **503** with `"draining": true` while replica drains |
| `GET /health?deep=true` | Returns `joern_http_ok`, `joern_repl_ok`, `repl_latency_ms`, `repl_error` |
| `GET /metrics` | Prometheus text format (`joern_proxy_*`) |
| `POST /cache-metrics` | LRU stats when query cache enabled (POST, not GET) |
| `GET /version` | Forwards `version` query to Joern |

Docker healthcheck uses `POST /query-sync` (same probe as `?deep=true`). HAProxy uses `GET /health` (TCP-only, no `deep`).

Optional monitoring: `deploy/compose.monitoring.yml` (Prometheus + Grafana).

---

## API surface (summary)

| Method | Path | Stateful |
|--------|------|----------|
| GET | `/health`, `/version`, `/metrics`, `/cache-metrics` | No |
| POST | `/parse`, `/parse/repo`, `/parse/repo/upload` | No |
| POST | `/query-sync` | Yes (affinity) |
| POST | `/graph/cfg`, `/dfg`, `/ddg`, `/pdg`, `/ast` | Yes (use `/query-sync` to import first) |
| POST | `/cleanup` | No (clears affinity) |

Details: [API reference](api_reference.md), [Query guide](query_guide.md).

---

## Code layout

| Package | Responsibility |
|---------|----------------|
| `joern_server/app.py` | FastAPI app, lifespan, router registration |
| `joern_server/config.py` | `Settings` loaded from environment |
| `joern_server/state.py` | `AppState` (affinity, cache, registry, semaphore, metrics) |
| `joern_server/api/routers/` | HTTP routes (thin handlers) |
| `joern_server/parse/` | Single/repo parse, language aliases, subprocess runner |
| `joern_server/graph/` | CFG/DFG/DDG/PDG/AST extraction |
| `joern_server/cpg/` | File-based registry, storage, path helpers |
| `joern_server/cache/` | LRU query cache |
| `joern_server/session/` | Affinity map, REPL lock |
| `joern_server/upstream/` | httpx calls to Joern `:18080` |
| `joern_server/lifecycle/` | Cleanup, drain, memory-triggered JVM restart |
| `joern_server/api/middleware/` | Drain middleware (503 while replica drains) |
| `joern_server/client.py` | `JoernHTTPQueryExecutor` for callers |

See [Developer guide — Package layout](developer_guide.md#package-layout) for the full tree.

---

## Related

- [Deployment](deploy.md)
- [Developer guide](developer_guide.md)
