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

### Shared volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `cpg-out` | `/workspace/cpg-out` | Parsed CPGs (all replicas) |
| `cpg-archive` | `/workspace/cpg-archive` | Parse deduplication archive |
| `joern-workspace` | `/workspace/joern-workspace` | Joern workspace |

---

## Health and readiness

| Check | Command |
|-------|---------|
| VIP / dev | `curl -s http://127.0.0.1:8080/health` |
| Per replica | `docker exec deploy-joern-1 curl -s http://127.0.0.1:8080/health` |

Healthy response:

```json
{"ok": true, "joern_ok": true, "latency_ms": 30}
```

**503** means the proxy is up but Joern is not — HAProxy should mark the backend down after deploy with current proxy code.

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

Pre-provisioned dashboard: **Joern Server** (query rate, errors, `joern_proxy_joern_up`, affinity map size). Panels filter with `up{job="joern-proxy"}` so stopped replicas from an earlier `--scale` do not appear as still UP.

---

## Environment variables

See `deploy/.env.example`. Important keys:

| Variable | Default | Role |
|----------|---------|------|
| `JOERN_JAVA_XMX` | `4g` / `8g` | JVM heap |
| `JOERN_MEMORY_LIMIT` | `8g` | Container memory cap |
| `JOERN_INTERNAL_PORT` | `18080` | Joern HTTP server |
| `JOERN_QUERY_TIMEOUT_SEC` | `600` | Proxy → Joern timeout |
| `QUERY_CACHE_MAX_SIZE` | `0` in scale | Per-replica query LRU |
| `CPG_ARCHIVE_MAX_COUNT` | `100` | Archive eviction |
| `JOERN_READY_TIMEOUT_SEC` | `120` | Entrypoint wait for Joern |
| `JOERN_MAX_RESTARTS` | `10` | Entrypoint restart limit |

---

## Helpers

| Script | Role |
|--------|------|
| `deploy/hotpatch.sh` | Copy `joern_server/` into running containers |
| `deploy/run-joern.sh` | Upstream Joern image without this proxy |
| `deploy/expose-port.sh` | SSH tunnel to :8080 |

Legacy MCP compose files: `deploy/archive/`.

---

## Related

- [Architecture](ARCHITECTURE.md)
- [Testing](testing.md)
- [deploy/README.md](../deploy/README.md)
