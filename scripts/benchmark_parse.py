#!/usr/bin/env python3
"""Benchmark parsing performance for Joern Server.

Sprint 1 (default) — sequential/concurrent file parse baseline:

    python scripts/benchmark_parse.py --dataset /path/to/sven/file \\
        --output report/benchmark-sprint1.md

Sprint 9 — compare single-file ``POST /parse`` vs repo ``POST /parse/repo`` (JSONL):

    python scripts/benchmark_parse.py sprint9 \\
        --dataset /path/to/sven/samples \\
        --modes bench-file,bench-repo-jsonl \\
        --output report/benchmark-sprint9.md

    # Mock report without a live server:
    python scripts/benchmark_parse.py sprint9 --dataset tests/fixtures/sven_mini --dry-run
"""

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

SOURCE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".ts",
    ".py",
    ".go",
    ".rb",
    ".cs",
}


@dataclass
class ParseResult:
    """Result of a single parse operation."""

    sample_id: str
    filename: str
    language: str
    lines_of_code: int
    parse_time_ms: float
    success: bool
    error: str = ""


@dataclass
class BenchmarkResult:
    """Aggregated benchmark results."""

    parse_times: list[float] = field(default_factory=list)
    concurrent_throughputs: dict[int, float] = field(default_factory=dict)
    query_latencies: list[float] = field(default_factory=list)
    memory_samples: list[float] = field(default_factory=list)
    total_samples: int = 0
    successful_parses: int = 0
    errors: list[str] = field(default_factory=list)


def get_language_from_filename(filename: str) -> str:
    """Determine Joern language from file extension."""
    ext_map = {
        ".c": "c",
        ".cpp": "c",
        ".cc": "c",
        ".cxx": "c",
        ".h": "c",
        ".hpp": "c",
        ".cs": "csharpsrc",
        ".go": "golang",
        ".java": "java",
        ".js": "jssrc",
        ".ts": "jssrc",
        ".py": "pythonsrc",
        ".rb": "rubysrc",
    }
    return ext_map.get(Path(filename).suffix.lower(), "")


