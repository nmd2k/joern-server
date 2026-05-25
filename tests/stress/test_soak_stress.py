"""
Soak stress test for Joern server reliability.

Simulates continuous load with N parallel workers, each running a tight loop
of parse → importCpg → M queries → cleanup. Catches OOM accumulation, replica
degradation under sustained load, CPG registry contention, and increasing
error rates over time.

Modes:
  Mode 1 (default): run for --soak-duration seconds or --soak-max-iters, whichever first.
  Mode 2 (--soak-fail-fast): stop immediately on any worker error, asserting failure.

Usage:

  pytest tests/stress/test_soak_stress.py -m stress -v -s

Tune via CLI args:

  pytest tests/stress/test_soak_stress.py -m stress -v -s \\
    --soak-url http://127.0.0.1:8080 \\
    --soak-duration 600 \\
    --soak-workers 8 \\
    --soak-queries 5 \\
    --soak-max-iters 1000 \\
    --soak-fail-fast \\
    --soak-no-archive
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from joern_server.client import JoernHTTPQueryExecutor

SOAK_SOURCE = """\
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    char *buf;
    size_t len;
    size_t cap;
} Buf;

Buf* make_buf(size_t cap) {
    Buf *b = (Buf*)malloc(sizeof(Buf));
    if (!b) return NULL;
    b->cap = cap > 0 ? cap : 256;
    b->buf = (char*)malloc(b->cap);
    if (!b->buf) { free(b); return NULL; }
    b->len = 0;
    return b;
}

int append(Buf *b, const char *s, size_t n) {
    if (!b || !s) return -1;
    if (b->len + n > b->cap) {
        size_t nc = b->cap * 2;
        while (nc < b->len + n) nc *= 2;
        char *nb = (char*)realloc(b->buf, nc);
        if (!nb) return -1;
        b->buf = nb; b->cap = nc;
    }
    memcpy(b->buf + b->len, s, n);
    b->len += n;
    return 0;
}

void free_buf(Buf *b) {
    if (b) { free(b->buf); free(b); }
}

