# Product Backlog

**Product:** Joern Server - Scalable CPG Analysis Platform
**Last updated:** 2026-04-29
**Target:** Research/Academic deployment (small codebases: 10-200 LOC)

> The product backlog is the single source of truth for all planned work.
> Items are ordered by priority. Agents pick from the top; humans re-order as strategy changes.

---

## Backlog Items


| ID     | Title                                                                          | Type    | Priority | Status | Sprint | Notes                                                                                                     |
| ------ | ------------------------------------------------------------------------------ | ------- | -------- | ------ | ------ | --------------------------------------------------------------------------------------------------------- |
| PB-001 | Optimize JVM memory for small codebases (10-200 LOC)                           | Chore   | High     | Done   | 1      | XMX=1g, Memory=4g; 67% reduction                                                                          |
| PB-002 | Benchmark parsing performance with /datadrive/data/sven/file/*                 | Spike   | High     | Done   | 1      | Script created, report template ready                                                                     |
| PB-003 | MCP test coverage & reliability: test all tools, compare vs HTTP, fix failures | Testing | High     | Done   | 2      | 85 unit tests (S2-T01), 22 parity tests (S2-T02), 6 regression tests (S2-T03), gated smoke tests (S2-T04) |
| PB-004 | Implement query result caching (LRU) for repeated queries                      | Feature | High     | Done   | 1      | LRU cache with TTL, 11 unit tests                                                                         |
| PB-005 | Add batch parse endpoint for multiple small files                              | Feature | Medium   | Open   | —      | Parse 10-20 small codebases in one request                                                                |
| PB-006 | Add Prometheus metrics endpoint                                                | Feature | Medium   | Open   | —      | Query latency, parse time, memory usage per session                                                       |
| PB-007 | Create academic use-case examples and tutorials                                | Chore   | Medium   | Open   | —      | Demo projects for security research                                                                       |
| PB-008 | Implement CPG result cache invalidation strategy                               | Feature | Medium   | Done      | 3   | Superseded by S3-001–S3-004: hash-based dedup, tag-based archive, disk LRU eviction                      |
| PB-009 | Add Rust language support to parser                                            | Feature | Medium   | Open   | —      | Expand language coverage                                                                                  |
| PB-010 | Create Python SDK for programmatic access                                      | Feature | Medium   | Open   | —      | Simplified API for research scripts                                                                       |
| PB-011 | Implement CPG diff tool for comparing analysis results                         | Feature | Low      | Open   | —      | Useful for tracking code changes                                                                          |
| PB-012 | Add query result streaming for large responses                                 | Feature | Low      | Open   | —      | Edge case for small codebases                                                                             |
| PB-013 | Add WebSocket support for real-time query results                              | Feature | Low      | Open   | —      | Alternative to SSE for MCP                                                                                |
| PB-014 | Implement distributed CPG storage (S3-compatible)                              | Feature | Low      | Open   | —      | For multi-node deployments                                                                                |
| PB-015 | Add GraphQL API layer                                                          | Feature | Low      | Open   | —      | Flexible querying interface                                                                               |
| PB-016 | Create automated benchmark suite for regression testing                        | Chore   | Low      | Open   | —      | Run on each PR with sven dataset                                                                          |
| PB-017 | Add support for incremental CPG updates                                        | Feature | Low      | Open   | —      | Parse only changed files                                                                                  |
| PB-018 | Implement graceful degradation on Joern server failure                         | Feature | Low      | Open   | —      | Queue queries, retry logic                                                                                |
| PB-019 | Write HTTP proxy and MCP server API reference documentation                    | Chore   | High     | Done   | —      | Written to .pms/docs/api/http_api.md and mcp_api.md                                                      |
| PB-020 | Fix /query-sync concurrency: add per-replica guard to prevent queued importCpg timeouts | Bug | High | In Sprint | 2-hotfix | No lock before httpx.post to single-threaded Joern REPL; multiple sessions on same replica queue; 600s timeout fires. See S2-HF01. |
| PB-021 | Add X-Served-By header to haproxy-joern.cfg; fix retry amplification and stale stick-table | Bug | High | In Sprint | 2-hotfix | Missing X-Served-By makes routing invisible; retries=2×600s=1800s worst case; no option redispatch. See S2-HF02. |
| PB-022 | CPG hash-based deduplication in `/parse`                                       | Feature | High     | Done      | 3      | SHA-256(source_code) → skip re-parse on cache hit; `cache_hit` field in response. See S3-001.             |
| PB-023 | Tag-based CPG archiving in `/cleanup`                                          | Feature | High     | Done      | 3      | `archive: true` moves CPG to archive dir + registry instead of deleting. See S3-002.                     |
| PB-024 | Disk LRU eviction for CPG archive                                              | Feature | High     | Done      | 3      | `CPG_ARCHIVE_MAX_COUNT` / `CPG_ARCHIVE_MAX_GB`; evict LRU on archive. See S3-003.                        |
| PB-025 | CPG cache index (hash → archive path registry)                                 | Feature | High     | Done      | 3      | JSON file-backed registry; thread-safe; self-healing on corruption. See S3-004.                           |
| PB-026 | Unit + integration tests for CPG cache                                         | Testing | High     | Done      | 3      | 15 unit + 10 integration tests, all passing. See S3-005.                                                  |
| PB-027 | Update HTTP API docs and SRS for CPG cache                                     | Chore   | Medium   | Done      | 3      | New fields, env vars, SRS Section 5 lifecycle update. See S3-006/S3-007.                                  |
| PB-028 | MCP tool: `find_methods` — global method search by name/annotation/modifier    | Feature | High     | Done      | 4      | 22 unit tests. commit 9846385 sprint/4. |
| PB-029 | MCP tool: `find_calls` — global call-site search by callee name/pattern        | Feature | High     | Done      | 4      | 13 unit tests. commit 94e2d38 sprint/4. |
| PB-030 | MCP tool: `get_dataflow` — taint reachability from source to sink              | Feature | High     | Done      | 4      | 14 unit tests. commit 3690d81 sprint/4. max_depth cap 20; [] on no-flow/Joern error. |
| PB-031 | MCP tool: `get_call_arguments` — structured argument access for a call node    | Feature | High     | Done      | 4      | 12 unit tests. commit 30c8538 sprint/4. |
| PB-032 | MCP tool: `find_literals` — search string/numeric literals across CPG          | Feature | High     | Done      | 4      | 15 unit tests. commit 3690d81 sprint/4. |
| PB-033 | MCP tool: `get_method_location` — file and line range for any method           | Feature | High     | Done      | 4      | 12 unit tests. commit 0314777 sprint/4. |
| PB-034 | MCP tool: `get_method_parameters` — structured parameter list with types       | Feature | Low      | Open      | —      | Nice-to-have from NeuralAtlas. Annotations included. |
| PB-035 | MCP tool: `get_control_dependencies` — conditions guarding a node              | Feature | Low      | Open      | —      | Nice-to-have from NeuralAtlas. Auth-check detection use case. |
| PB-036 | MCP tool: `get_assignments_in_method` — assignment targets and sources         | Feature | Low      | Open      | —      | Nice-to-have from NeuralAtlas. |
| PB-037 | MCP tool: `get_imports` — imports for a file or class                          | Feature | Low      | Open      | —      | Nice-to-have from NeuralAtlas. SBOM-adjacent checks. |
| PB-038 | MCP arm bypasses proxy cache on /parse with same sample_id                   | Bug     | High     | Done      | 5      | Verified: parse_source routes through proxy_post → /parse. 2 regression tests added. |
| PB-039 | Web playground: notebook-style interactive UI for CPG analysis               | Feature | High     | Done      | 5      | Vue 3 SPA in 4 modular files served by proxy; parse→load→query→25 tools workflow. 17 tests. |
| PB-040 | MCP tool ↔ CPGQL translation table for playground                            | Feature | High     | Done      | 5      | 25 tools with CPGQL translation in tool-definitions.js; escapeCPGQL helper. |
| PB-041 | Default filename `snippet.txt` causes empty CPG for C/C++ parsing             | Bug     | High     | Done      | 5-hotfix | `c2cpg` silently skips `.txt` files. Fix: `_LANGUAGE_EXT` mapping → `snippet.c` for C, `snippet.cpp` for C++, etc. See S5-HF01. |
| PB-042 | Strip ANSI escape codes from /query-sync stdout in proxy                      | Bug     | Medium   | Done      | 5-hotfix | Joern REPL wraps stdout in terminal color codes. Fix: `_strip_ansi()` applied to stdout field only (no schema change). See S5-HF02. |
| PB-043 | Web playground: interactive CPG visualization (CFG, DFG)                      | Feature | High     | Done     | 6      | Client request: render subgraphs (control-flow, data-flow) in-browser for debugging. Implemented with cytoscape.js + /graph/cfg + /graph/dfg endpoints. Standalone Express server delivers playground on :3000. |
| PB-044 | Standalone graph visualization service (extract from playground)              | Feature | High     | Done     | 7 | Graph vis extracted into its own `/graph` page independent of 4-panel playground. See S7-004. |
| PB-045 | All Joern graph types: PDG + AST endpoints                                   | Feature | High     | Done     | 7 | `/graph/pdg` and `/graph/ast` endpoints added with DOT + Scala tuple parsing. See S7-001, S7-002. |
| PB-046 | Graph node metadata: code, line, params, identifiers                         | Feature | High     | Done     | 7 | All `/graph/*` enriched with per-node metadata map; hover tooltip + click detail panel. See S7-003, S7-006, S7-007. |
| PB-047 | Playground file/module organization refactor                                 | Chore   | Medium   | Done     | 7 | `app.js` (350→27L) split into 3 Vue components; `index.html` (176→18L) minimal scaffold. See S7-008, S7-009. |


---

## Types

- **Feature** — new user-visible capability
- **Bug** — defect in existing behaviour
- **Chore** — internal improvement (refactor, dependency update, tooling)
- **Spike** — time-boxed research or proof-of-concept

## Status values

- **Open** — ready to be picked into a sprint
- **In Sprint** — currently assigned to an active sprint
- **Done** — completed and verified
- **Deferred** — intentionally postponed; add a note explaining why
- **Cancelled** — no longer needed; keep for audit trail

---

## Revision History


| Date       | Author | Change                                                                                                              |
| ---------- | ------ | ------------------------------------------------------------------------------------------------------------------- |
| 2026-04-16 | nmd2k  | Created initial backlog for research/Academic product scaling                                                       |
| 2026-04-16 | nmd2k  | PB-003 revised: vulnerability detectors replaced by MCP test coverage & reliability; Sprint 2 refocused accordingly |
| 2026-04-16 | agent  | PB-003 marked Done: Sprint 2 complete; 216 total tests passing                                                      |
| 2026-04-18 | agent  | Added PB-020 and PB-021 (Bug/High) following /query-sync timeout investigation; assigned to sprint 2-hotfix          |
| 2026-04-19 | agent  | Hotfix S2-HF01 merged to main (PR #5). Added PB-022–PB-027 for Sprint 3 CPG cache; PB-008 assigned to Sprint 3.     |
| 2026-04-23 | agent  | Added PB-028–PB-037 from NeuralAtlas feature request (`.pms/feature_req/1_mcp.md`); PB-028–PB-033 (High) assigned to Sprint 4; PB-034–PB-037 (Low/nice-to-have) to backlog. |
| 2026-04-24 | agent  | Added PB-038 (Bug/High): MCP arm bypasses proxy cache; assigned to Sprint 5. |
| 2026-04-29 | agent  | Added PB-039–PB-040; Sprint 5 complete: web playground delivered. 368 tests pass, 0 failures. |
| 2026-04-29 | agent  | Added PB-041 (Bug/High): default filename `snippet.txt` bypasses c2cpg C/C++ source detection; fixed with `_LANGUAGE_EXT` mapping. See S5-HF01. |
| 2026-04-29 | agent  | Added PB-042 (Bug/Medium): ANSI escape codes in /query-sync stdout; fixed with `_strip_ansi()` in proxy. Added PB-043 (Feature): CPG visualization for Sprint 6. |
| 2026-04-30 | agent  | Sprint 6 closed. Added PB-044–PB-047 for Sprint 7: standalone graph vis, all graph types, node metadata, playground refactor. |
| 2026-04-30 | agent  | Sprint 7 complete: all 10 items done. 384 tests pass (+16 new). PB-044–PB-047 marked Done. |


---

## Sprint Planning Notes

### Resource Budget


| Resource                      | Value               |
| ----------------------------- | ------------------- |
| Target concurrent sessions    | 20                  |
| Available RAM                 | 128GB               |
| RAM per container (optimized) | 4GB                 |
| Max containers on hardware    | ~32 (128GB / 4GB)   |
| Headroom for OS/overhead      | ~20 containers safe |


### Sprint 1 (Week 1-2): Memory Optimization + Baseline

**Focus:** Optimize for small codebases, establish performance baselines

Target items:

- PB-001: Optimize JVM memory (4GB → 1-2GB XMX, 12GB → 4GB container)
- PB-002: Benchmark with sven dataset
- PB-004: Query result caching

### Sprint 2 (Week 3-4): MCP Reliability & Test Coverage

**Focus:** Ensure every MCP tool produces correct, reproducible results. No new features until the existing 18 tools are verified reliable.

Target items:

- PB-003: Full test coverage for all 18 MCP tools; MCP vs HTTP parity; fix any broken scalar queries

### Sprint 3 (Week 5-6): CPG-Level Caching

**Focus:** Hash-based CPG deduplication, tag-based archiving, disk LRU eviction, zero RAM bloat from CPG artifacts, full test coverage, updated docs.

Target items:
- PB-022 (S3-001): Hash-based dedup in `/parse`
- PB-023 (S3-002): Tag-based archiving in `/cleanup`
- PB-024 (S3-003): Disk LRU eviction
- PB-025 (S3-004): CPG cache index/registry
- PB-026 (S3-005): Tests
- PB-027 (S3-006/007): API + SRS docs update
- PB-008: Close (superseded by above)

### Sprint 4 (Week 7-8): Vulnerability Hunting MCP Primitives

**Focus:** Implement 6 new MCP tools requested by NeuralAtlas to unblock agentic vuln-hunting workflows on CASTLE/SVEN benchmarks. All 6 tools are high-priority and blocking client research.

Target items:
- PB-028 (S4-001): `find_methods`
- PB-029 (S4-002): `find_calls`
- PB-030 (S4-003): `get_dataflow`
- PB-031 (S4-004): `get_call_arguments`
- PB-032 (S4-005): `find_literals`
- PB-033 (S4-006): `get_method_location`

### Sprint 5 (Week 11-12): Web Playground & MCP Cache Parity ✓ COMPLETE

**Focus:** Deliver a browser-based interactive playground (Jupyter-notebook style) for researchers to parse, load CPGs, run CPGQL queries, and execute MCP tools. Verify MCP /parse proxy routing for CPGRegistry cache hits.

Target items:
- ~~PB-038 (S5-001): Verify + regression-test MCP /parse proxy routing for CPGRegistry cache hit~~ Done
- ~~PB-039 (S5-002–S5-005): Web playground — Vue 3 notebook app (parse → load → query → MCP tools)~~ Done
- ~~PB-040 (S5-005): MCP tool ↔ CPGQL translation table~~ Done
- ~~PB-041 (S5-HF01): Default filename extension causes empty CPG for C/C++~~ Done (post-release hotfix)
- ~~PB-042 (S5-HF02): Strip ANSI escape codes from /query-sync stdout~~ Done (post-release hotfix)

### Sprint 6 (Week 13-14): Playground Standalone + Graph Visualization ✓ COMPLETE

**Focus:** Extract playground into standalone Node.js/Express service with MCP arm integration + CPG graph visualization (CFG/DFG).

Target items:
- ~~PB-043 (S6-001–S6-010): Web playground standalone Express server + MCP bridge + cytoscape.js graph vis~~ Done

### Sprint 7 (Week 15-16): Graph Vis Standalone + All Graph Types + Node Metadata + Playground Refactor ✓ COMPLETE

**Focus:** Extract graph visualization into standalone service; add all Joern graph types (PDG/AST); interactive node metadata (hover tooltip + click detail panel); refactor playground into components.

Target items:
- ~~PB-044 (S7-004): Standalone graph visualization service~~ Done
- ~~PB-045 (S7-001, S7-002): All Joern graph types (PDG + AST)~~ Done
- ~~PB-046 (S7-003, S7-006, S7-007): Graph node metadata + interactive UX~~ Done
- ~~PB-047 (S7-008, S7-009): Playground component refactor~~ Done

### Future Sprints

- Sprint 8: Developer experience (PB-007, PB-010) + nice-to-have MCP tools (PB-034–PB-037)
- Sprint 9: Advanced features (PB-009, PB-011)

---

## Research Use Cases

1. **Vulnerability Pattern Detection**: Query large codebases for security anti-patterns
2. **Code Clone Detection**: Find duplicated code across projects
3. **Call Graph Analysis**: Understand data flow for taint analysis
4. **Dependency Analysis**: Track library usage and potential vulnerabilities
5. **Educational Demos**: Showcase static analysis capabilities to students

