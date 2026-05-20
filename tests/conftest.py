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


@pytest.fixture(scope="session")
def repo_root():
    """Return the repository root directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
