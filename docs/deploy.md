# Deployment Guide

This guide describes how to run and deploy Joern Server. Production deploy is **HTTP-only on port 8080** (no MCP / `:9000`). The unified Docker image starts Joern + `joern_server/proxy.py` only.

All commands should be run from the **repository root** (as compose paths like `docker/Dockerfile` are relative to the repo root).

---

## Which Profile?

| Profile | Command | Use case |
|---------|---------|----------|
| **Dev** | `docker compose -f deploy/compose.dev.yml up -d` | Local development, single replica |
| **Scale** | `docker compose -f deploy/compose.scale.yml up -d --scale joern=4` | Multi-agent / parallel runs behind HAProxy |

Before deploying, copy `deploy/.env.example` to `deploy/.env` and set `JOERN_SERVER_AUTH_PASSWORD`.

---

## HTTP :8080 and Session Stickiness

Both profiles expose the Joern HTTP proxy on **port 8080** (override with `JOERN_PUBLISH_PORT` in dev, `JOERN_HTTP_PORT` in scale).

In **scale**, HAProxy routes by request header **`X-Session-Id`**. Send the same value on every `/query-sync` call in a conversation so loaded CPG state stays on one replica. If the header is missing, HAProxy falls back to source IP.

```python
headers = {"X-Session-Id": session_id, "Content-Type": "application/json"}
```

Scale up or down without changing the client URL:

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

---

## Quick Checks

```bash
curl http://127.0.0.1:8080/health

curl -s -u "joern:change-me" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: my-session" \
  -d '{"query":"val x = 41 + 1"}' \
  http://127.0.0.1:8080/query-sync | jq .
```

---

## Environment Configuration

See `deploy/.env.example` in the repository for:
- JVM heap settings
- Memory limits
- Authentication secrets
- Autoheal tuning parameters

---

## Deployment Helpers

| Script | Role |
|--------|------|
| `deploy/hotpatch.sh` | Push `joern_server/` changes into running containers without rebuilding |
| `deploy/run-joern.sh` | Standalone upstream Joern image (not compose) |
| `deploy/expose-port.sh` | SSH reverse tunnel for remote access to :8080 |
| `scripts/parse-and-serve.sh` | Build CPG into the shared `cpg-out` volume |

---

## Rebuild Image

```bash
docker compose -f deploy/compose.dev.yml build
docker compose -f deploy/compose.scale.yml up -d --scale joern=4
```
