# Developer guide

Guide for contributors and **coding agents** working in this repository.

---

## What this repo is

| Piece | Path | Role |
|-------|------|------|
| HTTP API | `joern_server/` (FastAPI + uvicorn) | Public API on :8080 |
| HTTP client | `joern_server/client.py` | `JoernHTTPQueryExecutor` |
| Metrics | `joern_server/metrics.py` | Prometheus text for `/metrics` |
| Docker image | `docker/Dockerfile` | Joern CLI + Python API |
| Entrypoint | `docker/unified-entrypoint.sh` | Start Joern, wait, start uvicorn, supervise |
| Dev compose | `deploy/compose.dev.yml` | Single replica |
| Scale compose | `deploy/compose.scale.yml` | HAProxy + N replicas |
| HAProxy | `deploy/haproxy.cfg` | Sticky routing |
| Playground UI | `playground-server/` | Optional; not served by `joern_server` |

MCP stacks under `deploy/archive/` are **out of scope** (removed Sprint 9).

---

## Package layout

The HTTP API is a modular FastAPI application. Entrypoint:

```sh
export PYTHONPATH=/app
python3 -m uvicorn joern_server.app:app --host 0.0.0.0 --port 8080
```

```
joern_server/
├── app.py                 # create_app(), lifespan, router registration
├── config.py              # Settings.from_env()
├── state.py               # AppState (affinity, cache, registry, semaphore)
├── client.py              # JoernHTTPQueryExecutor
├── metrics.py             # Prometheus text helpers
├── api/
│   ├── deps.py            # get_state() FastAPI dependency
│   └── routers/
│       ├── health.py      # GET /health, /version, /metrics, /cache-metrics
│       ├── query.py       # POST /query-sync
│       ├── parse.py       # POST /parse
│       ├── parse_repo.py  # POST /parse/repo, /parse/repo/upload
│       ├── graph.py       # POST /graph/{cfg,dfg,ddg,pdg,ast}
│       └── cleanup.py     # POST /cleanup
├── parse/                 # single.py, repo.py, runner.py, language.py, …
├── graph/                 # service.py, dot.py, scala_parse.py, …
├── cpg/                   # registry.py, storage.py, paths.py
├── cache/                 # lru.py, query_policy.py
├── session/               # affinity.py, repl_lock.py
├── upstream/              # joern.py — httpx to internal Joern :18080
├── lifecycle/             # cleanup.py
└── util/                  # headers, errors, env, query helpers
```

Import domain modules directly, e.g. `from joern_server.cache.lru import LRUCache`.

Internal design notes: `.pms/docs/sdd/sdd_v3_modular_fastapi.md` (local, gitignored).

---

## Local development loop

### 1. Unit tests (no Docker)

```bash
pytest tests/unit/ -q
```

### 2. Dev container

```bash
docker compose -f deploy/compose.dev.yml up -d --build
curl -s http://127.0.0.1:8080/health | jq .
```

### 3. Edit Python

Change `joern_server/**`, then either:

**Hot-patch** (fast, not durable):

```bash
./deploy/hotpatch.sh
docker compose -f deploy/compose.dev.yml restart joern
```

**Rebuild image** (required for entrypoint, Dockerfile, new dependencies):

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
docker compose -f deploy/compose.dev.yml up -d --force-recreate
```

---

## Unit testing with TestClient

Unit tests exercise the FastAPI app without a live Joern JVM. Use the shared harness in `tests/helpers/app.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state

def test_health_ok(tmp_path):
    state = make_test_state(tmp_path)
    client = TestClient(create_test_app(state=state))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "stdout": "1"}

    with patch("joern_server.upstream.joern.post_query_sync", return_value=mock_resp):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["joern_ok"] is True
```

| Helper | Purpose |
|--------|---------|
| `make_test_state(tmp_path, **overrides)` | Build `AppState` with temp CPG dirs |
| `create_test_app(state=...)` | FastAPI app wired to that state |
| `make_test_client(state=..., tmp_path=...)` | Convenience: app + `TestClient` |

Mock upstream Joern at `joern_server.upstream.joern` (e.g. `post_query_sync`, `probe_joern`). Do not import or spawn the deleted monolithic handler.

---

## Key concepts

### AppState

Process-wide state in `joern_server/state.py`, attached to the FastAPI app in lifespan:

- `affinity_cpg_path` — affinity key → CPG path after successful `importCpg`
- `repl_semaphore` — serializes `/query-sync` and `/graph/*`
- `query_cache` — optional `LRUCache` (enabled when `QUERY_CACHE_MAX_SIZE > 0`)
- `cpg_registry` — always-on `CPGRegistry` backed by a shared SQLite file (WAL mode); supports concurrent access across replicas via the `cpg-archive` volume
- `metrics` — Prometheus metrics collector

### REPL serialization

`repl_semaphore = threading.Semaphore(1)` wraps `/query-sync` and `/graph/*`. Parse (`joern-parse` subprocess) runs **outside** the semaphore.

### Health

`GET /health` probes internal Joern via `POST /query-sync` with `val _health = 1`. Returns **503** if Joern is down.

### Errors on `/query-sync`

| Exception | HTTP | `code` |
|-----------|------|--------|
| `httpx.TimeoutException` | 504 | `upstream_timeout` |
| `httpx.HTTPError` | 502 | `upstream_unreachable` |
| Other | 502 | `upstream_error` |

Body includes `request_id` when available.

---

## HAProxy and headers

Clients **must** send `X-Affinity-Key: <sample_id>` for correct behavior in scale mode.

Update `deploy/haproxy.cfg` only with matching changes to docs and `test_haproxy_stickiness.py`.

---

## Adding an HTTP endpoint

1. Implement logic in `joern_server/<domain>/` (e.g. `parse/`, `graph/`).
2. Add route in `joern_server/api/routers/<area>.py`.
3. Register router in `joern_server/app.py`.
4. Unit test with `tests/helpers/app.py` + `TestClient`; mock `joern_server.upstream.joern`.
5. Document in `docs/api_reference.md`.
6. If touching parse/cleanup: add Prometheus counters per SDD v3.

---

## Configuration surface

Environment variables are loaded in `joern_server/config.py` from env with defaults. Document new vars in `deploy/.env.example` and `docs/deploy.md`.

---

## Project management docs

`.pms/` is **gitignored** (local SRS, sprint backlogs, agent guide). User-facing docs live in **`docs/`** only; mirror critical HTTP/deploy changes here when sprint items complete.

---

## Agent workflow checklist

Before opening a PR or finishing a task:

- [ ] Read `.pms/backlog/sprint10.md` for current item status
- [ ] `pytest tests/unit/ -q` passes
- [ ] If HTTP behavior changed: update `docs/ARCHITECTURE.md`, `api_reference.md`, or `query_guide.md`
- [ ] If deploy changed: update `docs/deploy.md` and `deploy/.env.example`
- [ ] If headers or stickiness changed: update `test_haproxy_stickiness.py` and client docstrings
- [ ] Rebuild Docker image if entrypoint or `Dockerfile` changed
- [ ] Do not run `docker compose --force-recreate` on production scale without explicit approval

---

## Related

- [Architecture](ARCHITECTURE.md)
- [Testing](testing.md)
- [API reference](api_reference.md)
