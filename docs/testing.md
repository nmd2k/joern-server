# Testing guide

How to run and extend tests for Joern Server. Commands are from the **repository root**.

---

## Test layout

```
tests/
├── unit/                              # Fast, no live Joern
│   ├── test_deep_health.py
│   ├── test_affinity_cleanup.py
│   ├── test_query_sync_concurrency.py
│   ├── test_client_affinity.py
│   ├── test_sticky_routing.py
│   ├── test_lru_cache.py
│   ├── test_cpg_cache.py
│   ├── test_parse_repo.py
│   └── test_graph_endpoints.py
├── integration/
│   ├── test_joern_http_proxy.py       # Mock Joern subprocess
│   └── test_haproxy_stickiness.py     # Live VIP (opt-in)
└── stress/
    ├── test_joern_live_stress.py
    ├── test_session_lifecycle.py      # 250-session endurance (opt-in)
    └── test_primevul_stress.py        # PrimeVul JSONL parse + query (opt-in)
```

Markers (`pytest.ini`):

| Marker | Meaning |
|--------|---------|
| `integration` | Needs running server or external setup |
| `stress` | Long-running endurance tests |

---

## Quick commands

```bash
# Default CI-friendly run
pytest tests/unit/ -q

# All non-live tests
pytest -m "not integration and not stress" -q

# Mock integration (spawns proxy + mock Joern)
pytest tests/integration/test_joern_http_proxy.py -q

# Live stack on localhost:8080
pytest tests/stress/test_joern_live_stress.py -m integration -v

# HAProxy stickiness (scale profile must be up)
NEURALATLAS_RUN_HAPROXY_TESTS=1 \
  pytest tests/integration/test_haproxy_stickiness.py -m integration -v

# 250-session lifecycle (long; ensure stack healthy first)
NEURALATLAS_STRESS_LIFECYCLE_SESSIONS=250 \
NEURALATLAS_STRESS_LIFECYCLE_BATCH=8 \
  pytest tests/stress/test_session_lifecycle.py -m stress -v

# PrimeVul JSONL: 200 samples, 5 threads, 4x cpg.method.name.l (2s apart)
NEURALATLAS_PRIMEVUL_JSONL=/datadrive/data/raw/primevul/primevul_test_paired.jsonl \
NEURALATLAS_STRESS_PRIMEVUL_LIMIT=200 \
NEURALATLAS_STRESS_PRIMEVUL_WORKERS=5 \
  pytest tests/stress/test_primevul_stress.py -m stress -v -s
```

---

## Environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `NEURALATLAS_LIVE_JOERN_URL` | `http://127.0.0.1:8080` | Live / stress / HAProxy tests |
| `NEURALATLAS_RUN_HAPROXY_TESTS` | `0` | Enable HAProxy integration tests |
| `NEURALATLAS_HAPROXY_STICKY_REQUESTS` | `15` | Stickiness repeat count |
| `NEURALATLAS_STRESS_SESSIONS` | `16` | Concurrent session stress |
| `NEURALATLAS_STRESS_QUERIES_PER_SESSION` | `20` | Queries per session |
| `NEURALATLAS_STRESS_LIFECYCLE_SESSIONS` | `250` | Lifecycle test count |
| `NEURALATLAS_STRESS_LIFECYCLE_BATCH` | `8` | Parallel lifecycles |
| `NEURALATLAS_STRESS_LIFECYCLE_QUERIES` | `10` | Queries after import per session |
| `NEURALATLAS_STRESS_PARALLEL_CPG` | `0` | Set `1` for parallel parse/import (multi-replica) |
| `NEURALATLAS_RUN_REAL_JOERN_HTTP` | `0` | Real Joern sample CPG test |

---

## Writing tests

### Unit tests

- Mock `httpx.post` for `/query-sync` paths.
- Reset class-level state on `JoernProxyHandler` between tests:

```python
JoernProxyHandler._affinity_cpg_path = {}
JoernProxyHandler._active_affinity_key = None
JoernProxyHandler._active_cpg_path = None
```

- Use `X-Affinity-Key` in handler headers when testing session CPG logic.

### Live tests

Prerequisites:

1. `curl -s http://127.0.0.1:8080/health` returns `"joern_ok": true`
2. Clients send `X-Affinity-Key` matching `sample_id`

Stress tests use `JoernHTTPQueryExecutor` with `affinity_key=sample_id`.

### HAProxy test

Requires scale profile:

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=3
NEURALATLAS_RUN_HAPROXY_TESTS=1 pytest tests/integration/test_haproxy_stickiness.py -v
```

Asserts identical `X-Served-By` for repeated requests with the same `X-Affinity-Key`.

---

## Smoke CLI

```bash
python -m joern_server   # runs smoke.py against configured URL
```

---

## Related

- [Developer guide](developer_guide.md)
- [Deployment](deploy.md)
