# Drain, Memory Restart & Cache Resilience — Handoff

**Date:** 2026-05-25  
**Branch:** `fix/cpg-cache-reliability`  
**Commits:** `b7a883f`..`a519ae1` (6 commits on top of snapshot `582d901`)

---

## Summary

Added graceful JVM restart (with drain) when container memory exceeds threshold, and fixed a critical cache regression where replica restarts wiped the in-memory parse cache.

---

## Problems Addressed

### 1. JVM Heap Never Released (OOM on long-running replicas)

Joern retains heap after `close`. After many parse→query→cleanup cycles, container RSS grows until OOM. G1 periodic GC helps slowly but not enough under load.

**Solution:** After cleanup, if no CPG is loaded and container cgroup RSS > `JOERN_MEMORY_RESTART_MB`, schedule a graceful drain + supervised JVM restart.

### 2. Drain Must Not Disrupt Clients

When a replica restarts, active sessions lose their sticky backend. New parse requests must not see 503.

**Solution:** 
- Replica enters drain mode → `/health` returns 503 → HAProxy marks it down (`fall 1`, ~2s)
- `retry-on 503` + `option redispatch` + `retries 3` → new `X-Affinity-Key` sessions transparently redirected to healthy replicas
- After `JOERN_DRAIN_SEC` (default 7s), entrypoint restarts JVM

### 3. Cache Hits Lost After Restart

