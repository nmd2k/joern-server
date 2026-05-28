# Deployment guide

Run Joern Server locally (single replica) or in production (HAProxy + N replicas). All compose commands assume the **repository root** as the working directory.

---

## Profiles

| Profile | File | Use case |
|---------|------|----------|
| **Dev** | `deploy/compose.dev.yml` | Local development, one replica on host :8080 |
| **Scale** | `deploy/compose.scale.yml` | HAProxy VIP + N `joern` replicas |
| **Monitoring** | `deploy/compose.monitoring.yml` | Optional Prometheus + Grafana overlay |

```bash
cp deploy/.env.example deploy/.env
```

Set `JOERN_SERVER_AUTH_PASSWORD` (and optionally `JOERN_SERVER_AUTH_USERNAME`) before exposing the stack on a network.

---

## Dev profile

```bash
docker compose -f deploy/compose.dev.yml up -d
```

- Published port: `${JOERN_PUBLISH_PORT:-8080}`
- Build image: `docker compose -f deploy/compose.dev.yml build`

---

## Scale profile

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

- HTTP entry: **HAProxy** `joern-haproxy` on `${JOERN_HTTP_PORT:-8080}`
- Joern containers are **not** published individually; use the VIP only
- **autoheal** restarts unhealthy containers

### Stickiness

`deploy/haproxy.cfg` routes by:

1. `X-Affinity-Key` (use `sample_id`)
2. `X-Session-Id`
3. Client IP

Response header `X-Served-By` identifies the backend (e.g. `joern3`).

Run at least **3 replicas** in production so one replica can drain/restart while HAProxy serves new sessions on the others.

### Drain and transparent retry

When a replica must restart its Joern JVM (memory threshold after cleanup, or staging drain test), it enters **drain mode**:

1. New requests (except `/health`, `/metrics`, `/debug/drain/enabled`) receive **503** JSON with `code: replica_draining`.
2. `/health` returns **503** with `"draining": true` so HAProxy marks the backend down (`fall 1`, ~2s).
3. After `JOERN_DRAIN_SEC` (default **7**), the entrypoint restarts the Joern JVM.

HAProxy (`deploy/haproxy.cfg`) uses `retry-on 503`, `option redispatch`, and `retries 3` so clients hitting the VIP with a **new** `X-Affinity-Key` are retried on another replica without seeing the drain response.

If HAProxy has no healthy backend, clients see its default HTML 503 (`No server is available…`) — not the app JSON. That usually means too few replicas are up or a recent restart has not finished.

### Staggered restart (thundering-herd prevention)

Under uniform load, all replicas grow memory at similar rates and may cross `JOERN_MEMORY_RESTART_MB` at nearly the same time. Without coordination, they all drain and restart simultaneously, leaving zero healthy backends.

To prevent this, the restart mechanism uses **random jitter + HAProxy VIP health gating**:

1. When a replica crosses the memory threshold after cleanup, it waits a random delay (0 to `JOERN_RESTART_JITTER_SEC`, default **30s**).
2. After the jitter, it re-checks memory — if it dropped below threshold (e.g. due to GC), the restart is cancelled.
3. It probes the HAProxy VIP (`JOERN_HAPROXY_VIP`) to verify the cluster is healthy. If the VIP returns non-200, the restart is deferred to the next cleanup cycle.
4. Only if all checks pass does the replica enter drain mode.

This ensures restarts are naturally staggered across time, and no replica will restart if the cluster is already degraded.

Deferred restarts are tracked by the `joern_proxy_restart_deferred_total` counter (labels: `jitter_memory_recovered`, `cpg_loaded_during_jitter`, `cluster_degraded`).

### HAProxy stats

The stats page is available at `http://localhost:8404/stats` (auto-refresh 5s). It shows backend health, active sessions, and server state transitions — useful for debugging drain/restart events.

Recreate HAProxy after changing `haproxy.cfg`:

```bash
docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d joern-haproxy --force-recreate
```

### Shared CPG storage (`CPG_BASE_DIR`)

Both active CPGs and archive cache live under one base directory mounted at `/workspace/cpg`:

