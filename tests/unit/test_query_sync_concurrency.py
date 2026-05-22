"""Unit tests for S2-HF01: per-replica concurrency guard on /query-sync."""

import json
import threading
import time
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx

from tests.helpers.app import make_test_client, make_test_state


def _headers(affinity_key: str, *, request_id: str | None = None) -> dict[str, str]:
    return {
        "X-Affinity-Key": affinity_key,
        "X-Session-Id": f"sess-{affinity_key}",
        "X-Request-Id": request_id or f"req-{affinity_key}",
    }


class TestQuerySyncConcurrencyGuard:
    """Verify that the repl_semaphore serializes concurrent /query-sync calls."""

    def test_semaphore_serializes_two_concurrent_calls(self, tmp_path):
        """Two threads must not overlap inside httpx.post; second waits for first."""
        call_order = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def slow_post(*args, **kwargs):
            with lock:
                call_order.append("start")
            time.sleep(0.05)
            with lock:
                call_order.append("end")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"stdout": "result", "success": True}
            return mock_resp

        state = make_test_state(tmp_path)
        client = make_test_client(state=state)
        results = []

        def run_query(session_id):
            barrier.wait()
            with patch("joern_server.upstream.joern.post_query_sync", side_effect=slow_post):
                resp = client.post(
                    "/query-sync",
                    json={"query": "cpg.method.name.l"},
                    headers=_headers(session_id),
                )
            results.append((session_id, resp.status_code))

        t1 = threading.Thread(target=run_query, args=("session-A",))
        t2 = threading.Thread(target=run_query, args=("session-B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(call_order) == 4
        for i in range(0, len(call_order) - 1, 2):
            assert call_order[i] == "start"
            assert call_order[i + 1] == "end"
        assert len(results) == 2

    def test_timeout_returns_504(self, tmp_path):
        """httpx.TimeoutException must produce HTTP 504, not 502."""

        def fake_post(*args, **kwargs):
            raise httpx.TimeoutException("timed out")

        client = make_test_client(tmp_path=tmp_path)
        with patch("joern_server.upstream.joern.post_query_sync", side_effect=fake_post):
            resp = client.post(
                "/query-sync",
                json={"query": "cpg.method.name.l"},
                headers=_headers("sess", request_id="r1"),
            )

        assert resp.status_code == HTTPStatus.GATEWAY_TIMEOUT

    def test_other_exception_returns_502(self, tmp_path):
        """Non-timeout exceptions must still produce HTTP 502."""

        def fake_post(*args, **kwargs):
            raise httpx.NetworkError("connection refused")

        client = make_test_client(tmp_path=tmp_path)
        with patch("joern_server.upstream.joern.post_query_sync", side_effect=fake_post):
            resp = client.post(
                "/query-sync",
                json={"query": "cpg.method.name.l"},
                headers=_headers("sess", request_id="r1"),
            )

        assert resp.status_code == HTTPStatus.BAD_GATEWAY

    def test_error_log_contains_required_fields(self, tmp_path):
        """Error log event must include query_class, latency_ms, error_type."""
        log_calls = []

        def fake_post(*args, **kwargs):
            raise httpx.TimeoutException("timed out")

        def capture_log(request, event, *, affinity_key, req_id, **kwargs):
            log_calls.append((event, kwargs))

        client = make_test_client(tmp_path=tmp_path)
        with patch("joern_server.upstream.joern.post_query_sync", side_effect=fake_post):
            with patch("joern_server.api.routers.query._log_event", side_effect=capture_log):
                client.post(
                    "/query-sync",
                    json={"query": "cpg.method.name.l"},
                    headers=_headers("sess", request_id="r1"),
                )

        error_events = [(ev, kw) for ev, kw in log_calls if ev == "query_sync_error"]
        assert error_events, "No query_sync_error event logged"
        _, fields = error_events[0]
        assert "query_class" in fields
        assert "latency_ms" in fields
        assert "error_type" in fields
        assert fields["error_type"] == "TimeoutException"


class TestSessionCPGIsolation:
    def _run_query(self, client, state, affinity_key: str, query: str, fake_post):
        with patch("joern_server.upstream.joern.post_query_sync", side_effect=fake_post):
            resp = client.post(
                "/query-sync",
                json={"query": query},
                headers=_headers(affinity_key),
            )
        return resp.status_code, resp.json()

    @staticmethod
    def _mk_resp(status_code: int, body: dict):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = body
        return resp

    @staticmethod
    def _reset_affinity(state):
        state.affinity_cpg_path.clear()
        state.active_affinity_key = None
        state.active_cpg_path = None

    def test_session_without_import_cannot_see_other_session_cpg(self, tmp_path):
        state = make_test_state(tmp_path)
        self._reset_affinity(state)
        client = make_test_client(state=state)

        active_cpg = None
        methods_by_cpg = {
            "/tmp/a.cpg": ["only_a"],
            "/tmp/b.cpg": ["only_b"],
        }

        def fake_post(*args, **kwargs):
            nonlocal active_cpg
            raw_query = kwargs.get("query")
            if raw_query is None:
                payload = json.loads((kwargs.get("content") or b"{}").decode("utf-8"))
                raw_query = str(payload.get("query", ""))
            if raw_query.startswith('importCpg("'):
                active_cpg = raw_query.split('importCpg("', 1)[1].rsplit('")', 1)[0]
                return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})
            if raw_query == "close":
                active_cpg = None
                return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})
            if raw_query == "cpg.method.name.l":
                methods = methods_by_cpg.get(active_cpg, [])
                return self._mk_resp(200, {"success": True, "stdout": f"List({', '.join(methods)})", "stderr": ""})
            return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})

        st_a_import, _ = self._run_query(client, state, "cpg-a", 'importCpg("/tmp/a.cpg")', fake_post)
        assert st_a_import == HTTPStatus.OK

        st_a_q, body_a_q = self._run_query(client, state, "cpg-a", "cpg.method.name.l", fake_post)
        assert st_a_q == HTTPStatus.OK
        assert "only_a" in body_a_q.get("stdout", "")

        st_b_q, body_b_q = self._run_query(client, state, "cpg-b", "cpg.method.name.l", fake_post)
        assert st_b_q == HTTPStatus.OK
        assert "only_a" not in body_b_q.get("stdout", "")

    def test_session_a_result_stable_after_session_b_import(self, tmp_path):
        state = make_test_state(tmp_path)
        self._reset_affinity(state)
        client = make_test_client(state=state)

        active_cpg = None
        imports = []
        methods_by_cpg = {
            "/tmp/a.cpg": ["only_a"],
            "/tmp/b.cpg": ["only_b"],
        }

        def fake_post(*args, **kwargs):
            nonlocal active_cpg
            raw_query = kwargs.get("query")
            if raw_query is None:
                payload = json.loads((kwargs.get("content") or b"{}").decode("utf-8"))
                raw_query = str(payload.get("query", ""))
            if raw_query.startswith('importCpg("'):
                active_cpg = raw_query.split('importCpg("', 1)[1].rsplit('")', 1)[0]
                imports.append(active_cpg)
                return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})
            if raw_query == "close":
                active_cpg = None
                return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})
            if raw_query == "cpg.method.name.l":
                methods = methods_by_cpg.get(active_cpg, [])
                return self._mk_resp(200, {"success": True, "stdout": f"List({', '.join(methods)})", "stderr": ""})
            return self._mk_resp(200, {"success": True, "stdout": "", "stderr": ""})

        assert self._run_query(client, state, "cpg-a", 'importCpg("/tmp/a.cpg")', fake_post)[0] == HTTPStatus.OK
        assert self._run_query(client, state, "cpg-b", 'importCpg("/tmp/b.cpg")', fake_post)[0] == HTTPStatus.OK

        st_a_q, body_a_q = self._run_query(client, state, "cpg-a", "cpg.method.name.l", fake_post)
        assert st_a_q == HTTPStatus.OK
        assert "only_a" in body_a_q.get("stdout", "")
        assert "only_b" not in body_a_q.get("stdout", "")
        assert imports == ["/tmp/a.cpg", "/tmp/b.cpg", "/tmp/a.cpg"]