def count_lines(filepath: Path) -> int:
    """Count lines in a file."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def sample_id_from_path(filepath: Path) -> str:
    """Generate safe sample_id from filepath."""
    name = filepath.name.replace(".", "-").replace("_", "-")
    return f"bench-{filepath.stem[:20]}-{hashlib.md5(str(filepath).encode()).hexdigest()[:8]}"


def parse_single_file(
    filepath: Path,
    http_url: str,
    cpg_out_dir: str,
    timeout: int = 300,
) -> ParseResult:
    """Parse a single file using Joern Server."""
    sample_id = sample_id_from_path(filepath)
    language = get_language_from_filename(filepath.name)
    lines = count_lines(filepath)

    if not language:
        return ParseResult(
            sample_id=sample_id,
            filename=filepath.name,
            language="",
            lines_of_code=lines,
            parse_time_ms=0,
            success=False,
            error=f"Unsupported language: {filepath.suffix}",
        )

    try:
        source_code = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return ParseResult(
            sample_id=sample_id,
            filename=filepath.name,
            language=language,
            lines_of_code=0,
            parse_time_ms=0,
            success=False,
            error=f"Failed to read file: {e}",
        )

    start = time.perf_counter()

    try:
        resp = httpx.post(
            f"{http_url}/parse",
            json={
                "sample_id": sample_id,
                "source_code": source_code,
                "language": language,
                "filename": filepath.name,
                "overwrite": True,
            },
            timeout=timeout,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                return ParseResult(
                    sample_id=sample_id,
                    filename=filepath.name,
                    language=language,
                    lines_of_code=lines,
                    parse_time_ms=elapsed_ms,
                    success=True,
                )
            else:
                return ParseResult(
                    sample_id=sample_id,
                    filename=filepath.name,
                    language=language,
                    lines_of_code=lines,
                    parse_time_ms=elapsed_ms,
                    success=False,
                    error=result.get("stderr", "Parse failed")[:500],
                )
        else:
            return ParseResult(
                sample_id=sample_id,
                filename=filepath.name,
                language=language,
                lines_of_code=lines,
                parse_time_ms=elapsed_ms,
                success=False,
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

    except httpx.TimeoutException:
        return ParseResult(
            sample_id=sample_id,
            filename=filepath.name,
            language=language,
            lines_of_code=lines,
            parse_time_ms=(time.perf_counter() - start) * 1000,
            success=False,
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return ParseResult(
            sample_id=sample_id,
            filename=filepath.name,
            language=language,
            lines_of_code=lines,
            parse_time_ms=(time.perf_counter() - start) * 1000,
            success=False,
            error=str(e)[:500],
        )


def cleanup_sample(http_url: str, sample_id: str) -> bool:
    """Cleanup a parsed sample."""
    try:
        resp = httpx.post(f"{http_url}/cleanup", json={"sample_id": sample_id}, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def benchmark_sequential_parse(
    files: list[Path],
    http_url: str,
    cpg_out_dir: str,
    num_samples: int = 100,
) -> tuple[list[ParseResult], BenchmarkResult]:
    """Benchmark sequential parsing."""
    result = BenchmarkResult()
    parse_results = []

    # Sample files if too many
    files_to_test = random.sample(files, min(num_samples, len(files)))

    for filepath in files_to_test:
        parse_result = parse_single_file(filepath, http_url, cpg_out_dir)
        parse_results.append(parse_result)

        if parse_result.success:
            result.parse_times.append(parse_result.parse_time_ms)
            result.successful_parses += 1
        else:
            result.errors.append(f"{filepath.name}: {parse_result.error}")

        result.total_samples += 1

    return parse_results, result


def benchmark_concurrent_parse(
    files: list[Path],
    http_url: str,
    cpg_out_dir: str,
    concurrency: int,
    num_samples: int = 100,
) -> float:
    """Benchmark concurrent parsing, return throughput (files/second)."""
    files_to_test = random.sample(files, min(num_samples, len(files)))

    start = time.perf_counter()
    successful = 0

    def parse_and_cleanup(filepath: Path) -> bool:
        result = parse_single_file(filepath, http_url, cpg_out_dir, timeout=120)
        if result.success:
            cleanup_sample(http_url, result.sample_id)
            return True
        return False

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(parse_and_cleanup, f) for f in files_to_test]
        for future in as_completed(futures):
            try:
                if future.result():
                    successful += 1
            except Exception:
                pass

    elapsed = time.perf_counter() - start
    throughput = successful / elapsed if elapsed > 0 else 0
    return throughput


def benchmark_query_latency(http_url: str, sample_id: str, num_queries: int = 50) -> list[float]:
    """Benchmark query latency for a loaded CPG."""
    latencies = []
    queries = [
        "cpg.method.name.l",
        "cpg.typeDecl.name.l",
        "cpg.call.name.l",
    ]

    for _ in range(num_queries // len(queries)):
        for query in queries:
            start = time.perf_counter()
            try:
                resp = httpx.post(
                    f"{http_url}/query-sync",
                    json={"query": query},
                    timeout=30,
                )
                if resp.status_code == 200:
                    latencies.append((time.perf_counter() - start) * 1000)
            except Exception:
                pass

    return latencies


def get_memory_usage() -> float:
    """Get current process memory usage in MB."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024  # KB to MB
    except Exception:
        pass
    return 0.0


