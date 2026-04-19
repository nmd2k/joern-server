# NeuralAtlas backend — Joern Docker stack

Containerised Joern HTTP service with optional HAProxy load-balancing and MCP-over-SSE.  
All compose files live here; run them from the **repository root** (paths like `../docker/` are relative to the repo root).

## Backend architecture

```
┌─────────────────────────────────────────────────────────┐
│  AI Agent / IDE (MCP client)                            │
└──────────────┬──────────────────────────────────────────┘
               │  HTTP :8080 (query-sync, parse, health)
               │  MCP SSE :9000 (optional)
               ▼
┌─────────────────────────────────────────────────────────┐
│  HAProxy  (docker-compose.haproxy-scale.yml)            │
│  • Round-robin across joern replicas                    │
│  • Sticky sessions via X-Session-Id header              │
└──────────────┬──────────────────────────────────────────┘
               │  internal Docker network (joern-net)
       ┌───────┴───────┐
       ▼               ▼
  joern-1 …       joern-N
  ┌────────────────────┐
  │  proxy.py  :8080   │  ← thin Python proxy (auth, timeout, /parse endpoint)
  │  joern --server    │  ← CPGQL interpreter
  │  mcp server.py :9000│  ← FastMCP SSE (per replica, not behind HAProxy)
  └────────────────────┘
       │
       ▼
  /workspace/cpg-out/<sample_id>   (shared named volume)
```

## Compose files


| File                               | Purpose                       | When to use                                 |
| ---------------------------------- | ----------------------------- | ------------------------------------------- |
| `docker-compose.yml`               | Single Joern + autoheal       | Local dev / single-agent runs               |
| `docker-compose.router.yml`        | Single Joern + HAProxy router | Single instance with stable router endpoint |
| `docker-compose.haproxy-scale.yml` | N replicas behind HAProxy     | Multi-agent parallel runs (recommended)     |


## Quick start

### Scaled deployment (recommended)

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env — set JOERN_SERVER_AUTH_PASSWORD at minimum.

# Start N replicas behind HAProxy (from repo root):
docker compose -f deploy/docker-compose.haproxy-scale.yml up -d --scale joern=10
```

Single stable entry point:

- `http://localhost:${JOERN_HTTP_PORT:-8080}` → HAProxy → N Joern replicas
- `http://localhost:${JOERN_MCP_PORT:-9000}` → HAProxy → N MCP SSE servers

Scale up/down at any time without downtime:

```bash
docker compose -f deploy/docker-compose.haproxy-scale.yml up -d --scale joern=4
```

### Single instance

```bash
docker compose -f deploy/docker-compose.yml up -d
```

Endpoints:

- Joern HTTP: `http://localhost:${JOERN_PUBLISH_PORT:-8080}`
- MCP SSE: `http://localhost:${MCP_PUBLISH_PORT:-9000}/sse`

### HAProxy session affinity

HAProxy maintains sticky routing by request header `X-Session-Id`. Send the same header on all turns of a conversation so that CPG state (loaded graph) stays on the same replica:

```python
headers = {"X-Session-Id": session_id, "Content-Type": "application/json"}
```

Source-IP affinity is the fallback when the header is absent.

### Self-healing

`autoheal` (sidecar) monitors containers labelled `autoheal=true` and restarts unhealthy replicas independently. Healthy replicas continue serving traffic while one restarts.

## Environment variables


| Variable                                   | Purpose                                                        |
| ------------------------------------------ | -------------------------------------------------------------- |
| `JOERN_PUBLISH_PORT`                       | Host port → container `:8080` (single instance)                |
| `JOERN_HTTP_PORT`                          | HAProxy host port for HTTP (haproxy-scale)                     |
| `JOERN_MCP_PORT`                           | HAProxy host port for MCP SSE (haproxy-scale)                  |
| `JOERN_INTERNAL_PORT`                      | Internal Joern REPL port (proxy forwards here)                 |
| `JOERN_QUERY_TIMEOUT_SEC`                  | Proxy timeout for `/query-sync` (raise for long `importCpg`)   |
| `JOERN_JAVA_XMX`                           | Joern JVM heap (start with `4g`; raise for large graphs)       |
| `JOERN_MEMORY_LIMIT`                       | Docker cgroup memory cap — must exceed `JOERN_JAVA_XMX` by ≥8g |
| `JOERN_SERVER_AUTH_USERNAME` / `_PASSWORD` | HTTP basic auth (both required to enable)                      |
| `MCP_PUBLISH_PORT`                         | Host port for MCP SSE (single instance)                        |
| `JOERN_ROUTER_PUBLISH_PORT`                | HAProxy host port (router compose)                             |
| `AUTOHEAL_INTERVAL`                        | Seconds between autoheal checks                                |
| `AUTOHEAL_START_PERIOD`                    | Grace period before autoheal starts checking                   |
| `JOERN_IMAGE_TAG`                          | Docker image tag                                               |


See `deploy/.env.example` for defaults and optional `run-joern.sh` variables.

## API smoke tests

```bash
# Health check
curl http://127.0.0.1:8080/health

# Run a CPGQL query (with auth)
curl -s -u "joern:change-me" \
  -H "Content-Type: application/json" \
  -d '{"query":"val x = 41 + 1"}' \
  http://127.0.0.1:8080/query-sync | jq .
```

