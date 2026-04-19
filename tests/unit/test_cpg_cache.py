"""Unit tests for CPG hash-based caching (S3-001 through S3-004)."""

import hashlib
import json
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from joern_server.proxy import CPGRegistry, _dir_size_bytes, _get_hash_lock


class TestCPGRegistry:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry_path = Path(self._tmpdir) / "cpg-registry.json"

    def teardown_method(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_archive(self, name: str) -> Path:
        p = Path(self._tmpdir) / "archive" / name
        p.mkdir(parents=True, exist_ok=True)
        (p / "cpg.bin").write_bytes(b"fake-cpg-data")
        return p

    # ---- S3-004: registry basics ----

    def test_lookup_empty_returns_none(self):
        reg = CPGRegistry(self.registry_path)
        assert reg.lookup("nonexistent") is None

    def test_register_and_lookup(self):
        reg = CPGRegistry(self.registry_path)
        archive = self._make_archive("abc123")
        entry = {
            "archive_path": str(archive),
            "sample_id": "s1",
            "archived_at": "2026-01-01T00:00:00Z",
            "last_used": "2026-01-01T00:00:00Z",
            "size_bytes": 13,
        }
        reg.register("abc123", entry)
        result = reg.lookup("abc123")
        assert result is not None
        assert result["sample_id"] == "s1"

    def test_register_persists_to_disk(self):
        reg = CPGRegistry(self.registry_path)
        archive = self._make_archive("hash1")
        reg.register("hash1", {"archive_path": str(archive), "sample_id": "s1",
                                "archived_at": "T", "last_used": "T", "size_bytes": 13})
        # Load fresh instance from same file
        reg2 = CPGRegistry(self.registry_path)
        assert reg2.lookup("hash1") is not None

    def test_remove_deletes_entry(self):
        reg = CPGRegistry(self.registry_path)
        archive = self._make_archive("rmhash")
        reg.register("rmhash", {"archive_path": str(archive), "sample_id": "s2",
                                  "archived_at": "T", "last_used": "T", "size_bytes": 5})
        reg.remove("rmhash")
        assert reg.lookup("rmhash") is None

    def test_all_entries_returns_list(self):
        reg = CPGRegistry(self.registry_path)
        archive1 = self._make_archive("h1")
        archive2 = self._make_archive("h2")
        reg.register("h1", {"archive_path": str(archive1), "sample_id": "s1",
                              "archived_at": "T", "last_used": "T", "size_bytes": 13})
        reg.register("h2", {"archive_path": str(archive2), "sample_id": "s2",
                              "archived_at": "T", "last_used": "T", "size_bytes": 13})
        entries = reg.all_entries()
        assert len(entries) == 2

    def test_corrupt_registry_starts_empty(self):
        self.registry_path.write_text("NOT JSON{{{", encoding="utf-8")
        reg = CPGRegistry(self.registry_path)
        assert reg.lookup("anything") is None
        assert reg.all_entries() == []

    def test_missing_archive_path_self_heals(self):
        self.registry_path.write_text(
            json.dumps({"deadhash": {"archive_path": "/nonexistent/path", "sample_id": "s",
                                      "archived_at": "T", "last_used": "T", "size_bytes": 0}}),
            encoding="utf-8",
        )
        reg = CPGRegistry(self.registry_path)
        assert reg.lookup("deadhash") is None

    def test_thread_safe_concurrent_register(self):
        reg = CPGRegistry(self.registry_path)
        errors = []

        def worker(i):
            archive = self._make_archive(f"th{i}")
            try:
                reg.register(f"th{i}", {"archive_path": str(archive), "sample_id": f"s{i}",
                                         "archived_at": "T", "last_used": "T", "size_bytes": 13})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(reg.all_entries()) == 20

    # ---- S3-003: disk LRU eviction ----

    def test_evict_by_count(self):
        reg = CPGRegistry(self.registry_path, archive_max_count=2, archive_max_gb=50)
        for i in range(3):
            archive = self._make_archive(f"ev{i}")
            reg.register(f"ev{i}", {"archive_path": str(archive), "sample_id": f"s{i}",
                                     "archived_at": "T", "last_used": f"2026-01-0{i+1}T00:00:00Z",
                                     "size_bytes": 13})
        evicted = reg.evict_if_needed()
        assert evicted == 1
        assert len(reg.all_entries()) == 2
        # Oldest (ev0) should be evicted
        assert reg.lookup("ev0") is None

    def test_evict_by_size(self):
        # max 0.000001 GB (~1000 bytes) — force size eviction
        reg = CPGRegistry(self.registry_path, archive_max_count=100, archive_max_gb=0.000001)
        for i in range(2):
            archive = self._make_archive(f"sz{i}")
            # Write 600 bytes each
            (archive / "cpg.bin").write_bytes(b"x" * 600)
            reg.register(f"sz{i}", {"archive_path": str(archive), "sample_id": f"s{i}",
                                     "archived_at": "T", "last_used": f"2026-01-0{i+1}T00:00:00Z",
                                     "size_bytes": 600})
        evicted = reg.evict_if_needed()
        assert evicted >= 1

    def test_evict_removes_directory(self):
        reg = CPGRegistry(self.registry_path, archive_max_count=1, archive_max_gb=50)
        a0 = self._make_archive("old0")
        a1 = self._make_archive("new1")
        reg.register("old0", {"archive_path": str(a0), "sample_id": "s0",
                                "archived_at": "T", "last_used": "2026-01-01T00:00:00Z",
                                "size_bytes": 13})
        reg.register("new1", {"archive_path": str(a1), "sample_id": "s1",
                                "archived_at": "T", "last_used": "2026-01-02T00:00:00Z",
                                "size_bytes": 13})
        reg.evict_if_needed()
        # old0 directory should be gone
        assert not a0.exists()

    # ---- helpers ----

    def test_dir_size_bytes(self):
        d = Path(self._tmpdir) / "sizedir"
        d.mkdir()
        (d / "f1").write_bytes(b"hello")
        (d / "f2").write_bytes(b"world!")
        assert _dir_size_bytes(d) == 11

    def test_get_hash_lock_same_hash_same_lock(self):
        lock1 = _get_hash_lock("samehash")
        lock2 = _get_hash_lock("samehash")
        assert lock1 is lock2

    def test_get_hash_lock_different_hashes_different_locks(self):
        lock1 = _get_hash_lock("hash_aaa")
        lock2 = _get_hash_lock("hash_bbb")
        assert lock1 is not lock2
