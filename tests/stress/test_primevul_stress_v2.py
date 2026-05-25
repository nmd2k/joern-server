"""
PrimeVul multi-turn stress test v2 (live Joern stack required).

Simulates realistic client sessions: 8 parallel workers, each processing samples
with parse → importCpg → N follow-up query turns → cleanup.

Reports detailed error breakdown (502, 504, timeout, success rate) to verify
the reliability fixes for the 502 Bad Gateway issue.

Usage:

  pytest tests/stress/test_primevul_stress_v2.py -m stress -v -s

Tune via CLI args:

  pytest tests/stress/test_primevul_stress_v2.py -m stress -v -s \
    --stress-url http://127.0.0.1:8080 \
    --stress-jsonl /datadrive/data/raw/primevul/primevul_test_paired.jsonl \
    --stress-samples 200 \
    --stress-workers 8 \
    --stress-turns 10 \
    --stress-turn-sleep 1 \
    --stress-no-cleanup \
    --stress-language c \
    --stress-parse-timeout 900 \
    --stress-query-timeout 600
"""

from __future__ import annotations

import json
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

QUERIES = [
    "cpg.method.name.l",
    "cpg.call.name.l",
    "cpg.identifier.name.l",
    "cpg.method.fullName.l",
    "cpg.call.code.l",
    "cpg.identifier.code.l",
    "cpg.method.isExternal.l",
    "cpg.call.where(_.name(\".*free.*\")).l",
    "cpg.method.where(_.name(\".*main.*\")).l",
    "cpg.literal.code.l",
]

HTTP_502 = "502"
HTTP_504 = "504"
TIMEOUT = "timeout"
OTHER_ERROR = "other_error"


@dataclass
class StressConfig:
    url: str
    jsonl_path: str
    samples: int
    workers: int
    turns: int
    turn_sleep_sec: float
    do_cleanup: bool
    language: str
    parse_timeout: float
    query_timeout: float
    run_tag: str


@dataclass(frozen=True)
class PrimevulRecord:
    line_no: int
    idx: int | None
    func: str
    project: str | None


@dataclass
class ErrorCounter:
    count_502: int = 0
    count_504: int = 0
    count_timeout: int = 0
    count_other: int = 0
    count_success: int = 0

    def record(self, error_type: str | None) -> None:
        if error_type is None:
            self.count_success += 1
        elif error_type == HTTP_502:
            self.count_502 += 1
        elif error_type == HTTP_504:
            self.count_504 += 1
        elif error_type == TIMEOUT:
            self.count_timeout += 1
        else:
            self.count_other += 1

    @property
    def total(self) -> int:
        return self.count_502 + self.count_504 + self.count_timeout + self.count_other + self.count_success

    @property
    def total_errors(self) -> int:
        return self.count_502 + self.count_504 + self.count_timeout + self.count_other

    def summary(self) -> dict[str, Any]:
        return {
            "total_requests": self.total,
            "success": self.count_success,
            "success_rate": round(self.count_success / self.total, 4) if self.total > 0 else 0,
            "502_bad_gateway": self.count_502,
            "504_gateway_timeout": self.count_504,
            "timeout": self.count_timeout,
            "other_error": self.count_other,
        }


@pytest.fixture(scope="module")
def stress_cfg(request) -> StressConfig:
    return StressConfig(
        url=request.config.getoption("--stress-url").rstrip("/"),
        jsonl_path=request.config.getoption("--stress-jsonl"),
        samples=request.config.getoption("--stress-samples"),
        workers=request.config.getoption("--stress-workers"),
        turns=request.config.getoption("--stress-turns"),
        turn_sleep_sec=request.config.getoption("--stress-turn-sleep"),
        do_cleanup=not request.config.getoption("--stress-no-cleanup"),
        language=request.config.getoption("--stress-language"),
        parse_timeout=request.config.getoption("--stress-parse-timeout"),
        query_timeout=request.config.getoption("--stress-query-timeout"),
        run_tag=uuid.uuid4().hex[:10],
    )


