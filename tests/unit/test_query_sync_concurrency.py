"""Unit tests for S2-HF01: per-replica concurrency guard on /query-sync."""

import threading
import time
from http import HTTPStatus
from io import BytesIO
from unittest.mock import MagicMock, patch

import httpx
import pytest

from joern_server.proxy import JoernProxyHandler, LRUCache, main


def _make_handler(semaphore=None):
    """Return a JoernProxyHandler instance with class vars pre-configured."""
    JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
    JoernProxyHandler.repl_semaphore = semaphore or threading.Semaphore(1)
    JoernProxyHandler.query_cache = None
    JoernProxyHandler.parse_bin = "/bin/false"
    JoernProxyHandler.cpg_out_dir = "/tmp"
    JoernProxyHandler.parse_timeout_sec = 30
    JoernProxyHandler.query_timeout_sec = 5

    handler = JoernProxyHandler.__new__(JoernProxyHandler)
    handler.path = "/query-sync"
    handler.headers = {"X-Session-Id": "test-session", "X-Request-Id": "req-1",
                       "Content-Length": "0", "Content-Type": "application/json"}
    handler.wfile = BytesIO()
    handler.requestline = "POST /query-sync HTTP/1.1"
    handler.server = MagicMock()
    handler.client_address = ("127.0.0.1", 9999)
    return handler


class TestQuerySyncConcurrencyGuard:
    """Verify that the repl_semaphore serializes concurrent /query-sync calls."""

    def test_semaphore_serializes_two_concurrent_calls(self):
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

        sem = threading.Semaphore(1)
        results = []

        def run_handler(session_id):
            JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
            JoernProxyHandler.repl_semaphore = sem
            JoernProxyHandler.query_cache = None
            JoernProxyHandler.parse_bin = "/bin/false"
            JoernProxyHandler.cpg_out_dir = "/tmp"
            JoernProxyHandler.parse_timeout_sec = 30
            JoernProxyHandler.query_timeout_sec = 5

            handler = JoernProxyHandler.__new__(JoernProxyHandler)
            body = b'{"query": "cpg.method.name.l"}'
            handler.path = "/query-sync"
            handler.headers = {
                "X-Session-Id": session_id,
                "X-Request-Id": f"req-{session_id}",
                "Content-Length": str(len(body)),
                "Content-Type": "application/json",
            }
            handler.wfile = BytesIO()
            handler.requestline = "POST /query-sync HTTP/1.1"
            handler.server = MagicMock()
            handler.client_address = ("127.0.0.1", 9999)

            barrier.wait()  # both threads start together
            with patch("joern_server.proxy.httpx.post", side_effect=slow_post):
                with patch.object(handler, "_read_body", return_value=body):
                    with patch.object(handler, "_send_json"):
                        handler.do_POST()
            results.append(session_id)

        t1 = threading.Thread(target=run_handler, args=("session-A",))
        t2 = threading.Thread(target=run_handler, args=("session-B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Semaphore(1) guarantees no two "start" without an intervening "end".
        assert len(call_order) == 4
        for i in range(0, len(call_order) - 1, 2):
            assert call_order[i] == "start"
            assert call_order[i + 1] == "end"

    def test_timeout_returns_504(self):
        """httpx.TimeoutException must produce HTTP 504, not 502."""
        sent_status = []

        def fake_post(*args, **kwargs):
            raise httpx.TimeoutException("timed out")

        handler = _make_handler()
        body = b'{"query": "cpg.method.name.l"}'
        handler.headers = {
            "X-Session-Id": "sess",
            "X-Request-Id": "r1",
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }

        def capture_send_json(status, data):
            sent_status.append(status)

        with patch("joern_server.proxy.httpx.post", side_effect=fake_post):
            with patch.object(handler, "_read_body", return_value=body):
                with patch.object(handler, "_send_json", side_effect=capture_send_json):
                    handler.do_POST()

        assert sent_status == [HTTPStatus.GATEWAY_TIMEOUT]

    def test_other_exception_returns_502(self):
        """Non-timeout exceptions must still produce HTTP 502."""
        sent_status = []

        def fake_post(*args, **kwargs):
            raise httpx.NetworkError("connection refused")

        handler = _make_handler()
        body = b'{"query": "cpg.method.name.l"}'
        handler.headers = {
            "X-Session-Id": "sess",
            "X-Request-Id": "r1",
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }

        def capture_send_json(status, data):
            sent_status.append(status)

        with patch("joern_server.proxy.httpx.post", side_effect=fake_post):
            with patch.object(handler, "_read_body", return_value=body):
                with patch.object(handler, "_send_json", side_effect=capture_send_json):
                    handler.do_POST()

        assert sent_status == [HTTPStatus.BAD_GATEWAY]

    def test_error_log_contains_required_fields(self):
        """Error log event must include query_class, latency_ms, error_type."""
        log_calls = []

        def fake_post(*args, **kwargs):
            raise httpx.TimeoutException("timed out")

        handler = _make_handler()
        body = b'{"query": "cpg.method.name.l"}'
        handler.headers = {
            "X-Session-Id": "sess",
            "X-Request-Id": "r1",
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }

        def capture_log(event, **kwargs):
            log_calls.append((event, kwargs))

        with patch("joern_server.proxy.httpx.post", side_effect=fake_post):
            with patch.object(handler, "_read_body", return_value=body):
                with patch.object(handler, "_send_json"):
                    with patch.object(handler, "_log_event", side_effect=capture_log):
                        handler.do_POST()

        error_events = [(ev, kw) for ev, kw in log_calls if ev == "query_sync_error"]
        assert error_events, "No query_sync_error event logged"
        _, fields = error_events[0]
        assert "query_class" in fields
        assert "latency_ms" in fields
        assert "error_type" in fields
        assert fields["error_type"] == "TimeoutException"
