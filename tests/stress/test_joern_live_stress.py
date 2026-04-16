"""
Live Joern / NeuralAtlas proxy stress tests (default http://127.0.0.1:8080).

Verifies many concurrent logical sessions (distinct X-Session-Id) each issuing many
/query-sync calls without failures. On a single backend this is a load/smoke test;
with HAProxy stickiness it exercises per-session routing under concurrency.

Skip when the server is unreachable so CI without Joern still passes.

Run:
  pytest tests/stress/test_joern_live_stress.py -m integration -v

Tune:
  NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080
  NEURALATLAS_STRESS_SESSIONS=24
  NEURALATLAS_STRESS_QUERIES_PER_SESSION=30
  NEURALATLAS_STRESS_THREAD_WORKERS=32
  NEURALATLAS_STRESS_EXECUTOR_THREADS=8
  NEURALATLAS_STRESS_EXECUTOR_ROUNDS=15   (parse/import/cleanup per round)
  NEURALATLAS_STRESS_PARALLEL_CPG=1       required for parallel threads (multi-replica + sticky);
                                          default runs sessions sequentially so one shared Joern VM works
"""

from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
import pytest

from joern_server.client import JoernHTTPQueryExecutor
from joern_server.proxy import _safe_sample_id

LIVE_URL = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")

_VERSION_RE = re.compile(r'=\s*"([^"]+)"\s*$', re.MULTILINE)


def _joern_reported_version(stdout: str) -> str | None:
    """Joern pretty-prints `val resN: String = "x.y.z"`; binder name changes each query."""
    m = _VERSION_RE.search(stdout or "")
    return m.group(1) if m else None


