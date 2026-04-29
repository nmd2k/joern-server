# Sprint 7 Report

**Sprint:** 7
**Period:** 2026-04-30
**Branch:** `sprint/7`
**Status:** Complete

---

## Executive Summary

Sprint 7 delivered four major outcomes:
1. **PDG + AST graph endpoints** — two new backend endpoints (`/graph/pdg`, `/graph/ast`) expose all 4 Joern graph types (CFG, DDG, PDG, AST) via the API proxy.
2. **Node metadata enrichment** — all `/graph/*` endpoints now include per-node metadata (code, line_number, column_number, node_type, order, argument_index) via batch CPGQL queries with 50-node chunking.
3. **Standalone graph visualization** — graph vis extracted from the playground into its own `/graph` page with type selector, hover tooltips, and click detail panel.
4. **Playground component refactor** — `app.js` (350→27 lines) split into 3 Vue components; `index.html` (176→18 lines) is now a minimal scaffold.

---

## Sprint Deliverables

| ID     | Title                                          | Status | Notes |
| ------ | ---------------------------------------------- | ------ | ----- |
| S7-001 | Backend: Add `/graph/pdg` endpoint             | Done   | Uses `cpg.method.fullName(...).dotPdg.l`; DOT parsed via `_dot_to_graph()`; metadata enriched |
| S7-002 | Backend: Add `/graph/ast` endpoint             | Done   | Uses `cpg.method.fullName(...).ast.map(...)` Scala tuples; edges from `astParent.id → node.id` |
| S7-003 | Backend: Enrich graph endpoints with metadata  | Done   | `_fetch_node_metadata()` batch-queries node properties; chunked at 50 IDs; graceful fallback |
| S7-004 | Extract graph visualization into `/graph`      | Done   | Standalone page at `/graph` with its own Vue app + cytoscape; playground 4th panel → link |
| S7-005 | Graph UI: type selector (CFG/DDG/PDG/AST)      | Done   | Dropdown with 4 types; calls `/api/graph/<type>`; `/graph/ddg` alias for DFG |
| S7-006 | Graph UI: hover tooltip with node info         | Done   | `mouseover`/`mouseout` events; shows node type (color-coded), code snippet, line number |
| S7-007 | Graph UI: click detail panel with metadata     | Done   | Collapsible side panel; key-value table of all metadata fields; tap canvas to dismiss |
| S7-008 | Refactor `app.js` into Vue components          | Done   | 3 components: parse-panel (139L), query-panel (125L), tools-panel (190L); shared `window.PlaygroundState` |
| S7-009 | Refactor `index.html` panel templates          | Done   | All panel HTML extracted to component templates; index.html is 18-line layout skeleton |
| S7-010 | Update tests for new endpoints + component split | Done  | 16 new tests in `tests/unit/test_graph_endpoints.py`; playground tests updated; 0 regressions |

---

## Architecture Change