int process_data(const char *input, int count) {
    Buf *b = make_buf(512);
    if (!b) { printf("buffer alloc failed\\n"); return -1; }
    int i = 0;
    while (i < count) {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "item_%d", i);
        append(b, tmp, strlen(tmp));
        i++;
    }
    free_buf(b);
    return 0;
}
"""

SOAK_QUERIES_LIST = [
    "cpg.method.name.l",
    "cpg.call.name.l",
    "cpg.identifier.name.l",
    "cpg.method.where(_.name(\".*alloc.*\")).l",
    "cpg.literal.code.l",
]

HTTP_502 = "502"
HTTP_504 = "504"
TIMEOUT = "timeout"
OTHER_ERROR = "other_error"


@dataclass
class SoakConfig:
    url: str
    duration: int
    workers: int
    queries_per_session: int
    max_iters: int
    fail_fast: bool
    archive: bool
    run_tag: str


@dataclass
class MinuteBucket:
    parse_ok: int = 0
    parse_fail: int = 0
    parse_502: int = 0
    parse_504: int = 0
    parse_latencies_ms: list[float] = field(default_factory=list)
    query_ok: int = 0
    query_fail: int = 0
    query_502: int = 0
    query_504: int = 0
    query_latencies_ms: list[float] = field(default_factory=list)

    @property
    def parse_total(self) -> int:
        return self.parse_ok + self.parse_fail

    @property
    def query_total(self) -> int:
        return self.query_ok + self.query_fail

    @property
    def error_rate(self) -> float:
        total = self.parse_total + self.query_total
        if total == 0:
            return 0.0
        return (self.parse_fail + self.query_fail) / total

    @staticmethod
    def _percentile(sorted_vals: list[float], pct: float) -> float | None:
        if not sorted_vals:
            return None
        idx = int(len(sorted_vals) * pct)
        return round(sorted_vals[min(idx, len(sorted_vals) - 1)], 1)

    def p50_parse(self) -> float | None:
        return self._percentile(sorted(self.parse_latencies_ms), 0.50)

    def p95_parse(self) -> float | None:
        return self._percentile(sorted(self.parse_latencies_ms), 0.95)

    def p99_parse(self) -> float | None:
        return self._percentile(sorted(self.parse_latencies_ms), 0.99)

    def p50_query(self) -> float | None:
        return self._percentile(sorted(self.query_latencies_ms), 0.50)

    def p95_query(self) -> float | None:
        return self._percentile(sorted(self.query_latencies_ms), 0.95)

    def p99_query(self) -> float | None:
        return self._percentile(sorted(self.query_latencies_ms), 0.99)


class SoakStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: list[MinuteBucket] = []
        self._cumulative_errors: dict[str, int] = {}

    def _ensure_bucket(self, minute: int) -> MinuteBucket:
        while len(self._buckets) <= minute:
            self._buckets.append(MinuteBucket())
        return self._buckets[minute]

    def record_parse(self, minute: int, ok: bool, error_type: str, latency_ms: float) -> None:
        with self._lock:
            b = self._ensure_bucket(minute)
            if ok:
                b.parse_ok += 1
            else:
                b.parse_fail += 1
                key = error_type or OTHER_ERROR
                if key == HTTP_502:
                    b.parse_502 += 1
                elif key == HTTP_504:
                    b.parse_504 += 1
                self._cumulative_errors[key] = self._cumulative_errors.get(key, 0) + 1
            b.parse_latencies_ms.append(latency_ms)

    def record_query(self, minute: int, ok: bool, error_type: str, latency_ms: float) -> None:
        with self._lock:
            b = self._ensure_bucket(minute)
            if ok:
                b.query_ok += 1
            else:
                b.query_fail += 1
                key = error_type or OTHER_ERROR
                if key == HTTP_502:
                    b.query_502 += 1
                elif key == HTTP_504:
                    b.query_504 += 1
                self._cumulative_errors[key] = self._cumulative_errors.get(key, 0) + 1
            b.query_latencies_ms.append(latency_ms)

    def get_buckets(self) -> list[MinuteBucket]:
        with self._lock:
            return list(self._buckets)

    @property
    def total_parse_ok(self) -> int:
        with self._lock:
            return sum(b.parse_ok for b in self._buckets)

    @property
    def total_parse_fail(self) -> int:
        with self._lock:
            return sum(b.parse_fail for b in self._buckets)

    @property
    def total_query_ok(self) -> int:
        with self._lock:
            return sum(b.query_ok for b in self._buckets)

    @property
    def total_query_fail(self) -> int:
        with self._lock:
            return sum(b.query_fail for b in self._buckets)

    @property
    def total_requests(self) -> int:
        return self.total_parse_ok + self.total_parse_fail + self.total_query_ok + self.total_query_fail

    @property
    def total_error_rate(self) -> float:
        total = self.total_requests
        if total == 0:
            return 0.0
        return (self.total_parse_fail + self.total_query_fail) / total

    @property
    def errors_by_type(self) -> dict[str, int]:
        with self._lock:
            return dict(self._cumulative_errors)


class IterationCounter:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


def _live_ok(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/health", timeout=5.0)
        if r.status_code != 200:
            return False
        body = r.json()
        return body.get("joern_ok", body.get("ok")) is not False
    except Exception:
        return False


def _classify_error(error_str: str) -> str:
    lower = error_str.lower()
    if "502" in lower or "bad gateway" in lower:
        return HTTP_502
    if "504" in lower or "gateway timeout" in lower:
        return HTTP_504
    if "timeout" in lower or "timed out" in lower:
        return TIMEOUT
    return OTHER_ERROR


def _check_health_and_metrics(url: str, client: httpx.Client) -> tuple[bool, list[str]]:
    issues: list[str] = []
    healthy = True

    try:
        r_health = client.get(f"{url}/health", timeout=10.0)
        if r_health.status_code != 200:
            issues.append(f"health returned {r_health.status_code}")
            healthy = False
        else:
            body = r_health.json()
            if body.get("joern_ok", body.get("ok")) is False:
                issues.append("health reports joern not ok")
                healthy = False
    except Exception as e:
        issues.append(f"health check failed: {e}")
        healthy = False

    try:
        r_metrics = client.get(f"{url}/metrics", timeout=10.0)
        if r_metrics.status_code == 200:
            text = r_metrics.text
            for match in re.finditer(r'joern_proxy_joern_up(?:\{[^}]*\})?\s+(\d+(?:\.\d+)?)', text):
                val = float(match.group(1))
                if val != 1.0:
                    issues.append(f"joern_up gauge = {val} (expected 1)")
                    healthy = False
    except Exception as e:
        issues.append(f"metrics fetch failed: {e}")

    return healthy, issues


def _run_worker_loop(
    cfg: SoakConfig,
    worker_id: int,
    stop_event: threading.Event,
    stats: SoakStats,
    compteur: IterationCounter,
    t_start: float,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    selected_queries = [
        SOAK_QUERIES_LIST[i % len(SOAK_QUERIES_LIST)]
        for i in range(cfg.queries_per_session)
    ]

    with httpx.Client(timeout=600.0) as hc:
        ex = JoernHTTPQueryExecutor(
            cfg.url,
            http_client=hc,
            session_id=f"soak-{cfg.run_tag}-{worker_id}",
            reuse_base=True,
            retries=0,
            timeout=600.0,
        )

        while not stop_event.is_set():
            iter_num = compteur.increment()
            if iter_num > cfg.max_iters:
                break

            sample_id = f"soak-{cfg.run_tag}-w{worker_id}-{uuid.uuid4().hex[:8]}"
            ex.set_affinity_key(sample_id)
            session_errors: list[dict[str, Any]] = []
            minute = int((time.monotonic() - t_start) / 60)

            t_parse = time.perf_counter()
            pr = ex.parse_source(
                sample_id=sample_id,
                source_code=SOAK_SOURCE,
                language="c",
                filename="snippet.c",
                overwrite=True,
            )
            parse_latency = (time.perf_counter() - t_parse) * 1000.0
            parse_ok = pr.get("ok") is True
            stats.record_parse(
                minute,
                parse_ok,
                _classify_error(str(pr.get("error", ""))) if not parse_ok else "",
                parse_latency,
            )

            if not parse_ok:
                session_errors.append({"phase": "parse", "error": str(pr.get("error", "unknown"))})
                if cfg.fail_fast:
                    stop_event.set()
                errors.append({
                    "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                    "errors": session_errors,
                })
                if cfg.fail_fast:
                    return errors
                continue

            cpg_path = pr.get("cpg_path")
            if not isinstance(cpg_path, str) or not cpg_path:
                session_errors.append({"phase": "parse", "error": f"missing cpg_path: {pr}"})
                if cfg.fail_fast:
                    stop_event.set()
                errors.append({
                    "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                    "errors": session_errors,
                })
                if cfg.fail_fast:
                    return errors
                continue

            imp = ex.execute(f'importCpg("{cpg_path}")')
            imp_ok = imp.get("success") is True
            imp_latency = imp.get("latency_ms", 0.0)
            stats.record_query(
                minute,
                imp_ok,
                _classify_error(imp.get("stderr", "")) if not imp_ok else "",
                imp_latency,
            )

            if not imp_ok:
                session_errors.append({"phase": "importCpg", "error": imp.get("stderr", "unknown")})
                if cfg.fail_fast:
                    stop_event.set()
                    errors.append({
                        "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                        "errors": session_errors,
                    })
                    return errors

                clean = ex.cleanup(sample_id, archive=cfg.archive)
                if clean.get("ok") is not True:
                    session_errors.append({"phase": "cleanup", "error": str(clean.get("error", "unknown"))})
                errors.append({
                    "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                    "errors": session_errors,
                })
                continue

            for qi, query in enumerate(selected_queries):
                q_res = ex.execute(query)
                q_ok = q_res.get("success") is True
                q_latency = q_res.get("latency_ms", 0.0)
                stats.record_query(
                    minute,
                    q_ok,
                    _classify_error(q_res.get("stderr", "")) if not q_ok else "",
                    q_latency,
                )

                if not q_ok:
                    session_errors.append({
                        "phase": f"query_{qi + 1}",
                        "query": query,
                        "error": q_res.get("stderr", "unknown"),
                        "latency_ms": q_latency,
                    })
                    if cfg.fail_fast:
                        stop_event.set()
                        errors.append({
                            "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                            "errors": session_errors,
                        })
                        return errors

            clean = ex.cleanup(sample_id, archive=cfg.archive)
            if clean.get("ok") is not True:
                session_errors.append({"phase": "cleanup", "error": str(clean.get("error", "unknown"))})

            if session_errors:
                errors.append({
                    "worker": worker_id, "iter": iter_num, "sample_id": sample_id,
                    "errors": session_errors,
                })

    return errors


def _health_monitor(
    cfg: SoakConfig,
    stop_event: threading.Event,
    health_errors: list[str],
) -> None:
    with httpx.Client(timeout=15.0) as hc:
        while not stop_event.is_set():
            healthy, issues = _check_health_and_metrics(cfg.url, hc)
            if not healthy:
                ts = time.strftime("%H:%M:%S")
                for issue in issues:
                    msg = f"[soak-health {ts}] DEGRADED: {issue}"
                    health_errors.append(msg)
                    print(msg, flush=True)
            if stop_event.wait(30.0):
                break


def _progress_printer(
    stop_event: threading.Event,
    stats: SoakStats,
    compteur: IterationCounter,
    t_start: float,
) -> None:
    while not stop_event.wait(30.0):
        elapsed = time.monotonic() - t_start
        print(
            f"[soak] {elapsed:.0f}s | iters={compteur.value} "
            f"| parse ok={stats.total_parse_ok} fail={stats.total_parse_fail} "
            f"| query ok={stats.total_query_ok} fail={stats.total_query_fail} "
            f"| err_rate={stats.total_error_rate:.4f}",
            flush=True,
        )


@pytest.fixture(scope="module")
def soak_cfg(request) -> SoakConfig:
    return SoakConfig(
        url=request.config.getoption("--soak-url").rstrip("/"),
        duration=request.config.getoption("--soak-duration"),
        workers=request.config.getoption("--soak-workers"),
        queries_per_session=request.config.getoption("--soak-queries"),
        max_iters=request.config.getoption("--soak-max-iters"),
        fail_fast=request.config.getoption("--soak-fail-fast"),
        archive=not request.config.getoption("--soak-no-archive"),
        run_tag=uuid.uuid4().hex[:10],
    )


@pytest.fixture(scope="module")
def require_live_soak(soak_cfg: SoakConfig) -> str:
    if not _live_ok(soak_cfg.url):
        pytest.skip(f"Joern server not healthy at {soak_cfg.url}/health")
    return soak_cfg.url


@pytest.mark.stress
@pytest.mark.integration
def test_soak_stress(
    require_live_soak: str,
    soak_cfg: SoakConfig,
) -> None:
    """
    Run continuous soak test: N workers looping parse → importCpg → M queries
    → cleanup until duration or max iterations are reached.

    Measures per-minute error rates, latencies, and system health.
    """
    cfg = soak_cfg
    t_start = time.monotonic()
    stats = SoakStats()
    compteur = IterationCounter()
    stop_event = threading.Event()
    health_errors: list[str] = []

    mode = "fail-fast" if cfg.fail_fast else "continuous"
    print(
        f"\n[soak] url={cfg.url} mode={mode} duration={cfg.duration}s "
        f"workers={cfg.workers} queries={cfg.queries_per_session} "
        f"max_iters={cfg.max_iters} archive={cfg.archive} tag={cfg.run_tag}",
        flush=True,
    )

    health_thread = threading.Thread(
        target=_health_monitor, args=(cfg, stop_event, health_errors), daemon=True,
    )
    health_thread.start()

    progress_thread = threading.Thread(
        target=_progress_printer, args=(stop_event, stats, compteur, t_start), daemon=True,
    )
    progress_thread.start()

    worker_errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futures = [
            pool.submit(_run_worker_loop, cfg, w, stop_event, stats, compteur, t_start)
            for w in range(cfg.workers)
        ]

        while not stop_event.is_set():
            elapsed = time.monotonic() - t_start
            if elapsed >= cfg.duration:
                break
            if compteur.value >= cfg.max_iters:
                break
            time.sleep(0.5)

        stop_event.set()

        for f in futures:
            try:
                errs = f.result(timeout=30)
                worker_errors.extend(errs)
            except Exception as e:
                worker_errors.append({"worker": "unknown", "exception": str(e)})

    health_thread.join(timeout=5)
    progress_thread.join(timeout=5)

    elapsed = time.monotonic() - t_start
    buckets = stats.get_buckets()

    print(f"\n[soak] === RESULTS ===", flush=True)
    print(
        f"[soak] elapsed: {elapsed:.1f}s  iterations_completed: {compteur.value}",
        flush=True,
    )
    print(f"[soak] total error_rate: {stats.total_error_rate:.4f}", flush=True)
    print(
        f"[soak] parse: ok={stats.total_parse_ok} fail={stats.total_parse_fail}",
        flush=True,
    )
    print(
        f"[soak] query: ok={stats.total_query_ok} fail={stats.total_query_fail}",
        flush=True,
    )
    print(f"[soak] errors by type: {json.dumps(stats.errors_by_type)}", flush=True)
    print(f"[soak] health issues: {len(health_errors)}", flush=True)

    for i, b in enumerate(buckets):
        if b.parse_total == 0 and b.query_total == 0:
            continue
        print(
            f"[soak] minute {i:>3}: "
            f"parse ok={b.parse_ok} fail={b.parse_fail} (502={b.parse_502} 504={b.parse_504}) "
            f"| query ok={b.query_ok} fail={b.query_fail} (502={b.query_502} 504={b.query_504}) "
            f"| err_rate={b.error_rate:.4f} "
            f"| p50_p={b.p50_parse()} p95_p={b.p95_parse()} p99_p={b.p99_parse()} "
            f"p50_q={b.p50_query()} p95_q={b.p95_query()} p99_q={b.p99_query()}",
            flush=True,
        )

    for he in health_errors:
        print(f"[soak-health] {he}", flush=True)

    if worker_errors:
        print(f"\n[soak] worker errors ({len(worker_errors)}):", flush=True)
        for e in worker_errors[:20]:
            print(f"  {json.dumps(e, default=str)}", flush=True)
        if len(worker_errors) > 20:
            print(f"  ... and {len(worker_errors) - 20} more", flush=True)

    if cfg.fail_fast:
        assert len(worker_errors) == 0, (
            f"fail-fast mode: {len(worker_errors)} error(s) encountered"
        )

    assert len(health_errors) == 0, (
        f"{len(health_errors)} health check failure(s): {health_errors[:10]}"
    )

    assert stats.total_error_rate < 0.01, (
        f"total error rate {stats.total_error_rate:.4f} >= 1% "
        f"({stats.total_parse_fail + stats.total_query_fail} errors / "
        f"{stats.total_requests} requests)"
    )

    non_empty = [b for b in buckets if b.parse_total + b.query_total > 0]
    if len(non_empty) >= 2:
        first_rate = non_empty[0].error_rate
        last_rate = non_empty[-1].error_rate
        assert last_rate <= first_rate, (
            f"error rate increasing over time: "
            f"first_min={first_rate:.4f} last_min={last_rate:.4f}"
        )
