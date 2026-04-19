"""Integration tests for CPGRegistry with real filesystem I/O (S3-005)."""

import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

from joern_server.proxy import CPGRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_archive_dir(base: Path, name: str, size_bytes: int = 13) -> Path:
    """Create a minimal archive directory with a fake CPG binary."""
    p = base / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "cpg.bin").write_bytes(b"x" * size_bytes)
    return p


def _entry(archive_path: Path, sample_id: str, last_used: str, size_bytes: int = 13) -> dict:
    return {
        "archive_path": str(archive_path),
        "sample_id": sample_id,
        "archived_at": "2026-01-01T00:00:00Z",
        "last_used": last_used,
        "size_bytes": size_bytes,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_registry_lifecycle(tmp_path):
    """register → save → reload from disk → lookup succeeds."""
    registry_file = tmp_path / "cpg-registry.json"
    archive_dir = _make_archive_dir(tmp_path, "arch_abc")

    reg = CPGRegistry(registry_file)
    reg.register("abc", _entry(archive_dir, "sample-abc", "2026-01-01T00:00:00Z"))

    # Verify the JSON file was written
    assert registry_file.exists()
    raw = json.loads(registry_file.read_text())
    assert "abc" in raw

    # Fresh instance from the same file should find the entry
    reg2 = CPGRegistry(registry_file)
    result = reg2.lookup("abc")
    assert result is not None
    assert result["sample_id"] == "sample-abc"


def test_persistence_across_instances(tmp_path):
    """Entries survive creation of a new registry instance pointing to the same file."""
    registry_file = tmp_path / "reg.json"
    arch1 = _make_archive_dir(tmp_path, "arch1")
    arch2 = _make_archive_dir(tmp_path, "arch2")

    reg_a = CPGRegistry(registry_file)
    reg_a.register("hash1", _entry(arch1, "s1", "2026-01-01T00:00:00Z"))
    reg_a.register("hash2", _entry(arch2, "s2", "2026-01-02T00:00:00Z"))

    reg_b = CPGRegistry(registry_file)
    assert reg_b.lookup("hash1") is not None
    assert reg_b.lookup("hash2") is not None
    assert len(reg_b.all_entries()) == 2


def test_self_healing_missing_archive_path(tmp_path):
    """Entry whose archive_path no longer exists is silently dropped on load."""
    registry_file = tmp_path / "reg.json"

    # Write a registry entry pointing to a real archive
    arch = _make_archive_dir(tmp_path, "arch_gone")
    reg = CPGRegistry(registry_file)
    reg.register("gone_hash", _entry(arch, "s1", "2026-01-01T00:00:00Z"))

    # Delete the archive directory from disk
    shutil.rmtree(str(arch))
    assert not arch.exists()

    # New instance should drop the orphaned entry without crashing
    reg2 = CPGRegistry(registry_file)
    assert reg2.lookup("gone_hash") is None
    assert reg2.all_entries() == []


def test_self_healing_corrupt_json(tmp_path):
    """Garbage JSON in registry file → new instance starts empty without crashing."""
    registry_file = tmp_path / "reg.json"
    registry_file.write_text("}{NOT VALID JSON{{{", encoding="utf-8")

    reg = CPGRegistry(registry_file)
    assert reg.lookup("anything") is None
    assert reg.all_entries() == []


def test_lru_eviction_by_count(tmp_path):
    """Register 5 entries; max_count=3 → evict 2 oldest, keep 3 newest; deleted dirs gone."""
    registry_file = tmp_path / "reg.json"
    archives = []
    for i in range(5):
        arch = _make_archive_dir(tmp_path, f"ev{i}")
        archives.append(arch)

    reg = CPGRegistry(registry_file, archive_max_count=3, archive_max_gb=50.0)
    for i in range(5):
        # last_used timestamps increase with i — ev0 is oldest, ev4 is newest
        ts = f"2026-01-0{i+1}T00:00:00Z"
        reg.register(f"ev{i}", _entry(archives[i], f"s{i}", ts))

    evicted = reg.evict_if_needed()

    assert evicted == 2
    assert len(reg.all_entries()) == 3

    # Two oldest (ev0, ev1) must be evicted and their directories deleted
    assert reg.lookup("ev0") is None
    assert reg.lookup("ev1") is None
    assert not archives[0].exists()
    assert not archives[1].exists()

    # Three newest must survive
    for i in (2, 3, 4):
        assert reg.lookup(f"ev{i}") is not None
        assert archives[i].exists()


def test_lru_eviction_by_size(tmp_path):
    """Entries exceeding max_gb are evicted oldest-first until under the limit."""
    registry_file = tmp_path / "reg.json"

    # Each archive is ~1 000 bytes; set limit to ~1 500 bytes (≈ 0.0000014 GB)
    # so the first (oldest) entry gets evicted.
    byte_size = 1000
    max_gb = (byte_size * 1.5) / (1024 ** 3)

    reg = CPGRegistry(registry_file, archive_max_count=100, archive_max_gb=max_gb)
    for i in range(2):
        arch = _make_archive_dir(tmp_path, f"sz{i}", size_bytes=byte_size)
        ts = f"2026-01-0{i+1}T00:00:00Z"
        reg.register(f"sz{i}", _entry(arch, f"s{i}", ts, size_bytes=byte_size))

    evicted = reg.evict_if_needed()
    assert evicted >= 1
    # Oldest (sz0) should be the first to go
    assert reg.lookup("sz0") is None


def test_concurrent_registry_access(tmp_path):
    """10 threads simultaneously calling register + lookup — no exception, consistent state."""
    registry_file = tmp_path / "reg.json"
    reg = CPGRegistry(registry_file)
    errors: list[Exception] = []
    results: list[bool] = []

    def worker(i: int):
        arch = _make_archive_dir(tmp_path / "concurrent", f"th{i}")
        try:
            reg.register(f"th{i}", _entry(arch, f"s{i}", "2026-01-01T00:00:00Z"))
            found = reg.lookup(f"th{i}")
            results.append(found is not None)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Exceptions in threads: {errors}"
    assert all(results), "Some lookups returned None immediately after register"
    assert len(reg.all_entries()) == 10


def test_atomic_save(tmp_path):
    """Registry file is always valid JSON after save, even for large entries."""
    registry_file = tmp_path / "reg.json"
    reg = CPGRegistry(registry_file)

    # Create a large entry to stress the write path
    arch = _make_archive_dir(tmp_path, "big_arch", size_bytes=100)
    large_value = "A" * 100_000  # 100 KB string in an entry field
    entry = _entry(arch, "large-sample", "2026-01-01T00:00:00Z")
    entry["extra"] = large_value

    reg.register("big_hash", entry)

    # File must be parseable JSON immediately after register (atomic rename via .tmp)
    raw = registry_file.read_text(encoding="utf-8")
    parsed = json.loads(raw)  # raises if not valid JSON
    assert "big_hash" in parsed


def test_round_trip_encode_decode(tmp_path):
    """Unicode sample_id and non-ASCII source_hash survive save/reload exactly."""
    registry_file = tmp_path / "reg.json"
    arch = _make_archive_dir(tmp_path, "unicode_arch")

    unicode_sample_id = "sample-\u4e2d\u6587-\u00e9l\u00e8ve"
    non_ascii_hash = "\u00e9\u00e0\u00fc\u00f1hash123"

    reg = CPGRegistry(registry_file)
    entry = _entry(arch, unicode_sample_id, "2026-01-01T00:00:00Z")
    reg.register(non_ascii_hash, entry)

    # Reload from disk and verify exact match
    reg2 = CPGRegistry(registry_file)
    result = reg2.lookup(non_ascii_hash)
    assert result is not None
    assert result["sample_id"] == unicode_sample_id
    assert result["archive_path"] == str(arch)


def test_evict_if_needed_noop_under_limits(tmp_path):
    """When under both limits, evict_if_needed returns 0 and leaves registry unchanged."""
    registry_file = tmp_path / "reg.json"
    reg = CPGRegistry(registry_file, archive_max_count=10, archive_max_gb=50.0)

    for i in range(3):
        arch = _make_archive_dir(tmp_path, f"noop{i}")
        reg.register(f"noop{i}", _entry(arch, f"s{i}", f"2026-01-0{i+1}T00:00:00Z"))

    evicted = reg.evict_if_needed()
    assert evicted == 0
    assert len(reg.all_entries()) == 3
