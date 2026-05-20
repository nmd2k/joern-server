# Joern deploy profiles

Production deploy is **HTTP-only on port 8080** (no MCP / :9000). The unified Docker image starts Joern + `joern_server/proxy.py` only.

Run all commands from the **repository root** (compose paths like `../docker/Dockerfile` are relative to the repo root).

## Which profile?

| Profile | Command | Use case |
|---------|---------|----------|
| dev | `docker compose -f deploy/compose.dev.yml up -d` | Local development, single replica |
| scale | `docker compose -f deploy/compose.scale.yml up -d --scale joern=4` | Multi-agent / parallel runs behind HAProxy |

Copy `deploy/.env.example` to `deploy/.env` and set `JOERN_SERVER_AUTH_PASSWORD` before exposing the stack on a network.

## HTTP :8080 and session stickiness

Both profiles expose the Joern HTTP proxy on **port 8080** (override with `JOERN_PUBLISH_PORT` in dev, `JOERN_HTTP_PORT` in scale).

In **scale**, HAProxy routes by request header **`X-Session-Id`**. Send the same value on every `/query-sync` call in a conversation so loaded CPG state stays on one replica. If the header is missing, HAProxy falls back to source IP.

```python
headers = {"X-Session-Id": session_id, "Content-Type": "application/json"}
```

Scale up or down without changing the client URL:

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

## Quick checks

```bash
curl http://127.0.0.1:8080/health

curl -s -u "joern:change-me" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: my-session" \
  -d '{"query":"val x = 41 + 1"}' \
  http://127.0.0.1:8080/query-sync | jq .
```

## Environment

See `deploy/.env.example` for JVM heap, memory limits, auth, and autoheal tuning.

## Helpers

| Script | Role |
|--------|------|
| `deploy/hotpatch.sh` | Push `joern_server/` changes into running containers without rebuilding |
| `deploy/run-joern.sh` | Standalone upstream Joern image (not compose) |
| `deploy/expose-port.sh` | SSH reverse tunnel for remote access to :8080 |
| `scripts/parse-and-serve.sh` | Build CPG into the shared `cpg-out` volume |

## Rebuild image

```bash
docker compose -f deploy/compose.dev.yml build
docker compose -f deploy/compose.scale.yml up -d --scale joern=4
```

## Legacy compose files

Older `docker-compose*.yml` and HAProxy configs (including MCP :9000) live under `deploy/archive/`. Use `compose.dev.yml` or `compose.scale.yml` instead — see `deploy/archive/README.md`.
