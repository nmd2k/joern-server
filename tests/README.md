# Tests

See **[`docs/testing.md`](../docs/testing.md)** for the full testing guide.

```bash
pytest tests/unit/ -q
NEURALATLAS_RUN_HAPROXY_TESTS=1 pytest tests/integration/test_haproxy_stickiness.py -m integration -v
```
