"""Lightweight Prometheus text exposition (no prometheus_client dependency)."""

from __future__ import annotations

import os
import threading
import time
from typing import Optional


class PrometheusMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._hist_sum: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._hist_count: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._start_time = time.time()

    @staticmethod
    def _read_rss_bytes() -> float:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return float(int(line.split()[1]) * 1024)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _read_cgroup_memory() -> tuple[float, float]:
        usage = 0.0
        limit = 0.0
        for base in ("/sys/fs/cgroup",):
            usage_path = os.path.join(base, "memory.current")
            limit_path = os.path.join(base, "memory.max")
            if not os.path.exists(usage_path):
                usage_path = os.path.join(base, "memory", "memory.usage_in_bytes")
                limit_path = os.path.join(base, "memory", "memory.limit_in_bytes")
            try:
                if os.path.exists(usage_path):
                    with open(usage_path) as f:
                        usage = float(f.read().strip())
            except Exception:
                pass
            try:
                if os.path.exists(limit_path):
                    with open(limit_path) as f:
                        raw = f.read().strip()
                        if raw.isdigit():
                            limit = float(raw)
            except Exception:
                pass
            if usage > 0:
                break
        return usage, limit

    def inc(self, name: str, value: float = 1.0, labels: Optional[dict[str, str]] = None) -> None:
        key = (name, self._label_key(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None:
        key = (name, self._label_key(labels))
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None:
        key = (name, self._label_key(labels))
        with self._lock:
            self._hist_sum[key] = self._hist_sum.get(key, 0.0) + value
            self._hist_count[key] = self._hist_count.get(key, 0.0) + 1.0

    @staticmethod
    def _label_key(labels: Optional[dict[str, str]]) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((k, v) for k, v in labels.items()))

    @staticmethod
    def _fmt_labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        inner = ",".join(f'{k}="{v}"' for k, v in labels)
        return "{" + inner + "}"

    def render(self) -> str:
        lines: list[str] = []
        hostname = os.getenv("HOSTNAME", "unknown")
        lines.append(f'joern_proxy_info{{host="{hostname}"}} 1')
        lines.append(f"joern_proxy_uptime_seconds {time.time() - self._start_time:.3f}")
        lines.append(f'joern_proxy_memory_rss_bytes{{host="{hostname}"}} {self._read_rss_bytes()}')
        cg_usage, cg_limit = self._read_cgroup_memory()
        lines.append(f'joern_proxy_memory_container_usage_bytes{{host="{hostname}"}} {cg_usage}')
        if cg_limit > 0:
            lines.append(f'joern_proxy_memory_container_limit_bytes{{host="{hostname}"}} {cg_limit}')

        with self._lock:
            for (name, labels), val in sorted(self._counters.items()):
                lines.append(f"{name}_total{self._fmt_labels(labels)} {val}")
            for (name, labels), val in sorted(self._gauges.items()):
                lines.append(f"{name}{self._fmt_labels(labels)} {val}")
            for (name, labels), s in sorted(self._hist_sum.items()):
                cnt = self._hist_count.get((name, labels), 0.0)
                lbl = self._fmt_labels(labels)
                lines.append(f"{name}_sum{lbl} {s}")
                lines.append(f"{name}_count{lbl} {cnt}")

        return "\n".join(lines) + "\n"