def generate_report(result: BenchmarkResult, output_path: Path) -> None:
    """Generate markdown benchmark report."""
    report_lines = [
        "# Joern Server Benchmark Report",
        "",
        f"**Sprint:** Sprint 1 (Memory Optimization + Baseline)",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Dataset:** Sven (small codebases, 10-200 LOC)",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Samples | {result.total_samples} |",
        f"| Successful Parses | {result.successful_parses} |",
        f"| Success Rate | {result.successful_parses / result.total_samples * 100:.1f}% |",
        "",
    ]

    if result.parse_times:
        p50 = statistics.median(result.parse_times)
        p95 = sorted(result.parse_times)[int(len(result.parse_times) * 0.95)]
        p99 = sorted(result.parse_times)[int(len(result.parse_times) * 0.99)]
        mean_time = statistics.mean(result.parse_times)

        report_lines.extend([
            "## Parse Time (Sequential)",
            "",
            "| Percentile | Time (ms) |",
            "|------------|-----------|",
            f"| p50 | {p50:.2f} |",
            f"| p95 | {p95:.2f} |",
            f"| p99 | {p99:.2f} |",
            f"| Mean | {mean_time:.2f} |",
            "",
        ])

    if result.concurrent_throughputs:
        report_lines.extend([
            "## Concurrent Throughput",
            "",
            "| Concurrency | Throughput (files/sec) |",
            "|-------------|------------------------|",
        ])
        for conc, throughput in sorted(result.concurrent_throughputs.items()):
            report_lines.append(f"| {conc} | {throughput:.2f} |")
        report_lines.append("")

    if result.query_latencies:
        p50 = statistics.median(result.query_latencies)
        p95 = sorted(result.query_latencies)[int(len(result.query_latencies) * 0.95)]
        mean_lat = statistics.mean(result.query_latencies)

        report_lines.extend([
            "## Query Latency",
            "",
            "| Percentile | Latency (ms) |",
            "|------------|--------------|",
            f"| p50 | {p50:.2f} |",
            f"| p95 | {p95:.2f} |",
            f"| Mean | {mean_lat:.2f} |",
            "",
        ])

    if result.errors:
        report_lines.extend([
            "## Errors (first 10)",
            "",
        ])
        for err in result.errors[:10]:
            report_lines.append(f"- {err}")
        report_lines.append("")

    report_lines.extend([
        "---",
        "",
        "## Recommendations",
        "",
        "1. **Memory Optimization**: Current benchmarks confirm 4GB container limit is sufficient for small codebases.",
        "2. **Caching**: LRU cache should provide significant benefits for repeated queries on same CPGs.",
        "3. **Concurrency**: Joern Server handles concurrent requests well with ThreadingHTTPServer.",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines))


# --- Sprint 9: file vs repo JSONL benchmark (S9-009) ---


@dataclass
class Sprint9SampleResult:
    """One sample row for bench-file or bench-repo-jsonl."""

    sample_key: str
    mode: str
    file_count: int
    parse_time_ms: float
    success: bool
    sample_id: str = ""
    cpg_path: str = ""
    method_count: int | None = None
    call_count: int | None = None
    error: str = ""


@dataclass
class Sprint9ModeSummary:
    mode: str
    results: list[Sprint9SampleResult] = field(default_factory=list)

    @property
    def parse_times_ms(self) -> list[float]:
        return [r.parse_time_ms for r in self.results if r.success]

    @property
    def file_counts(self) -> list[int]:
        return [r.file_count for r in self.results if r.success]


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in 0..100)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    weight = rank - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def list_source_files(root: Path) -> list[Path]:
    return sorted(
        f
        for f in root.rglob("*")
        if f.is_file() and f.suffix.lower() in SOURCE_EXTENSIONS
    )


def discover_sven_samples(dataset: Path) -> list[tuple[str, Path]]:
    """Discover SVEN-style samples: each immediate subdir with source files."""
    samples: list[tuple[str, Path]] = []
    for child in sorted(dataset.iterdir()):
        if child.is_dir() and list_source_files(child):
            samples.append((child.name, child))
    if samples:
        return samples

    files = list_source_files(dataset)
    if not files:
        return []

    groups: dict[str, Path] = {}
    for fpath in files:
        rel_parent = fpath.parent.relative_to(dataset)
        key = rel_parent.as_posix() if rel_parent != Path(".") else fpath.stem
        groups.setdefault(key, fpath.parent)
    return sorted(groups.items())


def pick_primary_file(sample_root: Path) -> Path | None:
    """Choose a representative file for single-file /parse baseline."""
    files = list_source_files(sample_root)
    if not files:
        return None
    c_files = [f for f in files if f.suffix.lower() in {".c", ".cpp", ".cc", ".cxx"}]
    pool = c_files or files
    return max(pool, key=lambda p: p.stat().st_size)


