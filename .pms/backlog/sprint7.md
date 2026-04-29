# Sprint 7 Backlog

**Sprint:** 7
**Period:** 2026-04-30 – 2026-05-14 (2 weeks)
**Goal:** Extract graph visualization into standalone service; add all Joern graph types with interactive node metadata; refactor playground file organization.
**Branch:** `sprint/7`

---

## Context

Sprint 6 delivered CPG graph visualization (CFG/DFG) as a 4th panel inside the playground, and extracted the playground into a standalone Node.js/Express server. However:

1. **Graph vis is tightly coupled to playground** — the cytoscape.js rendering, graph state, and panel UI are mixed into `app.js` and `index.html`. To support richer interactions (hover tooltips, click-detail panels) and future embedding in other tools, the graph visualization needs to be a self-contained service/page with its own URL.

2. **Only 2 of 4 Joern graph types are surfaced** — Joern supports `.dotCfg`, `.dotDdg`, `.dotPdg` (DOT-format graphs per method), plus an AST tree for whole files. Sprint 6 only exposes CFG and DFG (via `.dotDdg`). PDG and AST are missing.

3. **Node metadata is limited to labels** — the `_dot_to_graph()` parser extracts only `id`, `label`, and `shape` from DOT output. Users need richer node properties (code snippet, line number, parameter types, identifier names) displayed via hover tooltips and a click detail panel.

4. **Playground `app.js` is monolithic** — 350 lines of mixed Vue state, API calls, MCP execution, cytoscape rendering, and UI helpers. As features grow, this file becomes unmaintainable.

---

## Backlog Items

| ID     | PB Ref | Title                                                              | Type    | Priority | Status | Acceptance Criteria |
| ------ | ------ | ------------------------------------------------------------------ | ------- | -------- | ------ | ------------------- |
| S7-001 | PB-045 | Backend: Add `/graph/pdg` endpoint                                 | Feature | High     | Done   | `POST /graph/pdg` accepts `method_full_name`, queries `cpg.method.fullName(...).dotPdg.l`, parses DOT → `{nodes, edges, metadata}`, returns 422 on empty result. |
| S7-002 | PB-045 | Backend: Add `/graph/ast` endpoint                                 | Feature | High     | Done   | `POST /graph/ast` accepts `method_full_name`, queries `cpg.method.fullName(...).ast` via Scala tuple export, returns JSON `{nodes, edges, metadata}` with parent-child edges. Falls back gracefully on errors. |
| S7-003 | PB-046 | Backend: Enrich graph endpoints with node metadata                 | Feature | High     | Done   | All `/graph/*` endpoints return an extra `metadata` map keyed by node ID containing: `code`, `line_number`, `column_number`, `node_type`, `order`, `argument_index`. Batch-queried with chunking (50/batch). |
| S7-004 | PB-044 | Extract graph visualization into standalone page                   | Feature | High     | Done   | New Express route `/graph` serves a standalone HTML page (`public/graph/index.html`) independent of the playground. Has its own URL, Vue state, and cytoscape instance. Playground's 4th panel becomes a link to `/graph`. |
| S7-005 | PB-045 | Graph UI: type selector (CFG / DDG / PDG / AST)                    | Feature | High     | Done   | Dropdown/segmented control to switch graph type. Calls the appropriate `/api/graph/<type>` endpoint (cfg, ddg, pdg, ast). Retains `method_full_name` input. |
| S7-006 | PB-046 | Graph UI: hover tooltip with basic node info                       | Feature | High     | Done   | Hovering a node shows a floating tooltip with: node label, node type (color-coded), line number (if available). Uses cytoscape.js `mouseover`/`mouseout` events + a positioned `<div>`. |
| S7-007 | PB-046 | Graph UI: click detail panel with full node metadata               | Feature | High     | Done   | Clicking a node opens a collapsible side panel showing: `id`, `label`, `code`, `line_number`, `column_number`, `node_type`, `order`, `argument_index`, plus any additional properties. |
| S7-008 | PB-047 | Refactor playground `app.js`: split into Vue components            | Chore   | Medium   | Done   | Split `app.js` (350→27 lines) into `components/parse-panel.js` (139L), `components/query-panel.js` (125L), `components/tools-panel.js` (190L). Shared state via `window.PlaygroundState`. |
| S7-009 | PB-047 | Refactor playground `index.html`: extract panel templates          | Chore   | Medium   | Done   | Extract each panel's HTML template into its own component file. `index.html` (176→18 lines) becomes minimal layout scaffold. |
| S7-010 | —      | Update tests for new graph endpoints + component split             | Testing | High     | Done   | Add unit tests for `/graph/pdg`, `/graph/ast`, metadata enrichment. Update playground tests to verify component split doesn't break existing workflows. No regressions. |

