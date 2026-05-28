#!/usr/bin/env python3
"""Reliable bulk parse + archive for IRIS snapshot repos.

Design goals:
  - Wait until HAProxy has enough healthy Joern backends before starting.
  - Resume using *latest* status per sample_id (not raw append noise).
  - Low parallelism (default 2) so replicas are not overwhelmed.
  - Parse + cleanup pinned to same replica (X-Affinity-Key + X-Session-Id).
  - Pass source_hash into /cleanup so archive works across replicas.
  - Retry 502/503/504 with backoff; never treat transient errors as final without retries.
  - Small repos first; optional skip list for known-oversized trees.

Usage:
  python scripts/bulk_iris_parse_archive.py --wait-backends 10 --workers 2 --resume --overwrite
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_BASE = os.environ.get("JOERN_BASE_URL", "http://localhost:8080")
DEFAULT_SNAPSHOTS = os.environ.get("IRIS_SNAPSHOTS_DIR", "/datadrive/data/IRIS/snapshots")
HAPROXY_STATS = os.environ.get("JOERN_HAPROXY_STATS", "http://127.0.0.1:8404/stats;csv")
CONTAINER_DATASETS = "/workspace/datasets"
VARIANTS = ("buggy", "fix")

# Repos whose .java tree exceeds server byte limits even after filtering.
DEFAULT_SKIP_SNAPSHOTS = frozenset({
    "aws-sdk-java__5555d847__cb66c50c",
})


@dataclass
class Job:
    snapshot: str
    variant: str
    java_files: int = 0

    @property
    def sample_id(self) -> str:
        return f"{self.snapshot}__{self.variant}"

    @property
    def source_root(self) -> str:
        return f"{CONTAINER_DATASETS}/{self.snapshot}/{self.variant}"


def discover_jobs(snapshots_dir: Path, *, skip_snapshots: set[str]) -> list[Job]:
    jobs: list[Job] = []
    for snap in sorted(snapshots_dir.iterdir()):
        if not snap.is_dir() or snap.name.startswith("."):
            continue
        if snap.name in skip_snapshots:
            continue
        for variant in VARIANTS:
            root = snap / variant
            if not root.is_dir():
                continue
            java_n = sum(1 for p in root.rglob("*.java") if p.is_file())
            jobs.append(Job(snapshot=snap.name, variant=variant, java_files=java_n))
    jobs.sort(key=lambda j: j.java_files)
    return jobs


def load_latest_status(progress_path: Path) -> dict[str, dict[str, Any]]:
    """Last record per sample_id wins."""
    latest: dict[str, dict[str, Any]] = {}
    if not progress_path.is_file():
        return latest
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("sample_id")
        if sid:
            latest[sid] = rec
    return latest


def compact_progress(progress_path: Path, latest: dict[str, dict[str, Any]]) -> None:
    """Rewrite progress file to one line per sample_id (latest only)."""
    if not latest:
        return
    tmp = progress_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for sid in sorted(latest.keys()):
            f.write(json.dumps(latest[sid], ensure_ascii=False) + "\n")
    tmp.replace(progress_path)


def append_progress(progress_path: Path, record: dict[str, Any], latest: dict[str, dict[str, Any]]) -> None:
    sid = record.get("sample_id")
    if sid:
        latest[sid] = record
    with progress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def _parse_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": str(data)[:500]}
    except Exception:
        return {"raw": resp.text[:500]}


def count_healthy_backends(stats_url: str) -> int:
    """Parse HAProxy CSV stats; count joern backends with status UP."""
    try:
        r = httpx.get(stats_url, timeout=5.0)
        if r.status_code != 200:
            return 0
        text = r.text
        n = 0
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            # pxname,svname,... status is typically 17th field in csv
            parts = line.split(",")
            if len(parts) < 18:
                continue
            pxname, svname, status = parts[0], parts[1], parts[17]
            if pxname == "be_joern_http" and svname.startswith("joern") and status == "UP":
                n += 1
        return n
    except Exception:
        return 0


def wait_for_cluster(
    base_url: str,
    *,
    min_backends: int,
    stats_url: str,
    timeout_sec: int,
) -> bool:
    deadline = time.time() + timeout_sec
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=15.0) as client:
        while time.time() < deadline:
            healthy = count_healthy_backends(stats_url)
            try:
                h = client.get("/health")
                ok = h.status_code == 200 and h.json().get("ok") is True
            except Exception:
                ok = False
            if ok and healthy >= min_backends:
                print(f"cluster ready: health ok, {healthy} backends UP (need>={min_backends})")
                return True
            print(f"waiting... health={ok} backends_up={healthy}/{min_backends}")
            time.sleep(5)
    return False


def parse_and_archive(
    client: httpx.Client,
    job: Job,
    *,
    overwrite: bool,
    include_extensions: Optional[list[str]],
    worker_id: int,
    max_retries: int = 10,
) -> dict[str, Any]:
    headers = {
        "X-Session-Id": f"iris-bulk-{worker_id}",
        "X-Affinity-Key": job.sample_id,
        "Content-Type": "application/json",
    }
    parse_body: dict[str, Any] = {
        "sample_id": job.sample_id,
        "source_root": job.source_root,
        "overwrite": overwrite,
    }
    if include_extensions:
        parse_body["include_extensions"] = include_extensions

    t0 = time.perf_counter()
    parse_resp: Optional[httpx.Response] = None
    parse_json: dict[str, Any] = {}

    for attempt in range(max_retries):
        try:
            parse_resp = client.post("/parse/repo", json=parse_body, headers=headers, timeout=7200.0)
            parse_json = _parse_json(parse_resp)
        except Exception as exc:
            if attempt + 1 >= max_retries:
                return {
                    "sample_id": job.sample_id,
                    "status": "parse_failed",
                    "http_status": 0,
                    "elapsed_sec": round(time.perf_counter() - t0, 2),
                    "response": {"error": str(exc)},
                }
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        if parse_resp.status_code in (502, 503, 504) and attempt + 1 < max_retries:
            time.sleep(min(90, 10 * (attempt + 1)))
            continue
        break

    parse_elapsed = time.perf_counter() - t0
    if parse_resp is None or parse_resp.status_code != 200 or not parse_json.get("ok"):
        code = parse_json.get("code", "")
        if code == "payload_too_large":
            return {
                "sample_id": job.sample_id,
                "status": "skipped_too_large",
                "http_status": parse_resp.status_code if parse_resp else 0,
                "elapsed_sec": round(parse_elapsed, 2),
                "response": parse_json,
            }
        return {
            "sample_id": job.sample_id,
            "status": "parse_failed",
            "http_status": parse_resp.status_code if parse_resp else 0,
            "elapsed_sec": round(parse_elapsed, 2),
            "response": parse_json,
        }

    source_hash = parse_json.get("source_hash")
    cleanup_body: dict[str, Any] = {"sample_id": job.sample_id, "archive": True}
    if source_hash:
        cleanup_body["source_hash"] = source_hash

    cleanup_resp: Optional[httpx.Response] = None
    cleanup_json: dict[str, Any] = {}
    for attempt in range(max_retries):
        try:
            cleanup_resp = client.post("/cleanup", json=cleanup_body, headers=headers, timeout=300.0)
            cleanup_json = _parse_json(cleanup_resp)
        except Exception as exc:
            if attempt + 1 >= max_retries:
                return {
                    "sample_id": job.sample_id,
                    "status": "archive_failed",
                    "parse_elapsed_sec": round(parse_elapsed, 2),
                    "source_hash": source_hash,
                    "cleanup_status": 0,
                    "cleanup": {"error": str(exc)},
                }
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        if cleanup_resp.status_code in (502, 503, 504) and attempt + 1 < max_retries:
            time.sleep(min(90, 10 * (attempt + 1)))
            continue
        break

    archived = (
        cleanup_resp is not None
        and cleanup_resp.status_code == 200
        and cleanup_json.get("archived") is True
    )
    return {
        "sample_id": job.sample_id,
        "status": "archived" if archived else "archive_failed",
        "parse_elapsed_sec": round(parse_elapsed, 2),
        "cache_hit": parse_json.get("cache_hit"),
        "source_hash": source_hash,
        "cleanup_status": cleanup_resp.status_code if cleanup_resp else 0,
        "cleanup": cleanup_json,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reliable bulk IRIS parse + archive")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--snapshots-dir", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--wait-backends", type=int, default=10, help="min HAProxy UP backends before start")
    parser.add_argument("--wait-timeout", type=int, default=600)
    parser.add_argument("--progress", default="deploy/iris_bulk_progress.jsonl")
    parser.add_argument("--include-extensions", default=".java")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--compact", action="store_true", help="rewrite progress to latest-only before run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--skip-snapshots",
        default=",".join(sorted(DEFAULT_SKIP_SNAPSHOTS)),
        help="comma-separated snapshot dir names to skip",
    )
    args = parser.parse_args()

    snapshots_dir = Path(args.snapshots_dir)
    if not snapshots_dir.is_dir():
        print(f"snapshots dir not found: {snapshots_dir}", file=sys.stderr)
        return 1

    skip = {s.strip() for s in args.skip_snapshots.split(",") if s.strip()}
    include_ext: Optional[list[str]] = None
    if args.include_extensions.strip():
        include_ext = [e.strip() for e in args.include_extensions.split(",") if e.strip()]

    progress_path = Path(args.progress)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    latest = load_latest_status(progress_path)

    if args.compact or args.resume:
        compact_progress(progress_path, latest)
        latest = load_latest_status(progress_path)
        print(f"compacted progress: {len(latest)} sample_ids")

    jobs = discover_jobs(snapshots_dir, skip_snapshots=skip)
    if args.limit > 0:
        jobs = jobs[: args.limit]

    if args.resume:
        done = {sid for sid, rec in latest.items() if rec.get("status") == "archived"}
        jobs = [j for j in jobs if j.sample_id not in done]
        print(f"resume: {len(done)} archived, {len(jobs)} remaining")

    print(f"jobs: {len(jobs)} (skip snapshots: {sorted(skip)})")
    if args.dry_run:
        for j in jobs[:8]:
            print(f"  {j.sample_id} java_files={j.java_files}")
        return 0

    if not wait_for_cluster(
        args.base_url,
        min_backends=args.wait_backends,
        stats_url=HAPROXY_STATS,
        timeout_sec=args.wait_timeout,
    ):
        print("cluster not ready", file=sys.stderr)
        return 1

    lock = threading.Lock()
    ok_count = 0
    fail_count = 0
    total_jobs = len(jobs)

    def run_one(worker_id: int, job: Job) -> dict[str, Any]:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=7200.0) as client:
            return parse_and_archive(
                client,
                job,
                overwrite=args.overwrite,
                include_extensions=include_ext,
                worker_id=worker_id,
            )

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        pending: dict[Any, Job] = {}
        job_iter = iter(jobs)
        worker_ids = list(range(max(1, args.workers)))

        def submit_next(wid: int) -> bool:
            try:
                job = next(job_iter)
            except StopIteration:
                return False
            fut = pool.submit(run_one, wid, job)
            pending[fut] = job
            return True

        for wid in worker_ids:
            if not submit_next(wid):
                break

        while pending:
            done_set, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for fut in done_set:
                job = pending.pop(fut)
                try:
                    result = fut.result()
                except Exception as exc:
                    result = {"sample_id": job.sample_id, "status": "exception", "error": str(exc)}
                with lock:
                    append_progress(progress_path, result, latest)
                    if result.get("status") == "archived":
                        ok_count += 1
                    elif result.get("status") == "skipped_too_large":
                        pass
                    else:
                        fail_count += 1
                    n = ok_count + fail_count
                    extra = ""
                    if result.get("parse_elapsed_sec"):
                        extra = f" ({result['parse_elapsed_sec']}s)"
                    print(f"[{n}/{total_jobs}] {result.get('status')}: {job.sample_id}{extra}")
                wid = worker_ids[n % len(worker_ids)]
                submit_next(wid)

    compact_progress(progress_path, latest)
    elapsed = time.perf_counter() - t0
    archived_total = sum(1 for r in latest.values() if r.get("status") == "archived")
    print(f"done: run archived={ok_count} failed={fail_count} elapsed={elapsed:.0f}s")
    print(f"total archived (latest): {archived_total}")
    print(f"progress: {progress_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