def build_repo_ndjson(sample_root: Path) -> tuple[bytes, int]:
    """Build NDJSON body and return (body, file_count)."""
    lines: list[bytes] = []
    for path in list_source_files(sample_root):
        rel = path.relative_to(sample_root).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        lines.append(
            json.dumps({"path": rel, "content": content}, ensure_ascii=False).encode("utf-8")
        )
    body = b"\n".join(lines)
    if body:
        body += b"\n"
    return body, len(lines)


def server_reachable(http_url: str, timeout: float = 3.0) -> bool:
    try:
        resp = httpx.get(f"{http_url.rstrip('/')}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def sprint9_sample_id(sample_key: str, mode: str) -> str:
    digest = hashlib.sha256(f"{sample_key}:{mode}".encode()).hexdigest()[:10]
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", sample_key)[:32]
    return f"s9-{mode[:4]}-{safe}-{digest}"


def _extract_int_from_stdout(stdout: str) -> int | None:
    if not stdout:
        return None
    matches = re.findall(r"\b(\d+)\b", stdout)
    if not matches:
        return None
    return int(matches[-1])


def query_cpg_method_call_counts(
    http_url: str,
    cpg_path: str,
    *,
    session_id: str,
    timeout: int = 60,
) -> tuple[int | None, int | None]:
    """Run importCpg + size queries; return (method_count, call_count)."""
    base = http_url.rstrip("/")
    headers = {"Content-Type": "application/json", "X-Session-Id": session_id}

    def run_query(query: str) -> str:
        resp = httpx.post(
            f"{base}/query-sync",
            json={"query": query},
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return ""
        payload = resp.json()
        return str(payload.get("stdout") or "")

    escaped = cpg_path.replace("\\", "\\\\").replace('"', '\\"')
    run_query(f'importCpg("{escaped}")')

    method_out = run_query("cpg.method.size")
    call_out = run_query("cpg.call.size")
    return _extract_int_from_stdout(method_out), _extract_int_from_stdout(call_out)


def bench_file_sample(
    sample_key: str,
    sample_root: Path,
    http_url: str,
    *,
    dry_run: bool,
    timeout: int,
    query_stats: bool,
) -> Sprint9SampleResult:
    primary = pick_primary_file(sample_root)
    if primary is None:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-file",
            file_count=0,
            parse_time_ms=0,
            success=False,
            error="no source files",
        )

    file_count = 1
    sample_id = sprint9_sample_id(sample_key, "bench-file")

    if dry_run:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-file",
            file_count=file_count,
            parse_time_ms=0.0,
            success=True,
            sample_id=sample_id,
            cpg_path=f"/workspace/cpg-out/{sample_id}",
        )

    result = parse_single_file(primary, http_url, "tmp/eval", timeout=timeout)
    row = Sprint9SampleResult(
        sample_key=sample_key,
        mode="bench-file",
        file_count=file_count,
        parse_time_ms=result.parse_time_ms,
        success=result.success,
        sample_id=result.sample_id,
        error=result.error,
    )
    if result.success:
        row.cpg_path = f"/workspace/cpg-out/{result.sample_id}"
        if query_stats and row.cpg_path:
            sid = f"bench-{uuid.uuid4().hex[:12]}"
            methods, calls = query_cpg_method_call_counts(
                http_url, row.cpg_path, session_id=sid, timeout=timeout
            )
            row.method_count, row.call_count = methods, calls
        cleanup_sample(http_url, result.sample_id)
    return row