| Host path | Container path | Purpose |
|-----------|----------------|---------|
| `${CPG_BASE_DIR}/out` | `/workspace/cpg/out` | Active CPGs (one per `sample_id`) |
| `${CPG_BASE_DIR}/archive` | `/workspace/cpg/archive` | Persistent parse dedup cache |
| Docker volume `joern-workspace` | `/workspace/joern-workspace` | Joern workspace |

Keeping both subdirectories on the same physical disk makes archive-to-active restores fast (intra-disk copy).

---

## Health and readiness

| Check | Command |
|-------|---------|
| VIP / dev | `curl -s http://127.0.0.1:8080/health` |
| Per replica | `docker exec deploy-joern-1 curl -s http://127.0.0.1:8080/health` |

Healthy response:

```json
{"ok": true, "joern_ok": true, "joern_http_ok": true, "latency_ms": 5}
```

With `?deep=true` the response also includes `joern_repl_ok`, `repl_latency_ms`, and `repl_error`.

**503** means the proxy is up but Joern is not ready, or the replica is **draining**:

```json
{"ok": false, "joern_ok": false, "joern_http_ok": false, "latency_ms": 0, "draining": true}
```

HAProxy marks draining backends down so traffic moves to other replicas.

Start period: allow **120s** (`start_period` in compose) for JVM + first parse readiness.

---

## Rebuild and roll out

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
docker compose -f deploy/compose.scale.yml restart joern-haproxy
```

**Hot-patch** (Python only, not persistent across recreate):

```bash
./deploy/hotpatch.sh
docker restart deploy-joern-1   # restart proxies to pick up code
```

Always rebuild the image before relying on hot-patch in production.

---

## Monitoring (optional)

Start the scale stack first, then add monitoring in a **separate Compose project** so `up`/`down` on monitoring never stops Joern:

```bash
# 1) Joern + HAProxy (if not already running)
docker compose -f deploy/compose.scale.yml up -d --scale joern=10

