# Sprint 5 Report

**Sprint:** 5
**Period:** 2026-04-28 – 2026-04-29
**Branch:** `sprint/5`
**Status:** Complete (with 1 post-release bug fix)

---

## Executive Summary

Sprint 5 delivered a browser-based interactive playground for Joern CPG analysis — a Vue 3 single-page application served by the proxy at `/playground`. Researchers can now parse source code, load CPGs, run raw CPGQL queries, and execute all 25 MCP tools without CLI access. The sprint also verified that MCP tool `/parse` calls correctly route through the proxy to benefit from CPGRegistry cache hits.

**Post-release testing** uncovered a server-side bug in the proxy's default filename logic: C/C++ source code parsed without an explicit `filename` field was saved as `snippet.txt`, which the `c2cpg` frontend silently skipped, producing empty CPGs. This has been fixed and verified against the playground workflow.

---

## Sprint Deliverables

| ID     | Title                                          | Status | Notes |
| ------ | ---------------------------------------------- | ------ | ----- |
| S5-001 | MCP /parse proxy routing verification          | Done   | 17 unit tests; cache_hit field verified; 2 regression tests |
| S5-002 | Playground: Vue 3 SPA scaffold                 | Done   | 3-panel layout with responsive CSS; served at /playground |
| S5-003 | Playground: Parse panel                        | Done   | Parse → auto-import CPG for C, Java, JS, Python |
| S5-004 | Playground: Query panel                        | Done   | Raw CPGQL input; result display; 20-entry query history |
| S5-005 | Playground: MCP tool translation table         | Done   | 25 tools in tool-definitions.js; CPGQL generation; 6 groups |
| S5-006 | Playground: MCP Tools panel                    | Done   | Dropdown by group; auto-generated params; _custom tool support |
| S5-007 | Playground unit tests                          | Done   | 17 tests: routes, path traversal, app structure |
| S5-008 | Parse-source proxy routing unit tests          | Done   | 17 tests: cache hit/miss, proxy failure, double-parse |

---

## Post-Release Bug: S5-HF01 — Default filename causes empty CPG for C/C++

### Discovery

During manual verification of the playground with the default example code:

```c
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main() {
    printf("Sum: %d\n", add(3, 4));
    return 0;
}
```

All queries returned empty results. The CPG loaded with only **11 nodes** and a single `<global>` method — missing both `add` and `main`.

### Root Cause

In `joern_server/proxy.py:460`, when no `filename` is provided by the client, the proxy defaults to:

```python
filename = str(data.get("filename", "")).strip() or "snippet.txt"
```

The Joern C/C++ frontend (`c2cpg.sh`) only processes files with recognized C/C++ extensions (`.c`, `.cpp`, `.h`, `.hpp`, `.cxx`, `.cc`). Files with `.txt` extension are silently skipped, producing a CPG with only structural scaffolding nodes.

**Affected languages:** C (`c`), C++ (`cpp`), Objective-C (`.m`), and any language whose frontend uses filename-based source detection.

**Not affected:** Python, JavaScript, Java, C#, Ruby — their respective frontends handle the extension differently or auto-detect based on content.

### Fix

Added a language-to-extension mapping `_LANGUAGE_EXT` and a `_default_filename()` function in `joern_server/proxy.py:95-117` that produces language-appropriate default filenames:

| Language     | Default filename |
| ------------ | ---------------- |
| `c` / `newc` | `snippet.c`      |
| `cpp`        | `snippet.cpp`    |
| `python`     | `snippet.py`     |
| `javascript` | `snippet.js`     |
| `java`       | `snippet.java`   |
| `php`        | `snippet.php`    |
| `ruby`       | `snippet.rb`     |
| `csharp`     | `snippet.cs`     |
| `swift`      | `snippet.swift`  |
| `golang`     | `snippet.go`     |
| `kotlin`     | `snippet.kt`     |
| `rust`       | `snippet.rs`     |
| (unknown)    | `snippet.txt`    |

Updated line 490 from:
```python
filename = str(data.get("filename", "")).strip() or "snippet.txt"
```
to:
```python
filename = str(data.get("filename", "")).strip() or _default_filename(language)
```