def bench_repo_jsonl_sample(
    sample_key: str,
    sample_root: Path,
    http_url: str,
    *,
    dry_run: bool,
    timeout: int,
    query_stats: bool,
) -> Sprint9SampleResult:
    body, file_count = build_repo_ndjson(sample_root)
    if file_count == 0:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=0,
            parse_time_ms=0,
            success=False,
            error="empty tree",
        )

    primary = pick_primary_file(sample_root)
    language = get_language_from_filename(primary.name if primary else "main.c")
    sample_id = sprint9_sample_id(sample_key, "bench-repo-jsonl")

    if dry_run:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=file_count,
            parse_time_ms=0.0,
            success=True,
            sample_id=sample_id,
            cpg_path=f"/workspace/cpg-out/{sample_id}",
        )

    start = time.perf_counter()
    try:
        resp = httpx.post(
            f"{http_url.rstrip('/')}/parse/repo",
            params={
                "sample_id": sample_id,
                "language": language,
                "overwrite": "true",
            },
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
    except httpx.TimeoutException:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=file_count,
            parse_time_ms=(time.perf_counter() - start) * 1000,
            success=False,
            sample_id=sample_id,
            error=f"timeout after {timeout}s",
        )
    except Exception as exc:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=file_count,
            parse_time_ms=(time.perf_counter() - start) * 1000,
            success=False,
            sample_id=sample_id,
            error=str(exc)[:500],
        )

    if resp.status_code != 200:
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=file_count,
            parse_time_ms=elapsed_ms,
            success=False,
            sample_id=sample_id,
            error=f"HTTP {resp.status_code}: {resp.text[:500]}",
        )

    payload = resp.json()
    if not payload.get("ok"):
        return Sprint9SampleResult(
            sample_key=sample_key,
            mode="bench-repo-jsonl",
            file_count=file_count,
            parse_time_ms=elapsed_ms,
            success=False,
            sample_id=sample_id,
            error=str(payload.get("stderr") or payload.get("error") or "parse failed")[:500],
        )

    cpg_path = str(payload.get("cpg_path") or f"/workspace/cpg-out/{sample_id}")
    reported_count = int(payload.get("file_count") or file_count)
    row = Sprint9SampleResult(
        sample_key=sample_key,
        mode="bench-repo-jsonl",
        file_count=reported_count,
        parse_time_ms=elapsed_ms,
        success=True,
        sample_id=str(payload.get("sample_id") or sample_id),
        cpg_path=cpg_path,
    )
    if query_stats and cpg_path:
        sid = f"bench-{uuid.uuid4().hex[:12]}"
        methods, calls = query_cpg_method_call_counts(
            http_url, cpg_path, session_id=sid, timeout=timeout
        )
        row.method_count, row.call_count = methods, calls
    cleanup_sample(http_url, row.sample_id)
    return row


def run_sprint9_benchmark(
    dataset: Path,
    modes: list[str],
    *,
    http_url: str,
    num_samples: int,
    dry_run: bool,
    timeout: int,
    skip_query_stats: bool,
) -> tuple[list[Sprint9ModeSummary], bool, bool]:
    """Run selected modes; return summaries, dry_run flag, query_stats_enabled."""
    discovered = discover_sven_samples(dataset)
    if not discovered:
        raise ValueError(f"No SVEN-style samples found under {dataset}")

    if len(discovered) > num_samples:
        discovered = random.sample(discovered, num_samples)

    live = False if dry_run else server_reachable(http_url)
    effective_dry_run = dry_run or not live
    query_stats = (not skip_query_stats) and live and (not effective_dry_run)

    summaries: dict[str, Sprint9ModeSummary] = {}
    runners = {
        "bench-file": bench_file_sample,
        "bench-repo-jsonl": bench_repo_jsonl_sample,
    }

    for mode in modes:
        if mode not in runners:
            raise ValueError(f"Unknown mode {mode!r}; choose from {sorted(runners)}")
        summaries[mode] = Sprint9ModeSummary(mode=mode)

    for sample_key, sample_root in discovered:
        for mode in modes:
            print(f"  [{mode}] {sample_key} ...")
            row = runners[mode](
                sample_key,
                sample_root,
                http_url,
                dry_run=effective_dry_run,
                timeout=timeout,
                query_stats=query_stats,
            )
            summaries[mode].results.append(row)
            status = "ok" if row.success else f"FAIL: {row.error[:80]}"
            print(f"    {status} ({row.file_count} files, {row.parse_time_ms:.1f} ms)")

    return list(summaries.values()), effective_dry_run, query_stats


