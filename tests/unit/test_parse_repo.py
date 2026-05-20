"""Unit tests for POST /parse/repo and POST /parse/repo/upload (S9-003–S9-006)."""

import datetime
import hashlib
import io
import json
import shutil
import tempfile
import threading
import zipfile
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from joern_server.proxy import (
    JoernProxyHandler,
    _canonical_tree_hash,
    _collect_tree_files,
    _extract_archive,
    _is_under_allowed_root,
    _parse_allowed_roots,
    _parse_multipart_archive,
    _validate_repo_path,
)


def _make_repo_handler(
    path="/parse/repo",
    *,
    content_type="application/json",
    body: bytes | None = None,
    query: str = "",
    cpg_out_dir: str | None = None,
    repo_uploads_dir: str | None = None,
    parse_repo_max_files: int | None = None,
    parse_repo_max_archive_bytes: int | None = None,
):
    JoernProxyHandler.internal_url = "http://127.0.0.1:18080/query-sync"
    JoernProxyHandler.repl_semaphore = threading.Semaphore(1)
    JoernProxyHandler.query_cache = None
    JoernProxyHandler.parse_bin = "/bin/echo"
    JoernProxyHandler.cpg_out_dir = cpg_out_dir or "/tmp/cpg-out-test"
    JoernProxyHandler.cpg_archive_dir = "/tmp/cpg-archive-test"
    if repo_uploads_dir is not None:
        JoernProxyHandler.repo_uploads_dir = repo_uploads_dir
    JoernProxyHandler.parse_timeout_sec = 30
    JoernProxyHandler.parse_repo_timeout_sec = 30
    if parse_repo_max_files is not None:
        JoernProxyHandler.parse_repo_max_files = parse_repo_max_files
    else:
        JoernProxyHandler.parse_repo_max_files = 2000
    JoernProxyHandler.parse_repo_max_bytes = 50_000_000
    if parse_repo_max_archive_bytes is not None:
        JoernProxyHandler.parse_repo_max_archive_bytes = parse_repo_max_archive_bytes
    else:
        JoernProxyHandler.parse_repo_max_archive_bytes = 500 * 1024 * 1024
    JoernProxyHandler.parse_repo_upload_ttl_hours = 24
    JoernProxyHandler.query_timeout_sec = 5
    JoernProxyHandler.cpg_registry = None

    handler = JoernProxyHandler.__new__(JoernProxyHandler)
    handler.path = f"{path}{query}"
    handler.headers = {"Content-Type": content_type}
    handler.wfile = BytesIO()
    handler.requestline = f"POST {handler.path} HTTP/1.1"
    handler.server = MagicMock()
    handler.client_address = ("127.0.0.1", 9999)

    if body is None:
        body = b""
    handler.headers["Content-Length"] = str(len(body))

    def _read_body():
        return body

    handler._read_body = _read_body
    if content_type == "application/x-ndjson":
        handler.rfile = BytesIO(body)
    else:
        handler.rfile = BytesIO(body)
    return handler


class TestValidateRepoPath:
    def test_valid_relative_paths(self):
        assert _validate_repo_path("src/main.c") is None
        assert _validate_repo_path("a/b/c.h") is None

    def test_rejects_parent_segments(self):
        assert _validate_repo_path("../etc/passwd") is not None
        assert _validate_repo_path("src/../x.c") is not None

    def test_rejects_absolute_paths(self):
        assert _validate_repo_path("/etc/passwd") is not None
        assert _validate_repo_path("\\windows\\x") is not None

    def test_rejects_empty_and_null(self):
        assert _validate_repo_path("") is not None
        assert _validate_repo_path("a\x00b") is not None


class TestCanonicalTreeHash:
    def test_stable_for_same_tree(self):
        files = {"b.c": "int b;", "a.c": "int a;"}
        h1 = _canonical_tree_hash(files)
        h2 = _canonical_tree_hash(dict(files))
        assert h1 == h2
        assert len(h1) == 64

    def test_order_independent(self):
        f1 = {"z.c": "z", "a.c": "a"}
        f2 = {"a.c": "a", "z.c": "z"}
        assert _canonical_tree_hash(f1) == _canonical_tree_hash(f2)

    def test_content_change_changes_hash(self):
        base = {"main.c": "int main(){}"}
        changed = {"main.c": "int main(){ return 0; }"}
        assert _canonical_tree_hash(base) != _canonical_tree_hash(changed)

    def test_matches_spec_concat(self):
        files = {"src/a.c": "hello"}
        h = hashlib.sha256()
        path = "src/a.c"
        content_hash = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(content_hash.encode("ascii"))
        h.update(b"\n")
        assert _canonical_tree_hash(files) == h.hexdigest()