The in-memory `sid_to_hash` dict (mapping sample_id → source_hash) was the primary fast path for parse dedup. After drain/restart, it was wiped, causing:
- `overwrite=False` requests → 409 CONFLICT (couldn't verify hash)
- `overwrite=True` requests → re-parse from scratch (~3s each)

**Solution:**
- On startup: scan `.joern_hash` sidecars in `cpg-out/` and rebuild `sid_to_hash`
- At parse time: if `sid_to_hash` misses, read `.joern_hash` sidecar from disk before falling through

---

## What Was Implemented

### New Files

| File | Purpose |
|------|---------|
| `joern_server/lifecycle/drain.py` | `begin_draining()`, `schedule_joern_restart_after_drain()` |
| `joern_server/lifecycle/joern_restart.py` | `request_joern_restart()`, `maybe_request_joern_restart_after_cleanup()`, cgroup RSS reader |
| `joern_server/api/middleware/drain.py` | `DrainMiddleware` — 503 JSON on all routes except health/metrics/debug when draining |
| `joern_server/api/routers/debug.py` | `GET /debug/drain/enabled`, `POST /debug/drain` (staging only) |
| `scripts/test_drain_live.sh` | End-to-end drain test through HAProxy VIP |
| `tests/integration/test_drain_haproxy.py` | Pytest: drain one replica, flood new sessions, verify 200 |
| `tests/unit/test_drain.py` | Unit tests for drain lifecycle |
| `tests/unit/test_joern_restart.py` | Unit tests for restart trigger |

### Modified Files

| File | Change |
|------|--------|
| `joern_server/state.py` | `draining`, `restart_scheduled`, `drain_lock` fields; `_rebuild_sid_to_hash()` on startup |
| `joern_server/config.py` | `joern_memory_restart_mb`, `joern_drain_sec`, `enable_drain_test` settings |
| `joern_server/app.py` | Register `DrainMiddleware` and `debug_router` |
| `joern_server/api/routers/health.py` | Return 503 with `"draining": true` when draining |
| `joern_server/lifecycle/cleanup.py` | Call `maybe_request_joern_restart_after_cleanup()`, improved `close` detection |
| `joern_server/parse/single.py` | Fall back to `.joern_hash` sidecar on disk when `sid_to_hash` is empty |
| `joern_server/cpg/paths.py` | Added `cpg_paths_equal()` |
| `deploy/haproxy.cfg` | `retry-on 503 502 conn-failure empty-response`, `option redispatch`, `retries 3`, `fall 1` |
| `deploy/compose.scale.yml` | `JOERN_MEMORY_RESTART_MB`, `JOERN_DRAIN_SEC`, `JOERN_ENABLE_DRAIN_TEST` env vars |
| `docker/unified-entrypoint.sh` | Watch `/tmp/joern-restart.requested`, G1 periodic GC flags |
| `deploy/.env.example` | Production-oriented defaults (4g/10g/6144) |
| `docs/deploy.md` | Drain docs, memory table, new env vars |
| `docs/ARCHITECTURE.md` | Drain lifecycle, FileCPGRegistry, middleware |
| `docs/testing.md` | Drain test layout and commands |
| `docs/api_reference.md` | Debug endpoints, draining health response |
| `docs/developer_guide.md` | Updated package layout |

---

## Key Lessons Learned

### 1. HAProxy `retry-on 503` requires ≥3 backends

Draining 1 replica leaves N-1. If N=2 and both are in transient states (one draining, one restarting from a previous drain), HAProxy has no backend → HTML 503 (`No server is available...`). Always run ≥3 replicas in production.

### 2. HAProxy HTML 503 ≠ App JSON 503

HAProxy's default error page is raw HTML. The app's drain response is JSON with `code: replica_draining`. Don't confuse them in tests or monitoring. The HTML means **no backend**, the JSON means **one specific backend is draining but others should be available**.

### 3. Docker healthy ≠ HAProxy backend ready

Containers can report `healthy` to Docker while HAProxy hasn't yet resolved their DNS or passed its health check (`inter 2s`). Tests must wait for `X-Served-By` visibility, not just `docker ps --filter health=healthy`.

### 4. In-memory state is the real cache — not the file archive

`sid_to_hash` provided the actual fast path (~1ms). The file-based archive (FileCPGRegistry) only helps when the same SOURCE CODE is sent with a DIFFERENT sample_id. After restart, the in-memory dict must be reconstructed from `.joern_hash` sidecars or the cache dies.

### 5. `.joern_hash` sidecars must be written at parse time

Old CPGs parsed before the sidecar feature was added cannot be cache-verified after restart. Always write the sidecar alongside the CPG. Over time, all production entries will have sidecars and restarts become seamless.

---

## Configuration (Production)

```env
# deploy/.env
JOERN_JAVA_XMX=4g                   # JVM heap for Joern REPL
JOERN_MEMORY_LIMIT=10g              # Docker cgroup cap
JOERN_MEMORY_RESTART_MB=6144        # Drain+restart when idle RSS > this
JOERN_DRAIN_SEC=7                   # Wait for HAProxy mark-down before restart
JOERN_ENABLE_DRAIN_TEST=0           # MUST be 0 in production
CPG_ARCHIVE_MAX_COUNT=5000          # LRU archive capacity
```

Scale: `docker compose -f deploy/compose.scale.yml --env-file deploy/.env up -d --scale joern=3`

---

## Drain Flow (visual)

```
Cleanup → RSS > threshold → begin_draining()
  ├── /health → 503 {"draining": true}
  ├── All other routes → 503 {"code": "replica_draining"}
  ├── HAProxy: fall 1 → mark DOWN after 1 failed check (~2s)
  ├── HAProxy: retry-on 503 → redispatch new keys to other backends
  └── sleep(JOERN_DRAIN_SEC) → write /tmp/joern-restart.requested
        └── unified-entrypoint detects flag → kill JVM → restart
              └── /health → 200 → HAProxy: rise 2 → mark UP
```

---

## Test Commands

```bash
# Unit tests (no Docker)
pytest tests/unit/ -q

# Live drain test (staging with JOERN_ENABLE_DRAIN_TEST=1, ≥3 replicas)
./scripts/test_drain_live.sh

# Or directly:
NEURALATLAS_RUN_DRAIN_TESTS=1 pytest tests/integration/test_drain_haproxy.py -m integration -v
```

---

## What Remains / Future Work

1. **Rebuild image** — hotpatch is not persistent; rebuild with `docker build -t neuralatlas-joern:local -f docker/Dockerfile .` before next `docker compose up`
2. **Legacy CPGs without `.joern_hash`** — 5,800+ entries parsed before the sidecar feature; cache won't work for these after restart until they're re-parsed
3. **Optional: archive at parse time** — currently only `cleanup(archive=true)` populates the file archive; archiving immediately after parse would make the registry more useful
4. **Optional: `JOERN_RESTART_ON_EVERY_CLEANUP=1`** — for aggressive scanning where memory grows fast; not implemented yet
5. **Volume cleanup** — legacy `cpg-out` flat files from old deployments still present (6,000+ entries)
6. ~~**Thundering-herd restarts**~~ — **Fixed** in [staggered_restart.md](staggered_restart.md): added random jitter + HAProxy VIP health gating to prevent simultaneous restarts across replicas
