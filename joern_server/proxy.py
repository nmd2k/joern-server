import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import httpx


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return default if v is None or v == "" else int(v)


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _upstream_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Headers to forward to the in-container Joern HTTP /query-sync (affinity + auth)."""
    h: dict[str, str] = {"Content-Type": "application/json"}
    auth = handler.headers.get("Authorization")
    if auth:
        h["Authorization"] = auth
    sid = handler.headers.get("X-Session-Id")
    if sid:
        h["X-Session-Id"] = sid
    rid = handler.headers.get("X-Request-Id")
    if rid:
        h["X-Request-Id"] = rid
    return h


def _safe_sample_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", raw.strip())
    return safe or "sample"


# Map common/alias language names to the Joern-recognized language strings.
# Run `joern-parse --list-languages` inside the container to see all valid names.
_LANGUAGE_ALIASES: dict[str, str] = {
    # Python: 'python' triggers missing py2cpg.sh; 'pythonsrc' uses pysrc2cpg (installed).
    "py": "pythonsrc",
    "python": "pythonsrc",
    # JavaScript / TypeScript
    "js": "jssrc",
    "ts": "jssrc",
    "javascript": "jssrc",
    "typescript": "jssrc",
    # C++ — c2cpg handles both C and C++ when given a .cpp/.cc file.
    "cpp": "c",
    "c++": "c",
    "cc": "c",
    "cxx": "c",
    # C# — alias
    "cs": "csharpsrc",
    "csharp": "csharpsrc",
    # Go
    "go": "golang",
    # Java aliases
    "javasrc": "java",
    # Ruby
    "rb": "rubysrc",
    "ruby": "rubysrc",
}


def _normalize_language(language: str) -> str:
    """Translate caller-supplied language alias to the Joern-native name."""
    return _LANGUAGE_ALIASES.get(language.lower(), language) if language else language


def _json_error(msg: str, *, code: str = "bad_request") -> dict[str, str]:
    return {"error": msg, "code": code}


class JoernProxyHandler(BaseHTTPRequestHandler):
    internal_url: str = ""
    parse_bin: str = "/opt/joern/joern-cli/joern-parse"
    cpg_out_dir: str = "/workspace/cpg-out"
    parse_timeout_sec: int = 900
    query_timeout_sec: int = 600

    def _log_event(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "component": "joern-proxy",
            "event": event,
            "path": self.path,
            "session_id": self.headers.get("X-Session-Id"),
            "ts_ms": int(time.time() * 1000),
        }
        payload.update(fields)
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except Exception:
            return

    @staticmethod
    def _classify_query(query: str) -> str:
        q = (query or "").strip()
        if not q:
            return "empty"
        if q.startswith("load_cpg("):
            return "load_cpg"
        if q.startswith("importCpg("):
            return "importCpg"
        if q == "version":
            return "version"
        if q == "help":
            return "help"
        m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", q)
        if m:
            return m.group(1)
        return "cpgql"

    @staticmethod
    def _preview_query(query: str, limit: int = 180) -> str:
        q = re.sub(r"\s+", " ", (query or "").strip())
        if len(q) <= limit:
            return q
        return q[:limit] + "...(truncated)"

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _parse_request_json(self) -> tuple[Optional[dict], Optional[dict]]:
        try:
            raw = self._read_body()
            data = json.loads(raw.decode("utf-8") if raw else "{}")
        except Exception:
            return None, _json_error("invalid JSON body")
        if not isinstance(data, dict):
            return None, _json_error("JSON body must be an object")
        return data, None

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/version":
            # Implement /version using a cheap Joern query.
            try:
                resp = httpx.post(
                    self.internal_url,
                    json={"query": "version"},
                    headers=_upstream_headers(self),
                    timeout=60,
                )
                resp.raise_for_status()
                body = resp.json()
                stdout = body.get("stdout", "")
                self._send_json(HTTPStatus.OK, {"stdout": stdout})
            except Exception as e:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(e)})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _handle_parse(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        sample_id_raw = str(data.get("sample_id", "")).strip()
        source_code = data.get("source_code")
        language = _normalize_language(str(data.get("language", "")).strip())
        filename = str(data.get("filename", "")).strip() or "snippet.txt"
        overwrite = bool(data.get("overwrite", False))

        if not sample_id_raw:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: sample_id"))
            return
        if not isinstance(source_code, str) or not source_code.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: source_code"))
            return

        sample_id = _safe_sample_id(sample_id_raw)
        cpg_out = Path(self.cpg_out_dir) / sample_id
        self._log_event(
            "parse_request",
            sample_id=sample_id,
            language=(language or None),
            overwrite=overwrite,
        )
        if cpg_out.exists() and not overwrite:
            self._send_json(
                HTTPStatus.CONFLICT,
                _json_error(
                    f"CPG output already exists at {cpg_out}; pass overwrite=true to replace",
                    code="cpg_exists",
                ),
            )
            return
        if cpg_out.exists() and overwrite:
            shutil.rmtree(cpg_out, ignore_errors=True)

        tmp_src_dir = Path(tempfile.mkdtemp(prefix=f"joern-src-{sample_id}-"))
        try:
            src_path = tmp_src_dir / Path(filename).name
            src_path.write_text(source_code, encoding="utf-8", newline="\n")
            cmd = [
                self.parse_bin,
                str(tmp_src_dir),
                "--output",
                str(cpg_out),
            ]
            if language:
                cmd.extend(["--language", language])

            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=self.parse_timeout_sec,
                check=False,
            )
            ok = proc.returncode == 0 and cpg_out.exists()
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
            self._log_event(
                "parse_result",
                sample_id=sample_id,
                ok=ok,
                return_code=proc.returncode,
            )
            self._send_json(
                status,
                {
                    "ok": ok,
                    "sample_id": sample_id,
                    "cpg_path": str(cpg_out),
                    "language": language or None,
                    "return_code": proc.returncode,
                    "stdout": proc.stdout[-100_000:],
                    "stderr": proc.stderr[-100_000:],
                },
            )
        except subprocess.TimeoutExpired:
            self._send_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                _json_error(
                    f"joern-parse timed out after {self.parse_timeout_sec}s",
                    code="parse_timeout",
                ),
            )
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="parse_failed"))
        finally:
            shutil.rmtree(tmp_src_dir, ignore_errors=True)

    def _handle_cleanup(self) -> None:
        data, err = self._parse_request_json()
        if err is not None or data is None:
            self._send_json(HTTPStatus.BAD_REQUEST, err or _json_error("invalid request"))
            return

        sample_id_raw = str(data.get("sample_id", "")).strip()
        if not sample_id_raw:
            self._send_json(HTTPStatus.BAD_REQUEST, _json_error("missing required field: sample_id"))
            return

        sample_id = _safe_sample_id(sample_id_raw)
        cpg_out = Path(self.cpg_out_dir) / sample_id
        existed = cpg_out.exists()
        self._log_event("cleanup_request", sample_id=sample_id, existed=bool(existed))
        try:
            if existed:
                shutil.rmtree(cpg_out, ignore_errors=True)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "sample_id": sample_id,
                    "cpg_path": str(cpg_out),
                    "deleted": bool(existed),
                },
            )
        except Exception as e:
            self._send_json(HTTPStatus.BAD_GATEWAY, _json_error(str(e), code="cleanup_failed"))

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path == "/parse":
            self._handle_parse()
            return

        if self.path == "/cleanup":
            self._handle_cleanup()
            return

        if self.path != "/query-sync":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        body = self._read_body()
        try:
            query_class = "unknown"
            query_preview = ""
            try:
                req = json.loads(body.decode("utf-8") if body else "{}")
                if isinstance(req, dict):
                    query_str = str(req.get("query", "") or "")
                    query_class = self._classify_query(query_str)
                    query_preview = self._preview_query(query_str)
            except Exception:
                query_class = "invalid_json"
            request_id = self.headers.get("X-Request-Id")
            t0 = time.perf_counter()
            resp = httpx.post(
                self.internal_url,
                content=body,
                headers=_upstream_headers(self),
                timeout=self.query_timeout_sec,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            # Preserve status and body; clients expect Joern's /query-sync JSON.
            resp_json = resp.json()
            success = None
            if isinstance(resp_json, dict):
                success = resp_json.get("success")
            self._log_event(
                "query_sync",
                request_id=request_id,
                query_class=query_class,
                query_preview=query_preview,
                status_code=resp.status_code,
                success=success,
                latency_ms=latency_ms,
            )
            self._send_json(resp.status_code, resp_json)
        except Exception as e:
            self._log_event("query_sync_error", error=str(e))
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(e)})

    def log_message(self, fmt: str, *args) -> None:
        # Silence default http.server logging in container logs.
        return


def main() -> None:
    proxy_host = _env_str("PROXY_HOST", "0.0.0.0")
    proxy_port = _env_int("PROXY_PORT", _env_int("JOERN_PUBLISH_PORT", 8080))

    internal_host = _env_str("JOERN_INTERNAL_HOST", "127.0.0.1")
    internal_port = _env_int("JOERN_INTERNAL_PORT", 18080)
    parse_bin = _env_str("JOERN_PARSE_BIN", "/opt/joern/joern-cli/joern-parse")
    cpg_out_dir = _env_str("CPG_OUT_DIR", "/workspace/cpg-out")
    parse_timeout_sec = _env_int("JOERN_PARSE_TIMEOUT_SEC", 900)
    # Default aligns with training/agent --joern-timeout (600s); router HAProxy allows up to 3600s.
    query_timeout_sec = _env_int("JOERN_QUERY_TIMEOUT_SEC", 600)
    # Joern HTTP server endpoint inside the container.
    internal_url = f"http://{internal_host}:{internal_port}/query-sync"

    JoernProxyHandler.internal_url = internal_url
    JoernProxyHandler.parse_bin = parse_bin
    JoernProxyHandler.cpg_out_dir = cpg_out_dir
    JoernProxyHandler.parse_timeout_sec = parse_timeout_sec
    JoernProxyHandler.query_timeout_sec = query_timeout_sec
    httpd = ThreadingHTTPServer((proxy_host, proxy_port), JoernProxyHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