### Verification

**Before fix:** `cpg.method.name("add").l` → `List()` (empty)

**After fix:** `cpg.method.name("add").l` → `List(Method(name="add", fullName="add", signature="int(int,int)", filename="snippet.c", ...))`

CPG node count increased from **11** to **68** nodes for the playground default example.

End-to-end playground workflow confirmed working:
1. POST `/parse` → `ok: true`, CPG produced
2. `importCpg("...")` → `Cpg[Graph[68 nodes]]`
3. `load_cpg("...")` → `true`
4. `cpg.method.name("add").l` → returns `add` Method node with full details

### Test Suite

Full unit test suite: **368 passed, 1 skipped, 0 failures** — no regressions introduced.

---

## Phase Summary

### Phase 1 — Planning
Scope defined from PB-038–PB-040: playground UI, MCP tool translation table, proxy cache verification. Risk of CDN dependency identified for air-gapped deployments (mitigated with vendored fallback copies).

### Phase 2 — Analysis
Vue 3 SPA selected for lightweight, no-build-step deployment. Three-panel layout mirrors Jupyter notebook workflow. Proxy serves static files from the same port (8080) to avoid CORS issues. All 25 MCP tools require CPGQL translation functions — each is a direct port of the MCP server's query builder.

### Phase 3 — Implementation
- `playground/index.html` — 3-panel CSS grid layout
- `playground/app.js` — Vue 3 app: parse→load→query→tools state machine
- `playground/tool-definitions.js` — 25 tool definitions with CPGQL generation and parameter specs
- `playground/style.css` — terminal monospace aesthetic
- `joern_server/proxy.py` — `_serve_playground()` handler with path traversal protection

### Phase 4 — Testing & Evaluation
- 17 playground unit tests (routes, path traversal, component structure)
- 17 parse-source unit tests (cache routing, proxy forwarding)
- Full suite: 368 passed, 1 skipped, 0 failures
- Post-release manual testing uncovered S5-HF01 (filename extension bug)

### Phase 5 — Documenting
- Sprint 5 backlog written: `.pms/backlog/sprint5.md`
- Sprint 5 report written: `.pms/report/sprint5-report.md` (this file)
- Product backlog updated: PB-038–PB-040 marked Done; S5-HF01 documented

---

## Files Changed

### Sprint 5 deliverables (playground)
| File | Change |
| ---- | ------ |
| `playground/index.html` | New — Vue 3 SPA shell with 3-panel layout |
| `playground/app.js` | New — Vue app: parse, query, tools workflow |
| `playground/tool-definitions.js` | New — 25 MCP tools with CPGQL translations |
| `playground/style.css` | New — Terminal monospace theme |
| `joern_server/proxy.py` | Modified — Added `/playground` GET route handler |

### Post-release bug fix (S5-HF01)
| File | Change |
| ---- | ------ |
| `joern_server/proxy.py` | Modified — Added `_LANGUAGE_EXT` mapping + `_default_filename()` function; updated default filename logic at line 490 |

---

## Test Results

```
======================== 368 passed, 1 skipped in 2.25s ========================
```

All test suites:
- `tests/unit/test_playground.py` — 17 passed
- `tests/unit/test_parse_source.py` — 17 passed
- `tests/unit/test_find_methods.py`, `test_find_calls.py`, `test_get_dataflow.py`, `test_get_call_arguments.py`, `test_find_literals.py`, `test_get_method_location.py` — all passing
- `tests/unit/test_mcp_tools_complete.py`, `test_mcp_common_tools.py` — all passing
- `tests/unit/test_lru_cache.py`, `test_cpg_cache.py`, `test_query_sync_concurrency.py`, `test_sticky_routing.py`, `test_client_affinity.py`, `test_joern_tool_dispatch.py` — all passing

---

## Revision History

| Date       | Author | Change |
| ---------- | ------ | ------ |
| 2026-04-29 | agent  | Initial sprint 5 report: all S5 items Done; documented S5-HF01 bug fix |
