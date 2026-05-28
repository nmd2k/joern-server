"""Unit tests for POST /parse."""

from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_parse_happy_path_mocks_subprocess(tmp_path) -> None:
    state = make_test_state(tmp_path, parse_bin="/bin/echo")
    client = TestClient(create_test_app(state=state))
    sample_id = "my-sample"
    cpg_path = tmp_path / "cpg-out" / sample_id

    def fake_run(*_args, **_kwargs):
        cpg_path.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="ok", stderr="")

    payload = {
        "sample_id": sample_id,
        "source_code": "int main(){}",
        "language": "c",
        "overwrite": True,
    }
    with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
        response = client.post("/parse", json=payload)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ok"] is True
    assert body["sample_id"] == sample_id
    assert body["cache_hit"] is False
    assert "source_hash" in body


def test_parse_missing_sample_id(tmp_path) -> None:
    client = TestClient(create_test_app(state=make_test_state(tmp_path)))
    response = client.post("/parse", json={"source_code": "x"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "sample_id" in response.json()["error"]


def test_parse_no_source_code_unknown_sid_returns_404(tmp_path) -> None:
    """No source_code and sample_id not in archive → restore-by-affinity 404."""
    client = TestClient(create_test_app(state=make_test_state(tmp_path)))
    response = client.post("/parse", json={"sample_id": "s1"})
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["code"] == "not_archived"


def test_parse_records_metrics(tmp_path) -> None:
    state = make_test_state(tmp_path, parse_bin="/bin/echo")
    client = TestClient(create_test_app(state=state))
    sample_id = "metrics-sample"
    cpg_path = tmp_path / "cpg-out" / sample_id

    def fake_run(*_args, **_kwargs):
        cpg_path.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
        client.post(
            "/parse",
            json={"sample_id": sample_id, "source_code": "int x;", "overwrite": True},
        )

    metrics = client.get("/metrics")
    text = metrics.text
    assert "joern_proxy_parse_requests_total" in text
    assert "joern_proxy_parse_duration_seconds_sum" in text


def test_parse_idempotent_cache_hit(tmp_path) -> None:
    state = make_test_state(tmp_path)
    sample_id = "cached"
    cpg_path = tmp_path / "cpg-out" / sample_id
    cpg_path.mkdir(parents=True)
    source = "int main(){}"
    import hashlib

    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with state.sid_hash_lock:
        state.sid_to_hash[sample_id] = source_hash

    client = TestClient(create_test_app(state=state))
    with patch("joern_server.parse.runner.subprocess.run") as mock_run:
        response = client.post(
            "/parse",
            json={"sample_id": sample_id, "source_code": source},
        )
        mock_run.assert_not_called()

    assert response.status_code == HTTPStatus.OK
    assert response.json()["cache_hit"] is True


# ---------------------------------------------------------------------------
# Restore-by-affinity tests (S11-2)
# ---------------------------------------------------------------------------

def test_restore_by_affinity_copies_from_archive(tmp_path) -> None:
    """POST /parse with only sample_id restores CPG from archive when mapping exists."""
    import hashlib
    import shutil

    state = make_test_state(tmp_path)
    sample_id = "restore-me"
    source = "int main(){}"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    # Simulate a CPG in the archive
    archive_path = tmp_path / "cpg-archive" / source_hash
    archive_path.mkdir(parents=True)
    (archive_path / "cpg.bin").write_bytes(b"\x00fake")

    # Register in registry
    state.cpg_registry.register(source_hash, {
        "sample_id": sample_id,
        "archive_path": str(archive_path),
        "archived_at": "2026-01-01T00:00:00Z",
        "last_used": "",
        "size_bytes": 4,
    })
    state.cpg_registry.register_sample_id(sample_id, source_hash)

    client = TestClient(create_test_app(state=state))
    response = client.post("/parse", json={"sample_id": sample_id})

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ok"] is True
    assert body["cache_hit"] is True
    assert body["restore"] == "archive"
    assert body["source_hash"] == source_hash
    assert (tmp_path / "cpg-out" / sample_id).exists()


def test_restore_by_affinity_archive_file_missing(tmp_path) -> None:
    """Returns 404 when sid_map has a hash but the archive entry no longer exists on disk."""
    import hashlib

    state = make_test_state(tmp_path)
    sample_id = "gone-cpg"
    source_hash = hashlib.sha256(b"x").hexdigest()

    # Only record the sid → hash mapping; no archive entry on disk.
    state.cpg_registry.register_sample_id(sample_id, source_hash)

    client = TestClient(create_test_app(state=state))
    response = client.post("/parse", json={"sample_id": sample_id})

    assert response.status_code == HTTPStatus.NOT_FOUND
    # No archive entry → "not_archived" (lookup returns None)
    assert response.json()["code"] in ("not_archived", "archive_missing")


# ---------------------------------------------------------------------------
# Registry sid_map persistence tests (S11-1)
# ---------------------------------------------------------------------------

def test_registry_register_and_lookup_sample_id(tmp_path) -> None:
    from joern_server.cpg.registry import CPGRegistry

    registry_path = tmp_path / "registry.db"
    reg = CPGRegistry(registry_path)

    reg.register_sample_id("my-sample", "abc" * 21 + "a")  # 64 chars
    result = reg.lookup_by_sample_id("my-sample")
    assert result == "abc" * 21 + "a"


def test_registry_all_sid_entries(tmp_path) -> None:
    from joern_server.cpg.registry import CPGRegistry

    reg = CPGRegistry(tmp_path / "registry.db")
    reg.register_sample_id("s1", "a" * 64)
    reg.register_sample_id("s2", "b" * 64)

    entries = dict(reg.all_sid_entries())
    assert entries["s1"] == "a" * 64
    assert entries["s2"] == "b" * 64


def test_registry_sid_map_survives_reload(tmp_path) -> None:
    from joern_server.cpg.registry import CPGRegistry

    db_path = tmp_path / "registry.db"
    reg1 = CPGRegistry(db_path)
    reg1.register_sample_id("persistent", "c" * 64)

    # New instance, same db file
    reg2 = CPGRegistry(db_path)
    assert reg2.lookup_by_sample_id("persistent") == "c" * 64


# ---------------------------------------------------------------------------
# Config CPG_BASE_DIR derivation tests (S11-3)
# ---------------------------------------------------------------------------

def test_config_cpg_base_dir_defaults(monkeypatch, tmp_path) -> None:
    from joern_server.config import Settings

    monkeypatch.setenv("CPG_BASE_DIR", str(tmp_path / "cpg"))
    monkeypatch.delenv("CPG_OUT_DIR", raising=False)
    monkeypatch.delenv("CPG_ARCHIVE_DIR", raising=False)

    s = Settings.from_env()
    assert s.cpg_out_dir == str(tmp_path / "cpg") + "/out"
    assert s.cpg_archive_dir == str(tmp_path / "cpg") + "/archive"


def test_config_cpg_out_dir_override_beats_base(monkeypatch, tmp_path) -> None:
    from joern_server.config import Settings

    monkeypatch.setenv("CPG_BASE_DIR", str(tmp_path / "cpg"))
    monkeypatch.setenv("CPG_OUT_DIR", str(tmp_path / "custom-out"))
    monkeypatch.delenv("CPG_ARCHIVE_DIR", raising=False)

    s = Settings.from_env()
    assert s.cpg_out_dir == str(tmp_path / "custom-out")
    assert s.cpg_archive_dir == str(tmp_path / "cpg") + "/archive"