# 2) Prometheus + Grafana only
docker compose -f deploy/compose.monitoring.yml --project-name joern-monitoring up -d
```

| Service | URL |
|---------|-----|
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (default `admin` / `admin`) |

Prometheus discovers `joern` containers via Docker socket and scrapes `:8080/metrics` (no need to pass `compose.scale.yml`).

Set `DOCKER_GID` in `deploy/.env` to the host docker group id (`stat -c %g /var/run/docker.sock`). Without it, Prometheus cannot read the socket and Grafana panels show **No data**.

**Avoid:** `docker compose -f deploy/compose.monitoring.yml up -d` while Joern is running under project `deploy` — Compose treats `joern` / `joern-haproxy` as removed from the project and stops them. Use `--project-name joern-monitoring` instead.

Pre-provisioned dashboard: **Joern Server** (query rate, parse rate/latency, cleanup rate, errors, `joern_proxy_joern_up`, affinity map size). Panels filter with `up{job="joern-proxy"}` so stopped replicas from an earlier `--scale` do not appear as still UP.

### Prometheus metrics (`joern_proxy_*`)

Scraped from `GET /metrics` on each replica. Counter names are exported with a `_total` suffix; histograms export `_sum` and `_count` pairs.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `joern_proxy_parse_requests_total` | Counter | `status`, `language`, `cache_hit` | Parse requests (`POST /parse`, `/parse/repo`, `/parse/repo/upload`); `status` is the HTTP status code; `cache_hit` is `true`/`false` |
| `joern_proxy_parse_duration_seconds_sum` | Histogram sum | — | Total parse wall time (seconds) |
| `joern_proxy_parse_duration_seconds_count` | Histogram count | — | Number of parse observations (use with `_sum` for average latency) |
| `joern_proxy_cleanup_requests_total` | Counter | `status`, `archived` | Cleanup requests (`POST /cleanup`); `archived` is `true` when the request archived the CPG instead of deleting it |
| `joern_proxy_draining` | Gauge | — | `1` while replica is draining before JVM restart |
| `joern_proxy_joern_restart_requests_total` | Counter | `reason` | Scheduled JVM restarts (e.g. `memory_threshold`) |
| `joern_proxy_restart_deferred_total` | Counter | `reason` | Restarts deferred by staggered restart logic (`jitter_memory_recovered`, `cpg_loaded_during_jitter`, `cluster_degraded`) |

Existing query-sync metrics (`joern_proxy_query_sync_requests_total`, `joern_proxy_query_sync_duration_seconds_*`) are unchanged.

---

## Environment variables

See `deploy/.env.example`. Important keys:

| Variable | Default | Role |
|----------|---------|------|
| `JOERN_JAVA_XMX` | `4g` (scale compose) | Joern REPL JVM heap (`-J-Xmx`) |
| `JOERN_MEMORY_LIMIT` | `8g` (scale compose) | Docker cgroup cap (Joern + proxy + `joern-parse` child) |
| `JOERN_MEMORY_RESTART_MB` | `3072` | After cleanup with no active CPG, restart Joern when container RSS exceeds this; `0` disables |
| `JOERN_DRAIN_SEC` | `7` | Wait before JVM restart so HAProxy marks replica down |
| `JOERN_RESTART_JITTER_SEC` | `30` | Max random delay before committing to drain+restart (thundering-herd prevention) |
| `JOERN_RESTART_MIN_PEERS` | `2` | Minimum healthy backends required before allowing a restart |
| `JOERN_HAPROXY_VIP` | `http://joern-haproxy:8080` | HAProxy VIP URL for cluster health gating; empty string disables the gate |
| `JOERN_ENABLE_DRAIN_TEST` | `0` | `1` exposes `POST /debug/drain` for staging tests only |
| `JOERN_INTERNAL_PORT` | `18080` | Joern HTTP server |
| `JOERN_QUERY_TIMEOUT_SEC` | `600` | Proxy → Joern timeout |
| `QUERY_CACHE_MAX_SIZE` | `0` in scale | Per-replica query LRU |
| `CPG_BASE_DIR` | `/workspace/cpg` (inside container) | Parent directory for active + archive CPG storage |
| `CPG_ARCHIVE_MAX_COUNT` | `500` (scale profile) | Archive eviction |
| `JOERN_READY_TIMEOUT_SEC` | `120` | Entrypoint wait for Joern |
| `JOERN_MAX_RESTARTS` | `10` | Entrypoint restart limit |
| `JOERN_WATCHDOG_INTERVAL_SEC` | `5` | Watchdog health probe interval |
| `JOERN_WATCHDOG_FAIL_THRESHOLD` | `3` | Consecutive failures before restart |

### Production memory (example: 125 GB host, 3 replicas)

Set `JOERN_MEMORY_LIMIT` above `JOERN_JAVA_XMX` plus headroom for the proxy, metaspace, and parse subprocess (`PARSE_JVM_XMX`, default **2g**). Set `JOERN_MEMORY_RESTART_MB` roughly **1.5–2.5 GB below** the cgroup limit so restarts happen before OOM.

| Profile | `JOERN_JAVA_XMX` | `JOERN_MEMORY_LIMIT` | `JOERN_MEMORY_RESTART_MB` |
|---------|------------------|----------------------|---------------------------|
| Standard (mixed samples) | `4g` | `10g` | `6144` |
| Heavy (large C#/Java CPGs) | `8g` | `12g` | `9216` |

Keep `JOERN_ENABLE_DRAIN_TEST=0` in production.

---

## Helpers

| Script | Role |
|--------|------|
| `deploy/hotpatch.sh` | Copy `joern_server/` into running containers |
| `deploy/run-joern.sh` | Upstream Joern image without this proxy |
| `deploy/expose-port.sh` | SSH tunnel to :8080 |
| `scripts/test_drain_live.sh` | Live HAProxy drain + redispatch test (staging; needs `JOERN_ENABLE_DRAIN_TEST=1`) |

Legacy MCP compose files: `deploy/archive/`.

---

## Related

- [Architecture](ARCHITECTURE.md)
- [Testing](testing.md)
- [deploy/README.md](../deploy/README.md)
