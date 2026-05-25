"""
PrimeVul JSONL stress test (live Joern stack required).

Loads ``func`` from a PrimeVul paired JSONL file, parses each sample, imports the CPG,
runs repeated ``cpg.method.name.l`` queries with a pause between queries, optionally
cleans up disk state per sample.

Default profile (200 samples, 5 workers, 4 queries, 2s sleep):

  NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080 \\
  pytest tests/stress/test_primevul_stress.py -m stress -v -s

Tune via env:

  NEURALATLAS_PRIMEVUL_JSONL=/datadrive/data/raw/primevul/primevul_test_paired.jsonl
  NEURALATLAS_STRESS_PRIMEVUL_LIMIT=200
  NEURALATLAS_STRESS_PRIMEVUL_WORKERS=5
  NEURALATLAS_STRESS_PRIMEVUL_QUERIES=4
  NEURALATLAS_STRESS_PRIMEVUL_QUERY_SLEEP_SEC=2
  NEURALATLAS_STRESS_PRIMEVUL_CLEANUP=1
  NEURALATLAS_STRESS_PRIMEVUL_LANGUAGE=c
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from joern_server.client import JoernHTTPQueryExecutor
from joern_server.cpg.paths import safe_sample_id

LIVE_URL = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")
JSONL_PATH = os.environ.get(
    "NEURALATLAS_PRIMEVUL_JSONL",
    "/datadrive/data/raw/primevul/primevul_test_paired.jsonl",
)
LIMIT = int(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_LIMIT", "200"))
WORKERS = int(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_WORKERS", "5"))
QUERIES_PER_SAMPLE = int(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_QUERIES", "4"))
QUERY_SLEEP_SEC = float(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_QUERY_SLEEP_SEC", "2"))
DO_CLEANUP = os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_CLEANUP", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
LANGUAGE = os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_LANGUAGE", "c")
PARSE_TIMEOUT = float(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_PARSE_TIMEOUT_SEC", "900"))
QUERY_TIMEOUT = float(os.environ.get("NEURALATLAS_STRESS_PRIMEVUL_QUERY_TIMEOUT_SEC", "600"))
RUN_TAG = os.environ.get("NEURALATLAS_STRESS_RUN_TAG", uuid.uuid4().hex[:10])

QUERY = "cpg.method.name.l"


@dataclass(frozen=True)
class PrimevulRecord:
    line_no: int
    idx: int | None
    func: str
    project: str | None


def _live_ok() -> bool:
    try:
        r = httpx.get(f"{LIVE_URL}/health", timeout=5.0)
        if r.status_code != 200:
            return False
        body = r.json()
        return body.get("joern_ok", body.get("ok")) is not False
    except Exception:
        return False


def load_primevul_records(path: str | Path, *, limit: int) -> list[PrimevulRecord]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"PrimeVul JSONL not found: {p}")
    out: list[PrimevulRecord] = []
    with p.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if len(out) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            func = obj.get("func")
            if not isinstance(func, str) or not func.strip():
                raise ValueError(f"line {line_no}: missing or empty 'func'")
            idx_raw = obj.get("idx")
            idx = int(idx_raw) if idx_raw is not None else None
            project = obj.get("project") if isinstance(obj.get("project"), str) else None
            out.append(
                PrimevulRecord(
                    line_no=line_no,
                    idx=idx,
                    func=func,
                    project=project,
                )
            )
    if len(out) < limit:
        raise ValueError(f"only {len(out)} records in {p}, need {limit}")
    return out


def _sample_id_for(rec: PrimevulRecord, run_tag: str) -> str:
    key = str(rec.idx) if rec.idx is not None else str(rec.line_no)
    return safe_sample_id(f"primevul-{run_tag}-{key}")


def _run_one_sample(base_url: str, rec: PrimevulRecord, run_tag: str) -> dict[str, Any]:
    sample_id = _sample_id_for(rec, run_tag)
    session_id = f"primevul-{run_tag}-L{rec.line_no}"
    t0 = time.perf_counter()
    stats: dict[str, Any] = {
        "sample_id": sample_id,
        "line_no": rec.line_no,
        "idx": rec.idx,
        "ok": False,
    }

    with httpx.Client(timeout=max(PARSE_TIMEOUT, QUERY_TIMEOUT) + 30.0) as hc:
        ex = JoernHTTPQueryExecutor(
            base_url,
            http_client=hc,
            session_id=session_id,
            affinity_key=sample_id,
            reuse_base=True,
            retries=1,
            timeout=max(PARSE_TIMEOUT, QUERY_TIMEOUT),
        )
        pr = ex.parse_source(
            sample_id=sample_id,
            source_code=rec.func,
            language=LANGUAGE,
            filename="snippet.c",
            overwrite=True,
        )
        if pr.get("ok") is not True:
            stats["error"] = f"parse failed: {pr}"
            stats["elapsed_sec"] = time.perf_counter() - t0
            return stats

        cpg_path = pr.get("cpg_path")
        if not isinstance(cpg_path, str) or not cpg_path:
            stats["error"] = f"parse missing cpg_path: {pr}"
            stats["elapsed_sec"] = time.perf_counter() - t0
            return stats

        stats["cpg_path"] = cpg_path
        stats["cache_hit"] = pr.get("cache_hit")
        stats["func_chars"] = len(rec.func)

        imp = ex.execute(f'importCpg("{cpg_path}")')
        if imp.get("success") is not True:
            stats["error"] = f"importCpg failed: {imp}"
            stats["elapsed_sec"] = time.perf_counter() - t0
            return stats

        query_latencies: list[float] = []
        for q in range(QUERIES_PER_SAMPLE):
            if q > 0:
                time.sleep(QUERY_SLEEP_SEC)
            res = ex.execute(QUERY)
            query_latencies.append(float(res.get("latency_ms") or 0.0))
            if res.get("success") is not True:
                stats["error"] = f"query {q + 1}/{QUERIES_PER_SAMPLE} failed: {res}"
                stats["query_latencies_ms"] = query_latencies
                stats["elapsed_sec"] = time.perf_counter() - t0
                return stats

        stats["query_latencies_ms"] = query_latencies
        if DO_CLEANUP:
            clean = ex.cleanup(sample_id)
            stats["cleanup_ok"] = clean.get("ok") is True
            if not stats["cleanup_ok"]:
                stats["cleanup_error"] = clean

        stats["ok"] = True
        stats["elapsed_sec"] = time.perf_counter() - t0
        return stats


@pytest.fixture(scope="module")
def require_live() -> str:
    if not _live_ok():
        pytest.skip(f"Joern VIP unhealthy at {LIVE_URL}/health")
    if not Path(JSONL_PATH).is_file():
        pytest.skip(f"PrimeVul JSONL missing: {JSONL_PATH}")
    return LIVE_URL


@pytest.fixture(scope="module")
def primevul_records() -> list[PrimevulRecord]:
    return load_primevul_records(JSONL_PATH, limit=LIMIT)


@pytest.mark.stress
@pytest.mark.integration
def test_primevul_parse_and_query_parallel(require_live: str, primevul_records: list[PrimevulRecord]) -> None:
    """
    Parse LIMIT PrimeVul functions with WORKERS parallel threads; QUERIES_PER_SAMPLE
    ``cpg.method.name.l`` calls separated by QUERY_SLEEP_SEC seconds.
    """
    base = require_live
    records = primevul_records
    run_tag = RUN_TAG
    t_start = time.perf_counter()

    print(
        f"\n[primevul-stress] url={base} jsonl={JSONL_PATH} n={len(records)} "
        f"workers={WORKERS} queries={QUERIES_PER_SAMPLE} sleep={QUERY_SLEEP_SEC}s tag={run_tag}",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {
            pool.submit(_run_one_sample, base, rec, run_tag): rec
            for rec in records
        }
        done = 0
        for fut in as_completed(futs):
            rec = futs[fut]
            done += 1
            try:
                stats = fut.result()
                results.append(stats)
                if not stats.get("ok"):
                    failures.append(
                        f"line={rec.line_no} idx={rec.idx} sample={stats.get('sample_id')}: "
                        f"{stats.get('error', 'unknown')}"
                    )
                if done % 10 == 0 or done == len(records):
                    ok_so_far = sum(1 for r in results if r.get("ok"))
                    print(
                        f"[primevul-stress] progress {done}/{len(records)} ok={ok_so_far} "
                        f"fail={len(failures)}",
                        flush=True,
                    )
            except Exception as exc:
                failures.append(f"line={rec.line_no} idx={rec.idx}: {exc}")

    elapsed = time.perf_counter() - t_start
    ok_count = sum(1 for r in results if r.get("ok"))
    parse_times = [r["elapsed_sec"] for r in results if r.get("ok") and "elapsed_sec" in r]

    summary = {
        "total": len(records),
        "ok": ok_count,
        "failed": len(failures),
        "workers": WORKERS,
        "wall_sec": round(elapsed, 2),
        "avg_sample_sec": round(sum(parse_times) / len(parse_times), 2) if parse_times else None,
    }
    print(f"[primevul-stress] summary {json.dumps(summary)}", flush=True)

    assert ok_count == len(records), (
        f"{len(failures)} failure(s); first 10:\n" + "\n".join(failures[:10])
        + (f"\n... and {len(failures) - 10} more" if len(failures) > 10 else "")
        + f"\nsummary={summary}"
    )
