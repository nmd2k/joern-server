# Getting started

Run Joern Server locally, parse a snippet, and execute your first CPGQL query.

---

## Prerequisites

- Docker and Docker Compose
- `curl` (or any HTTP client)
- Python 3.10+ (optional, for tests and MkDocs)

---

## 1. Configure environment

From the repository root:

```bash
cp deploy/.env.example deploy/.env
```

Edit `deploy/.env` and set at least:

```env
JOERN_SERVER_AUTH_PASSWORD=your-secure-password
```

Leave username empty to disable auth in dev, or set `JOERN_SERVER_AUTH_USERNAME` and password for Basic auth.

---

## 2. Start the dev profile

Single replica, port 8080 published on the host:

```bash
docker compose -f deploy/compose.dev.yml up -d
```

Check that the server is healthy:
```bash
docker compose -f deploy/compose.dev.yml ps
curl -s http://127.0.0.1:8080/health | jq .

>> {
>>   "ok": true,
>>   "joern_ok": true,
>>   "latency_ms": 42
>> }
```

---

## 3. Parse a code snippet

```bash
curl -s -X POST http://127.0.0.1:8080/parse \
  -H 'Content-Type: application/json' \
  -d '{
    "sample_id": "hello-world",
    "source_code": "int main() { return 0; }",
    "language": "c",
    "overwrite": true
  }' | jq .
```


---

## 4. Import and query

Use two headers:

- `X-Affinity-Key` — same as `sample_id` (routes to the correct replica in scale mode)
- `X-Session-Id` — your agent or run id (logging)

**Import the CPG:**

```bash
curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Affinity-Key: hello-world' \
  -H 'X-Session-Id: my-agent-run-1' \
  -d '{"query": "importCpg(\"/workspace/cpg-out/hello-world\")"}' | jq .
```

**Query methods:**

```bash
curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Affinity-Key: hello-world' \
  -H 'X-Session-Id: my-agent-run-1' \
  -d '{"query": "cpg.method.name.l"}' | jq .
```

---

## 5. Cleanup

```bash
curl -s -X POST http://127.0.0.1:8080/cleanup \
  -H 'Content-Type: application/json' \
  -H 'X-Affinity-Key: hello-world' \
  -d '{"sample_id": "hello-world"}' | jq .
```

---

## Python client

```python
from joern_server.client import JoernHTTPQueryExecutor

with JoernHTTPQueryExecutor(
    "http://127.0.0.1:8080",
    session_id="my-agent-run-1",
    affinity_key="hello-world",
    reuse_base=True,
) as ex:
    ex.parse_source(
        sample_id="hello-world",
        source_code="int main() { return 0; }",
        language="c",
        overwrite=True,
    )
    ex.execute('importCpg("/workspace/cpg-out/hello-world")')
    print(ex.execute("cpg.method.name.l"))
    ex.cleanup("hello-world")
```

---

## Scaled deployment

For multiple agents in parallel:

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

Clients still use `http://127.0.0.1:8080` (HAProxy VIP). See [Deployment](deploy.md).

---

## Next steps

- [Architecture](ARCHITECTURE.md) — how components fit together
- [Query guide](query_guide.md) — repo ingest, graph endpoints, CPGQL patterns
- [Deployment](deploy.md) — production configuration and monitoring
- [Developer guide](developer_guide.md) — contributing and extending the proxy
