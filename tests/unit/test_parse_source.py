"""
S5-001: Unit tests for parse_source MCP tool and proxy_post helper.

Covers:
  1. proxy_post success / failure / connection error
  2. parse_source happy-path (cache hit, fresh parse)
  3. parse_source proxy connection failure
  4. parse_source auto-loads CPG after successful parse
  5. parse_source with language override
  6. parse_source overwrite=False

Run with:
    pytest tests/unit/test_parse_source.py -v
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

MJOERN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp-joern")
)
if MJOERN_DIR not in sys.path:
    sys.path.insert(0, MJOERN_DIR)


@pytest.fixture(autouse=True)
def _setup_server_env(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8080")


class TestProxyPost:
    def test_proxy_post_success(self):
        import server

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "cache_hit": True}
        mock_resp.raise_for_status = MagicMock()

        with patch("server.requests.post", return_value=mock_resp) as mock_post:
            result = server.proxy_post("/parse", {"source_code": "int main(){}", "sample_id": "test1"})

        assert result == {"ok": True, "cache_hit": True}
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert call_url.endswith("/parse")

    def test_proxy_post_connection_error(self):
        import server
        import requests as req_lib

        with patch("server.requests.post", side_effect=req_lib.exceptions.ConnectionError("refused")):
            result = server.proxy_post("/parse", {"source_code": "x", "sample_id": "y"})

        assert result is None

    def test_proxy_post_json_decode_error(self):
        import server

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("err", "", 0)

        with patch("server.requests.post", return_value=mock_resp):
            result = server.proxy_post("/parse", {"source_code": "x", "sample_id": "y"})

        assert result is None

    def test_proxy_post_includes_session_id(self):
        import server

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        with patch("server.requests.post", return_value=mock_resp) as mock_post:
            server.proxy_post("/parse", {"source_code": "x", "sample_id": "y"})

        call_kwargs = mock_post.call_args[1]
        headers_arg = call_kwargs["headers"]
        assert "X-Session-Id" in headers_arg


class TestParseSource:
    def _make_parse_response(self, cache_hit=False, cpg_path="/workspace/cpg-out/test1"):
        return {
            "ok": True,
            "sample_id": "test1",
            "cpg_path": cpg_path,
            "cache_hit": cache_hit,
            "source_hash": "abc123",
        }

    def test_parse_source_cache_hit(self):
        import server

        parse_response = self._make_parse_response(cache_hit=True)

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value='val res0: String = "test"') as mock_joern:

            result_str = server.parse_source(source_code="int main(){}", sample_id="test1")
            result = json.loads(result_str)

        assert result["ok"] is True
        assert result["cache_hit"] is True
        assert result["cpg_path"] == "/workspace/cpg-out/test1"
        mock_proxy.assert_called_once()
        call_args = mock_proxy.call_args
        assert call_args[0][0] == "/parse"
        payload = call_args[0][1]
        assert payload["source_code"] == "int main(){}"
        assert payload["sample_id"] == "test1"
        assert payload["overwrite"] is True

    def test_parse_source_fresh_parse(self):
        import server

        parse_response = self._make_parse_response(cache_hit=False)

        with patch("server.proxy_post", return_value=parse_response), \
             patch("server.joern_remote", return_value='val res0: String = "test"') as mock_joern:

            result_str = server.parse_source(source_code="int main(){}", sample_id="test1")
            result = json.loads(result_str)

        assert result["ok"] is True
        assert result["cache_hit"] is False

    def test_parse_source_auto_loads_cpg(self):
        import server

        parse_response = self._make_parse_response(cpg_path="/workspace/cpg-out/test1")

        with patch("server.proxy_post", return_value=parse_response), \
             patch("server.joern_remote", return_value='Some("/workspace/cpg-out/test1")') as mock_joern:

            server.parse_source(source_code="int main(){}", sample_id="test1")

        calls = [c[0][0] for c in mock_joern.call_args_list]
        assert 'importCpg("/workspace/cpg-out/test1")' in calls
        assert 'load_cpg("/workspace/cpg-out/test1")' in calls

    def test_parse_source_updates_last_cpg_filepath(self):
        import server

        parse_response = self._make_parse_response(cpg_path="/workspace/cpg-out/my_sample")

        with patch("server.proxy_post", return_value=parse_response), \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(source_code="int main(){}", sample_id="my_sample")

        assert server._LAST_CPG_FILEPATH == "/workspace/cpg-out/my_sample"

    def test_parse_source_with_language(self):
        import server

        parse_response = self._make_parse_response()

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(source_code="def foo(): pass", sample_id="py1", language="python")

        payload = mock_proxy.call_args[0][1]
        assert payload["language"] == "python"

    def test_parse_source_no_language_default(self):
        import server

        parse_response = self._make_parse_response()

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(source_code="int main(){}", sample_id="c1", language="")

        payload = mock_proxy.call_args[0][1]
        assert "language" not in payload

    def test_parse_source_overwrite_false(self):
        import server

        parse_response = self._make_parse_response()

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(source_code="int main(){}", sample_id="c1", overwrite=False)

        payload = mock_proxy.call_args[0][1]
        assert payload["overwrite"] is False

    def test_parse_source_proxy_failure(self):
        import server

        with patch("server.proxy_post", return_value=None):
            result_str = server.parse_source(source_code="x", sample_id="y")
            result = json.loads(result_str)

        assert result["ok"] is False
        assert "error" in result

    def test_parse_source_proxy_returns_error(self):
        import server

        error_response = {"ok": False, "error": "missing required field: source_code", "code": "bad_request"}

        with patch("server.proxy_post", return_value=error_response):
            result_str = server.parse_source(source_code="", sample_id="y")
            result = json.loads(result_str)

        assert result["ok"] is False
        assert "error" in result

    def test_parse_source_no_cpg_path_still_succeeds(self):
        import server

        parse_response = {"ok": True, "sample_id": "test1", "cpg_path": "", "cache_hit": False, "source_hash": "abc"}

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote") as mock_joern:

            result_str = server.parse_source(source_code="int main(){}", sample_id="test1")
            result = json.loads(result_str)

        assert result["ok"] is True
        assert result["cpg_path"] == ""
        mock_joern.assert_not_called()

    def test_parse_source_routes_through_proxy_endpoint(self):
        import server

        parse_response = self._make_parse_response()

        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(source_code="int main(){}", sample_id="test1")

        assert mock_proxy.call_args[0][0] == "/parse"

    def test_parse_source_double_parse_cache_hit(self):
        """PB-038 regression: second parse with same source_code returns cache_hit=True."""
        import server

        parse_r1 = self._make_parse_response(cache_hit=False)
        parse_r2 = self._make_parse_response(cache_hit=True)

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return parse_r1 if call_count == 1 else parse_r2

        with patch("server.proxy_post", side_effect=side_effect) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            # First parse: fresh
            r1 = json.loads(server.parse_source(source_code="int main(){}", sample_id="test1"))
            assert r1["cache_hit"] is False

            # Second parse: cache hit
            r2 = json.loads(server.parse_source(source_code="int main(){}", sample_id="test1"))
            assert r2["cache_hit"] is True

        assert mock_proxy.call_count == 2
        for call in mock_proxy.call_args_list:
            assert call[0][0] == "/parse"

    def test_parse_source_proxy_post_passes_full_payload(self):
        """PB-038: parse_source passes source_code, sample_id, overwrite, language to proxy."""
        import server

        parse_response = self._make_parse_response()
        with patch("server.proxy_post", return_value=parse_response) as mock_proxy, \
             patch("server.joern_remote", return_value="true"):

            server.parse_source(
                source_code="def foo():\n    pass",
                sample_id="py-sample",
                language="python",
                overwrite=False,
            )

        payload = mock_proxy.call_args[0][1]
        assert payload["source_code"] == "def foo():\n    pass"
        assert payload["sample_id"] == "py-sample"
        assert payload["language"] == "python"
        assert payload["overwrite"] is False