class TestJsonlRepoParse:
    def test_jsonl_happy_path_mocks_joern_parse(self, tmp_path):
        cpg_dir = tmp_path / "cpg-out"
        cpg_dir.mkdir()

        ndjson = (
            json.dumps({"path": "src/main.c", "content": "int main(){}"}) + "\n"
            + json.dumps({"path": "src/util.h", "content": "#pragma once"}) + "\n"
        ).encode("utf-8")

        handler = _make_repo_handler(
            query="?sample_id=my-app&language=c&overwrite=true",
            content_type="application/x-ndjson",
            body=ndjson,
            cpg_out_dir=str(cpg_dir),
        )
        sent: list[tuple] = []

        def capture(status, data):
            sent.append((status, data))

        mock_proc = MagicMock(returncode=0)
        with patch.object(handler, "_send_json", side_effect=capture):
            with patch("joern_server.proxy.subprocess.run", return_value=mock_proc):
                with patch("joern_server.proxy.Path.exists", return_value=True):
                    handler.do_POST()

        assert sent[0][0] == HTTPStatus.OK
        resp = sent[0][1]
        assert resp["ok"] is True
        assert resp["parse_mode"] == "repo"
        assert resp["ingest_mode"] == "jsonl"
        assert resp["file_count"] == 2
        assert "source_hash" in resp

    def test_jsonl_invalid_path_returns_400(self):
        ndjson = (json.dumps({"path": "../evil.c", "content": "x"}) + "\n").encode("utf-8")
        handler = _make_repo_handler(
            query="?sample_id=app",
            content_type="application/x-ndjson",
            body=ndjson,
        )
        sent: list[tuple] = []

        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()

        assert sent[0][0] == HTTPStatus.BAD_REQUEST
        assert sent[0][1]["code"] == "invalid_path"

    def test_jsonl_missing_sample_id(self):
        handler = _make_repo_handler(
            query="",
            content_type="application/x-ndjson",
            body=b'{"path":"a.c","content":"x"}\n',
        )
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][0] == HTTPStatus.BAD_REQUEST

    def test_jsonl_file_limit(self):
        ndjson = (
            json.dumps({"path": "a.c", "content": "1"}) + "\n"
            + json.dumps({"path": "b.c", "content": "2"}) + "\n"
        ).encode("utf-8")
        handler = _make_repo_handler(
            query="?sample_id=app",
            content_type="application/x-ndjson",
            body=ndjson,
            parse_repo_max_files=1,
        )
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][1]["code"] == "payload_too_large"


class TestMutualExclusion:
    def test_upload_id_and_source_root_exclusive(self):
        body = json.dumps({
            "sample_id": "s1",
            "upload_id": "uuid",
            "source_root": "/workspace/datasets/x",
        }).encode("utf-8")
        handler = _make_repo_handler(body=body)
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][0] == HTTPStatus.BAD_REQUEST
        assert "mutually exclusive" in sent[0][1]["error"]

    def test_json_requires_upload_or_source_root(self):
        body = json.dumps({"sample_id": "s1"}).encode("utf-8")
        handler = _make_repo_handler(body=body)
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][0] == HTTPStatus.BAD_REQUEST