def _live_joern_reachable() -> bool:
    try:
        r = httpx.get(f"{LIVE_URL}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def require_live_joern() -> str:
    if not _live_joern_reachable():
        pytest.skip(f"Joern not reachable at {LIVE_URL}/health (set NEURALATLAS_LIVE_JOERN_URL)")
    return LIVE_URL


@pytest.mark.integration
def test_live_sequential_same_session_id_many_query_sync(require_live_joern: str) -> None:
    """Same X-Session-Id, many sequential /query-sync - baseline before concurrency."""
    url = require_live_joern
    sid = "live-sticky-sequential-1"
    n = int(os.environ.get("NEURALATLAS_STRESS_SEQUENTIAL", "50"))
    with httpx.Client(timeout=120.0) as client:
        for _ in range(n):
            r = client.post(
                f"{url}/query-sync",
                json={"query": "version"},
                headers={"Content-Type": "application/json", "X-Session-Id": sid},
            )
            r.raise_for_status()
            body = r.json()
            assert body.get("success") is True, body


@pytest.mark.integration
def test_live_stress_concurrent_sessions(require_live_joern: str) -> None:
    """
    Many threads x many queries, unique X-Session-Id per thread.
    Stresses VIP + Joern under parallel logical sessions.
    """
    url = require_live_joern
    num_sessions = int(os.environ.get("NEURALATLAS_STRESS_SESSIONS", "16"))
    per_session = int(os.environ.get("NEURALATLAS_STRESS_QUERIES_PER_SESSION", "20"))
    max_workers = int(os.environ.get("NEURALATLAS_STRESS_THREAD_WORKERS", "32"))

    def run_session(idx: int) -> tuple[int, int]:
        sid = f"live-stress-session-{idx}"
        ok = 0
        with httpx.Client(timeout=120.0) as client:
            for _ in range(per_session):
                r = client.post(
                    f"{url}/query-sync",
                    json={"query": "version"},
                    headers={"Content-Type": "application/json", "X-Session-Id": sid},
                )
                r.raise_for_status()
                body = r.json()
                if body.get("success") is True:
                    ok += 1
        return idx, ok

    with ThreadPoolExecutor(max_workers=min(max_workers, num_sessions)) as pool:
        futures = [pool.submit(run_session, i) for i in range(num_sessions)]
        results: list[tuple[int, int]] = []
        for fut in as_completed(futures):
            results.append(fut.result())

    for idx, ok in results:
        assert ok == per_session, f"session {idx}: got {ok}/{per_session} successes"

    assert len(results) == num_sessions


def _post_cleanup(cli: httpx.Client, base: str, sample_id: str, session_id: str) -> None:
    r = cli.post(
        f"{base}/cleanup",
        json={"sample_id": sample_id},
        headers={"Content-Type": "application/json", "X-Session-Id": session_id},
        timeout=180.0,
    )
    r.raise_for_status()
    body = r.json()
    assert body.get("ok") is True, body


@pytest.mark.integration
def test_live_executor_session_id_under_stress(require_live_joern: str) -> None:
    """
    Each thread: stable X-Session-Id and JoernHTTPQueryExecutor. Every round:

    1. POST /parse - unique C file with a unique function name (writes under /workspace/cpg-out/...)
    2. importCpg(that path) in /query-sync - loads the graph for `cpg.*` on this Joern stack
    3. cpg.method.name.l - must list this thread's function; must not list another thread's
       function at the same round index (detects routing / wrong interpreter)
    4. POST /cleanup - delete server CPG dir before the next round

    Requires /parse artifacts on a filesystem the Joern that handles /query-sync can read.

    **Concurrency:** Joern keeps one global ``cpg`` per interpreter process. Parallel threads
    against **one** backend race on that graph (you will see another thread's ``uniqfn_*`` in the
    method list - that is a failed test). Set ``NEURALATLAS_STRESS_PARALLEL_CPG=1`` only when
    each ``X-Session-Id`` is pinned to its **own** replica so sessions do not share an interpreter.
    Otherwise sessions run **one after another** (still 8x rounds work, full stack coverage).
    """
    base_url = require_live_joern
    rounds = int(os.environ.get("NEURALATLAS_STRESS_EXECUTOR_ROUNDS", "15"))
    threads = int(os.environ.get("NEURALATLAS_STRESS_EXECUTOR_THREADS", "8"))
    max_workers = int(os.environ.get("NEURALATLAS_STRESS_THREAD_WORKERS", str(threads)))
    parallel_cpg = os.environ.get("NEURALATLAS_STRESS_PARALLEL_CPG", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    run_tag = uuid.uuid4().hex[:8]

    def worker(tidx: int) -> None:
        session_id = f"executor-cpg-stress-{run_tag}-t{tidx}"
        with httpx.Client(timeout=180.0) as hc:
            ex = JoernHTTPQueryExecutor(
                base_url,
                http_client=hc,
                session_id=session_id,
                reuse_base=True,
                retries=1,
                timeout=180.0,
            )
            for rnd in range(rounds):
                fn = f"uniqfn_t{tidx}_r{rnd}"
                sample_id = f"na-{run_tag}-t{tidx}-r{rnd}"
                source = f"void {fn}(void) {{}}\n"
                pr = ex.parse_source(
                    sample_id=sample_id,
                    source_code=source,
                    language="C",
                    filename="snippet.c",
                    overwrite=True,
                )
                assert pr.get("ok") is True, pr
                cpg_path = pr.get("cpg_path")
                assert isinstance(cpg_path, str) and cpg_path, pr
                safe = _safe_sample_id(sample_id)
                assert safe in cpg_path or cpg_path.rstrip("/").endswith(safe), (cpg_path, safe)

                # Use importCpg (Joern console) so `cpg.*` traversals work on this stack.
                # load_cpg from server_tools.sc assigns a different workspace path than importCpg here.
                imported = ex.execute(f'importCpg("{cpg_path}")')
                assert imported.get("success") is True, imported

                names = ex.execute("cpg.method.name.l")
                assert names.get("success") is True, names
                out = str(names.get("stdout") or "")
                assert fn in out, (
                    f"thread {tidx} round {rnd}: expected method {fn!r} in stdout; got {out!r}"
                )
                for other in range(threads):
                    if other == tidx:
                        continue
                    alien = f"uniqfn_t{other}_r{rnd}"
                    assert alien not in out, (
                        f"cross-talk thread {tidx} round {rnd}: found other thread's {alien!r} in {out!r}"
                    )

                _post_cleanup(hc, base_url, sample_id, session_id)

    if parallel_cpg:
        with ThreadPoolExecutor(max_workers=min(max_workers, threads)) as pool:
            list(pool.map(worker, range(threads)))
    else:
        for tidx in range(threads):
            worker(tidx)


@pytest.mark.integration
def test_live_version_stdout_stable_per_session(require_live_joern: str) -> None:
    """Within one session id, repeated version queries report the same Joern version string."""
    url = require_live_joern
    sid = "live-version-stable-check"
    versions: list[str] = []
    with httpx.Client(timeout=60.0) as client:
        for _ in range(12):
            r = client.post(
                f"{url}/query-sync",
                json={"query": "version"},
                headers={"Content-Type": "application/json", "X-Session-Id": sid},
            )
            r.raise_for_status()
            body: dict[str, Any] = r.json()
            assert body.get("success") is True
            raw = str(body.get("stdout", ""))
            v = _joern_reported_version(raw)
            assert v is not None, f"could not parse version from: {raw!r}"
            versions.append(v)

    assert len(set(versions)) == 1, f"version string varied: {set(versions)!r}"