### Before (Sprint 6)
```
Browser → Playground Server (:3000)
           ├── /playground/index.html  (4-panel: parse, query, tools, embedded graph)
           ├── /api/* → Joern Proxy (:8080)
           │              ├── /graph/cfg  (CFG only)
           │              └── /graph/dfg  (DDG only)
           └── /mcp/tools/* → MCP SD

### After (Sprint 7)
```
Browser → Playground Server (:3000)
           ├── /playground/index.html  (3-panel: parse, query, tools)  [350→27 lines JS]
           ├── /graph                  (standalone graph page: type selector, tooltip, detail panel)
           ├── /api/* → Joern Proxy (:8080)
           │              ├── /graph/cfg  + metadata
           │              ├── /graph/dfg  + metadata
           │              ├── /graph/ddg  (alias → dfg)
           │              ├── /graph/pdg  (NEW: Program Dependence Graph + metadata)
           │              └── /graph/ast  (NEW: Abstract Syntax Tree + metadata)
           └── /mcp/tools/* → MCP SDK (SSE) → MCP Server (:9000)
```

### New Files

| File | Description |
| ---- | ----------- |
| `playground-server/public/components/parse-panel.js` | Parse panel Vue component (139 lines) |
| `playground-server/public/components/query-panel.js` | Query panel Vue component (125 lines) |
| `playground-server/public/components/tools-panel.js` | MCP tools panel Vue component (190 lines) |
| `playground-server/public/graph/index.html` | Standalone graph visualization page |
| `playground-server/public/graph/graph-app.js` | Graph Vue 3 app with cytoscape (10.3 KB) |
| `playground-server/public/graph/graph-styles.css` | Graph page CSS (dark theme, tooltip, detail panel) |
| `tests/unit/test_graph_endpoints.py` | 16 unit tests for PDG/AST/metadata endpoints |

### Modified Files

| File | Change |
| ---- | ------ |
| `joern_server/proxy.py` | +184 lines: `_handle_graph_pdg()`, `_handle_graph_ast()`, `_fetch_node_metadata()`, Scala tuple parser, `/graph/ddg` alias |
| `playground-server/public/app.js` | 350→27 lines: thin bootstrap with 3 component registrations |
| `playground-server/public/index.html` | 176→18 lines: minimal layout skeleton, component script tags, graph link |
| `playground-server/server.js` | +6 lines: `GET /graph` route + `express.static` for graph assets |
| `tests/unit/test_playground.py` | Updated paths for component split verification |
| `.pms/backlog/sprint7.md` | All 10 items marked Done |
| `.pms/docs/product_backlog.md` | PB-044–PB-047 marked Done; Sprint 7 section marked Complete |

---

## Test Results

```
======================== 384 passed, 1 skipped in 2.46s ========================
```

Breakdown:
- 368 existing tests pass (0 regressions)
- 16 new graph endpoint tests pass
- All 17 playground tests pass (updated for component refactor)
- 1 skipped (live integration test requiring running Joern server)

New test coverage:
| Test class | Tests | Coverage |
|-----------|-------|---------|
| `TestGraphPdg` | 5 | DOT parsing, 422/504/502 errors, metadata |
| `TestGraphAst` | 5 | Scala tuple parsing, 422/504/502 errors, edges, metadata |
| `TestFetchNodeMetadata` | 5 | Map structure, batch chunking, graceful failure, empty input, non-numeric |
| `TestGraphDdgAlias` | 1 | DDG routes to DFG handler |

---

## Key Decisions

1. **Scala tuple export for AST** — AST is not DOT-based, so we use `cpg.method.fullName(...).ast.map(node => (...8 fields)).l` to export a Scala `List[(Long,String,Option[Int],Option[Int],Int,Int,String,Option[Long],List[Long])]` and parse it with a custom `_parse_scala_tuples()` function. This avoids needing Joern's DOT output for AST.

2. **Metadata batch chunking (50 IDs)** — `_fetch_node_metadata()` splits node IDs into batches of 50 to avoid Joern REPL capacity limits for large graphs. Uses `cpg.all.id(...).map(...).l` to fetch properties for all nodes in one batch.

3. **Graceful metadata fallback** — If the metadata batch query fails (timeout, Joern error), the graph endpoint still returns `{nodes, edges}` without the `metadata` field rather than failing completely.

4. **Component shared state via `window.PlaygroundState`** — `Vue.reactive({sampleId, isLoaded, language})` on the window object lets all 3 panel components share parse/load state without prop drilling or a Vuex/Pinia dependency.

5. **`/graph/ddg` alias** — Registered as a proper alias routing to the existing DFG handler for consistency with Joern's official naming (`.dotDdg`).

---

## Definition of Done Checklist

- [x] S7-001: `/graph/pdg` endpoint returns PDG for a given method
- [x] S7-002: `/graph/ast` endpoint returns AST tree for a given method
- [x] S7-003: All graph endpoints include `metadata` map with node properties
- [x] S7-004: Standalone `/graph` page served by Express, independent of playground
- [x] S7-005: Graph type selector (CFG/DDG/PDG/AST) in frontend
- [x] S7-006: Hover tooltip shows node type, label, and line number
- [x] S7-007: Click detail panel shows full node metadata
- [x] S7-008: `app.js` split into 3 panel components
- [x] S7-009: `index.html` panel templates extracted into component files
- [x] S7-010: Tests for new endpoints + no regressions
- [x] Sprint report written at `report/sprint7-report.md`

---

## Revision History

| Date       | Author | Change |
| ---------- | ------ | ------ |
| 2026-04-30 | agent  | Sprint 7 report: standalone graph vis, all graph types, node metadata, playground refactor |
