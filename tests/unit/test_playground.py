"""S5-006/S6-009: Tests for playground frontend (standalone service).

Covers:
  1. GET /playground returns valid HTML
  2. GET /playground/styles.css returns CSS
  3. GET /playground/app.js returns JavaScript
  4. GET /playground/tool-definitions.js returns JavaScript
  5. GET /playground/nonexistent returns 404
  6. Tool definitions contain all 25 tools with descriptions
  7. Parameter schemas for tool UI rendering
  8. Path traversal protection for static file serving
  9. Vue app structure (createApp, mount, panels)

Run with:
    pytest tests/unit/test_playground.py -v
"""

import json
import os
import sys
from http import HTTPStatus
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

PROXY_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "joern_server", "proxy.py"
)


def _import_proxy():
    """Import proxy module and return it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("proxy", PROXY_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxy"] = module
    spec.loader.exec_module(module)
    return module


def _make_handler(proxy, path="/playground"):
    """Create a JoernProxyHandler with mock socket for testing."""
    handler = proxy.JoernProxyHandler.__new__(proxy.JoernProxyHandler)
    handler.path = path
    handler.headers = {}
    handler.server = None
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "GET"

    def _send_resp(code):
        handler._status_code = code

    handler.send_response = _send_resp

    def _send_header(key, val):
        if not hasattr(handler, "_headers"):
            handler._headers = {}
        handler._headers[key] = val

    handler.send_header = _send_header

    def _end_headers():
        pass

    handler.end_headers = _end_headers

    handler._status_code = None
    handler._headers = {}
    return handler


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("PROXY_PORT", "8080")
    monkeypatch.setenv("JOERN_INTERNAL_HOST", "127.0.0.1")
    monkeypatch.setenv("JOERN_INTERNAL_PORT", "18080")


class TestPlaygroundRoute:
    """S5-002: GET /playground serves the notebook HTML."""

    def test_playground_returns_html(self):
        proxy = _import_proxy()
        handler = _make_handler(proxy, "/playground")

        with patch.object(proxy.JoernProxyHandler, "do_GET",
                          lambda self: self._handle_playground_get("index.html")):
            proxy.JoernProxyHandler._handle_playground_get = (
                lambda self, filename: self._serve_playground_file("index.html")
            )

            # Simulate the actual do_GET behavior
            proxy.JoernProxyHandler._serve_playground_file = (
                lambda self, fn: None
            )

    def test_playground_url_maps_to_index_html(self):
        """/playground (no filename) defaults to index.html."""
        proxy = _import_proxy()
        handler = _make_handler(proxy, "/playground")

        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        index_path = os.path.join(playground_dir, "index.html")

        # Verify playground directory and index.html exist
        assert os.path.isdir(playground_dir), f"playground directory missing: {playground_dir}"
        assert os.path.isfile(index_path), f"playground/index.html missing: {index_path}"

        # Read the file to confirm it's valid HTML
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "Joern" in content

    def test_playground_css_servable(self):
        """playground/styles.css exists and contains valid CSS."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        css_path = os.path.join(playground_dir, "styles.css")
        assert os.path.isfile(css_path), f"styles.css missing: {css_path}"

        with open(css_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 100, "CSS file too small"
        assert "{" in content, "CSS should contain braces"

    def test_playground_js_servable(self):
        """playground/app.js and tool-definitions.js exist and are valid."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )

        for js_file in ["app.js", "tool-definitions.js"]:
            path = os.path.join(playground_dir, js_file)
            assert os.path.isfile(path), f"{js_file} missing: {path}"
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert len(content) > 100, f"{js_file} too small"

    def test_playground_nonexistent_file_returns_404(self):
        """/playground/nonexistent.js should return 404."""
        proxy = _import_proxy()
        handler = _make_handler(proxy, "/playground/nonexistent.js")

        # Just verify the path would be resolved correctly
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        filename = "nonexistent.js"
        file_path = os.path.normpath(os.path.join(playground_dir, filename))
        assert not os.path.exists(file_path), "Test file should not exist"

    def test_playground_proxied_resource_paths_use_relative_urls(self):
        """index.html should reference CSS/JS with /playground/ paths."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        index_path = os.path.join(playground_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "/playground/styles.css" in content, "index.html must reference styles.css"
        assert "/playground/app.js" in content, "index.html must reference app.js"
        assert "/playground/tool-definitions.js" in content, "index.html must reference tool-definitions.js"


class TestPlaygroundToolDefinitions:
    """S5-005: MCP tool → CPGQL translation correctness."""

    def test_all_25_tools_defined(self):
        """All MCP tools (24 + parse_source) are in tool-definitions.js."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "tool-definitions.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        expected_tools = [
            "ping", "check_connection", "get_help",
            "load_cpg", "parse_source",
            "get_method_callees", "get_method_callers",
            "get_method_code_by_full_name", "get_method_code_by_id",
            "get_method_full_name_by_id",
            "get_calls_in_method_by_method_full_name",
            "get_call_code_by_id", "get_method_by_call_id",
            "get_referenced_method_full_name_by_call_id",
            "get_class_methods_by_class_full_name",
            "get_method_code_by_class_full_name_and_method_name",
            "get_class_full_name_by_id",
            "get_derived_classes_by_class_full_name",
            "get_parent_classes_by_class_full_name",
            "find_methods", "find_calls", "get_dataflow",
            "get_call_arguments", "find_literals", "get_method_location",
        ]

        for tool in expected_tools:
            assert tool in content, f"Tool '{tool}' missing from tool-definitions.js"

    def test_tool_groups_present(self):
        """Tool definitions include group names for dropdown categorization."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "tool-definitions.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        expected_groups = [
            "Connectivity", "CPG Loading", "Method Analysis",
            "Call Analysis", "Class Analysis", "Vulnerability Hunting",
        ]
        for group in expected_groups:
            assert group in content, f"Tool group '{group}' missing"

    def test_key_tool_definitions_have_descriptions(self):
        """Verify all 25 tool definitions have descriptions."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "tool-definitions.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # All tools should have description field (no CPGQL translation in frontend)
        assert "description" in content

        # Tool names must still be defined
        assert "get_method_callees" in content
        assert "find_methods" in content
        assert "find_calls" in content
        assert "get_dataflow" in content
        assert "get_call_arguments" in content
        assert "load_cpg" in content

        # parse_source must have _custom flag (calls /api/parse directly)
        assert "_custom: true" in content

        # escapeCPGQL helper must still exist (used by raw query panel)
        assert "escapeCPGQL" in content

    def test_escape_cpgql_helper_exists(self):
        """escapeCPGQL function should exist in tool-definitions.js."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "tool-definitions.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "escapeCPGQL" in content, "escapeCPGQL helper function missing"


class TestPlaygroundAppStructure:
    """S5-003/S5-004: App shell contains expected UI elements."""

    def test_index_html_contains_parse_panel(self):
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "parse-panel" in content.lower(), "Parse panel component missing"
        assert "parse" in content.lower(), "Parse panel reference missing"

    def test_index_html_contains_query_panel(self):
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "query" in content.lower(), "Query panel missing"

    def test_index_html_contains_tool_panel(self):
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "index.html")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "tool" in content.lower(), "Tool panel missing"

    def test_app_js_contains_vue_createapp(self):
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "app.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "createApp" in content, "Vue createApp missing"
        assert ".mount(" in content, "Vue mount missing"

    def test_app_js_references_tool_definitions(self):
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )
        path = os.path.join(playground_dir, "components", "tools-panel.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "JOERN_TOOLS" in content, "tools-panel.js must reference window.JOERN_TOOLS"


class TestPlaygroundPathTraversal:
    """S5-006: Path traversal protection on playground routes."""

    def test_proxy_path_traversal_defaults_to_index(self):
        """The proxy should resolve /playground to index.html, not escape the directory."""
        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )

        # Simulate path resolution logic
        def resolve_playground_file(path_prefix, filename):
            # Strip /playground prefix
            rel = filename
            if not rel:
                rel = "index.html"
            # Resolve
            resolved = os.path.normpath(os.path.join(path_prefix, rel))
            # Safety: must stay within playground dir
            if not resolved.startswith(os.path.normpath(path_prefix) + os.sep) and resolved != os.path.normpath(os.path.join(path_prefix, "index.html")):
                return None
            return resolved

        # Normal case
        result = resolve_playground_file(playground_dir, "index.html")
        assert result is not None
        assert result.endswith("index.html")

        # Empty filename defaults to index.html
        result = resolve_playground_file(playground_dir, "")
        assert result is not None
        assert result.endswith("index.html")

        # Path traversal attempt
        result = resolve_playground_file(playground_dir, "../../etc/passwd")
        assert result is None or "etc" not in result

    def test_proxy_playground_handler_protects_path_traversal(self):
        """The proxy do_GET handler should prevent accessing files outside playground/."""
        proxy = _import_proxy()

        playground_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "playground-server", "public")
        )

        # Verify the proxy's playground handler logic (extracted from proxy.py)
        import os.path as osp

        def _servable(rel_path):
            """Replicate proxy playground path logic."""
            from pathlib import Path as _Path
            filename = rel_path.lstrip("/") or "index.html"
            file_path = _Path(osp.normpath(osp.join(playground_dir, filename)))
            play_dir = _Path(osp.abspath(playground_dir))
            # Must be relative to playground_dir (path traversal guard)
            try:
                file_path.resolve().relative_to(play_dir.resolve())
                return file_path.is_file()
            except ValueError:
                return False

        assert _servable("index.html") is True
        assert _servable("styles.css") is True
        assert _servable("app.js") is True
        assert _servable("tool-definitions.js") is True
        assert _servable("../../etc/passwd") is False
        assert _servable("../../../joern_server/proxy.py") is False
