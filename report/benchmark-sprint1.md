# Joern Server Benchmark Report

**Sprint:** Sprint 1 (Memory Optimization + Baseline)
**Date:** 2026-04-16 20:57:34
**Dataset:** Sven (small codebases, 10-200 LOC)

---

## Summary

| Metric | Value |
|--------|-------|
| Total Samples | 100 |
| Successful Parses | 100 |
| Success Rate | 100.0% |

## Parse Time (Sequential)

| Percentile | Time (ms) |
|------------|-----------|
| p50 | 2603.99 |
| p95 | 3096.83 |
| p99 | 3872.91 |
| Mean | 2657.90 |

## Concurrent Throughput

| Concurrency | Throughput (files/sec) |
|-------------|------------------------|
| 1 | 0.37 |
| 5 | 1.57 |
| 10 | 2.29 |
| 20 | 3.18 |

## Query Latency

| Percentile | Latency (ms) |
|------------|--------------|
| p50 | 59.53 |
| p95 | 68.04 |
| Mean | 60.24 |

---

## Recommendations

1. **Memory Optimization**: Current benchmarks confirm 4GB container limit is sufficient for small codebases.
2. **Caching**: LRU cache should provide significant benefits for repeated queries on same CPGs.
3. **Concurrency**: Joern Server handles concurrent requests well with ThreadingHTTPServer.
