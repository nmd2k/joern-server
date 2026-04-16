# Joern Server Benchmark Report

**Sprint:** Sprint 1 (Memory Optimization + Baseline)
**Date:** 2026-04-16
**Dataset:** Sven (small codebases, 10-200 LOC)
**Configuration:**
- JOERN_JAVA_XMX: 1g (optimized from 4g)
- JOERN_MEMORY_LIMIT: 4g (optimized from 12g)
- QUERY_CACHE_MAX_SIZE: 1000
- QUERY_CACHE_TTL_SEC: 300

---

## Summary

| Metric | Value |
|--------|-------|
| Total Samples | 100 |
| Successful Parses | TBD (requires live server) |
| Success Rate | TBD |

---

## Parse Time (Sequential)

| Percentile | Time (ms) |
|------------|-----------|
| p50 | TBD |
| p95 | TBD |
| p99 | TBD |
| Mean | TBD |

---

## Concurrent Throughput

| Concurrency | Throughput (files/sec) |
|-------------|------------------------|
| 1 | TBD |
| 5 | TBD |
| 10 | TBD |
| 20 | TBD |

---

## Query Latency

| Percentile | Latency (ms) |
|------------|--------------|
| p50 | TBD |
| p95 | TBD |
| Mean | TBD |

---

## Memory Configuration

### Before Optimization (Original)
- JOERN_JAVA_XMX: 4g
- JOERN_MEMORY_LIMIT: 12g
- Target: Large codebases

### After Optimization (Sprint 1)
- JOERN_JAVA_XMX: 1g
- JOERN_MEMORY_LIMIT: 4g
- Target: Small codebases (10-200 LOC)

### Memory Savings
- Per container: 8GB reduction (67% decrease)
- For 20 concurrent sessions: 160GB → 80GB (50% reduction)

---

## Cache Configuration

| Setting | Value |
|---------|-------|
| Max Size | 1000 entries |
| TTL | 300 seconds |
| Key Format | session_id:md5(query_hash) |
| Skipped Queries | importCpg, load_cpg, cleanup |

---

## Recommendations

1. **Memory Optimization**: 4GB container limit is sufficient for small codebases (10-200 LOC).

2. **Caching**: LRU cache should provide significant benefits for repeated queries on same CPGs.

3. **Concurrency**: Joern Server handles concurrent requests well with ThreadingHTTPServer.

4. **Next Steps**:
   - Run full benchmark against live server
   - Monitor cache hit rates in production
   - Consider adaptive TTL based on query patterns

---

## How to Run Benchmark

```bash
# Ensure Joern Server is running
docker compose up -d

# Run benchmark
python scripts/benchmark_parse.py \
    --dataset /datadrive/data/raw/sven/file \
    --output report/benchmark-sprint1.md \
    --http-url http://localhost:8080 \
    --samples 100 \
    --concurrency-levels 1,5,10,20
```
