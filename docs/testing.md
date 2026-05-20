# Testing Guide

This guide details how to run the test suite for Joern Server. The tests are consolidated under the `tests/` directory.

---

## Test Organization

```
tests/
├── __init__.py
├── conftest.py              # Pytest fixtures and configuration
├── unit/                    # Unit tests (no external dependencies)
│   ├── __init__.py
│   ├── test_client_affinity.py      # X-Session-Id header tests
│   ├── test_sticky_routing.py       # Load balancer sticky routing mocks
│   ├── test_joern_tool_dispatch.py  # Tool dispatch logic tests
│   └── test_mcp_common_tools.py     # Parser and tool wrapper tests
├── integration/             # Integration tests (require running services)
│   ├── __init__.py
│   ├── test_joern_http_proxy.py         # HTTP proxy forwarding tests
│   ├── test_mcp_client_integration.py   # MCP client integration tests
│   └── test_mcp_functional.py           # Full MCP tool functional tests
└── stress/                  # Load and stress tests
    ├── __init__.py
    └── test_joern_live_stress.py        # Concurrent session stress tests
```

---

## Running Tests

### Quick Start

```bash
# Run all unit tests (fast, no dependencies)
pytest tests/unit/

# Run all tests except integration
pytest -m "not integration"

# Run full test suite (requires services)
pytest -m integration
```

### Unit Tests

Unit tests use mocks and do not require external services:

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run specific test file
pytest tests/unit/test_mcp_common_tools.py -v
```

### Integration Tests

Integration tests require a running Joern server:

```bash
# Run all integration tests
pytest tests/integration/ -v -m integration

# HTTP proxy tests (requires proxy + mock Joern)
pytest tests/integration/test_joern_http_proxy.py -v
```

### Stress Tests

Stress tests validate performance under load:

```bash
# Run all stress tests
pytest tests/stress/ -v -m integration

# Run with custom concurrent configuration
NEURALATLAS_STRESS_SESSIONS=24 \
NEURALATLAS_STRESS_QUERIES_PER_SESSION=30 \
pytest tests/stress/test_joern_live_stress.py -v
```

---

## Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires external services (Joern Server) |
| `stress` | Load/stress testing |

To skip specific categories:

```bash
# Skip integration tests
pytest -m "not integration"

# Skip stress tests
pytest -m "not stress"
```

---

## Environment Variables

### For Integration Tests

```bash
# Joern server connection
export NEURALATLAS_JOERN_HOST=127.0.0.1
export NEURALATLAS_JOERN_PORT=8080
export NEURALATLAS_JOERN_AUTH_USERNAME=joern
export NEURALATLAS_JOERN_AUTH_PASSWORD=joern

# Enable real Joern tests
export NEURALATLAS_RUN_REAL_JOERN_HTTP=1
```

### For Stress Tests

```bash
# Stress test configuration
export NEURALATLAS_LIVE_JOERN_URL=http://127.0.0.1:8080
export NEURALATLAS_STRESS_SESSIONS=16
export NEURALATLAS_STRESS_QUERIES_PER_SESSION=20
export NEURALATLAS_STRESS_THREAD_WORKERS=32
export NEURALATLAS_STRESS_EXECUTOR_THREADS=8
export NEURALATLAS_STRESS_EXECUTOR_ROUNDS=15
export NEURALATLAS_STRESS_PARALLEL_CPG=0  # Set to 1 for multi-replica
```

---

## Coverage

```bash
# Generate coverage report
pytest --cov=joern_server --cov-report=html --cov-report=term
```
