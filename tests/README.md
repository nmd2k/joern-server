# Joern Server Test Suite

This directory contains the consolidated test suite for the joern-server project.

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
├── integration/             # Integration tests (require services)
│   ├── __init__.py
│   ├── test_joern_http_proxy.py         # HTTP proxy forwarding tests
│   ├── test_mcp_client_integration.py   # MCP client integration tests
│   └── test_mcp_functional.py           # Full MCP tool functional tests
└── stress/                  # Load and stress tests
    ├── __init__.py
    └── test_joern_live_stress.py        # Concurrent session stress tests
```

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

Unit tests use mocks and require no external services:

```bash
# All unit tests
pytest tests/unit/ -v

# Specific test file
pytest tests/unit/test_mcp_common_tools.py -v

# Specific test class
pytest tests/unit/test_mcp_common_tools.py::TestExtractValue -v

# Specific test function
pytest tests/unit/test_mcp_common_tools.py::TestExtractValue::test_string_value -v
```

### Integration Tests

Integration tests require running Joern server and/or MCP services:

```bash
# All integration tests
pytest tests/integration/ -v -m integration

# HTTP proxy tests (requires proxy + mock Joern)
pytest tests/integration/test_joern_http_proxy.py -v

# MCP client integration
pytest tests/integration/test_mcp_client_integration.py -v -m integration

# Full MCP functional test (all 18 tools)
pytest tests/integration/test_mcp_functional.py -v -m integration
```

### Stress Tests

Stress tests validate performance under load:

```bash
# All stress tests
pytest tests/stress/ -v -m integration

# With custom configuration
NEURALATLAS_STRESS_SESSIONS=24 \
NEURALATLAS_STRESS_QUERIES_PER_SESSION=30 \
pytest tests/stress/test_joern_live_stress.py -v
```

## Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires external services (Joern, MCP) |
| `stress` | Load/stress testing |

Skip markers:
```bash
# Skip integration tests
pytest -m "not integration"

# Skip stress tests
pytest -m "not stress"
```

## Environment Variables

### For Integration Tests

```bash
# Joern server connection
export NEURALATLAS_JOERN_HOST=127.0.0.1
export NEURALATLAS_JOERN_PORT=8080
export NEURALATLAS_JOERN_AUTH_USERNAME=joern
export NEURALATLAS_JOERN_AUTH_PASSWORD=joern

# MCP server connection
export NEURALATLAS_MCP_URL=http://127.0.0.1:9000/sse

# Enable real Joern tests
export NEURALATLAS_RUN_REAL_JOERN_HTTP=1
export NEURALATLAS_RUN_MCP_INTEGRATION=1
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

## Coverage

```bash
# Generate coverage report
pytest --cov=joern_server --cov=mcp-joern --cov-report=html --cov-report=term

# Open HTML report
# Chrome: google-chrome htmlcov/index.html
# Firefox: firefox htmlcov/index.html
```

## Test Development

### Adding New Tests

1. **Unit tests** (`tests/unit/`):
   - Use `httpx.MockTransport` for HTTP mocking
   - Use `unittest.mock.patch` for dependency mocking
   - No external service dependencies

2. **Integration tests** (`tests/integration/`):
   - Mark with `@pytest.mark.integration`
   - Start required services in test fixtures
   - Use `pytest.skip()` for optional dependencies

3. **Stress tests** (`tests/stress/`):
   - Mark with `@pytest.mark.integration`
   - Use environment variables for configuration
   - Test concurrent access patterns

### Test Fixtures

Common fixtures in `conftest.py`:

```python
@pytest.fixture(scope="session")
def repo_root():
    """Repository root directory."""

@pytest.fixture(scope="session")
def mcp_joern_dir(repo_root):
    """MCP-joern directory path."""
```

## Legacy Tests

Tests in `mcp-joern/tests/` are deprecated but still functional.
They have been migrated to this directory with the following mapping:

| Legacy Path | New Path |
|-------------|----------|
| `mcp-joern/tests/test_tools.py` | `tests/unit/test_mcp_common_tools.py` |
| `mcp-joern/tests/test_mcp_client.py` | `tests/integration/test_mcp_client_integration.py` |
| `mcp-joern/tests/test_mcp_functional.py` | `tests/integration/test_mcp_functional.py` |
| `tests/test_joern_client_affinity.py` | `tests/unit/test_client_affinity.py` |
| `tests/test_joern_sticky_routing_mock.py` | `tests/unit/test_sticky_routing.py` |
| `tests/test_joern_live_sticky_stress.py` | `tests/stress/test_joern_live_stress.py` |
| `tests/test_joern_http_proxy.py` | `tests/integration/test_joern_http_proxy.py` |
| `tests/test_mcp_client_integration.py` | `tests/integration/test_mcp_client_integration.py` |

## CI Integration

Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r mcp-joern/requirements.txt
      - run: pip install pytest pytest-cov
      - run: pytest tests/unit/ -v
      - run: pytest -m "not integration" -v
```
