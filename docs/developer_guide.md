# Developer guide

Guide for contributors and **coding agents** working in this repository.

---

## What this repo is

| Piece | Path | Role |
|-------|------|------|
| HTTP proxy | `joern_server/proxy.py` | Public API on :8080 |
| HTTP client | `joern_server/client.py` | `JoernHTTPQueryExecutor` |
| Metrics | `joern_server/metrics.py` | Prometheus text for `/metrics` |
| Docker image | `docker/Dockerfile` | Joern CLI + Python proxy |
| Entrypoint | `docker/unified-entrypoint.sh` | Start Joern, wait, start proxy, supervise |
| Dev compose | `deploy/compose.dev.yml` | Single replica |
| Scale compose | `deploy/compose.scale.yml` | HAProxy + N replicas |
| HAProxy | `deploy/haproxy.cfg` | Sticky routing |

Not in scope: MCP :9000 stacks under `deploy/archive/`.

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

Change `joern_server/*.py`, then either:

**Hot-patch** into running containers (fast, not durable):

```bash
./deploy/hotpatch.sh
docker compose -f deploy/compose.dev.yml restart joern
```

**Rebuild image** (correct for entrypoint or Dockerfile changes):

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
docker compose -f deploy/compose.dev.yml up -d --force-recreate
```

---

## Critical convention: Python imports in Docker

The entrypoint runs:

```sh
export PYTHONPATH=/app
python3 /app/joern_server/proxy.py
```

`proxy.py` must import sibling modules in a way that works both:

- **Tests / package:** `from joern_server.metrics import PrometheusMetrics`
- **Container script:** `from metrics import PrometheusMetrics` (fallback)

When adding new modules under `joern_server/`, keep this dual-import pattern or always set `PYTHONPATH=/app`.

---

## Key proxy concepts

### Affinity map

Class attribute `JoernProxyHandler._affinity_cpg_path: dict[str, str]` maps **affinity key** → CPG path after successful `importCpg`.

- Key from header `X-Affinity-Key` (typically `sample_id`)
- Updated in `/query-sync` handler on `importCpg` success
- Cleared in `_clear_affinity_state()` from `/cleanup`

### REPL serialization

`repl_semaphore = threading.Semaphore(1)` wraps `/query-sync` and `/graph/*`. Parse (`joern-parse` subprocess) runs **outside** the semaphore.

### Health

`GET /health` calls `_probe_joern()` — internal `POST /query-sync` with `val _health = 1`. Returns **503** if Joern is down.

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

1. Add handler method on `JoernProxyHandler` (follow `do_POST` routing in `do_POST`).
2. Use `_log_event()` for structured logs.
3. Use `_json_error(msg, code=...)` for error bodies.
4. If touching REPL: acquire `repl_semaphore` and consider `_activate_session_cpg_if_needed`.
5. Add unit test with mocked `httpx.post`.
6. Document in `docs/api_reference.md`.

---

## Configuration surface

Environment variables are read in `proxy.py` `main()` and in compose files. Prefer:

- Defaults in code via `_env_int` / `_env_str`
- Document new vars in `deploy/.env.example` and `docs/deploy.md`

---

## Project management docs

`.pms/` is **gitignored** (local SRS, sprint backlogs). User-facing docs live in **`docs/`** only.

---

## Agent workflow checklist

Before opening a PR or finishing a task:

- [ ] `pytest tests/unit/ -q` passes
- [ ] If proxy behavior changed: update `docs/ARCHITECTURE.md`, `api_reference.md`, or `query_guide.md`
- [ ] If deploy changed: update `docs/deploy.md` and `deploy/.env.example`
- [ ] If headers or stickiness changed: update `test_haproxy_stickiness.py` and client docstrings
- [ ] Rebuild Docker image if entrypoint or `Dockerfile` changed — do not rely on hot-patch alone
- [ ] Do not run `docker compose --force-recreate` on production scale without explicit approval

---

## Related

- [Architecture](ARCHITECTURE.md)
- [Testing](testing.md)
- [API reference](api_reference.md)