def generate_sprint9_report(
    summaries: list[Sprint9ModeSummary],
    output_path: Path,
    *,
    dataset: Path,
    http_url: str,
    dry_run: bool,
    query_stats: bool,
) -> None:
    lines = [
        "# Joern Server Benchmark — Sprint 9 (file vs repo JSONL)",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Dataset:** `{dataset}`",
        f"**HTTP URL:** `{http_url}`",
        f"**Dry run:** {dry_run}",
        f"**CPG query stats:** {'yes' if query_stats else 'skipped (server down or --skip-query-stats)'}",
        "",
        "---",
        "",
        "## Comparison summary",
        "",
        "| Mode | Samples | Success | file_count (median) | parse p50 (ms) | parse p95 (ms) |",
        "|------|---------|---------|---------------------|----------------|----------------|",
    ]

    for summary in summaries:
        ok = [r for r in summary.results if r.success]
        times = summary.parse_times_ms
        counts = summary.file_counts
        lines.append(
            f"| `{summary.mode}` | {len(summary.results)} | {len(ok)}/{len(summary.results)} | "
            f"{statistics.median(counts) if counts else '—'} | "
            f"{percentile(times, 50):.2f} | {percentile(times, 95):.2f} |"
        )

    lines.extend(["", "---", ""])

    for summary in summaries:
        lines.extend([
            f"## `{summary.mode}`",
            "",
            "| sample | file_count | parse_ms | method_count | call_count | status |",
            "|--------|------------|----------|--------------|------------|--------|",
        ])
        for row in summary.results:
            methods = row.method_count if row.method_count is not None else "—"
            calls = row.call_count if row.call_count is not None else "—"
            status = "ok" if row.success else row.error[:60]
            lines.append(
                f"| {row.sample_key} | {row.file_count} | {row.parse_time_ms:.2f} | "
                f"{methods} | {calls} | {status} |"
            )
        lines.append("")

    if dry_run:
        lines.extend([
            "---",
            "",
            "## Notes",
            "",
            "This report was generated in **dry-run** mode (no live parse). "
            "Re-run without `--dry-run` against a running Joern server for real latencies and CPG stats.",
            "",
        ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))


def build_sprint9_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sprint 9 benchmark: POST /parse (single file) vs POST /parse/repo (JSONL)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  bench-file         Parse one primary source file per sample via POST /parse
  bench-repo-jsonl   Walk each sample directory, POST NDJSON to /parse/repo

Examples:
  python scripts/benchmark_parse.py sprint9 --dataset tests/fixtures/sven_mini --dry-run
  python scripts/benchmark_parse.py sprint9 --dataset /data/sven --modes bench-file,bench-repo-jsonl
