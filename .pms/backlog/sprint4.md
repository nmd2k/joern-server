# Sprint 4 Backlog

**Sprint:** 4
**Period:** 2026-04-28 – 2026-05-09 (2 weeks)
**Goal:** Implement 6 vulnerability-hunting MCP primitives requested by NeuralAtlas to unblock agentic analysis on CASTLE/SVEN benchmarks.
**Branch:** `sprint/4`

---

## Context

NeuralAtlas identified that the current 15 MCP tools are navigation-only and cannot express standard vuln patterns (taint from source to sink, sink enumeration, argument inspection, literal search, file/line citations). This sprint closes the gap with 6 high-priority tools, enabling the MCP arm to match ~100% of the HTTP/CPGQL arm's expressive power for vulnerability hunting.

---

## Backlog Items

| ID     | PB Ref | Title                                                              | Type    | Priority | Status | Acceptance Criteria |
| ------ | ------ | ------------------------------------------------------------------ | ------- | -------- | ------ | ------------------- |
| S4-001 | PB-028 | MCP tool: `find_methods`                                           | Feature | High     | Done   | commit 9846385; 22 unit tests passing. name_pattern/annotation/modifier/full_name_pattern filters; at least one required. |
| S4-002 | PB-029 | MCP tool: `find_calls`                                             | Feature | High     | Done   | commit 94e2d38; 13 unit tests passing. callee_name_pattern + optional method_full_name_pattern scope filter. |
| S4-003 | PB-030 | MCP tool: `get_dataflow`                                           | Feature | High     | Done   | included in commit 3690d81; 14 unit tests passing. max_depth capped at 20; returns [] on no-flow or Joern error. |
| S4-004 | PB-031 | MCP tool: `get_call_arguments`                                     | Feature | High     | Done   | commit 30c8538; 12 unit tests passing. Strips L suffix from call_id; structured argIndex/code/typeFullName/nodeId output. |
| S4-005 | PB-032 | MCP tool: `find_literals`                                          | Feature | High     | Done   | commit 3690d81; 15 unit tests passing. pattern + literal_type ("string"/"int"/"any") filter. |
| S4-006 | PB-033 | MCP tool: `get_method_location`                                    | Feature | High     | Done   | commit 0314777; 12 unit tests passing. Accepts method_id or method_full_name; returns file/lineStart/lineEnd/columnStart/columnEnd. |
| S4-007 | —      | Unit + integration tests for all 6 new tools                      | Testing | High     | Done   | 88 unit tests across 6 new test files; 314 total suite passing; 0 regressions. |
| S4-008 | —      | Update MCP API docs for new tools                                  | Chore   | Medium   | Open   | `docs/api/mcp_api.md` updated with input/output schemas, CPGQL equivalents, and usage examples for all 6 tools. |

---

## Implementation Notes

### Tool architecture
All 6 tools follow the existing MCP tool pattern in `joern_server/`:
- Add tool definition to the MCP tool registry.
- Implement CPGQL query builder for each tool's parameters.
- Return structured JSON matching the output schema above.
- Propagate errors as structured MCP error responses (not raw exceptions).

### `get_dataflow` special considerations
- Dataflow queries are expensive; enforce `max_depth` cap (hard ceiling: 20).
- Return empty list (not error) when no flow is found — LLM agents must distinguish "no taint path" from "tool error".
- Consider a per-request timeout (e.g. 30 s) with a partial-result flag.

### Testing strategy
- Unit tests: mock Joern REPL responses; verify query construction and response parsing.
- Integration tests: parse a SVEN file with known vulns; verify each tool returns expected nodes.
- Parity tests: compare MCP tool output vs equivalent raw CPGQL via HTTP for key queries.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `get_dataflow` query timeout on large CPGs | Medium | Hard max_depth cap + per-request timeout; return partial results with `truncated: true` flag |
| CPGQL API differences across Joern versions | Low | Pin Joern version; test against locked image |
| Argument node structure varies by language | Medium | Defensive parsing; include raw `code` field as fallback |

---

## Definition of Done

- All 6 tools implemented and registered in MCP server.
- All S4-007 tests passing; zero regressions.
- MCP API docs updated (S4-008).
- Sprint report written at `report/sprint4-report.md`.
- Branch `sprint/4` merged to `main`.

---

## Revision History

| Date       | Author | Change                        |
| ---------- | ------ | ----------------------------- |
| 2026-04-23 | agent  | Created sprint 4 backlog from NeuralAtlas feature request (`.pms/feature_req/1_mcp.md`) |
