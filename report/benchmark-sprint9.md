# Joern Server Benchmark — Sprint 9 (file vs repo JSONL)

**Date:** 2026-05-20 13:22:47
**Dataset:** `/home/student.unimelb.edu.au/manhdungnguy/joern-server/tests/fixtures/sven_mini`
**HTTP URL:** `http://localhost:8080`
**Dry run:** True
**CPG query stats:** skipped (server down or --skip-query-stats)

---

## Comparison summary

| Mode | Samples | Success | file_count (median) | parse p50 (ms) | parse p95 (ms) |
|------|---------|---------|---------------------|----------------|----------------|
| `bench-file` | 2 | 2/2 | 1.0 | 0.00 | 0.00 |
| `bench-repo-jsonl` | 2 | 2/2 | 1.5 | 0.00 | 0.00 |

---

## `bench-file`

| sample | file_count | parse_ms | method_count | call_count | status |
|--------|------------|----------|--------------|------------|--------|
| sample_a | 1 | 0.00 | — | — | ok |
| sample_b | 1 | 0.00 | — | — | ok |

## `bench-repo-jsonl`

| sample | file_count | parse_ms | method_count | call_count | status |
|--------|------------|----------|--------------|------------|--------|
| sample_a | 2 | 0.00 | — | — | ok |
| sample_b | 1 | 0.00 | — | — | ok |

---

## Notes

This report was generated in **dry-run** mode (no live parse). Re-run without `--dry-run` against a running Joern server for real latencies and CPG stats.