class TestUploadIdFlow:
    @pytest.fixture
    def upload_env(self, tmp_path, monkeypatch):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        cpg_out = tmp_path / "cpg-out"
        cpg_out.mkdir()
        JoernProxyHandler.repo_uploads_dir = str(uploads)
        JoernProxyHandler.cpg_out_dir = str(cpg_out)
        return uploads

    def test_upload_then_parse_by_id(self, upload_env, tmp_path):
        upload_id = "550e8400-e29b-41d4-a716-446655440000"
        tree = upload_env / upload_id / "tree"
        tree.mkdir(parents=True)
        (tree / "main.c").write_text("int main(){}", encoding="utf-8")
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        meta = {"upload_id": upload_id, "expires_at": expires, "bytes_stored": 10}
        (upload_env / upload_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        body = json.dumps({
            "sample_id": "from-upload",
            "upload_id": upload_id,
            "language": "c",
            "overwrite": True,
        }).encode("utf-8")
        handler = _make_repo_handler(body=body, repo_uploads_dir=str(upload_env), cpg_out_dir=str(tmp_path / "cpg-out"))
        (tmp_path / "cpg-out").mkdir(exist_ok=True)
        sent: list[tuple] = []

        mock_proc = MagicMock(returncode=0)
        cpg_target = tmp_path / "cpg-out" / "from-upload"
        real_exists = Path.exists

        def exists_side_effect(self_path):
            if str(self_path) == str(cpg_target):
                return True
            return real_exists(self_path)

        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            with patch("joern_server.proxy.subprocess.run", return_value=mock_proc):
                with patch.object(Path, "exists", exists_side_effect):
                    handler.do_POST()

        assert sent[0][0] == HTTPStatus.OK
        assert sent[0][1]["ingest_mode"] == "upload"
        assert sent[0][1]["file_count"] == 1

    def test_expired_upload_returns_410(self, upload_env):
        upload_id = "dead-beef-dead-beef-deadbeefdead"
        tree = upload_env / upload_id / "tree"
        tree.mkdir(parents=True)
        (tree / "x.c").write_text("x", encoding="utf-8")
        meta = {
            "upload_id": upload_id,
            "expires_at": "2020-01-01T00:00:00Z",
            "bytes_stored": 1,
        }
        (upload_env / upload_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        body = json.dumps({"sample_id": "s", "upload_id": upload_id}).encode("utf-8")
        handler = _make_repo_handler(body=body, repo_uploads_dir=str(upload_env))
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][0] == HTTPStatus.GONE
        assert sent[0][1]["code"] == "upload_expired"


class TestSourceRoot:
    def test_allowed_root_collect_and_parse(self, tmp_path, monkeypatch):
        datasets = tmp_path / "datasets" / "proj"
        datasets.mkdir(parents=True)
        (datasets / "a.c").write_text("int a;", encoding="utf-8")
        monkeypatch.setenv("PARSE_REPO_ALLOWED_ROOTS", str(tmp_path / "datasets"))

        cpg_out = tmp_path / "cpg-out"
        cpg_out.mkdir()

        body = json.dumps({
            "sample_id": "ops-sample",
            "source_root": str(datasets),
            "language": "c",
            "overwrite": True,
        }).encode("utf-8")
        handler = _make_repo_handler(body=body, cpg_out_dir=str(cpg_out))
        sent: list[tuple] = []

        mock_proc = MagicMock(returncode=0)
        cpg_target = cpg_out / "ops-sample"
        real_exists = Path.exists

        def exists_side_effect(self_path):
            if str(self_path) == str(cpg_target):
                return True
            return real_exists(self_path)

        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            with patch("joern_server.proxy.subprocess.run", return_value=mock_proc):
                with patch.object(Path, "exists", exists_side_effect):
                    handler.do_POST()

        assert sent[0][1]["ingest_mode"] == "source_root"

    def test_disallowed_root_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PARSE_REPO_ALLOWED_ROOTS", str(tmp_path / "allowed"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "x.c").write_text("x", encoding="utf-8")

        body = json.dumps({
            "sample_id": "s",
            "source_root": str(outside),
        }).encode("utf-8")
        handler = _make_repo_handler(body=body)
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][1]["code"] == "invalid_source_root"


class TestRepoUploadEndpoint:
    def test_multipart_zip_upload(self, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("proj/main.c", "int main(){}")
        archive = buf.getvalue()

        boundary = "----boundary123"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="archive"; filename="repo.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode("utf-8") + archive + f"\r\n--{boundary}--\r\n".encode("utf-8")

        handler = _make_repo_handler(
            path="/parse/repo/upload",
            content_type=f"multipart/form-data; boundary={boundary}",
            body=body,
            repo_uploads_dir=str(uploads),
        )
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()

        assert sent[0][0] == HTTPStatus.OK
        resp = sent[0][1]
        assert resp["ok"] is True
        assert "upload_id" in resp
        assert resp["bytes_stored"] == len(archive)
        upload_dir = uploads / resp["upload_id"] / "tree"
        extracted = list(upload_dir.rglob("main.c"))
        assert extracted, f"expected main.c under {upload_dir}, got {list(upload_dir.rglob('*'))}"

    def test_oversize_archive_rejected(self, tmp_path):
        handler = _make_repo_handler(
            path="/parse/repo/upload",
            content_type="multipart/form-data; boundary=b",
            body=b"x" * 20,
            repo_uploads_dir=str(tmp_path / "uploads"),
            parse_repo_max_archive_bytes=10,
        )
        sent: list[tuple] = []
        with patch.object(handler, "_send_json", side_effect=lambda s, d: sent.append((s, d))):
            handler.do_POST()
        assert sent[0][1]["code"] == "payload_too_large"


class TestHelpers:
    def test_collect_tree_files_respects_limits(self, tmp_path):
        root = tmp_path / "tree"
        root.mkdir()
        (root / "a.c").write_text("a", encoding="utf-8")
        (root / "b.c").write_text("b", encoding="utf-8")
        files, err = _collect_tree_files(root, max_files=1, max_bytes=1000)
        assert files is None
        assert err["code"] == "payload_too_large"

    def test_parse_multipart_extracts_archive_field(self):
        boundary = "abc"
        payload = b"ZIPDATA"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="archive"; filename="r.zip"\r\n\r\n'
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        data, err = _parse_multipart_archive(body, f"multipart/form-data; boundary={boundary}")
        assert err is None
        assert data == payload

    def test_extract_zip(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("hello.c", "int x;")
        dest = tmp_path / "out"
        assert _extract_archive(buf.getvalue(), dest) is None
        assert (dest / "hello.c").read_text(encoding="utf-8") == "int x;"

    def test_is_under_allowed_root(self, tmp_path):
        allowed = [Path(tmp_path / "datasets").resolve()]
        inside = tmp_path / "datasets" / "proj"
        inside.mkdir(parents=True)
        outside = tmp_path / "other"
        outside.mkdir()
        assert _is_under_allowed_root(inside, allowed) is True
        assert _is_under_allowed_root(outside, allowed) is False
