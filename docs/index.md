# Joern Server

HTTP API and Docker deployment for [Joern](https://joern.io/) Code Property Graph (CPG) analysis. Remote clients send **CPGQL** to port **8080**; the FastAPI service handles parsing, session affinity, and optional horizontal scaling behind HAProxy.

This documentation describes the **current repository state** for operators, integrators, and coding agents extending the project.

---

## Documentation map

| Document | Audience | Contents |
|----------|----------|----------|
| [Architecture](ARCHITECTURE.md) | Everyone | Components, request flow, headers, storage, scaling |
| [Getting started](getting_started.md) | New users | Local dev setup, first parse + query |
| [Query guide](query_guide.md) | Client authors | Parse modes, CPGQL, headers, cleanup |
| [Deployment](deploy.md) | Operators | Compose profiles, HAProxy, monitoring, env vars |
| [Testing](testing.md) | Contributors | pytest layout, live and stress tests |
| [Developer guide](developer_guide.md) | Coding agents | Repo layout, conventions, safe change workflow |
| [API reference](api_reference.md) | Integrators | HTTP endpoints + Python client reference |

---

## Repository layout (high level)

```
joern-server/
├── joern_server/              # Python FastAPI HTTP API + client
│   ├── app.py                 # FastAPI entry (uvicorn joern_server.app:app)
│   ├── config.py              # Settings from environment
│   ├── state.py               # AppState (affinity, cache, registry)
│   ├── client.py              # JoernHTTPQueryExecutor
│   ├── metrics.py             # Prometheus text metrics
│   ├── api/
│   │   ├── deps.py
│   │   └── routers/           # health, query, parse, parse_repo, graph, cleanup
│   ├── parse/                 # Parse pipeline + language aliases
│   ├── graph/                 # CFG/DFG/DDG/PDG/AST
│   ├── cpg/                   # Registry and storage
│   ├── cache/                 # LRU query cache
│   ├── session/               # Affinity and REPL lock
│   ├── upstream/              # httpx client to Joern :18080
│   ├── lifecycle/             # Cleanup helpers
│   └── util/                  # Shared helpers
├── docker/                    # Unified image, entrypoint, healthcheck
├── deploy/                    # compose.dev.yml, compose.scale.yml, haproxy.cfg
├── tests/
│   ├── helpers/app.py         # TestClient harness
│   ├── unit/
│   ├── integration/
│   └── stress/
├── playground-server/         # Optional web UI (Node)
└── docs/                      # This site (MkDocs)
```

---

## Agent quick checklist

When integrating or modifying this repo:

1. **Parse** is stateless — `POST /parse` or `/parse/repo` writes to shared `cpg-out`.
2. **Query** is stateful — use `X-Affinity-Key: <sample_id>` on every `/query-sync` after `importCpg`.
3. **Logging** — use `X-Session-Id` for the agent run (separate from affinity).
4. **Scaled deploy** — one VIP (`:8080`); HAProxy sticks on `X-Affinity-Key`.
5. **Cleanup** — `POST /cleanup` removes disk CPG and in-memory affinity state on that replica.
6. **Health** — `GET /health` probes Joern; returns **503** if the REPL is unreachable.
7. **Docker** — API runs via uvicorn `joern_server.app:app` with `PYTHONPATH=/app` (see [Developer guide](developer_guide.md)).

---

## Quick start

```bash
cp deploy/.env.example deploy/.env
# set JOERN_SERVER_AUTH_PASSWORD in deploy/.env

docker compose -f deploy/compose.dev.yml up -d
curl -s http://127.0.0.1:8080/health | jq .
```

Scaled (10 replicas example):

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

Build docs locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

---

## External references

- [Joern documentation](https://docs.joern.io/)
- [deploy/README.md](../deploy/README.md) — short deploy pointer
