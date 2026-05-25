"""Unit tests for POST /parse/repo and POST /parse/repo/upload (S9-003–S9-006)."""

import datetime
import hashlib
import io
import json
import zipfile
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from joern_server.parse.tree import (
    canonical_tree_hash,
    collect_tree_files,
    extract_archive,
    is_under_allowed_root,
    parse_allowed_roots,
    parse_multipart_archive,
    validate_repo_path,
)
from tests.helpers.app import create_test_app, make_test_state


def _make_client(tmp_path, **overrides) -> tuple[TestClient, Path]:
    state = make_test_state(tmp_path, **overrides)
    return TestClient(create_test_app(state=state)), tmp_path


class TestValidateRepoPath:
    def test_valid_relative_paths(self):
        assert validate_repo_path("src/main.c") is None
        assert validate_repo_path("a/b/c.h") is None

    def test_rejects_parent_segments(self):
        assert validate_repo_path("../etc/passwd") is not None
        assert validate_repo_path("src/../x.c") is not None

    def test_rejects_absolute_paths(self):
        assert validate_repo_path("/etc/passwd") is not None
        assert validate_repo_path("\\windows\\x") is not None

    def test_rejects_empty_and_null(self):
        assert validate_repo_path("") is not None
        assert validate_repo_path("a\x00b") is not None


class TestCanonicalTreeHash:
    def test_stable_for_same_tree(self):
        files = {"b.c": "int b;", "a.c": "int a;"}
        h1 = canonical_tree_hash(files)
        h2 = canonical_tree_hash(dict(files))
        assert h1 == h2
        assert len(h1) == 64

    def test_order_independent(self):
        f1 = {"z.c": "z", "a.c": "a"}
        f2 = {"a.c": "a", "z.c": "z"}
        assert canonical_tree_hash(f1) == canonical_tree_hash(f2)

    def test_content_change_changes_hash(self):
        base = {"main.c": "int main(){}"}
        changed = {"main.c": "int main(){ return 0; }"}
        assert canonical_tree_hash(base) != canonical_tree_hash(changed)

    def test_matches_spec_concat(self):
        files = {"src/a.c": "hello"}
        h = hashlib.sha256()
        path = "src/a.c"
        content_hash = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(content_hash.encode("ascii"))
        h.update(b"\n")
        assert canonical_tree_hash(files) == h.hexdigest()


