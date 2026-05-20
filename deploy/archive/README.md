# Archived deploy files

These files were replaced in Sprint 9 by two HTTP-only profiles:

| Use instead | Replaces |
|-------------|----------|
| `deploy/compose.dev.yml` | `docker-compose.yml`, `docker-compose.router.yml` |
| `deploy/compose.scale.yml` | `docker-compose.haproxy-scale.yml` |
| `deploy/haproxy.cfg` | `haproxy-joern.cfg`, `haproxy-joern-mcp-scale.cfg` |

**Do not use archived compose files for new deployments.** They may still publish MCP on port :9000 and reference obsolete HAProxy backends.

From the repo root:

```bash
# dev — single replica
docker compose -f deploy/compose.dev.yml up -d

# scale — HAProxy + N replicas
docker compose -f deploy/compose.scale.yml up -d --scale joern=4
```

See `deploy/README.md` for the decision table and session stickiness (`X-Session-Id`).
