"""Unit tests for FileCPGRegistry (filesystem CPG cache)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from joern_server.cpg.file_registry import FileCPGRegistry


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(archive_path: Path, sample_id: str, *, size_bytes: int = 0) -> dict:
    return {
        "archive_path": str(archive_path),
        "sample_id": sample_id,
        "archived_at": "2026-01-01T00:00:00Z",
        "last_used": "2026-01-02T00:00:00Z",
        "size_bytes": size_bytes,
    }


def test_register_writes_meta_when_target_preexists(tmp_path: Path) -> None:
    """Simulate cleanup: archive file exists before register() — lookup must hit."""
    reg = FileCPGRegistry(tmp_path, archive_max_count=100, archive_max_gb=50.0)
    source_hash = _sha256_hex(b"source")
    target = tmp_path / source_hash
    target.write_bytes(b"cpg-bytes")

    reg.register(source_hash, _entry(target, "sample-a", size_bytes=len(b"cpg-bytes")))

    sidecar = tmp_path / f"{source_hash}.meta.json"
    assert sidecar.is_file()
    result = reg.lookup(source_hash)
    assert result is not None
    assert result["sample_id"] == "sample-a"
    assert result["archive_path"] == str(target)


def test_lookup_flat_file_with_sidecar(tmp_path: Path) -> None:
    reg = FileCPGRegistry(tmp_path)
    source_hash = _sha256_hex(b"flat")
    archive = tmp_path / source_hash
    archive.write_bytes(b"x" * 10)
    reg.register(source_hash, _entry(archive, "s-flat", size_bytes=10))

    found = reg.lookup(source_hash)
    assert found is not None
    assert found["size_bytes"] == 10


def test_lookup_legacy_flat_file_without_meta(tmp_path: Path) -> None:
    reg = FileCPGRegistry(tmp_path)
    source_hash = _sha256_hex(b"legacy")
    archive = tmp_path / source_hash
    archive.write_bytes(b"legacy-cpg")

    found = reg.lookup(source_hash)
    assert found is not None
    assert found["archive_path"] == str(archive)
    assert found["sample_id"] == ""


def test_lookup_directory_layout(tmp_path: Path) -> None:
    reg = FileCPGRegistry(tmp_path)
    source_hash = _sha256_hex(b"dir")
    archive_dir = tmp_path / source_hash
    archive_dir.mkdir()
    (archive_dir / "cpg.bin").write_bytes(b"data")
    reg.register(source_hash, _entry(archive_dir, "s-dir", size_bytes=4))

    found = reg.lookup(source_hash)
    assert found is not None
    assert Path(found["archive_path"]).is_dir()


def test_register_flat_file_from_external_source(tmp_path: Path) -> None:
    reg = FileCPGRegistry(tmp_path)
    source_hash = _sha256_hex(b"external")
    src = tmp_path / "external.cpg"
    src.write_bytes(b"from-src")

    reg.register(source_hash, _entry(src, "s-ext", size_bytes=8))

    target = tmp_path / source_hash
    assert target.is_file()
    assert target.read_bytes() == b"from-src"
    assert reg.lookup(source_hash) is not None


def test_evict_if_needed_removes_flat_file_and_sidecar(tmp_path: Path) -> None:
    import time

    reg = FileCPGRegistry(tmp_path, archive_max_count=1, archive_max_gb=50.0)
    h1 = _sha256_hex(b"old")
    h2 = _sha256_hex(b"new")
    p1 = tmp_path / h1
    p1.write_bytes(b"old")
    reg.register(h1, _entry(p1, "old", size_bytes=3))
    time.sleep(0.05)
    p2 = tmp_path / h2
    p2.write_bytes(b"new")
    reg.register(h2, _entry(p2, "new", size_bytes=3))

    evicted = reg.evict_if_needed()
    assert evicted >= 1
    assert len(reg.all_entries()) == 1
    assert reg.lookup(h1) is None
    assert reg.lookup(h2) is not None
    assert not (tmp_path / f"{h1}.meta.json").exists()