class TestJsonlRepoParse:
    def test_jsonl_happy_path_mocks_joern_parse(self, tmp_path):
        client, root = _make_client(tmp_path, parse_bin="/bin/echo")
        cpg_path = root / "cpg-out" / "my-app"

        def fake_run(*_args, **_kwargs):
            cpg_path.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0, stdout="", stderr="")

        ndjson = (
            json.dumps({"path": "src/main.c", "content": "int main(){}"}) + "\n"
            + json.dumps({"path": "src/util.h", "content": "#pragma once"}) + "\n"
        )
        with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
            response = client.post(
                "/parse/repo?sample_id=my-app&language=c&overwrite=true",
                content=ndjson,
                headers={"Content-Type": "application/x-ndjson"},
            )

        assert response.status_code == HTTPStatus.OK
        resp = response.json()
        assert resp["ok"] is True
        assert resp["parse_mode"] == "repo"
        assert resp["ingest_mode"] == "jsonl"
        assert resp["file_count"] == 2
        assert "source_hash" in resp

    def test_jsonl_invalid_path_returns_400(self, tmp_path):
        client, _ = _make_client(tmp_path)
        ndjson = json.dumps({"path": "../evil.c", "content": "x"}) + "\n"
        response = client.post(
            "/parse/repo?sample_id=app",
            content=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.json()["code"] == "invalid_path"

    def test_jsonl_missing_sample_id(self, tmp_path):
        client, _ = _make_client(tmp_path)
        response = client.post(
            "/parse/repo",
            content='{"path":"a.c","content":"x"}\n',
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST

    def test_jsonl_file_limit(self, tmp_path):
        client, _ = _make_client(tmp_path, parse_repo_max_files=1)
        ndjson = (
            json.dumps({"path": "a.c", "content": "1"}) + "\n"
            + json.dumps({"path": "b.c", "content": "2"}) + "\n"
        )
        response = client.post(
            "/parse/repo?sample_id=app",
            content=ndjson,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert response.json()["code"] == "payload_too_large"


class TestMutualExclusion:
    def test_upload_id_and_source_root_exclusive(self, tmp_path):
        client, _ = _make_client(tmp_path)
        response = client.post(
            "/parse/repo",
            json={
                "sample_id": "s1",
                "upload_id": "uuid",
                "source_root": "/workspace/datasets/x",
            },
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert "mutually exclusive" in response.json()["error"]

    def test_json_requires_upload_or_source_root(self, tmp_path):
        client, _ = _make_client(tmp_path)
        response = client.post("/parse/repo", json={"sample_id": "s1"})
        assert response.status_code == HTTPStatus.BAD_REQUEST


class TestUploadIdFlow:
    @pytest.fixture
    def upload_env(self, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        cpg_out = tmp_path / "cpg-out"
        cpg_out.mkdir()
        return uploads, cpg_out

    def test_upload_then_parse_by_id(self, upload_env, tmp_path):
        uploads, cpg_out = upload_env
        upload_id = "550e8400-e29b-41d4-a716-446655440000"
        tree = uploads / upload_id / "tree"
        tree.mkdir(parents=True)
        (tree / "main.c").write_text("int main(){}", encoding="utf-8")
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        meta = {"upload_id": upload_id, "expires_at": expires, "bytes_stored": 10}
        (uploads / upload_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        client = TestClient(
            create_test_app(
                make_test_state(
                    tmp_path,
                    repo_uploads_dir=str(uploads),
                    cpg_out_dir=str(cpg_out),
                    parse_bin="/bin/echo",
                )
            )
        )
        cpg_target = cpg_out / "from-upload"

        def fake_run(*_args, **_kwargs):
            cpg_target.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
            response = client.post(
                "/parse/repo",
                json={
                    "sample_id": "from-upload",
                    "upload_id": upload_id,
                    "language": "c",
                    "overwrite": True,
                },
            )

        assert response.status_code == HTTPStatus.OK
        assert response.json()["ingest_mode"] == "upload"
        assert response.json()["file_count"] == 1

    def test_expired_upload_returns_410(self, upload_env, tmp_path):
        uploads, _ = upload_env
        upload_id = "dead-beef-dead-beef-deadbeefdead"
        tree = uploads / upload_id / "tree"
        tree.mkdir(parents=True)
        (tree / "x.c").write_text("x", encoding="utf-8")
        meta = {
            "upload_id": upload_id,
            "expires_at": "2020-01-01T00:00:00Z",
            "bytes_stored": 1,
        }
        (uploads / upload_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        client = TestClient(
            create_test_app(make_test_state(tmp_path, repo_uploads_dir=str(uploads)))
        )
        response = client.post(
            "/parse/repo",
            json={"sample_id": "s", "upload_id": upload_id},
        )
        assert response.status_code == HTTPStatus.GONE
        assert response.json()["code"] == "upload_expired"


class TestSourceRoot:
    def test_allowed_root_collect_and_parse(self, tmp_path, monkeypatch):
        datasets = tmp_path / "datasets" / "proj"
        datasets.mkdir(parents=True)
        (datasets / "a.c").write_text("int a;", encoding="utf-8")
        monkeypatch.setenv("PARSE_REPO_ALLOWED_ROOTS", str(tmp_path / "datasets"))

        cpg_out = tmp_path / "cpg-out"
        cpg_out.mkdir()
        client = TestClient(
            create_test_app(
                make_test_state(tmp_path, cpg_out_dir=str(cpg_out), parse_bin="/bin/echo")
            )
        )
        cpg_target = cpg_out / "ops-sample"

        def fake_run(*_args, **_kwargs):
            cpg_target.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
            response = client.post(
                "/parse/repo",
                json={
                    "sample_id": "ops-sample",
                    "source_root": str(datasets),
                    "language": "c",
                    "overwrite": True,
                },
            )

        assert response.json()["ingest_mode"] == "source_root"

    def test_disallowed_root_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PARSE_REPO_ALLOWED_ROOTS", str(tmp_path / "allowed"))
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "x.c").write_text("x", encoding="utf-8")

        client, _ = _make_client(tmp_path)
        response = client.post(
            "/parse/repo",
            json={"sample_id": "s", "source_root": str(outside)},
        )
        assert response.json()["code"] == "invalid_source_root"


class TestRepoUploadEndpoint:
    def test_multipart_zip_upload(self, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        client = TestClient(
            create_test_app(make_test_state(tmp_path, repo_uploads_dir=str(uploads)))
        )

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

        response = client.post(
            "/parse/repo/upload",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        assert response.status_code == HTTPStatus.OK
        resp = response.json()
        assert resp["ok"] is True
        assert "upload_id" in resp
        assert resp["bytes_stored"] == len(archive)
        upload_dir = uploads / resp["upload_id"] / "tree"
        extracted = list(upload_dir.rglob("main.c"))
        assert extracted, f"expected main.c under {upload_dir}, got {list(upload_dir.rglob('*'))}"

    def test_oversize_archive_rejected(self, tmp_path):
        client = TestClient(
            create_test_app(
                make_test_state(
                    tmp_path,
                    parse_repo_max_archive_bytes=10,
                    repo_uploads_dir=str(tmp_path / "uploads"),
                )
            )
        )
        response = client.post(
            "/parse/repo/upload",
            content=b"x" * 20,
            headers={"Content-Type": "multipart/form-data; boundary=b"},
        )
        assert response.json()["code"] == "payload_too_large"


class TestHelpers:
    def test_collect_tree_files_respects_limits(self, tmp_path):
        root = tmp_path / "tree"
        root.mkdir()
        (root / "a.c").write_text("a", encoding="utf-8")
        (root / "b.c").write_text("b", encoding="utf-8")
        files, err = collect_tree_files(root, max_files=1, max_bytes=1000)
        assert files is None
        assert err["code"] == "payload_too_large"

    def test_parse_multipart_extracts_archive_field(self):
        boundary = "abc"
        payload = b"ZIPDATA"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="archive"; filename="r.zip"\r\n\r\n'
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        data, err = parse_multipart_archive(body, f"multipart/form-data; boundary={boundary}")
        assert err is None
        assert data == payload

    def test_extract_zip(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("hello.c", "int x;")
        dest = tmp_path / "out"
        assert extract_archive(buf.getvalue(), dest) is None
        assert (dest / "hello.c").read_text(encoding="utf-8") == "int x;"

    def test_is_under_allowed_root(self, tmp_path):
        allowed = [Path(tmp_path / "datasets").resolve()]
        inside = tmp_path / "datasets" / "proj"
        inside.mkdir(parents=True)
        outside = tmp_path / "other"
        outside.mkdir()
        assert is_under_allowed_root(inside, allowed) is True
        assert is_under_allowed_root(outside, allowed) is False

    def test_parse_allowed_roots_default(self, monkeypatch):
        monkeypatch.delenv("PARSE_REPO_ALLOWED_ROOTS", raising=False)
        roots = parse_allowed_roots()
        assert len(roots) >= 1
