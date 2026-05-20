"""Unit tests for scripts/benchmark_parse.py (S9-009)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "benchmark_parse.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sven_mini"


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_parse", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bench():
    return _load_benchmark_module()


class TestSprint9Helpers:
    def test_discover_sven_samples_subdirs(self, bench):
        samples = bench.discover_sven_samples(FIXTURE)
        keys = {k for k, _ in samples}
        assert keys == {"sample_a", "sample_b"}

    def test_build_repo_ndjson(self, bench):
        _, root = next((k, p) for k, p in bench.discover_sven_samples(FIXTURE) if k == "sample_a")
        body, count = bench.build_repo_ndjson(root)
        assert count == 2
        lines = body.decode("utf-8").strip().split("\n")
        assert len(lines) == 2
        paths = {json.loads(line)["path"] for line in lines}
        assert paths == {"main.c", "util.h"}

    def test_percentile(self, bench):
        assert bench.percentile([10.0, 20.0, 30.0], 50) == 20.0

    def test_pick_primary_file_prefers_c(self, bench):
        _, root = next((k, p) for k, p in bench.discover_sven_samples(FIXTURE) if k == "sample_a")
        primary = bench.pick_primary_file(root)
        assert primary is not None
        assert primary.suffix == ".c"


class TestSprint9Cli:
    def test_sprint9_help(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "sprint9", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "bench-file" in proc.stdout
        assert "bench-repo-jsonl" in proc.stdout
        assert "--dry-run" in proc.stdout

    def test_sprint9_dry_run_writes_report(self, bench, tmp_path):
        out = tmp_path / "bench.md"
        summaries, dry_run, query_stats = bench.run_sprint9_benchmark(
            FIXTURE,
            ["bench-file", "bench-repo-jsonl"],
            http_url="http://127.0.0.1:9",
            num_samples=10,
            dry_run=True,
            timeout=5,
            skip_query_stats=True,
        )
        assert dry_run is True
        assert query_stats is False
        assert len(summaries) == 2
        bench.generate_sprint9_report(
            summaries,
            out,
            dataset=FIXTURE,
            http_url="http://127.0.0.1:9",
            dry_run=True,
            query_stats=False,
        )
        text = out.read_text()
        assert "bench-file" in text
        assert "bench-repo-jsonl" in text
        assert "parse p50" in text
