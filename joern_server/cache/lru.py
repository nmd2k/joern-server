import threading
import time
from collections import OrderedDict
from typing import Optional


class LRUCache:
    """Thread-safe LRU cache with TTL support for query result caching.

    Metrics: hits, misses, evictions
    """

    def __init__(self, max_size: int = 1000, ttl_sec: int = 300):
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def _make_key(self, affinity_key: str, query_hash: str) -> str:
        """Create cache key from affinity_key (sample_id) and md5(query_hash)."""
        return f"{affinity_key}:{query_hash}"

    def get(self, affinity_key: str, query_hash: str) -> Optional[dict]:
        """Get cached result if exists and not expired."""
        key = self._make_key(affinity_key, query_hash)
        current_time = time.time()

        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None

            result, timestamp = self._cache[key]
            if current_time - timestamp > self.ttl_sec:
                # Entry expired
                del self._cache[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self.hits += 1
            return result

    def put(self, affinity_key: str, query_hash: str, result: dict) -> None:
        """Add result to cache, evicting LRU entries if necessary."""
        key = self._make_key(affinity_key, query_hash)
        current_time = time.time()

        with self._lock:
            # If max_size is 0, don't cache anything
            if self.max_size <= 0:
                return

            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # Remove oldest (least recently used)
                self.evictions += 1

            self._cache[key] = (result, current_time)

    def get_metrics(self) -> dict:
        """Return cache metrics."""
        with self._lock:
            hit_rate = self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0.0
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "size": len(self._cache),
                "max_size": self.max_size,
                "ttl_sec": self.ttl_sec,
                "hit_rate": hit_rate,
            }