def _live_ok(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/health", timeout=5.0)
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
    return safe_sample_id(f"primevul-v2-{run_tag}-{key}")


def _classify_error(error_str: str) -> str:
    lower = error_str.lower()
    if "502" in lower or "bad gateway" in lower:
        return HTTP_502
    if "504" in lower or "gateway timeout" in lower:
        return HTTP_504
    if "timeout" in lower or "timed out" in lower:
        return TIMEOUT
    return OTHER_ERROR


def _run_one_session(
    cfg: StressConfig,
    rec: PrimevulRecord,
    turn_queries: list[str],
) -> dict[str, Any]:
    sample_id = _sample_id_for(rec, cfg.run_tag)
    session_id = f"primevul-v2-{cfg.run_tag}-L{rec.line_no}"
    t0 = time.perf_counter()
    errors: list[dict[str, str]] = []
    turn_latencies: list[float] = []
    error_counter = ErrorCounter()
    query_timeout = max(cfg.parse_timeout, cfg.query_timeout)

    with httpx.Client(timeout=query_timeout + 30.0) as hc:
        ex = JoernHTTPQueryExecutor(
            cfg.url,
            http_client=hc,
            session_id=session_id,
            affinity_key=sample_id,
            reuse_base=True,
            retries=0,
            timeout=query_timeout,
        )

        pr = ex.parse_source(
            sample_id=sample_id,
            source_code=rec.func,
            language=cfg.language,
            filename="snippet.c",
            overwrite=True,
        )
        error_counter.record(None if pr.get("ok") is True else _classify_error(str(pr.get("error", ""))))
        if pr.get("ok") is not True:
            errors.append({"phase": "parse", "error": str(pr.get("error", "unknown"))})
            return {
                "sample_id": sample_id,
                "line_no": rec.line_no,
                "ok": False,
                "errors": errors,
                "error_counter": error_counter.summary(),
                "elapsed_sec": time.perf_counter() - t0,
            }

        cpg_path = pr.get("cpg_path")
        if not isinstance(cpg_path, str) or not cpg_path:
            errors.append({"phase": "parse", "error": f"missing cpg_path: {pr}"})
            return {
                "sample_id": sample_id,
                "line_no": rec.line_no,
                "ok": False,
                "errors": errors,
                "error_counter": error_counter.summary(),
                "elapsed_sec": time.perf_counter() - t0,
            }

        imp = ex.execute(f'importCpg("{cpg_path}")')
        imp_ok = imp.get("success") is True
        error_counter.record(None if imp_ok else _classify_error(imp.get("stderr", "")))
        if not imp_ok:
            errors.append({"phase": "importCpg", "error": imp.get("stderr", "unknown")})
            return {
                "sample_id": sample_id,
                "line_no": rec.line_no,
                "ok": False,
                "errors": errors,
                "error_counter": error_counter.summary(),
                "elapsed_sec": time.perf_counter() - t0,
            }

        for turn_idx in range(cfg.turns):
            if turn_idx > 0:
                time.sleep(cfg.turn_sleep_sec)
            query = turn_queries[turn_idx % len(turn_queries)]
            t_turn = time.perf_counter()
            res = ex.execute(query)
            turn_lat = (time.perf_counter() - t_turn) * 1000.0
            turn_latencies.append(turn_lat)

            success = res.get("success") is True
            stderr = res.get("stderr", "")
            error_counter.record(None if success else _classify_error(stderr))

            if not success:
                errors.append({
                    "phase": f"turn_{turn_idx + 1}",
                    "query": query,
                    "error": stderr,
                    "latency_ms": round(turn_lat, 1),
                })

        if cfg.do_cleanup:
            clean = ex.cleanup(sample_id)
            if clean.get("ok") is not True:
                errors.append({"phase": "cleanup", "error": str(clean.get("error", "unknown"))})

        stats = {
            "sample_id": sample_id,
            "line_no": rec.line_no,
            "ok": len(errors) == 0,
            "errors": errors,
            "error_counter": error_counter.summary(),
            "turn_latencies_ms": turn_latencies,
            "avg_turn_latency_ms": round(sum(turn_latencies) / len(turn_latencies), 1) if turn_latencies else 0,
            "elapsed_sec": time.perf_counter() - t0,
            "func_chars": len(rec.func),
            "cpg_path": cpg_path,
        }
        return stats


@pytest.fixture(scope="module")
def require_live(stress_cfg: StressConfig) -> str:
    if not _live_ok(stress_cfg.url):
        pytest.skip(f"Joern VIP unhealthy at {stress_cfg.url}/health")
    if not Path(stress_cfg.jsonl_path).is_file():
        pytest.skip(f"PrimeVul JSONL missing: {stress_cfg.jsonl_path}")
    return stress_cfg.url


@pytest.fixture(scope="module")
def primevul_records(stress_cfg: StressConfig) -> list[PrimevulRecord]:
    return load_primevul_records(stress_cfg.jsonl_path, limit=stress_cfg.samples)


@pytest.mark.stress
@pytest.mark.integration
def test_primevul_multi_turn_stress(
    require_live: str,
    primevul_records: list[PrimevulRecord],
    stress_cfg: StressConfig,
) -> None:
    """
    Parse SAMPLES PrimeVul functions with WORKERS parallel threads;
    each session runs TURNS follow-up queries with different CPGQL patterns.
    """
    base = require_live
    records = primevul_records
    cfg = stress_cfg
    t_start = time.perf_counter()

    print(
        f"\n[primevul-stress-v2] url={base} jsonl={cfg.jsonl_path} n={len(records)} "
        f"workers={cfg.workers} turns={cfg.turns} sleep={cfg.turn_sleep_sec}s tag={cfg.run_tag}",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    aggregate_errors = ErrorCounter()

    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futs = {
            pool.submit(_run_one_session, cfg, rec, QUERIES): rec
            for rec in records
        }
        done = 0
        for fut in as_completed(futs):
            rec = futs[fut]
            done += 1
            try:
                stats = fut.result()
                results.append(stats)
                ec = stats.get("error_counter", {})
                aggregate_errors.count_502 += ec.get("502_bad_gateway", 0)
                aggregate_errors.count_504 += ec.get("504_gateway_timeout", 0)
                aggregate_errors.count_timeout += ec.get("timeout", 0)
                aggregate_errors.count_other += ec.get("other_error", 0)
                aggregate_errors.count_success += ec.get("success", 0)

                if done % 10 == 0 or done == len(records):
                    ok_so_far = sum(1 for r in results if r.get("ok"))
                    print(
                        f"[primevul-stress-v2] progress {done}/{len(records)} ok={ok_so_far} "
                        f"fail={done - ok_so_far} 502={aggregate_errors.count_502} "
                        f"504={aggregate_errors.count_504} timeout={aggregate_errors.count_timeout}",
                        flush=True,
                    )
            except Exception as exc:
                aggregate_errors.count_other += 1
                print(f"[primevul-stress-v2] exception line={rec.line_no}: {exc}", flush=True)

    elapsed = time.perf_counter() - t_start
    ok_count = sum(1 for r in results if r.get("ok"))
    sample_times = [r["elapsed_sec"] for r in results if "elapsed_sec" in r]
    all_turn_latencies = []
    for r in results:
        all_turn_latencies.extend(r.get("turn_latencies_ms", []))

    summary = {
        "total_samples": len(records),
        "ok_samples": ok_count,
        "failed_samples": len(records) - ok_count,
        "workers": cfg.workers,
        "turns_per_sample": cfg.turns,
        "total_requests": aggregate_errors.total,
        "wall_sec": round(elapsed, 2),
        "avg_sample_sec": round(sum(sample_times) / len(sample_times), 2) if sample_times else None,
        "avg_turn_latency_ms": round(sum(all_turn_latencies) / len(all_turn_latencies), 1) if all_turn_latencies else None,
        "p95_turn_latency_ms": round(sorted(all_turn_latencies)[int(len(all_turn_latencies) * 0.95)], 1) if all_turn_latencies else None,
        "error_breakdown": aggregate_errors.summary(),
    }

    print(f"\n[primevul-stress-v2] summary {json.dumps(summary, indent=2)}", flush=True)

    all_failures = []
    for r in results:
        if r.get("errors"):
            for e in r["errors"]:
                all_failures.append(
                    f"line={r['line_no']} sample={r['sample_id']} phase={e['phase']}: {e.get('error', 'unknown')}"
                )

    assert aggregate_errors.count_502 == 0, (
        f"502 Bad Gateway errors detected: {aggregate_errors.count_502}. "
        f"Full summary: {json.dumps(summary, indent=2)}"
    )

    assert ok_count == len(records), (
        f"{len(records) - ok_count} sample(s) failed; first 10:\n"
        + "\n".join(all_failures[:10])
        + (f"\n... and {len(all_failures) - 10} more" if len(all_failures) > 10 else "")
        + f"\nsummary={json.dumps(summary, indent=2)}"
    )
