# Sprint 9 — HTTP-first platform (approved plan)

**Branch:** `sprint/9`  
**Full backlog:** `.pms/backlog/sprint9.md`  
**Design:** `.pms/docs/sdd/sdd_v2_http_platform.md`

---

## Goals

1. Remove MCP from deploy; HTTP `:8080` only.  
2. **Repo CPG** for remote clients via **JSONL** + **upload** for heavy trees.  
3. Document architecture and client usage (file vs repo).  
4. Benchmark file-only vs repo parse quality and latency.  
5. Simplify `deploy/` to two compose profiles.

---

## Parse API (approved)

### Single file (existing)

```
POST /parse
{ "sample_id", "source_code", "language?", "filename?", "overwrite?" }
```

### Repo — JSONL (primary, remote clients)

```
POST /parse/repo?sample_id=my-app&language=c&overwrite=false
Content-Type: application/x-ndjson

{"path":"src/main.c","content":"..."}
{"path":"src/util.h","content":"..."}
```

One request → one temp tree → one `joern-parse` → one CPG.

### Repo — heavy upload (two steps)

```
POST /parse/repo/upload     multipart: archive=@repo.zip
→ { "upload_id", "expires_at" }

POST /parse/repo            application/json
→ { "sample_id", "upload_id", "language", "overwrite" }
```

### Repo — ops only (server-mounted data)

```json
{ "sample_id", "source_root": "/workspace/datasets/...", "language", "overwrite" }
```

---

## Sprint 9 backlog (summary)

| ID | Deliverable |
|----|-------------|
| S9-001 | Remove MCP from Docker/compose |
| S9-002 | Remove playground MCP bridge |
| S9-003 | JSONL `POST /parse/repo` |
| S9-004 | Upload staging + `upload_id` parse |
| S9-005 | Ops `source_root` (medium) |
| S9-006 | Tree hash + CPGRegistry cache |
| S9-007 | `docs/ARCHITECTURE.md`, `docs/CLIENT_GUIDE.md` | **Done** |
| S9-008 | Complete `http_api.md` | **Done** |
| S9-009 | File vs repo benchmarks |
| S9-010 | Consolidate `deploy/` |
| S9-011 | Close PB-005 (superseded) |
| S9-012 | Remove MCP + tool wrappers entirely | **Done** (no `mcp-joern/`, CPGQL-only) |

---

## Documentation status (S9-007 / S9-008 / S9-012)

| Deliverable | Path | Status |
|-------------|------|--------|
| Architecture | `docs/ARCHITECTURE.md` | Done |
| Client guide | `docs/CLIENT_GUIDE.md` | Done |
| HTTP API (repo parse) | `.pms/docs/api/http_api.md` | Done |

Docs describe the **approved** API contract; implementation may land in parallel (S9-003, S9-004).

---

## Out of scope

Git clone on server, async job polling, multi-language monorepo in one parse.

---

## Merge prerequisite

Merge Sprint 7 (#8) and Sprint 8 (#9) to `main` before branching `sprint/9`.
