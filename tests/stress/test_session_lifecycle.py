"""
250-session lifecycle endurance (live stack required).

Per session (batched concurrency):
  parse → importCpg → N queries → cleanup

Run after foundation patches are deployed:
  NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080 \\
  NEURALATLAS_STRESS_LIFECYCLE_SESSIONS=250 \\
  NEURALATLAS_STRESS_LIFECYCLE_BATCH=8 \\
  NEURALATLAS_STRESS_LIFECYCLE_QUERIES=10 \\
  pytest tests/stress/test_session_lifecycle.py -m stress -v
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest

from joern_server.client import JoernHTTPQueryExecutor

LIVE_URL = os.environ.get("NEURALATLAS_LIVE_JOERN_URL", "http://127.0.0.1:8080").rstrip("/")
TOTAL_SESSIONS = int(os.environ.get("NEURALATLAS_STRESS_LIFECYCLE_SESSIONS", "250"))
BATCH_SIZE = int(os.environ.get("NEURALATLAS_STRESS_LIFECYCLE_BATCH", "8"))
QUERIES_PER_SESSION = int(os.environ.get("NEURALATLAS_STRESS_LIFECYCLE_QUERIES", "10"))
RUN_TAG = os.environ.get("NEURALATLAS_STRESS_RUN_TAG", uuid.uuid4().hex[:10])


def _live_ok() -> bool:
    try:
        r = httpx.get(f"{LIVE_URL}/health", timeout=5.0)
        if r.status_code != 200:
            return False
        body = r.json()
        return body.get("joern_ok", body.get("ok")) is not False
    except Exception:
        return False


@pytest.fixture(scope="module")
def require_live() -> str:
    if not _live_ok():
        pytest.skip(f"Joern VIP unhealthy at {LIVE_URL}/health")
    return LIVE_URL


def _run_one_session(base_url: str, idx: int) -> None:
    sample_id = f"lc-{RUN_TAG}-s{idx}"
    session_id = f"agent-lifecycle-{RUN_TAG}-{idx}"
    fn = f"lc_fn_{idx}"

    with httpx.Client(timeout=180.0) as hc:
        ex = JoernHTTPQueryExecutor(
            base_url,
            http_client=hc,
            session_id=session_id,
            affinity_key=sample_id,
            reuse_base=True,
            retries=1,
            timeout=180.0,
        )
        pr = ex.parse_source(
            sample_id=sample_id,
            source_code=f"void {fn}(void) {{}}\n",
            language="C",
            filename="snippet.c",
            overwrite=True,
        )
        assert pr.get("ok") is True, (idx, pr)
        cpg_path = pr.get("cpg_path")
        assert isinstance(cpg_path, str) and cpg_path, (idx, pr)

        imp = ex.execute(f'importCpg("{cpg_path}")')
        assert imp.get("success") is True, (idx, imp)

        for q in range(QUERIES_PER_SESSION):
            if q % 3 == 0:
                query = "version"
            elif q % 3 == 1:
                query = "cpg.method.name.l"
            else:
                query = f'cpg.method.name("{fn}").l'
            res = ex.execute(query)
            assert res.get("success") is True, (idx, q, query, res)
            if "method.name" in query and query != "version":
                assert fn in str(res.get("stdout") or ""), (idx, q, res)

        clean = ex.cleanup(sample_id)
        assert clean.get("ok") is True, (idx, clean)


@pytest.mark.stress
@pytest.mark.integration
def test_lifecycle_sessions_batched(require_live: str) -> None:
    """Run TOTAL_SESSIONS lifecycles with at most BATCH_SIZE in parallel."""
    base = require_live
    failures: list[str] = []
    indices = list(range(TOTAL_SESSIONS))

    for batch_start in range(0, len(indices), BATCH_SIZE):
        batch = indices[batch_start : batch_start + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futs = {pool.submit(_run_one_session, base, i): i for i in batch}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    fut.result()
                except Exception as exc:
                    failures.append(f"session {i}: {exc}")

    assert not failures, "failures:\n" + "\n".join(failures[:20]) + (
        f"\n... and {len(failures) - 20} more" if len(failures) > 20 else ""
    )