---

## Technical Design

### 1. Graph Visualization as Standalone Page

**Before (Sprint 6):**
```
Browser → Playground Server (:3000)
            └── /playground/index.html
                  ├── Panel 1: Parse
                  ├── Panel 2: Query
                  ├── Panel 3: MCP Tools
                  └── Panel 4: Graph Visualization  ← embedded panel
```

**After (Sprint 7):**
```
Browser → Playground Server (:3000)
            ├── /playground/index.html  (panels 1-3, link to /graph)
            └── /graph                  (standalone graph page)
                  ├── Method input + graph type selector
                  ├── Cytoscape.js canvas
                  ├── Hover tooltip overlay
                  └── Click detail side panel
```

**Express route:**
```javascript
// server.js addition
app.get('/graph', (req, res) => {
  res.sendFile(path.join(PLAYGROUND_DIR, 'graph', 'index.html'));
});
app.use('/graph', express.static(path.join(PLAYGROUND_DIR, 'graph')));
```

**File layout:**
```
playground-server/public/
├── index.html              (playground — panels 1-3 only)
├── app.js                  (thin bootstrap)
├── components/
│   ├── parse-panel.js
│   ├── query-panel.js
│   └── tools-panel.js
├── graph/
│   ├── index.html          (standalone graph page)
│   ├── graph-app.js        (graph-specific Vue app)
│   └── graph-styles.css
├── tool-definitions.js
└── styles.css
```

### 2. New Graph Endpoints

#### `POST /graph/pdg`

Same pattern as `/graph/cfg` but uses `dotPdg`:
```python
query = f'cpg.method.fullName("{escaped}").dotPdg.l'
```

#### `POST /graph/ast`

AST is non-DOT. Uses Joern's `cpg.method.fullName(...).ast` traversal to build a tree:
```python
# Query returns AST nodes of the method
query = f'''
cpg.method.fullName("{escaped}").ast.map(node => 
  (node.id, node.code, node.lineNumber, node.columnNumber, 
   node.order, node.argumentIndex, node.label, 
   node.astParent.id, node.astChildren.id)
).l
'''
```
The proxy transforms this into `{nodes, edges}` format:
- Nodes: each AST node with `{id, label, code, line_number, ...}`
- Edges: `astParent.id → node.id` for each child

#### Metadata Enrichment (`/graph/cfg`, `/graph/ddg`, `/graph/pdg`)

After DOT parsing, run a batch query to fetch properties for every node ID in the graph. The frontend receives `metadata` as a map:
```json
{
  "nodes": [...],
  "edges": [...],
  "metadata": {
    "1234": {"code": "x = a + b", "lineNumber": 42, "node_type": "CALL", ...},
    "5678": {"code": "foo", "lineNumber": 41, "node_type": "IDENTIFIER", ...}
  }
}
```

This avoids N+1 queries by batching:
```python
node_ids = [n["id"] for n in graph["nodes"]]
id_list = ", ".join(str(i) for i in node_ids)
query = f'cpg.all.id({id_list}).map(n => (n.id, n.code, n.lineNumber, ...)).l'
```

### 3. Interactive Node UX

#### Hover Tooltip

```javascript
cy.on('mouseover', 'node', function(evt) {
  var node = evt.target;
  var data = node.data();
  var meta = graphMetadata[data.id] || {};
  tooltip.innerHTML = `
    <div class="tt-type">${meta.node_type || data.label}</div>
    <div class="tt-code">${escapeHTML(meta.code || data.label || '')}</div>
    ${meta.lineNumber ? `<div class="tt-line">Line ${meta.lineNumber}</div>` : ''}
  `;
  tooltip.style.display = 'block';
  // position near cursor
});
```

#### Click Detail Panel

