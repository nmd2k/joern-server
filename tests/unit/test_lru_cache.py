"""Unit tests for LRU query result caching in proxy.py."""

import threading
import time
from collections import OrderedDict

import pytest

from joern_server.proxy import LRUCache


class TestLRUCache:
    """Test suite for LRUCache class."""

    def test_basic_put_and_get(self):
        """Test basic cache put and get operations."""
        cache = LRUCache(max_size=100, ttl_sec=300)

        cache.put("session1", "hash123", {"result": "value1"})
        result = cache.get("session1", "hash123")

        assert result == {"result": "value1"}

    def test_cache_miss_for_nonexistent_key(self):
        """Test that cache returns None for non-existent keys."""
        cache = LRUCache(max_size=100, ttl_sec=300)

        result = cache.get("session1", "nonexistent")

        assert result is None

    def test_cache_miss_for_different_session(self):
        """Test that different sessions don't share cache entries."""
        cache = LRUCache(max_size=100, ttl_sec=300)

        cache.put("session1", "hash123", {"result": "value1"})
        result = cache.get("session2", "hash123")

        assert result is None

    def test_lru_eviction(self):
        """Test that least recently used entries are evicted first."""
        cache = LRUCache(max_size=3, ttl_sec=300)

        # Fill cache to capacity
        cache.put("session1", "hash1", {"result": "1"})
        cache.put("session1", "hash2", {"result": "2"})
        cache.put("session1", "hash3", {"result": "3"})

        # Access hash1 to make it recently used
        cache.get("session1", "hash1")

        # Add new entry - should evict hash2 (least recently used)
        cache.put("session1", "hash4", {"result": "4"})

        # hash2 should be evicted, hash1 and hash3 should remain
        assert cache.get("session1", "hash1") == {"result": "1"}
        assert cache.get("session1", "hash2") is None  # Evicted
        assert cache.get("session1", "hash3") == {"result": "3"}
        assert cache.get("session1", "hash4") == {"result": "4"}

    def test_ttl_expiration(self):
        """Test that entries expire after TTL."""
        cache = LRUCache(max_size=100, ttl_sec=1)  # 1 second TTL

        cache.put("session1", "hash1", {"result": "value1"})

        # Should be available immediately
        assert cache.get("session1", "hash1") == {"result": "value1"}

        # Wait for TTL to expire
        time.sleep(1.1)

        # Should be expired
        assert cache.get("session1", "hash1") is None

    def test_cache_metrics(self):
        """Test that cache metrics are accurately tracked."""
        cache = LRUCache(max_size=2, ttl_sec=300)

        # Initial metrics
        metrics = cache.get_metrics()
        assert metrics["hits"] == 0
        assert metrics["misses"] == 0
        assert metrics["evictions"] == 0
        assert metrics["size"] == 0
        assert metrics["max_size"] == 2
        assert metrics["hit_rate"] == 0.0

        # Add entries and trigger misses
        cache.get("session1", "hash1")  # Miss
        cache.put("session1", "hash1", {"result": "1"})
        cache.get("session1", "hash2")  # Miss

        # Trigger hit
        cache.get("session1", "hash1")  # Hit

        # Trigger eviction
        cache.put("session1", "hash2", {"result": "2"})
        cache.put("session1", "hash3", {"result": "3"})  # Evicts hash1

        metrics = cache.get_metrics()
        assert metrics["hits"] == 1
        assert metrics["misses"] == 2
        assert metrics["evictions"] == 1
        assert metrics["size"] == 2
        assert metrics["hit_rate"] == 1 / 3

    def test_thread_safety(self):
        """Test that cache operations are thread-safe."""
        cache = LRUCache(max_size=1000, ttl_sec=300)
        num_threads = 10
        ops_per_thread = 100

        def worker(thread_id):
            for i in range(ops_per_thread):
                session_id = f"session{thread_id}"
                hash_id = f"hash{i}"
                cache.put(session_id, hash_id, {"thread": thread_id, "i": i})
                cache.get(session_id, hash_id)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify cache is in consistent state
        metrics = cache.get_metrics()
        assert metrics["size"] <= 1000  # Should not exceed max_size
        assert metrics["hits"] + metrics["misses"] == num_threads * ops_per_thread

    def test_update_existing_entry(self):
        """Test that updating an entry moves it to most recently used."""
        cache = LRUCache(max_size=3, ttl_sec=300)

        cache.put("session1", "hash1", {"result": "1"})
        cache.put("session1", "hash2", {"result": "2"})
        cache.put("session1", "hash3", {"result": "3"})

        # Update hash1 - should move to end
        cache.put("session1", "hash1", {"result": "1-updated"})

        # Add new entry - should evict hash2 (now least recently used)
        cache.put("session1", "hash4", {"result": "4"})

        assert cache.get("session1", "hash1") == {"result": "1-updated"}
        assert cache.get("session1", "hash2") is None  # Evicted
        assert cache.get("session1", "hash3") == {"result": "3"}
        assert cache.get("session1", "hash4") == {"result": "4"}

    def test_empty_cache_metrics(self):
        """Test metrics on empty cache."""
        cache = LRUCache(max_size=100, ttl_sec=300)
        metrics = cache.get_metrics()

        assert metrics["hit_rate"] == 0.0  # Should not divide by zero

    def test_max_size_zero(self):
        """Test cache with max_size=0 (no caching)."""
        cache = LRUCache(max_size=0, ttl_sec=300)

        cache.put("session1", "hash1", {"result": "1"})

        # With max_size=0, nothing is cached (early return in put)
        assert cache.get("session1", "hash1") is None
        assert cache.get_metrics()["size"] == 0
        assert cache.get_metrics()["evictions"] == 0  # No evictions, just no caching

    def test_key_generation(self):
        """Test that cache keys are properly generated."""
        cache = LRUCache(max_size=100, ttl_sec=300)

        cache.put("my-session", "abc123", {"result": "value"})

        # Key should combine session_id and hash
        result = cache.get("my-session", "abc123")
        assert result == {"result": "value"}

        # Different session should miss
        result = cache.get("other-session", "abc123")
        assert result is None

        # Different hash should miss
        result = cache.get("my-session", "xyz789")
        assert result is None
