"""Affinity key and cleanup clearing in-memory CPG state."""

import json
import threading
from io import BytesIO
from unittest.mock import MagicMock, patch

from joern_server.proxy import JoernProxyHandler


def _setup_handler() -> JoernProxyHandler:
    JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
    JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
    JoernProxyHandler.query_cache = None
    JoernProxyHandler.query_timeout_sec = 5
    JoernProxyHandler.metrics = None

    h = JoernProxyHandler.__new__(JoernProxyHandler)
    h.path = "/cleanup"
    h.headers = {"Content-Type": "application/json", "Content-Length": "0"}
    h.wfile = BytesIO()
    h.server = MagicMock()
    h.client_address = ("127.0.0.1", 1)
    h.cpg_registry = None
    return h


def test_cleanup_clears_affinity_map(tmp_path) -> None:
    cpg_dir = tmp_path / "cpg-out" / "demo1"
    cpg_dir.mkdir(parents=True)
    (cpg_dir / "metadata.json").write_text("{}", encoding="utf-8")
    cpg_path = str(cpg_dir)

    JoernProxyHandler._affinity_cpg_path = {"demo1": cpg_path}
    JoernProxyHandler._active_affinity_key = "demo1"
    JoernProxyHandler._active_cpg_path = cpg_path

    handler = _setup_handler()
    JoernProxyHandler.cpg_out_dir = str(tmp_path / "cpg-out")
    body = json.dumps({"sample_id": "demo1"}).encode("utf-8")
    handler.headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }

    close_calls: list[str] = []

    def fake_post(*_a, **kwargs):
        close_calls.append(kwargs.get("json", {}).get("query", ""))
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"success": True}
        return m

    with patch.object(handler, "_read_body", return_value=body):
        with patch("joern_server.proxy.httpx.post", side_effect=fake_post):
            with patch.object(handler, "_send_json"):
                handler._handle_cleanup()

    assert "demo1" not in JoernProxyHandler._affinity_cpg_path
    assert JoernProxyHandler._active_cpg_path is None
    assert "close" in close_calls
