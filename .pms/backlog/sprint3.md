# Sprint 3 Backlog

**Sprint:** 3
**Branch:** `sprint/3`
**Start date:** 2026-04-19
**End date:** 2026-05-02
**Goal:** CPG-level caching — hash-based deduplication, tag-based archiving, disk eviction, full test coverage, updated docs.

---

## Items

| ID     | Title                                              | Type    | Priority | Status | Notes |
| ------ | -------------------------------------------------- | ------- | -------- | ------ | ----- |
| S3-001 | CPG hash-based deduplication in `/parse`           | Feature | High     | Done | Compute SHA-256 of `source_code`; if matching CPG exists in archive, symlink/copy → skip joern-parse |
| S3-002 | Tag-based CPG archiving in `/cleanup`              | Feature | High     | Done | Accept `{"sample_id": "...", "archive": true}`; move CPG to archive dir instead of delete |
| S3-003 | Disk LRU eviction for CPG archive                  | Feature | High     | Done | Configurable `CPG_ARCHIVE_MAX_GB` / `CPG_ARCHIVE_MAX_COUNT`; evict LRU entries when limit hit |
| S3-004 | CPG cache index (hash → archive path registry)     | Feature | High     | Done | JSON file-backed registry mapping `source_hash` → `{archive_path, sample_id, timestamp, size_bytes}` |
| S3-005 | Unit + integration tests for CPG cache             | Testing | High     | Done | 15 unit tests + 10 integration tests; 258 passed, 0 new failures |
| S3-006 | Update HTTP API docs for new endpoints/fields      | Chore   | Medium   | Done | Document `archive` field in `/cleanup`, `cache_hit` field in `/parse` response, new env vars |
| S3-007 | Update SRS and product backlog for Sprint 3 scope  | Chore   | Medium   | Done | Promote CPG cache from "Future Considerations" to implemented features; close PB-008 |

---

## Acceptance Criteria

### S3-001 — CPG hash-based deduplication

- `POST /parse` computes `SHA-256(source_code)` (hex digest)
- Checks CPG archive registry for matching hash
- On **cache hit**: skips `joern-parse`, copies archived CPG to `cpg-out/<sample_id>`, returns `{"ok": true, ..., "cache_hit": true}`
- On **cache miss**: runs `joern-parse` as today, no behavior change, returns `"cache_hit": false`
- Hash is stored in the parse response log event
- Thread-safe: concurrent parses with same hash do not double-parse

### S3-002 — Tag-based CPG archiving

- `POST /cleanup` accepts optional `"archive": true` in JSON body
- When `archive=true`: moves CPG from `cpg-out/<sample_id>` to `cpg-archive/<source_hash>/` and records entry in registry
- When `archive=false` (default): existing hard-delete behavior unchanged
- Response includes `"archived": true/false`
- Registry entry includes: `source_hash`, `sample_id`, `archived_at`, `size_bytes`, `last_used`

### S3-003 — Disk LRU eviction

- New env vars: `CPG_ARCHIVE_MAX_COUNT` (default 100), `CPG_ARCHIVE_MAX_GB` (default 50)
- Eviction runs synchronously after each archive operation
- Evicts LRU entries (by `last_used`) until both limits satisfied
- Evicted entries: delete from disk + remove from registry
- Eviction count logged as structured JSON event

### S3-004 — CPG cache index

- Registry stored at `CPG_OUT_DIR/../cpg-registry.json` (sibling to `cpg-out/`)
- Format: `{ "<sha256>": { "archive_path": "...", "sample_id": "...", "archived_at": "ISO8601", "last_used": "ISO8601", "size_bytes": int } }`
- Protected by file-level lock (or in-process `threading.Lock`) — no corruption under concurrent access
- On startup: load registry; skip missing archive paths (self-healing)
- On corruption: log warning, start with empty registry

### S3-005 — Tests

- `tests/unit/test_cpg_cache.py` — mock filesystem; ≥10 unit tests covering all acceptance criteria
- `tests/integration/test_cpg_cache_integration.py` — uses real temp directory; tests full parse→archive→cache-hit→evict flow
- All existing tests still pass (no regressions)

### S3-006 — API docs

- `POST /parse` response: add `cache_hit: bool` field documentation
- `POST /cleanup` request: add optional `archive: bool` field; response: add `archived: bool`
- New env vars table: `CPG_ARCHIVE_MAX_COUNT`, `CPG_ARCHIVE_MAX_GB`, `CPG_ARCHIVE_DIR`

### S3-007 — Docs/backlog updates

- SRS Section 5 (CPG Lifecycle): add "Archived" state and transitions
- SRS Section 9 (Future Considerations): remove "CPG caching layer" — it is now implemented
- product_backlog.md: close PB-008; add S3 items as Done after sprint completes

---

## Design Notes

### CPG Archive Directory Layout

```
/workspace/
├── cpg-out/                        # Active CPGs (existing)
│   └── <sample_id>/
├── cpg-archive/                    # Archived CPGs (new)
│   └── <sha256_hex>/               # Keyed by source hash
│       └── <cpg files>
└── cpg-registry.json               # Hash → archive path index (new)
```

### `/parse` Flow with Cache

```
POST /parse {sample_id, source_code, language, ...}
  │
  ├─ hash = SHA-256(source_code)
  ├─ registry.lookup(hash) → hit?
  │    YES → copy cpg-archive/<hash>/ → cpg-out/<sample_id>/
  │           update registry.last_used
  │           return {ok:true, cache_hit:true}
  │    NO  → run joern-parse as before
  │           return {ok:true, cache_hit:false}
```

### `/cleanup` Flow with Archive Tag

```
POST /cleanup {sample_id, archive: true}
  │
  ├─ look up source_hash for this sample_id (from in-flight parse log or registry reverse lookup)
  ├─ move cpg-out/<sample_id>/ → cpg-archive/<hash>/
  ├─ write registry entry
  ├─ run disk eviction if over limits
  └─ return {ok:true, deleted:false, archived:true}
```

### RAM Footprint

- Registry is JSON on disk; loaded into memory as a dict — negligible (one entry ≈ 200 bytes, 1000 entries ≈ 200KB)
- No in-memory CPG data; all CPG bytes stay on disk
- Query result LRU cache (Sprint 1) unchanged — still caches CPGQL responses in RAM, not CPG bytes