## CPG build workflow

1. Start the service (creates volumes):
  ```bash
   docker compose -f deploy/docker-compose.yml up -d
  ```
2. Parse source into CPG:
  ```bash
   ./scripts/parse-and-serve.sh /absolute/path/to/source [language]
  ```
   The CPG is written to `/workspace/cpg-out/<dir_basename>`.
3. Import once, query many times:
  ```bash
   curl -s -u "joern:change-me" \
     -H "Content-Type: application/json" \
     -d '{"query":"importCpg(\"/workspace/cpg-out/c\")"}' \
     http://127.0.0.1:8080/query-sync | jq .

   curl -s -u "joern:change-me" \
     -H "Content-Type: application/json" \
     -d '{"query":"cpg.method.name.l"}' \
     http://127.0.0.1:8080/query-sync | jq .
  ```

## Applying code changes without rebuilding

When you change Python files (`joern_server/` or `mcp-joern/`) and the Dockerfile is unavailable, use the hot-patch script to push changes into running containers immediately:

```bash
# Patch all running deploy-joern-* containers
./deploy/hotpatch.sh

# Patch a single container
./deploy/hotpatch.sh deploy-joern-3

# Override the container name prefix (e.g. if you used a different compose project name)
COMPOSE_PREFIX=prod ./deploy/hotpatch.sh
```

**What it does:** copies changed files from the repo into each container via `docker cp`, then kills and restarts the proxy and MCP processes inside each container.

**Limitation:** hot-patch changes live only in the running container's writable layer. They are lost if the container is recreated (e.g. `docker compose up`, autoheal restart, machine reboot). Always rebuild the image afterwards.

---

## Rebuilding the image

Do a full rebuild whenever you change `docker/Dockerfile`, `docker/unified-entrypoint.sh`, system dependencies, or Python package requirements. Code-only changes to `joern_server/` or `mcp-joern/` can use [hotpatch](#applying-code-changes-without-rebuilding) instead, which is faster.

```bash
# From repo root — build the image:
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
# or via compose:
docker compose -f deploy/docker-compose.yml build

# Redeploy (rolling restart — HAProxy routes around containers being recreated):
docker compose -f deploy/docker-compose.haproxy-scale.yml up -d --scale joern=10
```

Docker Compose recreates containers one at a time; HAProxy keeps routing to healthy replicas during the restart.

---

## One-command local launcher

Mounts the repo at `/app` and imports `mcp-joern/server_tools.sc` (for `load_cpg`, `get_method_*`, etc.):

```bash
./deploy/run-joern.sh
```

## Troubleshooting: OutOfMemoryError

`-Xmx` caps **heap only**. The container cgroup limit (`JOERN_MEMORY_LIMIT`) caps all memory: heap + metaspace + stacks + JIT cache + direct buffers (XNIO/Undertow).

If `JOERN_MEMORY_LIMIT` is only slightly above `JOERN_JAVA_XMX`, non-heap usage exhausts the remainder.

**Fix:** set `JOERN_MEMORY_LIMIT` to at least `JOERN_JAVA_XMX + 8g` (use `+12g` for safety with large graphs).

```bash
# Verify running limit (0 = unlimited)
docker inspect <container> --format '{{.HostConfig.Memory}}'
```

## Helper scripts


| Script                        | Role                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| `scripts/parse-and-serve.sh`  | `joern-parse` source into the `cpg-out` volume               |
| `scripts/joern-scan-batch.sh` | `joern-scan` per subdirectory                                |
| `deploy/run-joern.sh`         | One-command local Joern launcher (imports `server_tools.sc`) |
| `deploy/expose-port.sh`       | Helper to re-expose ports                                    |


## File reference


| Path                                      | Role                                                                    |
| ----------------------------------------- | ----------------------------------------------------------------------- |
| `docker/Dockerfile`                       | Image build: Joern CLI, Python env, non-root user, entrypoints          |
| `docker/unified-entrypoint.sh`            | Starts Joern server + proxy + MCP server inside the container           |
| `docker/healthcheck.sh`                   | `/query-sync` liveness probe used by Docker and autoheal                |
| `docker/entrypoint.sh`                    | Legacy standalone Joern-only entrypoint (not used in unified image)     |
| `deploy/hotpatch.sh`                      | Push code changes into running containers without a full image rebuild   |
| `deploy/docker-compose.yml`               | Single instance compose                                                 |
| `deploy/docker-compose.router.yml`        | Single instance + HAProxy router                                        |
| `deploy/docker-compose.haproxy-scale.yml` | Scalable replicas + HAProxy                                             |
| `deploy/haproxy-joern.cfg`                | HAProxy config for router compose                                       |
| `deploy/haproxy-joern-mcp-scale.cfg`      | HAProxy config for scaled compose                                       |
| `deploy/.env.example`                     | Environment template                                                    |
| `joern_server/proxy.py`                   | Python proxy (auth, `/parse`, `/cleanup`, CPG cache, timeout)           |
| `mcp-joern/server.py`                     | FastMCP server (MCP-over-SSE)                                           |


