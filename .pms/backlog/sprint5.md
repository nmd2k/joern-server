# Sprint 5 Backlog

**Sprint:** 5
**Period:** 2026-04-29 – 2026-05-13 (2 weeks)
**Goal:** Fix MCP cache bypass + deliver a web interactive playground (Jupyter-like notebook) for researchers to parse, load, query CPGs and run MCP tools from a browser.
**Branch:** `sprint/5`

---

## Context

Researchers need a browser-based debugging tool to interactively parse source code into CPGs, run CPGQL queries, and execute MCP analysis tools without writing scripts. The playground serves as a simple demo that mirrors the full HTTP API surface — an alternative to curl/Postman for rapid experimentation.

Additionally, PB-038 (logged 2026-04-24) identified that MCP `/parse` may bypass the proxy's CPGRegistry cache. This sprint verifies the fix (the current codebase already routes `parse_source` through `proxy_post`) and adds a regression test.

---

## Backlog Items

| ID     | PB Ref | Title                                                              | Type    | Priority | Status  | Acceptance Criteria |
| ------ | ------ | ------------------------------------------------------------------ | ------- | -------- | ------- | ------------------- |
| S5-001 | PB-038 | Verify + regression-test MCP `/parse` proxy routing for CPGRegistry cache hit | Bug     | High     | Done    | `parse_source` MCP tool hits CPGRegistry cache on repeated identical source_code; 2 new regression tests added (17 total pass). Verified proxy_post routes through /parse. |
| S5-002 | —      | Add `GET /playground` route to proxy serving notebook HTML         | Feature | High     | Done    | `GET /playground` serves `index.html`; all playground assets (CSS, JS) served via `/playground/<file>`. Content-Type based on extension. Path traversal protected. |
| S5-003 | —      | Notebook: Source → CPG panel (parse + load)                        | Feature | High     | Done    | Source code textarea, language dropdown, sample_id input. "Parse & Load" calls POST /parse → importCpg/load_cpg. Result badge (green cache_hit / yellow fresh). Cleanup button. Loading spinner. |
| S5-004 | —      | Notebook: CPGQL query editor panel                                 | Feature | High     | Done    | CPGQL textarea (monospace). "Run Query" → POST /query-sync → JSON result panel with Copy. Query history (last 20, clickable). |
| S5-005 | —      | Notebook: MCP tool picker panel                                    | Feature | High     | Done    | Dropdown of 25 tools (24 MCP + parse_source) in 6 `<optgroup>` categories. Dynamic parameter forms. Execute translates to CPGQL → /query-sync. All 25 CPGQL translations embedded. |
| S5-006 | —      | Integration tests for playground routes                            | Testing | High     | Done    | 17 tests: GET /playground (HTML, CSS, JS serving), tool definitions (25 tools, 6 groups), CPGQL translation correctness, path traversal protection. All passing. |
| S5-HF01 | PB-041 | Post-release: default filename `snippet.txt` causes empty CPG for C/C++ | Bug     | High     | Done    | Added `_LANGUAGE_EXT` mapping + `_default_filename()` in `proxy.py`; C code now produces `snippet.c` → `cpg.method.name("add").l` returns the `add` Method (68 nodes vs 11). Full suite 368 passed, 0 regressions. See `report/sprint5-report.md` §Post-Release Bug. |

---

## Technical Design

### Frontend (`playground/index.html`)

- **Framework:** Vue 3 via CDN (`unpkg.com/vue@3/dist/vue.global.prod.js`)
- **Styling:** Inline CSS (zero external CSS dependencies). Dark terminal theme.
- **Layout:** Three collapsible panels stacked vertically:
  1. **Parse & Load** — source code input, language dropdown, sample_id field, "Parse & Load" button, result badge (cache hit / fresh)
  2. **CPGQL Query Editor** — `<textarea>` with monospace font, "Run Query" button, JSON result panel with copy button, history list
  3. **MCP Tool Runner** — tool dropdown (grouped `<optgroup>`), dynamic parameter form, "Execute" button, result panel
- **State:** In-memory only (no localStorage, no server sessions). Lost on page refresh.

### Proxy Route (`joern_server/proxy.py`)

Add a `GET /playground` handler that reads and serves `playground/index.html` as `text/html`. The HTML path is configurable via `PLAYGROUND_HTML` env var (or defaults to a relative path from the proxy module).

### MCP Tool ↔ CPGQL Translation

The playground embeds a mapping of tool names to CPGQL query templates. For example:

```javascript
const TOOL_CPGQL = {
  "get_method_callees": (p) => `cpg.method.fullName("${p.method_full_name}").callee.fullName.l`,
  "find_methods": (p) => {
    const clauses = [];
    if (p.name_pattern) clauses.push(`.name("${p.name_pattern}")`);
    if (p.full_name_pattern) clauses.push(`.fullName("${p.full_name_pattern}")`);
    // ...
    return `cpg.method${clauses.join('')}.l`;
  },
  // ... all 24 tools
};
```

This avoids SSE complexity and keeps all traffic on port 8080.

### File Layout

```
joern-server/
└── playground/
    └── index.html           # Single-file Vue 3 notebook app
```

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| MCP tool CPGQL translation errors | Medium | Unit-test all 24 tool templates; use server_tools.py CPGQL as source of truth |
| Large query results crash browser | Low | Truncate results > 500KB with "...(truncated)" notice |
| CORS issues with CDN Vue | Low | Vue loaded from CDN; no CORS needed for API calls (same-origin) |
| `joern-parse` timeout visible in UI | Medium | Show spinner + "Parsing... (may take up to 900s)" during /parse |

---

## Definition of Done

- [ ] S5-001: `parse_source` verified working through proxy with cache hit; regression test added.
- [ ] S5-002: `GET /playground` returns HTML.
- [ ] S5-003: Parse & Load panel functional (source code → CPG → loaded).
- [ ] S5-004: Query editor functional (text → /query-sync → JSON result).
- [ ] S5-005: MCP tool picker functional (all 24 tools, parameterized, execute via CPGQL).
- [x] S5-006: Integration tests passing.
- [x] No regressions in existing test suite.
- [x] Sprint report written at `report/sprint5-report.md`.
- [x] S5-HF01: Default filename extension fix verified; Docker image rebuilt.

---

## Revision History

| Date       | Author | Change                        |
| ---------- | ------ | ----------------------------- |
| 2026-04-29 | agent  | Created sprint 5 backlog with PB-038 + web playground scope |
| 2026-04-29 | agent  | Added S5-HF01 (PB-041): default filename extension bug fix for C/C++ CPG parsing |