A collapsible `<aside>` panel beside the cytoscape canvas. Clicking a node populates it:
```javascript
cy.on('tap', 'node', function(evt) {
  var node = evt.target;
  var meta = graphMetadata[node.data().id] || {};
  detailPanel.render(meta);  // shows all fields as a key-value table
  detailPanel.open();
});
```

Properties displayed:
| Field | Source |
|-------|--------|
| ID | `node.id` |
| Label | `dot[label]` |
| Node Type | metadata `node_type` |
| Code | metadata `code` |
| Line | metadata `lineNumber` |
| Column | metadata `columnNumber` |
| Order | metadata `order` |
| Arg Index | metadata `argumentIndex` |

### 4. Playground File Organization

**Current (`app.js` — 350 lines):**
```
app.js
├── createApp() — Vue 3 app with 28 methods/data properties
├── doParse(), _runQuery(), runTool() — 3 API call functions
├── renderGraph(), _initCytoscape() — graph rendering
├── formatRawResult(), copyResult() — UI helpers
└── onToolChange(), loadHistory() — state management
```

**Target structure:**
```
app.js (~50 lines) — bootstrap only:
  - Import components
  - Mount Vue app

components/parse-panel.js (~80 lines):
  - doParse(), sampleId, sourceCode, language states

components/query-panel.js (~100 lines):
  - _runQuery(), rawQuery, rawResult, queryHistory states
  - load_cpg/import_cpg helpers

components/tools-panel.js (~120 lines):
  - runTool(), selectedTool, toolParams, toolResult states
  - onToolChange()

graph/graph-app.js — self-contained graph Vue app:
  - graphMethod, graphType, graphLoading, graphError states
  - renderGraph(), _initCytoscape()
  - Hover/click handlers
```

Shared state via `window.PlaygroundState = Vue.reactive({...})` — keeps parse/load state accessible across panels without prop drilling.

---

## Graph Type Quick Reference

| Endpoint | Joern Query | DOT Support | Description |
|----------|-------------|-------------|-------------|
| `/graph/cfg` | `.dotCfg.l` | Yes | Control Flow Graph — basic blocks and branches |
| `/graph/dfg` | `.dotDdg.l` | Yes | Data Dependence Graph — data-flow edges (already exists as `/graph/dfg`) |
| `/graph/pdg` | `.dotPdg.l` | Yes | Program Dependence Graph — control + data dependencies |
| `/graph/ast` | `.ast` traversal | No (custom) | Abstract Syntax Tree — full parse tree |

> **Note:** The existing `/graph/dfg` uses `dotDdg` (Data Dependence Graph). We will also register `/graph/ddg` as an alias for consistency with Joern naming. Both `/graph/dfg` and `/graph/ddg` will route to the same handler for backward compatibility.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| AST query is expensive for large methods | Medium | Limit AST depth or node count; add timeout |
| Metadata batch query may exceed Joern REPL capacity | Medium | Chunk node IDs into batches of 50; cache metadata per CPG |
| Component split breaks existing MCP/parse workflow | Medium | Keep `window.PlaygroundState` compatible with current reactive patterns; run full test suite |
| Standalone graph page loses access to loaded CPG state | Low | Graph page always sends `method_full_name` to backend; backend queries Joern REPL directly (state agnostic) |

---

## Definition of Done

- [ ] S7-001: `/graph/pdg` endpoint returns PDG for a given method
- [ ] S7-002: `/graph/ast` endpoint returns AST tree for a given method
- [ ] S7-003: All graph endpoints include `metadata` map with node properties
- [ ] S7-004: Standalone `/graph` page served by Express, independent of playground
- [ ] S7-005: Graph type selector (CFG/DDG/PDG/AST) in frontend
- [ ] S7-006: Hover tooltip shows node type, label, and line number
- [ ] S7-007: Click detail panel shows full node metadata
- [ ] S7-008: `app.js` split into 3 panel components
- [ ] S7-009: `index.html` panel templates extracted into component files
- [ ] S7-010: Tests for new endpoints + no regressions
- [ ] Sprint report written at `report/sprint7-report.md`

---

## Revision History

| Date       | Author | Change |
| ---------- | ------ | ------ |
| 2026-04-30 | agent  | Created sprint 7 backlog: standalone graph vis, all Joern graph types, interactive node metadata, playground component split |
