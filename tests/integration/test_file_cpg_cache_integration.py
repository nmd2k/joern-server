"""Integration tests for FileCPGRegistry through parse/cleanup HTTP handlers."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_parse_cleanup_archive_parse_cache_hit(tmp_path: Path) -> None:
    """parse → cleanup(archive=true) → parse(same source, new sample_id) hits archive."""
    state = make_test_state(tmp_path, parse_bin="/bin/echo")
    client = TestClient(create_test_app(state=state))
    source = "int main(){ return 0; }"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    sample_a = "sample-a"
    sample_b = "sample-b"
    cpg_a = tmp_path / "cpg-out" / sample_a

    def fake_run(*_args, **_kwargs):
        cpg_a.parent.mkdir(parents=True, exist_ok=True)
        cpg_a.write_bytes(b"fake-cpg")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
        r1 = client.post(
            "/parse",
            json={"sample_id": sample_a, "source_code": source, "overwrite": True},
        )
    assert r1.status_code == HTTPStatus.OK
    assert r1.json()["cache_hit"] is False

    r_clean = client.post(
        "/cleanup",
        json={"sample_id": sample_a, "archive": True},
    )
    assert r_clean.status_code == HTTPStatus.OK
    assert r_clean.json()["archived"] is True
    assert r_clean.json()["source_hash"] == source_hash
    assert not cpg_a.exists()

    archive_file = tmp_path / "cpg-archive" / source_hash
    assert archive_file.is_file()
    assert (tmp_path / "cpg-archive" / f"{source_hash}.meta.json").is_file()

    with patch("joern_server.parse.runner.subprocess.run") as mock_run:
        r2 = client.post(
            "/parse",
            json={"sample_id": sample_b, "source_code": source, "overwrite": True},
        )
        mock_run.assert_not_called()

    assert r2.status_code == HTTPStatus.OK
    body = r2.json()
    assert body["cache_hit"] is True
    assert body["source_hash"] == source_hash
    assert Path(body["cpg_path"]).exists()