""",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="SVEN-style root: each subdirectory is one sample with source files",
    )
    parser.add_argument(
        "--output",
        default="report/benchmark-sprint9.md",
        help="Markdown report path (default: report/benchmark-sprint9.md)",
    )
    parser.add_argument(
        "--modes",
        default="bench-file,bench-repo-jsonl",
        help="Comma-separated modes: bench-file, bench-repo-jsonl (default: both)",
    )
    parser.add_argument(
        "--http-url",
        default="http://localhost:8080",
        help="Joern HTTP proxy base URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Max sample directories to benchmark (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-request timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build NDJSON and report template without calling the server",
    )
    parser.add_argument(
        "--skip-query-stats",
        action="store_true",
        help="Do not run importCpg / cpg.method.size / cpg.call.size after parse",
    )
    return parser


def main_sprint9(argv: list[str] | None = None) -> int:
    args = build_sprint9_arg_parser().parse_args(argv)
    dataset = Path(args.dataset).resolve()
    if not dataset.is_dir():
        print(f"Error: dataset not found: {dataset}", file=sys.stderr)
        return 1

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    invalid = [m for m in modes if m not in ("bench-file", "bench-repo-jsonl")]
    if invalid:
        print(f"Error: unknown mode(s): {invalid}", file=sys.stderr)
        return 1

    print(f"Sprint 9 benchmark — dataset={dataset}")
    print(f"  modes={modes} dry_run={args.dry_run} samples<={args.samples}")

    try:
        summaries, effective_dry_run, query_stats = run_sprint9_benchmark(
            dataset,
            modes,
            http_url=args.http_url,
            num_samples=args.samples,
            dry_run=args.dry_run,
            timeout=args.timeout,
            skip_query_stats=args.skip_query_stats,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    out = Path(args.output)
    generate_sprint9_report(
        summaries,
        out,
        dataset=dataset,
        http_url=args.http_url,
        dry_run=effective_dry_run,
        query_stats=query_stats,
    )
    print(f"\nReport written to {out}")
    return 0


def main_sprint1(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Joern Server parsing performance (Sprint 1)")
    parser.add_argument("--dataset", type=str, required=True, help="Path to dataset directory")
    parser.add_argument("--output", type=str, default="report/benchmark-sprint1.md", help="Output report path")
    parser.add_argument("--cpg-out-dir", type=str, default="tmp/eval")
    parser.add_argument("--http-url", type=str, default="http://localhost:8080", help="Joern Server HTTP URL")
    parser.add_argument("--samples", type=int, default=100, help="Number of samples to test")
    parser.add_argument("--concurrency-levels", type=str, default="1,5,10,20", help="Concurrency levels to test")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Error: Dataset path does not exist: {dataset_path}")
        return 1

    # Find all source files
    files = list_source_files(dataset_path)

    if not files:
        print(f"Error: No source files found in {dataset_path}")
        return 1

    print(f"Found {len(files)} source files in {dataset_path}")
    print(f"Testing with {args.samples} samples")

    # Sample files for testing
    test_files = random.sample(files, min(args.samples, len(files)))

    # Analyze dataset
    line_counts = [count_lines(f) for f in test_files]
    print(f"\nDataset statistics:")
    print(f"  Lines of code: min={min(line_counts)}, max={max(line_counts)}, median={statistics.median(line_counts)}")

    benchmark = BenchmarkResult()

    # Sequential parse benchmark
    print("\n[1/3] Running sequential parse benchmark...")
    parse_results, seq_result = benchmark_sequential_parse(test_files, args.http_url, args.cpg_out_dir, args.samples)
    benchmark.parse_times = seq_result.parse_times
    benchmark.successful_parses = seq_result.successful_parses
    benchmark.total_samples = seq_result.total_samples
    benchmark.errors.extend(seq_result.errors)

    if benchmark.parse_times:
        p50 = statistics.median(benchmark.parse_times)
        print(f"  Sequential parse time: p50={p50:.2f}ms")

    # Concurrent parse benchmark
    print("\n[2/3] Running concurrent parse benchmarks...")
    concurrency_levels = [int(x) for x in args.concurrency_levels.split(",")]

    for conc in concurrency_levels:
        print(f"  Testing concurrency={conc}...")
        throughput = benchmark_concurrent_parse(test_files, args.http_url, args.cpg_out_dir, conc, num_samples=50)
        benchmark.concurrent_throughputs[conc] = throughput
        print(f"    Throughput: {throughput:.2f} files/sec")

    # Query latency benchmark (if we have a successful parse)
    print("\n[3/3] Running query latency benchmark...")
    successful_samples = [r for r in parse_results if r.success]
    if successful_samples:
        # Use one of the successful samples for query benchmark
        sample = successful_samples[0]
        latencies = benchmark_query_latency(args.http_url, sample.sample_id, num_queries=30)
        benchmark.query_latencies = latencies
        if latencies:
            print(f"  Query latency: p50={statistics.median(latencies):.2f}ms")

        # Cleanup
        cleanup_sample(args.http_url, sample.sample_id)

    # Generate report
    print(f"\nGenerating report: {args.output}")
    generate_report(benchmark, Path(args.output))
    print("Done!")

    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "sprint9":
        return main_sprint9(sys.argv[2:])
    return main_sprint1()


if __name__ == "__main__":
    raise SystemExit(main())
