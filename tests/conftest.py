"""Pytest configuration and shared fixtures for joern-server tests."""

import os
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as requiring external services"
    )
    config.addinivalue_line(
        "markers", "stress: mark test as load/stress testing"
    )


def pytest_addoption(parser):
    """Add stress test CLI options."""
    group = parser.getgroup("stress", "Stress test options")
    group.addoption("--stress-url", default="http://127.0.0.1:8080", help="Joern server URL")
    group.addoption("--stress-jsonl", default="/datadrive/data/raw/primevul/primevul_test_paired.jsonl", help="PrimeVul JSONL path")
    group.addoption("--stress-samples", type=int, default=200, help="Number of samples to process")
    group.addoption("--stress-workers", type=int, default=8, help="Number of parallel workers")
    group.addoption("--stress-turns", type=int, default=10, help="Follow-up query turns per sample")
    group.addoption("--stress-turn-sleep", type=float, default=1.0, help="Seconds between turns")
    group.addoption("--stress-cleanup", action="store_true", default=True, help="Cleanup after each sample (default: on)")
    group.addoption("--stress-no-cleanup", action="store_true", default=False, help="Skip cleanup after each sample")
    group.addoption("--stress-language", default="c", help="Language for parsing")
    group.addoption("--stress-parse-timeout", type=float, default=900, help="Parse timeout in seconds")
    group.addoption("--stress-query-timeout", type=float, default=600, help="Query timeout in seconds")

    group.addoption("--soak-url", default="http://127.0.0.1:8080", help="Joern server URL for soak test")
    group.addoption("--soak-duration", type=int, default=600, help="Soak test duration in seconds")
    group.addoption("--soak-workers", type=int, default=8, help="Number of parallel soak workers")
    group.addoption("--soak-queries", type=int, default=5, help="Queries per soak session")
    group.addoption("--soak-max-iters", type=int, default=1000, help="Maximum total iterations")
    group.addoption("--soak-fail-fast", action="store_true", default=False, help="Stop on first error (fail-fast mode)")
    group.addoption("--soak-archive", action="store_true", default=True, help="Enable CPG archiving on cleanup (default: on)")
    group.addoption("--soak-no-archive", action="store_true", default=False, help="Disable CPG archiving on cleanup")


@pytest.fixture(scope="session")
def repo_root():
    """Return the repository root directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
